"""
core/theme.py — Dashin Research Platform
Light / dark theme applied globally from app.py after render_sidebar().

Because core/styles.py defines all component styles using CSS custom
properties (--bg, --surface, --text-1, etc.), dark mode only needs to
override those root variables — every component adapts automatically.
Text colours are explicitly set so nothing becomes invisible.
"""

import streamlit as st

# ── DARK MODE ─────────────────────────────────────────────────────────────────
DARK_CSS = """
<style>
/* ── Override CSS variables for dark mode ────────────────────────────────── */
:root {
    --bg:           #0D0D0D;
    --surface:      #1A1A1A;
    --surface-2:    #242424;
    --border:       #2A2A2A;
    --border-light: #1E1E1E;

    --text-1: #F0EDE8;
    --text-2: #C8C4BE;
    --text-3: #686460;

    --accent-bg:     rgba(201,169,110,.14);
    --accent-border: rgba(201,169,110,.32);

    --success-bg:     #0C2218;
    --success-border: #174A30;

    --error-bg:     #280C0C;
    --error-border: #4A1818;

    --info-bg:     #0C1228;
    --info-border: #182060;

    --purple-bg:     #160C28;
    --purple-border: #281860;

    --shadow-sm: 0 1px 4px rgba(0,0,0,.25);
    --shadow-md: 0 2px 12px rgba(0,0,0,.4);
}

/* ── App background ───────────────────────────────────────────────────────── */
.stApp { background: var(--bg) !important; }
.main .block-container { background: var(--bg) !important; }

/* ── Base text — be explicit so nothing goes invisible ───────────────────── */
.stApp p, .stApp li { color: var(--text-2) !important; }
.stApp h1, .stApp h2, .stApp h3, .stApp h4 { color: var(--text-1) !important; }
.stApp a { color: var(--accent) !important; }

/* ── Streamlit labels, captions, help text ───────────────────────────────── */
.stApp label                          { color: var(--text-3) !important; }
div[data-testid="stCaptionContainer"] { color: var(--text-3) !important; }
div[data-testid="stMarkdownContainer"] p { color: var(--text-2) !important; }

/* ── Streamlit Metrics ───────────────────────────────────────────────────── */
div[data-testid="metric-container"] label { color: var(--text-3) !important; }
div[data-testid="stMetricValue"]          { color: var(--text-1) !important; }
div[data-testid="stMetricDelta"]          { color: var(--text-3) !important; }

/* ── Inputs ──────────────────────────────────────────────────────────────── */
div.stTextInput input,
div.stTextArea textarea,
div.stNumberInput input {
    background: var(--surface) !important;
    border-color: var(--border) !important;
    color: var(--text-1) !important;
}
div[data-baseweb="select"] > div {
    background: var(--surface) !important;
    border-color: var(--border) !important;
    color: var(--text-1) !important;
}
div[data-baseweb="select"] [data-testid="stMarkdownContainer"] {
    color: var(--text-1) !important;
}
/* Multiselect tags */
div[data-baseweb="tag"] {
    background: var(--border) !important;
    color: var(--text-1) !important;
}
div.stCheckbox label span { color: var(--text-2) !important; }
div.stRadio    label span { color: var(--text-2) !important; }

/* ── Buttons ─────────────────────────────────────────────────────────────── */
div.stButton > button[kind="primary"] {
    background: #2A2A2A !important;
    color: var(--text-1) !important;
    border: 1px solid var(--border) !important;
}
div.stButton > button[kind="primary"]:hover {
    background: var(--accent) !important;
    color: #111 !important;
    border-color: var(--accent) !important;
}
div.stButton > button[kind="secondary"] {
    background: transparent !important;
    color: var(--text-2) !important;
    border-color: var(--border) !important;
}
div.stButton > button[kind="secondary"]:hover {
    border-color: var(--accent) !important;
    color: var(--accent) !important;
}

/* ── Dataframe ───────────────────────────────────────────────────────────── */
div[data-testid="stDataFrame"]    { background: var(--surface) !important; }
div[data-testid="stDataFrame"] th { background: var(--surface-2) !important; color: var(--accent) !important; border-color: var(--border) !important; }
div[data-testid="stDataFrame"] td { color: var(--text-2) !important; border-color: var(--border) !important; }

/* ── Expander ────────────────────────────────────────────────────────────── */
details         { background: var(--surface) !important; border-color: var(--border) !important; }
details summary { color: var(--accent) !important; }
details p, details li { color: var(--text-2) !important; }

/* ── Tabs ────────────────────────────────────────────────────────────────── */
button[data-baseweb="tab"]                       { color: var(--text-3) !important; }
button[data-baseweb="tab"][aria-selected="true"] { color: var(--text-1) !important; border-bottom-color: var(--accent) !important; }

/* ── Alert / info boxes ──────────────────────────────────────────────────── */
div[data-testid="stAlert"] { background: var(--surface) !important; border-color: var(--border) !important; }
div[data-testid="stAlert"] p { color: var(--text-2) !important; }

/* ── st.info / st.success / st.warning / st.error ───────────────────────── */
div[data-baseweb="notification"] {
    background: var(--surface-2) !important;
    border-color: var(--border) !important;
}

/* ── Dividers ────────────────────────────────────────────────────────────── */
hr { border-color: var(--border) !important; }

/* ── Custom HTML component classes ──────────────────────────────────────────
   All share the CSS variables so most adapt automatically.
   These overrides handle hardcoded colours that don't use variables.       */

/* Page titles */
.page-title { color: var(--text-1) !important; }
.page-sub   { color: var(--text-3) !important; }

/* Section headers */
.sec-hd, .sec-header { color: var(--text-1) !important; border-color: var(--border) !important; }

/* Stat cards — values + labels */
.stat-val   { color: var(--text-1) !important; }
.stat-label { color: var(--text-3) !important; }

/* KPI cards */
.kpi-val, .kpi-value { color: var(--text-1) !important; }
.kpi-label           { color: var(--text-3) !important; }

/* Card text */
.card-title, .task-title, .camp-title, .task-review-title,
.detail-name, .user-name, .list-card-name, .org-name {
    color: var(--text-1) !important;
}
.card-meta, .task-meta, .camp-meta, .camp-client,
.detail-sub, .user-email, .list-card-meta, .list-card-desc,
.org-meta { color: var(--text-3) !important; }

/* Tables */
.tbl td    { color: var(--text-2) !important; }
.tbl td.n  { color: var(--text-1) !important; }
.tbl td.co { color: var(--text-3) !important; }

/* Detail fields */
.detail-field-label { color: var(--text-3) !important; }
.detail-field-val   { color: var(--text-1) !important; }

/* CRM table text */
.crm-table td { color: var(--text-2) !important; }

/* KPI table rows */
.kpi-table-row { color: var(--text-2) !important; }

/* Cost cards */
.cost-num   { color: var(--text-1) !important; }
.cost-label { color: var(--text-3) !important; }
.breakdown-row { color: var(--text-2) !important; border-color: var(--border-light) !important; }

/* Tip / alert boxes */
.tip, .alert-warn {
    background: #1A1400 !important;
    border-color: #3A2C00 !important;
    color: var(--accent) !important;
}

/* Ready / not-ready banners */
.ready-banner {
    background: var(--success-bg) !important;
    border-color: var(--success-border) !important;
}
.not-ready-banner {
    background: #1A1400 !important;
    border-color: var(--accent-border) !important;
}

/* Lead rows */
.lead-row { color: var(--text-2) !important; border-color: var(--border-light) !important; }

/* Invite token display */
.invite-token { background: var(--surface-2) !important; color: var(--text-3) !important; border-color: var(--border) !important; }

/* Progress bar */
.progress-bar-wrap { background: var(--border) !important; }

/* Deadline indicators (already use !important with variables in _UNIQUE_CSS) */

/* Campaign header sub (gold colour is fine in dark) */
.camp-client { color: var(--accent) !important; }
</style>
"""

# ── LIGHT MODE ────────────────────────────────────────────────────────────────
LIGHT_CSS = """
<style>
/* ── Restore CSS variables to light defaults ─────────────────────────────── */
:root {
    --bg:           #F7F6F3;
    --surface:      #FFFFFF;
    --surface-2:    #F8F7F4;
    --border:       #E8E4DD;
    --border-light: #F0EDE8;

    --text-1: #1A1917;
    --text-2: #555555;
    --text-3: #999999;

    --accent-bg:     rgba(201,169,110,.08);
    --accent-border: rgba(201,169,110,.25);

    --success-bg:     #ECF7F0;
    --success-border: #B8DFC8;

    --error-bg:     #FDECEA;
    --error-border: #F0B8B8;

    --info-bg:     #EEF1FF;
    --info-border: #C0CEFF;

    --purple-bg:     #F3F0FF;
    --purple-border: #D0C0FF;

    --shadow-sm: 0 1px 4px rgba(0,0,0,.05);
    --shadow-md: 0 2px 12px rgba(0,0,0,.08);
}

/* ── Restore app background ──────────────────────────────────────────────── */
.stApp { background: var(--bg) !important; color: var(--text-1) !important; }
.main .block-container { background: var(--bg) !important; }

/* ── Restore text ────────────────────────────────────────────────────────── */
.stApp p, .stApp li { color: var(--text-2) !important; }
.stApp h1, .stApp h2, .stApp h3, .stApp h4 { color: var(--text-1) !important; }
.stApp label { color: var(--text-3) !important; }
div[data-testid="stCaptionContainer"] { color: var(--text-3) !important; }

/* ── Restore inputs ──────────────────────────────────────────────────────── */
div.stTextInput input,
div.stTextArea textarea,
div.stNumberInput input {
    background: var(--surface-2) !important;
    border-color: var(--border) !important;
    color: var(--text-1) !important;
}
div[data-baseweb="select"] > div {
    background: var(--surface-2) !important;
    border-color: var(--border) !important;
    color: var(--text-1) !important;
}
div.stCheckbox label span { color: var(--text-1) !important; }
div.stRadio    label span { color: var(--text-1) !important; }

/* ── Restore buttons ─────────────────────────────────────────────────────── */
div.stButton > button[kind="primary"] {
    background: #1A1917 !important;
    color: #FFF !important;
    border: none !important;
}
div.stButton > button[kind="primary"]:hover {
    background: var(--accent) !important;
    color: #FFF !important;
}
div.stButton > button[kind="secondary"] {
    background: transparent !important;
    color: var(--text-1) !important;
    border-color: var(--border) !important;
}

/* ── Restore dataframe ───────────────────────────────────────────────────── */
div[data-testid="stDataFrame"] th { background: var(--surface-2) !important; color: var(--text-1) !important; }
div[data-testid="stDataFrame"] td { color: var(--text-2) !important; }

/* ── Restore expander ────────────────────────────────────────────────────── */
details         { background: var(--surface) !important; border-color: var(--border) !important; }
details summary { color: var(--text-1) !important; }

/* ── Restore tabs ────────────────────────────────────────────────────────── */
button[data-baseweb="tab"]                       { color: var(--text-3) !important; }
button[data-baseweb="tab"][aria-selected="true"] { color: var(--text-1) !important; border-bottom-color: var(--accent) !important; }

/* ── Restore tip boxes ───────────────────────────────────────────────────── */
.tip, .alert-warn {
    background: #FFFDF5 !important;
    border-color: #E8D5A8 !important;
    color: #8A7040 !important;
}
</style>
"""


def apply_theme() -> bool:
    """
    Inject the current light or dark theme CSS.
    Called once per render from app.py after render_sidebar().
    Returns True if dark mode is active.
    """
    dark = st.session_state.get("dark_mode", False)
    st.markdown(DARK_CSS if dark else LIGHT_CSS, unsafe_allow_html=True)
    return dark
