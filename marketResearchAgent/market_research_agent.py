"""
market_research_agent.py
------------------------
Orchestrates all three RSS readers (Bloomberg, Reddit, Yahoo Finance),
extracts crypto tickers via Gemini 2.5 Pro, fetches live tick data,
and returns a fixed-format JSON trade-analysis object.

Usage (interactive CLI):
    python market_research_agent.py

Usage (as a module):
    from marketResearchAgent.market_research_agent import run
    result = run()   # returns dict
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import google.generativeai as genai
from dotenv import load_dotenv

# ── load .env ─────────────────────────────────────────────────────────────────
_here = Path(__file__).resolve().parent
for _parent in [_here, *_here.parents]:
    _env = _parent / ".env"
    if _env.exists():
        load_dotenv(_env)
        break

# ── sibling imports ───────────────────────────────────────────────────────────
sys.path.insert(0, str(_here))
import bloomberg_RSS_reader as bloomberg
import reddit_RSS_reader as reddit
import yahoo_RSS_reader as yahoo
from get_tick_data import get_ticker_data

# ── Gemini setup ──────────────────────────────────────────────────────────────
_API_KEY = os.environ.get("AI_STUDIO_API_KEY")
if not _API_KEY:
    raise EnvironmentError(
        "AI_STUDIO_API_KEY not found. Add it to your .env file."
    )
genai.configure(api_key=_API_KEY)
_MODEL = genai.GenerativeModel("gemini-2.5-pro")

# ── fixed output schema ───────────────────────────────────────────────────────
TRADE_SCHEMA = {
    "generated_at": "<ISO-8601 UTC timestamp>",
    "tickers_analyzed": ["<SYMBOL>"],
    "market_summary": "<one-paragraph macro summary>",
    "data_sources": {
        "bloomberg": True,
        "reddit": True,
        "yahoo_finance": True,
    },
    "trades": [
        {
            "ticker": "<SYMBOL>",
            "action": "<BUY|SELL|HOLD|WATCH>",
            "confidence": 0.0,
            "sentiment": "<BULLISH|BEARISH|NEUTRAL>",
            "current_price": 0.0,
            "price_change_pct_24h": 0.0,
            "rationale": "<concise trade rationale>",
            "key_drivers": ["<driver 1>", "<driver 2>"],
            "risk_factors": ["<risk 1>", "<risk 2>"],
            "time_horizon": "<SHORT|MEDIUM|LONG>",
            "news_sources": ["<Bloomberg|Reddit|Yahoo Finance>"],
        }
    ],
}


# ── helpers ───────────────────────────────────────────────────────────────────

def _prompt_urls(source_name: str, defaults: list[str]) -> list[str]:
    """
    Ask the user if they want to use default URLs or supply custom ones.
    Returns the final list of URLs to use.
    """
    print(f"\n{'─'*60}")
    print(f"  {source_name} RSS Reader")
    print(f"{'─'*60}")
    print("Default URLs:")
    for i, u in enumerate(defaults, 1):
        print(f"  {i}. {u}")

    choice = input(
        "\nUse defaults? [Y/n] or paste comma-separated custom URLs: "
    ).strip()

    if not choice or choice.lower() in ("y", "yes"):
        return defaults

    # User typed something other than Y — treat as custom URLs
    custom = [u.strip() for u in choice.split(",") if u.strip()]
    return custom if custom else defaults


def _extract_tickers_via_gemini(rss_text: str) -> list[str]:
    """
    Ask Gemini to extract unique crypto ticker symbols from the RSS feed text.
    Returns a deduplicated list of uppercase symbols.
    """
    prompt = f"""You are a financial data extractor.

Below is aggregated news text from Bloomberg, Reddit, and Yahoo Finance RSS feeds.
Extract every distinct crypto-currency ticker symbol mentioned (e.g. BTC, ETH, SOL).
Return ONLY a JSON array of uppercase symbol strings — no explanation, no markdown.

Example output: ["BTC","ETH","SOL"]

NEWS TEXT:
{rss_text[:40000]}
"""
    response = _MODEL.generate_content(prompt)
    raw = response.text.strip()

    # Strip any accidental markdown fences
    if raw.startswith("```"):
        raw = "\n".join(
            line for line in raw.splitlines()
            if not line.strip().startswith("```")
        ).strip()

    try:
        tickers = json.loads(raw)
        if isinstance(tickers, list):
            return list(dict.fromkeys(t.upper() for t in tickers if isinstance(t, str)))
    except json.JSONDecodeError:
        pass

    # Fallback: split on whitespace/commas if Gemini returned something non-JSON
    import re
    found = re.findall(r'\b[A-Z]{2,10}\b', raw)
    return list(dict.fromkeys(found))


def _build_trade_analysis(rss_text: str, tick_data: dict[str, dict]) -> dict:
    """
    Ask Gemini to synthesise a trade-analysis JSON object using the
    RSS context and the live tick data for each ticker.
    """
    tick_summary = json.dumps(tick_data, indent=2)

    prompt = f"""You are an expert crypto market analyst.

You have been given:
1. Aggregated news from Bloomberg, Reddit, and Yahoo Finance (below).
2. Live tick data for the relevant tickers (below).

Produce a JSON trade-analysis report that EXACTLY matches this schema
(fill in all fields; use null where data is unavailable):

{json.dumps(TRADE_SCHEMA, indent=2)}

Rules:
- "generated_at": use ISO-8601 UTC: {datetime.now(timezone.utc).isoformat()}
- "action": one of BUY, SELL, HOLD, WATCH
- "confidence": float 0.0–1.0
- "sentiment": one of BULLISH, BEARISH, NEUTRAL
- "time_horizon": one of SHORT (< 1 week), MEDIUM (1 week–3 months), LONG (> 3 months)
- "trades": one object per ticker in "tickers_analyzed"
- Return ONLY the raw JSON object — no markdown fences, no explanation.

─── LIVE TICK DATA ───────────────────────────────────────────────────────────
{tick_summary[:20000]}

─── NEWS CONTEXT ─────────────────────────────────────────────────────────────
{rss_text[:30000]}
"""

    response = _MODEL.generate_content(prompt)
    raw = response.text.strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = "\n".join(
            line for line in raw.splitlines()
            if not line.strip().startswith("```")
        ).strip()

    return json.loads(raw)


# ── main orchestrator ─────────────────────────────────────────────────────────

def run(
    bloomberg_urls: list[str] = None,
    reddit_urls: list[str] = None,
    yahoo_urls: list[str] = None,
    max_items: int = 15,
    interactive: bool = True,
) -> dict:
    """
    Full pipeline: RSS → ticker extraction → tick data → trade analysis JSON.

    Args:
        bloomberg_urls: Override Bloomberg RSS URLs (None = prompt/use defaults).
        reddit_urls:    Override Reddit RSS URLs (None = prompt/use defaults).
        yahoo_urls:     Override Yahoo Finance RSS URLs (None = prompt/use defaults).
        max_items:      Max articles/posts per feed URL.
        interactive:    If True, prompt the user for URL choices in the terminal.

    Returns:
        A dict conforming to TRADE_SCHEMA.
    """

    # ── 1. Gather RSS URLs ────────────────────────────────────────────────────
    if interactive:
        b_urls = _prompt_urls("Bloomberg",     bloomberg_urls or bloomberg.URLS)
        r_urls = _prompt_urls("Reddit",        reddit_urls    or reddit.URLS)
        y_urls = _prompt_urls("Yahoo Finance", yahoo_urls     or yahoo.URLS)
    else:
        b_urls = bloomberg_urls or bloomberg.URLS
        r_urls = reddit_urls    or reddit.URLS
        y_urls = yahoo_urls     or yahoo.URLS

    # ── 2. Fetch RSS feeds ────────────────────────────────────────────────────
    print("\n[1/4] Fetching RSS feeds …")

    bloomberg_text = bloomberg.fetch_feed(urls=b_urls, max_items=max_items)
    print(f"      Bloomberg  : {len(bloomberg_text):,} chars")

    reddit_text = reddit.fetch_feed(urls=r_urls, max_items=max_items)
    print(f"      Reddit     : {len(reddit_text):,} chars")

    yahoo_text = yahoo.fetch_feed(urls=y_urls, max_items=max_items)
    print(f"      Yahoo Fin. : {len(yahoo_text):,} chars")

    combined_rss = "\n\n===BLOOMBERG===\n\n" + bloomberg_text \
                 + "\n\n===REDDIT===\n\n"    + reddit_text    \
                 + "\n\n===YAHOO FINANCE===\n\n" + yahoo_text

    # ── 3. Extract tickers via Gemini ─────────────────────────────────────────
    print("\n[2/4] Extracting tickers with Gemini 2.5 Pro …")
    tickers = _extract_tickers_via_gemini(combined_rss)
    if not tickers:
        print("      No tickers found — returning empty report.")
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "tickers_analyzed": [],
            "market_summary": "No tickers were identified in the current RSS feeds.",
            "data_sources": {"bloomberg": True, "reddit": True, "yahoo_finance": True},
            "trades": [],
        }
    print(f"      Found: {tickers}")

    # ── 4. Fetch live tick data ───────────────────────────────────────────────
    print("\n[3/4] Fetching live tick data …")
    tick_data: dict[str, dict] = {}
    for ticker in tickers:
        try:
            tick_data[ticker] = get_ticker_data(ticker)
            price = tick_data[ticker].get("quote", {}).get("current_price")
            print(f"      {ticker:>8}  price={price}")
        except Exception as exc:
            print(f"      {ticker:>8}  ERROR: {exc}")
            tick_data[ticker] = {"error": str(exc)}

    # ── 5. Build trade analysis via Gemini ────────────────────────────────────
    print("\n[4/4] Generating trade analysis with Gemini 2.5 Pro …")
    analysis = _build_trade_analysis(combined_rss, tick_data)
    print("      Done.")

    return analysis


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    result = run(interactive=True)
    print("\n" + "═" * 60)
    print("TRADE ANALYSIS REPORT")
    print("═" * 60)
    print(json.dumps(result, indent=2))
