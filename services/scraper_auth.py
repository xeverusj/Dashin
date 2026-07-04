"""
services/scraper_auth.py — client-side activation gate for the desktop scraper.

The scraper checks in with the hosted app before it runs. If the token is
missing/invalid, or the org is deactivated/suspended/unpaid, the scraper refuses
to start — so a client who stops paying can't keep scraping for free.

Config is stored per-user at ~/.dashin/config.json:
    {"api_url": "https://app.example.com", "token": "dsh_..."}

Falls back to env vars DASHIN_API_URL / DASHIN_API_TOKEN if the file is absent.
Fails CLOSED: if the server can't be reached, activation is denied (prevents an
offline bypass), with a clear message.
"""

import os
import json
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None

CONFIG_DIR = Path.home() / ".dashin"
CONFIG_FILE = CONFIG_DIR / "config.json"


def load_config() -> dict:
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    # env overrides / fills gaps
    cfg.setdefault("api_url", os.environ.get("DASHIN_API_URL", "").strip())
    cfg.setdefault("token", os.environ.get("DASHIN_API_TOKEN", "").strip())
    return cfg


def save_config(api_url: str, token: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps({"api_url": api_url.strip(), "token": token.strip()}, indent=2),
        encoding="utf-8")


def check(api_url: str, token: str, timeout: int = 15) -> dict:
    """
    Ask the server whether this token belongs to an active, paid account.
    Returns the server's dict, or a fail-closed dict if unreachable.
    """
    if not api_url or not token:
        return {"ok": False, "active": False, "reason": "No server URL or token configured."}
    if requests is None:
        return {"ok": False, "active": False, "reason": "The 'requests' package is missing."}
    endpoint = api_url.rstrip("/") + "/api/auth/validate"
    try:
        r = requests.post(endpoint, headers={"Authorization": f"Bearer {token}"}, timeout=timeout)
        if r.status_code == 200:
            return r.json()
        return {"ok": False, "active": False,
                "reason": f"Server returned {r.status_code}. Check your URL/token."}
    except Exception as e:
        # Fail closed — can't verify entitlement, so don't allow the run.
        return {"ok": False, "active": False,
                "reason": f"Couldn't reach the Dashin server to verify your subscription "
                          f"({str(e)[:60]}). Check your internet connection."}


def login_prompt() -> dict:
    """Interactively collect the server URL + token and save them."""
    print("\n=== Dashin Scraper — Sign in ===")
    cfg = load_config()
    default_url = cfg.get("api_url", "")
    url = input(f"Dashin app URL{f' [{default_url}]' if default_url else ''}: ").strip() or default_url
    token = input("Your access token (from your account manager): ").strip()
    if url and token:
        save_config(url, token)
    return {"api_url": url, "token": token}


def require_active_account(interactive: bool = True) -> dict:
    """
    Gate the scraper. Returns the account dict when active; otherwise prints why
    and raises SystemExit so the scraper stops. Prompts for credentials once if
    they're missing (interactive mode).
    """
    cfg = load_config()
    if interactive and (not cfg.get("api_url") or not cfg.get("token")):
        cfg = login_prompt()

    acct = check(cfg.get("api_url", ""), cfg.get("token", ""))

    if acct.get("active"):
        print(f"✓ Signed in — {acct.get('org_name','your account')} "
              f"(subscription: {acct.get('subscription_status','active')}).")
        return acct

    # Not active — give the client one chance to re-enter credentials.
    print(f"\n✗ Cannot start: {acct.get('reason', 'account not active.')}")
    if interactive and acct.get("ok") is False and "token" in (acct.get("reason", "").lower()):
        cfg = login_prompt()
        acct = check(cfg.get("api_url", ""), cfg.get("token", ""))
        if acct.get("active"):
            print(f"✓ Signed in — {acct.get('org_name','your account')}.")
            return acct
        print(f"\n✗ Still cannot start: {acct.get('reason','account not active.')}")

    raise SystemExit(1)
