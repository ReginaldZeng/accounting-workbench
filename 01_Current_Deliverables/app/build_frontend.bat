@echo off
rem ============================================================
rem [Change Log] Date: 2026-08-05  Author: Claude / c  Version: V2.171
rem Build the React frontend into backend/static/ (one click).
rem
rem Why this exists: backend/static/ is a BUILD ARTIFACT and is no longer
rem committed to git (V2.171). Vite names bundles by content hash, so every
rem parallel branch produced a different assets/index-<hash>.js and the merge
rem of static/index.html conflicted every single time. Source is in git, so
rem the build is reproducible any time -- run this after cloning/pulling, or
rem after changing anything under frontend/src/.
rem
rem Usage:  build_frontend.bat          (interactive, pauses at the end)
rem         build_frontend.bat /auto    (called by the launcher, no pause)
rem
rem File name is ASCII on purpose, and so is every message below: non-ASCII
rem text inside .bat can break on zh-CN consoles / GBK codepage.
rem ============================================================
chcp 65001 >nul
setlocal
set "AUTO="
if /I "%~1"=="/auto" set "AUTO=1"

cd /d "%~dp0frontend"

echo ============================================
echo   Finance Workbench - build frontend
echo ============================================

where npm >nul 2>nul
if errorlevel 1 goto :no_npm

if not exist "node_modules" (
  echo Installing frontend dependencies, first run may take a few minutes...
  call npm install
  if errorlevel 1 goto :fail_install
)

echo Building...
call npm run build
if errorlevel 1 goto :fail_build

if not exist "..\backend\static\index.html" goto :fail_missing

echo.
echo [OK] Frontend built into backend\static\
if not defined AUTO pause
exit /b 0

:no_npm
echo [X] npm not found. Install Node.js LTS first: https://nodejs.org/
echo     Then run this file again.
if not defined AUTO pause
exit /b 1

:fail_install
echo [X] npm install failed. Check your network / registry, then run again.
if not defined AUTO pause
exit /b 1

:fail_build
echo [X] Build failed. Fix the errors above, then run again.
if not defined AUTO pause
exit /b 1

:fail_missing
echo [X] Build reported success but backend\static\index.html is missing.
echo     Check build.outDir in frontend\vite.config.js
if not defined AUTO pause
exit /b 1
