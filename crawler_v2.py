import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import pandas as pd
import re
import time
import math

# Multi-tier fetcher (TLS/JA3 impersonation → stealth browser). Lets the crawler
# get through TLS-fingerprint and soft bot-walls that plain requests can't —
# e.g. the exact block that stopped a naive requests.get on biotech-careers.
# Optional: if the import fails we fall back to plain requests below.
try:
    from core.fetch_tiers import fetch as _tiered_fetch
    _FETCH_TIERS = True
except Exception:
    _FETCH_TIERS = False

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CrawlerV2/1.0)"}


def _get_html(url, timeout=None):
    """
    Fetch a page's HTML, preferring the multi-tier fetcher and falling back to
    plain requests. Headed browsing is disabled — a bulk crawler must stay
    non-interactive — so it escalates only as far as a stealth *headless*
    browser. Returns the HTML string, or "" on failure.
    """
    timeout = timeout or DEFAULT_TIMEOUT
    if _FETCH_TIERS:
        try:
            res = _tiered_fetch(url, timeout=timeout, allow_headed=False, verbose=False)
            return res.html if res.ok else ""
        except Exception as e:
            print(f"  Tiered fetch error {url}: {str(e)[:80]}")
            # fall through to plain requests
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"  Error fetching {url}: {str(e)[:80]}")
        return ""

IMPORTANT_KEYWORDS = [
    "about",
    "company",
    "solution",
    "product",
    "platform",
    "customer",
    "case-study",
    "partner",
    "provider",
    "payer",
    "hospital",
    "technology",
    "evidence",
    "study",
    "paper",
    "clinical",
]

DEFAULT_TIMEOUT = 12


def clean_text(text):
    return " ".join(text.split())


def get_page_text(url):
    html = _get_html(url)
    if not html:
        return "", None
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return clean_text(soup.get_text(" ", strip=True)), soup
    except Exception as e:
        print(f"  Error parsing {url}: {str(e)[:80]}")
        return "", None


def discover_pages(base_url, max_pages=12):
    urls = set()
    html = _get_html(base_url)
    if not html:
        return []
    try:
        soup = BeautifulSoup(html, "html.parser")
        base_netloc = urlparse(base_url).netloc
        for link in soup.find_all("a", href=True):
            full = urljoin(base_url, link["href"])
            if urlparse(full).netloc != base_netloc:
                continue
            lower = full.lower()
            if any(keyword in lower for keyword in IMPORTANT_KEYWORDS):
                urls.add(full.split('#')[0])
    except Exception:
        pass
    return list(urls)[:max_pages]


def count_signals(text, signals):
    counts = {}
    text_lower = text.lower()
    for keyword in signals:
        counts[keyword] = len(re.findall(r"\b" + re.escape(keyword.lower()) + r"\b", text_lower))
    return counts


def compute_attendance_likelihood_score(per_page_texts):
    """
    Single metric: Attendance Likelihood Score (0–100).

    Weights (Jan-approved):
    - Clinical evidence: +25
    - Reimbursement/DiGA: +25
    - Hospital/provider deployment: +20
    - Patient outcomes: +15
    - Insurance/payer: +10
    - Wellness/fitness/supplements/beauty: −25

    Small modifiers:
    - Stage/maturity (~10%): more mature startups edge out earlier
    - Seniority of contact (~5%): founder/CEO available bumps score

    Output: 0–100 score, top signal breakdown.
    """

    ATTENDANCE_SIGNALS = {
        "clinical evidence": 25,
        "clinical trial": 25,
        "rct": 25,
        "real world evidence": 25,
        "reimbursement": 25,
        "diga": 25,
        "hospital": 20,
        "provider": 20,
        "health system": 20,
        "healthcare system": 20,
        "patient outcomes": 15,
        "outcomes": 15,
        "payer": 10,
        "insurance": 10,
        "insurer": 10,
    }

    ATTENDANCE_PENALTIES = {
        "wellness": -25,
        "fitness": -25,
        "supplement": -25,
        "beauty": -25,
        "cosmetics": -25,
        "consumer app": -15,
        "lifestyle": -10,
    }

    raw_score = 0.0
    signal_hits = {}
    penalty_hits = {}

    for source, text in per_page_texts:
        source_weight = 1.0
        url_low = source.lower()
        if any(x in url_low for x in ("case", "case-study", "case_study", "paper", "study")):
            source_weight = 2.0

        t = text.lower()

        for signal, weight in ATTENDANCE_SIGNALS.items():
            cnt = len(re.findall(r"\b" + re.escape(signal.lower()) + r"\b", t))
            if cnt > 0:
                contribution = min(cnt, 5) * weight * source_weight
                raw_score += contribution
                signal_hits[signal] = signal_hits.get(signal, 0) + cnt

        for penalty, weight in ATTENDANCE_PENALTIES.items():
            cnt = len(re.findall(r"\b" + re.escape(penalty.lower()) + r"\b", t))
            if cnt > 0:
                contribution = min(cnt, 5) * weight * source_weight
                raw_score += contribution
                penalty_hits[penalty] = penalty_hits.get(penalty, 0) + cnt

    capped = max(min(raw_score, 500), -100)
    attendance_score = int(round((capped + 100) / 600 * 100))
    attendance_score = max(0, min(100, attendance_score))

    return attendance_score, raw_score, signal_hits, penalty_hits


def classify_tier(score):
    if score >= 70:
        return "Enterprise"
    if score >= 40:
        return "Mid-market"
    return "SMB / Consumer"


def recommend_persona(positives):
    keys = " ".join(positives.keys()).lower()
    if any(x in keys for x in ("clinical", "evidence", "trial", "rct")):
        return "Head of Evidence / Clinical"
    if any(x in keys for x in ("payer", "insurance", "reimbursement")):
        return "Head of Payer Partnerships"
    if any(x in keys for x in ("hospital", "provider", "health system")):
        return "Head of BD - Hospitals"
    return "Head of BD / Partnerships"


def reason_for_ranking(positives):
    items = sorted(positives.items(), key=lambda x: x[1], reverse=True)
    if not items:
        return "No attendance signals found"
    top = [f"{k}({v})" for k, v in items[:3]]
    return ", ".join(top)


def analyze_site(site, max_pages=8, delay=1.0):
    print(f"  Fetching homepage...")
    homepage_text, soup = get_page_text(site)
    if not homepage_text:
        print(f"  Warning: No homepage text extracted")

    print(f"  Discovering pages...")
    pages = discover_pages(site, max_pages=max_pages)
    print(f"  Found {len(pages)} relevant pages")

    per_page_texts = [(site, homepage_text)]

    for i, p in enumerate(pages):
        print(f"    [{i+1}/{len(pages)}] {p}")
        t, _ = get_page_text(p)
        if t:
            per_page_texts.append((p, t))
        time.sleep(delay)

    attendance_score, raw, signal_hits, penalty_hits = compute_attendance_likelihood_score(per_page_texts)

    title = ""
    description = ""
    try:
        if soup:
            title = soup.title.string.strip() if soup.title else ""
            desc_tag = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
            if desc_tag and desc_tag.get("content"):
                description = desc_tag.get("content").strip()
    except Exception:
        pass

    sample_text = ("\n\n".join([t for _, t in per_page_texts]))[:2000]

    all_signals = {**signal_hits, **penalty_hits}
    top_signals_str = reason_for_ranking(all_signals)

    return {
        "website": site,
        "attendance_likelihood_score": attendance_score,
        "raw_score": raw,
        "positive_signals": signal_hits,
        "negative_signals": penalty_hits,
        "title": title,
        "description": description,
        "top_signals": top_signals_str,
        "text_sample": sample_text,
    }


if __name__ == "__main__":
    WEBSITES = [
        "https://who.foundation",
        "https://planny.ch",
        "https://icure.com",
        "https://herbonis.com",
        "https://bigomics.ch",
        "https://insights.md",
        "https://explorishealth.com",
        "https://b-rayz.ch",
        "https://aligned.ch",
        "https://nhumi.com",
        "https://agrisano.ch",
        "https://precisiacare.com",
        "https://collabree.com",
        "https://yourself.health",
        "https://genomsys.com",
        "https://evismo.com",
        "https://asanus.de",
    ]

    test_sites = WEBSITES
    print(f"Starting crawler on {len(test_sites)} sites...\n")

    rows = []
    for site in test_sites:
        print(f"Processing {site}")
        try:
            result = analyze_site(site, max_pages=6, delay=0.5)
            rows.append(result)
            print(f"  Attendance Likelihood Score: {result['attendance_likelihood_score']}\n")
        except Exception as e:
            print(f"  Error: {str(e)[:100]}\n")

    if rows:
        df = pd.DataFrame(rows)
        df.sort_values(by="attendance_likelihood_score", ascending=False, inplace=True)
        out = "proving_health_company_analysis_v2.csv"
        for col in ("positive_signals", "negative_signals"):
            df[col] = df[col].apply(lambda d: str(d))

        df.to_csv(out, index=False)
        print(f"\nRanked by Attendance Likelihood Score (0–100), descending:")
        print(df[["website", "attendance_likelihood_score", "top_signals"]].to_string())
        print(f"\nSaved {out}")
    else:
        print("No results to save.")
