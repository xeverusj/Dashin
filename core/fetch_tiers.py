"""
core/fetch_tiers.py — multi-tier fetch fallback with TLS/JA3 impersonation.

Implements the anti-block fetching strategy specced in the master doc (Module
A4), mirroring Scrapling's Fetcher · StealthyFetcher · PlayWrightFetcher tiering
*in our own code*. Nothing is imported from Scrapling (it is AGPL; importing it
into a product sold to clients would force the whole app open-source). The one
technique that genuinely needs a C library — matching a real browser's TLS
handshake so plain HTTP requests aren't fingerprinted as bots — is provided by
curl_cffi, which is MIT-licensed and safe to ship.

Three tiers, escalating only when a cheaper tier is blocked or returns nothing
useful:

  F1  http_impersonate   curl_cffi with a Chrome TLS/JA3 fingerprint.
                         Fast, cheap, no browser. Handles the majority of
                         company sites. Falls back to plain `requests` if
                         curl_cffi is missing.
  F2  stealth_browser    Headless Chromium via Playwright + webdriver patches.
                         For JS-rendered pages and soft bot-walls.
  F3  headed_browser     Full headed Chromium (visible / Xvfb). Last resort for
                         Cloudflare-style interstitials that demand a real
                         browser and, sometimes, a human.

Primary consumer is the company crawler (Module B). Event scrapers keep their
existing dedicated Playwright paths — do not reroute Tier 1 scrapers through this.

Usage:
    from core.fetch_tiers import fetch

    res = fetch("https://example.com/about")
    if res.ok:
        html = res.html          # rendered/returned HTML
        print(res.tier, res.status)   # which tier won, HTTP status
"""

import time
import random
from dataclasses import dataclass

# curl_cffi is optional. When present we get real browser TLS impersonation at
# the HTTP tier; when absent we degrade to `requests` and lean harder on the
# browser tiers for anything that fingerprint-blocks.
try:
    from curl_cffi import requests as _cffi
    _CFFI = True
except Exception:
    _CFFI = False

import requests as _requests


# ── Block detection ──────────────────────────────────────────────────────────

# HTTP statuses that mean "try a stronger tier", not "give up".
_BLOCK_STATUS = {401, 403, 402, 406, 409, 429, 503, 520, 521, 522, 523, 525, 526}

# Fingerprints of challenge / interstitial pages that return HTTP 200 but carry
# no real content.
_CHALLENGE_MARKERS = (
    "cf-browser-verification", "checking your browser", "just a moment",
    "cf-challenge", "captcha", "attention required", "access denied",
    "enable javascript and cookies", "ddos-guard", "px-captcha",
    "/cdn-cgi/challenge-platform",
)

_MIN_BODY = 80   # bodies shorter than this are "thin" · escalate


def _looks_blocked(status: int, body: str) -> tuple[bool, str]:
    """Return (blocked, reason). Used to decide whether to escalate a tier."""
    if status in _BLOCK_STATUS:
        return True, f"status {status}"
    text = (body or "")
    if len(text.strip()) < _MIN_BODY:
        return True, f"thin body ({len(text.strip())} chars)"
    low = text[:4000].lower()
    for m in _CHALLENGE_MARKERS:
        if m in low:
            return True, f"challenge marker '{m}'"
    return False, ""


# ── Result type ──────────────────────────────────────────────────────────────

@dataclass
class FetchResult:
    url: str
    html: str = ""
    status: int = 0
    tier: str = ""            # which tier produced this result
    ok: bool = False          # got usable content
    reason: str = ""          # if not ok, why (last block reason / error)
    escalations: tuple = ()   # tiers tried before this one, with reasons


_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
}


# ── Tier F1: HTTP with TLS impersonation ─────────────────────────────────────

def _fetch_http(url: str, timeout: int) -> tuple[int, str]:
    """Plain HTTP GET. Uses curl_cffi Chrome impersonation when available."""
    if _CFFI:
        # impersonate replays a real Chrome TLS/JA3 + HTTP2 fingerprint, so
        # servers that block on TLS signature (a very common first line of
        # defence) see an ordinary browser.
        r = _cffi.get(url, headers=_HEADERS, timeout=timeout,
                      impersonate="chrome124", allow_redirects=True)
        return r.status_code, r.text
    r = _requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
    return r.status_code, r.text


# ── Tier F2 / F3: Playwright browser ─────────────────────────────────────────

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
window.chrome = window.chrome || { runtime: {} };
"""


def _fetch_browser(url: str, timeout: int, headed: bool) -> tuple[int, str]:
    """Render the page in Chromium. headed=False for F2, True for F3."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=not headed,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context(user_agent=_UA,
                                  viewport={"width": 1440, "height": 900},
                                  locale="en-US")
        page = ctx.new_page()
        page.add_init_script(_STEALTH_JS)
        status = 0
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            status = resp.status if resp else 0
            # Give client-rendered content a moment, then let any challenge settle.
            page.wait_for_timeout(random.randint(1200, 2500))
            html = page.content()
        finally:
            browser.close()
        return status, html


# ── Public entry point ───────────────────────────────────────────────────────

def fetch(url: str, timeout: int = 15, allow_headed: bool = True,
          verbose: bool = True) -> FetchResult:
    """
    Fetch *url*, escalating through tiers only as needed.

    Starts at the cheapest tier (HTTP + TLS impersonation). If the response
    looks blocked or thin, escalates to a stealth headless browser, then — if
    allow_headed — to a full headed browser. Returns a FetchResult; inspect
    .ok, .html, .tier, .status, and .escalations to see what happened.

    allow_headed=False keeps everything headless (e.g. on a server with no
    display and no Xvfb).
    """
    escalations = []

    tiers = [("F1:http_impersonate", lambda: _fetch_http(url, timeout)),
             ("F2:stealth_browser",  lambda: _fetch_browser(url, timeout, headed=False))]
    if allow_headed:
        tiers.append(("F3:headed_browser", lambda: _fetch_browser(url, timeout, headed=True)))

    last_reason = "no tier succeeded"
    for tier_name, run in tiers:
        try:
            status, html = run()
        except Exception as e:
            last_reason = f"{tier_name} error: {str(e)[:120]}"
            escalations.append((tier_name, last_reason))
            if verbose:
                print(f"  [fetch] {tier_name} raised — {last_reason}")
            continue

        blocked, reason = _looks_blocked(status, html)
        if not blocked:
            if verbose:
                print(f"  [fetch] {tier_name} OK (status {status}, {len(html)} chars)")
            return FetchResult(url=url, html=html, status=status, tier=tier_name,
                               ok=True, escalations=tuple(escalations))

        last_reason = reason
        escalations.append((tier_name, reason))
        if verbose:
            print(f"  [fetch] {tier_name} blocked — {reason} — escalating")
        # small human-ish pause before trying a heavier tier
        time.sleep(random.uniform(0.4, 1.1))

    return FetchResult(url=url, status=0, tier="none", ok=False,
                       reason=last_reason, escalations=tuple(escalations))
