"""
services/ingest_service.py — turn scraper pushes into inventory rows.

Accepts the flexible row shape the scrapers' push_to_dashin() sends (company_name,
website, description, country, business_areas, industry, source, and optionally a
person: name/full_name/contact_name, title, email, linkedin_url) and writes it
into the org's inventory, deduplicating via lead_service.save_lead.

Company-only scrapes (e.g. the microbiome company lists) have no person — we use
the company name as the lead so the account still appears in inventory and can be
researched for contacts later. Extra fields (website→company.domain, industry,
country, email, linkedin) are upserted onto companies/enrichment.
"""

from datetime import datetime, timezone
from urllib.parse import urlparse

from core.db import get_connection
from services.lead_service import save_lead, make_key


def _pick(row: dict, *names) -> str:
    low = {(k or "").strip().lower(): v for k, v in row.items()}
    for n in names:
        v = low.get(n)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _domain_from_url(url: str) -> str:
    if not url:
        return ""
    if not url.startswith("http"):
        url = "https://" + url
    try:
        net = urlparse(url).netloc.lower()
        return net[4:] if net.startswith("www.") else net
    except Exception:
        return ""


def _update_company(org_id: int, company_name: str, domain: str, industry: str):
    if not company_name:
        return
    conn = get_connection()
    try:
        row = conn.execute("SELECT id FROM companies WHERE org_id=? AND name_key=?",
                           (org_id, make_key(company_name))).fetchone()
        if row:
            if domain:
                conn.execute("UPDATE companies SET domain=COALESCE(NULLIF(domain,''),?) WHERE id=?",
                             (domain, row["id"]))
            if industry:
                conn.execute("UPDATE companies SET industry=COALESCE(NULLIF(industry,''),?) WHERE id=?",
                             (industry, row["id"]))
            conn.commit()
    finally:
        conn.close()


def _upsert_enrichment(lead_id: int, email: str, linkedin: str, country: str, industry: str):
    if not (email or linkedin or country or industry):
        return
    conn = get_connection()
    try:
        exists = conn.execute("SELECT 1 FROM enrichment WHERE lead_id=?", (lead_id,)).fetchone()
        if exists:
            conn.execute("""UPDATE enrichment SET
                email       = COALESCE(NULLIF(email,''),?),
                linkedin_url= COALESCE(NULLIF(linkedin_url,''),?),
                country     = COALESCE(NULLIF(country,''),?),
                industry    = COALESCE(NULLIF(industry,''),?)
                WHERE lead_id=?""", (email, linkedin, country, industry, lead_id))
        else:
            conn.execute("""INSERT INTO enrichment
                (lead_id, email, linkedin_url, country, industry)
                VALUES (?,?,?,?,?)""", (lead_id, email, linkedin, country, industry))
        conn.commit()
    finally:
        conn.close()


def ingest_rows(org_id: int, rows: list, source: str = "scraper") -> dict:
    """
    Import a batch of scraped rows into the org's inventory.
    Returns {imported, new, updated, skipped, total}.
    """
    summary = {"imported": 0, "new": 0, "updated": 0, "skipped": 0, "total": len(rows or [])}
    session_id = f"ingest_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    for row in rows or []:
        company = _pick(row, "company_name", "company", "organisation", "organization")
        person = _pick(row, "full_name", "name", "contact_name", "person")
        title = _pick(row, "title", "job_title", "role", "position")
        website = _pick(row, "website", "url", "domain", "company_website")
        industry = _pick(row, "industry", "business_areas", "business_area")
        country = _pick(row, "country", "location")
        email = _pick(row, "email")
        linkedin = _pick(row, "linkedin_url", "linkedin")

        # No person → use the company as the account-level "lead" so it's visible.
        full_name = person or company
        if not full_name:
            summary["skipped"] += 1
            continue

        lead_id, is_new = save_lead(
            org_id=org_id, full_name=full_name, company_name=company,
            title=title, tags=source, session_id=session_id, event_name=source)
        if not lead_id:
            summary["skipped"] += 1
            continue

        _update_company(org_id, company, _domain_from_url(website), industry)
        _upsert_enrichment(lead_id, email, linkedin, country, industry)

        summary["imported"] += 1
        summary["new" if is_new else "updated"] += 1

    return summary
