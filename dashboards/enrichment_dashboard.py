"""
dashboards/enrichment_dashboard.py — Dashin Research Platform

Account Intelligence Enrichment Pipeline
Upload a CSV of companies → scrape website pages → Claude classification →
deterministic fit scoring → hook generation → Instantly-ready CSV export.

Two-pass Claude pipeline (anti-hallucination):
  Pass 1: Extract structured facts from website text → classification JSON
  Pass 2: Convert classification JSON → one 20-word account_hook sentence

Does NOT touch any existing scraper or lead tables.
"""

import io
import os
import re
import json
import time
import random

import streamlit as st
import pandas as pd

# ── Constants ─────────────────────────────────────────────────────────────────

ALLOWED_ROLES = {"super_admin", "org_admin", "manager", "researcher"}
MODEL = "claude-sonnet-4-6"

# Pages to attempt scraping per company.
# Each entry is (candidate_paths, label) — first path that returns content wins.
PAGES_TO_SCRAPE = [
    ([""],                                              "homepage"),
    (["/about", "/about-us", "/company", "/who-we-are"], "about"),
    (["/pricing", "/plans", "/price", "/plan"],          "pricing"),
    (["/product", "/features", "/platform", "/solution", "/how-it-works"], "product"),
    (["/careers", "/jobs", "/join-us", "/work-with-us"], "careers"),
]

# Domain column names accepted from CSV (tried in order)
DOMAIN_COLUMNS = ["company_domain", "company_website", "website", "domain"]

# Max chars extracted per page before sending to Claude
MAX_CHARS_PER_PAGE = 4000

# Max combined chars sent to classification prompt
MAX_MERGED_CHARS = 12000

# ── Prompts ───────────────────────────────────────────────────────────────────

CLASSIFICATION_PROMPT = """\
You are a B2B intelligence analyst qualifying companies for UGC (user-generated content) \
creative services.

We are looking for B2C tech companies that likely need high-volume ad creatives.

Ideal targets:
- B2C or B2SMB software, SaaS, or mobile apps
- AI tools, productivity apps, fintech, health apps, learning apps, dating apps
- Already run paid acquisition (Meta, TikTok, YouTube)
- Likely need high volume of ad creatives (UGC)

We do NOT want:
- B2B SaaS with no paid acquisition
- Marketing or creative agencies
- E-commerce/Shopify brands (beauty, fashion, supplements, CPG)
- Hardware-only companies
- Enterprise IT services

Company: {company_name}
Website: {company_website}

Website content (homepage, about, pricing, product, careers):
---
{website_text}
---

Analyze the company and return ONLY valid JSON matching this exact schema. \
Do not include any text outside the JSON block:

{{
  "company_name": "string",
  "company_website": "string",
  "is_tech_company": true,
  "company_type": "ai_tool | mobile_app | b2c_saas | gaming | marketplace | other",
  "target_market": "b2c | b2smb | b2b | mixed | unknown",
  "paid_ads_signal": true,
  "paid_ads_confidence": "high | medium | low",
  "ugc_need_signal": true,
  "ugc_need_confidence": "high | medium | low",
  "ai_signal": true,
  "subscription_signal": true,
  "mobile_app_signal": true,
  "creative_volume_signal": true,
  "excluded_segment": false,
  "excluded_reason": "",
  "reason_summary": "1-2 sentences explaining the fit assessment",
  "evidence": [
    {{
      "signal_type": "paid_ads | ugc_need | tech | exclusion | subscription | ai | mobile_app | creative_volume",
      "source": "homepage | pricing | careers | about | other",
      "quote_or_fact": "exact quote or fact from the content"
    }}
  ]
}}"""

HOOK_PROMPT = """\
You are writing a short outreach context sentence for a cold email.

Use ONLY the factual company information provided below.
Do not invent information. Do not exaggerate. Do not compliment the company. \
Do not use marketing buzzwords.

Company intelligence:
{classification_json}

Your task:
Write ONE short sentence (max 20 words) explaining why this company is relevant \
for UGC (user-generated content) creative services outreach.

Rules:
1. Use only the provided facts.
2. If paid_ads_signal is true, mention it.
3. If ai_signal or mobile_app_signal is true, mention it.
4. Otherwise reference the company_type or target_market.
5. Keep it neutral and factual.
6. Start with "Saw that", "Noticed", or "Looks like".

Return only the sentence. No quotes, no explanation."""


# ── Playwright helpers ────────────────────────────────────────────────────────

def _fetch_page_text(
    browser_context,
    domain: str,
    paths: list[str],
    timeout_ms: int = 15000,
) -> str | None:
    """
    Try each path in order and return body text from the first that succeeds.
    Truncates to MAX_CHARS_PER_PAGE. Returns None if all paths fail.
    """
    for path in paths:
        url = f"https://{domain}{path}"
        page = None
        try:
            page = browser_context.new_page()
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            time.sleep(random.uniform(1.2, 2.5))  # randomised JS rendering wait
            text = page.inner_text("body")
            if text and len(text.strip()) > 80:  # skip near-empty pages (redirects/errors)
                return text[:MAX_CHARS_PER_PAGE].strip()
        except Exception:
            pass
        finally:
            if page:
                try:
                    page.close()
                except Exception:
                    pass
    return None


# ── Claude helpers ────────────────────────────────────────────────────────────

def _classify_company(
    client,
    company_name: str,
    domain: str,
    merged_text: str,
    org_id: int,
    user_id: int,
) -> dict:
    """
    Pass 1: Send merged website text to Claude, get classification JSON back.
    Logs usage via ai_tracker. Raises on parse failure.
    """
    from core.ai_tracker import log_usage

    prompt = CLASSIFICATION_PROMPT.format(
        company_name=company_name,
        company_website=domain,
        website_text=merged_text[:MAX_MERGED_CHARS],
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    log_usage(
        org_id=org_id,
        tokens_input=response.usage.input_tokens,
        tokens_output=response.usage.output_tokens,
        feature="enrichment_classify",
        model=MODEL,
        user_id=user_id,
    )

    raw = response.content[0].text.strip()
    # Extract JSON block even if Claude wraps it in ```json ... ```
    match = re.search(r"\{[\s\S]+\}", raw)
    if match:
        raw = match.group(0)
    return json.loads(raw)


def _generate_hook(
    client,
    cls: dict,
    org_id: int,
    user_id: int,
) -> str:
    """
    Pass 2: Convert classification JSON → one 20-word account_hook sentence.
    Logs usage via ai_tracker. Returns empty string on failure.
    """
    from core.ai_tracker import log_usage

    prompt = HOOK_PROMPT.format(classification_json=json.dumps(cls, indent=2))

    response = client.messages.create(
        model=MODEL,
        max_tokens=80,
        messages=[{"role": "user", "content": prompt}],
    )

    log_usage(
        org_id=org_id,
        tokens_input=response.usage.input_tokens,
        tokens_output=response.usage.output_tokens,
        feature="enrichment_hook",
        model=MODEL,
        user_id=user_id,
    )

    return response.content[0].text.strip().strip('"').strip("'")


# ── Scoring ───────────────────────────────────────────────────────────────────

def _calculate_fit_score(cls: dict) -> tuple[int, str]:
    """
    Deterministic scoring from classification JSON.
    Returns (score: int, label: str).
    Score bands: 80+ = high, 55-79 = medium, 30-54 = low, <30 = reject.
    """
    score = 0
    if cls.get("is_tech_company"):                              score += 25
    if cls.get("target_market") in ("b2c", "b2smb", "mixed"):  score += 20
    if cls.get("paid_ads_signal"):                              score += 20
    if cls.get("ugc_need_signal"):                              score += 15
    if cls.get("ai_signal"):                                    score += 10
    if cls.get("mobile_app_signal"):                            score += 10
    if cls.get("subscription_signal"):                          score +=  5
    if cls.get("creative_volume_signal"):                       score +=  5
    if cls.get("excluded_segment"):                             score -= 50

    if score >= 80:   label = "high"
    elif score >= 55: label = "medium"
    elif score >= 30: label = "low"
    else:             label = "reject"

    return score, label


# ── Per-company pipeline ──────────────────────────────────────────────────────

def _process_company(
    browser_context,
    client,
    row: dict,
    org_id: int,
    user_id: int,
) -> dict:
    """
    Full enrichment pipeline for one company row.
    Never raises — all errors are captured in result["error"].
    """
    company_name = str(row.get("company_name", "")).strip()

    # Accept any recognised domain/website column; strip protocol + path
    raw_domain = ""
    for col in DOMAIN_COLUMNS:
        val = str(row.get(col, "")).strip()
        if val:
            raw_domain = val.lower()
            break
    domain = re.sub(r"^https?://", "", raw_domain).split("/")[0].split("?")[0]

    result = {
        "company_name":  company_name,
        "company_domain": domain,
        "first_name":    row.get("first_name", ""),
        "email":         row.get("email", ""),
        "pages_fetched": 0,
        "website_status": "ok",
        "fit_score":     0,
        "fit_label":     "reject",
        "company_type":  "",
        "target_market": "",
        "paid_ads_signal": "",
        "reason_summary": "",
        "account_hook":  "",
        "error":         "",
    }

    if not domain:
        result["website_status"] = "no_domain"
        result["error"] = "Missing company_domain"
        return result

    # ── Stage 1: Scrape pages ─────────────────────────────────────────────
    page_blocks = []
    for paths, label in PAGES_TO_SCRAPE:
        text = _fetch_page_text(browser_context, domain, paths)
        if text:
            page_blocks.append(f"[{label.upper()}]\n{text}")
            result["pages_fetched"] += 1

    if not page_blocks:
        result["website_status"] = "website_unavailable"
        result["error"] = "Could not fetch any pages — site may be blocking or offline"
        return result

    merged_text = "\n\n".join(page_blocks)

    # ── Stage 2: Claude classification ───────────────────────────────────
    try:
        cls = _classify_company(client, company_name, domain, merged_text, org_id, user_id)
    except json.JSONDecodeError as e:
        result["website_status"] = "parse_error"
        result["error"] = f"Claude returned invalid JSON: {e}"
        return result
    except Exception as e:
        result["website_status"] = "classify_error"
        result["error"] = f"Classification failed: {e}"
        return result

    fit_score, fit_label = _calculate_fit_score(cls)
    result.update({
        "fit_score":      fit_score,
        "fit_label":      fit_label,
        "company_type":   cls.get("company_type", ""),
        "target_market":  cls.get("target_market", ""),
        "paid_ads_signal": str(cls.get("paid_ads_signal", "")),
        "reason_summary": cls.get("reason_summary", ""),
    })

    # ── Stage 3: Hook generation (high / medium only) ─────────────────────
    if fit_label in ("high", "medium"):
        try:
            result["account_hook"] = _generate_hook(client, cls, org_id, user_id)
        except Exception as e:
            result["error"] = f"Hook generation failed: {e}"

    return result


# ── Dashboard render ──────────────────────────────────────────────────────────

def render(user: dict):
    role    = user.get("role", "researcher")
    org_id  = user.get("org_id", 1)
    user_id = user.get("id")

    if role not in ALLOWED_ROLES:
        st.error("🚫 You don't have permission to access this page.")
        return

    st.markdown("## Account Intelligence Enrichment")
    st.markdown(
        "Upload a company list → scrape websites → AI classification → "
        "fit scoring → hook generation → download Instantly-ready CSV."
    )

    # ── AI budget check ────────────────────────────────────────────────────
    from core.ai_tracker import can_use_ai
    ok, budget_msg = can_use_ai(org_id)
    if not ok:
        st.error(budget_msg)
        return
    if budget_msg:
        st.warning(budget_msg)

    # ── Step 1: Upload ─────────────────────────────────────────────────────
    st.markdown("### 1. Upload company list")
    st.caption(
        "**Required:** `company_name` + one of "
        "`company_domain` / `company_website` / `website` / `domain`  |  "
        "**Optional (passed through):** `first_name`, `email`"
    )

    uploaded = st.file_uploader("Choose CSV file", type=["csv"])
    if not uploaded:
        st.info("Upload a CSV to begin.")
        return

    try:
        df = pd.read_csv(uploaded)
    except Exception as e:
        st.error(f"Could not read CSV: {e}")
        return

    if "company_name" not in df.columns:
        st.error("CSV is missing required column: `company_name`")
        return
    if not any(c in df.columns for c in DOMAIN_COLUMNS):
        st.error(
            f"CSV must have a domain/website column. Accepted names: "
            f"{', '.join(f'`{c}`' for c in DOMAIN_COLUMNS)}"
        )
        return

    df = df.fillna("")
    st.success(f"Loaded **{len(df)}** companies.")
    with st.expander("Preview (first 5 rows)"):
        st.dataframe(df.head(5), use_container_width=True)

    # ── Step 2: Settings ───────────────────────────────────────────────────
    st.markdown("### 2. Export filter")
    min_fit = st.selectbox(
        "Minimum fit label to include in the Instantly export",
        options=["high only", "high + medium", "all (including low)"],
        index=1,
    )

    # ── Step 3: Run ────────────────────────────────────────────────────────
    st.markdown("### 3. Run enrichment")

    col_run, col_clear = st.columns([2, 1])
    run_clicked   = col_run.button("Run Enrichment Pipeline", type="primary")
    clear_clicked = col_clear.button("Clear results")

    if clear_clicked:
        st.session_state.pop("enrichment_results", None)
        st.rerun()

    if run_clicked:
        # Dependency checks
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            st.error(
                "Playwright is not installed. "
                "Run: `pip install playwright && playwright install chromium`"
            )
            return

        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            st.error("ANTHROPIC_API_KEY environment variable is not set.")
            return

        import anthropic as _ant

        # Optional stealth mode — patches navigator.webdriver and canvas fingerprints
        try:
            from playwright_stealth import stealth_sync as _stealth_fn
            _stealth_available = True
        except ImportError:
            _stealth_available = False

        results      = []
        progress_bar = st.progress(0)
        status_text  = st.empty()
        total        = len(df)

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

            # Inject stealth patches into every new page opened from this context
            if _stealth_available:
                ctx.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                """)

            client = _ant.Anthropic(api_key=api_key)

            for idx, row in enumerate(df.to_dict("records")):
                cname = str(row.get("company_name", "")).strip() or f"Row {idx + 1}"
                status_text.text(f"Processing {idx + 1}/{total}: {cname}")

                # Re-check budget every 10 companies to catch mid-run overruns
                if idx % 10 == 0:
                    ok, msg = can_use_ai(org_id)
                    if not ok:
                        st.error(f"AI budget exceeded — stopped at row {idx + 1}. {msg}")
                        break

                result = _process_company(ctx, client, row, org_id, user_id)
                results.append(result)
                progress_bar.progress((idx + 1) / total)

            ctx.close()
            browser.close()

        status_text.text(f"Done. Processed {len(results)}/{total} companies.")
        st.session_state["enrichment_results"] = results
        st.rerun()

    # ── Step 4: Results ────────────────────────────────────────────────────
    results = st.session_state.get("enrichment_results")
    if not results:
        return

    st.markdown("### 4. Results")
    results_df = pd.DataFrame(results)

    # Summary metrics
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Processed",   len(results_df))
    c2.metric("High fit",    int((results_df["fit_label"] == "high").sum()))
    c3.metric("Medium fit",  int((results_df["fit_label"] == "medium").sum()))
    c4.metric("Low fit",     int((results_df["fit_label"] == "low").sum()))
    c5.metric("Rejected",    int((results_df["fit_label"] == "reject").sum()))

    # Results table (key columns)
    display_cols = [
        "company_name", "company_domain", "fit_score", "fit_label",
        "company_type", "target_market", "paid_ads_signal",
        "reason_summary", "account_hook", "pages_fetched", "error",
    ]
    display_cols = [c for c in display_cols if c in results_df.columns]
    st.dataframe(results_df[display_cols], use_container_width=True, height=400)

    # Failures summary
    failures = results_df[results_df["error"] != ""]
    if not failures.empty:
        with st.expander(f"Errors / skipped ({len(failures)} rows)"):
            st.dataframe(
                failures[["company_name", "company_domain", "website_status", "error"]],
                use_container_width=True,
            )

    # ── Step 5: Export ─────────────────────────────────────────────────────
    st.markdown("### 5. Export")

    # Apply filter
    export_df = results_df.copy()
    if min_fit == "high only":
        export_df = export_df[export_df["fit_label"] == "high"]
    elif min_fit == "high + medium":
        export_df = export_df[export_df["fit_label"].isin(["high", "medium"])]

    st.caption(f"{len(export_df)} rows match the selected filter: **{min_fit}**")

    col_a, col_b = st.columns(2)

    with col_a:
        st.caption("Instantly upload CSV — `first_name`, `email`, `company`, `account_hook`")
        instantly_cols = [c for c in ("first_name", "email", "company_name", "account_hook")
                          if c in export_df.columns]
        instantly_df = export_df[instantly_cols].rename(columns={"company_name": "company"})
        st.download_button(
            label=f"Download Instantly CSV ({len(instantly_df)} rows)",
            data=instantly_df.to_csv(index=False),
            file_name="instantly_upload.csv",
            mime="text/csv",
            type="primary",
        )

    with col_b:
        st.caption("Full enrichment CSV — all fields including fit_score and reason_summary")
        st.download_button(
            label=f"Download Full CSV ({len(export_df)} rows)",
            data=export_df.to_csv(index=False),
            file_name="enrichment_full.csv",
            mime="text/csv",
        )
