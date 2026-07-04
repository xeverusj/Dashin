"""
scrape_healthtech.py — health.tech Speakers 2026 Scraper
=========================================================
Scrapes all speakers from https://www.health.tech/speakers-2026

Extracts: name, title, company, talk_title, bio
Saves to: Desktop/healthtech_speakers_2026.csv

Usage:
    python scrape_healthtech.py
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

START_URL = "https://www.health.tech/speakers-2026"

OUTPUT_CSV = os.path.join(
    os.environ.get("USERPROFILE", r"C:\Users\lenovo"), "Desktop", "healthtech_speakers_2026.csv"
)


def _human_delay(min_s=0.8, max_s=2.0):
    time.sleep(random.uniform(min_s, max_s))


def _inject_start_button(page):
    try:
        page.evaluate("""
            () => {
                if (document.getElementById('ht-scrape-btn')) return;
                var btn = document.createElement('button');
                btn.id = 'ht-scrape-btn';
                btn.innerHTML = '&#9654; START SCRAPING';
                btn.style.cssText = [
                    'position:fixed','top:12px','right:12px','z-index:2147483647',
                    'padding:14px 28px','background:#6610f2','color:white',
                    'font-size:16px','font-weight:bold','border:none',
                    'border-radius:8px','cursor:pointer','box-shadow:0 4px 12px rgba(0,0,0,0.4)'
                ].join(';');
                btn.onclick = function() {
                    window.ht_scrape_ready = true;
                    btn.innerHTML = '&#10003; Scraping...';
                    btn.style.background = '#198754';
                };
                document.body.appendChild(btn);
            }
        """)
    except Exception as e:
        print(f"  [btn] {e}")


def scroll_to_load_all(page):
    """Scroll down incrementally to trigger lazy-loaded CMS items."""
    print("  Scrolling to load all speakers...", flush=True)
    prev_count = 0
    for _ in range(30):
        page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
        time.sleep(0.8)
        count = len(page.query_selector_all(".speaker-text_container"))
        if count == prev_count and count > 0:
            # Scroll back to top and check one more time
            page.evaluate("window.scrollTo(0, 0)")
            time.sleep(0.5)
            break
        prev_count = count
        print(f"    {count} speakers loaded so far...", flush=True)


def extract_speakers(page):
    speakers = []
    cards = page.query_selector_all(".speaker-text_container")
    print(f"  Found {len(cards)} speaker cards")

    for card in cards:
        try:
            # Name
            name_el = card.query_selector(".board-label_name")
            name = name_el.inner_text().strip() if name_el else ""

            # Title — first .job-title
            title_els = card.query_selector_all(".job-title")
            title = title_els[0].inner_text().strip() if len(title_els) >= 1 else ""

            # Company — second .job-title (has border-left class)
            company_el = card.query_selector(".border-left-solid-white")
            company = company_el.inner_text().strip() if company_el else (
                title_els[1].inner_text().strip() if len(title_els) >= 2 else ""
            )

            # Talk title
            talk_el = card.query_selector(".show-hover-bio .font-weight-700")
            talk_title = talk_el.inner_text().strip() if talk_el else ""

            # Bio
            bio_el = card.query_selector(".show-hover-bio .w-richtext")
            bio = bio_el.inner_text().strip() if bio_el else ""

            if name:
                speakers.append({
                    "name": name,
                    "title": title,
                    "company": company,
                    "talk_title": talk_title,
                    "bio": bio,
                })
        except Exception as e:
            print(f"    [card error] {e}")

    return speakers


def main():
    print("\n" + "=" * 60)
    print("  health.tech Speakers 2026 Scraper")
    print("=" * 60)
    print(f"  Output: {OUTPUT_CSV}\n")

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
        _human_delay(3, 5)

        # Wait for at least one speaker card
        try:
            page.wait_for_selector(".speaker-text_container", timeout=15000)
            print("  Speakers loaded.")
        except Exception:
            print("  (speaker cards not found yet — page may still be loading)")

        print("  Click the purple START button when ready.\n")
        _inject_start_button(page)

        while True:
            try:
                if page.evaluate("() => window.ht_scrape_ready === true"):
                    print("  START clicked — scraping!\n")
                    break
            except Exception:
                pass
            time.sleep(0.5)

        # Scroll to load all lazy-loaded content
        scroll_to_load_all(page)
        _human_delay(1, 2)

        speakers = extract_speakers(page)
        browser.close()

    # Deduplicate by name
    seen = set()
    unique = []
    for s in speakers:
        if s["name"] not in seen:
            seen.add(s["name"])
            unique.append(s)

    print(f"\n  Total: {len(unique)} unique speakers ({len(speakers)} raw)")

    fields = ["name", "title", "company", "talk_title", "bio"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(unique)

    print(f"\n[DONE] Saved to: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
