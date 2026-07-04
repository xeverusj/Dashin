"""
app.py — Dashin Research Platform V2
Entry point. Run with: streamlit run app.py

Roles and their nav:
  super_admin       → Super Admin panel + all internal views
  org_admin         → All internal views + Admin panel
  manager           → Scraper, Inventory, Campaigns, Estimator, Research queue
  research_manager  → Research queue, Research manager view, Inventory
  campaign_manager  → Campaign manager view, Campaigns
  researcher        → Research queue, Inventory (own leads only)
  client_admin      → Full client portal
  client_user       → Full client portal
"""

import os
import sys
import subprocess
import logging
from pathlib import Path

# Force UTF-8 stdout/stderr so emoji in startup prints (e.g. core/db.py's "✅")
# don't crash the app under a Windows cp1252 console. Harmless on Linux/Mac.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass
sys.path.insert(0, str(Path(__file__).parent))

# ── Cloud environment setup ──────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import streamlit as _st_env
    if hasattr(_st_env, 'secrets'):
        for _key in ['ANTHROPIC_API_KEY', 'FLASK_SECRET_KEY', 'SMTP_HOST',
                     'SMTP_PORT', 'SMTP_USER', 'SMTP_PASS']:
            if _key not in os.environ and _key in _st_env.secrets:
                os.environ[_key] = str(_st_env.secrets[_key])
except Exception:
    pass
# ─────────────────────────────────────────────────────────────────────────────

import streamlit as st
from core.db import init_db, migrate_db, ensure_defaults, get_connection

# ── STARTUP SECURITY CHECK ────────────────────────────────────────────────────
def _check_committed_secrets():
    """Warn if the ANTHROPIC_API_KEY may have been committed to git history."""
    api_key = os.getenv('ANTHROPIC_API_KEY', '')
    if api_key.startswith('sk-ant-'):
        try:
            result = subprocess.run(
                ['git', 'log', '--all', '--full-history', '--', '.env'],
                capture_output=True, text=True, timeout=5,
                cwd=Path(__file__).parent
            )
            if result.stdout.strip():
                logging.warning(
                    "WARNING: .env appears to have been committed to git history. "
                    "Rotate your ANTHROPIC_API_KEY immediately at https://console.anthropic.com"
                )
                print(
                    "\n⚠️  SECURITY WARNING: .env may have been committed to git history.\n"
                    "   Rotate your ANTHROPIC_API_KEY at: https://console.anthropic.com\n"
                )
        except Exception:
            pass  # git not available or timeout — skip silently

_check_committed_secrets()

# ── PAGE CONFIG ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title  = "Dashin Research",
    page_icon   = "⚡",
    layout      = "wide",
    initial_sidebar_state = "expanded",
)

# ── GLOBAL CSS ────────────────────────────────────────────────────────────────
# Shared design system (fonts, variables, all shared components) — core/styles.py
from core.styles import inject_shared_css
inject_shared_css()

# Sidebar + app-chrome CSS — not shared with dashboards
st.markdown("""
<style>
#MainMenu, footer, header, .stDeployButton { visibility: hidden; }

/* ── Sidebar shell ───────────────────────────────────────────────────────── */
section[data-testid="stSidebar"] {
    background: rgba(255,255,255,0.85) !important;
    border-right: 1px solid rgba(122,116,134,0.12) !important;
    backdrop-filter: blur(20px) !important;
    min-width: 240px !important;
    max-width: 240px !important;
}
section[data-testid="stSidebar"] > div:first-child {
    padding: 24px 12px 20px;
}
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] span {
    color: #494455 !important;
    font-size: 12px !important;
    font-family: 'Inter', sans-serif !important;
}
section[data-testid="stSidebar"] .stRadio > div { gap: 1px !important; }
section[data-testid="stSidebar"] .stRadio label {
    background: transparent !important;
    border-radius: 6px !important;
    padding: 9px 14px !important;
    width: 100% !important;
    cursor: pointer !important;
    color: #7a7486 !important;
    font-size: 12px !important;
    font-weight: 600 !important;
    transition: all .15s !important;
}
section[data-testid="stSidebar"] .stRadio label:hover {
    background: #f3f4f5 !important;
    color: #191c1d !important;
}
section[data-testid="stSidebar"] .stRadio label[data-checked="true"],
section[data-testid="stSidebar"] .stRadio input:checked + div {
    background: rgba(84,22,201,0.08) !important;
    color: #5416c9 !important;
    border-right: 2px solid #5416c9 !important;
}

/* ── Sidebar components ──────────────────────────────────────────────────── */
.sb-logo-img {
    padding-bottom: 16px;
    margin-bottom: 12px;
    border-bottom: 1px solid rgba(122,116,134,0.15);
}
.sb-user {
    font-size: 11px;
    color: #7a7486;
    margin-bottom: 16px;
    line-height: 1.7;
}
.sb-user strong { color: #191c1d; font-weight: 700; }
.sb-role {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
    margin-top: 2px;
}
.sb-role-super_admin      { background:rgba(84,22,201,.10);  color:#5416c9; }
.sb-role-org_admin        { background:rgba(84,22,201,.08);  color:#6a39de; }
.sb-role-manager          { background:rgba(84,22,201,.08);  color:#5416c9; }
.sb-role-research_manager { background:rgba(84,22,201,.08);  color:#5416c9; }
.sb-role-campaign_manager { background:rgba(180,83,9,.10);   color:#b45309; }
.sb-role-researcher       { background:rgba(22,163,74,.10);  color:#16a34a; }
.sb-role-client_admin     { background:rgba(186,26,26,.10);  color:#ba1a1a; }
.sb-role-client_user      { background:rgba(122,116,134,.10);color:#7a7486; }
.sb-org {
    font-size: 10px;
    color: #7a7486;
    font-style: italic;
    margin-top: 2px;
}
.sb-div {
    border: none;
    border-top: 1px solid rgba(122,116,134,0.15);
    margin: 12px 0;
}
.sb-section-label {
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.18em;
    color: #7a7486;
    padding: 16px 14px 6px;
}
.sb-notif {
    background: #5416c9;
    color: #fff;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 10px;
    font-weight: 700;
    display: inline-block;
    margin-left: 6px;
}

/* ── Quota usage bar (sidebar bottom) ───────────────────────────────────── */
.sb-quota {
    background: #f3f4f5;
    border: 1px solid rgba(122,116,134,0.12);
    border-radius: 10px;
    padding: 14px;
    margin-top: 8px;
}
.sb-quota-label {
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.15em;
    color: #7a7486;
    margin-bottom: 8px;
}
.sb-quota-bar-bg {
    width: 100%;
    height: 4px;
    background: #e1e3e4;
    border-radius: 4px;
    margin-bottom: 6px;
    overflow: hidden;
}
.sb-quota-bar-fill {
    height: 100%;
    background: #5416c9;
    border-radius: 4px;
}
.sb-quota-text {
    font-size: 10px;
    color: #7a7486;
}

/* ── Main content layout ─────────────────────────────────────────────────── */
.main .block-container {
    max-width: 1200px !important;
    padding-top: 24px !important;
}
</style>
""", unsafe_allow_html=True)


# ── NAV DEFINITIONS ───────────────────────────────────────────────────────────
# Maps display label → page key → which roles can see it

NAV_ITEMS = [
    # (label, page_key, allowed_roles)
    ("⚡  Platform",         "superadmin",   {"super_admin"}),
    ("🔍  Smart Scraper",    "scraper",      {"super_admin","org_admin","manager","researcher"}),
    ("📦  Inventory",        "inventory",    {"super_admin","org_admin","manager","research_manager","researcher"}),
    ("🔬  Research Queue",   "research",     {"super_admin","org_admin","manager","research_manager","researcher"}),
    ("📋  Research Manager", "res_manager",  {"super_admin","org_admin","manager","research_manager"}),
    ("🚀  Campaigns",        "campaigns",    {"super_admin","org_admin","manager","campaign_manager"}),
    ("📊  Campaign Manager", "camp_manager", {"super_admin","org_admin","manager","campaign_manager"}),
    ("💰  Estimator",        "estimator",    {"super_admin","org_admin","manager"}),
    ("🎯  Enrichment",       "enrichment",   {"super_admin","org_admin","manager","researcher"}),
    ("🧮  AI Scoring",       "scoring",      {"super_admin","org_admin","manager","research_manager","researcher"}),
    ("🔗  LinkedIn Enricher","enricher",     {"super_admin","org_admin","manager","research_manager","researcher"}),
    ("📧  Email Match",      "email_match",  {"super_admin","org_admin","manager","research_manager","researcher"}),
    ("📤  Outreach",         "outreach",     {"super_admin","org_admin","manager","researcher"}),
    ("📊  Report Builder",   "report_builder", {"super_admin","org_admin","manager","campaign_manager"}),
    ("⚙️  Admin",            "admin",        {"super_admin","org_admin"}),
]

CLIENT_NAV_ITEMS = [
    ("🏠  Home",             "client_home"),
    ("👥  My Leads",         "client_leads"),
    ("📁  Campaigns",        "client_campaigns"),
    ("📊  Campaign Report",  "client_report"),
    ("📎  Files",            "client_files"),
    ("💬  Notes",            "client_notes"),
]

# ── SIDEBAR ───────────────────────────────────────────────────────────────────

def render_sidebar(user: dict) -> str:
    """Render sidebar and return selected page key."""
    role      = user.get("role", "researcher")
    is_client = role in ("client_admin", "client_user")

    # ── Notification count (cached 30s) ───────────────────────────────────
    unread = 0
    try:
        import time as _time
        _uid = user["id"]
        _ck  = f"_unread_{_uid}"
        _ct  = f"_unread_ts_{_uid}"
        if _time.time() - st.session_state.get(_ct, 0) > 30:
            from services.notification_service import unread_count as _uc
            st.session_state[_ck] = _uc(_uid)
            st.session_state[_ct] = _time.time()
        unread = st.session_state.get(_ck, 0)
    except Exception as _notif_err:
        logging.warning(f"[app.sidebar] unread_count: {_notif_err}")

    with st.sidebar:
        # Logo
        _logo_path = Path(__file__).parent / "assets" / "logo.png"
        if _logo_path.exists():
            st.markdown('<div class="sb-logo-img">', unsafe_allow_html=True)
            st.image(str(_logo_path), use_container_width=True)
            st.markdown('</div>', unsafe_allow_html=True)
        else:
            st.markdown(
                '<div class="sb-logo-img" style="font-size:20px;font-weight:900;'
                'color:#191c1d;padding-bottom:16px;margin-bottom:12px;'
                'border-bottom:1px solid rgba(122,116,134,0.15);">'
                'Dashin<span style="color:#5416c9">.</span></div>',
                unsafe_allow_html=True
            )

        # User info
        org_name       = user.get("org_name", "")
        client_name    = user.get("client_name", "")
        display_context = client_name if is_client else org_name

        notif_badge = f' <span class="sb-notif">🔔 {unread} new</span>' if unread > 0 else ""
        org_line = f'<div class="sb-org">{display_context}</div>' if display_context else ""
        st.markdown(
            f'<div class="sb-user">'
            f'<strong>{user["name"]}</strong><br>'
            f'{user["email"]}<br>'
            f'<span class="sb-role sb-role-{role}">{role.replace("_"," ")}</span>'
            f'{notif_badge}'
            f'{org_line}'
            f'</div><hr class="sb-div">',
            unsafe_allow_html=True,
        )

        # Navigation
        if is_client:
            nav_labels = [item[0] for item in CLIENT_NAV_ITEMS]
            nav_keys   = [item[1] for item in CLIENT_NAV_ITEMS]
        else:
            visible    = [(label, key)
                          for label, key, roles in NAV_ITEMS
                          if role in roles]
            nav_labels = [item[0] for item in visible]
            nav_keys   = [item[1] for item in visible]

        if not nav_labels:
            st.warning("No pages available for your role.")
            return "none"

        # Keep selected page in session state
        if "page" not in st.session_state:
            st.session_state["page"] = nav_keys[0]
        if st.session_state["page"] not in nav_keys:
            st.session_state["page"] = nav_keys[0]

        current_idx  = nav_keys.index(st.session_state["page"])
        choice       = st.radio(
            "Navigation",
            nav_labels,
            index=current_idx,
            label_visibility="collapsed",
        )
        selected_key = nav_keys[nav_labels.index(choice)]
        st.session_state["page"] = selected_key

        st.markdown('<hr class="sb-div">', unsafe_allow_html=True)

        # Quota usage
        try:
            from core.ai_tracker import get_org_usage
            usage  = get_org_usage(user.get("org_id"))
            used   = usage.get("cost_usd", 0)
            budget = usage.get("budget_usd", 0)
            pct    = min(int(usage.get("pct_used", 0)), 100)
            bar_color = "#ba1a1a" if pct >= 90 else "#b45309" if pct >= 75 else "#5416c9"
            st.markdown(f"""
            <div class="sb-quota">
                <div class="sb-quota-label">Quota Usage</div>
                <div class="sb-quota-bar-bg">
                    <div class="sb-quota-bar-fill" style="width:{pct}%;background:{bar_color};"></div>
                </div>
                <div class="sb-quota-text">${used:.2f} / ${budget:.0f} &nbsp;·&nbsp; {pct}%</div>
            </div>
            """, unsafe_allow_html=True)
        except Exception:
            pass

        st.markdown('<hr class="sb-div">', unsafe_allow_html=True)

        if st.button("Sign Out", use_container_width=True):
            for k in ["user", "org_id", "page"]:
                st.session_state.pop(k, None)
            st.rerun()

    return selected_key


# ── ROUTER ────────────────────────────────────────────────────────────────────

def route(page: str, user: dict):
    """Route to the correct dashboard based on page key and role."""
    role = user.get("role", "researcher")

    # ── Client portal (completely separate visual) ─────────────────────
    if role in ("client_admin", "client_user"):
        from dashboards.client_dashboard import render
        render(user)
        return

    # ── Super admin ────────────────────────────────────────────────────
    if page == "superadmin":
        if role != "super_admin":
            _access_denied()
            return
        from dashboards.superadmin_dashboard import render
        render(user)

    # ── Smart Scraper ──────────────────────────────────────────────────
    elif page == "scraper":
        if role not in ("super_admin","org_admin","manager","researcher"):
            _access_denied(); return
        try:
            from dashboards.scraper_dashboard import render
            render(user)
        except Exception as e:
            _dashboard_error("Scraper", e)

    # ── Inventory ─────────────────────────────────────────────────────
    elif page == "inventory":
        try:
            from dashboards.inventory_dashboard import render
            render(user)
        except Exception as e:
            _dashboard_error("Inventory", e)

    # ── Research Queue ─────────────────────────────────────────────────
    elif page == "research":
        try:
            from dashboards.research_dashboard import render
            render(user)
        except Exception as e:
            _dashboard_error("Research Queue", e)

    # ── Research Manager ───────────────────────────────────────────────
    elif page == "res_manager":
        if role not in ("super_admin","org_admin","manager","research_manager"):
            _access_denied(); return
        try:
            from dashboards.research_manager_dashboard import render
            render(user)
        except Exception as e:
            _dashboard_error("Research Manager", e)

    # ── Campaigns ─────────────────────────────────────────────────────
    elif page == "campaigns":
        if role not in ("super_admin","org_admin","manager","campaign_manager"):
            _access_denied(); return
        try:
            from dashboards.campaigns_dashboard import render
            render(user)
        except Exception as e:
            _dashboard_error("Campaigns", e)

    # ── Campaign Manager ───────────────────────────────────────────────
    elif page == "camp_manager":
        if role not in ("super_admin","org_admin","manager","campaign_manager"):
            _access_denied(); return
        try:
            from dashboards.campaign_manager_dashboard import render
            render(user)
        except Exception as e:
            _dashboard_error("Campaign Manager", e)

    # ── Estimator ─────────────────────────────────────────────────────
    elif page == "estimator":
        if role not in ("super_admin","org_admin","manager"):
            _access_denied(); return
        try:
            from dashboards.estimator_dashboard import render
            render(user)
        except Exception as e:
            _dashboard_error("Estimator", e)

    # ── Report Builder ────────────────────────────────────────────────
    elif page == "report_builder":
        if role not in ("super_admin","org_admin","manager","campaign_manager"):
            _access_denied(); return
        try:
            from dashboards.campaign_report_dashboard import render
            render(user)
        except Exception as e:
            _dashboard_error("Report Builder", e)

    # ── Admin ──────────────────────────────────────────────────────────
    elif page == "admin":
        if role not in ("super_admin","org_admin"):
            _access_denied(); return
        try:
            from dashboards.admin_dashboard import render
            render(user)
        except Exception as e:
            _dashboard_error("Admin", e)

    # ── Enrichment Pipeline ────────────────────────────────────────────
    elif page == "enrichment":
        if role not in ("super_admin","org_admin","manager","researcher"):
            _access_denied(); return
        try:
            from dashboards.enrichment_dashboard import render
            render(user)
        except Exception as e:
            _dashboard_error("Enrichment", e)

    # ── AI Scoring (bring-your-own-AI, no key) ─────────────────────────
    elif page == "scoring":
        if role not in ("super_admin","org_admin","manager","research_manager","researcher"):
            _access_denied(); return
        try:
            from dashboards.scoring_dashboard import render
            render(user)
        except Exception as e:
            _dashboard_error("AI Scoring", e)

    # ── Email List Matching (Module D3) ────────────────────────────────
    elif page == "email_match":
        if role not in ("super_admin","org_admin","manager","research_manager","researcher"):
            _access_denied(); return
        try:
            from dashboards.email_match_dashboard import render
            render(user)
        except Exception as e:
            _dashboard_error("Email Match", e)

    # ── LinkedIn Enricher (web search, no login) ───────────────────────
    elif page == "enricher":
        if role not in ("super_admin","org_admin","manager","research_manager","researcher"):
            _access_denied(); return
        try:
            from dashboards.enricher_dashboard import render
            render(user)
        except Exception as e:
            _dashboard_error("LinkedIn Enricher", e)

    # ── Outreach Pipeline ─────────────────────────────────────────────
    elif page == "outreach":
        if role not in ("super_admin","org_admin","manager","researcher"):
            _access_denied(); return
        try:
            from dashboards.outreach_dashboard import render
            render(user)
        except Exception as e:
            _dashboard_error("Outreach", e)

    else:
        st.info(f"Page '{page}' not found.")


def _access_denied():
    st.error("🚫 You don't have permission to access this page.")


def _dashboard_error(name: str, err: Exception):
    import traceback
    logging.exception(f"[app] {name} dashboard error: {err}")
    st.error(f"⚠ {name} dashboard failed to load.")
    with st.expander("Error details"):
        st.code(str(err))
    with st.expander("Traceback"):
        st.code(traceback.format_exc())


# ── LOGIN / SIGNUP ────────────────────────────────────────────────────────────

def render_login(invite_token: str = None):
    """Login page — or signup form if invite token present."""

    # Centered layout
    _, col, _ = st.columns([1, 1.3, 1])
    with col:
        st.markdown("""
        <div style="max-width:400px;margin:0 auto;background:white;
                    border:1px solid #E8E4DD;border-radius:14px;
                    padding:40px;box-shadow:0 4px 24px rgba(0,0,0,.06);
                    margin-top:60px;">
            <div style="font-family:'Playfair Display',serif;font-size:26px;
                        font-weight:700;color:#1A1917;margin-bottom:4px;">
                Dashin<span style="color:#C9A96E;">.</span>
            </div>
            <div style="font-size:13px;color:#999;margin-bottom:28px;">
                Research Operations Platform
            </div>
        </div>
        """, unsafe_allow_html=True)

        if invite_token:
            _render_signup(invite_token)
        else:
            _render_login_form()


def _render_login_form():
    with st.form("login_form"):
        email    = st.text_input("Email", placeholder="you@company.com")
        password = st.text_input("Password", type="password",
                                  placeholder="••••••••")
        submit   = st.form_submit_button("Sign In",
                                          use_container_width=True,
                                          type="primary")

    if submit:
        if not email or not password:
            st.error("Please enter your email and password.")
            return
        from core.auth import login, set_session
        user = login(email, password)
        if user:
            set_session(user)
            st.rerun()
        else:
            st.error("Invalid email or password, or account is inactive.")

    st.markdown(
        '<p style="text-align:center;font-size:11px;color:#BBB;margin-top:12px;">'
        'Default: admin@dashin.com / admin123'
        '</p>',
        unsafe_allow_html=True
    )


def _render_signup(token: str):
    """Self-serve signup form shown when following an invite link."""
    from services.invite_service import validate_token, redeem_token
    from core.auth import login, set_session

    invite = validate_token(token)
    if not invite:
        st.error("This invite link is invalid or has expired. "
                 "Please contact your account manager.")
        if st.button("Back to Sign In"):
            st.query_params.clear()
            st.rerun()
        return

    st.subheader(f"Create your account")
    st.caption(
        f"You're joining **{invite.get('client_name', 'your team')}** "
        f"as {invite.get('role','client_user').replace('_',' ').title()}"
    )

    with st.form("signup_form"):
        name      = st.text_input("Your full name *")
        email_val = invite.get("email") or ""
        email     = st.text_input(
            "Email *",
            value=email_val,
            disabled=bool(email_val)
        )
        password  = st.text_input("Password *", type="password",
                                   help="Minimum 8 characters")
        confirm   = st.text_input("Confirm password *", type="password")
        submit    = st.form_submit_button("Create Account",
                                           use_container_width=True,
                                           type="primary")

    if submit:
        errors = []
        if not name.strip():          errors.append("Name is required.")
        if not email.strip():         errors.append("Email is required.")
        if len(password) < 8:         errors.append("Password must be at least 8 characters.")
        if password != confirm:        errors.append("Passwords don't match.")

        if errors:
            for e in errors:
                st.error(e)
            return

        result = redeem_token(token, name, email, password)
        if result["success"]:
            user = login(email, password)
            if user:
                set_session(user)
                st.query_params.clear()
                st.rerun()
        else:
            st.error(result["error"])


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    # Initialise DB on every cold start
    init_db()
    migrate_db()
    ensure_defaults()

    # Check for invite token in URL params
    params = st.query_params
    invite_token = params.get("invite")

    # Not logged in
    if "user" not in st.session_state:
        render_login(invite_token=invite_token)
        return

    user = st.session_state["user"]

    # Re-validate user is still active (catch deactivations)
    conn = get_connection()
    live = conn.execute(
        "SELECT is_active, role FROM users WHERE id=?",
        (user["id"],)
    ).fetchone()
    conn.close()

    if not live or not live["is_active"]:
        st.session_state.pop("user", None)
        st.error("Your account has been deactivated. "
                 "Please contact your administrator.")
        st.stop()

    # Sync role if changed by admin
    if live["role"] != user.get("role"):
        user["role"] = live["role"]
        st.session_state["user"] = user

    # Onboarding wizard — show on first login until completed
    if not user.get("onboarded_at"):
        try:
            from dashboards.onboarding_wizard import render as render_onboarding
            render_onboarding(user)
            st.stop()
        except Exception as _ob_err:
            logging.warning(f"[app] Onboarding wizard failed: {_ob_err}")
            # Don't block the app if wizard fails

    # Render
    page = render_sidebar(user)

    # Apply light/dark theme on top of the shared design system
    from core.theme import apply_theme
    apply_theme()

    route(page, user)


if __name__ == "__main__":
    main()