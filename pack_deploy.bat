@echo off
rem ============================================================
rem [Change Log] Date: 2026-08-05  Author: Claude / c  Version: V2.191
rem One-click deploy package builder: rebuild frontend, then zip the backend
rem into 04_部署包/ with a version_stamp.json inside (server has no .git, the
rem stamp is how the UI shows its version there). Whitelist packing -- secrets
rem (conf.ini), local DB and bank uploads never enter the zip.
rem (ASCII-only messages on purpose: Chinese in .bat breaks on GBK codepage.)
rem ============================================================
chcp 65001 >nul

echo [1/2] Building frontend...
call "%~dp001_Current_Deliverables\app\build_frontend.bat" /auto
if errorlevel 1 (
  echo [X] Frontend build failed, packaging aborted.
  pause
  exit /b 1
)

echo [2/2] Packing...
set "PY=python"
where python >nul 2>nul
if errorlevel 1 set "PY=py"
%PY% "%~dp0pack_deploy.py"
echo.
pause
