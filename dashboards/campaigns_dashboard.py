"""
dashboards/campaigns_dashboard.py — Dashin Research Platform
Campaign Builder. Manager / Org Admin scope.
- Create campaigns + link to clients
- Add enriched leads from inventory
- Mark campaign "Ready to View" → notifies client
- View campaign status
- Export campaign data
"""

import logging
import streamlit as st
import pandas as pd
from datetime import datetime, date
from core.db import get_connection
from core.auth import can_mark_campaign_ready
from services.notification_service import notify_campaign_ready

STYLES = """
<style>
/* Campaign-specific banners and rows — shared components in core/styles.py */
</style>
"""

CAMPAIGN_STATUSES = ["building", "active", "paused", "ready",
                     "completed", "closed"]


def render(user: dict):
    from core.styles import inject_shared_css
    inject_shared_css()
    st.markdown(STYLES, unsafe_allow_html=True)

    org_id  = user["org_id"]
    user_id = user["id"]

    st.markdown(f"""
    <div class="cb-header">
        <div class="cb-title">Campaigns</div>
        <div class="cb-sub">{user['name']} · {date.today().strftime('%d %b %Y')}</div>
    </div>
    """, unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["📁 All Campaigns", "➕ New Campaign"])

    with tab1:
        _render_campaign_list(org_id, user_id, user)
    with tab2:
        _render_new_campaign(org_id, user_id)


# ── CAMPAIGN LIST ─────────────────────────────────────────────────────────────

def _render_campaign_list(org_id: int, user_id: int, user: dict):
    conn = get_connection()
    campaigns = conn.execute("""
        SELECT ca.*,
               cl.name AS client_name,
               u.name  AS created_by_name,
               (SELECT COUNT(*) FROM campaign_leads
                WHERE campaign_id=ca.id)              AS lead_count,
               (SELECT COUNT(*) FROM campaign_leads
                WHERE campaign_id=ca.id
                  AND crm_status='booked')            AS meetings,
               (SELECT COUNT(*) FROM campaign_leads
                WHERE campaign_id=ca.id
                  AND (SELECT email FROM enrichment
                       WHERE lead_id=campaign_leads.lead_id) IS NOT NULL) AS with_email
        FROM campaigns ca
        LEFT JOIN clients cl ON cl.id = ca.client_id
        LEFT JOIN users u    ON u.id  = ca.created_by
        WHERE ca.org_id=?
        ORDER BY ca.created_at DESC
    """, (org_id,)).fetchall()
    conn.close()

    if not campaigns:
        st.info("No campaigns yet. Create your first one →")
        return

    # Filter
    col1, col2 = st.columns([2, 1])
    with col1:
        search = st.text_input("Search", placeholder="Campaign name…",
                               label_visibility="collapsed")
    with col2:
        status_filter = st.selectbox(
            "Status", ["All"] + CAMPAIGN_STATUSES,
            label_visibility="collapsed",
            format_func=lambda x: x.title()
        )

    filtered = campaigns
    if search:
        filtered = [c for c in filtered
                    if search.lower() in c["name"].lower()]
    if status_filter != "All":
        filtered = [c for c in filtered
                    if c["status"] == status_filter]

    st.caption(f"{len(filtered)} campaign(s)")

    for camp in filtered:
        _render_campaign_card(camp, org_id, user_id, user)


def _render_campaign_card(camp, org_id: int, user_id: int, user: dict):
    status   = camp["status"] or "building"
    visible  = camp["is_visible_to_client"]
    leads    = camp["lead_count"] or 0
    target   = camp["target_count"] or 0
    pct      = min(int(leads / target * 100), 100) if target > 0 else 0

    st.markdown(f"""
    <div class="camp-card">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;">
            <div>
                <div class="camp-client">{camp.get('client_name','No client')}</div>
                <div class="camp-title">{camp['name']}</div>
                <div class="camp-meta">
                    📅 Created {(camp['created_at'] or '')[:10]} ·
                    👤 {camp.get('created_by_name','?')} ·
                    🎯 {leads}/{target or '?'} leads ·
                    📧 {camp.get('with_email',0)} emails ·
                    📅 {camp.get('meetings',0)} meetings
                </div>
            </div>
            <span class="status-badge s-{status}">{status}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Ready to view banner
    if visible:
        st.markdown(f"""
        <div class="ready-banner">
            ✅ <b>Visible to client</b> since
            {(camp.get('marked_ready_at') or '')[:10]}
        </div>
        """, unsafe_allow_html=True)
    elif can_mark_campaign_ready(user):
        st.markdown("""
        <div class="not-ready-banner">
            ⏳ <b>Not yet visible to client.</b>
            Mark as "Ready to View" when the data is ready.
        </div>
        """, unsafe_allow_html=True)

    # Actions row
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        if st.button("👁 Manage",
                     key=f"manage_{camp['id']}",
                     use_container_width=True):
            st.session_state[f"open_camp"] = camp["id"]

    with col2:
        if not visible and can_mark_campaign_ready(user):
            if st.button("🚀 Mark Ready",
                         key=f"ready_{camp['id']}",
                         use_container_width=True,
                         type="primary"):
                _mark_ready(camp, org_id, user_id)
                st.rerun()

    with col3:
        new_status = st.selectbox(
            "Change status",
            CAMPAIGN_STATUSES,
            index=CAMPAIGN_STATUSES.index(status),
            key=f"stat_{camp['id']}",
            label_visibility="collapsed",
            format_func=lambda x: x.title()
        )
        if new_status != status:
            _update_status(camp["id"], new_status)
            st.rerun()

    with col4:
        pass

    # Expanded management panel
    if st.session_state.get("open_camp") == camp["id"]:
        _render_campaign_manager(camp["id"], org_id, user_id, camp)

    st.markdown("---")


def _render_campaign_manager(campaign_id: int, org_id: int,
                               user_id: int, camp):
    st.markdown(f"#### Managing: {camp['name']}")

    t1, t2, t3 = st.tabs([
        f"👥 Leads ({camp['lead_count']})",
        "➕ Add Leads",
        "⬇ Export",
    ])

    with t1:
        _render_campaign_leads(campaign_id, org_id)
    with t2:
        _render_add_leads(campaign_id, org_id, camp)
    with t3:
        _render_export(campaign_id, camp["name"])

    if st.button("Close panel", key=f"close_{campaign_id}"):
        st.session_state["open_camp"] = None
        st.rerun()


def _render_campaign_leads(campaign_id: int, org_id: int):
    conn = get_connection()
    leads = conn.execute("""
        SELECT l.full_name, l.title, l.persona,
               co.name AS company,
               e.email, cl.crm_status,
               cl.is_reused, cl.added_at
        FROM campaign_leads cl
        JOIN leads l ON l.id = cl.lead_id
        LEFT JOIN companies co ON co.id = l.company_id
        LEFT JOIN enrichment e ON e.lead_id = l.id
        WHERE cl.campaign_id=?
        ORDER BY cl.added_at DESC
    """, (campaign_id,)).fetchall()
    conn.close()

    if not leads:
        st.info("No leads added to this campaign yet.")
        return

    st.caption(f"{len(leads)} leads")

    with_email = sum(1 for l in leads
                     if l["email"] and "@" in (l["email"] or ""))
    reused     = sum(1 for l in leads if l["is_reused"])
    col1, col2, col3 = st.columns(3)
    col1.metric("Total", len(leads))
    col2.metric("Email Found", with_email)
    col3.metric("Reused Leads", reused,
                help="Leads used for another client before")

    df = pd.DataFrame([{
        "Name":    l["full_name"],
        "Company": l.get("company",""),
        "Title":   l.get("title",""),
        "Persona": l.get("persona",""),
        "Email":   "✓" if (l.get("email") and "@" in (l["email"] or "")) else "—",
        "Status":  (l.get("crm_status") or "new").replace("_"," ").title(),
        "Reused":  "Yes" if l["is_reused"] else "No",
    } for l in leads])
    st.dataframe(df, use_container_width=True, hide_index=True)


def _render_add_leads(campaign_id: int, org_id: int, camp):
    client_id = camp.get("client_id")

    st.caption("Add enriched leads from inventory to this campaign.")

    # Filters
    col1, col2, col3 = st.columns(3)
    with col1:
        persona_f = st.selectbox(
            "Persona",
            ["All", "Decision Maker", "Senior Influencer",
             "Influencer", "IC", "Unknown"],
            key=f"pf_{campaign_id}"
        )
    with col2:
        email_only = st.checkbox("Email found only",
                                  value=True,
                                  key=f"eo_{campaign_id}")
    with col3:
        limit = st.number_input("Max results", min_value=10,
                                 max_value=500, value=100,
                                 key=f"lim_{campaign_id}")

    conn = get_connection()

    # Exclude leads already used for this client
    q = """
        SELECT l.id, l.full_name, l.title, l.persona,
               co.name AS company, e.email
        FROM leads l
        LEFT JOIN companies co ON co.id = l.company_id
        LEFT JOIN enrichment e ON e.lead_id = l.id
        WHERE l.org_id=?
          AND l.status IN ('enriched','no_email')
          AND l.archived_at IS NULL
          AND l.id NOT IN (
              SELECT lead_id FROM campaign_leads
              WHERE campaign_id=?
          )
    """
    params = [org_id, campaign_id]

    if client_id:
        q += " AND l.id NOT IN (SELECT lead_id FROM lead_usage WHERE client_id=?)"
        params.append(client_id)

    if persona_f != "All":
        q += " AND l.persona=?"
        params.append(persona_f)

    if email_only:
        q += " AND e.email IS NOT NULL AND e.email LIKE '%@%'"

    q += f" ORDER BY l.last_seen_at DESC LIMIT {limit}"

    available = conn.execute(q, params).fetchall()

    # Already in campaign (for reuse check)
    already_used_ids = set()
    if client_id:
        rows = conn.execute(
            "SELECT lead_id FROM lead_usage WHERE client_id=?",
            (client_id,)
        ).fetchall()
        already_used_ids = {r["lead_id"] for r in rows}

    conn.close()

    if not available:
        st.info("No available leads matching these filters.")
        return

    st.caption(f"{len(available)} available leads")

    # Show as selectable table
    df = pd.DataFrame([{
        "Name":    l["full_name"],
        "Company": l.get("company",""),
        "Title":   l.get("title",""),
        "Persona": l.get("persona",""),
        "Email":   "✓" if (l.get("email") and "@" in (l["email"] or "")) else "—",
        "ID":      l["id"],
    } for l in available])

    selected = st.multiselect(
        "Select leads to add",
        options=[l["id"] for l in available],
        format_func=lambda lid: next(
            (f"{l['full_name']} — {l.get('company','?')}"
             for l in available if l["id"] == lid), str(lid)
        ),
        key=f"sel_{campaign_id}"
    )

    if selected:
        st.caption(f"{len(selected)} selected")
        if st.button(f"Add {len(selected)} leads to campaign",
                     key=f"add_{campaign_id}",
                     type="primary"):
            _add_leads_to_campaign(
                campaign_id, org_id, client_id,
                selected, user_id=None
            )
            st.success(f"Added {len(selected)} leads!")
            st.rerun()


def _render_export(campaign_id: int, campaign_name: str):
    conn = get_connection()
    leads = conn.execute("""
        SELECT l.full_name, l.title, l.persona,
               co.name AS company,
               e.email, e.phone, e.linkedin_url,
               e.country, e.industry AS lead_industry,
               e.company_size,
               cl.crm_status
        FROM campaign_leads cl
        JOIN leads l ON l.id = cl.lead_id
        LEFT JOIN companies co ON co.id = l.company_id
        LEFT JOIN enrichment e ON e.lead_id = l.id
        WHERE cl.campaign_id=?
    """, (campaign_id,)).fetchall()
    conn.close()

    if not leads:
        st.info("No leads to export.")
        return

    all_df = pd.DataFrame([{
        "Name":         l["full_name"],
        "Company":      l.get("company",""),
        "Title":        l.get("title",""),
        "Email":        l.get("email",""),
        "Phone":        l.get("phone",""),
        "LinkedIn":     l.get("linkedin_url",""),
        "Country":      l.get("country",""),
        "Industry":     l.get("lead_industry",""),
        "Company Size": l.get("company_size",""),
        "Persona":      l.get("persona",""),
        "Status":       l.get("crm_status",""),
    } for l in leads])

    email_df = all_df[all_df["Email"].str.contains("@", na=False)]

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Total Leads", len(all_df))
        st.download_button(
            "⬇ All Leads CSV",
            data=all_df.to_csv(index=False),
            file_name=f"{campaign_name}_all.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with col2:
        st.metric("Email Found", len(email_df))
        st.download_button(
            "⬇ Email Found CSV",
            data=email_df.to_csv(index=False),
            file_name=f"{campaign_name}_emails.csv",
            mime="text/csv",
            use_container_width=True,
        )


# ── NEW CAMPAIGN ──────────────────────────────────────────────────────────────

def _render_new_campaign(org_id: int, user_id: int):
    conn    = get_connection()
    clients = conn.execute(
        "SELECT id, name FROM clients WHERE org_id=? AND is_active=1 ORDER BY name",
        (org_id,)
    ).fetchall()
    conn.close()

    with st.form("new_campaign_form"):
        st.subheader("Campaign Details")

        name = st.text_input("Campaign name *",
                              placeholder="e.g. Q1 2026 UK SaaS Outreach")

        col1, col2 = st.columns(2)
        with col1:
            client_map = {"No client": None}
            if clients:
                client_map.update({c["name"]: c["id"] for c in clients})
            client_sel = st.selectbox("Client", list(client_map.keys()))
            target     = st.number_input("Target leads", min_value=0,
                                          value=500, step=50)
        with col2:
            status = st.selectbox("Initial status",
                                   ["building", "active"],
                                   format_func=lambda x: x.title())

        description = st.text_area("Description / notes",
                                    height=80,
                                    placeholder="Context for researchers, "
                                                "persona notes, ICP reminders…")

        if st.form_submit_button("Create Campaign",
                                  use_container_width=True):
            if not name.strip():
                st.error("Campaign name is required.")
            else:
                conn = get_connection()
                conn.execute("""
                    INSERT INTO campaigns
                        (org_id, name, client_id, description,
                         target_count, created_by, created_at,
                         status, is_visible_to_client, lead_count)
                    VALUES (?,?,?,?,?,?,?,?,0,0)
                """, (org_id, name.strip(),
                      client_map[client_sel],
                      description.strip(),
                      target, user_id,
                      datetime.utcnow().isoformat(),
                      status))
                conn.commit()
                conn.close()
                st.success(f"Campaign '{name}' created!")
                st.rerun()


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _mark_ready(camp, org_id: int, user_id: int):
    now  = datetime.utcnow().isoformat()
    conn = get_connection()
    conn.execute("""
        UPDATE campaigns
        SET is_visible_to_client=1,
            marked_ready_by=?,
            marked_ready_at=?,
            status='ready'
        WHERE id=? AND org_id=?
    """, (user_id, now, camp["id"], org_id))
    conn.commit()
    conn.close()

    if camp.get("client_id"):
        notify_campaign_ready(
            org_id, camp["id"],
            camp["name"], camp["client_id"]
        )


def _update_status(campaign_id: int, new_status: str):
    conn = get_connection()
    conn.execute(
        "UPDATE campaigns SET status=? WHERE id=?",
        (new_status, campaign_id)
    )
    conn.commit()
    conn.close()


def _add_leads_to_campaign(campaign_id: int, org_id: int,
                            client_id: int | None,
                            lead_ids: list, user_id: int | None):
    conn = get_connection()
    now  = datetime.utcnow().isoformat()

    added = 0
    for lid in lead_ids:
        # Check if reused
        is_reused = 0
        if client_id:
            exists = conn.execute(
                "SELECT id FROM lead_usage WHERE lead_id=? AND client_id=?",
                (lid, client_id)
            ).fetchone()
            if exists:
                is_reused = 1

        try:
            conn.execute("""
                INSERT OR IGNORE INTO campaign_leads
                    (campaign_id, lead_id, is_reused, added_at, crm_status)
                VALUES (?,?,?,'?','new')
            """.replace("'?'", "?"),
            (campaign_id, lid, is_reused, now))

            # Mark lead as used
            if client_id:
                conn.execute("""
                    INSERT OR IGNORE INTO lead_usage
                        (lead_id, client_id, campaign_id, used_at)
                    VALUES (?,?,?,?)
                """, (lid, client_id, campaign_id, now))

            conn.execute(
                "UPDATE leads SET status='used', used_at=? WHERE id=?",
                (now, lid)
            )
            added += 1
        except Exception as e:
            logging.warning(f"[campaigns_dashboard] Failed to update lead {lid} status: {e}")

    # Update lead count
    conn.execute("""
        UPDATE campaigns
        SET lead_count=(
            SELECT COUNT(*) FROM campaign_leads WHERE campaign_id=?
        )
        WHERE id=?
    """, (campaign_id, campaign_id))

    conn.commit()
    conn.close()
    return added
