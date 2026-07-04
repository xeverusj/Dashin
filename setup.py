"""
setup.py — Dashin Research
Run this ONCE to create all folders and files needed.
It will NOT touch or overwrite worker.py, cleaner.py, data/, data_system/, or .env

Run with: python setup.py
"""

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).parent

# ── COLOURS FOR TERMINAL ──────────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def log_create(path):  print(f"  {GREEN}✓ Created{RESET}  {path}")
def log_skip(path):    print(f"  {YELLOW}→ Skipped{RESET}  {path}  (already exists)")
def log_info(msg):     print(f"  {BLUE}ℹ {RESET} {msg}")

def make_folder(path: Path):
    if path.exists():
        log_skip(path.relative_to(ROOT))
    else:
        path.mkdir(parents=True, exist_ok=True)
        log_create(path.relative_to(ROOT))

def make_file(path: Path, content: str = ""):
    if path.exists():
        log_skip(path.relative_to(ROOT))
    else:
        path.write_text(content, encoding="utf-8")
        log_create(path.relative_to(ROOT))

def safe_replace_file(path: Path, content: str):
    """Overwrite only if the file doesn't exist OR is empty."""
    if path.exists() and path.stat().st_size > 10:
        log_skip(path.relative_to(ROOT))
    else:
        path.write_text(content, encoding="utf-8")
        log_create(path.relative_to(ROOT))


print(f"\n{BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")
print(f"{BOLD}  Dashin Research — Project Setup{RESET}")
print(f"{BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}\n")

# ── STEP 1: PYTHON PACKAGE FOLDERS ───────────────────────────────────────────
print(f"{BOLD}[1/4] Creating package folders...{RESET}")
folders = [
    ROOT / "core",
    ROOT / "services",
    ROOT / "dashboards",
    ROOT / "data" / "system" / "sessions",   # DB + session CSVs live here
    ROOT / "data_system",                     # keep existing folder
]
for f in folders:
    make_folder(f)

# ── STEP 2: __init__.py FILES ─────────────────────────────────────────────────
print(f"\n{BOLD}[2/4] Creating __init__.py files...{RESET}")
init_files = [
    ROOT / "core"       / "__init__.py",
    ROOT / "services"   / "__init__.py",
    ROOT / "dashboards" / "__init__.py",
]
for f in init_files:
    make_file(f, "")

# ── STEP 3: PLACEHOLDER DASHBOARD FILES ──────────────────────────────────────
# These are stubs so app.py doesn't crash while dashboards are being built.
print(f"\n{BOLD}[3/4] Creating placeholder dashboard stubs...{RESET}")

stubs = {
    ROOT / "dashboards" / "research_dashboard.py": '''\
"""Research Queue dashboard — coming soon."""
import streamlit as st
def render(user):
    st.markdown("## 🔬 Research Queue")
    st.info("This dashboard is being built. Check back soon.")
''',
    ROOT / "dashboards" / "estimator_dashboard.py": '''\
"""Estimator dashboard — coming soon."""
import streamlit as st
def render(user):
    st.markdown("## 📊 Estimator")
    st.info("This dashboard is being built. Check back soon.")
''',
    ROOT / "dashboards" / "campaigns_dashboard.py": '''\
"""Campaigns dashboard — coming soon."""
import streamlit as st
def render(user):
    st.markdown("## 🚀 Campaigns")
    st.info("This dashboard is being built. Check back soon.")
''',
    ROOT / "dashboards" / "admin_dashboard.py": '''\
"""Admin dashboard — coming soon."""
import streamlit as st
def render(user):
    st.markdown("## ⚙️ Users & Clients")
    st.info("This dashboard is being built. Check back soon.")
''',
}
for path, content in stubs.items():
    make_file(path, content)

# ── STEP 4: REQUIREMENTS FILE ─────────────────────────────────────────────────
print(f"\n{BOLD}[4/4] Creating requirements.txt...{RESET}")
make_file(ROOT / "requirements.txt", """\
streamlit>=1.32.0
playwright>=1.40.0
pandas>=2.0.0
anthropic>=0.20.0
python-dotenv>=1.0.0
""")

# ── SUMMARY ───────────────────────────────────────────────────────────────────
print(f"""
{BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}
{GREEN}{BOLD}  ✅ Setup complete!{RESET}

{BOLD}  Your folder structure is now:{RESET}

  dashin/
  ├── app.py
  ├── worker.py           ← untouched
  ├── cleaner.py          ← untouched
  ├── setup.py            ← this file
  ├── requirements.txt
  ├── .env                ← untouched
  ├── core/
  │   ├── __init__.py
  │   └── db.py           ← paste from Claude
  ├── services/
  │   ├── __init__.py
  │   └── lead_service.py ← paste from Claude
  ├── dashboards/
  │   ├── __init__.py
  │   ├── scraper_dashboard.py   ← paste from Claude
  │   ├── inventory_dashboard.py ← paste from Claude
  │   ├── research_dashboard.py  (stub — will be replaced)
  │   ├── estimator_dashboard.py (stub — will be replaced)
  │   ├── campaigns_dashboard.py (stub — will be replaced)
  │   └── admin_dashboard.py     (stub — will be replaced)
  ├── data/
  │   └── system/
  │       └── sessions/
  └── data_system/        ← untouched

{BOLD}  Next steps:{RESET}

  1. Place the files from Claude into core/, services/, dashboards/
  2. Run:  pip install -r requirements.txt
  3. Run:  streamlit run app.py
  4. Login: admin@dashin.com / admin123

{BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}
""")
