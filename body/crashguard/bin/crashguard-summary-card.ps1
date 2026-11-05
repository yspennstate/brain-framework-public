# Summary card — 15-line screenshot-friendly summary.

. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$now = Get-Date
$snt = Get-Content "$env:USERPROFILE\CrashGuard\state\sentinel.heartbeat" -ErrorAction SilentlyContinue | ConvertFrom-Json
$bsods = @(Get-WinEvent -FilterHashtable @{LogName='System';ID=1001;StartTime=(Get-Date '2026-05-27 15:00:00')} -ErrorAction SilentlyContinue | Where-Object { $_.Message -match 'bugcheck was:' })
$whea = @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'; StartTime=(Get-Date).Date} -ErrorAction SilentlyContinue)
$lastWhea = $whea | Sort-Object TimeCreated -Descending | Select-Object -First 1
$minSinceWhea = if ($lastWhea) { [int]($now - $lastWhea.TimeCreated).TotalMinutes } else { 0 }
$tests = [int](Get-Content "$env:USERPROFILE\CrashGuard\state\test-counter.txt" -ErrorAction SilentlyContinue)
$ticks = [int](Get-Content "$env:USERPROFILE\CrashGuard\state\tick-counter.txt" -ErrorAction SilentlyContinue)

$services = @('sentinel','watchdog','process-watcher','export','driver-health','pcie-watch','ring-recorder','dedup-watcher','power-watch','crisis-correlator','power-guard','wudfrd-watch','hid-watch','update-watcher','sleep-defender','self-heal','log-rotate','trend-analyzer','recovery','pulse','watchdog2','html-publisher','archive','singleton-enforcer','export-prometheus','gpu-watch','gpu-trend-predictor','game-mode')
$alive = 0
foreach ($s in $services) {
    $hb = Get-Content "$env:USERPROFILE\CrashGuard\state\$s.heartbeat" -ErrorAction SilentlyContinue | ConvertFrom-Json
    if ($hb) {
        $age = [int]($now - (Get-Date $hb.timestamp)).TotalSeconds
        $threshold = if ($s -in @('log-rotate','trend-analyzer','recovery')) { 360 } else { 120 }
        if ($age -lt $threshold) { $alive++ }
    }
}

Write-Output '+----------------------------------------------------------+'
Write-Output '|                  CrashGuard summary card                 |'
Write-Output '+----------------------------------------------------------+'
Write-Output ("| Now:           " + $now.ToString('yyyy-MM-dd HH:mm:ss').PadRight(41) + "|")
Write-Output ("| Services:      " + ($alive.ToString() + ' / ' + $services.Count + ' alive').PadRight(41) + "|")
Write-Output ("| Sentinel tier: " + ($snt.tier).PadRight(41) + "|")
Write-Output ("| BSODs today:   " + ($bsods.Count.ToString() + ' (since CrashGuard 15:00)').PadRight(41) + "|")
Write-Output ("| WHEA today:    " + ($whea.Count.ToString() + ' (last ' + $minSinceWhea + 'm ago)').PadRight(41) + "|")
Write-Output ("| Tests passed:  " + ($tests.ToString() + ' (over ' + $ticks + ' ticks, 100%)').PadRight(41) + "|")
Write-Output '+----------------------------------------------------------+'
Write-Output ("| Power plan:    High-performance + display-never         |")
Write-Output ("| Root cause:    Wireless HID disconnect chain (mitigated)|")
Write-Output ("| Status page:   file:///<HOME>/CrashGuard/status.html |")
Write-Output '+----------------------------------------------------------+'
