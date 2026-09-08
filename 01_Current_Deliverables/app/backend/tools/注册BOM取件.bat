@echo off
rem [Change Log] Date:2026-09-08 Author:Claude/c Version:V2.525
rem Thin ASCII-only launcher for the BOM cost-sheet DOWNSTREAM pickup task.
rem All logic and Chinese messages live in install_bom_task.ps1 (UTF-8 with BOM).
rem WHY ASCII-ONLY: cmd.exe reads .bat with the system ANSI codepage.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_bom_task.ps1"
pause
