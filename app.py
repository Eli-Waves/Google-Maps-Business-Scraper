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
app_state = {"apify_dead": False}

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


def follow_up_hot_leads():
    print("[⏰] Hot lead follow-up checker running...")
    while True:
        try:
            from database import get_conn, row_to_dict
            conn, db = get_conn()
            cur = conn.cursor()
            if db == "pg":
                cur.execute("SELECT * FROM leads WHERE status='interested' AND updated_at < NOW() - INTERVAL '24 hours'")
            else:
                cur.execute("SELECT * FROM leads WHERE status='interested' AND updated_at < datetime('now', '-24 hours')")
            leads_to_follow = [row_to_dict(r, db, cur) for r in cur.fetchall()]
            cur.close(); conn.close()
            for lead in leads_to_follow:
                if lead.get("phone"):
                    try:
                        msg = "Hey, just checking in — are you still interested in getting a website for your business? We'd love to help."
                        send_message(lead["phone"], msg)
                        append_message(lead["phone"], "assistant", msg)
                        update_lead_status(lead["phone"], "hot_followed_up")
                    except Exception as e:
                        print(f"  [!] Follow-up failed: {e}")
                    time.sleep(10)
        except Exception as e:
            print(f"[!] Hot lead follow-up error: {e}")
        time.sleep(3600)


def follow_up_no_reply():
    print("[⏰] Follow-up checker running...")
    while True:
        try:
            from database import get_conn, row_to_dict
            conn, db = get_conn()
            cur = conn.cursor()
            if db == "pg":
                cur.execute("SELECT * FROM leads WHERE status='contacted' AND updated_at < NOW() - INTERVAL '24 hours'")
            else:
                cur.execute("SELECT * FROM leads WHERE status='contacted' AND updated_at < datetime('now', '-24 hours')")
            rows = [row_to_dict(r, db, cur) for r in cur.fetchall()]
            cur.close(); conn.close()
            for lead in rows:
                convo = json.loads(lead.get("conversation") or "[]")
                has_reply = any(m["role"] == "user" for m in convo)
                has_outreach = any(m["role"] == "assistant" for m in convo)
                if not has_reply and has_outreach and lead.get("phone"):
                    try:
                        msg = "Hey, just checking in — did you get my last message?"
                        send_message(lead["phone"], msg)
                        append_message(lead["phone"], "assistant", msg)
                        update_lead_status(lead["phone"], "followed_up")
                    except Exception as e:
                        print(f"  [!] Follow-up failed: {e}")
                    time.sleep(10)
        except Exception as e:
            print(f"[!] Follow-up error: {e}")
        time.sleep(3600)


@app.on_event("startup")
def startup():
    init_db()
    threading.Thread(target=self_ping, daemon=True).start()
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
        import re

        if not hasattr(app, "_last_owner_msg"):
            app._last_owner_msg = {}
        dedup_key = f"{phone}:{user_message}"
        last = app._last_owner_msg.get(dedup_key, 0)
        if time.time() - last < 10:
            return {"status": "duplicate"}
        app._last_owner_msg[dedup_key] = time.time()

        msg = user_message.strip()
        msg_lower = msg.lower()

        # TEXT [number] [optional message]
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
            try:
                send_message(target_phone, out_msg)
                append_message(target_phone, "assistant", out_msg)
                update_lead_status(target_phone, "contacted")
                send_message(phone, f"✅ Sent to {target_phone}:\n\"{out_msg}\"")
            except Exception as e:
                print(f"[!] Send failed: {e}")
                send_message(phone, f"❌ Failed to send to {target_phone}: {e}")
            return {"status": "ok"}

        # LIST
        if re.search(r'\blist\b', msg_lower):
            from database import get_all_leads
            all_leads = get_all_leads()
            if "hot" in msg_lower or "interested" in msg_lower:
                leads_out = [l for l in all_leads if l.get("status") == "interested"]
                label = "🔥 Hot leads"
            elif "replied" in msg_lower:
                leads_out = [l for l in all_leads if any(m["role"] == "user" for m in json.loads(l.get("conversation") or "[]"))]
                label = "💬 Replied"
            elif "converted" in msg_lower or "client" in msg_lower:
                leads_out = [l for l in all_leads if l.get("status") == "converted"]
                label = "✅ Converted"
            elif "new" in msg_lower:
                leads_out = [l for l in all_leads if l.get("status") == "new"]
                label = "🆕 New"
            else:
                leads_out = all_leads[:50]
                label = "📋 All leads"
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
        if any(w in msg_lower for w in ["stats", "how many", "count", "numbers"]):
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
                f"New: {new}\n"
                f"Contacted: {contacted}\n"
                f"Followed up: {followed}\n"
                f"🔥 Interested: {interested}\n"
                f"✅ Converted: {converted}\n"
                f"💰 Revenue: GHS {revenue}"
            )
            return {"status": "ok"}

        # CATEGORY — "how many schools" "send 30 schools" "list churches"
        category_send_match = re.search(r'send\s+(\d+)\s+(.+)', msg_lower)
        category_count_match = re.search(r'how many\s+(.+)', msg_lower)
        category_list_match = re.search(r'show\s+(.+)|all\s+(.+)', msg_lower)

        if category_send_match:
            limit = int(category_send_match.group(1))
            cat = category_send_match.group(2).strip()
            from database import get_all_leads
            all_leads = get_all_leads()
            targets = [l for l in all_leads if cat.lower() in (l.get("category") or "").lower() and l.get("phone")]
            if not targets:
                send_message(phone, f"No leads found in category '{cat}'.")
                return {"status": "ok"}
            batch = targets[:limit]
            send_message(phone, f"📋 {cat} leads ({len(targets)} total, sending you {len(batch)}):")
            lines = []
            for l in batch:
                lines.append(f"• {l.get('name')} — {l.get('phone')}")
            send_message(phone, "\n".join(lines))
            return {"status": "ok"}

        if category_count_match:
            cat = category_count_match.group(1).strip()
            from database import get_all_leads
            all_leads = get_all_leads()
            matches = [l for l in all_leads if cat.lower() in (l.get("category") or "").lower()]
            send_message(phone, f"Found {len(matches)} leads matching '{cat}'.")
            return {"status": "ok"}

        # SCRAPE [query] — "scrape zoos in accra" or just "scrape"
        if msg_lower.startswith("scrape"):
            custom_query = msg[6:].strip() if len(msg) > 6 else None
            if app_state.get("apify_dead"):
                send_message(phone, "❌ Apify credits exhausted. Top up at console.apify.com.")
                return {"status": "ok"}
            send_message(phone, f"🔍 Scraping: {custom_query or 'next category'}...")
            def do_scrape():
                try:
                    from database import upsert_lead as _ul
                    if custom_query:
                        leads = scrape_businesses(custom_query, limit=20)
                        saved = 0
                        for lead in leads:
                            if lead.get("phone"):
                                _ul(lead)
                                saved += 1
                    else:
                        from scheduler import scrape_until_target
                        saved = scrape_until_target(target=40)
                    if saved > 0:
                        send_message(phone, f"✅ Found {saved} new businesses. Say 'outreach' to message them.")
                    else:
                        send_message(phone, "⚠️ 0 leads found. Apify credits may be exhausted.")
                        app_state["apify_dead"] = True
                except Exception as e:
                    err = str(e)
                    if "402" in err or "Payment" in err:
                        app_state["apify_dead"] = True
                        send_message(phone, "❌ Apify credits exhausted. Top up at console.apify.com.")
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

        # MARK CONVERTED
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
                send_message(phone, f"✅ Marked {lead.get('name')} as converted!")
            return {"status": "ok"}

        # CONVO
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

        # BROADCAST
        broadcast_match = re.search(r'broadcast[:\s]+(\w+)[:\s]+(.+)', msg, re.IGNORECASE)
        if broadcast_match:
            category = broadcast_match.group(1).strip()
            bcast_msg = broadcast_match.group(2).strip()
            from database import get_all_leads
            all_leads = get_all_leads()
            targets = [l for l in all_leads if category.lower() in (l.get("category") or "").lower() and l.get("phone")]
            if not targets:
                send_message(phone, f"No leads in category '{category}'.")
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
                        print(f"[!] Broadcast failed: {e}")
                send_message(phone, f"✅ Broadcast done: {success}/{len(targets)} sent.")
            threading.Thread(target=do_broadcast, daemon=True).start()
            return {"status": "ok"}

        # HELP
        if any(w in msg_lower for w in ["help", "commands", "what can"]):
            send_message(phone,
                "🤖 Web GH Commands:\n\n"
                "• text [number] — send to any number\n"
                "• text [number] [message] — custom message\n"
                "• list hot/replied/new/all\n"
                "• stats — full breakdown\n"
                "• how many schools — count by category\n"
                "• send 30 schools — list 30 from category\n"
                "• scrape — next category\n"
                "• scrape zoos in accra — custom scrape\n"
                "• outreach — message new leads\n"
                "• convo [name] — see conversation\n"
                "• mark converted [name]\n"
                "• broadcast [category] [message]\n"
                "• help"
            )
            return {"status": "ok"}

        # AI FALLBACK
        from database import get_all_leads, get_conn, get_revenue
        conn, db = get_conn()
        cur = conn.cursor()
        def _count(q): cur.execute(q); return cur.fetchone()[0]
        total = _count("SELECT COUNT(*) FROM leads")
        new = _count("SELECT COUNT(*) FROM leads WHERE status='new'")
        contacted = _count("SELECT COUNT(*) FROM leads WHERE status='contacted'")
        interested = _count("SELECT COUNT(*) FROM leads WHERE status='interested'")
        converted = _count("SELECT COUNT(*) FROM leads WHERE status='converted'")
        cur.close(); conn.close()

        # Get all categories for context
        from database import get_all_leads
        all_leads = get_all_leads()
        categories = {}
        for l in all_leads:
            cat = (l.get("category") or "unknown").lower()
            categories[cat] = categories.get(cat, 0) + 1
        top_cats = sorted(categories.items(), key=lambda x: x[1], reverse=True)[:10]
        cat_summary = ", ".join([f"{k}({v})" for k, v in top_cats])

        system_prompt = f"""You are the AI assistant for Icon, owner of Web GH agency in Ghana.
Stats: {total} total | {new} new | {contacted} contacted | {interested} interested | {converted} converted
Top categories: {cat_summary}
Be casual, short (2-3 sentences). No bullet points."""

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
            f"🔥 Hot lead!\n\n"
            f"Name: {lead['name']}\n"
            f"Phone: {lead['phone']}\n"
            f"Category: {lead.get('category') or 'N/A'}\n\n"
            f"Last messages:\n{last_msgs}"
        )
        for admin in ADMIN_PHONES:
            send_message(admin, alert)
    else:
        update_lead_status(phone, "contacted")
    return {"status": "ok"}


@app.get("/lead/{phone}")
def get_lead_detail(phone: str):
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
    if app_state.get("apify_dead"):
        return {"status": "error", "message": "Apify credits exhausted"}
    from scheduler import scrape_until_target
    saved = scrape_until_target(target=40)
    if saved > 0:
        send_telegram(f"Scrape Done! Found {saved} businesses.")
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
        def count(q): cur.execute(q); return cur.fetchone()[0]
        total = count("SELECT COUNT(*) FROM leads")
        contacted = count("SELECT COUNT(*) FROM leads WHERE status='contacted'")
        interested = count("SELECT COUNT(*) FROM leads WHERE status='interested'")
        converted = count("SELECT COUNT(*) FROM leads WHERE status='converted'")
        cur.close(); conn.close()
    except:
        total = contacted = interested = converted = 0
    revenue = get_revenue()
    rows = get_all_leads()
    leads = []
    for row in rows:
        convo = json.loads(row.get("conversation") or "[]")
        has_user = any(m["role"] == "user" for m in convo)
        last_msg = convo[-1]["content"][:80] if convo else None
        leads.append({
            "name": row.get("name"),
            "phone": row.get("phone"),
            "category": row.get("category"),
            "status": row.get("status"),
            "replied": has_user,
            "unknown": not row.get("maps_url") and has_user,
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
