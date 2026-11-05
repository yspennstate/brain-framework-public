' autorecover launcher - SW_HIDE so there is NO console flash at logon (hard rule).
' 0 = SW_HIDE (never visible); True = wait. The .ps1 it runs is gate-heavy and idempotent:
' opt-in flag, once-per-boot marker, unclean-boot check, then relaunch's own guards.
Dim sh : Set sh = CreateObject("WScript.Shell")
Dim home : home = sh.ExpandEnvironmentStrings("%USERPROFILE%")
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & home & "\.claude\fleet\autorecover.ps1""", 0, True
