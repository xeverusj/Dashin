"""
core/theme.py — Dashin Research Platform

Streamlit native-widget fixes. The PALETTE now lives entirely in
core/styles.py (inject_shared_css sets the --bg/--surface/--accent/... tokens
per theme). This module no longer defines colours — it only maps Streamlit's
built-in widgets (inputs, selects, dataframes, tabs, alerts) and the custom card
text classes onto those variables, so both dark and light themes adapt
automatically with no hardcoded colours to drift out of sync.
"""

import streamlit as st

from core.styles import get_theme

# All rules reference the palette variables set by inject_shared_css(), so this
# single block works for BOTH themes — nothing here is theme-specific.
WIDGET_FIXES = """
<style>
/* Base text */
.stApp p, .stApp li { color: var(--text-2) !important; }
.stApp h1, .stApp h2, .stApp h3, .stApp h4 { color: var(--text-1) !important; }
.stApp a { color: var(--accent) !important; }
.stApp label { color: var(--text-3) !important; }
div[data-testid="stCaptionContainer"] { color: var(--text-3) !important; }
div[data-testid="stMarkdownContainer"] p { color: var(--text-2) !important; }

/* Metrics */
div[data-testid="stMetricValue"] { color: var(--text-1) !important; }
div[data-testid="metric-container"] label,
div[data-testid="stMetricDelta"] { color: var(--text-3) !important; }

/* Inputs / selects */
div.stTextInput input, div.stTextArea textarea, div.stNumberInput input {
    background: var(--surface) !important; border-color: var(--border) !important; color: var(--text-1) !important; }
div[data-baseweb="select"] > div {
    background: var(--surface) !important; border-color: var(--border) !important; color: var(--text-1) !important; }
div[data-baseweb="select"] [data-testid="stMarkdownContainer"] { color: var(--text-1) !important; }
div[data-baseweb="popover"] li { background: var(--surface) !important; color: var(--text-1) !important; }
div[data-baseweb="tag"] { background: var(--accent-bg) !important; color: var(--text-1) !important; }
div.stCheckbox label span, div.stRadio label span { color: var(--text-2) !important; }

/* Dataframe */
div[data-testid="stDataFrame"] { background: var(--surface) !important; }
div[data-testid="stDataFrame"] th { background: var(--surface-2) !important; color: var(--accent) !important; border-color: var(--border) !important; }
div[data-testid="stDataFrame"] td { color: var(--text-2) !important; border-color: var(--border) !important; }

/* Expander / tabs / alerts / dividers */
details { background: var(--surface) !important; border-color: var(--border) !important; }
details summary { color: var(--text-1) !important; }
details p, details li { color: var(--text-2) !important; }
button[data-baseweb="tab"] { color: var(--text-3) !important; }
button[data-baseweb="tab"][aria-selected="true"] { color: var(--text-1) !important; border-bottom-color: var(--accent) !important; }
div[data-testid="stAlert"] { background: var(--surface) !important; border-color: var(--border) !important; }
div[data-testid="stAlert"] p { color: var(--text-2) !important; }
div[data-baseweb="notification"] { background: var(--surface-2) !important; border-color: var(--border) !important; }
hr { border-color: var(--border) !important; }

/* Custom HTML card/text classes → palette variables (kills hardcoded drift) */
.page-title, .sec-hd, .sec-header, .stat-val, .kpi-val, .kpi-value,
.card-title, .task-title, .camp-title, .task-review-title, .detail-name,
.user-name, .list-card-name, .org-name, .cost-num, .tbl td.n,
.detail-field-val { color: var(--text-1) !important; }
.page-sub, .stat-label, .kpi-label, .card-meta, .task-meta, .camp-meta,
.detail-sub, .user-email, .list-card-meta, .list-card-desc, .org-meta,
.cost-label, .tbl td.co, .detail-field-label { color: var(--text-3) !important; }
.tbl td, .crm-table td, .kpi-table-row, .lead-row, .breakdown-row { color: var(--text-2) !important; }
.invite-token { background: var(--surface-2) !important; color: var(--text-3) !important; border-color: var(--border) !important; }
.sec-hd, .sec-header { border-color: var(--border) !important; }
.breakdown-row { border-color: var(--border-light) !important; }
</style>
"""


def apply_theme() -> bool:
    """
    Inject the native-widget fixes (palette-variable based). Called once per
    render from app.py after render_sidebar(). Returns True if dark is active.
    """
    st.markdown(WIDGET_FIXES, unsafe_allow_html=True)
    return get_theme() == "dark"
