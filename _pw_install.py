"""
_pw_install.py — installs the Chromium browser Playwright needs.

Run standalone (`python _pw_install.py`) or, in the frozen app, via the launcher's
dispatch (`dashin_scraper.exe --run-tool _pw_install.py`). Kept tiny so it can be
bundled and invoked without a system Python.
"""
import sys

try:
    from playwright.__main__ import main as _pw_main
    sys.argv = ["playwright", "install", "chromium"]
    _pw_main()
except SystemExit:
    pass
except Exception as e:
    print(f"[pw-install] failed: {e}")
    print("Manually run:  playwright install chromium")
