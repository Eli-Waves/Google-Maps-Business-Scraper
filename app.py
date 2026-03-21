"""
FastAPI server — WhatsApp webhook + dashboard
This is what Render runs 24/7.
"""
import os
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import PlainTextResponse
from database import init_db, get_lead_by_phone, update_lead_status, append_message, upsert_lead
from whatsapp import send_message, parse_incoming
from ai_chat import get_ai_reply
from telegram_notify import notify_hot_lead

app = FastAPI(title="Web Agency Bot")

VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN", "my_verify_token_123")


@app.on_event("startup")
def startup():
    init_db()
    print("[✓] App started")


# ── Webhook verification (Meta requires this) ─────────────────────────────────
@app.get("/webhook")
def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_token == VERIFY_TOKEN:
        print("[✓] Webhook verified by Meta")
        return PlainTextResponse(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


# ── Incoming WhatsApp messages ─────────────────────────────────────────────────
@app.post("/webhook")
async def receive_message(request: Request):
    data = await request.json()

    parsed = parse_incoming(data)
    if not parsed:
        return {"status": "ignored"}

    phone = parsed["phone"]
    user_message = parsed["message"]

    print(f"[↓] Message from {phone}: {user_message}")

    # Save user message
    append_message(phone, "user", user_message)

    # Get lead info — if unknown, create a temporary record so we can still reply
    lead = get_lead_by_phone(phone)
    if not lead:
        upsert_lead({
            "name": "Unknown",
            "phone": phone,
            "website": None,
            "category": None,
            "address": None,
            "maps_url": None,
        })
        lead = get_lead_by_phone(phone)

    if lead["status"] in ("converted", "not_interested"):
        return {"status": "skipped"}

    # Get AI reply
    reply, is_hot, is_cold = get_ai_reply(phone, user_message)

    # Send reply back
    send_message(phone, reply)
    append_message(phone, "assistant", reply)

    # Update status
    if is_hot:
        update_lead_status(phone, "interested")
        notify_hot_lead(lead)
        print(f"  🔥 HOT LEAD: {lead['name']} ({phone})")
    elif is_cold:
        update_lead_status(phone, "not_interested")
        print(f"  ❌ Not interested: {lead['name']} ({phone})")
    else:
        update_lead_status(phone, "contacted")

    return {"status": "ok"}


# ── Health check ───────────────────────────────────────────────────────────────
@app.get("/")
def health():
    return {"status": "running", "service": "Web Agency Bot 🚀"}
