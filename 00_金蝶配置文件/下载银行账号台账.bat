@echo off
chcp 65001 >nul
cd /d "%~dp0"
python download_bank_accounts.py
echo.
pause
