' high_broadcast_launcher.vbs - hidden hourly High-method mesh reminder.
' installed layout: %USERPROFILE%\brain-framework\... - adjust if cloned elsewhere
Dim sh : Set sh = CreateObject("WScript.Shell")
Dim home : home = sh.ExpandEnvironmentStrings("%USERPROFILE%")
sh.CurrentDirectory = home & "\brain-framework\delegation"
sh.Run "pythonw.exe """ & home & "\brain-framework\delegation\high_broadcast.py""", 0, True
