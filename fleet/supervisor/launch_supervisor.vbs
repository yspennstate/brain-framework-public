' Hidden entry point: SW_HIDE is applied at process creation, so there is no
' PseudoConsoleWindow flash.  True keeps Task Scheduler's IgnoreNew policy useful.
' pythonw.exe must be on PATH (or set the full path for your interpreter here).
Dim sh, rc : Set sh = CreateObject("WScript.Shell")
Dim home : home = sh.ExpandEnvironmentStrings("%USERPROFILE%")
sh.CurrentDirectory = home & "\.claude\fleet\codex_continuity"
rc = sh.Run("pythonw.exe """ & home & "\.claude\fleet\codex_continuity\supervisor.py""", 0, True)
WScript.Quit rc
