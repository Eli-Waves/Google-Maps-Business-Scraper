"""
FastAPI server — WhatsApp webhook
This is what Render runs 24/7.
"""
import os
import time
import json
import threading
import httpx as _httpx
from datetime import datetime
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import PlainTextResponse
from groq import Groq
from database import init_db, get_lead_by_phone, update_lead_status, append_message, upsert_lead, get_conversation
from whatsapp import send_message, parse_incoming, first_outreach_message, normalize_phone
from ai_chat import get_ai_reply
from telegram_notify import notify_hot_lead, send_telegram
from scraper import scrape_businesses, get_next_query
from scheduler import run_outreach

app = FastAPI(title="Web Agency Bot")

VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN", "my_verify_token_123")
SECRET_KEY = os.getenv("SECRET_KEY", "webgh_secret")
SELF_URL = "https://google-maps-business-scraper.onrender.com/ping"
OWNER_PHONE = "233530123985"
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


# FIXED: removed auto_scrape_and_outreach thread — was burning Apify credits
# and spamming businesses every 30 mins. Use /run-scrape endpoint manually instead.


def follow_up_hot_leads():
    """Follow up with hot leads after 24hrs if not converted."""
    print("[⏰] Hot lead follow-up checker running...")
    while True:
        try:
            from database import get_conn, row_to_dict
            conn, db = get_conn()
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
            leads_to_follow = [row_to_dict(r, db, cur) for r in rows]
            cur.close()
            conn.close()

            for lead in leads_to_follow:
                if lead.get("phone"):
                    try:
                        msg = "Hey, just checking in — are you still interested in getting a website for your business? We'd love to help."
                        send_message(lead["phone"], msg)
                        append_message(lead["phone"], "assistant", msg)
                        update_lead_status(lead["phone"], "hot_followed_up")
                        print(f"  [↻] Hot lead follow-up sent to {lead['name']}")
                    except Exception as e:
                        print(f"  [!] Follow-up failed: {e}")
                    time.sleep(10)
        except Exception as e:
            print(f"[!] Hot lead follow-up error: {e}")
        time.sleep(3600)


def follow_up_no_reply():
    """Send ONE follow-up to businesses that haven't replied after 24hrs (not 3hrs)."""
    print("[⏰] Follow-up checker running...")
    while True:
        try:
            from database import get_conn, row_to_dict
            conn, db = get_conn()
            cur = conn.cursor()
            # FIXED: changed from 3 hours to 24 hours, and only status='contacted' (not followed_up)
            if db == "pg":
                cur.execute("SELECT * FROM leads WHERE status='contacted' AND updated_at < NOW() - INTERVAL '24 hours'")
            else:
                cur.execute("SELECT * FROM leads WHERE status='contacted' AND updated_at < datetime('now', '-24 hours')")
            rows = [row_to_dict(r, db, cur) for r in cur.fetchall()]
            cur.close()
            conn.close()

            for lead in rows:
                convo = json.loads(lead.get("conversation") or "[]")
                has_reply = any(m["role"] == "user" for m in convo)
                has_outreach = any(m["role"] == "assistant" for m in convo)
                # FIXED: only follow up if first message actually went out
                if not has_reply and has_outreach and lead.get("phone"):
                    try:
                        msg = "Hey, just checking in — did you get my last message?"
                        send_message(lead["phone"], msg)
                        append_message(lead["phone"], "assistant", msg)
                        update_lead_status(lead["phone"], "followed_up")
                        print(f"  [↻] Follow-up sent to {lead['name']} ({lead['phone']})")
                    except Exception as e:
                        print(f"  [!] Follow-up failed: {e}")
                    time.sleep(10)
        except Exception as e:
            print(f"[!] Follow-up error: {e}")
        time.sleep(3600)


def get_stats() -> str:
    from database import get_conn, row_to_dict
    conn, db = get_conn()
    cur = conn.cursor()
    def _c(q): cur.execute(q); return cur.fetchone()[0]
    total = _c("SELECT COUNT(*) FROM leads")
    contacted = _c("SELECT COUNT(*) FROM leads WHERE status='contacted'")
    interested = _c("SELECT COUNT(*) FROM leads WHERE status='interested'")
    converted = _c("SELECT COUNT(*) FROM leads WHERE status='converted'")
    new = _c("SELECT COUNT(*) FROM leads WHERE status='new'")
    cur.execute("SELECT * FROM leads WHERE status='interested' ORDER BY updated_at DESC LIMIT 2")
    hot_rows = [row_to_dict(r, db, cur) for r in cur.fetchall()]
    cur.close(); conn.close()

    hot_summary = ""
    for lead in hot_rows:
        convo = json.loads(lead.get("conversation") or "[]")
        msgs = " | ".join([f"{m['role']}: {m['content'][:60]}" for m in convo[-3:]])
        hot_summary += f"\n- {lead.get('name')} ({lead.get('phone')}): {msgs}"

    prompt = f"""You are a WhatsApp assistant for Web GH agency owner in Ghana. Give a very short 2-3 sentence update then suggest ONE specific next action. Be casual like a friend texting.

Numbers: {total} total, {new} new, {contacted} contacted, {interested} interested, {converted} converted.
Hot leads:{hot_summary if hot_summary else " none yet"}

Max 3 sentences."""

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=200, temperature=0.7,
    )
    return response.choices[0].message.content.strip()


@app.on_event("startup")
def startup():
    init_db()
    threading.Thread(target=self_ping, daemon=True).start()
    # FIXED: removed auto_scrape thread, kept only follow-up threads
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

    if phone in ADMIN_PHONES:
        if not hasattr(app, "_last_owner_msg"):
            app._last_owner_msg = {}
        dedup_key = f"{phone}:{user_message}"
        last = app._last_owner_msg.get(dedup_key, 0)
        if time.time() - last < 10:
            return {"status": "duplicate"}
        app._last_owner_msg[dedup_key] = time.time()

        from database import get_all_leads, get_conn, row_to_dict
        conn, db = get_conn()
        cur = conn.cursor()
        def _count(q):
            cur.execute(q)
            return cur.fetchone()[0]
        total = _count("SELECT COUNT(*) FROM leads")
        new = _count("SELECT COUNT(*) FROM leads WHERE status='new'")
        contacted = _count("SELECT COUNT(*) FROM leads WHERE status='contacted'")
        interested = _count("SELECT COUNT(*) FROM leads WHERE status='interested'")
        converted = _count("SELECT COUNT(*) FROM leads WHERE status='converted'")
        cur.close()
        conn.close()

        all_leads_data = get_all_leads()[:200]

        replied = []
        hot_leads = []
        unknown_people = []
        no_reply = []
        outreach_message_sample = None

        for lead in all_leads_data:
            convo = json.loads(lead.get("conversation") or "[]")[-20:]
            has_user_reply = any(m["role"] == "user" for m in convo)
            is_scraped = lead.get("maps_url") not in (None, "")

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

        specific_lead = None
        for lead in all_leads_data:
            if lead.get("name") and lead["name"].lower() in user_message.lower():
                convo = json.loads(lead.get("conversation") or "[]")
                convo_text = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in convo])
                specific_lead = f"Lead: {lead['name']} | {lead['phone']} | Status: {lead['status']}\nFull conversation:\n{convo_text}"
                break

        system_prompt = f"""You are an AI business assistant for the owner of Web GH, a web design agency in Ghana.

NUMBERS: Total: {total} | New: {new} | Contacted: {contacted} | Interested: {interested} | Converted: {converted}

OUTREACH MESSAGE SENT:
"{outreach_message_sample if outreach_message_sample else 'Not sent yet'}"

HOT LEADS: {chr(10).join(hot_leads) if hot_leads else "None yet"}
REPLIED: {chr(10).join(replied[:10]) if replied else "None yet"}
UNKNOWN TEXTERS: {chr(10).join(unknown_people) if unknown_people else "None"}
NO REPLY: {chr(10).join(no_reply[:10]) if no_reply else "None"}

{f"SPECIFIC LEAD:{chr(10)}{specific_lead}" if specific_lead else ""}

Be casual and short (2-3 sentences). Only trigger actions if explicitly asked.
Actions: [DO:SCRAPE] [DO:OUTREACH] [DO:CONVERTED:phone] [DO:CHAT:phone]"""

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

        msg_lower = user_message.lower()
        if "[DO:SCRAPE]" not in reply and any(w in msg_lower for w in ["scrape", "find businesses", "find leads"]):
            reply += " [DO:SCRAPE]"
        if "[DO:OUTREACH]" not in reply and any(w in msg_lower for w in ["outreach", "message leads", "send messages"]):
            reply += " [DO:OUTREACH]"

        if "[DO:SCRAPE]" in reply:
            reply = reply.replace("[DO:SCRAPE]", "").strip()
            send_message(phone, "On it, finding businesses without websites...")
            def do_scrape():
                from scheduler import scrape_until_target
                saved = scrape_until_target(target=40)
                send_message(OWNER_PHONE, f"Done! Found {saved} new businesses. Messaging them now...")
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
                target_phone = normalize_phone(match.group(1))
                from database import get_conn as _gc2
                c2, d2 = _gc2()
                cu2 = c2.cursor()
                if d2 == "pg":
                    cu2.execute("UPDATE leads SET status='converted', updated_at=NOW() WHERE phone=%s", (target_phone,))
                else:
                    cu2.execute("UPDATE leads SET status='converted' WHERE phone=?", (target_phone,))
                c2.commit(); cu2.close(); c2.close()
                reply = re.sub(r'\[DO:CONVERTED:[^\]]+\]', '', reply).strip()

        if "[DO:CHAT:" in reply:
            import re
            match = re.search(r'\[DO:CHAT:([^\]]+)\]', reply)
            if match:
                target_phone = normalize_phone(match.group(1))
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

    # Regular lead handling
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
        history = get_conversation(phone)
        last_msgs = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in history[-4:]])
        alert = (
            f"Hot lead alert!\n\n"
            f"Name: {lead['name']}\n"
            f"Phone: {lead['phone']}\n"
            f"Category: {lead.get('category') or 'N/A'}\n\n"
            f"Last messages:\n{last_msgs}"
        )
        for admin in ADMIN_PHONES:
            send_message(admin, alert)
        print(f"  🔥 HOT LEAD: {lead['name']} ({phone})")
    else:
        update_lead_status(phone, "contacted")

    return {"status": "ok"}


# FIXED: added /lead/{phone} route to fix the 404 errors from dashboard
@app.get("/lead/{phone}")
def get_lead_detail(phone: str):
    from whatsapp import normalize_phone
    clean_phone = normalize_phone(phone.replace(" ", "").replace("+", ""))
    lead = get_lead_by_phone(clean_phone)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    convo = json.loads(lead.get("conversation") or "[]")
    return {**lead, "conversation": convo}


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
    from database import get_all_leads, get_revenue, get_conn

    try:
        conn, db = get_conn()
        cur = conn.cursor()
        def count(q):
            cur.execute(q)
            return cur.fetchone()[0]
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
