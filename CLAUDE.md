# CLAUDE.md — Dashin Research Platform

## Project Overview

**Dashin Research** is a multi-tenant SaaS research operations platform built with Python and Streamlit. It manages the full lifecycle of B2B lead research: web scraping → data cleaning → inventory management → research queue → campaign pipeline → client portal.

The application source lives inside `app to test with Jan/` (a zip archive in the repo root). Unzip it to start working:

```bash
unzip "app to test with Jan.zip"
cd "app to test with Jan"
```

---

## Technology Stack

| Layer | Technology |
|---|---|
| UI Framework | Streamlit >= 1.32.0 |
| Database | SQLite 3 (WAL mode, foreign keys enabled) |
| AI / LLM | Anthropic Claude (Sonnet) via `anthropic` SDK |
| Web Scraping | Playwright (sync API) + playwright-stealth |
| Data | pandas, openpyxl, CSV |
| Translation | DeepL API (optional, in cleaner.py) |
| Image handling | Pillow |
| HTTP | requests |
| Environment | python-dotenv |
| Python | 3.14+ (cpython-314 bytecode in __pycache__) |

---

## Directory Structure

```
app to test with Jan/
├── app.py                        # Streamlit entry point — routing, auth, sidebar
├── worker.py                     # Universal AI-powered event website scraper
├── clutch_scraper.py             # Dedicated Clutch.co company scraper
├── cleaner.py                    # CSV data cleaner (translations, normalization)
├── check_and_fix.py              # Data validation and fix utility
├── migrate.py                    # Database schema migration script
├── setup.py                      # One-time project bootstrap script
├── requirements.txt              # Python dependencies
├── .env                          # API keys (never commit — see Security below)
│
├── core/
│   ├── __init__.py
│   ├── db.py                     # SQLite schema, get_connection(), init_db(), migrate_db()
│   ├── auth.py                   # Login, session management, RBAC helpers
│   └── ai_tracker.py             # Anthropic API usage tracking and budget enforcement
│
├── services/
│   ├── __init__.py
│   ├── lead_service.py           # Lead CRUD, filtering, bulk ops
│   ├── flag_service.py           # Lead flag/annotation system
│   ├── invite_service.py         # Client invite token generation + redemption
│   ├── learning_service.py       # AI learning / feedback loop
│   ├── notification_service.py   # In-app notification system
│   ├── report_service.py         # Report generation
│   └── task_service.py           # Task assignment and tracking
│
├── dashboards/
│   ├── __init__.py
│   ├── superadmin_dashboard.py   # Platform-level admin (super_admin only)
│   ├── admin_dashboard.py        # Org-level user/client management
│   ├── scraper_dashboard.py      # Smart scraper UI
│   ├── inventory_dashboard.py    # Lead inventory browser
│   ├── research_dashboard.py     # Research queue
│   ├── research_manager_dashboard.py
│   ├── campaigns_dashboard.py    # Campaign pipeline
│   ├── campaign_manager_dashboard.py
│   ├── estimator_dashboard.py    # Cost/time estimator
│   └── client_dashboard.py       # Client portal (completely separate UX)
│
└── data/
    └── system/
        ├── dashin.db             # SQLite database (do not edit manually)
        ├── companies_master.csv  # Master company list
        ├── leads_master.csv      # Master leads list
        ├── layout_patterns.json  # Scraper selector patterns
        └── sessions/             # Per-scrape-session CSV exports
```

---

## Running the Application

### Initial Setup (first time only)
```bash
pip install -r requirements.txt
playwright install chromium
python setup.py          # Creates folders and stub files
```

### Start the App
```bash
streamlit run app.py
```

Default login: `admin@dashin.com` / `admin123`

### Standalone Scrapers (CLI tools)
```bash
# Universal event scraper (uses Claude Vision — requires ANTHROPIC_API_KEY)
python worker.py <event_url>

# Clutch.co company scraper
python clutch_scraper.py "https://clutch.co/de/web-designers/berlin"
python clutch_scraper.py "https://clutch.co/agencies/seo" --pages 5

# Data cleaner (reads from data_system/, writes cleaned output)
python cleaner.py

# Database migration
python migrate.py

# Data integrity check and auto-fix
python check_and_fix.py
```

---

## Architecture: Key Concepts

### Role-Based Access Control (RBAC)

Roles are hierarchical. Each dashboard checks `user["role"]` before rendering:

```
super_admin (100)        — Platform admin, all orgs
  org_admin (80)         — Manages one org, all its clients
    manager (60)         — Full internal access
      research_manager (50)   — Research queue + inventory
      campaign_manager (45)   — Campaign pipeline
        researcher (30)       — Own leads, research queue
          client_admin (20)   — Client portal (full)
            client_user (10)  — Client portal (view only)
```

Auth helpers are in `core/auth.py`: `has_role()`, `is_internal()`, `is_client()`, `can_manage_users()`, etc. Always use these functions — never hardcode role strings in business logic.

### Multi-Tenancy

- Every record in the DB is scoped to an `org_id`
- `super_admin` bypasses all org isolation checks
- `same_org(user, org_id)` in `core/auth.py` enforces org boundaries
- Client users are further scoped to `client_id`

### Database

- File: `data/system/dashin.db`
- Always use `get_connection()` from `core/db.py` — never open SQLite directly
- Connections return rows as plain dicts (custom `_dict_factory`)
- WAL mode is enabled; foreign keys are enforced
- Always call `conn.close()` after use — connections are not pooled

```python
from core.db import get_connection

conn = get_connection()
rows = conn.execute("SELECT * FROM leads WHERE org_id=?", (org_id,)).fetchall()
conn.close()
```

Schema tables (see `core/db.py` for full DDL):
- `organisations` — multi-tenant root
- `org_ai_usage` — per-org AI budget tracking
- `platform_ai_log` — every API call logged
- `clients` — client companies per org
- `users` — all users with role + org_id
- `invite_tokens` — client self-service signup
- `notifications` — in-app notification inbox
- `leads` — the core lead inventory

### AI Cost Tracking

Every Anthropic API call MUST be logged via `core/ai_tracker.py`. Before making a call, check if the org is within budget:

```python
from core.ai_tracker import can_use_ai, log_usage

if not can_use_ai(org_id):
    st.error("AI budget exceeded for this billing period.")
    return

# ... make the API call, capture usage ...
log_usage(org_id, user_id, feature="scraper", model="claude-sonnet-4-5",
          tokens_input=..., tokens_output=...)
```

Pricing constants (update if Anthropic changes pricing):
- Input: $3.00 / 1M tokens
- Output: $15.00 / 1M tokens

### Scraper Architecture (worker.py)

The universal scraper uses **Claude Vision** to dynamically identify HTML selectors:
1. Playwright navigates to the event URL with stealth mode
2. Screenshots are taken of the page
3. Claude Vision analyzes the screenshot to identify attendee card CSS selectors
4. Playwright extracts data using those selectors
5. Data is saved to `data/system/sessions/` as CSV and optionally into the DB

Human-like behavior is baked in: `_human_delay()` and `_human_scroll()` add randomized timing/scrolling between actions.

### Dashboard Convention

Every dashboard module must expose a single `render(user: dict)` function:

```python
# dashboards/my_dashboard.py
import streamlit as st

def render(user: dict):
    role = user["role"]
    org_id = user["org_id"]
    # ... dashboard code
```

`app.py` imports and calls `render(user)` via the router. Dashboard errors are caught and displayed with an expandable traceback — never let dashboard exceptions crash the whole app.

---

## Data Flow

```
Web Sources (event sites, Clutch.co)
    │
    ▼
worker.py / clutch_scraper.py  ──→  data/system/sessions/*.csv
    │
    ▼
cleaner.py  ──→  Normalized CSV
    │
    ▼
Scraper Dashboard (import UI)  ──→  leads table (SQLite)
    │
    ▼
Inventory Dashboard  ──→  Research Queue  ──→  Research Manager
    │
    ▼
Campaigns Dashboard  ──→  Campaign Manager  ──→  Estimator
    │
    ▼
Client Dashboard (read-only view for clients)
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in values (if `.env.example` does not exist, create `.env` manually):

```bash
ANTHROPIC_API_KEY=sk-ant-...        # Required — Claude API
DEEPL_API_KEY=...                   # Optional — translation in cleaner.py
SUPER_ADMIN_EMAIL=admin@dashin.com  # Alert recipient for AI budget warnings
SMTP_HOST=                          # Optional — for email notifications
SMTP_PORT=587
SMTP_USER=
SMTP_PASS=
```

---

## Security Notes

- **Never commit `.env`** — it contains API keys. The `.env` in the zip archive should be treated as compromised; rotate the `ANTHROPIC_API_KEY` immediately.
- Passwords are hashed with SHA-256 (`hashlib.sha256`). This is adequate for internal tooling but not production-grade — consider bcrypt for future upgrades.
- SQL queries use parameterized statements (`?` placeholders) throughout — no string interpolation in queries.
- Role checks must happen server-side (in `route()` in `app.py` and within each dashboard), not just in the UI.
- The `same_org()` function must be used whenever querying cross-org data.

---

## Development Conventions

### Adding a New Dashboard

1. Create `dashboards/my_dashboard.py` with a `render(user: dict)` function
2. Add a nav entry to `NAV_ITEMS` in `app.py`:
   ```python
   ("🆕  My Page", "my_page", {"super_admin", "org_admin"}),
   ```
3. Add a route handler in `route()` in `app.py`:
   ```python
   elif page == "my_page":
       if role not in ("super_admin", "org_admin"):
           _access_denied(); return
       from dashboards.my_dashboard import render
       render(user)
   ```

### Adding a New Service

Create `services/my_service.py`. Services are plain Python modules — no class required. They receive `org_id` and/or `user` as parameters and call `get_connection()` internally.

### Adding a New DB Table

Add a `CREATE TABLE IF NOT EXISTS` statement in `init_db()` in `core/db.py`. For schema changes to existing tables, add a migration step in `migrate_db()` using `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` pattern (check `migrate.py` for examples).

### Streamlit State

- User session: `st.session_state["user"]` — dict with `id`, `name`, `email`, `role`, `org_id`, `client_id`, etc.
- Current page: `st.session_state["page"]`
- Org id shortcut: `st.session_state["org_id"]`
- Use `st.rerun()` after state mutations

### Code Style

- No test suite exists — validate changes manually via the UI
- Python 3.14 features are acceptable
- Streamlit UI: use `unsafe_allow_html=True` for custom CSS blocks (established pattern)
- Maintain the design system: dark sidebar (`#111111`), cream content area (`#F7F6F3`), gold accent (`#C9A96E`)
- Dashboard imports are lazy (inside `route()`/`render()`) to avoid circular imports

---

## Known Structure Notes

- `data_system/` (underscore) is a legacy directory from V1; the active data directory is `data/system/` (slash)
- `__pycache__/` directories with `.cpython-314.pyc` files are present in the zip — these can be deleted and will regenerate
- `worker.py` appears in both the root and `data_system/` — the root version is the active one
- The `data/system/layout_patterns.json` stores learned CSS selector patterns from the scraper

---

## Git Branch

Active development branch: `claude/claude-md-mm49z4kwakdhcrlg-1ZYNQ`

Always push to this branch:
```bash
git push -u origin claude/claude-md-mm49z4kwakdhcrlg-1ZYNQ
```
