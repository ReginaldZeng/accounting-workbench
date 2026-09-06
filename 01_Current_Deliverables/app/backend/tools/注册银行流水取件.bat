@echo off
rem [Change Log] Date:2026-09-06 Author:Claude/c Version:V2.493
rem Thin ASCII-only launcher for the bank-statement UPSTREAM pickup task.
rem All logic and Chinese messages live in install_bank_task.ps1.
rem WHY ASCII-ONLY: cmd.exe reads .bat with the system ANSI codepage; a UTF-8 .bat with
rem Chinese becomes mojibake and every line is parsed as a bogus command. Keep this pure ASCII;
rem put anything Chinese in the .ps1 (UTF-8 with BOM).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_bank_task.ps1"
pause
