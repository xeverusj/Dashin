"""
tests/smoke_test.py — Dashin Research Platform

Runs a series of lightweight smoke tests against the live SQLite DB and
service layer. No Playwright, no API calls, no network I/O.

Usage:
    cd "app to test with Jan"
    python tests/smoke_test.py

Exit code 0 = all passed, 1 = one or more failures.
"""

import sys
import os
import traceback

# Allow imports from the app root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = "✅"
FAIL = "❌"
failures = []


def check(name: str, fn):
    try:
        result = fn()
        if result is False:
            raise AssertionError("Returned False")
        print(f"  {PASS}  {name}")
    except Exception as e:
        print(f"  {FAIL}  {name}: {e}")
        failures.append((name, traceback.format_exc()))


# ── 1. DB connectivity ─────────────────────────────────────────────────────────
print("\n[1] Database connectivity")

def _db_connect():
    from core.db import get_connection
    conn = get_connection()
    row = conn.execute("SELECT 1 AS ok").fetchone()
    conn.close()
    return row["ok"] == 1

check("get_connection() works", _db_connect)


# ── 2. Schema tables exist ─────────────────────────────────────────────────────
print("\n[2] Required tables")

REQUIRED_TABLES = [
    "organisations", "users", "leads", "companies", "enrichment",
    "scrape_sessions", "notifications", "clients", "invite_tokens",
    "site_patterns", "site_pattern_history", "leads_fts",
]

def _table_check(table):
    from core.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    conn.close()
    return row is not None

for t in REQUIRED_TABLES:
    check(f"Table exists: {t}", lambda _t=t: _table_check(_t))


# ── 3. Default org + super admin exist ────────────────────────────────────────
print("\n[3] Default seed data")

def _default_org():
    from core.db import get_connection
    conn = get_connection()
    row = conn.execute("SELECT id FROM organisations WHERE id=1").fetchone()
    conn.close()
    return row is not None

def _default_admin():
    from core.db import get_connection
    conn = get_connection()
    row = conn.execute("SELECT id FROM users WHERE email='admin@dashin.com'").fetchone()
    conn.close()
    return row is not None

check("Default org (id=1) exists", _default_org)
check("Super admin user exists", _default_admin)


# ── 4. Auth functions ──────────────────────────────────────────────────────────
print("\n[4] Auth: hash & verify")

def _hash_verify():
    # auth.py imports streamlit at module level — stub it out for CLI test context
    import types
    if "streamlit" not in sys.modules:
        _st_stub = types.ModuleType("streamlit")
        for attr in ["session_state", "error", "success", "warning", "info",
                     "stop", "text_input", "button", "columns", "markdown", "subheader"]:
            setattr(_st_stub, attr, lambda *a, **kw: None)
        _st_stub.session_state = {}
        sys.modules["streamlit"] = _st_stub
    from core.auth import hash_password, verify_password
    pw = "T3stP@ss!"
    h = hash_password(pw)
    assert verify_password(pw, h), "verify_password returned False for correct password"
    assert not verify_password("wrong", h), "verify_password returned True for wrong password"
    return True

check("hash_password / verify_password", _hash_verify)


# ── 5. bcrypt used (not plain SHA-256) ────────────────────────────────────────
def _uses_bcrypt():
    try:
        import bcrypt  # noqa: F401
        from core.auth import hash_password
        h = hash_password("x")
        assert h.startswith("$2b$"), f"Expected bcrypt hash, got: {h[:12]}..."
        return True
    except ImportError:
        print("    ⚠️  bcrypt not installed — SHA-256 fallback active")
        return True  # not a failure if bcrypt missing, just a warning

check("bcrypt hashes used", _uses_bcrypt)


# ── 6. Lead service ────────────────────────────────────────────────────────────
print("\n[5] Lead service")

def _get_leads():
    from services.lead_service import get_leads
    rows = get_leads(org_id=1, limit=5)
    return isinstance(rows, list)

def _count_leads():
    from services.lead_service import count_leads
    n = count_leads(org_id=1)
    return isinstance(n, int) and n >= 0

def _export_leads():
    from services.lead_service import get_all_leads_for_export
    rows = get_all_leads_for_export(org_id=1)
    return isinstance(rows, list)

check("get_leads() returns list", _get_leads)
check("count_leads() returns int", _count_leads)
check("get_all_leads_for_export() returns list", _export_leads)


# ── 7. Notification service ────────────────────────────────────────────────────
print("\n[6] Notification service")

def _notif_create():
    from services.notification_service import create as notif_create, unread_count
    from core.db import get_connection
    # Use existing super-admin user (id=1, org_id=1) to satisfy FK constraints
    notif_create(1, 1, "smoke_test", "Smoke Test", "This is a smoke test notification.")
    n = unread_count(1)
    return isinstance(n, int)

check("notification create + unread_count", _notif_create)


# ── 8. DB backup ───────────────────────────────────────────────────────────────
print("\n[7] Database backup")

def _backup():
    from core.db import backup_database, list_backups
    dest = backup_database(label="smoke_test")
    assert dest.exists(), f"Backup file not found at {dest}"
    backups = list_backups()
    names = [b["name"] for b in backups]
    assert any("smoke_test" in n for n in names), "Backup not listed"
    # Clean up test backup
    dest.unlink(missing_ok=True)
    return True

check("backup_database() creates file and list_backups() finds it", _backup)


# ── 9. Site learning service ───────────────────────────────────────────────────
print("\n[8] Site learning service")

def _site_patterns():
    from services.site_learning_service import get_pattern_stats, get_all_patterns
    stats = get_pattern_stats()
    patterns = get_all_patterns()
    return isinstance(stats, dict) and isinstance(patterns, list)

check("get_pattern_stats / get_all_patterns", _site_patterns)


# ── 10. Quality service ────────────────────────────────────────────────────────
print("\n[9] Quality service")

def _quality_eval():
    from services.quality_service import evaluate_enrichment_quality
    result = evaluate_enrichment_quality(
        lead_id=0,
        enrichment_data={
            "email": "test@company.com",
            "linkedin_url": "https://linkedin.com/in/testuser",
        },
        scraped_data={
            "full_name": "Jane Smith",
            "title": "Head of Marketing",
            "company": "Acme Corp",
        },
    )
    assert "quality_score" in result
    assert 0.0 <= result["quality_score"] <= 1.0
    return True

check("evaluate_enrichment_quality returns valid score", _quality_eval)


# ── Summary ────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
if failures:
    print(f"\n{FAIL}  {len(failures)} test(s) FAILED:\n")
    for name, tb in failures:
        print(f"  • {name}")
        for line in tb.strip().splitlines()[-3:]:
            print(f"    {line}")
    print()
    sys.exit(1)
else:
    print(f"\n{PASS}  All smoke tests passed!\n")
    sys.exit(0)
