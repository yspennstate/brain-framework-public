' agentA hidden entry point for the AgentA scheduled task.
' 0 = SW_HIDE (console never visible, not even a flash); True = wait so the
' task's no-overlap policy keeps working. Runners themselves are created via
' WMI with hidden startup info and outlive this script.
Dim sh : Set sh = CreateObject("WScript.Shell")
Dim home : home = sh.ExpandEnvironmentStrings("%USERPROFILE%")
sh.CurrentDirectory = home
sh.Run "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & home & "\agentA\start_agentA.ps1""", 0, True
