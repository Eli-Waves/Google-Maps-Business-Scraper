"""
FastAPI server — WhatsApp webhook
This is what Render runs 24/7.
"""
import os
import time
import threading
import httpx as _httpx
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import PlainTextResponse
from database import init_db, get_lead_by_phone, update_lead_status, append_message, upsert_lead, get_conversation
from whatsapp import send_message, parse_incoming
from ai_chat import get_ai_reply
from telegram_notify import notify_hot_lead
from scheduler import start_scheduler, run_scrape, run_outreach

app = FastAPI(title="Web Agency Bot")

VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN", "my_verify_token_123")
SECRET_KEY = os.getenv("SECRET_KEY", "webgh_secret")
SELF_URL = "https://google-maps-business-scraper.onrender.com/ping"


def self_ping():
    time.sleep(60)
    while True:
        try:
            _httpx.get(SELF_URL, timeout=10)
            print("[♻] Self-ping OK")
        except Exception as e:
            print(f"[!] Self-ping failed: {e}")
        time.sleep(240)


@app.on_event("startup")
def startup():
    init_db()
    threading.Thread(target=self_ping, daemon=True).start()
    start_scheduler()
    print("[✓] App started")


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


@app.post("/webhook")
async def receive_message(request: Request):
    data = await request.json()

    parsed = parse_incoming(data)
    if not parsed:
        return {"status": "ignored"}

    phone = parsed["phone"]
    user_message = parsed["message"]

    print(f"[↓] Message from {phone}: {user_message}")

    append_message(phone, "user", user_message)

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

    if lead["status"] in ("converted",):
        return {"status": "skipped"}

    reply, is_hot, _ = get_ai_reply(phone, user_message)

    send_message(phone, reply)
    append_message(phone, "assistant", reply)

    if is_hot and lead["status"] != "interested":
        history = get_conversation(phone)
        convo_text = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in history[-10:]])
        enriched_lead = dict(lead)
        enriched_lead["conversation_summary"] = convo_text
        update_lead_status(phone, "interested")
        notify_hot_lead(enriched_lead)
        print(f"  🔥 HOT LEAD: {lead['name']} ({phone})")
    else:
        update_lead_status(phone, "contacted")

    return {"status": "ok"}


# ── Manual triggers (protected by secret key) ─────────────────────────────────
@app.get("/run-scrape")
def trigger_scrape(key: str = Query(None)):
    if key != SECRET_KEY:
        raise HTTPException(status_code=403, detail="Invalid key")
    threading.Thread(target=run_scrape, daemon=True).start()
    return {"status": "Scrape started in background"}


@app.get("/run-outreach")
def trigger_outreach(key: str = Query(None)):
    if key != SECRET_KEY:
        raise HTTPException(status_code=403, detail="Invalid key")
    threading.Thread(target=run_outreach, daemon=True).start()
    return {"status": "Outreach started in background"}


@app.get("/")
def health():
    return {"status": "running", "service": "Web Agency Bot"}


@app.get("/ping")
def ping():
    return "pong"
