' [Change Log] Date:2026-08-08 Author:Claude/c Version:V2.242
' Hidden launcher for pull_reports.ps1.
'
' WHY THIS FILE EXISTS
' The scheduled task must run as the logged-on user (session 1), because the shared
' drive / mapped drive only exists in that user's session -- SYSTEM cannot see it.
' But anything started in session 1 gets a VISIBLE window. Running powershell.exe
' directly popped a console window on that PC every single minute, and Windows
' consoles default to QuickEdit mode: one stray click inside the window selects text
' and FREEZES the process until Esc is pressed. Frozen consoles then pile up on the
' desktop, one per minute, and syncing silently stops. (Observed in production.)
'
' Run(..., 0, True) starts PowerShell with a hidden window and WAITS for it.
' The wait matters: without it wscript would exit immediately, the task would look
' "finished" while PowerShell kept running, and both the task's execution time limit
' and its "do not start a new instance" rule would stop protecting anything.
'
' KEEP THIS FILE PURE ASCII. .vbs is read using the system ANSI codepage; a UTF-8
' file with Chinese comments becomes mojibake and fails to parse (same trap already
' hit with the .bat launcher).
Option Explicit
Dim sh, here, ps1
Set sh = CreateObject("WScript.Shell")
here = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
ps1 = here & "pull_reports.ps1"
WScript.Quit sh.Run("powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & ps1 & """", 0, True)
