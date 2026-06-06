"""
Market data sources for the Market Research Agent.

Primary source: CoinGecko's free public API (no key required). On any error or
rate-limit, we fall back to ``MOCK_SIGNALS`` so the demo always has data to work
with. All HTTP is async via httpx.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger("finley.data_sources")

COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# Our tickers → CoinGecko coin IDs.
COINGECKO_IDS: dict[str, str] = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "XRP": "ripple", "ADA": "cardano", "DOGE": "dogecoin",
    "DOT": "polkadot", "AVAX": "avalanche-2", "LINK": "chainlink",
    "MATIC": "matic-network", "LTC": "litecoin", "UNI": "uniswap",
    "ATOM": "cosmos", "NEAR": "near", "ARB": "arbitrum",
}
_ID_TO_TICKER = {v: k for k, v in COINGECKO_IDS.items()}

DEFAULT_TICKERS = ["BTC", "ETH", "SOL", "XRP", "DOGE"]


@dataclass
class MarketSignal:
    ticker: str
    data_type: str  # "tick_summary" | "news_digest" | "social_sentiment"
    source: str
    content: str  # text to embed
    sentiment_score: float | None
    confidence: float
    price_at_signal: float | None
    timestamp_utc: int

    def sentiment_label(self) -> str:
        if self.sentiment_score is None:
            return "neutral"
        if self.sentiment_score > 0.15:
            return "bullish"
        if self.sentiment_score < -0.15:
            return "bearish"
        return "neutral"


def _fmt_usd(value: float) -> str:
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.1f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1:
        return f"${value:,.2f}"
    return f"${value:.4f}"


async def fetch_price_signals(tickers: list[str] | None = None) -> list[MarketSignal]:
    """Fetch current price + 24h change/volume from CoinGecko and build tick signals."""
    tickers = tickers or DEFAULT_TICKERS
    ids = [COINGECKO_IDS[t] for t in tickers if t in COINGECKO_IDS]
    if not ids:
        return list(MOCK_SIGNALS)

    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.get(
                f"{COINGECKO_BASE}/simple/price",
                params={
                    "ids": ",".join(ids),
                    "vs_currencies": "usd",
                    "include_24hr_change": "true",
                    "include_24hr_vol": "true",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("CoinGecko price fetch failed (using mocks): %s", exc)
        return list(MOCK_SIGNALS)

    now = int(time.time())
    signals: list[MarketSignal] = []
    for coin_id, payload in data.items():
        ticker = _ID_TO_TICKER.get(coin_id, coin_id.upper())
        price = float(payload.get("usd", 0) or 0)
        change = float(payload.get("usd_24h_change", 0) or 0)
        volume = float(payload.get("usd_24h_vol", 0) or 0)
        sentiment = max(-1.0, min(1.0, change / 10.0))  # ±10% maps to ±1.0
        content = (
            f"{ticker} price {_fmt_usd(price)}. "
            f"{change:+.2f}% in 24h. Volume: {_fmt_usd(volume)}."
        )
        signals.append(
            MarketSignal(
                ticker=ticker,
                data_type="tick_summary",
                source="coingecko",
                content=content,
                sentiment_score=round(sentiment, 3),
                confidence=0.6,
                price_at_signal=price,
                timestamp_utc=now,
            )
        )

    return signals or list(MOCK_SIGNALS)


async def fetch_trending_signals() -> list[MarketSignal]:
    """Fetch trending coins from CoinGecko and build social_sentiment signals."""
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.get(f"{COINGECKO_BASE}/search/trending")
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("CoinGecko trending fetch failed: %s", exc)
        return []

    now = int(time.time())
    signals: list[MarketSignal] = []
    for item in (data.get("coins") or [])[:7]:
        coin = item.get("item", {})
        symbol = (coin.get("symbol") or "").upper()
        name = coin.get("name", symbol)
        rank = coin.get("market_cap_rank")
        if not symbol:
            continue
        content = (
            f"{name} ({symbol}) is trending on CoinGecko search"
            + (f", market-cap rank #{rank}." if rank else ".")
            + " Elevated retail attention and social interest."
        )
        signals.append(
            MarketSignal(
                ticker=symbol,
                data_type="social_sentiment",
                source="coingecko_trending",
                content=content,
                sentiment_score=0.4,
                confidence=0.45,
                price_at_signal=None,
                timestamp_utc=now,
            )
        )
    return signals


# ── Mock fallback (always available for the demo) ────────────────────────────
_NOW = int(time.time())
MOCK_SIGNALS: list[MarketSignal] = [
    MarketSignal("BTC", "tick_summary", "coingecko",
                 "BTC price $62,450.00. +3.20% in 24h. Volume: $28.4B.",
                 0.32, 0.7, 62450.0, _NOW),
    MarketSignal("ETH", "tick_summary", "coingecko",
                 "ETH price $3,420.00. +1.80% in 24h. Volume: $14.1B.",
                 0.18, 0.65, 3420.0, _NOW),
    MarketSignal("SOL", "tick_summary", "coingecko",
                 "SOL price $148.20. -2.40% in 24h. Volume: $3.8B.",
                 -0.24, 0.62, 148.20, _NOW),
    MarketSignal("XRP", "social_sentiment", "coingecko_trending",
                 "Ripple (XRP) is trending on CoinGecko search. Elevated retail attention.",
                 0.40, 0.45, 0.62, _NOW),
    MarketSignal("DOGE", "social_sentiment", "coingecko_trending",
                 "Dogecoin (DOGE) trending after a viral social post. Momentum building.",
                 0.55, 0.4, 0.16, _NOW),
]
