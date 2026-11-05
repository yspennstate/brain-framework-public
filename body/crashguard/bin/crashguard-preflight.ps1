# Preflight — single-shot user health check. Verifies services + audits + invariants.
# Use: powershell $env:USERPROFILE\CrashGuard\bin\crashguard-preflight.ps1

. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$pass = 0
$fail = 0
$warn = 0

function Check {
    param([string]$Name, [bool]$Ok, [string]$Detail, [switch]$Warning)
    if ($Warning -and -not $Ok) {
        Write-Output ('[WARN] ' + $Name + ' - ' + $Detail)
        $script:warn++
    } elseif ($Ok) {
        Write-Output ('[ ok ] ' + $Name)
        $script:pass++
    } else {
        Write-Output ('[FAIL] ' + $Name + ' - ' + $Detail)
        $script:fail++
    }
}

Write-Output '=== CrashGuard preflight ==='
Write-Output ''

# 1. Service heartbeats
$services = @('sentinel','watchdog','process-watcher','export','driver-health','pcie-watch','ring-recorder','dedup-watcher','power-watch','crisis-correlator','power-guard','wudfrd-watch','hid-watch','update-watcher','sleep-defender','self-heal','log-rotate','trend-analyzer','recovery','pulse')
foreach ($svc in $services) {
    $hbPath = "$env:USERPROFILE\CrashGuard\state\$svc.heartbeat"
    if (-not (Test-Path $hbPath)) { Check "service $svc" $false 'no heartbeat'; continue }
    $hb = Get-Content $hbPath -ErrorAction SilentlyContinue | ConvertFrom-Json
    if (-not $hb) { Check "service $svc" $false 'corrupt heartbeat'; continue }
    $age = [int]((Get-Date) - (Get-Date $hb.timestamp)).TotalSeconds
    # log-rotate is intentionally slow (300s cycle); accept up to 360s
    $threshold = if ($svc -in @('log-rotate','trend-analyzer','recovery')) { 360 } else { 120 }
    Check "service $svc" ($age -lt $threshold) ("age=$age" + 's')
}

Write-Output ''
# 2. Power plan check
$active = (powercfg /getactivescheme 2>$null).Trim()
Check 'power scheme High-performance' ($active -match '8c5e7fda') ('scheme=' + $active)

$ac = (powercfg /query SCHEME_CURRENT SUB_VIDEO VIDEOIDLE | Select-String 'AC Power Setting Index').ToString().Trim()
Check 'display timeout AC = never' ($ac -match '0x00000000') $ac

# 3. Sentinel state
$snt = Get-Content "$env:USERPROFILE\CrashGuard\state\sentinel.heartbeat" -ErrorAction SilentlyContinue | ConvertFrom-Json
if ($snt) {
    Check 'sentinel tier <= orange' ($snt.tier -in @('green','yellow','orange')) ('tier=' + $snt.tier)
    if ($snt.last_sample) {
        Check 'GPU temp < 80C' ($snt.last_sample.gpu_temp_c -lt 80) ('temp=' + $snt.last_sample.gpu_temp_c + 'C')
    }
}

# 4. No BSODs since session start
$bsods = Get-WinEvent -FilterHashtable @{LogName='System';ID=1001;StartTime=(Get-Date '2026-05-27 15:00:00')} -ErrorAction SilentlyContinue | Where-Object { $_.Message -match 'bugcheck was:' }
Check 'no new BSODs since session start' (-not $bsods) ('count=' + (@($bsods).Count))

# 5. Quick audit
$audit = & "$env:USERPROFILE\CrashGuard\audits\audit-runner.ps1" -Rounds 200 -Seed (Get-Random) 2>&1 | Select-String 'Pass rate' | Select-Object -First 1
Check 'audit-runner 200-round pass rate 100%' ($audit -match 'Pass rate: 100\.0%') $audit.ToString()

# 6. Recent WHEA events
$wheaToday = (Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'; StartTime=(Get-Date).Date} -ErrorAction SilentlyContinue).Count
Check -Warning 'WHEA events today <= 5' ($wheaToday -le 5) ('count=' + $wheaToday)

Write-Output ''
Write-Output '=== Summary ==='
Write-Output ("pass: " + $pass + "  fail: " + $fail + "  warn: " + $warn)
if ($fail -gt 0) { Write-Output 'PREFLIGHT FAIL'; exit 1 } else { Write-Output 'PREFLIGHT OK'; exit 0 }
