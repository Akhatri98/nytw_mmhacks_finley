"""
Gateway context assembly.

``build_context`` fans out to three sources in parallel:
  - Pinecone ``global::signals``    (recent market signals)
  - Pinecone ``global::stock_picks`` (recent generated picks)
  - Supabase ``trades``              (the user's recent trade history)

Each source is independently fault-tolerant: if one fails it contributes an
empty list and the others still return. ``build_context`` never raises.
"""

from __future__ import annotations

import asyncio
import logging

from broker_agent.supabase_client import service_client
from finley_agent.gemini_client import embed
from gateway.pinecone_client import pinecone_gateway

logger = logging.getLogger("finley.gateway")


async def _fetch_signals(embedding: list[float]) -> list[dict]:
    try:
        return await pinecone_gateway.query_signals(embedding, top_k=6, hours_ago=48)
    except Exception as exc:
        logger.error("Signal fetch failed: %s", exc)
        return []


async def _fetch_picks(embedding: list[float]) -> list[dict]:
    try:
        return await pinecone_gateway.query_picks(embedding, top_k=4)
    except Exception as exc:
        logger.error("Pick fetch failed: %s", exc)
        return []


async def _fetch_trades(user_id: str) -> list[dict]:
    """Recent trades for the user via the service client (works in demo + prod)."""
    try:
        client = await service_client()
        response = await (
            client.table("trades")
            .select("*")
            .eq("user_id", user_id)
            .order("executed_at", desc=True)
            .limit(10)
            .execute()
        )
        return response.data or []
    except Exception as exc:
        logger.error("Trade history fetch failed for user=%s: %s", user_id, exc)
        return []


async def build_context(query: str, user_id: str, clerk_jwt: str = "") -> dict:
    """Assemble RAG context for a user query.

    Returns ``{"market_signals": [...], "stock_picks": [...], "trade_history": [...]}``.
    On any per-source failure, that source is an empty list. Never raises.
    """
    try:
        embedding = await embed(query, task_type="RETRIEVAL_QUERY")
    except Exception as exc:
        logger.error("Query embedding failed: %s", exc)
        embedding = []

    signals, picks, trades = await asyncio.gather(
        _fetch_signals(embedding),
        _fetch_picks(embedding),
        _fetch_trades(user_id),
    )

    return {
        "market_signals": signals,
        "stock_picks": picks,
        "trade_history": trades,
    }
