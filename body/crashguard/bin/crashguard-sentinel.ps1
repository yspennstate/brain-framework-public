# CrashGuard Sentinel — main polling loop.
# Reads GPU/CPU/RAM telemetry, computes a Tier, fires mitigations via crashguard-mitigate.ps1.

[CmdletBinding()]
param(
    [switch]$Once,      # Single iteration (for tests)
    [switch]$DryRun,    # Don't actually mitigate, just log decisions
    [int]$MaxSeconds = 0  # 0 = forever
)

$ErrorActionPreference = 'Continue'
. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'crashguard-common.ps1')

$thresholds = CG-LoadJson (Join-Path $CG_CONFIGS 'thresholds.json')
$policies   = CG-LoadJson (Join-Path $CG_CONFIGS 'policies.json')
$nvsmi      = CG-FindNvSmi
$pollMs     = if ($thresholds._poll_interval_ms) { [int]$thresholds._poll_interval_ms } else { 1500 }
$ringSize   = if ($thresholds._telemetry_ring_size) { [int]$thresholds._telemetry_ring_size } else { 600 }

$ring = New-Object 'System.Collections.Generic.Queue[PSCustomObject]'
$tierHistory = New-Object 'System.Collections.Generic.Queue[string]'

$state = @{
    current_tier = 'green'
    current_tier_index = 0
    tier_entered_at = (Get-Date)
    last_action_at = $null
    active_mitigations = @()
    started_at = (Get-Date)
    iterations = 0
}

CG-Log 'sentinel' 'info' "Sentinel starting. PID=$PID DryRun=$DryRun nvsmi=$nvsmi pollMs=$pollMs"

$start = Get-Date
while ($true) {
    $iterStart = Get-Date
    $state.iterations++

    try {
        $gpu = CG-GpuTelemetry $nvsmi
        $cpu = CG-CpuRamTelemetry

        $sample = [PSCustomObject]@{
            ts = CG-Now
            unix = [int][double]::Parse((Get-Date -UFormat %s))
            gpu_temp_c = if ($gpu) { $gpu.temp_c } else { $null }
            gpu_util_pct = if ($gpu) { $gpu.util_pct } else { $null }
            gpu_mem_used_pct = if ($gpu) { $gpu.mem_used_pct } else { $null }
            gpu_power_pct = if ($gpu) { $gpu.power_pct } else { $null }
            gpu_mem_used_mb = if ($gpu) { $gpu.mem_used_mb } else { $null }
            gpu_power_w = if ($gpu) { $gpu.power_w } else { $null }
            cpu_util_pct = if ($cpu) { $cpu.cpu_util_pct } else { $null }
            ram_used_pct = if ($cpu) { $cpu.ram_used_pct } else { $null }
        }

        $ring.Enqueue($sample)
        while ($ring.Count -gt $ringSize) { [void]$ring.Dequeue() }

        $tierResult = CG-Tier $thresholds $sample

        # OR-in gpu-watch + gpu-predict tiers.
        # gpu-watch: current driver state (temp, throttle, HW thermal slowdown). Pre-failure.
        # gpu-predict: regression on temp trend, forecasts time to RED. Pre-PRE-failure.
        try {
            $tiersArr = @('green','yellow','orange','red','critical')
            foreach ($f in @('gpu-watch.tier','gpu-predict.tier')) {
                $p = Join-Path $CG_STATE $f
                if (Test-Path $p) {
                    $t = (Get-Content $p -ErrorAction SilentlyContinue) -as [string]
                    if ($t) {
                        $t = $t.Trim()
                        $idx = [Array]::IndexOf($tiersArr, $t)
                        if ($idx -gt $tierResult.index) {
                            $tierResult.index = $idx
                            $tierResult.tier = $t
                            $tierResult.reasons += ("{0}={1}" -f ($f -replace '\.tier$',''), $t)
                        }
                    }
                }
            }
        } catch {}

        $tierHistory.Enqueue($tierResult.tier)
        while ($tierHistory.Count -gt 30) { [void]$tierHistory.Dequeue() }

        # Sustain rules: need N samples at >= tier to escalate; M samples at lower to de-escalate
        $sustain = $thresholds._sustain_seconds
        $hysteresisSec = if ($thresholds._hysteresis_seconds.any) { [int]$thresholds._hysteresis_seconds.any } else { 15 }
        $samplesPerSec = 1000 / $pollMs

        $tierIdx = $tierResult.index
        $currIdx = $state.current_tier_index
        $tiers = @('green','yellow','orange','red','critical')

        $shouldEscalate = $false
        $shouldDeEscalate = $false

        $histArr = @($tierHistory)
        if ($tierIdx -gt $currIdx) {
            $needed = switch ($tiers[$tierIdx]) {
                'critical' { [int]($sustain.tier_critical * $samplesPerSec) }
                'red'      { [int]($sustain.tier_red * $samplesPerSec) }
                'orange'   { [int]($sustain.tier_orange * $samplesPerSec) }
                'yellow'   { [int]($sustain.tier_yellow * $samplesPerSec) }
                default { 1 }
            }
            if ($needed -lt 1) { $needed = 1 }
            $take = [Math]::Min($histArr.Count, $needed)
            $recent = $histArr | Select-Object -Last $take
            $hitCount = ($recent | Where-Object { ([Array]::IndexOf($tiers, $_)) -ge $tierIdx }).Count
            if ($hitCount -ge $needed) { $shouldEscalate = $true }
        }
        elseif ($tierIdx -lt $currIdx) {
            $needed = [int]($hysteresisSec * $samplesPerSec)
            if ($needed -lt 1) { $needed = 1 }
            $take = [Math]::Min($histArr.Count, $needed)
            $recent = $histArr | Select-Object -Last $take
            $belowCount = ($recent | Where-Object { ([Array]::IndexOf($tiers, $_)) -lt $currIdx }).Count
            if ($belowCount -ge $needed) { $shouldDeEscalate = $true }
        }

        if ($shouldEscalate) {
            CG-Log 'sentinel' 'warn' "ESCALATE: $($tiers[$currIdx]) -> $($tiers[$tierIdx]) reasons=$($tierResult.reasons -join ', ')"
            $state.current_tier = $tiers[$tierIdx]
            $state.current_tier_index = $tierIdx
            $state.tier_entered_at = (Get-Date)
            # Spike log: persistent record for later diagnosis
            $spikeLog = Join-Path $CG_TELEMETRY 'spike-log.jsonl'
            $top = $null
            try {
                $top = Get-Process | Sort-Object -Property WorkingSet64 -Descending | Select-Object -First 5 | ForEach-Object { [PSCustomObject]@{n=$_.Name; pid=$_.Id; cpu=[math]::Round($_.CPU,1); mb=[int]($_.WorkingSet64/1MB)} }
            } catch {}
            $spike = [PSCustomObject]@{
                ts = CG-Now
                from_tier = $tiers[$currIdx]
                to_tier   = $tiers[$tierIdx]
                reasons   = $tierResult.reasons
                sample    = $sample
                top_procs = $top
            }
            ($spike | ConvertTo-Json -Compress -Depth 5) | Add-Content -Path $spikeLog -Encoding utf8 -ErrorAction SilentlyContinue
            if (-not $DryRun) {
                & (Join-Path $CG_BIN 'crashguard-mitigate.ps1') -Tier $state.current_tier -Reasons ($tierResult.reasons -join '; ')
            } else {
                CG-Log 'sentinel' 'info' "DRY-RUN: would mitigate tier=$($state.current_tier)"
            }
            $state.last_action_at = (Get-Date)
        }
        elseif ($shouldDeEscalate) {
            CG-Log 'sentinel' 'info' "DE-ESCALATE: $($tiers[$currIdx]) -> $($tiers[$tierIdx])"
            $state.current_tier = $tiers[$tierIdx]
            $state.current_tier_index = $tierIdx
            $state.tier_entered_at = (Get-Date)
            if (-not $DryRun) {
                & (Join-Path $CG_BIN 'crashguard-mitigate.ps1') -Tier $state.current_tier -Reasons 'de-escalation' -Rollback
            }
            $state.last_action_at = (Get-Date)
        }

        # Heartbeat + last sample
        CG-Heartbeat 'sentinel' @{
            tier = $state.current_tier
            tier_index = $state.current_tier_index
            iterations = $state.iterations
            last_sample = $sample
            dry_run = [bool]$DryRun
        }

        # Snapshot every 10s
        if ($state.iterations % 7 -eq 0) {
            $snap = Join-Path $CG_TELEMETRY ('snapshots\snap_' + (Get-Date -Format 'yyyyMMdd_HH') + '.jsonl')
            ($sample | ConvertTo-Json -Compress) | Add-Content -Path $snap -Encoding utf8 -ErrorAction SilentlyContinue
        }
    } catch {
        CG-Log 'sentinel' 'error' "Iter exception: $_"
    }

    if ($Once) { break }
    if ($MaxSeconds -gt 0 -and ((Get-Date) - $start).TotalSeconds -gt $MaxSeconds) { break }

    $elapsed = ((Get-Date) - $iterStart).TotalMilliseconds
    $sleepMs = [Math]::Max(50, $pollMs - $elapsed)
    Start-Sleep -Milliseconds $sleepMs
}

CG-Log 'sentinel' 'info' "Sentinel exiting after $($state.iterations) iters."
