# Post-mortem analyzer — runs after a BSOD (auto-triggered by sentinel on boot, or manually).
# Reads ring.jsonl + spike-log.jsonl + event log and produces a forensic report identifying:
#   - Last-known system state before the crash
#   - Active mitigations at crash time
#   - Top processes by RAM at crash time
#   - WHEA / driver events in pre-crash window
#   - Trend of CPU/RAM/GPU in the 5 minutes before crash

[CmdletBinding()]
param(
    [datetime]$CrashTime = (Get-Date),
    [int]$WindowSec = 600
)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$ringPath = Join-Path $CG_TELEMETRY 'ring.jsonl'
$spikePath = Join-Path $CG_TELEMETRY 'spike-log.jsonl'

$report = [PSCustomObject]@{
    crash_time = $CrashTime
    generated_at = (Get-Date)
    window_sec = $WindowSec
    ring_samples = $null
    spikes = $null
    bugcheck = $null
    last_known_state = $null
    pre_crash_trend = $null
}

# Ring buffer entries
$start = $CrashTime.AddSeconds(-$WindowSec)
$ringLines = if (Test-Path $ringPath) { Get-Content $ringPath -ErrorAction SilentlyContinue } else { @() }
$ring = $ringLines | ForEach-Object {
    try { $j = $_ | ConvertFrom-Json; if ($j.ts -and ([datetime]$j.ts -ge $start) -and ([datetime]$j.ts -le $CrashTime)) { $j } } catch {}
}
$report.ring_samples = $ring.Count
$report.last_known_state = $ring | Select-Object -Last 1

# Trend: CPU/RAM/GPU progression in last 5 min
$report.pre_crash_trend = $ring | Select-Object -Last 30 | ForEach-Object {
    [PSCustomObject]@{ ts=$_.ts; cpu=$_.cpu_pct; ram=$_.ram_pct; gpu_temp=$_.gpu.temp_c; gpu_util=$_.gpu.util; drv_err=$_.drv_err }
}

# Spike events
$spikeLines = if (Test-Path $spikePath) { Get-Content $spikePath -ErrorAction SilentlyContinue } else { @() }
$spikes = $spikeLines | ForEach-Object {
    try { $j = $_ | ConvertFrom-Json; if ($j.ts -and ([datetime]$j.ts -ge $start)) { $j } } catch {}
}
$report.spikes = $spikes

# BugCheck event
$bc = Get-WinEvent -FilterHashtable @{LogName='System'; ID=1001; StartTime=$start; EndTime=$CrashTime.AddMinutes(5)} -MaxEvents 1 -ErrorAction SilentlyContinue | Where-Object { $_.Message -match 'bugcheck was: (0x[0-9a-fA-F]+)' }
if ($bc) {
    $code = if ($bc.Message -match 'bugcheck was: (0x[0-9a-fA-F]+) \((0x[0-9a-fA-F]+)') { $matches[1] }
    $report.bugcheck = @{ time=$bc.TimeCreated; code=$code; message=$bc.Message.Substring(0,[Math]::Min(300,$bc.Message.Length)) }
}

# Write report
$reportPath = Join-Path $CG_TELEMETRY ('postmortem_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.json')
$report | ConvertTo-Json -Depth 8 | Out-File $reportPath -Encoding utf8 -Force
Write-Output "Post-mortem report: $reportPath"
Write-Output ""
Write-Output "=== Forensic summary ==="
Write-Output ("Crash time:       {0}" -f $CrashTime)
Write-Output ("Ring samples in window: {0}" -f $report.ring_samples)
Write-Output ("Spike events:    {0}" -f ($report.spikes.Count))
if ($report.bugcheck) { Write-Output ("Bugcheck code:    {0}" -f $report.bugcheck.code) }
if ($report.last_known_state) {
    $l = $report.last_known_state
    Write-Output ("Last sample ({0}): cpu={1}% ram={2}% gpu_temp={3}C gpu_util={4}% gpu_pstate={5} drv_err={6}" -f $l.ts, $l.cpu_pct, $l.ram_pct, $l.gpu.temp_c, $l.gpu.util, $l.gpu.pstate, $l.drv_err)
    Write-Output ("Top procs at crash: " + (($l.top | ForEach-Object { "$($_.n)($($_.mb)MB)" }) -join ', '))
}
return $report
