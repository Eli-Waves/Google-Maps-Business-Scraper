"""
Google Business Scraper using Serper API
"""
import os
import requests
from database import init_db, upsert_lead

SERPER_KEY = os.getenv("SERPER_API_KEY")

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


def get_next_query():
    city = GHANA_CITIES[city_index[0] % len(GHANA_CITIES)]
    category = CATEGORIES[category_index[0] % len(CATEGORIES)]
    city_index[0] += 1
    category_index[0] += 1
    return f"{category} in {city} Ghana"


def get_phone_for_business(name: str, address: str = "") -> str | None:
    """Search specifically for a business's phone number."""
    headers = {
        "X-API-KEY": SERPER_KEY,
        "Content-Type": "application/json",
    }
    query = f"{name} {address} phone number Ghana"
    payload = {"q": query, "num": 3, "gl": "gh"}

    try:
        response = requests.post(
            "https://google.serper.dev/search",
            headers=headers,
            json=payload,
            timeout=10
        )
        data = response.json()

        # Check knowledge graph first
        kg = data.get("knowledgeGraph", {})
        if kg.get("phoneNumber"):
            return kg["phoneNumber"]

        # Check answer box
        answer = data.get("answerBox", {})
        if answer.get("phoneNumber"):
            return answer["phoneNumber"]

        return None
    except:
        return None


def scrape_businesses(query: str, limit: int = 20) -> list[dict]:
    print(f"[+] Searching: {query}")
    leads = []

    headers = {
        "X-API-KEY": SERPER_KEY,
        "Content-Type": "application/json",
    }

    payload = {"q": query, "num": limit, "gl": "gh", "hl": "en"}

    response = requests.post(
        "https://google.serper.dev/places",
        headers=headers,
        json=payload,
        timeout=15
    )
    response.raise_for_status()
    data = response.json()

    places = data.get("places", [])
    print(f"[+] Found {len(places)} businesses, fetching phone numbers...")

    for place in places:
        name = place.get("title") or place.get("name")
        phone = place.get("phoneNumber")
        address = place.get("address", "")
        website = place.get("website")
        category = place.get("type") or place.get("category")

        # If no phone, try to find it
        if not phone and name:
            phone = get_phone_for_business(name, address)

        lead = {
            "name": name,
            "phone": phone,
            "website": website,
            "category": category,
            "address": address,
            "maps_url": place.get("cid", ""),
        }

        leads.append(lead)
        print(f"  • {name} | {phone} | {category}")

    return leads


def run(query: str = None, limit: int = 20):
    init_db()
    if not query:
        query = get_next_query()
    leads = scrape_businesses(query, limit)
    saved = 0
    for lead in leads:
        if lead.get("phone"):
            upsert_lead(lead)
            saved += 1
    print(f"\n[✓] Saved {saved} leads with phone numbers")
    return leads


if __name__ == "__main__":
    run()
