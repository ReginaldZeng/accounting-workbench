' [Change Log] Date:2026-09-06 Author:Claude/c Version:V2.493
' Hidden launcher for bank_pull.ps1 (bank statement UPSTREAM pickup).
' Same rationale as pull_hidden.vbs: the scheduled task runs as the logged-on user
' (the shared drive only exists in that session); anything started there gets a
' VISIBLE window unless launched hidden. Run(..., 0, True) = hidden + wait, so the
' task's time-limit / no-overlap rules keep protecting it.
' KEEP THIS FILE PURE ASCII (.vbs is read with the system ANSI codepage).
Option Explicit
Dim sh, here, ps1
Set sh = CreateObject("WScript.Shell")
here = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
ps1 = here & "bank_pull.ps1"
WScript.Quit sh.Run("powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & ps1 & """", 0, True)
