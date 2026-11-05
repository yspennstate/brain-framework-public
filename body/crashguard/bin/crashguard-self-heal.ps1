# Self-heal — checks all 15+ CrashGuard services and auto-restarts any missing/stale ones.
# Why: watchdog.ps1 only watches the sentinel. Other services can die silently (e.g., sleep-defender,
# pcie-watch). This service guarantees that any service in the canonical list comes back if it dies.

[CmdletBinding()]
param([int]$IntervalSec = 30, [int]$StaleSec = 120, [switch]$Once)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

# Robustly compute heartbeat age. Tolerates missing/null 'timestamp', alternate
# 'ts' key, or unparseable JSON by falling back to the file's last-write time.
# Without this, a single bad heartbeat threw and aborted the whole heal iteration.
function CG-HeartbeatAgeSec {
    param([string]$Path)
    try {
        $hb = Get-Content $Path -ErrorAction Stop | ConvertFrom-Json
        $ts = $null
        if ($hb -and $hb.timestamp) { $ts = $hb.timestamp } elseif ($hb -and $hb.ts) { $ts = $hb.ts }
        if ($ts) { return ((Get-Date) - (Get-Date $ts)).TotalSeconds }
    } catch {}
    try { return ((Get-Date) - (Get-Item $Path -ErrorAction Stop).LastWriteTime).TotalSeconds } catch {}
    return 999999
}

$services = @(
    @{ name='sentinel';          script='crashguard-sentinel.ps1' }
    @{ name='watchdog';          script='crashguard-watchdog.ps1' }
    @{ name='process-watcher';   script='crashguard-process-watcher.ps1' }
    @{ name='export';            script='crashguard-export-state.ps1' }
    @{ name='driver-health';     script='crashguard-driver-health.ps1' }
    @{ name='pcie-watch';        script='crashguard-pcie-watch.ps1' }
    @{ name='ring-recorder';     script='crashguard-ring-recorder.ps1' }
    @{ name='dedup-watcher';     script='crashguard-dedup-watcher.ps1' }
    @{ name='power-watch';       script='crashguard-power-watch.ps1' }
    @{ name='crisis-correlator'; script='crashguard-crisis-correlator.ps1' }
    @{ name='power-guard';       script='crashguard-power-guard.ps1' }
    @{ name='wudfrd-watch';      script='crashguard-wudfrd-watch.ps1' }
    @{ name='hid-watch';         script='crashguard-hid-watch.ps1' }
    @{ name='update-watcher';    script='crashguard-update-watcher.ps1' }
    @{ name='sleep-defender';    script='crashguard-sleep-defender.ps1' }
    @{ name='log-rotate';        script='crashguard-log-rotate.ps1' }
    @{ name='trend-analyzer';    script='crashguard-trend-analyzer.ps1' }
    @{ name='recovery';          script='crashguard-recovery.ps1' }
    @{ name='pulse';             script='crashguard-pulse.ps1' }
    @{ name='watchdog2';         script='crashguard-watchdog2.ps1' }
    @{ name='html-publisher';    script='crashguard-html-publisher.ps1' }
    @{ name='archive';           script='crashguard-archive.ps1' }
    @{ name='singleton-enforcer'; script='crashguard-singleton-enforcer.ps1' }
    @{ name='gpu-watch';         script='crashguard-gpu-watch.ps1' }
    @{ name='gpu-trend-predictor'; script='crashguard-gpu-trend-predictor.ps1' }
    @{ name='gpu-keepalive';     script='crashguard-gpu-keepalive.py'; exe='pythonw' }
    @{ name='game-mode';         script='crashguard-game-mode.ps1' }
)

CG-Log 'self-heal' 'info' "Self-heal starting PID=$PID interval=${IntervalSec}s stale-threshold=${StaleSec}s services=$($services.Count)"

while ($true) {
    try {
        $restarted = 0
        $psExe = (Get-Command powershell.exe).Source
        foreach ($svc in $services) {
            $hbPath = Join-Path $CG_STATE ($svc.name + '.heartbeat')
            $needsRestart = $false
            $reason = ''
            # First check: is the actual script process running? If yes, don't restart even if heartbeat is stale.
            $scriptName = $svc.script
            $procName = if ($svc.exe) { "$($svc.exe).exe" } else { 'powershell.exe' }
            $alive = Get-CimInstance Win32_Process -Filter "Name='$procName'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match [Regex]::Escape($scriptName) }
            if ($alive) {
                # Process exists — only restart if STRONGLY stale. Some services have long cycles (archive=3600s).
                $slowCycle = @('archive','log-rotate','trend-analyzer','recovery')
                $threshold = if ($svc.name -in $slowCycle) { 4000 } else { $StaleSec * 3 }
                $age = CG-HeartbeatAgeSec $hbPath
                if ($age -gt $threshold) { $needsRestart = $true; $reason = "process alive but heartbeat very stale $([int]$age)s (threshold $threshold)" }
            } else {
                # No process running — definitely needs restart.
                if (-not (Test-Path $hbPath)) { $needsRestart = $true; $reason = 'no process, no heartbeat' }
                else {
                    $age = CG-HeartbeatAgeSec $hbPath
                    $needsRestart = $true; $reason = "no process running (heartbeat $([int]$age)s old)"
                }
            }
            if ($needsRestart) {
                # First: DEDUP. Kill any existing instances of this script so we don't spawn an Nth duplicate.
                $existing = Get-CimInstance Win32_Process -Filter "Name='$procName'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match [Regex]::Escape($svc.script) }
                if ($existing) {
                    foreach ($e in $existing) {
                        try { Stop-Process -Id $e.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
                    }
                    CG-Log 'self-heal' 'warn' "Dedup-killed $($existing.Count) existing instance(s) of $($svc.name) before respawn"
                    Start-Sleep -Milliseconds 500
                }
                $full = "$env:USERPROFILE\CrashGuard\bin\$($svc.script)"
                try {
                    if ($svc.exe -eq 'pythonw') {
                        $exePath = (Get-Command pythonw.exe -ErrorAction Stop).Source
                        $p = Start-Process -FilePath $exePath -ArgumentList '-u',$full -WindowStyle Hidden -PassThru -ErrorAction Stop
                    } else {
                        $p = Start-Process -FilePath $psExe -ArgumentList '-NoProfile','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File',$full -WindowStyle Hidden -PassThru -ErrorAction Stop
                    }
                    $restarted++
                    CG-Log 'self-heal' 'warn' "Restarted $($svc.name) pid=$($p.Id) reason=$reason"
                } catch { CG-Log 'self-heal' 'error' "Failed to restart $($svc.name): $_" }
            }
        }
        CG-Heartbeat 'self-heal' @{ services_checked = $services.Count; restarted_this_iter = $restarted }
    } catch {
        CG-Log 'self-heal' 'error' "Iter exception: $_"
    }
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSec
}
