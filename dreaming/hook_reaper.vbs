' hook_reaper launcher - 0 = SW_HIDE (never visible), True = wait
Dim sh : Set sh = CreateObject("WScript.Shell")
sh.Run "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ""python -s %USERPROFILE%\.claude\dreaming\hook_reaper.py""", 0, True
