# Generates session-summary.json — single-file structured snapshot for downstream tooling.

. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$sessionStart = Get-Date '2026-05-27 15:00:00'
$now = Get-Date

$services = @('sentinel','watchdog','process-watcher','export','driver-health','pcie-watch','ring-recorder','dedup-watcher','power-watch','crisis-correlator','power-guard','wudfrd-watch','hid-watch','update-watcher','sleep-defender','self-heal','log-rotate','trend-analyzer','recovery','pulse','watchdog2')

$serviceStatus = foreach ($svc in $services) {
    $hb = Get-Content "$env:USERPROFILE\CrashGuard\state\$svc.heartbeat" -ErrorAction SilentlyContinue | ConvertFrom-Json
    [PSCustomObject]@{
        name = $svc
        present = ($null -ne $hb)
        pid = if ($hb) { $hb.pid } else { 0 }
        age_sec = if ($hb) { [int](($now - (Get-Date $hb.timestamp)).TotalSeconds) } else { -1 }
    }
}

$snt = Get-Content "$env:USERPROFILE\CrashGuard\state\sentinel.heartbeat" -ErrorAction SilentlyContinue | ConvertFrom-Json
$bsods = @(Get-WinEvent -FilterHashtable @{LogName='System';ID=1001;StartTime=$sessionStart} -ErrorAction SilentlyContinue | Where-Object { $_.Message -match 'bugcheck was:' })
$whea = @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'; StartTime=(Get-Date).Date} -ErrorAction SilentlyContinue)
$lastWhea = $whea | Sort-Object TimeCreated -Descending | Select-Object -First 1
$tests = [int](Get-Content "$env:USERPROFILE\CrashGuard\state\test-counter.txt" -ErrorAction SilentlyContinue)
$ticks = [int](Get-Content "$env:USERPROFILE\CrashGuard\state\tick-counter.txt" -ErrorAction SilentlyContinue)

$summary = [PSCustomObject]@{
    generated_at = $now
    session_start = $sessionStart
    session_duration_min = [int](($now - $sessionStart).TotalMinutes)
    services = @{
        total = $services.Count
        healthy = (@($serviceStatus | Where-Object { $_.present -and $_.age_sec -lt 360 }).Count)
        stale = (@($serviceStatus | Where-Object { $_.present -and $_.age_sec -ge 360 }).Count)
        missing = (@($serviceStatus | Where-Object { -not $_.present }).Count)
        detail = $serviceStatus
    }
    sentinel = if ($snt) { @{
        tier = $snt.tier
        iters = $snt.iterations
        last_sample = $snt.last_sample
    }} else { $null }
    crashes = @{
        bsods_since_session_start = $bsods.Count
        whea_today = $whea.Count
        last_whea = if ($lastWhea) { $lastWhea.TimeCreated } else { $null }
        min_since_last_whea = if ($lastWhea) { [int](($now - $lastWhea.TimeCreated).TotalMinutes) } else { 99999 }
    }
    audits = @{
        watchdog_ticks = $ticks
        cumulative_tests = $tests
    }
    interventions_applied = @(
        'power scheme = High performance (was Power saver)'
        'display timeout AC = never (was 60s)'
        'display timeout DC = never (was 30s)'
        'standby AC = never'
        'hibernate AC = never'
        'sleep-defender SetThreadExecutionState ES_AWAYMODE running'
        '24 duplicate worker processes SUSPENDED (3.28 GB held)'
    )
    pending_user_actions = @(
        'Reinstall HID/headset driver via Lenovo Vantage'
        'Disconnect unstable wireless device (esp BT headset, Logitech receiver)'
        'sfc /scannow from admin PowerShell'
        'Optional: run kill-suspended-dupes.ps1 -Confirm to free 3.28 GB'
        'Optional: run harden-os.ps1 from admin PowerShell for TDR registry tweaks'
    )
    root_cause_chain = @(
        'Wireless HID device disconnect'
        'Kernel-PnP id=219 WUDFRd reload-failure'
        'PCIe stress on Bus 0x1:0x0:0x0 (NVIDIA GPU)'
        'WHEA-Logger id=17 corrected error'
        'nvlddmkm id=14 TDR (silent recovery) or fatal (BSOD 0x116)'
    )
}

$out = Join-Path $CG_TELEMETRY 'session-summary.json'
$summary | ConvertTo-Json -Depth 8 | Out-File -FilePath $out -Encoding utf8 -Force
Write-Output ('session-summary.json written to ' + $out)
Write-Output ''
Write-Output ('services: ' + $summary.services.healthy + ' healthy / ' + $summary.services.stale + ' stale / ' + $summary.services.missing + ' missing')
Write-Output ('sentinel tier: ' + $snt.tier)
Write-Output ('bsods since session start: ' + $summary.crashes.bsods_since_session_start)
Write-Output ('whea today: ' + $summary.crashes.whea_today + ' (last ' + $summary.crashes.min_since_last_whea + ' min ago)')
Write-Output ('tests: ' + $summary.audits.cumulative_tests + ' over ' + $summary.audits.watchdog_ticks + ' watchdog ticks')
