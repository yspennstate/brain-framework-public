# Watchdog-2 — watches self-heal specifically. Self-heal watches everything else, but if it dies,
# nothing else watches IT (except the original sentinel-watchdog). This closes the loop.

[CmdletBinding()]
param([int]$IntervalSec = 45, [int]$StaleSec = 90, [switch]$Once)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$targetPath = Join-Path $CG_STATE 'self-heal.heartbeat'

CG-Log 'watchdog2' 'info' ('Watchdog-2 starting PID=' + $PID + ' watching self-heal')

while ($true) {
    try {
        $needRestart = $false; $reason = ''
        if (-not (Test-Path $targetPath)) { $needRestart = $true; $reason = 'no heartbeat' }
        else {
            $hb = Get-Content $targetPath -ErrorAction SilentlyContinue | ConvertFrom-Json
            if ($hb) {
                $age = ((Get-Date) - (Get-Date $hb.timestamp)).TotalSeconds
                if ($age -gt $StaleSec) {
                    $proc = Get-Process -Id $hb.pid -ErrorAction SilentlyContinue
                    if (-not $proc) { $needRestart = $true; $reason = 'pid dead, hb stale ' + [int]$age + 's' }
                    elseif ($age -gt $StaleSec * 2) { $needRestart = $true; $reason = 'hb very stale ' + [int]$age + 's' }
                }
            } else { $needRestart = $true; $reason = 'unparseable hb' }
        }

        if ($needRestart) {
            CG-Log 'watchdog2' 'warn' ('Self-heal needs restart: ' + $reason)
            # Kill any existing self-heal instances first (avoid dups)
            Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'crashguard-self-heal' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
            Start-Sleep -Milliseconds 500
            $psExe = (Get-Command powershell.exe).Source
            Start-Process -FilePath $psExe -ArgumentList '-NoProfile','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File',"$env:USERPROFILE\CrashGuard\bin\crashguard-self-heal.ps1" -WindowStyle Hidden | Out-Null
            CG-Log 'watchdog2' 'warn' 'Self-heal restarted by watchdog2'
        }
        CG-Heartbeat 'watchdog2' @{ self_heal_needed_restart = $needRestart }
    } catch {
        CG-Log 'watchdog2' 'error' ('Iter exception: ' + $_)
    }
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSec
}
