"""
services/enrichment_finder.py — LinkedIn enrichment via web search (Module C).

Finds people's LinkedIn profiles WITHOUT logging into or scraping LinkedIn
itself — no li_at cookie, no LinkedIn bot-wall, no account risk. It searches the
open web for `site:linkedin.com/in ...` and reads the profile URL plus the
title/company out of the search result.

Why a real (headed) browser: both DuckDuckGo and Bing now block silent HTTP
requests and even headless browsers (DDG 418s, Bing shows a CAPTCHA). A visible
browser carries a genuine fingerprint and passes — the same pattern the rest of
Dashin's scrapers use, and it lets a human solve the rare challenge by hand. The
client picked accuracy over speed, so we render properly rather than guess.

Accuracy comes from cross-checking TWO engines: when DuckDuckGo and Bing agree
on the same profile URL, confidence is high; when they differ we validate each
against the name/company and keep the one that holds up.

Two modes:
  C1  enrich_contact(browser, name, company)   — the person is known.
  C2  discover_person(browser, company, roles) — only the company is known; try
                                                 a role priority list over up to
                                                 3 rounds.

Match confidence:
  exact      — both engines agree, OR name+company both validate
  probable   — a profile was found but only partly validated
  not_found  — no usable LinkedIn result
  needs_manual (C2 only) — no role validated after 3 rounds

The pure logic (query building, matching, result parsing, confidence) is
import-clean and unit-testable; only SearchBrowser touches the network.
"""

import re
import time
import random
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

# Politeness pacing between queries (long, randomized) so neither engine sees a burst.
MIN_DELAY = 2.5
MAX_DELAY = 6.0

_COMPANY_SUFFIXES = {
    "inc", "inc.", "llc", "ltd", "ltd.", "limited", "gmbh", "ag", "corp",
    "corporation", "co", "co.", "company", "plc", "bv", "b.v", "sarl", "srl",
    "sa", "s.a", "group", "holding", "holdings", "the", "foundation",
}


# ══════════════════════════════════════════════════════════════════════════════
# Pure logic — query building
# ══════════════════════════════════════════════════════════════════════════════

def build_query_contact(name: str, company: str = "", title: str = "") -> str:
    """
    Plain keyword search for a known person, e.g.
        "Bill Gates Gates Foundation Co-chair linkedin"
    We deliberately do NOT use the `site:linkedin.com/in "..."` operator — that
    form makes Bing/DDG return an AI/assistant answer instead of plain organic
    results. Appending the keyword "linkedin" surfaces the profile as a normal
    result, which we then filter to linkedin.com/in URLs when parsing.
    """
    parts = [p.strip() for p in (name, company, title, "linkedin") if p and p.strip()]
    return " ".join(parts)


def build_query_role(role: str, company: str) -> str:
    """
    Plain keyword search for whoever holds a role at a company, e.g.
        "Gates Foundation current CEO linkedin"
    Same reasoning as build_query_contact — natural keywords, no operators.
    """
    parts = [p.strip() for p in (company, "current", role, "linkedin") if p and p.strip()]
    return " ".join(parts)


# ══════════════════════════════════════════════════════════════════════════════
# Pure logic — matching
# ══════════════════════════════════════════════════════════════════════════════

def normalize_company(name: str) -> str:
    if not name:
        return ""
    s = re.sub(r"[^\w\s]", " ", str(name).lower())
    return " ".join(t for t in s.split() if t and t not in _COMPANY_SUFFIXES)


def company_in_text(target_company: str, text: str) -> bool:
    tgt = normalize_company(target_company)
    hay = normalize_company(text)
    if not tgt or not hay:
        return False
    tgt_tokens = set(tgt.split())
    if not tgt_tokens:
        return False
    overlap = tgt_tokens & set(hay.split())
    return len(overlap) / len(tgt_tokens) >= 0.6


def name_in_text(name: str, text: str) -> bool:
    if not name:
        return False
    toks = [t for t in re.sub(r"[^\w\s]", " ", name.lower()).split() if len(t) > 1]
    return bool(toks) and all(t in text.lower() for t in toks)


def classify_match(name_ok: bool, company_ok: bool, found_profile: bool,
                   engines_agree: bool = False) -> str:
    if not found_profile:
        return "not_found"
    if engines_agree or (company_ok and name_ok):
        return "exact"
    return "probable"


# ══════════════════════════════════════════════════════════════════════════════
# Pure logic — parsing rendered result pages
# ══════════════════════════════════════════════════════════════════════════════

def _clean_linkedin_url(url: str) -> str:
    if not url:
        return ""
    m = re.search(r"https?://[a-z]{0,3}\.?linkedin\.com/in/[^/?#\s]+", url, re.I)
    return m.group(0).rstrip("/") if m else ""


def parse_results(html: str, engine: str) -> list:
    """
    Parse a rendered results page into LinkedIn /in/ results:
    [{linkedin_url, title, snippet}] in result order. Selectors verified live:
      DuckDuckGo · article[data-testid=result], a[data-testid=result-title-a],
                   [data-result=snippet]
      Bing       · li.b_algo, h2 a, .b_caption
    """
    results = []
    if not html:
        return results
    soup = BeautifulSoup(html, "html.parser")

    if engine == "duckduckgo":
        blocks = soup.select("article[data-testid='result'], .react-results--main article")
        for res in blocks:
            a = res.select_one("a[data-testid='result-title-a'], h2 a")
            if not a:
                continue
            url = _clean_linkedin_url(a.get("href", ""))
            if not url:
                continue
            snip = res.select_one("[data-result='snippet'], [data-testid='result-snippet']")
            results.append({"linkedin_url": url,
                            "title": a.get_text(" ", strip=True),
                            "snippet": snip.get_text(" ", strip=True) if snip else ""})
    else:  # bing
        for res in soup.select("li.b_algo"):
            a = res.select_one("h2 a")
            if not a:
                continue
            url = _clean_linkedin_url(a.get("href", ""))
            if not url:
                continue
            snip = res.select_one(".b_caption p") or res.select_one(".b_caption")
            results.append({"linkedin_url": url,
                            "title": a.get_text(" ", strip=True),
                            "snippet": snip.get_text(" ", strip=True) if snip else ""})
    return results


def parse_profile_title(title: str) -> dict:
    """"Name - Title - Company | LinkedIn" (or "... at Company") · parts."""
    out = {"name": "", "title": "", "company": ""}
    if not title:
        return out
    t = re.sub(r"\s*[|\-–]\s*LinkedIn.*$", "", title.strip(), flags=re.I)
    parts = [p.strip() for p in re.split(r"\s+[-–]\s+", t) if p.strip()]
    if parts:
        out["name"] = parts[0]
    if len(parts) >= 3:
        out["title"], out["company"] = parts[1], parts[2]
    elif len(parts) == 2:
        m = re.split(r"\s+at\s+", parts[1], maxsplit=1, flags=re.I)
        out["title"] = m[0].strip()
        if len(m) == 2:
            out["company"] = m[1].strip()
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Network — headed browser session rendering both engines
# ══════════════════════════════════════════════════════════════════════════════

_ENGINE_URL = {
    "duckduckgo": "https://duckduckgo.com/?q=",
    "bing": "https://www.bing.com/search?q=",
}
_RESULT_WAIT = {
    "duckduckgo": "article[data-testid='result']",
    "bing": "li.b_algo",
}
_CHALLENGE_MARKERS = ("solve the challenge", "are you a robot", "unusual traffic",
                      "verify you are", "unexpected error")

_STEALTH_JS = """
Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});
window.chrome = window.chrome || {runtime:{}};
"""


class SearchBrowser:
    """
    A visible Chromium session used to render search-result pages. Kept open
    across a whole enrichment batch (one window, many queries) so it looks like
    one person browsing rather than a burst of fresh sessions.

    Use as a context manager:
        with SearchBrowser() as sb:
            results = sb.search("...", "duckduckgo")
    """

    def __init__(self, headless: bool = False):
        self.headless = headless
        self._pw = None
        self._browser = None
        self._page = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()

    def start(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox", "--disable-dev-shm-usage"])
        ctx = self._browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
            locale="en-US", viewport={"width": 1366, "height": 900})
        self._page = ctx.new_page()
        self._page.add_init_script(_STEALTH_JS)

    def close(self):
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    def search(self, query: str, engine: str, timeout: int = 30) -> str:
        """
        Render one query on one engine and return the results-page HTML. If a
        challenge/CAPTCHA appears (and the browser is visible), it pauses so the
        human can solve it, then continues — the established Dashin pattern.
        """
        page = self._page
        url = _ENGINE_URL[engine] + quote_plus(query)
        page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        try:
            page.wait_for_selector(_RESULT_WAIT[engine], timeout=12000)
        except Exception:
            # No results selector — maybe a challenge. Give a human a chance.
            body = ""
            try:
                body = page.evaluate("() => document.body.innerText.slice(0,600).toLowerCase()")
            except Exception:
                pass
            if any(m in body for m in _CHALLENGE_MARKERS) and not self.headless:
                print(f"  [{engine}] challenge shown — solve it in the window; "
                      "waiting up to 90s...")
                try:
                    page.wait_for_selector(_RESULT_WAIT[engine], timeout=90000)
                except Exception:
                    return ""
        page.wait_for_timeout(1200)
        return page.content()


# ══════════════════════════════════════════════════════════════════════════════
# Candidate selection + cross-engine reconciliation
# ══════════════════════════════════════════════════════════════════════════════

def _best_candidate(results: list, name: str = "", company: str = "") -> dict:
    """Pick the result that best matches the target; else the top result."""
    if not results:
        return {}
    if name:
        for r in results:
            blob = r["title"] + " " + r["snippet"]
            if name_in_text(name, blob):
                return r
    if company:
        for r in results:
            blob = r["title"] + " " + r["snippet"]
            if company_in_text(company, blob):
                return r
    return results[0]


# ══════════════════════════════════════════════════════════════════════════════
# Mode C1 — enrich a known contact (cross-checks both engines)
# ══════════════════════════════════════════════════════════════════════════════

def enrich_contact(browser: SearchBrowser, name: str, company: str = "",
                   title: str = "", engines=("duckduckgo", "bing")) -> dict:
    query = build_query_contact(name, company, title)
    per_engine = {}
    for eng in engines:
        html = browser.search(query, eng)
        cand = _best_candidate(parse_results(html, eng), name, company)
        if cand:
            per_engine[eng] = cand
        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    base = {"input_name": name, "input_company": company, "input_title": title,
            "linkedin_url": "", "found_name": "", "found_title": "", "found_company": "",
            "match_confidence": "not_found", "query": query,
            "engines_agree": False, "sources": ",".join(per_engine.keys()), "error": ""}
    if not per_engine:
        return base

    # Cross-check: do the engines agree on the same profile URL?
    urls = {eng: c["linkedin_url"] for eng, c in per_engine.items()}
    agree = len(set(urls.values())) == 1 and len(urls) > 1

    # Prefer the candidate that validates name+company; else the first engine's.
    chosen = None
    for eng, c in per_engine.items():
        blob = c["title"] + " " + c["snippet"]
        if name_in_text(name, blob) and (not company or company_in_text(company, blob)):
            chosen = c
            break
    if chosen is None:
        chosen = next(iter(per_engine.values()))

    fields = parse_profile_title(chosen["title"])
    blob = chosen["title"] + " " + chosen["snippet"]
    name_ok = name_in_text(name, blob)
    company_ok = company_in_text(company, blob) if company else False
    base.update({
        "linkedin_url": chosen["linkedin_url"],
        "found_name": fields["name"] or name,
        "found_title": fields["title"],
        "found_company": fields["company"],
        "match_confidence": classify_match(name_ok, company_ok, True, engines_agree=agree),
        "engines_agree": agree,
    })
    return base


# ══════════════════════════════════════════════════════════════════════════════
# Mode C2 — discover a person at a company by role priority (3 rounds)
# ══════════════════════════════════════════════════════════════════════════════

def discover_person(browser: SearchBrowser, company: str, roles: list,
                    engines=("duckduckgo", "bing")) -> dict:
    roles = [r for r in (roles or []) if r and r.strip()]
    rounds = []
    if len(roles) >= 1: rounds.append(("role", roles[0]))
    if len(roles) >= 2: rounds.append(("role", roles[1]))
    rounds.append(("broad", roles[2] if len(roles) >= 3 else ""))
    rounds = rounds[:3]

    attempts = []
    for i, (kind, role) in enumerate(rounds, start=1):
        query = build_query_role(role, company) if kind == "role" \
            else f"{company.strip()} team leadership linkedin"
        for eng in engines:
            html = browser.search(query, eng)
            for r in parse_results(html, eng):
                blob = r["title"] + " " + r["snippet"]
                if company_in_text(company, blob):
                    fields = parse_profile_title(r["title"])
                    return {"input_company": company, "linkedin_url": r["linkedin_url"],
                            "found_name": fields["name"], "found_title": fields["title"] or role,
                            "found_company": fields["company"] or company, "role_tried": role,
                            "round": i, "source": eng, "match_confidence": "exact",
                            "attempts": attempts, "error": ""}
            attempts.append({"round": i, "engine": eng, "query": query})
            time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    return {"input_company": company, "linkedin_url": "", "found_name": "",
            "found_title": "", "found_company": "", "role_tried": "",
            "round": len(rounds), "source": "", "match_confidence": "needs_manual",
            "attempts": attempts, "error": ""}
