"""
scrape_b2match_gitex.py — Gitex 2026 Attendee Scraper (b2match)
================================================================
Scrapes attendees from https://www.b2match.com/e/gitex-2026/components/64061

Extracts: name, title, company, description, country, profile_url
Saves to: Desktop/gitex2026_attendees.csv

Usage:
    python scrape_b2match_gitex.py

Flow:
    1. Chrome opens — log in manually with your b2match credentials
    2. Navigate to the attendees list if not already there
    3. Click the purple START button
    4. Scraper scrolls through all attendees automatically
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

START_URL = "https://www.b2match.com/e/gitex-2026/components/64061?cache=1"

OUTPUT_CSV = os.path.join(
    os.environ.get("USERPROFILE", r"C:\Users\lenovo"), "Desktop", "gitex2026_attendees.csv"
)


def _human_delay(min_s=0.8, max_s=2.0):
    time.sleep(random.uniform(min_s, max_s))


def _inject_start_button(page):
    try:
        page.evaluate("""
            () => {
                if (document.getElementById('b2m-scrape-btn')) return;
                var btn = document.createElement('button');
                btn.id = 'b2m-scrape-btn';
                btn.innerHTML = '&#9654; START SCRAPING';
                btn.style.cssText = [
                    'position:fixed','top:12px','right:12px','z-index:2147483647',
                    'padding:14px 28px','background:#6610f2','color:white',
                    'font-size:16px','font-weight:bold','border:none',
                    'border-radius:8px','cursor:pointer','box-shadow:0 4px 12px rgba(0,0,0,0.4)'
                ].join(';');
                btn.onclick = function() {
                    window.b2m_scrape_ready = true;
                    btn.innerHTML = '&#10003; Scraping...';
                    btn.style.background = '#198754';
                };
                document.body.appendChild(btn);
            }
        """)
    except Exception as e:
        print(f"  [btn] {e}")


def extract_attendees(page):
    """Extract all attendee cards currently visible on the page."""
    attendees = []
    try:
        # b2match attendee cards — try common selectors
        cards = page.query_selector_all("[class*='participant-card'], [class*='attendee-card'], [class*='person-card'], [class*='profile-card']")

        if not cards:
            # Fallback: look for list items with name + company structure
            cards = page.query_selector_all("li[class*='list-item'], div[class*='list-item']")

        if not cards:
            # Last resort: any linked card with a name heading
            cards = page.query_selector_all("a[href*='/participants/'], a[href*='/attendees/']")

        print(f"    Found {len(cards)} cards on page", flush=True)

        for card in cards:
            try:
                # Try various name selectors used by b2match
                name = ""
                for sel in ["h3", "h4", "[class*='name']", "[class*='title'] strong", "strong"]:
                    el = card.query_selector(sel)
                    if el:
                        t = el.inner_text().strip()
                        if t and len(t) > 1:
                            name = t
                            break

                # Job title
                title = ""
                for sel in ["[class*='job'], [class*='position'], [class*='role']", "p:nth-child(2)", "span:nth-child(2)"]:
                    el = card.query_selector(sel)
                    if el:
                        t = el.inner_text().strip()
                        if t:
                            title = t
                            break

                # Company
                company = ""
                for sel in ["[class*='company'], [class*='organisation'], [class*='organization']", "p:nth-child(3)"]:
                    el = card.query_selector(sel)
                    if el:
                        t = el.inner_text().strip()
                        if t:
                            company = t
                            break

                # Country
                country = ""
                for sel in ["[class*='country'], [class*='location']"]:
                    el = card.query_selector(sel)
                    if el:
                        t = el.inner_text().strip()
                        if t:
                            country = t
                            break

                # Profile URL
                href = card.get_attribute("href") or ""
                if not href:
                    link = card.query_selector("a[href*='/participants/'], a[href*='/profile/']")
                    if link:
                        href = link.get_attribute("href") or ""

                if name:
                    attendees.append({
                        "name": name,
                        "title": title,
                        "company": company,
                        "country": country,
                        "profile_url": href,
                    })
            except Exception as e:
                pass

    except Exception as e:
        print(f"  [extract error] {e}")

    return attendees


def scroll_and_collect(page):
    """Scroll through infinite scroll list collecting all attendees."""
    all_attendees = []
    seen_names = set()
    no_new_count = 0

    print("  Scrolling through attendees...", flush=True)

    for scroll_i in range(200):  # max 200 scroll attempts
        # Extract current visible cards
        batch = extract_attendees(page)
        new = 0
        for a in batch:
            if a["name"] and a["name"] not in seen_names:
                seen_names.add(a["name"])
                all_attendees.append(a)
                new += 1

        if new > 0:
            print(f"  [{scroll_i+1}] +{new} new ({len(all_attendees)} total)", flush=True)
            no_new_count = 0
        else:
            no_new_count += 1
            if no_new_count >= 5:
                print("  No new attendees after 5 scrolls — done.")
                break

        # Scroll down
        page.evaluate("window.scrollBy(0, window.innerHeight * 1.5)")
        _human_delay(1.2, 2.0)

        # Also try clicking "Load more" button if present
        try:
            load_more = page.query_selector("button[class*='load-more'], button[class*='show-more'], [class*='pagination'] button:last-child")
            if load_more and load_more.is_visible():
                load_more.click()
                _human_delay(1.5, 2.5)
        except Exception:
            pass

    return all_attendees


def main():
    print("\n" + "=" * 60)
    print("  Gitex 2026 Attendee Scraper (b2match)")
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

        print("\n  *** Log in with your b2match credentials ***")
        print("  *** Then navigate to the attendees list   ***")
        print("  *** Click the purple START button when ready ***\n")
        _inject_start_button(page)

        while True:
            try:
                if page.evaluate("() => window.b2m_scrape_ready === true"):
                    print("  START clicked — scraping!\n")
                    break
            except Exception:
                pass
            # Re-inject button in case page navigated
            _inject_start_button(page)
            time.sleep(0.5)

        _human_delay(1, 2)
        attendees = scroll_and_collect(page)
        browser.close()

    print(f"\n  Total: {len(attendees)} unique attendees")

    fields = ["name", "title", "company", "country", "profile_url"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(attendees)

    print(f"\n[DONE] Saved to: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
