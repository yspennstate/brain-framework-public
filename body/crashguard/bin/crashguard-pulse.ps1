# Pulse — emits one structured log line per minute summarizing state.
# Goal: grep-friendly chronological record. Use for "did we crash today?" answers.

[CmdletBinding()]
param([int]$IntervalSec = 60, [switch]$Once)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$pulseLog = Join-Path $CG_LOGS 'pulse.log'
$sessionStart = Get-Date '2026-05-27 15:00:00'

CG-Log 'pulse' 'info' ('Pulse starting PID=' + $PID + ' interval=' + $IntervalSec + 's')

while ($true) {
    try {
        $snt = Get-Content (Join-Path $CG_STATE 'sentinel.heartbeat') -ErrorAction SilentlyContinue | ConvertFrom-Json
        $bsods = @(Get-WinEvent -FilterHashtable @{LogName='System';ID=1001;StartTime=$sessionStart} -ErrorAction SilentlyContinue | Where-Object { $_.Message -match 'bugcheck was:' })
        $whea = @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'; StartTime=(Get-Date).Date} -ErrorAction SilentlyContinue)
        $lastWhea = $whea | Sort-Object TimeCreated -Descending | Select-Object -First 1
        $minSinceWhea = if ($lastWhea) { [int](((Get-Date) - $lastWhea.TimeCreated).TotalMinutes) } else { 99999 }

        $tier = if ($snt) { $snt.tier } else { '?' }
        $cpu = if ($snt -and $snt.last_sample) { $snt.last_sample.cpu_util_pct } else { '?' }
        $ram = if ($snt -and $snt.last_sample) { $snt.last_sample.ram_used_pct } else { '?' }
        $gpuT = if ($snt -and $snt.last_sample) { $snt.last_sample.gpu_temp_c } else { '?' }
        $gpuU = if ($snt -and $snt.last_sample) { $snt.last_sample.gpu_util_pct } else { '?' }

        $line = ('PULSE ' + (CG-Now) + ' tier=' + $tier + ' cpu=' + $cpu + '% ram=' + $ram + '% gpu=' + $gpuT + 'C/' + $gpuU + '% bsod_today=' + $bsods.Count + ' whea_today=' + $whea.Count + ' min_since_whea=' + $minSinceWhea)
        Add-Content -Path $pulseLog -Value $line -Encoding utf8 -ErrorAction SilentlyContinue

        CG-Heartbeat 'pulse' @{ bsods_today=$bsods.Count; whea_today=$whea.Count; min_since_whea=$minSinceWhea; tier=$tier }
    } catch {
        CG-Log 'pulse' 'error' ('Iter exception: ' + $_)
    }
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSec
}
