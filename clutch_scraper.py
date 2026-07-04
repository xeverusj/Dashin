import random
import time

def _human_delay(min_s=1.8, max_s=4.0):
    time.sleep(random.uniform(min_s, max_s))

def _human_scroll(page, scrolls=None):
    count = scrolls or random.randint(3, 6)
    for _ in range(count):
        page.mouse.wheel(0, random.randint(300, 700))
        time.sleep(random.uniform(0.25, 0.7))

"""
clutch_scraper.py — Dedicated Clutch.co Company Scraper
========================================================
Scrapes company listings from clutch.co with correct selectors.
Extracts: name, rating, reviews, location, min budget, hourly rate,
          team size, top services, website, clutch profile URL.

Usage:
    python clutch_scraper.py "https://clutch.co/de/web-designers/berlin"
    python clutch_scraper.py "https://clutch.co/agencies/seo" --pages 5
    python clutch_scraper.py "https://clutch.co/uk/app-developers" --pages 10

Output:
    data/system/sessions/clutch_YYYY-MM-DD_HH-MM.csv
    Also saves to Dashin inventory DB if connected.
"""

import sys
import os
import time
import datetime
import csv
import re
import json
from pathlib import Path
from urllib.parse import urlparse, urljoin

# Windows async fix
if sys.platform.startswith("win"):
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from playwright.sync_api import sync_playwright

# ── Stealth mode ──────────────────────────────────────────────────────────────
try:
    from playwright_stealth import stealth_sync as _stealth_sync
    _STEALTH_AVAILABLE = True
except ImportError:
    _STEALTH_AVAILABLE = False
    print("  [stealth] playwright-stealth not installed — run: pip install playwright-stealth")

# ── Inhouse HTML selector — lxml + cssselect ──────────────────────────────────
try:
    from core.html_selector import Selector as _HTMLSelector
    _HTML_SELECTOR_AVAILABLE = True
    print("  [html_selector] Inhouse adaptive parsing active ✓")
except ImportError:
    _HTML_SELECTOR_AVAILABLE = False

# ── DB integration (optional) ─────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
_db_available = False
try:
    from core.db import get_connection, init_db
    init_db()
    _db_available = True
    print("  [DB] Connected to Dashin inventory ✓")
except Exception as e:
    print(f"  [DB] CSV-only mode ({e})")

DATA_FOLDER    = "data/system"
SESSIONS_FOLDER = os.path.join(DATA_FOLDER, "sessions")
os.makedirs(SESSIONS_FOLDER, exist_ok=True)


# ── CLUTCH SELECTORS ──────────────────────────────────────────────────────────
# These are the actual CSS selectors for clutch.co company cards (verified Feb 2026)

CARD_SELECTOR     = "li.provider-list-item, div.provider-row, article.provider"
NAME_SELECTORS    = [
    "h3.company_info--name",
    ".company-name",
    "h3[class*='company']",
    "h3[class*='provider']",
    ".provider-info--name",
    "h3",
]
RATING_SELECTORS  = [
    "span.rating",
    ".sg-rating__number",
    "[class*='rating__number']",
    ".clutch-rating",
]
REVIEWS_SELECTORS = [
    "a.reviews-count",
    ".reviews-count",
    "[class*='review']",
    "a[href*='reviews']",
]
LOCATION_SELECTORS = [
    ".locality",
    "[class*='location']",
    "[class*='locality']",
    "span[class*='city']",
]
BUDGET_SELECTORS  = [
    ".min-project-size",
    "[class*='budget']",
    "[class*='project-size']",
    "li[class*='min']",
]
HOURLY_SELECTORS  = [
    ".hourly-rate",
    "[class*='hourly']",
    "[class*='hour']",
]
SIZE_SELECTORS    = [
    ".employees",
    "[class*='employees']",
    "[class*='company-size']",
    "[class*='size']",
]
SERVICE_SELECTORS = [
    ".services-provided--chart li",
    ".service-item",
    "[class*='service'] li",
    ".chart-item",
]


def _try_selectors(card, selectors: list) -> str:
    """Try multiple selectors on a Playwright element, return first non-empty text found."""
    for sel in selectors:
        try:
            el = card.query_selector(sel)
            if el:
                text = el.inner_text().strip()
                if text:
                    return text
        except Exception:
            continue
    return ""


def _try_selectors_html(sc, selectors: list) -> str:
    """
    Try multiple CSS selectors on an inhouse Selector element.
    Uses ::text pseudo-element for cleaner text extraction.
    Falls back to raw element text if ::text returns nothing.
    """
    for sel in selectors:
        try:
            # Try with ::text first for clean text-only extraction
            text = sc.css(f'{sel}::text').get()
            if text and text.strip():
                return text.strip()
            # Fallback: get element and read its combined text
            el = sc.css(sel).get()
            if el and el.strip():
                # Strip HTML tags from result
                clean = re.sub(r'<[^>]+>', ' ', el).strip()
                if clean:
                    return clean
        except Exception:
            continue
    return ""


def extract_card(card) -> dict:
    """
    Extract all fields from a single company card element.
    Uses the inhouse html_selector engine when available for more robust
    extraction (handles minified/obfuscated class names, ::text, ::attr()).
    Falls back to Playwright query_selector() when lxml/cssselect is not installed.
    """
    name = rating = reviews = location = budget = hourly = size = ""
    services: list = []
    profile_url = website_url = ""

    # ── Inhouse html_selector path ────────────────────────────────────────────
    if _HTML_SELECTOR_AVAILABLE:
        try:
            card_html = card.evaluate("el => el.outerHTML")
            sc = _HTMLSelector(card_html)

            name     = _try_selectors_html(sc, NAME_SELECTORS)
            rating   = _try_selectors_html(sc, RATING_SELECTORS)
            reviews  = _try_selectors_html(sc, REVIEWS_SELECTORS)
            location = _try_selectors_html(sc, LOCATION_SELECTORS)
            budget   = _try_selectors_html(sc, BUDGET_SELECTORS)
            hourly   = _try_selectors_html(sc, HOURLY_SELECTORS)
            size     = _try_selectors_html(sc, SIZE_SELECTORS)

            # Services
            for sel in SERVICE_SELECTORS:
                try:
                    items = sc.css(f'{sel}::text').getall()
                    items = [i.strip() for i in items if i.strip()][:3]
                    if items:
                        services = items
                        break
                except Exception:
                    continue

            # Profile URL — use ::attr(href)
            for sel in ["a[href*='/profile/']", "a.company_info--name", "h3 a",
                        ".provider-info--header a"]:
                try:
                    href = sc.css(f'{sel}::attr(href)').get()
                    if href:
                        profile_url = (urljoin("https://clutch.co", href)
                                       if href.startswith("/") else href)
                        break
                except Exception:
                    continue

            # Website URL
            for sel in ["a[href*='website']", "a.website-link",
                        "[class*='website'] a",
                        "a[rel*='nofollow'][target='_blank']"]:
                try:
                    href = sc.css(f'{sel}::attr(href)').get()
                    if href and 'clutch' not in href.lower():
                        website_url = href
                        break
                except Exception:
                    continue

        except Exception as e:
            # html_selector failed — reset and fall through to Playwright path
            name = rating = reviews = location = budget = hourly = size = ""
            services = []
            profile_url = website_url = ""

    # ── Playwright path (fallback or when lxml/cssselect not installed) ───────
    if not name:
        name     = _try_selectors(card, NAME_SELECTORS)
        rating   = _try_selectors(card, RATING_SELECTORS)
        reviews  = _try_selectors(card, REVIEWS_SELECTORS)
        location = _try_selectors(card, LOCATION_SELECTORS)
        budget   = _try_selectors(card, BUDGET_SELECTORS)
        hourly   = _try_selectors(card, HOURLY_SELECTORS)
        size     = _try_selectors(card, SIZE_SELECTORS)

        for sel in SERVICE_SELECTORS:
            try:
                items = card.query_selector_all(sel)
                if items:
                    services = [i.inner_text().strip() for i in items[:3]
                                if i.inner_text().strip()]
                    break
            except Exception:
                continue

        try:
            link = card.query_selector(
                "a[href*='/profile/'], a.company_info--name, h3 a, .provider-info--header a"
            )
            if link:
                href = link.get_attribute("href") or ""
                if href:
                    profile_url = (urljoin("https://clutch.co", href)
                                   if href.startswith("/") else href)
        except Exception:
            pass

        try:
            site_link = card.query_selector(
                "a[href*='website'], a.website-link, [class*='website'] a, "
                "a[rel*='nofollow'][target='_blank']:not([href*='clutch'])"
            )
            if site_link:
                website_url = site_link.get_attribute("href") or ""
        except Exception:
            pass

    # Clean up fields
    reviews_clean = re.sub(r"[^\d]", "", reviews) if reviews else ""
    rating_clean  = re.sub(r"[^\d.]", "", rating)[:4] if rating else ""

    return {
        "company_name": name,
        "rating":       rating_clean,
        "reviews":      reviews_clean,
        "location":     location,
        "min_budget":   budget,
        "hourly_rate":  hourly,
        "team_size":    size,
        "top_services": " | ".join(services),
        "clutch_url":   profile_url,
        "website":      website_url,
    }


def save_to_db(companies: list, source_url: str, org_id: int = 1):
    """Save scraped companies to Dashin inventory as leads."""
    if not _db_available or not companies:
        return 0

    conn = get_connection()
    now  = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()
    saved = 0

    for co in companies:
        name = co.get("company_name", "").strip()
        if not name:
            continue
        try:
            # name_key for dedup
            nk = re.sub(r"[^a-z0-9]", "", name.lower())

            # Upsert company
            co_row = conn.execute(
                "SELECT id FROM companies WHERE name_key=? AND org_id=?",
                (nk, org_id)
            ).fetchone()

            if co_row:
                company_id = (co_row if isinstance(co_row, dict) else dict(co_row))["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO companies (org_id, name, name_key, created_at) VALUES (?,?,?,?)",
                    (org_id, name, nk, now)
                )
                company_id = cur.lastrowid

            # Upsert lead (company as lead — name = company name)
            lead_row = conn.execute(
                "SELECT id FROM leads WHERE name_key=? AND org_id=?",
                (nk, org_id)
            ).fetchone()

            if not lead_row:
                location = co.get("location", "")
                cur = conn.execute("""
                    INSERT INTO leads
                        (org_id, full_name, name_key, title, company_id,
                         status, source_type, last_seen_at, times_seen)
                    VALUES (?,?,?,?,?,'new','clutch',?,1)
                """, (org_id, name, nk,
                      co.get("top_services", "")[:100] or None,
                      company_id, now))
                lead_id = cur.lastrowid

                # Enrichment with what we know
                conn.execute("""
                    INSERT OR IGNORE INTO enrichment
                        (lead_id, org_id, country, industry, notes, enriched_at)
                    VALUES (?,?,?,?,?,?)
                """, (lead_id, org_id,
                      location or None,
                      "Agency / Services",
                      json.dumps({
                          "rating":     co.get("rating"),
                          "reviews":    co.get("reviews"),
                          "min_budget": co.get("min_budget"),
                          "hourly_rate":co.get("hourly_rate"),
                          "team_size":  co.get("team_size"),
                          "clutch_url": co.get("clutch_url"),
                          "website":    co.get("website"),
                      }),
                      now))
                saved += 1

        except Exception as e:
            print(f"  [DB] Error saving {name}: {e}")
            continue

    conn.commit()
    conn.close()
    return saved


def get_next_page_url(page, current_url: str, page_num: int) -> str | None:
    """Try to find the URL for the next page of results."""
    # Method 1: Look for a next button / link
    try:
        next_btn = page.query_selector(
            "a[rel='next'], "
            "a[aria-label='Next page'], "
            ".pagination__item--next a, "
            "li.next a, "
            "button[aria-label='next'], "
            ".pager__item--next a"
        )
        if next_btn:
            href = next_btn.get_attribute("href")
            if href:
                return urljoin(current_url, href)
    except Exception:
        pass

    # Method 2: Clutch uses ?page=N in URL
    parsed = urlparse(current_url)
    # Remove existing page param and add next
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    # Check current URL for page param
    match = re.search(r"[?&]page=(\d+)", current_url)
    if match:
        current_p = int(match.group(1))
        next_p = current_p + 1
        new_url = re.sub(r"([?&])page=\d+", f"\\1page={next_p}", current_url)
        return new_url
    else:
        # No page param yet — add ?page=2
        sep = "&" if "?" in current_url else "?"
        return f"{current_url}{sep}page={page_num + 1}"


def scrape_clutch(start_url: str, max_pages: int = 20, org_id: int = 1):
    """
    Main scraping function.
    Opens browser, shows purple START button, then scrapes all pages.
    """
    print("\n" + "="*60)
    print("  Clutch.co Company Scraper")
    print("="*60)
    print(f"  URL     : {start_url}")
    print(f"  Max pages: {max_pages}")
    print()

    timestamp  = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M')
    session_id = f"clutch_{timestamp}"
    csv_path   = os.path.join(SESSIONS_FOLDER, f"{session_id}.csv")

    all_companies = []
    seen_names    = set()

    # ── Write session start to DB ─────────────────────────────────────────────
    if _db_available:
        try:
            conn_s = get_connection()
            conn_s.execute("""
                INSERT OR IGNORE INTO scrape_sessions
                    (id, org_id, event_url, event_name, category, status, started_at)
                VALUES (?, ?, ?, ?, 'Clutch Directory', 'running', ?)
            """, (session_id, org_id, start_url,
                  f"Clutch: {start_url.replace('https://clutch.co','').strip('/')}",
                  datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()))
            conn_s.commit()
            conn_s.close()
            print(f"  [DB] Session started: {session_id}")
        except Exception as e:
            print(f"  [DB] Could not write session start: {e}")

    FIELDS = [
        "company_name", "rating", "reviews", "location",
        "min_budget", "hourly_rate", "team_size",
        "top_services", "clutch_url", "website", "source_page"
    ]

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
            color_scheme="light",
            extra_http_headers={
                "Accept-Language": "en-GB,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            }
        )
        page = context.new_page()

        # Apply stealth patches
        if _STEALTH_AVAILABLE:
            _stealth_sync(page)
            print("  [stealth] Anti-detection active ✓")

        print(f"Opening: {start_url}")
        try:
            page.goto(start_url, timeout=60000, wait_until="domcontentloaded")
        except Exception as e:
            print(f"  Load warning: {e}")
        _human_delay(3, 5)

        # ── Purple START button ────────────────────────────────────────────
        print("  Apply any filters you need, then click the purple START button.\n")
        _inject_start_button(page)

        while True:
            try:
                if page.evaluate("() => window.clutch_scrape_ready === true"):
                    print("  START clicked — beginning scrape!\n")
                    break
            except Exception:
                pass
            _inject_start_button(page)
            time.sleep(1)

        # Get actual start URL (may have changed due to filters)
        current_url = page.url
        page_num = 1

        while page_num <= max_pages:
            print(f"  Scraping page {page_num} / {max_pages}...")

            # Wait for cards to load
            try:
                page.wait_for_selector(
                    "li.provider-list-item, div.provider-row, article.provider, "
                    "[class*='provider-list'], ul.providers-list li",
                    timeout=15000
                )
            except Exception:
                print(f"  No cards found on page {page_num} — trying anyway...")

            # Scroll to load lazy content
            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(1)
            page.evaluate("window.scrollTo(0, 0)")
            time.sleep(1)

            # Try multiple card selectors until we find cards
            cards = []
            card_selectors_to_try = [
                "li.provider-list-item",
                "div.provider-row",
                "article.provider",
                "[class*='provider-list-item']",
                "ul.providers-list > li",
                "[class*='provider_row']",
                "div[class*='provider'][class*='item']",
            ]
            for sel in card_selectors_to_try:
                try:
                    found = page.query_selector_all(sel)
                    if len(found) > 2:
                        cards = found
                        print(f"  Found {len(cards)} company cards (selector: {sel!r})")
                        break
                except Exception:
                    continue

            if not cards:
                print(f"  ⚠ No cards found on page {page_num}.")
                # Try extracting from page source as last resort
                cards_fallback = _extract_from_page_text(page, seen_names)
                if cards_fallback:
                    all_companies.extend(cards_fallback)
                    for c in cards_fallback:
                        seen_names.add(c["company_name"].lower())
                    print(f"  Extracted {len(cards_fallback)} companies via text fallback")
                break

            # Extract data from each card
            page_companies = []
            for card in cards:
                try:
                    data = extract_card(card)
                    name = data.get("company_name", "").strip()
                    if not name or name.lower() in seen_names:
                        continue
                    if len(name) < 2 or name.lower() in (
                        "view profile", "visit website", "services provided"
                    ):
                        continue
                    seen_names.add(name.lower())
                    data["source_page"] = current_url
                    page_companies.append(data)
                except Exception as e:
                    continue

            print(f"  ✓ {len(page_companies)} new companies extracted")
            all_companies.extend(page_companies)

            # Save to CSV after each page (incremental)
            _save_csv(all_companies, csv_path, FIELDS)
            print(f"  CSV saved: {len(all_companies)} total → {csv_path}")

            # Check if we should continue
            if page_num >= max_pages:
                print(f"\n  Reached max pages ({max_pages}).")
                break

            # Go to next page
            next_url = get_next_page_url(page, current_url, page_num)
            if not next_url or next_url == current_url:
                # Try clicking next button
                try:
                    next_btn = page.query_selector(
                        "a[rel='next'], .pagination__item--next a, li.next a"
                    )
                    if next_btn:
                        next_btn.click()
                        time.sleep(4)
                        current_url = page.url
                        page_num += 1
                        continue
                except Exception:
                    pass
                print("\n  No more pages found — scrape complete.")
                break

            print(f"  → Next page: {next_url}")
            try:
                page.goto(next_url, timeout=30000, wait_until="domcontentloaded")
                _human_delay(3, 5)
            except Exception as e:
                print(f"  Navigation error: {e}")
                break

            current_url = page.url
            page_num += 1

        browser.close()

    # ── Final save ──────────────────────────────────────────────────────────
    _save_csv(all_companies, csv_path, FIELDS)

    print(f"\n{'='*60}")
    print(f"  SCRAPE COMPLETE")
    print(f"  Companies found : {len(all_companies)}")
    print(f"  CSV saved to    : {csv_path}")

    # Save to DB
    db_saved = 0
    if _db_available and all_companies:
        db_saved = save_to_db(all_companies, start_url, org_id)
        print(f"  Saved to DB     : {db_saved} new leads in inventory")

    # ── Write session finish to DB ────────────────────────────────────────────
    if _db_available:
        try:
            conn_s = get_connection()
            conn_s.execute("""
                UPDATE scrape_sessions SET
                    status      = 'done',
                    leads_found = ?,
                    leads_new   = ?,
                    finished_at = ?
                WHERE id = ?
            """, (len(all_companies), db_saved,
                  datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat(), session_id))
            conn_s.commit()
            conn_s.close()
            print(f"  [DB] Session finished: {session_id}")
        except Exception as e:
            print(f"  [DB] Could not write session finish: {e}")

    print(f"{'='*60}\n")
    return all_companies, csv_path


def _inject_start_button(page):
    """Inject the purple START SCRAPING button into the page."""
    try:
        if page.evaluate("() => !!document.getElementById('clutch-scrape-btn')"):
            return
    except Exception:
        pass
    try:
        page.evaluate("""() => {
            if (document.getElementById('clutch-scrape-btn')) return;
            var btn = document.createElement('button');
            btn.id = 'clutch-scrape-btn';
            btn.innerHTML = '▶ START SCRAPING';
            btn.style.cssText = [
                'position:fixed', 'top:12px', 'right:12px', 'z-index:2147483647',
                'padding:14px 28px', 'background:#6610f2', 'color:white',
                'border:2px solid white', 'border-radius:8px', 'font-size:15px',
                'font-weight:bold', 'cursor:pointer',
                'box-shadow:0 4px 16px rgba(0,0,0,0.5)',
                'font-family:sans-serif'
            ].join(';');
            btn.onclick = function() {
                window.clutch_scrape_ready = true;
                this.innerHTML = '⏳ Running...';
                this.style.background = '#ffc107';
                this.style.color = 'black';
            };
            document.body.appendChild(btn);
        }""")
    except Exception:
        pass


def _extract_from_page_text(page, seen_names: set) -> list:
    """
    Fallback: extract company names directly from page HTML using Clutch's
    known JSON-LD structured data or meta tags.
    Uses Scrapling's selector engine when available for more reliable
    extraction of script[type="application/ld+json"] elements.
    """
    companies = []

    def _parse_jsonld_items(raw_text: str) -> list:
        """Parse a JSON-LD string and return a flat list of record items."""
        try:
            data = json.loads(raw_text)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("itemListElement", [data])
        except Exception:
            pass
        return []

    def _item_to_company(item: dict) -> dict | None:
        name = (item.get("name") or
                (item.get("item") or {}).get("name") or "")
        if not name or name.lower() in seen_names or len(name) <= 2:
            return None
        rating = str(
            item.get("ratingValue") or
            (item.get("aggregateRating") or {}).get("ratingValue") or ""
        )
        return {
            "company_name": name.strip(),
            "rating":       rating,
            "reviews":      "",
            "location":     "",
            "min_budget":   "",
            "hourly_rate":  "",
            "team_size":    "",
            "top_services": "",
            "clutch_url":   item.get("url") or "",
            "website":      "",
        }

    # ── Inhouse html_selector path ────────────────────────────────────────────
    if _HTML_SELECTOR_AVAILABLE:
        try:
            html = page.content()
            root = _HTMLSelector(html)
            # ::text on script elements returns the raw script body
            for raw in root.css('script[type="application/ld+json"]::text').getall():
                for item in _parse_jsonld_items(raw):
                    co = _item_to_company(item)
                    if co:
                        companies.append(co)
            if companies:
                return companies
        except Exception:
            pass  # fall through to Playwright path

    # ── Playwright path ───────────────────────────────────────────────────────
    try:
        scripts = page.query_selector_all('script[type="application/ld+json"]')
        for script in scripts:
            try:
                for item in _parse_jsonld_items(script.inner_text()):
                    co = _item_to_company(item)
                    if co:
                        companies.append(co)
            except Exception:
                continue
    except Exception:
        pass

    return companies


def _save_csv(companies: list, path: str, fields: list):
    """Save companies list to CSV file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(companies)


# ── ENTRY POINT ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Clutch.co Company Scraper")
    parser.add_argument("url", nargs="?",
                        default="https://clutch.co/agencies",
                        help="Clutch.co listing URL to scrape")
    parser.add_argument("--pages", type=int, default=20,
                        help="Maximum pages to scrape (default: 20)")
    parser.add_argument("--org_id", type=int, default=1,
                        help="Dashin org_id to save leads under")
    args = parser.parse_args()

    companies, csv_file = scrape_clutch(
        start_url=args.url,
        max_pages=args.pages,
        org_id=args.org_id,
    )

    # Print summary table
    if companies:
        print(f"\nFirst {min(5, len(companies))} results:")
        print("-" * 80)
        for c in companies[:5]:
            print(f"  {c['company_name']:<35} {c['rating']:>4} ★  "
                  f"{c['reviews']:>4} reviews  {c['location']}")
        print("-" * 80)
        print(f"Full results in: {csv_file}")
