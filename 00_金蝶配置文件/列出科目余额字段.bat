@echo off
cd /d "%~dp0"
python list_balance_fields.py
if errorlevel 1 py list_balance_fields.py
pause
