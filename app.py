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
from whatsapp import send_message, parse_incoming, first_outreach_message
from ai_chat import get_ai_reply
from telegram_notify import notify_hot_lead, send_telegram
from scraper import scrape_businesses, get_next_query
from scheduler import run_outreach

app = FastAPI(title="Web Agency Bot")

VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN", "my_verify_token_123")
SECRET_KEY = os.getenv("SECRET_KEY", "webgh_secret")
SELF_URL = "https://google-maps-business-scraper.onrender.com/ping"
OWNER_PHONE = "233530123985"   # Icon
ADMIN_PHONES = {
    "233530123985": "Icon",
    "233557808489": "Eli",
}
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
    """Runs scrape + outreach every 30 minutes automatically."""
    print("[⏰] Auto scheduler running — every 30 mins...")
    time.sleep(60)
    while True:
        try:
            from scheduler import scrape_until_target
            saved = scrape_until_target(target=40)
            if saved > 0:
                for admin in ADMIN_PHONES:
                    send_message(admin, f"Auto-scraped {saved} new businesses without websites. Messaging them now...", typing_delay=False)
                time.sleep(10)
                run_outreach()
        except Exception as e:
            print(f"[!] Auto scrape error: {e}")
        time.sleep(1800)


def follow_up_hot_leads():
    """Follow up with hot leads after 24hrs if not converted."""
    print("[⏰] Hot lead follow-up checker running...")
    while True:
        try:
            conn, db = __import__('database').get_conn()
            cur = conn.cursor()
            if db == "pg":
                cur.execute("""
                    SELECT * FROM leads 
                    WHERE status = 'interested' 
                    AND updated_at < NOW() - INTERVAL '24 hours'
                """)
            else:
                cur.execute("""
                    SELECT * FROM leads 
                    WHERE status = 'interested' 
                    AND updated_at < datetime('now', '-24 hours')
                """)
            rows = cur.fetchall()
            from database import row_to_dict
            leads_to_follow = [row_to_dict(r, db, cur) for r in rows]
            cur.close()
            conn.close()

            for lead in leads_to_follow:
                if lead.get("phone"):
                    try:
                        msg = "Hey, just checking in — are you still interested in getting a website for your business? We'd love to help."
                        send_message(lead["phone"], msg)
                        append_message(lead["phone"], "assistant", msg)
                        print(f"  [↻] Hot lead follow-up sent to {lead['name']}")
                    except Exception as e:
                        print(f"  [!] Follow-up failed: {e}")
                    time.sleep(10)
        except Exception as e:
            print(f"[!] Hot lead follow-up error: {e}")
        time.sleep(3600)


def follow_up_no_reply():
    """Send a follow-up to businesses that haven't replied after 3 hours."""
    print("[⏰] Follow-up checker running...")
    while True:
        try:
            from database import get_conn as _gc, row_to_dict as _rtd
            _conn, _db = _gc()
            _cur = _conn.cursor()
            if _db == "pg":
                _cur.execute("SELECT * FROM leads WHERE status='contacted' AND updated_at < NOW() - INTERVAL '3 hours'")
            else:
                _cur.execute("SELECT * FROM leads WHERE status='contacted' AND updated_at < datetime('now', '-3 hours')")
            rows = [_rtd(r, _db, _cur) for r in _cur.fetchall()]
            _cur.close()
            _conn.close()

            for lead in rows:
                convo = json.loads(lead.get("conversation") or "[]")
                has_reply = any(m["role"] == "user" for m in convo)
                if not has_reply and lead.get("phone"):
                    try:
                        msg = "Hey, just checking in — did you get my last message?"
                        send_message(lead["phone"], msg)
                        append_message(lead["phone"], "assistant", msg)
                        print(f"  [↻] Follow-up sent to {lead['name']} ({lead['phone']})")
                    except Exception as e:
                        print(f"  [!] Follow-up failed: {e}")
                    time.sleep(10)
        except Exception as e:
            print(f"[!] Follow-up error: {e}")
        time.sleep(3600)


def get_stats() -> str:
    from database import get_conn as _gc, row_to_dict as _rtd
    _conn, _db = _gc()
    _cur = _conn.cursor()
    def _c(q): _cur.execute(q); return _cur.fetchone()[0]
    total = _c("SELECT COUNT(*) FROM leads")
    contacted = _c("SELECT COUNT(*) FROM leads WHERE status='contacted'")
    interested = _c("SELECT COUNT(*) FROM leads WHERE status='interested'")
    converted = _c("SELECT COUNT(*) FROM leads WHERE status='converted'")
    new = _c("SELECT COUNT(*) FROM leads WHERE status='new'")
    _cur.execute("SELECT * FROM leads WHERE status='interested' ORDER BY updated_at DESC LIMIT 2")
    hot_rows = [_rtd(r, _db, _cur) for r in _cur.fetchall()]
    _cur.close(); _conn.close()

    hot_summary = ""
    for lead in hot_rows:
        convo = json.loads(lead.get("conversation") or "[]")
        msgs = " | ".join([f"{m['role']}: {m['content'][:60]}" for m in convo[-3:]])
        hot_summary += f"\n- {lead.get('name')} ({lead.get('phone')}): {msgs}"

    prompt = f"""You are a WhatsApp assistant for Web GH agency owner in Ghana. Give a very short 2-3 sentence update then suggest ONE specific next action. Be casual like a friend texting.

Numbers: {total} total, {new} new, {contacted} contacted, {interested} interested, {converted} converted.
Hot leads:{hot_summary if hot_summary else " none yet"}

- If new > 0: suggest running outreach
- If new == 0: suggest scraping more businesses
- If interested > 0: suggest following up with hot leads

Max 3 sentences."""

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=200, temperature=0.7,
    )
    return response.choices[0].message.content.strip()


def get_conversation_summary(search_term: str) -> str:
    from database import get_all_leads
    leads = get_all_leads()
    lead = next((l for l in leads if
        search_term.lower() in (l.get("name") or "").lower() or
        search_term in (l.get("phone") or "")), None)

    if not lead:
        return f"No lead found matching '{search_term}'."

    convo = json.loads(lead.get("conversation") or "[]")
    if not convo:
        return f"{lead.get('name')} hasn't replied yet."

    convo_text = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in convo])
    prompt = f"""Summarize this WhatsApp sales conversation for the Web GH owner.
Lead: {lead.get('name')} | {lead.get('phone')} | Status: {lead.get('status')}
Conversation:\n{convo_text}
3-4 sentences: what they said, interest level, objections, what to do next."""

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=200, temperature=0.7,
    )
    return f"{lead.get('name')} ({lead.get('phone')}):\n\n" + response.choices[0].message.content.strip()


def mark_converted(search_term: str) -> str:
    from database import get_all_leads, get_conn as _gc
    leads = get_all_leads()
    lead = next((l for l in leads if
        search_term.lower() in (l.get("name") or "").lower() or
        search_term in (l.get("phone") or "")), None)

    if not lead:
        return f"No lead found matching '{search_term}'."

    _conn, _db = _gc()
    _cur = _conn.cursor()
    if _db == "pg":
        _cur.execute("UPDATE leads SET status='converted', updated_at=NOW() WHERE phone=%s", (lead["phone"],))
    else:
        _cur.execute("UPDATE leads SET status='converted' WHERE phone=?", (lead["phone"],))
    _conn.commit()
    _cur.close(); _conn.close()
    return f"Marked {lead.get('name')} as converted! Well done on closing the deal."


@app.on_event("startup")
def startup():
    init_db()
    threading.Thread(target=self_ping, daemon=True).start()
    threading.Thread(target=auto_scrape_and_outreach, daemon=True).start()
    threading.Thread(target=follow_up_no_reply, daemon=True).start()
    threading.Thread(target=follow_up_hot_leads, daemon=True).start()
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

    # ── Owner/Admin commands ──────────────────────────────────────────────────
    if phone in ADMIN_PHONES:

        # Deduplicate — ignore if same message was processed in last 10 seconds
        if not hasattr(app, "_last_owner_msg"):
            app._last_owner_msg = {}
        dedup_key = f"{phone}:{user_message}"
        last = app._last_owner_msg.get(dedup_key, 0)
        if time.time() - last < 10:
            return {"status": "duplicate"}
        app._last_owner_msg[dedup_key] = time.time()

        # Pull all relevant data using new DB layer
        from database import get_all_leads, get_conn as _get_conn, row_to_dict as _row_to_dict
        _conn, _db = _get_conn()
        _cur = _conn.cursor()
        def _count(q):
            _cur.execute(q)
            return _cur.fetchone()[0]
        total = _count("SELECT COUNT(*) FROM leads")
        new = _count("SELECT COUNT(*) FROM leads WHERE status='new'")
        contacted = _count("SELECT COUNT(*) FROM leads WHERE status='contacted'")
        interested = _count("SELECT COUNT(*) FROM leads WHERE status='interested'")
        converted = _count("SELECT COUNT(*) FROM leads WHERE status='converted'")
        _cur.close()
        _conn.close()

        all_leads_data = get_all_leads()

        replied = []
        hot_leads = []
        unknown_people = []
        no_reply = []
        outreach_message_sample = None

        for lead in all_leads_data:
            convo = json.loads(lead.get("conversation") or "[]")
            has_user_reply = any(m["role"] == "user" for m in convo)
            is_scraped = lead.get("maps_url") not in (None, "")
            
            # Get the first outreach message sent
            if not outreach_message_sample and convo:
                first = convo[0]
                if first["role"] == "assistant":
                    outreach_message_sample = first["content"]

            last_msgs = " | ".join([f"{m['role']}: {m['content'][:60]}" for m in convo[-3:]])
            entry = f"- {lead.get('name')} ({lead.get('phone')}) [{lead.get('status')}]: {last_msgs}"

            if lead.get("status") == "interested":
                hot_leads.append(entry)
            elif not is_scraped and has_user_reply:
                unknown_people.append(entry)
            elif has_user_reply:
                replied.append(entry)
            elif lead.get("status") == "contacted":
                no_reply.append(f"- {lead.get('name')} ({lead.get('phone')})")

        # Check if asking about specific lead
        specific_lead = None
        for lead in all_leads_data:
            if lead.get("name") and lead["name"].lower() in user_message.lower():
                convo = json.loads(lead.get("conversation") or "[]")
                convo_text = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in convo])
                specific_lead = f"Lead: {lead['name']} | {lead['phone']} | Status: {lead['status']}\nFull conversation:\n{convo_text}"
                break

        system_prompt = f"""You are an AI business assistant for the owner of Web GH, a web design agency in Ghana. You have FULL visibility into all conversations.

NUMBERS: Total: {total} | New: {new} | Contacted: {contacted} | Interested: {interested} | Converted: {converted}

OUTREACH MESSAGE SENT TO ALL BUSINESSES:
"{outreach_message_sample if outreach_message_sample else 'Not sent yet'}"

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
[DO:CONVERTED:phone] - mark as converted
[DO:CHAT:phone] - start chatting with a specific phone number"""

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
        elif "scrape" in msg_lower or "[DO:SCRAPE]" in reply:
            send_message(phone, "On it, finding businesses without websites. Will keep searching until I get 40...")
            def do_scrape():
                from scheduler import scrape_until_target
                saved = scrape_until_target(target=40)
                send_message(OWNER_PHONE, f"Done! Found {saved} businesses without websites. Messaging them now...")
                time.sleep(5)
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
                from database import get_conn as _gc2
                _c2, _d2 = _gc2()
                _cu2 = _c2.cursor()
                if _d2 == "pg":
                    _cu2.execute("UPDATE leads SET status='converted', updated_at=NOW() WHERE phone=%s", (target_phone,))
                else:
                    _cu2.execute("UPDATE leads SET status='converted' WHERE phone=?", (target_phone,))
                _c2.commit(); _cu2.close(); _c2.close()
                reply = re.sub(r'\[DO:CONVERTED:[^\]]+\]', '', reply).strip()

        if "[DO:CHAT:" in reply:
            import re
            match = re.search(r'\[DO:CHAT:([^\]]+)\]', reply)
            if match:
                target_phone = match.group(1).replace(" ", "").replace("+", "")
                # Add as lead if not exists
                if not get_lead_by_phone(target_phone):
                    upsert_lead({"name": "Manual Contact", "phone": target_phone, "website": None, "category": None, "address": None, "maps_url": None})
                msg = first_outreach_message("there")
                send_message(target_phone, msg)
                append_message(target_phone, "assistant", msg)
                update_lead_status(target_phone, "contacted")
                reply = re.sub(r'\[DO:CHAT:[^\]]+\]', '', reply).strip()
                reply += f" Started chatting with {target_phone}."

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
        # Notify all admins via WhatsApp
        history = get_conversation(phone)
        last_msgs = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in history[-4:]])
        alert = (
            f"Hot lead alert!\n\n"
            f"Name: {lead['name']}\n"
            f"Phone: {lead['phone']}\n"
            f"Category: {lead.get('category') or 'N/A'}\n"
            f"Address: {lead.get('address') or 'N/A'}\n\n"
            f"Last messages:\n{last_msgs}"
        )
        for admin in ADMIN_PHONES:
            send_message(admin, alert)
        print(f"  🔥 HOT LEAD: {lead['name']} ({phone})")
    else:
        update_lead_status(phone, "contacted")

    return {"status": "ok"}


@app.get("/run-scrape")
def trigger_scrape(key: str = Query(None)):
    if key != SECRET_KEY:
        raise HTTPException(status_code=403, detail="Invalid key")
    from scheduler import scrape_until_target
    saved = scrape_until_target(target=40)
    if saved > 0:
        send_telegram(f"Scrape Done! Found {saved} businesses without websites.")
        def delayed_outreach():
            time.sleep(10)
            run_outreach()
        threading.Thread(target=delayed_outreach, daemon=True).start()
    return {"status": "done", "saved": saved}


@app.get("/run-outreach")
def trigger_outreach(key: str = Query(None)):
    if key != SECRET_KEY:
        raise HTTPException(status_code=403, detail="Invalid key")
    threading.Thread(target=run_outreach, daemon=True).start()
    return {"status": "Outreach started"}


@app.get("/")
def health():
    return {"status": "running", "service": "Web Agency Bot"}


@app.get("/dashboard")
def dashboard():
    from fastapi.responses import HTMLResponse
    with open("dashboard.html") as f:
        return HTMLResponse(f.read())


@app.get("/dashboard-data")
def dashboard_data():
    from database import get_all_leads, get_revenue
    import sqlite3 as _sq

    # Get counts
    try:
        conn, db = __import__('database').get_conn()
        cur = conn.cursor()
        def count(q, params=()):
            cur.execute(q, params)
            return cur.fetchone()[0]
        if db == "pg":
            total = count("SELECT COUNT(*) FROM leads")
            contacted = count("SELECT COUNT(*) FROM leads WHERE status='contacted'")
            interested = count("SELECT COUNT(*) FROM leads WHERE status='interested'")
            converted = count("SELECT COUNT(*) FROM leads WHERE status='converted'")
        else:
            total = count("SELECT COUNT(*) FROM leads")
            contacted = count("SELECT COUNT(*) FROM leads WHERE status='contacted'")
            interested = count("SELECT COUNT(*) FROM leads WHERE status='interested'")
            converted = count("SELECT COUNT(*) FROM leads WHERE status='converted'")
        cur.close()
        conn.close()
    except:
        total = contacted = interested = converted = 0

    revenue = get_revenue()
    rows = get_all_leads()

    leads = []
    for row in rows:
        convo = json.loads(row.get("conversation") or "[]")
        has_user = any(m["role"] == "user" for m in convo)
        last_msg = convo[-1]["content"][:80] if convo else None
        is_unknown = not row.get("maps_url")

        leads.append({
            "name": row.get("name"),
            "phone": row.get("phone"),
            "category": row.get("category"),
            "status": row.get("status"),
            "replied": has_user,
            "unknown": is_unknown and has_user,
            "last_message": last_msg,
            "conversation": convo,
            "deal_amount": row.get("deal_amount", 0),
        })

    return {
        "total": total,
        "contacted": contacted,
        "interested": interested,
        "converted": converted,
        "revenue": revenue,
        "leads": leads
    }


@app.get("/ping")
def ping():
    return "pong"
