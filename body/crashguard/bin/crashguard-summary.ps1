# One-shot status report — human-readable summary of CrashGuard state.
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$now = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
Write-Output '================================================================'
Write-Output ('CrashGuard summary report - ' + $now)
Write-Output '================================================================'
Write-Output ''

Write-Output '## Services'
$services = @('sentinel','watchdog','process-watcher','export','driver-health','pcie-watch','ring-recorder','dedup-watcher','power-watch','crisis-correlator','power-guard','wudfrd-watch','hid-watch','update-watcher','sleep-defender')
$alive = 0
$stale = 0
$missing = 0
foreach ($svc in $services) {
    $hb = Get-Content "$env:USERPROFILE\CrashGuard\state\$svc.heartbeat" -ErrorAction SilentlyContinue | ConvertFrom-Json
    if (-not $hb) { $missing++; Write-Output ('  ' + $svc + ' MISSING'); continue }
    $age = [int]((Get-Date) - (Get-Date $hb.timestamp)).TotalSeconds
    if ($age -gt 120) { $stale++; Write-Output ('  ' + $svc + ' STALE ' + $age + 's') }
    else { $alive++; Write-Output ('  ' + $svc + ' ok ' + $age + 's') }
}
$total = $services.Count
Write-Output ('Summary: ' + $alive + ' alive, ' + $stale + ' stale, ' + $missing + ' missing of ' + $total + ' total')
Write-Output ''

Write-Output '## Sentinel state'
$snt = Get-Content "$env:USERPROFILE\CrashGuard\state\sentinel.heartbeat" -ErrorAction SilentlyContinue | ConvertFrom-Json
if ($snt) {
    Write-Output ('  tier=' + $snt.tier + ' iters=' + $snt.iterations + ' pid=' + $snt.pid)
    if ($snt.last_sample) {
        $s = $snt.last_sample
        Write-Output ('  cpu=' + $s.cpu_util_pct + '% ram=' + $s.ram_used_pct + '% gpu_temp=' + $s.gpu_temp_c + 'C gpu_util=' + $s.gpu_util_pct + '%')
    }
}
Write-Output ''

Write-Output '## Last 10 spike-log entries'
$spike = Get-Content "$env:USERPROFILE\CrashGuard\telemetry\spike-log.jsonl" -ErrorAction SilentlyContinue
$last10 = $spike | Select-Object -Last 10
foreach ($l in $last10) {
    try { $j = $l | ConvertFrom-Json; Write-Output ('  ' + $j.ts + ' ' + $j.kind) } catch {}
}
Write-Output ('Total spike entries: ' + $spike.Count)
Write-Output ''

Write-Output '## BSODs since CrashGuard session start (2026-05-27 15:00)'
$bsods = Get-WinEvent -FilterHashtable @{LogName='System';ID=1001;StartTime=(Get-Date '2026-05-27 15:00:00')} -ErrorAction SilentlyContinue | Where-Object { $_.Message -match 'bugcheck was:' }
if ($bsods) { foreach ($b in $bsods) { Write-Output ('  ' + $b.TimeCreated) } } else { Write-Output '  (none)' }
Write-Output ''

Write-Output '## Audit/test counters'
$tests = Get-Content "$env:USERPROFILE\CrashGuard\state\test-counter.txt" -ErrorAction SilentlyContinue
$ticks = Get-Content "$env:USERPROFILE\CrashGuard\state\tick-counter.txt" -ErrorAction SilentlyContinue
Write-Output ('  watchdog ticks: ' + $ticks)
Write-Output ('  cumulative synthetic tests: ' + $tests)
Write-Output ''

Write-Output '## Root cause chain (per investigation 2026-05-27)'
Write-Output '  Wireless HID device disconnect -> WUDFRd id=219 reload fail -> PCIe stress'
Write-Output '  -> WHEA-Logger id=17 corrected error (Bus 0x1:0x0:0x0 = NVIDIA GPU slot)'
Write-Output '  -> nvlddmkm id=14 TDR-recovered (silent) OR TDR-fatal (BSOD 0x116)'
Write-Output ''

Write-Output '## Recommended user actions'
Write-Output '  1. Reinstall HID/touchpad/headset drivers via Lenovo Vantage'
Write-Output '  2. OR disconnect the unstable wireless device (esp BT headset / Logitech receiver)'
Write-Output '  3. OR run sfc /scannow from an elevated PowerShell'
Write-Output '  4. Consider rolling back NVIDIA driver 595.97 if WHEA storm persists'
Write-Output ''

Write-Output '## Power plan (verified)'
$active = (powercfg /getactivescheme 2>$null)
Write-Output ('  ' + $active.Trim())
$ac = (powercfg /query SCHEME_CURRENT SUB_VIDEO VIDEOIDLE | Select-String 'AC Power Setting Index').ToString().Trim()
$dc = (powercfg /query SCHEME_CURRENT SUB_VIDEO VIDEOIDLE | Select-String 'DC Power Setting Index').ToString().Trim()
Write-Output ('  Display timeout AC: ' + $ac)
Write-Output ('  Display timeout DC: ' + $dc)
