"""
migrate_passwords.py — One-time password migration to bcrypt.

Scans all users in the DB. Any user whose password_hash looks like a
legacy SHA-256 hex string (64 chars, no $2b$ prefix) is flagged for
a mandatory password reset on next login.

Usage:
    python migrate_passwords.py [--yes]

Flags:
    --yes   Skip the confirmation prompt and run immediately.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from core.db import get_connection, init_db

init_db()


def is_sha256_hash(h: str) -> bool:
    """Returns True if the string looks like a raw SHA-256 hex digest."""
    if not h:
        return False
    return len(h) == 64 and all(c in '0123456789abcdef' for c in h.lower()) and not h.startswith('$')


def run(yes: bool = False):
    conn = get_connection()

    # Add must_reset_password column if missing
    cols = {r['name'] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if 'must_reset_password' not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN must_reset_password INTEGER DEFAULT 0")
        conn.commit()
        print("  ✅ Added must_reset_password column to users table")

    # Find all users with legacy SHA-256 hashes
    users = conn.execute(
        "SELECT id, name, email, password FROM users"
    ).fetchall()

    legacy_users = [u for u in users if is_sha256_hash(u.get('password', '') or '')]

    print(f"\n{'='*60}")
    print(f"  Password Migration Report")
    print(f"{'='*60}")
    print(f"  Total users:          {len(users)}")
    print(f"  Legacy SHA-256 hashes: {len(legacy_users)}")
    print(f"  Already bcrypt:        {len(users) - len(legacy_users)}")

    if not legacy_users:
        print("\n  ✅ All passwords are already bcrypt. Nothing to do.")
        conn.close()
        return

    print(f"\n  Users that will be flagged for password reset:")
    for u in legacy_users:
        print(f"    [{u['id']:4}] {u['name']:<30}  {u['email']}")

    print(f"\n  These users will see a 'set new password' prompt on next login.")

    if not yes:
        answer = input("\n  Proceed? (y/n): ").strip().lower()
        if answer != 'y':
            print("  Aborted.")
            conn.close()
            return

    # Flag all legacy-hash users for password reset
    ids = [u['id'] for u in legacy_users]
    conn.executemany(
        "UPDATE users SET must_reset_password=1 WHERE id=?",
        [(i,) for i in ids]
    )
    conn.commit()
    conn.close()

    print(f"\n  ✅ Flagged {len(legacy_users)} users for password reset on next login.")
    print(f"  ✅ Their existing SHA-256 hashes remain valid for login until they reset.\n")


if __name__ == '__main__':
    run(yes='--yes' in sys.argv)
