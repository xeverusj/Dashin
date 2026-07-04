"""
services/access_control.py — Dashin Research Platform
Single source of truth for data visibility rules.

Every query that touches leads, clients, or orgs MUST go through here.
Never do raw WHERE org_id=? queries in dashboards without using these helpers.
"""

import logging
from core.db import get_connection, ROLES_BY_ORG_TYPE


def get_visible_org_ids(user: dict) -> list:
    """
    Returns list of org_ids this user can see data for.

    dashin super_admin / org_admin  → all org ids
    agency org_admin / manager      → their org + all child client orgs
    agency researcher/research_mgr  → their org only
    agency campaign_manager         → their org + client orgs with active campaigns
    freelance org_admin             → their org + assigned client orgs
    freelance researcher            → their org only
    client_admin / client_user      → their org only
    """
    role     = user.get('role')
    org_id   = user.get('org_id')
    org_type = user.get('org_type', 'agency')

    conn = get_connection()
    try:
        # Dashin staff see everything
        if org_type == 'dashin' or role == 'super_admin':
            rows = conn.execute("SELECT id FROM organisations WHERE is_active=1").fetchall()
            return [r['id'] for r in rows]

        # Agency/freelance org_admin/manager sees own org + all child client orgs
        if role in ('org_admin', 'manager') and org_type in ('agency', 'freelance', 'dashin'):
            rows = conn.execute("""
                SELECT id FROM organisations
                WHERE (id = ? OR parent_org_id = ?) AND is_active=1
            """, (org_id, org_id)).fetchall()
            return [r['id'] for r in rows]

        # Campaign manager sees own org + clients they manage campaigns for
        if role == 'campaign_manager':
            rows = conn.execute("""
                SELECT DISTINCT c.org_id AS id
                FROM campaigns c
                JOIN clients cl ON cl.id = c.client_id
                JOIN organisations o ON o.id = cl.org_id
                WHERE c.org_id = ? AND c.status != 'cancelled'
                  AND o.is_active = 1
                UNION
                SELECT ?
            """, (org_id, org_id)).fetchall()
            return [r['id'] for r in rows]

        # Freelance org: use assignments table
        if org_type == 'freelance':
            rows = conn.execute("""
                SELECT client_org_id AS id
                FROM freelancer_client_assignments
                WHERE freelancer_org_id = ? AND active = 1
                UNION SELECT ?
            """, (org_id, org_id)).fetchall()
            return [r['id'] for r in rows]

        # Everyone else (researcher, client roles) sees only their own org
        return [org_id]
    finally:
        conn.close()


def get_visible_leads_query(user: dict) -> tuple:
    """
    Returns (WHERE clause, params list) to filter leads by visibility.

    Client roles only see released_to_client=1 leads for their org.
    All other roles see leads per get_visible_org_ids().
    """
    role     = user.get('role')
    org_id   = user.get('org_id')
    org_type = user.get('org_type', 'agency')

    # Clients only see leads that have been released to them
    if org_type == 'client' or role in ('client_admin', 'client_user'):
        return "l.org_id = ? AND l.released_to_client = 1", [org_id]

    visible = get_visible_org_ids(user)
    placeholders = ','.join('?' * len(visible))
    return f"l.org_id IN ({placeholders})", list(visible)


def can_create_user(creator: dict, new_role: str, target_org_id: int) -> tuple:
    """
    Returns (allowed: bool, reason: str).

    Rules:
    - Dashin staff can create users in any org
    - Agency/freelance org_admin can only create users in their own org
    - No one can create a role above their own level
    - Roles must be valid for the target org's type
    """
    creator_role     = creator.get('role')
    creator_org_id   = creator.get('org_id')
    creator_org_type = creator.get('org_type', 'agency')

    conn = get_connection()
    try:
        target_org = conn.execute(
            "SELECT org_type FROM organisations WHERE id=?", (target_org_id,)
        ).fetchone()
    finally:
        conn.close()

    if not target_org:
        return False, "Organisation not found"

    target_org_type = target_org['org_type']

    # Dashin staff can create anywhere
    if creator_org_type == 'dashin' and creator_role in ('super_admin', 'org_admin', 'manager'):
        valid_roles = ROLES_BY_ORG_TYPE.get(target_org_type, [])
        if new_role not in valid_roles:
            return False, f"Role '{new_role}' is not valid for {target_org_type} orgs"
        return True, "OK"

    # Agency/freelance org_admin can only add to their own org
    if creator_role == 'org_admin' and creator_org_type in ('agency', 'freelance'):
        if target_org_id != creator_org_id:
            return False, "You can only add users to your own organisation"
        valid_roles = ROLES_BY_ORG_TYPE.get(target_org_type, [])
        if new_role not in valid_roles:
            return False, f"Role '{new_role}' is not valid for your organisation type"
        # Agency org_admin cannot create another org_admin
        if new_role == 'org_admin':
            return False, "Contact Dashin to add additional admins"
        return True, "OK"

    # Manager can add researchers and below in own org
    if creator_role == 'manager' and creator_org_type in ('agency', 'freelance'):
        if target_org_id != creator_org_id:
            return False, "You can only add users to your own organisation"
        allowed_to_create = ['researcher', 'client_user']
        if new_role not in allowed_to_create:
            return False, f"Managers can only create researcher or client_user roles"
        return True, "OK"

    return False, "You do not have permission to create users"


def can_view_org(user: dict, target_org_id: int) -> bool:
    """Check if user can view data for a given org."""
    return target_org_id in get_visible_org_ids(user)


def get_subscription_limits(org_id: int) -> dict:
    """Returns the subscription tier limits for an org."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT st.*
            FROM subscription_tiers st
            JOIN organisations o ON o.subscription_tier = st.tier
            WHERE o.id = ?
        """, (org_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def check_lead_limit(org_id: int) -> tuple:
    """
    Returns (within_limit: bool, used: int, limit: int|None).
    Checks if org has exceeded their monthly lead scrape limit.
    """
    from datetime import datetime
    limits = get_subscription_limits(org_id)
    max_leads = limits.get('max_leads_per_month')

    if max_leads is None:
        return True, 0, None  # unlimited

    conn = get_connection()
    try:
        month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        row = conn.execute("""
            SELECT COUNT(*) AS cnt FROM leads
            WHERE org_id = ? AND scraped_at >= ?
        """, (org_id, month_start.isoformat())).fetchone()
        used = row['cnt'] if row else 0
    finally:
        conn.close()

    return used < max_leads, used, max_leads
