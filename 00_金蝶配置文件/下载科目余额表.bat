@echo off
chcp 65001 >nul
cd /d "%~dp0"
python download_gl_balance.py
echo.
pause
