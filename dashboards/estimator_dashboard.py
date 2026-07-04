"""
dashboards/estimator_dashboard.py — Dashin Research Platform
Cost visibility for managers.
- Weekly cost snapshot
- Savings from reused leads
- Researcher cost breakdown
- Forecast based on benchmarks
- AI usage vs. pattern savings
"""

import streamlit as st
import pandas as pd
from datetime import datetime, timezone, date, timedelta
from core.db import get_connection
from core.ai_tracker import get_org_usage, get_feature_breakdown
from services.learning_service import (
    get_ai_savings_report, get_org_benchmarks,
)

def _rows(cursor_result):
    """Convert list of sqlite3.Row to list of dicts."""
    return [dict(r) for r in cursor_result]

def _row(cursor_result):
    """Convert sqlite3.Row to dict, or return {} if None."""
    return dict(cursor_result) if cursor_result else {}


STYLES = """
<style>
/* Estimator-specific — shared components in core/styles.py */
</style>
"""


def render(user: dict):
    from core.styles import inject_shared_css
    inject_shared_css()
    st.markdown(STYLES, unsafe_allow_html=True)

    org_id = user["org_id"]

    today      = date.today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    week_end   = (today - timedelta(days=today.weekday()) +
                  timedelta(days=6)).isoformat()

    st.markdown(f"""
    <div class="est-header">
        <div class="est-title">Cost Estimator</div>
        <div class="est-sub">
            Week of {week_start} ·
            Organisation: {user.get('org_name','?')}
        </div>
    </div>
    """, unsafe_allow_html=True)

    tab1, tab2, tab3, tab4 = st.tabs([
        "📊 This Week",
        "🤖 AI Usage",
        "💡 Savings",
        "🔮 Forecast",
    ])

    with tab1:
        _render_this_week(org_id, week_start, week_end)
    with tab2:
        _render_ai_usage(org_id)
    with tab3:
        _render_savings(org_id)
    with tab4:
        _render_forecast(org_id)


# ── THIS WEEK ─────────────────────────────────────────────────────────────────

def _render_this_week(org_id: int, week_start: str, week_end: str):
    conn = get_connection()

    # Leads worked this week
    fresh = conn.execute("""
        SELECT COUNT(*) AS c FROM enrichment e
        JOIN leads l ON l.id = e.lead_id
        WHERE l.org_id=?
          AND e.enriched_at BETWEEN ? AND ?
          AND e.email IS NOT NULL AND e.email LIKE '%@%'
    """, (org_id, week_start, week_end + "T23:59:59")).fetchone()["c"]

    no_email = conn.execute("""
        SELECT COUNT(*) AS c FROM enrichment e
        JOIN leads l ON l.id = e.lead_id
        WHERE l.org_id=?
          AND e.enriched_at BETWEEN ? AND ?
          AND (e.email IS NULL OR e.email NOT LIKE '%@%')
    """, (org_id, week_start, week_end + "T23:59:59")).fetchone()["c"]

    rejected = conn.execute("""
        SELECT COUNT(*) AS c FROM rejections r
        JOIN leads l ON l.id = r.lead_id
        WHERE l.org_id=?
          AND r.rejected_at BETWEEN ? AND ?
    """, (org_id, week_start, week_end + "T23:59:59")).fetchone()["c"]

    reused = conn.execute("""
        SELECT COUNT(*) AS c FROM lead_usage lu
        JOIN leads l ON l.id = lu.lead_id
        WHERE l.org_id=?
          AND lu.used_at BETWEEN ? AND ?
          AND EXISTS (
              SELECT 1 FROM lead_usage lu2
              WHERE lu2.lead_id = lu.lead_id
                AND lu2.client_id != lu.client_id
          )
    """, (org_id, week_start, week_end + "T23:59:59")).fetchone()["c"]

    # Researcher time + cost this week
    researcher_data = conn.execute("""
        SELECT u.name, u.hourly_rate,
               SUM(e.minutes_spent) AS total_mins,
               COUNT(*) AS leads_done
        FROM enrichment e
        JOIN leads l ON l.id = e.lead_id
        JOIN users u ON u.id = e.enriched_by
        WHERE l.org_id=?
          AND e.enriched_at BETWEEN ? AND ?
          AND u.role='researcher'
        GROUP BY u.id
        ORDER BY total_mins DESC
    """, (org_id, week_start, week_end + "T23:59:59")).fetchall()

    conn.close()

    # Cost calculation
    total_mins  = sum(r["total_mins"] or 0 for r in researcher_data)
    total_cost  = sum(
        ((r["total_mins"] or 0) / 60) * (r["hourly_rate"] or 0)
        for r in researcher_data
    )

    benchmarks  = get_org_benchmarks(org_id)
    avg_mins    = benchmarks.get("avg_enrichment_mins", 8)
    cost_per_lead = (avg_mins / 60) * 15  # assume £15/hr if no rate set

    # Metrics
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(f"""
        <div class="cost-card">
            <div class="cost-num">{fresh}</div>
            <div class="cost-label">Leads with Email</div>
        </div>
        """, unsafe_allow_html=True)
    with c2:
        st.markdown(f"""
        <div class="cost-card">
            <div class="cost-num">{no_email}</div>
            <div class="cost-label">No Email Found</div>
        </div>
        """, unsafe_allow_html=True)
    with c3:
        st.markdown(f"""
        <div class="cost-card">
            <div class="cost-num">£{total_cost:.0f}</div>
            <div class="cost-label">Researcher Cost</div>
        </div>
        """, unsafe_allow_html=True)
    with c4:
        st.markdown(f"""
        <div class="cost-card">
            <div class="cost-num green">{reused}</div>
            <div class="cost-label">Reused Leads</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("---")

    # Researcher breakdown
    if researcher_data:
        st.subheader("Researcher Breakdown")
        rows_html = ""
        for r in researcher_data:
            mins    = r["total_mins"] or 0
            hrs     = mins / 60
            cost    = hrs * (r["hourly_rate"] or 0)
            leads   = r["leads_done"] or 0
            cpl     = cost / leads if leads > 0 else 0

            rows_html += f"""
            <div class="breakdown-row">
                <div>
                    <b>{r['name']}</b><br>
                    <small style="color:#888;">{leads} leads ·
                    {mins:.0f} mins total</small>
                </div>
                <div style="text-align:right;">
                    <div style="font-family:monospace;">
                        £{cost:.2f}
                    </div>
                    <div style="font-size:11px;color:#888;">
                        £{cpl:.2f}/lead
                    </div>
                </div>
            </div>
            """

        st.markdown(f"""
        <div style="background:white;border:1px solid #E8E6E1;
                    border-radius:8px;padding:16px 20px;">
            {rows_html}
        </div>
        """, unsafe_allow_html=True)

    # Save snapshot
    if st.button("💾 Save This Week's Snapshot"):
        _save_snapshot(org_id, week_start, week_end,
                       fresh, reused, total_cost, researcher_data)
        st.success("Snapshot saved!")


def _save_snapshot(org_id: int, week_start: str, week_end: str,
                    fresh: int, reused: int, cost: float,
                    researcher_data: list):
    import json
    conn = get_connection()
    avg_mins = (sum(r["total_mins"] or 0 for r in researcher_data) /
                len(researcher_data)) if researcher_data else 0

    # Estimate cost per fresh lead (avg researcher rate)
    avg_rate = (
        sum(r["hourly_rate"] or 0 for r in researcher_data) /
        len(researcher_data)
    ) if researcher_data else 15

    mins_per = avg_mins / max(
        sum(r["leads_done"] or 0 for r in researcher_data), 1
    )
    cost_per = (mins_per / 60) * avg_rate

    breakdown = {r["name"]: {
        "leads": r["leads_done"],
        "mins":  r["total_mins"],
        "cost":  ((r["total_mins"] or 0) / 60) * (r["hourly_rate"] or 0),
    } for r in researcher_data}

    conn.execute("""
        INSERT INTO weekly_cost_snapshots
            (org_id, week_start, week_end,
             fresh_leads_count, fresh_leads_cost,
             reused_leads_count, reused_leads_saved,
             researcher_breakdown, snapshot_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (org_id, week_start, week_end,
          fresh, cost,
          reused, reused * cost_per,
          json.dumps(breakdown),
          datetime.now(timezone.utc).replace(tzinfo=None).isoformat()))
    conn.commit()
    conn.close()


# ── AI USAGE ─────────────────────────────────────────────────────────────────

def _render_ai_usage(org_id: int):
    usage = get_org_usage(org_id)
    budget = usage.get("budget_usd", 0)
    cost   = usage.get("cost_usd", 0)
    pct    = usage.get("pct_used", 0)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(f"""
        <div class="cost-card">
            <div class="cost-num gold">${cost:.3f}</div>
            <div class="cost-label">Used This Period</div>
        </div>
        """, unsafe_allow_html=True)
    with col2:
        st.markdown(f"""
        <div class="cost-card">
            <div class="cost-num">${budget:.0f}</div>
            <div class="cost-label">Monthly Budget</div>
        </div>
        """, unsafe_allow_html=True)
    with col3:
        remaining = max(budget - cost, 0)
        st.markdown(f"""
        <div class="cost-card">
            <div class="cost-num green">${remaining:.2f}</div>
            <div class="cost-label">Remaining</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("---")
    st.markdown(f"**Budget Usage: {pct}%**")
    bar_color = ("#F44336" if pct >= 80 else
                 "#FFC107" if pct >= 60 else "#4CAF50")
    st.markdown(f"""
    <div class="ai-usage-bar">
        <div class="ai-usage-fill"
             style="width:{min(pct,100)}%;background:{bar_color};"></div>
    </div>
    """, unsafe_allow_html=True)

    st.caption(f"Period: {usage.get('period_start','')[:10]} → "
               f"{usage.get('period_end','')[:10]}")

    if pct >= 80:
        st.warning("⚠ Approaching AI budget limit. "
                   "Contact Dashin to upgrade or wait for next billing period.")

    # Feature breakdown
    period_start = usage.get("period_start","")
    if period_start:
        breakdown = get_feature_breakdown(org_id, period_start)
        if breakdown:
            st.markdown("---")
            st.subheader("Cost by Feature")
            df = pd.DataFrame([{
                "Feature": b["feature"].title(),
                "Calls":   b["calls"],
                "Tokens":  f"{(b['tokens_in'] + b['tokens_out']):,}",
                "Cost ($)":round(b["cost"], 4),
            } for b in breakdown])
            st.dataframe(df, use_container_width=True, hide_index=True)

    # AI savings from pattern learning
    savings = get_ai_savings_report(org_id)
    if savings.get("total_sessions", 0) > 0:
        st.markdown("---")
        st.subheader("AI Savings from Pattern Learning")

        col1, col2, col3 = st.columns(3)
        col1.metric("Sessions Using Saved Patterns",
                    f"{savings['pattern_sessions']} / {savings['total_sessions']}")
        col2.metric("Pattern Coverage",
                    f"{savings['pattern_pct']}%")
        col3.metric("Estimated Savings",
                    f"${savings['estimated_saved_usd']:.3f}")

        st.caption(f"{savings['domains_learned']} domains learned so far. "
                   f"The more you scrape, the less AI you use.")


# ── SAVINGS ───────────────────────────────────────────────────────────────────

def _render_savings(org_id: int):
    conn = get_connection()

    # All-time reuse stats
    reuse = conn.execute("""
        SELECT COUNT(*) AS c,
               COUNT(DISTINCT lead_id) AS unique_leads
        FROM lead_usage lu
        WHERE EXISTS (
            SELECT 1 FROM leads l
            WHERE l.id = lu.lead_id AND l.org_id=?
        )
    """, (org_id,)).fetchone()

    # Leads used for 2+ clients
    multi = conn.execute("""
        SELECT COUNT(*) AS c FROM (
            SELECT lead_id, COUNT(DISTINCT client_id) AS clients
            FROM lead_usage lu
            JOIN leads l ON l.id = lu.lead_id
            WHERE l.org_id=?
            GROUP BY lead_id
            HAVING clients >= 2
        )
    """, (org_id,)).fetchone()["c"]

    benchmarks = get_org_benchmarks(org_id)
    avg_mins   = benchmarks.get("avg_enrichment_mins", 8)

    # Estimate avg cost per fresh lead
    avg_rate = conn.execute("""
        SELECT AVG(hourly_rate) AS avg
        FROM users
        WHERE org_id=? AND role='researcher'
          AND is_active=1 AND hourly_rate > 0
    """, (org_id,)).fetchone()["avg"] or 15

    cost_per_lead = (avg_mins / 60) * avg_rate
    total_saved   = multi * cost_per_lead

    # Historical snapshots
    snapshots = conn.execute("""
        SELECT * FROM weekly_cost_snapshots
        WHERE org_id=?
        ORDER BY week_start DESC
        LIMIT 12
    """, (org_id,)).fetchall()

    conn.close()

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(f"""
        <div class="cost-card">
            <div class="cost-num green">{multi}</div>
            <div class="cost-label">Leads Reused 2+ Times</div>
        </div>
        """, unsafe_allow_html=True)
    with c2:
        st.markdown(f"""
        <div class="cost-card">
            <div class="cost-num gold">£{cost_per_lead:.2f}</div>
            <div class="cost-label">Avg Cost Per Fresh Lead</div>
        </div>
        """, unsafe_allow_html=True)
    with c3:
        st.markdown(f"""
        <div class="cost-card">
            <div class="cost-num green">£{total_saved:.0f}</div>
            <div class="cost-label">Est. Saved via Reuse</div>
        </div>
        """, unsafe_allow_html=True)

    if snapshots:
        st.markdown("---")
        st.subheader("Weekly History")
        df = pd.DataFrame([{
            "Week":            s["week_start"][:10],
            "Fresh Leads":     s["fresh_leads_count"],
            "Fresh Cost (£)":  round(s["fresh_leads_cost"], 2),
            "Reused Leads":    s["reused_leads_count"],
            "Saved (£)":       round(s["reused_leads_saved"], 2),
        } for s in snapshots])
        st.dataframe(df, use_container_width=True, hide_index=True)


# ── FORECAST ─────────────────────────────────────────────────────────────────

def _render_forecast(org_id: int):
    st.subheader("Cost Forecast")
    st.caption("Estimated based on your org's benchmarks.")

    benchmarks = get_org_benchmarks(org_id)

    if not benchmarks:
        st.info("Not enough data yet. Benchmarks build automatically "
                "as your team works through leads.")
        return

    avg_mins  = benchmarks.get("avg_enrichment_mins", 8)
    reject_rt = benchmarks.get("avg_rejection_rate", 20)

    conn = get_connection()
    avg_rate = conn.execute("""
        SELECT AVG(hourly_rate) AS avg FROM users
        WHERE org_id=? AND role='researcher'
          AND is_active=1 AND hourly_rate > 0
    """, (org_id,)).fetchone()["avg"] or 15
    conn.close()

    # Inputs
    col1, col2 = st.columns(2)
    with col1:
        target_leads = st.number_input(
            "Target enriched leads",
            min_value=10, max_value=10000,
            value=500, step=50
        )
        hourly_rate = st.number_input(
            "Avg researcher hourly rate (£)",
            min_value=1.0, value=float(round(avg_rate, 2)),
            step=0.5
        )
    with col2:
        rejection_rt = st.number_input(
            "Expected rejection rate (%)",
            min_value=0, max_value=80,
            value=int(reject_rt), step=1
        )
        reuse_pct = st.slider(
            "Expected reuse %",
            min_value=0, max_value=50, value=20,
            help="% of leads that can be reused from inventory"
        )

    # Calculate
    usable_rate  = 1 - (rejection_rt / 100)
    raw_needed   = int(target_leads / usable_rate) if usable_rate > 0 else target_leads
    fresh_needed = int(raw_needed * (1 - reuse_pct / 100))
    reused_leads = raw_needed - fresh_needed

    cost_per     = (avg_mins / 60) * hourly_rate
    total_cost   = fresh_needed * cost_per
    savings      = reused_leads * cost_per
    hours_needed = (raw_needed * avg_mins) / 60

    st.markdown("---")
    st.subheader("Estimate")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Leads to Process", f"{raw_needed:,}")
        st.metric("Fresh Leads Needed", f"{fresh_needed:,}")
    with c2:
        st.metric("Researcher Hours", f"{hours_needed:.0f}h")
        st.metric("Estimated Cost", f"£{total_cost:,.0f}")
    with c3:
        st.metric("Reused from Inventory", f"{reused_leads:,}")
        st.metric("Estimated Savings", f"£{savings:,.0f}",
                  delta=f"${savings/1:.0f} saved")

    st.info(
        f"**Summary:** To deliver {target_leads} enriched leads at "
        f"a {rejection_rt}% rejection rate, your team needs to process "
        f"~{raw_needed:,} leads. With {reuse_pct}% reuse, "
        f"you'll research {fresh_needed:,} fresh leads at "
        f"£{cost_per:.2f}/lead — total estimate: **£{total_cost:,.0f}**."
    )
