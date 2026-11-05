# Watchdog of the sentinel: ensures it stays alive. Run as its own scheduled task.

[CmdletBinding()]
param(
    [int]$CheckIntervalSec = 30,
    [int]$MaxStaleSec = 10,
    [switch]$Once
)
# Per user 2026-05-27: watchdog cadence tightened from default 30s towards 15s for faster recovery.
if ($PSBoundParameters.Count -eq 0) { $CheckIntervalSec = 15; $MaxStaleSec = 8 }

$ErrorActionPreference = 'Continue'
. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'crashguard-common.ps1')

$sentinelScript = Join-Path $CG_BIN 'crashguard-sentinel.ps1'
$heartbeat = Join-Path $CG_STATE 'sentinel.heartbeat'

function Start-Sentinel {
    CG-Log 'watchdog' 'warn' "Starting sentinel"
    $psExe = (Get-Command powershell.exe).Source
    Start-Process -FilePath $psExe `
        -ArgumentList @('-NoProfile','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File', $sentinelScript) `
        -WindowStyle Hidden | Out-Null
    Start-Sleep -Seconds 3
}

while ($true) {
    try {
        $alive = $false
        if (Test-Path $heartbeat) {
            $hb = CG-LoadJson $heartbeat
            if ($hb) {
                $ageSec = ((Get-Date) - [datetime]::Parse($hb.timestamp)).TotalSeconds
                if ($ageSec -le $MaxStaleSec) {
                    # Also verify the process exists
                    $p = Get-Process -Id $hb.pid -ErrorAction SilentlyContinue
                    if ($p) { $alive = $true }
                }
            }
        }
        if (-not $alive) {
            CG-Log 'watchdog' 'warn' "Sentinel not alive - restarting"
            Start-Sentinel
        }
        CG-Heartbeat 'watchdog' @{ sentinel_alive = $alive }
    } catch {
        CG-Log 'watchdog' 'error' "Watchdog iter exception: $_"
    }
    if ($Once) { break }
    Start-Sleep -Seconds $CheckIntervalSec
}
