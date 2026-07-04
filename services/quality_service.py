"""
services/quality_service.py — Dashin Research Platform
Research Quality Evaluator.

Evaluates enrichment quality for leads, auto-approves high-quality ones,
and feeds rejection reasons back to researcher coaching.
"""

import re
import logging
from datetime import datetime, timezone


# ── SENIORITY KEYWORDS ────────────────────────────────────────────────────────

HIGH_SENIORITY = {
    "ceo", "cto", "coo", "cfo", "cmo", "ciso", "founder", "co-founder",
    "owner", "president", "managing director", "md", "chief executive",
    "chief technology", "chief operating", "chief financial", "chief marketing",
    "vp", "vice president", "head of", "director", "partner", "principal",
    "general manager", "regional manager", "global head",
}

MEDIUM_SENIORITY = {
    "senior manager", "senior director", "lead", "principal engineer",
    "country manager", "group head", "executive director",
    "manager", "team lead", "product manager", "project manager",
    "account manager", "business development", "sales manager",
    "marketing manager", "operations manager",
}

LOW_SENIORITY = {
    "engineer", "developer", "analyst", "consultant", "specialist",
    "associate", "coordinator", "executive", "advisor", "architect",
    "designer", "researcher", "scientist", "technician", "assistant",
    "intern", "junior", "graduate",
}

GENERIC_INBOXES = {
    "info", "contact", "hello", "hi", "support", "help", "admin",
    "administrator", "office", "team", "noreply", "no-reply",
    "donotreply", "sales", "marketing", "hr", "careers", "billing",
    "accounts", "general", "mail", "email",
}


def classify_seniority(title: str) -> str:
    """Returns 'high', 'medium', or 'low' based on job title."""
    if not title:
        return "low"
    t = title.lower()
    for kw in HIGH_SENIORITY:
        if kw in t:
            return "high"
    for kw in MEDIUM_SENIORITY:
        if kw in t:
            return "medium"
    return "low"


def is_valid_email_format(email: str) -> bool:
    if not email or "@" not in email:
        return False
    return bool(re.match(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', email.strip()))


def is_generic_email(email: str) -> bool:
    if not email or "@" not in email:
        return False
    local = email.split("@")[0].lower()
    clean = re.sub(r'[._\-]', '', local)
    return local in GENERIC_INBOXES or clean in GENERIC_INBOXES


def is_valid_linkedin(url: str) -> bool:
    """LinkedIn person URL must be linkedin.com/in/..., not a company page."""
    if not url:
        return False
    return bool(re.search(r'linkedin\.com/in/', url, re.IGNORECASE))


def _fuzzy_match(a: str, b: str) -> bool:
    """Simple substring match after normalisation."""
    if not a or not b:
        return True  # can't judge with missing data
    a_clean = re.sub(r'[^a-z0-9]', '', a.lower())
    b_clean = re.sub(r'[^a-z0-9]', '', b.lower())
    return (a_clean[:6] in b_clean or b_clean[:6] in a_clean or
            len(a_clean) < 4 or len(b_clean) < 4)


# ── MAIN EVALUATOR ─────────────────────────────────────────────────────────────

def evaluate_enrichment_quality(lead_id: int, enrichment_data: dict,
                                 scraped_data: dict = None) -> dict:
    """
    Evaluate the quality of a lead enrichment.

    Args:
        lead_id:        The lead being enriched.
        enrichment_data: Dict with keys: email, phone, linkedin_url, country,
                         industry, company_size, notes.
        scraped_data:   Optional dict with scraped name, title, company_name
                        (for consistency checks).

    Returns:
        {
            'quality_score': 0.0–1.0,
            'completeness_score': 0.0–1.0,
            'seniority_level': 'high'|'medium'|'low',
            'flags': [...],
            'auto_approved': bool,
        }
    """
    scraped_data = scraped_data or {}
    flags        = []

    email        = (enrichment_data.get("email") or "").strip()
    phone        = (enrichment_data.get("phone") or "").strip()
    linkedin     = (enrichment_data.get("linkedin_url") or "").strip()
    country      = (enrichment_data.get("country") or "").strip()
    industry     = (enrichment_data.get("industry") or "").strip()
    company_size = (enrichment_data.get("company_size") or "").strip()
    notes        = (enrichment_data.get("notes") or "").strip()

    scraped_name    = (scraped_data.get("full_name") or "").strip()
    scraped_title   = (scraped_data.get("title") or "").strip()
    scraped_company = (scraped_data.get("company_name") or "").strip()

    # ── Completeness score (5 key fields) ─────────────────────────────
    key_fields   = [email, scraped_title or industry, scraped_company or notes,
                    linkedin, country]
    filled_count = sum(1 for f in key_fields if f)
    completeness = filled_count / len(key_fields)

    # ── Email checks ──────────────────────────────────────────────────
    email_score = 0.0
    if email:
        if not is_valid_email_format(email):
            flags.append("invalid_email_format")
        elif is_generic_email(email):
            flags.append("generic_email")
            email_score = 0.2
        else:
            email_score = 1.0
    # Penalise missing email heavily
    else:
        flags.append("missing_email")

    # ── LinkedIn check ────────────────────────────────────────────────
    linkedin_score = 0.0
    if linkedin:
        if not is_valid_linkedin(linkedin):
            flags.append("invalid_linkedin")
        else:
            linkedin_score = 1.0

    # ── Consistency checks ────────────────────────────────────────────
    if scraped_name and enrichment_data.get("enriched_name"):
        enriched_name = enrichment_data["enriched_name"]
        if not _fuzzy_match(scraped_name, enriched_name):
            flags.append("name_mismatch")

    if scraped_company and enrichment_data.get("enriched_company"):
        if not _fuzzy_match(scraped_company, enrichment_data["enriched_company"]):
            flags.append("company_mismatch")

    # ── Seniority level ───────────────────────────────────────────────
    title_for_seniority = scraped_title or industry or ""
    seniority = classify_seniority(title_for_seniority)

    # ── Composite quality score ───────────────────────────────────────
    # Email is weighted heavily (0.5), completeness (0.3), linkedin (0.2)
    quality_score = round(
        (email_score * 0.50) +
        (completeness * 0.30) +
        (linkedin_score * 0.20),
        3
    )

    # Critical flags reduce score
    critical_flags = {"invalid_email_format", "generic_email", "missing_email",
                      "invalid_linkedin", "name_mismatch", "company_mismatch"}
    has_critical   = bool(set(flags) & critical_flags)

    # Auto-approval
    auto_approved = (
        quality_score >= 0.80 and
        not has_critical and
        bool(email)
    )

    return {
        "quality_score":     quality_score,
        "completeness_score": round(completeness, 3),
        "seniority_level":   seniority,
        "flags":             flags,
        "auto_approved":     auto_approved,
        "has_critical_flags": has_critical,
    }


# ── STATS HELPERS ─────────────────────────────────────────────────────────────

def get_researcher_quality_stats(org_id: int, researcher_id: int,
                                  week_start: str = None) -> dict:
    """
    Get quality stats for a researcher this week.
    week_start format: 'YYYY-MM-DD'. Defaults to current ISO week.
    """
    from core.db import get_connection
    if not week_start:
        from datetime import date, timedelta
        today = date.today()
        week_start = (today - timedelta(days=today.weekday())).isoformat()

    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT
                COUNT(*)                                       AS total,
                SUM(CASE WHEN l.status='enriched' THEN 1 END) AS enriched,
                AVG(CASE WHEN e.enriched_at >= ? THEN
                    CASE WHEN e.email IS NOT NULL THEN 0.8 ELSE 0.3 END
                    END)                                       AS avg_quality
            FROM leads l
            LEFT JOIN enrichment e ON e.lead_id = l.id
            WHERE l.org_id = ? AND e.enriched_by = ?
              AND e.enriched_at >= ?
        """, (week_start, org_id, researcher_id, week_start)).fetchone()

        rejection_rows = conn.execute("""
            SELECT reason, COUNT(*) AS c
            FROM rejections r
            JOIN leads l ON l.id = r.lead_id
            WHERE l.org_id = ? AND r.rejected_by = ?
              AND r.rejected_at >= ?
            GROUP BY reason ORDER BY c DESC LIMIT 5
        """, (org_id, researcher_id, week_start)).fetchall()

        return {
            "total":           (rows.get("total") or 0) if rows else 0,
            "enriched":        (rows.get("enriched") or 0) if rows else 0,
            "avg_quality":     round((rows.get("avg_quality") or 0), 3) if rows else 0,
            "top_rejections":  [dict(r) for r in rejection_rows],
        }
    except Exception as e:
        logging.warning(f"[quality_service.get_researcher_quality_stats] {e}")
        return {"total": 0, "enriched": 0, "avg_quality": 0, "top_rejections": []}
    finally:
        conn.close()


def get_org_quality_report(org_id: int) -> dict:
    """Top-level quality report for research manager dashboard."""
    from core.db import get_connection
    conn = get_connection()
    try:
        # Per-researcher stats
        researchers = conn.execute("""
            SELECT u.id, u.name,
                   COUNT(e.lead_id)                    AS enriched_count,
                   AVG(CASE WHEN e.email IS NOT NULL THEN 0.8 ELSE 0.3 END) AS avg_quality
            FROM users u
            LEFT JOIN enrichment e ON e.enriched_by = u.id
            LEFT JOIN leads l ON l.id = e.lead_id AND l.org_id = ?
            WHERE u.org_id = ? AND u.role = 'researcher' AND u.is_active = 1
            GROUP BY u.id
        """, (org_id, org_id)).fetchall()

        # Top flags
        flags = conn.execute("""
            SELECT flag_type, COUNT(*) AS c
            FROM lead_flags
            WHERE org_id = ? AND resolved = 0
            GROUP BY flag_type ORDER BY c DESC LIMIT 5
        """, (org_id,)).fetchall()

        return {
            "researchers": [dict(r) for r in researchers],
            "top_flags":   [dict(f) for f in flags],
        }
    except Exception as e:
        logging.warning(f"[quality_service.get_org_quality_report] {e}")
        return {"researchers": [], "top_flags": []}
    finally:
        conn.close()
