@echo off
rem ============================================================
rem [Change Log] Date: 2026-07-03  Author: Claude / c  Version: V1.0
rem Finance Workbench one-click launcher (project root).
rem Points to the approved current app: 01_Current_Deliverables\app\backend
rem Double-click to run. No need to enter version folders.
rem 2026-07-03: browser opens by polling port 8000 (opens when ready), not a fixed 6s sleep.
rem (ASCII-only messages on purpose: Chinese text inside .bat can break
rem  on zh-CN consoles / GBK codepage. Do not add non-ASCII here.)
rem ============================================================
chcp 65001 >nul

rem ============================================================
rem V2.173: one port per checkout, so parallel worktrees can run side by side.
rem   main repo            -> 8000 (unchanged, keeps existing bookmarks working)
rem   .claude\worktrees\X  -> 8001..8098, derived from X's name (stable across runs)
rem Before this, every checkout hardcoded 8000 AND killed whoever held it, so
rem launching a second worktree silently shot down the first one.
rem ============================================================
for %%i in ("%~dp0.") do set "SELF=%%~fi"
set "WTNAME="
echo %SELF%| find /i "\.claude\worktrees\" >nul
if not errorlevel 1 for %%i in ("%SELF%") do set "WTNAME=%%~nxi"
set "PORT=8000"
set "TAG=main"
if defined WTNAME set "TAG=%WTNAME%"
if defined WTNAME for /f %%p in ('powershell -NoProfile -Command "$n='%WTNAME%';$s=0;foreach($c in $n.ToCharArray()){$s+=[int]$c};8001+($s %% 98)"') do set "PORT=%%p"

cd /d "%~dp001_Current_Deliverables\app\backend"

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
%PY% -c "import fastapi, uvicorn, watchfiles" >nul 2>nul
if errorlevel 1 (
  echo Installing dependencies, first run may take a while...
  %PY% -m pip install -r requirements.txt
)
%PY% -c "import fastapi, uvicorn, watchfiles" >nul 2>nul
if errorlevel 1 (
  echo [X] Dependencies still missing. Check your internet, then run again.
  echo     Or install manually:  %PY% -m pip install fastapi "uvicorn[standard]"
  pause
  exit /b 1
)

rem --- ensure the frontend build artifact is present AND up to date ---
rem V2.171 took static/ out of git; V2.174 added the freshness check.
rem Why freshness matters: while static/ was tracked, `git checkout` swapped the built
rem frontend along with the source. Now it does not -- so a checkout whose static/ was
rem built before the latest frontend edit would silently serve the OLD screen, and you
rem would swear "my change isn't there". Rebuild when any frontend source is newer.
for /f %%s in ('powershell -NoProfile -Command "$b=Get-Item '.\static\index.html' -EA SilentlyContinue; if(-not $b){'STALE';exit}; $n=Get-ChildItem '..\frontend\src','..\frontend\index.html','..\frontend\vite.config.js','..\frontend\package.json' -Recurse -File -EA SilentlyContinue ^| Sort-Object LastWriteTime -Desc ^| Select-Object -First 1; if($n -and $n.LastWriteTime -gt $b.LastWriteTime){'STALE'}else{'FRESH'}"') do set "BUILDSTATE=%%s"
if not "%BUILDSTATE%"=="FRESH" (
  echo.
  echo [!] Frontend build is missing or older than the source. Rebuilding...
  call "%~dp001_Current_Deliverables\app\build_frontend.bat" /auto
)
if not exist "static\index.html" (
  echo [X] Frontend not built. The backend will still start, but only /api/* will work.
  echo     Fix the errors above, or run: 01_Current_Deliverables\app\build_frontend.bat
  pause
)

rem --- free THIS checkout's port from a leftover/old server of the SAME checkout ---
rem Only touches %PORT%, never another worktree's port, so parallel apps stay up.
echo Releasing port %PORT% if an old server of this checkout is still running...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT% " ^| findstr LISTENING') do (
  echo   killing old server PID %%p
  taskkill /F /PID %%p >nul 2>nul
)
ping -n 2 127.0.0.1 >nul

echo.
echo Starting server... the page opens automatically once the server is ready (usually 1-2s).
echo Keep this window open. Close it to stop the server.
echo Auto-reload is ON: backend code updates apply automatically, no manual restart.

rem --- open browser as soon as the port is actually listening (poll, not a fixed wait) ---
rem FW_NO_BROWSER=1 suppresses it (the start-all launcher sets it to avoid a tab storm).
if not "%FW_NO_BROWSER%"=="1" start "" powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 60;$i++){try{$c=New-Object Net.Sockets.TcpClient;$c.Connect('localhost',%PORT%);$c.Close();Start-Process 'http://localhost:%PORT%/';break}catch{Start-Sleep -Milliseconds 300}}"

rem --reload: hot-reload backend when code files change (no manual restart needed)
%PY% -m uvicorn app:app --port %PORT% --reload

echo.
echo Server stopped.
pause
