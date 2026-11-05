# Prometheus-format metrics exporter. Writes to telemetry/metrics.prom every 30s.
# Can be scraped by any Prometheus-compatible monitor.

[CmdletBinding()]
param([int]$IntervalSec = 30, [switch]$Once)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

CG-Log 'export-prometheus' 'info' ('Prometheus exporter starting PID=' + $PID + ' interval=' + $IntervalSec + 's')

while ($true) {
    try {
        $now = Get-Date
        $snt = Get-Content (Join-Path $CG_STATE 'sentinel.heartbeat') -ErrorAction SilentlyContinue | ConvertFrom-Json
        $bsods = @(Get-WinEvent -FilterHashtable @{LogName='System';ID=1001;StartTime=(Get-Date '2026-05-27 15:00:00')} -ErrorAction SilentlyContinue | Where-Object { $_.Message -match 'bugcheck was:' })
        $whea = @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'; StartTime=(Get-Date).Date} -ErrorAction SilentlyContinue)
        $lastWhea = $whea | Sort-Object TimeCreated -Descending | Select-Object -First 1
        $minSinceWhea = if ($lastWhea) { [int]($now - $lastWhea.TimeCreated).TotalMinutes } else { 99999 }
        $tests = [int](Get-Content (Join-Path $CG_STATE 'test-counter.txt') -ErrorAction SilentlyContinue)
        $ticks = [int](Get-Content (Join-Path $CG_STATE 'tick-counter.txt') -ErrorAction SilentlyContinue)

        $tierNum = switch ($snt.tier) { 'green' {0}; 'yellow' {1}; 'orange' {2}; 'red' {3}; 'critical' {4}; default {-1} }

        $lines = @()
        $lines += '# HELP crashguard_tier_level Current sentinel tier (0=green 1=yellow 2=orange 3=red 4=critical)'
        $lines += '# TYPE crashguard_tier_level gauge'
        $lines += "crashguard_tier_level $tierNum"
        $lines += ''
        $lines += '# HELP crashguard_cpu_pct Current CPU utilization'
        $lines += '# TYPE crashguard_cpu_pct gauge'
        $lines += ('crashguard_cpu_pct ' + $snt.last_sample.cpu_util_pct)
        $lines += ''
        $lines += '# HELP crashguard_ram_pct Current RAM utilization'
        $lines += '# TYPE crashguard_ram_pct gauge'
        $lines += ('crashguard_ram_pct ' + $snt.last_sample.ram_used_pct)
        $lines += ''
        $lines += '# HELP crashguard_gpu_temp_c GPU temperature in Celsius'
        $lines += '# TYPE crashguard_gpu_temp_c gauge'
        $lines += ('crashguard_gpu_temp_c ' + $snt.last_sample.gpu_temp_c)
        $lines += ''
        $lines += '# HELP crashguard_bsods_session Number of BSODs since CrashGuard session start'
        $lines += '# TYPE crashguard_bsods_session counter'
        $lines += ('crashguard_bsods_session ' + $bsods.Count)
        $lines += ''
        $lines += '# HELP crashguard_whea_today WHEA-Logger events today'
        $lines += '# TYPE crashguard_whea_today counter'
        $lines += ('crashguard_whea_today ' + $whea.Count)
        $lines += ''
        $lines += '# HELP crashguard_min_since_whea Minutes since last WHEA event'
        $lines += '# TYPE crashguard_min_since_whea gauge'
        $lines += ('crashguard_min_since_whea ' + $minSinceWhea)
        $lines += ''
        $lines += '# HELP crashguard_tests Total synthetic tests passed'
        $lines += '# TYPE crashguard_tests counter'
        $lines += ('crashguard_tests ' + $tests)
        $lines += ''
        $lines += '# HELP crashguard_ticks Watchdog ticks completed'
        $lines += '# TYPE crashguard_ticks counter'
        $lines += ('crashguard_ticks ' + $ticks)
        $lines += ''
        $lines += '# HELP crashguard_sentinel_iters Sentinel iterations since spawn'
        $lines += '# TYPE crashguard_sentinel_iters counter'
        $lines += ('crashguard_sentinel_iters ' + $snt.iterations)

        $out = Join-Path $CG_TELEMETRY 'metrics.prom'
        $lines -join "`n" | Out-File -FilePath $out -Encoding utf8 -Force

        CG-Heartbeat 'export-prometheus' @{ exported_at = $now.ToString('o') }
    } catch {
        CG-Log 'export-prometheus' 'error' ('Iter exception: ' + $_)
    }
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSec
}
