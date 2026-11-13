' SW_HIDE launcher for sidecar_refresh.ps1 - no console window, not even a flash.
' 0 = SW_HIDE; True = wait (no-overlap). CurrentDirectory does not expand %VARS%, so the home is expanded first.
Dim sh : Set sh = CreateObject("WScript.Shell")
Dim home : home = sh.ExpandEnvironmentStrings("%USERPROFILE%")
sh.CurrentDirectory = home & "\.claude\dreaming"
WScript.Quit sh.Run("C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & home & "\.claude\dreaming\sidecar_refresh\sidecar_refresh.ps1""", 0, True)
