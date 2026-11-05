# Crisis-correlator — fires when multiple BSOD-precursor signals stack within a short window.
# Each precursor alone could be benign, but TWO+ within a few minutes = high crash imminent.
# Precursors monitored (all read from spike-log.jsonl):
#   - whea-precursor (WHEA-Logger id=17 PCIe corrected error)
#   - driver-health-critical (nvlddmkm id=14 TDR-recovered)
#   - rapid-display-cycle (>=3 display-on/off events in 5 min)
#   - tier=critical escalations
#   - dxgkrnl error events

[CmdletBinding()]
param(
    [int]$IntervalSec = 15,
    [int]$WindowMin = 5,
    [int]$StackThreshold = 2,
    [switch]$Once
)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$spikePath = Join-Path $CG_TELEMETRY 'spike-log.jsonl'

# Track whether we've already alerted for the current crisis (debounce)
$lastAlertTs = [datetime]::MinValue

CG-Log 'crisis-correlator' 'info' "Crisis correlator starting PID=$PID interval=${IntervalSec}s window=${WindowMin}min threshold=$StackThreshold"

while ($true) {
    try {
        $cutoff = (Get-Date).AddMinutes(-$WindowMin)
        $entries = if (Test-Path $spikePath) { Get-Content $spikePath -ErrorAction SilentlyContinue } else { @() }

        $recent = $entries | ForEach-Object {
            try { $j = $_ | ConvertFrom-Json; if ($j.ts -and ([datetime]$j.ts -ge $cutoff)) { $j } } catch {}
        }
        $criticalKinds = @('whea-precursor','driver-health-critical','rapid-display-cycle')
        $hits = $recent | Where-Object { $_.kind -in $criticalKinds }
        $tierSpikes = $recent | Where-Object { $_.to_tier -eq 'critical' }

        $score = ($hits | ForEach-Object { $_.kind } | Sort-Object -Unique).Count + ($(if($tierSpikes){1}else{0}))

        if ($score -ge $StackThreshold) {
            # Debounce: only one alert per 10 min
            if (((Get-Date) - $lastAlertTs).TotalMinutes -lt 10) {
                CG-Log 'crisis-correlator' 'info' "Crisis still active (debounced), score=$score"
            } else {
                $lastAlertTs = Get-Date
                $kinds = ($hits | ForEach-Object { $_.kind } | Sort-Object -Unique) -join ', '
                CG-Log 'crisis-correlator' 'error' "*** CRISIS *** score=$score kinds=$kinds tierSpikes=$($tierSpikes.Count)"

                $crisisEntry = [PSCustomObject]@{
                    ts = CG-Now
                    kind = 'CRISIS'
                    score = $score
                    components = $kinds
                    tier_critical_spikes = $tierSpikes.Count
                    recent_count = $recent.Count
                }
                ($crisisEntry | ConvertTo-Json -Compress) | Add-Content -Path $spikePath -Encoding utf8 -ErrorAction SilentlyContinue

                # Capture forensic dump BEFORE mitigations alter state
                try {
                    & (Join-Path $CG_BIN 'crashguard-forensic-dump.ps1') -Reason "CRISIS: $kinds" | Out-Null
                } catch { CG-Log 'crisis-correlator' 'error' "forensic dump failed: $_" }

                # Trigger MAXIMUM mitigation + notify user
                try {
                    & (Join-Path $CG_BIN 'crashguard-mitigate.ps1') -Tier critical -Reasons "CRISIS: $kinds"
                } catch {}

                # Audible toast
                try {
                    Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
                    $ni = New-Object System.Windows.Forms.NotifyIcon
                    $ni.Icon = [System.Drawing.SystemIcons]::Error
                    $ni.BalloonTipTitle = 'CrashGuard CRISIS'
                    $ni.BalloonTipText = "Multiple BSOD precursors stacking: $kinds. Mitigations applied. Save your work."
                    $ni.Visible = $true
                    $ni.ShowBalloonTip(15000)
                    [System.Console]::Beep(880,300)
                    [System.Console]::Beep(880,300)
                    Start-Sleep -Milliseconds 200
                    $ni.Dispose()
                } catch {}
            }
        }

        CG-Heartbeat 'crisis-correlator' @{ score = $score; recent_in_window = $recent.Count }
    } catch {
        CG-Log 'crisis-correlator' 'error' "Iter exception: $_"
    }
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSec
}
