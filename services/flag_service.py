"""
services/flag_service.py — Dashin Research Platform
Auto-flags lead quality issues before manager review.
All 5 flag types — learns over time via learned_mappings.
"""

import re
import logging
from datetime import datetime

# ── KNOWN BAD DOMAINS ─────────────────────────────────────────────────────────

PERSONAL_DOMAINS = {
    "gmail.com","googlemail.com","yahoo.com","yahoo.co.uk","yahoo.fr",
    "yahoo.de","yahoo.es","yahoo.it","hotmail.com","hotmail.co.uk",
    "hotmail.fr","hotmail.de","hotmail.es","outlook.com","outlook.co.uk",
    "live.com","live.co.uk","msn.com","icloud.com","me.com","mac.com",
    "aol.com","protonmail.com","proton.me","zoho.com","zohomail.com",
    "yandex.com","yandex.ru","mail.com","mail.ru","gmx.com","gmx.de",
    "web.de","t-online.de","orange.fr","free.fr","wanadoo.fr",
    "laposte.net","bbox.fr","sfr.fr","libero.it","alice.it","virgilio.it",
    "tiscali.it","btinternet.com","sky.com","talktalk.net","ntlworld.com",
    "o2.co.uk","virginmedia.com","cox.net","comcast.net","att.net",
    "verizon.net","sbcglobal.net","bellsouth.net","earthlink.net",
    "fastmail.com","fastmail.fm","tutanota.com","tutamail.com",
    "pm.me","hey.com","duck.com",
}

ROLE_BASED_PREFIXES = {
    "info","contact","hello","hi","support","help","admin","administrator",
    "office","team","noreply","no-reply","donotreply","do-not-reply",
    "sales","marketing","hr","careers","jobs","billing","accounts",
    "finance","legal","press","media","news","pr","partnerships",
    "enquiries","enquiry","queries","query","service","services",
    "webmaster","postmaster","hostmaster","abuse","security","privacy",
    "data","gdpr","compliance","general","mail","email","contact",
    "reception","front","desk",
}

INVALID_TLDS = {"test","example","localhost","invalid","local","internal"}


def _get_conn():
    from core.db import get_connection
    return get_connection()


def _get_org_whitelist(org_id: int) -> set:
    """Domains the org has whitelisted (learned over time)."""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT key FROM learned_mappings
        WHERE (org_id=? OR org_id IS NULL)
          AND mapping_type='domain_whitelist'
    """, (org_id,)).fetchall()
    conn.close()
    return {r["key"].lower() for r in rows}


def _get_org_blacklist(org_id: int) -> set:
    """Domains the org has blacklisted."""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT key FROM learned_mappings
        WHERE (org_id=? OR org_id IS NULL)
          AND mapping_type='domain_blacklist'
    """, (org_id,)).fetchall()
    conn.close()
    return {r["key"].lower() for r in rows}


def extract_domain(email: str) -> str:
    """Extract domain from email address."""
    if not email or "@" not in email:
        return ""
    return email.split("@")[-1].strip().lower()


# ── FLAG DETECTION FUNCTIONS ──────────────────────────────────────────────────

def check_personal_email(email: str, org_id: int) -> dict | None:
    """Flag if email uses a known personal domain."""
    if not email:
        return None
    domain = extract_domain(email)
    if not domain:
        return None

    # Check org whitelist first
    whitelist = _get_org_whitelist(org_id)
    if domain in whitelist:
        return None

    if domain in PERSONAL_DOMAINS:
        return {
            "flag_type": "personal_email",
            "severity":  "critical",
            "detail":    f"Personal email domain: {domain}",
        }
    return None


def check_invalid_format(email: str) -> dict | None:
    """Flag emails with invalid format."""
    if not email:
        return None
    email = email.strip()

    # No @ symbol
    if "@" not in email:
        return {
            "flag_type": "invalid_email_format",
            "severity":  "critical",
            "detail":    "Missing @ symbol",
        }

    parts = email.split("@")
    if len(parts) != 2:
        return {
            "flag_type": "invalid_email_format",
            "severity":  "critical",
            "detail":    "Multiple @ symbols",
        }

    local, domain = parts

    # Empty local or domain
    if not local or not domain:
        return {
            "flag_type": "invalid_email_format",
            "severity":  "critical",
            "detail":    "Empty local or domain part",
        }

    # No dot in domain
    if "." not in domain:
        return {
            "flag_type": "invalid_email_format",
            "severity":  "critical",
            "detail":    f"Domain has no TLD: {domain}",
        }

    # Invalid TLD
    tld = domain.split(".")[-1].lower()
    if tld in INVALID_TLDS or len(tld) < 2:
        return {
            "flag_type": "invalid_email_format",
            "severity":  "critical",
            "detail":    f"Suspicious TLD: .{tld}",
        }

    # Basic regex
    pattern = r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, email):
        return {
            "flag_type": "invalid_email_format",
            "severity":  "critical",
            "detail":    "Fails email format validation",
        }

    return None


def check_role_based_email(email: str) -> dict | None:
    """Flag generic/role-based email prefixes."""
    if not email or "@" not in email:
        return None
    local = email.split("@")[0].lower().strip()

    # Remove common separators and check
    cleaned = re.sub(r'[._\-]', '', local)
    if local in ROLE_BASED_PREFIXES or cleaned in ROLE_BASED_PREFIXES:
        return {
            "flag_type": "role_based_email",
            "severity":  "warning",
            "detail":    f"Role-based prefix: {local}",
        }
    return None


def check_domain_mismatch(email: str, company_name: str,
                           org_id: int) -> dict | None:
    """
    Flag if email domain doesn't obviously match company name.
    Uses fuzzy matching — only flags clear mismatches.
    """
    if not email or not company_name or "@" not in email:
        return None

    domain = extract_domain(email)
    if not domain:
        return None

    # Skip personal/free domains (caught by personal_email check)
    if domain in PERSONAL_DOMAINS:
        return None

    # Check org whitelist
    whitelist = _get_org_whitelist(org_id)
    if domain in whitelist:
        return None

    # Normalise both for comparison
    domain_base = domain.split(".")[0].lower()
    company_norm = re.sub(r'[^a-z0-9]', '', company_name.lower())

    # Check if domain base appears anywhere in company name or vice versa
    if (domain_base in company_norm or
        company_norm in domain_base or
        len(domain_base) < 3):
        return None

    # Check if domain base is a substring of company words
    company_words = re.findall(r'[a-z0-9]+', company_name.lower())
    for word in company_words:
        if len(word) >= 3 and (word in domain_base or domain_base in word):
            return None

    # Flag as potential mismatch — low severity since many companies use
    # different domains (e.g. subsidiary, rebrand)
    return {
        "flag_type": "domain_mismatch",
        "severity":  "warning",
        "detail":    f"{domain} may not match {company_name}",
    }


def check_duplicate(lead_id: int, org_id: int) -> dict | None:
    """Flag if this lead appears to be a duplicate in the org's inventory."""
    conn = _get_conn()
    lead = conn.execute("""
        SELECT l.name_key, l.company_id, l.times_seen
        FROM leads l WHERE l.id=? AND l.org_id=?
    """, (lead_id, org_id)).fetchone()

    if not lead:
        conn.close()
        return None

    # Check if times_seen > 1 (seen at multiple events)
    if lead["times_seen"] > 1:
        conn.close()
        return {
            "flag_type": "duplicate",
            "severity":  "warning",
            "detail":    f"Seen {lead['times_seen']} times across events",
        }

    conn.close()
    return None


# ── MAIN FLAG RUNNER ──────────────────────────────────────────────────────────

def flag_lead(lead_id: int, org_id: int,
              email: str = None,
              company_name: str = None,
              auto_save: bool = True,
              conn=None) -> list:
    """
    Run all flag checks for a lead.
    Returns list of flag dicts.
    If auto_save=True, saves flags to DB.
    If conn is provided, uses that connection (for atomic transactions).
    """
    flags = []

    if email:
        for check_fn in [
            lambda: check_invalid_format(email),
            lambda: check_personal_email(email, org_id),
            lambda: check_role_based_email(email),
        ]:
            result = check_fn()
            if result:
                flags.append(result)

        if company_name:
            dm = check_domain_mismatch(email, company_name, org_id)
            if dm:
                flags.append(dm)

    dup = check_duplicate(lead_id, org_id)
    if dup:
        flags.append(dup)

    if auto_save and flags:
        _save_flags(lead_id, org_id, flags, conn=conn)

    return flags


def flag_batch(leads: list, org_id: int) -> dict:
    """
    Flag a batch of leads.
    leads = [{lead_id, email, company_name}, ...]
    Returns {lead_id: [flags], ...}
    """
    results = {}
    for lead in leads:
        lead_id = lead.get("lead_id") or lead.get("id")
        if not lead_id:
            continue
        flags = flag_lead(
            lead_id      = lead_id,
            org_id       = org_id,
            email        = lead.get("email"),
            company_name = lead.get("company_name") or lead.get("company"),
            auto_save    = True,
        )
        if flags:
            results[lead_id] = flags
    return results


def _save_flags(lead_id: int, org_id: int, flags: list, conn=None):
    """
    Save detected flags to DB, skip if already flagged with same type.
    If conn is provided, uses that connection (caller manages commit/close).
    """
    owns_conn = conn is None
    if owns_conn:
        conn = _get_conn()
    now = datetime.utcnow().isoformat()
    for flag in flags:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO lead_flags
                    (lead_id, org_id, flag_type, severity,
                     detail, auto_flagged, flagged_at)
                VALUES (?,?,?,?,?,1,?)
            """, (lead_id, org_id,
                  flag["flag_type"], flag["severity"],
                  flag.get("detail", ""), now))
        except Exception as e:
            logging.warning(f"[flag_service._save_flags] {e}")
    if owns_conn:
        conn.commit()
        conn.close()


def resolve_flag(flag_id: int, resolved_by: int,
                 note: str = "", learn: bool = True):
    """
    Mark a flag as resolved.
    If learn=True and flag_type is personal_email or domain_mismatch,
    add domain to org whitelist so it's not flagged again.
    """
    conn = _get_conn()
    flag = conn.execute(
        "SELECT * FROM lead_flags WHERE id=?", (flag_id,)
    ).fetchone()

    if not flag:
        conn.close()
        return

    now = datetime.utcnow().isoformat()
    conn.execute("""
        UPDATE lead_flags
        SET resolved=1, resolved_by=?, resolved_at=?, resolution_note=?
        WHERE id=?
    """, (resolved_by, now, note, flag_id))

    # Learn from resolution
    if learn and flag["flag_type"] in ("personal_email", "domain_mismatch"):
        # Extract domain from detail
        detail = flag.get("detail", "")
        domain_match = re.search(r'[\w.-]+\.[a-z]{2,}', detail)
        if domain_match:
            domain = domain_match.group(0).lower()
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO learned_mappings
                        (org_id, mapping_type, key, value, usage_count)
                    VALUES (?,?,?,?,1)
                """, (flag["org_id"], "domain_whitelist",
                      domain, "whitelisted_by_manager"))
            except Exception as e:
                logging.warning(f"[flag_service] domain_whitelist insert failed for {domain}: {e}")

            # Log learning event
            conn.execute("""
                INSERT INTO learning_events
                    (org_id, event_type, entity_type, entity_id,
                     old_value, new_value, triggered_by)
                VALUES (?,?,?,?,?,?,?)
            """, (flag["org_id"], "flag_dismissed",
                  "lead", flag["lead_id"],
                  flag["flag_type"], f"whitelisted:{domain}",
                  resolved_by))

    conn.commit()
    conn.close()


def get_unresolved_flags(org_id: int, lead_id: int = None) -> list:
    """Get all unresolved flags for an org or specific lead."""
    conn = _get_conn()
    if lead_id:
        rows = conn.execute("""
            SELECT f.*, l.full_name, l.title
            FROM lead_flags f
            JOIN leads l ON l.id = f.lead_id
            WHERE f.org_id=? AND f.lead_id=? AND f.resolved=0
            ORDER BY f.severity DESC, f.flagged_at DESC
        """, (org_id, lead_id)).fetchall()
    else:
        rows = conn.execute("""
            SELECT f.*, l.full_name, l.title,
                   co.name AS company_name,
                   e.email
            FROM lead_flags f
            JOIN leads l  ON l.id  = f.lead_id
            LEFT JOIN companies co ON co.id = l.company_id
            LEFT JOIN enrichment e ON e.lead_id = f.lead_id
            WHERE f.org_id=? AND f.resolved=0
            ORDER BY f.severity DESC, f.flagged_at DESC
        """, (org_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_flag_summary(org_id: int) -> dict:
    """Count unresolved flags by type for an org."""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT flag_type, severity, COUNT(*) AS cnt
        FROM lead_flags
        WHERE org_id=? AND resolved=0
        GROUP BY flag_type, severity
    """, (org_id,)).fetchall()
    conn.close()

    summary = {}
    total = 0
    for r in rows:
        summary[r["flag_type"]] = {
            "count":    r["cnt"],
            "severity": r["severity"],
        }
        total += r["cnt"]
    summary["total"] = total
    return summary
