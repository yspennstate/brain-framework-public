# Emergency rollback — reverses all active mitigations regardless of tier.
# Use when user wants to restore everything immediately.

[CmdletBinding()]
param([switch]$Confirm)

. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$activePath = Join-Path $CG_STATE 'active-mitigations.json'
if (-not (Test-Path $activePath)) { Write-Output 'no active-mitigations.json'; return }

$active = Get-Content $activePath -ErrorAction SilentlyContinue | ConvertFrom-Json
if (-not $active -or $active.Count -eq 0) { Write-Output 'no active mitigations'; return }

Write-Output ('Found ' + $active.Count + ' active mitigations:')
$active | Group-Object action | ForEach-Object { Write-Output ('  ' + $_.Name + ': ' + $_.Count) }
Write-Output ''

if (-not $Confirm) {
    Write-Output 'Re-run with -Confirm to roll back'
    return
}

$rolled = 0
foreach ($m in $active) {
    try {
        switch ($m.action) {
            'throttle' {
                $proc = Get-Process -Id $m.pid -ErrorAction SilentlyContinue
                if ($proc -and (CG-IsSameProcess $proc $m.name $m.process_started_at)) {
                    $priority = CG-ResolvePriorityClass $m.before
                    if ($null -eq $priority) {
                        $priority = [System.Diagnostics.ProcessPriorityClass]::Normal
                    }
                    $proc.PriorityClass = $priority
                    $rolled++
                }
            }
            'suspend' {
                $proc = Get-Process -Id $m.pid -ErrorAction SilentlyContinue
                if ($proc -and (CG-IsSameProcess $proc $m.name $m.process_started_at) -and (CG-Resume $m.pid)) { $rolled++ }
            }
            'suspend_browser_gpu' {
                $proc = Get-Process -Id $m.pid -ErrorAction SilentlyContinue
                if ($proc -and (CG-IsSameProcess $proc $m.name $m.process_started_at) -and (CG-Resume $m.pid)) { $rolled++ }
            }
            'gpu_power_limit' {
                $nvsmi = CG-FindNvSmi
                if ($nvsmi) { & $nvsmi -i 0 -pm 0 2>$null | Out-Null; $rolled++ }
            }
            'gpu_clock_lock' {
                $nvsmi = CG-FindNvSmi
                if ($nvsmi) { & $nvsmi -i 0 -rgc 2>$null | Out-Null; $rolled++ }
            }
            default { CG-Log 'rollback-all' 'info' ('skip ' + $m.action) }
        }
    } catch {}
}
Set-Content $activePath -Value '[]' -Encoding utf8 -Force
Write-Output ('Rolled back ' + $rolled + ' mitigations. Active list cleared.')

$entry = [PSCustomObject]@{
    ts = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss.fff')
    kind = 'rollback-all-invoked'
    rolled = $rolled
}
($entry | ConvertTo-Json -Compress) | Add-Content -Path (Join-Path $CG_TELEMETRY 'spike-log.jsonl') -Encoding utf8 -ErrorAction SilentlyContinue
