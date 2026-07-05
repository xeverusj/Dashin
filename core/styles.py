"""
core/styles.py — Dashin Research Platform
Master design system. Single source of truth for all shared visual styles.
All dashboards call inject_shared_css() from here instead of defining their own fonts,
base layout, buttons, badges, tables, and shared components.

Design system: light (#F8F9FA), clean white cards, purple accent (#5416C9),
Inter typeface across all weights.
"""

import streamlit as st

# ── CSS CUSTOM PROPERTIES + SHARED COMPONENTS ─────────────────────────────────
SHARED_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Instrument+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

/* ── DESIGN TOKENS ──────────────────────────────────────────────────────────
   Injected per-theme by inject_shared_css(theme). __ROOT_VARS__ is replaced
   with the dark or light token set. Fonts/radii are theme-independent and live
   here. */
:root {
    /* Type system */
    --font-sans:    'Instrument Sans', 'Inter', -apple-system, sans-serif;
    --font-display: 'Space Grotesk', 'Instrument Sans', sans-serif;
    --font-mono:    'IBM Plex Mono', ui-monospace, monospace;

    /* Elevation (radii shared; shadows tuned per-theme below) */
    --radius-sm: 8px;
    --radius-md: 12px;
    --radius-lg: 16px;
}
__ROOT_VARS__

/* ── BASE ────────────────────────────────────────────────────────────────── */
html, body, [class*="css"] {
    font-family: var(--font-sans) !important;
}
.stApp {
    background: var(--bg) !important;
    font-family: var(--font-sans) !important;
    color: var(--text-1) !important;
}
.main .block-container {
    background: transparent !important;
    padding: 2rem 2.5rem !important;
    max-width: 1280px !important;
}

/* ── TYPOGRAPHY: display font for headings, mono for data ─────────────────── */
h1, h2, h3,
.stApp h1, .stApp h2, .stApp h3,
[data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3,
.sb-name,
.rq-header-title, .rm-title, .cb-title, .cm-title,
.admin-title, .est-title, .sa-title {
    font-family: var(--font-display) !important;
    letter-spacing: -0.02em !important;
}
/* Metric numbers read as data · mono, tabular */
[data-testid="stMetricValue"],
.metric-value, .stat-num, .kpi-num {
    font-family: var(--font-mono) !important;
    font-feature-settings: 'tnum' 1 !important;
    letter-spacing: -0.02em !important;
}
.mono { font-family: var(--font-mono) !important; }

/* ── SIDEBAR ─────────────────────────────────────────────────────────────── */
[data-testid="stSidebar"] {
    background: var(--surface) !important;
    border-right: 1px solid var(--border) !important;
    backdrop-filter: blur(20px) !important;
}
[data-testid="stSidebar"] * {
    color: var(--text-2) !important;
    font-family: var(--font-sans) !important;
}
/* Sidebar brand mark */
.sb-brand {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 0 0 28px 0;
    margin-bottom: 8px;
    border-bottom: 1px solid var(--border);
}
.sb-name {
    font-size: 18px;
    font-weight: 900;
    letter-spacing: -0.5px;
    color: var(--text-1) !important;
    line-height: 1;
}
.sb-tagline {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--accent) !important;
    margin-top: 2px;
}
/* Sidebar nav items */
.sb-nav-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 14px;
    border-radius: var(--radius-sm);
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--text-3) !important;
    transition: all 0.2s;
    cursor: pointer;
    margin-bottom: 2px;
}
.sb-nav-item:hover {
    color: var(--text-1) !important;
    background: var(--surface-2) !important;
}
.sb-nav-item.active {
    background: var(--accent-bg) !important;
    color: var(--accent) !important;
    border-right: 2px solid var(--accent);
}
/* Sidebar user block */
.sb-user {
    padding-top: 16px;
    border-top: 1px solid var(--border);
    margin-top: auto;
}
.sb-user-name {
    font-size: 12px;
    font-weight: 700;
    color: var(--text-1) !important;
}
.sb-role {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: var(--accent) !important;
    opacity: 0.8;
}

/* ── NORMALIZE ALL DASHBOARD HEADER BARS ─────────────────────────────────── */
.rq-header, .rm-header, .cb-header, .cm-header,
.admin-header, .est-header, .sa-header {
    background: var(--surface) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-md) !important;
    padding: 20px 28px !important;
    color: var(--text-1) !important;
    margin-bottom: 24px !important;
    display: flex !important;
    align-items: flex-start !important;
    justify-content: space-between !important;
    box-shadow: var(--shadow-sm) !important;
}

/* Header title text */
.rq-header-title, .rm-title, .cb-title,
.cm-title, .admin-title, .est-title, .sa-title {
    font-family: var(--font-sans) !important;
    font-size: 22px !important;
    font-weight: 800 !important;
    letter-spacing: -0.5px !important;
    color: var(--text-1) !important;
}

/* Header subtitle text */
.rq-header-sub, .rm-sub, .cb-sub,
.cm-sub, .admin-sub, .est-sub, .sa-sub {
    font-family: var(--font-sans) !important;
    font-size: 12px !important;
    color: var(--text-3) !important;
    margin-top: 5px !important;
    font-weight: 400 !important;
    letter-spacing: 0 !important;
}

/* ── PAGE TITLES ─────────────────────────────────────────────────────────── */
.page-title {
    font-family: var(--font-sans);
    font-size: 36px;
    font-weight: 900;
    color: var(--text-1);
    letter-spacing: -1px;
    margin-bottom: 4px;
    line-height: 1.1;
}
.page-title span { color: var(--accent); }
.page-sub {
    font-size: 12px;
    color: var(--text-3);
    margin-bottom: 24px;
    line-height: 1.5;
    font-weight: 500;
    letter-spacing: 0.05em;
    text-transform: uppercase;
}

/* ── SECTION HEADERS ─────────────────────────────────────────────────────── */
.sec-hd, .sec-header {
    font-family: var(--font-sans);
    font-size: 15px;
    font-weight: 800;
    color: var(--text-1);
    margin: 28px 0 14px;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 8px;
    letter-spacing: -0.2px;
}

/* ── STAT CARDS ──────────────────────────────────────────────────────────── */
.stat-row { display: flex; gap: 14px; margin-bottom: 28px; flex-wrap: wrap; }
.stat-card {
    flex: 1;
    min-width: 130px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 20px 22px;
    box-shadow: var(--shadow-sm);
    transition: transform 0.3s, box-shadow 0.3s;
    position: relative;
    overflow: hidden;
}
.stat-card:hover {
    transform: translateY(-2px);
    box-shadow: var(--glow);
}
.stat-val {
    font-family: var(--font-sans);
    font-size: 28px;
    font-weight: 900;
    color: var(--text-1);
    line-height: 1;
    letter-spacing: -1px;
}
.stat-label {
    font-size: 9px;
    color: var(--text-3);
    text-transform: uppercase;
    letter-spacing: 0.15em;
    margin-top: 5px;
    font-weight: 700;
}
.stat-note { font-size: 11px; margin-top: 5px; font-weight: 700; }
.note-green { color: var(--success); }
.note-gold  { color: var(--accent); }
.note-blue  { color: var(--info); }
.note-red   { color: var(--error); }
.note-grey  { color: var(--text-3); }

/* ── KPI CARDS ───────────────────────────────────────────────────────────── */
.kpi-row {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
    margin-bottom: 24px;
}
.kpi-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 16px 18px;
    text-align: center;
    box-shadow: var(--shadow-sm);
}
.kpi-val {
    font-family: var(--font-sans);
    font-size: 26px;
    font-weight: 900;
    color: var(--text-1);
    letter-spacing: -0.5px;
}
.kpi-label {
    font-size: 9px;
    color: var(--text-3);
    text-transform: uppercase;
    letter-spacing: 0.15em;
    margin-top: 4px;
    font-weight: 700;
}

/* ── PANELS ──────────────────────────────────────────────────────────────── */
.panel, .launch-box, .camp-selector {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 20px 24px;
    margin-bottom: 16px;
    box-shadow: var(--shadow-sm);
}
.filter-bar {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 16px 20px;
    margin-bottom: 20px;
    box-shadow: var(--shadow-sm);
}

/* ── CARDS ───────────────────────────────────────────────────────────────── */
.card, .camp-card, .task-card, .task-review-card, .list-card, .user-row {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    margin-bottom: 10px;
    box-shadow: var(--shadow-sm);
    transition: box-shadow 0.2s, transform 0.2s;
}
.card, .camp-card { padding: 18px 22px; }
.task-card, .task-review-card { padding: 16px 20px; }
.list-card { padding: 18px 20px; }
.user-row  { padding: 14px 18px; display: flex; align-items: center; justify-content: space-between; }
.card:hover, .camp-card:hover, .task-card:hover {
    box-shadow: var(--glow);
    transform: translateY(-1px);
}

/* Task card priority left-border accent */
.task-card { border-left: 3px solid var(--border); }
.task-card.urgent { border-left-color: var(--error); }
.task-card.normal { border-left-color: var(--accent); }
.task-card.low    { border-left-color: var(--success); }

/* Card text elements */
.card-title, .task-title, .camp-title, .task-review-title {
    font-weight: 700;
    color: var(--text-1);
    font-size: 15px;
    margin-bottom: 3px;
}
.camp-title { font-size: 17px; font-weight: 800; }
.camp-client {
    font-size: 10px;
    color: var(--accent);
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-top: 2px;
    opacity: 0.7;
}
.card-meta, .task-meta, .camp-meta { font-size: 12px; color: var(--text-3); margin-top: 5px; }
.task-meta span { margin-right: 16px; }
.user-name  { font-weight: 700; font-size: 14px; color: var(--text-1); }
.user-email { font-size: 12px; color: var(--text-3); margin-top: 2px; }

/* ── LIST CARDS ──────────────────────────────────────────────────────────── */
.list-card-name {
    font-family: var(--font-sans);
    font-size: 15px;
    font-weight: 800;
    color: var(--text-1);
    margin-bottom: 2px;
    letter-spacing: -0.2px;
}
.list-card-meta { font-size: 11px; color: var(--text-3); margin-bottom: 8px; }
.list-card-desc { font-size: 12px; color: var(--text-2); line-height: 1.5; }

/* ── DETAIL PANEL ────────────────────────────────────────────────────────── */
.detail-panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 24px;
    margin-bottom: 16px;
    box-shadow: var(--shadow-sm);
}
.detail-name {
    font-family: var(--font-sans);
    font-size: 20px;
    font-weight: 800;
    color: var(--text-1);
    margin-bottom: 4px;
    letter-spacing: -0.3px;
}
.detail-sub   { font-size: 13px; color: var(--text-3); margin-bottom: 20px; }
.detail-grid  { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 16px; }
.detail-field {
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 7px;
    padding: 12px 14px;
}
.detail-field-label {
    font-size: 9px;
    color: var(--text-3);
    text-transform: uppercase;
    letter-spacing: 0.15em;
    font-weight: 700;
    margin-bottom: 4px;
}
.detail-field-val { font-size: 13px; color: var(--text-1); font-weight: 500; }

/* ── TABLE ───────────────────────────────────────────────────────────────── */
.tbl {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    overflow: hidden;
    margin-bottom: 20px;
    box-shadow: var(--shadow-sm);
}
.tbl table { width: 100%; border-collapse: collapse; font-size: 12px; }
.tbl th {
    background: var(--surface-2);
    padding: 10px 16px;
    text-align: left;
    font-size: 9px;
    font-weight: 700;
    color: var(--text-3);
    text-transform: uppercase;
    letter-spacing: 0.15em;
    border-bottom: 1px solid var(--border);
}
.tbl td {
    padding: 10px 16px;
    border-bottom: 1px solid var(--border-light);
    color: var(--text-2);
    vertical-align: middle;
    line-height: 1.4;
}
.tbl td.n  { color: var(--text-1); font-weight: 700; font-size: 13px; }
.tbl td.co { color: var(--text-2); font-weight: 500; }
.tbl tr:last-child td { border-bottom: none; }
.tbl tr:hover td { background: var(--accent-bg); }

/* ── BADGES ──────────────────────────────────────────────────────────────── */
.badge, .status-badge, .priority-badge, .status-pill, .role-chip, .chip {
    display: inline-block;
    padding: 3px 9px;
    border-radius: 20px;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    font-family: var(--font-sans) !important;
}
.status-badge { padding: 3px 12px; font-size: 10px; }
.status-pill  { padding: 3px 10px; font-size: 10px; }
.role-chip, .chip { padding: 3px 10px; font-size: 10px; border-radius: 12px; }

/* Lead status */
.b-new      { background: var(--info-bg);    color: var(--info);    border: 1px solid var(--info-border); }
.b-assigned { background: var(--accent-bg);  color: var(--accent);  border: 1px solid var(--accent-border); }
.b-progress { background: var(--purple-bg);  color: var(--purple);  border: 1px solid var(--purple-border); }
.b-enriched { background: var(--success-bg); color: var(--success); border: 1px solid var(--success-border); }
.b-used     { background: var(--surface-2);  color: var(--text-3);  border: 1px solid var(--border); }
.b-archived { background: var(--surface-3);  color: var(--text-3);  border: 1px solid var(--border); }

/* Task / general status */
.status-pending, .s-pending  { background: var(--surface-2); color: var(--text-3); border: 1px solid var(--border); }
.status-in_progress          { background: var(--info-bg);    color: var(--info);    border: 1px solid var(--info-border); }
.status-submitted            { background: rgba(180,83,9,.08);  color: #b45309;      border: 1px solid rgba(180,83,9,.20); }
.status-approved             { background: var(--success-bg); color: var(--success); border: 1px solid var(--success-border); }
.status-rejected             { background: var(--error-bg);   color: var(--error);   border: 1px solid var(--error-border); }

/* Campaign status */
.s-building  { background: var(--info-bg);    color: var(--info);    border: 1px solid var(--info-border); }
.s-active    { background: var(--success-bg); color: var(--success); border: 1px solid var(--success-border); }
.s-paused    { background: rgba(180,83,9,.08);  color: #b45309;      border: 1px solid rgba(180,83,9,.20); }
.s-ready     { background: var(--success-bg); color: var(--success); border: 1px solid var(--success-border); }
.s-completed { background: var(--purple-bg);  color: var(--purple);  border: 1px solid var(--purple-border); }
.s-closed    { background: var(--surface-2);  color: var(--text-3);  border: 1px solid var(--border); }

/* Scraper session status */
.b-run  { background: var(--accent-bg);  color: var(--accent);  border: 1px solid var(--accent-border); }
.b-done { background: var(--success-bg); color: var(--success); border: 1px solid var(--success-border); }
.b-fail { background: var(--error-bg);   color: var(--error);   border: 1px solid var(--error-border); }

/* Priority */
.priority-urgent, .p-urgent { background: var(--error-bg);   color: var(--error);   border: 1px solid var(--error-border); }
.priority-normal, .p-normal { background: var(--accent-bg);  color: var(--accent);  border: 1px solid var(--accent-border); }
.priority-low,    .p-low    { background: var(--success-bg); color: var(--success); border: 1px solid var(--success-border); }

/* Persona */
.p-dm  { background: var(--error-bg);   color: var(--error);   border: 1px solid var(--error-border); }
.p-si  { background: var(--accent-bg);  color: var(--accent);  border: 1px solid var(--accent-border); }
.p-inf { background: var(--info-bg);    color: var(--info);    border: 1px solid var(--info-border); }
.p-ic  { background: var(--success-bg); color: var(--success); border: 1px solid var(--success-border); }
.p-unk { background: var(--surface-2);  color: var(--text-3);  border: 1px solid var(--border); }

/* Role chips */
.chip-super_admin,      .role-super_admin      { background: var(--accent-bg);         color: var(--accent);  border: 1px solid var(--accent-border); }
.chip-org_admin,        .role-org_admin        { background: var(--accent-bg);         color: var(--accent);  border: 1px solid var(--accent-border); }
.chip-manager,          .role-manager          { background: var(--info-bg);           color: var(--info);    border: 1px solid var(--info-border); }
.chip-research_manager, .role-research_manager { background: var(--purple-bg);         color: var(--purple);  border: 1px solid var(--purple-border); }
.chip-campaign_manager, .role-campaign_manager { background: rgba(180,83,9,.08);       color: #b45309;        border: 1px solid rgba(180,83,9,.20); }
.chip-researcher,       .role-researcher       { background: var(--success-bg);        color: var(--success); border: 1px solid var(--success-border); }
.chip-client_admin,     .role-client_admin     { background: var(--error-bg);          color: var(--error);   border: 1px solid var(--error-border); }
.chip-client_user,      .role-client_user      { background: var(--surface-2);         color: var(--text-3);  border: 1px solid var(--border); }

/* CRM contact statuses */
.s-new               { background: var(--surface-2);     color: var(--text-3);  border: 1px solid var(--border); }
.s-contacted         { background: var(--info-bg);       color: var(--info);    border: 1px solid var(--info-border); }
.s-waiting           { background: rgba(180,83,9,.08);   color: #b45309;        border: 1px solid rgba(180,83,9,.20); }
.s-responded         { background: var(--success-bg);    color: var(--success); border: 1px solid var(--success-border); }
.s-interested        { background: var(--accent-bg);     color: var(--accent);  border: 1px solid var(--accent-border); }
.s-meeting_requested { background: rgba(180,83,9,.08);   color: #b45309;        border: 1px solid rgba(180,83,9,.20); }
.s-booked            { background: var(--success-bg);    color: var(--success); border: 1px solid var(--success-border); }
.s-not_interested    { background: var(--error-bg);      color: var(--error);   border: 1px solid var(--error-border); }
.s-no_show           { background: var(--purple-bg);     color: var(--purple);  border: 1px solid var(--purple-border); }

/* ── TIP / ALERT BOXES ───────────────────────────────────────────────────── */
.tip, .alert-warn {
    background: var(--accent-bg);
    border: 1px solid var(--accent-border);
    border-left: 3px solid var(--accent);
    border-radius: var(--radius-sm);
    padding: 12px 16px;
    font-size: 12px;
    color: var(--accent);
    margin: 12px 0;
    line-height: 1.5;
}
.alert-err, .conflict-warn {
    background: var(--error-bg);
    border: 1px solid var(--error-border);
    border-left: 3px solid var(--error);
    border-radius: var(--radius-sm);
    padding: 10px 14px;
    font-size: 12px;
    color: var(--error);
    margin: 8px 0;
}
.alert-ok, .conflict-ok {
    background: var(--success-bg);
    border: 1px solid var(--success-border);
    border-left: 3px solid var(--success);
    border-radius: var(--radius-sm);
    padding: 10px 14px;
    font-size: 12px;
    color: var(--success);
    margin: 8px 0;
}

/* KPI performance indicators */
.kpi-good { color: var(--success) !important; font-weight: 700; }
.kpi-warn { color: #b45309 !important;         font-weight: 700; }
.kpi-bad  { color: var(--error) !important;    font-weight: 700; }

/* ── TERMINAL ────────────────────────────────────────────────────────────── */
.terminal {
    background: var(--surface-2);
    color: var(--text-2);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 16px;
    font-family: 'Courier New', monospace;
    font-size: 11px;
    max-height: 380px;
    overflow-y: auto;
    white-space: pre-wrap;
    line-height: 1.6;
    margin-top: 12px;
}

/* ── BUTTONS ─────────────────────────────────────────────────────────────── */
div.stButton > button {
    border-radius: 8px !important;
    font-family: var(--font-sans) !important;
    font-weight: 700 !important;
    font-size: 12px !important;
    letter-spacing: 0.05em !important;
    transition: all 0.15s !important;
    padding: 8px 18px !important;
    text-transform: uppercase !important;
}
div.stButton > button[kind="primary"] {
    background: var(--accent-strong) !important;
    color: #fff !important;
    border: none !important;
    box-shadow: 0 4px 14px -2px rgba(91, 46, 229, 0.45) !important;
}
div.stButton > button[kind="primary"]:hover {
    background: var(--accent-light) !important;
    box-shadow: 0 6px 22px -2px rgba(91, 46, 229, 0.55) !important;
}
div.stButton > button[kind="secondary"] {
    background: var(--surface) !important;
    color: var(--text-2) !important;
    border: 1px solid var(--border) !important;
}
div.stButton > button[kind="secondary"]:hover {
    border-color: var(--accent) !important;
    color: var(--accent) !important;
    background: var(--accent-bg) !important;
}

/* ── FORM ELEMENTS ───────────────────────────────────────────────────────── */
div.stTextInput input,
div.stTextArea textarea,
div.stNumberInput input {
    border: 1px solid var(--border) !important;
    border-radius: 7px !important;
    background: var(--surface) !important;
    font-size: 13px !important;
    font-family: var(--font-sans) !important;
    color: var(--text-1) !important;
    padding: 10px 14px !important;
}
div.stTextInput input:focus,
div.stTextArea textarea:focus {
    border-color: var(--accent) !important;
    box-shadow: 0 0 0 3px var(--accent-bg) !important;
}
div[data-baseweb="select"] > div {
    border: 1px solid var(--border) !important;
    border-radius: 7px !important;
    background: var(--surface) !important;
    font-size: 13px !important;
    font-family: var(--font-sans) !important;
    color: var(--text-1) !important;
}
div[data-baseweb="select"] [data-value] { color: var(--text-1) !important; }
div[data-baseweb="menu"] { background: var(--surface) !important; border: 1px solid var(--border) !important; box-shadow: var(--shadow-md) !important; }
div[data-baseweb="option"] { background: transparent !important; color: var(--text-1) !important; }
div[data-baseweb="option"]:hover { background: var(--accent-bg) !important; }

/* ── TABS ────────────────────────────────────────────────────────────────── */
button[data-baseweb="tab"] {
    font-family: var(--font-sans) !important;
    font-size: 12px !important;
    font-weight: 600 !important;
    color: var(--text-3) !important;
    text-transform: uppercase !important;
    letter-spacing: 0.05em !important;
}
button[data-baseweb="tab"][aria-selected="true"] {
    color: var(--text-1) !important;
    border-bottom-color: var(--accent) !important;
}

/* ── STREAMLIT NATIVE COMPONENTS ─────────────────────────────────────────── */
div[data-testid="metric-container"] {
    background: var(--surface) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-md) !important;
    box-shadow: var(--shadow-sm) !important;
}
div[data-testid="metric-container"] label { color: var(--text-3) !important; font-size: 11px !important; font-weight: 700 !important; text-transform: uppercase !important; letter-spacing: 0.1em !important; }
div[data-testid="stMetricValue"] { color: var(--text-1) !important; font-weight: 900 !important; }

div[data-testid="stDataFrame"] th { background: var(--surface-2) !important; color: var(--text-3) !important; border-color: var(--border) !important; }
div[data-testid="stDataFrame"] td { color: var(--text-2) !important; border-color: var(--border-light) !important; }

details         { background: var(--surface) !important; border-color: var(--border) !important; border-radius: var(--radius-sm) !important; }
details summary { color: var(--text-1) !important; font-family: var(--font-sans) !important; font-weight: 600 !important; }

div[data-testid="stAlert"] { border-radius: var(--radius-sm) !important; }

/* Progress bars */
div[data-testid="stProgress"] > div > div {
    background: var(--accent) !important;
}

/* File uploader */
div[data-testid="stFileUploadDropzone"] {
    background: var(--surface-2) !important;
    border: 1px dashed var(--border) !important;
    border-radius: var(--radius-md) !important;
    color: var(--text-2) !important;
}
div[data-testid="stFileUploadDropzone"]:hover {
    border-color: var(--accent) !important;
    background: var(--accent-bg) !important;
}

/* ── ORG / PLATFORM CARDS (admin) ────────────────────────────────────────── */
.org-card, .platform-stat {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 18px 20px;
    margin-bottom: 10px;
    box-shadow: var(--shadow-sm);
}
.org-name { font-family: var(--font-sans); font-size: 15px; font-weight: 800; color: var(--text-1); margin-bottom: 3px; letter-spacing: -0.2px; }
.org-meta { font-size: 12px; color: var(--text-3); }

/* ── COST CARDS (estimator) ──────────────────────────────────────────────── */
.cost-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 18px;
    text-align: center;
    box-shadow: var(--shadow-sm);
}
.cost-num {
    font-family: var(--font-sans);
    font-size: 28px;
    font-weight: 900;
    color: var(--text-1);
    letter-spacing: -1px;
}
.cost-num.green { color: var(--success); }
.cost-num.gold  { color: var(--accent); }
.cost-label {
    font-size: 10px;
    color: var(--text-3);
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-top: 4px;
    font-weight: 700;
}
.breakdown-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 0;
    border-bottom: 1px solid var(--border-light);
    font-size: 13px;
    color: var(--text-2);
}
.breakdown-row:last-child { border-bottom: none; }

/* ── CAMPAIGN BANNERS ────────────────────────────────────────────────────── */
.ready-banner {
    background: var(--success-bg);
    border: 1px solid var(--success-border);
    border-radius: var(--radius-sm);
    padding: 14px 18px;
    margin: 10px 0;
    display: flex;
    align-items: center;
    gap: 12px;
    color: var(--success);
}
.not-ready-banner {
    background: var(--accent-bg);
    border: 1px dashed var(--accent-border);
    border-radius: var(--radius-sm);
    padding: 14px 18px;
    margin: 10px 0;
    color: var(--accent);
}
.lead-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 0;
    border-bottom: 1px solid var(--border-light);
    font-size: 13px;
    color: var(--text-2);
}
.lead-row:last-child { border-bottom: none; }

/* ── KPI TABLE (research manager) ────────────────────────────────────────── */
.kpi-table {
    background: var(--surface);
    border-radius: var(--radius-md);
    border: 1px solid var(--border);
    overflow: hidden;
    margin-bottom: 20px;
    box-shadow: var(--shadow-sm);
}
.kpi-table-header {
    background: var(--surface-2);
    color: var(--text-3);
    padding: 10px 16px;
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    display: grid;
    grid-template-columns: 2fr 1fr 1fr 1fr 1fr 1fr 1fr;
    gap: 8px;
}
.kpi-table-row {
    padding: 12px 16px;
    display: grid;
    grid-template-columns: 2fr 1fr 1fr 1fr 1fr 1fr 1fr;
    gap: 8px;
    border-bottom: 1px solid var(--border-light);
    font-size: 13px;
    align-items: center;
    color: var(--text-2);
}
.kpi-table-row:last-child { border-bottom: none; }
.kpi-table-row:hover { background: var(--accent-bg); }

/* ── CRM TABLE (campaign manager) ────────────────────────────────────────── */
.crm-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    background: var(--surface);
    border-radius: var(--radius-md);
    overflow: hidden;
    box-shadow: var(--shadow-sm);
}
.crm-table th {
    background: var(--surface-2);
    color: var(--text-3);
    padding: 10px 14px;
    text-align: left;
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.12em;
}
.crm-table td {
    padding: 10px 14px;
    border-bottom: 1px solid var(--border-light);
    color: var(--text-2);
    vertical-align: middle;
}
.crm-table tr:last-child td { border-bottom: none; }
.crm-table tr:hover td { background: var(--accent-bg); }

/* ── CLIENT CARDS (admin) ────────────────────────────────────────────────── */
.client-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 16px 20px;
    margin-bottom: 10px;
    box-shadow: var(--shadow-sm);
}
.invite-token {
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 8px 12px;
    font-family: 'Courier New', monospace;
    font-size: 11px;
    color: var(--text-3);
    word-break: break-all;
}
</style>
"""


# ── THEME TOKEN SETS ─────────────────────────────────────────────────────────
# Dark is the default (an all-day data tool reads better dark); light is optional.
# Violet accent per the approved design direction.

_DARK_VARS = """
:root {
    --bg:           #131019;
    --surface:      #1c1926;
    --surface-2:    #242030;
    --surface-3:    #2e2a3d;
    --surface-4:    #383247;
    --border:       rgba(240, 238, 247, 0.10);
    --border-light: rgba(240, 238, 247, 0.05);

    --text-1: #f0eef7;
    --text-2: #b4afc9;
    --text-3: #8d89a3;

    --accent:        #8e74f8;
    --accent-light:  #a78bff;
    --accent-strong: #5b2ee5;
    --accent-bg:     rgba(142, 116, 248, 0.14);
    --accent-border: rgba(142, 116, 248, 0.30);

    --success: #34d399; --success-bg: rgba(52,211,153,0.14); --success-border: rgba(52,211,153,0.30);
    --error:   #fb7185; --error-bg:   rgba(251,113,133,0.14); --error-border:  rgba(251,113,133,0.30);
    --warning: #fbbf24; --warning-bg: rgba(251,191,36,0.14);  --warning-border: rgba(251,191,36,0.30);
    --info:    #8e74f8; --info-bg:    rgba(142,116,248,0.12); --info-border:   rgba(142,116,248,0.24);
    --purple:  #8e74f8; --purple-bg:  rgba(142,116,248,0.14); --purple-border: rgba(142,116,248,0.30);

    --shadow-sm: 0 4px 20px -6px rgba(0,0,0,0.55);
    --shadow-md: 0 12px 40px -12px rgba(0,0,0,0.60);
    --glow:      0 10px 40px -8px rgba(91,46,229,0.45);
}
"""

_LIGHT_VARS = """
:root {
    --bg:           #efede8;
    --surface:      #ffffff;
    --surface-2:    #f0eef7;
    --surface-3:    #e7e4ef;
    --surface-4:    #ded9ec;
    --border:       rgba(23, 21, 31, 0.12);
    --border-light: rgba(23, 21, 31, 0.06);

    --text-1: #17151f;
    --text-2: #56526b;
    --text-3: #8d89a3;

    --accent:        #5b2ee5;
    --accent-light:  #8e74f8;
    --accent-strong: #5b2ee5;
    --accent-bg:     rgba(91, 46, 229, 0.08);
    --accent-border: rgba(91, 46, 229, 0.22);

    --success: #0e7c6c; --success-bg: rgba(14,124,108,0.10); --success-border: rgba(14,124,108,0.24);
    --error:   #c0362c; --error-bg:   rgba(192,54,44,0.09);  --error-border:  rgba(192,54,44,0.22);
    --warning: #b45309; --warning-bg: rgba(180,83,9,0.10);   --warning-border: rgba(180,83,9,0.22);
    --info:    #5b2ee5; --info-bg:    rgba(91,46,229,0.06);  --info-border:   rgba(91,46,229,0.18);
    --purple:  #5b2ee5; --purple-bg:  rgba(91,46,229,0.08);  --purple-border: rgba(91,46,229,0.20);

    --shadow-sm: 0 4px 20px -6px rgba(23,21,31,0.08);
    --shadow-md: 0 12px 40px -14px rgba(23,21,31,0.12);
    --glow:      0 10px 30px -6px rgba(91,46,229,0.20);
}
"""


def get_theme() -> str:
    """Current theme from session state — 'dark' (default) or 'light'."""
    try:
        return st.session_state.get("ui_theme", "dark")
    except Exception:
        return "dark"


def inject_shared_css(theme: str = None):
    """
    Inject the master design system CSS for the active theme.
    Call this at the start of every dashboard's render() function.
    """
    theme = theme or get_theme()
    vars_block = _LIGHT_VARS if theme == "light" else _DARK_VARS
    st.markdown(SHARED_CSS.replace("__ROOT_VARS__", vars_block), unsafe_allow_html=True)
