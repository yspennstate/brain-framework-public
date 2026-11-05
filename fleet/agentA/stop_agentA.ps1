# Stop agentA: disarm the watchdog task, drop the STOP sentinel, and kill all
# runner process trees (runners + their claude/codex children). Never touches
# any other claude/codex process on the machine.
$base = "$env:USERPROFILE\agentA"
New-Item -ItemType File -Path "$base\STOP" -Force | Out-Null
schtasks /Change /TN AgentA /DISABLE 2>$null | Out-Null

$runners = Get-CimInstance Win32_Process -Filter "name='powershell.exe'" |
    Where-Object { $_.CommandLine -match 'agentA\\run_one\.ps1' }
foreach ($r in $runners) {
    "killing runner tree pid=$($r.ProcessId)"
    taskkill /T /F /PID $r.ProcessId 2>$null | Out-Null
}
Get-ChildItem "$base\state\*.pid" -ErrorAction SilentlyContinue | Remove-Item -Force
"agentA stopped ($(@($runners).Count) runner trees killed). Re-arm with spin_up_agentA.ps1"
