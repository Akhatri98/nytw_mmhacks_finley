"""
MarketResearchAgent — periodic ingestion + anomaly detection + proactive alerts.

A single ``run_cycle`` call:
  1. Fetches price signals (CoinGecko, with mock fallback).
  2. Writes every signal to Pinecone (global::signals).
  3. Detects anomalies (>5% price move vs last cycle, or a strong first-seen move).
  4. Asks Gemini for a directional pick per anomaly and writes it to Pinecone.
  5. Alerts all users via SendBlue and logs each alert to Supabase.

Designed to be called on a loop (see ``main._research_loop``) or on demand.
Every step is fault-tolerant; one failure never aborts the whole cycle.
"""

from __future__ import annotations

import json
import logging
import re

from broker_agent.supabase_client import service_client
from finley_agent import sendblue
from finley_agent.gemini_client import generate_response
from market_research_agent.data_sources import MarketSignal, fetch_price_signals
from market_research_agent.pinecone_writer import generate_and_write_pick, write_signals_batch

logger = logging.getLogger("finley.research")

ANOMALY_THRESHOLD = 0.05  # 5% price move vs last cycle = anomaly worth alerting on
STRONG_MOVE_SENTIMENT = 0.5  # first-seen ticker with |sentiment| >= this is an anomaly

_PICK_SYSTEM_PROMPT = (
    "You are a crypto research analyst. Given a single market signal, decide a "
    "directional view. Output STRICT JSON only, no prose, in exactly this shape: "
    '{"direction": "long|short|watch", "rationale": "<one concise sentence>", '
    '"confidence": <number between 0 and 1>}'
)


class MarketResearchAgent:
    def __init__(self) -> None:
        self._last_prices: dict[str, float] = {}

    async def run_cycle(self) -> None:
        logger.info("Market research cycle starting…")
        try:
            signals = await fetch_price_signals()
        except Exception as exc:
            logger.error("Signal fetch failed; aborting cycle: %s", exc)
            return

        try:
            await write_signals_batch(signals)
        except Exception as exc:
            logger.error("Writing signals to Pinecone failed: %s", exc)

        anomalies = self._detect_anomalies(signals)
        logger.info("Cycle: %d signals, %d anomalies", len(signals), len(anomalies))

        for signal in anomalies:
            try:
                direction, rationale, confidence = await self._generate_pick_for_anomaly(
                    signal.ticker, signal
                )
                signal_id = f"signal_{signal.ticker}_{signal.timestamp_utc}"
                pick_id = await generate_and_write_pick(
                    ticker=signal.ticker,
                    direction=direction,
                    rationale=rationale,
                    signal_ids=[signal_id],
                    confidence=confidence,
                )
                await self._alert_users(signal.ticker, signal, pick_id)
            except Exception as exc:
                logger.error("Anomaly handling failed for %s: %s", signal.ticker, exc)

        logger.info("Market research cycle complete.")

    # ── anomaly detection ──────────────────────────────────────────────────────
    def _detect_anomalies(self, signals: list[MarketSignal]) -> list[MarketSignal]:
        anomalies: list[MarketSignal] = []
        for s in signals:
            if s.price_at_signal is None:
                continue
            last = self._last_prices.get(s.ticker)
            if last and last > 0:
                move = abs(s.price_at_signal - last) / last
                if move >= ANOMALY_THRESHOLD:
                    anomalies.append(s)
            elif s.sentiment_score is not None and abs(s.sentiment_score) >= STRONG_MOVE_SENTIMENT:
                # First time we've seen this ticker and it's already moving hard.
                anomalies.append(s)
            self._last_prices[s.ticker] = s.price_at_signal
        return anomalies

    # ── Gemini pick generation ─────────────────────────────────────────────────
    async def _generate_pick_for_anomaly(self, ticker: str, signal: MarketSignal) -> tuple[str, str, float]:
        user_message = (
            f"Ticker: {ticker}\n"
            f"Signal: {signal.content}\n"
            f"Sentiment score: {signal.sentiment_score}\n"
            f"Source: {signal.source}\n"
            "Return your directional call as strict JSON."
        )
        text = await generate_response(
            system_prompt=_PICK_SYSTEM_PROMPT,
            conversation_history=[],
            user_message=user_message,
            context=None,
        )
        parsed = self._parse_pick_json(text)
        if parsed:
            return parsed

        # Fallback: derive a view directly from the sentiment.
        score = signal.sentiment_score or 0.0
        direction = "long" if score > 0.15 else "short" if score < -0.15 else "watch"
        return direction, signal.content, round(min(0.9, max(0.4, abs(score) + 0.4)), 2)

    @staticmethod
    def _parse_pick_json(text: str) -> tuple[str, str, float] | None:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
            direction = str(data.get("direction", "watch")).lower()
            if direction not in {"long", "short", "watch"}:
                direction = "watch"
            rationale = str(data.get("rationale", "")).strip() or "No rationale provided."
            confidence = float(data.get("confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))
            return direction, rationale, round(confidence, 2)
        except (json.JSONDecodeError, ValueError, TypeError):
            return None

    # ── proactive alerts ───────────────────────────────────────────────────────
    async def _alert_users(self, ticker: str, signal: MarketSignal, pick_id: str) -> None:
        try:
            client = await service_client()
            resp = await client.table("users").select("clerk_user_id, phone_number").execute()
            users = resp.data or []
        except Exception as exc:
            logger.error("Could not load users for alerts: %s", exc)
            return

        change_pct = (signal.sentiment_score or 0.0) * 10.0  # inverse of sentiment scaling
        alert_type = "spike" if change_pct >= 0 else "dip"
        price_str = f"${signal.price_at_signal:,.2f}" if signal.price_at_signal else "market price"
        brief = signal.content.split(".")[0]
        message = (
            f"📊 {ticker} alert: {brief}. "
            f"Price: {price_str} ({change_pct:+.1f}%). "
            f"My read: {signal.sentiment_label()}."
        )

        for user in users:
            phone = user.get("phone_number")
            user_id = user.get("clerk_user_id")
            if not phone:
                continue
            await sendblue.send_message(phone, message, send_style="invisible")
            try:
                await client.table("alerts").insert({
                    "user_id": user_id,
                    "ticker": ticker,
                    "alert_type": alert_type,
                    "message_sent": message,
                    "signal_id": pick_id,
                }).execute()
            except Exception as exc:
                logger.error("Alert log insert failed for %s: %s", user_id, exc)
