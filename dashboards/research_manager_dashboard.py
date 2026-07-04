"""
dashboards/research_manager_dashboard.py — Dashin Research Platform
Research Manager workspace.
- Review submitted tasks + approve/reject with notes
- Set weekly quotas per researcher
- KPI dashboard for entire team
- Flag review + resolution
- Task assignment
"""

import streamlit as st
from datetime import datetime, date, timedelta
from core.db import get_connection
from services.task_service import (
    get_tasks, create_task, approve_task, reject_task,
    set_quota, get_team_quotas, get_team_kpis,
)
from services.flag_service import (
    get_unresolved_flags, get_flag_summary, resolve_flag,
)
from services.lead_service import get_lead


STYLES = """
<style>
/* Research manager unique CSS — all shared components in core/styles.py */
</style>
"""


def render(user: dict):
    from core.styles import inject_shared_css
    inject_shared_css()
    st.markdown(STYLES, unsafe_allow_html=True)

    org_id  = user["org_id"]
    user_id = user["id"]

    today      = date.today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()

    st.markdown(f"""
    <div class="rm-header">
        <div>
            <div class="rm-title">Research Manager</div>
            <div class="rm-sub">
                {user['name']} · Week of {week_start}
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Summary stats
    flag_summary = get_flag_summary(org_id)
    pending_tasks = get_tasks(org_id, status="submitted")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Awaiting Review", len(pending_tasks),
                  "submitted tasks")
    with c2:
        st.metric("Active Flags",
                  flag_summary.get("total", 0),
                  f"{flag_summary.get('personal_email',{}).get('count',0)} personal emails")
    with c3:
        team_kpis = get_team_kpis(org_id, week_start)
        avg_reject = (
            sum(k["rejection_rate"] for k in team_kpis) / len(team_kpis)
            if team_kpis else 0
        )
        st.metric("Team Avg Rejection", f"{avg_reject:.1f}%",
                  f"{len(team_kpis)} researchers")

    st.markdown("---")

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "✅ Review Tasks",
        "📊 Team KPIs",
        "⚑ Flags",
        "🎯 Quotas",
        "➕ Assign Task",
        "🔬 Quality Report",
    ])

    with tab1:
        _render_task_review(org_id, user_id, pending_tasks)
    with tab2:
        _render_team_kpis(org_id, week_start)
    with tab3:
        _render_flags(org_id, user_id)
    with tab4:
        _render_quotas(org_id, user_id, week_start)
    with tab5:
        _render_assign_task(org_id, user_id)
    with tab6:
        _render_quality_report(org_id)


# ── TASK REVIEW ───────────────────────────────────────────────────────────────

def _render_task_review(org_id: int, user_id: int, submitted_tasks: list):
    if not submitted_tasks:
        st.info("No tasks waiting for review.")
        return

    st.caption(f"{len(submitted_tasks)} task(s) submitted for review")

    for task in submitted_tasks:
        st.markdown(f"""
        <div class="task-review-card">
            <div class="task-review-title">{task['title']}</div>
            <div class="task-review-meta">
                Researcher: {task['assignee_name']} ·
                Type: {task['task_type'].replace('_',' ').title()} ·
                Progress: {task['completed_count']}/{task['target_count']} leads ·
                Submitted: {(task.get('submitted_at') or '')[:10]}
            </div>
        </div>
        """, unsafe_allow_html=True)

        if task.get("description"):
            st.caption(task["description"])

        col1, col2 = st.columns([1, 2])
        with col1:
            if st.button("✅ Approve", key=f"approve_{task['id']}",
                         use_container_width=True):
                approve_task(task["id"], user_id)
                st.success("Task approved!")
                st.rerun()
        with col2:
            with st.form(f"reject_task_{task['id']}"):
                note = st.text_input("Rejection note (required)",
                                     key=f"rnote_{task['id']}")
                if st.form_submit_button("↩ Send Back for Revision",
                                         use_container_width=True):
                    if not note.strip():
                        st.error("Please provide a reason.")
                    else:
                        reject_task(task["id"], user_id, note)
                        st.info("Sent back to researcher.")
                        st.rerun()
        st.markdown("---")


# ── TEAM KPIs ─────────────────────────────────────────────────────────────────

def _render_team_kpis(org_id: int, week_start: str):
    period = st.radio("Period", ["This week", "All time"],
                      horizontal=True, label_visibility="collapsed")
    ws = week_start if period == "This week" else None
    kpis = get_team_kpis(org_id, ws)

    if not kpis:
        st.info("No researchers found.")
        return

    # Table header
    st.markdown("""
    <div class="kpi-table">
    <div class="kpi-table-header">
        <span>Researcher</span>
        <span>Enriched</span>
        <span>Reject %</span>
        <span>Personal Email %</span>
        <span>Avg Min</span>
        <span>Bounce %</span>
        <span>Quota %</span>
    </div>
    """, unsafe_allow_html=True)

    for k in kpis:
        def _cls(val, warn=15, bad=30, invert=False):
            if invert:
                if val >= warn:
                    return "kpi-good"
                return "kpi-warn"
            if val >= bad:
                return "kpi-bad"
            if val >= warn:
                return "kpi-warn"
            return "kpi-good"

        quota_str = (f"{k['quota_pct']:.0f}%"
                     if k["quota_pct"] is not None else "—")
        quota_cls = _cls(k["quota_pct"] or 0, warn=50, bad=0, invert=True)

        st.markdown(f"""
        <div class="kpi-table-row">
            <span><b>{k['researcher_name']}</b></span>
            <span>{k['total_enriched']}</span>
            <span class="{_cls(k['rejection_rate'])}">{k['rejection_rate']}%</span>
            <span class="{_cls(k['personal_email_rate'], warn=5, bad=15)}">{k['personal_email_rate']}%</span>
            <span>{k['avg_mins_per_lead']}m</span>
            <span class="{_cls(k['bounce_rate'], warn=5, bad=15)}">{k['bounce_rate']}%</span>
            <span class="{quota_cls}">{quota_str}</span>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)

    # Highlight issues
    issues = []
    for k in kpis:
        if k["personal_email_rate"] >= 15:
            issues.append(f"⚠ {k['researcher_name']}: "
                          f"{k['personal_email_rate']}% personal email rate")
        if k["rejection_rate"] >= 30:
            issues.append(f"⚠ {k['researcher_name']}: "
                          f"{k['rejection_rate']}% rejection rate")
        if k["quota_pct"] is not None and k["quota_pct"] < 50:
            issues.append(f"⚠ {k['researcher_name']}: "
                          f"only {k['quota_pct']:.0f}% of quota reached")

    if issues:
        with st.expander(f"⚠ {len(issues)} issue(s) need attention"):
            for issue in issues:
                st.warning(issue)


# ── FLAGS ─────────────────────────────────────────────────────────────────────

def _render_flags(org_id: int, user_id: int):
    flags = get_unresolved_flags(org_id)

    if not flags:
        st.success("No unresolved flags. Clean list!")
        return

    summary = get_flag_summary(org_id)
    cols = st.columns(5)
    flag_types = [
        "personal_email", "invalid_email_format",
        "role_based_email", "domain_mismatch", "duplicate"
    ]
    for i, ft in enumerate(flag_types):
        with cols[i]:
            count = summary.get(ft, {}).get("count", 0)
            st.metric(ft.replace("_", " ").title(), count)

    st.markdown("---")
    st.caption(f"{len(flags)} unresolved flag(s)")

    for flag in flags:
        severity  = flag.get("severity", "warning")
        card_cls  = "flag-card" + (" warning" if severity == "warning" else "")
        icon      = "🔴" if severity == "critical" else "🟡"

        st.markdown(f"""
        <div class="{card_cls}">
            <div style="font-weight:600;font-size:13px;">
                {icon} {flag['flag_type'].replace('_',' ').title()}
                — {flag.get('full_name','Unknown')}
            </div>
            <div style="font-size:12px;color:#666;margin-top:4px;">
                {flag.get('detail','')} ·
                Flagged: {(flag.get('flagged_at') or '')[:10]}
            </div>
        </div>
        """, unsafe_allow_html=True)

        col1, col2, col3 = st.columns([1, 1, 3])
        with col1:
            if st.button("✅ Dismiss", key=f"dismiss_flag_{flag['id']}",
                         use_container_width=True):
                resolve_flag(flag["id"], user_id,
                             "Reviewed and dismissed", learn=True)
                st.rerun()
        with col2:
            if st.button("✏ Confirm", key=f"confirm_flag_{flag['id']}",
                         use_container_width=True):
                resolve_flag(flag["id"], user_id,
                             "Confirmed as invalid", learn=False)
                st.rerun()


# ── QUOTAS ────────────────────────────────────────────────────────────────────

def _render_quotas(org_id: int, user_id: int, week_start: str):
    existing = get_team_quotas(org_id, week_start)

    if existing:
        st.subheader(f"Week of {week_start}")
        for q in existing:
            target    = q["target_leads"]
            delivered = q.get("tasks_completed", 0)
            pct       = min(int(delivered / target * 100), 100) if target else 0
            st.markdown(f"""
            <div class="quota-card">
                <div style="font-weight:600;">{q['researcher_name']}</div>
                <div style="font-size:12px;color:#888;margin:4px 0;">
                    Target: {target} leads · Delivered: {delivered}
                </div>
            </div>
            """, unsafe_allow_html=True)
            st.progress(pct / 100, text=f"{pct}%")

    st.markdown("---")
    st.subheader("Set Quota")

    conn        = get_connection()
    researchers = conn.execute("""
        SELECT id, name FROM users
        WHERE org_id=? AND role='researcher' AND is_active=1
        ORDER BY name
    """, (org_id,)).fetchall()
    conn.close()

    if not researchers:
        st.info("No researchers in this org.")
        return

    with st.form("set_quota_form"):
        researcher_map  = {r["name"]: r["id"] for r in researchers}
        selected        = st.selectbox("Researcher", list(researcher_map.keys()))
        week            = st.date_input("Week start (Monday)",
                                        value=date.fromisoformat(week_start))
        target_leads    = st.number_input("Lead target", min_value=0,
                                          value=100, step=10)
        target_enriched = st.number_input("Enriched target",
                                          min_value=0, value=80, step=10)
        notes           = st.text_input("Notes (optional)")

        if st.form_submit_button("Set Quota", use_container_width=True):
            set_quota(
                org_id        = org_id,
                researcher_id = researcher_map[selected],
                set_by        = user_id,
                week_start    = week.isoformat(),
                target_leads  = target_leads,
                target_enriched = target_enriched,
                notes         = notes,
            )
            st.success(f"Quota set for {selected}!")
            st.rerun()


# ── ASSIGN TASK ───────────────────────────────────────────────────────────────

def _render_assign_task(org_id: int, user_id: int):
    conn        = get_connection()
    researchers = conn.execute("""
        SELECT id, name FROM users
        WHERE org_id=? AND role='researcher' AND is_active=1
        ORDER BY name
    """, (org_id,)).fetchall()
    campaigns   = conn.execute("""
        SELECT id, name FROM campaigns
        WHERE org_id=? AND status NOT IN ('completed','closed')
        ORDER BY name
    """, (org_id,)).fetchall()
    conn.close()

    if not researchers:
        st.info("No researchers available.")
        return

    with st.form("assign_task_form"):
        st.subheader("New Task")

        title       = st.text_input("Task title *")
        task_type   = st.selectbox("Task type", [
            "enrich_batch", "find_linkedin",
            "verify_emails", "build_list"
        ], format_func=lambda x: x.replace("_", " ").title())

        col1, col2 = st.columns(2)
        with col1:
            researcher_map = {r["name"]: r["id"] for r in researchers}
            assigned_to    = st.selectbox("Assign to",
                                          list(researcher_map.keys()))
            priority       = st.selectbox("Priority",
                                          ["normal", "urgent", "low"])
        with col2:
            deadline    = st.date_input("Deadline (optional)",
                                        value=None)
            target_count = st.number_input("Lead target", min_value=0,
                                           value=50, step=10)

        camp_map    = {"None": None}
        camp_map.update({c["name"]: c["id"] for c in campaigns})
        campaign    = st.selectbox("Link to campaign (optional)",
                                   list(camp_map.keys()))
        description = st.text_area("Description / instructions", height=80)

        if st.form_submit_button("Assign Task", use_container_width=True):
            if not title.strip():
                st.error("Task title is required.")
            else:
                from services.task_service import create_task
                task_id = create_task(
                    org_id       = org_id,
                    title        = title,
                    task_type    = task_type,
                    assigned_to  = researcher_map[assigned_to],
                    assigned_by  = user_id,
                    description  = description,
                    priority     = priority,
                    deadline     = deadline.isoformat() if deadline else None,
                    target_count = target_count,
                    campaign_id  = camp_map[campaign],
                )
                st.success(f"Task #{task_id} assigned to {assigned_to}!")
                st.rerun()


# ── QUALITY REPORT ────────────────────────────────────────────────────────────

def _render_quality_report(org_id: int):
    try:
        from services.quality_service import get_org_quality_report
        report = get_org_quality_report(org_id)
    except Exception as e:
        st.error(f"Could not load quality report: {e}")
        return

    st.subheader("Team Quality Report")

    researchers = report.get("researchers", [])
    top_flags   = report.get("top_flags", [])

    if researchers:
        st.caption("Average enrichment quality per researcher (this week)")
        for r in researchers:
            name       = r.get("name", "?")
            count      = r.get("enriched_count") or 0
            avg_q      = round((r.get("avg_quality") or 0) * 100)
            colour     = "#3d9e6a" if avg_q >= 80 else "#c9a96e" if avg_q >= 50 else "#d45050"
            col_name, col_bar, col_count = st.columns([2, 3, 1])
            with col_name:
                st.markdown(f"**{name}**")
            with col_bar:
                st.markdown(
                    f'<div style="background:#f0f0f0;border-radius:4px;height:10px;margin-top:6px">'
                    f'<div style="background:{colour};height:10px;border-radius:4px;width:{avg_q}%"></div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            with col_count:
                st.markdown(f"**{avg_q}%**  _{count} leads_")

    if top_flags:
        st.divider()
        st.caption("Top unresolved flags (what's going wrong)")
        for f in top_flags:
            label = f.get("flag_type", "?").replace("_", " ").title()
            count = f.get("c", 0)
            st.markdown(f"- **{label}**: {count} open flags")
