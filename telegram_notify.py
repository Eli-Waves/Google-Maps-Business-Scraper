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
    msg = (
        f"🔥 <b>HOT LEAD!</b>\n\n"
        f"👤 <b>Name:</b> {lead.get('name', 'Unknown')}\n"
        f"📞 <b>Phone:</b> {lead.get('phone')}\n"
        f"🏷 <b>Category:</b> {lead.get('category', 'N/A')}\n"
        f"📍 <b>Address:</b> {lead.get('address', 'N/A')}\n"
        f"🌐 <b>Website:</b> {lead.get('website') or 'None (opportunity!)'}\n\n"
        f"💬 They're interested! Follow up now and close the deal.\n"
        f"💰 Pitch: GHS 500 setup + GHS 700/month"
    )
    send_telegram(msg)


def notify_scrape_done(count: int, query: str):
    send_telegram(f"✅ Scrape complete!\n\nQuery: <b>{query}</b>\nNew leads saved: <b>{count}</b>")
