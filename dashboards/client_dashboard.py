"""
dashboards/client_dashboard.py — Dashin Research Platform
Full client portal. Only shows campaigns marked is_visible_to_client=1.
Tabs: Home · All Leads · Campaigns · Files & Templates · Notes
"""

import html
import streamlit as st
import pandas as pd
from datetime import datetime, timezone, date
from core.db import get_connection
from services.notification_service import (
    get_all, mark_all_read, unread_count,
)
from services.report_service import (
    get_weekly_stats, get_campaign_totals, generate_xlsx,
)

def _rows(cursor_result):
    """Convert list of sqlite3.Row to list of dicts."""
    return [dict(r) for r in cursor_result]

def _row(cursor_result):
    """Convert sqlite3.Row to dict, or return {} if None."""
    return dict(cursor_result) if cursor_result else {}


STYLES = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;600&family=Lato:wght@300;400;700&display=swap');

.stApp { background: var(--surface); font-family: 'Lato', sans-serif; }
section[data-testid="stSidebar"] { background: var(--text-1) !important; }
section[data-testid="stSidebar"] * { color: var(--surface-2) !important; }

.portal-header {
    background: linear-gradient(135deg, var(--text-1) 0%, #2C2A27 60%, #3D3A35 100%);
    border-radius: 12px;
    padding: 28px 32px;
    color: var(--surface-2);
    margin-bottom: 28px;
    position: relative;
    overflow: hidden;
}
.portal-header::before {
    content: '';
    position: absolute;
    top: -40px; right: -40px;
    width: 180px; height: 180px;
    background: rgba(201,169,110,0.08);
    border-radius: 50%;
}
.portal-client-name {
    font-family: 'Playfair Display', serif;
    font-size: 26px;
    font-weight: 600;
    letter-spacing: -0.3px;
    margin-bottom: 6px;
}
.portal-badge {
    display: inline-block;
    background: rgba(201,169,110,0.2);
    color: var(--accent);
    border: 1px solid rgba(201,169,110,0.3);
    padding: 3px 12px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
    margin-bottom: 14px;
}
.portal-icp {
    font-size: 13px;
    color: var(--text-3);
    max-width: 500px;
    line-height: 1.5;
}

.home-stat {
    background: white;
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 20px;
    text-align: center;
    box-shadow: 0 1px 4px rgba(0,0,0,0.04);
}
.home-stat-num {
    font-family: 'Playfair Display', serif;
    font-size: 36px;
    font-weight: 600;
    color: var(--text-1);
    line-height: 1;
}
.home-stat-label {
    font-size: 12px;
    color: var(--text-3);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-top: 6px;
}

.camp-progress-card {
    background: white;
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 20px 24px;
    margin-bottom: 16px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.04);
}
.camp-name {
    font-family: 'Playfair Display', serif;
    font-size: 18px;
    font-weight: 600;
    color: var(--text-1);
    margin-bottom: 4px;
}
.camp-status-chip {
    display: inline-block;
    padding: 3px 12px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.chip-active   { background: var(--success-bg); color: var(--success); }
.chip-building { background: var(--info-bg); color: var(--info); }
.chip-paused   { background: var(--surface-2); color: #E65100; }
.chip-ready    { background: var(--success-border); color: #1B5E20; }

.meeting-card {
    background: linear-gradient(135deg, var(--success-bg) 0%, #F1F8E9 100%);
    border: 1px solid var(--success-border);
    border-radius: 8px;
    padding: 14px 18px;
    margin-bottom: 10px;
    display: flex;
    align-items: center;
    gap: 16px;
}
.meeting-date {
    background: var(--success);
    color: white;
    border-radius: 8px;
    padding: 8px 12px;
    text-align: center;
    min-width: 52px;
}
.meeting-day   { font-size: 22px; font-weight: 700; line-height: 1; }
.meeting-month { font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }
.meeting-info  { flex: 1; }
.meeting-name  { font-weight: 700; font-size: 14px; color: var(--text-1); }
.meeting-co    { font-size: 13px; color: var(--text-2); }

.notif-card {
    background: var(--surface-2);
    border: 1px solid var(--accent-border);
    border-radius: 8px;
    padding: 12px 16px;
    margin-bottom: 8px;
}
.notif-title { font-weight: 600; font-size: 13px; color: var(--text-1); }
.notif-body  { font-size: 12px; color: var(--text-2); margin-top: 3px; }
.notif-time  { font-size: 11px; color: var(--text-3); margin-top: 4px; }

.lead-table { font-size: 13px; }

.status-pill {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 600;
}
.s-booked            { background:var(--success-border); color:#1B5E20; }
.s-interested        { background:#E1F5FE; color:#01579B; }
.s-meeting_requested { background:var(--surface-2); color:#E65100; }
.s-responded         { background:var(--success-bg); color:var(--success); }
.s-waiting           { background:#FFF9C4; color:#827717; }
.s-contacted         { background:var(--info-bg); color:var(--info); }
.s-not_interested    { background:#FFCDD2; color:#B71C1C; }
.s-enriched          { background:#F3E5F5; color:#4A148C; }
.s-no_email          { background:var(--surface-2); color:var(--text-3); }

.file-card {
    background: white;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 18px;
    margin-bottom: 10px;
    display: flex;
    align-items: center;
    justify-content: space-between;
}
.file-icon { font-size: 24px; margin-right: 12px; }
.file-name { font-weight: 600; font-size: 13px; }
.file-meta { font-size: 11px; color: var(--text-3); margin-top: 2px; }

.approval-pending  { color: #E65100; }
.approval-approved { color: var(--success); }
.approval-rejected { color: var(--error); }

.note-card {
    background: white;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 18px;
    margin-bottom: 10px;
}
.note-author { font-weight: 700; font-size: 13px; }
.note-role   { font-size: 11px; color: var(--accent); font-style: italic; }
.note-text   { font-size: 13px; color: #333; margin-top: 8px; line-height: 1.6; }
.note-time   { font-size: 11px; color: var(--text-3); margin-top: 6px; }
</style>
"""

CRM_STATUSES = [
    "contacted", "waiting", "responded", "interested",
    "meeting_requested", "booked", "not_interested", "no_show"
]


def render(user: dict):
    st.markdown(STYLES, unsafe_allow_html=True)

    org_id    = user["org_id"]
    client_id = user["client_id"]
    user_id   = user["id"]

    if not client_id:
        st.error("Your account is not linked to a client profile. "
                 "Please contact your account manager.")
        return

    # Load client profile
    conn   = get_connection()
    client = conn.execute(
        "SELECT * FROM clients WHERE id=? AND org_id=?",
        (client_id, org_id)
    ).fetchone()
    conn.close()

    if not client:
        st.error("Client profile not found.")
        return

    # ── Portal Header ─────────────────────────────────────────────────
    icp_preview = (client["icp_notes"] or "")[:120]
    if len(client["icp_notes"] or "") > 120:
        icp_preview += "…"

    st.markdown(f"""
    <div class="portal-header">
        <div class="portal-badge">Client Portal</div>
        <div class="portal-client-name">{html.escape(client['name'])}</div>
        <div class="portal-icp">{html.escape(icp_preview or client.get('industry',''))}</div>
    </div>
    """, unsafe_allow_html=True)

    # ── Notifications bell ────────────────────────────────────────────
    unread = unread_count(user_id)
    if unread > 0:
        if st.button(f"🔔 {unread} new notification{'s' if unread > 1 else ''}",
                     type="secondary"):
            mark_all_read(user_id)
            st.rerun()

    # ── Tabs ──────────────────────────────────────────────────────────
    tab1, tab2, tab3, tab_report, tab_email, tab_tmpl, tab4, tab5 = st.tabs([
        "🏠 Home",
        "👥 My Leads",
        "📁 Campaigns",
        "📊 Campaign Report",
        "📧 Email Accounts",
        "📝 Templates",
        "📎 Files",
        "💬 Notes",
    ])

    with tab1:
        _render_home(org_id, client_id, client, user_id)
    with tab2:
        _render_leads(org_id, client_id, user_id)
    with tab3:
        _render_campaigns(org_id, client_id, user_id)
    with tab_report:
        try:
            from dashboards.campaign_report_dashboard import render as _render_rpt
            _render_rpt(user)
        except Exception as _rpt_err:
            st.error(f"Report error: {_rpt_err}")
    with tab_email:
        _render_email_accounts(org_id, client_id)
    with tab_tmpl:
        _render_templates(org_id, client_id, user_id)
    with tab4:
        _render_files(org_id, client_id, user_id)
    with tab5:
        _render_notes(org_id, client_id, user_id, user)


# ── EMAIL ACCOUNTS (read-only credentials) ────────────────────────────────────

def _render_email_accounts(org_id: int, client_id: int):
    from services.client_portal_service import list_email_accounts
    st.markdown("#### 📧 Your Email Accounts")
    st.caption("The mailboxes your campaigns send from. Passwords are hidden — "
               "click **Show** to reveal.")
    accounts = list_email_accounts(org_id, client_id)
    if not accounts:
        st.info("No email accounts have been set up yet. Your account manager will "
                "add them here.")
        return
    for a in accounts:
        with st.container(border=True):
            top = st.columns([3, 2])
            with top[0]:
                st.markdown(f"**{a.get('label') or a.get('email_address')}**")
                st.markdown(f"✉️ `{a.get('email_address','')}`")
                if a.get("provider"):
                    st.caption(f"Provider: {a['provider']}")
            with top[1]:
                show = st.toggle("Show password", key=f"pw_show_{a['id']}")
                pw = a.get("password") or ""
                st.text_input("Password", value=pw if show else "•" * (len(pw) or 8),
                              key=f"pw_{a['id']}", disabled=True,
                              label_visibility="collapsed")
                if a.get("webmail_url"):
                    st.markdown(f"[Open webmail →]({a['webmail_url']})")


# ── TEMPLATES (own page) ──────────────────────────────────────────────────────

def _render_templates(org_id: int, client_id: int, user_id: int):
    from services.client_portal_service import get_client_templates
    st.markdown("#### 📝 Email Templates")
    st.caption("The templates your campaigns use.")
    templates = get_client_templates(org_id, client_id)
    if not templates:
        st.info("No templates shared yet.")
        return
    for t in templates:
        with st.expander(t.get("name") or t.get("subject") or f"Template #{t.get('id')}"):
            if t.get("subject"):
                st.markdown(f"**Subject:** {t['subject']}")
            body = t.get("body") or t.get("content") or ""
            if body:
                st.text_area("Body", value=body, height=200,
                             key=f"tmpl_{t.get('id')}", disabled=True,
                             label_visibility="collapsed")


# ── HOME ──────────────────────────────────────────────────────────────────────

def _render_home(org_id: int, client_id: int, client, user_id: int):

    conn = get_connection()

    # Aggregate stats across all visible campaigns
    stats = conn.execute("""
        SELECT
            COUNT(DISTINCT cl.lead_id)                        AS total_leads,
            SUM(CASE WHEN e.email IS NOT NULL
                      AND e.email != '' THEN 1 END)           AS with_email,
            SUM(CASE WHEN cl.crm_status='booked' THEN 1 END)  AS meetings,
            COUNT(DISTINCT ca.id)                              AS campaigns
        FROM campaigns ca
        JOIN campaign_leads cl ON cl.campaign_id = ca.id
        LEFT JOIN enrichment e ON e.lead_id = cl.lead_id
        WHERE ca.org_id=? AND ca.client_id=?
          AND ca.is_visible_to_client=1
    """, (org_id, client_id)).fetchone()

    # Upcoming meetings
    meetings = conn.execute("""
        SELECT l.full_name, co.name AS company,
               cl.meeting_date, ca.name AS campaign_name
        FROM campaign_leads cl
        JOIN campaigns ca ON ca.id = cl.campaign_id
        JOIN leads l      ON l.id  = cl.lead_id
        LEFT JOIN companies co ON co.id = l.company_id
        WHERE ca.org_id=? AND ca.client_id=?
          AND ca.is_visible_to_client=1
          AND cl.crm_status='booked'
          AND cl.meeting_date IS NOT NULL
        ORDER BY cl.meeting_date ASC
        LIMIT 5
    """, (org_id, client_id)).fetchall()

    # Recent campaign activity
    recent_campaigns = conn.execute("""
        SELECT ca.id, ca.name, ca.status, ca.marked_ready_at,
               (SELECT COUNT(*) FROM campaign_leads
                WHERE campaign_id=ca.id) AS lead_count,
               (SELECT SUM(total_sent) FROM campaign_weekly_stats
                WHERE campaign_id=ca.id) AS emails_sent,
               (SELECT SUM(meetings_done) FROM campaign_weekly_stats
                WHERE campaign_id=ca.id) AS meetings_done
        FROM campaigns ca
        WHERE ca.org_id=? AND ca.client_id=?
          AND ca.is_visible_to_client=1
        ORDER BY ca.marked_ready_at DESC
        LIMIT 3
    """, (org_id, client_id)).fetchall()

    # Recent files
    recent_files = conn.execute("""
        SELECT cf.file_name, cf.file_type, cf.uploaded_at,
               ca.name AS campaign_name
        FROM campaign_files cf
        JOIN campaigns ca ON ca.id = cf.campaign_id
        WHERE ca.org_id=? AND ca.client_id=?
          AND ca.is_visible_to_client=1
        ORDER BY cf.uploaded_at DESC
        LIMIT 3
    """, (org_id, client_id)).fetchall()

    conn.close()

    # ── Stats row ─────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    for col, (num, label) in zip(
        [c1, c2, c3, c4],
        [
            (stats["total_leads"] or 0,   "Total Leads"),
            (stats["with_email"]  or 0,   "Emails Found"),
            (stats["meetings"]    or 0,   "Meetings Booked"),
            (stats["campaigns"]   or 0,   "Campaigns"),
        ]
    ):
        with col:
            st.markdown(f"""
            <div class="home-stat">
                <div class="home-stat-num">{num}</div>
                <div class="home-stat-label">{label}</div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("---")

    col_left, col_right = st.columns([1, 1])

    # ── Upcoming meetings ─────────────────────────────────────────────
    with col_left:
        st.markdown("#### 📅 Confirmed Meetings")
        if meetings:
            for m in meetings:
                try:
                    d = date.fromisoformat(m["meeting_date"][:10])
                    day_str   = d.strftime("%d")
                    month_str = d.strftime("%b")
                except Exception:
                    day_str = "?"; month_str = "?"

                st.markdown(f"""
                <div class="meeting-card">
                    <div class="meeting-date">
                        <div class="meeting-day">{day_str}</div>
                        <div class="meeting-month">{month_str}</div>
                    </div>
                    <div class="meeting-info">
                        <div class="meeting-name">{m['full_name']}</div>
                        <div class="meeting-co">
                            {m.get('company','') or '—'} ·
                            {m['campaign_name']}
                        </div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("No confirmed meetings yet.")

    # ── Campaign progress ─────────────────────────────────────────────
    with col_right:
        st.markdown("#### 📊 Campaign Progress")
        if recent_campaigns:
            for c in recent_campaigns:
                chip_cls = {
                    "active": "chip-active",
                    "building": "chip-building",
                    "paused": "chip-paused",
                    "ready": "chip-ready",
                }.get(c["status"], "chip-building")

                sent    = c["emails_sent"]     or 0
                done    = c["meetings_done"]   or 0
                leads   = c["lead_count"]      or 0

                st.markdown(f"""
                <div class="camp-progress-card">
                    <div style="display:flex;align-items:center;
                                justify-content:space-between;margin-bottom:10px;">
                        <div class="camp-name">{html.escape(c['name'])}</div>
                        <span class="camp-status-chip {chip_cls}">{html.escape(c['status'])}</span>
                    </div>
                    <div style="display:grid;grid-template-columns:repeat(3,1fr);
                                gap:8px;text-align:center;">
                        <div>
                            <div style="font-size:20px;font-weight:700;">{leads}</div>
                            <div style="font-size:11px;color:var(--text-3);">Contacts</div>
                        </div>
                        <div>
                            <div style="font-size:20px;font-weight:700;">{sent}</div>
                            <div style="font-size:11px;color:var(--text-3);">Emails Sent</div>
                        </div>
                        <div>
                            <div style="font-size:20px;font-weight:700;">{done}</div>
                            <div style="font-size:11px;color:var(--text-3);">Meetings</div>
                        </div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("No active campaigns yet.")

    # ── Recent files ──────────────────────────────────────────────────
    if recent_files:
        st.markdown("---")
        st.markdown("#### 📎 Recently Shared Files")
        for f in recent_files:
            icon = {"template": "📝", "case_study": "📄",
                    "brief": "📋", "report": "📊"}.get(
                f["file_type"], "📎")
            st.markdown(f"""
            <div class="file-card">
                <div style="display:flex;align-items:center;">
                    <span class="file-icon">{icon}</span>
                    <div>
                        <div class="file-name">{f['file_name']}</div>
                        <div class="file-meta">
                            {f['campaign_name']} ·
                            {(f['uploaded_at'] or '')[:10]}
                        </div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)

    # ── Notifications ──────────────────────────────────────────────────
    notifs = get_all(user_id, limit=5)
    if notifs:
        st.markdown("---")
        st.markdown("#### 🔔 Recent Notifications")
        for n in notifs[:3]:
            st.markdown(f"""
            <div class="notif-card">
                <div class="notif-title">{html.escape(n['title'])}</div>
                <div class="notif-body">{html.escape(n.get('body',''))}</div>
                <div class="notif-time">{(n.get('created_at') or '')[:16].replace('T',' ')}</div>
            </div>
            """, unsafe_allow_html=True)


# ── MY LEADS ─────────────────────────────────────────────────────────────────

def _render_leads(org_id: int, client_id: int, user_id: int):

    conn = get_connection()

    # Get all campaigns for filter
    camps = conn.execute("""
        SELECT id, name FROM campaigns
        WHERE org_id=? AND client_id=?
          AND is_visible_to_client=1
        ORDER BY name
    """, (org_id, client_id)).fetchall()

    all_leads = conn.execute("""
        SELECT l.full_name, l.title,
               co.name   AS company,
               l.persona,
               e.email, e.linkedin_url, e.country,
               e.industry AS enrich_industry,
               cl.crm_status,
               cl.meeting_date,
               ca.name   AS campaign_name,
               la.event_name
        FROM campaign_leads cl
        JOIN campaigns ca  ON ca.id  = cl.campaign_id
        JOIN leads l       ON l.id   = cl.lead_id
        LEFT JOIN companies co ON co.id = l.company_id
        LEFT JOIN enrichment e  ON e.lead_id = l.id
        LEFT JOIN (
            SELECT lead_id, MIN(event_name) AS event_name
            FROM lead_appearances GROUP BY lead_id
        ) la ON la.lead_id = l.id
        WHERE ca.org_id=? AND ca.client_id=?
          AND ca.is_visible_to_client=1
        ORDER BY cl.crm_status, l.full_name
    """, (org_id, client_id)).fetchall()
    conn.close()

    if not all_leads:
        st.info("No leads available yet. Your campaign data will appear here "
                "once your account manager marks it as ready.")
        return

    # ── Filters ───────────────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        search = st.text_input("Search", placeholder="Name, company…",
                               label_visibility="collapsed")
    with col2:
        camp_names = ["All campaigns"] + [c["name"] for c in camps]
        camp_filter = st.selectbox("Campaign", camp_names,
                                   label_visibility="collapsed")
    with col3:
        email_filter = st.selectbox(
            "Email", ["All", "Email found", "No email"],
            label_visibility="collapsed"
        )
    with col4:
        persona_filter = st.selectbox(
            "Persona",
            ["All", "Decision Maker", "Senior Influencer",
             "Influencer", "IC", "Unknown"],
            label_visibility="collapsed"
        )

    # Apply filters
    filtered = list(all_leads)
    if search:
        s = search.lower()
        filtered = [r for r in filtered
                    if s in (r["full_name"] or "").lower()
                    or s in (r["company"] or "").lower()]
    if camp_filter != "All campaigns":
        filtered = [r for r in filtered
                    if r["campaign_name"] == camp_filter]
    if email_filter == "Email found":
        filtered = [r for r in filtered
                    if r["email"] and "@" in r["email"]]
    elif email_filter == "No email":
        filtered = [r for r in filtered
                    if not r["email"] or "@" not in (r["email"] or "")]
    if persona_filter != "All":
        filtered = [r for r in filtered
                    if r["persona"] == persona_filter]

    # ── Tabs: Email Found / No Email ──────────────────────────────────
    with_email = [r for r in filtered
                  if r["email"] and "@" in (r["email"] or "")]
    no_email   = [r for r in filtered
                  if not r["email"] or "@" not in (r["email"] or "")]

    t1, t2 = st.tabs([
        f"✉ Email Found ({len(with_email)})",
        f"◯ No Email ({len(no_email)})",
    ])

    with t1:
        _render_lead_table(with_email, show_email=True,
                           client_id=client_id,
                           campaign_name=camp_filter
                           if camp_filter != "All campaigns" else "all")
    with t2:
        _render_lead_table(no_email, show_email=False,
                           client_id=client_id,
                           campaign_name=camp_filter
                           if camp_filter != "All campaigns" else "all")


def _render_lead_table(leads: list, show_email: bool,
                       client_id: int, campaign_name: str):
    if not leads:
        st.info("No leads in this view.")
        return

    st.caption(f"{len(leads)} lead(s)")

    # Export button
    df = pd.DataFrame([{
        "Name":         r["full_name"],
        "Company":      r.get("company",""),
        "Title":        r.get("title",""),
        "Email":        r.get("email","") if show_email else "",
        "LinkedIn":     r.get("linkedin_url",""),
        "Country":      r.get("country",""),
        "Industry":     r.get("enrich_industry",""),
        "Persona":      r.get("persona",""),
        "Status":       (r.get("crm_status","") or "").replace("_"," ").title(),
        "Meeting Date": r.get("meeting_date","") or "",
        "Campaign":     r.get("campaign_name",""),
        "Event Source": r.get("event_name",""),
    } for r in leads])

    st.download_button(
        f"⬇ Export CSV",
        data=df.to_csv(index=False),
        file_name=f"leads_{campaign_name}_{date.today()}.csv",
        mime="text/csv",
    )

    # Render table
    rows_html = ""
    for r in leads:
        status  = r.get("crm_status","") or "new"
        pill    = f'<span class="status-pill s-{status}">{status.replace("_"," ").title()}</span>'
        email   = r.get("email","") or ""
        email_cell = (f'<a href="mailto:{email}" style="color:var(--info);">{email}</a>'
                      if show_email and "@" in email else
                      '<span style="color:var(--text-3);">—</span>')
        li_url  = r.get("linkedin_url","") or ""
        li_cell = (f'<a href="{li_url}" target="_blank" '
                   f'style="color:#0077B5;">LinkedIn</a>'
                   if li_url else "—")
        meeting = r.get("meeting_date","") or ""
        meet_cell = f'✅ {meeting[:10]}' if meeting else ""

        rows_html += f"""
        <tr>
            <td><b>{r['full_name']}</b><br>
                <small style="color:var(--text-3);">{r.get('company','')}</small></td>
            <td style="font-size:12px;color:var(--text-2);">{r.get('title','')}</td>
            <td>{pill}{f'<br><small style="color:var(--success);">{meet_cell}</small>' if meet_cell else ''}</td>
            <td>{email_cell}</td>
            <td>{li_cell}</td>
            <td style="font-size:12px;color:var(--text-3);">{r.get('country','')}</td>
            <td style="font-size:12px;color:var(--text-3);">{r.get('campaign_name','')}</td>
        </tr>
        """

    st.markdown(f"""
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead>
            <tr style="background:var(--text-1);color:white;">
                <th style="padding:10px;text-align:left;">Name</th>
                <th style="padding:10px;text-align:left;">Title</th>
                <th style="padding:10px;text-align:left;">Status</th>
                <th style="padding:10px;text-align:left;">Email</th>
                <th style="padding:10px;text-align:left;">LinkedIn</th>
                <th style="padding:10px;text-align:left;">Country</th>
                <th style="padding:10px;text-align:left;">Campaign</th>
            </tr>
        </thead>
        <tbody>{rows_html}</tbody>
    </table>
    """, unsafe_allow_html=True)


# ── CAMPAIGNS ─────────────────────────────────────────────────────────────────

def _render_campaigns(org_id: int, client_id: int, user_id: int):
    conn = get_connection()
    camps = conn.execute("""
        SELECT ca.*,
               (SELECT COUNT(*) FROM campaign_leads
                WHERE campaign_id=ca.id)              AS lead_count,
               (SELECT COUNT(*) FROM campaign_leads
                WHERE campaign_id=ca.id
                  AND crm_status='booked')            AS meetings_booked,
               (SELECT SUM(total_sent) FROM campaign_weekly_stats
                WHERE campaign_id=ca.id)              AS total_sent,
               (SELECT SUM(responded) FROM campaign_weekly_stats
                WHERE campaign_id=ca.id)              AS total_responded
        FROM campaigns ca
        WHERE ca.org_id=? AND ca.client_id=?
          AND ca.is_visible_to_client=1
        ORDER BY ca.marked_ready_at DESC
    """, (org_id, client_id)).fetchall()
    conn.close()

    if not camps:
        st.info("No campaigns available yet.")
        return

    for camp in camps:
        with st.expander(
            f"📁 {camp['name']} — "
            f"{camp['lead_count']} leads · "
            f"{camp['meetings_booked'] or 0} meetings booked",
            expanded=False
        ):
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Contacts", camp["lead_count"])
            col2.metric("Emails Sent", camp["total_sent"] or 0)
            col3.metric("Responded", camp["total_responded"] or 0)
            col4.metric("Meetings", camp["meetings_booked"] or 0)

            # Weekly stats chart
            weekly = get_weekly_stats(camp["id"])
            if weekly:
                df = pd.DataFrame(weekly)
                if "week_label" in df.columns:
                    df = df.set_index("week_label")
                    plot_cols = [c for c in
                                 ["total_sent","responded","interested",
                                  "meetings_done"]
                                 if c in df.columns]
                    if plot_cols:
                        st.line_chart(df[plot_cols])

            # Download XLSX
            xlsx = generate_xlsx(camp["id"], camp["name"], "")
            if xlsx:
                st.download_button(
                    "⬇ Download Report (.xlsx)",
                    data=xlsx,
                    file_name=f"{camp['name']}_report.xlsx",
                    mime="application/vnd.openxmlformats-officedocument"
                         ".spreadsheetml.sheet",
                    key=f"dl_xlsx_{camp['id']}",
                )


# ── FILES & TEMPLATES ─────────────────────────────────────────────────────────

def _render_files(org_id: int, client_id: int, user_id: int):
    conn = get_connection()
    files = conn.execute("""
        SELECT cf.*, ca.name AS campaign_name,
               u.name AS uploaded_by_name
        FROM campaign_files cf
        JOIN campaigns ca ON ca.id = cf.campaign_id
        LEFT JOIN users u ON u.id = cf.uploaded_by
        WHERE ca.org_id=? AND ca.client_id=?
          AND ca.is_visible_to_client=1
        ORDER BY cf.uploaded_at DESC
    """, (org_id, client_id)).fetchall()

    templates = conn.execute("""
        SELECT ct.*, ca.name AS campaign_name
        FROM campaign_templates ct
        JOIN campaigns ca ON ca.id = ct.campaign_id
        WHERE ca.org_id=? AND ca.client_id=?
          AND ca.is_visible_to_client=1
        ORDER BY ct.campaign_id, ct.sequence_step, ct.version DESC
    """, (org_id, client_id)).fetchall()
    conn.close()

    t1, t2, t3 = st.tabs(["📎 Files", "📝 Templates", "⬆ Upload"])

    with t1:
        if not files:
            st.info("No files shared yet.")
        else:
            for f in files:
                icon = {"template": "📝", "case_study": "📄",
                        "brief": "📋", "report": "📊"}.get(
                    f["file_type"], "📎")
                st.markdown(f"""
                <div class="file-card">
                    <div style="display:flex;align-items:center;">
                        <span class="file-icon">{icon}</span>
                        <div>
                            <div class="file-name">{f['file_name']}</div>
                            <div class="file-meta">
                                {f['campaign_name']} ·
                                Uploaded by {f['uploaded_by_name'] or '—'} ·
                                {(f['uploaded_at'] or '')[:10]}
                            </div>
                        </div>
                    </div>
                </div>
                """, unsafe_allow_html=True)

                if f.get("file_data"):
                    st.download_button(
                        f"⬇ Download",
                        data=f["file_data"],
                        file_name=f["file_name"],
                        key=f"dl_file_{f['id']}",
                    )

    with t2:
        if not templates:
            st.info("No templates shared yet.")
        else:
            for t in templates:
                approval_icon = {
                    "approved": "🟢", "pending": "🟡",
                    "rejected": "🔴", "changes_requested": "🟠"
                }.get(t["approval_status"], "⚪")

                with st.expander(
                    f"Step {t['sequence_step']} — {t['campaign_name']} "
                    f"v{t['version']} {approval_icon}",
                    expanded=False
                ):
                    if t.get("subject"):
                        st.markdown(f"**Subject:** {t['subject']}")
                    st.text_area("Body", value=t["body"] or "",
                                 height=180,
                                 key=f"tmpl_{t['id']}",
                                 disabled=True)

                    # Client can approve/request changes
                    if t["approval_status"] == "pending":
                        col1, col2 = st.columns(2)
                        with col1:
                            if st.button("✅ Approve Template",
                                         key=f"app_tmpl_{t['id']}",
                                         use_container_width=True):
                                _approve_template(t["id"], user_id)
                                st.rerun()
                        with col2:
                            with st.form(f"tmpl_note_{t['id']}"):
                                note = st.text_input("Request changes")
                                if st.form_submit_button(
                                    "Request Changes",
                                    use_container_width=True
                                ):
                                    _request_template_changes(
                                        t["id"], user_id, note
                                    )
                                    st.rerun()

                    if t.get("client_notes"):
                        st.caption(f"Your notes: {t['client_notes']}")

    with t3:
        st.subheader("Upload a File")
        st.caption("Upload case studies, briefs, or other materials "
                   "to share with your campaign manager.")

        conn2  = get_connection()
        camps2 = conn2.execute("""
            SELECT id, name FROM campaigns
            WHERE org_id=? AND client_id=?
              AND is_visible_to_client=1
            ORDER BY name
        """, (org_id, client_id)).fetchall()
        conn2.close()

        if not camps2:
            st.info("No campaigns available to attach files to.")
            return

        with st.form("upload_file_form"):
            camp_map = {c["name"]: c["id"] for c in camps2}
            camp_sel = st.selectbox("Campaign", list(camp_map.keys()))
            file_type = st.selectbox("File type",
                                     ["case_study", "brief", "other"],
                                     format_func=lambda x:
                                     x.replace("_", " ").title())
            uploaded = st.file_uploader("Choose file",
                                        type=["pdf","doc","docx",
                                              "ppt","pptx","png",
                                              "jpg","xlsx","csv"])
            if st.form_submit_button("Upload") and uploaded:
                conn3 = get_connection()
                conn3.execute("""
                    INSERT INTO campaign_files
                        (campaign_id, org_id, file_name, file_type,
                         file_data, file_size, uploaded_by, uploaded_at)
                    SELECT ?, org_id, ?, ?, ?, ?, ?, ?
                    FROM campaigns WHERE id=?
                """, (camp_map[camp_sel],
                      uploaded.name, file_type,
                      uploaded.read(), uploaded.size,
                      user_id,
                      datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                      camp_map[camp_sel]))
                conn3.commit()
                conn3.close()
                st.success("File uploaded!")
                st.rerun()


# ── NOTES ─────────────────────────────────────────────────────────────────────

def _render_notes(org_id: int, client_id: int,
                  user_id: int, user: dict):
    conn  = get_connection()
    camps = conn.execute("""
        SELECT id, name FROM campaigns
        WHERE org_id=? AND client_id=?
          AND is_visible_to_client=1
        ORDER BY name
    """, (org_id, client_id)).fetchall()
    conn.close()

    if not camps:
        st.info("No campaigns available.")
        return

    camp_map = {c["name"]: c["id"] for c in camps}
    selected = st.selectbox("Campaign", list(camp_map.keys()))
    camp_id  = camp_map[selected]

    # Load notes (exclude internal ones)
    conn2 = get_connection()
    notes = conn2.execute("""
        SELECT cn.*, u.name AS author_name, u.role AS author_role
        FROM campaign_notes cn
        JOIN users u ON u.id = cn.author_id
        WHERE cn.campaign_id=? AND cn.is_internal=0
        ORDER BY cn.created_at ASC
    """, (camp_id,)).fetchall()
    conn2.close()

    # Display thread
    if notes:
        for n in notes:
            is_me = n["author_id"] == user_id
            align = "right" if is_me else "left"
            bg    = "var(--success-bg)" if is_me else "var(--surface-2)"
            role_label = {
                "manager": "Account Manager",
                "campaign_manager": "Campaign Manager",
                "org_admin": "Team",
                "client_admin": "You (Admin)",
                "client_user": "You",
            }.get(n["author_role"], n["author_role"].replace("_"," ").title())

            st.markdown(f"""
            <div style="text-align:{align};margin-bottom:12px;">
                <div style="display:inline-block;max-width:70%;
                            background:{bg};border-radius:10px;
                            padding:12px 16px;text-align:left;">
                    <div style="font-weight:700;font-size:12px;
                                color:var(--accent);">{role_label}</div>
                    <div style="font-size:13px;color:var(--text-1);
                                margin-top:4px;line-height:1.6;">
                        {n['note']}
                    </div>
                    <div style="font-size:11px;color:var(--text-3);margin-top:6px;">
                        {(n['created_at'] or '')[:16].replace('T',' ')}
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("No messages yet. Start a conversation below.")

    st.markdown("---")

    # New note form
    with st.form(f"new_note_form_{camp_id}"):
        note_text = st.text_area("Leave a note or question",
                                 height=80,
                                 placeholder="Ask your campaign manager anything…")
        if st.form_submit_button("Send", use_container_width=True):
            if note_text.strip():
                conn3 = get_connection()
                conn3.execute("""
                    INSERT INTO campaign_notes
                        (campaign_id, author_id, author_role,
                         note, is_internal, created_at)
                    VALUES (?,?,?,?,0,?)
                """, (camp_id, user_id, user["role"],
                      note_text.strip(),
                      datetime.now(timezone.utc).replace(tzinfo=None).isoformat()))
                conn3.commit()
                conn3.close()
                st.rerun()


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _approve_template(template_id: int, user_id: int):
    conn = get_connection()
    conn.execute("""
        UPDATE campaign_templates
        SET approval_status='approved', approved_by=?, approved_at=?
        WHERE id=?
    """, (user_id, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), template_id))
    conn.commit()
    conn.close()


def _request_template_changes(template_id: int,
                               user_id: int, note: str):
    conn = get_connection()
    conn.execute("""
        UPDATE campaign_templates
        SET approval_status='changes_requested',
            client_notes=?
        WHERE id=?
    """, (note, template_id))
    conn.commit()
    conn.close()
