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

# ── Owner/Admin commands ──────────────────────────────────────────────────
    if phone in ADMIN_PHONES:
        import re

        # Dedup
        if not hasattr(app, "_last_owner_msg"):
            app._last_owner_msg = {}
        dedup_key = f"{phone}:{user_message}"
        last = app._last_owner_msg.get(dedup_key, 0)
        if time.time() - last < 10:
            return {"status": "duplicate"}
        app._last_owner_msg[dedup_key] = time.time()
        msg = user_message.strip()
        msg_lower = msg.lower()
        print(f"[DEBUG] msg='{msg}' | startswith_text={msg_lower.startswith('text ')}")

        # ── HARDCODED COMMANDS (no AI needed) ────────────────────────────────

        # TEXT [number] [optional custom message]
        if msg_lower.startswith("text "):
            parts = msg[5:].strip().split()
            phone_tokens = []
            msg_tokens = []
            for t in parts:
                if any(c.isdigit() for c in t) and not msg_tokens:
                    phone_tokens.append(t)
                else:
                    msg_tokens.append(t)
            target_raw = "".join(phone_tokens)
            custom_msg = " ".join(msg_tokens).strip()
            target_phone = normalize_phone(target_raw)
            if not target_phone or len(target_phone) < 9:
                send_message(phone, f"Couldn't parse that number: {target_raw}")
                return {"status": "ok"}
            if not get_lead_by_phone(target_phone):
                upsert_lead({"name": "Manual Contact", "phone": target_phone,
                             "website": None, "category": None, "address": None, "maps_url": None})
            out_msg = custom_msg if custom_msg else first_outreach_message("there")
            print(f"[DEBUG] target_phone='{target_phone}' | out_msg='{out_msg}'")
            try:
                send_message(target_phone, out_msg)
                append_message(target_phone, "assistant", out_msg)
                update_lead_status(target_phone, "contacted")
                send_message(phone, f"✅ Sent to {target_phone}:\n\"{out_msg}\"")
            except Exception as e:
                print(f"[DEBUG] Send failed: {e}")
                send_message(phone, f"❌ Failed to send to {target_phone}: {e}")
            return {"status": "ok"}

        # LIST LEADS / LIST HOT / LIST ALL / LIST REPLIED
        if re.search(r'\blist\b', msg_lower):
            from database import get_all_leads
            all_leads = get_all_leads()

            if "hot" in msg_lower or "interested" in msg_lower:
                leads_out = [l for l in all_leads if l.get("status") == "interested"]
                label = "🔥 Hot leads"
            elif "replied" in msg_lower:
                leads_out = [l for l in all_leads if any(
                    m["role"] == "user" for m in json.loads(l.get("conversation") or "[]")
                )]
                label = "💬 Leads who replied"
            elif "converted" in msg_lower or "client" in msg_lower:
                leads_out = [l for l in all_leads if l.get("status") == "converted"]
                label = "✅ Converted clients"
            elif "new" in msg_lower:
                leads_out = [l for l in all_leads if l.get("status") == "new"]
                label = "🆕 New leads"
            else:
                leads_out = all_leads[:50]
                label = "📋 All leads (first 50)"

            if not leads_out:
                send_message(phone, f"{label}: none yet.")
                return {"status": "ok"}

            lines = [f"{label} ({len(leads_out)}):"]
            for l in leads_out[:30]:
                lines.append(f"• {l.get('name')} — {l.get('phone')} [{l.get('status')}]")
            if len(leads_out) > 30:
                lines.append(f"...and {len(leads_out) - 30} more")

            send_message(phone, "\n".join(lines))
            return {"status": "ok"}

        # STATS
        if any(w in msg_lower for w in ["stats", "status", "how many", "count", "numbers"]):
            from database import get_conn, get_revenue
            conn, db = get_conn()
            cur = conn.cursor()
            def _c(q): cur.execute(q); return cur.fetchone()[0]
            total = _c("SELECT COUNT(*) FROM leads")
            new = _c("SELECT COUNT(*) FROM leads WHERE status='new'")
            contacted = _c("SELECT COUNT(*) FROM leads WHERE status='contacted'")
            followed = _c("SELECT COUNT(*) FROM leads WHERE status='followed_up'")
            interested = _c("SELECT COUNT(*) FROM leads WHERE status='interested'")
            converted = _c("SELECT COUNT(*) FROM leads WHERE status='converted'")
            cur.close(); conn.close()
            revenue = get_revenue()
            send_message(phone,
                f"📊 Web GH Stats:\n"
                f"Total: {total}\n"
                f"New (unsent): {new}\n"
                f"Contacted: {contacted}\n"
                f"Followed up: {followed}\n"
                f"🔥 Interested: {interested}\n"
                f"✅ Converted: {converted}\n"
                f"💰 Revenue: GHS {revenue}"
            )
            return {"status": "ok"}

        # SCRAPE
        if any(w in msg_lower for w in ["scrape", "find businesses", "find leads", "get leads"]):
            send_message(phone, "🔍 Scraping for new businesses...")
            def do_scrape():
                try:
                    from scheduler import scrape_until_target
                    saved = scrape_until_target(target=40)
                    if saved > 0:
                        send_message(phone, f"✅ Found {saved} new businesses. Say 'outreach' to message them.")
                    else:
                        send_message(phone, "⚠️ Scrape returned 0 leads. Apify credits may be exhausted — check console.apify.com")
                except Exception as e:
                    err = str(e)
                    if "402" in err or "Payment" in err:
                        send_message(phone, "❌ Apify credits exhausted. Top up at console.apify.com to scrape again.")
                    else:
                        send_message(phone, f"❌ Scrape failed: {err}")
            threading.Thread(target=do_scrape, daemon=True).start()
            return {"status": "ok"}

        # OUTREACH
        if any(w in msg_lower for w in ["outreach", "message leads", "message them", "send messages", "start outreach"]):
            from database import get_conn
            conn, db = get_conn()
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM leads WHERE status='new'")
            new_count = cur.fetchone()[0]
            cur.close(); conn.close()

            if new_count == 0:
                send_message(phone, "No new leads to contact. Scrape first.")
                return {"status": "ok"}

            send_message(phone, f"📤 Sending outreach to {new_count} new leads...")
            threading.Thread(target=run_outreach, daemon=True).start()
            return {"status": "ok"}

        # MARK CONVERTED: name or number
        converted_match = re.search(r'(mark|convert|converted)[:\s]+(.+)', msg, re.IGNORECASE)
        if converted_match:
            search = converted_match.group(2).strip()
            from database import get_all_leads, get_conn
            all_leads = get_all_leads()
            lead = next((l for l in all_leads if
                search.lower() in (l.get("name") or "").lower() or
                search in (l.get("phone") or "")), None)
            if not lead:
                send_message(phone, f"No lead found matching '{search}'.")
            else:
                conn, db = get_conn()
                cur = conn.cursor()
                if db == "pg":
                    cur.execute("UPDATE leads SET status='converted', updated_at=NOW() WHERE phone=%s", (lead["phone"],))
                else:
                    cur.execute("UPDATE leads SET status='converted' WHERE phone=?", (lead["phone"],))
                conn.commit(); cur.close(); conn.close()
                send_message(phone, f"✅ Marked {lead.get('name')} ({lead.get('phone')}) as converted!")
            return {"status": "ok"}

        # CONVO [name or number] — show full conversation
        convo_match = re.search(r'(convo|conversation|what did|chat)[:\s]+(.+)', msg, re.IGNORECASE)
        if convo_match:
            search = convo_match.group(2).strip()
            from database import get_all_leads
            all_leads = get_all_leads()
            lead = next((l for l in all_leads if
                search.lower() in (l.get("name") or "").lower() or
                search in (l.get("phone") or "")), None)
            if not lead:
                send_message(phone, f"No lead found matching '{search}'.")
            else:
                convo = json.loads(lead.get("conversation") or "[]")
                if not convo:
                    send_message(phone, f"{lead.get('name')} hasn't replied yet.")
                else:
                    lines = [f"💬 {lead.get('name')} ({lead.get('phone')}) — {lead.get('status')}:"]
                    for m in convo[-10:]:
                        role = "You" if m["role"] == "assistant" else lead.get("name", "Them")
                        lines.append(f"{role}: {m['content']}")
                    send_message(phone, "\n".join(lines))
            return {"status": "ok"}

        # BROADCAST [category] [message] — send to all leads in a category
        broadcast_match = re.search(r'broadcast[:\s]+(\w+)[:\s]+(.+)', msg, re.IGNORECASE)
        if broadcast_match:
            category = broadcast_match.group(1).strip()
            bcast_msg = broadcast_match.group(2).strip()
            from database import get_all_leads
            all_leads = get_all_leads()
            targets = [l for l in all_leads if
                category.lower() in (l.get("category") or "").lower() and l.get("phone")]
            if not targets:
                send_message(phone, f"No leads found in category '{category}'.")
                return {"status": "ok"}
            send_message(phone, f"📢 Broadcasting to {len(targets)} leads in '{category}'...")
            def do_broadcast():
                success = 0
                for lead in targets:
                    try:
                        send_message(lead["phone"], bcast_msg, typing_delay=False)
                        append_message(lead["phone"], "assistant", bcast_msg)
                        success += 1
                        time.sleep(5)
                    except Exception as e:
                        print(f"[!] Broadcast failed for {lead['phone']}: {e}")
                send_message(phone, f"✅ Broadcast done: {success}/{len(targets)} sent.")
            threading.Thread(target=do_broadcast, daemon=True).start()
            return {"status": "ok"}

        # HELP
        if any(w in msg_lower for w in ["help", "commands", "what can"]):
            send_message(phone,
                "🤖 Web GH Bot Commands:\n\n"
                "• *text [number]* — send outreach to any number\n"
                "• *text [number] [message]* — send custom message\n"
                "• *list hot* — show interested leads\n"
                "• *list replied* — show leads who replied\n"
                "• *list new* — show unsent leads\n"
                "• *list all* — show all leads\n"
                "• *stats* — full breakdown\n"
                "• *scrape* — find new businesses\n"
                "• *outreach* — message all new leads\n"
                "• *convo [name]* — see full conversation\n"
                "• *mark converted [name]* — mark as client\n"
                "• *broadcast [category] [message]* — bulk message by category\n"
                "• *help* — show this"
            )
            return {"status": "ok"}

        # ── AI FALLBACK for anything else ────────────────────────────────────
        from database import get_all_leads, get_conn, row_to_dict, get_revenue
        conn, db = get_conn()
        cur = conn.cursor()
        def _count(q): cur.execute(q); return cur.fetchone()[0]
        total = _count("SELECT COUNT(*) FROM leads")
        new = _count("SELECT COUNT(*) FROM leads WHERE status='new'")
        contacted = _count("SELECT COUNT(*) FROM leads WHERE status='contacted'")
        interested = _count("SELECT COUNT(*) FROM leads WHERE status='interested'")
        converted = _count("SELECT COUNT(*) FROM leads WHERE status='converted'")
        cur.close(); conn.close()

        system_prompt = f"""You are the AI assistant for Icon, owner of Web GH agency in Ghana.
Stats: {total} total | {new} new | {contacted} contacted | {interested} interested | {converted} converted

Be casual, short (2-3 sentences max). No bullet points. Don't trigger any actions — just answer."""

        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            max_tokens=150,
            temperature=0.7,
        )
        reply = response.choices[0].message.content.strip()
        send_message(phone, reply)
        return {"status": "ok"}
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
