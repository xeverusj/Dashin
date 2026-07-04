"""
check_and_fix.py — Run this from your project root to diagnose and fix the source_type issue.

Usage:
    python check_and_fix.py [--org-id N] [--dry-run] [--yes]

Flags:
    --org-id N   Only fix data for org with id=N. Default: all orgs.
    --dry-run    Show what would change without making any changes.
    --yes        Skip the confirmation prompt.
"""
import sys
import datetime
import logging
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# ── Parse args ────────────────────────────────────────────────────────────────
ORG_ID   = None
DRY_RUN  = '--dry-run' in sys.argv
YES      = '--yes' in sys.argv

for i, arg in enumerate(sys.argv):
    if arg == '--org-id' and i + 1 < len(sys.argv):
        try:
            ORG_ID = int(sys.argv[i + 1])
        except ValueError:
            print(f"ERROR: --org-id must be an integer, got: {sys.argv[i+1]}")
            sys.exit(1)

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_FILE = ROOT / 'fix_log.txt'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)

from core.db import get_connection, init_db
init_db()
conn = get_connection()
now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()

print("\n" + "="*60)
print(f"  Dashin DB Diagnostic & Fix")
if DRY_RUN:
    print(f"  MODE: DRY RUN — no changes will be made")
if ORG_ID:
    print(f"  SCOPE: org_id = {ORG_ID}")
else:
    print(f"  SCOPE: all orgs")
print("="*60 + "\n")


# ── Helper ────────────────────────────────────────────────────────────────────
def _org_where(alias: str = "") -> tuple:
    """Return (WHERE clause fragment, params list) for optional org filter."""
    prefix = f"{alias}." if alias else ""
    if ORG_ID is not None:
        return f"AND {prefix}org_id=?", [ORG_ID]
    return "", []


# ── [1] Check if source_type column exists ────────────────────────────────────
log.info("[1] Checking leads table columns...")
cols = conn.execute("PRAGMA table_info(leads)").fetchall()
col_names = [c["name"] for c in cols]
log.info(f"    Columns: {col_names}")

if "source_type" not in col_names:
    log.info("    source_type column MISSING — adding it now...")
    if not DRY_RUN:
        conn.execute("ALTER TABLE leads ADD COLUMN source_type TEXT DEFAULT 'event'")
        conn.commit()
        log.info("    source_type column added")
    else:
        log.info("    [DRY RUN] Would add: ALTER TABLE leads ADD COLUMN source_type TEXT DEFAULT 'event'")
else:
    log.info("    source_type column exists")


# ── [2] Current source_type distribution ─────────────────────────────────────
log.info("\n[2] Checking source_type distribution...")
frag, params = _org_where()
rows = conn.execute(
    f"SELECT source_type, COUNT(*) as c FROM leads WHERE 1=1 {frag} GROUP BY source_type",
    params
).fetchall()
for r in rows:
    log.info(f"    source_type={r['source_type']!r:20} -> {r['c']:,} leads")


# ── [3] Enrichment / industry check ──────────────────────────────────────────
log.info("\n[3] Checking enrichment industry tags...")
frag, params = _org_where("l")
agency_count = conn.execute(
    f"SELECT COUNT(*) as c FROM enrichment e JOIN leads l ON l.id=e.lead_id "
    f"WHERE e.industry='Agency / Services' {frag}",
    params
).fetchone()["c"]
log.info(f"    'Agency / Services' enrichment rows: {agency_count:,}")

notes_count = conn.execute(
    f"SELECT COUNT(*) as c FROM enrichment e JOIN leads l ON l.id=e.lead_id "
    f"WHERE e.notes IS NOT NULL AND e.notes LIKE '%rating%' {frag}",
    params
).fetchone()["c"]
log.info(f"    Notes with 'rating' field: {notes_count:,}")


# ── [4] Import Clutch CSV companies ──────────────────────────────────────────
log.info("\n[4] Importing Clutch companies from CSV files into database...")

import re as _re
try:
    import pandas as pd
    import json

    sessions_folder = ROOT / "data" / "system" / "sessions"
    clutch_csvs = list(sessions_folder.glob("clutch_*.csv"))
    log.info(f"    Found {len(clutch_csvs)} Clutch CSV file(s)")

    total_imported = 0
    total_skipped  = 0

    # Determine target orgs
    if ORG_ID is not None:
        target_orgs = [ORG_ID]
    else:
        org_rows = conn.execute("SELECT id FROM organisations WHERE is_active=1").fetchall()
        target_orgs = [r["id"] for r in org_rows]

    log.info(f"    Target orgs: {target_orgs}")

    for csv_path in clutch_csvs:
        try:
            df = pd.read_csv(csv_path)
            if "company_name" not in df.columns:
                log.info(f"    Skipping {csv_path.name} — no company_name column")
                continue

            log.info(f"\n    Processing {csv_path.name} ({len(df)} rows) for orgs {target_orgs}...")

            for target_org_id in target_orgs:
                imported = 0
                skipped  = 0

                for _, row in df.iterrows():
                    name = str(row.get("company_name", "")).strip()
                    if not name or name.lower() in ("nan", "none", ""):
                        continue

                    nk = _re.sub(r"[^a-z0-9]", "", name.lower())
                    if not nk:
                        continue

                    existing = conn.execute(
                        "SELECT id FROM leads WHERE name_key=? AND org_id=?",
                        (nk, target_org_id)
                    ).fetchone()

                    if existing:
                        if not DRY_RUN:
                            conn.execute(
                                "UPDATE leads SET source_type='clutch' WHERE name_key=? AND org_id=?",
                                (nk, target_org_id)
                            )
                        skipped += 1
                        continue

                    co_existing = conn.execute(
                        "SELECT id FROM companies WHERE name_key=? AND org_id=?",
                        (nk, target_org_id)
                    ).fetchone()

                    if co_existing:
                        company_id = co_existing["id"]
                    else:
                        if not DRY_RUN:
                            cur = conn.execute(
                                "INSERT INTO companies (org_id, name, name_key) VALUES (?,?,?)",
                                (target_org_id, name, nk)
                            )
                            company_id = cur.lastrowid
                        else:
                            company_id = None

                    location = str(row.get("location", "")).strip()
                    services = str(row.get("top_services", "")).strip()

                    if not DRY_RUN:
                        cur = conn.execute("""
                            INSERT OR IGNORE INTO leads
                                (org_id, full_name, name_key, title, company_id,
                                 status, source_type, first_seen_at, last_seen_at, times_seen)
                            VALUES (?, ?, ?, ?, ?, 'new', 'clutch', ?, ?, 1)
                        """, (target_org_id, name, nk,
                              services[:100] if services and services != "nan" else None,
                              company_id, now, now))

                        if cur.lastrowid:
                            lead_id = cur.lastrowid
                            meta = {
                                "rating":      str(row.get("rating", "")).strip(),
                                "reviews":     str(row.get("reviews", "")).strip(),
                                "min_budget":  str(row.get("min_budget", "")).strip(),
                                "hourly_rate": str(row.get("hourly_rate", "")).strip(),
                                "team_size":   str(row.get("team_size", "")).strip(),
                                "clutch_url":  str(row.get("clutch_url", "")).strip(),
                                "website":     str(row.get("website", "")).strip(),
                            }
                            conn.execute("""
                                INSERT OR IGNORE INTO enrichment
                                    (lead_id, country, industry, notes, enriched_at)
                                VALUES (?, ?, 'Agency / Services', ?, ?)
                            """, (lead_id,
                                  location if location and location != "nan" else None,
                                  json.dumps(meta), now))
                            imported += 1
                        else:
                            skipped += 1
                    else:
                        imported += 1  # count as "would import" in dry-run

                if not DRY_RUN:
                    conn.commit()

                prefix = "[DRY RUN] Would import" if DRY_RUN else "Imported"
                log.info(f"    org_id={target_org_id}: {prefix} {imported} new, {skipped} already exist")
                total_imported += imported
                total_skipped  += skipped

        except Exception as e:
            log.error(f"    ERROR processing {csv_path.name}: {e}", exc_info=True)

    log.info(f"\n    Total {'would import' if DRY_RUN else 'imported'}: {total_imported}")
    log.info(f"    Total skipped: {total_skipped}")

except ImportError:
    log.warning("    pandas not installed — skipping CSV import step")


# ── [5] Pre-flight report & confirmation ─────────────────────────────────────
if not DRY_RUN and not YES:
    print(f"\n{'='*60}")
    print(f"  About to apply the changes above.")
    answer = input("  Proceed? (y/n): ").strip().lower()
    if answer != 'y':
        conn.close()
        print("  Aborted. No changes were committed.")
        sys.exit(0)


# ── [6] Final distribution ────────────────────────────────────────────────────
log.info("\n[5] Final source_type distribution:")
frag, params = _org_where()
rows = conn.execute(
    f"SELECT source_type, COUNT(*) as c FROM leads WHERE 1=1 {frag} GROUP BY source_type",
    params
).fetchall()
for r in rows:
    log.info(f"    source_type={r['source_type']!r:20} -> {r['c']:,} leads")

conn.close()

print("\n" + "="*60)
if DRY_RUN:
    print("  Dry run complete. No changes made.")
else:
    print("  Done. Restart Streamlit now.")
print("="*60 + "\n")
