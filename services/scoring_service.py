"""
services/scoring_service.py — configurable AI scoring (Module B2 + B3).

Replaces the banned deterministic keyword scorer (crawler_v2.analyze_site — the
method that ranked WHO Foundation top-3 on hospital mentions). Scoring here is an
AI *judgment* step that reads each company's actual crawled site text against a
plain-language niche profile. Nothing about scoring is hardcoded: a new niche is
a new row in scoring_profiles, no code change.

Two scoring routes, both driven by the same profile + crawled text:

  1. score_companies_claude(...)   — in-app, Anthropic. Budget-gated via
                                     ai_tracker.can_use_ai and logged via
                                     log_usage. Costs tokens, so it only runs on
                                     an explicit call.
  2. export_for_gpt(...) / import_scored_csv(...) — zero in-app cost. Writes a
                                     scoring-ready CSV + a companion prompt .txt
                                     so the scores can be produced externally in
                                     GPT, then re-imported and matched back by
                                     domain.

cross_validate(...) merges a Claude run and a GPT run and flags any company where
the two models disagree by more than 20 points — the proven Proving-Health
workflow, made native.

Every route returns the same per-company result shape:
    {
      "company_name":      str,
      "domain":            str,
      "score":             int (0-100),
      "tier":              "A".."E",
      "rationale":         str,
      "best_contact":      str,     # suggestion, "" if none
      "contradiction":     bool,    # site text contradicts the stated industry
      "not_site_verified": bool,    # crawl was thin/blocked/failed → scored on
                                    #   whatever text existed; treat with caution
      "model":             str,
      "error":             str,     # "" on success
    }
"""

import os
import re
import csv
import json

from core.db import get_connection

# Scoring model. Sonnet-class judgment is worth it here — the whole point is to
# avoid the shallow keyword matching that produced false positives.
DEFAULT_MODEL = "claude-sonnet-4-6"

# Text shorter than this means the crawl gathered almost nothing — the score is
# then based on the industry label alone and must be flagged not_site_verified.
_MIN_VERIFIABLE_TEXT = 200


# ══════════════════════════════════════════════════════════════════════════════
# Profile CRUD (B2)
# ══════════════════════════════════════════════════════════════════════════════

def create_profile(org_id: int, name: str, niche_description: str = "",
                   gate_rules: str = "", score_criteria: str = "",
                   disqualifiers: str = "", opener_tone_notes: str = "",
                   created_by: int = None) -> int:
    """Create a scoring profile. Returns the new profile id."""
    conn = get_connection()
    try:
        cur = conn.execute("""
            INSERT INTO scoring_profiles
                (org_id, name, niche_description, gate_rules, score_criteria,
                 disqualifiers, opener_tone_notes, created_by)
            VALUES (?,?,?,?,?,?,?,?)
        """, (org_id, name, niche_description, gate_rules, score_criteria,
              disqualifiers, opener_tone_notes, created_by))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_profiles(org_id: int) -> list:
    """All scoring profiles for an org, newest first."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM scoring_profiles WHERE org_id=? ORDER BY created_at DESC",
            (org_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_profile(profile_id: int) -> dict:
    """One scoring profile by id, or None."""
    conn = get_connection()
    try:
        r = conn.execute("SELECT * FROM scoring_profiles WHERE id=?",
                         (profile_id,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def update_profile(profile_id: int, **fields) -> None:
    """Update named columns on a profile (name, niche_description, gate_rules, ...)."""
    allowed = {"name", "niche_description", "gate_rules", "score_criteria",
               "disqualifiers", "opener_tone_notes"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    cols = ", ".join(f"{k}=?" for k in sets)
    conn = get_connection()
    try:
        conn.execute(f"UPDATE scoring_profiles SET {cols} WHERE id=?",
                     (*sets.values(), profile_id))
        conn.commit()
    finally:
        conn.close()


def delete_profile(profile_id: int, org_id: int) -> None:
    """Delete a profile (org-scoped so one org can't delete another's)."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM scoring_profiles WHERE id=? AND org_id=?",
                     (profile_id, org_id))
        conn.commit()
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# Shared helpers
# ══════════════════════════════════════════════════════════════════════════════

def normalize_domain(url_or_domain: str) -> str:
    """
    Reduce a URL or messy domain string to a bare host for matching, e.g.
    'https://www.Acme.io/about?x=1' → 'acme.io'. Used to match re-imported GPT
    scores back to the right company regardless of URL formatting.
    """
    if not url_or_domain:
        return ""
    s = str(url_or_domain).strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    s = s.split("/")[0].split("?")[0].split("#")[0]
    return s.strip()


def tier_from_score(score) -> str:
    """Fallback A–E banding when a model omits an explicit tier."""
    try:
        s = int(score)
    except (TypeError, ValueError):
        return "E"
    if s >= 80: return "A"
    if s >= 60: return "B"
    if s >= 40: return "C"
    if s >= 20: return "D"
    return "E"


def _company_text(company: dict) -> str:
    """Pull the crawled text off a company row, tolerating column-name variants."""
    for k in ("crawled_text", "text_sample", "intelligence_raw", "description"):
        v = company.get(k)
        if v and str(v).strip():
            return str(v)
    return ""


def _company_domain(company: dict) -> str:
    for k in ("domain", "company_domain", "website", "url", "Website", "URL"):
        v = company.get(k)
        if v and str(v).strip():
            return normalize_domain(v)
    return ""


def _crawl_status(company: dict) -> str:
    return str(company.get("crawl_status") or company.get("website_status") or "").lower()


def _is_unverified(company: dict, text: str) -> bool:
    """True when we can't trust the score to reflect the real site."""
    if _crawl_status(company) in ("thin", "blocked", "failed", "no_url", "error", "timeout"):
        return True
    return len(text.strip()) < _MIN_VERIFIABLE_TEXT


def build_scoring_prompt(profile: dict, company: dict) -> str:
    """
    Assemble the judgment prompt from a plain-language profile + one company's
    crawled text. This is the whole configurability story: the profile fields
    are dropped in verbatim, so the same code scores "event attendance
    likelihood" and "microbiome software-gap fit" with no branching.
    """
    name = company.get("company_name") or company.get("name") or "(unknown)"
    stated_industry = company.get("industry") or company.get("Industry") or "(not provided)"
    text = _company_text(company)[:12000]   # cap to keep tokens sane

    return f"""You are a rigorous B2B research analyst scoring how well a company fits a specific prospecting profile. Judge ONLY from the company's real website text below — do not reward keyword presence, reward genuine fit. A hospital that merely mentions a keyword is NOT a fit unless the text shows real substance.

=== SCORING PROFILE ===
Niche / what we want:
{profile.get('niche_description') or '(none given)'}

Hard gate (must be true to score above 20 at all):
{profile.get('gate_rules') or '(no hard gate)'}

What raises the score:
{profile.get('score_criteria') or '(use judgment about fit to the niche)'}

Disqualifiers (drive the score toward 0):
{profile.get('disqualifiers') or '(none)'}

=== COMPANY ===
Name: {name}
Stated industry label: {stated_industry}

Website text (crawled):
\"\"\"
{text or '(no site text was gathered)'}
\"\"\"

=== YOUR TASK ===
Return ONLY a JSON object, no prose, with exactly these keys:
{{
  "score": <integer 0-100, how well this company fits the profile>,
  "tier": "<A|B|C|D|E, where A=80-100, B=60-79, C=40-59, D=20-39, E=0-19>",
  "rationale": "<2-3 sentences citing specifics from the site text, not generic praise>",
  "best_contact": "<the role/title most worth reaching at this company for this niche, or empty string>",
  "contradiction": <true if the website text clearly contradicts the stated industry label, else false>
}}"""


def _parse_json_block(raw: str) -> dict:
    """Extract the first JSON object from a model response, tolerating stray prose."""
    if not raw:
        return {}
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def _result_from_model(company: dict, parsed: dict, model: str, text: str,
                       error: str = "") -> dict:
    """Normalize a model's parsed JSON into the canonical result dict."""
    score = parsed.get("score")
    try:
        score = max(0, min(100, int(score)))
    except (TypeError, ValueError):
        score = 0
    tier = str(parsed.get("tier") or "").strip().upper()
    if tier not in ("A", "B", "C", "D", "E"):
        tier = tier_from_score(score)
    return {
        "company_name": company.get("company_name") or company.get("name") or "",
        "domain": _company_domain(company),
        "score": score,
        "tier": tier,
        "rationale": str(parsed.get("rationale") or "").strip(),
        "best_contact": str(parsed.get("best_contact") or "").strip(),
        "contradiction": bool(parsed.get("contradiction")),
        "not_site_verified": _is_unverified(company, text),
        "model": model,
        "error": error,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Route 1 — Claude native scoring (B3)  [costs tokens; budget-gated]
# ══════════════════════════════════════════════════════════════════════════════

def score_companies_claude(profile: dict, companies: list, org_id: int,
                           api_key: str = None, user_id: int = None,
                           model: str = DEFAULT_MODEL, max_tokens: int = 700) -> dict:
    """
    Score a batch of companies with Anthropic, one call per company for reliable
    JSON. Enforces the org AI budget before the first call and logs every call's
    token usage.

    Returns {"results": [...], "blocked": bool, "message": str}. If the org is
    over budget, results is empty and blocked=True with the budget message —
    nothing is charged.
    """
    from core.ai_tracker import can_use_ai, log_usage

    ok, msg = can_use_ai(org_id)
    if not ok:
        return {"results": [], "blocked": True, "message": msg}

    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return {"results": [], "blocked": True,
                "message": "No ANTHROPIC_API_KEY set. Use the GPT export path instead."}

    import anthropic
    client = anthropic.Anthropic(api_key=key)

    results = []
    for company in companies:
        text = _company_text(company)
        prompt = build_scoring_prompt(profile, company)
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text if resp.content else ""
            parsed = _parse_json_block(raw)
            results.append(_result_from_model(company, parsed, model, text))

            # Log token usage for this call against the org budget.
            try:
                log_usage(org_id,
                          tokens_input=resp.usage.input_tokens,
                          tokens_output=resp.usage.output_tokens,
                          feature="scoring", model=model, user_id=user_id)
            except Exception:
                pass

            # Re-check budget mid-batch; stop cleanly if we've hit the cap.
            ok, msg = can_use_ai(org_id)
            if not ok:
                return {"results": results, "blocked": True,
                        "message": f"{msg} Stopped after {len(results)} of {len(companies)}."}
        except Exception as e:
            results.append(_result_from_model(company, {}, model, text,
                                              error=str(e)[:200]))

    return {"results": results, "blocked": False, "message": ""}


# ══════════════════════════════════════════════════════════════════════════════
# Route 2 — GPT export / re-import (B3)  [zero in-app cost]
# ══════════════════════════════════════════════════════════════════════════════

def export_for_gpt(profile: dict, companies: list, out_dir: str) -> tuple:
    """
    Write a scoring-ready CSV plus a companion prompt .txt so the batch can be
    scored externally in GPT at no in-app cost. Returns (csv_path, prompt_path).

    The CSV carries one row per company with its crawled text; the .txt holds the
    profile-embedded instructions and the exact JSON shape to return, so the
    re-import can match scores back by domain.
    """
    os.makedirs(out_dir, exist_ok=True)
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", (profile.get("name") or "profile")).strip("_")
    csv_path = os.path.join(out_dir, f"scoring_input_{safe}.csv")
    prompt_path = os.path.join(out_dir, f"scoring_prompt_{safe}.txt")

    fields = ["company_name", "domain", "industry", "crawl_status", "crawled_text"]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for c in companies:
            w.writerow({
                "company_name": c.get("company_name") or c.get("name") or "",
                "domain": _company_domain(c),
                "industry": c.get("industry") or "",
                "crawl_status": _crawl_status(c),
                "crawled_text": _company_text(c)[:12000],
            })

    instructions = f"""SCORING TASK — run this over every row of {os.path.basename(csv_path)}.

Score how well each company fits the profile below, judging ONLY from its
`crawled_text`. Reward genuine fit, not keyword presence.

--- PROFILE: {profile.get('name','')} ---
Niche / what we want:
{profile.get('niche_description') or '(none given)'}

Hard gate (must be true to score above 20):
{profile.get('gate_rules') or '(no hard gate)'}

What raises the score:
{profile.get('score_criteria') or '(judgment about fit)'}

Disqualifiers (drive score toward 0):
{profile.get('disqualifiers') or '(none)'}

--- OUTPUT ---
Produce a CSV with these exact columns (keep the same `domain` value so scores
can be matched back):
  domain, score, tier, rationale, best_contact, contradiction

Where score is 0-100, tier is A(80-100)/B(60-79)/C(40-59)/D(20-39)/E(0-19),
rationale is 2-3 specific sentences, best_contact is a role/title or blank, and
contradiction is true/false (does the site text contradict the industry label).
"""
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(instructions)

    return csv_path, prompt_path


def export_from_guide(guide_text: str, companies: list, out_dir: str,
                      label: str = "custom") -> tuple:
    """
    Same as export_for_gpt, but the client pastes a single free-text scoring
    guide (whatever their own AI gave them) instead of filling structured
    profile fields. The guide is embedded verbatim as the instructions, so the
    client keeps full control of how their AI judges — we don't reinterpret it.

    Returns (csv_path, prompt_path).
    """
    os.makedirs(out_dir, exist_ok=True)
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", (label or "custom")).strip("_") or "custom"
    csv_path = os.path.join(out_dir, f"scoring_input_{safe}.csv")
    prompt_path = os.path.join(out_dir, f"scoring_prompt_{safe}.txt")

    fields = ["company_name", "domain", "industry", "crawl_status", "crawled_text"]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for c in companies:
            w.writerow({
                "company_name": c.get("company_name") or c.get("name") or "",
                "domain": _company_domain(c),
                "industry": c.get("industry") or "",
                "crawl_status": _crawl_status(c),
                "crawled_text": _company_text(c)[:12000],
            })

    instructions = f"""SCORING TASK — run this over every row of {os.path.basename(csv_path)}.

Score how well each company fits the guide below, judging ONLY from its
`crawled_text` column. Reward genuine fit, not keyword presence.

=== YOUR SCORING GUIDE ===
{guide_text.strip() or '(no guide was pasted)'}

=== OUTPUT (required) ===
Return a CSV with these exact columns, keeping each row's `domain` unchanged so
scores can be matched back:
  domain, score, tier, rationale, best_contact, contradiction

score = 0-100. tier = A(80-100)/B(60-79)/C(40-59)/D(20-39)/E(0-19).
rationale = 2-3 specific sentences. best_contact = a role/title or blank.
contradiction = true/false (does the site text contradict the industry label?).
"""
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(instructions)

    return csv_path, prompt_path


def import_scored_csv(path: str, companies: list = None) -> list:
    """
    Read a GPT-scored CSV back in and normalize it to result dicts, matched by
    domain. If `companies` is provided, carries over crawl_status so
    not_site_verified stays accurate; otherwise not_site_verified is False.

    Accepts flexible column names (domain/website, score, tier, rationale,
    best_contact, contradiction) and is case-insensitive on headers.
    """
    by_domain = {}
    if companies:
        for c in companies:
            by_domain[_company_domain(c)] = c

    results = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        # normalize header lookup
        for row in reader:
            low = {(k or "").strip().lower(): v for k, v in row.items()}
            domain = normalize_domain(low.get("domain") or low.get("website") or low.get("url") or "")
            src = by_domain.get(domain, {})
            text = _company_text(src) if src else ""
            parsed = {
                "score": low.get("score"),
                "tier": low.get("tier"),
                "rationale": low.get("rationale"),
                "best_contact": low.get("best_contact"),
                "contradiction": str(low.get("contradiction", "")).strip().lower() in ("true", "1", "yes"),
            }
            res = _result_from_model(
                {**src, "company_name": low.get("company_name") or (src.get("company_name") if src else "") or domain,
                 "domain": domain},
                parsed, model="gpt-import", text=text)
            results.append(res)
    return results


# ══════════════════════════════════════════════════════════════════════════════
# Cross-validation (B3) — the proven two-model workflow
# ══════════════════════════════════════════════════════════════════════════════

def cross_validate(results_a: list, results_b: list, disagree_threshold: int = 20) -> list:
    """
    Merge two scoring runs (e.g. Claude vs GPT) by domain and flag disagreements.

    Returns one row per company:
      {domain, company_name, score_a, score_b, tier_a, tier_b, score_delta,
       disagreement (bool), needs_review (bool), rationale_a, rationale_b}

    disagreement/needs_review is True when |score_a - score_b| > threshold —
    exactly the >20-point rule from the Proving-Health workflow, surfacing the
    companies a human should look at.
    """
    a_by = {r["domain"]: r for r in results_a if r.get("domain")}
    b_by = {r["domain"]: r for r in results_b if r.get("domain")}
    domains = list(dict.fromkeys([*a_by.keys(), *b_by.keys()]))  # preserve order

    merged = []
    for d in domains:
        a = a_by.get(d, {})
        b = b_by.get(d, {})
        sa, sb = a.get("score"), b.get("score")
        if sa is not None and sb is not None:
            delta = abs(int(sa) - int(sb))
            disagree = delta > disagree_threshold
        else:
            delta = None
            disagree = True  # only one model scored it → worth a human glance
        merged.append({
            "domain": d,
            "company_name": a.get("company_name") or b.get("company_name") or d,
            "score_a": sa, "score_b": sb,
            "tier_a": a.get("tier"), "tier_b": b.get("tier"),
            "score_delta": delta,
            "disagreement": disagree,
            "needs_review": disagree,
            "rationale_a": a.get("rationale", ""),
            "rationale_b": b.get("rationale", ""),
        })
    return merged
