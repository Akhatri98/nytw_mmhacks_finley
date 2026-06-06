from typing import NamedTuple


class KrakenPair(NamedTuple):
    display: str   # "XBT/USD"   — human-readable
    url_slug: str  # "XBT-USD"   — used in pro.kraken.com/app/trade/<slug>
    api_pair: str  # "XBTUSD"    — used in Kraken public REST API ?pair=


# Expand this dict as new assets are needed.
TICKER_MAP: dict[str, KrakenPair] = {
    "BTC":   KrakenPair("XBT/USD",   "XBT-USD",   "XBTUSD"),
    "XBT":   KrakenPair("XBT/USD",   "XBT-USD",   "XBTUSD"),
    "ETH":   KrakenPair("ETH/USD",   "ETH-USD",   "ETHUSD"),
    "SOL":   KrakenPair("SOL/USD",   "SOL-USD",   "SOLUSD"),
    "XRP":   KrakenPair("XRP/USD",   "XRP-USD",   "XRPUSD"),
    "ADA":   KrakenPair("ADA/USD",   "ADA-USD",   "ADAUSD"),
    "DOGE":  KrakenPair("DOGE/USD",  "DOGE-USD",  "DOGEUSD"),
    "DOT":   KrakenPair("DOT/USD",   "DOT-USD",   "DOTUSD"),
    "AVAX":  KrakenPair("AVAX/USD",  "AVAX-USD",  "AVAXUSD"),
    "LINK":  KrakenPair("LINK/USD",  "LINK-USD",  "LINKUSD"),
    "MATIC": KrakenPair("MATIC/USD", "MATIC-USD", "MATICUSD"),
    "LTC":   KrakenPair("LTC/USD",   "LTC-USD",   "LTCUSD"),
    "UNI":   KrakenPair("UNI/USD",   "UNI-USD",   "UNIUSD"),
    "ATOM":  KrakenPair("ATOM/USD",  "ATOM-USD",  "ATOMUSD"),
    "NEAR":  KrakenPair("NEAR/USD",  "NEAR-USD",  "NEARUSD"),
    "APT":   KrakenPair("APT/USD",   "APT-USD",   "APTUSD"),
    "ARB":   KrakenPair("ARB/USD",   "ARB-USD",   "ARBUSD"),
    "OP":    KrakenPair("OP/USD",    "OP-USD",    "OPUSD"),
}


def to_kraken_pair(ticker: str) -> KrakenPair:
    """Normalise a caller-supplied ticker to a KrakenPair.

    Accepts "BTC", "BTCUSD", "BTC/USD" — all map to the same pair.
    Raises ValueError for unknown tickers so callers know to add an entry.
    """
    normalised = (
        ticker.upper()
        .removesuffix("USD")
        .removesuffix("/USD")
        .strip()
    )
    pair = TICKER_MAP.get(normalised)
    if pair is None:
        raise ValueError(
            f"Unsupported ticker '{ticker}' (normalised: '{normalised}'). "
            "Add an entry to TICKER_MAP in broker_agent/ticker_map.py."
        )
    return pair
