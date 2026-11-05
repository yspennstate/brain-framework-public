# Self-test for crisis-correlator: inject fake precursor entries into spike-log.jsonl
# and verify crisis-correlator detects + writes a CRISIS entry within 30 seconds.
# Safe — fake entries are tagged with kind='whea-precursor-TEST' / 'driver-health-critical-TEST'
# so a follow-up cleanup pass can remove them.

[CmdletBinding()]
param([switch]$Cleanup, [int]$TimeoutSec = 45, [switch]$DryRun)

# DryRun: stop crisis-correlator first so it can't react, run test in isolation, restart
if ($DryRun) {
    Write-Output "DryRun: stopping crisis-correlator..."
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'crashguard-crisis-correlator' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 800
}

. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$spikePath = Join-Path $CG_TELEMETRY 'spike-log.jsonl'

if ($Cleanup) {
    # Remove test-tagged entries
    $lines = Get-Content $spikePath -ErrorAction SilentlyContinue
    $clean = $lines | Where-Object { $_ -notmatch '"_test":true' }
    Set-Content -Path $spikePath -Value $clean -Encoding utf8 -Force
    "Removed $($lines.Count - $clean.Count) test entries"
    return
}

# Inject 2 different precursors with current timestamp (within crisis-correlator window)
$now = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss.fff')
$entries = @(
    [PSCustomObject]@{ ts=$now; kind='whea-precursor'; severity='CORRECTED'; is_pcie=$true; event_id=17; _test=$true }
    [PSCustomObject]@{ ts=$now; kind='driver-health-critical'; signals=@(@{kind='tdr-recovered';severity='CRITICAL'}); _test=$true }
)
foreach ($e in $entries) {
    ($e | ConvertTo-Json -Compress -Depth 5) | Add-Content -Path $spikePath -Encoding utf8 -Force
}
Write-Output "Injected 2 fake precursor entries at $now"

# Wait for crisis-correlator to detect (it polls every 15s)
$start = Get-Date
$detected = $false
while (((Get-Date) - $start).TotalSeconds -lt $TimeoutSec) {
    Start-Sleep -Seconds 3
    $recent = Get-Content $spikePath -Tail 10
    foreach ($line in $recent) {
        if ($line -match '"kind":"CRISIS"' -and $line -notmatch '"_test"') {
            try {
                $parsed = $line | ConvertFrom-Json
                $when = [datetime]$parsed.ts
                if ($when -ge $start.AddSeconds(-2)) {
                    Write-Output "PASS: crisis-correlator fired CRISIS within $([int](($when - $start).TotalSeconds))s. Components: $($parsed.components). Score: $($parsed.score)"
                    $detected = $true
                    break
                }
            } catch {}
        }
    }
    if ($detected) { break }
}
if (-not $detected) { Write-Output "FAIL: no CRISIS entry detected in ${TimeoutSec}s" }

# Cleanup
Write-Output "Cleaning test entries..."
$lines = Get-Content $spikePath -ErrorAction SilentlyContinue
$clean = $lines | Where-Object { $_ -notmatch '"_test":true' }
Set-Content -Path $spikePath -Value $clean -Encoding utf8 -Force
"Removed $($lines.Count - $clean.Count) test entries"

if ($DryRun) {
    Write-Output "DryRun: restarting crisis-correlator..."
    $psExe = (Get-Command powershell.exe).Source
    Start-Process -FilePath $psExe -ArgumentList '-NoProfile','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File',"$env:USERPROFILE\CrashGuard\bin\crashguard-crisis-correlator.ps1" -WindowStyle Hidden | Out-Null
}

if ($detected) { exit 0 } else { exit 1 }
