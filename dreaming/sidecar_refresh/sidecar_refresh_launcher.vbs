' SW_HIDE launcher for sidecar_refresh.ps1 - no console window, not even a flash
' (no_flashing_terminal_windows_2026_06_07). 0 = SW_HIDE; True = wait (no-overlap).
Dim sh : Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "%USERPROFILE%\.claude\dreaming"
sh.Run "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""%USERPROFILE%\.claude\dreaming\sidecar_refresh\sidecar_refresh.ps1""", 0, True
