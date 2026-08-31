@echo off
cd /d "%~dp0"
echo ============================================
echo   Kingdee PO Download - running...
echo ============================================
echo.
python download_po.py
echo.
echo ============================================
echo   Done. You can close this window now.
echo ============================================
pause
