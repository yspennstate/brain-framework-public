' launch_humanness.vbs - hidden launcher for the weekly humanness regression check.
' 0 = SW_HIDE (no window, not even a flash); True = wait so a task's
' no-overlap policy works. Per the standing no-flash rule.
Dim sh : Set sh = CreateObject("WScript.Shell")
Dim home : home = sh.ExpandEnvironmentStrings("%USERPROFILE%")
sh.CurrentDirectory = home
sh.Run "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & home & "\.claude\dreaming\humanness_check.ps1""", 0, True
