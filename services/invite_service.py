"""
services/invite_service.py — Dashin Research Platform
Client self-serve signup via invite tokens.
Admin generates a link → client clicks it → sets email + password → gets access.
"""

import secrets
import string
from datetime import datetime, timezone, timedelta
from core.db import get_connection


TOKEN_EXPIRY_DAYS = 7


def generate_token() -> str:
    """Generate a cryptographically secure invite token."""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(48))


def hash_password(password: str) -> str:
    """Hash password using bcrypt (imported from core.auth for consistency)."""
    from core.auth import hash_password as _hash
    return _hash(password)


def create_invite(
    org_id:      int,
    client_id:   int,
    created_by:  int,
    role:        str  = "client_user",
    email:       str  = None,
    expiry_days: int  = TOKEN_EXPIRY_DAYS,
) -> dict:
    """
    Create an invite token for a client user.
    Returns {token, invite_url, expires_at}
    """
    conn    = get_connection()
    token   = generate_token()
    expires = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=expiry_days)).isoformat()
    now     = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    conn.execute("""
        INSERT INTO invite_tokens
            (org_id, token, client_id, role, email,
             created_by, expires_at, created_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, (org_id, token, client_id, role, email,
          created_by, expires, now))
    conn.commit()

    # Build invite URL — uses env var or falls back to localhost
    import os
    base_url = os.getenv("DASHIN_BASE_URL", "http://localhost:8501")
    invite_url = f"{base_url}/?invite={token}"

    conn.close()
    return {
        "token":      token,
        "invite_url": invite_url,
        "expires_at": expires,
    }


def validate_token(token: str) -> dict | None:
    """
    Validate an invite token.
    Returns token row dict if valid and unused, else None.
    """
    conn = get_connection()
    row  = conn.execute("""
        SELECT t.*, c.name AS client_name, o.name AS org_name
        FROM invite_tokens t
        LEFT JOIN clients c ON c.id = t.client_id
        LEFT JOIN organisations o ON o.id = t.org_id
        WHERE t.token=? AND t.used_at IS NULL
    """, (token,)).fetchone()
    conn.close()

    if not row:
        return None

    # Check expiry
    expires = datetime.fromisoformat(row["expires_at"])
    if datetime.now(timezone.utc).replace(tzinfo=None) > expires:
        return None

    return dict(row)


def redeem_token(token: str, name: str,
                 email: str, password: str) -> dict:
    """
    Complete signup — create user account and mark token used.
    Returns {success, user_id, error}
    """
    conn = get_connection()

    # Validate
    invite = conn.execute("""
        SELECT * FROM invite_tokens
        WHERE token=? AND used_at IS NULL
    """, (token,)).fetchone()

    if not invite:
        conn.close()
        return {"success": False, "error": "Invalid or expired invite link."}

    expires = datetime.fromisoformat(invite["expires_at"])
    if datetime.now(timezone.utc).replace(tzinfo=None) > expires:
        conn.close()
        return {"success": False, "error": "This invite link has expired."}

    # Check email not already registered
    existing = conn.execute(
        "SELECT id FROM users WHERE email=?", (email.lower().strip(),)
    ).fetchone()
    if existing:
        conn.close()
        return {"success": False,
                "error": "An account with this email already exists."}

    # Validate password
    if len(password) < 8:
        conn.close()
        return {"success": False,
                "error": "Password must be at least 8 characters."}

    now     = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    pw_hash = hash_password(password)

    # Create user
    cur = conn.execute("""
        INSERT INTO users
            (org_id, name, email, password, role,
             client_id, is_active, created_at)
        VALUES (?,?,?,?,?,?,1,?)
    """, (invite["org_id"], name.strip(),
          email.lower().strip(), pw_hash,
          invite["role"], invite["client_id"], now))

    user_id = cur.lastrowid

    # Mark token used
    conn.execute("""
        UPDATE invite_tokens
        SET used_at=?, used_by=?
        WHERE token=?
    """, (now, user_id, token))

    conn.commit()
    conn.close()

    return {"success": True, "user_id": user_id, "error": None}


def get_pending_invites(org_id: int, client_id: int = None) -> list:
    """List all unused, non-expired invites for an org."""
    conn = get_connection()
    q    = """
        SELECT t.*, c.name AS client_name,
               u.name AS created_by_name
        FROM invite_tokens t
        LEFT JOIN clients c ON c.id = t.client_id
        LEFT JOIN users u   ON u.id = t.created_by
        WHERE t.org_id=? AND t.used_at IS NULL
          AND t.expires_at > datetime('now')
    """
    params = [org_id]
    if client_id:
        q += " AND t.client_id=?"
        params.append(client_id)
    q += " ORDER BY t.created_at DESC"

    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def revoke_token(token_id: int, org_id: int):
    """Revoke an invite by marking it as expired."""
    conn = get_connection()
    conn.execute("""
        UPDATE invite_tokens
        SET expires_at = datetime('now', '-1 second')
        WHERE id=? AND org_id=?
    """, (token_id, org_id))
    conn.commit()
    conn.close()
