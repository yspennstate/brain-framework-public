' launch_compute_governor.vbs - start the compute governor with NO window, not even a flash.
' installed layout: %USERPROFILE%\brain-framework\... - adjust if cloned elsewhere
Dim sh : Set sh = CreateObject("WScript.Shell")
Dim home : home = sh.ExpandEnvironmentStrings("%USERPROFILE%")
sh.CurrentDirectory = home & "\brain-framework\governor"
sh.Run "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File """ & home & "\brain-framework\governor\compute_governor.ps1""", 0, True
