"""
In-memory conversation + pending-trade store.

Demo-grade (no Redis): a process-local dict keyed by phone number, guarded by an
asyncio lock. Holds Gemini-format chat history plus the single in-flight D.Ask
trade awaiting YES/NO confirmation (with a TTL).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ConversationState:
    history: list[dict] = field(default_factory=list)  # Gemini message format
    pending_trade: dict | None = None  # set during the D.Ask flow
    pending_trade_expires: datetime | None = None
    last_active: datetime = field(default_factory=_now)


class ConversationStore:
    """Thread-safe (asyncio) in-memory store keyed by phone number."""

    def __init__(self) -> None:
        self._store: dict[str, ConversationState] = {}
        self._lock = asyncio.Lock()

    async def get(self, phone: str) -> ConversationState:
        async with self._lock:
            state = self._store.get(phone)
            if state is None:
                state = ConversationState()
                self._store[phone] = state
            state.last_active = _now()
            return state

    async def set_pending_trade(self, phone: str, trade: dict, ttl_seconds: int = 120) -> None:
        async with self._lock:
            state = self._store.setdefault(phone, ConversationState())
            state.pending_trade = trade
            state.pending_trade_expires = _now() + timedelta(seconds=ttl_seconds)
            state.last_active = _now()

    async def consume_pending_trade(self, phone: str) -> dict | None:
        """Return and clear the pending trade if present and not expired."""
        async with self._lock:
            state = self._store.get(phone)
            if not state or state.pending_trade is None:
                return None
            expires = state.pending_trade_expires
            trade = state.pending_trade
            state.pending_trade = None
            state.pending_trade_expires = None
            if expires is not None and _now() > expires:
                return None  # expired — treat as no pending trade
            return trade

    async def peek_pending_trade(self, phone: str) -> dict | None:
        """Return the pending trade without consuming it (None if expired/absent)."""
        async with self._lock:
            state = self._store.get(phone)
            if not state or state.pending_trade is None:
                return None
            if state.pending_trade_expires and _now() > state.pending_trade_expires:
                return None
            return state.pending_trade

    async def add_message(self, phone: str, role: str, text: str) -> None:
        """Append a message in Gemini format ({"role", "parts"})."""
        async with self._lock:
            state = self._store.setdefault(phone, ConversationState())
            state.history.append({"role": role, "parts": [text]})
            # Keep history bounded.
            if len(state.history) > 20:
                state.history = state.history[-20:]
            state.last_active = _now()

    async def clear_history(self, phone: str) -> None:
        async with self._lock:
            state = self._store.get(phone)
            if state:
                state.history = []
                state.pending_trade = None
                state.pending_trade_expires = None


conversation_store = ConversationStore()
