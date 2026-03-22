"""
Google Maps Scraper using Apify
Returns full business details including phone numbers
"""
import os
import time
import requests
from database import init_db, upsert_lead

APIFY_TOKEN = os.getenv("APIFY_API_KEY")
ACTOR_ID = "compass~crawler-google-places"

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


def scrape_businesses(query: str, limit: int = 20) -> list[dict]:
    print(f"[+] Apify scraping: {query}")

    # Start the actor run
    run_url = f"https://api.apify.com/v2/acts/{ACTOR_ID}/runs"
    payload = {
        "searchStringsArray": [query],
        "maxCrawledPlacesPerSearch": limit,
        "language": "en",
        "countryCode": "gh",
        "includeContacts": True,
    }

    headers = {"Authorization": f"Bearer {APIFY_TOKEN}"}
    response = requests.post(run_url, json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    run_data = response.json()
    run_id = run_data["data"]["id"]
    dataset_id = run_data["data"]["defaultDatasetId"]

    print(f"[+] Run started: {run_id}. Waiting for results...")

    # Wait for run to complete
    status_url = f"https://api.apify.com/v2/actor-runs/{run_id}"
    for _ in range(60):  # Wait up to 5 minutes
        time.sleep(5)
        status_res = requests.get(status_url, headers=headers).json()
        status = status_res["data"]["status"]
        print(f"  Status: {status}")
        if status in ("SUCCEEDED", "FAILED", "ABORTED"):
            break

    if status != "SUCCEEDED":
        print(f"[!] Apify run failed with status: {status}")
        return []

    # Fetch results
    dataset_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items"
    items_res = requests.get(dataset_url, headers=headers, params={"limit": limit}).json()

    leads = []
    for item in items_res:
        name = item.get("title") or item.get("name")
        phone = item.get("phone") or item.get("phoneUnformatted")
        address = item.get("address") or item.get("street")
        website = item.get("website") or item.get("url")
        category = item.get("categoryName") or item.get("category")

        lead = {
            "name": name,
            "phone": phone,
            "website": website,
            "category": category,
            "address": address,
            "maps_url": item.get("url", ""),
        }

        leads.append(lead)
        print(f"  • {name} | {phone} | {category}")

    print(f"[+] Found {len(leads)} businesses")
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
