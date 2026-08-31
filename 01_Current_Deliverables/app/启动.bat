@echo off
rem ============================================================
rem Secondary launcher (inside app/). The project-root launcher is the one to
rem use day to day; this one exists for running the app folder standalone.
rem V2.173: same per-checkout port rule as the root launcher -- see there for why.
rem (ASCII-only messages on purpose: Chinese text inside .bat can break on
rem  zh-CN consoles / GBK codepage. Do not add non-ASCII here.)
rem ============================================================
chcp 65001 >nul

rem worktree root is two levels up from app/
for %%i in ("%~dp0..\..") do set "SELF=%%~fi"
set "WTNAME="
echo %SELF%| find /i "\.claude\worktrees\" >nul
if not errorlevel 1 for %%i in ("%SELF%") do set "WTNAME=%%~nxi"
set "PORT=8000"
set "TAG=main"
if defined WTNAME set "TAG=%WTNAME%"
if defined WTNAME for /f %%p in ('powershell -NoProfile -Command "$n='%WTNAME%';$s=0;foreach($c in $n.ToCharArray()){$s+=[int]$c};8001+($s %% 98)"') do set "PORT=%%p"

cd /d "%~dp0backend"

echo ============================================
echo   Finance Workbench - local app
echo   checkout : %TAG%
echo   port     : %PORT%    ->  http://localhost:%PORT%/
echo ============================================

rem --- pick python launcher (python or py) ---
set "PY=python"
where python >nul 2>nul
if errorlevel 1 set "PY=py"
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo [X] No Python found. Please install Python 3 first.
  pause
  exit /b 1
)

rem --- ensure deps (install only if missing) ---
%PY% -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
  echo Installing dependencies, first run may take a while...
  %PY% -m pip install -r requirements.txt
)
%PY% -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
  echo [X] Dependencies still missing. Check your internet, then run again.
  echo     Or install manually:  %PY% -m pip install fastapi "uvicorn[standard]"
  pause
  exit /b 1
)

rem --- ensure the frontend build artifact is present AND up to date (V2.174) ---
rem See the project-root launcher for why freshness is checked, not just existence.
for /f %%s in ('powershell -NoProfile -Command "$b=Get-Item '.\static\index.html' -EA SilentlyContinue; if(-not $b){'STALE';exit}; $n=Get-ChildItem '..\frontend\src','..\frontend\index.html','..\frontend\vite.config.js','..\frontend\package.json' -Recurse -File -EA SilentlyContinue ^| Sort-Object LastWriteTime -Desc ^| Select-Object -First 1; if($n -and $n.LastWriteTime -gt $b.LastWriteTime){'STALE'}else{'FRESH'}"') do set "BUILDSTATE=%%s"
if not "%BUILDSTATE%"=="FRESH" (
  echo.
  echo [!] Frontend build is missing or older than the source. Rebuilding...
  call "%~dp0build_frontend.bat" /auto
)
if not exist "static\index.html" (
  echo [X] Frontend not built. The backend will still start, but only /api/* will work.
  echo     Fix the errors above, or run: build_frontend.bat
  pause
)

rem --- free THIS checkout's port only (fixes error 10048 / stale Not Found) ---
echo Releasing port %PORT% if an old server of this checkout is still running...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT% " ^| findstr LISTENING') do (
  echo   killing old server PID %%p
  taskkill /F /PID %%p >nul 2>nul
)
rem small wait for the port to be released
ping -n 2 127.0.0.1 >nul

echo.
echo Starting server... the browser will open in ~6s at http://localhost:%PORT%
echo Keep this window open. Close it to stop the server.

if not "%FW_NO_BROWSER%"=="1" start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 6; Start-Process 'http://localhost:%PORT%/'"

%PY% -m uvicorn app:app --port %PORT%

echo.
echo Server stopped.
pause
