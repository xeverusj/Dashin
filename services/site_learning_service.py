"""
services/site_learning_service.py — Dashin Research Platform
AI Site Learning Agent: discover → verify → classify → monitor.

Four phases for every unknown site:
  Phase 1 — Discover:  Claude Vision identifies selectors
  Phase 2 — Verify:    Quality-check the scraped data
  Phase 3 — Classify:  stable (data-test) vs fragile (class-based)
  Phase 4 — Monitor:   track success/fail counts, expire stale patterns
"""

import re
import json
import logging
from datetime import datetime, timezone, timedelta

FRAGILE_EXPIRY_DAYS = 30
MIN_CONFIDENCE      = 0.65
MIN_QUALITY_SCORE   = 0.70
FRAGILE_MIN_CONF    = 0.80   # fragile selectors need higher bar


# ── DB HELPER ─────────────────────────────────────────────────────────────────

def _conn():
    from core.db import get_connection
    return get_connection()


# ── PHASE 1: CLASSIFY SELECTOR TYPE ──────────────────────────────────────────

def classify_selector_type(card_selector: str) -> str:
    """
    Returns 'stable' if selector uses data-test/data-testid attributes,
    'fragile' otherwise (class-based, obfuscated, etc.)
    """
    if not card_selector:
        return 'fragile'
    if re.search(r'\[data-test', card_selector) or re.search(r'\[data-testid', card_selector):
        return 'stable'
    return 'fragile'


def is_obfuscated_selector(selector: str) -> bool:
    """Returns True if selector looks like an obfuscated CSS class (css-xxxxx, styled-components)."""
    if not selector:
        return False
    obfuscated_patterns = [
        r'css-[a-z0-9]{5,}',          # css-abc12
        r'\.[a-z]{2,4}[0-9]{4,}',     # .ab1234
        r'sc-[a-z]{5,}',              # styled-components: sc-abcdef
    ]
    return any(re.search(p, selector, re.IGNORECASE) for p in obfuscated_patterns)


# ── PHASE 2: VERIFY SCRAPE QUALITY ───────────────────────────────────────────

def verify_scrape_quality(leads_batch: list) -> tuple:
    """
    Evaluate the quality of a scraped batch.

    Checks:
    - Minimum 3 leads returned
    - >= 70% have a name that passes is_valid_name()
    - >= 50% have a non-N/A title OR company (not just name)
    - <= 20% garbage words (numbers, single words that are categories)

    Returns: (passed: bool, score: float, report: dict)
    """
    if not leads_batch:
        return False, 0.0, {"error": "Empty batch"}

    total = len(leads_batch)

    if total < 3:
        return False, 0.0, {
            "error": f"Too few leads: {total} (minimum 3)",
            "total": total,
        }

    valid_names = sum(1 for l in leads_batch
                      if is_valid_name(str(l.get("name") or l.get("full_name") or "")))
    has_context = sum(1 for l in leads_batch
                      if _has_context(l))

    names_pct   = valid_names / total
    context_pct = has_context / total

    # Weighted score
    score = (names_pct * 0.6) + (context_pct * 0.4)

    passed = (names_pct >= 0.70 and context_pct >= 0.50 and score >= MIN_QUALITY_SCORE)

    report = {
        "total":        total,
        "valid_names":  valid_names,
        "names_pct":    round(names_pct, 3),
        "has_context":  has_context,
        "context_pct":  round(context_pct, 3),
        "quality_score": round(score, 3),
        "passed":       passed,
        "timestamp":    datetime.now(timezone.utc).isoformat(),
    }
    return passed, round(score, 3), report


def is_valid_name(name: str) -> bool:
    """Returns True if the string looks like a person's name (not garbage)."""
    if not name or len(name.strip()) < 2:
        return False
    name = name.strip()
    # Reject pure numbers
    if name.replace(" ", "").isdigit():
        return False
    # Reject very short single tokens
    if len(name) < 3:
        return False
    # Reject obvious category words
    GARBAGE = {"speaker", "attendee", "delegate", "participant",
                "sponsor", "exhibitor", "moderator", "panelist",
                "visitor", "guest", "member", "staff", "team"}
    if name.lower() in GARBAGE:
        return False
    return True


def _has_context(lead: dict) -> bool:
    """Returns True if lead has at least title or company (not just a name)."""
    title   = str(lead.get("title") or "").strip()
    company = str(lead.get("company") or lead.get("company_name") or "").strip()
    na_vals = {"", "n/a", "na", "none", "null", "unknown", "-", "—"}
    return title.lower() not in na_vals or company.lower() not in na_vals


# ── PHASE 3 & 4: PERSIST AND MONITOR ────────────────────────────────────────

def save_site_pattern(domain: str, pattern: dict, quality_score: float,
                      leads_found: int) -> bool:
    """
    Persist a new or updated site pattern after quality verification.
    Only saves if confidence and quality thresholds are met.

    Returns True if saved, False if rejected.
    """
    confidence    = pattern.get("confidence", 0.0)
    card_selector = pattern.get("card_selector", "")
    selector_type = classify_selector_type(card_selector)

    # Threshold gate
    if confidence < MIN_CONFIDENCE:
        _log_history(domain, card_selector, confidence, quality_score,
                     "rejected", f"confidence {confidence:.2f} < {MIN_CONFIDENCE}")
        return False

    if quality_score < MIN_QUALITY_SCORE:
        _log_history(domain, card_selector, confidence, quality_score,
                     "rejected", f"quality {quality_score:.2f} < {MIN_QUALITY_SCORE}")
        return False

    if selector_type == 'fragile' and confidence < FRAGILE_MIN_CONF:
        _log_history(domain, card_selector, confidence, quality_score,
                     "rejected", f"fragile selector needs confidence >= {FRAGILE_MIN_CONF}")
        return False

    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    conn = _conn()
    try:
        conn.execute("""
            INSERT INTO site_patterns
                (domain, card_selector, name_selector, title_selector,
                 company_selector, extra_selector, pagination_type,
                 next_button_selector, confidence, selector_type,
                 quality_score, last_quality_check, last_success_at,
                 last_attempt_at, success_count, fail_count,
                 verified_by, notes, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,0,'ai',?,?,?)
            ON CONFLICT(domain) DO UPDATE SET
                card_selector        = excluded.card_selector,
                name_selector        = excluded.name_selector,
                title_selector       = excluded.title_selector,
                company_selector     = excluded.company_selector,
                confidence           = excluded.confidence,
                selector_type        = excluded.selector_type,
                quality_score        = excluded.quality_score,
                last_quality_check   = excluded.last_quality_check,
                last_success_at      = excluded.last_success_at,
                last_attempt_at      = excluded.last_attempt_at,
                success_count        = success_count + 1,
                updated_at           = excluded.updated_at
        """, (domain,
              card_selector,
              pattern.get("name_selector"),
              pattern.get("title_selector"),
              pattern.get("company_selector"),
              pattern.get("extra_selector"),
              pattern.get("pagination_type", "url_param"),
              pattern.get("next_button_selector"),
              confidence,
              selector_type,
              quality_score,
              now,          # last_quality_check
              now,          # last_success_at
              now,          # last_attempt_at
              pattern.get("notes"),
              now,          # created_at
              now,          # updated_at
              ))
        conn.commit()
        _log_history(domain, card_selector, confidence, quality_score, "promoted",
                     None, leads_found)
        return True
    except Exception as e:
        logging.warning(f"[site_learning] save_site_pattern failed: {e}")
        return False
    finally:
        conn.close()


def get_site_pattern(domain: str) -> dict | None:
    """
    Look up a valid (non-expired) pattern for a domain.
    Returns None if not found, expired, or below confidence threshold.
    """
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM site_patterns WHERE domain=?", (domain,)
        ).fetchone()
    except Exception as e:
        logging.warning(f"[site_learning_service.get_site_pattern] DB query failed for {domain}: {e}")
        row = None
    finally:
        conn.close()

    if not row:
        return None

    if row.get("confidence", 0) < MIN_CONFIDENCE:
        return None

    # Check expiry for fragile selectors
    if row.get("selector_type") == "fragile":
        last_success = row.get("last_success_at")
        if not last_success:
            return None  # never successfully verified
        try:
            ls_dt = datetime.fromisoformat(last_success)
            if (datetime.now() - ls_dt).days > FRAGILE_EXPIRY_DAYS:
                _log_history(domain, row.get("card_selector"), row.get("confidence"),
                             row.get("quality_score"), "expired",
                             f"fragile pattern > {FRAGILE_EXPIRY_DAYS} days old")
                return None
        except Exception as e:
            logging.warning(f"[site_learning_service.get_site_pattern] Expiry check failed for {domain}: {e}")
            return None

    return dict(row)


def record_pattern_attempt(domain: str, success: bool, leads_found: int = 0):
    """Update success/fail counters after each scrape using a known pattern."""
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    conn = _conn()
    try:
        if success:
            conn.execute("""
                UPDATE site_patterns
                SET success_count = success_count + 1,
                    last_success_at = ?,
                    last_attempt_at = ?,
                    fail_count = 0
                WHERE domain = ?
            """, (now, now, domain))
        else:
            conn.execute("""
                UPDATE site_patterns
                SET fail_count = fail_count + 1,
                    last_attempt_at = ?
                WHERE domain = ?
            """, (now, domain))
            # Check if fail_count >= 3 → expire
            row = conn.execute(
                "SELECT fail_count FROM site_patterns WHERE domain=?", (domain,)
            ).fetchone()
            if row and (row.get("fail_count") or 0) >= 3:
                conn.execute(
                    "UPDATE site_patterns SET last_success_at=NULL WHERE domain=?",
                    (domain,)
                )
                _log_history(domain, None, None, None, "expired",
                             f"fail_count >= 3")
                logging.warning(f"[site_learning] Pattern for {domain} expired (3 consecutive failures)")
        conn.commit()
    except Exception as e:
        logging.warning(f"[site_learning] record_pattern_attempt: {e}")
    finally:
        conn.close()


def expire_pattern(domain: str):
    """Manually expire a pattern so it will be re-learned on next use."""
    conn = _conn()
    try:
        conn.execute(
            "UPDATE site_patterns SET last_success_at=NULL WHERE domain=?",
            (domain,)
        )
        conn.commit()
        _log_history(domain, None, None, None, "expired", "manually expired")
    except Exception as e:
        logging.warning(f"[site_learning] expire_pattern: {e}")
    finally:
        conn.close()


def mark_pattern_stable(domain: str, notes: str = ""):
    """Promote a pattern to 'stable' + verified_by='manual'."""
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    conn = _conn()
    try:
        conn.execute("""
            UPDATE site_patterns
            SET selector_type='stable', verified_by='manual',
                notes=?, updated_at=?
            WHERE domain=?
        """, (notes, now, domain))
        conn.commit()
    except Exception as e:
        logging.warning(f"[site_learning] mark_pattern_stable: {e}")
    finally:
        conn.close()


def get_all_patterns() -> list:
    """Return all site patterns for the library UI."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM site_patterns ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logging.warning(f"[site_learning] get_all_patterns: {e}")
        return []
    finally:
        conn.close()


def get_pattern_stats() -> dict:
    """Summary stats for the site library UI."""
    conn = _conn()
    try:
        row = conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN selector_type='stable' THEN 1 END) AS stable,
                SUM(CASE WHEN selector_type='fragile' THEN 1 END) AS fragile,
                SUM(CASE WHEN fail_count >= 3 THEN 1 END) AS failed,
                AVG(quality_score) AS avg_quality
            FROM site_patterns
        """).fetchone()
        return dict(row) if row else {}
    except Exception as e:
        logging.warning(f"[site_learning_service.get_pattern_stats] DB query failed: {e}")
        return {}
    finally:
        conn.close()


def get_expiring_soon(days: int = 7) -> list:
    """Return fragile patterns expiring within `days` days."""
    now = datetime.now()
    cutoff = (now - timedelta(days=FRAGILE_EXPIRY_DAYS - days)).isoformat()
    conn = _conn()
    try:
        rows = conn.execute("""
            SELECT * FROM site_patterns
            WHERE selector_type='fragile'
              AND last_success_at IS NOT NULL
              AND last_success_at < ?
        """, (cutoff,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logging.warning(f"[site_learning_service.get_expiring_soon] DB query failed: {e}")
        return []
    finally:
        conn.close()


# ── INTERNAL HELPERS ──────────────────────────────────────────────────────────

def _log_history(domain: str, card_selector, confidence, quality_score,
                 outcome: str, failure_reason: str = None, leads_found: int = 0):
    conn = _conn()
    try:
        conn.execute("""
            INSERT INTO site_pattern_history
                (domain, card_selector, confidence, quality_score,
                 outcome, failure_reason, leads_found)
            VALUES (?,?,?,?,?,?,?)
        """, (domain, card_selector, confidence, quality_score,
              outcome, failure_reason, leads_found))
        conn.commit()
    except Exception as e:
        logging.warning(f"[site_learning] _log_history: {e}")
    finally:
        conn.close()


# ── UPDATED CLAUDE PROMPT ─────────────────────────────────────────────────────

SELECTOR_PROMPT_ADDENDUM = """
SELECTOR PRIORITY (most important first):
1. data-test / data-testid attributes — these survive deployments. ALWAYS prefer these.
   Example: [data-testid="attendee-card"], [data-test="participant"]
2. Semantic/structural selectors — article, li[class], .speaker-card etc.
3. If you ONLY see obfuscated class names (css-xxxxx, sc-xxxxx, styled-components hashes),
   use structural selectors instead: article, li:has(h2 + p), div:has(img + h3), etc.
   NEVER return obfuscated class names as the primary selector.

CONFIDENCE RULES:
- Return confidence: 0.9 if you found data-test/data-testid selectors
- Return confidence: 0.7 if you used structural/semantic selectors
- Return confidence: 0.5 or less if you are guessing or not sure
- If you cannot find a reliable selector with confidence >= 0.7:
  return confidence: 0.3 and explain in the 'reasoning' field. DO NOT guess.

REQUIRED OUTPUT FIELDS:
{
  "card_selector": "...",        // the main card/item selector
  "name_selector": "...",        // selector for person's name within the card
  "title_selector": "...",       // selector for job title
  "company_selector": "...",     // selector for company name
  "pagination_type": "...",      // load_more | numbered | infinite | url_param | none
  "next_button_selector": "...", // selector for next/load-more button (if applicable)
  "confidence": 0.0,             // 0.0 – 1.0
  "selector_confidence": 0.0,    // confidence specifically in the selector (separate)
  "reasoning": "..."             // explain your logic — logged for debugging
}
"""
