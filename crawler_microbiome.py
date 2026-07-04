"""
Microbiome Company Scorer for InnovITech / Microbiolance campaign.

Goal: Find microbiome diagnostics/analytics companies in Western EU that could
      become clients of InnovITech — companies doing real sequencing/wet-lab work
      but light on in-house software/bioinformatics.

Workflow:
  1. Read Crunchbase CSV (companies6-12-2026.csv or similar)
  2. Gate-check each company using Full Description (microbiome core signal must exist)
  3. For gate-passing companies: crawl website for additional signals
  4. Score on InnovITech prospect fit (0–100)
  5. Assign type tag (1–4) and geography tier
  6. Save ranked CSV to Desktop
"""

import os
import re
import sys
import time
import json
import requests
import pandas as pd
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed — env vars must be set in the shell

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

COMPANY_TIMEOUT = 35  # hard wall-clock seconds per company

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MicrobiomeCrawler/1.0)"}
DEFAULT_TIMEOUT = 8

# Multi-tier fetcher (TLS impersonation → stealth headless browser), used to get
# through fingerprint/soft bot-walls. Optional — falls back to plain requests.
try:
    from core.fetch_tiers import fetch as _tiered_fetch
    _FETCH_TIERS = True
except Exception:
    _FETCH_TIERS = False

# Pages to try beyond homepage (microbiome-specific paths)
EXTRA_PATHS = [
    "/about", "/about-us", "/technology", "/science", "/platform",
    "/products", "/solutions", "/how-it-works", "/our-science",
]

# Tier-1 Western EU country keywords in headquarters location
TIER1_GEO = ["germany", "united kingdom", "netherlands", "france", "switzerland",
             "denmark", "sweden", "finland", "belgium", "austria",
             " uk", " de", " nl", " fr", " ch", " dk", " se", " fi", " be", " at"]

# ---------------------------------------------------------------------------
# Gate: must contain at least one of these to proceed to scoring
# ---------------------------------------------------------------------------

MICROBIOME_GATE = [
    "microbiome", "microbiota", "gut microbiome", "gut microbiota",
    "metagenomics", "16s", "shotgun sequencing", "metatranscriptomics",
    "microbiome testing", "microbiome diagnostics", "microbiome biomarkers",
    "microbiome sequencing", "gut flora", "gut bacteria",
]

# ---------------------------------------------------------------------------
# Scoring signals
# ---------------------------------------------------------------------------

# Positive: company is a good InnovITech prospect (needs software built)
POSITIVE_SIGNALS = {
    # Core microbiome presence (high weight — confirms gate passed on site too)
    "microbiome": 15,
    "microbiota": 15,
    "metagenomics": 20,
    "16s": 20,
    "shotgun sequencing": 20,
    "metatranscriptomics": 20,
    # Signals they do real lab/sequencing work
    "sequencing": 12,
    "dna sequencing": 15,
    "rna sequencing": 12,
    "next generation sequencing": 15,
    "ngs": 15,
    "biobank": 10,
    "sample collection": 10,
    "stool sample": 15,
    "fecal sample": 15,
    "gut sample": 10,
    # They generate reports / have a software need
    "diagnostic report": 15,
    "microbiome report": 15,
    "testing kit": 15,
    "test kit": 15,
    "at-home test": 12,
    "clinical report": 12,
    "reporting": 8,
    # Clinical / reimbursement positioning (sophisticated, more SW need)
    "reimbursement": 20,
    "clinical trial": 18,
    "clinical study": 15,
    "clinical validation": 15,
    "ce mark": 12,
    "fda": 12,
    "medical device": 10,
    "ivd": 12,
    "disease detection": 12,
    "biomarker discovery": 15,
    # Explicit analytics / platform need signals
    "analytics": 8,
    "data analysis": 8,
    "bioinformatics pipeline": 15,
    "analysis pipeline": 12,
    "dashboard": 8,
    "visualization": 6,
    # Disease-area signals (clinical focus = more SW need)
    "inflammatory bowel disease": 12,
    "ibd": 10,
    "ibs": 10,
    "crohn": 10,
    "colorectal cancer": 12,
    "oncology": 8,
    "diabetes": 8,
    "obesity": 8,
    "mental health": 8,
    "metabolic": 8,
}

# Negative: company already has heavy in-house platform (less need for InnovITech)
# or is consumer-only wellness (not the right target)
NEGATIVE_SIGNALS = {
    "proprietary pipeline": -20,
    "proprietary platform": -15,
    "in-house bioinformatics": -20,
    "own platform": -10,
    "supplements": -20,
    "probiotic supplement": -20,
    "probiotic capsule": -15,
    "nutraceutical": -15,
    "beauty": -15,
    "cosmetics": -15,
    "fitness": -10,
    "weight loss": -10,
    "consumer app": -10,
}

# ---------------------------------------------------------------------------
# Type tagging
# ---------------------------------------------------------------------------

TYPE_KEYWORDS = {
    "Type 1 - Diagnostics": [
        "testing kit", "test kit", "at-home test", "direct-to-consumer",
        "dtc", "consumer testing", "microbiome report", "wellness report",
        "home testing", "stool test", "gut test",
    ],
    "Type 2 - Clinical": [
        "physician", "gastroenterologist", "clinician", "hospital",
        "clinical validation", "reimbursement", "medical device", "ivd",
        "clinical microbiome", "functional medicine", "disease detection",
        "clinical report",
    ],
    "Type 3 - Biomarker Platform": [
        "biomarker discovery", "biomarker platform", "bioinformatics pipeline",
        "metagenomics platform", "sequencing platform", "microbiome signatures",
        "ibd biomarker", "ibs biomarker", "oncology biomarker",
        "drug discovery", "target discovery",
    ],
    "Type 4 - Therapeutics": [
        "live biotherapeutics", "lbp", "microbiome-based drug",
        "precision probiotic", "microbiome drug", "fecal microbiota transplant",
        "fmt", "therapeutic", "biopharmaceutical",
    ],
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def clean_text(text: str) -> str:
    return " ".join(text.split())


def get_page_text(url: str):
    # Prefer the tiered fetcher (headless only — this is a bulk crawler); fall
    # back to plain requests if it's unavailable or errors.
    html = ""
    if _FETCH_TIERS:
        try:
            res = _tiered_fetch(url, timeout=DEFAULT_TIMEOUT, allow_headed=False, verbose=False)
            html = res.html if res.ok else ""
        except Exception as e:
            print(f"    Tiered fetch error {url}: {str(e)[:70]}")
    if not html:
        try:
            r = requests.get(url, headers=HEADERS, timeout=(5, DEFAULT_TIMEOUT))
            r.raise_for_status()
            html = r.text
        except Exception as e:
            print(f"    Fetch error {url}: {str(e)[:70]}")
            return "", None
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "nav", "footer"]):
            tag.decompose()
        return clean_text(soup.get_text(" ", strip=True)), soup
    except Exception as e:
        print(f"    Parse error {url}: {str(e)[:70]}")
        return "", None


def try_extra_pages(base_url: str, max_extra: int = 4) -> list[str]:
    """Try a fixed list of high-signal paths and return text for those that load."""
    results = []
    base_netloc = urlparse(base_url).netloc
    for path in EXTRA_PATHS[:max_extra]:
        url = base_url.rstrip("/") + path
        text, _ = get_page_text(url)
        if len(text) > 200:
            results.append((url, text))
        time.sleep(0.3)
    return results


def passes_gate(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in MICROBIOME_GATE)


def score_text(text: str) -> tuple[float, dict, dict]:
    t = text.lower()
    raw = 0.0
    pos_hits: dict = {}
    neg_hits: dict = {}

    for signal, weight in POSITIVE_SIGNALS.items():
        cnt = len(re.findall(r"\b" + re.escape(signal) + r"\b", t))
        if cnt > 0:
            raw += min(cnt, 5) * weight
            pos_hits[signal] = cnt

    for signal, weight in NEGATIVE_SIGNALS.items():
        cnt = len(re.findall(r"\b" + re.escape(signal) + r"\b", t))
        if cnt > 0:
            raw += min(cnt, 3) * weight  # cap penalty contribution
            neg_hits[signal] = cnt

    return raw, pos_hits, neg_hits


def normalize_score(raw: float) -> int:
    """Map raw score to 0–100. Raw range roughly -60 to +600."""
    capped = max(min(raw, 600), -60)
    s = int(round((capped + 60) / 660 * 100))
    return max(0, min(100, s))


def assign_type(text: str) -> str:
    t = text.lower()
    scores = {}
    for type_name, keywords in TYPE_KEYWORDS.items():
        scores[type_name] = sum(1 for kw in keywords if kw in t)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "Unclassified"


def geography_tier(location: str) -> tuple[str, int]:
    if not isinstance(location, str):
        return "Other", 0
    loc = location.lower()
    if any(g in loc for g in TIER1_GEO):
        return "Tier 1 - Western EU", 10
    return "Other", 0


def top_signals_str(pos: dict, neg: dict) -> str:
    all_sig = {**pos, **{k: v for k, v in neg.items()}}
    items = sorted(all_sig.items(), key=lambda x: abs(x[1]), reverse=True)[:4]
    if not items:
        return "No signals found"
    return ", ".join(f"{k}({v})" for k, v in items)


def safe_url(s) -> str:
    if not isinstance(s, str):
        return ""
    s = s.strip()
    if not s or s.lower() in ("nan", "none", ""):
        return ""
    if not s.startswith("http"):
        s = "https://" + s
    return s


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

def process_company(row: dict) -> dict:
    name = row.get("Organization Name", "")
    website = safe_url(row.get("Website", ""))
    description = str(row.get("Full Description", "") or row.get("Description", "") or "")
    location = str(row.get("Headquarters Location", "") or "")
    employees = str(row.get("Number of Employees", "") or "")

    geo_tier, geo_bonus = geography_tier(location)

    # Always crawl — description alone may miss microbiome signals on the site
    all_texts = [("description", description)]
    crawl_status = "description_only"

    if website:
        homepage_text, _ = get_page_text(website)
        if homepage_text:
            all_texts.append((website, homepage_text))
            crawl_status = "ok"
            extra = try_extra_pages(website, max_extra=3)
            all_texts.extend(extra)
        else:
            crawl_status = "failed"

    # Combine all text and gate-check on everything (description + crawled site)
    combined = " ".join(t for _, t in all_texts)
    gate_passed = passes_gate(combined)

    raw, pos_hits, neg_hits = score_text(combined)

    # Geography bonus
    raw += geo_bonus

    score = normalize_score(raw) if gate_passed else 0
    company_type = assign_type(combined) if gate_passed else "DISQUALIFIED - No microbiome core"

    return {
        "gate_passed": gate_passed,
        "microbiome_score": score,
        "raw_score": round(raw, 1),
        "geo_tier": geo_tier,
        "company_type": company_type,
        "top_signals": top_signals_str(pos_hits, neg_hits),
        "positive_signals": str(pos_hits),
        "negative_signals": str(neg_hits),
        "crawl_status": crawl_status,
    }


CHECKPOINT_CSV = None  # set in __main__


def main(input_csv: str, output_csv: str, checkpoint_csv: str):
    print(f"Reading: {input_csv}")
    df = pd.read_csv(input_csv)
    print(f"  {len(df)} companies loaded")

    # Load checkpoint to find already-processed indices
    done_indices: set = set()
    if os.path.exists(checkpoint_csv):
        try:
            ckpt = pd.read_csv(checkpoint_csv)
            if "_input_idx" in ckpt.columns:
                done_indices = set(ckpt["_input_idx"].dropna().astype(int).tolist())
                print(f"  Resuming from checkpoint — {len(done_indices)} rows already done")
        except Exception as e:
            print(f"  Checkpoint load failed ({e}), starting fresh")

    gate_pass = 0
    gate_fail = 0
    result_cols = ["gate_passed", "microbiome_score", "raw_score", "geo_tier",
                   "company_type", "top_signals", "positive_signals", "negative_signals",
                   "crawl_status", "_input_idx"]

    # Open checkpoint in append mode; write header only if new
    write_header = not os.path.exists(checkpoint_csv) or os.path.getsize(checkpoint_csv) == 0
    ckpt_file = open(checkpoint_csv, "a", encoding="utf-8", newline="")
    import csv as _csv
    writer = _csv.writer(ckpt_file)
    if write_header:
        writer.writerow(list(df.columns) + result_cols)
        ckpt_file.flush()

    for idx, row in df.iterrows():
        if idx in done_indices:
            continue

        name = row.get("Organization Name", f"Row {idx}")
        print(f"[{idx+1}/{len(df)}] {name}")
        row_dict = row.to_dict()
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(process_company, row_dict)
                result = future.result(timeout=COMPANY_TIMEOUT)
        except FuturesTimeout:
            print(f"  [TIMEOUT] Skipping {name} after {COMPANY_TIMEOUT}s")
            result = {
                "gate_passed": False, "microbiome_score": 0, "raw_score": 0,
                "geo_tier": "Unknown", "company_type": "TIMEOUT",
                "top_signals": "", "positive_signals": "{}", "negative_signals": "{}",
                "crawl_status": "timeout",
            }
        except Exception as e:
            print(f"  [ERROR] {name}: {e}")
            result = {
                "gate_passed": False, "microbiome_score": 0, "raw_score": 0,
                "geo_tier": "Unknown", "company_type": "ERROR",
                "top_signals": "", "positive_signals": "{}", "negative_signals": "{}",
                "crawl_status": "error",
            }
        result["_input_idx"] = idx

        writer.writerow(list(row.values) + [result[c] for c in result_cols])
        ckpt_file.flush()

        if result["gate_passed"]:
            gate_pass += 1
            print(f"  [OK] Score: {result['microbiome_score']} | {result['company_type']} | {result['geo_tier']}")
            print(f"    Signals: {result['top_signals']}")
        else:
            gate_fail += 1
        time.sleep(0.2)

    ckpt_file.close()
    print(f"\nDone: {gate_pass} newly qualified, {gate_fail} newly disqualified")

    # Build final sorted output — deduplicate by Organization Name, keep best score
    out = pd.read_csv(checkpoint_csv)
    out = out.drop(columns=["_input_idx"], errors="ignore")
    out["microbiome_score"] = pd.to_numeric(out["microbiome_score"], errors="coerce").fillna(0)
    out = out.sort_values("microbiome_score", ascending=False)
    out = out.drop_duplicates(subset=["Organization Name"], keep="first")

    # Add manual_review flag for timed-out companies
    out["manual_review"] = out["crawl_status"].apply(
        lambda s: "YES - site timed out, check manually" if str(s) == "timeout" else ""
    )

    # Sort: qualified first by score, then timeouts, then disqualified
    def sort_key(row):
        if str(row["gate_passed"]).lower() == "true":
            return (0, -row["microbiome_score"])
        elif str(row["crawl_status"]) == "timeout":
            return (1, 0)
        else:
            return (2, 0)

    out["_sort"] = out.apply(sort_key, axis=1)
    out = out.sort_values("_sort").drop(columns=["_sort"])
    out["rank"] = range(1, len(out) + 1)
    out.to_csv(output_csv, index=False, encoding="utf-8")
    print(f"\nSaved: {output_csv}")

    qualified = out[out["gate_passed"].astype(str).str.lower() == "true"].head(15)
    print("\nTop 15 qualified microbiome companies:")
    for _, r in qualified.iterrows():
        print(f"  #{int(r['rank'])} {r.get('Organization Name','?')} | {r['microbiome_score']} | {r['company_type']} | {r.get('Headquarters Location','?')}")

    timeouts = out[out["crawl_status"] == "timeout"]
    print(f"\nTimeouts to review manually: {len(timeouts)}")
    for _, r in timeouts.iterrows():
        print(f"  {r.get('Organization Name','?')} | {r.get('Website','?')}")

    # Push qualified leads to Dashin API if credentials are configured
    api_url = os.environ.get("DASHIN_API_URL", "").strip()
    api_token = os.environ.get("DASHIN_API_TOKEN", "").strip()
    if api_url and api_token:
        push_to_dashin(out, api_url, api_token)
    else:
        print("\n[API] DASHIN_API_URL / DASHIN_API_TOKEN not set — skipping API push.")
        print("      Set them in your .env file to enable automatic import.")


def push_to_dashin(df: pd.DataFrame, api_url: str, api_token: str) -> None:
    """POST gate-passing rows from the scored DataFrame to the Dashin /api/leads/import endpoint.

    Only companies where gate_passed == True are pushed. Maps Crunchbase column
    names to Dashin lead fields.
    """
    # Filter to gate-passing companies only
    qualified = df[df["gate_passed"].astype(str).str.lower() == "true"].copy()
    if qualified.empty:
        print("[API] No gate-passing companies to push.")
        return

    rows = []
    for _, r in qualified.iterrows():
        name = str(r.get("Organization Name", "") or "").strip()
        if not name:
            continue

        website = str(r.get("Website", "") or "").strip()
        if website and website.lower() not in ("nan", "none", ""):
            if not website.startswith("http"):
                website = "https://" + website
        else:
            website = ""

        # Build description from Full Description or Description
        description = str(r.get("Full Description", "") or r.get("Description", "") or "").strip()

        # Country from Headquarters Location
        country = str(r.get("Headquarters Location", "") or "").strip()
        if country.lower() in ("nan", "none"):
            country = ""

        # Score
        try:
            score = int(float(r.get("microbiome_score", 0) or 0))
        except (ValueError, TypeError):
            score = 0

        # Score type from company_type column
        score_type = str(r.get("company_type", "") or "").strip()
        if score_type.lower() in ("nan", "none", "disqualified - no microbiome core", "timeout", "error", "unclassified"):
            score_type = ""

        # Crawl status for manual_review
        crawl_status = str(r.get("crawl_status", "") or "").strip()
        manual_review = "YES - site timed out, check manually" if crawl_status == "timeout" else ""

        rows.append({
            "company_name": name,
            "website": website,
            "description": description,
            "country": country,
            "score": score,
            "score_type": score_type,
            "crawl_status": crawl_status,
            "manual_review": manual_review,
            "source": "crunchbase-microbiome",
        })

    if not rows:
        print("[API] No valid rows to push after filtering.")
        return

    endpoint = api_url.rstrip("/") + "/api/leads/import"
    payload = {"rows": rows, "source": "crunchbase-microbiome"}

    print(f"\n[API] Pushing {len(rows)} qualified microbiome leads to {endpoint} ...")
    try:
        resp = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload),
            timeout=30,
        )
        if resp.ok:
            print(f"[API] Success — {resp.status_code}: {resp.text[:200]}")
        else:
            print(f"[API] Failed — HTTP {resp.status_code}: {resp.text[:300]}")
    except Exception as e:
        print(f"[API] Error pushing to Dashin: {e}")


if __name__ == "__main__":
    # Input: the Crunchbase export
    INPUT_CSV = r"C:\Users\lenovo\Downloads\biome6-13-2026.csv"

    # Output: Desktop
    userprofile = os.environ.get("USERPROFILE", r"C:\Users\lenovo")
    desktop = os.path.join(userprofile, "Desktop")
    if not os.path.isdir(desktop):
        desktop = userprofile
    OUTPUT_CSV = os.path.join(desktop, "microbiome_companies_scored.csv")

    # Checkpoint file lives next to output (same folder, different name)
    CHECKPOINT_CSV = os.path.join(os.path.dirname(OUTPUT_CSV), "microbiome_checkpoint.csv")

    if not os.path.exists(INPUT_CSV):
        print(f"Input CSV not found: {INPUT_CSV}")
        sys.exit(1)

    main(INPUT_CSV, OUTPUT_CSV, CHECKPOINT_CSV)
