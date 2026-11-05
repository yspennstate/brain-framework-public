# Power-plan guardian — keeps the system on High-performance + display-never settings.
# Windows occasionally reverts these (Windows Update, Lenovo Vantage, group policy, user-action via Lenovo apps).
# This service re-applies on every check.

[CmdletBinding()]
param([int]$IntervalSec = 60, [switch]$Once)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$HP = '8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c'  # High performance scheme GUID

CG-Log 'power-guard' 'info' "Power guardian starting PID=$PID interval=${IntervalSec}s"

while ($true) {
    try {
        $changed = $false
        $active = (powercfg /getactivescheme 2>$null)
        if ($active -notmatch $HP) {
            CG-Log 'power-guard' 'warn' "Power scheme drifted (was: $($active.Trim())). Re-applying High performance."
            powercfg /setactive $HP 2>$null | Out-Null
            $changed = $true
        }

        # Check display timeouts
        $ac = (powercfg /query SCHEME_CURRENT SUB_VIDEO VIDEOIDLE | Select-String 'AC Power Setting Index').ToString()
        $dc = (powercfg /query SCHEME_CURRENT SUB_VIDEO VIDEOIDLE | Select-String 'DC Power Setting Index').ToString()
        if ($ac -notmatch '0x00000000') {
            CG-Log 'power-guard' 'warn' "Display AC timeout drifted ($ac). Resetting to 0."
            powercfg /change monitor-timeout-ac 0 2>$null | Out-Null
            $changed = $true
        }
        if ($dc -notmatch '0x00000000') {
            CG-Log 'power-guard' 'warn' "Display DC timeout drifted ($dc). Resetting to 0."
            powercfg /change monitor-timeout-dc 0 2>$null | Out-Null
            $changed = $true
        }

        # Standby/hibernate AC
        powercfg /change standby-timeout-ac 0 2>$null | Out-Null
        powercfg /change hibernate-timeout-ac 0 2>$null | Out-Null

        if ($changed) {
            $entry = [PSCustomObject]@{
                ts = CG-Now
                kind = 'power-plan-reapplied'
                action = 'Power-guard reset settings due to drift'
            }
            ($entry | ConvertTo-Json -Compress) | Add-Content -Path (Join-Path $CG_TELEMETRY 'spike-log.jsonl') -Encoding utf8 -ErrorAction SilentlyContinue
        }

        CG-Heartbeat 'power-guard' @{ scheme_ok = (-not $changed); changes_this_iter = $changed }
    } catch {
        CG-Log 'power-guard' 'error' "Iter exception: $_"
    }
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSec
}
