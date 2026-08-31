@echo off
rem ============================================================
rem [Change Log] Date: 2026-08-05  Author: Claude / c  Version: V2.175
rem One-click launcher for ALL checkouts: the main repo plus every worktree
rem under .claude\worktrees\. Each gets its own port, so parallel branches
rem can run side by side and be compared in the browser.
rem
rem V2.175: ONE window total, servers run hidden in the background, and NO
rem browser tabs are opened by default (the address table is printed instead).
rem The previous version delegated to each worktree's own launcher -- worktrees
rem still carrying the OLD launcher hardcoded port 8000, killed each other and
rem each popped a browser tab at localhost:8000. Now uvicorn is started
rem directly per port; per-worktree bats are not involved at all.
rem
rem This file is only a thin wrapper -- the real work is in start_all.ps1,
rem which is stored as UTF-8 with BOM so it can print Chinese safely.
rem (ASCII-only inside .bat on purpose: Chinese here can break on GBK codepage.)
rem
rem Usage:  double-click                     start all, print the address table
rem         一键启动全部.bat -Open july         ...and open just that one page
rem         一键启动全部.bat -ListOnly          just show the table, start nothing
rem Stop:   停止全部.bat
rem ============================================================
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_all.ps1" %*
echo.
pause
