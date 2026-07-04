"""
core/auth.py — Dashin Research Platform
Login, session management, role-based access control, org isolation.
"""

import hashlib
import logging
import streamlit as st
from datetime import datetime
from core.db import get_connection

try:
    import bcrypt as _bcrypt
    _BCRYPT_AVAILABLE = True
except ImportError:
    _BCRYPT_AVAILABLE = False
    logging.warning("[auth] bcrypt not installed — passwords insecure. Run: pip install bcrypt")

# ── ROLE HIERARCHY ────────────────────────────────────────────────────────────
# Higher number = more permissions within its scope

ROLE_LEVELS = {
    "super_admin":       100,
    "org_admin":          80,
    "manager":            60,
    "research_manager":   50,
    "campaign_manager":   45,
    "researcher":         30,
    "client_admin":       20,
    "client_user":        10,
}

# Roles that belong to the internal team (not clients)
INTERNAL_ROLES = {
    "super_admin", "org_admin", "manager",
    "research_manager", "campaign_manager", "researcher"
}

# Roles that belong to clients
CLIENT_ROLES = {"client_admin", "client_user"}


def hash_password(password: str) -> str:
    """Hash a password using bcrypt. Falls back to SHA-256 if bcrypt unavailable."""
    if _BCRYPT_AVAILABLE:
        return _bcrypt.hashpw(password.encode('utf-8'), _bcrypt.gensalt()).decode('utf-8')
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(plain: str, stored: str) -> bool:
    """
    Verify a password against a stored hash.
    Handles both bcrypt ($2b$ prefix) and legacy SHA-256 (64-char hex).
    """
    if _BCRYPT_AVAILABLE and stored.startswith('$2b$'):
        return _bcrypt.checkpw(plain.encode('utf-8'), stored.encode('utf-8'))
    # Legacy SHA-256 path
    return hashlib.sha256(plain.encode()).hexdigest() == stored


def login(email: str, password: str) -> dict | None:
    """
    Attempt login. Returns user dict on success, None on failure.
    Updates last_login timestamp.
    """
    conn = get_connection()
    user = conn.execute("""
        SELECT u.*,
               o.name               AS org_name,
               o.tier               AS org_tier,
               o.is_active          AS org_active,
               o.org_type           AS org_type,
               o.subscription_tier  AS subscription_tier,
               c.name               AS client_name
        FROM users u
        LEFT JOIN organisations o ON o.id = u.org_id
        LEFT JOIN clients c       ON c.id = u.client_id
        WHERE u.email=? AND u.is_active=1
    """, (email.lower().strip(),)).fetchone()

    if not user:
        conn.close()
        return None

    # Verify password (bcrypt or legacy SHA-256)
    stored_hash = user.get("password") or ""
    if not verify_password(password, stored_hash):
        conn.close()
        return None

    # Check org is active
    if not user["org_active"]:
        conn.close()
        return None

    # If using legacy hash, upgrade it to bcrypt now
    if _BCRYPT_AVAILABLE and not stored_hash.startswith('$2b$'):
        new_hash = hash_password(password)
        conn.execute(
            "UPDATE users SET password=? WHERE id=?",
            (new_hash, user["id"])
        )

    # Update last login
    conn.execute(
        "UPDATE users SET last_login=? WHERE id=?",
        (datetime.utcnow().isoformat(), user["id"])
    )
    conn.commit()
    conn.close()
    return dict(user)


def logout():
    """Clear session state."""
    for key in ["user", "org_id", "page"]:
        if key in st.session_state:
            del st.session_state[key]
    st.rerun()


def get_current_user() -> dict | None:
    """Get logged-in user from session state."""
    return st.session_state.get("user")


def require_login():
    """Redirect to login if not authenticated."""
    if not get_current_user():
        st.stop()


def set_session(user: dict):
    """Store user in session after login."""
    st.session_state["user"]   = user
    st.session_state["org_id"] = user["org_id"]


# ── PERMISSION CHECKS ─────────────────────────────────────────────────────────

def has_role(user: dict, *roles) -> bool:
    """Check if user has any of the given roles."""
    return user.get("role") in roles


def is_internal(user: dict) -> bool:
    return user.get("role") in INTERNAL_ROLES


def is_client(user: dict) -> bool:
    return user.get("role") in CLIENT_ROLES


def is_super_admin(user: dict) -> bool:
    return user.get("role") == "super_admin"


def can_manage_users(user: dict) -> bool:
    return has_role(user, "super_admin", "org_admin")


def can_manage_research(user: dict) -> bool:
    return has_role(user, "super_admin", "org_admin",
                    "manager", "research_manager")


def can_approve_lists(user: dict) -> bool:
    return has_role(user, "super_admin", "org_admin",
                    "manager", "research_manager")


def can_mark_campaign_ready(user: dict) -> bool:
    return has_role(user, "super_admin", "org_admin", "manager")


def can_view_costs(user: dict) -> bool:
    return has_role(user, "super_admin", "org_admin", "manager")


def can_access_scraper(user: dict) -> bool:
    return has_role(user, "super_admin", "org_admin",
                    "manager", "researcher")


def can_access_campaign_mgmt(user: dict) -> bool:
    return has_role(user, "super_admin", "org_admin",
                    "manager", "campaign_manager")


def same_org(user: dict, org_id: int) -> bool:
    """Ensure user belongs to the given org (super_admin bypasses)."""
    if is_super_admin(user):
        return True
    return user.get("org_id") == org_id


def can_see_client_data(user: dict, client_id: int) -> bool:
    """
    Internal users can see all clients in their org.
    Client users can only see their own client.
    """
    if is_internal(user):
        return True
    return user.get("client_id") == client_id


# ── LOGIN UI ──────────────────────────────────────────────────────────────────

def render_login_page(invite_token: str = None):
    """
    Render the login page.
    If invite_token is provided, shows signup form instead.
    """
    st.markdown("""
    <style>
    .login-container {
        max-width: 420px;
        margin: 60px auto;
        padding: 40px;
        background: #FFFFFF;
        border-radius: 12px;
        border: 1px solid #E8E6E1;
        box-shadow: 0 4px 24px rgba(0,0,0,0.06);
    }
    .login-logo {
        text-align: center;
        font-size: 28px;
        font-weight: 700;
        color: #1A1917;
        margin-bottom: 8px;
        letter-spacing: -0.5px;
    }
    .login-sub {
        text-align: center;
        color: #888;
        font-size: 13px;
        margin-bottom: 32px;
    }
    </style>
    <div class="login-container">
        <div class="login-logo">⚡ Dashin</div>
        <div class="login-sub">Research Operations Platform</div>
    </div>
    """, unsafe_allow_html=True)

    if invite_token:
        _render_signup_form(invite_token)
    else:
        _render_login_form()


def _render_login_form():
    # Handle must_reset_password flow
    if st.session_state.get("_pending_reset_user"):
        _render_password_reset_form()
        return

    with st.form("login_form"):
        st.subheader("Sign in")
        email    = st.text_input("Email", placeholder="you@agency.com")
        password = st.text_input("Password", type="password")
        submit   = st.form_submit_button("Sign in", use_container_width=True)

    if submit:
        if not email or not password:
            st.error("Please enter your email and password.")
            return
        user = login(email, password)
        if user:
            if user.get("must_reset_password"):
                st.session_state["_pending_reset_user"] = user
                st.rerun()
            else:
                set_session(user)
                st.rerun()
        else:
            st.error("Invalid email or password, or account is inactive.")


def _render_password_reset_form():
    """Shown after login when must_reset_password=1."""
    pending = st.session_state.get("_pending_reset_user", {})
    st.warning(f"Welcome {pending.get('name', '')}! You must set a new password before continuing.")
    with st.form("reset_form"):
        new_pw  = st.text_input("New password", type="password", help="Minimum 8 characters")
        new_pw2 = st.text_input("Confirm new password", type="password")
        submit  = st.form_submit_button("Set password", use_container_width=True)

    if submit:
        if len(new_pw) < 8:
            st.error("Password must be at least 8 characters.")
            return
        if new_pw != new_pw2:
            st.error("Passwords don't match.")
            return
        conn = get_connection()
        new_hash = hash_password(new_pw)
        conn.execute(
            "UPDATE users SET password=?, must_reset_password=0 WHERE id=?",
            (new_hash, pending["id"])
        )
        conn.commit()
        conn.close()
        st.session_state.pop("_pending_reset_user", None)
        pending["must_reset_password"] = 0
        set_session(pending)
        st.success("Password updated. Redirecting...")
        st.rerun()


def _render_signup_form(token: str):
    """Signup form shown when user follows an invite link."""
    from services.invite_service import validate_token, redeem_token

    invite = validate_token(token)
    if not invite:
        st.error("This invite link is invalid or has expired. Please contact your account manager.")
        return

    st.subheader(f"Create your account")
    st.caption(f"You're joining **{invite.get('client_name','your organisation')}**")

    with st.form("signup_form"):
        name       = st.text_input("Your name")
        email      = st.text_input(
            "Email",
            value=invite.get("email") or "",
            disabled=bool(invite.get("email"))
        )
        password   = st.text_input("Password", type="password",
                                    help="Minimum 8 characters")
        password2  = st.text_input("Confirm password", type="password")
        submit     = st.form_submit_button("Create account",
                                           use_container_width=True)

    if submit:
        if not name or not email or not password:
            st.error("Please fill in all fields.")
            return
        if password != password2:
            st.error("Passwords don't match.")
            return
        result = redeem_token(token, name, email, password)
        if result["success"]:
            # Auto-login
            user = login(email, password)
            if user:
                set_session(user)
                st.success("Account created! Redirecting...")
                st.rerun()
        else:
            st.error(result["error"])


# ── NOTIFICATION BADGE ────────────────────────────────────────────────────────

def render_notification_badge(user: dict):
    """Show unread notification count in sidebar."""
    from services.notification_service import unread_count
    count = unread_count(user["id"])
    if count > 0:
        st.sidebar.markdown(
            f'<span style="background:#E53935;color:white;padding:2px 8px;'
            f'border-radius:12px;font-size:11px;font-weight:700;">'
            f'🔔 {count} new</span>',
            unsafe_allow_html=True
        )
