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
        "SELECT name, phone, conversation FROM leads WHERE status='interested' ORDER BY updated_at DESC LIMIT 2"
    ).fetchall()

    hot_summary = ""
    for lead in hot_leads:
        convo = json.loads(lead["conversation"] or "[]")
        last_msgs = convo[-3:] if convo else []
        msgs = " | ".join([f"{m['role']}: {m['content'][:60]}" for m in last_msgs])
        hot_summary += f"\n- {lead['name']} ({lead['phone']}): {msgs}"

    conn.close()

    prompt = f"""You are a WhatsApp assistant for Web GH agency owner in Ghana. Give a very short 2-3 sentence update then suggest ONE specific next action based on the numbers. Be casual like a friend texting.

Numbers: {total} total, {new} new, {contacted} contacted, {interested} interested, {converted} converted.
Hot leads:{hot_summary if hot_summary else " none yet"}

Logic for suggestion:
- If new > 0: suggest running outreach
- If new == 0 and total < 10: suggest scraping more businesses
- If interested > 0: suggest following up with the hot leads
- If contacted > 20 and interested == 0: suggest scraping a different category

Keep it short and casual. Max 3 sentences total including the suggestion."""

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

        # Deduplicate — ignore if same message was processed in last 10 seconds
        if not hasattr(app, "_last_owner_msg"):
            app._last_owner_msg = {}
        last = app._last_owner_msg.get(user_message, 0)
        if time.time() - last < 10:
            return {"status": "duplicate"}
        app._last_owner_msg[user_message] = time.time()

        # Pull all relevant data
        conn = sqlite3.connect("leads.db")
        conn.row_factory = sqlite3.Row
        total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        new = conn.execute("SELECT COUNT(*) FROM leads WHERE status='new'").fetchone()[0]
        contacted = conn.execute("SELECT COUNT(*) FROM leads WHERE status='contacted'").fetchone()[0]
        interested = conn.execute("SELECT COUNT(*) FROM leads WHERE status='interested'").fetchone()[0]
        converted = conn.execute("SELECT COUNT(*) FROM leads WHERE status='converted'").fetchone()[0]

        # Leads who have REPLIED (conversation has user messages)
        all_leads = conn.execute("SELECT * FROM leads ORDER BY updated_at DESC").fetchall()
        
        replied = []
        hot_leads = []
        unknown_people = []
        no_reply = []

        for lead in all_leads:
            convo = json.loads(lead["conversation"] or "[]")
            has_user_reply = any(m["role"] == "user" for m in convo)
            is_scraped = lead["maps_url"] not in (None, "")
            
            last_msgs = " | ".join([f"{m['role']}: {m['content'][:60]}" for m in convo[-3:]])
            entry = f"- {lead['name']} ({lead['phone']}) [{lead['status']}]: {last_msgs}"

            if lead["status"] == "interested":
                hot_leads.append(entry)
            elif not is_scraped and has_user_reply:
                unknown_people.append(entry)
            elif has_user_reply:
                replied.append(entry)
            elif lead["status"] == "contacted":
                no_reply.append(f"- {lead['name']} ({lead['phone']})")

        conn.close()

        # Check if asking about specific lead
        specific_lead = None
        conn2 = sqlite3.connect("leads.db")
        conn2.row_factory = sqlite3.Row
        for lead in conn2.execute("SELECT * FROM leads").fetchall():
            if lead["name"] and lead["name"].lower() in user_message.lower():
                convo = json.loads(lead["conversation"] or "[]")
                convo_text = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in convo])
                specific_lead = f"Lead: {lead['name']} | {lead['phone']} | Status: {lead['status']}\nFull conversation:\n{convo_text}"
                break
        conn2.close()

        system_prompt = f"""You are an AI business assistant for the owner of Web GH, a web design agency in Ghana. You have FULL visibility into all conversations.

NUMBERS: Total: {total} | New: {new} | Contacted: {contacted} | Interested: {interested} | Converted: {converted}

HOT LEADS (interested, with conversations):
{chr(10).join(hot_leads) if hot_leads else "None yet"}

LEADS WHO REPLIED (but not yet interested):
{chr(10).join(replied[:10]) if replied else "None yet"}

UNKNOWN PEOPLE WHO TEXTED THE BOT (not from scrape):
{chr(10).join(unknown_people) if unknown_people else "None"}

CONTACTED BUT NO REPLY YET:
{chr(10).join(no_reply[:10]) if no_reply else "None"} {"...and more" if len(no_reply) > 10 else ""}

{f"SPECIFIC LEAD FULL CONVERSATION:{chr(10)}{specific_lead}" if specific_lead else ""}

RULES:
- Be casual and short (2-3 sentences max)
- ONLY trigger actions if owner EXPLICITLY asks
- If owner asks about a specific business, use their conversation data
- If someone outside the scrape texted, mention it — could be a potential client
- No bullet points

ACTIONS (only if explicitly asked):
[DO:SCRAPE] - find new businesses
[DO:OUTREACH] - message new leads  
[DO:CONVERTED:phone] - mark as converted"""

        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            max_tokens=200,
            temperature=0.7,
        )

        reply = response.choices[0].message.content.strip()

        # Fallback keyword detection in case AI misses the tags
        msg_lower = user_message.lower()
        if "[DO:SCRAPE]" not in reply and any(w in msg_lower for w in ["scrape", "find businesses", "find leads", "search for"]):
            reply += " [DO:SCRAPE]"
        if "[DO:OUTREACH]" not in reply and any(w in msg_lower for w in ["outreach", "message leads", "send messages", "message them"]):
            reply += " [DO:OUTREACH]"

        # Handle action tags
        if "[DO:SCRAPE]" in reply:
            reply = reply.replace("[DO:SCRAPE]", "").strip()
            def do_scrape():
                query = get_next_query()
                leads = scrape_businesses(query, limit=20)
                saved = 0
                for lead in leads:
                    if lead.get("phone"):
                        upsert_lead(lead)
                        saved += 1
                send_message(OWNER_PHONE, f"Done! Saved {saved} businesses from: {query}. Messaging them in 10 seconds...")
                time.sleep(10)
                run_outreach()
            threading.Thread(target=do_scrape, daemon=True).start()

        if "[DO:OUTREACH]" in reply:
            reply = reply.replace("[DO:OUTREACH]", "").strip()
            threading.Thread(target=run_outreach, daemon=True).start()

        if "[DO:CONVERTED:" in reply:
            import re
            match = re.search(r'\[DO:CONVERTED:([^\]]+)\]', reply)
            if match:
                target_phone = match.group(1)
                conn2 = sqlite3.connect("leads.db")
                conn2.execute("UPDATE leads SET status='converted' WHERE phone=?", (target_phone,))
                conn2.commit()
                conn2.close()
                reply = re.sub(r'\[DO:CONVERTED:[^\]]+\]', '', reply).strip()

        send_message(phone, reply)
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

    def delayed_outreach():
        time.sleep(10)
        run_outreach()
    threading.Thread(target=delayed_outreach, daemon=True).start()

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
