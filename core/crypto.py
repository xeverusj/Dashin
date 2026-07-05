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
import secrets as _secrets

try:
    from cryptography.fernet import Fernet
    _AVAILABLE = True
except Exception:
    _AVAILABLE = False

_PREFIX = "enc:v1:"
_cached_secret = None


def _secret_file():
    """Keyfile path — kept next to the DB so it lives on the persistent volume
    and survives restarts (the key MUST stay stable or encrypted data is lost)."""
    try:
        from core.db import DB_PATH
        return DB_PATH.parent / ".dashin_secret"
    except Exception:
        return None


def _get_secret() -> str:
    """
    Resolve the encryption secret, in priority order:
      1. DASHIN_SECRET_KEY env var (production — set by the operator)
      2. a persisted keyfile on the data volume (auto-managed)
      3. generate a strong key once, persist it to the keyfile, and use it
    This means there is never a hardcoded/insecure fallback, and no manual setup
    is required for it to be secure out of the box.
    """
    global _cached_secret
    if _cached_secret:
        return _cached_secret

    env = os.environ.get("DASHIN_SECRET_KEY", "").strip()
    if env:
        _cached_secret = env
        return env

    path = _secret_file()
    if path is not None:
        try:
            if path.exists():
                _cached_secret = path.read_text(encoding="utf-8").strip()
                if _cached_secret:
                    return _cached_secret
            # Generate once and persist with restrictive permissions.
            path.parent.mkdir(parents=True, exist_ok=True)
            gen = _secrets.token_urlsafe(48)
            path.write_text(gen, encoding="utf-8")
            try:
                os.chmod(path, 0o600)
            except Exception:
                pass
            logging.info("[crypto] generated a new persistent encryption key at %s", path)
            _cached_secret = gen
            return gen
        except Exception as e:
            logging.warning("[crypto] could not read/write keyfile (%s) — "
                            "falling back to a process-random key for this run", e)

    # Last resort (e.g. read-only FS): a random key for this process only.
    _cached_secret = _secrets.token_urlsafe(48)
    return _cached_secret


def _fernet():
    key = base64.urlsafe_b64encode(hashlib.sha256(_get_secret().encode()).digest())
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
