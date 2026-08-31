@echo off
chcp 65001 >nul
cd /d "%~dp0"
python download_gl_licai.py
echo.
pause
