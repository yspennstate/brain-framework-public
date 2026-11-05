# Forensic ring buffer — records full system state every 3 seconds in a rolling file.
# After any future BSOD, the last entries pre-crash are preserved on disk.
# Goal: rebuild what was happening immediately before the crash, since the OS event log
# loses 30s-2min of pre-crash entries when the kernel panics.

[CmdletBinding()]
param(
    [int]$IntervalSec = 3,
    [int]$RingSize = 600,   # 600 samples * 3s = 30 minutes of history
    [switch]$Once
)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$ringPath = Join-Path $CG_TELEMETRY 'ring.jsonl'
$ringIndex = Join-Path $CG_TELEMETRY 'ring.idx'

# We use a simple approach: write each sample to ring.jsonl as a new line,
# then trim to last $RingSize lines every 60 samples.

$nvsmi = CG-FindNvSmi
$pdh_cpu_total = '\Processor Information(_Total)\% Processor Time'

CG-Log 'ring-recorder' 'info' "Ring recorder starting PID=$PID interval=$IntervalSec ring=$RingSize samples"

$iter = 0
while ($true) {
    try {
        $iter++

        # GPU detail
        $gpu = $null
        if ($nvsmi) {
            $sw = [System.Diagnostics.Stopwatch]::StartNew()
            $r = & $nvsmi --query-gpu=temperature.gpu,utilization.gpu,utilization.memory,memory.used,memory.total,power.draw,clocks.gr,clocks.mem,fan.speed,pstate --format=csv,noheader,nounits 2>$null
            $sw.Stop()
            if ($r) {
                $v = ($r -split "`r?`n")[0] -split '\s*,\s*'
                $gpu = @{
                    temp_c=[int]$v[0]; util=[int]$v[1]; mem_util=[int]$v[2]
                    mem_used_mb=[int]$v[3]; mem_total_mb=[int]$v[4]
                    power_w=[double]$v[5]; clock_gr=[int]$v[6]; clock_mem=[int]$v[7]
                    fan=if($v[8] -match '\d'){[int]$v[8]}else{0}
                    pstate=$v[9]
                    query_ms=[int]$sw.ElapsedMilliseconds
                }
            }
        }

        # CPU/RAM
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
        $cpu = (Get-CimInstance Win32_Processor -ErrorAction SilentlyContinue | Measure-Object -Property LoadPercentage -Average).Average
        $ramFree = [int]$os.FreePhysicalMemory
        $ramTotal = [int]$os.TotalVisibleMemorySize
        $ramPct = if($ramTotal){[int](100 - $ramFree/$ramTotal*100)} else {0}

        # Top 3 CPU procs (cheap)
        $top = Get-Process | Sort-Object -Property WorkingSet64 -Descending | Select-Object -First 3 | ForEach-Object { @{n=$_.Name; mb=[int]($_.WorkingSet64/1MB)} }

        # Power state
        $power = (Get-CimInstance -Namespace 'root\WMI' -ClassName 'BatteryStatus' -ErrorAction SilentlyContinue | Select-Object -First 1)
        $onBat = if($power){-not $power.PowerOnline}else{$false}

        # Recent driver events (last 30s)
        $cutoff = (Get-Date).AddSeconds(-30)
        $drvErr = 0; $drvWarn = 0
        try {
            $evt = Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=$cutoff; ProviderName=@('nvlddmkm','Display','igfx','iaStorAV','Microsoft-Windows-WHEA-Logger')} -ErrorAction SilentlyContinue
            $drvErr = ($evt | Where-Object {$_.LevelDisplayName -in 'Error','Critical'}).Count
            $drvWarn = ($evt | Where-Object {$_.LevelDisplayName -eq 'Warning'}).Count
        } catch {}

        $sample = [PSCustomObject]@{
            ts = CG-Now
            iter = $iter
            cpu_pct = [int]$cpu
            ram_pct = $ramPct
            ram_free_mb = [int]($ramFree/1KB)
            gpu = $gpu
            top = $top
            on_battery = $onBat
            drv_err = $drvErr
            drv_warn = $drvWarn
            proc_count = (Get-Process).Count
        }
        ($sample | ConvertTo-Json -Compress -Depth 5) | Add-Content -Path $ringPath -Encoding utf8 -ErrorAction SilentlyContinue

        # Trim ring file every 60 iterations
        if ($iter % 60 -eq 0) {
            $lines = Get-Content $ringPath -ErrorAction SilentlyContinue
            if ($lines -and $lines.Count -gt $RingSize) {
                $keep = $lines | Select-Object -Last $RingSize
                Set-Content $ringPath -Value $keep -Encoding utf8 -Force
            }
        }

        CG-Heartbeat 'ring-recorder' @{ iter=$iter; cpu=$sample.cpu_pct; gpu_temp=$gpu.temp_c; gpu_util=$gpu.util }
    } catch {
        CG-Log 'ring-recorder' 'error' "Iter exception: $_"
    }
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSec
}
