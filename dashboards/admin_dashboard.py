"""
dashboards/admin_dashboard.py — Dashin Research Platform
Org Admin workspace.
- User management (create, deactivate, change role)
- Client management (create, edit)
- Generate invite links for client portal access
- View pending invites
- Org settings
"""

import html
import streamlit as st
import hashlib
from datetime import datetime, timezone, date
from core.db import get_connection, ROLES_BY_ORG_TYPE
from core.auth import ROLE_LEVELS, INTERNAL_ROLES
from services.invite_service import (
    create_invite, get_pending_invites, revoke_token,
)
from services.access_control import can_create_user

def _rows(cursor_result):
    """Convert list of sqlite3.Row to list of dicts."""
    return [dict(r) for r in cursor_result]

def _row(cursor_result):
    """Convert sqlite3.Row to dict, or return {} if None."""
    return dict(cursor_result) if cursor_result else {}


STYLES = """
<style>
/* Admin-specific — shared components in core/styles.py */
</style>
"""

INTERNAL_ROLE_OPTIONS = [
    "researcher", "campaign_manager", "research_manager",
    "manager", "org_admin",
]
CLIENT_ROLE_OPTIONS = ["client_user", "client_admin"]

ROLE_DISPLAY = {
    'org_admin':         'Admin',
    'manager':           'Manager',
    'research_manager':  'Research Manager',
    'researcher':        'Researcher',
    'campaign_manager':  'Campaign Manager',
    'client_user':       'Client Viewer',
    'client_admin':      'Client Admin',
    'super_admin':       'Super Admin (Dashin)',
}


def render(user: dict):
    from core.styles import inject_shared_css
    inject_shared_css()
    st.markdown(STYLES, unsafe_allow_html=True)

    org_id  = user["org_id"]
    user_id = user["id"]

    # ── Org stats ─────────────────────────────────────────────────────
    conn  = get_connection()
    org   = conn.execute(
        "SELECT * FROM organisations WHERE id=?", (org_id,)
    ).fetchone()
    users = conn.execute(
        "SELECT COUNT(*) AS c FROM users WHERE org_id=? AND is_active=1",
        (org_id,)
    ).fetchone()["c"]
    clients = conn.execute(
        "SELECT COUNT(*) AS c FROM clients WHERE org_id=? AND is_active=1",
        (org_id,)
    ).fetchone()["c"]
    leads = conn.execute(
        "SELECT COUNT(*) AS c FROM leads WHERE org_id=?", (org_id,)
    ).fetchone()["c"]
    conn.close()

    tier_label = (org["tier"] or "starter").title()

    st.markdown(f"""
    <div class="admin-header">
        <div>
            <div class="admin-title">{org['name']}</div>
            <div class="admin-sub">Admin Panel · {tier_label} Plan</div>
        </div>
        <div>
            <span class="stat-chip">👥 {users} users</span>
            <span class="stat-chip">🏢 {clients} clients</span>
            <span class="stat-chip">🧑 {leads:,} leads</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    tab1, tab2, tab_prev, tab3, tab4, tab5, tab6 = st.tabs([
        "👥 Users",
        "🏢 Clients",
        "👁 Preview Client",
        "🏛 Client Orgs",
        "🔗 Invite Links",
        "⚙️ Org Settings",
        "🧠 Site Library",
    ])

    with tab1:
        _render_users(org_id, user_id)
    with tab2:
        _render_clients(org_id, user_id)
    with tab_prev:
        _render_client_preview(org_id, user)
    with tab3:
        _render_client_orgs(org_id, user_id, user)
    with tab4:
        _render_invites(org_id, user_id)
    with tab5:
        _render_org_settings(org_id, org)
    with tab6:
        from dashboards.site_library_dashboard import render as render_site_lib
        # Admins can re-learn but not delete or mark stable (super_admin only)
        render_site_lib(user, allow_delete=False, allow_mark_stable=False)


# ── PREVIEW CLIENT DASHBOARD ──────────────────────────────────────────────────

def _render_client_preview(org_id: int, admin_user: dict):
    """
    Let an admin see a client's portal exactly as that client sees it. We render
    the real client_dashboard with a synthetic 'user' scoped to the chosen client
    (using one of the client's portal users when available, so notifications and
    lead visibility match). It's a live view of their read-only portal.
    """
    st.markdown("#### 👁 Preview a client's portal")
    st.caption("See exactly what a client sees when they log in — their report, "
               "email accounts, templates, leads and files.")

    conn = get_connection()
    clients = conn.execute(
        "SELECT id, name FROM clients WHERE org_id=? AND is_active=1 ORDER BY name",
        (org_id,)).fetchall()

    if not clients:
        conn.close()
        st.info("No active clients to preview yet.")
        return

    names = {c["name"]: c["id"] for c in clients}
    picked = st.selectbox("Client", list(names.keys()), key="preview_client_pick")
    client_id = names[picked]

    # Prefer a real portal user of this client so the view matches theirs.
    portal_user = conn.execute(
        """SELECT id, name, email, role FROM users
           WHERE client_id=? AND org_id=? AND is_active=1
           ORDER BY (role='client_admin') DESC, id LIMIT 1""",
        (client_id, org_id)).fetchone()
    conn.close()

    preview_user = {
        "id":        portal_user["id"] if portal_user else admin_user.get("id"),
        "name":      portal_user["name"] if portal_user else "Preview",
        "email":     portal_user["email"] if portal_user else "",
        "role":      portal_user["role"] if portal_user else "client_admin",
        "org_id":    org_id,
        "client_id": client_id,
    }

    who = (f"as portal user **{preview_user['email']}**" if portal_user
           else "with no portal user yet (using a stand-in — notifications/leads "
                "may differ from the real client's view)")
    st.warning(f"🔍 **Preview mode** — viewing **{picked}**'s portal {who}. "
               "This is read-only for you; nothing here is sent to the client.")

    st.markdown("---")
    try:
        from dashboards.client_dashboard import render as render_client
        render_client(preview_user)
    except Exception as e:
        st.error(f"Could not render client portal preview: {e}")
        st.exception(e)


# ── USERS ─────────────────────────────────────────────────────────────────────

def _render_users(org_id: int, user_id: int):
    conn  = get_connection()
    users = conn.execute("""
        SELECT u.*, c.name AS client_name
        FROM users u
        LEFT JOIN clients c ON c.id = u.client_id
        WHERE u.org_id=?
        ORDER BY u.role, u.name
    """, (org_id,)).fetchall()
    conn.close()

    st.caption(f"{len(users)} user(s)")

    # Filter
    role_filter = st.selectbox(
        "Filter by role",
        ["All"] + INTERNAL_ROLE_OPTIONS + CLIENT_ROLE_OPTIONS,
        label_visibility="collapsed"
    )
    filtered = [u for u in users
                if role_filter == "All" or u["role"] == role_filter]

    for u in filtered:
        active_badge = "🟢" if u["is_active"] else "🔴"
        client_str   = f" → {u['client_name']}" if u.get("client_name") else ""

        col1, col2 = st.columns([3, 1])
        with col1:
            st.markdown(f"""
            <div class="user-row">
                <div>
                    <div class="user-name">
                        {active_badge} {html.escape(u['name'])}
                    </div>
                    <div class="user-email">
                        {html.escape(u['email'])}{html.escape(client_str)}
                    </div>
                </div>
                <span class="role-chip role-{html.escape(u['role'])}">{html.escape(u['role'].replace('_',' '))}</span>
            </div>
            """, unsafe_allow_html=True)

        with col2:
            if u["id"] != user_id:
                with st.expander("Edit", expanded=False):
                    _render_edit_user(u, org_id)

    st.markdown("---")
    st.subheader("Add User")
    _render_add_user(org_id)


def _render_edit_user(u: dict, org_id: int):
    current_user = st.session_state.get('user', {})
    creator_org_type = current_user.get('org_type', 'agency')
    conn = get_connection()
    target_org = conn.execute(
        "SELECT org_type FROM organisations WHERE id=?", (org_id,)
    ).fetchone()
    conn.close()
    edit_roles = ROLES_BY_ORG_TYPE.get(
        target_org['org_type'] if target_org else creator_org_type,
        INTERNAL_ROLE_OPTIONS
    )
    # Non-dashin creators cannot assign org_admin
    if creator_org_type not in ('dashin',):
        edit_roles = [r for r in edit_roles if r != 'org_admin']

    with st.form(f"edit_user_{u['id']}"):
        new_role = st.selectbox(
            "Role",
            edit_roles,
            index=edit_roles.index(u["role"]) if u["role"] in edit_roles else 0,
            format_func=lambda x: ROLE_DISPLAY.get(x, x.replace('_', ' ').title()),
            key=f"er_{u['id']}"
        )
        hourly = st.number_input(
            "Hourly rate (£)",
            value=float(u["hourly_rate"] or 0),
            min_value=0.0, step=0.5,
            key=f"eh_{u['id']}"
        )
        active = st.checkbox("Active", value=bool(u["is_active"]),
                             key=f"ea_{u['id']}")

        if st.form_submit_button("Save"):
            conn = get_connection()
            conn.execute("""
                UPDATE users
                SET role=?, hourly_rate=?, is_active=?
                WHERE id=? AND org_id=?
            """, (new_role, hourly, int(active), u["id"], org_id))
            conn.commit()
            conn.close()
            st.success("Saved!")
            st.rerun()


def _render_add_user(org_id: int):
    current_user = st.session_state.get('user', {})
    creator_org_type = current_user.get('org_type', 'agency')

    # Determine which roles are valid for this org type
    available_roles = ROLES_BY_ORG_TYPE.get(creator_org_type, INTERNAL_ROLE_OPTIONS)

    # super_admin / dashin admins adding to this org — use the target org's type
    conn = get_connection()
    target_org = conn.execute(
        "SELECT org_type FROM organisations WHERE id=?", (org_id,)
    ).fetchone()
    conn.close()
    if target_org:
        available_roles = ROLES_BY_ORG_TYPE.get(target_org['org_type'], INTERNAL_ROLE_OPTIONS)

    # Never expose org_admin to non-dashin creators (they must contact Dashin)
    if creator_org_type not in ('dashin',) and 'org_admin' in available_roles:
        available_roles = [r for r in available_roles if r != 'org_admin']

    role_display = [ROLE_DISPLAY.get(r, r.replace('_', ' ').title()) for r in available_roles]

    conn = get_connection()
    clients = conn.execute(
        "SELECT id, name FROM clients WHERE org_id=? AND is_active=1",
        (org_id,)
    ).fetchall()
    conn.close()

    with st.form("add_user_form"):
        col1, col2 = st.columns(2)
        with col1:
            name     = st.text_input("Full name *")
            email    = st.text_input("Email *")
        with col2:
            password = st.text_input("Password *", type="password",
                                     help="Min 8 characters")
            selected_display = st.selectbox("Role", role_display)
            role = available_roles[role_display.index(selected_display)]

        hourly = st.number_input("Hourly rate (£)", min_value=0.0,
                                  value=0.0, step=0.5)

        if st.form_submit_button("Create User", use_container_width=True):
            if not name or not email or not password:
                st.error("Name, email and password are required.")
            elif len(password) < 8:
                st.error("Password must be at least 8 characters.")
            else:
                # Validate against access control
                allowed, reason = can_create_user(current_user, role, org_id)
                if not allowed:
                    st.error(f"Permission denied: {reason}")
                else:
                    try:
                        from core.auth import hash_password
                        pw   = hash_password(password)
                        conn = get_connection()
                        conn.execute("""
                            INSERT INTO users
                                (org_id, name, email, password, role,
                                 hourly_rate, is_active, created_at)
                            VALUES (?,?,?,?,?,?,1,?)
                        """, (org_id, name, email.lower().strip(),
                              pw, role, hourly,
                              datetime.now(timezone.utc).replace(tzinfo=None).isoformat()))
                        conn.commit()
                        conn.close()
                        st.success(f"User {name} created as {ROLE_DISPLAY.get(role, role)}!")
                        st.rerun()
                    except Exception as e:
                        if "UNIQUE" in str(e):
                            st.error("That email is already registered.")
                        else:
                            st.error(f"Error: {e}")


# ── CLIENTS ───────────────────────────────────────────────────────────────────

def _render_clients(org_id: int, user_id: int):
    conn    = get_connection()
    clients = conn.execute("""
        SELECT c.*,
               (SELECT COUNT(*) FROM campaigns
                WHERE client_id=c.id)                    AS campaign_count,
               (SELECT COUNT(*) FROM users
                WHERE client_id=c.id AND is_active=1)    AS portal_users
        FROM clients c
        WHERE c.org_id=?
        ORDER BY c.is_active DESC, c.name
    """, (org_id,)).fetchall()
    conn.close()

    for c in clients:
        active = "🟢" if c["is_active"] else "🔴"
        st.markdown(f"""
        <div class="client-card">
            <div style="display:flex;justify-content:space-between;">
                <div>
                    <div class="client-name">{active} {c['name']}</div>
                    <div class="client-meta">
                        {c.get('industry','') or 'No industry set'} ·
                        {c['campaign_count']} campaigns ·
                        {c['portal_users']} portal users
                    </div>
                    {f'<div style="font-size:12px;color:var(--text-3);margin-top:4px;">{c["icp_notes"][:80]}…</div>' if c.get('icp_notes') else ''}
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        with st.expander("Edit client"):
            _render_edit_client(c, org_id)

        with st.expander("📧 Email accounts (client sees these read-only)"):
            _render_client_email_accounts(c, org_id, user_id)

    st.markdown("---")
    st.subheader("Add Client")
    _render_add_client(org_id)


def _render_client_email_accounts(c: dict, org_id: int, user_id: int):
    """Agency-side manager for a client's mailbox credentials."""
    from services.client_portal_service import (
        list_email_accounts, add_email_account, delete_email_account)

    accounts = list_email_accounts(org_id, c["id"])
    for a in accounts:
        row = st.columns([4, 1])
        with row[0]:
            st.markdown(f"**{a.get('label') or a['email_address']}** — "
                        f"`{a['email_address']}` / `{a.get('password') or '(no password)'}`"
                        + (f" · {a['provider']}" if a.get("provider") else ""))
        with row[1]:
            if st.button("Delete", key=f"del_email_{a['id']}"):
                delete_email_account(a["id"], org_id)
                st.rerun()
    if not accounts:
        st.caption("No mailboxes yet.")

    with st.form(f"add_email_{c['id']}"):
        st.caption("Add a mailbox")
        addr  = st.text_input("Email address", key=f"em_addr_{c['id']}")
        pw    = st.text_input("Password", key=f"em_pw_{c['id']}")
        label = st.text_input("Label (optional)", key=f"em_lbl_{c['id']}")
        cc = st.columns(2)
        with cc[0]:
            provider = st.text_input("Provider (optional)", key=f"em_prov_{c['id']}")
        with cc[1]:
            webmail = st.text_input("Webmail URL (optional)", key=f"em_web_{c['id']}")
        if st.form_submit_button("Add mailbox") and addr.strip():
            add_email_account(org_id, c["id"], addr, pw, label, provider, webmail, user_id)
            st.success("Mailbox added.")
            st.rerun()


def _render_edit_client(c: dict, org_id: int):
    with st.form(f"edit_client_{c['id']}"):
        name     = st.text_input("Name", value=c["name"])
        industry = st.text_input("Industry",
                                  value=c.get("industry") or "")
        website  = st.text_input("Website",
                                  value=c.get("website") or "")
        icp      = st.text_area("ICP Notes",
                                 value=c.get("icp_notes") or "",
                                 height=80)
        active   = st.checkbox("Active", value=bool(c["is_active"]))

        if st.form_submit_button("Save Changes"):
            conn = get_connection()
            conn.execute("""
                UPDATE clients
                SET name=?, industry=?, website=?,
                    icp_notes=?, is_active=?
                WHERE id=? AND org_id=?
            """, (name, industry, website, icp,
                  int(active), c["id"], org_id))
            conn.commit()
            conn.close()
            st.success("Client updated!")
            st.rerun()


def _render_add_client(org_id: int):
    with st.form("add_client_form"):
        col1, col2 = st.columns(2)
        with col1:
            name     = st.text_input("Client name *")
            industry = st.text_input("Industry")
        with col2:
            website  = st.text_input("Website")

        icp = st.text_area("ICP / Target Audience Notes",
                            height=80,
                            placeholder="e.g. B2B SaaS companies 50-500 employees, "
                                        "UK/US, looking for VP Sales / Head of Revenue")

        if st.form_submit_button("Add Client", use_container_width=True):
            if not name.strip():
                st.error("Client name is required.")
            else:
                conn = get_connection()
                conn.execute("""
                    INSERT INTO clients
                        (org_id, name, industry, website,
                         icp_notes, is_active, created_at)
                    VALUES (?,?,?,?,?,1,?)
                """, (org_id, name, industry, website, icp,
                      datetime.now(timezone.utc).replace(tzinfo=None).isoformat()))
                conn.commit()
                conn.close()
                st.success(f"Client '{name}' added!")
                st.rerun()


# ── INVITE LINKS ──────────────────────────────────────────────────────────────

def _render_invites(org_id: int, user_id: int):
    # Pending invites
    pending = get_pending_invites(org_id)

    if pending:
        st.subheader(f"Active Invite Links ({len(pending)})")
        for inv in pending:
            import os
            base = os.getenv("DASHIN_BASE_URL", "http://localhost:8501")
            url  = f"{base}/?invite={inv['token']}"

            st.markdown(f"""
            <div class="invite-card">
                <div style="font-weight:600;margin-bottom:6px;">
                    {inv.get('client_name','Unknown Client')} —
                    {inv.get('role','client_user').replace('_',' ').title()}
                </div>
                <div class="invite-token">{url}</div>
                <div style="font-size:11px;color:var(--text-3);margin-top:6px;">
                    Created by {inv.get('created_by_name','?')} ·
                    Expires {(inv.get('expires_at',''))[:10]}
                    {f'· Pre-filled: {inv["email"]}' if inv.get('email') else ''}
                </div>
            </div>
            """, unsafe_allow_html=True)

            col1, col2 = st.columns([1, 4])
            with col1:
                if st.button("Revoke", key=f"rev_{inv['id']}",
                             type="secondary"):
                    revoke_token(inv["id"], org_id)
                    st.rerun()
            with col2:
                st.code(url, language=None)

        st.markdown("---")

    # Generate new invite
    st.subheader("Generate New Invite Link")

    conn    = get_connection()
    clients = conn.execute(
        "SELECT id, name FROM clients WHERE org_id=? AND is_active=1 ORDER BY name",
        (org_id,)
    ).fetchall()
    conn.close()

    if not clients:
        st.info("Add a client first before generating invite links.")
        return

    with st.form("gen_invite_form"):
        client_map = {c["name"]: c["id"] for c in clients}
        client_sel = st.selectbox("Client", list(client_map.keys()))
        role       = st.selectbox(
            "Role",
            CLIENT_ROLE_OPTIONS,
            format_func=lambda x: x.replace("_", " ").title(),
            help="Client Admin can manage team members within their account."
        )
        email_hint = st.text_input(
            "Pre-fill email (optional)",
            placeholder="jane@company.com"
        )
        expiry_days = st.slider("Link valid for (days)", 1, 30, 7)

        if st.form_submit_button("Generate Link",
                                  use_container_width=True):
            result = create_invite(
                org_id      = org_id,
                client_id   = client_map[client_sel],
                created_by  = user_id,
                role        = role,
                email       = email_hint.strip() or None,
                expiry_days = expiry_days,
            )
            st.success("Invite link generated!")
            st.code(result["invite_url"], language=None)
            st.caption(f"Expires: {result['expires_at'][:10]}")
            st.info("Share this link with your client. "
                    "It works once and expires in "
                    f"{expiry_days} days.")
            st.rerun()


# ── CLIENT ORGS ───────────────────────────────────────────────────────────────

def _render_client_orgs(org_id: int, user_id: int, user: dict):
    """
    Manage client organisations that are children of this agency.
    Creates org_type='client' entries linked via parent_org_id.
    """
    st.markdown("### Client Organisations")
    st.caption(
        "Client orgs are separate portal accounts for your clients. "
        "They see only leads released to them."
    )

    conn = get_connection()
    child_orgs = conn.execute("""
        SELECT o.*,
               (SELECT COUNT(*) FROM leads WHERE org_id=o.id) AS lead_count,
               (SELECT COUNT(*) FROM campaigns ca
                JOIN clients cl ON cl.id=ca.client_id
                WHERE cl.org_id=o.id AND ca.status NOT IN ('cancelled','closed')) AS active_campaigns,
               (SELECT COUNT(*) FROM users WHERE org_id=o.id AND is_active=1) AS user_count
        FROM organisations o
        WHERE o.parent_org_id=? AND o.org_type='client'
        ORDER BY o.created_at DESC
    """, (org_id,)).fetchall()
    conn.close()

    # Show existing client orgs
    if child_orgs:
        st.markdown(f"**{len(child_orgs)} client organisation(s)**")
        for co in child_orgs:
            status_icon = "🟢" if co['is_active'] else "🔴"
            sub_tier = co.get('subscription_tier', 'client_direct')
            st.markdown(f"""
            <div class="client-card">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                    <div>
                        <div class="client-name">{status_icon} {co['name']}</div>
                        <div class="client-meta">
                            Type: Client Org ·
                            Tier: {sub_tier.replace('_',' ').title()} ·
                            👥 {co['user_count']} users ·
                            🧑 {co['lead_count']:,} leads ·
                            📁 {co['active_campaigns']} active campaigns
                        </div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            with st.expander(f"Manage — {co['name']}"):
                with st.form(f"edit_client_org_{co['id']}"):
                    cname = st.text_input("Organisation name", value=co['name'])
                    cactive = st.checkbox("Active", value=bool(co['is_active']))
                    if st.form_submit_button("Save"):
                        conn2 = get_connection()
                        conn2.execute(
                            "UPDATE organisations SET name=?, is_active=? WHERE id=? AND parent_org_id=?",
                            (cname, int(cactive), co['id'], org_id)
                        )
                        conn2.commit()
                        conn2.close()
                        st.success("Saved!")
                        st.rerun()
    else:
        st.info("No client organisations yet. Create one below.")

    st.markdown("---")
    st.subheader("Add Client Organisation")
    st.caption(
        "This creates a separate login portal for your client. "
        "They'll only see leads you release to them."
    )

    with st.form("add_client_org_form"):
        col1, col2 = st.columns(2)
        with col1:
            co_name        = st.text_input("Organisation name *",
                                            placeholder="Acme Insurance Ltd")
            contact_name   = st.text_input("Primary contact name *",
                                            placeholder="Jane Smith")
        with col2:
            contact_email  = st.text_input("Primary contact email *",
                                            placeholder="jane@acme.com",
                                            help="This becomes their login email")
            sub_type       = st.selectbox(
                "Subscription type",
                ["Via our agency", "Has own Dashin subscription"],
                help="'Via our agency' creates a new client portal. "
                     "'Has own Dashin subscription' links an existing account."
            )

        temp_password = st.text_input(
            "Temporary password *", type="password",
            help="They will be prompted to change this on first login"
        )

        if st.form_submit_button("Create Client Org", use_container_width=True):
            errors = []
            if not co_name.strip():      errors.append("Organisation name is required.")
            if not contact_name.strip(): errors.append("Contact name is required.")
            if not contact_email.strip(): errors.append("Contact email is required.")
            if not temp_password or len(temp_password) < 8:
                errors.append("Temporary password must be at least 8 characters.")

            if errors:
                for e in errors:
                    st.error(e)
            else:
                try:
                    import re as _re2
                    slug_base = _re2.sub(r'[^a-z0-9]', '-', co_name.lower().strip())
                    conn3 = get_connection()

                    # Create the client org
                    cur = conn3.execute("""
                        INSERT INTO organisations
                            (name, slug, tier, org_type, parent_org_id,
                             subscription_tier, subscription_status,
                             ai_budget_usd, max_users, max_clients, max_leads,
                             is_active, onboarded_by, onboarded_at)
                        VALUES (?, ?, 'starter', 'client', ?,
                                'client_direct', 'active',
                                0, 3, 0, 100000,
                                1, ?, datetime('now'))
                    """, (co_name.strip(), slug_base, org_id, user_id))
                    new_org_id = cur.lastrowid

                    # Create the client_admin user
                    from core.auth import hash_password
                    pw_hash = hash_password(temp_password)
                    conn3.execute("""
                        INSERT INTO users
                            (org_id, name, email, password, role,
                             is_active, must_reset_password, created_at)
                        VALUES (?, ?, ?, ?, 'client_admin', 1, 1, datetime('now'))
                    """, (new_org_id, contact_name.strip(),
                          contact_email.lower().strip(), pw_hash))

                    conn3.commit()
                    conn3.close()

                    st.success(f"✅ Client org '{co_name}' created!")
                    st.info(
                        f"**Login credentials for {contact_name}:**\n\n"
                        f"Email: `{contact_email.lower().strip()}`\n\n"
                        f"Temporary password: `{temp_password}`\n\n"
                        f"They will be asked to change their password on first login."
                    )
                    st.rerun()
                except Exception as ex:
                    if "UNIQUE" in str(ex):
                        st.error("That email or organisation slug is already registered.")
                    else:
                        st.error(f"Error creating client org: {ex}")


# ── ORG SETTINGS ─────────────────────────────────────────────────────────────

def _render_org_settings(org_id: int, org):
    conn = get_connection()

    st.subheader("Organisation Settings")

    with st.form("org_settings_form"):
        name = st.text_input("Organisation name", value=org["name"])
        slug = st.text_input("Slug (URL-friendly)", value=org["slug"],
                             help="Lowercase letters and hyphens only")

        st.markdown("---")
        st.markdown("**Plan & Limits** *(read-only — contact Dashin to change)*")

        col1, col2, col3 = st.columns(3)
        col1.metric("Tier", org["tier"].title())
        col2.metric("AI Budget", f"${org['ai_budget_usd']:.0f}/mo")
        col3.metric("Max Users", str(org["max_users"]))

        billing_day = st.number_input(
            "Billing anniversary day",
            min_value=1, max_value=28,
            value=int(org["billing_day"] or 1),
            help="Day of month when AI usage resets"
        )

        if st.form_submit_button("Save Settings"):
            conn.execute("""
                UPDATE organisations
                SET name=?, slug=?, billing_day=?
                WHERE id=?
            """, (name, slug.lower().strip(), billing_day, org_id))
            conn.commit()
            st.success("Settings saved!")
            st.rerun()

    conn.close()

    _render_scraper_tokens(org_id)


def _render_scraper_tokens(org_id: int):
    """Generate/revoke API tokens the desktop scraper uses to push into this org."""
    from services import token_service as ts

    st.markdown("---")
    st.subheader("🔑 Desktop Scraper Tokens")
    st.caption("A token lets the desktop scraper push scraped leads straight into "
               "this org's inventory. Give one token per client machine. The full "
               "token is shown once at creation — copy it then.")

    # Show a freshly generated token once (kept in session_state until dismissed)
    new_tok = st.session_state.get("new_scraper_token")
    if new_tok:
        st.success("New token created — copy it now, it won't be shown again:")
        st.code(new_tok, language=None)
        if st.button("Done (hide token)"):
            st.session_state.pop("new_scraper_token", None)
            st.rerun()

    with st.form("gen_token_form"):
        label = st.text_input("Label", placeholder="e.g. Client A — office laptop")
        if st.form_submit_button("Generate token"):
            st.session_state["new_scraper_token"] = ts.generate_token(org_id, label)
            st.rerun()

    tokens = ts.list_tokens(org_id)
    if not tokens:
        st.caption("No tokens yet.")
        return
    for t in tokens:
        cols = st.columns([3, 2, 2, 1])
        status = "🔴 revoked" if t["revoked"] else "🟢 active"
        cols[0].markdown(f"**{t.get('label') or '(no label)'}** · {status}")
        cols[1].caption(f"created {(t.get('created_at') or '')[:10]}")
        cols[2].caption(f"last used {(t.get('last_used_at') or '—')[:10]}")
        if not t["revoked"]:
            if cols[3].button("Revoke", key=f"revoke_tok_{t['id']}"):
                ts.revoke_token(t["id"], org_id)
                st.rerun()
