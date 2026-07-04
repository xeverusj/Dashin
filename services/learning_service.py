"""
services/learning_service.py — Dashin Research Platform
The platform's memory. Records what works, builds patterns,
reduces AI dependency over time.
"""

import json
import logging
import re
from datetime import datetime, timezone


def _get_conn():
    from core.db import get_connection
    return get_connection()


# ══════════════════════════════════════════════════════════════════════════════
# SCRAPER PATTERN LEARNING
# ══════════════════════════════════════════════════════════════════════════════

def get_layout_pattern(domain: str, org_id: int = None) -> dict | None:
    """
    Look up a saved layout pattern for a domain.
    Checks org-specific first, then global.
    Returns pattern dict if confidence >= 0.7, else None.
    """
    conn = _get_conn()
    # Try org-specific first
    row = None
    if org_id:
        row = conn.execute("""
            SELECT * FROM layout_patterns
            WHERE domain=? AND org_id=?
              AND confidence >= 0.7
            ORDER BY confidence DESC, success_count DESC
            LIMIT 1
        """, (domain, org_id)).fetchone()

    # Fall back to global pattern
    if not row:
        row = conn.execute("""
            SELECT * FROM layout_patterns
            WHERE domain=? AND org_id IS NULL
              AND confidence >= 0.7
            ORDER BY confidence DESC, success_count DESC
            LIMIT 1
        """, (domain,)).fetchone()

    conn.close()
    if row:
        result = dict(row)
        if result.get("selectors"):
            try:
                result["selectors"] = json.loads(result["selectors"])
            except Exception as e:
                logging.warning(f"[learning_service] Failed to parse selectors JSON: {e}")
        return result
    return None


def record_pattern_success(domain: str, layout_type: str,
                            selectors: dict, pagination_type: str,
                            org_id: int = None, leads_found: int = 0):
    """Record a successful scrape pattern."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    selectors_json = json.dumps(selectors) if selectors else None

    existing = conn.execute("""
        SELECT id, success_count, fail_count FROM layout_patterns
        WHERE domain=? AND layout_type=?
          AND (org_id=? OR (org_id IS NULL AND ? IS NULL))
    """, (domain, layout_type, org_id, org_id)).fetchone()

    if existing:
        s = existing["success_count"] + 1
        f = existing["fail_count"]
        confidence = round(s / (s + f), 3)
        conn.execute("""
            UPDATE layout_patterns
            SET success_count=?, confidence=?, last_used=?,
                last_verified=?, selectors=?, pagination_type=?
            WHERE id=?
        """, (s, confidence, now, now,
              selectors_json, pagination_type, existing["id"]))
    else:
        conn.execute("""
            INSERT INTO layout_patterns
                (org_id, domain, layout_type, selectors, pagination_type,
                 success_count, fail_count, confidence, last_used, last_verified)
            VALUES (?,?,?,?,?,1,0,1.0,?,?)
        """, (org_id, domain, layout_type, selectors_json,
              pagination_type, now, now))

    # Log learning event
    conn.execute("""
        INSERT INTO learning_events
            (org_id, event_type, entity_type, new_value, logged_at)
        VALUES (?,?,?,?,?)
    """, (org_id, "pattern_success", "session",
          f"{domain}:{layout_type}:{leads_found}_leads", now))

    conn.commit()
    conn.close()


def record_pattern_failure(domain: str, layout_type: str,
                            org_id: int = None):
    """Record a failed scrape attempt — reduces confidence."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    existing = conn.execute("""
        SELECT id, success_count, fail_count FROM layout_patterns
        WHERE domain=? AND layout_type=?
          AND (org_id=? OR (org_id IS NULL AND ? IS NULL))
    """, (domain, layout_type, org_id, org_id)).fetchone()

    if existing:
        s = existing["success_count"]
        f = existing["fail_count"] + 1
        confidence = round(s / (s + f), 3)
        conn.execute("""
            UPDATE layout_patterns
            SET fail_count=?, confidence=?, last_used=?
            WHERE id=?
        """, (f, confidence, now, existing["id"]))

        conn.execute("""
            INSERT INTO learning_events
                (org_id, event_type, entity_type, new_value, logged_at)
            VALUES (?,?,?,?,?)
        """, (org_id, "pattern_fail", "session",
              f"{domain}:{layout_type}:confidence={confidence}", now))

    conn.commit()
    conn.close()


def should_skip_ai(domain: str, org_id: int = None) -> bool:
    """
    Returns True if we have a high-confidence pattern and can skip AI.
    Threshold: confidence >= 0.85 AND success_count >= 3
    """
    pattern = get_layout_pattern(domain, org_id)
    if not pattern:
        return False
    return (pattern["confidence"] >= 0.85 and
            pattern["success_count"] >= 3)


# ══════════════════════════════════════════════════════════════════════════════
# COLUMN SYNONYM LEARNING
# ══════════════════════════════════════════════════════════════════════════════

def record_column_mapping(raw_column: str, mapped_field: str,
                           org_id: int = None):
    """
    Record that a CSV column name maps to a Dashin field.
    e.g. "Contact Name" -> "full_name"
    """
    conn = _get_conn()
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    key = raw_column.lower().strip()

    conn.execute("""
        INSERT INTO learned_mappings
            (org_id, mapping_type, key, value, usage_count, last_used)
        VALUES (?,?,?,?,1,?)
        ON CONFLICT(org_id, mapping_type, key) DO UPDATE SET
            usage_count = usage_count + 1,
            value       = excluded.value,
            last_used   = excluded.last_used
    """, (org_id, "column_synonym", key, mapped_field, now))

    conn.execute("""
        INSERT INTO learning_events
            (org_id, event_type, entity_type, old_value, new_value, logged_at)
        VALUES (?,?,?,?,?,?)
    """, (org_id, "column_mapped", "upload", key, mapped_field, now))

    conn.commit()
    conn.close()


def get_learned_column_mappings(org_id: int = None) -> dict:
    """
    Returns {raw_column_name: dashin_field} for this org + global.
    """
    conn = _get_conn()
    rows = conn.execute("""
        SELECT key, value, usage_count FROM learned_mappings
        WHERE mapping_type='column_synonym'
          AND (org_id=? OR org_id IS NULL)
        ORDER BY usage_count DESC
    """, (org_id,)).fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


# ══════════════════════════════════════════════════════════════════════════════
# PERSONA CLASSIFICATION LEARNING
# ══════════════════════════════════════════════════════════════════════════════

def record_persona_correction(title: str, old_persona: str,
                               new_persona: str, org_id: int,
                               corrected_by: int):
    """
    When a manager overrides a persona classification, learn from it.
    """
    conn = _get_conn()
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    key = title.lower().strip()

    conn.execute("""
        INSERT INTO learned_mappings
            (org_id, mapping_type, key, value, usage_count, last_used)
        VALUES (?,?,?,?,1,?)
        ON CONFLICT(org_id, mapping_type, key) DO UPDATE SET
            value       = excluded.value,
            usage_count = usage_count + 1,
            last_used   = excluded.last_used
    """, (org_id, "persona_title", key, new_persona, now))

    conn.execute("""
        INSERT INTO learning_events
            (org_id, event_type, entity_type,
             old_value, new_value, triggered_by, logged_at)
        VALUES (?,?,?,?,?,?,?)
    """, (org_id, "persona_corrected", "lead",
          f"{title}:{old_persona}", f"{title}:{new_persona}",
          corrected_by, now))

    conn.commit()
    conn.close()


def get_persona_for_title(title: str, org_id: int) -> str | None:
    """
    Look up learned persona for a job title.
    Returns persona string or None if not learned yet.
    """
    conn = _get_conn()
    key = title.lower().strip()

    row = conn.execute("""
        SELECT value FROM learned_mappings
        WHERE mapping_type='persona_title'
          AND key=?
          AND (org_id=? OR org_id IS NULL)
        ORDER BY CASE WHEN org_id=? THEN 0 ELSE 1 END
        LIMIT 1
    """, (key, org_id, org_id)).fetchone()

    conn.close()
    return row["value"] if row else None


# ══════════════════════════════════════════════════════════════════════════════
# ORG BENCHMARKS
# ══════════════════════════════════════════════════════════════════════════════

def update_org_benchmarks(org_id: int):
    """
    Recalculate all benchmarks for an org from historical data.
    Called periodically (e.g. after each task completion).
    """
    conn = _get_conn()
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    # Avg enrichment time per lead
    avg_mins = conn.execute("""
        SELECT AVG(e.minutes_spent) AS avg_mins
        FROM enrichment e
        JOIN leads l ON l.id = e.lead_id
        WHERE l.org_id=? AND e.minutes_spent > 0
    """, (org_id,)).fetchone()["avg_mins"]

    # Rejection rate
    total_leads = conn.execute(
        "SELECT COUNT(*) AS c FROM leads WHERE org_id=?", (org_id,)
    ).fetchone()["c"]

    rejected = conn.execute("""
        SELECT COUNT(*) AS c FROM rejections r
        JOIN leads l ON l.id = r.lead_id WHERE l.org_id=?
    """, (org_id,)).fetchone()["c"]

    # Personal email rate
    total_enriched = conn.execute("""
        SELECT COUNT(*) AS c FROM enrichment e
        JOIN leads l ON l.id = e.lead_id
        WHERE l.org_id=? AND e.email IS NOT NULL AND e.email != ''
    """, (org_id,)).fetchone()["c"]

    personal_flagged = conn.execute("""
        SELECT COUNT(*) AS c FROM lead_flags lf
        JOIN leads l ON l.id = lf.lead_id
        WHERE l.org_id=? AND lf.flag_type='personal_email'
    """, (org_id,)).fetchone()["c"]

    # Weekly stats averages
    stats = conn.execute("""
        SELECT
            AVG(CAST(opens AS REAL) / NULLIF(total_sent,0)) AS avg_open_rate,
            AVG(CAST(responded AS REAL) / NULLIF(total_sent,0)) AS avg_response_rate,
            AVG(CAST(meetings_done AS REAL) / NULLIF(total_sent,0) * 100) AS avg_mtg_per_100
        FROM campaign_weekly_stats cws
        JOIN campaigns ca ON ca.id = cws.campaign_id
        WHERE ca.org_id=? AND total_sent > 0
    """, (org_id,)).fetchone()

    # Avg leads per scrape session
    scrape_avg = conn.execute("""
        SELECT AVG(leads_new) AS avg_leads
        FROM scrape_sessions
        WHERE org_id=? AND status='done' AND leads_new > 0
    """, (org_id,)).fetchone()["avg_leads"]

    benchmarks = {
        "avg_enrichment_mins":    avg_mins or 0,
        "avg_rejection_rate":     (rejected / total_leads * 100) if total_leads else 0,
        "avg_personal_email_rate":(personal_flagged / total_enriched * 100) if total_enriched else 0,
        "avg_open_rate":          (stats["avg_open_rate"] or 0) * 100,
        "avg_response_rate":      (stats["avg_response_rate"] or 0) * 100,
        "avg_meetings_per_100":   stats["avg_mtg_per_100"] or 0,
        "avg_scrape_leads_per_session": scrape_avg or 0,
    }

    for metric, value in benchmarks.items():
        conn.execute("""
            INSERT INTO org_benchmarks (org_id, metric, value, calculated_at)
            VALUES (?,?,?,?)
            ON CONFLICT(org_id, metric) DO UPDATE SET
                value         = excluded.value,
                calculated_at = excluded.calculated_at
        """, (org_id, metric, round(value, 3), now))

    conn.commit()
    conn.close()
    return benchmarks


def get_org_benchmarks(org_id: int) -> dict:
    """Get all current benchmarks for an org."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT metric, value, calculated_at FROM org_benchmarks WHERE org_id=?",
        (org_id,)
    ).fetchall()
    conn.close()
    return {r["metric"]: r["value"] for r in rows}


# ══════════════════════════════════════════════════════════════════════════════
# AI SAVINGS REPORT
# ══════════════════════════════════════════════════════════════════════════════

def get_ai_savings_report(org_id: int) -> dict:
    """
    Show how much AI is being saved by pattern learning.
    """
    conn = _get_conn()

    total_sessions = conn.execute(
        "SELECT COUNT(*) AS c FROM scrape_sessions WHERE org_id=?",
        (org_id,)
    ).fetchone()["c"]

    pattern_sessions = conn.execute(
        "SELECT COUNT(*) AS c FROM scrape_sessions WHERE org_id=? AND pattern_used=1",
        (org_id,)
    ).fetchone()["c"]

    ai_sessions = total_sessions - pattern_sessions

    # Avg cost per AI session
    avg_cost = conn.execute("""
        SELECT AVG(ai_cost_usd) AS avg
        FROM scrape_sessions
        WHERE org_id=? AND pattern_used=0 AND ai_cost_usd > 0
    """, (org_id,)).fetchone()["avg"] or 0.05

    saved = pattern_sessions * avg_cost

    # Pattern coverage
    patterns = conn.execute("""
        SELECT COUNT(DISTINCT domain) AS c
        FROM layout_patterns
        WHERE (org_id=? OR org_id IS NULL) AND confidence >= 0.85
    """, (org_id,)).fetchone()["c"]

    conn.close()

    return {
        "total_sessions":     total_sessions,
        "ai_sessions":        ai_sessions,
        "pattern_sessions":   pattern_sessions,
        "pattern_pct":        round(pattern_sessions / total_sessions * 100, 1) if total_sessions else 0,
        "estimated_saved_usd":round(saved, 4),
        "domains_learned":    patterns,
        "avg_cost_per_ai_session": round(avg_cost, 4),
    }
