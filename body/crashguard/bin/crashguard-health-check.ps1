# Wakeup health-check — verifies all 4 services alive + sentinel responding.
# Returns 0 = healthy; 1 = needs intervention.

. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$problems = New-Object 'System.Collections.Generic.List[string]'

# Heartbeats
$expected = @{
    'sentinel'        = 5
    'watchdog'        = 60
    'process-watcher' = 30
    'export'          = 10
}

foreach ($svc in $expected.Keys) {
    $hb = CG-LoadJson (Join-Path $CG_STATE "$svc.heartbeat")
    if (-not $hb) { $problems.Add("MISSING $svc heartbeat"); continue }
    $age = ((Get-Date) - [datetime]::Parse($hb.timestamp)).TotalSeconds
    if ($age -gt $expected[$svc]) { $problems.Add("$svc heartbeat stale ${age}s") }
}

# Exported state
if (-not (Test-Path 'C:\ProgramData\CrashGuard\tier.txt')) { $problems.Add('exported tier.txt missing') }

# Tier consistency
$hb = CG-LoadJson (Join-Path $CG_STATE 'sentinel.heartbeat')
if ($hb -and $hb.last_sample) {
    $s = $hb.last_sample
    if ($null -eq $s.gpu_temp_c) { $problems.Add('GPU telemetry returned null (nvidia-smi may have failed)') }
    if ($null -eq $s.cpu_util_pct) { $problems.Add('CPU telemetry returned null') }
}

$report = [PSCustomObject]@{
    healthy   = ($problems.Count -eq 0)
    problems  = $problems
    sentinel  = if ($hb) { @{ tier=$hb.tier; iters=$hb.iterations } } else { $null }
    when      = CG-Now
}
$report | ConvertTo-Json -Depth 5
if ($problems.Count -gt 0) { exit 1 } else { exit 0 }
