' CrashGuard auto-start launcher - SW_HIDE so there is NO console flash at logon
' (hard rule: never pop/flash a terminal window). 0 = SW_HIDE, True = wait.
' crashguard-launch-all.ps1 is idempotent + singleton-enforced, so re-running is safe.
Dim sh : Set sh = CreateObject("WScript.Shell")
Dim home : home = sh.ExpandEnvironmentStrings("%USERPROFILE%")
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & home & "\CrashGuard\bin\crashguard-launch-all.ps1""", 0, True
