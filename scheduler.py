"""
Auto scheduler — runs scraper at 12pm daily then outreach 30 mins later
"""
import time
import threading
from datetime import datetime
import asyncio
from scraper import scrape_google_maps
from database import init_db, upsert_lead, get_new_leads, update_lead_status, append_message
from whatsapp import send_message, first_outreach_message
from telegram_notify import notify_scrape_done, send_telegram

# Ghana cities to rotate through
GHANA_CITIES = [
    "Accra", "Kumasi", "Tamale", "Takoradi", "Cape Coast",
    "Sunyani", "Koforidua", "Ho", "Bolgatanga", "Wa"
]

# Mix of business categories
CATEGORIES = [
    "restaurants", "hotels", "salons", "pharmacies", "supermarkets",
    "schools", "clinics", "car rentals", "event centers", "guest houses",
    "boutiques", "hardware stores", "printing shops", "logistics companies"
]

city_index = [0]
category_index = [0]


def get_next_query():
    city = GHANA_CITIES[city_index[0] % len(GHANA_CITIES)]
    category = CATEGORIES[category_index[0] % len(CATEGORIES)]
    city_index[0] += 1
    category_index[0] += 1
    return f"{category} in {city} Ghana"


def run_scrape():
    print("[⏰] Scheduler: Starting daily scrape...")
    init_db()
    query = get_next_query()
    print(f"[+] Query: {query}")

    try:
        leads = asyncio.run(scrape_google_maps(query, limit=100))
        saved = 0
        details = []

        for lead in leads:
            # Accept leads with phone number even if other details are missing
            if lead.get("phone"):
                upsert_lead(lead)
                saved += 1
                details.append(
                    f"• {lead.get('name', 'Unknown')} | {lead.get('phone')} | {lead.get('category', 'N/A')} | {lead.get('address', 'N/A')}"
                )

        # Send full details to Telegram
        if details:
            chunk_size = 30
            for i in range(0, len(details), chunk_size):
                chunk = details[i:i+chunk_size]
                msg = f"✅ <b>Scrape Complete!</b>\n<b>Query:</b> {query}\n<b>Saved:</b> {saved} leads\n\n" + "\n".join(chunk)
                send_telegram(msg)

        notify_scrape_done(saved, query)
        print(f"[✓] Scrape done: {saved} leads saved")

    except Exception as e:
        print(f"[!] Scrape error: {e}")
        send_telegram(f"❌ Scrape failed: {e}")


def run_outreach():
    print("[⏰] Scheduler: Starting outreach...")
    leads = get_new_leads()

    if not leads:
        print("[!] No new leads to contact")
        return

    success = 0
    for lead in leads:
        phone = lead["phone"]
        name = lead["name"] or "there"
        try:
            message = first_outreach_message(name)
            send_message(phone, message)
            append_message(phone, "assistant", message)
            update_lead_status(phone, "contacted")
            print(f"  [✓] Messaged {name} ({phone})")
            success += 1
        except Exception as e:
            print(f"  [!] Failed for {name} ({phone}): {e}")
        time.sleep(12)  # 12 second delay between messages

    send_telegram(f"📤 <b>Outreach done!</b>\nMessaged <b>{success}/{len(leads)}</b> leads")
    print(f"[✓] Outreach done: {success}/{len(leads)}")


def scheduler_loop():
    print("[⏰] Scheduler running — will scrape daily at 12:00pm")
    while True:
        now = datetime.now()
        if now.hour == 12 and now.minute == 0:
            run_scrape()
            time.sleep(1800)  # Wait 30 mins
            run_outreach()
            time.sleep(60)  # Avoid double trigger
        time.sleep(30)  # Check every 30 seconds


def start_scheduler():
    thread = threading.Thread(target=scheduler_loop, daemon=True)
    thread.start()
    print("[✓] Scheduler started")
