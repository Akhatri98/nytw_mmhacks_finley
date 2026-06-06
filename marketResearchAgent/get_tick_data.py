"""
finnhub_ticker.py
-----------------
Fetches real-time and historical numeric data for a crypto ticker
using the Finnhub API.

Usage:
    pip install requests python-dotenv
    # Add FINNHUB_API_KEY to your .env

    from finnhub_ticker import get_ticker_data
    data = get_ticker_data("BTC")
    data = get_ticker_data("ETH")
"""

import os
import time
import requests
from pathlib import Path
from dotenv import load_dotenv

# Walk up from this file's location until we find a .env, then load it
_here = Path(__file__).resolve().parent
for _parent in [_here, *_here.parents]:
    _env = _parent / ".env"
    if _env.exists():
        load_dotenv(_env)
        break

FINNHUB_BASE = "https://finnhub.io/api/v1"

# Mapping of simple crypto symbols → Finnhub exchange:pair format
# Defaults to BINANCE:{SYMBOL}USDT for anything not listed here
CRYPTO_SYMBOL_MAP = {
    "BTC":  "BINANCE:BTCUSDT",
    "ETH":  "BINANCE:ETHUSDT",
    "BNB":  "BINANCE:BNBUSDT",
    "SOL":  "BINANCE:SOLUSDT",
    "XRP":  "BINANCE:XRPUSDT",
    "ADA":  "BINANCE:ADAUSDT",
    "DOGE": "BINANCE:DOGEUSDT",
    "AVAX": "BINANCE:AVAXUSDT",
    "DOT":  "BINANCE:DOTUSDT",
    "LINK": "BINANCE:LINKUSDT",
    "LTC":  "BINANCE:LTCUSDT",
    "UNI":  "BINANCE:UNIUSDT",
    "ATOM": "BINANCE:ATOMUSDT",
    "MATIC":"BINANCE:MATICUSDT",
}


def _to_finnhub_symbol(ticker: str) -> str:
    """Convert a simple symbol like 'BTC' to 'BINANCE:BTCUSDT'."""
    upper = ticker.upper().strip()
    # Already in exchange:pair format
    if ":" in upper:
        return upper
    return CRYPTO_SYMBOL_MAP.get(upper, f"BINANCE:{upper}USDT")


def get_ticker_data(ticker: str, api_key: str = None) -> dict:
    """
    Fetch real-time and historical numeric data for a crypto ticker.

    Args:
        ticker:  Simple crypto symbol, e.g. "BTC", "ETH", "SOL"
        api_key: Finnhub API key. Falls back to FINNHUB_API_KEY env var.

    Returns:
        A JSON-serialisable dict with:
            quote       – real-time price, change, OHLC
            candles_1d  – last 30 days of daily OHLCV
            candles_1h  – last 7 days of hourly OHLCV
            candles_5m  – last 24 hours of 5-minute OHLCV
            crypto_profile – exchange, base/quote currency info
        meta        – ticker, finnhub_symbol, timestamp, errors
    """

    key = api_key or os.environ.get("FINNHUB_API_KEY")
    if not key:
        raise ValueError(
            "No Finnhub API key supplied. "
            "Pass api_key= or set the FINNHUB_API_KEY environment variable."
        )

    session = requests.Session()
    session.params = {"token": key}  # type: ignore[assignment]

    raw_ticker = ticker.upper().strip()
    finnhub_symbol = _to_finnhub_symbol(raw_ticker)
    errors: list[str] = []
    now = int(time.time())

    # ── helpers ────────────────────────────────────────────────────────────────

    def get(endpoint: str, extra_params: dict = None) -> dict | list | None:
        params = extra_params or {}
        try:
            r = session.get(f"{FINNHUB_BASE}{endpoint}", params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")
            return None

    def candles(resolution: str, from_ts: int, to_ts: int) -> dict:
        raw = get("/crypto/candle", {
            "symbol":     finnhub_symbol,
            "resolution": resolution,
            "from":       from_ts,
            "to":         to_ts,
        })
        if not raw or raw.get("s") != "ok":
            return {}
        return {
            "timestamps": raw.get("t", []),
            "open":       raw.get("o", []),
            "high":       raw.get("h", []),
            "low":        raw.get("l", []),
            "close":      raw.get("c", []),
            "volume":     raw.get("v", []),
        }

    # ── real-time quote ────────────────────────────────────────────────────────
    # Finnhub crypto quote endpoint
    raw_quote = get("/quote", {"symbol": finnhub_symbol}) or {}
    quote = {
        "current_price": raw_quote.get("c"),
        "change":        raw_quote.get("d"),
        "change_pct":    raw_quote.get("dp"),
        "high_day":      raw_quote.get("h"),
        "low_day":       raw_quote.get("l"),
        "open_day":      raw_quote.get("o"),
        "prev_close":    raw_quote.get("pc"),
        "timestamp":     raw_quote.get("t"),
    }

    # ── candles (free for crypto on Finnhub) ──────────────────────────────────

    one_day    = 86_400
    candles_1d = candles("D",  now - 30 * one_day, now)   # 30 daily bars
    candles_1h = candles("60", now - 7  * one_day, now)   # 7d hourly bars
    candles_5m = candles("5",  now - 1  * one_day, now)   # 24h × 5-min bars

    # ── crypto profile (exchange, base/quote currency) ────────────────────────

    raw_exchanges = get("/crypto/exchange") or []
    # Just note which exchange we're using rather than dumping the full list
    exchange = finnhub_symbol.split(":")[0] if ":" in finnhub_symbol else "BINANCE"

    raw_profile = get("/crypto/profile", {"symbol": finnhub_symbol}) or {}
    crypto_profile = {
        "exchange":      exchange,
        "base_currency": raw_profile.get("baseCurrency"),
        "currency":      raw_profile.get("currency"),
        "description":   raw_profile.get("description"),
        "displaySymbol": raw_profile.get("displaySymbol"),
    }

    # ── assemble ──────────────────────────────────────────────────────────────

    return {
        "meta": {
            "ticker":         raw_ticker,
            "finnhub_symbol": finnhub_symbol,
            "fetched_at":     now,
            "errors":         errors,
        },
        "quote":          quote,
        "candles_1d":     candles_1d,
        "candles_1h":     candles_1h,
        "candles_5m":     candles_5m,
        "crypto_profile": crypto_profile,
    }


# ── quick CLI test ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import sys

    ticker = sys.argv[1] if len(sys.argv) > 1 else "BTC"
    print(f"Fetching data for {ticker}...\n")
    result = get_ticker_data(ticker)
    print(json.dumps(result, indent=2))