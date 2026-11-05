# One-shot tool: kills the duplicate worker-process instances that CrashGuard suspended at tick 39.
# Frees ~3.2 GB of held RAM. IRREVERSIBLE.
# Use: powershell $env:USERPROFILE\CrashGuard\bin\crashguard-kill-suspended-dupes.ps1 [-Confirm]

[CmdletBinding()]
param([switch]$Confirm)

. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

# Find suspended duplicate observers: any watched python script with >=2 instances.
# The OLDER one is the "kept" one; the YOUNGER ones are the suspended duplicates we'll kill.
$pythons = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue
$byScript = @{}
foreach ($p in $pythons) {
    if ($p.CommandLine -match '(\w+_observer\.py|\w+_recorder\.py|\w+_streamer\.py|\w+_bot\.py|\w+_loop\.py|\w+_logger\.py|\w+_watchdog\.py)') {
        $s = $matches[1]
        if (-not $byScript.ContainsKey($s)) { $byScript[$s] = New-Object 'System.Collections.Generic.List[object]' }
        $proc = Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
        $byScript[$s].Add([PSCustomObject]@{ Pid=$p.ProcessId; Start=[datetime]$p.CreationDate; MB=if($proc){[int]($proc.WorkingSet64/1MB)}else{0} })
    }
}

$dupGroups = $byScript.GetEnumerator() | Where-Object { $_.Value.Count -ge 2 }
$toKill = @()
$totalMB = 0
foreach ($g in $dupGroups) {
    $sorted = $g.Value | Sort-Object Start
    $youngest = $sorted | Select-Object -Skip 1
    foreach ($y in $youngest) { $toKill += $y; $totalMB += $y.MB }
}

Write-Output ('Found ' + $toKill.Count + ' duplicate processes holding ' + $totalMB + ' MB')
if ($toKill.Count -eq 0) { Write-Output 'Nothing to kill'; return }

if (-not $Confirm) {
    Write-Output ''
    Write-Output 'Re-run with -Confirm to actually kill them. Preview:'
    $toKill | Select-Object -First 10 Pid, MB, Start | Format-Table -AutoSize
    return
}

$killed = 0
foreach ($t in $toKill) {
    try {
        Stop-Process -Id $t.Pid -Force -ErrorAction Stop
        $killed++
        CG-Log 'kill-dupes' 'warn' ('Killed dup pid=' + $t.Pid + ' MB=' + $t.MB)
    } catch {}
}
Write-Output ('Killed ' + $killed + ' duplicate observer processes. Freed approximately ' + $totalMB + ' MB.')

$entry = [PSCustomObject]@{
    ts = CG-Now
    kind = 'duplicate-observers-killed'
    count = $killed
    freed_mb = $totalMB
}
($entry | ConvertTo-Json -Compress) | Add-Content -Path (Join-Path $CG_TELEMETRY 'spike-log.jsonl') -Encoding utf8 -ErrorAction SilentlyContinue
