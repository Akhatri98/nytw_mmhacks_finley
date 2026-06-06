"""Kraken (kraken.com) order placement via Playwright browser automation.

Target UI: https://www.kraken.com/trade/<pair>  (regular Kraken, NOT Kraken Pro)

All CSS/role selectors are marked with:
    # selector — verify against live UI if it breaks
so they are easy to grep and patch if Kraken's front-end changes.
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
# Read lazily inside _perform_login so the module imports cleanly in DEMO_MODE
# (where Kraken credentials are not required).
KRAKEN_EMAIL: str = os.getenv("KRAKEN_EMAIL", "")
KRAKEN_PASSWORD: str = os.getenv("KRAKEN_PASSWORD", "")
HEADLESS: bool = os.getenv("HEADLESS", "false").lower() == "true"
DEMO_MODE: bool = os.getenv("DEMO_MODE", "false").lower() == "true"

# A 1×1 transparent PNG — used as a stand-in trade-confirmation screenshot in
# DEMO_MODE so the full upload/record/send pipeline exercises real bytes.
_DEMO_PNG: bytes = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "000000000a49444154789c6260000000000200e221bc33000000000049454e44ae426082"
)
USER_DATA_DIR: str = os.getenv(
    "BROWSER_DATA_DIR",
    str(Path(__file__).parent.parent / "browser_data"),
)
TWO_FA_TIMEOUT_MS: int = 60_000  # 60 s for manual 2FA entry

KRAKEN_BASE = "https://www.kraken.com"
KRAKEN_SIGN_IN = f"{KRAKEN_BASE}/sign-in"


# ── Kraken REST API fallback ──────────────────────────────────────────────────

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
            first_key = next(iter(result))  # e.g. "XXBTZUSD"
            last_price_str: str = result[first_key]["c"][0]  # c = [last_trade_price, lot_volume]
            return Decimal(last_price_str)
    except Exception as exc:
        logger.warning("Kraken REST price fetch failed: %s", exc)
        return None


def _generate_demo_screenshot(ticker: str, direction: str, quantity: Decimal) -> bytes:
    """Return placeholder PNG bytes for a simulated trade confirmation."""
    logger.info("DEMO_MODE screenshot for %s %s %s", direction, quantity, ticker)
    return _DEMO_PNG


# ── Login helpers ─────────────────────────────────────────────────────────────

async def _is_signed_in(page: Page) -> bool:
    """Return True if the trade page is showing the authenticated order form.

    Kraken renders almost nothing on the trade page when unauthenticated, so
    presence of any order-form input is a reliable authenticated signal.
    """
    try:
        # selector — verify against live UI if it breaks
        await page.wait_for_selector(
            'input[name="quantity"], '
            'input[name="amount"], '
            'input[name="volume"], '
            'input[name="orderQty"], '
            'button:has-text("Buy"), '
            'button:has-text("Sell")',
            timeout=6_000,
        )
        return True
    except PWTimeout:
        return False


async def _perform_login(page: Page) -> None:
    """Navigate to sign-in, fill credentials, handle optional 2FA."""
    logger.info("Navigating to Kraken sign-in page…")
    await page.goto(KRAKEN_SIGN_IN, wait_until="domcontentloaded", timeout=30_000)

    # Fill username/email — Kraken's field is name="username"
    # selector — verify against live UI if it breaks
    username_selector = 'input[name="username"]'
    await page.wait_for_selector(username_selector, timeout=15_000)
    await page.fill(username_selector, KRAKEN_EMAIL)

    # Fill password
    # selector — verify against live UI if it breaks
    await page.fill('input[name="password"], input[type="password"]', KRAKEN_PASSWORD)

    # Submit — Kraken's login button says "Continue"
    # selector — verify against live UI if it breaks
    await page.click('button[type="submit"]', timeout=10_000)

    # Handle optional 2FA (TOTP code input)
    two_fa_selector = (
        'input[name="otp"], '
        'input[name="totp"], '
        'input[name="token"], '
        'input[placeholder*="code" i], '
        'input[placeholder*="2FA" i], '
        'input[placeholder*="authenticator" i], '
        'input[aria-label*="code" i]'
    )
    try:
        # selector — verify against live UI if it breaks
        await page.wait_for_selector(two_fa_selector, timeout=8_000)
        print(
            "\n[Finley BrokerAgent] *** 2FA REQUIRED ***\n"
            "Enter your Kraken authentication code in the browser window.\n"
            f"Waiting up to {TWO_FA_TIMEOUT_MS // 1_000}s…\n"
        )
        # Wait until the 2FA field is gone (user submitted the code)
        await page.wait_for_selector(two_fa_selector, state="hidden", timeout=TWO_FA_TIMEOUT_MS)
    except PWTimeout:
        pass  # No 2FA prompt — continue

    # Wait for redirect away from the sign-in page
    await page.wait_for_url(
        lambda url: "sign-in" not in url,
        timeout=25_000,
    )
    logger.info("Kraken login successful — redirected to %s", page.url)


# ── Order placement ───────────────────────────────────────────────────────────

async def place_order(
    ticker: str,
    direction: str,
    quantity: Decimal,
) -> dict:
    """Place a market order on kraken.com via Playwright.

    Returns:
        {
            "price_executed": Decimal | None,
            "screenshot_bytes": bytes | None,
            "status": "executed" | "failed",
            "error": str | None,
        }
    """
    pair: KrakenPair = to_kraken_pair(ticker)
    trade_url = f"{KRAKEN_BASE}/trade/{pair.url_slug}"
    side_text = "Buy" if direction == "buy" else "Sell"

    # ── DEMO_MODE: skip the browser entirely, return a simulated fill ─────────
    if DEMO_MODE:
        price = await _fetch_last_price_rest(pair.api_pair) or Decimal("50000")
        logger.info(
            "DEMO_MODE simulated order: %s %s %s @ %s", direction, quantity, ticker, price
        )
        return {
            "price_executed": price,
            "screenshot_bytes": _generate_demo_screenshot(ticker, direction, quantity),
            "status": "executed",
            "error": None,
        }

    if not (KRAKEN_EMAIL and KRAKEN_PASSWORD):
        return {
            "price_executed": None,
            "screenshot_bytes": None,
            "status": "failed",
            "error": "KRAKEN_EMAIL / KRAKEN_PASSWORD not set (and DEMO_MODE is off).",
        }

    Path(USER_DATA_DIR).mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
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
                await page.goto(trade_url, wait_until="domcontentloaded", timeout=30_000)

            # ── Step 3: Wait for the order form panel ─────────────────────
            # selector — verify against live UI if it breaks
            await page.wait_for_selector(
                '[data-testid="order-form"], '
                'form[class*="OrderForm"], '
                '[class*="order-form"], '
                '[class*="OrderPanel"], '
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
                f'label:has-text("{side_text}"), '
                f'button:has-text("{side_text}")',
                timeout=10_000,
            )
            await asyncio.sleep(0.4)

            # ── Step 5: Select Market order type ─────────────────────────
            # The regular Kraken UI has an order-type dropdown or segmented control.
            # selector — verify against live UI if it breaks
            try:
                await page.click(
                    'button:has-text("Market"), '
                    '[data-testid="order-type-market"], '
                    '[data-order-type="market"], '
                    '[role="option"]:has-text("Market"), '
                    'label:has-text("Market")',
                    timeout=5_000,
                )
                await asyncio.sleep(0.3)
            except PWTimeout:
                logger.warning("Market order-type selector timed out — assuming Market is already active")

            # ── Step 6: Enter quantity ────────────────────────────────────
            # selector — verify against live UI if it breaks
            qty_input = await page.wait_for_selector(
                'input[name="quantity"], '
                'input[name="amount"], '
                'input[name="volume"], '
                'input[name="qty"], '
                'input[placeholder*="quantity" i], '
                'input[placeholder*="amount" i], '
                'input[placeholder*="volume" i], '
                'input[data-testid="quantity-input"], '
                'input[data-testid="amount-input"]',
                timeout=10_000,
            )
            await qty_input.triple_click()
            await qty_input.type(str(quantity), delay=40)
            await asyncio.sleep(0.3)

            # ── Step 7: Submit the order ──────────────────────────────────
            # selector — verify against live UI if it breaks
            await page.click(
                f'button:has-text("Place {side_text} Order"), '
                f'button:has-text("{side_text} now"), '
                f'button:has-text("{side_text} Now"), '
                f'button[data-testid="place-order-button"], '
                f'button[data-testid="submit-order-button"], '
                f'button[type="submit"]:has-text("{side_text}")',
                timeout=10_000,
            )

            # ── Step 8: Wait for confirmation ─────────────────────────────
            # selector — verify against live UI if it breaks
            try:
                await page.wait_for_selector(
                    '[data-testid="order-confirmation"], '
                    '[data-testid="order-success"], '
                    '.notification--success, '
                    '[class*="successBanner"], '
                    '[class*="OrderConfirmation"], '
                    ':has-text("Order placed"), '
                    ':has-text("Order submitted"), '
                    ':has-text("successfully")',
                    timeout=15_000,
                )
                logger.info("Order confirmation received")
            except PWTimeout:
                logger.warning("Confirmation selector timed out — order may still have executed; proceeding")

            await asyncio.sleep(1.2)

            # ── Step 9: Full-page screenshot ──────────────────────────────
            screenshot_bytes = await page.screenshot(full_page=True)

            # ── Step 10: Extract executed price from UI, fallback to REST ─
            # selector — verify against live UI if it breaks
            try:
                price_el = await page.query_selector(
                    '[data-testid="execution-price"], '
                    '[data-testid="fill-price"], '
                    '[class*="execPrice"], '
                    '[class*="fillPrice"], '
                    '[class*="execution-price"], '
                    '[class*="averagePrice"]'
                )
                if price_el:
                    raw = await price_el.inner_text()
                    cleaned = raw.replace("$", "").replace(",", "").strip()
                    price_executed = Decimal(cleaned)
                    logger.info("Extracted execution price from page: %s", price_executed)
            except Exception as exc:
                logger.debug("Could not extract price from confirmation UI: %s", exc)

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
