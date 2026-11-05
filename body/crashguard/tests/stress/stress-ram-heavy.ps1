# Heavy RAM stress — allocates a large memory block to validate ram_used_pct thresholds.
# Default 16 GB allocation; user has 64 GB so this brings used past 50% baseline + 25% = orange/red territory.

[CmdletBinding()]
param([int]$DurationSec = 20, [int]$AllocMb = 16384)

. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

Write-Output ('Heavy RAM stress: allocating ' + $AllocMb + ' MB for ' + $DurationSec + ' s')
$pre = CG-CpuRamTelemetry
Write-Output ('Pre: RAM=' + $pre.ram_used_pct + '% free=' + $pre.ram_free_mb + ' MB')

# Use a background job to allocate + touch memory
$job = Start-Job -ScriptBlock {
    param($Mb, $Dur)
    $arr = New-Object byte[] ([int64]$Mb * 1MB)
    # Touch each page to actually commit physical RAM
    for ($i = 0; $i -lt $arr.Length; $i += 4096) { $arr[$i] = 1 }
    Start-Sleep -Seconds $Dur
} -ArgumentList $AllocMb, $DurationSec

$samples = @()
$sw = [System.Diagnostics.Stopwatch]::StartNew()
while ($sw.Elapsed.TotalSeconds -lt $DurationSec + 5) {
    Start-Sleep -Milliseconds 1500
    $t = CG-CpuRamTelemetry
    if ($t) { $samples += [PSCustomObject]@{ elapsed=[int]$sw.Elapsed.TotalSeconds; ram=$t.ram_used_pct } }
}
$job | Stop-Job; $job | Remove-Job -Force

$peak = ($samples | Measure-Object -Property ram -Maximum).Maximum
Write-Output ('Peak RAM during stress: ' + $peak + '%')

$hb = Get-Content "$env:USERPROFILE\CrashGuard\state\sentinel.heartbeat" -ErrorAction SilentlyContinue | ConvertFrom-Json
Write-Output ('Sentinel tier observed: ' + $hb.tier)
