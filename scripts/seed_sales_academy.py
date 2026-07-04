"""
scripts/seed_sales_academy.py

Seeds the DB with:
- Sales Academy as an agency org (org_type='agency')
- One org_admin user for Sales Academy
- 2 test client orgs under Sales Academy

Run from project root:
    python scripts/seed_sales_academy.py
"""

import sys
import os
from pathlib import Path
from datetime import datetime

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.db import init_db, migrate_db, ensure_defaults, get_connection


def seed():
    print("Initialising database...")
    init_db()
    migrate_db()
    ensure_defaults()

    conn = get_connection()
    now = datetime.utcnow().isoformat()

    # ── 1. Create Sales Academy org ───────────────────────────────────────────
    existing = conn.execute(
        "SELECT id FROM organisations WHERE name='Sales Academy'"
    ).fetchone()

    if existing:
        agency_id = existing['id']
        print(f"  ✓ Sales Academy already exists (id={agency_id})")
    else:
        cur = conn.execute("""
            INSERT INTO organisations
                (name, slug, tier, org_type, subscription_tier,
                 subscription_status, ai_budget_usd, billing_day,
                 max_users, max_clients, max_leads,
                 is_active, onboarded_at, created_at)
            VALUES ('Sales Academy', 'sales-academy', 'growth', 'agency',
                    'growth', 'active',
                    20.0, 1,
                    15, 10, 5000,
                    1, ?, ?)
        """, (now, now))
        agency_id = cur.lastrowid
        conn.commit()
        print(f"  ✓ Sales Academy created (id={agency_id})")

    # ── 2. Create agency admin user ───────────────────────────────────────────
    try:
        import bcrypt as _bcrypt
        pw_hash = _bcrypt.hashpw(b'changeme123', _bcrypt.gensalt()).decode('utf-8')
    except ImportError:
        import hashlib
        pw_hash = hashlib.sha256(b'changeme123').hexdigest()

    existing_user = conn.execute(
        "SELECT id FROM users WHERE email='admin@salesacademy.com'"
    ).fetchone()

    if existing_user:
        print(f"  ✓ Admin user already exists")
    else:
        conn.execute("""
            INSERT INTO users
                (org_id, name, email, password, role,
                 is_active, must_reset_password, onboarded_at, created_at)
            VALUES (?, 'Sales Academy Admin', 'admin@salesacademy.com', ?,
                    'org_admin', 1, 1, NULL, ?)
        """, (agency_id, pw_hash, now))
        conn.commit()
        print(f"  ✓ Admin user created: admin@salesacademy.com / changeme123")
        print(f"    (must_reset_password=1 — they must change on first login)")

    # ── 3. Create 2 test client orgs under Sales Academy ─────────────────────
    client_orgs = [
        {
            "name":  "Test Client Alpha",
            "slug":  "test-client-alpha",
            "email": "admin@alpha-client.com",
        },
        {
            "name":  "Test Client Beta",
            "slug":  "test-client-beta",
            "email": "admin@beta-client.com",
        },
    ]

    for co in client_orgs:
        existing_co = conn.execute(
            "SELECT id FROM organisations WHERE name=?", (co["name"],)
        ).fetchone()

        if existing_co:
            co_id = existing_co['id']
            print(f"  ✓ {co['name']} already exists (id={co_id})")
        else:
            cur2 = conn.execute("""
                INSERT INTO organisations
                    (name, slug, tier, org_type, parent_org_id,
                     subscription_tier, subscription_status,
                     ai_budget_usd, max_users, max_clients, max_leads,
                     is_active, created_at)
                VALUES (?, ?, 'starter', 'client', ?,
                        'client_direct', 'active',
                        0, 3, 0, 100000,
                        1, ?)
            """, (co["name"], co["slug"], agency_id, now))
            co_id = cur2.lastrowid
            conn.commit()
            print(f"  ✓ {co['name']} created (id={co_id})")

        # Create a client_admin user for the client org
        existing_cu = conn.execute(
            "SELECT id FROM users WHERE email=?", (co["email"],)
        ).fetchone()

        if not existing_cu:
            conn.execute("""
                INSERT INTO users
                    (org_id, name, email, password, role,
                     is_active, must_reset_password, created_at)
                VALUES (?, ?, ?, ?, 'client_admin', 1, 1, ?)
            """, (co_id, f"{co['name']} Admin", co["email"], pw_hash, now))
            conn.commit()
            print(f"    ✓ Client admin: {co['email']} / changeme123")

    conn.close()

    print()
    print("=" * 50)
    print("✅ Sales Academy seeded successfully")
    print("=" * 50)
    print()
    print(f"  Agency org_id : {agency_id}")
    print()
    print("  Login credentials:")
    print(f"  Agency admin  : admin@salesacademy.com / changeme123")
    print(f"  Alpha client  : admin@alpha-client.com / changeme123")
    print(f"  Beta client   : admin@beta-client.com / changeme123")
    print()
    print("  All accounts have must_reset_password=1")
    print("  They will be prompted to set a new password on first login.")
    print()


if __name__ == "__main__":
    seed()
