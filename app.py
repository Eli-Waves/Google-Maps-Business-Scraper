"""
FastAPI server — WhatsApp webhook
This is what Render runs 24/7.
"""
import os
import time
import threading
import asyncio
import httpx as _httpx
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import PlainTextResponse
from database import init_db, get_lead_by_phone, update_lead_status, append_message, upsert_lead, get_conversation
from whatsapp import send_message, parse_incoming
from ai_chat import get_ai_reply
from telegram_notify import notify_hot_lead, send_telegram
from scraper import scrape_google_maps, ensure_chromium
from scheduler import get_next_query, run_outreach

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
    ensure_chromium()  # Install Chromium at startup so it's ready
    threading.Thread(target=self_ping, daemon=True).start()
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


@app.get("/run-scrape")
async def trigger_scrape(key: str = Query(None)):
    if key != SECRET_KEY:
        raise HTTPException(status_code=403, detail="Invalid key")

    query = get_next_query()
    print(f"[+] Scraping: {query}")

    try:
        leads = await scrape_google_maps(query, limit=50)
        saved = 0
        details = []

        for lead in leads:
            if lead.get("phone"):
                upsert_lead(lead)
                saved += 1
                details.append(f"• {lead.get('name','?')} | {lead.get('phone')} | {lead.get('category','N/A')}")

        if details:
            chunk_size = 30
            for i in range(0, len(details), chunk_size):
                chunk = details[i:i+chunk_size]
                msg = f"✅ <b>Scrape Done!</b>\n<b>Query:</b> {query}\n<b>Saved:</b> {saved}\n\n" + "\n".join(chunk)
                send_telegram(msg)

        return {"status": "done", "query": query, "saved": saved}

    except Exception as e:
        send_telegram(f"❌ Scrape failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/run-outreach")
def trigger_outreach(key: str = Query(None)):
    if key != SECRET_KEY:
        raise HTTPException(status_code=403, detail="Invalid key")
    threading.Thread(target=run_outreach, daemon=True).start()
    return {"status": "Outreach started"}


@app.get("/")
def health():
    return {"status": "running", "service": "Web Agency Bot"}


@app.get("/ping")
def ping():
    return "pong"
