"""
Google Maps Business Scraper
Scrapes: business name, phone number, website, category
Usage: python scraper.py --query "restaurants in Accra" --limit 20
"""

import asyncio
import json
import re
import argparse
from playwright.async_api import async_playwright


async def scrape_google_maps(query: str, limit: int = 20) -> list[dict]:
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}"
        print(f"[+] Searching: {url}")
        await page.goto(url, timeout=60000)
        await page.wait_for_timeout(3000)

        # Scroll the results panel to load more listings
        panel = page.locator('div[role="feed"]')
        prev_count = 0
        for _ in range(15):
            await panel.evaluate("el => el.scrollBy(0, 2000)")
            await page.wait_for_timeout(1500)
            links = await page.locator('a[href*="/maps/place/"]').all()
            if len(links) >= limit:
                break
            if len(links) == prev_count:
                break
            prev_count = len(links)

        # Collect listing links
        links = await page.locator('a[href*="/maps/place/"]').all()
        hrefs = []
        seen = set()
        for link in links:
            href = await link.get_attribute("href")
            if href and href not in seen:
                seen.add(href)
                hrefs.append(href)
            if len(hrefs) >= limit:
                break

        print(f"[+] Found {len(hrefs)} listings. Extracting details...")

        for i, href in enumerate(hrefs):
            try:
                await page.goto(href, timeout=30000)
                await page.wait_for_timeout(2000)

                name = await page.title()
                name = name.replace(" - Google Maps", "").strip()

                # Phone
                phone = None
                phone_el = page.locator('button[data-item-id*="phone"] div.fontBodyMedium')
                if await phone_el.count() > 0:
                    phone = await phone_el.first.text_content()

                # Website
                website = None
                web_el = page.locator('a[data-item-id="authority"]')
                if await web_el.count() > 0:
                    website = await web_el.first.get_attribute("href")

                # Category
                category = None
                cat_el = page.locator('button[jsaction*="category"]')
                if await cat_el.count() > 0:
                    category = await cat_el.first.text_content()

                # Address
                address = None
                addr_el = page.locator('button[data-item-id="address"] div.fontBodyMedium')
                if await addr_el.count() > 0:
                    address = await addr_el.first.text_content()

                business = {
                    "name": name,
                    "phone": phone.strip() if phone else None,
                    "website": website,
                    "category": category.strip() if category else None,
                    "address": address.strip() if address else None,
                    "maps_url": href,
                    "messaged": False,
                    "status": "new"   # new | contacted | interested | converted
                }

                results.append(business)
                print(f"  [{i+1}] {name} | {phone} | {category}")

            except Exception as e:
                print(f"  [!] Error on listing {i+1}: {e}")
                continue

        await browser.close()

    return results


def save_results(results: list[dict], filename: str = "leads.json"):
    # Load existing leads to avoid duplicates
    try:
        with open(filename, "r") as f:
            existing = json.load(f)
    except FileNotFoundError:
        existing = []

    existing_phones = {b["phone"] for b in existing if b.get("phone")}
    new_leads = [b for b in results if b.get("phone") not in existing_phones]

    all_leads = existing + new_leads
    with open(filename, "w") as f:
        json.dump(all_leads, f, indent=2, ensure_ascii=False)

    print(f"\n[✓] Saved {len(new_leads)} new leads ({len(all_leads)} total) → {filename}")
    return new_leads


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default="restaurants in Accra Ghana", help="Search query")
    parser.add_argument("--limit", type=int, default=20, help="Max businesses to scrape")
    parser.add_argument("--output", default="leads.json", help="Output file")
    args = parser.parse_args()

    results = asyncio.run(scrape_google_maps(args.query, args.limit))
    save_results(results, args.output)
