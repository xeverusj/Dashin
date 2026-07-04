"""
services/lead_service.py — Dashin Research Platform V2
Full lead lifecycle: save, deduplicate, enrich, flag, assign, archive.
All operations scoped to org_id.
"""

import re
import logging
from datetime import datetime, timezone
from core.db import get_connection


# ── NORMALISATION ─────────────────────────────────────────────────────────────

def make_key(text: str) -> str:
    """Normalise a name or company to a dedup key."""
    if not text:
        return ""
    t = text.lower().strip()
    t = re.sub(r'\b(inc|ltd|llc|corp|co|the|and|&|plc|gmbh|sas|bv|ag)\b', '', t)
    t = re.sub(r'[^a-z0-9]', '', t)
    return t


def get_or_create_company(conn, org_id: int, company_name: str) -> int | None:
    """Get or create a company record. Returns company_id."""
    if not company_name or not company_name.strip():
        return None
    name_key = make_key(company_name)
    if not name_key:
        return None
    existing = conn.execute(
        "SELECT id FROM companies WHERE org_id=? AND name_key=?",
        (org_id, name_key)
    ).fetchone()
    if existing:
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO companies (org_id, name, name_key) VALUES (?,?,?)",
        (org_id, company_name.strip(), name_key)
    )
    return cur.lastrowid


# ── PERSONA DETECTION ─────────────────────────────────────────────────────────

PERSONA_RULES = {
    "Decision Maker": [
        "ceo","cto","coo","cfo","cmo","ciso","founder","co-founder",
        "owner","president","managing director","md","chief executive",
        "chief technology","chief operating","chief financial",
        "chief marketing","vp","vice president","head of","director",
        "partner","principal",
    ],
    "Senior Influencer": [
        "senior manager","senior director","lead","principal engineer",
        "general manager","regional manager","country manager",
        "global head","group head","executive director",
    ],
    "Influencer": [
        "manager","supervisor","team lead","product manager",
        "project manager","account manager","business development",
        "sales manager","marketing manager","operations manager",
    ],
    "IC": [
        "engineer","developer","analyst","consultant","specialist",
        "associate","coordinator","executive","advisor","architect",
        "designer","researcher","scientist","technician",
    ],
}


def classify_persona(title: str, org_id: int = None) -> str:
    """
    Classify a job title into a persona.
    Checks learned mappings first, then rule-based.
    """
    if not title:
        return "Unknown"

    # Check learned mappings
    if org_id:
        try:
            from services.learning_service import get_persona_for_title
            learned = get_persona_for_title(title, org_id)
            if learned:
                return learned
        except Exception as e:
            logging.warning(f"[lead_service.classify_persona] Learning service lookup failed: {e}")

    title_lower = title.lower()
    for persona, keywords in PERSONA_RULES.items():
        for kw in keywords:
            if kw in title_lower:
                return persona
    return "Unknown"


# ── CORE LEAD OPERATIONS ──────────────────────────────────────────────────────

def save_lead(
    org_id:       int,
    full_name:    str,
    company_name: str = "",
    title:        str = "",
    attendee_type:str = "",
    tags:         str = "",
    event_name:   str = "",
    event_url:    str = "",
    category:     str = "",
    layout:       str = "",
    session_id:   str = "",
) -> tuple:
    """
    Save a lead to the inventory. Deduplicates by name_key + company_id.
    Returns (lead_id, is_new: bool)
    """
    if not full_name or not full_name.strip():
        return None, False

    name_key   = make_key(full_name)
    if len(name_key) < 2:
        return None, False

    persona    = classify_persona(title, org_id)
    now        = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    conn       = get_connection()

    company_id = get_or_create_company(conn, org_id, company_name)

    existing   = conn.execute("""
        SELECT id, times_seen FROM leads
        WHERE org_id=? AND name_key=?
          AND (company_id=? OR (company_id IS NULL AND ? IS NULL))
    """, (org_id, name_key, company_id, company_id)).fetchone()

    if existing:
        conn.execute("""
            UPDATE leads
            SET times_seen=?, last_seen_at=?
            WHERE id=?
        """, (existing["times_seen"] + 1, now, existing["id"]))
        lead_id = existing["id"]
        is_new  = False
    else:
        cur = conn.execute("""
            INSERT INTO leads
                (org_id, full_name, name_key, title, company_id,
                 persona, attendee_type, tags, status,
                 times_seen, first_seen_at, last_seen_at)
            VALUES (?,?,?,?,?,?,?,?,'new',1,?,?)
        """, (org_id, full_name.strip(), name_key, title,
              company_id, persona, attendee_type, tags, now, now))
        lead_id = cur.lastrowid
        is_new  = True

    # Record appearance
    if event_name or event_url or session_id:
        conn.execute("""
            INSERT INTO lead_appearances
                (lead_id, event_name, event_url, category,
                 layout, scraped_at, session_id)
            VALUES (?,?,?,?,?,?,?)
        """, (lead_id, event_name, event_url, category,
              layout, now, session_id))

    conn.commit()
    conn.close()
    return lead_id, is_new


def get_lead(lead_id: int, org_id: int) -> dict | None:
    """Get a single lead with enrichment and company data."""
    conn = get_connection()
    row  = conn.execute("""
        SELECT l.*,
               co.name     AS company_name,
               e.email, e.phone, e.linkedin_url,
               e.country, e.industry AS enrich_industry,
               e.company_size, e.notes AS enrich_notes,
               e.minutes_spent, e.enriched_at,
               u.name      AS enriched_by_name
        FROM leads l
        LEFT JOIN companies  co ON co.id = l.company_id
        LEFT JOIN enrichment e  ON e.lead_id = l.id
        LEFT JOIN users      u  ON u.id = e.enriched_by
        WHERE l.id=? AND l.org_id=?
    """, (lead_id, org_id)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_leads(
    org_id:    int,
    status:    str  = None,
    persona:   str  = None,
    search:    str  = None,
    limit:     int  = 200,
    offset:    int  = 0,
) -> list:
    """Get leads for an org with optional filters."""
    conn   = get_connection()
    q      = """
        SELECT l.*,
               co.name AS company_name,
               e.email, e.linkedin_url, e.country
        FROM leads l
        LEFT JOIN companies  co ON co.id = l.company_id
        LEFT JOIN enrichment e  ON e.lead_id = l.id
        WHERE l.org_id=?
    """
    params = [org_id]
    if status:
        q += " AND l.status=?";  params.append(status)
    if persona:
        q += " AND l.persona=?"; params.append(persona)
    q += " ORDER BY l.last_seen_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]

    rows = []
    if search:
        # Try FTS5 first
        fts_q = q.replace("WHERE l.org_id=?",
                           "WHERE l.org_id=? AND l.id IN "
                           "(SELECT rowid FROM leads_fts WHERE leads_fts MATCH ?)")
        fts_params = [org_id, search.replace('"', '""')]
        if status:
            fts_params.append(status)
        if persona:
            fts_params.append(persona)
        fts_params += [limit, offset]
        try:
            rows = conn.execute(fts_q, fts_params).fetchall()
        except Exception:
            pass  # FTS5 unavailable or query error — fall through to LIKE

    if not rows:
        # Build query fresh if FTS failed or no search
        q2 = """
            SELECT l.*,
                   co.name AS company_name,
                   e.email, e.linkedin_url, e.country
            FROM leads l
            LEFT JOIN companies  co ON co.id = l.company_id
            LEFT JOIN enrichment e  ON e.lead_id = l.id
            WHERE l.org_id=?
        """
        p2 = [org_id]
        if status:
            q2 += " AND l.status=?";  p2.append(status)
        if persona:
            q2 += " AND l.persona=?"; p2.append(persona)
        if search:
            q2 += " AND (l.full_name LIKE ? OR co.name LIKE ? OR l.title LIKE ?)"
            s   = f"%{search}%"
            p2 += [s, s, s]
        q2 += " ORDER BY l.last_seen_at DESC LIMIT ? OFFSET ?"
        p2 += [limit, offset]
        rows = conn.execute(q2, p2).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def enrich_lead(
    lead_id:      int,
    org_id:       int,
    enriched_by:  int,
    email:        str   = None,
    phone:        str   = None,
    linkedin_url: str   = None,
    country:      str   = None,
    industry:     str   = None,
    company_size: str   = None,
    notes:        str   = None,
    minutes_spent:float = 0,
    auto_flag:    bool  = True,
) -> dict:
    """
    Enrich a lead with contact data.
    Automatically flags issues if auto_flag=True.
    Returns {enriched: bool, flags: list}
    """
    conn = get_connection()
    now  = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    flags = []

    try:
        # Get company name for domain mismatch check
        lead = conn.execute("""
            SELECT l.*, co.name AS company_name
            FROM leads l
            LEFT JOIN companies co ON co.id = l.company_id
            WHERE l.id=? AND l.org_id=?
        """, (lead_id, org_id)).fetchone()

        if not lead:
            conn.close()
            return {"enriched": False, "flags": []}

        # Begin atomic transaction
        conn.execute("BEGIN")

        # Upsert enrichment
        conn.execute("""
            INSERT INTO enrichment
                (lead_id, email, phone, linkedin_url, country,
                 industry, company_size, enriched_by, enriched_at,
                 notes, minutes_spent)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(lead_id) DO UPDATE SET
                email         = COALESCE(excluded.email,         email),
                phone         = COALESCE(excluded.phone,         phone),
                linkedin_url  = COALESCE(excluded.linkedin_url,  linkedin_url),
                country       = COALESCE(excluded.country,       country),
                industry      = COALESCE(excluded.industry,      industry),
                company_size  = COALESCE(excluded.company_size,  company_size),
                enriched_by   = excluded.enriched_by,
                enriched_at   = excluded.enriched_at,
                notes         = COALESCE(excluded.notes,         notes),
                minutes_spent = minutes_spent + excluded.minutes_spent
        """, (lead_id, email, phone, linkedin_url, country,
              industry, company_size, enriched_by, now, notes, minutes_spent))

        # Update lead status
        new_status = "enriched" if email else "no_email"
        conn.execute(
            "UPDATE leads SET status=?, enriched_at=? WHERE id=?",
            (new_status, now, lead_id)
        )

        # Run auto-flags inside the same transaction
        if auto_flag and email:
            from services.flag_service import flag_lead
            flags = flag_lead(
                lead_id      = lead_id,
                org_id       = org_id,
                email        = email,
                company_name = lead["company_name"],
                auto_save    = True,
                conn         = conn,   # share the open transaction
            )

        conn.commit()

    except Exception as e:
        logging.warning(f"[lead_service.enrich_lead] Transaction failed, rolling back: {e}")
        try:
            conn.rollback()
        except Exception as rb_err:
            logging.warning(f"[lead_service.enrich_lead] Rollback failed: {rb_err}")
        conn.close()
        return {"enriched": False, "flags": []}

    conn.close()

    # Update learning benchmarks (non-critical, runs outside transaction)
    try:
        from services.learning_service import update_org_benchmarks
        update_org_benchmarks(org_id)
    except Exception as e:
        logging.warning(f"[lead_service.enrich_lead] benchmark update failed: {e}")

    return {"enriched": True, "flags": flags}


def reject_lead(lead_id: int, org_id: int,
                rejected_by: int, reason: str, note: str = ""):
    """Reject a lead with a reason."""
    conn = get_connection()
    now  = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    conn.execute("""
        INSERT INTO rejections (lead_id, reason, note, rejected_by, rejected_at)
        VALUES (?,?,?,?,?)
    """, (lead_id, reason, note, rejected_by, now))
    conn.execute(
        "UPDATE leads SET status='archived' WHERE id=? AND org_id=?",
        (lead_id, org_id)
    )
    conn.commit()
    conn.close()


def archive_leads(org_id: int, lead_ids: list,
                  list_name: str, created_by: int,
                  description: str = "") -> int:
    """Archive a batch of leads into a named list."""
    conn = get_connection()
    now  = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    cur = conn.execute("""
        INSERT INTO archived_lists
            (org_id, name, description, created_by, created_at)
        VALUES (?,?,?,?,?)
    """, (org_id, list_name, description, created_by, now))
    list_id = cur.lastrowid

    conn.executemany("""
        UPDATE leads SET status='archived', archived_at=?,
               archived_list_id=?
        WHERE id=? AND org_id=?
    """, [(now, list_id, lid, org_id) for lid in lead_ids])

    conn.commit()
    conn.close()
    return list_id


def link_to_client(lead_id: int, client_id: int,
                   campaign_id: int = None) -> bool:
    """Record that a lead has been used for a client (prevents reuse)."""
    conn = get_connection()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO lead_usage
                (lead_id, client_id, campaign_id, used_at)
            VALUES (?,?,?,?)
        """, (lead_id, client_id, campaign_id,
              datetime.now(timezone.utc).replace(tzinfo=None).isoformat()))
        conn.execute(
            "UPDATE leads SET status='used', used_at=? WHERE id=?",
            (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), lead_id)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logging.warning(f"[lead_service.mark_lead_used] Failed for lead {lead_id}: {e}")
        conn.close()
        return False


def is_available_for_client(lead_id: int, client_id: int) -> bool:
    """Check if a lead has already been used for this client."""
    conn = get_connection()
    row  = conn.execute("""
        SELECT id FROM lead_usage
        WHERE lead_id=? AND client_id=?
    """, (lead_id, client_id)).fetchone()
    conn.close()
    return row is None


def get_inventory_stats(org_id: int) -> dict:
    """Summary stats for the inventory dashboard."""
    conn = get_connection()
    stats = conn.execute("""
        SELECT
            COUNT(*)                                         AS total,
            SUM(CASE WHEN status='new'       THEN 1 END)    AS new,
            SUM(CASE WHEN status='enriched'  THEN 1 END)    AS enriched,
            SUM(CASE WHEN status='no_email'  THEN 1 END)    AS no_email,
            SUM(CASE WHEN status='used'      THEN 1 END)    AS used,
            SUM(CASE WHEN status='archived'  THEN 1 END)    AS archived,
            SUM(CASE WHEN times_seen > 1     THEN 1 END)    AS seen_multiple,
            COUNT(DISTINCT company_id)                       AS unique_companies
        FROM leads WHERE org_id=?
    """, (org_id,)).fetchone()
    conn.close()
    return dict(stats)


# ── SCRAPE SESSION MANAGEMENT ─────────────────────────────────────────────────

def start_session(
    event_url:  str,
    event_name: str = "",
    category:   str = "",
    layout:     str = "",
    org_id:     int = 1,
) -> str:
    """
    Create a scrape_sessions row with status='running'.
    Returns the session id string so worker.py can reference it.
    """
    import uuid as _uuid
    session_id = f"session_{_uuid.uuid4().hex[:12]}"
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO scrape_sessions
                (id, org_id, event_url, event_name, category, layout,
                 status, started_at)
            VALUES (?,?,?,?,?,?,'running',?)
        """, (session_id, org_id, event_url, event_name or "",
              category or "", layout or "",
              datetime.now(timezone.utc).replace(tzinfo=None).isoformat()))
        conn.commit()
    except Exception as e:
        print(f"  [DB] start_session error: {e}")
    finally:
        conn.close()
    return session_id


def finish_session(
    session_id:  str,
    leads_found: int = 0,
    leads_new:   int = 0,
    leads_dupes: int = 0,
    status:      str = "done",
) -> None:
    """
    Close out a scrape session and record final lead counts.

    status defaults to 'done' (normal completion) but the caller can pass
    'failed' (crashed with an exception) or 'stopped' (user interrupted) so a
    crashed/killed run is never left dangling at 'running' — which is what the
    session dashboard reads to show what's live vs finished.
    """
    conn = get_connection()
    try:
        conn.execute("""
            UPDATE scrape_sessions
            SET status=?,
                leads_found=?,
                leads_new=?,
                leads_dupes=?,
                finished_at=?
            WHERE id=?
        """, (status, leads_found, leads_new, leads_dupes,
              datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), session_id))
        conn.commit()
    except Exception as e:
        print(f"  [DB] finish_session error: {e}")
    finally:
        conn.close()


def get_all_leads_for_export(
    org_id: int,
    status: str = None,
    persona: str = None,
) -> list:
    """
    Return ALL leads for an org without a row-count cap.
    Use only for CSV/Excel exports — not for UI rendering.
    """
    conn = get_connection()
    q = """
        SELECT l.*,
               co.name AS company_name,
               e.email, e.linkedin_url, e.country
        FROM leads l
        LEFT JOIN companies  co ON co.id = l.company_id
        LEFT JOIN enrichment e  ON e.lead_id = l.id
        WHERE l.org_id=?
    """
    params = [org_id]
    if status:
        q += " AND l.status=?"; params.append(status)
    if persona:
        q += " AND l.persona=?"; params.append(persona)
    q += " ORDER BY l.last_seen_at DESC"
    try:
        rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logging.warning(f"[lead_service.get_all_leads_for_export] Query failed: {e}")
        return []
    finally:
        conn.close()


def count_leads(
    org_id: int,
    status: str = None,
    persona: str = None,
    search: str = None,
) -> int:
    """Return the total count of leads matching the given filters (for pagination)."""
    conn = get_connection()
    q = "SELECT COUNT(*) AS n FROM leads l WHERE l.org_id=?"
    params = [org_id]
    if status:
        q += " AND l.status=?"; params.append(status)
    if persona:
        q += " AND l.persona=?"; params.append(persona)
    if search:
        q += " AND (l.full_name LIKE ? OR l.title LIKE ? OR l.company LIKE ?)"
        like = f"%{search}%"
        params += [like, like, like]
    try:
        row = conn.execute(q, params).fetchone()
        return row["n"] if row else 0
    except Exception as e:
        logging.warning(f"[lead_service.count_leads] Query failed: {e}")
        return 0
    finally:
        conn.close()
