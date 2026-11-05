# azure_poll.ps1 - fetch Azure box compute health WITHOUT ever blocking the governor loop.
# Launched fire-and-forget (hidden) by compute_governor.ps1 every ~5 min. Does the ssh with a
# hard WaitForExit timeout + kill, parses sentinel JSON (or derives from PSI/loadavg fallback),
# and atomically writes azure_last.json. The governor only ever READS that file.

[CmdletBinding()]
param([switch]$LibraryOnly)

function New-FallbackAzureHealth {
    param(
        [double]$PsiCpu,
        [double]$Load1,
        [bool]$SentinelStale = $false,
        [bool]$MeasurementAvailable = $true
    )
    $status = 'warn'
    $reasons = @('fallback_telemetry')
    if ($SentinelStale) { $reasons += 'sentinel_stale' }
    if ($PsiCpu -ge 40 -or ($Load1 -ge 10.0 -and $PsiCpu -ge 10)) {
        $status = 'distress'; $reasons = @('cpu_pressure') + $reasons
    } elseif ($PsiCpu -ge 25 -or $Load1 -ge 7.0) {
        $reasons = @('elevated_pressure') + $reasons
    }
    $health = [ordered]@{
        fallback = $true; fallback_measurement_available = $MeasurementAvailable
        telemetry_degraded = $true; sentinel_stale = $SentinelStale
        status = $status; mode = 'throttle'; reasons = $reasons
        psi_cpu_some_avg60 = $PsiCpu; load1 = $Load1
    }
    if ($SentinelStale) {
        $health.protected_health = 'unknown'
        $health.protected_alerts = @('protected_telemetry_stale')
    }
    return [pscustomobject]$health
}

function Select-AzureHealth($Sentinel, [bool]$SentinelStale, $Fallback) {
    if ($Sentinel -and -not $SentinelStale) { return $Sentinel }
    if ($Fallback) { return $Fallback }
    if ($SentinelStale) {
        return New-FallbackAzureHealth -PsiCpu 0 -Load1 0 `
            -SentinelStale $true -MeasurementAvailable $false
    }
    return $null
}

function Test-SentinelStale($Sentinel, [datetime]$NowUtc = [datetime]::UtcNow) {
    if (-not $Sentinel -or -not $Sentinel.ts) { return $true }
    try {
        $stamp = [datetime]::ParseExact("$($Sentinel.ts)", 'yyyy-MM-dd HH:mm:ss', $null)
        $ageMinutes = ($NowUtc - $stamp).TotalMinutes
        return ($ageMinutes -gt 10 -or $ageMinutes -lt -2)
    } catch {
        return $true
    }
}

if ($LibraryOnly) { return }

$ErrorActionPreference = 'SilentlyContinue'
$StateDir  = "$env:USERPROFILE\.claude\compute"
$OutFile   = Join-Path $StateDir 'azure_last.json'
$AzureSsh  = '<box-user>@<host-ip>'
$AzureKey  = "$env:USERPROFILE\.ssh\<box-ssh-key>.pem"
$TimeoutMs = 15000

$remoteCmd = 'cat /var/local/compute_health.json 2>/dev/null; echo ---MARK---; head -1 /proc/pressure/cpu; cat /proc/loadavg'

$outTmp = Join-Path $StateDir ('azure_poll_out_{0}.txt' -f $PID)
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = 'ssh'
$psi.Arguments = ('-i "{0}" -o BatchMode=yes -o ConnectTimeout=8 -o LogLevel=ERROR -o StrictHostKeyChecking=accept-new {1} "{2}"' -f $AzureKey, $AzureSsh, $remoteCmd)
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.CreateNoWindow = $true

$ok = $false
$txt = ''
try {
    $p = [System.Diagnostics.Process]::Start($psi)
    if ($p.WaitForExit($TimeoutMs)) {
        $txt = $p.StandardOutput.ReadToEnd()
        if ($p.ExitCode -eq 0 -and $txt) { $ok = $true }
    } else {
        try { $p.Kill() } catch {}
    }
} catch {}

$azure = $null
if ($ok) {
    $parts = ($txt -split '---MARK---')
    $sentinel = $null
    if ($parts[0].Trim()) {
        try { $sentinel = $parts[0] | ConvertFrom-Json } catch {}
    }
    # A dead sentinel leaves a stale file that still parses; an absent/unparseable record is
    # equally degraded. Check the producer's own UTC clock because write-behind liveness lies.
    $sentinelStale = Test-SentinelStale $sentinel
    # Reader-facing freshness: the box stamps ts in UTC while the laptop state file stamps
    # ts in local time (UTC+offset), so a fresh box ts can read as hours-stale to a human or
    # Codex session reading the embedded block. Surface age_s (UTC-correct) and ts_local so
    # freshness is unambiguous. Fires only when a sentinel is present; fully fail-safe.
    if ($sentinel -and $sentinel.ts) {
        try {
            $stampUtc = [datetime]::ParseExact("$($sentinel.ts)", 'yyyy-MM-dd HH:mm:ss', $null)
            $ageS = [int][math]::Round(([datetime]::UtcNow - $stampUtc).TotalSeconds)
            $tsLocal = [datetime]::SpecifyKind($stampUtc, [System.DateTimeKind]::Utc).ToLocalTime().ToString('yyyy-MM-dd HH:mm:ss')
            $sentinel | Add-Member -NotePropertyName age_s -NotePropertyValue $ageS -Force
            $sentinel | Add-Member -NotePropertyName ts_local -NotePropertyValue $tsLocal -Force
        } catch {}
    }
    $fallback = $null
    if ($parts.Count -ge 2) {
        $psiM  = [regex]::Match($parts[1], 'avg60=([0-9.]+)')
        $loadM = [regex]::Match($parts[1], '([0-9]+\.[0-9]+) [0-9]+\.[0-9]+ [0-9]+\.[0-9]+')
        if ($psiM.Success -or $loadM.Success) {
            $psiV = 0.0; if ($psiM.Success) { $psiV = [double]$psiM.Groups[1].Value }
            $l1   = 0.0; if ($loadM.Success) { $l1 = [double]$loadM.Groups[1].Value }
            $fallback = New-FallbackAzureHealth -PsiCpu $psiV -Load1 $l1 `
                -SentinelStale $sentinelStale
        }
    }
    $azure = Select-AzureHealth $sentinel $sentinelStale $fallback
}

$rec = [ordered]@{
    fetched_at = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
    ok         = [bool]($null -ne $azure)
    azure      = $azure
}
try {
    $tmp = "$OutFile.tmp$PID"
    ($rec | ConvertTo-Json -Depth 6) | Set-Content -Path $tmp -Encoding UTF8
    Move-Item -Force $tmp $OutFile
} catch {}
Remove-Item $outTmp -ErrorAction SilentlyContinue
