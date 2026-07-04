"""
services/email_matcher.py — email-list matching (Module D3).

Upload an email list (from any external finder/verifier) and attach each address
to the right lead already in inventory. Matching runs in priority order:

  1. exact    — the row carries a name (+ optional company) that matches a lead.
  2. inferred — no name, but the email local part implies one (john.doe@… →
                "john doe") that matches a lead.
  3. domain   — only the domain is known; if exactly one lead sits at that
                company/domain, auto-match it; if several, send it to review with
                the candidates ranked by name similarity.

Anything still unmatched goes to the unmatched_emails pool — never dropped, so a
later scrape can still claim it. Matched emails are written to the lead's
enrichment row with email_source='upload'.

The pure logic (email parsing, name inference, name/company matching, and the
per-row decision `match_row_against_leads`) is import-clean and unit-testable;
`match_emails` is the DB-backed orchestration that returns a match report.
"""

import re
from datetime import datetime, timezone

from core.db import get_connection

_COMPANY_SUFFIXES = {
    "inc", "llc", "ltd", "limited", "gmbh", "ag", "corp", "corporation", "co",
    "company", "plc", "bv", "sarl", "srl", "sa", "group", "holding", "holdings",
    "the", "foundation",
}
# Generic mailbox domains that should NOT be used for company-domain matching.
_FREEMAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
             "aol.com", "gmx.com", "protonmail.com", "live.com", "me.com"}


# ── Pure helpers ──────────────────────────────────────────────────────────────

def local_of(email: str) -> str:
    return email.split("@", 1)[0].strip().lower() if email and "@" in email else ""


def domain_of(email: str) -> str:
    return email.split("@", 1)[1].strip().lower() if email and "@" in email else ""


def infer_name_from_local(local: str) -> str:
    """
    Turn an email local part into a probable name:
      "john.doe" → "john doe", "jsmith" → "jsmith" (unsplittable, left as-is),
      "john_doe99" → "john doe". Digits and plus-tags are stripped.
    """
    if not local:
        return ""
    local = re.sub(r"\+.*$", "", local)          # drop +tags
    local = re.sub(r"\d+", " ", local)           # drop digits
    parts = [p for p in re.split(r"[._\-]+", local) if p]
    return " ".join(parts).strip()


def _tokens(name: str) -> set:
    return {t for t in re.sub(r"[^\w\s]", " ", (name or "").lower()).split() if len(t) > 1}


def name_matches(query_name: str, lead_name: str) -> bool:
    """True if every token of the (shorter) query name appears in the lead name."""
    q, l = _tokens(query_name), _tokens(lead_name)
    if not q or not l:
        return False
    return q.issubset(l) or l.issubset(q)


def normalize_company(name: str) -> str:
    if not name:
        return ""
    s = re.sub(r"[^\w\s]", " ", str(name).lower())
    return " ".join(t for t in s.split() if t and t not in _COMPANY_SUFFIXES)


def company_matches(a: str, b: str) -> bool:
    ta, tb = set(normalize_company(a).split()), set(normalize_company(b).split())
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) >= 0.6


# ── Pure decision: match one row against candidate leads ──────────────────────

def match_row_against_leads(row: dict, leads: list) -> dict:
    """
    Decide how an uploaded email row matches the given candidate leads (already
    org-scoped). Pure — no DB. `leads` is a list of dicts with at least
    id, full_name, company_name, email_domain (domain of any existing email or
    the company's domain, may be "").

    Returns {tier, lead_id, candidates} where tier is one of
    exact | inferred | domain | review | unmatched.
    """
    email = (row.get("email") or "").strip().lower()
    name = (row.get("name") or "").strip()
    company = (row.get("company") or "").strip()
    dom = domain_of(email)

    # Tier 1 — exact: row name matches a lead (company must agree if both given).
    if name:
        for ld in leads:
            if name_matches(name, ld.get("full_name", "")):
                if not company or not ld.get("company_name") or \
                        company_matches(company, ld["company_name"]):
                    return {"tier": "exact", "lead_id": ld["id"], "candidates": []}

    # Tier 2 — inferred: derive a name from the local part, match it.
    inferred = infer_name_from_local(local_of(email))
    if inferred and " " in inferred:   # only trust multi-token inferences
        for ld in leads:
            if name_matches(inferred, ld.get("full_name", "")):
                if not company or not ld.get("company_name") or \
                        company_matches(company, ld["company_name"]):
                    return {"tier": "inferred", "lead_id": ld["id"], "candidates": []}

    # Tier 3 — domain: leads whose company sits at this email domain.
    if dom and dom not in _FREEMAIL:
        at_domain = [ld for ld in leads if ld.get("email_domain") == dom]
        if len(at_domain) == 1:
            return {"tier": "domain", "lead_id": at_domain[0]["id"], "candidates": []}
        if len(at_domain) > 1:
            # rank candidates by name similarity to the inferred name
            def sim(ld):
                return len(_tokens(inferred) & _tokens(ld.get("full_name", "")))
            ranked = sorted(at_domain, key=sim, reverse=True)
            return {"tier": "review", "lead_id": None,
                    "candidates": [c["id"] for c in ranked]}

    return {"tier": "unmatched", "lead_id": None, "candidates": []}


# ── DB-backed orchestration ───────────────────────────────────────────────────

def _load_leads(org_id: int) -> list:
    """Load org leads with company name + a best-known email domain for matching."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT l.id, l.full_name,
                   c.name    AS company_name,
                   c.domain  AS company_domain,
                   e.email   AS existing_email
            FROM leads l
            LEFT JOIN companies c  ON c.id = l.company_id
            LEFT JOIN enrichment e ON e.lead_id = l.id
            WHERE l.org_id = ?
        """, (org_id,)).fetchall()
    finally:
        conn.close()
    leads = []
    for r in rows:
        d = dict(r)
        dom = (d.get("company_domain") or "").strip().lower()
        if not dom and d.get("existing_email"):
            dom = domain_of(d["existing_email"])
        leads.append({"id": d["id"], "full_name": d.get("full_name", ""),
                      "company_name": d.get("company_name", ""), "email_domain": dom})
    return leads


def _write_email(lead_id: int, email: str, verified: bool):
    """Upsert the matched email onto the lead's enrichment row (source=upload)."""
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    conn = get_connection()
    try:
        exists = conn.execute("SELECT 1 FROM enrichment WHERE lead_id=?", (lead_id,)).fetchone()
        if exists:
            conn.execute("""UPDATE enrichment
                            SET email=?, email_source='upload', email_verified=?,
                                email_matched_at=? WHERE lead_id=?""",
                         (email, 1 if verified else 0, now, lead_id))
        else:
            conn.execute("""INSERT INTO enrichment
                            (lead_id, email, email_source, email_verified, email_matched_at)
                            VALUES (?,?, 'upload', ?, ?)""",
                         (lead_id, email, 1 if verified else 0, now))
        conn.commit()
    finally:
        conn.close()


def _park_unmatched(org_id: int, row: dict):
    conn = get_connection()
    try:
        conn.execute("""INSERT INTO unmatched_emails
                        (org_id, email, raw_name, raw_company, verified)
                        VALUES (?,?,?,?,?)""",
                     (org_id, (row.get("email") or "").strip().lower(),
                      row.get("name", ""), row.get("company", ""),
                      1 if row.get("verified") else 0))
        conn.commit()
    finally:
        conn.close()


def match_emails(org_id: int, rows: list, write: bool = True) -> dict:
    """
    Match a list of uploaded email rows against org inventory and (optionally)
    persist the results. Each row: {email, name?, company?, verified?}.

    Returns a report:
      {matched_exact, matched_inferred, matched_domain, needs_review, unmatched,
       total, details:[{email, tier, lead_id, candidates}]}
    """
    leads = _load_leads(org_id)
    report = {"matched_exact": 0, "matched_inferred": 0, "matched_domain": 0,
              "needs_review": 0, "unmatched": 0, "total": 0, "details": []}

    for row in rows:
        email = (row.get("email") or "").strip().lower()
        if not email or "@" not in email:
            continue
        report["total"] += 1
        decision = match_row_against_leads(row, leads)
        tier = decision["tier"]

        if tier in ("exact", "inferred", "domain") and decision["lead_id"]:
            if write:
                _write_email(decision["lead_id"], email, bool(row.get("verified")))
            report[{"exact": "matched_exact", "inferred": "matched_inferred",
                    "domain": "matched_domain"}[tier]] += 1
        elif tier == "review":
            report["needs_review"] += 1
        else:
            if write:
                _park_unmatched(org_id, row)
            report["unmatched"] += 1

        report["details"].append({"email": email, "tier": tier,
                                   "lead_id": decision["lead_id"],
                                   "candidates": decision["candidates"]})
    return report
