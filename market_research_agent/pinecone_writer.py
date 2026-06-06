"""
Pinecone writer for the Market Research Agent.

Embeds signal/pick text and upserts it into the appropriate Pinecone namespace.
TTL is conveyed as an ``expires_utc`` metadata field (the recency filters at
query time enforce the effective window).
"""

from __future__ import annotations

import asyncio
import logging
import time

from gateway.pinecone_client import pinecone_gateway
from market_research_agent.data_sources import MarketSignal
from market_research_agent.embedder import embed

logger = logging.getLogger("finley.pinecone_writer")

# TTL by signal data_type, in hours.
_TTL_HOURS = {"tick_summary": 4, "news_digest": 48, "social_sentiment": 24}


async def write_signal(signal: MarketSignal) -> str:
    """Embed and upsert a single market signal. Returns its signal_id."""
    signal_id = f"signal_{signal.ticker}_{signal.timestamp_utc}"
    ttl_hours = _TTL_HOURS.get(signal.data_type, 24)
    embedding = await embed(signal.content, task_type="RETRIEVAL_DOCUMENT")
    metadata = {
        "signal_id": signal_id,
        "ticker": signal.ticker,
        "data_type": signal.data_type,
        "source": signal.source,
        "content": signal.content,
        "sentiment_score": signal.sentiment_score if signal.sentiment_score is not None else 0.0,
        "sentiment_label": signal.sentiment_label(),
        "confidence": signal.confidence,
        "price_at_signal": signal.price_at_signal if signal.price_at_signal is not None else 0.0,
        "timestamp_utc": signal.timestamp_utc,
        "expires_utc": signal.timestamp_utc + ttl_hours * 3600,
    }
    await pinecone_gateway.upsert_signal(signal_id, embedding, metadata)
    return signal_id


async def write_signals_batch(signals: list[MarketSignal]) -> list[str]:
    """Embed + upsert many signals concurrently. Returns their signal_ids."""
    if not signals:
        return []
    return list(await asyncio.gather(*(write_signal(s) for s in signals)))


async def generate_and_write_pick(
    ticker: str,
    direction: str,
    rationale: str,
    signal_ids: list[str],
    confidence: float,
) -> str:
    """Embed a pick rationale and upsert it to global::stock_picks. Returns pick_id."""
    pick_id = f"pick_{ticker}_{int(time.time())}"
    ttl_hours = 6
    now = int(time.time())
    embedding = await embed(rationale, task_type="RETRIEVAL_DOCUMENT")
    metadata = {
        "pick_id": pick_id,
        "ticker": ticker,
        "direction": direction,
        "rationale": rationale,
        "confidence": confidence,
        "source_signal_ids": signal_ids,
        "timestamp_utc": now,
        "expires_utc": now + ttl_hours * 3600,
    }
    await pinecone_gateway.upsert_pick(pick_id, embedding, metadata)
    return pick_id
