# Driver-health observer — catches the TDR-internal failures that load-based detection misses.
# Per user: all prior 0x116 BSODs had GPU ~0% utilization — so driver state, not load, is the trigger.

[CmdletBinding()]
param([int]$IntervalSec = 5, [switch]$Once)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

function Get-DriverSignals {
    $signals = New-Object 'System.Collections.Generic.List[PSCustomObject]'

    # 1a. nvlddmkm id=14 (TDR recovered) — HIGHEST priority live crash precursor.
    # 7 of these in last 90d; same period had 12 BSODs (5 BSODs were unrecovered TDRs that couldn't even fire id=14).
    try {
        $cutoff = (Get-Date).AddSeconds(-30)
        $tdrEvt = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='nvlddmkm'; ID=14; StartTime=$cutoff} -ErrorAction SilentlyContinue
        foreach ($e in $tdrEvt) {
            $signals.Add([PSCustomObject]@{ kind='tdr-recovered'; severity='CRITICAL'; src='nvlddmkm'; id=14; msg='GPU driver TDR — recovered live. Near-miss BSOD.' })
        }
    } catch {}

    # 1b. Display driver event-log warnings/errors in the last 60s (other ids)
    try {
        $cutoff = (Get-Date).AddSeconds(-60)
        $evt = Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=$cutoff; ProviderName=@('Display','nvlddmkm','igfx','iaStorAV')} -MaxEvents 5 -ErrorAction SilentlyContinue
        foreach ($e in $evt) {
            if ($e.ProviderName -eq 'nvlddmkm' -and $e.Id -eq 14) { continue }  # handled above
            if ($e.ProviderName -eq 'nvlddmkm' -and $e.Id -eq 153) { continue } # informational
            $signals.Add([PSCustomObject]@{ kind='evt'; severity='WARN'; src=$e.ProviderName; id=$e.Id; msg=$e.Message.Substring(0,[Math]::Min(120,$e.Message.Length)) })
        }
    } catch {}

    # 2. ThreadedDeviceManagement WHQL — check Device Manager problem state
    try {
        $bad = Get-PnpDevice -Status 'Error' -ErrorAction SilentlyContinue | Where-Object { $_.Class -in 'Display','System' }
        foreach ($d in $bad) {
            $signals.Add([PSCustomObject]@{ kind='pnp'; severity='ERROR'; src=$d.InstanceId; id=0; msg="$($d.FriendlyName) status=Error" })
        }
    } catch {}

    # 3. NVIDIA driver responsiveness — query takes >2s means likely stuck
    $nvsmi = CG-FindNvSmi
    if ($nvsmi) {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $r = & $nvsmi --query-gpu=name --format=csv,noheader 2>$null
        $sw.Stop()
        if ($sw.ElapsedMilliseconds -gt 2000) {
            $signals.Add([PSCustomObject]@{ kind='nvsmi_slow'; severity='WARN'; src='nvidia-smi'; id=0; msg="query took ${($sw.ElapsedMilliseconds)}ms" })
        }
        if (-not $r) {
            $signals.Add([PSCustomObject]@{ kind='nvsmi_fail'; severity='ERROR'; src='nvidia-smi'; id=0; msg='query returned empty' })
        }
    }

    # 4. DwmCore / Compositor responsiveness
    try {
        $dwm = Get-Process dwm -ErrorAction SilentlyContinue
        if ($dwm) {
            $age = ((Get-Date) - $dwm.StartTime).TotalMinutes
            $signals.Add([PSCustomObject]@{ kind='dwm_ok'; severity='INFO'; src='dwm'; id=$dwm.Id; msg="age=${[int]$age}min cpu=$([math]::Round($dwm.CPU,1))s" })
        } else {
            $signals.Add([PSCustomObject]@{ kind='dwm_missing'; severity='ERROR'; src='dwm'; id=0; msg='dwm.exe not running' })
        }
    } catch {}

    return $signals
}

CG-Log 'driver-health' 'info' "Driver health observer starting PID=$PID interval=$IntervalSec"

while ($true) {
    try {
        $signals = Get-DriverSignals
        $crit = @($signals | Where-Object { $_.severity -eq 'CRITICAL' })
        $errors = @($signals | Where-Object { $_.severity -eq 'ERROR' })
        $warns = @($signals | Where-Object { $_.severity -eq 'WARN' })

        if ($crit.Count -gt 0) {
            CG-Log 'driver-health' 'error' ("CRITICAL driver signals: " + (($crit | ForEach-Object { "$($_.kind):$($_.msg)" }) -join '; '))
            $entry = [PSCustomObject]@{ ts=(CG-Now); kind='driver-health-critical'; signals=$crit }
            ($entry | ConvertTo-Json -Compress -Depth 5) | Add-Content -Path (Join-Path $CG_TELEMETRY 'spike-log.jsonl') -Encoding utf8 -ErrorAction SilentlyContinue
            # Trigger immediate RED mitigations — TDR-recovered = near-miss BSOD
            try {
                & (Join-Path $CG_BIN 'crashguard-mitigate.ps1') -Tier red -Reasons "nvlddmkm-id-14-TDR-recovered (live precursor)"
            } catch { CG-Log 'driver-health' 'error' "mitigate fail: $_" }
        }
        if ($errors.Count -gt 0) {
            CG-Log 'driver-health' 'error' ("Driver errors: " + (($errors | ForEach-Object { "$($_.kind):$($_.msg)" }) -join '; '))
            $entry = [PSCustomObject]@{ ts=(CG-Now); kind='driver-health-error'; signals=$errors }
            ($entry | ConvertTo-Json -Compress -Depth 5) | Add-Content -Path (Join-Path $CG_TELEMETRY 'spike-log.jsonl') -Encoding utf8 -ErrorAction SilentlyContinue
        }
        if ($warns.Count -gt 0) {
            CG-Log 'driver-health' 'warn' ("Driver warns: " + (($warns | ForEach-Object { "$($_.kind):$($_.msg)" }) -join '; '))
        }
        CG-Heartbeat 'driver-health' @{ errors=$errors.Count; warns=$warns.Count }
    } catch {
        CG-Log 'driver-health' 'error' "Iter exception: $_"
    }
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSec
}
