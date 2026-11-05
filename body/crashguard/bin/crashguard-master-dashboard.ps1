# Master dashboard — clears screen and shows all 19 services + sentinel + spike-tail every 5s.
# Use: powershell $env:USERPROFILE\CrashGuard\bin\crashguard-master-dashboard.ps1

[CmdletBinding()]
param([int]$RefreshSec = 5)

. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$services = @('sentinel','watchdog','process-watcher','export','driver-health','pcie-watch','ring-recorder','dedup-watcher','power-watch','crisis-correlator','power-guard','wudfrd-watch','hid-watch','update-watcher','sleep-defender','self-heal','log-rotate','trend-analyzer','recovery')

while ($true) {
    Clear-Host
    $now = Get-Date
    Write-Output ('CrashGuard Master Dashboard - ' + $now.ToString('yyyy-MM-dd HH:mm:ss'))
    Write-Output ('refresh ' + $RefreshSec + 's, Ctrl-C to exit')
    Write-Output ''

    # Services
    Write-Output 'SERVICES'
    foreach ($svc in $services) {
        $hb = Get-Content "$env:USERPROFILE\CrashGuard\state\$svc.heartbeat" -ErrorAction SilentlyContinue | ConvertFrom-Json
        if ($hb) {
            $age = [int]((Get-Date) - (Get-Date $hb.timestamp)).TotalSeconds
            $stat = if ($age -lt 60) { 'OK ' } elseif ($age -lt 360) { 'old' } else { 'STL' }
            Write-Output ('  [' + $stat + '] ' + $svc.PadRight(20) + ' pid=' + $hb.pid + ' age=' + $age + 's')
        } else {
            Write-Output ('  [MIS] ' + $svc)
        }
    }
    Write-Output ''

    # Sentinel detail
    $sntHb = Get-Content "$env:USERPROFILE\CrashGuard\state\sentinel.heartbeat" -ErrorAction SilentlyContinue | ConvertFrom-Json
    if ($sntHb -and $sntHb.last_sample) {
        $s = $sntHb.last_sample
        Write-Output ('SENTINEL  tier=' + $sntHb.tier + '  iters=' + $sntHb.iterations)
        Write-Output ('  CPU=' + $s.cpu_util_pct + '%  RAM=' + $s.ram_used_pct + '%  GPU temp=' + $s.gpu_temp_c + 'C util=' + $s.gpu_util_pct + '%  power=' + $s.gpu_power_pct + '%')
    }
    Write-Output ''

    # Test counters
    $tests = Get-Content "$env:USERPROFILE\CrashGuard\state\test-counter.txt" -ErrorAction SilentlyContinue
    $ticks = Get-Content "$env:USERPROFILE\CrashGuard\state\tick-counter.txt" -ErrorAction SilentlyContinue
    Write-Output ('TESTS  ticks=' + $ticks + '  cumulative=' + $tests)
    Write-Output ''

    # BSODs since session start
    $bsods = Get-WinEvent -FilterHashtable @{LogName='System';ID=1001;StartTime=(Get-Date '2026-05-27 15:00:00')} -ErrorAction SilentlyContinue | Where-Object { $_.Message -match 'bugcheck was:' }
    Write-Output ('BSODs since session start: ' + (@($bsods).Count))
    Write-Output ''

    # WHEA today
    $whea = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'; StartTime=(Get-Date).Date} -ErrorAction SilentlyContinue
    $lastWhea = $whea | Sort-Object TimeCreated -Descending | Select-Object -First 1
    $ageWhea = if ($lastWhea) { [int](($now - $lastWhea.TimeCreated).TotalMinutes) } else { 0 }
    Write-Output ('WHEA today: ' + (@($whea).Count) + '  last ' + $ageWhea + 'min ago')
    Write-Output ''

    # Last 5 spike entries
    Write-Output 'LAST SPIKES'
    Get-Content "$env:USERPROFILE\CrashGuard\telemetry\spike-log.jsonl" -Tail 5 -ErrorAction SilentlyContinue | ForEach-Object {
        try { $j = $_ | ConvertFrom-Json; Write-Output ('  ' + $j.ts + ' ' + $j.kind) } catch {}
    }

    Start-Sleep -Seconds $RefreshSec
}
