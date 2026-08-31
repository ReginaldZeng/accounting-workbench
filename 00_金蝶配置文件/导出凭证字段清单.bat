@echo off
chcp 65001 >nul
cd /d "%~dp0"
python list_gl_fields.py
echo.
pause
