' hidden launcher (SW_HIDE at process creation; True = wait)
' installed layout: %USERPROFILE%\brain-framework\... - adjust if cloned elsewhere
Dim sh : Set sh = CreateObject("WScript.Shell")
Dim home : home = sh.ExpandEnvironmentStrings("%USERPROFILE%")
sh.CurrentDirectory = home & "\brain-framework\resilience"
' powershell.exe by absolute path: a scheduled task inherits a PATH it does not
' control, so a bare name is resolvable to something else entirely.
sh.Run "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File """ & home & "\brain-framework\resilience\run_backup.ps1""", 0, True
