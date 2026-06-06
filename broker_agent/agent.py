"""
Finley BrokerAgent
==================

Places trades on Kraken Pro via Playwright and records them in Supabase.

── Setup ────────────────────────────────────────────────────────────────────
Copy .env.example to .env and fill in:

    SUPABASE_URL            https://<project>.supabase.co
    SUPABASE_SERVICE_ROLE_KEY   <service role key>     # bypasses RLS for writes
    SUPABASE_ANON_KEY       <anon/public key>          # used with Clerk JWT for reads
    KRAKEN_EMAIL            your-kraken-login@email.com
    KRAKEN_PASSWORD         your-kraken-password

Optional:
    SCREENSHOT_BUCKET       trade-screenshots          # Supabase Storage bucket name
    HEADLESS                false                      # set to "true" for CI/production
    BROWSER_DATA_DIR        ./browser_data             # persistent Chromium profile path

Install dependencies:
    pip install -r requirements.txt
    playwright install chromium

── Standalone test ──────────────────────────────────────────────────────────
    python -m broker_agent.agent

── 2FA caveat ───────────────────────────────────────────────────────────────
Kraken may require TOTP / U2F on the first login. The agent detects the 2FA
input and pauses up to 60 seconds, printing a console prompt so a human can
enter the code in the visible browser window.

On every subsequent call the persistent browser context (browser_data/) reuses
the saved session and skips login entirely. For CI/headless environments:
authenticate manually once with HEADLESS=false, copy the resulting browser_data/
directory to your deployment environment, and mount it at BROWSER_DATA_DIR.

── Usage ────────────────────────────────────────────────────────────────────
    from broker_agent import BrokerAgent, TradeInstruction
    from decimal import Decimal

    instruction = TradeInstruction(
        user_id="user_abc123",
        clerk_jwt="<clerk session JWT>",
        ticker="BTC",
        direction="buy",
        quantity=Decimal("0.001"),
    )
    result = await BrokerAgent().execute(instruction)
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from dotenv import load_dotenv

load_dotenv()  # ensure .env is available when broker_agent is imported standalone

from .compliance import check_compliance
from .kraken_playwright import place_order
from .models import ComplianceResult, TradeInstruction, TradeResult
from .supabase_client import service_client

logger = logging.getLogger(__name__)

SCREENSHOT_BUCKET: str = os.getenv("SCREENSHOT_BUCKET", "trade-screenshots")
SUPABASE_URL: str = os.environ["SUPABASE_URL"]


class BrokerAgent:
    """Single-method agent: await BrokerAgent().execute(instruction)."""

    async def execute(self, instruction: TradeInstruction) -> TradeResult:
        # ── 1. Pre-trade compliance check ─────────────────────────────────
        compliance: ComplianceResult = await check_compliance(instruction)

        if not compliance.approved:
            logger.warning(
                "Trade CANCELLED by compliance for user=%s %s %s. Hard blocks: %s",
                instruction.user_id,
                instruction.direction,
                instruction.ticker,
                [r.get("rule_category") for r in compliance.hard_blocks],
            )
            block_texts = "; ".join(
                r.get("rule_text", r.get("rule_category", "unknown"))
                for r in compliance.hard_blocks
            )
            return TradeResult(
                success=False,
                status="cancelled",
                compliance=compliance,
                error=f"Blocked by compliance: {block_texts}",
            )

        if compliance.warnings:
            logger.info(
                "Proceeding with %d soft warning(s) for user=%s",
                len(compliance.warnings),
                instruction.user_id,
            )

        # ── 2. Execute on Kraken via Playwright ───────────────────────────
        order_result = await place_order(
            ticker=instruction.ticker,
            direction=instruction.direction,
            quantity=instruction.quantity,
        )

        price_executed: Decimal | None = order_result["price_executed"]
        screenshot_bytes: bytes | None = order_result["screenshot_bytes"]
        order_status: str = order_result["status"]
        error: str | None = order_result.get("error")

        if order_status != "executed":
            return TradeResult(
                success=False,
                status="failed",
                compliance=compliance,
                price_executed=price_executed,
                error=error,
            )

        # ── 3. Upload screenshot ──────────────────────────────────────────
        trade_id = str(uuid.uuid4())
        screenshot_url: str | None = None

        if screenshot_bytes:
            screenshot_url = await self._upload_screenshot(
                screenshot_bytes, instruction.user_id, trade_id
            )

        # ── 4. Write trade record to Supabase ─────────────────────────────
        db_trade_id = await self._write_trade(
            instruction=instruction,
            trade_id=trade_id,
            price_executed=price_executed,
            screenshot_url=screenshot_url or "upload_failed",
            status=order_status,
        )

        logger.info(
            "Trade RECORDED trade_id=%s user=%s %s %s qty=%s price=%s",
            db_trade_id,
            instruction.user_id,
            instruction.direction,
            instruction.ticker,
            instruction.quantity,
            price_executed,
        )

        return TradeResult(
            success=True,
            trade_id=db_trade_id,
            status="executed",
            price_executed=price_executed,
            screenshot_url=screenshot_url,
            compliance=compliance,
        )

    # ── Internal helpers ──────────────────────────────────────────────────

    async def _upload_screenshot(
        self, screenshot_bytes: bytes, user_id: str, trade_id: str
    ) -> str | None:
        path = f"screenshots/{user_id}/{trade_id}.png"
        public_url = (
            f"{SUPABASE_URL}/storage/v1/object/public/{SCREENSHOT_BUCKET}/{path}"
        )
        try:
            client = await service_client()
            storage = client.storage.from_(SCREENSHOT_BUCKET)
            # supabase-py async storage: content-type goes in file_options; upsert
            # is conveyed via the "upsert" header value. Wrapped so an upload
            # failure (e.g. missing bucket) never aborts a successful trade.
            try:
                await storage.upload(
                    path=path,
                    file=screenshot_bytes,
                    file_options={"content-type": "image/png", "upsert": "true"},
                )
            except Exception as inner:
                # Fall back to overwriting via update if the object already exists.
                logger.warning(
                    "Screenshot upload retry via update (trade_id=%s): %s", trade_id, inner
                )
                await storage.update(
                    path=path,
                    file=screenshot_bytes,
                    file_options={"content-type": "image/png"},
                )
            logger.info("Screenshot uploaded → %s", public_url)
            return public_url
        except Exception as exc:
            logger.error("Screenshot upload failed (trade_id=%s): %s", trade_id, exc)
            return None

    async def _write_trade(
        self,
        instruction: TradeInstruction,
        trade_id: str,
        price_executed: Decimal | None,
        screenshot_url: str,
        status: str,
    ) -> str:
        """INSERT into trades using the service-role client (bypasses RLS)."""
        client = await service_client()

        row = {
            "id": trade_id,
            "user_id": instruction.user_id,
            # Strip "/USD" before "USD" so "ETH/USD" → "ETH" (not "ETH/").
            "ticker": instruction.ticker.upper().removesuffix("/USD").removesuffix("USD"),
            "direction": instruction.direction,
            "quantity": str(instruction.quantity),
            # price_executed is NOT NULL in the schema; use "0" if unavailable
            # (indicates price data could not be retrieved — investigate separately)
            "price_executed": str(price_executed) if price_executed is not None else "0",
            "broker": "kraken",
            "status": status,
            "screenshot_url": screenshot_url,
            "trigger_signal_id": instruction.trigger_signal_id,
            "notes": instruction.notes,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

        response = await client.table("trades").insert(row).execute()
        inserted = response.data or []
        return inserted[0]["id"] if inserted else trade_id


# ── Standalone test entry point ───────────────────────────────────────────────
if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )

    test_instruction = TradeInstruction(
        user_id="test_user_local",
        clerk_jwt=os.getenv("TEST_CLERK_JWT", ""),
        ticker="BTC",
        direction="buy",
        quantity=Decimal("0.001"),
        notes="Standalone smoke test",
    )

    async def _main() -> None:
        agent = BrokerAgent()
        result = await agent.execute(test_instruction)
        print("\n=== TradeResult ===")
        print(result.model_dump_json(indent=2))

    asyncio.run(_main())
