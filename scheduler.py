"""
Scheduler helpers
"""
import time
from database import get_new_leads, update_lead_status, append_message
from whatsapp import send_message, first_outreach_message
from scraper import scrape_businesses

GHANA_CITIES = [
    "Accra", "Kumasi", "Tamale", "Takoradi", "Cape Coast",
    "Sunyani", "Koforidua", "Ho", "Bolgatanga", "Wa",
    "Tema", "Ashaiman", "Kasoa", "Madina", "Spintex",
    "Osu", "Adenta", "Dome", "Lapaz", "Abeka"
]

# Prioritize categories less likely to have websites
CATEGORIES = [
    "chop bars", "drinking spots", "barbershops", "hair salons",
    "tailors", "spare parts shops", "mechanics", "provisions stores",
    "cold stores", "phone repair shops", "furniture makers",
    "welding shops", "building materials", "paint shops",
    "electrical shops", "event decorators", "bakeries", "pastry shops",
    "daycare centers", "kindergartens", "private schools",
    "tutoring centers", "laundry services", "printing shops",
    "photo studios", "restaurants", "hotels", "salons", "pharmacies",
    "supermarkets", "clinics", "guest houses", "boutiques",
    "hardware stores", "logistics companies", "gyms"
]

city_index = [0]
category_index = [0]

ADMIN_PHONES = ["233530123985", "233557808489"]
TARGET_LEADS = 40


def get_next_query():
    city = GHANA_CITIES[city_index[0] % len(GHANA_CITIES)]
    category = CATEGORIES[category_index[0] % len(CATEGORIES)]
    city_index[0] += 1
    category_index[0] += 1
    return f"{category} in {city} Ghana"


def scrape_until_target(target: int = TARGET_LEADS) -> int:
    """Keep scraping different queries until we find `target` businesses without websites."""
    from database import upsert_lead
    saved = 0
    attempts = 0
    max_attempts = 20  # prevent infinite loop

    while saved < target and attempts < max_attempts:
        query = get_next_query()
        print(f"[+] Scraping: {query} (have {saved}/{target} so far)")
        try:
            leads = scrape_businesses(query, limit=20)
            for lead in leads:
                if lead.get("phone"):
                    upsert_lead(lead)
                    saved += 1
            if saved >= target:
                break
        except Exception as e:
            print(f"[!] Scrape error: {e}")
            if "402" in str(e) or "Payment" in str(e):
                print("[!] Apify credits exhausted — stopping scrape")
                for admin in ADMIN_PHONES:
                    try:
                        send_message(admin, "Apify credits ran out. Top up at console.apify.com to continue scraping.", typing_delay=False)
                    except: pass
                break
        attempts += 1
        time.sleep(3)  # small delay between queries

    print(f"[✓] Scrape complete: {saved} businesses saved after {attempts} queries")
    return saved


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
