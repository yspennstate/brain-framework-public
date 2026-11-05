# Quick status snapshot for humans / dashboards.

. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'crashguard-common.ps1')

$sentinelHb = CG-LoadJson (Join-Path $CG_STATE 'sentinel.heartbeat')
$watchdogHb = CG-LoadJson (Join-Path $CG_STATE 'watchdog.heartbeat')
$active     = if (Test-Path (Join-Path $CG_STATE 'active-mitigations.json')) { CG-LoadJson (Join-Path $CG_STATE 'active-mitigations.json') } else { @() }

Write-Output "=== CrashGuard Status ==="
Write-Output "Now: $(CG-Now)"
if ($sentinelHb) {
    $age = ((Get-Date) - [datetime]::Parse($sentinelHb.timestamp)).TotalSeconds
    Write-Output ("Sentinel: pid={0} tier={1} iters={2} age={3:N1}s dry={4}" -f $sentinelHb.pid, $sentinelHb.tier, $sentinelHb.iterations, $age, $sentinelHb.dry_run)
    if ($sentinelHb.last_sample) {
        $s = $sentinelHb.last_sample
        Write-Output ("  GPU: temp={0}C util={1}% mem={2}% power={3}%" -f $s.gpu_temp_c, $s.gpu_util_pct, $s.gpu_mem_used_pct, $s.gpu_power_pct)
        Write-Output ("  CPU: {0}%  RAM: {1}%" -f $s.cpu_util_pct, $s.ram_used_pct)
    }
} else {
    Write-Output "Sentinel: NOT RUNNING"
}
if ($watchdogHb) {
    $age = ((Get-Date) - [datetime]::Parse($watchdogHb.timestamp)).TotalSeconds
    Write-Output ("Watchdog: pid={0} age={1:N1}s" -f $watchdogHb.pid, $age)
} else {
    Write-Output "Watchdog: NOT RUNNING"
}
Write-Output ("Active mitigations: {0}" -f $active.Count)
$active | Select-Object -Last 8 | ForEach-Object { Write-Output ("  - {0} {1}/{2} {3}->{4} @{5}" -f $_.tier, $_.action, $_.name, $_.before, $_.after, $_.applied_at) }
