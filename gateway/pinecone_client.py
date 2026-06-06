"""
Pinecone gateway — async wrapper around the synchronous Pinecone SDK (v4/v5).

The SDK is synchronous, so all index operations run inside ``asyncio.to_thread``.
The index is created lazily on first use if it does not exist. Every method is
defensive: on any failure (missing index, network error) reads return ``[]`` and
writes log-and-continue, so a Pinecone outage never breaks the conversation.

Namespaces:
    global::signals      market signal vectors (Market Research Agent writes)
    global::stock_picks  generated trade picks
"""

from __future__ import annotations

import asyncio
import logging
import time

import config

logger = logging.getLogger("finley.pinecone")

SIGNALS_NAMESPACE = "global::signals"
PICKS_NAMESPACE = "global::stock_picks"


class PineconeGateway:
    def __init__(self) -> None:
        self._pc = None
        self._index = None
        self._init_error: str | None = None
        try:
            from pinecone import Pinecone

            self._pc = Pinecone(api_key=config.PINECONE_API_KEY)
            self._ensure_index()
            self._index = self._pc.Index(config.PINECONE_INDEX_NAME)
        except Exception as exc:  # pragma: no cover - depends on live Pinecone
            self._init_error = str(exc)
            logger.error("Pinecone init failed (degrading gracefully): %s", exc)

    # ── index lifecycle ──────────────────────────────────────────────────────
    def _ensure_index(self) -> None:
        """Create the serverless index if it doesn't already exist."""
        try:
            existing = [ix["name"] for ix in self._pc.list_indexes()]
        except Exception as exc:
            logger.warning("Could not list Pinecone indexes: %s", exc)
            return
        if config.PINECONE_INDEX_NAME in existing:
            return
        try:
            from pinecone import ServerlessSpec

            logger.info("Creating Pinecone index '%s' (dim=%d)", config.PINECONE_INDEX_NAME, config.EMBED_DIM)
            self._pc.create_index(
                name=config.PINECONE_INDEX_NAME,
                dimension=config.EMBED_DIM,
                metric="cosine",
                spec=ServerlessSpec(cloud="aws", region="us-east-1"),
            )
            # Wait briefly for the index to become ready.
            for _ in range(30):
                try:
                    if self._pc.describe_index(config.PINECONE_INDEX_NAME)["status"]["ready"]:
                        break
                except Exception:
                    pass
                time.sleep(1)
        except Exception as exc:
            logger.error("Pinecone index creation failed: %s", exc)

    @property
    def available(self) -> bool:
        return self._index is not None

    # ── queries ──────────────────────────────────────────────────────────────
    async def _query(self, embedding: list[float], top_k: int, namespace: str, hours_ago: int) -> list[dict]:
        if not self.available:
            return []
        cutoff = int(time.time()) - (hours_ago * 3600)
        try:
            result = await asyncio.to_thread(
                self._index.query,
                vector=embedding,
                top_k=top_k,
                namespace=namespace,
                include_metadata=True,
                filter={"timestamp_utc": {"$gte": cutoff}},
            )
            matches = result.get("matches", []) if isinstance(result, dict) else result["matches"]
            return [m["metadata"] for m in matches if m.get("metadata")]
        except Exception as exc:
            logger.error("Pinecone query failed (ns=%s): %s", namespace, exc)
            return []

    async def query_signals(self, embedding: list[float], top_k: int = 6, hours_ago: int = 48) -> list[dict]:
        """Query global::signals with a recency filter."""
        return await self._query(embedding, top_k, SIGNALS_NAMESPACE, hours_ago)

    async def query_picks(self, embedding: list[float], top_k: int = 4) -> list[dict]:
        """Query global::stock_picks, restricted to the last 6 hours."""
        return await self._query(embedding, top_k, PICKS_NAMESPACE, hours_ago=6)

    # ── upserts ──────────────────────────────────────────────────────────────
    async def _upsert(self, vector_id: str, embedding: list[float], metadata: dict, namespace: str) -> None:
        if not self.available:
            logger.warning("Pinecone unavailable; skipping upsert id=%s", vector_id)
            return
        try:
            await asyncio.to_thread(
                self._index.upsert,
                vectors=[{"id": vector_id, "values": embedding, "metadata": metadata}],
                namespace=namespace,
            )
        except Exception as exc:
            logger.error("Pinecone upsert failed (id=%s ns=%s): %s", vector_id, namespace, exc)

    async def upsert_signal(self, signal_id: str, embedding: list[float], metadata: dict) -> None:
        await self._upsert(signal_id, embedding, metadata, SIGNALS_NAMESPACE)

    async def upsert_pick(self, pick_id: str, embedding: list[float], metadata: dict) -> None:
        await self._upsert(pick_id, embedding, metadata, PICKS_NAMESPACE)


# Module-level singleton.
pinecone_gateway = PineconeGateway()
