"""
services/client_portal_service.py — client-portal extras.

Small helpers for the two client-portal additions:
  • email mailbox credentials the agency manages per client (client sees them
    read-only in their portal), and
  • the email templates a client's campaigns use (promoted to its own page).

Kept as plain functions; each opens/closes its own connection.
"""

from core.db import get_connection
from core import crypto


# ── Email accounts ────────────────────────────────────────────────────────────

def list_email_accounts(org_id: int, client_id: int) -> list:
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT * FROM client_email_accounts
               WHERE org_id=? AND client_id=? ORDER BY created_at DESC""",
            (org_id, client_id)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            # Passwords are stored encrypted at rest; decrypt for display.
            d["password"] = crypto.decrypt(d.get("password", ""))
            out.append(d)
        return out
    finally:
        conn.close()


def add_email_account(org_id: int, client_id: int, email_address: str,
                      password: str = "", label: str = "", provider: str = "",
                      webmail_url: str = "", created_by: int = None) -> int:
    conn = get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO client_email_accounts
               (org_id, client_id, label, email_address, password, provider,
                webmail_url, created_by)
               VALUES (?,?,?,?,?,?,?,?)""",
            (org_id, client_id, label.strip(), email_address.strip(),
             crypto.encrypt(password),   # encrypt at rest — never store plaintext
             provider.strip(), webmail_url.strip(), created_by))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def delete_email_account(account_id: int, org_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM client_email_accounts WHERE id=? AND org_id=?",
                     (account_id, org_id))
        conn.commit()
    finally:
        conn.close()


# ── Templates ─────────────────────────────────────────────────────────────────

def get_client_templates(org_id: int, client_id: int) -> list:
    """Email templates shared with this client (from campaign_templates)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT * FROM campaign_templates
               WHERE org_id=? AND (client_id=? OR client_id IS NULL)
               ORDER BY created_at DESC""",
            (org_id, client_id)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        # campaign_templates schema may not have client_id in older DBs
        try:
            rows = conn.execute(
                "SELECT * FROM campaign_templates WHERE org_id=? ORDER BY id DESC",
                (org_id,)).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []
    finally:
        conn.close()
