@echo off
rem ============================================================
rem [Change Log] Date: 2026-08-05  Author: Claude / c  Version: V2.173
rem Start the Vite dev server (frontend hot-reload) for THIS checkout.
rem
rem You only need this when editing frontend/src and you want changes to appear
rem without rebuilding. For plain testing just run the project-root launcher --
rem the backend already serves the built frontend, no second window needed.
rem
rem Ports follow the same per-checkout rule as the launcher:
rem   backend  = 8000 (main) or 8001..8098 (worktree, derived from its name)
rem   dev page = backend + 1000
rem so the backend of THIS checkout is what the dev page talks to -- no cross-talk.
rem
rem Start the backend first (project-root launcher), then this.
rem (ASCII-only messages on purpose: Chinese text inside .bat can break on
rem  zh-CN consoles / GBK codepage. Do not add non-ASCII here.)
rem ============================================================
chcp 65001 >nul

rem worktree root is two levels up from app/
for %%i in ("%~dp0..\..") do set "SELF=%%~fi"
set "WTNAME="
echo %SELF%| find /i "\.claude\worktrees\" >nul
if not errorlevel 1 for %%i in ("%SELF%") do set "WTNAME=%%~nxi"
set "FW_PORT=8000"
set "TAG=main"
if defined WTNAME set "TAG=%WTNAME%"
if defined WTNAME for /f %%p in ('powershell -NoProfile -Command "$n='%WTNAME%';$s=0;foreach($c in $n.ToCharArray()){$s+=[int]$c};8001+($s %% 98)"') do set "FW_PORT=%%p"

set /a DEVPORT=%FW_PORT%+1000

cd /d "%~dp0frontend"

echo ============================================
echo   Finance Workbench - frontend dev server
echo   checkout    : %TAG%
echo   backend API : http://localhost:%FW_PORT%    (start it first!)
echo   dev page    : http://localhost:%DEVPORT%
echo ============================================

where npm >nul 2>nul
if errorlevel 1 (
  echo [X] npm not found. Install Node.js LTS first: https://nodejs.org/
  pause
  exit /b 1
)

if not exist "node_modules" (
  echo Installing frontend dependencies, first run may take a few minutes...
  call npm install
  if errorlevel 1 (
    echo [X] npm install failed. Check your network / registry, then run again.
    pause
    exit /b 1
  )
)

echo.
echo Keep this window open. Close it to stop the dev server.
call npm run dev

echo.
echo Dev server stopped.
pause
