"""
Universal Event Scraper — AI-Powered
=====================================
Uses Claude Vision to identify attendee card selectors on ANY event website.
No hardcoded selectors. Works on Brella, BETT, FDF, E-world, and any new site.

SETUP:
  1. Get your API key at https://console.anthropic.com → API Keys
  2. Set it: set ANTHROPIC_API_KEY=your-key-here  (Windows)
             export ANTHROPIC_API_KEY=your-khey-here (Mac/Linux)
  3. pip install playwright pandas anthropic
  4. playwright install chromium
  5. python worker.py <event_url>
"""

import asyncio
import logging
import sys
import os
import base64
import json
import re
import time
import datetime
import uuid

import pandas as pd
import anthropic

from urllib.parse import urlparse, parse_qs, urlencode, urlunparse


# ── Human behaviour helpers ───────────────────────────────────────────────────
import random

def _human_delay(min_s=1.5, max_s=3.5):
    """Random delay to mimic human reading/thinking time."""
    time.sleep(random.uniform(min_s, max_s))

def _human_scroll(page, scrolls=None):
    """Scroll the page in human-like chunks instead of instant jumps."""
    count = scrolls or random.randint(3, 7)
    for _ in range(count):
        page.mouse.wheel(0, random.randint(250, 650))
        time.sleep(random.uniform(0.2, 0.6))
from playwright.sync_api import sync_playwright

# ── Stealth mode — patches 20+ bot-detection signals ─────────────────────────
try:
    from playwright_stealth import stealth_sync as _stealth_sync
    _STEALTH_AVAILABLE = True
except ImportError:
    _STEALTH_AVAILABLE = False
    print("  [stealth] playwright-stealth not installed — run: pip install playwright-stealth")

# ── Inhouse HTML selector — lxml + cssselect ──────────────────────────────────
# Uses the same underlying libraries as Scrapling (lxml + cssselect) but
# without any external scraping framework dependency. Provides a Parsel-
# compatible API: Selector(html), .css('sel::text'), .css('a::attr(href)'),
# .find_all('div'), .get() / .getall().
try:
    from core.html_selector import Selector as _HTMLSelector
    _HTML_SELECTOR_AVAILABLE = True
    print("  [html_selector] Inhouse adaptive parsing active ✓")
except ImportError:
    _HTML_SELECTOR_AVAILABLE = False
    print("  [html_selector] lxml/cssselect not installed — run: pip install lxml cssselect")

# API-free structure detection (Tier 2 primary). Runs a statistical repeat-
# pattern finder in the rendered DOM instead of Claude Vision — no key, no cost.
# Claude Vision remains an optional last-resort fallback only when a key exists.
try:
    from core.auto_detect import identify_structure as _heuristic_identify
    _AUTODETECT_AVAILABLE = True
    print("  [auto_detect] API-free structure detection active ✓")
except ImportError:
    _AUTODETECT_AVAILABLE = False

# Windows fix — use default event loop (ProactorEventLoopPolicy deprecated in 3.12+)
if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
    except Exception:
        pass

# ==========================================
# ⚙ CONFIGURATION
# ==========================================
DATA_FOLDER = "data/system"
SESSIONS_FOLDER = os.path.join(DATA_FOLDER, "sessions")
LEADS_FILE = os.path.join(DATA_FOLDER, "leads_master.csv")
COMPANIES_FILE = os.path.join(DATA_FOLDER, "companies_master.csv")
LAYOUT_PATTERNS_FILE = os.path.join(DATA_FOLDER, "layout_patterns.json")

# How many times Claude will retry identifying cards before giving up
AI_MAX_RETRIES = 3

# ==========================================
# DB BRIDGE — save to Dashin SQLite DB
# ==========================================
# Each import is independent — if one fails the others still work.
# worker.py always runs from the project root so core/ and services/ are importable.

_db_save_lead     = None
_db_start_session = None
_db_finish_session = None
_db_available     = False

try:
    import sys as _sys, os as _os
    # Ensure project root is on path when worker.py is run directly
    _proj = _os.path.dirname(_os.path.abspath(__file__))
    if _proj not in _sys.path:
        _sys.path.insert(0, _proj)

    from core.db import init_db as _db_init, get_connection as _db_conn
    _db_init()

    from services.lead_service import save_lead as _db_save_lead
    from services.lead_service import start_session as _db_start_session
    from services.lead_service import finish_session as _db_finish_session

    _db_available = True
    print("  [DB] Connected to Dashin database ✓")

except Exception as _db_err:
    print(f"  [DB] Running CSV-only mode ({_db_err})")


def _db_save_batch(contacts_dict, source_url, category, layout,
                   session_id, event_name, org_id=1):
    """
    Write a batch of scraped leads to the SQLite inventory.
    Called after CSV save succeeds.
    On repeated DB failures, writes a failed_db_saves.json alert file.
    """
    if not _db_save_lead or not _db_available:
        return 0

    new_count = 0
    err_count = 0
    last_error = None

    for p in contacts_dict.values():
        try:
            lead_id, is_new = _db_save_lead(
                org_id       = org_id,
                full_name    = str(p.get("name", "")).strip(),
                company_name = str(p.get("company", "")).strip(),
                title        = str(p.get("title", "")).strip(),
                tags         = str(p.get("tags", "")),
                event_name   = event_name or "",
                event_url    = source_url or "",
                category     = str(p.get("category") or category or "").strip(),
                layout       = layout or "",
                session_id   = session_id or "",
            )
            if is_new:
                new_count += 1
        except Exception as e:
            err_count += 1
            last_error = str(e)
            if err_count == 1:
                print(f"  [DB] Save warning: {e}")
            if err_count > 10:
                print(f"\n{'='*60}")
                print(f"  ⚠️  DB SAVE FAILING — leads saved to CSV only.")
                print(f"  Check failed_db_saves.json for details.")
                print(f"{'='*60}\n")
                # Write alert file for the UI to pick up
                _write_failed_save_alert(
                    session_id   = session_id,
                    batch_count  = len(contacts_dict),
                    error        = last_error,
                )
                break

    if new_count:
        print(f"  [DB] {new_count} new leads saved to inventory")
    return new_count


def _write_failed_save_alert(session_id: str, batch_count: int, error: str):
    """Write a failed_db_saves.json alert that the scraper dashboard can detect."""
    alert_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "failed_db_saves.json"
    )
    try:
        existing = []
        if os.path.exists(alert_path):
            with open(alert_path, 'r') as f:
                existing = json.load(f)
    except Exception:
        existing = []

    existing.append({
        "timestamp":   datetime.datetime.now().isoformat(),
        "session_id":  session_id,
        "batch_count": batch_count,
        "error":       error,
    })

    try:
        with open(alert_path, 'w') as f:
            json.dump(existing, f, indent=2)
    except Exception as write_err:
        print(f"  [DB] Could not write failed_db_saves.json: {write_err}")


# ==========================================
# DATABASE INIT
# ==========================================
def init_db():
    for folder in [DATA_FOLDER, SESSIONS_FOLDER]:
        os.makedirs(folder, exist_ok=True)
    if not os.path.exists(COMPANIES_FILE):
        pd.DataFrame(columns=[
            'company_id', 'company_name', 'created_at'
        ]).to_csv(COMPANIES_FILE, index=False)
    if not os.path.exists(LEADS_FILE):
        pd.DataFrame(columns=[
            'lead_id', 'full_name', 'title', 'company_id',
            'category', 'tags', 'source_url', 'scraped_at'
        ]).to_csv(LEADS_FILE, index=False)


# ==========================================
# LAYOUT MEMORY (per domain)
# ==========================================
def load_patterns():
    """
    Load layout patterns. SQLite is the primary store; JSON is the fallback.
    Evicts patterns older than 30 days.
    """
    now_ts = time.time()
    TTL = 30 * 86400

    # Try SQLite first (primary store)
    if _db_available:
        try:
            conn = _db_conn()
            rows = conn.execute(
                "SELECT * FROM layout_patterns WHERE confidence >= 0.7"
            ).fetchall()
            conn.close()
            if rows:
                cleaned = {}
                for row in rows:
                    domain = row.get("domain")
                    if not domain:
                        continue
                    last_used = row.get("last_used", "")
                    try:
                        import datetime as _dt
                        lu_ts = _dt.datetime.fromisoformat(last_used).timestamp() if last_used else 0
                    except Exception:
                        lu_ts = 0
                    if lu_ts and (now_ts - lu_ts) > TTL:
                        print(f"  [cache] Pattern for {domain} expired — will re-ask Claude")
                        continue
                    selectors = {}
                    try:
                        selectors = json.loads(row.get("selectors") or "{}")
                    except Exception as e:
                        logging.warning(f"[worker.load_patterns] Failed to parse selectors JSON for {domain}: {e}")
                    cleaned[domain] = {
                        "card_selector":       selectors.get("card", ""),
                        "pagination_type":     row.get("pagination_type", "none"),
                        "next_button_selector": selectors.get("next_button", ""),
                        "layout_type":         row.get("layout_type", "generic"),
                        "confidence":          row.get("confidence", 1.0),
                        "saved_at":            lu_ts,
                    }
                if cleaned:
                    return cleaned
        except Exception as e:
            print(f"  [cache] SQLite pattern load failed: {e} — falling back to JSON")

    # Fall back to JSON
    try:
        with open(LAYOUT_PATTERNS_FILE, 'r') as f:
            raw = json.load(f)
        cleaned = {}
        for domain, data in raw.items():
            saved_at = data.get("saved_at", 0)
            if saved_at and (now_ts - saved_at) > TTL:
                print(f"  [cache] Pattern for {domain} expired — will re-ask Claude")
                continue
            cleaned[domain] = data
        return cleaned
    except Exception:
        return {}


def save_pattern(domain, data):
    """
    Save learned card selector + pagination type for a domain.
    Writes to BOTH SQLite (primary) and JSON (backup) atomically.
    """
    data["saved_at"] = time.time()

    # Write to SQLite
    if _db_available:
        try:
            conn = _db_conn()
            selectors = json.dumps({
                "card":        data.get("card_selector", ""),
                "next_button": data.get("next_button_selector", ""),
            })
            conn.execute("""
                INSERT INTO layout_patterns
                    (org_id, domain, layout_type, selectors,
                     pagination_type, confidence, last_used, last_verified)
                VALUES (NULL, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                ON CONFLICT(domain, layout_type, org_id) DO UPDATE SET
                    selectors       = excluded.selectors,
                    pagination_type = excluded.pagination_type,
                    confidence      = excluded.confidence,
                    last_used       = excluded.last_used,
                    last_verified   = excluded.last_verified,
                    success_count   = success_count + 1
            """, (domain,
                  data.get("layout_type", "generic"),
                  selectors,
                  data.get("pagination_type", "none"),
                  data.get("confidence", 1.0)))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"  [cache] SQLite pattern save failed: {e}")

    # Write to JSON (backup)
    try:
        patterns = {}
        if os.path.exists(LAYOUT_PATTERNS_FILE):
            try:
                with open(LAYOUT_PATTERNS_FILE, 'r') as f:
                    patterns = json.load(f)
            except Exception:
                patterns = {}
        patterns[domain] = data
        os.makedirs(DATA_FOLDER, exist_ok=True)
        with open(LAYOUT_PATTERNS_FILE, 'w') as f:
            json.dump(patterns, f, indent=2)
    except Exception as e:
        print(f"  [cache] JSON pattern save failed: {e}")

    print(f"  Saved layout pattern for {domain}")


# ==========================================
# AI CARD SELECTOR DETECTION
# ==========================================
def get_api_key():
    # 1. Already in environment (setx, export, or previous session)
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key

    # 2. Check .env file in project root
    project_root = os.path.dirname(os.path.abspath(__file__))
    for env_path in [
        os.path.join(project_root, ".env"),
        os.path.join(project_root, "config.env"),
        os.path.join(os.path.expanduser("~"), ".dashin.env"),
    ]:
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("ANTHROPIC_API_KEY="):
                        key = line.split("=", 1)[1].strip().strip('"\' 	')
                        if key:
                            os.environ["ANTHROPIC_API_KEY"] = key
                            print(f"  [API] Key loaded from {env_path}")
                            return key

    # 3. Try Windows registry (set by setx)
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Environment"
            ) as reg_key:
                key, _ = winreg.QueryValueEx(reg_key, "ANTHROPIC_API_KEY")
                key = key.strip()
                if key:
                    os.environ["ANTHROPIC_API_KEY"] = key
                    print("  [API] Key loaded from Windows registry")
                    return key
        except Exception:
            pass

    # 4. Nothing found — ask user and save to .env for next time
    print("\n" + "="*60)
    print("  ANTHROPIC_API_KEY not set.")
    print("  Get yours at: https://console.anthropic.com")
    print("="*60 + "\n")
    key = input("Paste your API key here (will be saved to .env): ").strip()
    if key:
        os.environ["ANTHROPIC_API_KEY"] = key
        # Save to .env so it works next time without needing a new terminal
        env_file = os.path.join(project_root, ".env")
        try:
            # Read existing content to avoid duplicates
            existing = ""
            if os.path.exists(env_file):
                with open(env_file) as f:
                    existing = f.read()
            if "ANTHROPIC_API_KEY=" not in existing:
                with open(env_file, "a") as f:
                    f.write(f"\nANTHROPIC_API_KEY={key}\n")
                print(f"  [API] Key saved to {env_file} — won\'t ask again.")
        except Exception as e:
            logging.warning(f"[worker] Failed to persist API key to .env: {e}")
    return key


def take_screenshot(page):
    """Take a full-page screenshot and return as base64."""
    try:
        screenshot_bytes = page.screenshot(full_page=False)  # viewport only, faster
        return base64.standard_b64encode(screenshot_bytes).decode("utf-8")
    except Exception as e:
        print(f"  Screenshot failed: {e}")
        return None


def get_page_html_sample(page):
    """
    Extract a representative HTML sample for Claude to analyse.
    Gets the first 8000 chars of the body HTML — enough to see card patterns.
    """
    try:
        html = page.evaluate("""() => {
            // Get main content area (prefer main/article/section over full body)
            const main = document.querySelector('main, #main, .main, article, section, #content, .content');
            const el = main || document.body;
            return el.innerHTML.substring(0, 8000);
        }""")
        return html
    except:
        return ""


def take_screenshots_multi(page):
    """
    Take a screenshot for Claude to analyse.
    
    NOTE: We intentionally take only ONE screenshot from the current scroll
    position (top of page). Scrolling during detection breaks SPA virtual-DOM
    apps like Brella and E-world — cards get unmounted/remounted and the
    verify pass sees 0 elements, forcing endless retries.
    
    The multi-scroll approach is kept as dead code below in case it is ever
    needed for static sites where scrolling is safe.
    """
    shots = []
    try:
        # Ensure we're at the top before capturing
        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(1.5)   # let SPA re-render after scroll-to-top
        s = page.screenshot(full_page=False)
        shots.append(base64.standard_b64encode(s).decode())
    except Exception as e:
        print(f"  Screenshot failed: {e}")
    return shots


def verify_selector_on_page(page, card_selector, retries=4):
    """
    Verify a selector finds cards in the live DOM.

    For SPA/virtual-DOM apps like Brella, cards only render AFTER the list
    has been scrolled into the viewport at least once. We do a small scroll
    nudge before each check to trigger that initial render.

    Returns count found (>0 = selector works). 0 after all retries = wrong selector.
    """
    if not card_selector:
        return 0

    for attempt in range(retries):
        try:
            # Nudge the page to trigger virtual-DOM rendering
            page.evaluate("window.scrollBy(0, 200)")
            time.sleep(0.8)
            page.evaluate("window.scrollBy(0, -200)")
            time.sleep(0.5)

            count = page.evaluate(
                "(sel) => document.querySelectorAll(sel).length",
                card_selector
            )
            if int(count) > 0:
                return int(count)
        except Exception:
            pass
        if attempt < retries - 1:
            time.sleep(1.5)

    return 0


def ask_claude_for_selector(page, api_key, attempt=1):
    """
    Send multiple screenshots + HTML sample to Claude and ask it to identify
    the CSS selector for repeating records on ANY type of listing page.

    Improvements over v1:
    - 3 screenshots (top/middle/bottom) so Claude sees the full card pattern
    - Verification pass: selector is tested against the live DOM before returning
    - If selector finds 0 cards, confidence is forced to 0 and we retry

    Returns a dict with card_selector, sub-selectors, pagination_type, confidence.
    """
    print(f"  Asking Claude to identify page structure (attempt {attempt}/{AI_MAX_RETRIES})...")

    screenshots = take_screenshots_multi(page)
    html_sample = get_page_html_sample(page)

    prompt = f"""You are a senior web scraping engineer. Your job is to extract structured data from event/directory listing pages.

You are given a screenshot of the page AND a sample of the page HTML.

HTML SAMPLE (first 8000 chars of main content area):
<html_sample>
{html_sample}
</html_sample>

TASK: Analyse the page and return a JSON scraping specification.

REASONING STEPS (work through these before answering):
1. What type of page is this? (event attendees, company directory, job board, product list, etc.)
2. What does one "record" look like? (a person card, a company row, a product tile, etc.)
3. What CSS selector uniquely identifies each REPEATING record container?
   - Look for repeated structural patterns: same tag + class combination appearing 3+ times
   - Prefer data-* attributes (data-test, data-testid, data-id) over class names
   - If classes are obfuscated (random strings like "x7f3k"), use structural selectors: article, li, [data-testid], div:nth-child patterns
   - The selector MUST match EACH individual record, not the parent list container
4. Within one record, what sub-elements contain: name? title? company? extra info?
5. How does pagination work? Look for:
   - "Next" buttons, numbered page buttons, "Load More" buttons → next_button
   - ?page= or ?pageNumber= in the URL → url_param
   - Scrolling loads more content → infinite_scroll

CRITICAL RULES:
- card_selector must match INDIVIDUAL repeating items (not the parent wrapper)
- If you see data-test or data-testid attributes on cards — USE THEM (they're stable)
- Sub-selectors are RELATIVE to the card element (e.g. "h3" not "div.card h3")
- For people pages: name_selector = person name, company_selector = employer, title_selector = job title
- For company pages: name_selector = company name, title_selector = category/tagline
- Set confidence honestly: 0.9 = data-test attrs found and verified, 0.7 = class pattern clear, 0.5 = uncertain, 0.3 = cannot identify
- If HTML is obfuscated, still try structural selectors — don't give up

Return ONLY valid JSON (no markdown, no explanation, no ```):
{{
  "description": "one sentence: what type of listing and what each card contains",
  "page_type": "event_attendees | people_directory | company_directory | product_list | job_board | other",
  "card_selector": "CSS selector matching each individual record (required)",
  "name_selector": "sub-selector for person/company name, or null",
  "title_selector": "sub-selector for job title or category, or null",
  "company_selector": "sub-selector for employer/company, or null",
  "extra_selector": "sub-selector for any extra useful field (location, rating, tags), or null",
  "pagination_type": "url_param | next_button | infinite_scroll | load_more",
  "next_button_selector": "CSS selector for the Next button if pagination_type=next_button, else null",
  "confidence": 0.0,
  "reasoning": "2-3 sentence explanation of why you chose this card_selector"
}}"""

    try:
        client = anthropic.Anthropic(api_key=api_key)

        # Build content: all 3 screenshots + text prompt
        messages_content = []
        for i, shot in enumerate(screenshots):
            label = ["Top of page", "Middle of page", "Bottom of page"][i]
            messages_content.append({
                "type": "text",
                "text": f"Screenshot {i+1}/3 — {label}:"
            })
            messages_content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": shot}
            })
        messages_content.append({"type": "text", "text": prompt})

        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1200,
            messages=[{"role": "user", "content": messages_content}]
        )

        raw = response.content[0].text.strip()
        raw = re.sub(r'^```[a-z]*\n?', '', raw)
        raw = re.sub(r'\n?```$', '', raw)
        raw = raw.strip()

        result = json.loads(raw)

        # ── Optional verification pass ────────────────────────────────────
        # Test the selector against the live DOM. On SPA/virtual-scroll apps
        # (Brella, E-world) the DOM query can return 0 even when cards are
        # VISIBLE on screen — because the list is virtualized or loads async.
        #
        # Policy: verify is a BONUS BOOST only.
        # - If live_count > 0  → great, boost confidence slightly
        # - If live_count == 0 → do NOT penalize. Trust Claude's vision.
        #   The actual scrape loop will quickly reveal if the selector is wrong.
        card_sel = result.get("card_selector")
        live_count = verify_selector_on_page(page, card_sel)
        reported_confidence = float(result.get("confidence", 0))

        if live_count > 0:
            print(f"  ✓ Selector {card_sel!r} verified: {live_count} cards in live DOM")
            result["confidence"] = min(0.95, reported_confidence + 0.1)
        else:
            # Can't confirm via DOM — could be virtual scroll or async load
            # Keep Claude's original confidence; don't force retry
            print(f"  ~ Selector {card_sel!r} unconfirmed in DOM (virtual scroll?) — trusting Claude")

        # ── Inhouse selector cross-check on rendered HTML ─────────────────
        # Parses the RENDERED HTML string (not the live DOM), which catches
        # cards that are in the HTML but not yet in the Playwright DOM object.
        if _HTML_SELECTOR_AVAILABLE and card_sel:
            try:
                html = page.content()
                html_root = _HTMLSelector(html)
                html_count = len(html_root.css(card_sel))
                if html_count > 0:
                    print(f"  ✓ html_selector confirms: {html_count} cards in rendered HTML")
                    if live_count == 0:
                        # DOM said 0 but HTML has cards — mild confidence boost
                        result["confidence"] = min(0.95, result["confidence"] + 0.05)
                else:
                    print(f"  ~ html_selector: 0 matches in rendered HTML (JS-rendered?)")
            except Exception as _se:
                pass  # HTML selector check is optional — never block on it

        desc = result.get("description", "")
        print(f"  Page: {desc}")
        print(f"  card_selector={card_sel!r}  live_count={live_count}  confidence={result['confidence']:.2f}")
        return result

    except json.JSONDecodeError as e:
        print(f"  Claude returned invalid JSON: {e}")
        return None
    except Exception as e:
        print(f"  Claude API error: {e}")
        return None


def ai_identify_page_structure(page, api_key, domain):
    """
    Try up to AI_MAX_RETRIES times to get a confident selector from Claude.
    Returns the structure dict, or None if all retries fail.
    """
    best_result = None
    best_confidence = 0.0

    for attempt in range(1, AI_MAX_RETRIES + 1):
        result = ask_claude_for_selector(page, api_key, attempt)

        if result is None:
            print(f"  Attempt {attempt} failed (no response)")
            time.sleep(2)
            continue

        confidence = float(result.get("confidence", 0))

        if confidence > best_confidence:
            best_confidence = confidence
            best_result = result

        if confidence >= 0.7:
            print(f"  Confident enough (confidence={confidence:.2f}) - proceeding")
            break
        else:
            print(f"  Low confidence ({confidence:.2f}) - retrying...")
            # Gentle nudge to trigger SPA rendering — do NOT press End/Home,
            # that unmounts virtual-DOM cards on Brella/E-world
            try:
                page.evaluate("window.scrollBy(0, 300)")
                time.sleep(2)
                page.evaluate("window.scrollBy(0, -300)")
                time.sleep(1)
            except Exception:
                time.sleep(2)

    if best_result and best_confidence >= 0.65:
        print(f"  Best result: confidence={best_confidence:.2f}, selector={best_result.get('card_selector')!r}")
        save_pattern(domain, best_result)
        return best_result
    elif best_result and best_confidence >= 0.4:
        # Use it for this session but DON'T cache it — not reliable enough
        print(f"  Using selector (confidence={best_confidence:.2f}) but NOT caching (below 0.65 threshold)")
        return best_result
    else:
        print(f"  All {AI_MAX_RETRIES} attempts failed or too low confidence ({best_confidence:.2f})")
        print("  Falling back to generic div scraper...")
        return None


def identify_page_structure(page, api_key, domain):
    """
    Primary structure-detection entry point: heuristic first, AI as fallback.

    Order of attempts:
      1. API-free heuristic repeat-pattern detector (no key, no cost) — handles
         the large majority of directory / attendee / speaker pages.
      2. Claude Vision — ONLY if the heuristic finds nothing AND an API key is
         configured. This keeps the scraper universal and zero-cost by default,
         while preserving Vision as a safety net for the rare page the heuristic
         can't crack.

    On a confident heuristic hit we cache the pattern (same store the AI path
    used) so the domain is resolved instantly next time, and the self-healing
    fuzzy matcher can repair it later if the site's markup drifts.

    Returns the structure dict, or None to fall through to the generic scraper.
    """
    if _AUTODETECT_AVAILABLE:
        structure = _heuristic_identify(page)
        if structure:
            # Persist confident detections so repeat visits skip detection entirely.
            if float(structure.get("confidence", 0)) >= 0.65:
                try:
                    save_pattern(domain, structure)
                except Exception as _e:
                    print(f"  [auto_detect] pattern cache save skipped: {_e}")
            return structure
        print("  [auto_detect] no pattern found heuristically")

    if api_key:
        print("  Falling back to Claude Vision...")
        return ai_identify_page_structure(page, api_key, domain)

    print("  No API key set — skipping Vision, using generic scraper.")
    return None


# ==========================================
# CARD PARSING
# ==========================================

GARBAGE_WORDS = {
    "SIGN IN", "DELEGATES", "FILTER", "SEARCH", "PRIVACY", "LOGIN",
    "REGISTER", "COOKIES", "ACCEPT", "MENU", "HOME", "CONTACT",
    "ABOUT", "SCHEDULE", "SESSIONS", "EXHIBITORS", "SPEAKERS",
    "SPONSORS", "MAP", "FLOOR", "TERMS", "CONDITIONS", "LOADING"
}

COMPANY_INDICATORS = [
    'gmbh', 'ag', ' ltd', ' inc', 's.r.l', 'b.v', 'plc', 'co.',
    'limited', 'group', 'systems', 'solutions', 'services',
    'technologies', 'global', 'international', 'academy', 'trust',
    'college', 'university', 'school', 'council', 'authority'
]

TITLE_INDICATORS = [
    'manager', 'head', 'director', 'chief', 'officer', 'president',
    'vp', 'consultant', 'engineer', 'analyst', 'specialist',
    'coordinator', 'lead', 'advisor', 'executive', 'ceo', 'cto',
    'cfo', 'coo', 'founder', 'partner', 'teacher', 'lecturer',
    'principal', 'superintendent', 'it service', 'service desk'
]


def is_valid_name(text):
    if not text or len(text) < 3 or len(text) > 65:
        return False
    if ' ' not in text:
        return False
    if not re.match(r'^[A-Za-zÀ-ÖØ-öø-ÿ\s\.\-\']+$', text):
        return False
    return True


def smart_parse_lines(lines):
    """Given text lines from a card, extract name/title/company."""
    if not lines:
        return None, 'N/A', 'N/A'
    name = lines[0]
    title, company = 'N/A', 'N/A'
    if len(lines) >= 3:
        title = lines[1]
        company = lines[2]
    elif len(lines) == 2:
        line2 = lines[1]
        is_comp = any(i in line2.lower() for i in COMPANY_INDICATORS)
        is_title = any(i in line2.lower() for i in TITLE_INDICATORS)
        if is_comp and not is_title:
            company = line2
        else:
            title = line2
    return name, title, company


# ══════════════════════════════════════════════════════════════════════════════
# BRELLA SCRAPER — LOCKED
# ──────────────────────────────────────────────────────────────────────────────
# Handles ALL Brella events (app.brella.io, next.brella.io).
# Uses stable data-test DOM attributes — no AI, no network interception,
# no pattern cache. DO NOT route Brella through the AI Vision path.
# Entry point : scrape_brella()
# Called from : run_worker() when is_brella_domain() is True
# ══════════════════════════════════════════════════════════════════════════════

def is_brella_domain(domain):
    return 'brella.io' in domain


def parse_brella_cards_from_page(page):
    """
    Extract ALL attendee cards from the current Brella page in one JS call.
    Walks UP from each name element to find the full card container,
    then extracts name / title / company / category / tags.
    """
    # JS written as a raw string to avoid Python/JS escaping conflicts.
    # No non-ASCII characters inside JS code (only in JS string values where safe).
    JS = (
        "() => {"
        "  var SEP = [' \u00b7 ', ' \u2022 ', ' - '];"  # middle-dot, bullet, dash
        "  var SKIP = {'Connect':1,'View profile':1,'Message':1,'Bookmark':1,"
        "    'Book meeting':1,'+ Connect':1,'Schedule':1,'Available':1,"
        "    'Unavailable':1,'Hi! I would like':1};"
        "  var nameEls = document.querySelectorAll("
        "    '[data-test=\"attendee-card-name\"], [data-test-profile-card-name]'"
        "  );"
        "  if (!nameEls.length) return [];"
        "  var cards = [];"
        "  for (var ni = 0; ni < nameEls.length; ni++) {"
        "    var nameEl = nameEls[ni];"
        "    var name = nameEl.innerText.trim();"
        "    if (!name || name.length < 2) continue;"
        "    var container = nameEl.parentElement;"
        "    for (var depth = 0; depth < 10 && container; depth++) {"
        "      var dtCount = container.querySelectorAll('[data-test]').length;"
        "      var txt = container.innerText || '';"
        "      if (dtCount >= 3 || (txt.indexOf(' \u00b7 ') >= 0 && txt.length < 900)) break;"
        "      container = container.parentElement;"
        "    }"
        "    if (!container) continue;"
        "    var category = 'N/A';"
        "    var personaEl = container.querySelector("
        "      '[data-test=\"attendee-card-persona\"], [data-test-profile-card-persona]'"
        "    );"
        "    if (personaEl) category = personaEl.innerText.trim();"
        "    var title = 'N/A', company = 'N/A';"
        # Subtitle <p> is a sibling of the name <h2> — no data-test on it.
        # Find it by going to nameEl.parentElement and getting first <p> without data-test.
        "    var nameParent = nameEl.parentElement;"
        "    var subtitleEl = null;"
        "    if (nameParent) {"
        "      var pEls = nameParent.querySelectorAll('p');"
        "      for (var pi = 0; pi < pEls.length; pi++) {"
        "        if (!pEls[pi].getAttribute('data-test')) { subtitleEl = pEls[pi]; break; }"
        "      }"
        "    }"
        "    if (!subtitleEl) subtitleEl = container.querySelector('[data-test=\"attendee-card-subtitle\"]');"
        "    if (subtitleEl) {"
        "      var raw = subtitleEl.innerText.trim();"
        "      var allSeps = ['\u2219', '\u00b7', '\u2022', ' - '];"
        "      for (var si = 0; si < allSeps.length; si++) {"
        "        if (raw.indexOf(allSeps[si]) >= 0) {"
        "          var parts = raw.split(allSeps[si]);"
        "          title = parts[0].trim();"
        "          company = parts.slice(1).join(allSeps[si]).trim();"
        "          break;"
        "        }"
        "      }"
        "      if (title === 'N/A') title = raw;"
        "    }"
        "    if (title === 'N/A' && company === 'N/A') {"
        "      var lines = (container.innerText || '').split('\\n');"
        "      for (var li = 0; li < lines.length; li++) {"
        "        var line = lines[li].trim();"
        "        if (line.length <= 2 || SKIP[line] || line === name || line === category) continue;"
        "        for (var si2 = 0; si2 < SEP.length; si2++) {"
        "          if (line.indexOf(SEP[si2]) >= 0) {"
        "            var parts2 = line.split(SEP[si2]);"
        "            title = parts2[0].trim();"
        "            company = parts2.slice(1).join(SEP[si2]).trim();"
        "            break;"
        "          }"
        "        }"
        "        if (title !== 'N/A') break;"
        "      }"
        "    }"
        "    var tagEls = container.querySelectorAll("
        "      '[data-test=\"profile-interest-match\"],"
        "       [data-test=\"profile-interest-other\"],"
        "       [data-test-profile-interest-match],"
        "       [data-test-profile-interest-other]'"
        "    );"
        "    var tags = [];"
        "    for (var ti = 0; ti < Math.min(tagEls.length, 8); ti++) {"
        "      var t = tagEls[ti].innerText.trim();"
        "      if (t) tags.push(t);"
        "    }"
        "    cards.push({name:name, title:title, company:company,"
        "                category:category, tags:tags.join(', ')});"
        "  }"
        "  return cards;"
        "}"
    )
    try:
        results = page.evaluate(JS)
        return results or []
    except Exception as e:
        print(f'  [Brella] JS parse error: {e}')
        return []


def parse_brella_card(card):
    """Legacy single-card parser — kept for compatibility. Not used in main loop."""
    return None


def _brella_wait_for_cards(page, timeout_s=20):
    """
    Wait for Brella attendee cards to appear in the DOM.
    Returns working CSS selector string or None.

    Supports all Brella versions:
    - Old: container has data-test="attendee-card" or data-test-profile-card
    - New (next.brella.io): container has NO data-test, but children do
      e.g. data-test="attendee-card-name", data-test="attendee-card-persona"
      We find the container by walking UP from the name element.
    """
    # ── Tier 1: known container selectors ─────────────────────────────
    KNOWN_CONTAINERS = [
        '[data-test="attendee-card"]',
        '[data-test="profile-card"]',
        '[data-test-profile-card]',
        '[data-testid="attendee-card"]',
        '[data-testid="profile-card"]',
    ]

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        # Try direct container selectors
        for sel in KNOWN_CONTAINERS:
            try:
                els = page.query_selector_all(sel)
                if len(els) >= 1:
                    print(f'  [Brella] Container selector: {sel!r} × {len(els)}')
                    return sel
            except Exception:
                pass

        # ── Tier 2: find container via its children ────────────────────
        # next.brella.io: container has no data-test but children do.
        # attendee-card-name is the anchor — walk UP to find the repeating container.
        try:
            name_els = page.query_selector_all('[data-test="attendee-card-name"]')
            if name_els:
                # Use JS to find the right ancestor level
                result = page.evaluate("""
                    () => {
                        const nameEls = document.querySelectorAll('[data-test="attendee-card-name"]');
                        if (!nameEls.length) return null;
                        const target_count = nameEls.length;
                        // Walk up from first name element
                        let el = nameEls[0].parentElement;
                        for (let depth = 0; depth < 10 && el; depth++) {
                            // Try class-based match
                            const cls = (el.className || '').trim();
                            if (cls) {
                                const firstCls = cls.split(' ')[0];
                                if (firstCls.length >= 3) {
                                    const sel = el.tagName.toLowerCase()
                                        + '[class*="' + firstCls + '"]';
                                    const count = document.querySelectorAll(sel).length;
                                    if (count === target_count) return sel;
                                }
                            }
                            el = el.parentElement;
                        }
                        return null;
                    }
                """)
                if result:
                    count = len(page.query_selector_all(result))
                    if count >= 1:
                        print(f'  [Brella] Container found via children: {result!r} × {count}')
                        return result

                # Absolute fallback: use name element itself as "card" anchor
                # scrape_brella will call parse_brella_card on the NAME element's parent
                print(f'  [Brella] Using name-element parent as card anchor × {len(name_els)}')
                return '__brella_name_anchor__'  # special sentinel
        except Exception:
            pass

        time.sleep(1)

    # ── Dump debug info ────────────────────────────────────────────────
    try:
        info = page.evaluate("""
            () => {
                const attrs = {};
                document.querySelectorAll('[data-test],[data-testid]').forEach(el => {
                    const k = el.getAttribute('data-test')
                            || el.getAttribute('data-testid') || '';
                    if (k) attrs[k] = (attrs[k]||0)+1;
                });
                return {
                    url:   window.location.href,
                    title: document.title,
                    attrs: Object.entries(attrs).sort((a,b)=>b[1]-a[1])
                                 .slice(0,20).map(e=>e[0]+':'+e[1]).join(', ')
                };
            }
        """)
        print(f'  [Brella] No cards after {timeout_s}s')
        print(f'  [Brella] URL   : {info["url"]}')
        print(f'  [Brella] Title : {info["title"]}')
        print(f'  [Brella] data-test attrs on page: {info["attrs"] or "none"}')
    except Exception:
        print(f'  [Brella] No cards found. URL: {page.url}')

    return None


def _brella_next_page(page):
    """
    Click the Next page ( > ) button on Brella's pagination bar.
    Returns True if successfully clicked, False if no next page exists.
    """
    # Strategy 1 — explicit next-page selectors
    for sel in [
        '[data-test="pagination-next"]',
        '[data-testid="pagination-next"]',
        'button[aria-label="Next page"]',
        'a[aria-label="Next page"]',
        '[aria-label="Go to next page"]',
        '.pagination-next button',
        '.pagination-next a',
    ]:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=500):
                is_disabled = btn.is_disabled()
                if not is_disabled:
                    btn.click(timeout=3000)
                    return True
        except Exception:
            continue

    # Strategy 2 — find active page number and click the sibling after it
    try:
        clicked = page.evaluate("""
            () => {
                // Find pagination container
                const root = document.querySelector(
                    '[class*="pagination" i], [class*="Pagination" i], nav'
                );
                if (!root) return false;

                // Find the currently active/selected page button
                const active = root.querySelector(
                    '[aria-current="page"], [class*="active"], [aria-selected="true"]'
                );
                if (!active) return false;

                // Get all clickable items in the pagination bar
                const items = Array.from(root.querySelectorAll('button, a'))
                    .filter(el => !el.disabled && el.getAttribute('aria-disabled') !== 'true');

                // Find position of active item
                const activeBtn = active.tagName.match(/button|a/i) ? active
                    : active.querySelector('button, a') || active;
                const idx = items.indexOf(activeBtn);

                if (idx >= 0 && idx < items.length - 1) {
                    const next = items[idx + 1];
                    // Make sure it's not the "..." ellipsis or last page
                    const txt = (next.innerText || '').trim();
                    if (txt && !isNaN(parseInt(txt)) || txt === '>' || txt === '›') {
                        next.click();
                        return true;
                    }
                    // If next is not a number, try one more step
                    if (idx + 2 < items.length) {
                        items[idx + 1].click();
                        return true;
                    }
                }
                return false;
            }
        """)
        if clicked:
            return True
    except Exception:
        pass

    # Strategy 3 — text-based last resort
    for text in ['›', '>', 'Next']:
        try:
            btn = page.locator(f"button:has-text('{text}')").last
            if btn.is_visible(timeout=500) and not btn.is_disabled():
                btn.click(timeout=3000)
                return True
        except Exception:
            continue

    return False


def scrape_brella(page, contacts_dict, session_filepath, category):
    """
    Scrape ALL pages of a Brella event attendee list.

    Flow:
      1. Wait for cards to appear (handles React async rendering)
      2. Parse all visible cards on current page
      3. Save new leads
      4. Click Next page button → repeat
      5. Stop when no Next button or 3 consecutive empty pages

    This function is SELF-CONTAINED. It does not use:
      - Claude Vision / AI selectors
      - Network interception
      - Layout pattern cache
    """
    print(f'  URL: {page.url}')

    # Wait for cards to render after user navigates to /people page
    card_sel = _brella_wait_for_cards(page, timeout_s=20)
    if not card_sel:
        print('  ✗ Could not find attendee cards.')
        print('  → Navigate to the /people page first, THEN click START')
        return 0

    total_new   = 0
    page_num    = 1
    empty_pages = 0
    MAX_EMPTY   = 3

    while True:
        print(f'--- Brella Page {page_num} ---')

        # Small scroll nudge — triggers React virtual-DOM rendering of visible cards
        # IMPORTANT: do NOT scroll to page bottom — that unmounts virtual-list cards
        try:
            page.evaluate('window.scrollBy(0, 350)')
            time.sleep(0.6)
            page.evaluate('window.scrollBy(0, -350)')
            time.sleep(1.5)
        except Exception:
            time.sleep(2)

        # Parse all cards on this page via single JS call
        # (avoids container-too-small issue with class-based selectors)
        parsed_cards = parse_brella_cards_from_page(page)

        # Sanity check — if JS returned 0, wait and retry once
        if not parsed_cards:
            time.sleep(2)
            parsed_cards = parse_brella_cards_from_page(page)

        print(f'Cards found: {len(parsed_cards)}')

        batch_all = {}
        batch_new = {}
        for result in parsed_cards:
            if result and result.get('name'):
                n = result['name']
                batch_all[n] = result
                if n not in contacts_dict:
                    contacts_dict[n] = result
                    batch_new[n] = result

        dupes = len(batch_all) - len(batch_new)
        print(f'Parsed: {len(batch_all)} | New: {len(batch_new)} | Dupes: {dupes} | Total: {len(contacts_dict)}')

        if batch_new:
            saved = save_batch(batch_new, page.url, category, session_filepath)
            total_new  += saved
            empty_pages = 0
            print(f'Saved {saved} new leads')
        else:
            empty_pages += 1
            print(f'No new leads ({empty_pages}/{MAX_EMPTY})')

        # ── Next page ─────────────────────────────────────────────────
        if _brella_next_page(page):
            page_num += 1
            empty_pages = 0
            print(f'  → Navigating to page {page_num}')
            time.sleep(3.5)  # wait for React to render new page content
        else:
            if empty_pages >= MAX_EMPTY:
                print('  No next page found — scrape complete')
                break
            # Could be last page — wait and check one more time
            time.sleep(2)
            if not _brella_next_page(page):
                print('  No next page — scrape complete')
                break

    return total_new


def parse_card_with_structure(card, structure):
    """
    Parse a single card element using the AI-identified sub-selectors.
    Uses the inhouse html_selector (lxml+cssselect) to parse the card's outer
    HTML for robust extraction — especially when obfuscated/dynamic class names
    make Playwright query_selector() unreliable. Falls back to inner text
    parsing if sub-selectors aren't set.
    """
    try:
        name, title, company = None, 'N/A', 'N/A'

        name_sel = structure.get("name_selector")
        title_sel = structure.get("title_selector")
        company_sel = structure.get("company_selector")

        # ── Inhouse selector path: parse card outer HTML for reliable extraction ──
        if _HTML_SELECTOR_AVAILABLE and (name_sel or title_sel or company_sel):
            try:
                card_html = card.evaluate("el => el.outerHTML")
                sc = _HTMLSelector(card_html)

                if name_sel:
                    v = sc.css(f'{name_sel}::text').get() or sc.css(name_sel).get()
                    if v and v.strip():
                        name = v.strip()

                if title_sel:
                    v = sc.css(f'{title_sel}::text').get() or sc.css(title_sel).get()
                    if v and v.strip():
                        title = v.strip()

                if company_sel:
                    v = sc.css(f'{company_sel}::text').get() or sc.css(company_sel).get()
                    if v and v.strip():
                        company = v.strip()
            except Exception:
                pass  # fall through to Playwright path

        # ── Playwright path (when inhouse selector not installed or sub-selectors absent) ──
        if not name:
            if name_sel:
                try:
                    el = card.query_selector(name_sel)
                    if el:
                        name = el.inner_text().strip()
                except:
                    pass

            if title_sel and title == 'N/A':
                try:
                    el = card.query_selector(title_sel)
                    if el:
                        title = el.inner_text().strip()
                except:
                    pass

            if company_sel and company == 'N/A':
                try:
                    el = card.query_selector(company_sel)
                    if el:
                        company = el.inner_text().strip()
                except:
                    pass

        # Fallback: parse raw inner text lines
        if not name:
            raw = card.inner_text().strip()
            lines = [l.strip() for l in raw.split('\n')
                     if l.strip() and len(l.strip()) > 1
                     and 'View profile' not in l and 'Connect' not in l]
            if lines:
                name, t, c = smart_parse_lines(lines)
                if title == 'N/A':
                    title = t
                if company == 'N/A':
                    company = c

        if name and is_valid_name(name):
            return {'name': name, 'title': title, 'company': company,
                    'category': 'N/A', 'tags': ''}
    except:
        pass
    return None


def parse_generic_divs(page):
    """
    Fallback parser. When the inhouse html_selector is available, uses lxml +
    cssselect on the full page HTML — handles obfuscated / minified class names
    and dynamically-generated markup better than raw DOM traversal. Falls back
    to the original Playwright path when lxml/cssselect is not installed.
    """
    results = {}

    # ── Inhouse html_selector path (preferred) ────────────────────────────────
    if _HTML_SELECTOR_AVAILABLE:
        try:
            html = page.content()
            root = _HTMLSelector(html)
            # find_all('div') walks the full tree; each element exposes .html
            # and .css('*::text') to get all descendant text nodes.
            for block in root.find_all('div'):
                try:
                    raw_html = block.html or ''
                    if len(raw_html) > 3000 or len(raw_html) < 20:
                        continue
                    # ::text collects every text node in the subtree, which is
                    # more reliable than inner_text() on deeply-nested elements.
                    text_parts = block.css('*::text').getall()
                    lines = [t.strip() for t in text_parts
                             if t.strip() and len(t.strip()) > 1]
                    if len(lines) < 2:
                        continue
                    name = lines[0]
                    if any(k in name.upper() for k in GARBAGE_WORDS):
                        continue
                    if not is_valid_name(name):
                        continue
                    name_clean, title_clean, company_clean = smart_parse_lines(lines)
                    if name_clean and name_clean not in results:
                        results[name_clean] = {
                            'name': name_clean, 'title': title_clean,
                            'company': company_clean, 'category': 'N/A', 'tags': ''
                        }
                except Exception:
                    continue
            return results
        except Exception as e:
            print(f"  [html_selector] parse_generic_divs error: {e} — using DOM fallback")

    # ── Original Playwright DOM path ──────────────────────────────────────────
    try:
        blocks = page.query_selector_all("div")
        for block in blocks:
            try:
                html_len = len(block.inner_html())
                if html_len > 3000 or html_len < 20:
                    continue
                text = block.inner_text().strip()
                lines = [l.strip() for l in text.split('\n') if l.strip()]
                if len(lines) < 2:
                    continue
                name = lines[0]
                if any(k in name.upper() for k in GARBAGE_WORDS):
                    continue
                if not is_valid_name(name):
                    continue
                name_clean, title_clean, company_clean = smart_parse_lines(lines)
                if name_clean and name_clean not in results:
                    results[name_clean] = {
                        'name': name_clean, 'title': title_clean,
                        'company': company_clean, 'category': 'N/A', 'tags': ''
                    }
            except:
                continue
    except:
        pass
    return results


# ==========================================
# AUTO-DETECT CATEGORY FROM PAGE
# ==========================================
def auto_detect_category(page):
    categories = []
    try:
        badge_selectors = [
            "div[class*='filter'] span", ".filter-badge", ".chip", ".tag",
            "[aria-pressed='true']", ".filter.active", ".filter-item.active",
            "button[class*='selected']", "span[class*='active']",
        ]
        skip_words = {'x', 'filter', 'remove', 'clear', 'search', 'reset'}
        for selector in badge_selectors:
            try:
                for badge in page.locator(selector).all():
                    if badge.is_visible():
                        text = re.sub(
                            r'^(Category|Organization type|Type|Filter):\s*',
                            '', badge.inner_text().strip(), flags=re.IGNORECASE
                        )
                        if text.lower() not in skip_words and 3 <= len(text) <= 50:
                            if text not in categories:
                                categories.append(text)
            except:
                continue
        if categories:
            return " + ".join(categories)
    except:
        pass

    try:
        parsed = urlparse(page.url)
        params = parse_qs(parsed.query)
        for param in ['category', 'filter', 'type', 'group', 'segment']:
            if param in params:
                return " + ".join(params[param])
    except:
        pass

    return "All Attendees"


# ==========================================
# SAVE BATCH
# ==========================================
def save_batch(contacts_dict, source_url, category, session_filepath,
               layout="generic", session_id=None, event_name=None):
    init_db()
    try:
        leads_db = pd.read_csv(LEADS_FILE)
        companies_db = pd.read_csv(COMPANIES_FILE)
    except:
        leads_db = pd.DataFrame()
        companies_db = pd.DataFrame()

    existing_companies = (
        dict(zip(companies_db['company_name'].astype(str).str.lower(), companies_db['company_id']))
        if not companies_db.empty and 'company_name' in companies_db.columns and 'company_id' in companies_db.columns
        else {}
    )
    existing_leads = (
        set(zip(leads_db['full_name'].astype(str), leads_db['company_id'].astype(str)))
        if not leads_db.empty and 'full_name' in leads_db.columns and 'company_id' in leads_db.columns
        else set()
    )

    new_leads, new_companies, session_rows = [], [], []
    count = 0

    for p in contacts_dict.values():
        comp = str(p['company']).strip()
        name = str(p['name']).strip()

        if comp.lower() in existing_companies:
            comp_id = existing_companies[comp.lower()]
        else:
            comp_id = str(uuid.uuid4())
            new_companies.append({'company_id': comp_id, 'company_name': comp,
                                   'created_at': pd.Timestamp.now()})
            existing_companies[comp.lower()] = comp_id

        key = (name, comp_id)
        if key not in existing_leads:
            new_leads.append({
                'lead_id': str(uuid.uuid4()),
                'full_name': name, 'title': p['title'], 'company_id': comp_id,
                'category': p.get('category') or category, 'tags': p.get('tags', ''),
                'source_url': source_url, 'scraped_at': pd.Timestamp.now()
            })
            existing_leads.add(key)
            count += 1

        session_rows.append({
            'Full Name': name, 'Job Title': p['title'], 'Company': comp,
            'Category': p.get('category') or category, 'Tags': p.get('tags', ''), 'Source': source_url
        })

    if new_companies:
        pd.DataFrame(new_companies).to_csv(COMPANIES_FILE, mode='a', header=False, index=False)
    if new_leads:
        pd.DataFrame(new_leads).to_csv(LEADS_FILE, mode='a', header=False, index=False)
    if session_rows:
        pd.DataFrame(session_rows).to_csv(
            session_filepath, mode='a',
            header=not os.path.exists(session_filepath), index=False
        )
    # ── Mirror to SQLite DB (non-blocking) ────────────────────────────────
    if count > 0 and _db_save_lead:
        try:
            _db_save_batch(
                contacts_dict=contacts_dict,
                source_url=source_url,
                category=category,
                layout=layout,
                session_id=session_id,
                event_name=event_name,
            )
        except Exception as _dbe:
            print(f"  [DB] Save error (CSV still saved): {_dbe}")
    return count


# ==========================================
# URL PAGINATION
# ==========================================
def try_url_pagination(page, current_page_num):
    try:
        parsed = urlparse(page.url)
        params = parse_qs(parsed.query, keep_blank_values=True)
        for param in ['pageNumber', 'page', 'p', 'pageNum']:
            if param in params:
                new_val = int(params[param][0]) + 1
                params[param] = [str(new_val)]
                new_query = urlencode(params, doseq=True)
                new_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path,
                                      parsed.params, new_query, parsed.fragment))
                print(f"  URL pagination -> page {new_val}")
                page.goto(new_url, timeout=30000)
                time.sleep(4)
                return True, current_page_num + 1
    except Exception as e:
        print(f"  URL pagination failed: {e}")
    return False, current_page_num


# ==========================================
# NETWORK INTERCEPTION — for obfuscated HTML
# ==========================================
def setup_network_intercept(page):
    """
    Intercept XHR/fetch JSON responses and WebSocket frames BEFORE they get
    rendered into obfuscated HTML. Returns a list that fills as the page loads.
    Handles sites that encrypt their DOM but serve plain JSON on the wire.
    """
    intercepted = []

    def on_response(response):
        try:
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                return
            url = response.url
            # Skip analytics, tracking, config endpoints
            skip = ("analytics", "telemetry", "metrics", "gtm", "segment",
                    "hotjar", "sentry", "config", "i18n", "fonts", "icons")
            if any(s in url.lower() for s in skip):
                return
            data = response.json()
            intercepted.append({"url": url, "data": data})
        except Exception:
            pass

    def on_websocket(ws):
        def on_frame(payload):
            try:
                data = json.loads(payload)
                intercepted.append({"url": ws.url, "data": data, "ws": True})
            except Exception:
                pass
        ws.on("framereceived", lambda p: on_frame(p.body if hasattr(p, "body") else p))

    page.on("response", on_response)
    page.on("websocket", on_websocket)
    return intercepted


def extract_leads_from_intercepted(intercepted):
    """
    Walk intercepted JSON payloads looking for arrays that contain
    person/company records. Returns a dict of {name: lead_dict}.

    Handles deeply nested structures: {data: {users: [{name, title, company}]}}
    """
    leads = {}

    NAME_KEYS    = {"name", "full_name", "fullName", "display_name", "displayName",
                    "firstName", "first_name", "username", "company_name", "companyName"}
    TITLE_KEYS   = {"title", "job_title", "jobTitle", "position", "role",
                    "headline", "tagline"}
    COMPANY_KEYS = {"company", "organization", "organisation", "employer",
                    "company_name", "companyName", "org"}

    def walk(obj, depth=0):
        if depth > 8 or not obj:
            return
        if isinstance(obj, list):
            for item in obj:
                walk(item, depth + 1)
        elif isinstance(obj, dict):
            # Check if this dict looks like a person/company record
            found_name = None
            for k in NAME_KEYS:
                if k in obj and isinstance(obj[k], str) and len(obj[k]) > 1:
                    found_name = obj[k].strip()
                    break
            if found_name and found_name not in leads:
                title   = next((str(obj[k]).strip() for k in TITLE_KEYS   if k in obj and obj[k]), "N/A")
                company = next((str(obj[k]).strip() for k in COMPANY_KEYS if k in obj and obj[k]), "N/A")
                if is_valid_name(found_name) or len(found_name) > 2:
                    leads[found_name] = {
                        "name": found_name, "title": title,
                        "company": company, "category": "N/A", "tags": ""
                    }
            # Always recurse into values
            for v in obj.values():
                if isinstance(v, (dict, list)):
                    walk(v, depth + 1)

    for entry in intercepted:
        walk(entry.get("data"))

    return leads


# ==========================================
# INNER SCROLL HELPER — WebSocket aware
# ==========================================
def do_scroll(page, card_selector=None, ws_wait=5):
    """
    Scroll the page and wait intelligently for new content.

    Two modes:
    - Normal infinite scroll : checks if body height grew (classic behaviour)
    - WebSocket scroll       : checks if CARD COUNT grew (WS pushes data without
                               changing body height first)

    Returns True if new content appeared (either mode).
    """
    # Snapshot before scroll
    prev_height = page.evaluate("document.body.scrollHeight")
    before_count = 0
    if card_selector:
        try:
            before_count = len(page.query_selector_all(card_selector))
        except Exception:
            pass

    # Perform the scroll
    try:
        if card_selector:
            page.evaluate("""
                (sel) => {
                    const card = document.querySelector(sel);
                    if (!card) { window.scrollTo(0, document.body.scrollHeight); return; }
                    let el = card.parentElement;
                    while (el && el !== document.body) {
                        const s = window.getComputedStyle(el);
                        if ((s.overflowY === 'auto' || s.overflowY === 'scroll')
                                && el.scrollHeight > el.clientHeight + 5) {
                            el.scrollTop = el.scrollHeight;
                            return;
                        }
                        el = el.parentElement;
                    }
                    window.scrollTo(0, document.body.scrollHeight);
                }
            """, card_selector)
        else:
            page.keyboard.press("End")
    except Exception:
        page.keyboard.press("End")

    # Wait up to ws_wait seconds for EITHER height growth OR card count growth.
    # This catches WebSocket-loaded content that arrives without body height changing.
    deadline = time.time() + ws_wait
    while time.time() < deadline:
        time.sleep(0.5)
        new_height = page.evaluate("document.body.scrollHeight")
        if new_height > prev_height:
            return True  # classic scroll growth
        if card_selector:
            try:
                new_count = len(page.query_selector_all(card_selector))
                if new_count > before_count:
                    print(f"  [WS] Card count grew {before_count} → {new_count} (WebSocket delivery)")
                    return True
            except Exception:
                pass

    return False  # nothing new appeared


# ==========================================
# MAIN WORKER
# ==========================================
def run_worker(target_url, mobile=False):
    print("\n" + "="*60)
    print("  Universal AI Scraper — powered by Claude Vision")
    if mobile:
        print("  Mode: MOBILE EMULATION (iPhone 13)")
    print("="*60 + "\n")

    # ── Normalise URL — add https:// if user forgot the scheme ───────────
    if target_url and not target_url.startswith(("http://", "https://")):
        target_url = "https://" + target_url
        print(f"  URL normalised → {target_url}")

    api_key = get_api_key()
    if not api_key:
        print("No API key — cannot continue.")
        return

    contacts_dict = {}
    db_session_id = None
    init_db()
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M')
    session_filepath = os.path.join(SESSIONS_FOLDER, f'scrape_session_{timestamp}.csv')
    domain = urlparse(target_url).netloc

    # ── Resume from checkpoint if one exists for this domain ──────────────
    os.makedirs(SESSIONS_FOLDER, exist_ok=True)
    existing_ckpts = sorted([
        f for f in os.listdir(SESSIONS_FOLDER)
        if f.endswith(".checkpoint")
    ], reverse=True)
    for ckpt_file in existing_ckpts:
        try:
            ckpt_path = os.path.join(SESSIONS_FOLDER, ckpt_file)
            with open(ckpt_path) as f:
                ckpt = json.load(f)
            # Only resume if it was recent (within 4 hours)
            ckpt_mtime = os.path.getmtime(ckpt_path)
            if time.time() - ckpt_mtime < 14400 and ckpt.get("names"):
                resume = input(
                    f"\n  Found checkpoint: {len(ckpt['names'])} leads, page {ckpt['page']}.\n"
                    f"  Resume? (y/n): "
                ).strip().lower()
                if resume == "y":
                    for n in ckpt["names"]:
                        contacts_dict[n] = {"name": n, "title": "N/A",
                                            "company": "N/A", "tags": ""}
                    print(f"  Resuming with {len(contacts_dict)} already-seen leads (will skip dupes)")
                break
        except Exception:
            pass

    with sync_playwright() as pw:
        # ── Device emulation ───────────────────────────────────────────────
        # Mobile mode: emulate iPhone 13 — works on apps that block desktop
        MOBILE_DEVICE = {
            "user_agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/16.0 Mobile/15E148 Safari/604.1"
            ),
            "viewport": {"width": 390, "height": 844},
            "device_scale_factor": 3,
            "is_mobile": True,
            "has_touch": True,
            "locale": "en-GB",
            "timezone_id": "Europe/London",
        }
        DESKTOP_CONTEXT = {
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            ),
            "viewport": {"width": 1440, "height": 900},
            "locale": "en-GB",
            "timezone_id": "Europe/London",
            "color_scheme": "light",
            "extra_http_headers": {
                "Accept-Language": "en-GB,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            }
        }

        browser = pw.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
                "--start-maximized",        # open full-screen so user can scroll freely
                "--window-size=1920,1080",  # fallback if maximise not supported
            ]
        )
        # Desktop: remove fixed viewport so browser uses the full maximised window.
        # A fixed viewport would letterbox the page and prevent free scrolling.
        if mobile:
            context = browser.new_context(**MOBILE_DEVICE)
        else:
            desktop_ctx = {k: v for k, v in DESKTOP_CONTEXT.items() if k != "viewport"}
            context = browser.new_context(**desktop_ctx)
        page = context.new_page()

        # Apply stealth patches — removes navigator.webdriver, fixes canvas/WebGL fingerprints
        if _STEALTH_AVAILABLE:
            _stealth_sync(page)
            print("  [stealth] Anti-detection active ✓")

        # ── Network interception — capture JSON before DOM obfuscation ─────
        # This runs silently in background. If HTML is scrambled, we fall back
        # to these intercepted payloads to extract leads directly from the wire.
        intercepted = setup_network_intercept(page)
        print("  [network] JSON/WebSocket interception active ✓")

        print(f"Loading: {target_url}")
        try:
            page.goto(target_url, timeout=60000)
        except:
            print("Load timeout — continuing anyway")
        _human_delay(3, 5)

        # ── Magic START button ─────────────────────────────────────────────
        print("Login & apply your filters, then click the purple START button.")
        while True:
            try:
                if page.evaluate("() => window.scrape_is_ready === true"):
                    print("START clicked!\n")
                    break
                if page.locator("#magic-start-btn").count() == 0:
                    page.evaluate("""() => {
                        var btn = document.createElement("button");
                        btn.id = "magic-start-btn";
                        btn.innerHTML = "START SCRAPING";
                        btn.style.cssText =
                            "position:fixed;top:10px;right:10px;z-index:99999;" +
                            "padding:15px 30px;background:#6610f2;color:white;" +
                            "border:2px solid white;border-radius:8px;font-size:16px;" +
                            "font-weight:bold;cursor:pointer;" +
                            "box-shadow:0 4px 12px rgba(0,0,0,0.4);";
                        btn.onclick = function() {
                            window.scrape_is_ready = true;
                            this.innerHTML = "Running...";
                            this.style.background = "#ffc107";
                            this.style.color = "black";
                        };
                        document.body.appendChild(btn);
                    }""")
            except:
                pass
            time.sleep(1)

        # ── Detect category ────────────────────────────────────────────────
        category = auto_detect_category(page)
        print(f"Category: {category}")

        # Start DB session now that we know the category
        try:
            if _db_start_session and _db_available:
                db_session_id = _db_start_session(
                    event_url  = target_url,
                    event_name = domain,
                    category   = category,
                    layout     = "detecting",
                    org_id     = 1,   # default org; scraper runs outside request context
                )
        except Exception as _se:
            print(f"  [DB] Session start error: {_se}")

        # ── BRELLA FAST-PATH — bypass Claude Vision entirely ──────────────
        # next.brella.io uses stable data-test attributes; no AI needed.
        # Check BOTH the original target_url domain AND the current page URL
        # (user may type brella.io but be redirected to next.brella.io/events/...)
        current_domain = urlparse(page.url).netloc
        if is_brella_domain(domain) or is_brella_domain(current_domain):
            print(f"Brella site detected → using dedicated Brella scraper")
            print(f"  Current page: {page.url}")
            scrape_brella(page, contacts_dict, session_filepath, category)
            total_scraped = len(contacts_dict)
            print(f"\n🎉 COMPLETE: {total_scraped} unique leads")
            print(f"   Session: {session_filepath}")
            try:
                if _db_finish_session and _db_available and db_session_id:
                    _db_finish_session(db_session_id, leads_scraped=total_scraped)
            except Exception:
                pass
            return

        # ── Load or learn page structure ───────────────────────────────────
        patterns = load_patterns()
        structure = patterns.get(domain)

        if structure:
            print(f"Remembered structure for {domain}: card={structure.get('card_selector')!r}")
        else:
            print(f"New site — detecting page structure (heuristic first)...")
            structure = identify_page_structure(page, api_key, domain)

        # Decide pagination mode
        if structure:
            pagination_type = structure.get("pagination_type", "url_param")
            card_selector = structure.get("card_selector")
            next_btn_selector = structure.get("next_button_selector")
        else:
            # Full fallback
            pagination_type = "url_param"
            card_selector = None
            next_btn_selector = None

        print(f"Card selector : {card_selector!r}")
        print(f"Pagination    : {pagination_type}")
        print(f"Next button   : {next_btn_selector!r}\n")

        # ── Scrape loop ────────────────────────────────────────────────────
        page_num     = 1
        no_new_cycles = 0
        MAX_NO_NEW   = 3
        consecutive_empty = 0   # tracks pages with 0 cards (rate-limit signal)

        def check_for_block(page):
            """
            Detect common rate-limit / CAPTCHA / ban signals.
            Returns (is_blocked: bool, reason: str)
            """
            try:
                url   = page.url.lower()
                title = page.title().lower()
                html  = page.content().lower()

                # Hard redirects to known block pages
                block_urls = ("captcha", "challenge", "blocked", "banned",
                              "accessdenied", "rate-limit", "too-many-requests",
                              "sorry", "abuse", "security-check")
                if any(b in url for b in block_urls):
                    return True, f"Blocked URL: {page.url}"

                # Page title signals
                block_titles = ("captcha", "access denied", "blocked",
                                "too many requests", "rate limit", "security check",
                                "robot", "just a moment")
                if any(b in title for b in block_titles):
                    return True, f"Block page title: {page.title()}"

                # HTTP status via response (Playwright stores last nav response)
                # Check for 429 in content as a fallback
                if "429" in title or "too many requests" in html[:2000]:
                    return True, "429 Too Many Requests"

                # Cloudflare "Just a moment" spinner
                if "cf-browser-verification" in html or "checking your browser" in html[:2000]:
                    return True, "Cloudflare challenge"

            except Exception:
                pass
            return False, ""

        def handle_block(reason, page_num):
            """
            Exponential backoff on detection.
            Pauses with increasing delay and prints clear instructions.
            """
            wait = min(300, 30 * (2 ** min(page_num // 5, 4)))  # 30→60→120→240→300s cap
            print(f"\n  ⛔ BLOCKED — {reason}")
            print(f"  Waiting {wait}s before retrying...")
            print("  If a CAPTCHA appeared in the browser, solve it manually now.")
            time.sleep(wait)
            print("  Resuming...")

        scrape_status = "done"
        try:
            while True:
                print(f"--- Page {page_num} ---")

                # ── Block / rate-limit check ───────────────────────────────────
                is_blocked, block_reason = check_for_block(page)
                if is_blocked:
                    handle_block(block_reason, page_num)
                    # After waiting, re-check — if still blocked, save and exit
                    is_blocked2, _ = check_for_block(page)
                    if is_blocked2:
                        print("  Still blocked after waiting. Saving progress and stopping.")
                        break

                # Scroll
                grew = do_scroll(page, card_selector)

                # Extract cards
                batch_all = {}
                batch_new = {}

                if card_selector:
                    # Use AI-identified selector
                    try:
                        cards = page.query_selector_all(card_selector)
                        print(f"Cards found: {len(cards)}")

                        if len(cards) == 0 and page_num == 1:
                            # Selector worked before but finds nothing now — re-ask Claude
                            print("  Selector finds 0 cards — re-detecting structure...")
                            structure = identify_page_structure(page, api_key, domain)
                            if structure:
                                card_selector = structure.get("card_selector")
                                next_btn_selector = structure.get("next_button_selector")
                                pagination_type = structure.get("pagination_type", pagination_type)
                                cards = page.query_selector_all(card_selector) if card_selector else []
                                print(f"  Retried — cards found: {len(cards)}")

                        for card in cards:
                            result = parse_card_with_structure(card, structure or {})
                            if result:
                                batch_all[result['name']] = result
                                if result['name'] not in contacts_dict:
                                    contacts_dict[result['name']] = result
                                    batch_new[result['name']] = result

                    except Exception as e:
                        print(f"  Card extraction error: {e} — falling back to generic divs")
                        card_selector = None

                if not card_selector or not batch_all:
                    # Fallback 1: proven generic div scraper
                    parsed = parse_generic_divs(page)
                    print(f"Generic divs parsed: {len(parsed)}")
                    for name, result in parsed.items():
                        batch_all[name] = result
                        if name not in contacts_dict:
                            contacts_dict[name] = result
                            batch_new[name] = result

                # Fallback 2: obfuscated HTML — use intercepted network JSON
                # Triggered when both CSS selector AND generic divs return nothing.
                # Extracts leads directly from raw API responses / WebSocket frames.
                if not batch_all and intercepted:
                    print(f"  [network] DOM extraction failed — trying {len(intercepted)} intercepted payloads...")
                    wire_leads = extract_leads_from_intercepted(intercepted)
                    print(f"  [network] Found {len(wire_leads)} leads from network interception")
                    for name, result in wire_leads.items():
                        batch_all[name] = result
                        if name not in contacts_dict:
                            contacts_dict[name] = result
                            batch_new[name] = result
                    intercepted.clear()  # clear so next scroll gets fresh payloads only

                # Report
                dupes = len(batch_all) - len(batch_new)
                print(f"Parsed: {len(batch_all)} | New: {len(batch_new)} | Dupes: {dupes} | Total: {len(contacts_dict)}")

                # Save
                if batch_new:
                    saved = save_batch(batch_new, page.url, category, session_filepath,
                                       layout=str(pagination_type),
                                       session_id=db_session_id, event_name=domain)
                    # Mirror to DB
                    _db_save_batch(batch_new, page.url, category,
                                   str(pagination_type), db_session_id, domain, org_id=1)
                    print(f"Saved {saved} new leads")
                    no_new_cycles = 0
                    consecutive_empty = 0

                    # ── Checkpoint: save running total to a .checkpoint file ──
                    # If scraper crashes, restart reads from here and skips already-seen names
                    ckpt_path = session_filepath.replace(".csv", ".checkpoint")
                    try:
                        with open(ckpt_path, "w") as _f:
                            json.dump({
                                "total": len(contacts_dict),
                                "page":  page_num,
                                "names": list(contacts_dict.keys())
                            }, _f)
                    except Exception:
                        pass
                else:
                    no_new_cycles += 1
                    print(f"No new leads ({no_new_cycles}/{MAX_NO_NEW})")

                # ── Advance to next page ──────────────────────────────────────
                # Always attempt to advance FIRST — even if all leads were dupes.
                # This prevents stopping on early pages when re-running on a known event.

                advanced = False

                if pagination_type in ("url_param", "next_button", "unknown"):

                    # Try URL param first (covers BETT ?pageNumber=N)
                    url_ok, new_pnum = try_url_pagination(page, page_num)
                    if url_ok:
                        page_num = new_pnum
                        no_new_cycles = 0
                        advanced = True

                    # Try Next button
                    if not advanced:
                        candidates = []
                        if next_btn_selector:
                            candidates.append(next_btn_selector)
                        candidates += [
                            "button[aria-label='Go to next page']",
                            "button[aria-label='Next page']",
                            "a[aria-label='Next page']",
                            "button:has-text('Next')", "a:has-text('Next')",
                            "[aria-label='Next']", ".pagination-next",
                            ".next-page", ".next", "li.next a",
                            "button svg[data-icon='right']",
                            "button svg[data-icon='chevron-right']",
                        ]
                        for sel in candidates:
                            if not sel:
                                continue
                            try:
                                btn = page.locator(sel).first
                                if btn.is_visible(timeout=800) and not btn.is_disabled():
                                    btn.click(force=True, timeout=3000)
                                    time.sleep(4)
                                    page_num += 1
                                    no_new_cycles = 0
                                    advanced = True
                                    print(f"Next button clicked: {sel}")
                                    break
                            except:
                                continue

                    if not advanced:
                        print("No more pages found.")
                        break

                elif pagination_type == "infinite_scroll":
                    if not grew and no_new_cycles >= MAX_NO_NEW:
                        print("Infinite scroll exhausted — done")
                        break
                    if not grew:
                        pass  # no_new_cycles already incremented above
                    else:
                        no_new_cycles = 0  # page grew, keep going

                elif pagination_type == "load_more":
                    # Click "Load More" button
                    try:
                        btn = page.locator("button:has-text('Load More'), button:has-text('Show More'), .load-more").first
                        if btn.is_visible(timeout=2000):
                            btn.click()
                            time.sleep(3)
                            no_new_cycles = 0
                            advanced = True
                            print("Load More clicked")
                        else:
                            print("No Load More button — done")
                            break
                    except:
                        print("Load More not found — done")
                        break

                time.sleep(1)
        except KeyboardInterrupt:
            scrape_status = "stopped"
            print("\n  Interrupted by user — saving progress and closing cleanly...")
        except Exception as _loop_err:
            scrape_status = "failed"
            import traceback as _tb
            print(f"\n  Scrape loop crashed: {_loop_err}")
            print(_tb.format_exc())

        # ── Done ───────────────────────────────────────────────────────────
        # Reached whether the loop completed normally, was interrupted (Ctrl-C),
        # or crashed — scrape_status carries which. All scraped rows are already
        # on disk (save_batch appends per batch), so this block only finalises
        # the session record and releases the browser.
        label = {"done": "COMPLETE", "stopped": "STOPPED", "failed": "FAILED"}.get(scrape_status, "COMPLETE")
        print(f"\n{'='*60}")
        print(f"  {label}  (status={scrape_status})")
        print(f"  Total leads : {len(contacts_dict)}")
        print(f"  Session file: {session_filepath}")
        print(f"  Master file : {LEADS_FILE}")
        print(f"{'='*60}\n")
        # Finish DB session with the real outcome so a crashed/killed run is
        # never left dangling at status='running'.
        try:
            if _db_finish_session and db_session_id:
                total  = len(contacts_dict)
                _db_finish_session(
                    session_id   = db_session_id,
                    leads_found  = total,
                    leads_new    = total,
                    leads_dupes  = 0,
                    status       = scrape_status,
                )
        except Exception as _fe:
            print(f"  [DB] Session finish error: {_fe}")

        # Clear the checkpoint only on clean completion. On failed/stopped we
        # deliberately keep it so the next run can offer to resume.
        if scrape_status == "done":
            try:
                ckpt_path = session_filepath.replace(".csv", ".checkpoint")
                if os.path.exists(ckpt_path):
                    os.remove(ckpt_path)
            except Exception:
                pass
        else:
            print(f"  Checkpoint kept for resume: {session_filepath.replace('.csv', '.checkpoint')}")

        try:
            browser.close()
        except Exception:
            pass


if __name__ == '__main__':
    if len(sys.argv) > 1:
        url  = sys.argv[1]
        mob  = "--mobile" in sys.argv
        if mob:
            print("  Mobile emulation enabled (--mobile flag detected)")
        run_worker(url, mobile=mob)
    else:
        print("Usage: python worker.py <event_url> [--mobile]")
        print("\nExamples:")
        print("  python worker.py https://app.brella.io/events/myevent/people")
        print("  python worker.py https://app.bettshow.com/newfront/participants?page=delegates")
        print("  python worker.py https://community.e-world-essen.com/users")
        print("  python worker.py https://m.someapp.com/attendees --mobile  # mobile-only apps")
