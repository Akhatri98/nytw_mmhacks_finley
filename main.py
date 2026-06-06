"""
Finley — FastAPI webhook server.

Endpoints:
  POST /webhook/sendblue  — inbound iMessages from SendBlue (processed in background)
  GET  /health            — health check
  POST /research/cycle    — manually trigger a market-research cycle (demo)
  GET  /demo/seed-signals — seed Pinecone with mock signals (call once before a demo)

Run:
  uvicorn main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import BackgroundTasks, FastAPI, Request

import config
from finley_agent.agent import FinleyAgent
from finley_agent.sendblue import parse_webhook
from market_research_agent.agent import MarketResearchAgent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("finley.main")

app = FastAPI(title="Finley", version="2.0")
finley = FinleyAgent()
researcher = MarketResearchAgent()

RESEARCH_INTERVAL_SECONDS = 300  # 5 minutes


@app.on_event("startup")
async def startup() -> None:
    config.log_config_summary()
    logger.info("Finley starting up. DEMO_MODE=%s", config.DEMO_MODE)
    asyncio.create_task(_research_loop())


async def _research_loop() -> None:
    """Run a market-research cycle every few minutes in the background."""
    while True:
        try:
            await researcher.run_cycle()
        except Exception as exc:  # never let the loop die
            logger.error("Research cycle error: %s", exc)
        await asyncio.sleep(RESEARCH_INTERVAL_SECONDS)


@app.post("/webhook/sendblue")
async def sendblue_webhook(request: Request, background_tasks: BackgroundTasks) -> dict:
    """Receive an inbound iMessage and dispatch it to Finley in the background.

    Returns 200 immediately so SendBlue doesn't time out; the actual handling
    (Gemini, gateway, trade) happens after the response is sent.
    """
    try:
        payload = await request.json()
    except Exception:
        return {"status": "ignored", "reason": "invalid json"}

    msg = parse_webhook(payload)

    # Ignore our own outbound copies and empty messages.
    if msg.get("is_from_me") or not msg.get("content") or not msg.get("from_number"):
        return {"status": "ok"}

    background_tasks.add_task(
        finley.handle_message,
        from_number=msg["from_number"],
        content=msg["content"],
    )
    return {"status": "ok"}


@app.post("/research/cycle")
async def trigger_research() -> dict:
    """Manually trigger a market-research cycle. For demo / testing."""
    await researcher.run_cycle()
    return {"status": "cycle complete"}


@app.get("/demo/seed-signals")
async def seed_demo_signals() -> dict:
    """Seed Pinecone with mock market signals. Call once before the demo."""
    from market_research_agent.data_sources import MOCK_SIGNALS
    from market_research_agent.pinecone_writer import write_signals_batch

    ids = await write_signals_batch(MOCK_SIGNALS)
    return {"status": "seeded", "signal_ids": ids}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "demo_mode": config.DEMO_MODE}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=config.PORT, reload=False)
