"""
core/crypto.py — reversible encryption for secrets that must be displayed back
(e.g. client mailbox passwords shown read-only in the client portal).

These can't be hashed — the portal has to show the actual value — so they are
encrypted at rest with a key derived from the DASHIN_SECRET_KEY environment
variable. A database dump alone therefore does NOT expose the credentials; an
attacker also needs the key, which lives only in the server's environment.

Values are stored with an "enc:v1:" prefix so decrypt() can transparently pass
through legacy plaintext rows (written before encryption was added).
"""

import os
import base64
import hashlib
import logging

try:
    from cryptography.fernet import Fernet
    _AVAILABLE = True
except Exception:
    _AVAILABLE = False

_PREFIX = "enc:v1:"
_warned = False


def _fernet():
    global _warned
    secret = os.environ.get("DASHIN_SECRET_KEY", "").strip()
    if not secret:
        if not _warned:
            logging.warning("[crypto] DASHIN_SECRET_KEY not set — using an insecure "
                            "development key. Set DASHIN_SECRET_KEY in production.")
            _warned = True
        secret = "dashin-dev-insecure-key-change-me"
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt(text: str) -> str:
    """Encrypt a secret for storage. Returns plaintext unchanged if the crypto
    library is unavailable (degrades gracefully rather than losing data)."""
    if not text or not _AVAILABLE:
        return text or ""
    if isinstance(text, str) and text.startswith(_PREFIX):
        return text  # already encrypted
    return _PREFIX + _fernet().encrypt(text.encode()).decode()


def decrypt(text: str) -> str:
    """Decrypt a stored secret. Legacy plaintext (no prefix) is returned as-is."""
    if not text or not isinstance(text, str) or not text.startswith(_PREFIX):
        return text or ""
    if not _AVAILABLE:
        return text
    try:
        return _fernet().decrypt(text[len(_PREFIX):].encode()).decode()
    except Exception:
        logging.warning("[crypto] failed to decrypt a value (wrong key?) — returning blank")
        return ""


def is_available() -> bool:
    return _AVAILABLE
