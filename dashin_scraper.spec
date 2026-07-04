# PyInstaller spec for the Dashin desktop scraper (onedir, console app).
#
# Tricky bit: the launcher runs worker.py / run_enricher.py / etc. via runpy, so
# PyInstaller's import analysis never sees THOSE scripts' dependencies. We must
# (a) ship the scripts themselves as data files (runpy reads the .py), and
# (b) force-include every package they import as hidden imports.
#
# Build:   pyinstaller dashin_scraper.spec --noconfirm
# Output:  dist/DashinScraper/DashinScraper.exe  (ship the whole folder)

import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []

# Heavy 3rd-party packages the runpy'd scrapers rely on. collect_all grabs their
# data/binaries too (e.g. Playwright's node driver).
for pkg in ("playwright", "playwright_stealth", "curl_cffi", "bs4",
            "reportlab", "pandas", "lxml", "cssselect", "openpyxl"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception:
        pass

# Local packages — freeze every submodule so runpy targets can import them.
hiddenimports += collect_submodules("core")
hiddenimports += collect_submodules("services")
hiddenimports += ["requests", "dotenv"]

# The runpy target scripts, shipped as data so run_path can read them.
_scripts = [
    "worker.py", "clutch_scraper.py", "run_enricher.py", "_pw_install.py",
    "crawler_v2.py", "crawler_microbiome.py", "score_csv.py",
    "scrape_ensun.py", "scrape_biotech_careers.py", "scrape_hlth.py",
    "scrape_healthtech.py", "scrape_b2match_gitex.py",
]
datas += [(s, ".") for s in _scripts if os.path.exists(s)]
# Learned scraper patterns (worker.py Tier-2 cache), if present.
if os.path.exists(os.path.join("data", "system", "layout_patterns.json")):
    datas += [(os.path.join("data", "system", "layout_patterns.json"),
               os.path.join("data", "system"))]

a = Analysis(
    ["dashin_scraper.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["streamlit", "tkinter"],   # dashboard-only / unused → smaller build
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="DashinScraper",
    console=True,          # it's an interactive CLI (sign-in + menu)
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name="DashinScraper",
)
