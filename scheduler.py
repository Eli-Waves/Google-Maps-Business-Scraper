"""
Scheduler helpers
"""
import time
from database import get_new_leads, update_lead_status, append_message
from whatsapp import send_message, first_outreach_message

GHANA_CITIES = [
    "Accra", "Kumasi", "Tamale", "Takoradi", "Cape Coast",
    "Sunyani", "Koforidua", "Ho", "Bolgatanga", "Wa"
]

CATEGORIES = [
    "restaurants", "hotels", "salons", "pharmacies", "supermarkets",
    "schools", "clinics", "car rentals", "event centers", "guest houses",
    "boutiques", "hardware stores", "printing shops", "logistics companies"
]

city_index = [0]
category_index = [0]

OWNER_PHONE = "233530123985"


def get_next_query():
    city = GHANA_CITIES[city_index[0] % len(GHANA_CITIES)]
    category = CATEGORIES[category_index[0] % len(CATEGORIES)]
    city_index[0] += 1
    category_index[0] += 1
    return f"{category} in {city} Ghana"


def run_outreach():
    print("[⏰] Scheduler: Starting outreach...")
    leads = get_new_leads()

    if not leads:
        print("[!] No new leads to contact")
        send_message(OWNER_PHONE, "Outreach triggered but no new leads found.")
        return

    # Notify owner first
    send_message(OWNER_PHONE, f"Outreach starting now. Messaging {len(leads)} businesses. You will get Telegram alerts for hot leads.")

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
        time.sleep(12)

    send_message(OWNER_PHONE, f"Outreach done. Messaged {success}/{len(leads)} businesses.")
    print(f"[✓] Outreach done: {success}/{len(leads)}")
