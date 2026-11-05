# PCIe / WHEA-Logger watcher.
#
# REVISED 2026-06-01 after root-cause analysis (see docs/rootcause-2026-06-01-idle-rtd3-tdr.md):
# the recurring 0x116 is an IDLE dGPU device-loss (RTD3/GC6 D3cold->D0 restore failure), not a
# load/thermal crash. Corrected PCIe AER errors (WHEA id=17) are a *health* signal of a marginal
# GPU PCIe link -- they are already-corrected/recovered events that happen ~11x/day here.
#
# OLD behaviour fired crashguard-mitigate -Tier red on every single corrected error, which
# killed ollama, suspended browsers, and throttled ALL codex/observers + dropped GPU clocks.
# That does NOTHING for an idle device-loss and needlessly disrupts the user's workloads.
#
# NEW behaviour:
#   * Record every WHEA event to spike-log (forensics, unchanged).
#   * On a corrected GPU AER: ENSURE the GPU keepalive is alive (the real mitigation -- it keeps
#     the dGPU in D0 so the failing power transition can't happen) and track the error RATE.
#   * Only on an AER *storm* (link actively degrading) raise a loud alert + state marker. Never
#     kill/throttle workloads for a corrected error.
#   * On a FATAL/uncorrectable WHEA: record loudly for postmortem (no workload action can stop an
#     in-progress fatal error).

[CmdletBinding()]
param([int]$IntervalSec = 5, [switch]$Once)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$KEEPALIVE_PY = "$env:USERPROFILE\CrashGuard\bin\crashguard-gpu-keepalive.py"
$AER_WINDOW_MIN = 5      # rolling window for storm detection
$AER_STORM_COUNT = 6     # >= this many corrected AERs in the window = "storm"

# Ensure the anti-RTD3 GPU keepalive is running. Safe to call often: the keepalive
# self-enforces a singleton (a second instance exits), so a racing launch is harmless.
function Ensure-Keepalive {
    $ka = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'crashguard-gpu-keepalive' } | Select-Object -First 1
    if ($ka) { return $true }
    try {
        $pyw = (Get-Command pythonw.exe -ErrorAction Stop).Source
        Start-Process -FilePath $pyw -ArgumentList '-u', $KEEPALIVE_PY -WindowStyle Hidden | Out-Null
        CG-Log 'pcie-watch' 'warn' 'GPU keepalive was NOT running -- relaunched it in response to PCIe link stress'
        return $true
    } catch {
        CG-Log 'pcie-watch' 'error' "could not launch GPU keepalive: $_"
        return $false
    }
}

# Track last-seen event time so we don't double-report
$lastSeenFile = Join-Path $CG_STATE 'pcie-watch.last-seen.txt'
$lastSeen = if (Test-Path $lastSeenFile) { Get-Date (Get-Content $lastSeenFile) } else { (Get-Date).AddSeconds(-60) }

$aerTimes = New-Object System.Collections.ArrayList   # rolling timestamps of corrected GPU AERs
$stormActive = $false

CG-Log 'pcie-watch' 'info' "PCIe / WHEA watcher starting PID=$PID interval=$IntervalSec (keepalive-centric response)"

while ($true) {
    try {
        $cutoff = $lastSeen.AddSeconds(1)
        # WHEA-Logger: id=17 corrected, id=1/18 uncorrectable/fatal
        $events = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'; StartTime=$cutoff} -ErrorAction SilentlyContinue
        if ($events) {
            foreach ($e in $events) {
                $isPCIe = $e.Message -match 'PCI Express' -or $e.Message -match 'PCIe'
                $severity = if ($e.Id -eq 17) { 'CORRECTED' } elseif ($e.Id -in @(1,18)) { 'FATAL' } else { "id=$($e.Id)" }
                $tag = if ($isPCIe) { 'PCIe' } else { 'OTHER' }
                CG-Log 'pcie-watch' 'warn' "WHEA-$severity-$tag at $($e.TimeCreated): $($e.Message.Substring(0,[Math]::Min(150,$e.Message.Length)))"

                $entry = [PSCustomObject]@{
                    ts        = CG-Now
                    kind      = 'whea-precursor'
                    event_time = $e.TimeCreated
                    severity  = $severity
                    is_pcie   = $isPCIe
                    event_id  = $e.Id
                    message   = $e.Message.Substring(0,[Math]::Min(400,$e.Message.Length))
                }
                ($entry | ConvertTo-Json -Compress -Depth 5) | Add-Content -Path (Join-Path $CG_TELEMETRY 'spike-log.jsonl') -Encoding utf8 -ErrorAction SilentlyContinue

                if ($isPCIe -and $e.Id -eq 17) {
                    # Corrected (recovered) GPU link error. The real mitigation for the idle
                    # device-loss is keeping the dGPU pinned in D0 -> ensure keepalive. Track rate.
                    [void]$aerTimes.Add($e.TimeCreated)
                    Ensure-Keepalive | Out-Null
                }
                elseif ($e.Id -in @(1,18)) {
                    CG-Log 'pcie-watch' 'error' "WHEA FATAL/uncorrectable id=$($e.Id) -- recording for postmortem (no live workload action can stop an in-progress fatal error)"
                }
            }
            $lastSeen = ($events | Sort-Object TimeCreated | Select-Object -Last 1).TimeCreated
            Set-Content -Path $lastSeenFile -Value $lastSeen.ToString('o') -Encoding utf8 -Force
        }

        # Prune the rolling AER window and evaluate storm state
        $cut = (Get-Date).AddMinutes(-$AER_WINDOW_MIN)
        $kept = @($aerTimes | Where-Object { $_ -gt $cut })
        $aerTimes.Clear(); foreach ($t in $kept) { [void]$aerTimes.Add($t) }
        $rate = $aerTimes.Count

        $stormPath = Join-Path $CG_STATE 'pcie-aer-storm.json'
        if ($rate -ge $AER_STORM_COUNT) {
            Ensure-Keepalive | Out-Null
            if (-not $stormActive) {
                $stormActive = $true
                CG-Log 'pcie-watch' 'error' "PCIe AER STORM: $rate corrected GPU errors in ${AER_WINDOW_MIN}min -- link degrading. GPU keepalive ensured. Recommend driver update / hardware check. (No workloads touched.)"
            }
            @{ active=$true; rate=$rate; window_min=$AER_WINDOW_MIN; updated=(CG-Now);
               note='Marginal GPU PCIe link. dGPU pinned to D0 via keepalive. Consider driver 595.97 update / GPU reseat.' } |
                ConvertTo-Json | Set-Content -Path $stormPath -Encoding utf8 -Force
        } else {
            if ($stormActive) {
                $stormActive = $false
                CG-Log 'pcie-watch' 'info' "PCIe AER storm cleared (rate=$rate in ${AER_WINDOW_MIN}min)"
                @{ active=$false; rate=$rate; window_min=$AER_WINDOW_MIN; updated=(CG-Now) } |
                    ConvertTo-Json | Set-Content -Path $stormPath -Encoding utf8 -Force
            }
        }

        CG-Heartbeat 'pcie-watch' @{ last_seen = $lastSeen.ToString('o'); aer_rate_5min = $rate; storm = $stormActive }
    } catch {
        CG-Log 'pcie-watch' 'error' "Iter exception: $_"
    }
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSec
}
