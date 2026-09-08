' [Change Log] Date:2026-09-08 Author:Claude/c Version:V2.525
' Hidden launcher for bom_pull.ps1 (BOM cost-sheet DOWNSTREAM pickup to the cost accountant's PC).
' Same rationale as pull_hidden.vbs: run hidden + wait so the task's time-limit / no-overlap rules apply.
' KEEP THIS FILE PURE ASCII (.vbs is read with the system ANSI codepage).
Option Explicit
Dim sh, here, ps1
Set sh = CreateObject("WScript.Shell")
here = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
ps1 = here & "bom_pull.ps1"
WScript.Quit sh.Run("powershell.exe -NoProfile -ExecutionPolicy Bypass -File " & Chr(34) & ps1 & Chr(34), 0, True)
