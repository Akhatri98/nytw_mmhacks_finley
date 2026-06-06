import hmac
import hashlib
from typing import Optional
import httpx
from config import LOCAL_MODE, MY_DEDICATED_NUMBER, BASE_URL, SENDBLUE_HEADERS, SENDBLUE_WEBHOOK_SECRET

def verify_sendblue_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
    if not signature_header or not SENDBLUE_WEBHOOK_SECRET:
        return False
    expected = hmac.new(
        SENDBLUE_WEBHOOK_SECRET.encode(),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)

async def send_imessage(to_number: str, text: str) -> None:
    if LOCAL_MODE:
        print(f"\n💬 [Sendblue iMessage → {to_number}]\n{text}")
        return
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{BASE_URL}/send-message",
            json={"from_number": MY_DEDICATED_NUMBER, "number": to_number, "content": text},
            headers=SENDBLUE_HEADERS,
        )

async def send_imessage_with_attachment(to_number: str, text: str, media_url: str) -> None:
    if LOCAL_MODE:
        print(f"\n💬 [Sendblue iMessage → {to_number}]\n{text}\n🖼️  [Receipt]: {media_url}")
        return
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{BASE_URL}/send-message",
            json={
                "from_number": MY_DEDICATED_NUMBER,
                "number":      to_number,
                "content":     text,
                "media_url":   media_url,
            },
            headers=SENDBLUE_HEADERS,
        )

async def set_typing_indicator(to_number: str, active: bool) -> None:
    if LOCAL_MODE:
        return
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{BASE_URL}/typing-indicator",
            json={"phone_number": to_number, "status": active},
            headers=SENDBLUE_HEADERS,
        )