@echo off
rem ============================================================
rem [Change Log] Date: 2026-08-05  Author: Claude / c  Version: V2.175
rem Stop every backend started by the one-click launcher (all checkouts).
rem Thin wrapper around stop_all.ps1 (UTF-8 BOM, prints Chinese safely).
rem (ASCII-only inside .bat on purpose.)
rem ============================================================
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_all.ps1" %*
echo.
pause
