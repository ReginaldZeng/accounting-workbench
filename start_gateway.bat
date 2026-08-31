@echo off
rem ============================================================
rem AI Gateway launcher (V2.302). Independent process on port 8020.
rem Config: 01_Current_Deliverables\app\backend\.env  (see .env.example)
rem (ASCII-only on purpose: Chinese in .bat breaks on GBK consoles.)
rem ============================================================
cd /d "%~dp0\01_Current_Deliverables\app\backend"
echo Starting AI Gateway on http://localhost:8020 ...
py -m uvicorn gateway:app --port 8020
pause