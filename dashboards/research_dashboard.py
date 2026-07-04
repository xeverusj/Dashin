"""
dashboards/research_dashboard.py — Dashin Research Platform
Researcher's personal workspace.
- See assigned tasks with priority + deadline
- Start tasks, log enrichment per lead
- Reject leads with reasons
- Reassign tasks to colleagues
- Track own progress
"""

import streamlit as st
from datetime import datetime, date
from core.auth import has_role, can_access_scraper
from core.db import get_connection
from services.task_service import (
    get_tasks, get_task, start_task, submit_task,
    update_progress, reassign_task, get_researcher_kpis,
)
from services.lead_service import (
    get_leads, get_lead, enrich_lead, reject_lead, save_lead,
)
from services.flag_service import get_unresolved_flags, get_flag_summary


# ── STYLES (page-specific only — shared styles via core/styles.py) ────────────

_UNIQUE_CSS = """
<style>
/* kpi-value: alias for .kpi-val used in research queue HTML */
.kpi-value {
    font-family: 'Playfair Display', serif;
    font-size: 26px;
    font-weight: 700;
    color: var(--text-1);
}
/* Flag chips */
.flag-chip {
    display: inline-block;
    background: var(--error-bg);
    color: var(--error);
    border: 1px solid var(--error-border);
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 600;
    margin-right: 4px;
}
.flag-warning { background: #FFF8E1; color: #8D6E0A; border-color: #F0D97A; }
/* Progress bar */
.progress-bar-wrap { background: var(--border-light); border-radius: 4px; height: 6px; margin-top: 8px; overflow: hidden; }
.progress-bar-fill { height: 100%; border-radius: 4px; background: var(--accent); transition: width 0.3s; }
/* Deadline colours */
.deadline-overdue { color: var(--error) !important; font-weight: 600; }
.deadline-soon    { color: #B45309 !important; }
</style>
"""

REJECTION_REASONS = [
    "wrong_persona",
    "duplicate",
    "bounced_email",
    "personal_email",
    "out_of_market",
    "incomplete_data",
    "wrong_company_size",
    "wrong_geography",
    "other",
]


def render(user: dict):
    from core.styles import inject_shared_css
    inject_shared_css()
    st.markdown(_UNIQUE_CSS, unsafe_allow_html=True)

    org_id = user["org_id"]
    user_id = user["id"]
    week_start = date.today().replace(
        day=date.today().day - date.today().weekday()
    ).isoformat()

    # ── Header ───────────────────────────────────────────────────────
    st.markdown(f"""
    <div class="rq-header">
        <div>
            <div class="rq-header-title">Research Queue</div>
            <div class="rq-header-sub">
                {user['name']} · {date.today().strftime('%A, %d %b %Y')}
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── KPI Row ───────────────────────────────────────────────────────
    kpis = get_researcher_kpis(org_id, user_id, week_start)
    _render_kpis(kpis)

    # ── Tabs ──────────────────────────────────────────────────────────
    tab1, tab2, tab3 = st.tabs([
        "📋 My Tasks",
        "✏️ Enrich a Lead",
        "📊 My Stats",
    ])

    with tab1:
        _render_task_list(user, org_id, user_id)

    with tab2:
        _render_enrichment_form(user, org_id, user_id)

    with tab3:
        _render_my_stats(org_id, user_id)


# ── KPI ROW ───────────────────────────────────────────────────────────────────

def _render_kpis(kpis: dict):
    quota_target = kpis.get("quota_target")
    quota_pct    = kpis.get("quota_pct")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Tasks This Week",
                  f"{kpis['completed']}/{kpis['assigned']}",
                  f"{kpis['completion_rate']}% done")
    with c2:
        st.metric("Leads Enriched",
                  kpis["total_enriched"],
                  f"Target: {quota_target}" if quota_target else "No quota set")
    with c3:
        st.metric("Rejection Rate",
                  f"{kpis['rejection_rate']}%",
                  f"Personal email: {kpis['personal_email_rate']}%",
                  delta_color="inverse")


# ── TASK LIST ─────────────────────────────────────────────────────────────────

def _render_task_list(user: dict, org_id: int, user_id: int):
    tasks = get_tasks(org_id, assigned_to=user_id)

    # Filter
    status_filter = st.selectbox(
        "Filter by status",
        ["All", "pending", "in_progress", "submitted", "approved", "rejected"],
        label_visibility="collapsed"
    )
    if status_filter != "All":
        tasks = [t for t in tasks if t["status"] == status_filter]

    if not tasks:
        st.info("No tasks assigned to you yet.")
        return

    st.caption(f"{len(tasks)} task{'s' if len(tasks) != 1 else ''}")

    for task in tasks:
        _render_task_card(task, user, org_id, user_id)


def _render_task_card(task: dict, user: dict, org_id: int, user_id: int):
    priority  = task.get("priority", "normal")
    status    = task.get("status",   "pending")
    deadline  = task.get("deadline")
    target    = task.get("target_count", 0)
    completed = task.get("completed_count", 0)
    pct       = int(completed / target * 100) if target > 0 else 0

    # Deadline colour
    deadline_class = ""
    deadline_str   = ""
    if deadline:
        dl = date.fromisoformat(deadline[:10])
        days_left = (dl - date.today()).days
        deadline_str = dl.strftime("%d %b")
        if days_left < 0:
            deadline_class = "deadline-overdue"
            deadline_str  += " (overdue)"
        elif days_left <= 2:
            deadline_class = "deadline-soon"

    rejection_note = task.get("rejection_note","")

    st.markdown(f"""
    <div class="task-card {priority}">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;">
            <div>
                <div class="task-title">{task['title']}</div>
                <div class="task-meta">
                    <span>📂 {task['task_type'].replace('_',' ').title()}</span>
                    <span class="{deadline_class}">📅 {deadline_str or 'No deadline'}</span>
                    <span>🎯 {completed}/{target} leads</span>
                </div>
                {f'<div style="margin-top:6px;font-size:12px;color:#C62828;">↩ Revision needed: {rejection_note}</div>' if rejection_note and status != 'approved' else ''}
            </div>
            <div style="display:flex;gap:6px;flex-shrink:0;">
                <span class="priority-badge priority-{priority}">{priority}</span>
                <span class="status-badge status-{status}">{status.replace('_',' ')}</span>
            </div>
        </div>
        {f'<div class="progress-bar-wrap"><div class="progress-bar-fill" style="width:{pct}%"></div></div>' if target > 0 else ''}
    </div>
    """, unsafe_allow_html=True)

    if task.get("description"):
        with st.expander("Task details"):
            st.write(task["description"])

    # Action buttons
    col1, col2, col3, col4 = st.columns([1, 1, 1, 2])

    with col1:
        if status == "pending":
            if st.button("▶ Start", key=f"start_{task['id']}",
                         use_container_width=True):
                start_task(task["id"], user_id)
                st.rerun()

    with col2:
        if status == "in_progress":
            if st.button("📤 Submit", key=f"submit_{task['id']}",
                         use_container_width=True):
                submit_task(task["id"], user_id)
                st.success("Submitted for review!")
                st.rerun()

    with col3:
        if status in ("pending", "in_progress"):
            if st.button("↩ Reassign", key=f"reassign_{task['id']}",
                         use_container_width=True):
                st.session_state[f"reassign_open_{task['id']}"] = True

    with col4:
        if status == "in_progress" and target > 0:
            new_count = st.number_input(
                "Update progress",
                min_value=0, max_value=target,
                value=completed,
                key=f"prog_{task['id']}",
                label_visibility="collapsed"
            )
            if new_count != completed:
                if st.button("Save", key=f"saveprog_{task['id']}"):
                    update_progress(task["id"], new_count)
                    st.rerun()

    # Reassign form
    if st.session_state.get(f"reassign_open_{task['id']}"):
        with st.form(f"reassign_form_{task['id']}"):
            conn         = get_connection()
            colleagues   = conn.execute("""
                SELECT id, name FROM users
                WHERE org_id=? AND role='researcher'
                  AND is_active=1 AND id!=?
                ORDER BY name
            """, (org_id, user_id)).fetchall()
            conn.close()

            if not colleagues:
                st.info("No other researchers to reassign to.")
            else:
                options = {r["name"]: r["id"] for r in colleagues}
                to_name = st.selectbox("Reassign to", list(options.keys()))
                reason  = st.text_input("Reason (optional)")
                if st.form_submit_button("Confirm Reassign"):
                    reassign_task(
                        task["id"], user_id,
                        options[to_name], reason
                    )
                    st.session_state[f"reassign_open_{task['id']}"] = False
                    st.success(f"Reassigned to {to_name}")
                    st.rerun()

    st.markdown("---")


# ── ENRICHMENT FORM ───────────────────────────────────────────────────────────

def _render_enrichment_form(user: dict, org_id: int, user_id: int):
    st.subheader("Enrich a Lead")
    st.caption("Find and fill in contact details for an assigned lead.")

    # Lead selector — show leads in_progress or assigned
    conn  = get_connection()
    leads = conn.execute("""
        SELECT l.id, l.full_name, l.title,
               co.name AS company_name
        FROM leads l
        LEFT JOIN companies co ON co.id = l.company_id
        WHERE l.org_id=?
          AND l.status IN ('new','assigned','in_progress')
        ORDER BY l.last_seen_at DESC
        LIMIT 100
    """, (org_id,)).fetchall()
    conn.close()

    if not leads:
        st.info("No leads available to enrich right now.")
        return

    lead_options = {
        f"{r['full_name']} — {r['company_name'] or 'Unknown Co'}": r["id"]
        for r in leads
    }
    selected_name = st.selectbox("Select lead", list(lead_options.keys()))
    lead_id       = lead_options[selected_name]
    lead          = get_lead(lead_id, org_id)

    if not lead:
        return

    # Show existing data
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**Name:** {lead['full_name']}")
        st.markdown(f"**Title:** {lead['title'] or '—'}")
        st.markdown(f"**Company:** {lead.get('company_name') or '—'}")
        st.markdown(f"**Persona:** {lead['persona']}")

    with col2:
        if lead.get("email"):
            st.markdown(f"**Email (existing):** {lead['email']}")
        if lead.get("linkedin_url"):
            st.markdown(f"**LinkedIn (existing):** {lead['linkedin_url']}")

    # Show existing flags
    flags = get_unresolved_flags(org_id, lead_id)
    if flags:
        flag_html = " ".join([
            f'<span class="flag-chip {"flag-warning" if f["severity"]=="warning" else ""}">'
            f'⚑ {f["flag_type"].replace("_"," ")}</span>'
            for f in flags
        ])
        st.markdown(f"**Flags:** {flag_html}", unsafe_allow_html=True)

    st.markdown("---")

    # Enrichment form
    with st.form(f"enrich_form_{lead_id}"):
        st.markdown("**Contact Details**")
        r1c1, r1c2 = st.columns(2)
        with r1c1:
            email       = st.text_input("Email", value=lead.get("email") or "")
            phone       = st.text_input("Phone", value=lead.get("phone") or "")
        with r1c2:
            linkedin    = st.text_input("LinkedIn URL",
                                        value=lead.get("linkedin_url") or "")
            country     = st.text_input("Country",
                                        value=lead.get("country") or "")

        r2c1, r2c2 = st.columns(2)
        with r2c1:
            industry    = st.text_input("Industry",
                                        value=lead.get("enrich_industry") or "")
        with r2c2:
            company_size = st.selectbox("Company Size",
                ["", "1-10", "11-50", "51-200", "201-500",
                 "501-1000", "1001-5000", "5000+"],
                index=0)

        notes        = st.text_area("Notes", value=lead.get("enrich_notes") or "",
                                    height=80)
        minutes      = st.number_input("Time spent (minutes)",
                                       min_value=0.0, value=0.0, step=0.5)

        submitted = st.form_submit_button("Save Enrichment",
                                          use_container_width=True)

    if submitted:
        result = enrich_lead(
            lead_id      = lead_id,
            org_id       = org_id,
            enriched_by  = user_id,
            email        = email.strip() or None,
            phone        = phone.strip() or None,
            linkedin_url = linkedin.strip() or None,
            country      = country.strip() or None,
            industry     = industry.strip() or None,
            company_size = company_size or None,
            notes        = notes.strip() or None,
            minutes_spent= minutes,
        )
        if result["enriched"]:
            if result["flags"]:
                flag_names = ", ".join(
                    f["flag_type"].replace("_", " ")
                    for f in result["flags"]
                )
                st.warning(f"Saved — but flags detected: {flag_names}. "
                           f"Your manager will review.")
            else:
                st.success("Lead enriched successfully!")
            st.rerun()

    st.markdown("---")
    st.subheader("Reject a Lead")
    st.caption("Mark a lead as unusable with a reason.")

    with st.form(f"reject_form_{lead_id}"):
        reason = st.selectbox("Rejection reason",
                              [r.replace("_", " ").title()
                               for r in REJECTION_REASONS])
        note   = st.text_input("Additional note (optional)")
        reject = st.form_submit_button("Reject Lead",
                                       use_container_width=True,
                                       type="secondary")
    if reject:
        reason_key = reason.lower().replace(" ", "_")
        reject_lead(lead_id, org_id, user_id, reason_key, note)
        st.info("Lead rejected and removed from queue.")
        st.rerun()


# ── MY STATS ──────────────────────────────────────────────────────────────────

def _render_my_stats(org_id: int, user_id: int):
    st.subheader("My Performance")

    period = st.radio("Period", ["This week", "All time"],
                      horizontal=True, label_visibility="collapsed")
    week_start = None
    if period == "This week":
        today      = date.today()
        week_start = (today - __import__('datetime').timedelta(
            days=today.weekday())).isoformat()

    kpis = get_researcher_kpis(org_id, user_id, week_start)

    col1, col2, col3 = st.columns(3)
    metrics = [
        ("Leads Enriched",    kpis["total_enriched"],         None),
        ("Rejection Rate",    f"{kpis['rejection_rate']}%",   "lower is better"),
        ("Personal Email %",  f"{kpis['personal_email_rate']}%","lower is better"),
        ("Avg Mins / Lead",   f"{kpis['avg_mins_per_lead']}m",None),
        ("Bounce Rate",       f"{kpis['bounce_rate']}%",      "lower is better"),
        ("Task Completion",   f"{kpis['completion_rate']}%",  None),
    ]
    cols = [c for c in st.columns(3)]
    for i, (label, value, help_text) in enumerate(metrics):
        with cols[i % 3]:
            st.metric(label, value, help=help_text)

    if kpis.get("quota_target"):
        st.markdown("---")
        st.markdown("**Weekly Quota Progress**")
        pct = min(kpis["quota_pct"] or 0, 100)
        st.progress(pct / 100,
                    text=f"{kpis['quota_delivered']} / {kpis['quota_target']} "
                         f"leads ({pct:.0f}%)")
