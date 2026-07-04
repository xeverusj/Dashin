"""
score_csv.py — crawl a CSV of company websites into a scoring-ready CSV.

NOTE: this used to call crawler_v2.analyze_site, a deterministic keyword scorer.
That scoring method is banned (it produced false positives — e.g. ranking a
company highly just for mentioning a keyword). Scoring is now an AI judgment step
done in the app's "AI Scoring" page against a plain-language profile.

So this script no longer scores. It only *gathers*: it crawls each website
(homepage + a few high-signal pages, through the multi-tier fetcher with TLS
impersonation) and appends the gathered text to the sheet. Feed the output into
the AI Scoring page, which lets you (or your own AI) score it with judgment.

Usage:
    python score_csv.py input.csv [output.csv]

Input needs a website/URL column (case-insensitive: website, url, domain,
company_domain). company_name is passed through if present.

Output columns appended: crawled_text, crawl_status (ok|thin|failed|no_url),
pages_fetched.
"""

import os
import sys
import time

import pandas as pd

from crawler_v2 import get_page_text, discover_pages

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

MAX_PAGES = 5          # homepage + up to 4 discovered pages
MAX_TEXT = 12000       # cap combined text per company (keeps scoring prompts sane)
_WEBSITE_COLS = ("website", "url", "domain", "company_domain", "company_website")


def _find_website_col(df: pd.DataFrame):
    norm = {str(c).strip().lower(): c for c in df.columns}
    for cand in _WEBSITE_COLS:
        if cand in norm:
            return norm[cand]
    return None


def safe_website(s: str) -> str:
    if not isinstance(s, str):
        return ""
    s = s.strip()
    if not s or s.lower() in ("nan", "none"):
        return ""
    return s if s.startswith("http") else "https://" + s


def gather_site(site: str) -> dict:
    """Crawl a site into combined text. Returns crawled_text/crawl_status/pages_fetched."""
    if not site:
        return {"crawled_text": "", "crawl_status": "no_url", "pages_fetched": 0}

    texts = []
    home_text, _ = get_page_text(site)
    if home_text:
        texts.append(home_text)
    for url in discover_pages(site, max_pages=MAX_PAGES - 1):
        t, _ = get_page_text(url)
        if t and len(t) > 200:
            texts.append(t)
        time.sleep(0.3)

    combined = " ".join(texts)[:MAX_TEXT]
    if not combined:
        status = "failed"
    elif len(combined) < 300:
        status = "thin"
    else:
        status = "ok"
    return {"crawled_text": combined, "crawl_status": status, "pages_fetched": len(texts)}


def main(input_path: str, output_path: str):
    if not os.path.exists(input_path):
        print(f"Input file not found: {input_path}")
        return
    df = pd.read_csv(input_path).fillna("")
    website_col = _find_website_col(df)
    if website_col is None:
        print(f"Input CSV needs a website column (one of: {', '.join(_WEBSITE_COLS)})")
        return

    print(f"Crawling {len(df)} companies (gather only — no scoring)...")
    results = []
    for idx, row in df.iterrows():
        site = safe_website(row.get(website_col, ""))
        print(f"[{idx+1}/{len(df)}] {site or '(no url)'}", flush=True)
        res = gather_site(site)
        print(f"    {res['crawl_status']} — {res['pages_fetched']} pages, "
              f"{len(res['crawled_text'])} chars")
        results.append(res)

    out = pd.concat([df.reset_index(drop=True),
                     pd.DataFrame(results).reset_index(drop=True)], axis=1)
    out.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"\n[DONE] Scoring-ready CSV: {output_path}")
    print("Next: open the app's 'AI Scoring' page, upload this file, paste your "
          "scoring guide, and score it with your own AI.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python score_csv.py input.csv [output.csv]")
        sys.exit(1)
    inp = sys.argv[1]
    outp = sys.argv[2] if len(sys.argv) > 2 else \
        os.path.splitext(inp)[0] + "_crawled.csv"
    main(inp, outp)
