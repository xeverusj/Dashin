"""
dashboards/enricher_dashboard.py — LinkedIn Enricher UI (Module C).

Finds LinkedIn profiles via web search in a visible browser — no LinkedIn login.
Two modes, auto-detected from the uploaded CSV:

  C1 (contact)  rows have a name  · find that person's profile.
  C2 (roles)    company-only rows · the user picks which titles to try, then it
                searches "<company> current <title> linkedin" over up to 3 rounds.

The search runs in a separate visible browser window (launched as a subprocess,
same pattern as the scrapers) so it stays out of Streamlit's process and the user
can solve the occasional CAPTCHA by hand. Results are written progressively to an
output CSV that the user loads back here.

Convention: exposes render(user: dict), called by app.py's router.
"""

import os
import sys
import time
import subprocess
from pathlib import Path

import pandas as pd
import streamlit as st

ALLOWED_ROLES = {"super_admin", "org_admin", "manager", "research_manager", "researcher"}

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_JOBS_DIR = _PROJECT_ROOT / "data" / "system" / "enrichment_jobs"

_NAME_COLS = ["name", "full_name", "contact", "person"]
_COMPANY_COLS = ["company", "company_name", "organisation", "organization"]

_COMMON_TITLES = ["CEO", "Founder", "Co-Founder", "Managing Director", "President",
                  "Head of Growth", "Head of Marketing", "Head of Sales", "CTO",
                  "COO", "CMO", "VP Sales", "Business Development"]


def _has_col(df, options):
    import re
    cols = {re.sub(r"[^a-z0-9]+", "_", str(c).strip().lower()).strip("_") for c in df.columns}
    # exact normalized match, or the option appears as a substring of a column
    return any(o in cols or any(o in c for c in cols) for o in options)


def render(user: dict):
    role = user.get("role", "researcher")
    if role not in ALLOWED_ROLES:
        st.error("You don't have permission to access this page.")
        return

    st.markdown("## LinkedIn Enricher")
    st.markdown(
        "Find LinkedIn profiles by **web search** — no LinkedIn login, no cookies. "
        "A visible browser window does the searching (solve a CAPTCHA yourself on "
        "the rare occasion one appears). Upload a CSV and it picks the mode for you."
    )

    _JOBS_DIR.mkdir(parents=True, exist_ok=True)

    # ── Step 1: upload ────────────────────────────────────────────────────────
    st.markdown("### 1. Upload your list")
    st.caption(
        "**People to enrich (C1):** include a `name` column (plus optional "
        "`company` and `title` for accuracy).  \n"
        "**Companies only (C2):** just a `company` column — you'll choose which "
        "titles to look for."
    )
    up = st.file_uploader("CSV file", type=["csv"], key="enr_csv")
    if not up:
        st.info("Upload a CSV to begin.")
        return

    try:
        df = pd.read_csv(up).fillna("")
    except Exception as e:
        st.error(f"Could not read CSV: {e}")
        return

    has_name = _has_col(df, _NAME_COLS)
    has_company = _has_col(df, _COMPANY_COLS)
    if not has_name and not has_company:
        st.error(f"CSV needs a name column ({'/'.join(_NAME_COLS)}) "
                 f"or a company column ({'/'.join(_COMPANY_COLS)}).")
        return

    st.success(f"Loaded **{len(df)}** rows.")
    with st.expander("Preview (first 5)"):
        st.dataframe(df.head(5), use_container_width=True)

    # ── Step 2: mode + titles ─────────────────────────────────────────────────
    st.markdown("### 2. Choose mode")
    default_mode = "Contacts (find each person)" if has_name else "Companies (find people by title)"
    mode_label = st.radio(
        "Mode", ["Contacts (find each person)", "Companies (find people by title)"],
        index=0 if has_name else 1,
        help="Auto-selected from your columns; override if needed.")
    mode = "contact" if mode_label.startswith("Contacts") else "roles"

    titles = []
    if mode == "roles":
        st.markdown("#### Which titles should it look for?")
        st.caption("It tries them in order — first round uses the first title, then "
                   "the next, then a broadened search. Ordered by priority.")
        picked = st.multiselect("Common titles", _COMMON_TITLES, default=["CEO", "Founder"])
        extra = st.text_input("Add custom titles (comma-separated)", "")
        titles = picked + [t.strip() for t in extra.split(",") if t.strip()]
        if not titles:
            st.warning("Pick at least one title to search for.")
    elif not has_name:
        st.warning("Contacts mode needs a `name` column — switch to Companies mode "
                   "or upload a list with names.")

    # ── Step 3: launch ────────────────────────────────────────────────────────
    st.markdown("### 3. Run the enricher")
    st.caption("Opens a browser window and works through the list. Results are "
               "saved continuously, so a stop mid-run keeps what's done.")

    can_run = (mode == "contact" and has_name) or (mode == "roles" and titles)
    if st.button("▶ Start enrichment", type="primary", disabled=not can_run):
        job_id = f"enrich_{int(time.time())}"
        in_path = _JOBS_DIR / f"{job_id}_input.csv"
        out_path = _JOBS_DIR / f"{job_id}_output.csv"
        df.to_csv(in_path, index=False, encoding="utf-8-sig")

        cmd = [sys.executable, str(_PROJECT_ROOT / "run_enricher.py"),
               "--input", str(in_path), "--output", str(out_path), "--mode", mode]
        if mode == "roles":
            cmd += ["--titles", ",".join(titles)]

        if sys.platform == "win32":
            bat = _JOBS_DIR / f"{job_id}_run.bat"
            args = " ".join(f'"{c}"' for c in cmd[1:])
            with open(bat, "w") as bf:
                bf.write("@echo off\n")
                bf.write("echo   Dashin LinkedIn Enricher\n")
                bf.write("echo   A search browser will open. Solve any CAPTCHA if shown.\n")
                bf.write(f'cd /d "{_PROJECT_ROOT}"\n')
                bf.write(f'"{cmd[0]}" {args}\n')
                bf.write("echo.\necho   Enrichment finished.\npause\n")
            proc = subprocess.Popen(["cmd", "/c", "start", "Dashin Enricher", str(bat)],
                                    cwd=str(_PROJECT_ROOT))
        else:
            proc = subprocess.Popen(cmd, cwd=str(_PROJECT_ROOT))

        st.session_state["enr_out_path"] = str(out_path)
        st.success(f"Enricher launched (PID {proc.pid}). A browser window will open. "
                   "Come back and load the results below once it finishes.")

    # ── Step 4: results ───────────────────────────────────────────────────────
    out_path = st.session_state.get("enr_out_path")
    if out_path:
        st.markdown("### 4. Results")
        if st.button("Load / refresh results"):
            pass  # triggers rerun
        if os.path.exists(out_path):
            try:
                res = pd.read_csv(out_path)
            except Exception:
                res = pd.DataFrame()
            if len(res):
                st.warning(
                    "**Web-search matches — verify before trusting.** These profiles "
                    "were found by search ranking, not confirmed on LinkedIn. Give any "
                    "**probable** / **needs_manual** rows (and company-only results) a "
                    "quick eyeball before use."
                )
                if "match_confidence" in res.columns:
                    counts = res["match_confidence"].value_counts().to_dict()
                    st.write(" | ".join(f"**{k}**: {v}" for k, v in counts.items()))
                st.dataframe(res, use_container_width=True, hide_index=True)
                st.download_button("Download results CSV",
                                   res.to_csv(index=False).encode("utf-8-sig"),
                                   file_name="linkedin_enriched.csv", mime="text/csv")
            else:
                st.info("No results yet — the job may still be running. Refresh in a bit.")
        else:
            st.info("Waiting for the enricher to write its first results…")
