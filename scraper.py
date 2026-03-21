"""
Google Business Scraper using Serper API
Fast, reliable, no browser needed
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


def scrape_businesses(query: str, limit: int = 50) -> list[dict]:
    print(f"[+] Searching: {query}")
    leads = []

    headers = {
        "X-API-KEY": SERPER_KEY,
        "Content-Type": "application/json",
    }

    # Search local businesses via Serper places
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
    print(f"[+] Found {len(places)} businesses")

    for place in places:
        name = place.get("title") or place.get("name")
        phone = place.get("phoneNumber")
        address = place.get("address")
        website = place.get("website")
        category = place.get("type") or place.get("category")
        rating = place.get("rating")

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


def run(query: str = None, limit: int = 50):
    init_db()
    if not query:
        query = get_next_query()
    leads = scrape_businesses(query, limit)
    saved = 0
    for lead in leads:
        if lead.get("phone"):
            upsert_lead(lead)
            saved += 1
    print(f"\n[✓] Saved {saved} leads to database")
    return leads


if __name__ == "__main__":
    run()
