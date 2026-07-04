"""
services/token_service.py — per-org API tokens for the desktop scraper ingest.

The desktop scraper authenticates its pushes with a token tied to one org. We
store only the SHA-256 hash; the raw token is returned once at creation and shown
to the admin then — never recoverable afterwards (regenerate if lost).

Token format: "dsh_" + 40 hex chars, so it's recognizable and easy to spot in
logs/config without being guessable.
"""

import os
import hashlib
from datetime import datetime, timezone

from core.db import get_connection


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def generate_token(org_id: int, label: str = "", created_by: int = None) -> str:
    """Create a new token for an org. Returns the RAW token (store the hash)."""
    raw = "dsh_" + os.urandom(20).hex()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO api_tokens (org_id, token_hash, label, created_by)
               VALUES (?,?,?,?)""",
            (org_id, _hash(raw), (label or "").strip(), created_by))
        conn.commit()
    finally:
        conn.close()
    return raw


def validate_token(token: str) -> int | None:
    """Return the org_id for a valid, non-revoked token, else None. Touches last_used_at."""
    if not token or not token.strip():
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, org_id, revoked FROM api_tokens WHERE token_hash=?",
            (_hash(token.strip()),)).fetchone()
        if not row or row["revoked"]:
            return None
        conn.execute("UPDATE api_tokens SET last_used_at=? WHERE id=?", (_now(), row["id"]))
        conn.commit()
        return row["org_id"]
    finally:
        conn.close()


# Subscription states that are allowed to run the scraper.
_ACTIVE_SUB = {"active", "trial", "trialing", "trialling", ""}


def check_account_active(token: str) -> dict:
    """
    Validate a token AND that the org behind it is entitled to run the scraper.
    Returns {ok, active, org_id, org_name, subscription_status, reason}.

    'active' is False (so the scraper refuses to run) when the token is invalid
    or revoked, the org is deactivated/suspended, or its subscription lapsed —
    this is what stops a client from scraping for free once they stop paying.
    """
    org_id = validate_token(token)
    if not org_id:
        return {"ok": False, "active": False, "org_id": None, "org_name": "",
                "subscription_status": "", "reason": "Invalid or revoked token."}

    conn = get_connection()
    try:
        org = conn.execute(
            """SELECT name, is_active, suspended_at, subscription_status
               FROM organisations WHERE id=?""", (org_id,)).fetchone()
    finally:
        conn.close()
    if not org:
        return {"ok": False, "active": False, "org_id": org_id, "org_name": "",
                "subscription_status": "", "reason": "Organisation not found."}

    sub = (org["subscription_status"] or "").strip().lower()
    if not org["is_active"]:
        reason = "This account is deactivated. Contact your account manager."
    elif org["suspended_at"]:
        reason = "This account is suspended (likely a billing issue)."
    elif sub not in _ACTIVE_SUB:
        reason = f"Subscription is '{sub}'. Renew to keep using the scraper."
    else:
        reason = ""

    active = reason == ""
    return {"ok": True, "active": active, "org_id": org_id,
            "org_name": org["name"], "subscription_status": sub or "active",
            "reason": reason}


def list_tokens(org_id: int) -> list:
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT id, label, created_at, last_used_at, revoked
               FROM api_tokens WHERE org_id=? ORDER BY created_at DESC""",
            (org_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def revoke_token(token_id: int, org_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute("UPDATE api_tokens SET revoked=1 WHERE id=? AND org_id=?",
                     (token_id, org_id))
        conn.commit()
    finally:
        conn.close()
