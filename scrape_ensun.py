"""
scrape_ensun.py — ensun.io Microbiome Company Scraper
======================================================
Scrapes microbiome companies (UK + Germany, Manufacturer + Service Provider)
from ensun.io search results.

Extracts: name, location, employees, founded, key_takeaway, core_business, url
Saves to: Desktop/ensun_microbiome.csv
Optionally pushes results to Dashin API (set DASHIN_API_URL + DASHIN_API_TOKEN in .env).

Usage:
    python scrape_ensun.py
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

START_URL = (
    "https://ensun.io/search"
    "?q=Microbiome"
    "&locations=Turkey%2Cnull%2Cnull"
    "&locations=Belgium%2Cnull%2Cnull"
    "&categories=MANUFACTURER"
    "&categories=SERVICE_PROVIDER"
)

OUTPUT_CSV = os.path.join(
    os.environ.get("USERPROFILE", r"C:\Users\lenovo"), "Desktop", "ensun_microbiome_turkey_belgium.csv"
)


def _human_delay(min_s=0.8, max_s=2.0):
    time.sleep(random.uniform(min_s, max_s))


def _inject_start_button(page):
    try:
        page.evaluate("""
            () => {
                if (document.getElementById('ensun-scrape-btn')) return;
                var btn = document.createElement('button');
                btn.id = 'ensun-scrape-btn';
                btn.innerHTML = '&#9654; START SCRAPING';
                btn.style.cssText = [
                    'position:fixed','top:12px','right:12px','z-index:2147483647',
                    'padding:14px 28px','background:#6610f2','color:white',
                    'font-size:16px','font-weight:bold','border:none',
                    'border-radius:8px','cursor:pointer','box-shadow:0 4px 12px rgba(0,0,0,0.4)'
                ].join(';');
                btn.onclick = function() {
                    window.ensun_scrape_ready = true;
                    btn.innerHTML = '&#10003; Scraping...';
                    btn.style.background = '#198754';
                };
                document.body.appendChild(btn);
            }
        """)
    except Exception as e:
        print(f"  [btn] {e}")


def extract_cards(page):
    """Extract all company cards visible on the current page."""
    # Wait for cards to render
    try:
        page.wait_for_selector("p.mui-115383y, [class*='mui-115383y']", timeout=10000)
    except Exception:
        # Try alternative: wait for any company name paragraph
        try:
            page.wait_for_selector("button[aria-label^='Go to page']", timeout=5000)
        except Exception:
            pass

    cards = []
    try:
        card_els = page.query_selector_all("div.mui-p7j1f2")
        if not card_els:
            # Fallback: find cards by structure (grid container with company name p)
            card_els = page.evaluate("""() => {
                // Find all elements that look like company cards
                const all = document.querySelectorAll('div[class*="MuiGrid-container"]');
                return Array.from(all).filter(el => {
                    const text = el.innerText || '';
                    return text.includes('Key takeaway') && text.includes('Employees');
                }).map(el => el.outerHTML);
            }""")

        for card_el in card_els:
            try:
                name = ""
                location = ""
                employees = ""
                founded = ""
                key_takeaway = ""
                core_business = ""

                # Company name — first large typography paragraph
                name_el = card_el.query_selector("p.mui-115383y")
                if not name_el:
                    # Try any p inside the title stack
                    name_el = card_el.query_selector(".mui-5ax1kt p, .mui-115383y")
                if name_el:
                    name = name_el.inner_text().strip()

                # Location, employees, founded — the three info rows
                info_ps = card_el.query_selector_all("p.mui-12sex2n")
                if len(info_ps) >= 1:
                    location = info_ps[0].inner_text().strip()
                if len(info_ps) >= 2:
                    employees = info_ps[1].inner_text().strip()
                if len(info_ps) >= 3:
                    founded = info_ps[2].inner_text().strip()

                # Key takeaway
                kt_el = card_el.query_selector("p.mui-1fqmk4b")
                if kt_el:
                    key_takeaway = kt_el.inner_text().strip()

                # Core business
                cb_el = card_el.query_selector("p.mui-hmvu0h")
                if cb_el:
                    core_business = cb_el.inner_text().strip()

                if name:
                    cards.append({
                        "name": name,
                        "location": location,
                        "employees": employees,
                        "founded": founded,
                        "key_takeaway": key_takeaway,
                        "core_business": core_business,
                    })
            except Exception as e:
                print(f"    [card error] {e}")

    except Exception as e:
        print(f"  [extract error] {e}")

    return cards


def get_total_pages(page):
    """Find the last page number from pagination."""
    try:
        page_btns = page.query_selector_all("button[aria-label^='Go to page']")
        if page_btns:
            nums = []
            for btn in page_btns:
                label = btn.get_attribute("aria-label") or ""
                try:
                    nums.append(int(label.replace("Go to page ", "")))
                except Exception:
                    pass
            return max(nums) if nums else 1
    except Exception:
        pass
    return 1


def go_to_next_page(page):
    """Click the Next page button (arrow)."""
    try:
        # MUI pagination next button has aria-label="Go to next page"
        btn = page.query_selector("button[aria-label='Go to next page']")
        if btn:
            disabled = btn.get_attribute("disabled")
            if disabled is not None:
                return False  # last page
            btn.click()
            _human_delay(2.0, 3.5)
            return True
    except Exception as e:
        print(f"  [pagination error] {e}")
    return False


def main():
    print("\n" + "=" * 60)
    print("  ensun.io Microbiome Scraper (UK + Germany)")
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

        print(f"  Opening: {START_URL}")
        page.goto(START_URL, wait_until="domcontentloaded", timeout=60000)
        _human_delay(3, 5)

        # Wait for results to render
        print("  Waiting for results to load...")
        try:
            page.wait_for_selector("button[aria-label^='Go to page']", timeout=20000)
            print("  Results loaded.")
        except Exception:
            print("  (pagination not found yet, proceeding anyway)")
        _human_delay(2, 3)
        print("  Starting scrape...\n")

        # Detect total pages
        total_pages = get_total_pages(page)
        print(f"  Detected {total_pages} pages\n")

        pg = 1
        while True:
            print(f"  [Page {pg}/{total_pages}]", end=" ", flush=True)
            cards = extract_cards(page)
            all_companies.extend(cards)
            print(f"{len(cards)} companies")

            if pg >= total_pages:
                break
            success = go_to_next_page(page)
            if not success:
                print("  (next button not found or disabled — done)")
                break
            pg += 1
            _human_delay(1.0, 2.0)

        browser.close()

    # Deduplicate by name
    seen = set()
    unique = []
    for c in all_companies:
        if c["name"] not in seen:
            seen.add(c["name"])
            unique.append(c)

    print(f"\n  Total: {len(unique)} unique companies ({len(all_companies)} raw)")

    # Save
    fields = ["name", "location", "employees", "founded", "key_takeaway", "core_business"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(unique)

    print(f"\n[DONE] Saved to: {OUTPUT_CSV}")

    # Push to Dashin API if credentials are configured
    api_url = os.environ.get("DASHIN_API_URL", "").strip()
    api_token = os.environ.get("DASHIN_API_TOKEN", "").strip()
    if api_url and api_token:
        push_to_dashin(unique, api_url, api_token)
    else:
        print("\n[API] DASHIN_API_URL / DASHIN_API_TOKEN not set — skipping API push.")
        print("      Set them in your .env file to enable automatic import.")


def push_to_dashin(companies: list, api_url: str, api_token: str) -> None:
    """POST scraped companies to the Dashin /api/leads/import endpoint."""
    rows = [
        {
            "company_name": c.get("name", ""),
            "description": c.get("key_takeaway", ""),
            "country": c.get("location", ""),
            "business_areas": "Microbiome",
            "source": "ensun.io",
        }
        for c in companies
        if c.get("name", "").strip()
    ]

    if not rows:
        print("[API] No rows to push.")
        return

    endpoint = api_url.rstrip("/") + "/api/leads/import"
    payload = {"rows": rows, "source": "ensun.io"}

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
