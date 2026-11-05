# Trend analyzer — scans ring.jsonl every 5 min looking for slow-build anomalies
# that escape per-sample tier evaluation (which is reactive). Watches for:
#   - GPU temperature creeping up over a 15-min window (>5C rise)
#   - RAM growing monotonically over 30 min (>5% rise + no drop)
#   - CPU average above 70% for sustained 10 min
#   - Driver error count trending up (>5 events in 15min vs zero in prior 15min)

[CmdletBinding()]
param([int]$IntervalSec = 300, [switch]$Once)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$ringPath = Join-Path $CG_TELEMETRY 'ring.jsonl'

CG-Log 'trend-analyzer' 'info' ('Trend analyzer starting PID=' + $PID + ' interval=' + $IntervalSec + 's')

while ($true) {
    try {
        if (Test-Path $ringPath) {
            $samples = Get-Content $ringPath -ErrorAction SilentlyContinue | ForEach-Object {
                try { $_ | ConvertFrom-Json } catch {}
            } | Where-Object { $_.ts }

            $now = Get-Date
            $window15 = $samples | Where-Object { [datetime]$_.ts -ge $now.AddMinutes(-15) }
            $window30 = $samples | Where-Object { [datetime]$_.ts -ge $now.AddMinutes(-30) }
            $window10 = $samples | Where-Object { [datetime]$_.ts -ge $now.AddMinutes(-10) }

            $anomalies = New-Object 'System.Collections.Generic.List[string]'

            # GPU temperature creep
            $gputemps = $window15 | Where-Object { $_.gpu -and $_.gpu.temp_c } | ForEach-Object { [int]$_.gpu.temp_c }
            if ($gputemps -and $gputemps.Count -ge 50) {
                $first10 = ($gputemps | Select-Object -First 10 | Measure-Object -Average).Average
                $last10  = ($gputemps | Select-Object -Last 10 | Measure-Object -Average).Average
                if ($last10 - $first10 -gt 5) { $anomalies.Add('GPU-temp-creep ' + [int]$first10 + 'C -> ' + [int]$last10 + 'C') }
            }

            # RAM monotonic growth (allow some noise: end >= start + 5 AND no significant drop)
            $rams = $window30 | ForEach-Object { [int]$_.ram_pct }
            if ($rams -and $rams.Count -ge 50) {
                $minR = ($rams | Measure-Object -Minimum).Minimum
                $maxR = ($rams | Measure-Object -Maximum).Maximum
                $last5 = ($rams | Select-Object -Last 5 | Measure-Object -Average).Average
                if ($maxR - $minR -gt 5 -and $last5 -ge $maxR - 1) { $anomalies.Add('RAM-monotonic ' + [int]$minR + '% -> ' + [int]$last5 + '%') }
            }

            # CPU sustained-high
            $cpus = $window10 | ForEach-Object { [int]$_.cpu_pct }
            if ($cpus -and $cpus.Count -ge 30) {
                $avgC = ($cpus | Measure-Object -Average).Average
                if ($avgC -gt 70) { $anomalies.Add('CPU-sustained-high ' + [int]$avgC + '% avg over 10min') }
            }

            # Driver errors trending up
            $first15Errs = (($window30 | Where-Object { [datetime]$_.ts -lt $now.AddMinutes(-15) }) | ForEach-Object { [int]$_.drv_err } | Measure-Object -Sum).Sum
            $last15Errs = ($window15 | ForEach-Object { [int]$_.drv_err } | Measure-Object -Sum).Sum
            if ($last15Errs -gt 5 -and $first15Errs -eq 0) { $anomalies.Add('Driver-err-spike ' + $first15Errs + ' -> ' + $last15Errs) }

            if ($anomalies.Count -gt 0) {
                CG-Log 'trend-analyzer' 'warn' ('Trends detected: ' + ($anomalies -join '; '))
                $entry = [PSCustomObject]@{
                    ts = CG-Now
                    kind = 'trend-anomaly'
                    anomalies = $anomalies
                    sample_count_15min = $window15.Count
                }
                ($entry | ConvertTo-Json -Compress -Depth 3) | Add-Content -Path (Join-Path $CG_TELEMETRY 'spike-log.jsonl') -Encoding utf8 -ErrorAction SilentlyContinue
            }
            CG-Heartbeat 'trend-analyzer' @{ samples_in_ring = $samples.Count; anomalies_this_iter = $anomalies.Count }
        }
    } catch {
        CG-Log 'trend-analyzer' 'error' ('Iter exception: ' + $_)
    }
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSec
}
