import uuid
import re
import asyncio
from typing import Optional, Dict, Any
from database import (
    db_get_authorized_user,
    db_is_asset_allowed,
    db_get_allowed_assets_list,
    db_get_asset_price,
    db_get_market_context,
    db_log_trade,
)
from utils import send_imessage, send_imessage_with_attachment, set_typing_indicator

class MockBrokerAgent:
    async def execute_trade(self, action: str, asset: str, qty: int) -> str:
        await asyncio.sleep(1) 
        filename = f"screenshot_conf_{asset.lower()}_{action.lower()}.png"
        return f"https://your-storage-bucket.com/receipts/{filename}"

broker = MockBrokerAgent()

async def verify_trade_scope(phone_number: str, asset: str, est_value: float) -> tuple[bool, str]:
    if not await db_is_asset_allowed(asset):
        return False, f"{asset} is currently not available for execution options."

    user = await db_get_authorized_user(phone_number)
    if not user:
        return False, "User authorization profile not found."
        
    max_usd = user["max_trade_usd"]
    if est_value > max_usd:
        return False, f"Estimated value ${est_value:,.2f} exceeds single-trade threshold limit (${max_usd:,.2f})."

    return True, "OK"


class FinleyUserInterface:
    def __init__(self, user_phone: str):
        self.user_phone = user_phone
        self.active_d_ask: Optional[Dict[str, Any]] = None

    async def handle_inbound(self, message_text: str) -> None:
        try:
            await set_typing_indicator(self.user_phone, active=True)
            await self._route(message_text)
        finally:
            await set_typing_indicator(self.user_phone, active=False)

    async def send_proactive_alert(self, ticker: str, trigger_reason: str, current_price: float) -> None:
        context = await db_get_market_context(ticker)
        alert_body = (
            f"🚨 [Finley Alert] {ticker.upper()} Volatility Event Triggered\n"
            f"• Action Parameter: {trigger_reason}\n"
            f"• Current Price: ${current_price:.2f}\n"
            f"• Structural Profile: {context}\n\n"
            f"Respond with instructions (e.g., 'Buy 10 shares of {ticker}') to stage verification contract."
        )
        await self._send(alert_body)

    async def _route(self, message_text: str) -> None:
        clean_text = message_text.strip().lower()

        if self.active_d_ask:
            await self._handle_d_ask_resolution(clean_text)
            return

        if "buy" in clean_text or "sell" in clean_text:
            await self._parse_and_stage_trade(message_text)

        elif any(kw in clean_text for kw in ["status", "why", "analyze", "research", "what happened"]):
            ticker = None
            for word in clean_text.split():
                if await db_is_asset_allowed(word.upper()):
                    ticker = word.upper()
                    break

            if ticker:
                await self._handle_market_query(ticker)
            else:
                allowed = await db_get_allowed_assets_list()
                await self._send(f"Please state execution asset ticker. Covered listings: {', '.join(sorted(allowed))}.")
        else:
            await self._send(
                "🤖 Finley Agent: Operational. Systems standing by.\n\n"
                "Available Operations:\n"
                "  • 'Analyze [Asset]' — Fetch market telemetry\n"
                "  • 'Buy [X] shares of [Asset]' — Stage an order configuration"
            )

    async def _handle_market_query(self, ticker: str) -> None:
        context = await db_get_market_context(ticker)
        await self._send(f"📊 Analytics Profile ({ticker}):\n\n{context}\n\nStage order setup from this point?")

    async def _parse_and_stage_trade(self, raw_text: str) -> None:
        match = re.search(r'(buy|sell)\s+(\d+)\s+shares?\s+(?:of\s+)?([a-z]+)', raw_text, re.IGNORECASE)
        if not match:
            await self._send("❌ Formatting anomaly. Please use template schema: 'Buy X shares of TICKER'")
            return

        action, qty_str, ticker = match.groups()
        qty = int(qty_str)
        ticker = ticker.upper()

        unit_price = await db_get_asset_price(ticker)
        if unit_price is None:
            await self._send(f"⚠️ Pricing stream down or asset context missing for {ticker}.")
            return

        total_value = qty * unit_price
        authorized, reason = await verify_trade_scope(self.user_phone, ticker, total_value)
        if not authorized:
            await self._send(f"⚠️ Transaction Rejected: {reason}")
            return

        self.active_d_ask = {
            "session_id":  str(uuid.uuid4())[:8],
            "action":      action.upper(),
            "ticker":      ticker,
            "quantity":    qty,
            "unit_price":  unit_price,
            "total_value": total_value,
        }

        await self._send(
            f"❓ [D.Ask Verification Pipeline]\n"
            f"Staged Action Contract (ID: #{self.active_d_ask['session_id']}):\n"
            f"  ➡️  {self.active_d_ask['action']} {self.active_d_ask['quantity']} shares of {self.active_d_ask['ticker']}\n"
            f"  💵  Total Exposure: ${self.active_d_ask['total_value']:,.2f} (@ ${self.active_d_ask['unit_price']:.2f}/unit)\n\n"
            f"Reply 'YES' to clear execution parameters, or 'NO' to close contract cleanly."
        )

    async def _handle_d_ask_resolution(self, user_confirmation: str) -> None:
        staged_trade = self.active_d_ask
        if user_confirmation in ["yes", "y", "confirm", "approve", "execute"]:
            await self._send(f"🔒 Processing Signature Token #{staged_trade['session_id']}...")
            self.active_d_ask = None
            await self._dispatch_to_execution_layer(staged_trade)
        else:
            self.active_d_ask = None
            await self._send(f"🛑 Verification safe-drop. Contract #{staged_trade['session_id']} cleared.")

    async def _dispatch_to_execution_layer(self, confirmed_trade: Dict[str, Any]) -> None:
        authorized, reason = await verify_trade_scope(self.user_phone, confirmed_trade["ticker"], confirmed_trade["total_value"])
        if not authorized:
            await self._send(f"⚠️ Guardrail breach caught at execution perimeter: {reason}")
            return

        receipt_url = await broker.execute_trade(
            action=confirmed_trade["action"],
            asset=confirmed_trade["ticker"],
            qty=confirmed_trade["quantity"],
        )

        await db_log_trade({
            "session_id":   confirmed_trade["session_id"],
            "phone_number": self.user_phone,
            "action":       confirmed_trade["action"],
            "ticker":       confirmed_trade["ticker"],
            "quantity":     confirmed_trade["quantity"],
            "unit_price":   confirmed_trade["unit_price"],
            "total_value":  confirmed_trade["total_value"],
            "receipt_url":  receipt_url,
            "status":       "executed",
        })

        conf_msg = (
            f"✅ Order confirmed: {confirmed_trade['action']} {confirmed_trade['quantity']} units {confirmed_trade['ticker']}.\n"
            f"Trace ID Token: #{confirmed_trade['session_id']}."
        )
        await send_imessage_with_attachment(self.user_phone, conf_msg, receipt_url)

    async def _send(self, payload: str) -> None:
        await send_imessage(self.user_phone, payload)


# Stateful memory management session map
_sessions: Dict[str, FinleyUserInterface] = {}

def get_session(phone_number: str) -> FinleyUserInterface:
    if phone_number not in _sessions:
        _sessions[phone_number] = FinleyUserInterface(phone_number)
    return _sessions[phone_number]