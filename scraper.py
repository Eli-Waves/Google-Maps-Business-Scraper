"""
Google Maps Business Scraper
"""
import asyncio
import argparse
import subprocess
import sys
from playwright.async_api import async_playwright
from database import init_db, upsert_lead


def ensure_chromium():
    """Install Chromium if not present."""
    import os
    browser_path = os.path.expanduser("~/.cache/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-linux64/chrome-headless-shell")
    if not os.path.exists(browser_path):
        print("[+] Installing Chromium...")
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        print("[✓] Chromium installed")


async def scrape_google_maps(query: str, limit: int = 100) -> list[dict]:
    ensure_chromium()
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}"
        print(f"[+] Searching: {url}")
        await page.goto(url, timeout=60000)
        await page.wait_for_timeout(3000)

        panel = page.locator('div[role="feed"]')
        prev_count = 0
        for _ in range(20):
            await panel.evaluate("el => el.scrollBy(0, 2000)")
            await page.wait_for_timeout(1500)
            links = await page.locator('a[href*="/maps/place/"]').all()
            if len(links) >= limit or len(links) == prev_count:
                break
            prev_count = len(links)

        links = await page.locator('a[href*="/maps/place/"]').all()
        hrefs = list({await l.get_attribute("href") for l in links if await l.get_attribute("href")})[:limit]

        print(f"[+] Found {len(hrefs)} listings. Extracting details...")

        for i, href in enumerate(hrefs):
            try:
                await page.goto(href, timeout=30000)
                await page.wait_for_timeout(2000)

                name = (await page.title()).replace(" - Google Maps", "").strip()

                phone = None
                phone_el = page.locator('button[data-item-id*="phone"] div.fontBodyMedium')
                if await phone_el.count() > 0:
                    phone = (await phone_el.first.text_content()).strip()

                website = None
                web_el = page.locator('a[data-item-id="authority"]')
                if await web_el.count() > 0:
                    website = await web_el.first.get_attribute("href")

                category = None
                cat_el = page.locator('button[jsaction*="category"]')
                if await cat_el.count() > 0:
                    category = (await cat_el.first.text_content()).strip()

                address = None
                addr_el = page.locator('button[data-item-id="address"] div.fontBodyMedium')
                if await addr_el.count() > 0:
                    address = (await addr_el.first.text_content()).strip()

                lead = {
                    "name": name,
                    "phone": phone,
                    "website": website,
                    "category": category,
                    "address": address,
                    "maps_url": href,
                }

                results.append(lead)
                print(f"  [{i+1}] {name} | {phone} | {category}")

            except Exception as e:
                print(f"  [!] Error on listing {i+1}: {e}")

        await browser.close()

    return results


def run(query: str, limit: int = 100):
    init_db()
    leads = asyncio.run(scrape_google_maps(query, limit))
    saved = 0
    for lead in leads:
        if lead.get("phone"):
            upsert_lead(lead)
            saved += 1
    print(f"\n[✓] Saved {saved} new leads to database")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default="restaurants in Accra Ghana")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    run(args.query, args.limit)
