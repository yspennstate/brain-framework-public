# GPU watch — polls nvidia-smi every 3s, trips tier on pre-failure signals.
# Why: prior BSODs were 0x116 TDR. nvlddmkm id=14 is post-recovery. This service watches the
# driver-readable signals (temp, power, memory, throttle reasons) so we can mitigate BEFORE TDR.

[CmdletBinding()]
param([int]$IntervalSec = 3, [switch]$Once)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$nvsmi = (Get-Command nvidia-smi -ErrorAction SilentlyContinue).Source
if (-not $nvsmi) {
    CG-Log 'gpu-watch' 'error' 'nvidia-smi not found — gpu-watch cannot start'
    return
}

$trend = Join-Path $CG_STATE 'gpu-trend.jsonl'
$tierPath = Join-Path $CG_STATE 'gpu-watch.tier'

# Thermal/power tier thresholds (NVIDIA RTX PRO 2000 Blackwell laptop = max 80W TGP, Tjmax ~95C)
$THERMAL_YELLOW = 75
$THERMAL_ORANGE = 82
$THERMAL_RED    = 88
$THERMAL_CRIT   = 92

$POWER_PCT_YELLOW = 70
$POWER_PCT_RED    = 90

$MEM_PCT_YELLOW = 80
$MEM_PCT_RED    = 95

CG-Log 'gpu-watch' 'info' "gpu-watch starting PID=$PID interval=${IntervalSec}s thermal_red=${THERMAL_RED}C"

while ($true) {
    try {
        $raw = & $nvsmi --query-gpu=temperature.gpu,power.draw,power.limit,memory.used,memory.total,utilization.gpu,clocks_throttle_reasons.active,clocks_throttle_reasons.hw_thermal_slowdown,clocks_throttle_reasons.hw_power_brake_slowdown --format=csv,noheader,nounits 2>$null
        if (-not $raw) { Start-Sleep -Seconds $IntervalSec; continue }
        $parts = $raw -split ','
        if ($parts.Count -lt 9) { Start-Sleep -Seconds $IntervalSec; continue }

        $temp = 0; [int]::TryParse($parts[0].Trim(), [ref]$temp) | Out-Null
        $power = 0.0; [double]::TryParse($parts[1].Trim(), [ref]$power) | Out-Null
        $powerLim = 0.0; [double]::TryParse($parts[2].Trim(), [ref]$powerLim) | Out-Null
        $memUsed = 0; [int]::TryParse($parts[3].Trim(), [ref]$memUsed) | Out-Null
        $memTotal = 0; [int]::TryParse($parts[4].Trim(), [ref]$memTotal) | Out-Null
        $util = 0; [int]::TryParse($parts[5].Trim(), [ref]$util) | Out-Null
        $throttleHex = $parts[6].Trim()
        $hwThermal = $parts[7].Trim()
        $hwPower   = $parts[8].Trim()

        $powerPct = if ($powerLim -gt 0) { [math]::Round($power / $powerLim * 100, 1) } else { 0 }
        $memPct = if ($memTotal -gt 0) { [math]::Round($memUsed / $memTotal * 100, 1) } else { 0 }
        $throttleVal = 0
        try { $throttleVal = [Convert]::ToInt64($throttleHex, 16) } catch {}

        # Tier evaluation — gate goes UP as severity grows
        $tier = 'green'
        $reasons = @()
        if ($temp -ge $THERMAL_YELLOW) { $tier = 'yellow'; $reasons += "temp=${temp}C>=yellow${THERMAL_YELLOW}" }
        if ($powerPct -ge $POWER_PCT_YELLOW) { if ($tier -eq 'green') { $tier = 'yellow' }; $reasons += "power=${powerPct}%" }
        if ($memPct -ge $MEM_PCT_YELLOW) { if ($tier -eq 'green') { $tier = 'yellow' }; $reasons += "mem=${memPct}%" }
        if ($temp -ge $THERMAL_ORANGE) { $tier = 'orange'; $reasons += "temp>=orange${THERMAL_ORANGE}" }
        # Only HW throttle bits matter. Ignore idle/SW power cap which are benign at idle.
        # 0x1=GPU_IDLE, 0x2=APPLICATIONS_CLOCKS, 0x4=SW_POWER_CAP, 0x100=DISPLAY_CLOCK — benign
        # 0x8=HW_SLOWDOWN, 0x40=HW_THERMAL, 0x80=HW_POWER_BRAKE — real throttle
        $hwMask = 0x8 -bor 0x40 -bor 0x80
        $hwThrottle = ($throttleVal -band $hwMask) -ne 0
        if ($hwThrottle) { if ($tier -eq 'green' -or $tier -eq 'yellow') { $tier = 'orange' }; $reasons += "hw_throttle=$throttleHex" }
        if ($temp -ge $THERMAL_RED -or $powerPct -ge $POWER_PCT_RED -or $memPct -ge $MEM_PCT_RED) {
            $tier = 'red'
            if ($temp -ge $THERMAL_RED) { $reasons += "temp>=RED${THERMAL_RED}" }
            if ($powerPct -ge $POWER_PCT_RED) { $reasons += "power>=RED${POWER_PCT_RED}%" }
            if ($memPct -ge $MEM_PCT_RED) { $reasons += "mem>=RED${MEM_PCT_RED}%" }
        }
        if ($temp -ge $THERMAL_CRIT -or $hwThermal -eq 'Active' -or $hwPower -eq 'Active') {
            $tier = 'critical'
            $reasons += "TJ_CRIT or HW_THERMAL or HW_POWER_BRAKE - GPU near TDR risk"
        }

        # Persist current tier so sentinel can OR it with CPU/system tier
        Set-Content -Path $tierPath -Value $tier -Encoding utf8 -Force

        $snapshot = [pscustomobject]@{
            timestamp   = (Get-Date).ToString('o')
            temp_c      = $temp
            power_w     = $power
            power_pct   = $powerPct
            mem_used_mb = $memUsed
            mem_pct     = $memPct
            util_pct    = $util
            throttle    = $throttleHex
            hw_thermal  = $hwThermal
            hw_power    = $hwPower
            tier        = $tier
            reasons     = ($reasons -join '|')
        }
        $line = $snapshot | ConvertTo-Json -Compress
        Add-Content -Path $trend -Value $line -Encoding utf8

        # Trim trend log to last 5000 lines once a minute (every ~20 iters at 3s)
        if ((Get-Random -Maximum 20) -eq 0) {
            $lines = Get-Content $trend -ErrorAction SilentlyContinue
            if ($lines.Count -gt 5000) {
                $keep = $lines[-5000..-1]
                Set-Content -Path $trend -Value $keep -Encoding utf8 -Force
            }
        }

        if ($tier -in @('orange','red','critical')) {
            CG-Log 'gpu-watch' 'warn' "tier=$tier temp=${temp}C power=${power}W(${powerPct}%) mem=${memPct}% throttle=$throttleHex reasons=$($reasons -join '|')"
        }

        CG-Heartbeat 'gpu-watch' @{ tier=$tier; temp_c=$temp; power_pct=$powerPct; mem_pct=$memPct; throttle=$throttleHex }
    } catch {
        CG-Log 'gpu-watch' 'error' "Iter exception: $_"
    }
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSec
}
