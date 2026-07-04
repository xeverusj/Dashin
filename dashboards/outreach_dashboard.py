"""
dashboards/outreach_dashboard.py — Dashin Research Platform

Outreach Pipeline Dashboard — 3 tabs:
  Tab 1  "From LinkedIn"   → contact selection with title scoring
  Tab 2  "Upload & Enrich" → CSV upload or enrich staged contacts via Claude
  Tab 3  "Inventory"       → view / export all outreach_contacts

Reuses the enrichment engine from enrichment_dashboard.py (same 2-pass Claude
pipeline, same Playwright crawling, same prompts).
"""

import io
import os
import re
import json
import time
import random

import streamlit as st
import pandas as pd

from core.db import get_connection

# ── Constants ─────────────────────────────────────────────────────────────────

ALLOWED_ROLES = {"super_admin", "org_admin", "manager", "researcher"}
MODEL = "claude-sonnet-4-6"

# Title scoring — higher = better fit for outreach decision-maker
TITLE_SCORES = {
    # Tier 1 — ideal (90-100)
    "head of growth":           100,
    "vp growth":                100,
    "growth lead":               98,
    "director of growth":        98,
    "head of marketing":         95,
    "vp marketing":              95,
    "chief marketing officer":   95,
    "cmo":                       95,
    "director of marketing":     93,
    "head of performance":       92,
    "performance marketing":     92,
    "head of acquisition":       92,
    "head of demand gen":        90,
    "director of demand gen":    90,
    "head of paid media":        90,
    "head of paid":              90,
    "growth marketing manager":  90,

    # Tier 2 — good (70-89)
    "marketing manager":         85,
    "digital marketing manager": 85,
    "growth manager":            85,
    "performance manager":       82,
    "paid media manager":        82,
    "paid social manager":       80,
    "user acquisition manager":  80,
    "ua manager":                80,
    "brand manager":             78,
    "content marketing manager": 75,
    "social media manager":      72,
    "marketing lead":            78,
    "marketing director":        88,
    "creative director":         70,

    # Tier 3 — acceptable (50-69)
    "founder":                   66,
    "co-founder":                66,
    "ceo":                       65,
    "coo":                       60,
    "managing director":         60,
    "general manager":           58,
    "head of product":           55,
    "product manager":           50,
    "vp product":                55,

    # Reject — wrong department (0-20)
    "sales":                     15,
    "account executive":         15,
    "sdr":                       10,
    "bdr":                       10,
    "account manager":           15,
    "customer success":          10,
    "hr":                         5,
    "human resources":            5,
    "recruiter":                  5,
    "talent":                     5,
    "finance":                    5,
    "legal":                      5,
    "engineer":                  10,
    "developer":                 10,
    "devops":                     5,
    "data analyst":              10,
    "data scientist":            10,
}


def _score_title(title: str) -> tuple[int, str]:
    """
    Score a job title for outreach relevance.
    Returns (score, tier_label).
    """
    if not title:
        return 0, "unknown"

    t = title.lower().strip()

    # Exact match first
    if t in TITLE_SCORES:
        score = TITLE_SCORES[t]
    else:
        # Substring match — pick the highest scoring keyword found
        best = 0
        for keyword, s in TITLE_SCORES.items():
            if keyword in t:
                best = max(best, s)
        score = best

    if score >= 90:   return score, "tier_1"
    if score >= 70:   return score, "tier_2"
    if score >= 50:   return score, "tier_3"
    if score >= 20:   return score, "low"
    if score > 0:     return score, "reject"
    return 0, "unknown"


def _tier_badge(tier: str) -> str:
    """Return coloured HTML badge for a tier label."""
    colors = {
        "tier_1":  "#2ecc71",
        "tier_2":  "#3498db",
        "tier_3":  "#f39c12",
        "low":     "#e67e22",
        "reject":  "#e74c3c",
        "unknown": "#95a5a6",
    }
    bg = colors.get(tier, "#95a5a6")
    return f'<span style="background:{bg};color:#fff;padding:2px 8px;border-radius:4px;font-size:0.8em">{tier}</span>'


# ── Sales Navigator CSV column mapping ────────────────────────────────────────

SN_COLUMN_MAP = {
    # Sales Nav export columns → our standard names
    "first name":    "first_name",
    "last name":     "last_name",
    "full name":     "full_name",
    "title":         "title",
    "job title":     "title",
    "company":       "company_name",
    "company name":  "company_name",
    "account name":  "company_name",
    "person linkedin url":    "linkedin_profile",
    "linkedin url":           "linkedin_profile",
    "profile url":            "linkedin_profile",
    "url":                    "linkedin_profile",
    "location":      "location",
    "geography":     "location",
    "email":         "email",
    "website":       "company_domain",
    "company website": "company_domain",
    "domain":        "company_domain",
}


def _normalize_csv_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Map Sales Navigator / Apollo / generic CSV columns to standard names."""
    rename_map = {}
    for col in df.columns:
        mapped = SN_COLUMN_MAP.get(col.lower().strip())
        if mapped and mapped not in df.columns:
            rename_map[col] = mapped
    df = df.rename(columns=rename_map)

    # Build full_name if missing but first/last present
    if "full_name" not in df.columns and "first_name" in df.columns:
        last = df.get("last_name", pd.Series([""] * len(df)))
        df["full_name"] = (df["first_name"].fillna("") + " " + last.fillna("")).str.strip()

    return df


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — FROM LINKEDIN (contact selection + title scoring)
# ═══════════════════════════════════════════════════════════════════════════════

def _render_tab_from_linkedin(user: dict):
    org_id  = user.get("org_id", 1)
    user_id = user.get("id")

    st.markdown("### Contact Selection & Title Scoring")
    st.markdown(
        "Upload a Sales Navigator CSV export (or any people list) → "
        "auto-score titles → pick the best 1-2 contacts per company → stage for enrichment."
    )

    uploaded = st.file_uploader(
        "Upload people CSV (Sales Navigator export, Apollo, etc.)",
        type=["csv"],
        key="outreach_linkedin_upload",
    )
    if not uploaded:
        st.info("Upload a CSV of people to begin. Expected columns: name, title, company.")
        return

    try:
        df = pd.read_csv(uploaded)
    except Exception as e:
        st.error(f"Could not read CSV: {e}")
        return

    df = _normalize_csv_columns(df)
    df = df.fillna("")

    # Validate minimum columns
    if "company_name" not in df.columns:
        st.error("CSV must contain a `company_name` (or `company` / `account name`) column.")
        return
    if "title" not in df.columns:
        st.warning("No `title` column found — all contacts will score 0.")

    # Score every row
    df["_score"], df["_tier"] = zip(*df["title"].apply(_score_title))

    st.success(f"Loaded **{len(df)}** contacts from **{df['company_name'].nunique()}** companies.")

    # Summary metrics
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total contacts", len(df))
    c2.metric("Tier 1",  int((df["_tier"] == "tier_1").sum()))
    c3.metric("Tier 2",  int((df["_tier"] == "tier_2").sum()))
    c4.metric("Tier 3",  int((df["_tier"] == "tier_3").sum()))
    c5.metric("Rejected", int((df["_tier"] == "reject").sum()))

    # Group by company → show expander per company with contacts
    st.markdown("---")
    st.markdown("### Select contacts per company")
    st.caption(
        "Tier 1 & 2 contacts are **pre-selected**. "
        "Rejected contacts (Sales, HR, etc.) are unchecked. "
        "Adjust as needed, then click **Stage Selected**."
    )

    grouped = df.groupby("company_name", sort=False)

    # Track selections in session state
    if "outreach_selections" not in st.session_state:
        st.session_state["outreach_selections"] = {}

    selections = st.session_state["outreach_selections"]

    for company, group in grouped:
        group_sorted = group.sort_values("_score", ascending=False)
        best_score = group_sorted["_score"].max()
        best_tier  = group_sorted.iloc[0]["_tier"]

        with st.expander(
            f"{company}  —  {len(group_sorted)} contacts  |  best: {best_tier} ({best_score})",
            expanded=(best_tier in ("tier_1", "tier_2")),
        ):
            for idx, row in group_sorted.iterrows():
                name  = row.get("full_name") or f"{row.get('first_name', '')} {row.get('last_name', '')}".strip()
                title = row.get("title", "")
                score = row["_score"]
                tier  = row["_tier"]

                # Pre-select tier 1 & 2, not reject
                default_checked = tier in ("tier_1", "tier_2", "tier_3")

                col_check, col_info = st.columns([1, 5])
                with col_check:
                    checked = st.checkbox(
                        "Select",
                        value=selections.get(idx, default_checked),
                        key=f"sel_{idx}",
                        label_visibility="collapsed",
                    )
                    selections[idx] = checked
                with col_info:
                    badge = _tier_badge(tier)
                    li_url = row.get("linkedin_profile", "")
                    li_link = f' — <a href="{li_url}" target="_blank">LinkedIn</a>' if li_url else ""
                    st.markdown(
                        f"**{name}** — {title}  {badge}  (score: {score}){li_link}",
                        unsafe_allow_html=True,
                    )

    st.session_state["outreach_selections"] = selections

    # Stage button
    st.markdown("---")
    selected_indices = [idx for idx, checked in selections.items() if checked]
    selected_df = df.loc[df.index.isin(selected_indices)].copy()

    st.markdown(f"**{len(selected_df)}** contacts selected across **{selected_df['company_name'].nunique()}** companies.")

    if st.button("Stage Selected for Enrichment", type="primary", disabled=len(selected_df) == 0):
        conn = get_connection()
        staged = 0
        try:
            for _, row in selected_df.iterrows():
                first = row.get("first_name", "")
                last  = row.get("last_name", "")
                if not first and "full_name" in row:
                    parts = str(row["full_name"]).split(" ", 1)
                    first = parts[0]
                    last  = parts[1] if len(parts) > 1 else ""

                score, tier = _score_title(row.get("title", ""))

                conn.execute("""
                    INSERT INTO outreach_contacts
                        (org_id, first_name, last_name, email, title,
                         linkedin_profile, company_name, company_domain,
                         title_score, title_tier, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'staged')
                """, (
                    org_id,
                    first, last,
                    row.get("email", ""),
                    row.get("title", ""),
                    row.get("linkedin_profile", ""),
                    row.get("company_name", ""),
                    row.get("company_domain", ""),
                    score, tier,
                ))
                staged += 1
            conn.commit()
        finally:
            conn.close()

        st.success(f"Staged **{staged}** contacts. Go to the **Upload & Enrich** tab to run enrichment.")
        st.session_state.pop("outreach_selections", None)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — UPLOAD & ENRICH (Claude pipeline)
# ═══════════════════════════════════════════════════════════════════════════════

def _render_tab_enrich(user: dict):
    org_id  = user.get("org_id", 1)
    user_id = user.get("id")

    st.markdown("### Enrich Staged Contacts")
    st.markdown(
        "Runs the 2-pass Claude pipeline on staged contacts: "
        "scrape website → extract intelligence → generate hook."
    )

    # AI budget check
    from core.ai_tracker import can_use_ai
    ok, budget_msg = can_use_ai(org_id)
    if not ok:
        st.error(budget_msg)
        return
    if budget_msg:
        st.warning(budget_msg)

    # Load staged contacts
    conn = get_connection()
    staged = conn.execute(
        "SELECT * FROM outreach_contacts WHERE org_id=? AND status='staged' ORDER BY company_name",
        (org_id,),
    ).fetchall()
    conn.close()

    if not staged:
        st.info("No staged contacts. Use the **From LinkedIn** tab to stage contacts first.")

        # Alternative: direct CSV upload
        st.markdown("---")
        st.markdown("#### Or upload a company CSV directly")
        st.caption(
            "**Required:** `company_name` + one of "
            "`company_domain` / `company_website` / `website` / `domain`  |  "
            "**Optional:** `first_name`, `last_name`, `email`, `title`"
        )

        uploaded = st.file_uploader("Choose CSV file", type=["csv"], key="outreach_direct_upload")
        if uploaded:
            try:
                df = pd.read_csv(uploaded)
            except Exception as e:
                st.error(f"Could not read CSV: {e}")
                return

            df = _normalize_csv_columns(df)
            df = df.fillna("")

            if "company_name" not in df.columns:
                st.error("CSV must contain a `company_name` column.")
                return

            # Insert as staged contacts
            conn = get_connection()
            count = 0
            try:
                for _, row in df.iterrows():
                    domain_col = ""
                    for c in ["company_domain", "company_website", "website", "domain"]:
                        if row.get(c):
                            domain_col = str(row[c]).strip()
                            break

                    score, tier = _score_title(row.get("title", ""))
                    conn.execute("""
                        INSERT INTO outreach_contacts
                            (org_id, first_name, last_name, email, title,
                             company_name, company_domain,
                             title_score, title_tier, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'staged')
                    """, (
                        org_id,
                        row.get("first_name", ""),
                        row.get("last_name", ""),
                        row.get("email", ""),
                        row.get("title", ""),
                        row.get("company_name", ""),
                        domain_col,
                        score, tier,
                    ))
                    count += 1
                conn.commit()
            finally:
                conn.close()

            st.success(f"Imported **{count}** contacts as staged. Click **Run Enrichment** below.")
            st.rerun()

        return

    # Show staged contacts summary
    staged_df = pd.DataFrame(staged)
    unique_companies = staged_df["company_name"].nunique()
    with_domain = staged_df[staged_df["company_domain"].astype(str).str.strip() != ""]

    c1, c2, c3 = st.columns(3)
    c1.metric("Staged contacts", len(staged_df))
    c2.metric("Companies",       unique_companies)
    c3.metric("With domain",     len(with_domain))

    with st.expander("Preview staged contacts"):
        display = staged_df[["first_name", "last_name", "title", "company_name",
                              "company_domain", "title_score", "title_tier"]].copy()
        st.dataframe(display, use_container_width=True)

    if staged_df["company_domain"].astype(str).str.strip().eq("").all():
        st.warning(
            "No company domains found. Add `company_domain` in the CSV "
            "or the pipeline won't be able to scrape websites."
        )

    # Run enrichment
    if st.button("Run Enrichment Pipeline", type="primary"):
        _run_enrichment_pipeline(staged, org_id, user_id)


def _run_enrichment_pipeline(staged: list[dict], org_id: int, user_id: int):
    """Run the 2-pass Claude pipeline on staged outreach_contacts."""
    # Import enrichment engine from the enrichment dashboard
    from dashboards.enrichment_dashboard import (
        PAGES_TO_SCRAPE, MAX_CHARS_PER_PAGE, MAX_MERGED_CHARS,
        _fetch_page_text, _classify_company, _generate_hook,
        _calculate_fit_score,
    )
    from core.ai_tracker import can_use_ai

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        st.error("Playwright not installed. Run: `pip install playwright && playwright install chromium`")
        return

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        st.error("ANTHROPIC_API_KEY environment variable is not set.")
        return

    import anthropic as _ant

    try:
        from playwright_stealth import stealth_sync as _stealth_fn
        _stealth_available = True
    except ImportError:
        _stealth_available = False

    # Deduplicate by company_domain — enrich each company once
    companies = {}
    for row in staged:
        domain = re.sub(r"^https?://", "", str(row.get("company_domain", "")).strip()).split("/")[0]
        if domain and domain not in companies:
            companies[domain] = {
                "company_name": row["company_name"],
                "company_domain": domain,
            }

    if not companies:
        st.error("No contacts have a company_domain — cannot scrape websites.")
        return

    progress_bar = st.progress(0)
    status_text  = st.empty()
    total = len(companies)
    enrichment_cache = {}  # domain → result dict

    _DESKTOP_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(
            user_agent=_DESKTOP_UA,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        if _stealth_available:
            ctx.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            """)

        client = _ant.Anthropic(api_key=api_key)

        for idx, (domain, info) in enumerate(companies.items()):
            cname = info["company_name"]
            status_text.text(f"Enriching {idx + 1}/{total}: {cname} ({domain})")

            # Budget check every 10
            if idx % 10 == 0:
                ok, msg = can_use_ai(org_id)
                if not ok:
                    st.error(f"AI budget exceeded at company {idx + 1}. {msg}")
                    break

            # Stage 1: Scrape pages
            page_blocks = []
            for paths, label in PAGES_TO_SCRAPE:
                text = _fetch_page_text(ctx, domain, paths)
                if text:
                    page_blocks.append(f"[{label.upper()}]\n{text}")

            pages_fetched = len(page_blocks)
            result = {"pages_fetched": pages_fetched, "website_status": "ok", "error": ""}

            if not page_blocks:
                result["website_status"] = "website_unavailable"
                result["error"] = "Could not fetch any pages"
                enrichment_cache[domain] = result
                progress_bar.progress((idx + 1) / total)
                continue

            merged_text = "\n\n".join(page_blocks)

            # Stage 2: Claude classification
            try:
                cls = _classify_company(client, cname, domain, merged_text, org_id, user_id)
            except Exception as e:
                result["error"] = f"Classification failed: {e}"
                enrichment_cache[domain] = result
                progress_bar.progress((idx + 1) / total)
                continue

            fit_score, fit_label = _calculate_fit_score(cls)
            result.update({
                "classification": cls,
                "fit_score": fit_score,
                "fit_label": fit_label,
            })

            # Stage 3: Hook generation (high / medium only)
            if fit_label in ("high", "medium"):
                try:
                    result["account_hook"] = _generate_hook(client, cls, org_id, user_id)
                except Exception as e:
                    result["error"] = f"Hook failed: {e}"

            enrichment_cache[domain] = result
            progress_bar.progress((idx + 1) / total)

        ctx.close()
        browser.close()

    # Write results back to outreach_contacts
    conn = get_connection()
    enriched_count = 0
    try:
        for row in staged:
            domain = re.sub(r"^https?://", "", str(row.get("company_domain", "")).strip()).split("/")[0]
            cached = enrichment_cache.get(domain)
            if not cached:
                continue

            cls = cached.get("classification", {})
            conn.execute("""
                UPDATE outreach_contacts SET
                    company_summary    = ?,
                    industry           = ?,
                    product_or_service = ?,
                    business_model     = ?,
                    target_customer    = ?,
                    primary_markets    = ?,
                    marketing_channels = ?,
                    influencer_usage   = ?,
                    hiring_signal      = ?,
                    recent_signal      = ?,
                    intelligence_raw   = ?,
                    account_hook       = ?,
                    pages_fetched      = ?,
                    website_status     = ?,
                    error              = ?,
                    status             = 'enriched',
                    enriched_at        = datetime('now')
                WHERE id = ?
            """, (
                cls.get("reason_summary", ""),
                cls.get("company_type", ""),
                cls.get("target_market", ""),
                cls.get("business_model", ""),
                cls.get("target_customer", ""),
                cls.get("primary_markets", ""),
                str(cls.get("paid_ads_signal", "")),
                str(cls.get("ugc_need_signal", "")),
                str(cls.get("creative_volume_signal", "")),
                str(cls.get("ai_signal", "")),
                json.dumps(cls) if cls else "",
                cached.get("account_hook", ""),
                cached.get("pages_fetched", 0),
                cached.get("website_status", ""),
                cached.get("error", ""),
                row["id"],
            ))
            enriched_count += 1
        conn.commit()
    finally:
        conn.close()

    status_text.text(f"Done. Enriched {enriched_count} contacts across {len(enrichment_cache)} companies.")
    st.success(f"Enriched **{enriched_count}** contacts. Check the **Inventory** tab for results.")
    st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — INVENTORY (view + export)
# ═══════════════════════════════════════════════════════════════════════════════

def _render_tab_inventory(user: dict):
    org_id = user.get("org_id", 1)

    st.markdown("### Outreach Inventory")

    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM outreach_contacts WHERE org_id=? ORDER BY created_at DESC",
        (org_id,),
    ).fetchall()
    conn.close()

    if not rows:
        st.info("No outreach contacts yet. Stage contacts via the **From LinkedIn** tab.")
        return

    df = pd.DataFrame(rows)

    # Filters
    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        status_filter = st.multiselect(
            "Status",
            options=sorted(df["status"].unique()),
            default=list(df["status"].unique()),
        )
    with col_f2:
        tier_options = sorted(df["title_tier"].dropna().unique()) if "title_tier" in df.columns else []
        tier_filter = st.multiselect(
            "Title tier",
            options=tier_options,
            default=tier_options,
        ) if tier_options else []
    with col_f3:
        company_filter = st.text_input("Company search", "")

    filtered = df[df["status"].isin(status_filter)]
    if tier_filter:
        filtered = filtered[filtered["title_tier"].isin(tier_filter)]
    if company_filter:
        filtered = filtered[filtered["company_name"].str.contains(company_filter, case=False, na=False)]

    # Metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total",    len(filtered))
    c2.metric("Staged",   int((filtered["status"] == "staged").sum()))
    c3.metric("Enriched", int((filtered["status"] == "enriched").sum()))
    c4.metric("Exported", int((filtered["status"] == "exported").sum()))

    # Results table
    display_cols = [
        "first_name", "last_name", "title", "title_score", "title_tier",
        "company_name", "company_domain", "account_hook",
        "company_summary", "industry", "influencer_usage",
        "status", "enriched_at",
    ]
    display_cols = [c for c in display_cols if c in filtered.columns]
    st.dataframe(filtered[display_cols], use_container_width=True, height=450)

    # Detail expanders for enriched contacts
    enriched = filtered[filtered["status"] == "enriched"]
    if not enriched.empty:
        st.markdown("---")
        st.markdown("### Enriched Contact Details")
        for _, row in enriched.iterrows():
            label = f"{row.get('first_name', '')} {row.get('last_name', '')} — {row.get('company_name', '')}"
            with st.expander(label):
                col_a, col_b = st.columns(2)
                with col_a:
                    st.markdown("**Company Intelligence**")
                    st.markdown(f"- **Summary:** {row.get('company_summary', '—')}")
                    st.markdown(f"- **Industry:** {row.get('industry', '—')}")
                    st.markdown(f"- **Product:** {row.get('product_or_service', '—')}")
                    st.markdown(f"- **Business model:** {row.get('business_model', '—')}")
                    st.markdown(f"- **Target customer:** {row.get('target_customer', '—')}")
                    st.markdown(f"- **Markets:** {row.get('primary_markets', '—')}")
                    st.markdown(f"- **Marketing channels:** {row.get('marketing_channels', '—')}")
                    st.markdown(f"- **Influencer/UGC:** {row.get('influencer_usage', '—')}")
                    st.markdown(f"- **Hiring signal:** {row.get('hiring_signal', '—')}")
                with col_b:
                    st.markdown("**Outreach Hook**")
                    hook = row.get("account_hook", "")
                    if hook:
                        st.success(hook)
                    else:
                        st.warning("No hook generated (may be low fit)")

                    st.markdown(f"**Title:** {row.get('title', '—')} (score: {row.get('title_score', 0)})")
                    li = row.get("linkedin_profile", "")
                    if li:
                        st.markdown(f"[LinkedIn profile]({li})")

    # Export section
    st.markdown("---")
    st.markdown("### Export")

    export_df = filtered.copy()
    col_e1, col_e2, col_e3 = st.columns(3)

    with col_e1:
        st.caption("**Instantly CSV** — first_name, email, company, account_hook")
        instantly_cols = [c for c in ("first_name", "email", "company_name", "account_hook")
                          if c in export_df.columns]
        instantly_df = export_df[export_df["account_hook"].astype(str).str.strip() != ""][instantly_cols]
        instantly_df = instantly_df.rename(columns={"company_name": "company"})
        st.download_button(
            label=f"Instantly CSV ({len(instantly_df)} rows)",
            data=instantly_df.to_csv(index=False),
            file_name="outreach_instantly.csv",
            mime="text/csv",
            type="primary",
        )

    with col_e2:
        st.caption("**Full CSV** — all fields")
        st.download_button(
            label=f"Full CSV ({len(export_df)} rows)",
            data=export_df.to_csv(index=False),
            file_name="outreach_full.csv",
            mime="text/csv",
        )

    with col_e3:
        st.caption("**Mark as exported**")
        enriched_ids = list(filtered[filtered["status"] == "enriched"]["id"])
        if enriched_ids and st.button(f"Mark {len(enriched_ids)} enriched → exported"):
            conn = get_connection()
            try:
                conn.executemany(
                    "UPDATE outreach_contacts SET status='exported' WHERE id=?",
                    [(i,) for i in enriched_ids],
                )
                conn.commit()
            finally:
                conn.close()
            st.success(f"Marked {len(enriched_ids)} contacts as exported.")
            st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN RENDER
# ═══════════════════════════════════════════════════════════════════════════════

def render(user: dict):
    role = user.get("role", "researcher")
    if role not in ALLOWED_ROLES:
        st.error("You don't have permission to access this page.")
        return

    st.markdown("## Outreach Pipeline")

    tab1, tab2, tab3 = st.tabs([
        "From LinkedIn",
        "Upload & Enrich",
        "Inventory",
    ])

    with tab1:
        _render_tab_from_linkedin(user)
    with tab2:
        _render_tab_enrich(user)
    with tab3:
        _render_tab_inventory(user)
