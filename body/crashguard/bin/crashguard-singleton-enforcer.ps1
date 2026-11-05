# Singleton enforcer — ensures only ONE instance of each CrashGuard service runs.
# Kills extras (keeps oldest). Runs every 2 minutes.

[CmdletBinding()]
param([int]$IntervalSec = 120, [switch]$Once)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$scripts = @(
    'crashguard-sentinel.ps1','crashguard-watchdog.ps1','crashguard-process-watcher.ps1',
    'crashguard-export-state.ps1','crashguard-driver-health.ps1','crashguard-pcie-watch.ps1',
    'crashguard-ring-recorder.ps1','crashguard-dedup-watcher.ps1','crashguard-power-watch.ps1',
    'crashguard-crisis-correlator.ps1','crashguard-power-guard.ps1','crashguard-wudfrd-watch.ps1',
    'crashguard-hid-watch.ps1','crashguard-update-watcher.ps1','crashguard-sleep-defender.ps1',
    'crashguard-self-heal.ps1','crashguard-log-rotate.ps1','crashguard-trend-analyzer.ps1',
    'crashguard-recovery.ps1','crashguard-pulse.ps1','crashguard-watchdog2.ps1',
    'crashguard-html-publisher.ps1','crashguard-archive.ps1','crashguard-singleton-enforcer.ps1'
)

CG-Log 'singleton-enforcer' 'info' ('Singleton enforcer starting PID=' + $PID + ' interval=' + $IntervalSec + 's services=' + $scripts.Count)

while ($true) {
    try {
        $killed = 0
        $myPid = $PID
        foreach ($s in $scripts) {
            $procs = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match [Regex]::Escape($s) } | Sort-Object CreationDate
            if ($procs -and $procs.Count -ge 2) {
                # For singleton-enforcer itself, keep $myPid; otherwise keep oldest
                if ($s -eq 'crashguard-singleton-enforcer.ps1') {
                    $kill = $procs | Where-Object { $_.ProcessId -ne $myPid }
                } else {
                    $kill = $procs | Select-Object -Skip 1
                }
                foreach ($k in $kill) {
                    try { Stop-Process -Id $k.ProcessId -Force -ErrorAction SilentlyContinue; $killed++ } catch {}
                }
                CG-Log 'singleton-enforcer' 'warn' ('Killed ' + $kill.Count + ' duplicate of ' + ($s -replace '\.ps1$',''))
            }
        }
        CG-Heartbeat 'singleton-enforcer' @{ killed_this_iter = $killed }
    } catch {
        CG-Log 'singleton-enforcer' 'error' ('Iter exception: ' + $_)
    }
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSec
}
