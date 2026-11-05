# Power-state watcher — detects rapid display on/off cycling that precedes BSODs.
# Per investigation 2026-05-27: 8 power transitions in 30 min before 7:34 crash, 4 before 6:42 crash.
# Rapid display cycling = likely TDR trigger (driver re-initialization stress).

[CmdletBinding()]
param([int]$IntervalSec = 15, [int]$WindowMin = 5, [int]$CycleThreshold = 3, [switch]$Once)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

CG-Log 'power-watch' 'info' "Power-watch starting PID=$PID interval=${IntervalSec}s window=${WindowMin}min threshold=${CycleThreshold}"

while ($true) {
    try {
        $cutoff = (Get-Date).AddMinutes(-$WindowMin)
        # id 506=display off, 507=display on, 1=power-state-change, 42=sleep, 107=resume
        $power = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-Power'; StartTime=$cutoff} -ErrorAction SilentlyContinue | Where-Object { $_.Id -in 506,507,42,107 }
        if ($power) {
            $byId = $power | Group-Object Id
            $report = ($byId | ForEach-Object { "id$($_.Name)=$($_.Count)" }) -join ' '
            $cycles = ($power | Where-Object { $_.Id -in 506,507 }).Count
            $sleeps = ($power | Where-Object { $_.Id -in 42,107 }).Count

            if ($cycles -ge $CycleThreshold) {
                CG-Log 'power-watch' 'error' "Display-cycling pattern detected: $report in last ${WindowMin}min - TDR precursor pattern"
                $entry = [PSCustomObject]@{
                    ts = CG-Now
                    kind = 'rapid-display-cycle'
                    window_min = $WindowMin
                    cycles = $cycles
                    sleeps = $sleeps
                    detail = $report
                }
                ($entry | ConvertTo-Json -Compress) | Add-Content -Path (Join-Path $CG_TELEMETRY 'spike-log.jsonl') -Encoding utf8 -ErrorAction SilentlyContinue

                # Trigger ORANGE mitigations as precursor warning
                try { & (Join-Path $CG_BIN 'crashguard-mitigate.ps1') -Tier orange -Reasons "rapid-display-cycle: $report" } catch {}
            }
            elseif ($cycles -gt 0 -or $sleeps -gt 0) {
                CG-Log 'power-watch' 'info' "Power events: $report (under threshold)"
            }
        }
        CG-Heartbeat 'power-watch' @{ last_check = (Get-Date).ToString('o') }
    } catch {
        CG-Log 'power-watch' 'error' "Iter exception: $_"
    }
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSec
}
