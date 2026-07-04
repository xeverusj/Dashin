@echo off
REM Build the Dashin desktop scraper into a shippable folder.
REM Run from the project root:  deploy\build_scraper.bat
echo ============================================
echo   Building Dashin Scraper (PyInstaller)
echo ============================================

pip install pyinstaller >nul 2>&1
python -m PyInstaller dashin_scraper.spec --noconfirm --distpath dist --workpath build_pyi

echo.
echo Done. Ship the whole folder:  dist\DashinScraper\
echo Client double-clicks:         dist\DashinScraper\DashinScraper.exe
pause
