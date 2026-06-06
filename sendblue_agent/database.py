import asyncio
from typing import Optional, Dict, List, Any
from config import get_db

async def db_get_authorized_user(phone_number: str) -> Optional[Dict]:
    result = await asyncio.to_thread(
        lambda: get_db()
        .table("authorized_users")
        .select("phone_number, max_trade_usd, is_active")
        .eq("phone_number", phone_number)
        .eq("is_active", True)
        .maybe_single()
        .execute()
    )
    return result.data

async def db_is_asset_allowed(ticker: str) -> bool:
    result = await asyncio.to_thread(
        lambda: get_db()
        .table("allowed_assets")
        .select("ticker")
        .eq("ticker", ticker.upper())
        .eq("is_active", True)
        .maybe_single()
        .execute()
    )
    return result.data is not None

async def db_get_allowed_assets_list() -> List[str]:
    result = await asyncio.to_thread(
        lambda: get_db()
        .table("allowed_assets")
        .select("ticker")
        .eq("is_active", True)
        .execute()
    )
    return [row["ticker"] for row in result.data] if result.data else []

async def db_get_asset_price(ticker: str) -> Optional[float]:
    result = await asyncio.to_thread(
        lambda: get_db()
        .table("asset_prices")
        .select("price")
        .eq("ticker", ticker.upper())
        .maybe_single()
        .execute()
    )
    return result.data["price"] if result.data else None

async def db_get_market_context(ticker: str) -> str:
    result = await asyncio.to_thread(
        lambda: get_db()
        .table("market_context")
        .select("context_text")
        .eq("ticker", ticker.upper())
        .order("updated_at", desc=True)
        .limit(1)
        .execute()
    )
    if result.data and len(result.data) > 0:
        return result.data[0]["context_text"]
    return "No recent structural tracking or context data logged for this asset."

async def db_log_trade(trade: Dict[str, Any]) -> None:
    await asyncio.to_thread(
        lambda: get_db()
        .table("trades")
        .insert(trade)
        .execute()
    )