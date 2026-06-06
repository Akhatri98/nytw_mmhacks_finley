"""
Seed a demo user (and verify standard compliance rules) for the Finley demo.

NOTE: this folder is intentionally NOT a Python package — adding an __init__.py
here would shadow the installed `supabase` pip package. Run it as a script:

    python supabase/seed.py

The demo keys every user by phone number (``users.clerk_user_id == phone``) so
that trade inserts satisfy the ``trades.user_id`` foreign key without Clerk.
Set DEMO_USER_PHONE to the iMessage number you'll text from; it defaults to
SENDBLUE_FROM_NUMBER.
"""

import asyncio
import os
import sys

# Make the project root importable when run as `python supabase/seed.py`.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402  (loads .env, validates config)
from broker_agent.supabase_client import service_client  # noqa: E402

DEMO_USER_PHONE = (
    os.getenv("DEMO_USER_PHONE")
    or config.SENDBLUE_FROM_NUMBER
    or "+10000000000"
)

DEMO_USER = {
    "clerk_user_id": DEMO_USER_PHONE,  # keyed by phone for the demo
    "phone_number": DEMO_USER_PHONE,
    "preferred_broker": "kraken",
    "risk_tolerance": "moderate",
    "max_single_position_pct": 20.00,
}


async def seed() -> None:
    client = await service_client()

    # Upsert the demo user (conflict on clerk_user_id → update).
    resp = await (
        client.table("users")
        .upsert(DEMO_USER, on_conflict="clerk_user_id")
        .execute()
    )
    user_rows = resp.data or []
    print(f"✅ Demo user upserted: clerk_user_id={DEMO_USER['clerk_user_id']} "
          f"phone={DEMO_USER['phone_number']}")

    # Verify standard compliance rules are present (seeded by schema.sql).
    rules_resp = await (
        client.table("compliance_rules")
        .select("rule_category, severity, scope")
        .eq("scope", "standard")
        .execute()
    )
    rules = rules_resp.data or []
    print(f"✅ Standard compliance rules found: {len(rules)}")
    for r in rules:
        print(f"   - {r.get('rule_category')} [{r.get('severity')}]")

    if not rules:
        print("⚠️  No standard compliance rules found. Apply supabase/schema.sql first.")

    print("\nSeed complete. You can now run: uvicorn main:app --port 8000")
    _ = user_rows  # touched for clarity; data already printed above


if __name__ == "__main__":
    asyncio.run(seed())
