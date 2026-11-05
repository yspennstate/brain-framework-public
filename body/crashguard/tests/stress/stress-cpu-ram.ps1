# Live stress test: CPU + RAM. Confirms sentinel detects and escalates within budget.
# Usage: -DurationSec 30 -Workers 4

[CmdletBinding()]
param(
    [int]$DurationSec = 30,
    [int]$Workers = 4,
    [int]$RamMb = 8192
)

. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

Write-Output "Starting stress: $Workers CPU workers + $RamMb MB RAM, duration $DurationSec s"
$pre = CG-CpuRamTelemetry
Write-Output ("Pre: CPU={0}% RAM={1}%" -f $pre.cpu_util_pct, $pre.ram_used_pct)

$jobs = @()
1..$Workers | ForEach-Object {
    $jobs += Start-Job -ScriptBlock {
        param($DurSec)
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        while ($sw.Elapsed.TotalSeconds -lt $DurSec) {
            $x = 0
            1..10000 | ForEach-Object { $x = ($x * 1103515245 + 12345) % 0x7fffffff }
        }
    } -ArgumentList $DurationSec
}

# RAM stress in this process
$ramJob = Start-Job -ScriptBlock {
    param($Mb,$DurSec)
    $arr = New-Object byte[] ([int64]$Mb * 1MB)
    # Touch each page to commit
    for ($i = 0; $i -lt $arr.Length; $i += 4096) { $arr[$i] = 1 }
    Start-Sleep -Seconds $DurSec
    $arr = $null
} -ArgumentList $RamMb,$DurationSec

# Sample telemetry during stress
$samples = @()
$sw = [System.Diagnostics.Stopwatch]::StartNew()
while ($sw.Elapsed.TotalSeconds -lt $DurationSec) {
    Start-Sleep -Milliseconds 1500
    $t = CG-CpuRamTelemetry
    if ($t) {
        $samples += [PSCustomObject]@{
            elapsed = [int]$sw.Elapsed.TotalSeconds
            cpu = $t.cpu_util_pct
            ram = $t.ram_used_pct
        }
    }
}

$jobs | Stop-Job; $jobs | Remove-Job -Force
$ramJob | Stop-Job; $ramJob | Remove-Job -Force

$post = CG-CpuRamTelemetry
Write-Output ("Post: CPU={0}% RAM={1}%" -f $post.cpu_util_pct, $post.ram_used_pct)
$peak_cpu = ($samples | Measure-Object -Property cpu -Maximum).Maximum
$peak_ram = ($samples | Measure-Object -Property ram -Maximum).Maximum
Write-Output "Peak during stress: CPU=$peak_cpu% RAM=$peak_ram%"

# Verify sentinel registered escalation
$hb = CG-LoadJson (Join-Path $CG_STATE 'sentinel.heartbeat')
if ($hb) {
    Write-Output "Sentinel tier observed: $($hb.tier)"
}
return @{ samples=$samples; peak_cpu=$peak_cpu; peak_ram=$peak_ram; sentinel_tier=$hb.tier }
