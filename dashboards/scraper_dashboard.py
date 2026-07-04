"""
dashboards/scraper_dashboard.py — Dashin Research Platform V2
Smart Scraper tab. Launches worker.py as a non-blocking subprocess,
streams output in real-time, and enforces a one-scrhape-at-a-time lock per org.
"""

import os
import sys
import json
import time
import signal
import datetime
import subprocess
import logging
from pathlib import Path

import streamlit as st
import pandas as pd

from core.db import get_connection

# ── STYLE (page-specific only — shared styles via core/styles.py) ─────────────
# All shared components (.stat-card, .tbl, .badge, .b-run/done/fail, .terminal,
# .tip, .page-title, .page-sub, .launch-box, buttons, inputs) live in core/styles.py
CSS = ""  # kept for compatibility; actual styles injected in render()

_PROJECT_ROOT = Path(__file__).parent.parent
_SESSIONS_DIR = _PROJECT_ROOT / "data" / "system" / "sessions"
_FAILED_SAVES = _PROJECT_ROOT / "failed_db_saves.json"


# ── DATA HELPERS ──────────────────────────────────────────────────────────────

def _get_lead_stats(org_id: int) -> dict:
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT
                COUNT(*)                                        AS total,
                SUM(CASE WHEN status='new'      THEN 1 END)    AS new,
                SUM(CASE WHEN status='enriched' THEN 1 END)    AS enriched,
                SUM(CASE WHEN status='used'     THEN 1 END)    AS used,
                SUM(CASE WHEN status='archived' THEN 1 END)    AS archived
            FROM leads WHERE org_id=?
        """, (org_id,)).fetchone()
        if row:
            return {k: (row.get(k) or 0) for k in ("total","new","enriched","used","archived")}
    except Exception as e:
        logging.warning(f"[scraper_dashboard._get_lead_stats] {e}")
    finally:
        conn.close()
    return {"total": 0, "new": 0, "enriched": 0, "used": 0, "archived": 0}


def _get_recent_sessions(org_id: int, limit: int = 15) -> list:
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT id, event_name, event_url, category, layout,
                   status, leads_found, leads_new, leads_dupes,
                   ai_cost_usd, pattern_used, started_at, finished_at
            FROM scrape_sessions
            WHERE org_id=? OR org_id IS NULL
            ORDER BY started_at DESC LIMIT ?
        """, (org_id, limit)).fetchall()
        return rows if rows else []
    except Exception as e:
        logging.warning(f"[scraper_dashboard._get_recent_sessions] {e}")
        return []
    finally:
        conn.close()


def _get_running_session(org_id: int) -> dict | None:
    """Return the currently running scrape session for this org, if any.
    Sessions older than 6 hours are automatically marked as failed (stale lock)."""
    conn = get_connection()
    try:
        # Auto-expire sessions stuck in 'running' for more than 6 hours
        conn.execute("""
            UPDATE scrape_sessions SET status='failed'
            WHERE status='running'
              AND started_at < datetime('now', '-6 hours')
        """)
        conn.commit()

        row = conn.execute("""
            SELECT * FROM scrape_sessions
            WHERE (org_id=? OR org_id IS NULL) AND status='running'
            ORDER BY started_at DESC LIMIT 1
        """, (org_id,)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def _save_scrape_session(org_id, url, source_name, industry_sel,
                         company_type_sel, notes_input, client_id):
    """Insert a running scrape_session row and return the session id."""
    import uuid
    session_id = f"session_{uuid.uuid4().hex[:12]}"
    conn = get_connection()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO scrape_sessions
                (id, org_id, event_url, event_name,
                 category, status, started_at)
            VALUES (?,?,?,?,?,'running',?)
        """, (session_id, org_id, url,
              source_name or None,
              industry_sel or None,
              datetime.datetime.utcnow().isoformat()))
        conn.commit()
    except Exception as e:
        logging.warning(f"[scraper_dashboard._save_scrape_session] {e}")
    finally:
        conn.close()
    return session_id


def _mark_session_done(session_id: str, exit_code: int):
    conn = get_connection()
    try:
        status = "done" if exit_code == 0 else "failed"
        conn.execute("""
            UPDATE scrape_sessions
            SET status=?, finished_at=?
            WHERE id=?
        """, (status, datetime.datetime.utcnow().isoformat(), session_id))
        conn.commit()
    except Exception as e:
        logging.warning(f"[scraper_dashboard._mark_session_done] {e}")
    finally:
        conn.close()


def badge(status: str) -> str:
    cls = {"running": "b-run", "done": "b-done", "failed": "b-fail"}.get(status, "b-run")
    return f'<span class="badge {cls}">{status.title()}</span>'


def _read_log(log_path: str) -> str:
    """Read subprocess log file, return last 200 lines."""
    try:
        with open(log_path, "r", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-200:])
    except Exception:
        return ""


def _is_process_running(pid: int) -> bool:
    """Check if a PID is still alive."""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _render_learning_result_card(log_content: str):
    """Show a post-scrape learning result card based on scrape output."""
    if not log_content:
        return
    # Parse key signals from the log
    is_new_site  = "Saved layout pattern for" in log_content
    used_cache   = "Using cached pattern" in log_content or "Pattern" in log_content
    quality_fail = "Quality check failed" in log_content or "quality" in log_content.lower() and "fail" in log_content.lower()
    domain_match = None

    import re
    m = re.search(r"Saved layout pattern for ([^\s\n]+)", log_content)
    if m:
        domain_match = m.group(1)
    else:
        m = re.search(r"Using cached pattern for ([^\s\n]+)", log_content)
        if m:
            domain_match = m.group(1)

    if quality_fail:
        st.markdown(
            "**Quality check failed** — leads found had insufficient title/company data. "
            "Pattern NOT cached. Try navigating to the attendee list page directly.",
            unsafe_allow_html=False,
        )
    elif is_new_site and domain_match:
        selector_type = "STABLE" if "data-test" in log_content else "FRAGILE"
        colour = "var(--success-bg)" if selector_type == "STABLE" else "var(--surface-2)"
        st.markdown(
            f'<div style="background:{colour};border-radius:8px;padding:14px 18px;'
            f'font-size:13px;margin:12px 0">'
            f'<strong>New site learned: {domain_match}</strong><br>'
            f'Selector type: <strong>{selector_type}</strong><br>'
            f'Added to site library'
            f'</div>',
            unsafe_allow_html=True,
        )
    elif used_cache and domain_match:
        st.markdown(
            f'<div style="background:var(--surface-2);border-radius:8px;padding:14px 18px;'
            f'font-size:13px;margin:12px 0">'
            f'Used <strong>cached pattern</strong> for {domain_match}'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Check for expiring patterns
    try:
        from services.site_learning_service import get_expiring_soon
        expiring = get_expiring_soon(7)
        if expiring:
            domains = ", ".join(p["domain"] for p in expiring[:3])
            st.markdown(
                f'<div style="background:var(--surface-2);border-radius:8px;padding:14px 18px;'
                f'font-size:13px;margin:8px 0">'
                f'Cached pattern(s) expiring soon (fragile selectors): {domains}'
                f'</div>',
                unsafe_allow_html=True,
            )
    except Exception as e:
        logging.warning(f"[scraper_dashboard] Failed to render expiring patterns banner: {e}")


def _check_failed_saves() -> list:
    """Return list of failed DB saves, or empty list."""
    if not _FAILED_SAVES.exists():
        return []
    try:
        with open(_FAILED_SAVES) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _clear_failed_saves():
    try:
        _FAILED_SAVES.unlink(missing_ok=True)
    except Exception:
        pass


# ── RENDER ─────────────────────────────────────────────────────────────────────

def render(user: dict = None):
    try:
        from playwright.sync_api import sync_playwright
        _playwright_ok = True
    except ImportError:
        _playwright_ok = False

    if not _playwright_ok:
        st.info("###Scraper runs on your local machine")
        st.markdown("""
        The scraper opens a real browser so you can log in to Brella, BETT,
        and other event platforms. It cannot run in the cloud.

        **To scrape a new event locally:**
        1. Pull latest code to your machine
        2. `pip install playwright playwright-stealth`
        3. `playwright install chromium`
        4. `python worker.py https://next.brella.io/events/EVENTNAME/people`

        All scraped leads save automatically to the shared inventory.
        """)
        return

    org_id = (user or {}).get("org_id", 1)
    role   = (user or {}).get("role", "researcher")
    is_admin = role in ("super_admin", "org_admin")

    from core.styles import inject_shared_css
    inject_shared_css()

    st.markdown('<div class="page-title">Smart Scraper</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="page-sub">Launch a scraping session on any event directory. '
        'Claude AI identifies the page structure automatically.</div>',
        unsafe_allow_html=True,
    )

    # ── Failed DB saves warning banner ────────────────────────────────
    failed_saves = _check_failed_saves()
    if failed_saves:
        total_leads = sum(s.get("batch_count", 0) for s in failed_saves)
        st.error(
            f"**{total_leads} leads are in CSV only** — {len(failed_saves)} scrape session(s) "
            f"failed to write to the database. Use **Sync CSV to DB** below to recover them."
        )
        if is_admin:
            if st.button("Sync CSV to DB", type="secondary"):
                _sync_csv_to_db(org_id)
                _clear_failed_saves()
                st.success("Sync complete. Reload to see updated counts.")
                st.rerun()

    # ── Stats row ─────────────────────────────────────────────────────
    s = _get_lead_stats(org_id)
    st.markdown(f"""
    <div class="stat-row">
      <div class="stat-card"><div class="stat-val">{s['total']:,}</div>
        <div class="stat-label">Total Leads</div><div class="stat-note">All time</div></div>
      <div class="stat-card"><div class="stat-val">{s['new']:,}</div>
        <div class="stat-label">New</div><div class="stat-note">Awaiting research</div></div>
      <div class="stat-card"><div class="stat-val">{s['enriched']:,}</div>
        <div class="stat-label">Enriched</div><div class="stat-note">Ready to use</div></div>
      <div class="stat-card"><div class="stat-val">{s['used']:,}</div>
        <div class="stat-label">Used</div><div class="stat-note">Across all clients</div></div>
      <div class="stat-card"><div class="stat-val">{s['archived']:,}</div>
        <div class="stat-label">Archived</div><div class="stat-note">In named lists</div></div>
    </div>
    """, unsafe_allow_html=True)

    # ── Active scrapes panel (supports multiple concurrent) ───────────
    # Migrate legacy single-scrape session state to list format
    if "scrape_pid" in st.session_state and "active_scrapes" not in st.session_state:
        old = {
            "pid":        st.session_state.pop("scrape_pid"),
            "log":        st.session_state.pop("scrape_log_file", None),
            "session_id": st.session_state.pop("scrape_session_id", None),
            "start":      st.session_state.pop("scrape_start_time", None),
            "url":        "—",
        }
        st.session_state["active_scrapes"] = [old]

    active_scrapes = st.session_state.setdefault("active_scrapes", [])

    # Partition into still-running vs just-finished
    still_running = [s for s in active_scrapes if _is_process_running(s["pid"])]
    just_finished = [s for s in active_scrapes if not _is_process_running(s["pid"])]

    # Handle finished scrapes
    for s in just_finished:
        log_content = _read_log(s["log"]) if s.get("log") else ""
        exit_code = 0 if "" in log_content or "Done" in log_content else 1
        if s.get("session_id"):
            _mark_session_done(s["session_id"], exit_code)
        if exit_code == 0:
            st.success(f"Scrape finished: {s.get('url', '')}")
        else:
            st.warning(f"Scrape ended with errors: {s.get('url', '')}")
        with st.expander("View output"):
            st.code(log_content or "(no output)", language="bash")
        _render_learning_result_card(log_content)

    st.session_state["active_scrapes"] = still_running

    if still_running:
        st.markdown(
            f'<div class="sec-header">{len(still_running)} Scrape(s) Running</div>',
            unsafe_allow_html=True,
        )
        for idx, s in enumerate(still_running):
            elapsed = ""
            if s.get("start"):
                secs = int(time.time() - s["start"])
                elapsed = f"  ·  {secs // 60}m {secs % 60}s"
            with st.expander(f"{s.get('url', 'Scrape')} {elapsed}", expanded=True):
                col_stop, col_refresh, _ = st.columns([1, 1, 5])
                with col_stop:
                    if st.button("⏹ Stop", key=f"stop_{idx}", type="secondary"):
                        try:
                            os.kill(s["pid"], signal.SIGTERM)
                            time.sleep(1)
                            if _is_process_running(s["pid"]):
                                os.kill(s["pid"], signal.SIGKILL)
                        except Exception as e:
                            logging.warning(f"[scraper] stop failed: {e}")
                        if s.get("session_id"):
                            _mark_session_done(s["session_id"], 1)
                        st.session_state["active_scrapes"] = [
                            x for x in still_running if x["pid"] != s["pid"]
                        ]
                        st.rerun()
                with col_refresh:
                    if st.button("Refresh", key=f"refresh_{idx}", type="secondary"):
                        st.rerun()
                log_content = _read_log(s["log"]) if s.get("log") else ""
                st.markdown(
                    f'<div class="terminal">{log_content or "Starting…"}</div>',
                    unsafe_allow_html=True,
                )

        # Auto-refresh every 3 seconds while any scrape is running
        pass  # auto-refresh removed — use Refresh button instead

    # ── Launch form ───────────────────────────────────────────────────
    st.markdown('<div class="sec-header">Launch New Scrape</div>', unsafe_allow_html=True)

    SCRAPER_TYPES = {
        "Event Directory": {
            "key": "event", "script": "worker.py",
            "placeholder": "https://app.bettshow.com/newfront/participants",
            "tip": "Scrapes attendee/delegate lists from event apps. Works on Brella, BETT, FDF, Swapcard, and most event platforms.",
        },
        "Clutch": {
            "key": "clutch", "script": "clutch_scraper.py",
            "placeholder": "https://clutch.co/uk/agencies/seo",
            "tip": "Scrapes agency/company listings from Clutch.co.",
        },
        "Generic List": {
            "key": "generic", "script": "worker.py",
            "placeholder": "https://any-directory-or-listing.com",
            "tip": "AI vision fallback for any other listing page.",
        },
    }

    conn_cl = get_connection()
    clients_raw = conn_cl.execute(
        "SELECT id, name FROM clients WHERE org_id=? AND is_active=1 ORDER BY name",
        (org_id,)
    ).fetchall()
    conn_cl.close()
    client_options = ["— No client (general scrape) —"] + [c["name"] for c in clients_raw]
    client_id_map  = {c["name"]: c["id"] for c in clients_raw}

    INDUSTRIES = ["— Not specified —","EdTech","HealthTech","FinTech","SaaS / Software",
                  "Professional Services","Retail / eCommerce","Media & Publishing",
                  "Logistics & Supply Chain","Manufacturing","Real Estate",
                  "HR & Recruitment","Legal","Marketing & Advertising",
                  "Cybersecurity","AI / Data","Energy & CleanTech","Other"]
    COMPANY_TYPES = ["— Not specified —","Enterprise (1000+)","Mid-Market (100–999)",
                     "SMB (10–99)","Startup (<10)","Non-profit / Association",
                     "Government / Public Sector","Agency","Consultancy"]

    with st.container():
        st.markdown('<div class="launch-box">', unsafe_allow_html=True)

        scraper_label = st.selectbox("Scraper type", list(SCRAPER_TYPES.keys()))
        scraper = SCRAPER_TYPES[scraper_label]
        is_coming_soon = scraper["script"] is None

        st.markdown(
            f'<div class="tip">{scraper["tip"]}'
            f'{"<br><b>Coming soon — not yet available.</b>" if is_coming_soon else ""}</div>',
            unsafe_allow_html=True,
        )

        if not is_coming_soon:
            url = st.text_input("URL *", placeholder=scraper["placeholder"])

            col1, col2, col3 = st.columns(3)
            with col1:
                client_sel = st.selectbox("Client this is for", client_options)
            with col2:
                industry_preset = st.selectbox("Target industry", INDUSTRIES)
                industry_custom = st.text_input("Or type your own", placeholder="e.g. PropTech…")
                industry_sel = industry_custom.strip() or (
                    None if industry_preset == "— Not specified —" else industry_preset)
            with col3:
                comptype_preset = st.selectbox("Company type", COMPANY_TYPES)
                comptype_custom = st.text_input("Or type your own ", placeholder="e.g. Scale-up…")
                company_type_sel = comptype_custom.strip() or (
                    None if comptype_preset == "— Not specified —" else comptype_preset)

            col4, col5 = st.columns(2)
            with col4:
                source_name = st.text_input("Source / event name",
                                            placeholder="BETT 2026 / Clutch UK SEO")
            with col5:
                notes_input = st.text_input("Notes (optional)",
                                            placeholder="VP+ roles only, filter by UK")

            cdp_url = ""
            if scraper["key"] == "clutch":
                max_pages = st.slider("Max pages to scrape", 1, 50, 20)
                st.caption(f"Will scrape up to ~{max_pages * 25} companies")
                mobile_mode = False
            else:
                max_pages = 20
                mobile_mode = st.checkbox("Mobile emulation mode")
                with st.expander("Advanced: attach to an emulator / device (app-only events)"):
                    st.caption(
                        "For events that exist **only** as a native app. Run the app in an "
                        "Android emulator (or device) with Chrome remote debugging enabled, "
                        "then paste its debug URL below. The scraper attaches and captures "
                        "the app's attendee API directly — no proxy or cert install needed. "
                        "Leave blank for normal web scraping."
                    )
                    cdp_url = st.text_input("Emulator/device debug URL",
                                            placeholder="http://127.0.0.1:9222").strip()

            c1, c2 = st.columns([1, 4])
            with c1:
                go = st.button("Launch", type="primary", use_container_width=True)
            with c2:
                st.markdown(
                    '<p style="font-size:12px;color:var(--text-3);padding-top:12px">'
                    "Opens a browser window — login &amp; click the purple START button to begin.</p>",
                    unsafe_allow_html=True,
                )
        else:
            url = ""
            go = False
            client_sel = client_options[0]
            industry_sel = None
            company_type_sel = None
            source_name = ""
            notes_input = ""
            max_pages = 20
            mobile_mode = False
            cdp_url = ""

        st.markdown("</div>", unsafe_allow_html=True)

    if go:
        if not url.strip():
            st.error("Please paste a URL.")
        else:
            script_path = _PROJECT_ROOT / scraper["script"]
            if not script_path.exists():
                st.error(f"Script not found: {script_path}")
            else:
                selected_client_id = client_id_map.get(client_sel)
                session_id = _save_scrape_session(
                    org_id, url.strip(), source_name.strip(),
                    industry_sel, company_type_sel, notes_input.strip(),
                    selected_client_id,
                )

                # Build command
                if scraper["key"] == "clutch":
                    cmd = [sys.executable, str(script_path),
                           url.strip(), "--pages", str(max_pages)]
                else:
                    cmd = [sys.executable, str(script_path), url.strip()]
                    if mobile_mode:
                        cmd.append("--mobile")
                    if cdp_url:
                        cmd += ["--cdp", cdp_url]

                # Launch scraper as a visible subprocess.
                # headless=False in the scraper opens a visible Chromium window.
                # We write a small launcher script that runs the scraper,
                # tees output to a log file, and keeps the window open.
                _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
                log_path = str(_SESSIONS_DIR / f"{session_id}_log.txt")

                if sys.platform == "win32":
                    # Windows: write a temp .bat file, then open it in a
                    # new visible cmd window. User sees live output AND
                    # the Chromium browser pops up for interaction.
                    bat_path = _SESSIONS_DIR / f"{session_id}_run.bat"
                    py_exe = cmd[0]  # python executable path
                    py_args = " ".join(f'"{c}"' for c in cmd[1:])
                    with open(bat_path, "w") as bf:
                        bf.write('@echo off\n')
                        bf.write('echo ============================================\n')
                        bf.write('echo   Dashin Scraper\n')
                        bf.write('echo   Login, set filters, click START SCRAPING\n')
                        bf.write('echo ============================================\n')
                        bf.write('echo.\n')
                        bf.write(f'cd /d "{_PROJECT_ROOT}"\n')
                        bf.write(f'"{py_exe}" {py_args}\n')
                        bf.write('echo.\n')
                        bf.write('echo ============================================\n')
                        bf.write('echo   Scrape finished.\n')
                        bf.write('echo ============================================\n')
                        bf.write('pause\n')

                    # Touch the log file so dashboard doesn't error
                    with open(log_path, "w") as lf:
                        lf.write("Scraper launched in a new window.\n")
                        lf.write("Check the Dashin Scraper terminal for live output.\n")

                    proc = subprocess.Popen(
                        ['cmd', '/c', 'start', 'Dashin Scraper', str(bat_path)],
                        cwd=str(_PROJECT_ROOT),
                    )
                else:
                    # macOS / Linux
                    with open(log_path, "w") as log_fh:
                        proc = subprocess.Popen(
                            cmd,
                            stdout=log_fh,
                            stderr=subprocess.STDOUT,
                            cwd=str(_PROJECT_ROOT),
                        )

                st.session_state.setdefault("active_scrapes", []).append({
                    "pid":        proc.pid,
                    "log":        log_path,
                    "session_id": session_id,
                    "start":      time.time(),
                    "url":        url.strip(),
                })

                st.success(
                    f"Scrape launched (PID {proc.pid}). "
                    "A browser window should open — login, set filters, then click the purple START button."
                )
                st.rerun()

    # ── Recent sessions table ─────────────────────────────────────────
    st.markdown('<div class="sec-header">Recent Sessions</div>', unsafe_allow_html=True)

    col_r, _ = st.columns([1, 6])
    with col_r:
        if st.button("Refresh", type="secondary"):
            st.rerun()

    sessions = _get_recent_sessions(org_id, 15)
    if not sessions:
        st.markdown('<p style="color:var(--text-3);font-size:13px;padding:16px 0">No sessions yet.</p>',
                    unsafe_allow_html=True)
    else:
        rows_html = ""
        for sess in sessions:
            evt = (sess.get("event_name") or sess.get("event_url") or "?")[:45]
            cat = sess.get("category") or "—"
            dt  = (sess.get("started_at") or "")[:16].replace("T", " ")
            ai  = sess.get("ai_cost_usd") or 0
            pat = "Pattern" if sess.get("pattern_used") else "AI"
            rows_html += f"""<tr>
              <td class="n">{evt}</td><td>{cat}</td>
              <td>{sess.get('leads_found', 0)}</td>
              <td style="color:var(--success);font-weight:600">{sess.get('leads_new', 0)}</td>
              <td style="color:var(--text-3)">{sess.get('leads_dupes', 0)}</td>
              <td>{badge(sess.get('status', 'running'))}</td>
              <td style="color:var(--text-3);font-size:11px">{pat}</td>
              <td style="color:var(--text-3);font-size:11px">${ai:.4f}</td>
              <td style="color:var(--text-3);font-size:11px">{dt}</td>
            </tr>"""

        st.markdown(f"""
        <div class="tbl"><table>
          <thead><tr>
            <th>Event</th><th>Category</th><th>Found</th>
            <th>New</th><th>Dupes</th><th>Status</th>
            <th>Method</th><th>AI Cost</th><th>Date</th>
          </tr></thead><tbody>{rows_html}</tbody>
        </table></div>
        """, unsafe_allow_html=True)

    # ── Session file downloads ────────────────────────────────────────
    sdir = _PROJECT_ROOT / "data" / "system" / "sessions"
    if sdir.exists():
        csvs = sorted(sdir.glob("*.csv"), reverse=True)
        if csvs:
            st.markdown('<div class="sec-header">Download Session Files</div>',
                        unsafe_allow_html=True)
            sel = st.selectbox("File", csvs, format_func=lambda p: p.name,
                               label_visibility="collapsed")
            if sel:
                try:
                    df = pd.read_csv(sel)
                    st.download_button(
                        f"Download {sel.name} ({len(df)} rows)",
                        df.to_csv(index=False).encode(),
                        sel.name, "text/csv",
                    )
                    with st.expander("Preview first 20 rows"):
                        st.dataframe(df.head(20), use_container_width=True)
                except Exception as e:
                    st.error(str(e))


# ── CSV to DB Sync ────────────────────────────────────────────────────────────

def _sync_csv_to_db(org_id: int):
    """Import leads from session CSVs that aren't yet in the DB."""
    try:
        from services.lead_service import save_lead
    except Exception as e:
        st.error(f"Cannot import lead_service: {e}")
        return

    sdir = _PROJECT_ROOT / "data" / "system" / "sessions"
    csvs = list(sdir.glob("*.csv")) if sdir.exists() else []
    if not csvs:
        st.info("No session CSVs found.")
        return

    total_new = 0
    for csv_path in csvs:
        try:
            df = pd.read_csv(csv_path)
            for _, row in df.iterrows():
                name = str(row.get("name") or row.get("full_name") or "").strip()
                if not name:
                    continue
                _, is_new = save_lead(
                    org_id       = org_id,
                    full_name    = name,
                    company_name = str(row.get("company") or "").strip(),
                    title        = str(row.get("title") or "").strip(),
                    event_url    = str(row.get("source_url") or "").strip(),
                    session_id   = csv_path.stem,
                )
                if is_new:
                    total_new += 1
        except Exception as e:
            logging.warning(f"[sync_csv_to_db] {csv_path.name}: {e}")

    st.success(f"Synced {total_new} new leads from {len(csvs)} CSV files.")
