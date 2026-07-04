"""
dashboards/campaign_manager_dashboard.py — Dashin Research Platform
Campaign Manager workspace.
- CRM view per campaign (matches Excel format)
- Update lead statuses + notes
- Download enriched contacts CSV
- View + copy outreach templates
- Enter weekly stats → auto-generate report
- Download XLSX report
"""

import io
import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
from core.db import get_connection
from services.report_service import (
    save_weekly_stats, get_weekly_stats,
    get_campaign_totals, get_crm_snapshot,
    generate_xlsx, week_label, current_week_start,
)
from services.notification_service import notify_meeting_booked

STYLES = """
<style>
/* Campaign manager specific — shared components in core/styles.py */
</style>
"""

CRM_STATUSES = [
    "new", "contacted", "waiting", "responded", "interested",
    "meeting_requested", "booked", "not_interested", "no_show"
]


def render(user: dict):
    from core.styles import inject_shared_css
    inject_shared_css()
    st.markdown(STYLES, unsafe_allow_html=True)
    org_id  = user["org_id"]
    user_id = user["id"]

    st.markdown(f"""
    <div class="cm-header">
        <div class="cm-title">Campaign Manager</div>
        <div class="cm-sub">{user['name']} · {date.today().strftime('%d %b %Y')}</div>
    </div>
    """, unsafe_allow_html=True)

    # Campaign selector
    conn      = get_connection()
    campaigns = conn.execute("""
        SELECT ca.id, ca.name, ca.status,
               cl.name AS client_name,
               (SELECT COUNT(*) FROM campaign_leads
                WHERE campaign_id = ca.id) AS lead_count
        FROM campaigns ca
        LEFT JOIN clients cl ON cl.id = ca.client_id
        WHERE ca.org_id=?
          AND ca.status NOT IN ('closed')
        ORDER BY ca.created_at DESC
    """, (org_id,)).fetchall()
    conn.close()

    if not campaigns:
        st.info("No active campaigns.")
        return

    camp_map     = {f"{c['name']} — {c['client_name'] or 'No client'}": c
                    for c in campaigns}
    selected_key = st.selectbox("Select campaign", list(camp_map.keys()),
                                label_visibility="collapsed")
    campaign     = camp_map[selected_key]
    campaign_id  = campaign["id"]

    # Stats row
    totals = get_campaign_totals(campaign_id)
    crm    = get_crm_snapshot(campaign_id)

    status_counts = {}
    for row in crm:
        s = row.get("status", "new")
        status_counts[s] = status_counts.get(s, 0) + 1

    cols = st.columns(6)
    stats = [
        ("Total Leads",    campaign["lead_count"]),
        ("Booked",         status_counts.get("booked", 0)),
        ("Interested",     status_counts.get("interested", 0) +
                           status_counts.get("meeting_requested", 0)),
        ("Responded",      status_counts.get("responded", 0)),
        ("Emails Sent",    totals.get("total_sent", 0)),
        ("Meetings Done",  totals.get("total_meetings", 0)),
    ]
    for i, (label, value) in enumerate(stats):
        with cols[i]:
            st.markdown(f"""
            <div class="stats-card">
                <div class="stats-num">{value}</div>
                <div class="stats-label">{label}</div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("---")

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📋 CRM View",
        "✏️ Update Status",
        "📧 Templates",
        "📊 Weekly Report",
        "⬇️ Download",
    ])

    with tab1:
        _render_crm_view(crm, campaign_id)
    with tab2:
        _render_status_update(campaign_id, org_id, user_id, crm)
    with tab3:
        _render_templates(campaign_id, org_id, user_id)
    with tab4:
        _render_weekly_report(campaign_id, user_id)
    with tab5:
        _render_downloads(campaign_id,
                          campaign["name"],
                          campaign.get("client_name", ""),
                          crm)


# ── CRM VIEW ──────────────────────────────────────────────────────────────────

def _render_crm_view(crm: list, campaign_id: int):
    if not crm:
        st.info("No leads in this campaign yet.")
        return

    # Filter
    col1, col2 = st.columns([2, 1])
    with col1:
        search = st.text_input("Search name / company",
                               placeholder="Filter...",
                               label_visibility="collapsed")
    with col2:
        status_filter = st.selectbox(
            "Status",
            ["All"] + CRM_STATUSES,
            label_visibility="collapsed"
        )

    filtered = crm
    if search:
        s = search.lower()
        filtered = [r for r in filtered
                    if s in (r.get("full_name","")).lower()
                    or s in (r.get("company","")).lower()]
    if status_filter != "All":
        filtered = [r for r in filtered
                    if r.get("status") == status_filter]

    st.caption(f"{len(filtered)} of {len(crm)} leads")

    # Build HTML table
    rows_html = ""
    for r in filtered:
        status = r.get("status", "new")
        pill   = f'<span class="status-pill s-{status}">{status.replace("_"," ")}</span>'
        email  = r.get("email") or "—"
        email_str = f'<a href="mailto:{email}">{email}</a>' if "@" in email else email
        meeting = r.get("meeting_date") or ""
        meeting_str = f"📅 {meeting[:10]}" if meeting else ""
        notes   = (r.get("notes") or "")[:60]
        notes_str = f'{notes}{"…" if len(r.get("notes","")) > 60 else ""}'

        rows_html += f"""
        <tr>
            <td><b>{r.get('full_name','')}</b><br>
                <small style="color:#888;">{r.get('company','')}</small></td>
            <td style="color:#888;">{r.get('role','')}</td>
            <td>{pill}{f'<br><small style="color:#2E7D32">{meeting_str}</small>' if meeting_str else ''}</td>
            <td>{email_str}</td>
            <td style="color:#888;font-size:12px;">{r.get('outreach_from','')}</td>
            <td style="font-size:12px;color:#666;">{notes_str}</td>
        </tr>
        """

    st.markdown(f"""
    <table class="crm-table">
        <thead>
            <tr>
                <th>Name / Company</th>
                <th>Role</th>
                <th>Status</th>
                <th>Email</th>
                <th>Sent From</th>
                <th>Notes</th>
            </tr>
        </thead>
        <tbody>{rows_html}</tbody>
    </table>
    """, unsafe_allow_html=True)


# ── STATUS UPDATE ─────────────────────────────────────────────────────────────

def _render_status_update(campaign_id: int, org_id: int,
                           user_id: int, crm: list):
    if not crm:
        st.info("No leads to update.")
        return

    lead_options = {
        f"{r['full_name']} — {r.get('company','?')}": r
        for r in crm
    }
    selected_key = st.selectbox("Select lead to update",
                                list(lead_options.keys()))
    lead_row     = lead_options[selected_key]

    current_status = lead_row.get("status", "new")
    st.caption(f"Current status: **{current_status.replace('_',' ').title()}**")

    with st.form("update_status_form"):
        new_status = st.selectbox(
            "New status",
            CRM_STATUSES,
            index=CRM_STATUSES.index(current_status)
            if current_status in CRM_STATUSES else 0,
            format_func=lambda x: x.replace("_", " ").title()
        )
        next_step  = st.text_input("Next step",
                                   value=lead_row.get("next_step") or "")
        outreach   = st.text_input("Outreach sent from (email)",
                                   value=lead_row.get("outreach_from") or "")
        meeting_date = st.date_input(
            "Meeting date (if booked)",
            value=None
        ) if new_status == "booked" else None

        notes      = st.text_area("Notes / email thread summary",
                                  value=lead_row.get("notes") or "",
                                  height=100)
        submitted  = st.form_submit_button("Update", use_container_width=True)

    if submitted:
        conn = get_connection()
        now  = datetime.utcnow().isoformat()
        conn.execute("""
            UPDATE campaign_leads
            SET crm_status=?, next_step=?, outreach_from=?,
                meeting_date=?, notes=?,
                last_updated_by=?, last_updated_at=?
            WHERE campaign_id=? AND lead_id=(
                SELECT id FROM leads
                WHERE full_name=? AND org_id=?
                LIMIT 1
            )
        """, (new_status, next_step, outreach,
              meeting_date.isoformat() if meeting_date else None,
              notes, user_id, now,
              campaign_id,
              lead_row["full_name"], org_id))

        # Audit log
        conn.execute("""
            INSERT INTO crm_updates
                (campaign_id, lead_id, old_status, new_status,
                 note, meeting_date, changed_by, changed_by_role, changed_at)
            SELECT ?, cl.lead_id, ?, ?, ?, ?, ?, ?, ?
            FROM campaign_leads cl
            JOIN leads l ON l.id = cl.lead_id
            WHERE cl.campaign_id=? AND l.full_name=? AND l.org_id=?
            LIMIT 1
        """, (campaign_id, current_status, new_status,
              notes, meeting_date.isoformat() if meeting_date else None,
              user_id, user["role"] if (user := st.session_state.get("user")) else "campaign_manager",
              now, campaign_id, lead_row["full_name"], org_id))

        conn.commit()
        conn.close()

        # Notify client if meeting booked
        if new_status == "booked" and meeting_date:
            conn2 = get_connection()
            camp  = conn2.execute(
                "SELECT client_id FROM campaigns WHERE id=?",
                (campaign_id,)
            ).fetchone()
            conn2.close()
            if camp and camp["client_id"]:
                notify_meeting_booked(
                    org_id, campaign_id,
                    lead_row["full_name"],
                    meeting_date.isoformat(),
                    camp["client_id"]
                )

        st.success(f"Updated to: {new_status.replace('_',' ').title()}")
        st.rerun()


# ── TEMPLATES ─────────────────────────────────────────────────────────────────

def _render_templates(campaign_id: int, org_id: int, user_id: int):
    conn      = get_connection()
    templates = conn.execute("""
        SELECT * FROM campaign_templates
        WHERE campaign_id=?
        ORDER BY sequence_step ASC, version DESC
    """, (campaign_id,)).fetchall()
    conn.close()

    if templates:
        st.caption(f"{len(templates)} template(s)")
        for t in templates:
            approval_color = {
                "approved": "🟢", "pending": "🟡",
                "rejected": "🔴", "changes_requested": "🟠"
            }.get(t["approval_status"], "⚪")

            with st.expander(
                f"Step {t['sequence_step']} — v{t['version']} "
                f"{approval_color} {t['approval_status'].replace('_',' ').title()}"
            ):
                if t.get("subject"):
                    st.markdown(f"**Subject:** {t['subject']}")
                st.text_area("Body", value=t["body"], height=200,
                             key=f"tmpl_body_{t['id']}",
                             disabled=True)
                if t.get("client_notes"):
                    st.info(f"Client notes: {t['client_notes']}")

                # Copy button hint
                st.caption("📋 Select all text above and copy to your email tool.")
    else:
        st.info("No templates uploaded for this campaign yet.")

    st.markdown("---")
    st.subheader("Upload New Template")

    with st.form("upload_template_form"):
        step    = st.number_input("Sequence step", min_value=1,
                                  max_value=10, value=1)
        subject = st.text_input("Subject line")
        body    = st.text_area("Email body *", height=200)
        notes   = st.text_input("Internal notes")

        if st.form_submit_button("Save Template"):
            if not body.strip():
                st.error("Body is required.")
            else:
                conn = get_connection()
                # Get next version
                existing = conn.execute("""
                    SELECT MAX(version) AS v FROM campaign_templates
                    WHERE campaign_id=? AND sequence_step=?
                """, (campaign_id, step)).fetchone()["v"] or 0

                conn.execute("""
                    INSERT INTO campaign_templates
                        (campaign_id, org_id, version, subject, body,
                         sequence_step, created_by, internal_notes,
                         approval_status, created_at)
                    SELECT ?, org_id, ?, ?, ?, ?, ?, ?, 'pending', ?
                    FROM campaigns WHERE id=?
                """, (campaign_id, existing + 1, subject, body,
                      step, user_id, notes,
                      datetime.utcnow().isoformat(), campaign_id))
                conn.commit()
                conn.close()
                st.success("Template saved — pending client approval.")
                st.rerun()


# ── WEEKLY REPORT ─────────────────────────────────────────────────────────────

def _render_weekly_report(campaign_id: int, user_id: int):
    st.subheader("Enter Weekly Stats")
    st.caption("Enter the numbers from your email sending tool each week.")

    existing_stats = get_weekly_stats(campaign_id)

    # Show existing history
    if existing_stats:
        df = pd.DataFrame(existing_stats)
        show_cols = ["week_label", "cold_emails_sent", "followups_sent",
                     "total_sent", "open_rate", "responded",
                     "interested", "scheduled", "meetings_done"]
        show_cols = [c for c in show_cols if c in df.columns]
        st.dataframe(
            df[show_cols].rename(columns={
                "week_label": "Week",
                "cold_emails_sent": "Cold",
                "followups_sent": "Follow-ups",
                "total_sent": "Total Sent",
                "open_rate": "Open %",
                "responded": "Responded",
                "interested": "Interested",
                "scheduled": "Scheduled",
                "meetings_done": "Meetings Done",
            }),
            use_container_width=True,
            hide_index=True,
        )
        st.markdown("---")

    # Entry form
    with st.form("weekly_stats_form"):
        week_start_date = st.date_input(
            "Week start (Monday)",
            value=date.fromisoformat(current_week_start())
        )

        col1, col2 = st.columns(2)
        with col1:
            cold      = st.number_input("Cold emails sent",
                                        min_value=0, value=0, step=1)
            followups = st.number_input("Follow-ups sent",
                                        min_value=0, value=0, step=1)
            opens     = st.number_input("Opens", min_value=0,
                                        value=0, step=1)
        with col2:
            responded  = st.number_input("Responded", min_value=0,
                                         value=0, step=1)
            interested = st.number_input("Interested / Pipeline",
                                         min_value=0, value=0, step=1)
            scheduled  = st.number_input("Scheduled", min_value=0,
                                         value=0, step=1)
            meetings   = st.number_input("Meetings done", min_value=0,
                                         value=0, step=1)

        if st.form_submit_button("Save Week", use_container_width=True):
            save_weekly_stats(
                campaign_id      = campaign_id,
                week_start       = week_start_date.isoformat(),
                cold_emails_sent = cold,
                followups_sent   = followups,
                opens            = opens,
                responded        = responded,
                interested       = interested,
                scheduled        = scheduled,
                meetings_done    = meetings,
                entered_by       = user_id,
            )
            st.success(f"Week of {week_start_date} saved!")
            st.rerun()


# ── DOWNLOADS ─────────────────────────────────────────────────────────────────

def _render_downloads(campaign_id: int, campaign_name: str,
                       client_name: str, crm: list):
    st.subheader("Download Data")

    # Enriched contacts CSV
    if crm:
        df = pd.DataFrame([{
            "Name":         r.get("full_name",""),
            "Company":      r.get("company",""),
            "Title":        r.get("role",""),
            "Email":        r.get("email",""),
            "Status":       r.get("status",""),
            "Next Step":    r.get("next_step",""),
            "Outreach From":r.get("outreach_from",""),
            "Meeting Date": r.get("meeting_date","") or "",
            "Notes":        r.get("notes","") or "",
        } for r in crm])

        # Email-only CSV
        email_df = df[df["Email"].str.contains("@", na=False)]

        col1, col2 = st.columns(2)
        with col1:
            st.download_button(
                "⬇ All Contacts CSV",
                data=df.to_csv(index=False),
                file_name=f"{campaign_name}_all_contacts.csv",
                mime="text/csv",
                use_container_width=True,
            )
        with col2:
            st.download_button(
                "⬇ Email-Found Only CSV",
                data=email_df.to_csv(index=False),
                file_name=f"{campaign_name}_email_contacts.csv",
                mime="text/csv",
                use_container_width=True,
            )

    st.markdown("---")

    # XLSX report
    st.markdown("**Weekly Report (Excel)**")
    st.caption("Downloads in the same format as the report you currently send clients.")

    xlsx_bytes = generate_xlsx(campaign_id, campaign_name, client_name)
    if xlsx_bytes:
        st.download_button(
            "⬇ Download Weekly Report (.xlsx)",
            data=xlsx_bytes,
            file_name=f"{campaign_name}_report_{date.today().isoformat()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    else:
        st.info("Install openpyxl to enable XLSX export: `pip install openpyxl`")
