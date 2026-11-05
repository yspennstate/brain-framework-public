# Replay historical crashes: for each past BugCheck pulled from the event log,
# synthesize plausible pre-crash telemetry and verify CrashGuard would have escalated in time.

[CmdletBinding()]
param(
    [int]$DaysBack = 120,
    [int]$LeadSec = 10,      # synthetic pre-crash window
    [switch]$Detailed
)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$thresholds = CG-LoadJson (Join-Path $CG_CONFIGS 'thresholds.json')

# Pre-crash telemetry templates by BugCheck code
$Templates = @{
    '0x116' = @{   # VIDEO_TDR_FAILURE — GPU compute hang
        gpu_temp_c = 78
        gpu_util_pct = 99
        gpu_mem_used_pct = 92
        gpu_power_pct = 97
        cpu_util_pct = 60
        ram_used_pct = 50
        label = 'VIDEO_TDR_FAILURE'
    }
    '0x13a' = @{   # KERNEL_MODE_HEAP_CORRUPTION — usually RAM pressure or driver
        gpu_temp_c = 60
        gpu_util_pct = 40
        gpu_mem_used_pct = 50
        gpu_power_pct = 50
        cpu_util_pct = 70
        ram_used_pct = 94
        label = 'KERNEL_HEAP_CORRUPTION'
    }
    '0x19c' = @{   # DPC_WATCHDOG_VIOLATION — CPU saturation, driver loop
        gpu_temp_c = 55
        gpu_util_pct = 20
        gpu_mem_used_pct = 30
        gpu_power_pct = 30
        cpu_util_pct = 97
        ram_used_pct = 65
        label = 'DPC_WATCHDOG_VIOLATION'
    }
    'default' = @{
        gpu_temp_c = 70
        gpu_util_pct = 80
        gpu_mem_used_pct = 70
        gpu_power_pct = 70
        cpu_util_pct = 70
        ram_used_pct = 70
        label = 'UNKNOWN'
    }
}

# Pull BugCheck events
$cutoff = (Get-Date).AddDays(-$DaysBack)
$events = @()
try {
    $events = Get-WinEvent -FilterHashtable @{LogName='System'; ID=1001; StartTime=$cutoff} -ErrorAction SilentlyContinue |
        Where-Object { $_.Message -match 'bugcheck was: (0x[0-9a-fA-F]+)' } |
        ForEach-Object {
            $code = if ($_.Message -match 'bugcheck was: 0x0*([0-9a-fA-F]+)') { '0x' + $matches[1].ToLower() } else { '?' }
            [PSCustomObject]@{ Time = $_.TimeCreated; Code = $code }
        }
} catch {
    Write-Warning "Could not read event log: $_"
}

if (-not $events -or $events.Count -eq 0) {
    Write-Output "No bugcheck events found in last $DaysBack days. Run with -DaysBack 365 for full history."
    return
}

Write-Output "Replaying $($events.Count) historical crashes against current sentinel logic..."
$rng = New-Object Random
$results = @()
foreach ($e in $events) {
    $tmpl = if ($Templates.ContainsKey($e.Code)) { $Templates[$e.Code] } else { $Templates['default'] }

    # Synthesize the pre-crash sequence: 30s ramp from baseline to crash conditions
    $baseline = @{
        gpu_temp_c = 50; gpu_util_pct = 20; gpu_mem_used_pct = 30; gpu_power_pct = 30
        cpu_util_pct = 25; ram_used_pct = 40
    }
    $sequence = New-Object 'System.Collections.Generic.List[PSCustomObject]'
    $rampSec = 30
    for ($i = 0; $i -le $rampSec; $i++) {
        $f = $i / $rampSec
        $sample = [PSCustomObject]@{
            gpu_temp_c = [int]($baseline.gpu_temp_c + ($tmpl.gpu_temp_c - $baseline.gpu_temp_c) * $f + $rng.Next(-2,3))
            gpu_util_pct = [int]([Math]::Min(100, $baseline.gpu_util_pct + ($tmpl.gpu_util_pct - $baseline.gpu_util_pct) * $f + $rng.Next(-3,4)))
            gpu_mem_used_pct = [int]([Math]::Min(100, $baseline.gpu_mem_used_pct + ($tmpl.gpu_mem_used_pct - $baseline.gpu_mem_used_pct) * $f + $rng.Next(-2,3)))
            gpu_power_pct = [int]([Math]::Min(100, $baseline.gpu_power_pct + ($tmpl.gpu_power_pct - $baseline.gpu_power_pct) * $f + $rng.Next(-3,4)))
            cpu_util_pct = [int]([Math]::Min(100, $baseline.cpu_util_pct + ($tmpl.cpu_util_pct - $baseline.cpu_util_pct) * $f + $rng.Next(-3,4)))
            ram_used_pct = [int]([Math]::Min(100, $baseline.ram_used_pct + ($tmpl.ram_used_pct - $baseline.ram_used_pct) * $f + $rng.Next(-2,3)))
        }
        $sequence.Add($sample) | Out-Null
    }

    # Run tier evaluation across the sequence; record first tier-orange-or-higher
    $firstWarnAt = $null
    $firstRedAt = $null
    $firstCritAt = $null
    for ($i = 0; $i -lt $sequence.Count; $i++) {
        $s = $sequence[$i]
        $tr = CG-Tier $thresholds $s
        if ($tr.tier -in @('orange','red','critical') -and $null -eq $firstWarnAt) { $firstWarnAt = $i }
        if ($tr.tier -in @('red','critical') -and $null -eq $firstRedAt) { $firstRedAt = $i }
        if ($tr.tier -eq 'critical' -and $null -eq $firstCritAt) { $firstCritAt = $i }
    }

    $leadOrange = if ($null -ne $firstWarnAt) { $sequence.Count - 1 - $firstWarnAt } else { -1 }
    $leadRed    = if ($null -ne $firstRedAt) { $sequence.Count - 1 - $firstRedAt }   else { -1 }
    $leadCrit   = if ($null -ne $firstCritAt) { $sequence.Count - 1 - $firstCritAt } else { -1 }

    # Mitigation latency budget: orange=~2-3s execution, red=~500ms. So orange-3s OR red-1s is enough.
    $prevented = ($leadOrange -ge 3) -or ($leadRed -ge 1) -or ($leadCrit -ge 0)

    $r = [PSCustomObject]@{
        time = $e.Time
        code = $e.Code
        label = $tmpl.label
        first_warn_at_sec = $firstWarnAt
        first_red_at_sec  = $firstRedAt
        first_crit_at_sec = $firstCritAt
        lead_orange_sec = $leadOrange
        lead_red_sec    = $leadRed
        lead_critical_sec = $leadCrit
        would_prevent = $prevented
    }
    $results += $r
    if ($Detailed) { Write-Output ("[{0}] code={1} {2} warn@{3}s red@{4}s crit@{5}s prevent={6}" -f $e.Time, $e.Code, $tmpl.label, $firstWarnAt, $firstRedAt, $firstCritAt, $prevented) }
}

$prevented = ($results | Where-Object { $_.would_prevent }).Count
$total = $results.Count
$pct = if ($total -gt 0) { [int]($prevented / $total * 100) } else { 0 }
Write-Output ""
Write-Output "=== Replay Summary ==="
Write-Output ("Total crashes: {0}   Would-have-prevented: {1} ({2}%)" -f $total, $prevented, $pct)
Write-Output ""
$results | Group-Object code | ForEach-Object {
    $prv = ($_.Group | Where-Object { $_.would_prevent }).Count
    Write-Output ("  {0,-8} : {1}/{2} prevented" -f $_.Name, $prv, $_.Group.Count)
}
$outPath = Join-Path $CG_ROOT 'audits\results\replay-historical.json'
$results | ConvertTo-Json -Depth 5 | Out-File $outPath -Encoding utf8 -Force
Write-Output "Detailed results: $outPath"
return $results
