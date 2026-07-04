"""
dashboards/email_match_dashboard.py — Email-list matching UI (Module D3).

Upload an email list from any external finder/verifier and attach each address to
the right lead in inventory. Shows a match report (exact / inferred / domain /
needs-review / unmatched) so nothing is silently dropped.

Convention: exposes render(user: dict), called by app.py's router.
"""

import re
import pandas as pd
import streamlit as st

from services import email_matcher as em

ALLOWED_ROLES = {"super_admin", "org_admin", "manager", "research_manager", "researcher"}

_EMAIL_COLS = ["email", "email_address", "e_mail", "mail"]
_NAME_COLS = ["name", "full_name", "contact", "person"]
_COMPANY_COLS = ["company", "company_name", "organisation", "organization"]
_VERIFIED_COLS = ["verified", "email_verified", "valid", "status"]


def _find(cols, options):
    norm = {re.sub(r"[^a-z0-9]+", "_", str(c).strip().lower()).strip("_"): c for c in cols}
    for o in options:
        if o in norm:
            return norm[o]
    for key, orig in norm.items():
        if any(o in key for o in options):
            return orig
    return None


def render(user: dict):
    role = user.get("role", "researcher")
    org_id = user.get("org_id", 1)
    if role not in ALLOWED_ROLES:
        st.error("🚫 You don't have permission to access this page.")
        return

    st.markdown("## Email List Matching")
    st.markdown(
        "Upload an email list and it attaches each address to the matching lead "
        "already in your inventory — by name, by the email's implied name, or by "
        "company domain. Nothing is thrown away: unmatched emails are kept in a "
        "pool for later."
    )

    st.markdown("### 1. Upload email list")
    st.caption("Needs an `email` column. Optional but improves matching: `name`, "
               "`company`, and a `verified` column.")
    up = st.file_uploader("CSV file", type=["csv"], key="em_csv")
    if not up:
        st.info("Upload a CSV to begin.")
        return

    try:
        df = pd.read_csv(up).fillna("")
    except Exception as e:
        st.error(f"Could not read CSV: {e}")
        return

    email_col = _find(df.columns, _EMAIL_COLS)
    if not email_col:
        st.error(f"No email column found (looked for {', '.join(_EMAIL_COLS)}).")
        return
    name_col = _find(df.columns, _NAME_COLS)
    company_col = _find(df.columns, _COMPANY_COLS)
    verified_col = _find(df.columns, _VERIFIED_COLS)

    st.success(f"Loaded **{len(df)}** rows. Email column: `{email_col}`"
               + (f", name: `{name_col}`" if name_col else "")
               + (f", company: `{company_col}`" if company_col else ""))

    rows = []
    for _, r in df.iterrows():
        v = str(r.get(verified_col, "")).strip().lower() if verified_col else ""
        rows.append({
            "email": str(r.get(email_col, "")).strip(),
            "name": str(r.get(name_col, "")).strip() if name_col else "",
            "company": str(r.get(company_col, "")).strip() if company_col else "",
            "verified": v in ("true", "1", "yes", "valid", "verified"),
        })

    st.markdown("### 2. Match against inventory")
    dry = st.checkbox("Preview only (don't write to the database yet)", value=True)
    if st.button("Match emails", type="primary"):
        report = em.match_emails(org_id, rows, write=not dry)
        st.session_state["em_report"] = report
        st.session_state["em_dry"] = dry

    report = st.session_state.get("em_report")
    if report:
        st.markdown("### 3. Match report")
        if st.session_state.get("em_dry"):
            st.info("Preview run — nothing was written. Uncheck **Preview only** and "
                    "re-run to save matches.")
        c = st.columns(5)
        c[0].metric("Exact", report["matched_exact"])
        c[1].metric("Inferred", report["matched_inferred"])
        c[2].metric("Domain", report["matched_domain"])
        c[3].metric("Needs review", report["needs_review"])
        c[4].metric("Unmatched", report["unmatched"])

        matched = report["matched_exact"] + report["matched_inferred"] + report["matched_domain"]
        st.write(f"**{matched}/{report['total']}** emails matched to a lead"
                 f"  ·  **{report['needs_review']}** need a human to pick among candidates"
                 f"  ·  **{report['unmatched']}** parked in the unmatched pool.")

        if report["needs_review"]:
            st.warning("⚠ **Needs review:** these emails matched a company with several "
                       "people — pick the right person manually. Candidate lead IDs are "
                       "in the detail table below (ranked by name similarity).")

        det = pd.DataFrame(report["details"])
        if len(det):
            det["candidates"] = det["candidates"].apply(lambda x: ", ".join(map(str, x)) if x else "")
            st.dataframe(det, use_container_width=True, hide_index=True)
            st.download_button("⬇ Download match report",
                               det.to_csv(index=False).encode("utf-8-sig"),
                               file_name="email_match_report.csv", mime="text/csv")
