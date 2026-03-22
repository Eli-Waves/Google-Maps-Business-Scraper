"""
Scheduler helpers
"""
import time
from database import get_new_leads, update_lead_status, append_message
from whatsapp import send_message, first_outreach_message

GHANA_CITIES = [
    "Accra", "Kumasi", "Tamale", "Takoradi", "Cape Coast",
    "Sunyani", "Koforidua", "Ho", "Bolgatanga", "Wa",
    "Tema", "Ashaiman", "Kasoa", "Madina", "Spintex",
    "Osu", "Adenta", "Dome", "Lapaz", "Abeka"
]

CATEGORIES = [
    "restaurants", "hotels", "salons", "pharmacies", "supermarkets",
    "schools", "clinics", "car rentals", "event centers", "guest houses",
    "boutiques", "hardware stores", "printing shops", "logistics companies",
    "barbershops", "gyms", "churches", "bakeries", "laundry services",
    "auto repair shops", "electronics shops", "furniture stores", "travel agencies",
    "photography studios", "catering services", "daycare centers", "real estate"
]

city_index = [0]
category_index = [0]

ADMIN_PHONES = ["233530123985", "233557808489"]


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
        return

    success = 0
    for lead in leads:
        phone = lead["phone"]
        name = lead["name"] or "there"
        try:
            # Double-check status to prevent duplicate sends
            from database import get_lead_by_phone
            current = get_lead_by_phone(phone)
            if current and current["status"] != "new":
                continue
            message = first_outreach_message(name)
            send_message(phone, message, typing_delay=False)
            append_message(phone, "assistant", message)
            update_lead_status(phone, "contacted")
            print(f"  [✓] Messaged {name} ({phone})")
            success += 1
        except Exception as e:
            print(f"  [!] Failed for {name} ({phone}): {e}")
        time.sleep(12)

    for admin in ADMIN_PHONES:
        try:
            send_message(admin, f"Outreach done. Messaged {success}/{len(leads)} businesses.", typing_delay=False)
        except:
            pass
    print(f"[✓] Outreach done: {success}/{len(leads)}")
