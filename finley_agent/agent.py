"""
FinleyAgent — the iMessage conversational interface.

Flow for an inbound message:
  1. Normalise the incoming text.
  2. If a D.Ask trade is pending and the message is YES/NO → confirm or cancel.
  3. Otherwise: build RAG context (gateway), generate a Gemini response.
  4. If the response contains a D.Ask block → stage the trade for confirmation.
  5. Send the response via SendBlue (and a screenshot follow-up after execution).

Every external interaction is defensive: any unexpected failure results in a
friendly fallback iMessage rather than an HTTP 500.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation

import config
from broker_agent import BrokerAgent, TradeInstruction
from finley_agent import sendblue
from finley_agent.conversation_store import conversation_store
from finley_agent.gemini_client import generate_response
from gateway.context import build_context

logger = logging.getLogger("finley.agent")

FINLEY_SYSTEM_PROMPT = """
You are Finley, an AI-native trading assistant communicating over iMessage. You are sharp,
concise, and direct — this is a text conversation, not a chatbot interface. Keep responses
under 150 words unless the user asks for detail. Use plain text only (no markdown, no bullets —
this is iMessage).

You help users:
- Understand what's happening in the markets
- Get stock/crypto recommendations from your research agent
- Execute trades on their Kraken account
- Track their trade history
- Manage trading compliance rules

When a user wants to trade, extract: ticker, direction (buy/sell), and quantity.
Then respond ONLY with a D.Ask confirmation in this exact format:
"📋 Confirm trade:
[direction] [quantity] [ticker]
Est. price: ~$[price]
Reply YES to execute or NO to cancel."

If you detect a trade intent but are missing information, ask for it. Do not fabricate prices —
say "~$[price]" only if you have the price from market context, otherwise say "market price".

Never execute a trade without explicit D.Ask confirmation. Never discuss specific compliance
rules in detail — just say a trade is blocked and why in plain English.
"""

_YES = {"yes", "y", "yeah", "yep", "yup", "confirm", "confirmed", "ok", "okay", "sure", "do it", "go", "execute"}
_NO = {"no", "n", "nope", "nah", "cancel", "stop", "abort", "nevermind", "never mind"}

# Matches a D.Ask trade line like "buy 0.01 BTC".
_TRADE_RE = re.compile(r"\b(buy|sell)\b\s+([0-9]*\.?[0-9]+)\s+([A-Za-z]{2,6})", re.IGNORECASE)


class FinleyAgent:
    def __init__(self) -> None:
        self._broker = BrokerAgent()

    # ── public entry point ────────────────────────────────────────────────────
    async def handle_message(self, from_number: str, content: str, clerk_jwt: str = "") -> str:
        """Process an inbound iMessage. Sends the reply via SendBlue and returns it."""
        content = (content or "").strip()
        if not from_number or not content:
            return ""

        try:
            # 1) YES/NO to a pending D.Ask?
            pending = await conversation_store.peek_pending_trade(from_number)
            if pending and self._is_confirmation(content):
                reply = await self._handle_trade_confirmation(
                    from_number, confirmed=self._is_affirmative(content), clerk_jwt=clerk_jwt
                )
                await conversation_store.add_message(from_number, "model", reply)
                await sendblue.send_message(from_number, reply)
                return reply

            # 2) Normal turn: record user message, build context, generate reply.
            await conversation_store.add_message(from_number, "user", content)
            state = await conversation_store.get(from_number)
            user_id = self._lookup_user_id_by_phone(from_number)

            context = await build_context(content, user_id, clerk_jwt)
            # history[:-1] excludes the just-added user turn (passed separately).
            reply = await generate_response(
                system_prompt=FINLEY_SYSTEM_PROMPT,
                conversation_history=state.history[:-1],
                user_message=content,
                context=context,
            )

            # 3) Did Finley issue a D.Ask? If so, stage the trade.
            trade = self._extract_trade_intent(reply)
            if trade:
                await conversation_store.set_pending_trade(
                    from_number, {**trade, "clerk_jwt": clerk_jwt}, ttl_seconds=120
                )

            await conversation_store.add_message(from_number, "model", reply)
            await sendblue.send_message(from_number, reply)
            return reply

        except Exception as exc:
            logger.exception("handle_message failed for %s: %s", from_number, exc)
            fallback = "Sorry, I hit a snag. Try again in a moment."
            await sendblue.send_message(from_number, fallback)
            return fallback

    # ── D.Ask confirmation ─────────────────────────────────────────────────────
    async def _handle_trade_confirmation(self, from_number: str, confirmed: bool, clerk_jwt: str = "") -> str:
        trade = await conversation_store.consume_pending_trade(from_number)
        if trade is None:
            return "That confirmation expired or I don't have a trade staged. Tell me what you'd like to trade."

        if not confirmed:
            return (
                f"❌ Cancelled. No trade placed for "
                f"{trade.get('direction')} {trade.get('quantity')} {trade.get('ticker')}."
            )

        return await self._execute_trade_and_respond(
            from_number, trade, clerk_jwt or trade.get("clerk_jwt", "")
        )

    async def _execute_trade_and_respond(self, from_number: str, trade_params: dict, clerk_jwt: str) -> str:
        user_id = self._lookup_user_id_by_phone(from_number)
        try:
            instruction = TradeInstruction(
                user_id=user_id,
                clerk_jwt=clerk_jwt or "",
                ticker=trade_params["ticker"],
                direction=trade_params["direction"],
                quantity=Decimal(str(trade_params["quantity"])),
                notes="Executed via Finley iMessage",
            )
        except (KeyError, InvalidOperation) as exc:
            logger.error("Bad staged trade params %s: %s", trade_params, exc)
            return "Something was off with that trade. Tell me the ticker, side, and amount again."

        result = await self._broker.execute(instruction)

        if not result.success:
            reason = result.error or "unknown error"
            if result.status == "cancelled":
                return f"🚫 Trade blocked. {reason}"
            return f"⚠️ Couldn't execute that trade: {reason}"

        verb = "Bought" if instruction.direction == "buy" else "Sold"
        price = result.price_executed
        msg = (
            f"✅ Trade executed!\n"
            f"{verb} {instruction.quantity} {instruction.ticker} at ${price}.\n"
            f"Trade ID: {result.trade_id}"
        )

        # Follow up with the confirmation screenshot if we have a usable URL.
        if result.screenshot_url and result.screenshot_url != "upload_failed":
            await sendblue.send_image_message(
                from_number, "Trade confirmation 📸", result.screenshot_url
            )

        return msg

    # ── parsing helpers ─────────────────────────────────────────────────────────
    def _extract_trade_intent(self, response_text: str) -> dict | None:
        """Parse a D.Ask block in Finley's response into trade params, or None."""
        if "confirm trade" not in response_text.lower():
            return None
        match = _TRADE_RE.search(response_text)
        if not match:
            return None
        try:
            quantity = Decimal(match.group(2))
        except InvalidOperation:
            return None
        if quantity <= 0:
            return None
        return {
            "ticker": match.group(3).upper(),
            "direction": match.group(1).lower(),
            "quantity": quantity,
        }

    def _is_confirmation(self, content: str) -> bool:
        return self._normalise(content) in (_YES | _NO)

    def _is_affirmative(self, content: str) -> bool:
        return self._normalise(content) in _YES

    @staticmethod
    def _normalise(content: str) -> str:
        return content.strip().lower().rstrip("!. ")

    def _lookup_user_id_by_phone(self, phone: str) -> str:
        """Map a phone number to a Clerk user_id.

        In this demo build every user is keyed by their phone number — the seed
        script inserts a ``users`` row whose ``clerk_user_id`` equals the phone,
        so trade inserts satisfy the ``trades.user_id`` foreign key. The
        ``DEMO_MODE`` flag is kept here to document where a real Clerk lookup
        (query ``users`` by ``phone_number``) would slot in for production.
        """
        _ = config.DEMO_MODE  # both paths key on phone in this build
        return phone
