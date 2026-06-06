"""
SendBlue iMessage transport.

Outbound: POST to SendBlue's send-message API with the ``sb-api-key-id`` header.
Inbound: ``parse_webhook`` normalises SendBlue's webhook payload into a small,
stable dict the agent consumes.

All outbound calls are defensive: a SendBlue outage logs an error and returns a
result dict rather than raising, so a failed send never crashes message handling.
"""

from __future__ import annotations

import logging
import os

import httpx

import config

logger = logging.getLogger("finley.sendblue")

SENDBLUE_BASE = "https://api.sendblue.co"
_HEADERS = {
    "sb-api-key-id": config.SENDBLUE_API_KEY,
    "sb-api-secret-key": os.getenv("SENDBLUE_SECRET", ""),
    "Content-Type": "application/json",
}


async def send_message(to_number: str, content: str, send_style: str = "invisible") -> dict:
    """POST a plain iMessage. Returns the SendBlue response JSON (or an error dict)."""
    if not to_number:
        logger.warning("send_message called with empty to_number; skipping")
        return {"status": "skipped", "reason": "no recipient"}

    body = {
        "number": to_number,
        "content": content,
        "from_number": config.SENDBLUE_FROM_NUMBER or None,
        "send_style": send_style,
    }
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.post(
                f"{SENDBLUE_BASE}/api/send-message", headers=_HEADERS, json=body
            )
            resp.raise_for_status()
            logger.info("Sent iMessage to %s (%d chars)", to_number, len(content))
            return resp.json()
    except Exception as exc:
        logger.error("SendBlue send_message failed to %s: %s", to_number, exc)
        return {"status": "error", "error": str(exc)}


async def send_image_message(to_number: str, content: str, media_url: str) -> dict:
    """Send an iMessage with an image attachment (e.g. a trade screenshot)."""
    if not to_number:
        return {"status": "skipped", "reason": "no recipient"}

    body = {
        "number": to_number,
        "content": content,
        "from_number": config.SENDBLUE_FROM_NUMBER or None,
        "media_url": media_url,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(
                f"{SENDBLUE_BASE}/api/send-message", headers=_HEADERS, json=body
            )
            resp.raise_for_status()
            logger.info("Sent image iMessage to %s", to_number)
            return resp.json()
    except Exception as exc:
        logger.error("SendBlue send_image_message failed to %s: %s", to_number, exc)
        return {"status": "error", "error": str(exc)}


def parse_webhook(payload: dict) -> dict:
    """Normalise a SendBlue inbound webhook payload.

    Returns:
        {
            "from_number": str,
            "content": str,
            "message_id": str,
            "is_from_me": bool,
            "timestamp": str,
        }
    """
    payload = payload or {}
    # SendBlue marks our own outbound copies with is_outbound=True.
    is_outbound = bool(payload.get("is_outbound", False))
    return {
        "from_number": payload.get("from_number") or payload.get("number") or "",
        "content": (payload.get("content") or "").strip(),
        "message_id": str(payload.get("message_handle") or payload.get("id") or ""),
        "is_from_me": is_outbound,
        "timestamp": payload.get("date_sent") or payload.get("date_updated") or "",
    }
