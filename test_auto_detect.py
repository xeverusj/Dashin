"""
Quick validation of core/auto_detect.py against the real sites scraped today.
Loads each page headless and runs the heuristic detector — prints what it found.
"""
import sys
if sys.platform.startswith("win"):
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from playwright.sync_api import sync_playwright
from core.auto_detect import identify_structure

SITES = [
    ("HLTH speakers",      "https://hlth.com/events/europe/speakers/"),
    ("health.tech",        "https://www.health.tech/speakers-2026"),
    ("biotech-careers",    "https://biotech-careers.org/business-area/microbiome"),
    ("ensun microbiome",   "https://ensun.io/search?q=Microbiome&locations=Finland%2Cnull%2Cnull"),
]

def main():
    only = sys.argv[1].lower() if len(sys.argv) > 1 else None
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=[
            "--disable-blink-features=AutomationControlled", "--no-sandbox"])
        ctx = browser.new_context(user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"))
        page = ctx.new_page()

        for name, url in SITES:
            if only and only not in name.lower():
                continue
            print("\n" + "=" * 64)
            print(f"  {name}\n  {url}")
            print("=" * 64)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(4000)
                s = identify_structure(page)
                if s:
                    print(f"  card_selector : {s['card_selector']}")
                    print(f"  name_selector : {s['name_selector']}")
                    print(f"  title_selector: {s['title_selector']}")
                    print(f"  company_sel   : {s['company_selector']}")
                    print(f"  pagination    : {s['pagination_type']}  next={s['next_button_selector']}")
                    print(f"  confidence    : {s['confidence']:.2f}  cards={s['card_count']}")
                    live = len(page.query_selector_all(s['card_selector']))
                    print(f"  LIVE COUNT    : {live} elements match card_selector")
                else:
                    print("  >> no structure detected")
            except Exception as e:
                print(f"  ERROR: {e}")

        browser.close()

if __name__ == "__main__":
    main()
