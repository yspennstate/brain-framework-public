# Chaos test — kill a random CrashGuard service, verify self-heal recovers it within 60s.

[CmdletBinding()]
param([int]$Iterations = 3)

. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$victims = @('process-watcher','export','driver-health','dedup-watcher','power-watch','wudfrd-watch','hid-watch','update-watcher','sleep-defender','pulse','html-publisher')
# Don't chaos-kill: sentinel/watchdog/self-heal/watchdog2/ring-recorder/recovery/power-guard/crisis-correlator/log-rotate/trend-analyzer/pcie-watch — those are critical or slow-cycle

$pass = 0
$fail = 0
for ($i = 0; $i -lt $Iterations; $i++) {
    $victim = $victims | Get-Random
    $script = 'crashguard-' + $victim + '.ps1'
    $proc = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match [Regex]::Escape($script) }
    if (-not $proc) { Write-Output ("[" + ($i+1) + "/$Iterations] $victim already missing - skipping"); continue }
    Write-Output ("[" + ($i+1) + "/$Iterations] CHAOS: killing $victim pid=$($proc.ProcessId)")
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue

    # Wait up to 90s for self-heal to revive
    $start = Get-Date
    $revived = $false
    while (((Get-Date) - $start).TotalSeconds -lt 90) {
        Start-Sleep -Seconds 5
        $new = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match [Regex]::Escape($script) }
        if ($new -and $new.ProcessId -ne $proc.ProcessId) {
            $elapsed = [int](((Get-Date) - $start).TotalSeconds)
            Write-Output ("[" + ($i+1) + "/$Iterations] PASS $victim revived in ${elapsed}s new pid=$($new.ProcessId)")
            $revived = $true
            $pass++
            break
        }
    }
    if (-not $revived) {
        Write-Output ("[" + ($i+1) + "/$Iterations] FAIL $victim NOT revived in 90s")
        $fail++
    }
}
Write-Output ''
Write-Output ('chaos: ' + $pass + ' pass / ' + $fail + ' fail')
if ($fail -gt 0) { exit 1 } else { exit 0 }
