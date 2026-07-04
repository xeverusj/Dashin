"""
services/task_service.py — Dashin Research Platform
Task assignment, quota management, progress tracking, reassignment.
"""

import logging
from datetime import datetime, timezone
from core.db import get_connection


def create_task(
    org_id:           int,
    title:            str,
    task_type:        str,
    assigned_to:      int,
    assigned_by:      int,
    description:      str  = "",
    priority:         str  = "normal",
    deadline:         str  = None,
    target_count:     int  = 0,
    campaign_id:      int  = None,
    archived_list_id: int  = None,
) -> int:
    """Create a new task. Returns task_id."""
    conn = get_connection()
    now  = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    cur  = conn.execute("""
        INSERT INTO tasks
            (org_id, title, task_type, description, priority, status,
             assigned_to, assigned_by, assigned_at, deadline,
             target_count, campaign_id, archived_list_id, created_at)
        VALUES (?,?,?,?,?,'pending',?,?,?,?,?,?,?,?)
    """, (org_id, title, task_type, description, priority,
          assigned_to, assigned_by, now, deadline,
          target_count, campaign_id, archived_list_id, now))
    task_id = cur.lastrowid

    # Notify researcher
    _notify(conn, org_id, assigned_to,
            "task_assigned",
            f"New task: {title}",
            f"Priority: {priority}. Target: {target_count} leads." if target_count else f"Priority: {priority}.")

    conn.commit()
    conn.close()
    return task_id


def get_tasks(org_id: int, assigned_to: int = None,
              status: str = None, task_type: str = None) -> list:
    """Get tasks for an org, optionally filtered."""
    conn = get_connection()
    q    = """
        SELECT t.*,
               u_a.name AS assignee_name,
               u_b.name AS assigned_by_name,
               ca.name  AS campaign_name
        FROM tasks t
        LEFT JOIN users u_a ON u_a.id = t.assigned_to
        LEFT JOIN users u_b ON u_b.id = t.assigned_by
        LEFT JOIN campaigns ca ON ca.id = t.campaign_id
        WHERE t.org_id = ?
    """
    params = [org_id]
    if assigned_to:
        q += " AND t.assigned_to = ?"
        params.append(assigned_to)
    if status:
        q += " AND t.status = ?"
        params.append(status)
    if task_type:
        q += " AND t.task_type = ?"
        params.append(task_type)
    q += " ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, t.deadline ASC NULLS LAST"

    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_task(task_id: int) -> dict | None:
    conn = get_connection()
    row  = conn.execute("""
        SELECT t.*,
               u_a.name AS assignee_name,
               u_b.name AS assigned_by_name,
               ca.name  AS campaign_name
        FROM tasks t
        LEFT JOIN users u_a ON u_a.id = t.assigned_to
        LEFT JOIN users u_b ON u_b.id = t.assigned_by
        LEFT JOIN campaigns ca ON ca.id = t.campaign_id
        WHERE t.id = ?
    """, (task_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def start_task(task_id: int, user_id: int):
    """Researcher marks task as started."""
    conn = get_connection()
    now  = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    conn.execute("""
        UPDATE tasks SET status='in_progress', started_at=?
        WHERE id=? AND assigned_to=? AND status='pending'
    """, (now, task_id, user_id))
    conn.commit()
    conn.close()


def update_progress(task_id: int, completed_count: int):
    """Update how many leads completed so far."""
    conn = get_connection()
    conn.execute("""
        UPDATE tasks SET completed_count=?
        WHERE id=?
    """, (completed_count, task_id))
    conn.commit()
    conn.close()


def submit_task(task_id: int, user_id: int):
    """
    Researcher submits task for manager/research manager approval.
    """
    conn = get_connection()
    now  = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    task = conn.execute(
        "SELECT * FROM tasks WHERE id=?", (task_id,)
    ).fetchone()

    if not task:
        conn.close()
        return

    conn.execute("""
        UPDATE tasks SET status='submitted', submitted_at=?
        WHERE id=? AND assigned_to=?
    """, (now, task_id, user_id))

    # Notify research manager / manager
    mgrs = conn.execute("""
        SELECT id FROM users
        WHERE org_id=? AND role IN ('research_manager','manager','org_admin')
          AND is_active=1
    """, (task["org_id"],)).fetchall()

    for mgr in mgrs:
        _notify(conn, task["org_id"], mgr["id"],
                "task_submitted",
                f"Task submitted for review: {task['title']}",
                f"Submitted by researcher. {task['completed_count']} leads completed.")

    conn.commit()
    conn.close()


def approve_task(task_id: int, approved_by: int):
    """Research manager / manager approves a submitted task."""
    conn = get_connection()
    now  = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()

    conn.execute("""
        UPDATE tasks SET status='approved', approved_at=?, approved_by=?
        WHERE id=?
    """, (now, approved_by, task_id))

    if task:
        _notify(conn, task["org_id"], task["assigned_to"],
                "task_approved",
                f"Task approved: {task['title']}",
                "Your submission has been approved.")

    conn.commit()
    conn.close()


def reject_task(task_id: int, rejected_by: int, note: str):
    """Research manager rejects a task — sends back to researcher."""
    conn = get_connection()
    now  = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()

    conn.execute("""
        UPDATE tasks SET status='in_progress', rejection_note=?
        WHERE id=?
    """, (note, task_id))

    if task:
        _notify(conn, task["org_id"], task["assigned_to"],
                "task_rejected",
                f"Task needs revision: {task['title']}",
                note)

    conn.commit()
    conn.close()


def reassign_task(task_id: int, from_user: int,
                  to_user: int, reason: str = ""):
    """Researcher reassigns a task to a colleague."""
    conn = get_connection()
    now  = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()

    conn.execute("""
        UPDATE tasks SET assigned_to=? WHERE id=?
    """, (to_user, task_id))

    conn.execute("""
        INSERT INTO task_reassignments
            (task_id, from_user, to_user, reason, reassigned_at)
        VALUES (?,?,?,?,?)
    """, (task_id, from_user, to_user, reason, now))

    if task:
        _notify(conn, task["org_id"], to_user,
                "task_assigned",
                f"Task reassigned to you: {task['title']}",
                f"Reassigned by colleague. Reason: {reason}" if reason else "Reassigned by colleague.")

    conn.commit()
    conn.close()


# ── QUOTAS ────────────────────────────────────────────────────────────────────

def set_quota(org_id: int, researcher_id: int, set_by: int,
              week_start: str, target_leads: int,
              target_enriched: int = 0, notes: str = "") -> int:
    """Set weekly quota for a researcher."""
    conn = get_connection()
    now  = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    cur  = conn.execute("""
        INSERT INTO research_quotas
            (org_id, researcher_id, set_by, week_start,
             target_leads, target_enriched, notes, created_at)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(org_id, researcher_id, week_start) DO UPDATE SET
            target_leads    = excluded.target_leads,
            target_enriched = excluded.target_enriched,
            notes           = excluded.notes
    """, (org_id, researcher_id, set_by, week_start,
          target_leads, target_enriched, notes, now))

    _notify(conn, org_id, researcher_id,
            "quota_set",
            f"Weekly quota set: {target_leads} leads",
            f"Week of {week_start}. {notes}" if notes else f"Week of {week_start}.")

    conn.commit()
    conn.close()
    return cur.lastrowid


def get_quota(org_id: int, researcher_id: int,
              week_start: str) -> dict | None:
    conn = get_connection()
    row  = conn.execute("""
        SELECT q.*, u.name AS researcher_name
        FROM research_quotas q
        JOIN users u ON u.id = q.researcher_id
        WHERE q.org_id=? AND q.researcher_id=? AND q.week_start=?
    """, (org_id, researcher_id, week_start)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_team_quotas(org_id: int, week_start: str) -> list:
    """Get all researcher quotas for a given week."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT q.*, u.name AS researcher_name,
               (SELECT COUNT(*) FROM tasks t
                WHERE t.assigned_to = q.researcher_id
                  AND t.status IN ('approved','done')
                  AND t.assigned_at >= q.week_start) AS tasks_completed
        FROM research_quotas q
        JOIN users u ON u.id = q.researcher_id
        WHERE q.org_id=? AND q.week_start=?
        ORDER BY u.name
    """, (org_id, week_start)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── RESEARCHER KPIs ───────────────────────────────────────────────────────────

def get_researcher_kpis(org_id: int, researcher_id: int,
                         week_start: str = None) -> dict:
    """
    Calculate all 6 KPIs for a researcher.
    week_start = ISO date string, or None for all-time.
    """
    conn = get_connection()
    date_filter = f"AND a.assigned_at >= '{week_start}'" if week_start else ""

    # 1. Leads assigned vs completed
    assigned = conn.execute(f"""
        SELECT COUNT(*) AS c FROM tasks
        WHERE org_id=? AND assigned_to=? {date_filter.replace('a.','') }
    """, (org_id, researcher_id)).fetchone()["c"]

    completed = conn.execute(f"""
        SELECT COUNT(*) AS c FROM tasks
        WHERE org_id=? AND assigned_to=?
          AND status IN ('approved','done')
          {'AND assigned_at >= ?' if week_start else ''}
    """, (org_id, researcher_id) + ((week_start,) if week_start else ())).fetchone()["c"]

    # 2. Rejection rate
    total_enriched = conn.execute("""
        SELECT COUNT(*) AS c FROM enrichment e
        JOIN leads l ON l.id = e.lead_id
        WHERE l.org_id=? AND e.enriched_by=?
    """, (org_id, researcher_id)).fetchone()["c"]

    rejected = conn.execute("""
        SELECT COUNT(*) AS c FROM rejections r
        JOIN leads l ON l.id = r.lead_id
        WHERE l.org_id=? AND r.rejected_by=?
    """, (org_id, researcher_id)).fetchone()["c"]

    # 3. Personal email rate
    personal_flagged = conn.execute("""
        SELECT COUNT(*) AS c FROM lead_flags lf
        JOIN enrichment e ON e.lead_id = lf.lead_id
        JOIN leads l ON l.id = lf.lead_id
        WHERE l.org_id=? AND e.enriched_by=?
          AND lf.flag_type='personal_email'
    """, (org_id, researcher_id)).fetchone()["c"]

    # 4. Avg time per lead
    avg_time = conn.execute("""
        SELECT AVG(e.minutes_spent) AS avg
        FROM enrichment e
        JOIN leads l ON l.id = e.lead_id
        WHERE l.org_id=? AND e.enriched_by=? AND e.minutes_spent > 0
    """, (org_id, researcher_id)).fetchone()["avg"] or 0

    # 5. Bounce rate (flagged as invalid email)
    bounced = conn.execute("""
        SELECT COUNT(*) AS c FROM lead_flags lf
        JOIN enrichment e ON e.lead_id = lf.lead_id
        JOIN leads l ON l.id = lf.lead_id
        WHERE l.org_id=? AND e.enriched_by=?
          AND lf.flag_type='invalid_email_format'
    """, (org_id, researcher_id)).fetchone()["c"]

    # 6. Delivered vs target (from quota)
    quota = None
    if week_start:
        quota = get_quota(org_id, researcher_id, week_start)

    def pct(a, b):
        return round(a / b * 100, 1) if b > 0 else 0

    conn.close()

    return {
        "assigned":           assigned,
        "completed":          completed,
        "completion_rate":    pct(completed, assigned),
        "total_enriched":     total_enriched,
        "rejected":           rejected,
        "rejection_rate":     pct(rejected, total_enriched),
        "personal_email_count": personal_flagged,
        "personal_email_rate":  pct(personal_flagged, total_enriched),
        "avg_mins_per_lead":  round(avg_time, 1),
        "bounced":            bounced,
        "bounce_rate":        pct(bounced, total_enriched),
        "quota_target":       quota["target_leads"] if quota else None,
        "quota_delivered":    total_enriched,
        "quota_pct":          pct(total_enriched, quota["target_leads"]) if quota else None,
    }


def get_team_kpis(org_id: int, week_start: str = None) -> list:
    """Get KPIs for all researchers in an org."""
    conn = get_connection()
    researchers = conn.execute("""
        SELECT id, name FROM users
        WHERE org_id=? AND role='researcher' AND is_active=1
        ORDER BY name
    """, (org_id,)).fetchall()
    conn.close()

    results = []
    for r in researchers:
        kpis = get_researcher_kpis(org_id, r["id"], week_start)
        kpis["researcher_id"]   = r["id"]
        kpis["researcher_name"] = r["name"]
        results.append(kpis)
    return results


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _notify(conn, org_id: int, user_id: int,
            ntype: str, title: str, body: str = ""):
    """Insert a notification (uses existing conn)."""
    try:
        conn.execute("""
            INSERT INTO notifications
                (org_id, user_id, type, title, body, created_at)
            VALUES (?,?,?,?,?,?)
        """, (org_id, user_id, ntype, title, body,
              datetime.now(timezone.utc).replace(tzinfo=None).isoformat()))
    except Exception as e:
        logging.warning(f"[task_service] Failed to insert notification: {e}")
