# GPU trend predictor — reads gpu-trend.jsonl, runs linear regression on temp_c over recent window,
# predicts when temp will hit RED threshold (88C). Writes prediction tier to gpu-predict.tier.
# Sentinel ORs this in alongside gpu-watch.tier (current state) and CPU tier.
# Why: catches slow thermal climb BEFORE current-state thresholds trip.

[CmdletBinding()]
param(
    [int]$IntervalSec = 15,
    [int]$WindowSec = 120,
    [double]$RedTempC = 88.0,
    [double]$CritTempC = 92.0,
    [double]$MinSlopeAlert = 2.0,
    [switch]$Once
)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$trend = Join-Path $CG_STATE 'gpu-trend.jsonl'
$predTierPath = Join-Path $CG_STATE 'gpu-predict.tier'
$forecastLog = Join-Path $CG_STATE 'gpu-forecast.jsonl'

CG-Log 'gpu-trend-predictor' 'info' "starting PID=$PID interval=${IntervalSec}s window=${WindowSec}s red=${RedTempC}C crit=${CritTempC}C"

function Compute-Slope($points) {
    # Linear regression: returns slope in degrees-C per minute, intercept (deg-C at first point)
    if ($points.Count -lt 5) { return $null }
    $n = $points.Count
    $sumX = 0.0; $sumY = 0.0; $sumXY = 0.0; $sumXX = 0.0
    $t0 = $points[0].t_sec
    for ($i = 0; $i -lt $n; $i++) {
        $x = $points[$i].t_sec - $t0  # seconds offset
        $y = $points[$i].temp_c
        $sumX += $x; $sumY += $y
        $sumXY += $x * $y; $sumXX += $x * $x
    }
    $denom = ($n * $sumXX - $sumX * $sumX)
    if ($denom -eq 0) { return $null }
    $slopePerSec = ($n * $sumXY - $sumX * $sumY) / $denom
    $intercept = ($sumY - $slopePerSec * $sumX) / $n
    return @{
        slope_per_sec = $slopePerSec
        slope_per_min = $slopePerSec * 60.0
        intercept     = $intercept
        n_points      = $n
    }
}

while ($true) {
    try {
        $tier = 'green'
        $reasons = @()
        $forecast = $null
        $slope = $null

        if (Test-Path $trend) {
            $lines = Get-Content $trend -ErrorAction SilentlyContinue
            if ($lines -and $lines.Count -ge 5) {
                # Parse last ~$WindowSec / 3sec = WindowSec/3 lines (samples are every 3s)
                $lookback = [Math]::Min($lines.Count, [int]([math]::Ceiling($WindowSec / 3.0)))
                $recent = $lines[-$lookback..-1]
                $points = New-Object 'System.Collections.Generic.List[object]'
                $epoch = [DateTime]::new(1970,1,1,0,0,0,[DateTimeKind]::Utc)
                foreach ($l in $recent) {
                    try {
                        $j = $l | ConvertFrom-Json
                        $ts = (Get-Date $j.timestamp).ToUniversalTime()
                        $points.Add(@{ t_sec = [double]($ts.Subtract($epoch).TotalSeconds); temp_c = [double]$j.temp_c })
                    } catch {}
                }

                if ($points.Count -ge 5) {
                    $slope = Compute-Slope $points
                    if ($slope) {
                        $currentTemp = $points[-1].temp_c
                        $slopeMin = $slope.slope_per_min

                        # Forecast: how long until temp reaches RED at current slope?
                        $etaRedSec = $null
                        $etaCritSec = $null
                        if ($slopeMin -gt 0.01) {
                            $deltaRed = $RedTempC - $currentTemp
                            $deltaCrit = $CritTempC - $currentTemp
                            if ($deltaRed -gt 0) { $etaRedSec = [int]($deltaRed / $slope.slope_per_sec) }
                            if ($deltaCrit -gt 0) { $etaCritSec = [int]($deltaCrit / $slope.slope_per_sec) }
                        }

                        $forecast = @{
                            current_temp_c = $currentTemp
                            slope_per_min  = [math]::Round($slopeMin, 3)
                            n_points       = $slope.n_points
                            eta_red_sec    = $etaRedSec
                            eta_crit_sec   = $etaCritSec
                        }

                        # Tier classification — predictive
                        if ($slopeMin -ge $MinSlopeAlert -and $currentTemp -ge 60) {
                            $tier = 'yellow'
                            $reasons += "slope=${slopeMin}C/min rising"
                        }
                        if ($etaRedSec -and $etaRedSec -le 600 -and $currentTemp -ge 70) {
                            $tier = 'orange'
                            $reasons += "eta_red=${etaRedSec}s<=10min"
                        }
                        if ($etaRedSec -and $etaRedSec -le 180 -and $currentTemp -ge 75) {
                            $tier = 'red'
                            $reasons += "eta_red=${etaRedSec}s<=3min"
                        }
                        if ($etaCritSec -and $etaCritSec -le 120 -and $currentTemp -ge 80) {
                            $tier = 'critical'
                            $reasons += "eta_crit=${etaCritSec}s<=2min - imminent TDR risk"
                        }
                    }
                }
            }
        }

        Set-Content -Path $predTierPath -Value $tier -Encoding utf8 -Force

        # Forecast log (1 line every iter)
        $logLine = [pscustomobject]@{
            timestamp = (Get-Date).ToString('o')
            tier      = $tier
            reasons   = ($reasons -join '|')
            forecast  = $forecast
        } | ConvertTo-Json -Compress -Depth 5
        Add-Content -Path $forecastLog -Value $logLine -Encoding utf8

        if ($tier -in @('orange','red','critical')) {
            CG-Log 'gpu-trend-predictor' 'warn' "tier=$tier $($reasons -join '; ')"
        }

        CG-Heartbeat 'gpu-trend-predictor' @{
            tier = $tier
            slope_c_per_min = if ($slope) { [math]::Round($slope.slope_per_min, 3) } else { 0 }
            n_points = if ($slope) { $slope.n_points } else { 0 }
        }
    } catch {
        CG-Log 'gpu-trend-predictor' 'error' "Iter exception: $_"
    }
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSec
}
