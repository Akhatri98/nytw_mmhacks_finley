"""Kraken Pro order placement via Playwright browser automation.

All CSS/role selectors are marked with:
    # selector — verify against live UI if it breaks
so they're easy to grep and patch if Kraken's front-end changes.
"""
import asyncio
import logging
import os
from decimal import Decimal
from pathlib import Path

import httpx
from playwright.async_api import (
    BrowserContext,
    Page,
    async_playwright,
)
from playwright.async_api import TimeoutError as PWTimeout

from .ticker_map import KrakenPair, to_kraken_pair

logger = logging.getLogger(__name__)

# ── Configuration from env ──────────────────────────────────────────────────
KRAKEN_EMAIL: str = os.environ["KRAKEN_EMAIL"]
KRAKEN_PASSWORD: str = os.environ["KRAKEN_PASSWORD"]
HEADLESS: bool = os.getenv("HEADLESS", "false").lower() == "true"
USER_DATA_DIR: str = os.getenv(
    "BROWSER_DATA_DIR",
    str(Path(__file__).parent.parent / "browser_data"),
)
TWO_FA_TIMEOUT_MS: int = 60_000  # 60 s for manual 2FA entry


# ── Kraken REST API fallback ─────────────────────────────────────────────────

async def _fetch_last_price_rest(api_pair: str) -> Decimal | None:
    """GET last trade price from Kraken's public REST ticker endpoint."""
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.get(
                "https://api.kraken.com/0/public/Ticker",
                params={"pair": api_pair},
            )
            data = r.json()
            if data.get("error"):
                logger.warning("Kraken REST error for %s: %s", api_pair, data["error"])
                return None
            result = data.get("result", {})
            if not result:
                return None
            first_pair = next(iter(result))  # e.g. "XXBTZUSD"
            last_price_str: str = result[first_pair]["c"][0]  # "c" = [last_trade_price, lot_volume]
            return Decimal(last_price_str)
    except Exception as exc:
        logger.warning("Kraken REST price fetch failed: %s", exc)
        return None


# ── Login helpers ─────────────────────────────────────────────────────────────

async def _is_signed_in(page: Page) -> bool:
    """Return True if Kraken Pro shows an authenticated account header."""
    try:
        # selector — verify against live UI if it breaks
        await page.wait_for_selector(
            '[data-testid="header-account-button"], '
            '[aria-label="Account menu"], '
            '.account-menu-trigger, '
            'button[class*="AccountMenu"]',
            timeout=5_000,
        )
        return True
    except PWTimeout:
        return False


async def _perform_login(page: Page) -> None:
    """Click Sign In, fill credentials, handle optional 2FA."""
    logger.info("Initiating Kraken Pro login…")

    # Click the sign-in entry point in the header
    # selector — verify against live UI if it breaks
    await page.click(
        'a[href*="sign-in"], '
        'button:has-text("Sign in"), '
        'button:has-text("Sign In"), '
        '[data-testid="sign-in-button"], '
        '[data-testid="header-sign-in"]',
        timeout=15_000,
    )

    # Fill email / username
    # selector — verify against live UI if it breaks
    email_selector = (
        'input[name="username"], '
        'input[type="email"], '
        'input[placeholder*="email" i], '
        'input[placeholder*="username" i]'
    )
    await page.wait_for_selector(email_selector, timeout=15_000)
    await page.fill(email_selector, KRAKEN_EMAIL)

    # Fill password
    # selector — verify against live UI if it breaks
    await page.fill('input[name="password"], input[type="password"]', KRAKEN_PASSWORD)

    # Submit the login form
    # selector — verify against live UI if it breaks
    await page.click('button[type="submit"]', timeout=10_000)

    # Handle optional 2FA
    two_fa_selector = (
        'input[name="otp"], '
        'input[name="totp"], '
        'input[name="2fa"], '
        'input[placeholder*="code" i], '
        'input[placeholder*="2FA" i], '
        'input[placeholder*="authenticator" i]'
    )
    try:
        # selector — verify against live UI if it breaks
        await page.wait_for_selector(two_fa_selector, timeout=8_000)
        print(
            "\n[Finley BrokerAgent] *** 2FA REQUIRED ***\n"
            "Enter your Kraken authentication code in the browser window.\n"
            f"Waiting up to {TWO_FA_TIMEOUT_MS // 1_000}s…\n"
        )
        # Wait for the 2FA field to disappear (user submitted the code)
        # selector — verify against live UI if it breaks
        await page.wait_for_selector(two_fa_selector, state="hidden", timeout=TWO_FA_TIMEOUT_MS)
    except PWTimeout:
        # No 2FA prompt appeared — continue to authenticated-state check
        pass

    # Confirm we are now signed in
    # selector — verify against live UI if it breaks
    await page.wait_for_selector(
        '[data-testid="header-account-button"], '
        '[aria-label="Account menu"], '
        '.account-menu-trigger, '
        'button[class*="AccountMenu"], '
        'nav:has-text("Trade")',
        timeout=25_000,
    )
    logger.info("Kraken Pro login successful")


# ── Order placement ───────────────────────────────────────────────────────────

async def place_order(
    ticker: str,
    direction: str,
    quantity: Decimal,
) -> dict:
    """Place a market order on Kraken Pro via Playwright.

    Returns a dict:
        {
            "price_executed": Decimal | None,
            "screenshot_bytes": bytes | None,
            "status": "executed" | "failed",
            "error": str | None,
        }
    """
    pair: KrakenPair = to_kraken_pair(ticker)
    trade_url = f"https://pro.kraken.com/app/trade/{pair.url_slug}"
    side_text = "Buy" if direction == "buy" else "Sell"

    # Ensure browser_data dir exists for persistent context
    Path(USER_DATA_DIR).mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        # Persistent context reuses the saved login session across calls
        context: BrowserContext = await p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=HEADLESS,
            viewport={"width": 1440, "height": 900},
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page: Page = await context.new_page()
        screenshot_bytes: bytes | None = None
        price_executed: Decimal | None = None

        try:
            # ── Step 1: Navigate to the trading pair ──────────────────────
            logger.info("Navigating to %s", trade_url)
            await page.goto(trade_url, wait_until="domcontentloaded", timeout=30_000)

            # ── Step 2: Login if needed ───────────────────────────────────
            if not await _is_signed_in(page):
                await _perform_login(page)
                # Re-navigate after login (may have been redirected)
                if pair.url_slug.lower() not in page.url.lower():
                    await page.goto(trade_url, wait_until="domcontentloaded", timeout=30_000)

            # ── Step 3: Wait for the order form panel ─────────────────────
            # selector — verify against live UI if it breaks
            await page.wait_for_selector(
                '[data-testid="order-form"], '
                'form[class*="rder"], '
                '[class*="OrderPanel"], '
                '[class*="order-panel"], '
                'section[aria-label*="order" i]',
                timeout=20_000,
            )

            # ── Step 4: Select Buy or Sell tab ────────────────────────────
            # selector — verify against live UI if it breaks
            await page.click(
                f'[data-testid="{direction}-tab"], '
                f'button[data-side="{direction}"], '
                f'[role="tab"]:has-text("{side_text}"), '
                f'button[class*="BuySell"]:has-text("{side_text}"), '
                f'button:has-text("{side_text}")',
                timeout=10_000,
            )
            await asyncio.sleep(0.4)

            # ── Step 5: Select Market order type ─────────────────────────
            # selector — verify against live UI if it breaks
            try:
                await page.click(
                    'button:has-text("Market"), '
                    '[data-order-type="market"], '
                    '[data-testid="order-type-market"], '
                    '[role="option"]:has-text("Market")',
                    timeout=5_000,
                )
                await asyncio.sleep(0.3)
            except PWTimeout:
                logger.warning("Market order-type selector timed out — assuming Market is already selected")

            # ── Step 6: Enter quantity ────────────────────────────────────
            # selector — verify against live UI if it breaks
            qty_input = await page.wait_for_selector(
                'input[name="quantity"], '
                'input[name="amount"], '
                'input[name="orderQty"], '
                'input[name="qty"], '
                'input[placeholder*="quantity" i], '
                'input[placeholder*="amount" i], '
                'input[data-testid="quantity-input"], '
                'input[data-testid="amount-input"]',
                timeout=10_000,
            )
            await qty_input.triple_click()          # clear existing value
            await qty_input.type(str(quantity), delay=40)
            await asyncio.sleep(0.3)

            # ── Step 7: Submit the order ──────────────────────────────────
            # selector — verify against live UI if it breaks
            await page.click(
                f'button:has-text("Place {side_text} Order"), '
                f'button:has-text("{side_text} Now"), '
                f'button[data-testid="place-order-button"], '
                f'button[data-testid="submit-order"], '
                f'button[type="submit"]:has-text("{side_text}")',
                timeout=10_000,
            )

            # ── Step 8: Wait for confirmation ─────────────────────────────
            # selector — verify against live UI if it breaks
            try:
                await page.wait_for_selector(
                    '[data-testid="order-confirmation"], '
                    '[data-testid="order-success"], '
                    '.order-confirmation, '
                    '[class*="OrderConfirmation"], '
                    ':has-text("Order placed"), '
                    ':has-text("Order submitted"), '
                    ':has-text("successfully")',
                    timeout=15_000,
                )
                logger.info("Order confirmation banner detected")
            except PWTimeout:
                logger.warning("Confirmation selector timed out — order may still have executed; proceeding")

            await asyncio.sleep(1.2)  # allow UI to settle before screenshot

            # ── Step 9: Full-page screenshot ──────────────────────────────
            screenshot_bytes = await page.screenshot(full_page=True)

            # ── Step 10: Extract executed price ───────────────────────────
            # Try to read the fill price from the confirmation UI first.
            # selector — verify against live UI if it breaks
            try:
                price_el = await page.query_selector(
                    '[data-testid="execution-price"], '
                    '[data-testid="fill-price"], '
                    '[class*="execPrice"], '
                    '[class*="fillPrice"], '
                    '[class*="execution-price"]'
                )
                if price_el:
                    raw = await price_el.inner_text()
                    cleaned = raw.replace("$", "").replace(",", "").strip()
                    price_executed = Decimal(cleaned)
                    logger.info("Extracted execution price from page: %s", price_executed)
            except Exception as exc:
                logger.debug("Could not extract price from confirmation UI: %s", exc)

            # Fall back to Kraken REST ticker if UI price extraction failed
            if price_executed is None:
                logger.info("Falling back to Kraken REST API for last trade price")
                price_executed = await _fetch_last_price_rest(pair.api_pair)

            return {
                "price_executed": price_executed,
                "screenshot_bytes": screenshot_bytes,
                "status": "executed",
                "error": None,
            }

        except Exception as exc:
            logger.exception("Playwright order placement failed: %s", exc)
            # Best-effort screenshot for debugging
            try:
                screenshot_bytes = await page.screenshot(full_page=True)
            except Exception:
                pass
            return {
                "price_executed": None,
                "screenshot_bytes": screenshot_bytes,
                "status": "failed",
                "error": str(exc),
            }

        finally:
            await context.close()
