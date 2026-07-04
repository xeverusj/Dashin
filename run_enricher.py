"""
run_enricher.py — CLI runner for the LinkedIn enricher (Module C).

Launched as a visible subprocess by the enrichment dashboard (like the scrapers)
so the search browser window opens outside Streamlit's process. Reads an input
CSV, enriches each row, and writes results progressively so a crash/stop never
loses completed rows.

Usage:
  python run_enricher.py --input contacts.csv --output enriched.csv --mode contact
  python run_enricher.py --input companies.csv --output enriched.csv --mode roles \
         --titles "CEO,Founder,Head of Growth"

Modes:
  contact  C1 — each row has a name (+ company/title); find that person's profile.
  roles    C2 — each row is a company; try the --titles list over up to 3 rounds.

Column detection is tolerant: name/full_name/contact, company/company_name/
organisation, title/job_title/role.
"""

import sys
import os
import re
import csv
import argparse

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

if sys.platform.startswith("win"):
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from services import enrichment_finder as ef


def _norm_key(k: str) -> str:
    """Normalize a header: lowercase, non-alphanumerics → underscore."""
    return re.sub(r"[^a-z0-9]+", "_", (k or "").strip().lower()).strip("_")


def _pick(row: dict, *names) -> str:
    """
    Return the first present, non-empty value whose column matches any of *names*.
    Headers are normalized ("Full Name" → "full_name") and matched exactly first,
    then by substring so "name" also catches "contact_name"/"full_name".
    """
    norm = {_norm_key(k): v for k, v in row.items()}
    for n in names:                       # exact normalized match
        v = norm.get(n)
        if v and str(v).strip():
            return str(v).strip()
    for n in names:                       # substring fallback
        for nk, v in norm.items():
            if n in nk and v and str(v).strip():
                return str(v).strip()
    return ""


CONTACT_FIELDS = ["input_name", "input_company", "input_title", "linkedin_url",
                  "found_name", "found_title", "found_company", "match_confidence",
                  "engines_agree", "sources", "query"]
ROLE_FIELDS = ["input_company", "linkedin_url", "found_name", "found_title",
               "found_company", "role_tried", "round", "source", "match_confidence"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--mode", choices=["contact", "roles"], required=True)
    ap.add_argument("--titles", default="", help="comma-separated roles for --mode roles")
    args = ap.parse_args()

    rows = []
    with open(args.input, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    print(f"Loaded {len(rows)} rows from {args.input}")

    fields = CONTACT_FIELDS if args.mode == "contact" else ROLE_FIELDS
    titles = [t.strip() for t in args.titles.split(",") if t.strip()]
    if args.mode == "roles" and not titles:
        print("ERROR: --mode roles requires --titles"); sys.exit(1)

    # Open output up front and write header, then append each row as it finishes
    # (progressive export — a crash/stop keeps completed rows).
    out = open(args.output, "w", encoding="utf-8-sig", newline="")
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
    writer.writeheader(); out.flush()

    done = 0
    try:
        with ef.SearchBrowser(headless=False) as sb:
            print("Browser open. Solve any CAPTCHA in the window if one appears.\n")
            for i, row in enumerate(rows, 1):
                company = _pick(row, "company", "company_name", "organisation", "organization")
                if args.mode == "contact":
                    name = _pick(row, "name", "full_name", "contact", "person")
                    title = _pick(row, "title", "job_title", "role", "position")
                    if not name:
                        print(f"[{i}/{len(rows)}] (skip — no name)")
                        continue
                    print(f"[{i}/{len(rows)}] {name} @ {company or '?'} ...", flush=True)
                    res = ef.enrich_contact(sb, name, company, title)
                else:
                    if not company:
                        print(f"[{i}/{len(rows)}] (skip — no company)")
                        continue
                    print(f"[{i}/{len(rows)}] {company} (roles: {', '.join(titles)}) ...", flush=True)
                    res = ef.discover_person(sb, company, titles)

                writer.writerow(res); out.flush()
                done += 1
                print(f"    → {res.get('match_confidence')} | {res.get('linkedin_url') or '(none)'}")
    except KeyboardInterrupt:
        print("\nInterrupted — completed rows are saved.")
    finally:
        out.close()
        print(f"\n[DONE] {done} rows written to {args.output}")


if __name__ == "__main__":
    main()
