@echo off
rem [Change Log] Date:2026-08-07 Author:Claude/c Version:V2.241
rem Thin ASCII-only launcher. All logic and Chinese messages live in install_task.ps1.
rem
rem WHY ASCII-ONLY: cmd.exe reads .bat files using the system ANSI codepage while executing
rem them line by line. A UTF-8 .bat with Chinese text becomes mojibake and every line gets
rem parsed as a bogus command ("... is not recognized as an internal or external command").
rem "chcp 65001" does NOT fix it -- the decoding already went wrong at read time.
rem So: keep this file pure ASCII forever, and put anything Chinese in the .ps1 (UTF-8 with BOM).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_task.ps1"
pause
