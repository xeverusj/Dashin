"""
dashin_scraper.py — the client-facing desktop launcher (the app they run).

Flow:
  1. Sign-in gate — checks the account is active/paid with the hosted app. If the
     client hasn't paid (deactivated/suspended/lapsed), it refuses to start.
  2. Menu — pick a tool. The chosen scraper opens its own visible browser window,
     the client logs into the target site / clicks START, and results are saved
     locally AND pushed into their dashboard (their token is passed through).

This is the file to package into a one-click .exe (PyInstaller) later.
"""

import os
import sys
import runpy
import subprocess

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

# When frozen by PyInstaller, sys._MEIPASS is the extracted bundle dir (where the
# .py scripts + data live); otherwise it's this file's folder.
_FROZEN = getattr(sys, "frozen", False)
_ROOT = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))

# ── Frozen dispatch ───────────────────────────────────────────────────────────
# A frozen exe can't run `python worker.py` (there's no python). Instead the
# launcher re-invokes ITSELF with `--run-tool <script> <args>` and we runpy the
# bundled script here, before the GUI/menu code loads.
if sys.argv[1:2] == ["--run-tool"]:
    _script = sys.argv[2]
    sys.argv = [_script] + sys.argv[3:]           # tools parse their own argv
    runpy.run_path(os.path.join(_ROOT, _script), run_name="__main__")
    sys.exit(0)

from services.scraper_auth import require_active_account, load_config, login_prompt


def _ensure_browser():
    """First-run: make sure Chromium is available; download it if missing."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            pw.chromium.executable_path  # raises if not installed
        return
    except Exception:
        pass
    print("\n  First run — downloading the browser (one-time, ~150 MB)…")
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"]
                       if not _FROZEN else
                       [sys.executable, "--run-tool", "_pw_install.py"],
                       check=False)
    except Exception as e:
        print(f"  [browser] automatic download failed: {e}")
        print("  If scraping doesn't open a window, run: playwright install chromium")


def _tool_cmd(script: str, extra: list) -> list:
    """Build the subprocess command, frozen-aware."""
    if _FROZEN:
        return [sys.executable, "--run-tool", script] + extra
    return [sys.executable, os.path.join(_ROOT, script)] + extra

TOOLS = [
    ("Universal scraper (any event/directory site)", "worker.py", "url"),
    ("Clutch.co scraper",                             "clutch_scraper.py", "url"),
    ("LinkedIn enricher (find profiles for a CSV)",   "run_enricher.py", "enricher"),
]


def _run_tool(script: str, mode: str, env: dict):
    if mode == "url":
        target = input("  Paste the URL to scrape: ").strip()
        if not target:
            print("  (no URL — cancelled)"); return
        cmd = _tool_cmd(script, [target])
    elif mode == "enricher":
        csv_path = input("  Path to your contacts/companies CSV: ").strip().strip('"')
        if not csv_path or not os.path.exists(csv_path):
            print("  (file not found — cancelled)"); return
        out = os.path.splitext(csv_path)[0] + "_enriched.csv"
        m = input("  Mode — [c]ontacts (have names) or [r]oles (companies only)? ").strip().lower()
        extra = ["--input", csv_path, "--output", out,
                 "--mode", "roles" if m.startswith("r") else "contact"]
        if m.startswith("r"):
            titles = input("  Which titles to search (comma-separated, e.g. CEO,Founder)? ").strip()
            extra += ["--titles", titles or "CEO,Founder"]
        cmd = _tool_cmd("run_enricher.py", extra)
    else:
        return

    print(f"\n  Launching {script} … a browser window will open.\n")
    subprocess.run(cmd, env=env)


def main():
    print("=" * 52)
    print("            DASHIN  ·  Desktop Scraper")
    print("=" * 52)

    # 1) Sign-in / entitlement gate — stops here if the account isn't active.
    acct = require_active_account(interactive=True)

    # Make sure the browser is available (downloads once if needed).
    _ensure_browser()

    # Pass the signed-in credentials down so scrapers push to the right org.
    cfg = load_config()
    env = dict(os.environ)
    env["DASHIN_API_URL"] = cfg.get("api_url", "")
    env["DASHIN_API_TOKEN"] = cfg.get("token", "")

    while True:
        print("\nWhat would you like to run?")
        for i, (label, _, _) in enumerate(TOOLS, 1):
            print(f"  {i}. {label}")
        print("  s. Switch account / re-enter token")
        print("  0. Quit")
        choice = input("\nChoice: ").strip().lower()

        if choice == "0":
            print("Goodbye."); return
        if choice == "s":
            login_prompt()
            acct = require_active_account(interactive=True)
            cfg = load_config()
            env["DASHIN_API_URL"] = cfg.get("api_url", "")
            env["DASHIN_API_TOKEN"] = cfg.get("token", "")
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(TOOLS):
            _, script, mode = TOOLS[int(choice) - 1]
            _run_tool(script, mode, env)
        else:
            print("  (unrecognised choice)")


if __name__ == "__main__":
    main()
