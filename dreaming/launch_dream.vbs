' launch_dream.vbs - hidden launcher for the nightly brain dream.
' 0 = SW_HIDE (no window, not even a flash); True = wait so the task's
' no-overlap policy works. Per the standing no-flash rule.
Dim sh : Set sh = CreateObject("WScript.Shell")
Dim home : home = sh.ExpandEnvironmentStrings("%USERPROFILE%")
sh.CurrentDirectory = home
Dim dreamExit
dreamExit = sh.Run("C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & home & "\.claude\dreaming\run_dream.ps1""", 0, True)
WScript.Quit dreamExit
