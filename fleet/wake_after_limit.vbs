' one-shot launcher for wake_after_limit.ps1 - SW_HIDE so there is NO console flash (hard rule).
Dim sh : Set sh = CreateObject("WScript.Shell")
Dim home : home = sh.ExpandEnvironmentStrings("%USERPROFILE%")
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & home & "\.claude\fleet\wake_after_limit.ps1""", 0, True
