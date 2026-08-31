@echo off
chcp 65001 >nul
cd /d "%~dp0"
python download_gl_bank.py
echo.
pause
