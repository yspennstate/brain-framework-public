# Stats — daily rollup of spike-log entries. Counts by kind + time-of-day distribution.
# Run manually: powershell $env:USERPROFILE\CrashGuard\bin\crashguard-stats.ps1

. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$spikePath = Join-Path $CG_TELEMETRY 'spike-log.jsonl'
if (-not (Test-Path $spikePath)) { Write-Output 'no spike-log yet'; return }

$today = (Get-Date).Date
$entries = Get-Content $spikePath -ErrorAction SilentlyContinue | ForEach-Object {
    try { $j = $_ | ConvertFrom-Json; $j } catch {}
} | Where-Object { $_.ts -and ([datetime]$_.ts).Date -eq $today }

Write-Output '=== Spike-log stats — today ==='
Write-Output ('Total entries today: ' + $entries.Count)
Write-Output ''
Write-Output 'By kind:'
$entries | Group-Object kind | Sort-Object Count -Descending | ForEach-Object { Write-Output ('  ' + $_.Name + ': ' + $_.Count) }
Write-Output ''
Write-Output 'By hour:'
$entries | Group-Object { ([datetime]$_.ts).Hour } | Sort-Object Name | ForEach-Object { Write-Output ('  hour=' + $_.Name + ': ' + $_.Count) }
Write-Output ''
Write-Output 'Tier escalations today:'
$entries | Where-Object { $_.to_tier } | Group-Object to_tier | Sort-Object Count -Descending | ForEach-Object { Write-Output ('  -> ' + $_.Name + ': ' + $_.Count) }
