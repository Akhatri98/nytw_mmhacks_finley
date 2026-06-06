#!/usr/bin/env python3
"""
Finley BrokerAgent — smoke test
================================
Verifies each integration layer in isolation. Does NOT place a real order.

Steps
-----
  1  Env vars present
  2  Module imports (all deps installed + chromium available)
  3  Ticker map
  4  Supabase DB connectivity
  5  Compliance check (standard rules only, no real JWT required)
  6  Playwright: navigate to kraken.com/trade/BTC-USD, verify form visible, screenshot
  7  Supabase Storage: upload 1-pixel test PNG, verify URL, clean up

Usage
-----
  python smoke_test.py               # all steps
  python smoke_test.py --no-browser  # skip step 6 (no Playwright)
  python smoke_test.py --no-storage  # skip step 7 (no Storage upload)
"""
import asyncio
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── ANSI colours ──────────────────────────────────────────────────────────────
GREEN = "\033[32m"
RED   = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"
BOLD  = "\033[1m"

_results: list[tuple[str, bool, str, float]] = []  # (name, passed, detail, elapsed)


def _print_header(step: int, name: str) -> None:
    print(f"\n{BOLD}[{step}] {name}{RESET}")


def _ok(step: int, name: str, detail: str, elapsed: float) -> None:
    _results.append((name, True, detail, elapsed))
    print(f"  {GREEN}✓{RESET}  {detail}  ({elapsed:.2f}s)")


def _fail(step: int, name: str, detail: str, elapsed: float) -> None:
    _results.append((name, False, detail, elapsed))
    print(f"  {RED}✗{RESET}  {detail}  ({elapsed:.2f}s)")


def _skip(name: str, reason: str) -> None:
    _results.append((name, True, f"SKIPPED — {reason}", 0))
    print(f"  {YELLOW}–{RESET}  SKIPPED — {reason}")


# ── Minimal valid 1×1 white PNG (no external deps) ───────────────────────────
_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xdc\xccY\xe7"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


# ── Step 1: Env vars ──────────────────────────────────────────────────────────

def step1_env_vars() -> bool:
    _print_header(1, "Env vars")
    t = time.perf_counter()
    required = [
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "KRAKEN_EMAIL",
        "KRAKEN_PASSWORD",
    ]
    # Accept either key name for the service role
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")
    missing = [k for k in required if not os.getenv(k)]
    if not service_key:
        missing.append("SUPABASE_SERVICE_ROLE_KEY")

    elapsed = time.perf_counter() - t
    if missing:
        _fail(1, "Env vars", f"Missing: {', '.join(missing)}", elapsed)
        return False
    _ok(1, "Env vars", "All required env vars present", elapsed)
    return True


# ── Step 2: Module imports ────────────────────────────────────────────────────

def step2_imports() -> bool:
    _print_header(2, "Module imports")
    t = time.perf_counter()
    failures = []

    deps = [
        ("pydantic", "pydantic"),
        ("httpx", "httpx"),
        ("dotenv", "python-dotenv"),
        ("supabase", "supabase"),
        ("playwright.async_api", "playwright"),
    ]
    for module, pkg in deps:
        try:
            __import__(module)
        except ImportError:
            failures.append(pkg)

    elapsed = time.perf_counter() - t
    if failures:
        _fail(2, "Imports", f"Missing packages: {', '.join(failures)}  →  pip install -r requirements.txt", elapsed)
        return False

    # Check chromium is installed — use subprocess to avoid sync_playwright
    # conflicting with the asyncio event loop that wraps main().
    import subprocess
    result = subprocess.run(
        [sys.executable, "-c",
         "from playwright.sync_api import sync_playwright\n"
         "with sync_playwright() as p: print(p.chromium.executable_path)"],
        capture_output=True, text=True, timeout=15,
    )
    elapsed = time.perf_counter() - t
    exe = result.stdout.strip()
    if result.returncode != 0 or not Path(exe).exists():
        detail = result.stderr.strip().splitlines()[0] if result.stderr.strip() else "executable not found"
        _fail(2, "Imports", f"Chromium not installed: {detail}  →  playwright install chromium", elapsed)
        return False

    _ok(2, "Imports", f"All deps present, chromium at {exe}", elapsed)
    return True


# ── Step 3: Ticker map ────────────────────────────────────────────────────────

def step3_ticker_map() -> bool:
    _print_header(3, "Ticker map")
    t = time.perf_counter()
    from broker_agent.ticker_map import to_kraken_pair

    cases = [
        ("BTC",    "BTC-USD", "XBTUSD"),
        ("BTCUSD", "BTC-USD", "XBTUSD"),
        ("ETH",    "ETH-USD", "ETHUSD"),
        ("SOL",    "SOL-USD", "SOLUSD"),
    ]
    failures = []
    for ticker, want_slug, want_api in cases:
        try:
            pair = to_kraken_pair(ticker)
            if pair.url_slug != want_slug:
                failures.append(f"{ticker} url_slug={pair.url_slug!r} (want {want_slug!r})")
            if pair.api_pair != want_api:
                failures.append(f"{ticker} api_pair={pair.api_pair!r} (want {want_api!r})")
        except Exception as exc:
            failures.append(f"{ticker}: {exc}")

    # Also verify unknown ticker raises cleanly
    try:
        to_kraken_pair("FAKECOIN999")
        failures.append("FAKECOIN999 should have raised ValueError")
    except ValueError:
        pass

    elapsed = time.perf_counter() - t
    if failures:
        _fail(3, "Ticker map", "; ".join(failures), elapsed)
        return False
    _ok(3, "Ticker map", f"{len(cases)} pairs mapped correctly, unknown ticker raises ValueError", elapsed)
    return True


# ── Step 4: Supabase DB connectivity ─────────────────────────────────────────

async def step4_supabase_db() -> bool:
    _print_header(4, "Supabase DB connectivity")
    t = time.perf_counter()
    try:
        from broker_agent.supabase_client import service_client
        client = await service_client()
        # Lightweight probe: count active standard compliance rules
        response = await (
            client.table("compliance_rules")
            .select("id", count="exact")
            .eq("active", True)
            .eq("scope", "standard")
            .execute()
        )
        count = response.count if response.count is not None else len(response.data or [])
        elapsed = time.perf_counter() - t
        _ok(4, "Supabase DB", f"Connected — {count} active standard compliance rule(s) found", elapsed)
        return True
    except Exception as exc:
        elapsed = time.perf_counter() - t
        _fail(4, "Supabase DB", str(exc), elapsed)
        return False


# ── Step 5: Compliance check ──────────────────────────────────────────────────

async def step5_compliance() -> bool:
    _print_header(5, "Compliance check")
    t = time.perf_counter()
    try:
        from broker_agent.compliance import check_compliance
        from broker_agent.models import TradeInstruction

        # Use an empty JWT — standard rules have no auth.uid() requirement in RLS
        # so they will be returned; user_defined rules won't (expected for this test).
        instruction = TradeInstruction(
            user_id="smoke_test_user",
            clerk_jwt="",   # no real session — standard rules only
            ticker="BTC",
            direction="buy",
            quantity=Decimal("0.001"),
        )
        result = await check_compliance(instruction)
        elapsed = time.perf_counter() - t

        summary = (
            f"approved={result.approved}, "
            f"hard_blocks={len(result.hard_blocks)}, "
            f"warnings={len(result.warnings)}"
        )
        # Standard seed data has hard_block rules, so approved=False is expected
        # unless your DB has been modified. Either outcome is fine — we just want
        # the query to complete without an exception.
        _ok(5, "Compliance", f"Query completed — {summary}", elapsed)
        return True
    except Exception as exc:
        elapsed = time.perf_counter() - t
        _fail(5, "Compliance", str(exc), elapsed)
        return False


# ── Step 6: Playwright — navigate to Kraken, verify order form ───────────────

async def step6_playwright() -> bool:
    _print_header(6, "Playwright — Kraken trade page")
    t = time.perf_counter()
    from pathlib import Path as _Path

    from playwright.async_api import TimeoutError as PWTimeout
    from playwright.async_api import async_playwright

    from broker_agent.kraken_playwright import (
        USER_DATA_DIR,
        HEADLESS,
        _is_signed_in,
        _perform_login,
    )

    screenshot_path = _Path("smoke_screenshot.png")
    _Path(USER_DATA_DIR).mkdir(parents=True, exist_ok=True)

    try:
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=USER_DATA_DIR,
                headless=HEADLESS,
                viewport={"width": 1440, "height": 900},
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = await context.new_page()

            await page.goto(
                "https://www.kraken.com/trade/BTC-USD",
                wait_until="domcontentloaded",
                timeout=30_000,
            )

            if not await _is_signed_in(page):
                print(f"  {YELLOW}  Not signed in — attempting login…{RESET}")
                await _perform_login(page)
                await page.goto(
                    "https://www.kraken.com/trade/BTC-USD",
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )

            # Verify the order form is visible — stop here, do NOT interact with it
            # selector — verify against live UI if it breaks
            try:
                await page.wait_for_selector(
                    '[data-testid="order-form"], '
                    'form[class*="OrderForm"], '
                    '[class*="order-form"], '
                    '[class*="OrderPanel"], '
                    'section[aria-label*="order" i]',
                    timeout=15_000,
                )
                form_found = True
            except PWTimeout:
                form_found = False

            screenshot_bytes = await page.screenshot(full_page=False)
            screenshot_path.write_bytes(screenshot_bytes)
            await context.close()

        elapsed = time.perf_counter() - t
        if form_found:
            _ok(6, "Playwright", f"Order form visible. Screenshot → {screenshot_path}", elapsed)
        else:
            # Page loaded but form selector didn't match — selectors need updating
            _fail(
                6, "Playwright",
                f"Page loaded but order form selector didn't match. "
                f"Check smoke_screenshot.png and update selectors in kraken_playwright.py",
                elapsed,
            )
            return False
        return True

    except Exception as exc:
        elapsed = time.perf_counter() - t
        _fail(6, "Playwright", str(exc), elapsed)
        return False


# ── Step 7: Supabase Storage upload ──────────────────────────────────────────

async def step7_storage() -> bool:
    _print_header(7, "Supabase Storage")
    t = time.perf_counter()
    bucket = os.getenv("SCREENSHOT_BUCKET", "trade-screenshots")
    test_path = "smoke-test/1x1.png"
    supabase_url = os.environ["SUPABASE_URL"]

    try:
        from broker_agent.supabase_client import service_client
        client = await service_client()

        await client.storage.from_(bucket).upload(
            path=test_path,
            file=_PNG_1X1,
            file_options={"content-type": "image/png", "upsert": "true"},
        )
        public_url = f"{supabase_url}/storage/v1/object/public/{bucket}/{test_path}"

        # Clean up the test file
        await client.storage.from_(bucket).remove([test_path])

        elapsed = time.perf_counter() - t
        _ok(7, "Storage", f"Upload + delete OK. Public URL pattern: {public_url}", elapsed)
        return True
    except Exception as exc:
        elapsed = time.perf_counter() - t
        _fail(7, "Storage", str(exc), elapsed)
        return False


# ── Summary ───────────────────────────────────────────────────────────────────

def _print_summary() -> bool:
    passed = sum(1 for _, ok, _, _ in _results if ok)
    total  = len(_results)
    print(f"\n{'─' * 54}")
    print(f"{BOLD}Results: {passed}/{total} passed{RESET}\n")
    all_ok = True
    for name, ok, detail, elapsed in _results:
        icon = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}"
        note = f"  ({elapsed:.2f}s)" if elapsed else ""
        print(f"  {icon}  {name}{note}")
        if not ok:
            print(f"       {RED}{detail}{RESET}")
            all_ok = False
    print()
    return all_ok


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    no_browser = "--no-browser" in sys.argv
    no_storage = "--no-storage" in sys.argv

    print(f"\n{BOLD}Finley BrokerAgent — smoke test{RESET}")
    print(f"{'─' * 54}")
    if no_browser:
        print(f"{YELLOW}  --no-browser: skipping Playwright step{RESET}")
    if no_storage:
        print(f"{YELLOW}  --no-storage: skipping Storage step{RESET}")

    # Steps 1-3 are synchronous
    env_ok = step1_env_vars()
    if not env_ok:
        print(f"\n{RED}Halting — fix env vars before continuing.{RESET}\n")
        sys.exit(1)

    imports_ok = step2_imports()
    if not imports_ok:
        print(f"\n{RED}Halting — fix missing dependencies before continuing.{RESET}\n")
        sys.exit(1)

    step3_ticker_map()

    # Steps 4-7 are async
    await step4_supabase_db()
    await step5_compliance()

    if no_browser:
        _skip("Playwright", "--no-browser flag set")
    else:
        await step6_playwright()

    if no_storage:
        _skip("Storage", "--no-storage flag set")
    else:
        await step7_storage()

    all_ok = _print_summary()
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
