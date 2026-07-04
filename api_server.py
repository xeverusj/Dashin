"""
api_server.py — lightweight ingest API for desktop scraper pushes.

Runs ALONGSIDE the Streamlit app (Streamlit can't serve custom POST endpoints).
The desktop scraper's push_to_dashin() posts scraped rows here with a per-org
Bearer token; validated rows are written into that org's inventory.

Endpoints:
  GET  /health              → {"ok": true}
  POST /api/leads/import    → body {"rows":[...], "source":"..."}, header
                              Authorization: Bearer <token>. Returns an import
                              summary. Token maps the push to exactly one org.

Run:
  python api_server.py                 # defaults to 0.0.0.0:8000
  PORT=9000 python api_server.py

Uses only the standard library, so there's nothing extra to install.
"""

import os
import sys
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from services.token_service import validate_token, check_account_active
from services.ingest_service import ingest_rows

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))
MAX_BODY = 25 * 1024 * 1024  # 25 MB cap on a single push


class Handler(BaseHTTPRequestHandler):
    # Quieter logging (one line per request)
    def log_message(self, fmt, *args):
        sys.stdout.write("  [api] " + (fmt % args) + "\n")

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _token(self) -> str:
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return self.headers.get("X-Api-Token", "").strip()

    def do_GET(self):
        path = self.path.rstrip("/")
        if path == "/health":
            return self._send(200, {"ok": True, "service": "dashin-ingest"})
        # Activation check the desktop scraper calls on startup.
        if path == "/api/auth/validate":
            return self._send(200, check_account_active(self._token()))
        return self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        # The scraper can also POST the activation check (avoids caching).
        if self.path.rstrip("/") == "/api/auth/validate":
            return self._send(200, check_account_active(self._token()))

        if self.path.rstrip("/") != "/api/leads/import":
            return self._send(404, {"ok": False, "error": "not found"})

        # Ingest requires an ACTIVE account, not just a valid token — so a lapsed
        # client's pushes are refused too, not only blocked from running.
        acct = check_account_active(self._token())
        if not acct["active"]:
            return self._send(403, {"ok": False, "error": acct.get("reason") or "account inactive"})
        org_id = acct["org_id"]

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            return self._send(400, {"ok": False, "error": "empty or oversized body"})

        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as e:
            return self._send(400, {"ok": False, "error": f"bad JSON: {e}"})

        rows = data.get("rows") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return self._send(400, {"ok": False, "error": "expected {'rows': [...]}"})

        source = (data.get("source") if isinstance(data, dict) else "") or "scraper"
        try:
            summary = ingest_rows(org_id, rows, source=source)
        except Exception as e:
            return self._send(500, {"ok": False, "error": f"ingest failed: {e}"})

        return self._send(200, {"ok": True, "org_id": org_id, **summary})


def main():
    # Make sure schema exists before serving.
    try:
        from core.db import init_db, migrate_db
        init_db(); migrate_db()
    except Exception as e:
        print(f"[api] schema init warning: {e}")

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[api] Dashin ingest API listening on http://{HOST}:{PORT}")
    print(f"[api]   POST /api/leads/import   (Bearer token)")
    print(f"[api]   GET  /health")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[api] shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
