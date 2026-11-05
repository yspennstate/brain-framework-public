# uninstall_compute_governor.ps1 - remove the governor cleanly (tasks, loop, no orphans).
# State/ledger files in $env:USERPROFILE\.claude\compute are kept as the audit record.

$ErrorActionPreference = 'SilentlyContinue'

foreach ($t in 'ComputeGovernor', 'ComputeGovernorAliveCheck') {
    Unregister-ScheduledTask -TaskName $t -Confirm:$false
    "unregistered $t"
}

$gpid = Get-Content $env:USERPROFILE\.claude\compute\governor.pid -ErrorAction SilentlyContinue | Select-Object -First 1
if ($gpid) {
    $p = Get-Process -Id $gpid -ErrorAction SilentlyContinue
    if ($p -and $p.ProcessName -match 'powershell') { Stop-Process -Id $gpid -Force -Confirm:$false; "stopped loop pid $gpid" }
}

# lift any Idle-escalated AI processes back to BelowNormal so nothing stays orphaned
foreach ($p in (Get-Process | Where-Object { @('python','pythonw','node','codex','claude','uv','julia','Rscript','deno') -contains $_.ProcessName })) {
    try { if ("$($p.PriorityClass)" -eq 'Idle') { $p.PriorityClass = 'BelowNormal'; "lifted $($p.Id) $($p.ProcessName)" } } catch {}
}

# retire the state file so compute_sense never nags sessions about a deliberate uninstall
$retired = @{ ts = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'); host = $env:COMPUTERNAME; status = 'retired';
              reasons = @(); note = 'governor uninstalled deliberately; reinstall via install_compute_governor.ps1' } | ConvertTo-Json
Set-Content -Path $env:USERPROFILE\.claude\compute\compute_state.json -Value $retired -Encoding UTF8
"uninstalled (state retired; ledger kept in $env:USERPROFILE\.claude\compute)"
