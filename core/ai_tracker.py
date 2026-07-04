"""
core/ai_tracker.py — Dashin Research Platform
Tracks Anthropic API usage per org.
- Logs every API call with tokens + cost
- Maintains monthly budget per org
- Alerts super admin at 80% usage
- Returns whether an org can make AI calls
"""

import sqlite3
import smtplib
import os
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from pathlib import Path

# Anthropic pricing (per million tokens) — update if prices change
PRICE_INPUT_PER_M  = 3.00   # Claude Sonnet input
PRICE_OUTPUT_PER_M = 15.00  # Claude Sonnet output

SUPER_ADMIN_EMAIL  = os.getenv("SUPER_ADMIN_EMAIL", "admin@dashin.com")
SMTP_HOST          = os.getenv("SMTP_HOST", "")
SMTP_PORT          = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER          = os.getenv("SMTP_USER", "")
SMTP_PASS          = os.getenv("SMTP_PASS", "")


def _get_conn():
    from core.db import get_connection
    return get_connection()


def tokens_to_usd(tokens_input: int, tokens_output: int) -> float:
    """Convert token counts to USD cost."""
    cost = (tokens_input  / 1_000_000) * PRICE_INPUT_PER_M
    cost += (tokens_output / 1_000_000) * PRICE_OUTPUT_PER_M
    return round(cost, 6)


def get_billing_period(billing_day: int) -> tuple:
    """
    Returns (period_start, period_end) as ISO date strings
    based on the org's billing anniversary day.
    """
    today = date.today()
    if today.day >= billing_day:
        start = today.replace(day=billing_day)
    else:
        start = (today - relativedelta(months=1)).replace(day=billing_day)
    end = (start + relativedelta(months=1)) - relativedelta(days=1)
    return start.isoformat(), end.isoformat()


def get_org_usage(org_id: int) -> dict:
    """
    Returns current period usage for an org.
    {tokens_input, tokens_output, cost_usd, budget_usd, pct_used, period_start, period_end}
    """
    conn = _get_conn()
    org = conn.execute(
        "SELECT ai_budget_usd, billing_day FROM organisations WHERE id=?",
        (org_id,)
    ).fetchone()

    if not org:
        conn.close()
        return {"cost_usd": 0, "budget_usd": 0, "pct_used": 0}

    period_start, period_end = get_billing_period(org["billing_day"])

    usage = conn.execute("""
        SELECT tokens_input, tokens_output, cost_usd, alert_80_sent
        FROM org_ai_usage
        WHERE org_id=? AND period_start=?
    """, (org_id, period_start)).fetchone()

    conn.close()

    budget = org["ai_budget_usd"]
    if usage:
        cost = usage["cost_usd"]
        return {
            "tokens_input":   usage["tokens_input"],
            "tokens_output":  usage["tokens_output"],
            "cost_usd":       cost,
            "budget_usd":     budget,
            "pct_used":       round((cost / budget * 100) if budget > 0 else 0, 1),
            "alert_80_sent":  usage["alert_80_sent"],
            "period_start":   period_start,
            "period_end":     period_end,
        }
    return {
        "tokens_input":  0,
        "tokens_output": 0,
        "cost_usd":      0.0,
        "budget_usd":    budget,
        "pct_used":      0.0,
        "alert_80_sent": 0,
        "period_start":  period_start,
        "period_end":    period_end,
    }


def can_use_ai(org_id: int) -> tuple:
    """
    Returns (True/False, message).
    Hard blocks at 100% budget to prevent runaway costs.
    Warns at 80%.
    """
    usage = get_org_usage(org_id)
    if usage["budget_usd"] > 0 and usage["pct_used"] >= 100:
        return False, (
            f"🚫 AI budget exceeded ({usage['pct_used']}% used, "
            f"${usage['cost_usd']:.2f}/${usage['budget_usd']:.2f}). "
            "Contact Dashin to upgrade your plan."
        )
    if usage["pct_used"] >= 80:
        return True, f"⚠ AI budget at {usage['pct_used']}% — approaching limit."
    return True, ""


def log_usage(
    org_id:        int,
    tokens_input:  int,
    tokens_output: int,
    feature:       str = "scraper",
    model:         str = "claude-sonnet-4-6",
    user_id:       int = None,
    session_id:    str = None,
) -> float:
    """
    Log an AI API call. Updates org_ai_usage and platform_ai_log.
    Returns cost in USD.
    """
    cost = tokens_to_usd(tokens_input, tokens_output)
    now  = datetime.utcnow().isoformat()

    conn = _get_conn()

    # Get billing period
    org = conn.execute(
        "SELECT billing_day, ai_budget_usd, name FROM organisations WHERE id=?",
        (org_id,)
    ).fetchone()

    if not org:
        conn.close()
        return cost

    period_start, period_end = get_billing_period(org["billing_day"])

    # Upsert org_ai_usage
    conn.execute("""
        INSERT INTO org_ai_usage
            (org_id, period_start, period_end, tokens_input, tokens_output, cost_usd, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(org_id, period_start) DO UPDATE SET
            tokens_input  = tokens_input  + excluded.tokens_input,
            tokens_output = tokens_output + excluded.tokens_output,
            cost_usd      = cost_usd      + excluded.cost_usd,
            updated_at    = excluded.updated_at
    """, (org_id, period_start, period_end, tokens_input, tokens_output, cost, now))

    # Log individual call
    conn.execute("""
        INSERT INTO platform_ai_log
            (org_id, user_id, session_id, feature, model,
             tokens_input, tokens_output, cost_usd, logged_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (org_id, user_id, session_id, feature, model,
          tokens_input, tokens_output, cost, now))

    conn.commit()

    # Check if we need to send 80% alert
    usage = conn.execute("""
        SELECT cost_usd, alert_80_sent FROM org_ai_usage
        WHERE org_id=? AND period_start=?
    """, (org_id, period_start)).fetchone()

    budget = org["ai_budget_usd"]
    if usage and budget > 0:
        pct = (usage["cost_usd"] / budget) * 100
        if pct >= 80 and not usage["alert_80_sent"]:
            _send_80pct_alert(org_id, org["name"], pct, usage["cost_usd"], budget)
            conn.execute("""
                UPDATE org_ai_usage SET alert_80_sent=1
                WHERE org_id=? AND period_start=?
            """, (org_id, period_start))
            conn.commit()

    conn.close()
    return cost


def _send_80pct_alert(org_id: int, org_name: str, pct: float,
                      cost: float, budget: float):
    """Send email alert to super admin when org hits 80% AI budget."""
    subject = f"[Dashin] AI Budget Alert — {org_name} at {pct:.0f}%"
    body = f"""
Dashin AI Budget Alert

Organisation:  {org_name} (ID: {org_id})
Usage:         ${cost:.4f} of ${budget:.2f} ({pct:.1f}%)
Action needed: Consider contacting the org to discuss upgrading their plan.

This is an automated alert from Dashin Research Platform.
    """.strip()

    if not SMTP_HOST or not SMTP_USER:
        # No email configured — print to console
        print(f"\n⚠  AI BUDGET ALERT: {org_name} is at {pct:.0f}% (${cost:.4f}/${budget:.2f})\n")
        return

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            msg = f"Subject: {subject}\n\n{body}"
            server.sendmail(SMTP_USER, SUPER_ADMIN_EMAIL, msg)
    except Exception as e:
        print(f"⚠ Could not send AI alert email: {e}")


# ── SUPER ADMIN QUERIES ───────────────────────────────────────────────────────

def get_platform_summary() -> dict:
    """Total AI spend across all orgs this month."""
    conn = _get_conn()
    today = date.today()
    month_start = today.replace(day=1).isoformat()

    rows = conn.execute("""
        SELECT
            SUM(tokens_input)  AS total_input,
            SUM(tokens_output) AS total_output,
            SUM(cost_usd)      AS total_cost
        FROM platform_ai_log
        WHERE logged_at >= ?
    """, (month_start,)).fetchone()

    conn.close()
    return {
        "total_input":  rows["total_input"]  or 0,
        "total_output": rows["total_output"] or 0,
        "total_cost":   round(rows["total_cost"] or 0, 4),
        "month_start":  month_start,
    }


def get_all_org_usage() -> list:
    """Usage for all orgs in their current billing period."""
    conn = _get_conn()
    orgs = conn.execute("""
        SELECT id, name, tier, ai_budget_usd, billing_day
        FROM organisations WHERE is_active=1
        ORDER BY name
    """).fetchall()
    conn.close()

    results = []
    for org in orgs:
        u = get_org_usage(org["id"])
        results.append({
            "org_id":       org["id"],
            "org_name":     org["name"],
            "tier":         org["tier"],
            "budget_usd":   org["ai_budget_usd"],
            "cost_usd":     u["cost_usd"],
            "pct_used":     u["pct_used"],
            "period_start": u["period_start"],
            "alert_80_sent":u.get("alert_80_sent", 0),
        })
    return results


def get_monthly_trend(org_id: int, months: int = 6) -> list:
    """Historical monthly AI spend for an org."""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT period_start, cost_usd, tokens_input, tokens_output
        FROM org_ai_usage
        WHERE org_id=?
        ORDER BY period_start DESC
        LIMIT ?
    """, (org_id, months)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_feature_breakdown(org_id: int, period_start: str) -> list:
    """Break down AI cost by feature for an org in a period."""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT feature,
               SUM(tokens_input)  AS tokens_in,
               SUM(tokens_output) AS tokens_out,
               SUM(cost_usd)      AS cost,
               COUNT(*)           AS calls
        FROM platform_ai_log
        WHERE org_id=? AND logged_at >= ?
        GROUP BY feature
        ORDER BY cost DESC
    """, (org_id, period_start)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
