' launch_alive_check.vbs - run the governor alive-check with NO window (SW_HIDE), wait for exit
' installed layout: %USERPROFILE%\brain-framework\... - adjust if cloned elsewhere
Dim sh : Set sh = CreateObject("WScript.Shell")
Dim home : home = sh.ExpandEnvironmentStrings("%USERPROFILE%")
sh.CurrentDirectory = home & "\brain-framework\governor"
sh.Run "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File """ & home & "\brain-framework\governor\governor_alive_check.ps1""", 0, True
