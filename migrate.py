"""
migrate.py — Dashin Research
Imports all existing CSV data from data_system/ into the new SQLite database.
Safe to run multiple times — duplicates are automatically skipped.

Run with: python migrate.py
"""

import os
import sys
import glob
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# ── COLOURS ───────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
BLUE   = "\033[94m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def log_ok(msg):   print(f"  {GREEN}✓{RESET}  {msg}")
def log_skip(msg): print(f"  {YELLOW}→{RESET}  {msg}")
def log_err(msg):  print(f"  {RED}✗{RESET}  {msg}")
def log_info(msg): print(f"  {BLUE}ℹ{RESET}  {msg}")

# ── INIT DB ───────────────────────────────────────────────────────────────────
print(f"\n{BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")
print(f"{BOLD}  Dashin Research — Data Migration{RESET}")
print(f"{BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}\n")

try:
    from core.db import init_db, get_connection
    from services.lead_service import save_lead
    init_db()
    log_ok("Database initialised")
except Exception as e:
    log_err(f"Could not load core modules: {e}")
    print(f"\n  Make sure you run this from your project root folder.")
    print(f"  Example: cd C:\\...\\dashin && python migrate.py\n")
    sys.exit(1)

# ── FIND CSV FILES ────────────────────────────────────────────────────────────

# Look in all likely locations — including subfolders
search_paths = [
    ROOT / "data_system",
    ROOT / "data_system" / "sessions",      # ← your actual location
    ROOT / "data" / "system",
    ROOT / "data" / "system" / "sessions",
    ROOT / "data",
]

csv_files = []
for path in search_paths:
    if path.exists():
        # glob one level + also check subfolders named 'sessions'
        found = list(path.glob("*.csv")) + list(path.glob("sessions/*.csv"))
        found = list(set(found))  # dedupe
        csv_files.extend(found)
        if found:
            log_info(f"Found {len(found)} CSV file(s) in {path.relative_to(ROOT)}/")

# Also check root folder
root_csvs = list(ROOT.glob("*.csv"))
csv_files.extend(root_csvs)
if root_csvs:
    log_info(f"Found {len(root_csvs)} CSV file(s) in root folder")

# Deduplicate
csv_files = list(set(csv_files))

if not csv_files:
    print(f"\n  {YELLOW}No CSV files found. Nothing to migrate.{RESET}\n")
    sys.exit(0)

print(f"\n{BOLD}[1/3] Discovered {len(csv_files)} CSV file(s){RESET}")
for f in csv_files:
    print(f"      {f.relative_to(ROOT)}")


# ── COLUMN NAME NORMALISERS ───────────────────────────────────────────────────

def find_col(df, candidates):
    """Find the first matching column name (case-insensitive)."""
    cols_lower = {c.lower(): c for c in df.columns}
    for c in candidates:
        if c.lower() in cols_lower:
            return cols_lower[c.lower()]
    return None

def extract_lead_fields(row, df):
    """Try to extract name/title/company from a row regardless of column names."""
    name_col    = find_col(df, ['full_name','name','Full Name','Name'])
    title_col   = find_col(df, ['title','job_title','Job Title','Title','position'])
    company_col = find_col(df, ['company','company_name','Company','Organisation'])
    cat_col     = find_col(df, ['category','Category','type','Type','filter'])
    tags_col    = find_col(df, ['tags','Tags','interests','Interests'])
    url_col     = find_col(df, ['source_url','source','Source','url','URL','event_url'])

    name    = str(row[name_col]).strip()    if name_col    else ""
    title   = str(row[title_col]).strip()   if title_col   else ""
    company = str(row[company_col]).strip() if company_col else "Unknown"
    category= str(row[cat_col]).strip()     if cat_col     else "Imported"
    tags    = str(row[tags_col]).strip()    if tags_col    else ""
    url     = str(row[url_col]).strip()     if url_col     else ""

    # Clean up NaN/None strings
    for val in [name, title, company, category, tags, url]:
        val = "" if val.lower() in ("nan","none","n/a","") else val

    return name, title, company, category, tags, url


# ── MIGRATION ─────────────────────────────────────────────────────────────────

print(f"\n{BOLD}[2/3] Importing leads...{RESET}\n")

total_processed = 0
total_new       = 0
total_dupes     = 0
total_skipped   = 0
file_results    = []

# Skip master files — we only want session/event files
# (master files are reconstructed from sessions anyway)
SKIP_FILENAMES = {"leads_master.csv", "companies_master.csv"}

for csv_path in sorted(csv_files):
    fname = csv_path.name

    if fname in SKIP_FILENAMES:
        log_skip(f"Skipping master file: {fname}")
        continue

    print(f"  📄 {csv_path.relative_to(ROOT)}")

    try:
        # Try reading with different encodings
        df = None
        for enc in ["utf-8", "utf-8-sig", "latin-1", "cp1252"]:
            try:
                df = pd.read_csv(csv_path, encoding=enc)
                break
            except Exception:
                continue

        if df is None or df.empty:
            log_skip(f"  Empty or unreadable — skipped")
            continue

        # Detect if this looks like a leads file
        name_col = find_col(df, ['full_name','name','Full Name','Name'])
        if not name_col:
            log_skip(f"  No name column found — skipped (not a leads file)")
            continue

        # Derive event name from filename
        event_name = fname.replace(".csv","").replace("_"," ").replace("-"," ")

        file_new   = 0
        file_dupes = 0
        file_skip  = 0

        for _, row in df.iterrows():
            name, title, company, category, tags, url = extract_lead_fields(row, df)

            # Skip obvious garbage rows
            if not name or len(name) < 3:
                file_skip += 1
                continue
            if name.upper() in ("SIGN IN","DELEGATES","SEARCH","FILTER",
                                 "FULL NAME","NAME","NAN"):
                file_skip += 1
                continue
            if len(name.split()) < 2:
                file_skip += 1
                continue

            # Clean up
            company  = company  if company  and company.lower()  not in ("nan","none","") else "Unknown"
            title    = title    if title    and title.lower()    not in ("nan","none","") else ""
            category = category if category and category.lower() not in ("nan","none","") else "Imported"
            tags     = tags     if tags     and tags.lower()     not in ("nan","none","") else ""
            url      = url      if url      and url.lower()      not in ("nan","none","") else ""

            try:
                result = save_lead(
                    full_name    = name,
                    company      = company,
                    title        = title,
                    tags         = tags,
                    event_name   = event_name,
                    event_url    = url,
                    category     = category,
                    layout       = "imported",
                    session_id   = f"migrate_{fname[:20]}"
                )
                if result["status"] == "new":
                    file_new += 1
                else:
                    file_dupes += 1
            except Exception as e:
                file_skip += 1

        total_new    += file_new
        total_dupes  += file_dupes
        total_skipped+= file_skip
        total_processed += file_new + file_dupes

        status_str = f"{GREEN}+{file_new} new{RESET}"
        if file_dupes:
            status_str += f"  {YELLOW}{file_dupes} dupes{RESET}"
        if file_skip:
            status_str += f"  {file_skip} skipped"

        print(f"     {status_str}")
        file_results.append((fname, file_new, file_dupes, file_skip))

    except Exception as e:
        log_err(f"  Failed to process {fname}: {e}")


# ── VERIFY ────────────────────────────────────────────────────────────────────

print(f"\n{BOLD}[3/3] Verifying database...{RESET}")
try:
    conn = get_connection()

    # ── Add source_type column if missing (permanent fix for lead origin tracking)
    try:
        conn.execute("ALTER TABLE leads ADD COLUMN source_type TEXT DEFAULT 'event'")
        conn.commit()
        log_ok("Added source_type column to leads")
    except Exception:
        pass  # already exists

    # ── Backfill source_type for existing Clutch leads
    try:
        conn.execute("""
            UPDATE leads SET source_type = 'clutch'
            WHERE id IN (
                SELECT DISTINCT l.id FROM leads l
                LEFT JOIN enrichment e ON e.lead_id = l.id
                LEFT JOIN lead_appearances la ON la.lead_id = l.id
                LEFT JOIN scrape_sessions ss ON ss.id = la.session_id
                WHERE e.industry = 'Agency / Services'
                   OR (e.notes IS NOT NULL AND e.notes LIKE '%rating%')
                   OR (ss.event_url LIKE '%clutch.co%')
            )
        """)
        conn.commit()
        log_ok("Backfilled source_type='clutch' on existing Clutch leads")
    except Exception as e:
        log_err(f"Clutch backfill failed: {e}")

    # Backfill org_id=1 on any rows saved before multi-org migration
    for table in ["leads", "companies", "scrape_sessions",
                  "enrichment", "lead_appearances", "rejections"]:
        try:
            conn.execute(f"UPDATE {table} SET org_id=1 WHERE org_id IS NULL")
        except Exception:
            pass  # table may not have org_id column yet
    conn.commit()
    log_ok("Backfilled org_id=1 on legacy rows")

    lead_count    = conn.execute("SELECT COUNT(*) AS c FROM leads").fetchone()["c"]
    company_count = conn.execute("SELECT COUNT(*) AS c FROM companies").fetchone()["c"]
    appear_count  = conn.execute("SELECT COUNT(*) AS c FROM lead_appearances").fetchone()["c"]
    conn.close()
    log_ok(f"{lead_count:,} leads in database")
    log_ok(f"{company_count:,} companies in database")
    log_ok(f"{appear_count:,} event appearances logged")
except Exception as e:
    log_err(f"Could not verify: {e}")


# ── SUMMARY ───────────────────────────────────────────────────────────────────
print(f"""
{BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}
{GREEN}{BOLD}  Migration Complete{RESET}

  {GREEN}✓ {total_new:,} new leads imported{RESET}
  {YELLOW}→ {total_dupes:,} duplicates skipped{RESET}
    {total_skipped:,} garbage rows skipped

{BOLD}  Refresh your Dashin app — leads will now appear in Inventory.{RESET}
{BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}
""")
