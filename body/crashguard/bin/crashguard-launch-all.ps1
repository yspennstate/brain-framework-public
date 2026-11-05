# One-shot launcher: ensure all CrashGuard services are running.
# Idempotent — safe to invoke from any startup hook.

[CmdletBinding()]
param()
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$psExe = (Get-Command powershell.exe).Source
$services = @(
    @{ name='sentinel';        script='crashguard-sentinel.ps1' }
    @{ name='watchdog';        script='crashguard-watchdog.ps1' }
    @{ name='process-watcher'; script='crashguard-process-watcher.ps1' }
    @{ name='export-state';    script='crashguard-export-state.ps1' }
    @{ name='driver-health';   script='crashguard-driver-health.ps1' }
    @{ name='pcie-watch';      script='crashguard-pcie-watch.ps1' }
    @{ name='ring-recorder';   script='crashguard-ring-recorder.ps1' }
    @{ name='dedup-watcher';   script='crashguard-dedup-watcher.ps1' }
    @{ name='power-watch';     script='crashguard-power-watch.ps1' }
    @{ name='crisis-correlator'; script='crashguard-crisis-correlator.ps1' }
    @{ name='power-guard';      script='crashguard-power-guard.ps1' }
    @{ name='wudfrd-watch';     script='crashguard-wudfrd-watch.ps1' }
    @{ name='hid-watch';        script='crashguard-hid-watch.ps1' }
    @{ name='update-watcher';   script='crashguard-update-watcher.ps1' }
    @{ name='sleep-defender';   script='crashguard-sleep-defender.ps1' }
    @{ name='self-heal';        script='crashguard-self-heal.ps1' }
    @{ name='log-rotate';       script='crashguard-log-rotate.ps1' }
    @{ name='trend-analyzer';   script='crashguard-trend-analyzer.ps1' }
    @{ name='recovery';         script='crashguard-recovery.ps1' }
    @{ name='pulse';            script='crashguard-pulse.ps1' }
    @{ name='watchdog2';        script='crashguard-watchdog2.ps1' }
    @{ name='html-publisher';   script='crashguard-html-publisher.ps1' }
    @{ name='archive';            script='crashguard-archive.ps1' }
    @{ name='singleton-enforcer'; script='crashguard-singleton-enforcer.ps1' }
    @{ name='export-prometheus';  script='crashguard-export-prometheus.ps1' }
    @{ name='gpu-watch';          script='crashguard-gpu-watch.ps1' }
    @{ name='gpu-trend-predictor'; script='crashguard-gpu-trend-predictor.ps1' }
    @{ name='gpu-keepalive';      script='crashguard-gpu-keepalive.py'; exe='pythonw' }
    @{ name='game-mode';          script='crashguard-game-mode.ps1' }
)

foreach ($svc in $services) {
    $procName = if ($svc.exe) { "$($svc.exe).exe" } else { 'powershell.exe' }
    $running = Get-CimInstance Win32_Process -Filter "Name='$procName'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match [Regex]::Escape($svc.script) }
    if ($running) {
        CG-Log 'launch-all' 'info' "$($svc.name) already running pid=$($running.ProcessId)"
    } else {
        $full = "$env:USERPROFILE\CrashGuard\bin\$($svc.script)"
        if ($svc.exe -eq 'pythonw') {
            $exePath = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
            $p = Start-Process -FilePath $exePath -ArgumentList '-u',$full -WindowStyle Hidden -PassThru
        } else {
            $p = Start-Process -FilePath $psExe -ArgumentList '-NoProfile','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File',$full -WindowStyle Hidden -PassThru
        }
        CG-Log 'launch-all' 'info' "Started $($svc.name) pid=$($p.Id)"
        Start-Sleep -Milliseconds 800
    }
}
Write-Output "All CrashGuard services accounted for."
