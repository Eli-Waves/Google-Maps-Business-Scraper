"""
Telegram notifications — alerts you when a lead is hot
"""
import os
import httpx

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send_telegram(message: str):
    if not BOT_TOKEN or not CHAT_ID:
        print("[!] Telegram not configured, skipping notification")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    httpx.post(url, json={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"})


def notify_hot_lead(lead: dict):
    phone = lead.get('phone') or 'N/A'
    name = lead.get('name') or 'Unknown'
    category = lead.get('category') or 'N/A'
    address = lead.get('address') or 'N/A'
    website = lead.get('website') or 'No website'
    convo = lead.get('conversation_summary') or 'No conversation recorded'

    msg = (
        f"🔥 <b>HOT LEAD!</b>\n\n"
        f"📞 <b>Phone:</b> {phone}\n"
        f"👤 <b>Name:</b> {name}\n"
        f"🏷 <b>Category:</b> {category}\n"
        f"📍 <b>Address:</b> {address}\n"
        f"🌐 <b>Website:</b> {website}\n\n"
        f"💬 <b>Conversation:</b>\n<pre>{convo}</pre>\n\n"
        f"💰 Close at GHS 500 setup + GHS 700/month"
    )
    send_telegram(msg)


def notify_scrape_done(count: int, query: str):
    send_telegram(f"✅ Scrape complete!\n\nQuery: <b>{query}</b>\nNew leads saved: <b>{count}</b>")
