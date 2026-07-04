# Packaging the Dashin Desktop Scraper

Turns `dashin_scraper.py` into a folder a non-technical client can double-click —
no Python, no pip. It signs them in (paywall gate), then lets them run the
scrapers/enricher; results push to their dashboard.

## Build it
From the project root, on a **Windows** machine (PyInstaller builds per-OS):
```
deploy\build_scraper.bat
```
or directly:
```
pyinstaller dashin_scraper.spec --noconfirm
```
Output: **`dist\DashinScraper\`** — the whole folder is the app. The client runs
`DashinScraper.exe` inside it.

## What the client experiences
1. Double-click `DashinScraper.exe`.
2. **Sign in** — first run asks for the Dashin app URL and their access token
   (you generate the token in the dashboard: Admin → Org Settings → Desktop
   Scraper Tokens). Saved to `~/.dashin/config.json` for next time.
   - If their account is unpaid/suspended/deactivated, it **refuses to start**.
3. **First run only** downloads Chromium (~150 MB, one time).
4. Pick a tool → a browser window opens → they log into the target site / click
   START → results save locally **and** flow into their dashboard.

## Distribution
- Zip `dist\DashinScraper\` and send it, or make an installer with **Inno Setup**
  (point it at the folder, add a Start-menu shortcut to `DashinScraper.exe`).
- It's per-OS: build on Windows for Windows clients, on macOS for Mac clients.

## Known caveats (be aware)
- **Antivirus / SmartScreen** may flag an unsigned PyInstaller exe. For real
  distribution, sign it with a code-signing certificate (~$100/yr) — that removes
  the warning and is worth it for a paid product.
- **First-run browser download** needs internet. To avoid it, you can pre-bundle
  Chromium (bigger package) — ask and we can switch the spec to bundle it.
- The exe is a **console app** (shows a terminal with the sign-in + menu). A
  windowed GUI is a later polish step if you want it.
- Rebuild whenever the scrapers or `services/`/`core/` change — the scripts are
  baked into the package.

## Security notes
- The paywall is enforced **server-side too**: even a tampered client can't push
  data to the dashboard once the account lapses (the ingest API returns 403).
- The client's token lives in `~/.dashin/config.json` in plain text — fine for
  this model (it only grants push access to their own org and is revocable).
