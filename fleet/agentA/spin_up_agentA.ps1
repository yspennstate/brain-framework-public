# Spin up agentA: re-arm and fire the hidden launcher via the AgentA scheduled
# task (fully detached from the calling shell, zero visible windows).
# Sessions already DONE/BLOCKED/ERROR/STALLED are left alone - delete the
# matching file under agentA\done or agentA\state to re-arm one of those.
$base = "$env:USERPROFILE\agentA"
Remove-Item "$base\STOP" -Force -ErrorAction SilentlyContinue
schtasks /Change /TN AgentA /ENABLE | Out-Null
schtasks /Run /TN AgentA | Out-Null
"agentA spin-up fired. Check in ~2 min with: powershell -File $env:USERPROFILE\agentA\status_agentA.ps1"
