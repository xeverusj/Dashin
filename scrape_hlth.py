"""
scrape_hlth.py — HLTH Europe Speakers Scraper
==============================================
Scrapes all speakers from https://hlth.com/events/europe/speakers/

Extracts: name, title, company, profile_url
Saves to: Desktop/hlth_europe_speakers.csv

Usage:
    python scrape_hlth.py
"""

import sys
import os
import csv
import time
import random

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

START_URL = "https://hlth.com/events/europe/speakers/"

OUTPUT_CSV = os.path.join(
    os.environ.get("USERPROFILE", r"C:\Users\lenovo"), "Desktop", "hlth_europe_speakers.csv"
)


def _human_delay(min_s=0.8, max_s=2.0):
    time.sleep(random.uniform(min_s, max_s))


def _inject_start_button(page):
    try:
        page.evaluate("""
            () => {
                if (document.getElementById('hlth-scrape-btn')) return;
                var btn = document.createElement('button');
                btn.id = 'hlth-scrape-btn';
                btn.innerHTML = '&#9654; START SCRAPING';
                btn.style.cssText = [
                    'position:fixed','top:12px','right:12px','z-index:2147483647',
                    'padding:14px 28px','background:#6610f2','color:white',
                    'font-size:16px','font-weight:bold','border:none',
                    'border-radius:8px','cursor:pointer','box-shadow:0 4px 12px rgba(0,0,0,0.4)'
                ].join(';');
                btn.onclick = function() {
                    window.hlth_scrape_ready = true;
                    btn.innerHTML = '&#10003; Scraping...';
                    btn.style.background = '#198754';
                };
                document.body.appendChild(btn);
            }
        """)
    except Exception as e:
        print(f"  [btn] {e}")


def get_total_pages(page):
    try:
        btns = page.query_selector_all("a.pagination-item")
        nums = []
        for btn in btns:
            dp = btn.get_attribute("data-page") or ""
            try:
                nums.append(int(dp))
            except Exception:
                pass
        return max(nums) if nums else 1
    except Exception:
        return 1


def extract_speakers(page):
    speakers = []
    try:
        cards = page.query_selector_all("a.data-item.speaker_main")
        for card in cards:
            try:
                name_el = card.query_selector(".name")
                title_el = card.query_selector(".title")
                company_el = card.query_selector(".company")
                href = card.get_attribute("href") or ""

                name = name_el.inner_text().strip() if name_el else ""
                title = title_el.inner_text().strip() if title_el else ""
                company = company_el.inner_text().strip() if company_el else ""

                if name:
                    speakers.append({
                        "name": name,
                        "title": title,
                        "company": company,
                        "profile_url": href,
                    })
            except Exception as e:
                print(f"    [card error] {e}")
    except Exception as e:
        print(f"  [extract error] {e}")
    return speakers


def go_to_page(page, page_num):
    try:
        btn = page.query_selector(f"a.pagination-item[data-page='{page_num}']")
        if btn:
            btn.click()
            _human_delay(2.0, 3.5)
            # Wait for cards to reload
            page.wait_for_selector("a.data-item.speaker_main", timeout=10000)
            return True
    except Exception as e:
        print(f"  [pagination error] {e}")
    return False


def main():
    print("\n" + "=" * 60)
    print("  HLTH Europe Speakers Scraper")
    print("=" * 60)
    print(f"  Output: {OUTPUT_CSV}\n")

    all_speakers = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
        )
        page = context.new_page()

        if _STEALTH:
            _stealth_sync(page)
            print("  [stealth] Anti-detection active")

        print(f"  Opening: {START_URL}")
        page.goto(START_URL, wait_until="domcontentloaded", timeout=60000)
        _human_delay(2, 4)

        # Wait for speaker cards
        try:
            page.wait_for_selector("a.data-item.speaker_main", timeout=15000)
        except Exception:
            print("  (speaker cards not found yet)")

        print("  Click the purple START button when ready.\n")
        _inject_start_button(page)

        while True:
            try:
                if page.evaluate("() => window.hlth_scrape_ready === true"):
                    print("  START clicked — scraping!\n")
                    break
            except Exception:
                pass
            time.sleep(0.5)

        total_pages = get_total_pages(page)
        print(f"  Detected {total_pages} pages\n")

        # Scrape page 1
        print(f"  [Page 1/{total_pages}]", end=" ", flush=True)
        speakers = extract_speakers(page)
        all_speakers.extend(speakers)
        print(f"{len(speakers)} speakers")

        # Scrape remaining pages
        for pg in range(2, total_pages + 1):
            print(f"  [Page {pg}/{total_pages}]", end=" ", flush=True)
            success = go_to_page(page, pg)
            if not success:
                print("could not navigate — stopping")
                break
            speakers = extract_speakers(page)
            all_speakers.extend(speakers)
            print(f"{len(speakers)} speakers")
            _human_delay(1.0, 2.0)

        browser.close()

    # Deduplicate by name
    seen = set()
    unique = []
    for s in all_speakers:
        if s["name"] not in seen:
            seen.add(s["name"])
            unique.append(s)

    print(f"\n  Total: {len(unique)} unique speakers ({len(all_speakers)} raw)")

    fields = ["name", "title", "company", "profile_url"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(unique)

    print(f"\n[DONE] Saved to: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
