"""
core/db.py — Dashin Research Platform — Schema V2
Multi-org SaaS with full role hierarchy, learning system, AI cost tracking.

Run directly to initialise:  python core/db.py
"""

import logging
import os
import sqlite3
from pathlib import Path

# ── ROLE DEFINITIONS PER ORG TYPE ─────────────────────────────────────────────
# Used by access_control.py and admin dashboard Add User form.
# Maps org_type → list of valid roles assignable within that org.
ROLES_BY_ORG_TYPE = {
    'dashin':    ['super_admin', 'org_admin', 'manager'],
    'agency':    ['org_admin', 'manager', 'research_manager',
                  'researcher', 'campaign_manager', 'client_user'],
    'freelance': ['org_admin', 'manager', 'research_manager',
                  'researcher', 'client_user'],
    'client':    ['client_admin', 'client_user'],
}


def _get_db_path():
    if os.getenv('DB_PATH'):
        return Path(os.getenv('DB_PATH'))
    if os.getenv('RAILWAY_ENVIRONMENT') or os.getenv('HOME') == '/home/appuser' or os.getenv('STREAMLIT_SHARING_MODE'):
        return Path('/tmp/dashin.db')
    base = Path(__file__).parent
    path = base / '..' / 'data' / 'system' / 'dashin.db'
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


DB_PATH = _get_db_path()


def _dict_factory(cursor, row):
    """Return rows as plain dicts so .get() works everywhere."""
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = _dict_factory   # dicts, not sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_connection()
    c = conn.cursor()

    # ══════════════════════════════════════════════════════════════════
    # PLATFORM LAYER — Super Admin scope
    # ══════════════════════════════════════════════════════════════════

    c.execute("""
    CREATE TABLE IF NOT EXISTS organisations (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        name                TEXT    NOT NULL,
        slug                TEXT    NOT NULL UNIQUE,   -- used in URLs / display
        tier                TEXT    NOT NULL DEFAULT 'starter',
        -- scraper | starter | growth | agency | enterprise
        ai_budget_usd       REAL    NOT NULL DEFAULT 8.0,
        billing_day         INTEGER DEFAULT 1,         -- day of month billing resets
        max_users           INTEGER DEFAULT 5,
        max_clients         INTEGER DEFAULT 3,
        max_leads           INTEGER DEFAULT 10000,
        is_active           INTEGER DEFAULT 1,
        notes               TEXT,                      -- super admin notes
        created_at          TEXT    DEFAULT (datetime('now')),
        suspended_at        TEXT,
        -- Multi-tenant hierarchy fields
        org_type            TEXT    NOT NULL DEFAULT 'agency',
        -- values: 'dashin' | 'agency' | 'freelance' | 'client'
        parent_org_id       INTEGER REFERENCES organisations(id),
        -- NULL for dashin/top-level; set for client orgs under an agency
        subscription_tier   TEXT    NOT NULL DEFAULT 'free',
        -- values: 'free' | 'starter' | 'growth' | 'enterprise' | 'client_direct'
        subscription_status TEXT    NOT NULL DEFAULT 'active',
        -- values: 'active' | 'suspended' | 'cancelled'
        onboarded_at        TEXT,
        onboarded_by        INTEGER REFERENCES users(id)
        -- the Dashin account manager who created them
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS org_ai_usage (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id          INTEGER NOT NULL REFERENCES organisations(id),
        period_start    TEXT    NOT NULL,          -- billing anniversary date
        period_end      TEXT    NOT NULL,
        tokens_input    INTEGER DEFAULT 0,
        tokens_output   INTEGER DEFAULT 0,
        cost_usd        REAL    DEFAULT 0.0,
        alert_80_sent   INTEGER DEFAULT 0,         -- 1 = 80% email sent
        updated_at      TEXT    DEFAULT (datetime('now')),
        UNIQUE(org_id, period_start)
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS platform_ai_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id          INTEGER REFERENCES organisations(id),
        user_id         INTEGER,                   -- who triggered it
        session_id      TEXT,                      -- scrape session if applicable
        feature         TEXT NOT NULL,             -- scraper | cleaner | other
        model           TEXT,
        tokens_input    INTEGER DEFAULT 0,
        tokens_output   INTEGER DEFAULT 0,
        cost_usd        REAL    DEFAULT 0.0,
        logged_at       TEXT    DEFAULT (datetime('now'))
    )""")

    # ══════════════════════════════════════════════════════════════════
    # ORG LAYER — Org Admin scope
    # ══════════════════════════════════════════════════════════════════

    c.execute("""
    CREATE TABLE IF NOT EXISTS clients (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id          INTEGER NOT NULL REFERENCES organisations(id),
        name            TEXT    NOT NULL,
        industry        TEXT,
        icp_notes       TEXT,
        website         TEXT,
        is_active       INTEGER DEFAULT 1,
        created_at      TEXT    DEFAULT (datetime('now'))
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id              INTEGER NOT NULL REFERENCES organisations(id),
        name                TEXT    NOT NULL,
        email               TEXT    NOT NULL UNIQUE,
        password            TEXT    NOT NULL,
        role                TEXT    DEFAULT 'researcher',
        -- super_admin | org_admin | manager | research_manager |
        -- researcher | campaign_manager | client_admin | client_user
        client_id           INTEGER REFERENCES clients(id),
        -- set for client_admin and client_user roles
        hourly_rate         REAL    DEFAULT 0.0,
        is_active           INTEGER DEFAULT 1,
        must_reset_password INTEGER DEFAULT 0,
        last_login          TEXT,
        onboarded_at        TEXT,
        created_at          TEXT    DEFAULT (datetime('now'))
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS invite_tokens (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id          INTEGER NOT NULL REFERENCES organisations(id),
        token           TEXT    NOT NULL UNIQUE,
        client_id       INTEGER REFERENCES clients(id),
        role            TEXT    NOT NULL DEFAULT 'client_user',
        email           TEXT,                      -- pre-fill email if known
        created_by      INTEGER REFERENCES users(id),
        used_at         TEXT,
        used_by         INTEGER REFERENCES users(id),
        expires_at      TEXT    NOT NULL,
        created_at      TEXT    DEFAULT (datetime('now'))
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS notifications (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id          INTEGER REFERENCES organisations(id),
        user_id         INTEGER REFERENCES users(id),
        client_id       INTEGER REFERENCES clients(id),
        type            TEXT    NOT NULL,
        -- campaign_ready | task_assigned | list_approved | flag_raised |
        -- ai_limit_warning | meeting_booked | new_note
        title           TEXT    NOT NULL,
        body            TEXT,
        link_to         TEXT,
        is_read         INTEGER DEFAULT 0,
        created_at      TEXT    DEFAULT (datetime('now'))
    )""")

    # ══════════════════════════════════════════════════════════════════
    # LEAD INVENTORY
    # ══════════════════════════════════════════════════════════════════

    c.execute("""
    CREATE TABLE IF NOT EXISTS companies (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id          INTEGER NOT NULL REFERENCES organisations(id),
        name            TEXT    NOT NULL,
        name_key        TEXT    NOT NULL,
        industry        TEXT,                       -- Module D1: categorization
        domain          TEXT,                       -- for email-domain matching (D3)
        created_at      TEXT    DEFAULT (datetime('now')),
        UNIQUE(org_id, name_key)
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS archived_lists (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id          INTEGER NOT NULL REFERENCES organisations(id),
        name            TEXT    NOT NULL,
        industry        TEXT,
        description     TEXT,
        created_by      INTEGER REFERENCES users(id),
        created_at      TEXT    DEFAULT (datetime('now'))
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS leads (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id          INTEGER NOT NULL REFERENCES organisations(id),
        full_name       TEXT    NOT NULL,
        name_key        TEXT    NOT NULL,
        title           TEXT,
        company_id      INTEGER REFERENCES companies(id),
        persona         TEXT    DEFAULT 'Unknown',
        -- Decision Maker | Senior Influencer | Influencer | IC | Unknown
        attendee_type   TEXT,
        tags            TEXT,
        source_type     TEXT    DEFAULT 'event',
        -- event | clutch | csv_upload | manual
        status          TEXT    DEFAULT 'new',
        -- new | assigned | in_progress | enriched | no_email |
        -- contacted | waiting | responded | interested |
        -- meeting_requested | booked | not_interested | no_show | archived
        times_seen          INTEGER DEFAULT 1,
        first_seen_at       TEXT    DEFAULT (datetime('now')),
        last_seen_at        TEXT    DEFAULT (datetime('now')),
        enriched_at         TEXT,
        used_at             TEXT,
        archived_at         TEXT,
        archived_list_id    INTEGER REFERENCES archived_lists(id),
        released_to_client  INTEGER DEFAULT 0,
        -- 0 = in agency inventory only; 1 = visible to the assigned client
        scraped_at          TEXT    DEFAULT (datetime('now')),
        UNIQUE(org_id, name_key, company_id)
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS lead_appearances (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        lead_id         INTEGER NOT NULL REFERENCES leads(id),
        event_name      TEXT,
        event_url       TEXT,
        category        TEXT,
        layout          TEXT,
        scraped_at      TEXT    DEFAULT (datetime('now')),
        session_id      TEXT
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS enrichment (
        lead_id         INTEGER PRIMARY KEY REFERENCES leads(id),
        email           TEXT,
        phone           TEXT,
        linkedin_url    TEXT,
        country         TEXT,
        industry        TEXT,
        company_size    TEXT,
        enriched_by     INTEGER REFERENCES users(id),
        enriched_at     TEXT,
        notes           TEXT,
        minutes_spent   REAL    DEFAULT 0
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS lead_usage (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        lead_id         INTEGER NOT NULL REFERENCES leads(id),
        client_id       INTEGER NOT NULL REFERENCES clients(id),
        campaign_id     INTEGER REFERENCES campaigns(id),
        used_at         TEXT    DEFAULT (datetime('now')),
        UNIQUE(lead_id, client_id)
    )""")

    # ══════════════════════════════════════════════════════════════════
    # AUTO-FLAGS
    # ══════════════════════════════════════════════════════════════════

    c.execute("""
    CREATE TABLE IF NOT EXISTS lead_flags (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        lead_id         INTEGER NOT NULL REFERENCES leads(id),
        org_id          INTEGER NOT NULL REFERENCES organisations(id),
        flag_type       TEXT    NOT NULL,
        -- personal_email | duplicate | invalid_email_format |
        -- role_based_email | domain_mismatch
        severity        TEXT    DEFAULT 'warning',  -- warning | critical
        detail          TEXT,                       -- e.g. "gmail.com domain"
        auto_flagged    INTEGER DEFAULT 1,           -- 1=system, 0=manual
        flagged_at      TEXT    DEFAULT (datetime('now')),
        resolved        INTEGER DEFAULT 0,
        resolved_by     INTEGER REFERENCES users(id),
        resolved_at     TEXT,
        resolution_note TEXT
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS rejections (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        lead_id         INTEGER NOT NULL REFERENCES leads(id),
        reason          TEXT    NOT NULL,
        -- wrong_persona | duplicate | bounced_email | personal_email |
        -- out_of_market | incomplete_data | wrong_company_size |
        -- wrong_geography | other
        note            TEXT,
        rejected_by     INTEGER REFERENCES users(id),
        rejected_at     TEXT    DEFAULT (datetime('now'))
    )""")

    # ══════════════════════════════════════════════════════════════════
    # RESEARCH OPERATIONS
    # ══════════════════════════════════════════════════════════════════

    c.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id          INTEGER NOT NULL REFERENCES organisations(id),
        title           TEXT    NOT NULL,
        task_type       TEXT    NOT NULL,
        -- enrich_batch | find_linkedin | verify_emails | build_list
        description     TEXT,
        priority        TEXT    DEFAULT 'normal',   -- urgent | normal | low
        status          TEXT    DEFAULT 'pending',
        -- pending | in_progress | submitted | approved | rejected | done
        assigned_to     INTEGER REFERENCES users(id),
        assigned_by     INTEGER REFERENCES users(id),
        assigned_at     TEXT    DEFAULT (datetime('now')),
        deadline        TEXT,
        target_count    INTEGER DEFAULT 0,          -- quota target
        completed_count INTEGER DEFAULT 0,          -- progress
        started_at      TEXT,
        submitted_at    TEXT,
        approved_at     TEXT,
        approved_by     INTEGER REFERENCES users(id),
        rejection_note  TEXT,
        campaign_id     INTEGER REFERENCES campaigns(id),
        archived_list_id INTEGER REFERENCES archived_lists(id),
        created_at      TEXT    DEFAULT (datetime('now'))
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS task_reassignments (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id         INTEGER NOT NULL REFERENCES tasks(id),
        from_user       INTEGER REFERENCES users(id),
        to_user         INTEGER REFERENCES users(id),
        reason          TEXT,
        reassigned_at   TEXT    DEFAULT (datetime('now'))
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS research_quotas (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id          INTEGER NOT NULL REFERENCES organisations(id),
        researcher_id   INTEGER NOT NULL REFERENCES users(id),
        set_by          INTEGER REFERENCES users(id),
        week_start      TEXT    NOT NULL,
        target_leads    INTEGER DEFAULT 0,
        target_enriched INTEGER DEFAULT 0,
        notes           TEXT,
        created_at      TEXT    DEFAULT (datetime('now')),
        UNIQUE(org_id, researcher_id, week_start)
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS scrape_sessions (
        id              TEXT    PRIMARY KEY,
        org_id          INTEGER REFERENCES organisations(id),
        event_url       TEXT,
        event_name      TEXT,
        category        TEXT,
        layout          TEXT,
        status          TEXT    DEFAULT 'running',
        leads_found     INTEGER DEFAULT 0,
        leads_new       INTEGER DEFAULT 0,
        leads_dupes     INTEGER DEFAULT 0,
        ai_tokens_used  INTEGER DEFAULT 0,
        ai_cost_usd     REAL    DEFAULT 0.0,
        pattern_used    INTEGER DEFAULT 0,          -- 1 = used saved pattern (no AI)
        started_at      TEXT    DEFAULT (datetime('now')),
        finished_at     TEXT
    )""")

    # ══════════════════════════════════════════════════════════════════
    # CAMPAIGNS
    # ══════════════════════════════════════════════════════════════════

    c.execute("""
    CREATE TABLE IF NOT EXISTS campaigns (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id          INTEGER NOT NULL REFERENCES organisations(id),
        name            TEXT    NOT NULL,
        client_id       INTEGER REFERENCES clients(id),
        description     TEXT,
        target_count    INTEGER DEFAULT 0,
        created_by      INTEGER REFERENCES users(id),
        created_at      TEXT    DEFAULT (datetime('now')),
        status          TEXT    DEFAULT 'building',
        -- building | active | paused | ready | completed | closed
        is_visible_to_client INTEGER DEFAULT 0,
        marked_ready_by INTEGER REFERENCES users(id),
        marked_ready_at TEXT,
        exported_at     TEXT,
        lead_count      INTEGER DEFAULT 0
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS campaign_leads (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id     INTEGER NOT NULL REFERENCES campaigns(id),
        lead_id         INTEGER NOT NULL REFERENCES leads(id),
        is_reused       INTEGER DEFAULT 0,
        added_at        TEXT    DEFAULT (datetime('now')),
        crm_status      TEXT    DEFAULT 'new',
        -- new | contacted | waiting | responded | interested |
        -- meeting_requested | booked | not_interested | no_show
        next_step       TEXT,
        outreach_from   TEXT,                       -- which mailbox sent
        meeting_date    TEXT,
        notes           TEXT,
        last_updated_by INTEGER REFERENCES users(id),
        last_updated_at TEXT,
        UNIQUE(campaign_id, lead_id)
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS crm_updates (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id     INTEGER NOT NULL REFERENCES campaigns(id),
        lead_id         INTEGER NOT NULL REFERENCES leads(id),
        old_status      TEXT,
        new_status      TEXT    NOT NULL,
        note            TEXT,
        meeting_date    TEXT,
        changed_by      INTEGER REFERENCES users(id),
        changed_by_role TEXT,
        changed_at      TEXT    DEFAULT (datetime('now'))
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS campaign_weekly_stats (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id     INTEGER NOT NULL REFERENCES campaigns(id),
        week_label      TEXT    NOT NULL,           -- e.g. "12-19 Jan"
        week_start      TEXT,
        week_end        TEXT,
        cold_emails_sent INTEGER DEFAULT 0,
        followups_sent  INTEGER DEFAULT 0,
        total_sent      INTEGER DEFAULT 0,
        opens           INTEGER DEFAULT 0,
        open_rate       REAL    DEFAULT 0,
        responded       INTEGER DEFAULT 0,
        interested      INTEGER DEFAULT 0,
        scheduled       INTEGER DEFAULT 0,
        meetings_done   INTEGER DEFAULT 0,
        entered_by      INTEGER REFERENCES users(id),
        entered_at      TEXT    DEFAULT (datetime('now')),
        UNIQUE(campaign_id, week_label)
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS campaign_files (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id     INTEGER NOT NULL REFERENCES campaigns(id),
        org_id          INTEGER NOT NULL REFERENCES organisations(id),
        file_name       TEXT    NOT NULL,
        file_type       TEXT    DEFAULT 'other',
        -- template | case_study | brief | report | other
        file_data       BLOB,
        file_size       INTEGER,
        uploaded_by     INTEGER REFERENCES users(id),
        uploaded_at     TEXT    DEFAULT (datetime('now')),
        is_template     INTEGER DEFAULT 0,
        approval_status TEXT    DEFAULT 'pending',
        -- pending | approved | rejected | changes_requested
        approved_by     INTEGER REFERENCES users(id),
        approved_at     TEXT,
        approval_note   TEXT
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS campaign_templates (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id     INTEGER NOT NULL REFERENCES campaigns(id),
        org_id          INTEGER NOT NULL REFERENCES organisations(id),
        version         INTEGER DEFAULT 1,
        subject         TEXT,
        body            TEXT    NOT NULL,
        sequence_step   INTEGER DEFAULT 1,
        -- 1=cold, 2=follow-up 1, 3=follow-up 2 etc.
        created_by      INTEGER REFERENCES users(id),
        created_at      TEXT    DEFAULT (datetime('now')),
        approval_status TEXT    DEFAULT 'pending',
        approved_by     INTEGER REFERENCES users(id),
        approved_at     TEXT,
        client_notes    TEXT,
        internal_notes  TEXT
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS campaign_notes (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id     INTEGER NOT NULL REFERENCES campaigns(id),
        author_id       INTEGER NOT NULL REFERENCES users(id),
        author_role     TEXT,
        note            TEXT    NOT NULL,
        is_internal     INTEGER DEFAULT 0,  -- 1 = only visible to org, not client
        created_at      TEXT    DEFAULT (datetime('now'))
    )""")

    # ══════════════════════════════════════════════════════════════════
    # ESTIMATOR / COST TRACKING
    # ══════════════════════════════════════════════════════════════════

    c.execute("""
    CREATE TABLE IF NOT EXISTS weekly_cost_snapshots (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id          INTEGER NOT NULL REFERENCES organisations(id),
        week_start      TEXT    NOT NULL,
        week_end        TEXT    NOT NULL,
        fresh_leads_count   INTEGER DEFAULT 0,
        fresh_leads_cost    REAL    DEFAULT 0,
        reused_leads_count  INTEGER DEFAULT 0,
        reused_leads_saved  REAL    DEFAULT 0,
        researcher_breakdown TEXT,              -- JSON
        snapshot_at     TEXT    DEFAULT (datetime('now'))
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS estimator_metrics (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id          INTEGER NOT NULL REFERENCES organisations(id),
        researcher_id   INTEGER REFERENCES users(id),
        avg_enrichment_mins  REAL,
        rejection_rate       REAL,
        personal_email_rate  REAL,
        bounce_rate          REAL,
        usable_yield_rate    REAL,
        leads_delivered      INTEGER DEFAULT 0,
        leads_target         INTEGER DEFAULT 0,
        measured_at     TEXT    DEFAULT (datetime('now'))
    )""")

    # ══════════════════════════════════════════════════════════════════
    # LEARNING SYSTEM
    # ══════════════════════════════════════════════════════════════════

    c.execute("""
    CREATE TABLE IF NOT EXISTS layout_patterns (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id          INTEGER REFERENCES organisations(id),
        -- NULL = global pattern shared across all orgs
        domain          TEXT    NOT NULL,
        layout_type     TEXT    NOT NULL,
        -- grid | list | table | card | generic
        selectors       TEXT,                   -- JSON: {name, title, company, ...}
        pagination_type TEXT,
        -- load_more | numbered | infinite | none
        success_count   INTEGER DEFAULT 1,
        fail_count      INTEGER DEFAULT 0,
        confidence      REAL    DEFAULT 1.0,    -- success/(success+fail)
        last_used       TEXT    DEFAULT (datetime('now')),
        last_verified   TEXT    DEFAULT (datetime('now')),
        created_at      TEXT    DEFAULT (datetime('now')),
        UNIQUE(domain, layout_type, org_id)
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS learned_mappings (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id          INTEGER REFERENCES organisations(id),
        -- NULL = global mapping
        mapping_type    TEXT    NOT NULL,
        -- column_synonym | persona_title | domain_whitelist |
        -- domain_blacklist | flag_exception
        key             TEXT    NOT NULL,
        value           TEXT    NOT NULL,
        confidence      REAL    DEFAULT 1.0,
        usage_count     INTEGER DEFAULT 1,
        last_used       TEXT    DEFAULT (datetime('now')),
        created_at      TEXT    DEFAULT (datetime('now')),
        UNIQUE(org_id, mapping_type, key)
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS org_benchmarks (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id          INTEGER NOT NULL REFERENCES organisations(id),
        metric          TEXT    NOT NULL,
        -- avg_enrichment_mins | avg_rejection_rate | avg_personal_email_rate
        -- avg_open_rate | avg_response_rate | avg_meetings_per_100_leads
        -- avg_bounce_rate | avg_scrape_leads_per_session
        value           REAL    NOT NULL,
        sample_size     INTEGER DEFAULT 0,
        calculated_at   TEXT    DEFAULT (datetime('now')),
        UNIQUE(org_id, metric)
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS learning_events (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id          INTEGER REFERENCES organisations(id),
        event_type      TEXT    NOT NULL,
        -- pattern_success | pattern_fail | column_mapped | persona_corrected
        -- flag_confirmed | flag_dismissed | scrape_completed
        entity_type     TEXT,                   -- lead | session | task
        entity_id       INTEGER,
        old_value       TEXT,
        new_value       TEXT,
        triggered_by    INTEGER REFERENCES users(id),
        -- NULL = system-triggered
        logged_at       TEXT    DEFAULT (datetime('now'))
    )""")

    # ══════════════════════════════════════════════════════════════════
    # SITE PATTERN LIBRARY — AI learning agent
    # ══════════════════════════════════════════════════════════════════

    c.execute("""
    CREATE TABLE IF NOT EXISTS site_patterns (
        domain              TEXT    PRIMARY KEY,
        card_selector       TEXT    NOT NULL,
        name_selector       TEXT,
        title_selector      TEXT,
        company_selector    TEXT,
        extra_selector      TEXT,
        pagination_type     TEXT    DEFAULT 'url_param',
        next_button_selector TEXT,
        confidence          REAL    DEFAULT 0.0,
        selector_type       TEXT    DEFAULT 'fragile',
        -- 'stable' (data-test/data-testid) or 'fragile' (class-based)
        quality_score       REAL    DEFAULT 0.0,
        last_quality_check  TEXT,
        last_success_at     TEXT,
        last_attempt_at     TEXT,
        success_count       INTEGER DEFAULT 0,
        fail_count          INTEGER DEFAULT 0,
        verified_by         TEXT    DEFAULT 'ai',   -- 'ai' or 'manual'
        notes               TEXT,
        created_at          TEXT    DEFAULT (datetime('now')),
        updated_at          TEXT    DEFAULT (datetime('now'))
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS site_pattern_history (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        domain          TEXT    NOT NULL,
        card_selector   TEXT,
        confidence      REAL,
        quality_score   REAL,
        outcome         TEXT,
        -- 'promoted' | 'rejected' | 'expired' | 're-learned'
        failure_reason  TEXT,
        leads_found     INTEGER,
        created_at      TEXT    DEFAULT (datetime('now'))
    )""")

    # ══════════════════════════════════════════════════════════════════
    # FREELANCER → CLIENT ASSIGNMENTS
    # ══════════════════════════════════════════════════════════════════

    c.execute("""
    CREATE TABLE IF NOT EXISTS freelancer_client_assignments (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        freelancer_org_id INTEGER NOT NULL REFERENCES organisations(id),
        client_org_id     INTEGER NOT NULL REFERENCES organisations(id),
        assigned_by       INTEGER NOT NULL REFERENCES users(id),
        assigned_at       TEXT    DEFAULT (datetime('now')),
        active            INTEGER DEFAULT 1,
        UNIQUE(freelancer_org_id, client_org_id)
    )""")

    # ══════════════════════════════════════════════════════════════════
    # SUBSCRIPTION TIERS
    # ══════════════════════════════════════════════════════════════════

    c.execute("""
    CREATE TABLE IF NOT EXISTS subscription_tiers (
        tier                TEXT PRIMARY KEY,
        display_name        TEXT NOT NULL,
        max_users           INTEGER,        -- NULL = unlimited
        max_clients         INTEGER,        -- NULL = unlimited
        max_leads_per_month INTEGER,        -- NULL = unlimited
        can_enrich          INTEGER DEFAULT 1,
        can_run_campaigns   INTEGER DEFAULT 1,
        can_use_ai          INTEGER DEFAULT 1,
        white_label_portal  INTEGER DEFAULT 0,
        api_access          INTEGER DEFAULT 0,
        price_gbp_monthly   REAL    DEFAULT 0
    )""")

    # Seed subscription tiers (INSERT OR IGNORE = idempotent)
    _tiers = [
        ('free',          'Free',          1,    0,    200,   0, 0, 0, 0, 0, 0),
        ('starter',       'Starter',       5,    2,    1000,  1, 1, 0, 0, 0, 149),
        ('growth',        'Growth',        15,   10,   5000,  1, 1, 1, 0, 0, 399),
        ('enterprise',    'Enterprise',    None, None, None,  1, 1, 1, 1, 1, 999),
        ('client_direct', 'Client Direct', 3,    None, None,  0, 0, 0, 0, 0, 49),
    ]
    c.executemany("""
        INSERT OR IGNORE INTO subscription_tiers
            (tier, display_name, max_users, max_clients, max_leads_per_month,
             can_enrich, can_run_campaigns, can_use_ai,
             white_label_portal, api_access, price_gbp_monthly)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, _tiers)

    # ══════════════════════════════════════════════════════════════════
    # INDEXES — keep queries fast as data grows
    # ══════════════════════════════════════════════════════════════════

    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_leads_org        ON leads(org_id)",
        "CREATE INDEX IF NOT EXISTS idx_leads_status     ON leads(org_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_leads_name_key   ON leads(name_key)",
        "CREATE INDEX IF NOT EXISTS idx_leads_company    ON leads(company_id)",
        "CREATE INDEX IF NOT EXISTS idx_enrich_lead      ON enrichment(lead_id)",
        "CREATE INDEX IF NOT EXISTS idx_flags_lead       ON lead_flags(lead_id)",
        "CREATE INDEX IF NOT EXISTS idx_flags_org        ON lead_flags(org_id, resolved)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_org        ON tasks(org_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_assigned   ON tasks(assigned_to, status)",
        "CREATE INDEX IF NOT EXISTS idx_campaigns_org    ON campaigns(org_id)",
        "CREATE INDEX IF NOT EXISTS idx_campaigns_client ON campaigns(client_id)",
        "CREATE INDEX IF NOT EXISTS idx_cl_campaign      ON campaign_leads(campaign_id)",
        "CREATE INDEX IF NOT EXISTS idx_cl_lead          ON campaign_leads(lead_id)",
        "CREATE INDEX IF NOT EXISTS idx_crm_campaign     ON crm_updates(campaign_id)",
        "CREATE INDEX IF NOT EXISTS idx_users_org        ON users(org_id)",
        "CREATE INDEX IF NOT EXISTS idx_users_role       ON users(org_id, role)",
        "CREATE INDEX IF NOT EXISTS idx_ai_log_org       ON platform_ai_log(org_id)",
        "CREATE INDEX IF NOT EXISTS idx_ai_usage_org     ON org_ai_usage(org_id)",
        "CREATE INDEX IF NOT EXISTS idx_patterns_domain  ON layout_patterns(domain)",
        "CREATE INDEX IF NOT EXISTS idx_mappings_org     ON learned_mappings(org_id, mapping_type)",
        "CREATE INDEX IF NOT EXISTS idx_benchmarks_org   ON org_benchmarks(org_id)",
        "CREATE INDEX IF NOT EXISTS idx_notif_user       ON notifications(user_id, is_read)",
        "CREATE INDEX IF NOT EXISTS idx_appearances_lead ON lead_appearances(lead_id)",
        "CREATE INDEX IF NOT EXISTS idx_companies_org    ON companies(org_id)",
    ]
    for idx in indexes:
        c.execute(idx)

    # ══════════════════════════════════════════════════════════════════
    # FTS5 FULL-TEXT SEARCH — replaces LIKE queries
    # ══════════════════════════════════════════════════════════════════
    try:
        c.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS leads_fts USING fts5(
            full_name,
            title,
            company_name,
            tags,
            content='',
            contentless_delete=1
        )""")

        # Triggers to keep FTS in sync
        c.execute("""
        CREATE TRIGGER IF NOT EXISTS leads_fts_ai
        AFTER INSERT ON leads BEGIN
            INSERT INTO leads_fts(rowid, full_name, title, company_name, tags)
            VALUES (new.id, new.full_name, new.title,
                    COALESCE((SELECT name FROM companies WHERE id=new.company_id), ''),
                    new.tags);
        END""")

        # NOTE: the FTS5 'delete' command is rejected on a contentless_delete=1
        # table in this SQLite build ("'delete' may not be used with a
        # contentless_delete=1 table"), which would break every UPDATE/DELETE on
        # leads (re-scrape, status change, enrichment, archive). Use a plain
        # DELETE FROM leads_fts WHERE rowid=... instead — that works.
        c.execute("""
        CREATE TRIGGER IF NOT EXISTS leads_fts_ad
        AFTER DELETE ON leads BEGIN
            DELETE FROM leads_fts WHERE rowid = old.id;
        END""")

        c.execute("""
        CREATE TRIGGER IF NOT EXISTS leads_fts_au
        AFTER UPDATE ON leads BEGIN
            DELETE FROM leads_fts WHERE rowid = old.id;
            INSERT INTO leads_fts(rowid, full_name, title, company_name, tags)
            VALUES (new.id, new.full_name, new.title,
                    COALESCE((SELECT name FROM companies WHERE id=new.company_id), ''),
                    new.tags);
        END""")
    except Exception as _fts_err:
        # SQLite build without FTS5 — degrade gracefully
        print(f"  [db] FTS5 not available: {_fts_err}")

    # ══════════════════════════════════════════════════════════════════
    # CAMPAIGN REPORT BUILDER — client-facing performance reports
    # ══════════════════════════════════════════════════════════════════

    c.execute("""
    CREATE TABLE IF NOT EXISTS campaign_reports (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id          INTEGER NOT NULL REFERENCES organisations(id),
        client_id       INTEGER REFERENCES clients(id),
        title           TEXT    NOT NULL DEFAULT 'Campaign Performance Report',
        date_range      TEXT,
        total_cold      INTEGER DEFAULT 0,
        total_followups INTEGER DEFAULT 0,
        total_emails    INTEGER DEFAULT 0,
        total_responses INTEGER DEFAULT 0,
        total_interested INTEGER DEFAULT 0,
        total_meetings  INTEGER DEFAULT 0,
        crm_count       INTEGER DEFAULT 0,
        campaigns_count INTEGER DEFAULT 0,
        analysis_notes  TEXT,
        is_published    INTEGER DEFAULT 0,
        created_by      INTEGER REFERENCES users(id),
        created_at      TEXT    DEFAULT (datetime('now')),
        updated_at      TEXT    DEFAULT (datetime('now'))
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS report_campaigns (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        report_id   INTEGER NOT NULL REFERENCES campaign_reports(id),
        name        TEXT    NOT NULL,
        cold        INTEGER DEFAULT 0,
        followups   INTEGER DEFAULT 0,
        total       INTEGER DEFAULT 0,
        responses   INTEGER DEFAULT 0,
        interested  INTEGER DEFAULT 0,
        meetings    INTEGER DEFAULT 0,
        rate        REAL    DEFAULT 0.0,
        status      TEXT    DEFAULT 'Active',
        sort_order  INTEGER DEFAULT 0
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS report_weekly_periods (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        report_id       INTEGER NOT NULL REFERENCES campaign_reports(id),
        period_label    TEXT    NOT NULL,
        period_order    INTEGER DEFAULT 0,
        total_emails    INTEGER DEFAULT 0,
        total_responses INTEGER DEFAULT 0,
        total_interested INTEGER DEFAULT 0,
        rate            REAL    DEFAULT 0.0
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS report_weekly_campaigns (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        period_id       INTEGER NOT NULL REFERENCES report_weekly_periods(id),
        report_id       INTEGER NOT NULL REFERENCES campaign_reports(id),
        campaign_name   TEXT    NOT NULL,
        cold            INTEGER DEFAULT 0,
        followups       INTEGER DEFAULT 0,
        total           INTEGER DEFAULT 0,
        responses       INTEGER DEFAULT 0,
        interested      INTEGER DEFAULT 0,
        rate            REAL    DEFAULT 0.0
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS report_crm_contacts (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        report_id       INTEGER NOT NULL REFERENCES campaign_reports(id),
        campaign_name   TEXT,
        contact_name    TEXT,
        company         TEXT,
        role            TEXT,
        email           TEXT,
        website         TEXT,
        status          TEXT,
        met             TEXT,
        notes           TEXT,
        lead_status     TEXT    DEFAULT 'Open',
        client_notes    TEXT,
        interest_status TEXT,
        sort_order      INTEGER DEFAULT 0,
        updated_at      TEXT    DEFAULT (datetime('now'))
    )""")

    c.execute("CREATE INDEX IF NOT EXISTS idx_rpt_org     ON campaign_reports(org_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_rpt_client  ON campaign_reports(client_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_rpt_camp    ON report_campaigns(report_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_rpt_week    ON report_weekly_periods(report_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_rpt_crm     ON report_crm_contacts(report_id)")

    # ══════════════════════════════════════════════════════════════════
    # OUTREACH CONTACTS — intelligence-first outreach pipeline
    # ══════════════════════════════════════════════════════════════════

    c.execute("""
    CREATE TABLE IF NOT EXISTS outreach_contacts (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id                  INTEGER NOT NULL REFERENCES organisations(id),
        -- Contact (Layer 1)
        first_name              TEXT,
        last_name               TEXT,
        email                   TEXT,
        title                   TEXT,
        linkedin_profile        TEXT,
        -- Company
        company_name            TEXT    NOT NULL,
        company_domain          TEXT,
        -- Account Intelligence (Layer 2)
        company_summary         TEXT,
        industry                TEXT,
        product_or_service      TEXT,
        business_model          TEXT,
        target_customer         TEXT,
        primary_markets         TEXT,
        marketing_channels      TEXT,
        influencer_usage        TEXT,
        hiring_signal           TEXT,
        recent_signal           TEXT,
        intelligence_raw        TEXT,
        -- AI Output (Layer 3)
        account_hook            TEXT,
        -- Status
        title_score             INTEGER DEFAULT 0,
        title_tier              TEXT,
        status                  TEXT    DEFAULT 'new',
        pages_fetched           INTEGER DEFAULT 0,
        website_status          TEXT,
        error                   TEXT,
        enriched_at             TEXT,
        created_at              TEXT    DEFAULT (datetime('now'))
    )""")

    # ══════════════════════════════════════════════════════════════════
    # API TOKENS (desktop scraper → dashboard ingest, org-scoped)
    # ══════════════════════════════════════════════════════════════════
    # The desktop scraper authenticates its pushes with a per-org token. We store
    # only a SHA-256 hash of the token; the raw value is shown to the admin once
    # at creation and never again.
    c.execute("""
    CREATE TABLE IF NOT EXISTS api_tokens (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id        INTEGER NOT NULL REFERENCES organisations(id),
        token_hash    TEXT    NOT NULL UNIQUE,
        label         TEXT,
        created_by    INTEGER REFERENCES users(id),
        created_at    TEXT    DEFAULT (datetime('now')),
        last_used_at  TEXT,
        revoked       INTEGER DEFAULT 0
    )""")

    # ══════════════════════════════════════════════════════════════════
    # CLIENT EMAIL ACCOUNTS (client portal — mailbox credentials)
    # ══════════════════════════════════════════════════════════════════
    # Agency admin enters the mailboxes used for a client's campaigns; the client
    # sees them read-only in their portal. Passwords are stored so they can be
    # shown back to the client (plaintext at rest — internal tooling).
    c.execute("""
    CREATE TABLE IF NOT EXISTS client_email_accounts (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id        INTEGER NOT NULL REFERENCES organisations(id),
        client_id     INTEGER NOT NULL REFERENCES clients(id),
        label         TEXT,                       -- e.g. "Main outreach inbox"
        email_address TEXT    NOT NULL,
        password      TEXT,
        provider      TEXT,                       -- e.g. Google Workspace, Outlook
        webmail_url   TEXT,                       -- optional login link
        created_by    INTEGER REFERENCES users(id),
        created_at    TEXT    DEFAULT (datetime('now'))
    )""")

    # ══════════════════════════════════════════════════════════════════
    # UNMATCHED EMAILS (Module D3)
    # ══════════════════════════════════════════════════════════════════
    # Uploaded emails that couldn't be matched to a lead are parked here, never
    # discarded — so a later scrape/enrichment can still claim them.
    c.execute("""
    CREATE TABLE IF NOT EXISTS unmatched_emails (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id        INTEGER NOT NULL REFERENCES organisations(id),
        email         TEXT    NOT NULL,
        raw_name      TEXT,
        raw_company   TEXT,
        verified      INTEGER DEFAULT 0,
        uploaded_at   TEXT    DEFAULT (datetime('now'))
    )""")

    # ══════════════════════════════════════════════════════════════════
    # SCORING PROFILES (Module B2)
    # ══════════════════════════════════════════════════════════════════
    # Plain-language, per-niche scoring criteria. The whole profile is injected
    # into the AI scoring prompt — nothing about scoring is hardcoded, so a new
    # niche (e.g. "microbiome diagnostics" vs "event attendance likelihood")
    # needs only a new row here, no code change. Deterministic keyword scoring
    # is banned (it produced the WHO-Foundation false positive); scoring is
    # always an AI judgment step reading the actual crawled site text.
    c.execute("""
    CREATE TABLE IF NOT EXISTS scoring_profiles (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id            INTEGER NOT NULL REFERENCES organisations(id),
        name              TEXT    NOT NULL,
        niche_description TEXT,                       -- what we're looking for, in prose
        gate_rules        TEXT,                       -- hard must-haves to even qualify
        score_criteria    TEXT,                       -- what pushes score up / down
        disqualifiers     TEXT,                       -- signals that zero out a company
        opener_tone_notes TEXT,                       -- voice/tone for opener generation
        created_by        INTEGER,
        created_at        TEXT    DEFAULT (datetime('now'))
    )""")

    conn.commit()
    conn.close()
    print(f"✅ Dashin V2 database ready at {DB_PATH}")


def migrate_db():
    """
    Safe migration — adds new columns and tables to existing databases.
    Skips anything that already exists.
    Run after init_db() on live databases.
    """
    conn = get_connection()
    c = conn.cursor()

    # New columns on existing tables
    new_cols = [
        # Module D: inventory v2
        ("companies", "industry",                "TEXT"),
        ("companies", "domain",                  "TEXT"),
        ("enrichment", "email_source",           "TEXT"),        # scrape | upload | manual
        ("enrichment", "email_verified",         "INTEGER DEFAULT 0"),
        ("enrichment", "email_matched_at",       "TEXT"),
        ("leads", "industry",                    "TEXT"),        # surfaced from company
        # organisations
        ("organisations", "ai_budget_usd",       "REAL DEFAULT 8.0"),
        ("organisations", "billing_day",          "INTEGER DEFAULT 1"),
        ("organisations", "max_users",            "INTEGER DEFAULT 5"),
        ("organisations", "max_clients",          "INTEGER DEFAULT 3"),
        ("organisations", "max_leads",            "INTEGER DEFAULT 10000"),
        ("organisations", "suspended_at",         "TEXT"),
        # Multi-tenant hierarchy
        ("organisations", "org_type",             "TEXT NOT NULL DEFAULT 'agency'"),
        ("organisations", "parent_org_id",        "INTEGER"),
        ("organisations", "subscription_tier",    "TEXT NOT NULL DEFAULT 'free'"),
        ("organisations", "subscription_status",  "TEXT NOT NULL DEFAULT 'active'"),
        ("organisations", "onboarded_at",         "TEXT"),
        ("organisations", "onboarded_by",         "INTEGER"),
        # users
        ("users", "org_id",                      "INTEGER DEFAULT 1"),
        ("users", "client_id",                   "INTEGER"),
        ("users", "last_login",                  "TEXT"),
        ("users", "must_reset_password",         "INTEGER DEFAULT 0"),
        ("users", "onboarded_at",                "TEXT"),
        # leads
        ("leads", "org_id",                      "INTEGER DEFAULT 1"),
        ("leads", "source_type",                 "TEXT DEFAULT 'event'"),
        ("leads", "released_to_client",          "INTEGER DEFAULT 0"),
        ("leads", "scraped_at",                  "TEXT DEFAULT (datetime('now'))"),
        # companies
        ("companies", "org_id",                  "INTEGER DEFAULT 1"),
        # campaigns
        ("campaigns", "org_id",                  "INTEGER DEFAULT 1"),
        ("campaigns", "is_visible_to_client",    "INTEGER DEFAULT 0"),
        ("campaigns", "marked_ready_by",         "INTEGER"),
        ("campaigns", "marked_ready_at",         "TEXT"),
        ("campaigns", "lead_count",              "INTEGER DEFAULT 0"),
        # campaign_leads
        ("campaign_leads", "crm_status",         "TEXT DEFAULT 'new'"),
        ("campaign_leads", "next_step",          "TEXT"),
        ("campaign_leads", "outreach_from",      "TEXT"),
        ("campaign_leads", "meeting_date",       "TEXT"),
        ("campaign_leads", "notes",              "TEXT"),
        ("campaign_leads", "last_updated_by",    "INTEGER"),
        ("campaign_leads", "last_updated_at",    "TEXT"),
        # scrape_sessions
        ("scrape_sessions", "org_id",            "INTEGER DEFAULT 1"),
        ("scrape_sessions", "ai_tokens_used",    "INTEGER DEFAULT 0"),
        ("scrape_sessions", "ai_cost_usd",       "REAL DEFAULT 0.0"),
        ("scrape_sessions", "pattern_used",      "INTEGER DEFAULT 0"),
        ("scrape_sessions", "scrape_quality",    "TEXT"),
        ("scrape_sessions", "process_pid",       "INTEGER"),
        # archived_lists
        ("archived_lists", "org_id",             "INTEGER DEFAULT 1"),
        # clients
        ("clients", "org_id",                    "INTEGER DEFAULT 1"),
    ]

    added = 0
    for table, col, defn in new_cols:
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
            added += 1
        except Exception:
            pass  # Column already exists — expected during repeated migration runs

    # Fix the broken FTS delete/update triggers on existing DBs (contentless_delete
    # syntax must pass rowid only). Without this, any UPDATE/DELETE on leads fails.
    try:
        c.execute("DROP TRIGGER IF EXISTS leads_fts_ad")
        c.execute("DROP TRIGGER IF EXISTS leads_fts_au")
        c.execute("""CREATE TRIGGER leads_fts_ad AFTER DELETE ON leads BEGIN
            DELETE FROM leads_fts WHERE rowid = old.id;
        END""")
        c.execute("""CREATE TRIGGER leads_fts_au AFTER UPDATE ON leads BEGIN
            DELETE FROM leads_fts WHERE rowid = old.id;
            INSERT INTO leads_fts(rowid, full_name, title, company_name, tags)
            VALUES (new.id, new.full_name, new.title,
                    COALESCE((SELECT name FROM companies WHERE id=new.company_id), ''),
                    new.tags);
        END""")
    except Exception as _fts_fix_err:
        print(f"  [db] FTS trigger fix skipped: {_fts_fix_err}")

    # API tokens (desktop scraper ingest) — added to existing DBs
    try:
        c.execute("""
        CREATE TABLE IF NOT EXISTS api_tokens (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id        INTEGER NOT NULL REFERENCES organisations(id),
            token_hash    TEXT    NOT NULL UNIQUE,
            label         TEXT,
            created_by    INTEGER REFERENCES users(id),
            created_at    TEXT    DEFAULT (datetime('now')),
            last_used_at  TEXT,
            revoked       INTEGER DEFAULT 0
        )""")
    except Exception:
        pass

    # Client email accounts (client portal) — added to existing DBs
    try:
        c.execute("""
        CREATE TABLE IF NOT EXISTS client_email_accounts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id        INTEGER NOT NULL REFERENCES organisations(id),
            client_id     INTEGER NOT NULL REFERENCES clients(id),
            label         TEXT,
            email_address TEXT    NOT NULL,
            password      TEXT,
            provider      TEXT,
            webmail_url   TEXT,
            created_by    INTEGER REFERENCES users(id),
            created_at    TEXT    DEFAULT (datetime('now'))
        )""")
    except Exception:
        pass

    # Unmatched emails pool (Module D3) — added to existing DBs
    try:
        c.execute("""
        CREATE TABLE IF NOT EXISTS unmatched_emails (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id        INTEGER NOT NULL REFERENCES organisations(id),
            email         TEXT    NOT NULL,
            raw_name      TEXT,
            raw_company   TEXT,
            verified      INTEGER DEFAULT 0,
            uploaded_at   TEXT    DEFAULT (datetime('now'))
        )""")
    except Exception:
        pass

    # Scoring profiles (Module B2) — added to existing DBs
    try:
        c.execute("""
        CREATE TABLE IF NOT EXISTS scoring_profiles (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id            INTEGER NOT NULL REFERENCES organisations(id),
            name              TEXT    NOT NULL,
            niche_description TEXT,
            gate_rules        TEXT,
            score_criteria    TEXT,
            disqualifiers     TEXT,
            opener_tone_notes TEXT,
            created_by        INTEGER,
            created_at        TEXT    DEFAULT (datetime('now'))
        )""")
    except Exception:
        pass

    # Create new tables introduced in multi-tenant update
    try:
        c.execute("""
        CREATE TABLE IF NOT EXISTS freelancer_client_assignments (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            freelancer_org_id INTEGER NOT NULL REFERENCES organisations(id),
            client_org_id     INTEGER NOT NULL REFERENCES organisations(id),
            assigned_by       INTEGER NOT NULL REFERENCES users(id),
            assigned_at       TEXT    DEFAULT (datetime('now')),
            active            INTEGER DEFAULT 1,
            UNIQUE(freelancer_org_id, client_org_id)
        )""")
    except Exception:
        pass

    try:
        c.execute("""
        CREATE TABLE IF NOT EXISTS subscription_tiers (
            tier                TEXT PRIMARY KEY,
            display_name        TEXT NOT NULL,
            max_users           INTEGER,
            max_clients         INTEGER,
            max_leads_per_month INTEGER,
            can_enrich          INTEGER DEFAULT 1,
            can_run_campaigns   INTEGER DEFAULT 1,
            can_use_ai          INTEGER DEFAULT 1,
            white_label_portal  INTEGER DEFAULT 0,
            api_access          INTEGER DEFAULT 0,
            price_gbp_monthly   REAL    DEFAULT 0
        )""")
        _tiers = [
            ('free',          'Free',          1,    0,    200,   0, 0, 0, 0, 0, 0),
            ('starter',       'Starter',       5,    2,    1000,  1, 1, 0, 0, 0, 149),
            ('growth',        'Growth',        15,   10,   5000,  1, 1, 1, 0, 0, 399),
            ('enterprise',    'Enterprise',    None, None, None,  1, 1, 1, 1, 1, 999),
            ('client_direct', 'Client Direct', 3,    None, None,  0, 0, 0, 0, 0, 49),
        ]
        c.executemany("""
            INSERT OR IGNORE INTO subscription_tiers
                (tier, display_name, max_users, max_clients, max_leads_per_month,
                 can_enrich, can_run_campaigns, can_use_ai,
                 white_label_portal, api_access, price_gbp_monthly)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, _tiers)
    except Exception:
        pass

    # Ensure default org exists for existing single-tenant data
    try:
        c.execute("""
            INSERT OR IGNORE INTO organisations
                (id, name, slug, tier, ai_budget_usd, billing_day,
                 max_users, max_clients, max_leads,
                 org_type, subscription_tier, subscription_status)
            VALUES (1, 'Dashin', 'dashin', 'enterprise',
                    50.0, 1, 9999, 9999, 9999999,
                    'dashin', 'enterprise', 'active')
        """)
        # Ensure org_id=1 is always marked as 'dashin' type
        c.execute("""
            UPDATE organisations SET org_type='dashin' WHERE id=1
        """)
    except Exception as e:
        logging.warning(f"[db.migrate_db] Failed to insert default org: {e}")

    # Ensure super admin exists
    try:
        try:
            import bcrypt as _bcrypt
            pw = _bcrypt.hashpw(b"admin123", _bcrypt.gensalt()).decode('utf-8')
        except ImportError:
            import hashlib
            pw = hashlib.sha256("admin123".encode()).hexdigest()
        c.execute("""
            INSERT OR IGNORE INTO users
                (id, org_id, name, email, password, role)
            VALUES (1, 1, 'Super Admin', 'admin@dashin.com', ?, 'super_admin')
        """, (pw,))
    except Exception as e:
        logging.warning(f"[db.migrate_db] Failed to insert default admin user: {e}")

    # Campaign report tables (added in report-builder update)
    for _sql in [
        """CREATE TABLE IF NOT EXISTS campaign_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT, org_id INTEGER, client_id INTEGER,
            title TEXT DEFAULT 'Campaign Performance Report', date_range TEXT,
            total_cold INTEGER DEFAULT 0, total_followups INTEGER DEFAULT 0,
            total_emails INTEGER DEFAULT 0, total_responses INTEGER DEFAULT 0,
            total_interested INTEGER DEFAULT 0, total_meetings INTEGER DEFAULT 0,
            crm_count INTEGER DEFAULT 0, campaigns_count INTEGER DEFAULT 0,
            analysis_notes TEXT, is_published INTEGER DEFAULT 0,
            created_by INTEGER, created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')))""",
        """CREATE TABLE IF NOT EXISTS report_campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT, report_id INTEGER, name TEXT,
            cold INTEGER DEFAULT 0, followups INTEGER DEFAULT 0, total INTEGER DEFAULT 0,
            responses INTEGER DEFAULT 0, interested INTEGER DEFAULT 0,
            meetings INTEGER DEFAULT 0, rate REAL DEFAULT 0.0,
            status TEXT DEFAULT 'Active', sort_order INTEGER DEFAULT 0)""",
        """CREATE TABLE IF NOT EXISTS report_weekly_periods (
            id INTEGER PRIMARY KEY AUTOINCREMENT, report_id INTEGER,
            period_label TEXT, period_order INTEGER DEFAULT 0,
            total_emails INTEGER DEFAULT 0, total_responses INTEGER DEFAULT 0,
            total_interested INTEGER DEFAULT 0, rate REAL DEFAULT 0.0)""",
        """CREATE TABLE IF NOT EXISTS report_weekly_campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT, period_id INTEGER, report_id INTEGER,
            campaign_name TEXT, cold INTEGER DEFAULT 0, followups INTEGER DEFAULT 0,
            total INTEGER DEFAULT 0, responses INTEGER DEFAULT 0,
            interested INTEGER DEFAULT 0, rate REAL DEFAULT 0.0)""",
        """CREATE TABLE IF NOT EXISTS report_crm_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, report_id INTEGER,
            campaign_name TEXT, contact_name TEXT, company TEXT, role TEXT,
            email TEXT, website TEXT, status TEXT, met TEXT, notes TEXT,
            lead_status TEXT DEFAULT 'Open', client_notes TEXT,
            interest_status TEXT, sort_order INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now')))""",
    ]:
        try:
            c.execute(_sql)
        except Exception:
            pass

    # Populate FTS index if empty (one-time backfill)
    try:
        fts_count = c.execute("SELECT COUNT(*) AS n FROM leads_fts").fetchone()
        leads_count = c.execute("SELECT COUNT(*) AS n FROM leads").fetchone()
        if fts_count and leads_count and (fts_count["n"] == 0) and (leads_count["n"] > 0):
            c.execute("""
                INSERT INTO leads_fts(rowid, full_name, title, company_name, tags)
                SELECT l.id, l.full_name, l.title,
                       COALESCE(co.name, ''),
                       COALESCE(l.tags, '')
                FROM leads l
                LEFT JOIN companies co ON co.id = l.company_id
            """)
            print(f"  [FTS] Backfilled {leads_count['n']} leads into search index")
    except Exception as _fts_err:
        pass  # FTS5 not available — skip silently

    conn.commit()
    conn.close()
    print(f"✅ Migration complete — {added} columns added")


def ensure_defaults():
    """
    Called on every app startup.
    Creates the default org and super admin if DB is empty.
    """
    conn = get_connection()
    c = conn.cursor()

    # Default org — Dashin itself
    c.execute("""
        INSERT OR IGNORE INTO organisations
            (id, name, slug, tier, ai_budget_usd, billing_day,
             max_users, max_clients, max_leads,
             org_type, subscription_tier, subscription_status)
        VALUES (1, 'Dashin', 'dashin', 'enterprise',
                50.0, 1, 9999, 9999, 9999999,
                'dashin', 'enterprise', 'active')
    """)
    # Always ensure org 1 is marked as dashin type
    c.execute("UPDATE organisations SET org_type='dashin' WHERE id=1")

    # Super admin — use bcrypt if available
    try:
        import bcrypt as _bcrypt
        pw = _bcrypt.hashpw(b"admin123", _bcrypt.gensalt()).decode('utf-8')
    except ImportError:
        import hashlib
        pw = hashlib.sha256("admin123".encode()).hexdigest()

    c.execute("""
        INSERT OR IGNORE INTO users
            (id, org_id, name, email, password, role)
        VALUES (1, 1, 'Super Admin', 'admin@dashin.com', ?, 'super_admin')
    """, (pw,))

    conn.commit()
    conn.close()


def backup_database(label: str = "") -> Path:
    """
    Create a timestamped copy of dashin.db in data/backups/.
    Uses SQLite online backup API (safe while app is running).
    Returns the path to the backup file.
    """
    import shutil
    from datetime import datetime as _dt

    backup_dir = DB_PATH.parent.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    ts = _dt.utcnow().strftime("%Y%m%d_%H%M%S")
    slug = f"_{label}" if label else ""
    dest = backup_dir / f"dashin_{ts}{slug}.db"

    # Use SQLite backup API for a consistent hot-copy
    src_conn = get_connection()
    dest_conn = sqlite3.connect(str(dest))
    try:
        src_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        src_conn.close()

    logging.info(f"[db.backup_database] Backup created: {dest}")
    return dest


def list_backups() -> list:
    """Return list of backup dicts {name, path, size_kb, created_at}."""
    backup_dir = DB_PATH.parent.parent / "backups"
    if not backup_dir.exists():
        return []
    results = []
    for f in sorted(backup_dir.glob("dashin_*.db"), reverse=True):
        stat = f.stat()
        results.append({
            "name": f.name,
            "path": str(f),
            "size_kb": round(stat.st_size / 1024, 1),
            "created_at": stat.st_mtime,
        })
    return results


if __name__ == "__main__":
    init_db()
    migrate_db()
    ensure_defaults()
    print("\n  Default login: admin@dashin.com / admin123")
    print("  Change this immediately in Users & Clients.\n")
