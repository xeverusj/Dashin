"""
dashboards/onboarding_wizard.py — Dashin Research Platform
First-login onboarding wizard.

Shown once when a user's onboarded_at is NULL.
After completing, sets onboarded_at on both user and org.
"""

import streamlit as st
from core.db import get_connection


STYLES = """
<style>
.ob-container {
    max-width: 640px;
    margin: 40px auto;
    background: #FFFFFF;
    border: 1px solid #E8E4DD;
    border-radius: 14px;
    padding: 40px 48px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.06);
}
.ob-logo {
    font-family: 'Playfair Display', serif;
    font-size: 24px;
    font-weight: 700;
    color: #1A1917;
    margin-bottom: 6px;
}
.ob-logo span { color: #C9A96E; }
.ob-step {
    font-size: 11px;
    color: #BBB;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    font-weight: 600;
    margin-bottom: 24px;
}
.ob-title {
    font-size: 22px;
    font-weight: 700;
    color: #1A1917;
    margin-bottom: 8px;
}
.ob-sub {
    font-size: 14px;
    color: #888;
    margin-bottom: 28px;
    line-height: 1.6;
}
.ob-next-item {
    background: #F8F7F4;
    border: 1px solid #E8E4DD;
    border-radius: 8px;
    padding: 12px 16px;
    margin-bottom: 8px;
    font-size: 13px;
    color: #1A1917;
}
.ob-next-item strong { color: #C9A96E; }
</style>
"""


def render(user: dict):
    """
    Multi-step onboarding wizard.
    Call this before routing if user.onboarded_at is None.
    """
    st.markdown(STYLES, unsafe_allow_html=True)

    # Centered layout
    _, col, _ = st.columns([1, 2, 1])
    with col:
        st.markdown("""
        <div style="text-align:center;margin-bottom:32px;">
            <div class="ob-logo">Dashin<span>.</span></div>
        </div>
        """, unsafe_allow_html=True)

        step     = st.session_state.get("onboarding_step", 1)
        org_type = user.get("org_type", "agency")

        # ── Step 1: Org profile ────────────────────────────────────────────
        if step == 1:
            st.markdown(
                '<div class="ob-step">Step 1 of 3 — Your Organisation</div>',
                unsafe_allow_html=True
            )
            st.markdown(
                '<div class="ob-title">Welcome to Dashin Research</div>',
                unsafe_allow_html=True
            )
            st.markdown(
                '<div class="ob-sub">Let\'s set up your account. '
                'This only takes a minute.</div>',
                unsafe_allow_html=True
            )

            conn = get_connection()
            org  = conn.execute(
                "SELECT * FROM organisations WHERE id=?", (user["org_id"],)
            ).fetchone()
            conn.close()
            org = dict(org) if org else {}

            with st.form("ob_step1"):
                org_name = st.text_input(
                    "Organisation name",
                    value=org.get("name", ""),
                    placeholder="Sales Academy Ltd"
                )
                industry = st.selectbox(
                    "Primary industry",
                    ["Insurance", "Finance", "Technology",
                     "Education", "Healthcare", "Real Estate", "Other"]
                )
                website = st.text_input(
                    "Website (optional)",
                    placeholder="https://yourcompany.com"
                )

                if st.form_submit_button("Next →", use_container_width=True, type="primary"):
                    # Save org name update if changed
                    if org_name.strip() and org_name.strip() != org.get("name", ""):
                        conn2 = get_connection()
                        conn2.execute(
                            "UPDATE organisations SET name=? WHERE id=?",
                            (org_name.strip(), user["org_id"])
                        )
                        conn2.commit()
                        conn2.close()
                        # Update session
                        st.session_state["user"]["org_name"] = org_name.strip()

                    st.session_state["onboarding_step"] = 2
                    st.rerun()

        # ── Step 2: Context-specific setup ────────────────────────────────
        elif step == 2:
            st.markdown(
                '<div class="ob-step">Step 2 of 3 — Your Setup</div>',
                unsafe_allow_html=True
            )

            if org_type == "client":
                st.markdown(
                    '<div class="ob-title">Your Account</div>',
                    unsafe_allow_html=True
                )
                st.markdown(
                    '<div class="ob-sub">You\'re joining as a client. '
                    'Your leads will appear in your portal once your agency '
                    'releases them to you.</div>',
                    unsafe_allow_html=True
                )

                with st.form("ob_step2_client"):
                    has_agency = st.radio(
                        "Are you working with a Dashin partner agency?",
                        ["Yes, I was referred by an agency",
                         "No, I'm subscribing directly to Dashin"]
                    )
                    agency_email = ""
                    if "Yes" in has_agency:
                        agency_email = st.text_input(
                            "Agency contact email",
                            placeholder="contact@youragency.com",
                            help="So we can link your accounts"
                        )

                    if st.form_submit_button("Next →", use_container_width=True, type="primary"):
                        st.session_state["onboarding_step"] = 3
                        st.rerun()

            elif org_type in ("agency", "freelance"):
                st.markdown(
                    '<div class="ob-title">Your Team</div>',
                    unsafe_allow_html=True
                )
                st.markdown(
                    '<div class="ob-sub">Tell us a bit about how you operate '
                    'so we can tailor your experience.</div>',
                    unsafe_allow_html=True
                )

                with st.form("ob_step2_agency"):
                    team_size = st.selectbox(
                        "Team size",
                        ["Just me", "2–5 people", "6–15 people", "16+ people"]
                    )
                    primary_use = st.multiselect(
                        "What will you mainly use Dashin for?",
                        ["Lead research & enrichment",
                         "Event / conference scraping",
                         "Campaign management",
                         "Client reporting",
                         "All of the above"]
                    )

                    if st.form_submit_button("Next →", use_container_width=True, type="primary"):
                        st.session_state["onboarding_step"] = 3
                        st.rerun()

            else:
                # dashin staff — skip step 2
                st.session_state["onboarding_step"] = 3
                st.rerun()

        # ── Step 3: Done ───────────────────────────────────────────────────
        elif step >= 3:
            st.markdown(
                '<div class="ob-step">Step 3 of 3 — You\'re all set!</div>',
                unsafe_allow_html=True
            )
            st.markdown(
                '<div class="ob-title">🎉 Account Ready</div>',
                unsafe_allow_html=True
            )

            if org_type == "agency":
                next_steps = [
                    ("🏢 Add your clients",
                     "Go to Admin → Clients to add your first client."),
                    ("👥 Invite your team",
                     "Go to Admin → Users to add researchers and managers."),
                    ("🔍 Start scraping",
                     "Use the Smart Scraper to pull attendees from events."),
                ]
            elif org_type == "freelance":
                next_steps = [
                    ("👥 Add your team",
                     "Go to Admin → Users to add researchers."),
                    ("🔍 Start scraping",
                     "Use the Smart Scraper to pull attendees from events."),
                    ("📦 Browse inventory",
                     "Your scraped leads will appear in the Inventory."),
                ]
            elif org_type == "client":
                next_steps = [
                    ("📦 Your Inventory",
                     "Leads your agency enriches for you will appear here."),
                    ("📁 Campaigns",
                     "Review and approve campaigns your agency builds."),
                    ("💬 Leave notes",
                     "Add notes and feedback on leads and campaigns."),
                ]
            else:
                next_steps = [
                    ("⚡ Platform", "Manage all organisations from the Platform tab."),
                    ("🏢 Orgs", "Create and manage agency accounts."),
                ]

            for title, desc in next_steps:
                st.markdown(f"""
                <div class="ob-next-item">
                    <strong>{title}</strong><br>
                    <span style="color:#888;font-size:12px;">{desc}</span>
                </div>
                """, unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            if st.button("Go to Dashboard →", use_container_width=True, type="primary"):
                # Mark onboarding complete
                conn = get_connection()
                conn.execute(
                    "UPDATE users SET onboarded_at=datetime('now') WHERE id=?",
                    (user["id"],)
                )
                conn.execute(
                    "UPDATE organisations SET onboarded_at=datetime('now') WHERE id=?",
                    (user["org_id"],)
                )
                conn.commit()
                conn.close()

                # Update session so the wizard doesn't show again
                st.session_state["user"]["onboarded_at"] = "done"
                st.session_state.pop("onboarding_step", None)
                st.rerun()
