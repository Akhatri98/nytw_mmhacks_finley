"""
Gemini client — chat generation + text embeddings.

The ``google.generativeai`` SDK is synchronous, so every network call is wrapped
in ``asyncio.to_thread`` to avoid blocking the FastAPI event loop. Every external
call is defensive: embedding failures return a zero vector, generation failures
return a friendly fallback string. Nothing here raises to the caller.
"""

from __future__ import annotations

import asyncio
import logging

import google.generativeai as genai

import config

logger = logging.getLogger("finley.gemini")

genai.configure(api_key=config.GEMINI_API_KEY)

CHAT_MODEL = "gemini-2.5-pro"
EMBED_MODEL = "models/gemini-embedding-001"

# Keep Gemini history bounded to control token cost.
MAX_HISTORY_MESSAGES = 10


async def embed(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float]:
    """Embed ``text`` with gemini-embedding-001 (truncated to ``config.EMBED_DIM``).

    Returns a list of ``config.EMBED_DIM`` floats. On any failure, returns a
    zero vector of the same length so callers can proceed (a zero vector simply
    matches nothing meaningful in Pinecone).
    """
    text = (text or "").strip()
    if not text:
        return [0.0] * config.EMBED_DIM
    try:
        result = await asyncio.to_thread(
            genai.embed_content,
            model=EMBED_MODEL,
            content=text,
            task_type=task_type,
            output_dimensionality=config.EMBED_DIM,
        )
        embedding = result["embedding"]
        # Some SDK versions nest the vector under ["embedding"]["values"].
        if isinstance(embedding, dict):
            embedding = embedding.get("values", [])
        if not embedding:
            raise ValueError("empty embedding returned")
        return list(embedding)
    except Exception as exc:
        logger.error("Embedding failed (%s): %s", task_type, exc)
        return [0.0] * config.EMBED_DIM


def format_context_for_prompt(context: dict | None) -> str:
    """Render the RAG context dict into a compact, prompt-injectable text block.

    Truncated aggressively because this is prepended to every user message.
    """
    if not context:
        return ""

    lines: list[str] = []

    signals = (context.get("market_signals") or [])[:6]
    if signals:
        lines.append("MARKET SIGNALS:")
        for s in signals:
            ticker = s.get("ticker", "?")
            sentiment = s.get("sentiment_label") or s.get("sentiment_score", "n/a")
            source = s.get("source", "?")
            content = (s.get("content") or "").strip()[:140]
            lines.append(f"- {ticker} [{sentiment}] ({source}): {content}")

    picks = (context.get("stock_picks") or [])[:4]
    if picks:
        lines.append("STOCK PICKS:")
        for p in picks:
            ticker = p.get("ticker", "?")
            direction = p.get("direction", "?")
            confidence = p.get("confidence", "?")
            rationale = (p.get("rationale") or "").strip()[:160]
            lines.append(f"- {ticker} {direction} (conf {confidence}): {rationale}")

    trades = (context.get("trade_history") or [])[:5]
    if trades:
        lines.append("RECENT TRADES:")
        for t in trades:
            ticker = t.get("ticker", "?")
            direction = t.get("direction", "?")
            qty = t.get("quantity", "?")
            price = t.get("price_executed", "?")
            status = t.get("status", "?")
            lines.append(f"- {direction} {qty} {ticker} @ ${price} ({status})")

    return "\n".join(lines)


async def generate_response(
    system_prompt: str,
    conversation_history: list[dict],
    user_message: str,
    context: dict | None = None,
) -> str:
    """Generate a Gemini chat response.

    ``conversation_history`` is in Gemini format: ``[{"role": "user"|"model",
    "parts": ["..."]}]``. The RAG ``context`` is formatted and prepended to the
    user message. Returns the model's text, or a friendly fallback on error.
    """
    context_block = format_context_for_prompt(context)
    if context_block:
        full_user_message = (
            f"[CONTEXT — use only if relevant, do not read it back verbatim]\n"
            f"{context_block}\n\n[USER MESSAGE]\n{user_message}"
        )
    else:
        full_user_message = user_message

    history = list(conversation_history or [])[-MAX_HISTORY_MESSAGES:]

    try:
        model = genai.GenerativeModel(
            model_name=CHAT_MODEL,
            system_instruction=system_prompt,
        )
        chat = model.start_chat(history=history)
        response = await asyncio.to_thread(chat.send_message, full_user_message)
        text = (getattr(response, "text", "") or "").strip()
        return text or "Sorry, I didn't catch that — can you rephrase?"
    except Exception as exc:
        logger.error("Gemini generation failed: %s", exc)
        return "Sorry, I hit a snag reaching my brain. Try again in a moment."
