import json
import asyncio
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException, Header

from config import init_supabase, LOCAL_MODE, get_db
from schemas import SendblueWebhookPayload, ProactiveAlertRequest
from utils import verify_sendblue_signature
from database import db_get_authorized_user
from interface import get_session

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_supabase()
    yield

app = FastAPI(title="Finley Interface System Engine", lifespan=lifespan)

@app.post("/webhook")
async def sendblue_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_sendblue_signature: Optional[str] = Header(default=None),
):
    raw_body = await request.body()
    if not verify_sendblue_signature(raw_body, x_sendblue_signature):
        raise HTTPException(status_code=401, detail="Signature Verification Error.")

    try:
        payload = SendblueWebhookPayload(**json.loads(raw_body))
    except Exception:
        raise HTTPException(status_code=422, detail="Malformed payload format parameters.")

    if payload.status:
        return {"status": "receipt_acknowledged"}

    # Confirm user registration via db_get_authorized_user inside intercept
    user_row = await db_get_authorized_user(payload.number)
    if not user_row:
        return {"status": "dropped"} 

    session = get_session(payload.number)
    background_tasks.add_task(session.handle_inbound, payload.content)
    return {"status": "enqueued"}

@app.post("/alert")
async def proactive_alert(payload: ProactiveAlertRequest, background_tasks: BackgroundTasks):
    session = get_session(payload.target_number)
    background_tasks.add_task(
        session.send_proactive_alert,
        payload.asset,
        payload.reason,
        payload.current_price,
    )
    return {"status": "alert_dispatched"}


async def terminal_simulation() -> None:
    init_supabase()
    test_number = "+15558675309"
    
    print("\n⚠️  TEST SYSTEM REGISTRATION CHECK:")
    user_exists = await db_get_authorized_user(test_number)
    if not user_exists:
        print(f" Inserting temporary verification entry for {test_number} inside Supabase...")
        await asyncio.to_thread(
            lambda: get_db().table("authorized_users").insert({
                "phone_number": test_number, 
                "max_trade_usd": 50000.0, 
                "is_active": True
            }).execute()
        )
    
    finley = get_session(test_number)
    print("=" * 65)
    print("     FINLEY STREAMING INTERFACE AGENT - LOCAL TERMINAL CONSOLE")
    print("=" * 65)

    await finley.send_proactive_alert(
        ticker="NVDA",
        trigger_reason="Blackwell platform adoption structural shift",
        current_price=131.25,
    )

    loop = asyncio.get_event_loop()
    while True:
        try:
            user_input = await loop.run_in_executor(None, input, "\niMessage text entry: ")
            if user_input.strip().lower() in ["exit", "quit"]:
                break
            await finley.handle_inbound(user_input)
        except (KeyboardInterrupt, EOFError):
            break

if __name__ == "__main__":
    if LOCAL_MODE:
        asyncio.run(terminal_simulation())
    else:
        import uvicorn
        uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)