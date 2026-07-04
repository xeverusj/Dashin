"""
dashboards/campaign_report_dashboard.py — Campaign Report Builder

Agency side: create/edit campaign performance reports per client.
Client side: view their report, download PDF, update lead status + notes.

Visual style matches the Comet PDF (dark navy, gold accents, coloured KPIs).
"""

import json
import streamlit as st
from datetime import datetime
from core.db import get_connection

# ── Colour palette (matches the PDF) ─────────────────────────────────────────
_DARK_BG   = "#0d1b2a"
_CARD_BG   = "#112033"
_BORDER    = "#1e3a5f"
_GOLD      = "#f0b429"
_GREEN     = "#22c55e"
_ORANGE    = "#f59e0b"
_RED       = "#ef4444"
_MUTED     = "#8da9c4"
_WHITE     = "#f0f4f8"

STATUS_COLOUR = {
    "Best":         _GREEN,
    "Strong":       _GREEN,
    "Good":         _ORANGE,
    "Marginal":     _ORANGE,
    "Small sample": _MUTED,
    "Low":          _RED,
    "Weak":         _RED,
    "Dead":         _RED,
    "Active":       _GREEN,
    "Paused":       _ORANGE,
}

LEAD_STATUSES = ["Open", "In Progress", "Meeting Booked", "Won", "Not Interested", "No Show"]

# ─────────────────────────────────────────────────────────────────────────────
# DB HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _q(sql, params=()):
    conn = get_connection()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows

def _run(sql, params=()):
    conn = get_connection()
    cur = conn.execute(sql, params)
    conn.commit()
    lid = cur.lastrowid
    conn.close()
    return lid

def _get_clients(org_id):
    return _q("SELECT id, name FROM clients WHERE org_id=? AND is_active=1 ORDER BY name", (org_id,))

def _get_reports(org_id, client_id=None):
    if client_id:
        return _q("SELECT * FROM campaign_reports WHERE org_id=? AND client_id=? ORDER BY updated_at DESC",
                  (org_id, client_id))
    return _q("SELECT cr.*, c.name as client_name FROM campaign_reports cr "
              "LEFT JOIN clients c ON c.id=cr.client_id "
              "WHERE cr.org_id=? ORDER BY cr.updated_at DESC", (org_id,))

def _get_report(report_id):
    rows = _q("SELECT * FROM campaign_reports WHERE id=?", (report_id,))
    return rows[0] if rows else None

def _get_campaigns(report_id):
    return _q("SELECT * FROM report_campaigns WHERE report_id=? ORDER BY sort_order, id", (report_id,))

def _get_weekly_periods(report_id):
    return _q("SELECT * FROM report_weekly_periods WHERE report_id=? ORDER BY period_order", (report_id,))

def _get_weekly_camps(period_id):
    return _q("SELECT * FROM report_weekly_campaigns WHERE period_id=? ORDER BY id", (period_id,))

def _get_crm_contacts(report_id):
    return _q("SELECT * FROM report_crm_contacts WHERE report_id=? ORDER BY sort_order, id", (report_id,))

def _save_report_totals(report_id):
    camps = _get_campaigns(report_id)
    cold  = sum(c["cold"]       for c in camps)
    fups  = sum(c["followups"]  for c in camps)
    total = sum(c["total"]      for c in camps)
    resp  = sum(c["responses"]  for c in camps)
    inter = sum(c["interested"] for c in camps)
    meet  = sum(c["meetings"]   for c in camps)
    crm_n = len(_get_crm_contacts(report_id))
    _run("""UPDATE campaign_reports SET
            total_cold=?, total_followups=?, total_emails=?,
            total_responses=?, total_interested=?, total_meetings=?,
            crm_count=?, campaigns_count=?, updated_at=datetime('now')
            WHERE id=?""",
         (cold, fups, total, resp, inter, meet, crm_n, len(camps), report_id))

def _pdf_inputs(report, camps, contacts):
    """Map DB rows to the plain dicts/lists services.report_pdf expects."""
    import json as _json

    # Client name for the header kicker
    client_name = ""
    if report.get("client_id"):
        rows = _q("SELECT name FROM clients WHERE id=?", (report["client_id"],))
        if rows:
            client_name = rows[0]["name"]

    report_d = dict(report)
    report_d["client_name"] = client_name

    camp_d = [{
        "name": c.get("name", ""), "cold": c.get("cold", 0),
        "followups": c.get("followups", 0), "total": c.get("total", 0),
        "responses": c.get("responses", 0), "booked": c.get("meetings", 0),
        "rate": c.get("rate", 0), "status": c.get("status", ""),
    } for c in camps]

    crm_d = [{
        "campaign_name": ct.get("campaign_name", ""),
        "contact_name": ct.get("contact_name", ""),
        "company": ct.get("company", ""), "role": ct.get("role", ""),
        "status": ct.get("status", ""), "notes": ct.get("notes", ""),
    } for ct in contacts]

    # analysis_notes is a JSON list of {title, body, action} sections.
    insights = []
    if report.get("analysis_notes"):
        try:
            insights = _json.loads(report["analysis_notes"])
        except Exception:
            insights = [{"title": "Analysis", "body": report["analysis_notes"], "action": ""}]

    return report_d, camp_d, crm_d, insights


def _html_report(report, campaigns, periods, crm_contacts):
    """Generate a printable HTML string that matches the Comet PDF style."""

    def _rate_color(r):
        if r >= 3.5: return _GREEN
        if r >= 2.0: return _ORANGE
        return _RED

    def _sc(status):
        return STATUS_COLOUR.get(status, _MUTED)

    # KPI row
    rate_overall = (report["total_responses"] / report["total_emails"] * 100
                    if report["total_emails"] else 0)
    int_rate     = (report["total_interested"] / report["total_emails"] * 100
                    if report["total_emails"] else 0)

    kpi_html = ""
    for val, label, sub in [
        (f"{report['total_emails']:,}",    "Total Emails",
         f"{report['total_cold']:,} cold · {report['total_followups']:,} follow-ups"),
        (f"{report['total_responses']:,}", "Responses",
         f"{rate_overall:.1f}% overall rate"),
        (f"{report['total_interested']:,}", "Interested",
         f"{int_rate:.1f}% interest rate"),
        (f"{report['total_meetings']:,}",  "Meetings Booked", "Confirmed & scheduled"),
        (f"{report['crm_count']:,}+",      "CRM Contacts",    "Across all campaigns"),
        (f"{report['campaigns_count']:,}", "Campaigns",       report.get("date_range","") or ""),
    ]:
        kpi_html += f"""
        <div class="kpi-card">
            <div class="kpi-val">{val}</div>
            <div class="kpi-lbl">{label}</div>
            <div class="kpi-sub">{sub}</div>
        </div>"""

    # Campaign rows
    camp_rows = ""
    for c in campaigns:
        sc = _sc(c["status"])
        rate = c["rate"] if c["rate"] else (c["responses"] / c["total"] * 100 if c["total"] else 0)
        name_style = f"color:{_GOLD};" if c["status"] in ("Dead",) else ""
        camp_rows += f"""
        <tr>
            <td style="{name_style}">{c["name"]}</td>
            <td>{c["cold"]:,}</td>
            <td>{c["followups"]:,}</td>
            <td style="color:{_GOLD};font-weight:600;">{c["total"]:,}</td>
            <td style="color:{_GREEN if c["responses"] else _MUTED};">{c["responses"]}</td>
            <td style="color:{_GREEN if c["interested"] else _MUTED};">{c["interested"]}</td>
            <td>{rate:.1f}%</td>
            <td style="color:{sc};font-weight:600;">{c["status"]}</td>
        </tr>"""

    # Weekly breakdown
    weekly_html = ""
    for p in periods:
        wcamps = _get_weekly_camps(p["id"])
        wrows = ""
        for wc in wcamps:
            wr = wc["rate"] if wc["rate"] else (wc["responses"] / wc["total"] * 100 if wc["total"] else 0)
            sc2 = _GREEN if wc["interested"] else (_ORANGE if wc["responses"] else _MUTED)
            wrows += f"""
            <tr>
                <td style="color:{_GOLD};">{wc["campaign_name"]}</td>
                <td>{wc["cold"]}</td>
                <td>{wc["followups"]}</td>
                <td style="color:{_GOLD};">{wc["total"]}</td>
                <td style="color:{_GREEN if wc['responses'] else _MUTED};">{wc["responses"]}</td>
                <td style="color:{sc2};">{wc["interested"]}</td>
                <td>{wr:.1f}%</td>
            </tr>"""
        prate = p["rate"] if p["rate"] else (p["total_responses"] / p["total_emails"] * 100 if p["total_emails"] else 0)
        weekly_html += f"""
        <div class="week-block">
            <div class="week-header">
                {p["period_label"]} &nbsp;·&nbsp;
                {p["total_emails"]:,} emails &nbsp;·&nbsp;
                {p["total_responses"]} responses &nbsp;·&nbsp;
                {p["total_interested"]} interested &nbsp;·&nbsp;
                {prate:.1f}% rate
            </div>
            <table class="data-table">
                <thead><tr>
                    <th>Campaign</th><th>Cold</th><th>Follow-ups</th>
                    <th>Total</th><th>Responses</th><th>Interested</th><th>Rate</th>
                </tr></thead>
                <tbody>{wrows}</tbody>
            </table>
        </div>"""

    # CRM contacts
    crm_rows = ""
    for ct in crm_contacts:
        st_col = _GREEN if ct["status"] and ("book" in ct["status"].lower() or "met" in ct["status"].lower()) \
                 else _ORANGE if ct["status"] else _MUTED
        crm_rows += f"""
        <tr>
            <td style="color:{_GOLD};">{ct.get("campaign_name","")}</td>
            <td><strong>{ct.get("contact_name","")}</strong></td>
            <td>{ct.get("company","")}</td>
            <td>{ct.get("role","")}</td>
            <td style="color:{st_col};font-weight:600;">{ct.get("status","")}</td>
            <td style="color:#aac;">{ct.get("notes","")}</td>
        </tr>"""

    analysis_html = ""
    if report.get("analysis_notes"):
        try:
            sections = json.loads(report["analysis_notes"])
            for s in sections:
                analysis_html += f"""
                <div class="analysis-block">
                    <div class="analysis-title">{s.get("title","")}</div>
                    <p>{s.get("body","").replace(chr(10),"<br>")}</p>
                    {f'<p class="action-line">{s["action"]}</p>' if s.get("action") else ""}
                </div>"""
        except Exception:
            analysis_html = f"<p>{report['analysis_notes']}</p>"

    now_str = datetime.now().strftime("%B %d, %Y")
    title   = report.get("title", "Campaign Performance Report")
    dr      = report.get("date_range", "")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700;900&display=swap');
  * {{ box-sizing: border-box; margin:0; padding:0; }}
  body {{
    background: {_DARK_BG};
    color: {_WHITE};
    font-family: 'Inter', sans-serif;
    font-size: 13px;
    line-height: 1.6;
    padding: 40px;
  }}
  .agency-label {{ color:{_GOLD}; font-size:11px; font-weight:700;
    letter-spacing:.12em; text-transform:uppercase; margin-bottom:8px; }}
  h1 {{ font-size:40px; font-weight:900; line-height:1.1; margin-bottom:4px; }}
  h2 {{ font-size:24px; font-weight:700; color:{_GOLD}; margin:40px 0 12px; }}
  .subtitle {{ color:{_MUTED}; font-size:13px; margin-bottom:32px; }}
  hr {{ border:none; border-top:1px solid {_BORDER}; margin:8px 0 32px; }}
  .kpi-grid {{ display:grid; grid-template-columns:repeat(6,1fr); gap:12px; margin-bottom:28px; }}
  .kpi-card {{
    background:{_CARD_BG}; border:1px solid {_BORDER};
    border-radius:8px; padding:16px 14px; text-align:center;
  }}
  .kpi-val {{ font-size:28px; font-weight:900; color:{_GOLD}; }}
  .kpi-lbl {{ font-size:11px; color:{_WHITE}; font-weight:600; margin:4px 0 2px; }}
  .kpi-sub {{ font-size:10px; color:{_MUTED}; }}
  .data-table {{ width:100%; border-collapse:collapse; margin:8px 0 0; }}
  .data-table th {{
    background:#1a2e45; color:{_MUTED}; font-size:11px; font-weight:600;
    text-align:left; padding:8px 10px; border-bottom:1px solid {_BORDER};
  }}
  .data-table td {{
    padding:9px 10px; border-bottom:1px solid rgba(30,58,95,0.5);
    font-size:12px; color:{_WHITE};
  }}
  .data-table tr:last-child td {{ border-bottom:none; }}
  .week-block {{
    background:{_CARD_BG}; border:1px solid {_BORDER};
    border-left:3px solid {_GOLD}; border-radius:6px;
    margin-bottom:16px; overflow:hidden;
  }}
  .week-header {{
    background:#1a2e45; padding:10px 16px;
    font-weight:700; font-size:12px; color:{_GOLD};
  }}
  .week-block .data-table {{ margin:0; }}
  .analysis-block {{
    background:{_CARD_BG}; border:1px solid {_BORDER};
    border-left:3px solid {_BORDER}; border-radius:6px;
    padding:20px; margin-bottom:16px;
  }}
  .analysis-title {{
    font-size:11px; font-weight:700; color:{_GOLD};
    text-transform:uppercase; letter-spacing:.1em; margin-bottom:8px;
  }}
  .action-line {{ color:{_GOLD}; font-weight:600; margin-top:10px; font-style:italic; }}
  .footer {{
    text-align:center; color:{_MUTED}; font-size:10px;
    margin-top:60px; border-top:1px solid {_BORDER}; padding-top:20px;
  }}
  @media print {{
    body {{ padding:20px; }}
    .week-block, .analysis-block {{ page-break-inside:avoid; }}
  }}
</style>
</head>
<body>
  <div class="agency-label">B2B Outreach — Full Campaign Report</div>
  <h1>Campaign Performance<br><span style="color:{_GOLD};">Summary Report</span></h1>
  <div class="subtitle">{dr} &nbsp;·&nbsp; {report['campaigns_count']} Campaigns</div>
  <hr>

  <div class="kpi-grid">{kpi_html}</div>

  <h2>Campaign Overview</h2>
  <table class="data-table">
    <thead><tr>
      <th>Campaign</th><th>Cold</th><th>Follow-ups</th>
      <th>Total</th><th>Responses</th><th>Interested</th><th>Rate</th><th>Status</th>
    </tr></thead>
    <tbody>{camp_rows}</tbody>
  </table>

  {'<h2>Weekly Breakdown</h2>' + weekly_html if weekly_html else ''}

  {'<h2>CRM Pipeline — Key Contacts</h2><table class="data-table"><thead><tr><th>Campaign</th><th>Contact</th><th>Company</th><th>Role</th><th>Status</th><th>Notes</th></tr></thead><tbody>' + crm_rows + '</tbody></table>' if crm_rows else ''}

  {'<h2>Analysis — What Is Working</h2>' + analysis_html if analysis_html else ''}

  <div class="footer">
    Report generated {now_str} &nbsp;·&nbsp; {title}
  </div>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# SHARED CSS (dark report theme)
# ─────────────────────────────────────────────────────────────────────────────

_REPORT_CSS = f"""
<style>
.rpt-section {{
    background:{_DARK_BG}; border-radius:12px;
    padding:24px; margin-bottom:20px;
    border:1px solid {_BORDER};
}}
.rpt-kpi-row {{ display:flex; gap:12px; flex-wrap:wrap; margin-bottom:8px; }}
.rpt-kpi {{
    background:{_CARD_BG}; border:1px solid {_BORDER};
    border-radius:8px; padding:16px 20px; min-width:130px; flex:1;
    text-align:center;
}}
.rpt-kpi-val {{ font-size:30px; font-weight:900; color:{_GOLD}; line-height:1; }}
.rpt-kpi-lbl {{ font-size:11px; color:{_WHITE}; font-weight:600; margin-top:4px; }}
.rpt-kpi-sub {{ font-size:10px; color:{_MUTED}; }}
.rpt-title {{
    font-size:28px; font-weight:900; color:{_WHITE};
    line-height:1.1; margin-bottom:4px;
}}
.rpt-subtitle {{ font-size:12px; color:{_MUTED}; margin-bottom:0; }}
.rpt-gold {{ color:{_GOLD}; }}
.rpt-green {{ color:{_GREEN}; }}
.rpt-orange {{ color:{_ORANGE}; }}
.rpt-red {{ color:{_RED}; }}
.rpt-muted {{ color:{_MUTED}; }}
.rpt-table {{
    width:100%; border-collapse:collapse;
    background:{_CARD_BG}; border-radius:8px; overflow:hidden;
}}
.rpt-table th {{
    background:#1a2e45; color:{_MUTED}; font-size:11px;
    text-align:left; padding:10px 12px; border-bottom:1px solid {_BORDER};
}}
.rpt-table td {{
    padding:10px 12px; border-bottom:1px solid rgba(30,58,95,0.4);
    font-size:12px; color:{_WHITE};
}}
.rpt-table tr:hover td {{ background:rgba(30,58,95,0.3); }}
.rpt-week-block {{
    background:{_CARD_BG}; border:1px solid {_BORDER};
    border-left:3px solid {_GOLD}; border-radius:6px;
    margin-bottom:12px; overflow:hidden;
}}
.rpt-week-hdr {{
    background:#1a2e45; padding:10px 16px;
    font-weight:700; font-size:12px; color:{_GOLD};
}}
.rpt-badge {{
    display:inline-block; padding:2px 10px; border-radius:20px;
    font-size:11px; font-weight:700;
}}
</style>
"""


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY
# ─────────────────────────────────────────────────────────────────────────────

def render(user: dict):
    st.markdown(_REPORT_CSS, unsafe_allow_html=True)
    role = user.get("role", "")
    is_client = role in ("client_admin", "client_user")

    if is_client:
        _render_client_view(user)
    else:
        _render_agency_view(user)


# ─────────────────────────────────────────────────────────────────────────────
# AGENCY VIEW
# ─────────────────────────────────────────────────────────────────────────────

def _render_agency_view(user):
    org_id = user["org_id"]

    st.markdown(f"""
    <div style="margin-bottom:24px;">
      <div class="rpt-title">📊 Campaign Report Builder</div>
      <div class="rpt-subtitle">Create and publish performance reports for clients</div>
    </div>""", unsafe_allow_html=True)

    clients = _get_clients(org_id)
    if not clients:
        st.warning("No clients found. Create a client first in Admin → Clients.")
        return

    col_sel, col_rpt, col_btn = st.columns([2, 3, 1])
    with col_sel:
        client_opts = {c["name"]: c["id"] for c in clients}
        chosen_client = st.selectbox("Client", list(client_opts.keys()), key="rpt_client")
        client_id = client_opts[chosen_client]

    reports = _get_reports(org_id, client_id)
    with col_rpt:
        rpt_opts = {f"{r['title']} ({r['date_range'] or 'No date'})  {'✅' if r['is_published'] else '📝'}": r["id"]
                    for r in reports}
        rpt_opts = {"➕ Create new report": None} | rpt_opts
        chosen_rpt_label = st.selectbox("Report", list(rpt_opts.keys()), key="rpt_select")
        report_id = rpt_opts[chosen_rpt_label]

    with col_btn:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        if report_id and st.button("🗑 Delete", key="rpt_del"):
            conn = get_connection()
            conn.execute("DELETE FROM report_crm_contacts WHERE report_id=?", (report_id,))
            conn.execute("DELETE FROM report_weekly_campaigns WHERE report_id=?", (report_id,))
            conn.execute("DELETE FROM report_weekly_periods WHERE report_id=?", (report_id,))
            conn.execute("DELETE FROM report_campaigns WHERE report_id=?", (report_id,))
            conn.execute("DELETE FROM campaign_reports WHERE id=?", (report_id,))
            conn.commit(); conn.close()
            st.rerun()

    if report_id is None:
        _render_create_report(org_id, client_id, user)
        return

    report = _get_report(report_id)
    if not report:
        st.error("Report not found.")
        return

    # Publish toggle
    pub_col, dl_col = st.columns([3, 1])
    with pub_col:
        published = bool(report["is_published"])
        lbl = "✅ Published — visible to client" if published else "📝 Draft — not visible to client"
        if st.toggle(lbl, value=published, key="rpt_pub"):
            if not published:
                _run("UPDATE campaign_reports SET is_published=1, updated_at=datetime('now') WHERE id=?", (report_id,))
                st.success("Published! Client can now view this report.")
                st.rerun()
        else:
            if published:
                _run("UPDATE campaign_reports SET is_published=0, updated_at=datetime('now') WHERE id=?", (report_id,))
                st.info("Moved back to draft.")
                st.rerun()

    # Download button
    camps    = _get_campaigns(report_id)
    periods  = _get_weekly_periods(report_id)
    contacts = _get_crm_contacts(report_id)
    with dl_col:
        html_content = _html_report(report, camps, periods, contacts)
        st.download_button("⬇️ HTML",
                           data=html_content.encode("utf-8"),
                           file_name=f"report_{report_id}.html",
                           mime="text/html",
                           use_container_width=True)

    # PDF downloads — full report + one-pager, both generated from this data.
    try:
        from services.report_pdf import build_full_report_pdf, build_onepager_pdf
        rpt_d, camp_d, crm_d, insights = _pdf_inputs(report, camps, contacts)
        p1, p2 = st.columns(2)
        with p1:
            st.download_button(
                "⬇ Full report (PDF)",
                data=build_full_report_pdf(rpt_d, camp_d, crm_d, insights),
                file_name=f"{(rpt_d.get('client_name') or 'client')}_full_report.pdf",
                mime="application/pdf", use_container_width=True)
        with p2:
            st.download_button(
                "⬇ One-pager (PDF)",
                data=build_onepager_pdf(rpt_d, camp_d, insights),
                file_name=f"{(rpt_d.get('client_name') or 'client')}_one_pager.pdf",
                mime="application/pdf", use_container_width=True)
    except Exception as _pdf_err:
        st.caption(f"PDF export unavailable: {_pdf_err}")

    st.markdown("---")

    tab_sum, tab_camps, tab_weekly, tab_crm, tab_analysis = st.tabs([
        "📋 Summary", "📈 Campaigns", "📅 Weekly", "👥 CRM Pipeline", "📝 Analysis"
    ])

    # ── Tab: Summary ──────────────────────────────────────────────────────────
    with tab_sum:
        _render_summary_edit(report, report_id)

    # ── Tab: Campaigns ────────────────────────────────────────────────────────
    with tab_camps:
        _render_campaigns_edit(report_id)

    # ── Tab: Weekly ───────────────────────────────────────────────────────────
    with tab_weekly:
        _render_weekly_edit(report_id)

    # ── Tab: CRM Pipeline ─────────────────────────────────────────────────────
    with tab_crm:
        _render_crm_edit(report_id)

    # ── Tab: Analysis ─────────────────────────────────────────────────────────
    with tab_analysis:
        _render_analysis_edit(report, report_id)


def _render_create_report(org_id, client_id, user):
    st.markdown("### Create New Report")
    with st.form("new_report_form"):
        title = st.text_input("Report Title", value="Campaign Performance Report")
        date_range = st.text_input("Date Range", placeholder="e.g. January 12 – May 17, 2026")
        submitted = st.form_submit_button("✅ Create Report")
    if submitted:
        rid = _run("""INSERT INTO campaign_reports
                      (org_id, client_id, title, date_range, created_by)
                      VALUES (?,?,?,?,?)""",
                   (org_id, client_id, title, date_range, user["id"]))
        st.success(f"Report created! (ID {rid})")
        st.rerun()


def _render_summary_edit(report, report_id):
    st.markdown("#### Edit Report Header & KPIs")
    with st.form("summary_form"):
        c1, c2 = st.columns(2)
        with c1:
            title      = st.text_input("Title", value=report["title"] or "")
            date_range = st.text_input("Date Range", value=report["date_range"] or "")
        with c2:
            meetings  = st.number_input("Meetings Booked", min_value=0, value=int(report["total_meetings"] or 0))
            crm_count = st.number_input("CRM Contacts (override)", min_value=0, value=int(report["crm_count"] or 0))

        st.caption("💡 Email totals are auto-calculated from the Campaigns tab. Override meetings & CRM count here.")
        if st.form_submit_button("💾 Save Header"):
            _run("""UPDATE campaign_reports SET title=?, date_range=?, total_meetings=?,
                    updated_at=datetime('now') WHERE id=?""",
                 (title, date_range, meetings, report_id))
            _save_report_totals(report_id)
            st.success("Saved!")
            st.rerun()

    # Live preview
    rpt = _get_report(report_id)
    if rpt:
        _render_kpi_cards(rpt)


def _render_campaigns_edit(report_id):
    st.markdown("#### Campaign Performance Table")

    camps = _get_campaigns(report_id)
    if camps:
        # Quick-edit table using data_editor
        import pandas as pd
        df = pd.DataFrame([dict(c) for c in camps])
        edit_cols = ["name", "cold", "followups", "responses", "interested", "meetings", "status"]
        df_edit = df[["id"] + edit_cols].copy()
        df_edit["rate"] = df.apply(
            lambda r: round(r["responses"] / r["total"] * 100, 1) if r["total"] else 0, axis=1)

        edited = st.data_editor(
            df_edit.drop(columns=["id"]),
            use_container_width=True,
            num_rows="fixed",
            column_config={
                "name":      st.column_config.TextColumn("Campaign", width="medium"),
                "cold":      st.column_config.NumberColumn("Cold", width="small"),
                "followups": st.column_config.NumberColumn("Follow-ups", width="small"),
                "responses": st.column_config.NumberColumn("Responses", width="small"),
                "interested":st.column_config.NumberColumn("Interested", width="small"),
                "meetings":  st.column_config.NumberColumn("Meetings", width="small"),
                "rate":      st.column_config.NumberColumn("Rate %", disabled=True, width="small"),
                "status":    st.column_config.SelectboxColumn("Status", width="small",
                               options=["Best","Strong","Good","Marginal","Small sample",
                                        "Low","Weak","Dead","Active","Paused"]),
            },
            key="camp_editor",
        )

        if st.button("💾 Save Campaign Table"):
            conn = get_connection()
            for i, row in edited.iterrows():
                camp_id = df_edit.iloc[i]["id"]
                total = int(row["cold"]) + int(row["followups"])
                rate  = round(row["responses"] / total * 100, 2) if total else 0
                conn.execute("""UPDATE report_campaigns SET
                    name=?, cold=?, followups=?, total=?,
                    responses=?, interested=?, meetings=?, rate=?, status=?
                    WHERE id=?""",
                    (row["name"], int(row["cold"]), int(row["followups"]), total,
                     int(row["responses"]), int(row["interested"]), int(row["meetings"]),
                     rate, row["status"], camp_id))
            conn.commit(); conn.close()
            _save_report_totals(report_id)
            st.success("Campaigns saved!")
            st.rerun()

    st.markdown("---")
    st.markdown("##### Add Campaign")
    with st.form("add_camp_form"):
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        n    = c1.text_input("Name")
        cold = c2.number_input("Cold", min_value=0, value=0)
        fups = c3.number_input("Follow-ups", min_value=0, value=0)
        resp = c4.number_input("Responses", min_value=0, value=0)
        inter= c5.number_input("Interested", min_value=0, value=0)
        stat = c6.selectbox("Status", ["Active","Best","Strong","Good","Marginal","Low","Weak","Dead","Paused"])
        meet = st.number_input("Meetings", min_value=0, value=0)
        if st.form_submit_button("➕ Add Campaign"):
            if n:
                total = cold + fups
                rate  = round(resp / total * 100, 2) if total else 0
                _run("""INSERT INTO report_campaigns
                        (report_id, name, cold, followups, total, responses,
                         interested, meetings, rate, status)
                        VALUES (?,?,?,?,?,?,?,?,?,?)""",
                     (report_id, n, cold, fups, total, resp, inter, meet, rate, stat))
                _save_report_totals(report_id)
                st.rerun()
            else:
                st.warning("Enter a campaign name.")


def _render_weekly_edit(report_id):
    st.markdown("#### Weekly Breakdown")

    periods = _get_weekly_periods(report_id)
    for p in periods:
        with st.expander(f"📅 {p['period_label']}  —  {p['total_emails']:,} emails · {p['total_responses']} responses · {p['total_interested']} interested"):
            wcamps = _get_weekly_camps(p["id"])
            if wcamps:
                import pandas as pd
                df_wc = pd.DataFrame([dict(w) for w in wcamps])
                edited_wc = st.data_editor(
                    df_wc[["campaign_name","cold","followups","responses","interested"]],
                    use_container_width=True, num_rows="fixed",
                    key=f"wc_{p['id']}",
                )
                if st.button("💾 Save", key=f"wc_save_{p['id']}"):
                    conn = get_connection()
                    for i, row in edited_wc.iterrows():
                        wid = df_wc.iloc[i]["id"]
                        total = int(row["cold"]) + int(row["followups"])
                        rate  = round(row["responses"] / total * 100, 2) if total else 0
                        conn.execute("""UPDATE report_weekly_campaigns SET
                            campaign_name=?, cold=?, followups=?, total=?,
                            responses=?, interested=?, rate=? WHERE id=?""",
                            (row["campaign_name"], int(row["cold"]), int(row["followups"]),
                             total, int(row["responses"]), int(row["interested"]), rate, wid))
                    # Update period totals
                    t_emails = sum(int(r["cold"]) + int(r["followups"]) for _, r in edited_wc.iterrows())
                    t_resp   = sum(int(r["responses"])  for _, r in edited_wc.iterrows())
                    t_inter  = sum(int(r["interested"]) for _, r in edited_wc.iterrows())
                    t_rate   = round(t_resp / t_emails * 100, 2) if t_emails else 0
                    conn.execute("""UPDATE report_weekly_periods SET
                        total_emails=?, total_responses=?, total_interested=?, rate=?
                        WHERE id=?""", (t_emails, t_resp, t_inter, t_rate, p["id"]))
                    conn.commit(); conn.close()
                    st.success("Saved!")
                    st.rerun()

            # Add campaign to this period
            with st.form(f"add_wc_{p['id']}"):
                w1, w2, w3, w4, w5 = st.columns(5)
                wn   = w1.text_input("Campaign")
                wc   = w2.number_input("Cold",       min_value=0, value=0, key=f"wc_{p['id']}_c")
                wf   = w3.number_input("Follow-ups", min_value=0, value=0, key=f"wc_{p['id']}_f")
                wr   = w4.number_input("Responses",  min_value=0, value=0, key=f"wc_{p['id']}_r")
                wi   = w5.number_input("Interested", min_value=0, value=0, key=f"wc_{p['id']}_i")
                if st.form_submit_button("➕ Add"):
                    if wn:
                        wtotal = wc + wf
                        wrate  = round(wr / wtotal * 100, 2) if wtotal else 0
                        _run("""INSERT INTO report_weekly_campaigns
                                (period_id, report_id, campaign_name, cold, followups,
                                 total, responses, interested, rate)
                                VALUES (?,?,?,?,?,?,?,?,?)""",
                             (p["id"], report_id, wn, wc, wf, wtotal, wr, wi, wrate))
                        st.rerun()

            # Delete period
            if st.button("🗑 Delete this period", key=f"del_period_{p['id']}"):
                conn = get_connection()
                conn.execute("DELETE FROM report_weekly_campaigns WHERE period_id=?", (p["id"],))
                conn.execute("DELETE FROM report_weekly_periods WHERE id=?", (p["id"],))
                conn.commit(); conn.close()
                st.rerun()

    st.markdown("---")
    st.markdown("##### Add Weekly Period")
    with st.form("add_period_form"):
        pl = st.text_input("Period Label", placeholder="e.g. Jan 12–18 · 469 emails · 25 responses")
        if st.form_submit_button("➕ Add Period"):
            if pl:
                next_ord = len(periods)
                _run("""INSERT INTO report_weekly_periods
                        (report_id, period_label, period_order) VALUES (?,?,?)""",
                     (report_id, pl, next_ord))
                st.rerun()


def _render_crm_edit(report_id):
    st.markdown("#### CRM Pipeline — Key Contacts")

    contacts = _get_crm_contacts(report_id)
    if contacts:
        import pandas as pd
        df_c = pd.DataFrame([dict(c) for c in contacts])
        edit_cols = ["campaign_name","contact_name","company","role","email",
                     "website","status","met","notes","lead_status","client_notes","interest_status"]
        df_show = df_c[["id"] + [col for col in edit_cols if col in df_c.columns]].copy()

        edited_c = st.data_editor(
            df_show.drop(columns=["id"]),
            use_container_width=True, num_rows="fixed",
            column_config={
                "campaign_name":  st.column_config.TextColumn("Campaign"),
                "contact_name":   st.column_config.TextColumn("Contact"),
                "company":        st.column_config.TextColumn("Company"),
                "role":           st.column_config.TextColumn("Role"),
                "email":          st.column_config.TextColumn("Email"),
                "website":        st.column_config.TextColumn("Website"),
                "status":         st.column_config.TextColumn("Status"),
                "met":            st.column_config.TextColumn("Met?"),
                "notes":          st.column_config.TextColumn("Notes"),
                "lead_status":    st.column_config.SelectboxColumn("Lead Status",
                                    options=LEAD_STATUSES),
                "client_notes":   st.column_config.TextColumn("Client Notes"),
                "interest_status":st.column_config.TextColumn("Interest Status"),
            },
            key="crm_editor",
        )
        if st.button("💾 Save CRM Table"):
            conn = get_connection()
            for i, row in edited_c.iterrows():
                cid = df_show.iloc[i]["id"]
                conn.execute("""UPDATE report_crm_contacts SET
                    campaign_name=?, contact_name=?, company=?, role=?,
                    email=?, website=?, status=?, met=?, notes=?,
                    lead_status=?, client_notes=?, interest_status=?,
                    updated_at=datetime('now') WHERE id=?""",
                    (row.get("campaign_name",""), row.get("contact_name",""),
                     row.get("company",""), row.get("role",""),
                     row.get("email",""), row.get("website",""),
                     row.get("status",""), row.get("met",""),
                     row.get("notes",""), row.get("lead_status","Open"),
                     row.get("client_notes",""), row.get("interest_status",""), cid))
            conn.commit(); conn.close()
            _save_report_totals(report_id)
            st.success("CRM saved!")
            st.rerun()

    st.markdown("---")
    st.markdown("##### Add Contact")
    with st.form("add_crm_form"):
        r1c1, r1c2, r1c3, r1c4 = st.columns(4)
        camp_n = r1c1.text_input("Campaign")
        name   = r1c2.text_input("Full Name")
        comp   = r1c3.text_input("Company")
        role   = r1c4.text_input("Role")
        r2c1, r2c2, r2c3, r2c4 = st.columns(4)
        email  = r2c1.text_input("Email")
        web    = r2c2.text_input("Website")
        status = r2c3.text_input("Status", placeholder="e.g. Booked ✓")
        met    = r2c4.text_input("Met?")
        notes  = st.text_area("Notes", height=60)
        if st.form_submit_button("➕ Add Contact"):
            if name:
                _run("""INSERT INTO report_crm_contacts
                        (report_id, campaign_name, contact_name, company, role,
                         email, website, status, met, notes, lead_status)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                     (report_id, camp_n, name, comp, role, email, web, status, met, notes, "Open"))
                _save_report_totals(report_id)
                st.rerun()
            else:
                st.warning("Contact name is required.")


def _render_analysis_edit(report, report_id):
    st.markdown("#### Honest Analysis Sections")
    st.caption("These appear in the PDF as highlighted analysis blocks with action items.")

    existing = []
    if report.get("analysis_notes"):
        try:
            existing = json.loads(report["analysis_notes"])
        except Exception:
            existing = []

    # Show existing sections
    for idx, s in enumerate(existing):
        with st.expander(f"Section {idx+1}: {s.get('title','')}", expanded=False):
            with st.form(f"analysis_edit_{idx}"):
                new_title  = st.text_input("Title", value=s.get("title",""), key=f"at_{idx}")
                new_body   = st.text_area("Body",   value=s.get("body",""),  key=f"ab_{idx}", height=100)
                new_action = st.text_input("Action Line", value=s.get("action",""), key=f"aa_{idx}")
                col_save, col_del = st.columns(2)
                if col_save.form_submit_button("💾 Save"):
                    existing[idx] = {"title": new_title, "body": new_body, "action": new_action}
                    _run("UPDATE campaign_reports SET analysis_notes=?, updated_at=datetime('now') WHERE id=?",
                         (json.dumps(existing), report_id))
                    st.rerun()
            if st.button("🗑 Delete Section", key=f"del_sec_{idx}"):
                existing.pop(idx)
                _run("UPDATE campaign_reports SET analysis_notes=?, updated_at=datetime('now') WHERE id=?",
                     (json.dumps(existing), report_id))
                st.rerun()

    st.markdown("---")
    st.markdown("##### Add New Analysis Section")
    with st.form("add_analysis_form"):
        atitle  = st.text_input("Title", placeholder="e.g. TOP PERFORMER: MALTA (3.8% RATE)")
        abody   = st.text_area("Body",   placeholder="Analysis text...", height=100)
        aaction = st.text_input("Action Line", placeholder="e.g. Prioritise Malta follow-ups now...")
        if st.form_submit_button("➕ Add Section"):
            if atitle:
                existing.append({"title": atitle, "body": abody, "action": aaction})
                _run("UPDATE campaign_reports SET analysis_notes=?, updated_at=datetime('now') WHERE id=?",
                     (json.dumps(existing), report_id))
                st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# CLIENT VIEW
# ─────────────────────────────────────────────────────────────────────────────

def _render_client_view(user):
    org_id    = user["org_id"]
    client_id = user.get("client_id")

    # Find published reports for this client
    reports = _q("""SELECT * FROM campaign_reports
                    WHERE org_id=? AND client_id=? AND is_published=1
                    ORDER BY updated_at DESC""", (org_id, client_id))

    if not reports:
        st.markdown(f"""
        <div class="rpt-section" style="text-align:center; padding:60px 20px;">
          <div style="font-size:48px; margin-bottom:16px;">📊</div>
          <div class="rpt-title" style="font-size:22px;">No reports published yet</div>
          <div class="rpt-subtitle" style="margin-top:8px;">
            Your campaign report will appear here once it's ready.
          </div>
        </div>""", unsafe_allow_html=True)
        return

    # Let client select if multiple
    if len(reports) > 1:
        rpt_opts = {f"{r['title']} ({r['date_range'] or ''})": r["id"] for r in reports}
        chosen = st.selectbox("Select Report", list(rpt_opts.keys()))
        report_id = rpt_opts[chosen]
    else:
        report_id = reports[0]["id"]

    report   = _get_report(report_id)
    camps    = _get_campaigns(report_id)
    periods  = _get_weekly_periods(report_id)
    contacts = _get_crm_contacts(report_id)

    # ── Header ────────────────────────────────────────────────────────────────
    dl_html = _html_report(report, camps, periods, contacts)
    col_hdr, col_dl = st.columns([4, 1])
    with col_hdr:
        st.markdown(f"""
        <div style="margin-bottom:20px;">
          <div style="color:{_GOLD}; font-size:11px; font-weight:700;
               letter-spacing:.12em; text-transform:uppercase;">
            B2B OUTREACH — FULL CAMPAIGN REPORT
          </div>
          <div class="rpt-title">{report.get('title','Campaign Performance Report')}</div>
          <div class="rpt-subtitle">{report.get('date_range','')}</div>
        </div>""", unsafe_allow_html=True)
    with col_dl:
        st.markdown("<div style='height:36px'></div>", unsafe_allow_html=True)
        st.download_button("⬇️ Download Report",
                           data=dl_html.encode("utf-8"),
                           file_name="campaign_report.html",
                           mime="text/html",
                           use_container_width=True)

    # ── KPI Cards ─────────────────────────────────────────────────────────────
    _render_kpi_cards(report)

    # ── Campaign Table ────────────────────────────────────────────────────────
    if camps:
        st.markdown(f"<h3 style='color:{_GOLD}; margin:28px 0 12px;'>Campaign Overview</h3>",
                    unsafe_allow_html=True)
        rows_html = ""
        for c in camps:
            total = c["total"] or (c["cold"] + c["followups"])
            rate  = c["rate"] if c["rate"] else (c["responses"] / total * 100 if total else 0)
            sc    = STATUS_COLOUR.get(c["status"], _MUTED)
            name_col = f"color:{_GOLD};" if c["status"] == "Dead" else ""
            rows_html += f"""<tr>
              <td style="{name_col}">{c['name']}</td>
              <td>{c['cold']:,}</td>
              <td>{c['followups']:,}</td>
              <td style="color:{_GOLD};font-weight:600;">{total:,}</td>
              <td style="color:{_GREEN if c['responses'] else _MUTED};">{c['responses']}</td>
              <td style="color:{_GREEN if c['interested'] else _MUTED};">{c['interested']}</td>
              <td>{rate:.1f}%</td>
              <td><span class="rpt-badge" style="background:{sc}22;color:{sc};">{c['status']}</span></td>
            </tr>"""

        st.markdown(f"""
        <table class="rpt-table">
          <thead><tr>
            <th>Campaign</th><th>Cold</th><th>Follow-ups</th>
            <th>Total</th><th>Responses</th><th>Interested</th><th>Rate</th><th>Status</th>
          </tr></thead>
          <tbody>{rows_html}</tbody>
        </table>""", unsafe_allow_html=True)

    # ── Weekly Breakdown ──────────────────────────────────────────────────────
    if periods:
        st.markdown(f"<h3 style='color:{_GOLD}; margin:28px 0 12px;'>Weekly Breakdown</h3>",
                    unsafe_allow_html=True)
        for p in periods:
            wcamps = _get_weekly_camps(p["id"])
            prate  = p["rate"] if p["rate"] else (p["total_responses"]/p["total_emails"]*100 if p["total_emails"] else 0)
            wrows  = ""
            for wc in wcamps:
                wr = wc["rate"] if wc["rate"] else (wc["responses"]/wc["total"]*100 if wc["total"] else 0)
                wrows += f"""<tr>
                  <td style="color:{_GOLD};">{wc['campaign_name']}</td>
                  <td>{wc['cold']}</td><td>{wc['followups']}</td>
                  <td style="color:{_GOLD};">{wc['total']}</td>
                  <td style="color:{_GREEN if wc['responses'] else _MUTED};">{wc['responses']}</td>
                  <td style="color:{_GREEN if wc['interested'] else _MUTED};">{wc['interested']}</td>
                  <td>{wr:.1f}%</td>
                </tr>"""
            st.markdown(f"""
            <div class="rpt-week-block">
              <div class="rpt-week-hdr">
                {p['period_label']} &nbsp;·&nbsp;
                {p['total_emails']:,} emails &nbsp;·&nbsp;
                {p['total_responses']} responses &nbsp;·&nbsp;
                {p['total_interested']} interested &nbsp;·&nbsp;
                {prate:.1f}% rate
              </div>
              <table class="rpt-table" style="border-radius:0;">
                <thead><tr>
                  <th>Campaign</th><th>Cold</th><th>Follow-ups</th>
                  <th>Total</th><th>Responses</th><th>Interested</th><th>Rate</th>
                </tr></thead>
                <tbody>{wrows}</tbody>
              </table>
            </div>""", unsafe_allow_html=True)

    # ── CRM Pipeline ──────────────────────────────────────────────────────────
    if contacts:
        st.markdown(f"<h3 style='color:{_GOLD}; margin:28px 0 12px;'>CRM Pipeline — Your Leads</h3>",
                    unsafe_allow_html=True)
        st.caption("You can update the **Lead Status** and add **your own notes** for each contact.")

        for ct in contacts:
            st.markdown(f"""
            <div style="background:{_CARD_BG}; border:1px solid {_BORDER};
                 border-radius:8px; padding:14px 18px; margin-bottom:10px; display:flex;
                 align-items:center; gap:20px;">
              <div style="flex:2;">
                <div style="font-weight:700; color:{_WHITE}; font-size:13px;">{ct.get('contact_name','')}</div>
                <div style="color:{_MUTED}; font-size:11px;">{ct.get('role','')} · {ct.get('company','')}</div>
                <div style="color:{_GOLD}; font-size:11px; margin-top:2px;">{ct.get('campaign_name','')}</div>
              </div>
              <div style="flex:1; color:{_MUTED}; font-size:11px;">
                <div style="color:{_WHITE}; font-weight:600;">{ct.get('status','')}</div>
                <div>{ct.get('notes','')[:80]}</div>
              </div>
            </div>""", unsafe_allow_html=True)

            with st.expander(f"✏️ Update: {ct.get('contact_name','')}"):
                with st.form(f"client_crm_{ct['id']}"):
                    new_status = st.selectbox("Lead Status",
                                              LEAD_STATUSES,
                                              index=LEAD_STATUSES.index(ct.get("lead_status","Open"))
                                                    if ct.get("lead_status") in LEAD_STATUSES else 0,
                                              key=f"ls_{ct['id']}")
                    new_notes = st.text_area("Your Notes",
                                             value=ct.get("client_notes","") or "",
                                             height=80, key=f"cn_{ct['id']}")
                    if st.form_submit_button("💾 Save"):
                        _run("""UPDATE report_crm_contacts SET
                                lead_status=?, client_notes=?, updated_at=datetime('now')
                                WHERE id=?""",
                             (new_status, new_notes, ct["id"]))
                        st.success("Updated!")
                        st.rerun()

    # ── Analysis ──────────────────────────────────────────────────────────────
    if report.get("analysis_notes"):
        try:
            sections = json.loads(report["analysis_notes"])
            if sections:
                st.markdown(f"<h3 style='color:{_GOLD}; margin:28px 0 12px;'>Campaign Analysis</h3>",
                            unsafe_allow_html=True)
                for s in sections:
                    st.markdown(f"""
                    <div style="background:{_CARD_BG}; border:1px solid {_BORDER};
                         border-left:3px solid {_BORDER}; border-radius:8px;
                         padding:20px; margin-bottom:14px;">
                      <div style="font-size:11px; font-weight:700; color:{_GOLD};
                           text-transform:uppercase; letter-spacing:.1em; margin-bottom:8px;">
                        {s.get('title','')}
                      </div>
                      <div style="color:{_WHITE}; font-size:13px; line-height:1.7;">
                        {s.get('body','').replace(chr(10),'<br>')}
                      </div>
                      {'<div style="color:'+_GOLD+'; font-weight:600; margin-top:12px; font-style:italic;">'+s["action"]+'</div>' if s.get("action") else ''}
                    </div>""", unsafe_allow_html=True)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# SHARED UI
# ─────────────────────────────────────────────────────────────────────────────

def _render_kpi_cards(report):
    rate_overall = (report["total_responses"] / report["total_emails"] * 100
                    if report["total_emails"] else 0)
    int_rate     = (report["total_interested"] / report["total_emails"] * 100
                    if report["total_emails"] else 0)

    kpis = [
        (f"{report['total_emails']:,}",     "Total Emails",
         f"{report['total_cold']:,} cold · {report['total_followups']:,} follow-ups"),
        (f"{report['total_responses']:,}",  "Responses",       f"{rate_overall:.1f}% overall rate"),
        (f"{report['total_interested']:,}", "Interested",      f"{int_rate:.1f}% interest rate"),
        (f"{report['total_meetings']:,}",   "Meetings Booked", "Confirmed & scheduled"),
        (f"{report['crm_count']:,}+",       "CRM Contacts",    "Across all campaigns"),
        (f"{report['campaigns_count']:,}",  "Campaigns",       report.get("date_range","") or ""),
    ]

    cards = "".join(f"""
    <div class="rpt-kpi">
        <div class="rpt-kpi-val">{v}</div>
        <div class="rpt-kpi-lbl">{l}</div>
        <div class="rpt-kpi-sub">{s}</div>
    </div>""" for v, l, s in kpis)

    st.markdown(f'<div class="rpt-kpi-row">{cards}</div>', unsafe_allow_html=True)
