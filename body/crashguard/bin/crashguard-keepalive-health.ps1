# CrashGuard keepalive effectiveness snapshot + fast supervisor.
# Each run: (1) ensures the anti-RTD3 GPU keepalive is alive (relaunch if not),
# (2) records a one-line health sample (keepalive up?, GPU resident?, PCIe AER rate),
# building a time-series so we can SEE whether the idle-TDR fix is holding.
# Cheap and self-contained; safe to run on a short cron or on demand.
[CmdletBinding()]
param([switch]$Quiet)

$ErrorActionPreference = 'Continue'
$BIN   = "$env:USERPROFILE\CrashGuard\bin"
$LOGS  = "$env:USERPROFILE\CrashGuard\logs"
$STATE = "$env:USERPROFILE\CrashGuard\state"
$KEEPALIVE_PY = Join-Path $BIN 'crashguard-gpu-keepalive.py'
$jsonl = Join-Path $LOGS 'keepalive-health.jsonl'

# 1) Ensure keepalive alive (singleton-safe: it self-exits if a dup is already running)
$ka = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -EA SilentlyContinue |
    Where-Object { $_.CommandLine -match 'crashguard-gpu-keepalive' } | Select-Object -First 1
$relaunched = $false
if (-not $ka) {
    try {
        $pyw = (Get-Command pythonw.exe -EA Stop).Source
        Start-Process -FilePath $pyw -ArgumentList '-u', $KEEPALIVE_PY -WindowStyle Hidden | Out-Null
        $relaunched = $true
    } catch {}
}

# 2) GPU residency (memory.used > 0 => device on the bus, not powered off)
$memUsed = -1; $pstate = '?'; $powerW = -1
try {
    $nv = (Get-Command nvidia-smi -EA Stop).Source
    $row = (& $nv --query-gpu=memory.used,pstate,power.draw --format=csv,noheader,nounits) -split '\s*,\s*'
    $memUsed = [int]$row[0]; $pstate = $row[1].Trim(); $powerW = [double]$row[2]
} catch {}

# 3) PCIe AER rate, last 60 min (WHEA-Logger id 17)
$aer60 = -1
try {
    $aer60 = @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'; Id=17; StartTime=(Get-Date).AddMinutes(-60)} -EA SilentlyContinue).Count
} catch { $aer60 = 0 }

# storm state (written by pcie-watch)
$storm = $false
try { $s = Get-Content (Join-Path $STATE 'pcie-aer-storm.json') -EA Stop | ConvertFrom-Json; $storm = [bool]$s.active } catch {}

$kaAlive = [bool]($ka -or $relaunched)
$resident = ($memUsed -gt 0)
$rec = [ordered]@{
    ts = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    keepalive_alive = $kaAlive
    keepalive_relaunched = $relaunched
    gpu_resident = $resident
    gpu_mem_used_mb = $memUsed
    pstate = $pstate
    power_w = $powerW
    aer_last_60min = $aer60
    aer_storm = $storm
}
($rec | ConvertTo-Json -Compress) | Add-Content -Path $jsonl -Encoding utf8 -EA SilentlyContinue

if (-not $Quiet) {
    $flag = if ($kaAlive -and $resident -and -not $storm) { 'OK' } else { 'ATTENTION' }
    "[$flag] keepalive_alive=$kaAlive resident=$resident mem=${memUsed}MB pstate=$pstate aer60=$aer60 storm=$storm relaunched=$relaunched"
}
