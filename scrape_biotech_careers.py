"""
scrape_biotech_careers.py — biotech-careers.org Microbiome Directory Scraper
=============================================================================
Scrapes all 221 microbiome companies from:
  https://biotech-careers.org/business-area/microbiome

Extracts: name, businessAreas, website, description, country, companyPage
Saves to: Desktop/biotech_careers_microbiome_full.csv
Optionally pushes results to Dashin API (set DASHIN_API_URL + DASHIN_API_TOKEN in .env).

Usage:
    python scrape_biotech_careers.py
"""

import sys
import os
import csv
import time
import random
import json
import requests as _requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed — env vars must be set in the shell

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

if sys.platform.startswith("win"):
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from playwright.sync_api import sync_playwright

try:
    from playwright_stealth import stealth_sync as _stealth_sync
    _STEALTH = True
except ImportError:
    _STEALTH = False
    print("  [stealth] playwright-stealth not installed — run: pip install playwright-stealth")

LISTING_BASE = "https://biotech-careers.org/business-area/microbiome?name_selective=All&country=All&career_page=All&internship_page=All"
TOTAL_PAGES = 4

OUTPUT_CSV = os.path.join(os.environ.get("USERPROFILE", r"C:\Users\lenovo"), "Desktop", "biotech_careers_microbiome_full.csv")


def _human_delay(min_s=0.8, max_s=2.0):
    time.sleep(random.uniform(min_s, max_s))


def _inject_start_button(page):
    try:
        if page.evaluate("() => !!document.getElementById('btc-scrape-btn')"):
            return
        page.evaluate("""
            () => {
                if (document.getElementById('btc-scrape-btn')) return;
                var btn = document.createElement('button');
                btn.id = 'btc-scrape-btn';
                btn.innerHTML = '&#9654; START SCRAPING';
                btn.style.cssText = [
                    'position:fixed','top:12px','right:12px','z-index:2147483647',
                    'padding:14px 28px','background:#6610f2','color:white',
                    'font-size:16px','font-weight:bold','border:none',
                    'border-radius:8px','cursor:pointer','box-shadow:0 4px 12px rgba(0,0,0,0.4)'
                ].join(';');
                btn.onclick = function() {
                    window.btc_scrape_ready = true;
                    btn.innerHTML = '✓ Scraping...';
                    btn.style.background = '#198754';
                };
                document.body.appendChild(btn);
            }
        """)
    except Exception as e:
        print(f"  [btn] {e}")


def scrape_listing_page(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    _human_delay(1.5, 3.0)

    cards = page.query_selector_all(".views-row")
    companies = []
    for card in cards:
        name_el = card.query_selector("h2 a, h3 a")
        if not name_el:
            continue
        name = name_el.inner_text().strip()
        href = name_el.get_attribute("href") or ""
        company_page = ("https://biotech-careers.org" + href) if href.startswith("/") else href

        ba_links = card.query_selector_all(".comma-list a")
        business_areas = " | ".join(a.inner_text().strip() for a in ba_links)

        desc_el = card.query_selector(".field--name-body .field__item")
        description = desc_el.inner_text().strip() if desc_el else ""

        companies.append({
            "name": name,
            "businessAreas": business_areas,
            "description": description,
            "companyPage": company_page,
            "website": "",
            "country": "",
        })
    return companies


def enrich_profile(page, company):
    try:
        page.goto(company["companyPage"], wait_until="domcontentloaded", timeout=20000)
        _human_delay(0.5, 1.2)

        # Website
        website = ""
        labels = page.query_selector_all(".field__label")
        for label in labels:
            if label.inner_text().strip() == "Website:":
                website = page.evaluate("""(label) => {
                    const parent = label.parentElement;
                    const a = parent ? parent.querySelector('a[href]') : null;
                    return a ? a.href : '';
                }""", label)
                if website and "?" in website:
                    website = website.split("?")[0]
                break

        # Full description from profile page (not truncated)
        desc_el = page.query_selector(".field--name-body .field__item")
        if desc_el:
            full_desc = desc_el.inner_text().strip()
            if len(full_desc) > len(company["description"]):
                company["description"] = full_desc

        # Country — last line of location block
        country = ""
        loc_el = page.query_selector(".mt-4")
        if loc_el:
            text = loc_el.inner_text().strip()
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            lines = [l for l in lines if "location" not in l.lower() and l]
            if lines:
                country = lines[-1]

        company["website"] = website
        company["country"] = country

    except Exception as e:
        print(f"  [warn] {company['name']}: {e}")


def main():
    print("\n" + "=" * 60)
    print("  biotech-careers.org Microbiome Directory Scraper")
    print("=" * 60)
    print(f"  Output: {OUTPUT_CSV}\n")

    all_companies = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ]
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="en-GB",
            timezone_id="Europe/London",
            extra_http_headers={
                "Accept-Language": "en-GB,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
        )
        page = context.new_page()

        if _STEALTH:
            _stealth_sync(page)
            print("  [stealth] Anti-detection active")

        print(f"  Opening: {LISTING_BASE}")
        page.goto(LISTING_BASE, wait_until="domcontentloaded", timeout=60000)
        _human_delay(2, 4)

        print("  Click the purple START button when ready.\n")
        _inject_start_button(page)

        while True:
            try:
                if page.evaluate("() => window.btc_scrape_ready === true"):
                    print("  START clicked — scraping!\n")
                    break
            except Exception:
                pass
            time.sleep(0.5)

        # Step 1: Collect all listing pages
        print("=== Step 1: Listing pages ===")
        for pg in range(TOTAL_PAGES):
            url = LISTING_BASE if pg == 0 else LISTING_BASE + f"&page={pg}"
            print(f"  Page {pg + 1}/{TOTAL_PAGES}...", end=" ", flush=True)
            companies = scrape_listing_page(page, url)
            all_companies.extend(companies)
            print(f"{len(companies)} companies")

        print(f"\n  Total: {len(all_companies)} companies found")

        # Step 2: Enrich each company profile
        print("\n=== Step 2: Company profiles ===")
        for i, company in enumerate(all_companies):
            print(f"  [{i + 1}/{len(all_companies)}] {company['name']}", end=" ... ", flush=True)
            enrich_profile(page, company)
            print(f"{company['website'] or '(no website)'} | {company['country'] or '?'}")

        browser.close()

    # Save
    fields = ["name", "businessAreas", "website", "description", "country", "companyPage"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_companies)

    with_website = sum(1 for c in all_companies if c["website"])
    print(f"\n[DONE] Saved {len(all_companies)} companies ({with_website} with website) to:")
    print(f"       {OUTPUT_CSV}")

    # Push to Dashin API if credentials are configured
    api_url = os.environ.get("DASHIN_API_URL", "").strip()
    api_token = os.environ.get("DASHIN_API_TOKEN", "").strip()
    if api_url and api_token:
        push_to_dashin(all_companies, api_url, api_token)
    else:
        print("\n[API] DASHIN_API_URL / DASHIN_API_TOKEN not set — skipping API push.")
        print("      Set them in your .env file to enable automatic import.")


def push_to_dashin(companies: list, api_url: str, api_token: str) -> None:
    """POST scraped companies to the Dashin /api/leads/import endpoint."""
    rows = [
        {
            "company_name": c.get("name", ""),
            "website": c.get("website", ""),
            "description": c.get("description", ""),
            "country": c.get("country", ""),
            "business_areas": c.get("businessAreas", ""),
            "source": "biotech-careers.org",
        }
        for c in companies
        if c.get("name", "").strip()
    ]

    if not rows:
        print("[API] No rows to push.")
        return

    endpoint = api_url.rstrip("/") + "/api/leads/import"
    payload = {"rows": rows, "source": "biotech-careers.org"}

    print(f"\n[API] Pushing {len(rows)} leads to {endpoint} ...")
    try:
        resp = _requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload),
            timeout=30,
        )
        if resp.ok:
            print(f"[API] Success — {resp.status_code}: {resp.text[:200]}")
        else:
            print(f"[API] Failed — HTTP {resp.status_code}: {resp.text[:300]}")
    except Exception as e:
        print(f"[API] Error pushing to Dashin: {e}")


if __name__ == "__main__":
    main()
