"""
dashboards/inventory_dashboard.py
Inventory — browse all leads, filter by status/industry/persona,
archive into named lists, check client conflicts, bulk actions.
"""

import streamlit as st
import sys, json
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.db import get_connection, init_db
from services.access_control import get_visible_leads_query, get_visible_org_ids

def _safe_row(row):
    """Convert sqlite3.Row or dict to dict safely. Handles both db.py versions."""
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except Exception:
        return {}

# ── STYLE ─────────────────────────────────────────────────────────────────────
CSS = """
<style>
/* MultiSelect widget (inventory-specific) */
div.stMultiSelect > div > div {
    border: 1px solid var(--border) !important;
    border-radius: 7px !important;
    background: var(--surface-2) !important;
}
/* Pagination */
.pag-info { font-size: 12px; color: var(--text-3); text-align: center; padding: 12px 0; }
/* Empty state */
.empty-state { text-align: center; padding: 48px 24px; color: #BBB; font-size: 14px; }
.empty-icon  { font-size: 36px; margin-bottom: 12px; }
/* Pointer cursor on lead rows */
.tbl tr:hover td { cursor: pointer; }
</style>
"""

# ── HELPERS ───────────────────────────────────────────────────────────────────

STATUS_OPTIONS = ["all","new","assigned","in_progress","enriched","used","archived"]
PERSONA_OPTIONS = ["all","Decision Maker","Senior Influencer","Influencer","IC","Unknown"]
REJECTION_REASONS = ["duplicate","wrong_persona","no_company","insufficient_data","out_of_market","other"]

def status_badge(s):
    cls = {"new":"b-new","assigned":"b-assigned","in_progress":"b-progress",
           "enriched":"b-enriched","used":"b-used","archived":"b-archived"}.get(s,"b-new")
    label = s.replace("_"," ").title()
    return f'<span class="badge {cls}">{label}</span>'

def persona_badge(p):
    cls = {"Decision Maker":"p-dm","Senior Influencer":"p-si",
           "Influencer":"p-inf","IC":"p-ic"}.get(p,"p-unk")
    return f'<span class="badge {cls}">{p or "Unknown"}</span>'

def get_stats(org_id=None, user=None):
    conn = get_connection()
    out = {}

    # Build the visibility filter
    if user:
        vis_where, vis_params = get_visible_leads_query(user)
        org_clause = f"AND ({vis_where})"
        org_param  = vis_params
    elif org_id:
        org_clause = "AND org_id=?"
        org_param  = [org_id]
    else:
        org_clause = ""
        org_param  = []

    for s in ["new","assigned","in_progress","enriched","used","archived"]:
        row = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM leads WHERE status=? {org_clause}",
            [s] + org_param
        ).fetchone()
        out[s] = (row if isinstance(row, dict) else (dict(row) if row else {})).get("cnt") or 0
    out["total"] = sum(out.values())
    row2 = conn.execute(
        f"SELECT COUNT(*) AS cnt FROM leads WHERE status='enriched' {org_clause}",
        org_param
    ).fetchone()
    out["reusable"] = (row2 if isinstance(row2, dict) else (dict(row2) if row2 else {})).get("cnt") or 0
    conn.close()
    return out

def get_leads(status=None, persona=None, search=None, industry=None,
              list_id=None, stale=False, page=1, per_page=40, org_id=None, user=None):
    conn = get_connection()
    where, params = [], []

    # Use access control if user context is provided
    if user:
        vis_where, vis_params = get_visible_leads_query(user)
        where.append(f"({vis_where})")
        params.extend(vis_params)
    elif org_id:
        where.append("l.org_id=?"); params.append(org_id)

    if status and status != "all":
        where.append("l.status=?"); params.append(status)
    if persona and persona != "all":
        where.append("l.persona=?"); params.append(persona)
    if search:
        where.append("(l.full_name LIKE ? OR co.name LIKE ? OR l.title LIKE ?)")
        params += [f"%{search}%"]*3
    if industry:
        # Match the industry from any source: scrape (leads/companies) or enrichment.
        where.append("COALESCE(NULLIF(l.industry,''), NULLIF(co.industry,''), NULLIF(e.industry,''))=?")
        params.append(industry)
    if list_id:
        where.append("l.archived_list_id=?"); params.append(list_id)
    if stale:
        # D2: enriched more than 90 days ago = stale, flag for re-enrichment.
        where.append("l.enriched_at IS NOT NULL AND l.enriched_at < date('now','-90 days')")

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    offset = (page-1)*per_page

    count_row = conn.execute(f"""
        SELECT COUNT(*) AS cnt FROM leads l
        LEFT JOIN companies co ON co.id=l.company_id
        LEFT JOIN enrichment e ON e.lead_id=l.id
        {where_sql}
    """, params).fetchone()
    total = (count_row if isinstance(count_row, dict) else dict(count_row) if count_row else {}).get("cnt") or 0

    rows = conn.execute(f"""
        SELECT l.*, co.name AS company_name,
               e.email, e.linkedin_url, e.country, e.industry AS enriched_industry,
               e.company_size, e.notes, e.minutes_spent,
               u.name AS enriched_by_name,
               al.name AS list_name
        FROM leads l
        LEFT JOIN companies co ON co.id=l.company_id
        LEFT JOIN enrichment e ON e.lead_id=l.id
        LEFT JOIN users u ON u.id=e.enriched_by
        LEFT JOIN archived_lists al ON al.id=l.archived_list_id
        {where_sql}
        ORDER BY l.last_seen_at DESC
        LIMIT ? OFFSET ?
    """, params+[per_page, offset]).fetchall()
    conn.close()
    return [dict(r) for r in rows], total

def get_lead_clients(lead_id):
    """Return all clients this lead has been used for."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT c.name, lu.used_at, ca.name AS campaign
        FROM lead_usage lu
        JOIN clients c ON c.id=lu.client_id
        LEFT JOIN campaigns ca ON ca.id=lu.campaign_id
        WHERE lu.lead_id=?
        ORDER BY lu.used_at DESC
    """, (lead_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_lead_events(lead_id):
    """Return all events this lead appeared at."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT event_name, category, scraped_at
        FROM lead_appearances
        WHERE lead_id=?
        ORDER BY scraped_at DESC
    """, (lead_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_archived_lists():
    conn = get_connection()
    rows = conn.execute("""
        SELECT al.*, COUNT(l.id) AS lead_count,
               u.name AS created_by_name
        FROM archived_lists al
        LEFT JOIN leads l ON l.archived_list_id=al.id
        LEFT JOIN users u ON u.id=al.created_by
        GROUP BY al.id
        ORDER BY al.created_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_clients():
    conn = get_connection()
    rows = conn.execute("SELECT id, name FROM clients ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_industries():
    # Industry can come from the scrape (leads/companies) or from enrichment.
    # Union all three sources so the filter covers every categorized lead (D1).
    conn = get_connection()
    rows = conn.execute("""
        SELECT DISTINCT industry FROM (
            SELECT industry FROM enrichment
            UNION SELECT industry FROM leads
            UNION SELECT industry FROM companies
        )
        WHERE industry IS NOT NULL AND industry != ''
        ORDER BY industry
    """).fetchall()
    conn.close()
    return [r["industry"] for r in rows]

def create_archived_list(name, industry, description, user_id):
    conn = get_connection()
    cur = conn.execute("""
        INSERT INTO archived_lists (name, industry, description, created_by)
        VALUES (?,?,?,?)
    """, (name, industry, description, user_id))
    list_id = cur.lastrowid
    conn.commit()
    conn.close()
    return list_id

def archive_leads(lead_ids, list_id):
    conn = get_connection()
    conn.execute(f"""
        UPDATE leads SET status='archived', archived_at=datetime('now'),
        archived_list_id=?
        WHERE id IN ({','.join('?'*len(lead_ids))})
    """, [list_id]+lead_ids)
    conn.commit()
    conn.close()

def update_lead_status(lead_id, new_status):
    conn = get_connection()
    conn.execute("UPDATE leads SET status=? WHERE id=?", (new_status, lead_id))
    conn.commit()
    conn.close()

def check_client_conflicts(lead_ids, client_id):
    """Returns list of lead_ids already used for this client."""
    if not lead_ids or not client_id:
        return []
    conn = get_connection()
    placeholders = ','.join('?'*len(lead_ids))
    rows = conn.execute(f"""
        SELECT lead_id FROM lead_usage
        WHERE lead_id IN ({placeholders}) AND client_id=?
    """, lead_ids+[client_id]).fetchall()
    conn.close()
    return [r["lead_id"] for r in rows]


# ── DETAIL PANEL ──────────────────────────────────────────────────────────────

def render_detail_panel(lead, user):
    st.markdown(f"""
    <div class="detail-panel">
      <div class="detail-name">{lead['full_name']}</div>
      <div class="detail-sub">{lead.get('title') or '—'} · {lead.get('company_name') or '—'}</div>
      <div class="detail-grid">
        <div class="detail-field">
          <div class="detail-field-label">Status</div>
          <div class="detail-field-val">{status_badge(lead.get('status','new'))}</div>
        </div>
        <div class="detail-field">
          <div class="detail-field-label">Persona</div>
          <div class="detail-field-val">{persona_badge(lead.get('persona'))}</div>
        </div>
        <div class="detail-field">
          <div class="detail-field-label">Email</div>
          <div class="detail-field-val">{lead.get('email') or '—'}</div>
        </div>
        <div class="detail-field">
          <div class="detail-field-label">LinkedIn</div>
          <div class="detail-field-val">{('<a href="'+lead['linkedin_url']+'" target="_blank">View ↗</a>') if lead.get('linkedin_url') else '—'}</div>
        </div>
        <div class="detail-field">
          <div class="detail-field-label">Country</div>
          <div class="detail-field-val">{lead.get('country') or '—'}</div>
        </div>
        <div class="detail-field">
          <div class="detail-field-label">Industry</div>
          <div class="detail-field-val">{lead.get('enriched_industry') or '—'}</div>
        </div>
        <div class="detail-field">
          <div class="detail-field-label">Company Size</div>
          <div class="detail-field-val">{lead.get('company_size') or '—'}</div>
        </div>
        <div class="detail-field">
          <div class="detail-field-label">Times Seen</div>
          <div class="detail-field-val">{lead.get('times_seen',1)}× across events</div>
        </div>
      </div>
    """, unsafe_allow_html=True)

    # Events history
    events = get_lead_events(lead['id'])
    if events:
        ev_html = "".join([
            f'<div style="font-size:11px;color:#888;padding:4px 0;border-bottom:1px solid #f0ede8">'
            f'<b style="color:#555">{e.get("event_name") or "Unknown event"}</b>'
            f'{"  ·  "+e["category"] if e.get("category") else ""}'
            f'<span style="float:right;color:#bbb">{(e.get("scraped_at") or "")[:10]}</span></div>'
            for e in events
        ])
        st.markdown(f"""
        <div style="margin-bottom:12px">
          <div style="font-size:10px;color:#aaa;text-transform:uppercase;letter-spacing:1.2px;
            font-weight:700;margin-bottom:6px">Event History</div>
          {ev_html}
        </div>
        """, unsafe_allow_html=True)

    # Client usage
    used_clients = get_lead_clients(lead['id'])
    if used_clients:
        cl_html = "".join([
            f'<div style="font-size:11px;color:#d45050;padding:4px 0;border-bottom:1px solid #fdecea">'
            f'<b>{c["name"]}</b>'
            f'{"  ·  "+c["campaign"] if c.get("campaign") else ""}'
            f'<span style="float:right;color:#f0b8b8">{(c.get("used_at") or "")[:10]}</span></div>'
            for c in used_clients
        ])
        st.markdown(f"""
        <div style="margin-bottom:12px">
          <div style="font-size:10px;color:#d45050;text-transform:uppercase;letter-spacing:1.2px;
            font-weight:700;margin-bottom:6px">⚠ Already Used For</div>
          {cl_html}
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown('<div class="conflict-ok">✓ Not used for any client yet — safe to use</div>',
                    unsafe_allow_html=True)

    if lead.get('notes'):
        st.markdown(f"""
        <div style="background:#fffdf5;border:1px solid #e8d5a8;border-radius:7px;
          padding:12px 14px;font-size:12px;color:#8a7040;margin-bottom:12px">
          <b>Notes:</b> {lead['notes']}
        </div>""", unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)

    # Quick actions
    role = user.get("role","researcher")
    if role in ("admin","manager"):
        cols = st.columns(3)
        with cols[0]:
            if lead.get("status") != "archived":
                if st.button("📦 Archive", key=f"arch_{lead['id']}", use_container_width=True):
                    st.session_state["archive_lead_id"] = lead['id']
                    st.session_state["detail_lead"] = None
                    st.rerun()
        with cols[1]:
            new_status = st.selectbox("Change status",
                ["new","assigned","in_progress","enriched","used","archived"],
                index=["new","assigned","in_progress","enriched","used","archived"].index(
                    lead.get("status","new")),
                key=f"st_{lead['id']}", label_visibility="collapsed")
        with cols[2]:
            if st.button("✓ Update", key=f"upd_{lead['id']}", use_container_width=True, type="primary"):
                update_lead_status(lead['id'], new_status)
                st.success("Status updated")
                st.session_state["detail_lead"] = None
                st.rerun()

    if st.button("✕ Close", key=f"close_{lead['id']}", type="secondary"):
        st.session_state["detail_lead"] = None
        st.rerun()


# ── ARCHIVED LISTS VIEW ────────────────────────────────────────────────────────

def render_archived_lists(user):
    st.markdown('<div style="font-family:\'Playfair Display\',serif;font-size:28px;font-weight:700;color:#1a1917;margin-bottom:4px">Archived Lists</div>', unsafe_allow_html=True)
    st.markdown('<div style="font-size:13px;color:#999;margin-bottom:24px">Named lead lists organised by industry. Reusable assets.</div>', unsafe_allow_html=True)

    lists = get_archived_lists()

    # Create new list
    role = user.get("role","researcher")
    if role in ("admin","manager"):
        with st.expander("＋ Create New List"):
            n1, n2 = st.columns(2)
            with n1:
                ln  = st.text_input("List name", placeholder="EdTech UK Q1 2026")
                li  = st.text_input("Industry", placeholder="Education Technology")
            with n2:
                ld  = st.text_area("Description", placeholder="Decision makers from UK EdTech events...", height=90)
            if st.button("Create List", type="primary"):
                if ln.strip():
                    create_archived_list(ln.strip(), li.strip(), ld.strip(), user['id'])
                    st.success(f"✅ List '{ln}' created")
                    st.rerun()
                else:
                    st.error("List name is required.")

    if not lists:
        st.markdown('<div class="empty-state"><div class="empty-icon">📂</div>No archived lists yet.</div>', unsafe_allow_html=True)
        return

    for lst in lists:
        with st.container():
            st.markdown(f"""
            <div class="list-card">
              <div style="display:flex;align-items:flex-start;justify-content:space-between">
                <div>
                  <div class="list-card-name">{lst['name']}</div>
                  <div class="list-card-meta">
                    {lst.get('industry') or 'No industry set'}
                    · <b style="color:#1a1917">{lst['lead_count']}</b> leads
                    · Created {(lst.get('created_at') or '')[:10]}
                    {' · by '+lst['created_by_name'] if lst.get('created_by_name') else ''}
                  </div>
                  {('<div class="list-card-desc">'+lst['description']+'</div>') if lst.get('description') else ''}
                </div>
              </div>
            </div>
            """, unsafe_allow_html=True)
            c1, c2, _ = st.columns([1,1,4])
            with c1:
                if st.button(f"View leads", key=f"vl_{lst['id']}", use_container_width=True):
                    st.session_state["inv_list_filter"] = lst['id']
                    st.session_state["inv_view"] = "leads"
                    st.rerun()
            with c2:
                df_data = _get_list_export(lst['id'])
                if df_data:
                    import pandas as pd
                    df = pd.DataFrame(df_data)
                    st.download_button(f"⬇ Export CSV", df.to_csv(index=False).encode(),
                                       f"{lst['name'].replace(' ','_')}.csv", "text/csv",
                                       key=f"exp_{lst['id']}", use_container_width=True)

def _get_list_export(list_id):
    conn = get_connection()
    rows = conn.execute("""
        SELECT l.full_name, l.title, co.name AS company,
               l.persona, l.status, e.email, e.linkedin_url,
               e.country, e.industry, e.company_size, l.tags,
               l.first_seen_at, l.enriched_at
        FROM leads l
        LEFT JOIN companies co ON co.id=l.company_id
        LEFT JOIN enrichment e ON e.lead_id=l.id
        WHERE l.archived_list_id=?
        ORDER BY l.full_name
    """, (list_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── CLIENT CONFLICT CHECKER ───────────────────────────────────────────────────

def render_conflict_checker():
    st.markdown('<div class="sec-hd">Client Conflict Checker</div>', unsafe_allow_html=True)
    st.markdown('<p style="font-size:13px;color:#888;margin-bottom:16px">Check which leads in a list are already used for a specific client before exporting.</p>', unsafe_allow_html=True)

    clients = get_clients()
    lists   = get_archived_lists()

    if not clients:
        st.info("No clients added yet. Add clients in the Admin dashboard.")
        return
    if not lists:
        st.info("No archived lists yet.")
        return

    c1, c2 = st.columns(2)
    with c1:
        sel_list = st.selectbox("Select list", lists,
            format_func=lambda x: f"{x['name']} ({x['lead_count']} leads)")
    with c2:
        sel_client = st.selectbox("Select client", clients,
            format_func=lambda x: x['name'])

    if st.button("Check Conflicts", type="primary"):
        _oid = (user or {}).get("org_id") if 'user' in dir() else None
        leads_in_list, _ = get_leads(list_id=sel_list['id'], per_page=9999, org_id=_oid)
        lead_ids = [l['id'] for l in leads_in_list]
        conflicts = check_client_conflicts(lead_ids, sel_client['id'])

        safe    = len(lead_ids) - len(conflicts)
        conflict_pct = round(len(conflicts)/len(lead_ids)*100) if lead_ids else 0

        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Total in List", len(lead_ids))
        col_b.metric("Safe to Use", safe, delta=f"{100-conflict_pct}%")
        col_c.metric("Already Used", len(conflicts), delta=f"-{conflict_pct}%", delta_color="inverse")

        if conflicts:
            st.markdown(f'<div class="conflict-warn">⚠ {len(conflicts)} lead(s) already used for <b>{sel_client["name"]}</b> and will be excluded from any export.</div>', unsafe_allow_html=True)
            # Show conflicting leads
            conflict_names = [l['full_name'] for l in leads_in_list if l['id'] in conflicts]
            with st.expander(f"View {len(conflicts)} conflicting lead(s)"):
                for n in conflict_names:
                    st.markdown(f'<div style="font-size:12px;color:#d45050;padding:3px 0">✕ {n}</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="conflict-ok">✓ No conflicts — all {len(lead_ids)} leads are safe to use for {sel_client["name"]}.</div>', unsafe_allow_html=True)


# ── MAIN LEADS TABLE ──────────────────────────────────────────────────────────

def render_leads_table(user):
    # Filters bar
    st.markdown('<div class="filter-bar">', unsafe_allow_html=True)
    f1, f2, f3, f4, f5 = st.columns([2.5, 1.5, 1.5, 1.5, 1])
    with f1:
        search = st.text_input("Search", placeholder="Search by name, company, title...",
                               label_visibility="collapsed",
                               value=st.session_state.get("inv_search",""))
    with f2:
        status = st.selectbox("Status", STATUS_OPTIONS,
                              index=STATUS_OPTIONS.index(st.session_state.get("inv_status","all")),
                              label_visibility="collapsed",
                              format_func=lambda s: "All Statuses" if s=="all" else s.replace("_"," ").title())
    with f3:
        persona = st.selectbox("Persona", PERSONA_OPTIONS,
                               index=PERSONA_OPTIONS.index(st.session_state.get("inv_persona","all")),
                               label_visibility="collapsed",
                               format_func=lambda p: "All Personas" if p=="all" else p)
    with f4:
        industries = ["all"] + get_industries()
        industry = st.selectbox("Industry", industries,
                                label_visibility="collapsed",
                                format_func=lambda i: "All Industries" if i=="all" else i)
    with f5:
        if st.button("Clear", type="secondary", use_container_width=True):
            for k in ["inv_search","inv_status","inv_persona","inv_list_filter","inv_stale"]:
                st.session_state.pop(k, None)
            st.rerun()
    stale = st.checkbox("Stale only (enriched > 90 days ago)",
                        value=st.session_state.get("inv_stale", False),
                        help="Leads whose enrichment is older than 90 days — candidates for re-enrichment.")
    st.markdown('</div>', unsafe_allow_html=True)

    # Save filter state
    st.session_state["inv_search"]  = search
    st.session_state["inv_status"]  = status
    st.session_state["inv_persona"] = persona
    st.session_state["inv_stale"]   = stale

    list_id = st.session_state.get("inv_list_filter")
    page    = st.session_state.get("inv_page", 1)

    leads, total = get_leads(
        status  = status  if status  != "all" else None,
        persona = persona if persona != "all" else None,
        search  = search  or None,
        industry= industry if industry != "all" else None,
        list_id = list_id,
        stale   = stale,
        page    = page,
        user    = user,   # use access_control for org visibility
    )

    per_page = 40
    total_pages = max(1, (total + per_page - 1) // per_page)

    # Bulk archive (admin/manager only)
    role = user.get("role","researcher")
    selected_ids = []

    if role in ("admin","manager") and leads:
        with st.expander("Bulk Actions"):
            bc1, bc2 = st.columns([3,2])
            with bc1:
                all_lists = get_archived_lists()
                if all_lists:
                    target_list = st.selectbox("Archive selected leads to list →",
                        all_lists, format_func=lambda x: x['name'],
                        label_visibility="visible")
                else:
                    st.info("Create an archived list first in the 'Archived Lists' tab.")
                    target_list = None
            with bc2:
                multi_sel = st.multiselect("Select leads to archive",
                    options=[l['id'] for l in leads],
                    format_func=lambda i: next((l['full_name'] for l in leads if l['id']==i), str(i)))
            if multi_sel and target_list and st.button("Archive Selected", type="primary"):
                archive_leads(multi_sel, target_list['id'])
                st.success(f"✅ {len(multi_sel)} leads archived to '{target_list['name']}'")
                st.rerun()

    # Table
    st.markdown(f'<div style="font-size:12px;color:#aaa;margin-bottom:10px">{total:,} leads found</div>', unsafe_allow_html=True)

    if not leads:
        st.markdown('<div class="empty-state"><div class="empty-icon">🔍</div>No leads match your filters.</div>', unsafe_allow_html=True)
        return

    # Build table HTML
    rows_html = ""
    for l in leads:
        rows_html += f"""
        <tr onclick="window.parent.document.dispatchEvent(new CustomEvent('leadClick', {{detail: {l['id']}}}))">
          <td class="n">{l['full_name']}</td>
          <td class="co">{l.get('company_name') or '—'}</td>
          <td style="color:#777">{l.get('title') or '—'}</td>
          <td>{persona_badge(l.get('persona'))}</td>
          <td>{status_badge(l.get('status','new'))}</td>
          <td style="color:#aaa;font-size:11px">{l.get('enriched_industry') or '—'}</td>
          <td style="color:#bbb;font-size:11px">{(l.get('last_seen_at') or '')[:10]}</td>
        </tr>"""

    st.markdown(f"""
    <div class="tbl"><table>
      <thead><tr>
        <th>Name</th><th>Company</th><th>Title</th>
        <th>Persona</th><th>Status</th><th>Industry</th><th>Last Seen</th>
      </tr></thead>
      <tbody>{rows_html}</tbody>
    </table></div>""", unsafe_allow_html=True)

    # Click to view detail via selectbox (Streamlit-compatible)
    sel = st.selectbox("Click a lead to view details →",
        options=[None]+[l['id'] for l in leads],
        format_func=lambda i: "— Select a lead —" if i is None else next(
            (f"{l['full_name']} · {l.get('company_name','')}" for l in leads if l['id']==i), str(i)),
        label_visibility="collapsed")
    if sel:
        lead_data = next((l for l in leads if l['id']==sel), None)
        if lead_data:
            st.markdown("---")
            render_detail_panel(lead_data, user)

    # Pagination
    st.markdown('<div class="pag-info">', unsafe_allow_html=True)
    pc1, pc2, pc3 = st.columns([1,2,1])
    with pc1:
        if page > 1 and st.button("← Prev", type="secondary", use_container_width=True):
            st.session_state["inv_page"] = page - 1
            st.rerun()
    with pc2:
        st.markdown(f'<div style="text-align:center;font-size:12px;color:#aaa;padding-top:10px">Page {page} of {total_pages} · {total:,} leads</div>', unsafe_allow_html=True)
    with pc3:
        if page < total_pages and st.button("Next →", type="secondary", use_container_width=True):
            st.session_state["inv_page"] = page + 1
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)




# ── UPLOAD TAB ────────────────────────────────────────────────────────────────

def render_upload_tab(user):
    st.markdown('''
    <div style="margin-bottom:20px">
      <div style="font-family:'Playfair Display',serif;font-size:20px;font-weight:700;
        color:#1a1917;margin-bottom:6px">Upload Enriched CSV</div>
      <div style="font-size:13px;color:#999">
        Upload a researcher-enriched CSV. The system will cross-check against inventory,
        add emails and enrichment data, and link leads to the selected client and campaign.
      </div>
    </div>
    ''', unsafe_allow_html=True)

    # ── Column detection & email validation (inline) ─────────────────────────
    def detect_columns(df):
        """Auto-detect field→column mapping from CSV headers."""
        import re
        mapping = {}
        col_lower = {c: c.lower().strip().replace(" ","_") for c in df.columns}
        NAME_KEYS    = ["full_name","name","contact","contact_name","attendee","delegate","first_name"]
        COMPANY_KEYS = ["company","organisation","organization","employer","account","company_name"]
        EMAIL_KEYS   = ["email","email_address","work_email","e-mail","e_mail"]
        LINKEDIN_KEYS= ["linkedin","linkedin_url","profile_url","linkedin_profile"]
        PHONE_KEYS   = ["phone","phone_number","mobile","telephone","tel"]
        COUNTRY_KEYS = ["country","location","region","country_region"]
        INDUSTRY_KEYS= ["industry","sector","vertical","business_type"]
        TITLE_KEYS   = ["title","job_title","position","role","job_role"]

        for col, key in col_lower.items():
            if any(k in key for k in NAME_KEYS)     and "full_name"    not in mapping: mapping["full_name"]    = col
            if any(k in key for k in COMPANY_KEYS)  and "company"      not in mapping: mapping["company"]      = col
            if any(k in key for k in EMAIL_KEYS)     and "email"        not in mapping: mapping["email"]        = col
            if any(k in key for k in LINKEDIN_KEYS)  and "linkedin_url" not in mapping: mapping["linkedin_url"] = col
            if any(k in key for k in PHONE_KEYS)     and "phone"        not in mapping: mapping["phone"]        = col
            if any(k in key for k in COUNTRY_KEYS)   and "country"      not in mapping: mapping["country"]      = col
            if any(k in key for k in INDUSTRY_KEYS)  and "industry"     not in mapping: mapping["industry"]     = col
            if any(k in key for k in TITLE_KEYS)     and "title"        not in mapping: mapping["title"]        = col
        return mapping

    def is_valid_email(email):
        import re
        if not email or not isinstance(email, str): return False
        email = email.strip().lower()
        if email in ("nan","none","","n/a","-","—"): return False
        return bool(re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", email))

    def process_upload(df, client_id, campaign_name, event_name,
                        uploaded_by_user_id, col_map=None, org_id=None):
        """Import a researcher CSV into inventory. Returns result dict."""
        from datetime import datetime
        import re, hashlib

        if col_map is None:
            col_map = detect_columns(df)

        conn = get_connection()
        now  = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        _org_id = org_id or 1

        result = {
            "new_leads": 0, "enriched": 0, "skipped": 0,
            "email_found": [], "no_email_found": [], "errors": []
        }

        def clean(val):
            if val is None: return ""
            s = str(val).strip()
            return "" if s.lower() in ("nan","none","n/a","-","—") else s

        def name_key(name, company):
            """Normalise for dedup."""
            import re
            s = (name + " " + company).lower()
            s = re.sub(r"\b(inc|ltd|llc|corp|co|the|and|plc)\b", "", s)
            return re.sub(r"[^a-z0-9]", "", s)

        for _, row in df.iterrows():
            try:
                full_name = clean(row.get(col_map.get("full_name",""), ""))
                if not full_name:
                    result["skipped"] += 1
                    continue

                company_name = clean(row.get(col_map.get("company",""), ""))
                email        = clean(row.get(col_map.get("email",""), ""))
                linkedin     = clean(row.get(col_map.get("linkedin_url",""), ""))
                phone        = clean(row.get(col_map.get("phone",""), ""))
                country      = clean(row.get(col_map.get("country",""), ""))
                industry     = clean(row.get(col_map.get("industry",""), ""))
                title        = clean(row.get(col_map.get("title",""), ""))
                has_email    = is_valid_email(email)

                # Get or create company
                company_id = None
                if company_name:
                    co_key = re.sub(r"[^a-z0-9]", "", company_name.lower())
                    co_row = conn.execute(
                        "SELECT id FROM companies WHERE name_key=? AND org_id=?",
                        (co_key, _org_id)
                    ).fetchone()
                    if co_row:
                        company_id = (co_row if isinstance(co_row, dict) else dict(co_row))["id"]
                    else:
                        cur = conn.execute(
                            "INSERT INTO companies (org_id, name, name_key, created_at) VALUES (?,?,?,?)",
                            (_org_id, company_name, co_key, now)
                        )
                        company_id = cur.lastrowid

                # Dedup lead
                nk = name_key(full_name, company_name)
                existing = conn.execute(
                    "SELECT id FROM leads WHERE name_key=? AND org_id=?",
                    (nk, _org_id)
                ).fetchone()
                existing = (existing if isinstance(existing, dict) else dict(existing)) if existing else None

                if existing:
                    lead_id     = existing["id"]
                    was_existing = True
                    result["enriched"] += 1
                else:
                    cur = conn.execute("""
                        INSERT INTO leads
                            (org_id, full_name, name_key, title, company_id,
                             status, last_seen_at, times_seen, created_at)
                        VALUES (?,?,?,?,?,?,?,1,?)
                    """, (_org_id, full_name, nk, title or None, company_id,
                          "enriched" if has_email else "no_email", now, now))
                    lead_id     = cur.lastrowid
                    was_existing = False
                    result["new_leads"] += 1

                # Upsert enrichment
                if has_email or linkedin or phone or country or industry:
                    conn.execute("""
                        INSERT INTO enrichment
                            (lead_id, org_id, email, linkedin_url, phone,
                             country, industry, enriched_by, enriched_at)
                        VALUES (?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(lead_id) DO UPDATE SET
                            email        = COALESCE(excluded.email, email),
                            linkedin_url = COALESCE(excluded.linkedin_url, linkedin_url),
                            phone        = COALESCE(excluded.phone, phone),
                            country      = COALESCE(excluded.country, country),
                            industry     = COALESCE(excluded.industry, industry),
                            enriched_at  = excluded.enriched_at
                    """, (lead_id, _org_id,
                          email or None, linkedin or None, phone or None,
                          country or None, industry or None,
                          uploaded_by_user_id, now))

                lead_info = {
                    "full_name": full_name, "company": company_name,
                    "email": email if has_email else "",
                    "country": country, "industry": industry,
                    "was_existing": was_existing
                }
                if has_email:
                    result["email_found"].append(lead_info)
                else:
                    result["no_email_found"].append(lead_info)

            except Exception as e:
                result["errors"].append(str(e))

        conn.commit()
        conn.close()
        return result

    # ── Upload form ───────────────────────────────────────────────────────────
    with st.container():
        st.markdown('<div class="launch-box">', unsafe_allow_html=True)

        c1, c2 = st.columns(2)
        with c1:
            clients = get_clients()
            if not clients:
                st.warning("No clients exist yet. Add clients in Users & Clients first.")
                st.markdown('</div>', unsafe_allow_html=True)
                return
            client = st.selectbox(
                "Client this list belongs to",
                clients,
                format_func=lambda x: x["name"]
            )
        with c2:
            campaign_name = st.text_input(
                "Campaign / project name",
                placeholder="BETT 2026 Outreach"
            )

        c3, c4 = st.columns(2)
        with c3:
            event_name = st.text_input(
                "Event or source name",
                placeholder="BETT Show 2026"
            )
        with c4:
            st.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)

        uploaded_file = st.file_uploader(
            "Upload researcher CSV",
            type=["csv"],
            help="Any CSV with name + company columns. Email, LinkedIn, industry, country auto-detected."
        )

        st.markdown('</div>', unsafe_allow_html=True)

    if not uploaded_file:
        # Show expected format hint
        st.markdown("""
        <div class="tip" style="background:#fffdf5;border:1px solid #e8d5a8;
          border-left:3px solid #c9a96e;border-radius:6px;padding:14px 18px;
          font-size:12px;color:#8a7040;margin-top:16px">
          <b>💡 Accepted column names (any format):</b><br><br>
          <b>Name:</b> Full Name, Name, Contact, Contact Name<br>
          <b>Company:</b> Company, Organisation, Employer, Account<br>
          <b>Email:</b> Email, Email Address, Work Email<br>
          <b>LinkedIn:</b> LinkedIn, LinkedIn URL, Profile URL<br>
          <b>Country:</b> Country, Location, Region<br>
          <b>Industry:</b> Industry, Sector, Vertical<br><br>
          Column names are auto-detected — no fixed template needed.
        </div>
        """, unsafe_allow_html=True)
        return

    # ── Preview detected columns ──────────────────────────────────────────────
    import pandas as pd
    # detect_columns and is_valid_email defined above

    try:
        df = pd.read_csv(uploaded_file, encoding="utf-8", on_bad_lines="skip")
    except Exception:
        try:
            uploaded_file.seek(0)
            df = pd.read_csv(uploaded_file, encoding="latin-1", on_bad_lines="skip")
        except Exception as e:
            st.error(f"Could not read CSV: {e}")
            return

    col_map = detect_columns(df)
    total_rows = len(df)

    # Count emails
    email_col = col_map.get("email")
    if email_col:
        email_count = df[email_col].apply(
            lambda x: is_valid_email(str(x).strip())
        ).sum()
        no_email_count = total_rows - email_count
    else:
        email_count = 0
        no_email_count = total_rows

    # Show detected mapping
    st.markdown('<div class="sec-hd">Detected Columns</div>', unsafe_allow_html=True)

    map_html = ""
    field_labels = {
        "full_name":"Name","company":"Company","email":"Email",
        "linkedin_url":"LinkedIn","phone":"Phone","country":"Country",
        "industry":"Industry","title":"Title"
    }
    for field, label in field_labels.items():
        detected = col_map.get(field)
        if detected:
            map_html += f'''
            <div style="display:flex;justify-content:space-between;padding:8px 0;
              border-bottom:1px solid #f0ede8;font-size:12px">
              <span style="color:#aaa;font-weight:600;text-transform:uppercase;
                letter-spacing:.8px;font-size:10px">{label}</span>
              <span style="color:#1a1917;font-weight:500">
                <span style="color:#3d9e6a">✓</span> {detected}
              </span>
            </div>'''
        else:
            map_html += f'''
            <div style="display:flex;justify-content:space-between;padding:8px 0;
              border-bottom:1px solid #f0ede8;font-size:12px">
              <span style="color:#aaa;font-weight:600;text-transform:uppercase;
                letter-spacing:.8px;font-size:10px">{label}</span>
              <span style="color:#bbb">— not found</span>
            </div>'''

    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Total Rows", f"{total_rows:,}")
    col_b.metric("✅ Email Found", f"{int(email_count):,}")
    col_c.metric("❌ No Email", f"{int(no_email_count):,}")

    st.markdown(f'''
    <div class="tbl" style="margin-bottom:20px">
      <table><tbody>{map_html}</tbody></table>
    </div>
    ''', unsafe_allow_html=True)

    # Preview first 5 rows
    with st.expander("Preview first 5 rows"):
        st.dataframe(df.head(5), use_container_width=True)

    # ── Validate before processing ────────────────────────────────────────────
    can_process = True
    if "full_name" not in col_map and "_combine_name" not in col_map:
        st.error("❌ Cannot find a name column. Check your CSV has a 'Full Name' or 'Name' column.")
        can_process = False
    if not campaign_name.strip():
        st.warning("⚠ Please enter a campaign name above.")
        can_process = False
    if not client:
        st.warning("⚠ Please select a client.")
        can_process = False

    if not can_process:
        return

    # ── Process button ────────────────────────────────────────────────────────
    st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)
    if st.button("⬆ Process & Import", type="primary", use_container_width=False):

        with st.spinner("Processing CSV — cross-checking against inventory..."):
            try:
                result = process_upload(
                    df                  = df,
                    client_id           = client["id"],
                    campaign_name       = campaign_name.strip(),
                    event_name          = event_name.strip() or campaign_name.strip(),
                    uploaded_by_user_id = user["id"],
                )
            except Exception as e:
                st.error(f"Import failed: {e}")
                return

        if result["errors"]:
            for err in result["errors"]:
                st.error(f"Error: {err}")
            return

        # ── Results summary ───────────────────────────────────────────────────
        found     = len(result["email_found"])
        no_email  = len(result["no_email_found"])
        new_l     = result["new_leads"]
        enriched  = result["enriched"]
        skipped   = result["skipped"]

        st.markdown(f"""
        <div style="background:#ecf7f0;border:1px solid #b8dfc8;border-radius:10px;
          padding:20px 24px;margin:16px 0">
          <div style="font-family:'Playfair Display',serif;font-size:18px;font-weight:700;
            color:#1a1917;margin-bottom:12px">✅ Import Complete</div>
          <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px">
            <div>
              <div style="font-family:'Playfair Display',serif;font-size:22px;
                font-weight:700;color:#3d9e6a">{found}</div>
              <div style="font-size:10px;color:#888;text-transform:uppercase;
                letter-spacing:1px;margin-top:2px">Email Found</div>
            </div>
            <div>
              <div style="font-family:'Playfair Display',serif;font-size:22px;
                font-weight:700;color:#c9a96e">{no_email}</div>
              <div style="font-size:10px;color:#888;text-transform:uppercase;
                letter-spacing:1px;margin-top:2px">No Email</div>
            </div>
            <div>
              <div style="font-family:'Playfair Display',serif;font-size:22px;
                font-weight:700;color:#4a6cf7">{new_l}</div>
              <div style="font-size:10px;color:#888;text-transform:uppercase;
                letter-spacing:1px;margin-top:2px">New to Inventory</div>
            </div>
            <div>
              <div style="font-family:'Playfair Display',serif;font-size:22px;
                font-weight:700;color:#1a1917">{enriched}</div>
              <div style="font-size:10px;color:#888;text-transform:uppercase;
                letter-spacing:1px;margin-top:2px">Existing Enriched</div>
            </div>
          </div>
          <div style="font-size:12px;color:#888;margin-top:12px">
            Campaign: <b style="color:#1a1917">{campaign_name}</b>
            · Client: <b style="color:#1a1917">{client["name"]}</b>
            · {skipped} rows skipped
          </div>
        </div>
        """, unsafe_allow_html=True)

        # ── Email found list ──────────────────────────────────────────────────
        if result["email_found"]:
            st.markdown('<div class="sec-hd">✅ Email Found</div>', unsafe_allow_html=True)
            rows_html = ""
            for l in result["email_found"]:
                tag = '<span style="font-size:10px;color:#3d9e6a">NEW</span>' if not l["was_existing"] else ""
                rows_html += f"""<tr>
                  <td class="n">{l["full_name"]} {tag}</td>
                  <td>{l.get("company","—")}</td>
                  <td>{l.get("email","—")}</td>
                  <td>{l.get("country","—")}</td>
                  <td>{l.get("industry","—")}</td>
                </tr>"""
            st.markdown(f'''<div class="tbl"><table>
              <thead><tr><th>Name</th><th>Company</th><th>Email</th>
              <th>Country</th><th>Industry</th></tr></thead>
              <tbody>{rows_html}</tbody>
            </table></div>''', unsafe_allow_html=True)

            # Export email found
            import pandas as pd
            df_out = pd.DataFrame(result["email_found"])
            st.download_button(
                "⬇ Download Email Found CSV",
                df_out.to_csv(index=False).encode(),
                f"{campaign_name.replace(' ','_')}_email_found.csv",
                "text/csv"
            )

        # ── No email found list ───────────────────────────────────────────────
        if result["no_email_found"]:
            st.markdown('<div class="sec-hd" style="margin-top:24px">❌ No Email Found</div>', unsafe_allow_html=True)
            rows_html = ""
            for l in result["no_email_found"]:
                rows_html += f"""<tr>
                  <td class="n">{l["full_name"]}</td>
                  <td>{l.get("company","—")}</td>
                  <td style="color:#aaa">No email found</td>
                  <td>{l.get("linkedin_url","—")}</td>
                  <td>{l.get("industry","—")}</td>
                </tr>"""
            st.markdown(f'''<div class="tbl"><table>
              <thead><tr><th>Name</th><th>Company</th><th>Email</th>
              <th>LinkedIn</th><th>Industry</th></tr></thead>
              <tbody>{rows_html}</tbody>
            </table></div>''', unsafe_allow_html=True)

            df_out2 = pd.DataFrame(result["no_email_found"])
            st.download_button(
                "⬇ Download No Email CSV",
                df_out2.to_csv(index=False).encode(),
                f"{campaign_name.replace(' ','_')}_no_email.csv",
                "text/csv",
                key="dl_no_email"
            )

# ── MAIN ──────────────────────────────────────────────────────────────────────


# ══════════════════════════════════════════════════════════════════════════════
# CLEAN & FILTER TAB
# ══════════════════════════════════════════════════════════════════════════════

import re as _re

PERSONAL_EMAIL_DOMAINS = {
    "gmail.com","yahoo.com","hotmail.com","outlook.com","aol.com",
    "icloud.com","live.com","msn.com","mail.com","gmx.com",
    "yandex.com","protonmail.com","yahoo.co.uk","googlemail.com",
    "me.com","mac.com","yahoo.fr","yahoo.de","yahoo.co.in",
}

LEGAL_SUFFIXES = [
    r"\s+GmbH$", r"\s+AG$", r"\s+Ltd\.?$", r"\s+Inc\.?$",
    r"\s+LLC$", r"\s+S\.A\.?$", r"\s+B\.V\.?$", r"\s+PLC$",
    r"\s+Limited$", r"\s+Corp\.?$", r"\s+Co\.$", r"\s+S\.r\.l\.?$",
    r"\s+Pty\.?$", r"\s+KG$", r"\s+OHG$", r"\s+GbR$", r"\s+\(ex\)$",
]

HONORIFICS = [
    r"\bDr\.?\b", r"\bMr\.?\b", r"\bMrs\.?\b", r"\bMs\.?\b",
    r"\bProf\.?\b", r"\bEng\.?\b", r"\bSir\b", r"\bMadam\b",
]

# Clutch company size bands (matches what clutch stores)
CLUTCH_SIZE_BANDS = [
    "All sizes",
    "Freelancer (1)",
    "Small (2–9)",
    "Small-Mid (10–49)",
    "Mid (50–249)",
    "Mid-Large (250–999)",
    "Enterprise (1000+)",
]

CLUTCH_BUDGET_BANDS = [
    "Any",
    "$1,000+", "$5,000+", "$10,000+", "$25,000+", "$50,000+",
]

CLUTCH_HOURLY_BANDS = [
    "Any",
    "< $25/hr", "$25–$49/hr", "$50–$99/hr",
    "$100–$149/hr", "$150–$199/hr", "$200+/hr",
]


def _clean_name(name: str) -> str:
    if not name: return name
    for h in HONORIFICS:
        name = _re.sub(h, "", name, flags=_re.IGNORECASE)
    if name.isupper():
        name = name.title()
    return _re.sub(r"\s+", " ", name).strip()


def _clean_company(name: str) -> str:
    if not name: return name
    for s in LEGAL_SUFFIXES:
        name = _re.sub(s, "", name, flags=_re.IGNORECASE)
    if name.isupper() or name.islower():
        name = name.title()
    return _re.sub(r"\s+", " ", name).strip()


def _is_personal_email(email: str) -> bool:
    if not email: return False
    domain = email.strip().lower().split("@")[-1]
    return domain in PERSONAL_EMAIL_DOMAINS


def _translate_title(title: str, api_key: str) -> tuple:
    """Translate a non-English title via DeepL. Returns (translated, lang)."""
    if not title or all(ord(c) < 128 for c in title):
        return title, "en"
    try:
        import requests as _req
        r = _req.post(
            "https://api-free.deepl.com/v2/translate",
            data={"auth_key": api_key, "text": title, "target_lang": "EN"},
            timeout=8
        )
        r.raise_for_status()
        d = r.json()["translations"][0]
        return d["text"], d["detected_source_language"]
    except Exception:
        return title, "unknown"


def render_clean_filter_tab(user: dict):
    """Clean & Filter tab — handles both people leads and Clutch company leads."""
    org_id = (user or {}).get("org_id", 1)

    st.markdown('''
    <div style="font-family:'Playfair Display',serif;font-size:22px;
      font-weight:700;color:#1a1917;margin-bottom:4px">Clean & Filter</div>
    <div style="font-size:13px;color:#999;margin-bottom:24px">
      Clean names, filter by title/seniority, remove personal emails,
      or filter Clutch companies by size, budget and rating.
    </div>
    ''', unsafe_allow_html=True)

    # ── Mode selector ─────────────────────────────────────────────────────────
    mode = st.radio(
        "What are you cleaning?",
        ["👤  People / Event Leads", "🏢  Clutch Company Leads"],
        horizontal=True,
    )

    st.markdown("---")

    if "👤" in mode:
        _render_people_cleaner(org_id)
    else:
        _render_clutch_filter(org_id)


def _render_people_cleaner(org_id: int):
    """Cleaning UI for people/event leads."""

    # Load leads from DB
    conn = get_connection()

    # ── Session picker ────────────────────────────────────────────────────────
    try:
        all_sessions = conn.execute("""
            SELECT id, event_name, event_url, started_at, leads_new
            FROM scrape_sessions
            WHERE (org_id=? OR org_id IS NULL)
            ORDER BY started_at DESC
            LIMIT 50
        """, (org_id,)).fetchall()
    except Exception:
        all_sessions = []

    session_options = {"All leads (entire inventory)": None}
    for s in all_sessions:
        label = (s.get("event_name") or s.get("event_url") or "Unknown")[:50]
        dt    = (s.get("started_at") or "")[:10]
        n     = s.get("leads_new") or "?"
        session_options[f"{label}  ·  {dt}  ·  {n} leads"] = s.get("id")

    selected_session_label = st.selectbox(
        "Filter by scrape session",
        list(session_options.keys()),
        help="Clean leads from a specific scrape, or all at once"
    )
    selected_session_id = session_options[selected_session_label]

    # ── Load leads ────────────────────────────────────────────────────────────
    try:
        if selected_session_id:
            # First try lead_appearances (populated by new scrapes)
            rows = conn.execute("""
                SELECT DISTINCT l.id, l.full_name, l.title, l.status,
                       co.name AS company_name,
                       e.email, e.country, e.industry
                FROM leads l
                LEFT JOIN companies co ON co.id = l.company_id
                LEFT JOIN enrichment e ON e.lead_id = l.id
                INNER JOIN lead_appearances la ON la.lead_id = l.id
                WHERE (l.org_id=? OR l.org_id IS NULL)
                  AND la.session_id = ?
                ORDER BY l.full_name
            """, (org_id, selected_session_id)).fetchall()

            # Fallback: old scrapes have no lead_appearances — match by timestamp window
            if not rows:
                session_row = conn.execute(
                    "SELECT started_at, finished_at FROM scrape_sessions WHERE id=?",
                    (selected_session_id,)
                ).fetchone()
                if session_row:
                    started  = session_row["started_at"] or ""
                    finished = session_row["finished_at"] or ""
                    if started and finished:
                        rows = conn.execute("""
                            SELECT l.id, l.full_name, l.title, l.status,
                                   co.name AS company_name,
                                   e.email, e.country, e.industry
                            FROM leads l
                            LEFT JOIN companies co ON co.id = l.company_id
                            LEFT JOIN enrichment e ON e.lead_id = l.id
                            WHERE (l.org_id=? OR l.org_id IS NULL)
                              AND l.source_type = 'event'
                              AND l.first_seen_at BETWEEN ? AND ?
                            ORDER BY l.full_name
                        """, (org_id, started, finished)).fetchall()
                    elif started:
                        # Session still running or no finish time — show all event leads
                        # scraped on the same day
                        day = started[:10]
                        rows = conn.execute("""
                            SELECT l.id, l.full_name, l.title, l.status,
                                   co.name AS company_name,
                                   e.email, e.country, e.industry
                            FROM leads l
                            LEFT JOIN companies co ON co.id = l.company_id
                            LEFT JOIN enrichment e ON e.lead_id = l.id
                            WHERE (l.org_id=? OR l.org_id IS NULL)
                              AND l.source_type = 'event'
                              AND l.first_seen_at LIKE ?
                            ORDER BY l.full_name
                        """, (org_id, f"{day}%")).fetchall()

                if not rows:
                    st.info("No leads found for this session. "
                            "This session predates the lead-appearance tracker — "
                            "select **All leads** to see the full inventory.")
                    conn.close()
                    return
        else:
            rows = conn.execute("""
                SELECT l.id, l.full_name, l.title, l.status,
                       co.name AS company_name,
                       e.email, e.country, e.industry
                FROM leads l
                LEFT JOIN companies co ON co.id = l.company_id
                LEFT JOIN enrichment e ON e.lead_id = l.id
                WHERE (l.org_id=? OR l.org_id IS NULL)
                  AND (l.source_type = 'event' OR l.source_type IS NULL)
                ORDER BY l.full_name
            """, (org_id,)).fetchall()
    except Exception as e:
        st.error(f"Could not load leads: {e}")
        conn.close()
        return
    finally:
        conn.close()

    if not rows:
        st.info("No leads in inventory yet.")
        return

    import pandas as pd
    df = pd.DataFrame(rows)

    session_note = f" from selected session" if selected_session_id else " from inventory"
    st.markdown(f"**{len(df):,} leads loaded{session_note}**")

    # ── Cleaning options ──────────────────────────────────────────────────────
    st.markdown('<div class="sec-hd" style="margin-top:16px">🧹 Cleaning Options</div>',
                unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)
    with col1:
        do_clean_names    = st.checkbox("Clean names", value=True,
                                         help="Remove Dr/Mr/Prof, fix ALL CAPS")
        do_clean_companies = st.checkbox("Clean company names", value=True,
                                          help="Remove GmbH/Ltd/Inc suffixes, fix casing")
    with col2:
        do_remove_personal = st.checkbox("Remove personal emails", value=True,
                                          help="Remove gmail, yahoo, hotmail etc.")
        do_translate       = st.checkbox("Translate foreign titles (DeepL)",
                                          help="Requires DEEPL_API_KEY in .env")
    with col3:
        deepl_key = ""
        if do_translate:
            deepl_key = st.text_input("DeepL API key",
                                       value=__import__("os").getenv("DEEPL_API_KEY",""),
                                       type="password")

    # ── Title filter ──────────────────────────────────────────────────────────
    st.markdown('<div class="sec-hd" style="margin-top:20px">🎯 Title / Seniority Filter</div>',
                unsafe_allow_html=True)

    # Show most common titles for reference
    if "title" in df.columns:
        top_titles = df["title"].dropna().value_counts().head(12)
        if not top_titles.empty:
            title_chips = "  ".join([
                f'<span style="background:#f8f7f4;border:1px solid #e8e4dd;'
                f'border-radius:20px;padding:3px 10px;font-size:11px;'
                f'color:#888;margin:2px;display:inline-block">{t} ({c})</span>'
                for t, c in top_titles.items()
            ])
            st.markdown(
                f'<div style="margin-bottom:10px;font-size:11px;color:#bbb">'
                f'Top titles in this batch:</div>{title_chips}',
                unsafe_allow_html=True
            )

    tcol1, tcol2 = st.columns(2)
    with tcol1:
        include_raw = st.text_input(
            "Include titles containing (comma separated)",
            placeholder="Director, VP, Head, Chief, CXO, Founder",
            help="Leave blank to include all"
        )
    with tcol2:
        exclude_raw = st.text_input(
            "Exclude titles containing (comma separated)",
            placeholder="Sales, Marketing, Intern, Student, Assistant",
            help="Leave blank to exclude none"
        )

    include_kw = [k.strip() for k in include_raw.split(",") if k.strip()]
    exclude_kw = [k.strip() for k in exclude_raw.split(",") if k.strip()]

    # ── Preview & Apply ───────────────────────────────────────────────────────
    if st.button("▶ Preview Results", type="secondary"):
        st.session_state["cleaner_preview"] = True

    if st.session_state.get("cleaner_preview"):
        result_df = df.copy()

        # Clean names
        if do_clean_names and "full_name" in result_df.columns:
            result_df["full_name"] = result_df["full_name"].apply(
                lambda x: _clean_name(str(x)) if x else x
            )

        # Clean companies
        if do_clean_companies and "company_name" in result_df.columns:
            result_df["company_name"] = result_df["company_name"].apply(
                lambda x: _clean_company(str(x)) if x else x
            )

        # Remove personal emails
        removed_email = 0
        if do_remove_personal and "email" in result_df.columns:
            mask = result_df["email"].apply(
                lambda x: not _is_personal_email(str(x)) if x else True
            )
            removed_email = (~mask).sum()
            result_df = result_df[mask]

        # Title filter
        removed_title = 0
        if include_kw or exclude_kw:
            def title_passes(title):
                if not title or str(title).lower() in ("nan","n/a",""): return not include_kw
                t = str(title).lower()
                if include_kw and not any(k.lower() in t for k in include_kw): return False
                if exclude_kw and any(k.lower() in t for k in exclude_kw): return False
                return True
            mask2 = result_df["title"].apply(title_passes)
            removed_title = (~mask2).sum()
            result_df = result_df[mask2]

        # Translate titles
        if do_translate and deepl_key and "title" in result_df.columns:
            with st.spinner(f"Translating {len(result_df)} titles via DeepL..."):
                translations = []
                langs = []
                for t in result_df["title"]:
                    tr, lg = _translate_title(str(t) if t else "", deepl_key)
                    translations.append(tr)
                    langs.append(lg)
                result_df["title"] = translations
                result_df["title_language"] = langs

        # Summary
        kept = len(result_df)
        total = len(df)
        removed = total - kept

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Original", f"{total:,}")
        c2.metric("After filters", f"{kept:,}")
        c3.metric("Removed", f"{removed:,}", delta=f"-{removed}", delta_color="inverse")
        c4.metric("Personal emails removed", f"{removed_email:,}")

        if removed_title:
            st.caption(f"  {removed_title:,} leads removed by title filter")

        # Preview table
        show_cols = ["full_name","title","company_name","email","country"]
        show_cols = [c for c in show_cols if c in result_df.columns]
        st.dataframe(result_df[show_cols].head(50), use_container_width=True)

        # Store result for apply
        st.session_state["cleaner_result_df"] = result_df
        st.session_state["cleaner_result_ids"] = result_df["id"].tolist()

        # Export button
        import pandas as pd2
        csv_bytes = result_df[show_cols].to_csv(index=False).encode()
        st.download_button(
            f"⬇ Download cleaned CSV ({kept:,} leads)",
            csv_bytes,
            "cleaned_leads.csv",
            "text/csv"
        )

        # Apply to DB
        st.markdown("---")
        st.markdown("**Apply cleaning to inventory database**")
        st.caption("This will update names and company names in your live inventory.")

        if st.button("✅ Apply cleaning to DB", type="primary"):
            conn2 = get_connection()
            updated = 0
            try:
                for _, row in result_df.iterrows():
                    conn2.execute(
                        "UPDATE leads SET full_name=?, title=? WHERE id=?",
                        (row.get("full_name"), row.get("title"), row["id"])
                    )
                    if row.get("company_name"):
                        conn2.execute(
                            """UPDATE companies SET name=? WHERE id=(
                               SELECT company_id FROM leads WHERE id=?)""",
                            (row["company_name"], row["id"])
                        )
                    updated += 1
                conn2.commit()
                st.success(f"✅ {updated:,} leads updated in inventory.")
                st.session_state["cleaner_preview"] = False
            except Exception as e:
                st.error(f"Update failed: {e}")
            finally:
                conn2.close()


def _render_clutch_filter(org_id: int):
    """Filtering UI for Clutch company leads."""

    conn = get_connection()

    # Show info about Clutch scrape sessions — broad query catches
    # sessions created by either worker.py or scraper_dashboard
    try:
        clutch_sessions = conn.execute("""
            SELECT id, event_name, event_url, started_at, leads_new, leads_found
            FROM scrape_sessions
            WHERE (org_id=? OR org_id IS NULL)
              AND (event_url LIKE '%clutch%'
                   OR event_name LIKE '%clutch%'
                   OR LOWER(event_url) LIKE '%clutch%')
            ORDER BY started_at DESC
        """, (org_id,)).fetchall()
    except Exception:
        clutch_sessions = []

    if clutch_sessions:
        session_labels = []
        for s in clutch_sessions:
            url   = s.get("event_url") or ""
            label = s.get("event_name") or url.replace("https://clutch.co","").strip("/") or "Clutch scrape"
            dt    = (s.get("started_at") or "")[:10]
            n     = s.get("leads_new") or s.get("leads_found") or "?"
            session_labels.append(f"{label}  ·  {dt}  ·  {n} companies")
        st.selectbox("Clutch scrape sessions (info only)", session_labels,
                     help="All Clutch leads are shown below regardless of session")

    # ── Load leads — use source_type='clutch' (the permanent fix) ───────────
    try:
        # Primary: source_type column (set by clutch_scraper on all new scrapes)
        rows = conn.execute("""
            SELECT DISTINCT l.id, l.full_name AS company_name, l.title,
                   l.status, e.country, e.industry, e.notes,
                   co.name AS co_name
            FROM leads l
            LEFT JOIN companies co ON co.id = l.company_id
            LEFT JOIN enrichment e ON e.lead_id = l.id
            WHERE (l.org_id=? OR l.org_id IS NULL)
              AND l.source_type = 'clutch'
            ORDER BY l.full_name
        """, (org_id,)).fetchall()

        # Legacy fallback: leads before source_type existed —
        # tagged by enrichment industry field set by clutch_scraper
        if not rows:
            rows = conn.execute("""
                SELECT DISTINCT l.id, l.full_name AS company_name, l.title,
                       l.status, e.country, e.industry, e.notes,
                       co.name AS co_name
                FROM leads l
                LEFT JOIN companies co ON co.id = l.company_id
                LEFT JOIN enrichment e ON e.lead_id = l.id
                WHERE (l.org_id=? OR l.org_id IS NULL)
                  AND (e.industry = 'Agency / Services'
                       OR (e.notes IS NOT NULL AND e.notes LIKE '%"rating"%'))
                ORDER BY l.full_name
            """, (org_id,)).fetchall()
            if rows:
                st.caption("ℹ️ Showing legacy Clutch leads. Run `python migrate.py` to tag them permanently.")

    except Exception as e:
        st.error(f"Could not load leads: {e}")
        conn.close()
        return
    finally:
        conn.close()

    if not rows:
        st.info("No Clutch leads found. Run the Clutch scraper from the Smart Scraper tab first.")
        return

    import pandas as pd, json as _json

    # Parse the notes JSON field (contains rating, budget, etc from Clutch scraper)
    records = []
    for r in rows:
        rec = dict(r)
        notes_raw = rec.get("notes") or "{}"
        try:
            meta = _json.loads(notes_raw)
        except Exception:
            meta = {}
        rec["rating"]      = meta.get("rating","")
        rec["reviews"]     = meta.get("reviews","")
        rec["min_budget"]  = meta.get("min_budget","")
        rec["hourly_rate"] = meta.get("hourly_rate","")
        rec["team_size"]   = meta.get("team_size","")
        rec["clutch_url"]  = meta.get("clutch_url","")
        rec["website"]     = meta.get("website","")
        records.append(rec)

    df = pd.DataFrame(records)
    st.markdown(f"**{len(df):,} Clutch company leads loaded**")

    st.markdown('<div class="sec-hd" style="margin-top:16px">🔍 Filter Options</div>',
                unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)

    with col1:
        # Company size — multiselect
        size_options = ["1","2–9","10–49","50–249","250–999","1000+"]
        size_sel = st.multiselect(
            "Company size (select one or more)",
            size_options,
            help="Leave blank for any size"
        )

        # Rating
        min_rating = st.slider("Minimum rating ★", 0.0, 5.0, 0.0, 0.1)

    with col2:
        # Min budget
        budget_options = ["Any","$1,000+","$5,000+","$10,000+","$25,000+","$50,000+"]
        budget_sel = st.selectbox("Min project budget", budget_options)

        # Hourly rate
        hourly_options = ["Any","<$25","$25–$49","$50–$99","$100–$149","$150–$199","$200+"]
        hourly_sel = st.selectbox("Hourly rate", hourly_options)

    with col3:
        # Services keyword
        services_include = st.text_input(
            "Services must include",
            placeholder="Web Design, SEO, Branding",
            help="Comma separated — company must offer at least one"
        )
        services_exclude = st.text_input(
            "Services to exclude",
            placeholder="Advertising, PR",
        )

        # Location filter
        location_filter = st.text_input(
            "Location contains",
            placeholder="Berlin, UK, Germany"
        )

    # Min reviews
    min_reviews = st.number_input("Minimum number of reviews", 0, 1000, 0, step=5)

    if st.button("▶ Apply Filters", type="secondary"):

        result = df.copy()

        # Rating filter
        if min_rating > 0:
            def parse_rating(r):
                try: return float(str(r).strip())
                except: return 0.0
            result = result[result["rating"].apply(parse_rating) >= min_rating]

        # Reviews filter
        if min_reviews > 0:
            def parse_reviews(r):
                try: return int(str(r).strip())
                except: return 0
            result = result[result["reviews"].apply(parse_reviews) >= min_reviews]

        # Company size filter — multiselect (size_sel is now a list)
        if size_sel:
            pattern = "|".join([s.replace("+", "\\+").replace("–", "[-–]") for s in size_sel])
            result = result[result["team_size"].str.contains(
                pattern, case=False, na=False, regex=True
            )]

        # Budget filter — extract number and compare
        if budget_sel != "Any":
            budget_num = int(budget_sel.replace("$","").replace(",","").replace("+",""))
            def parse_budget(b):
                if not b: return 0
                nums = _re.findall(r"\d+", str(b).replace(",",""))
                return int(nums[0]) * 1000 if nums else 0
            result = result[result["min_budget"].apply(parse_budget) >= budget_num]

        # Hourly filter
        if hourly_sel != "Any":
            hourly_map = {
                "<$25": (0, 25), "$25–$49": (25, 49),
                "$50–$99": (50, 99), "$100–$149": (100, 149),
                "$150–$199": (150, 199), "$200+": (200, 9999),
            }
            lo, hi = hourly_map.get(hourly_sel, (0, 9999))
            def in_hourly_range(h):
                if not h: return True  # keep if unknown
                nums = _re.findall(r"\d+", str(h))
                if not nums: return True
                mid = int(nums[0])
                return lo <= mid <= hi
            result = result[result["hourly_rate"].apply(in_hourly_range)]

        # Services filter
        svc_inc = [s.strip().lower() for s in services_include.split(",") if s.strip()]
        svc_exc = [s.strip().lower() for s in services_exclude.split(",") if s.strip()]
        if svc_inc:
            result = result[result["title"].str.lower().apply(
                lambda t: any(s in str(t) for s in svc_inc)
            )]
        if svc_exc:
            result = result[~result["title"].str.lower().apply(
                lambda t: any(s in str(t) for s in svc_exc)
            )]

        # Location filter
        if location_filter.strip():
            result = result[result["country"].str.contains(
                location_filter.strip(), case=False, na=False
            )]

        # Results
        kept = len(result)
        total = len(df)
        removed = total - kept

        c1, c2, c3 = st.columns(3)
        c1.metric("Total companies", f"{total:,}")
        c2.metric("Matching filters", f"{kept:,}")
        c3.metric("Filtered out", f"{removed:,}", delta=f"-{removed}", delta_color="inverse")

        if kept == 0:
            st.warning("No companies match these filters. Try loosening the criteria.")
            return

        # Show results
        show_cols = ["company_name","rating","reviews","team_size",
                     "min_budget","hourly_rate","country","title","clutch_url"]
        show_cols = [c for c in show_cols if c in result.columns]
        result_display = result[show_cols].rename(columns={
            "company_name":"Company","rating":"Rating","reviews":"Reviews",
            "team_size":"Size","min_budget":"Min Budget",
            "hourly_rate":"Hourly","country":"Location",
            "title":"Services","clutch_url":"Clutch URL"
        })
        st.dataframe(result_display, use_container_width=True)

        # Export
        csv_bytes = result[show_cols].to_csv(index=False).encode()
        st.download_button(
            f"⬇ Download filtered list ({kept:,} companies)",
            csv_bytes,
            "clutch_filtered.csv",
            "text/csv"
        )

        # Archive to list
        st.markdown("---")
        list_name = st.text_input("Save as archived list", placeholder="Clutch Berlin Web Design 4.5+")
        if list_name.strip() and st.button("📂 Archive this selection", type="primary"):
            conn3 = get_connection()
            try:
                now = __import__("datetime").datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                cur = conn3.execute(
                    "INSERT INTO archived_lists (org_id, name, created_at) VALUES (?,?,?)",
                    (org_id, list_name.strip(), now)
                )
                list_id = cur.lastrowid
                for lead_id in result["id"].tolist():
                    conn3.execute(
                        "UPDATE leads SET archived_list_id=?, status='archived' WHERE id=?",
                        (list_id, lead_id)
                    )
                conn3.commit()
                st.success(f"✅ {kept:,} companies archived to '{list_name}'")
            except Exception as e:
                st.error(f"Archive failed: {e}")
            finally:
                conn3.close()



def render(user):
    init_db()
    from core.styles import inject_shared_css
    inject_shared_css()
    st.markdown(CSS, unsafe_allow_html=True)

    # Archive lead flow (triggered from detail panel)
    if "archive_lead_id" in st.session_state:
        aid = st.session_state.pop("archive_lead_id")
        st.markdown('<div style="font-family:\'Playfair Display\',serif;font-size:20px;font-weight:700;color:#1a1917;margin-bottom:16px">Archive Lead</div>', unsafe_allow_html=True)
        lists = get_archived_lists()
        if lists:
            sel_list = st.selectbox("Choose archived list", lists,
                format_func=lambda x: f"{x['name']} ({x.get('industry') or 'No industry'})")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("✓ Archive", type="primary", use_container_width=True):
                    archive_leads([aid], sel_list['id'])
                    st.success(f"Lead archived to '{sel_list['name']}'")
                    st.rerun()
            with c2:
                if st.button("Cancel", type="secondary", use_container_width=True):
                    st.rerun()
        else:
            st.warning("No archived lists exist yet. Create one in the 'Archived Lists' tab first.")
            if st.button("Cancel"):
                st.rerun()
        return

    st.markdown('<div class="page-title">Inventory</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">Your global lead database — browse, filter, archive, and check client conflicts.</div>', unsafe_allow_html=True)

    # Stats — use access control layer for org visibility
    try:
        s = get_stats(user=user)
    except Exception:
        s = {k:0 for k in ["total","new","enriched","used","archived","reusable"]}

    st.markdown(f"""
    <div class="stat-row">
      <div class="stat-card">
        <div class="stat-val">{s.get('total',0):,}</div>
        <div class="stat-label">Total</div>
        <div class="stat-note note-grey">All leads</div>
      </div>
      <div class="stat-card">
        <div class="stat-val">{s.get('new',0):,}</div>
        <div class="stat-label">New</div>
        <div class="stat-note note-blue">Unresearched</div>
      </div>
      <div class="stat-card">
        <div class="stat-val">{s.get('assigned',0)+s.get('in_progress',0):,}</div>
        <div class="stat-label">In Progress</div>
        <div class="stat-note note-gold">Being researched</div>
      </div>
      <div class="stat-card">
        <div class="stat-val">{s.get('enriched',0):,}</div>
        <div class="stat-label">Enriched</div>
        <div class="stat-note note-green">Ready to use</div>
      </div>
      <div class="stat-card">
        <div class="stat-val">{s.get('used',0):,}</div>
        <div class="stat-label">Used</div>
        <div class="stat-note note-grey">Across clients</div>
      </div>
      <div class="stat-card">
        <div class="stat-val">{s.get('archived',0):,}</div>
        <div class="stat-label">Archived</div>
        <div class="stat-note note-gold">In named lists</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # Tabs
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📋  All Leads",
        "📂  Archived Lists",
        "⚡  Conflict Checker",
        "⬆  Upload Enriched CSV",
        "🧹  Clean & Filter",
    ])

    with tab1:
        render_leads_table(user)

    with tab2:
        render_archived_lists(user)

    with tab3:
        render_conflict_checker()

    with tab4:
        render_upload_tab(user)

    with tab5:
        render_clean_filter_tab(user)
