"""
dashboards/site_library_dashboard.py — Dashin Research Platform
Reusable Site Library UI used by both superadmin and admin dashboards.

render(user, allow_delete=False, allow_mark_stable=False)
"""

import streamlit as st
import logging
from datetime import datetime

from services.site_learning_service import (
    get_all_patterns,
    get_pattern_stats,
    get_expiring_soon,
    expire_pattern,
    mark_pattern_stable,
)

# ── STYLE ─────────────────────────────────────────────────────────────────────
CSS = """
<style>
.lib-stat{background:#fff;border:1px solid #e8e4dd;border-radius:10px;
  padding:16px 20px;text-align:center;flex:1;min-width:110px}
.lib-stat-val{font-size:26px;font-weight:700;color:#1a1917}
.lib-stat-label{font-size:10px;color:#aaa;text-transform:uppercase;letter-spacing:1.2px;margin-top:3px}
.lib-row{display:flex;gap:12px;margin-bottom:22px;flex-wrap:wrap}
.badge-stable{background:#ecf7f0;color:#3d9e6a;border:1px solid #b8dfc8;
  display:inline-block;padding:2px 10px;border-radius:20px;font-size:10px;font-weight:600}
.badge-fragile{background:#fff8ec;color:#c9a96e;border:1px solid #e8d5a8;
  display:inline-block;padding:2px 10px;border-radius:20px;font-size:10px;font-weight:600}
.badge-expired{background:#fdecea;color:#d45050;border:1px solid #f0b8b8;
  display:inline-block;padding:2px 10px;border-radius:20px;font-size:10px;font-weight:600}
.badge-failed{background:#fdecea;color:#d45050;border:1px solid #f0b8b8;
  display:inline-block;padding:2px 10px;border-radius:20px;font-size:10px;font-weight:600}
</style>
"""


def _is_expired(pattern: dict) -> bool:
    from datetime import timedelta
    from services.site_learning_service import FRAGILE_EXPIRY_DAYS
    if pattern.get("selector_type") != "fragile":
        return False
    last = pattern.get("last_success_at")
    if not last:
        return True
    try:
        ls = datetime.fromisoformat(last)
        return (datetime.now() - ls).days > FRAGILE_EXPIRY_DAYS
    except Exception:
        return True


def _status(pattern: dict) -> str:
    if (pattern.get("fail_count") or 0) >= 3:
        return "failed"
    if _is_expired(pattern):
        return "expired"
    return "active"


def _quality_bar(score) -> str:
    if score is None:
        return "—"
    pct = int((score or 0) * 100)
    colour = "#3d9e6a" if pct >= 70 else "#c9a96e" if pct >= 50 else "#d45050"
    return (f'<div style="background:#f0f0f0;border-radius:4px;height:8px;width:80px;display:inline-block">'
            f'<div style="background:{colour};height:8px;border-radius:4px;width:{pct}%"></div></div>'
            f'&nbsp;<span style="font-size:11px;color:{colour}">{pct}%</span>')


def render(user: dict, allow_delete: bool = False, allow_mark_stable: bool = False):
    """
    Render the site library.
    allow_delete / allow_mark_stable = True for super_admin only.
    """
    st.markdown(CSS, unsafe_allow_html=True)
    st.subheader("Site Pattern Library")

    # ── Stats row ─────────────────────────────────────────────────────
    stats = get_pattern_stats()
    total    = stats.get("total") or 0
    stable   = stats.get("stable") or 0
    fragile  = stats.get("fragile") or 0
    failed   = stats.get("failed") or 0
    avg_q    = stats.get("avg_quality") or 0.0
    expiring = get_expiring_soon(7)

    st.markdown(f"""
    <div class="lib-row">
      <div class="lib-stat">
        <div class="lib-stat-val">{total}</div>
        <div class="lib-stat-label">Total Sites</div>
      </div>
      <div class="lib-stat">
        <div class="lib-stat-val" style="color:#3d9e6a">{stable}</div>
        <div class="lib-stat-label">Stable (never expires)</div>
      </div>
      <div class="lib-stat">
        <div class="lib-stat-val" style="color:#c9a96e">{fragile}</div>
        <div class="lib-stat-label">Fragile (30-day TTL)</div>
      </div>
      <div class="lib-stat">
        <div class="lib-stat-val" style="color:#d45050">{failed}</div>
        <div class="lib-stat-label">Failed (needs attention)</div>
      </div>
      <div class="lib-stat">
        <div class="lib-stat-val">{int(avg_q * 100)}%</div>
        <div class="lib-stat-label">Avg quality score</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # Expiring soon banner
    if expiring:
        domains_str = ", ".join(p["domain"] for p in expiring[:5])
        st.warning(
            f"⚠️ **{len(expiring)} fragile pattern(s) expiring within 7 days:** {domains_str}"
        )

    # ── Filters ───────────────────────────────────────────────────────
    filter_opts = ["All", "Stable only", "Fragile only", "Expired", "Failed"]
    flt = st.radio("Filter", filter_opts, horizontal=True, label_visibility="collapsed")

    patterns = get_all_patterns()

    if flt == "Stable only":
        patterns = [p for p in patterns if p.get("selector_type") == "stable"]
    elif flt == "Fragile only":
        patterns = [p for p in patterns if p.get("selector_type") == "fragile" and not _is_expired(p)]
    elif flt == "Expired":
        patterns = [p for p in patterns if _is_expired(p)]
    elif flt == "Failed":
        patterns = [p for p in patterns if (p.get("fail_count") or 0) >= 3]

    if not patterns:
        st.info("No patterns match this filter.")
        return

    # ── Pattern table ─────────────────────────────────────────────────
    for p in patterns:
        st_type = p.get("selector_type", "fragile")
        status  = _status(p)

        badge_html = {
            "active":  f'<span class="badge-{st_type}">{st_type.upper()}</span>',
            "expired": '<span class="badge-expired">EXPIRED</span>',
            "failed":  '<span class="badge-failed">FAILED</span>',
        }.get(status, "")

        last_ok = (p.get("last_success_at") or "never")[:10]
        succ = p.get("success_count") or 0
        fail = p.get("fail_count") or 0

        with st.expander(
            f"{p['domain']}  —  {st_type.upper()}  ·  "
            f"Quality: {int((p.get('quality_score') or 0)*100)}%  ·  "
            f"✓{succ} ✗{fail}  ·  last ok: {last_ok}",
            expanded=False
        ):
            col_info, col_actions = st.columns([3, 1])

            with col_info:
                st.markdown(
                    f"{badge_html}&nbsp;&nbsp;"
                    f"Confidence: **{int((p.get('confidence') or 0)*100)}%**  ·  "
                    f"Status: **{status}**",
                    unsafe_allow_html=True,
                )
                st.markdown(f"**Card selector:** `{p.get('card_selector', '—')}`")
                if p.get("name_selector"):
                    st.markdown(f"Name: `{p['name_selector']}` · "
                                f"Title: `{p.get('title_selector','—')}` · "
                                f"Company: `{p.get('company_selector','—')}`")
                st.markdown(f"Quality score: {_quality_bar(p.get('quality_score'))}  ·  "
                            f"Verified by: **{p.get('verified_by','ai')}**",
                            unsafe_allow_html=True)
                if p.get("notes"):
                    st.caption(p["notes"])

            with col_actions:
                # Re-learn button — available to all
                if st.button("🔄 Re-learn", key=f"relearn_{p['domain']}"):
                    expire_pattern(p["domain"])
                    st.success(f"Pattern cleared. Next scrape of {p['domain']} will re-analyse.")
                    st.rerun()

                # Delete — super_admin only
                if allow_delete:
                    if st.button("🗑 Delete", key=f"del_{p['domain']}"):
                        try:
                            from core.db import get_connection
                            conn = get_connection()
                            conn.execute(
                                "DELETE FROM site_patterns WHERE domain=?",
                                (p["domain"],)
                            )
                            conn.commit()
                            conn.close()
                            st.success(f"Deleted pattern for {p['domain']}.")
                            st.rerun()
                        except Exception as e:
                            logging.warning(f"[site_library] delete: {e}")
                            st.error(str(e))

                # Mark stable — super_admin only
                if allow_mark_stable and st_type != "stable":
                    notes_key = f"stable_notes_{p['domain']}"
                    note_val = st.text_input("Note (why stable?)", key=notes_key,
                                             placeholder="e.g. Manually verified 2026-01")
                    if st.button("✅ Mark Stable", key=f"stable_{p['domain']}"):
                        mark_pattern_stable(p["domain"], note_val)
                        st.success(f"{p['domain']} marked as STABLE.")
                        st.rerun()
