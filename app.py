"""
FastAPI server — WhatsApp webhook
This is what Render runs 24/7.
"""
import os
import time
import json
import threading
import sqlite3
import httpx as _httpx
from datetime import datetime
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import PlainTextResponse
from groq import Groq
from database import init_db, get_lead_by_phone, update_lead_status, append_message, upsert_lead, get_conversation
from whatsapp import send_message, parse_incoming
from ai_chat import get_ai_reply
from telegram_notify import notify_hot_lead, send_telegram
from scraper import scrape_businesses, get_next_query
from scheduler import run_outreach

app = FastAPI(title="Web Agency Bot")

VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN", "my_verify_token_123")
SECRET_KEY = os.getenv("SECRET_KEY", "webgh_secret")
SELF_URL = "https://google-maps-business-scraper.onrender.com/ping"
OWNER_PHONE = "233530123985"
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))


def self_ping():
    time.sleep(60)
    while True:
        try:
            _httpx.get(SELF_URL, timeout=10)
            print("[♻] Self-ping OK")
        except Exception as e:
            print(f"[!] Self-ping failed: {e}")
        time.sleep(240)


def auto_scrape_and_outreach():
    """Runs daily at 12pm automatically."""
    print("[⏰] Auto scheduler running...")
    while True:
        now = datetime.now()
        if now.hour == 12 and now.minute == 0:
            send_message(OWNER_PHONE, "Daily auto-scrape starting now...")
            query = get_next_query()
            leads = scrape_businesses(query, limit=20)
            saved = 0
            for lead in leads:
                if lead.get("phone"):
                    upsert_lead(lead)
                    saved += 1
            send_message(OWNER_PHONE, f"Scraped {saved} businesses. Starting outreach in 30 mins...")
            time.sleep(1800)
            run_outreach()
            time.sleep(60)
        time.sleep(30)


def get_stats() -> str:
    conn = sqlite3.connect("leads.db")
    conn.row_factory = sqlite3.Row
    total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    contacted = conn.execute("SELECT COUNT(*) FROM leads WHERE status='contacted'").fetchone()[0]
    interested = conn.execute("SELECT COUNT(*) FROM leads WHERE status='interested'").fetchone()[0]
    converted = conn.execute("SELECT COUNT(*) FROM leads WHERE status='converted'").fetchone()[0]
    new = conn.execute("SELECT COUNT(*) FROM leads WHERE status='new'").fetchone()[0]

    hot_leads = conn.execute(
        "SELECT name, phone, conversation FROM leads WHERE status='interested' ORDER BY updated_at DESC LIMIT 3"
    ).fetchall()

    hot_summary = ""
    for lead in hot_leads:
        convo = json.loads(lead["conversation"] or "[]")
        last_msgs = convo[-3:] if convo else []
        msgs = " | ".join([f"{m['role']}: {m['content'][:60]}" for m in last_msgs])
        hot_summary += f"\n- {lead['name']} ({lead['phone']}): {msgs}"

    conn.close()

    prompt = f"""You are a business assistant giving a WhatsApp briefing to the owner of Web GH, a web design agency in Ghana.

Data:
- Total businesses: {total}
- New (not messaged): {new}
- Contacted: {contacted}
- Interested (hot leads): {interested}
- Converted to clients: {converted}

Recent hot lead conversations:{hot_summary if hot_summary else " None yet"}

Give a short friendly WhatsApp-style briefing (max 5 sentences). Highlight hot leads, summarize conversations, suggest one next action. No bullet points."""

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=200,
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()


def get_conversation_summary(search_term: str) -> str:
    """Get AI summary of a specific lead's conversation."""
    conn = sqlite3.connect("leads.db")
    conn.row_factory = sqlite3.Row

    # Search by name or phone
    row = conn.execute(
        "SELECT * FROM leads WHERE phone LIKE ? OR LOWER(name) LIKE ?",
        (f"%{search_term}%", f"%{search_term.lower()}%")
    ).fetchone()
    conn.close()

    if not row:
        return f"No lead found matching '{search_term}'."

    lead = dict(row)
    convo = json.loads(lead.get("conversation") or "[]")

    if not convo:
        return f"{lead['name']} hasn't replied yet."

    convo_text = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in convo])

    prompt = f"""Summarize this WhatsApp sales conversation for the business owner of Web GH.
Lead: {lead['name']} | {lead['phone']} | Status: {lead['status']}

Conversation:
{convo_text}

Give a 3-4 sentence summary: what they said, their interest level, any objections, and what to do next. Be direct and conversational."""

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=200,
        temperature=0.7,
    )
    return f"{lead['name']} ({lead['phone']}):\n\n" + response.choices[0].message.content.strip()


def mark_converted(search_term: str) -> str:
    """Mark a lead as converted."""
    conn = sqlite3.connect("leads.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM leads WHERE phone LIKE ? OR LOWER(name) LIKE ?",
        (f"%{search_term}%", f"%{search_term.lower()}%")
    ).fetchone()

    if not row:
        conn.close()
        return f"No lead found matching '{search_term}'."

    conn.execute("UPDATE leads SET status='converted' WHERE phone=?", (row["phone"],))
    conn.commit()
    conn.close()
    return f"Marked {row['name']} as converted! Well done on closing the deal."


@app.on_event("startup")
def startup():
    init_db()
    threading.Thread(target=self_ping, daemon=True).start()
    threading.Thread(target=auto_scrape_and_outreach, daemon=True).start()
    print("[✓] App started")


@app.get("/webhook")
def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_token == VERIFY_TOKEN:
        return PlainTextResponse(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhook")
async def receive_message(request: Request):
    data = await request.json()

    parsed = parse_incoming(data)
    if not parsed:
        return {"status": "ignored"}

    phone = parsed["phone"]
    user_message = parsed["message"].strip()

    print(f"[↓] Message from {phone}: {user_message}")

    # ── Owner commands ────────────────────────────────────────────────────────
    if phone == OWNER_PHONE:

        # Use AI to interpret what the owner wants
        intent_response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{
                "role": "system",
                "content": """You are a command interpreter for a WhatsApp sales bot. 
Classify the owner's message into exactly one of these intents and respond with ONLY the intent name and any extracted parameter, nothing else:

STATS - owner wants a progress report or asks how things are going
SCRAPE:[query] - owner wants to find new businesses. Extract the search query if mentioned, else use "auto"
OUTREACH - owner wants to message new leads
CONVERSATION:[name_or_phone] - owner wants to see or ask about a specific lead's conversation
CONVERTED:[name_or_phone] - owner says a deal was closed or a client was won
HELP - owner wants to know what commands are available
UNKNOWN - none of the above

Examples:
"how's it going?" -> STATS
"find restaurants in Kumasi" -> SCRAPE:restaurants in Kumasi Ghana
"message the new leads" -> OUTREACH
"what did Treehouse say?" -> CONVERSATION:Treehouse
"Treehouse just paid" -> CONVERTED:Treehouse
"what can you do?" -> HELP"""
            }, {
                "role": "user",
                "content": user_message
            }],
            max_tokens=50,
            temperature=0,
        )

        intent = intent_response.choices[0].message.content.strip()
        print(f"[Owner intent]: {intent}")

        if intent.startswith("STATS"):
            send_message(phone, get_stats())

        elif intent.startswith("SCRAPE"):
            parts = intent.split(":", 1)
            query = parts[1].strip() if len(parts) > 1 and parts[1].strip() != "auto" else None
            send_message(phone, "On it, finding new businesses now...")
            def do_scrape(q=query):
                actual_query = q or get_next_query()
                leads = scrape_businesses(actual_query, limit=20)
                saved = 0
                for lead in leads:
                    if lead.get("phone"):
                        upsert_lead(lead)
                        saved += 1
                send_message(OWNER_PHONE, f"Done! Saved {saved} new businesses from: {actual_query}")
            threading.Thread(target=do_scrape, daemon=True).start()

        elif intent.startswith("OUTREACH"):
            send_message(phone, "Starting outreach now...")
            threading.Thread(target=run_outreach, daemon=True).start()

        elif intent.startswith("CONVERSATION"):
            parts = intent.split(":", 1)
            search = parts[1].strip() if len(parts) > 1 else ""
            if search:
                send_message(phone, get_conversation_summary(search))
            else:
                send_message(phone, "Who do you want to know about? Say their name or number.")

        elif intent.startswith("CONVERTED"):
            parts = intent.split(":", 1)
            search = parts[1].strip() if len(parts) > 1 else ""
            if search:
                send_message(phone, mark_converted(search))
            else:
                send_message(phone, "Who closed the deal? Say their name or number.")

        elif intent.startswith("HELP"):
            send_message(phone, (
                "Just talk to me naturally! For example:\n\n"
                "\"How's it going?\" - get a progress report\n"
                "\"Find salons in Accra\" - scrape new leads\n"
                "\"Message the new leads\" - start outreach\n"
                "\"What did Treehouse say?\" - see a conversation\n"
                "\"Treehouse just paid\" - mark as converted"
            ))

        else:
            send_message(phone, "I didn't quite get that. Try asking 'how's it going?' or 'help' for options.")

        return {"status": "ok"}

    # ── Regular lead handling ─────────────────────────────────────────────────
    append_message(phone, "user", user_message)

    lead = get_lead_by_phone(phone)
    if not lead:
        upsert_lead({"name": "Unknown", "phone": phone, "website": None, "category": None, "address": None, "maps_url": None})
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
def trigger_scrape(key: str = Query(None)):
    if key != SECRET_KEY:
        raise HTTPException(status_code=403, detail="Invalid key")
    query = get_next_query()
    leads = scrape_businesses(query, limit=20)
    saved = 0
    details = []
    for lead in leads:
        if lead.get("phone"):
            upsert_lead(lead)
            saved += 1
            details.append(f"• {lead.get('name','?')} | {lead.get('phone')} | {lead.get('category','N/A')}")
    if details:
        send_telegram(f"Scrape Done!\nQuery: {query}\nSaved: {saved}\n\n" + "\n".join(details[:30]))
    return {"status": "done", "query": query, "saved": saved}


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
