# Pulse graph — ASCII text graph of last N pulse entries.

[CmdletBinding()]
param([int]$N = 30)

. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$pulseLog = Join-Path $CG_LOGS 'pulse.log'
if (-not (Test-Path $pulseLog)) { Write-Output 'no pulse log yet'; return }

$entries = Get-Content $pulseLog -Tail $N | Where-Object { $_ -match 'PULSE' }
if (-not $entries) { Write-Output 'no PULSE entries'; return }

Write-Output ('=== Pulse graph — last ' + $entries.Count + ' entries ===')
Write-Output 'ts                       tier     CPU% RAM% gpuT'
Write-Output '------                   -------- ---- ---- ----'
foreach ($e in $entries) {
    if ($e -match 'PULSE (\S+ \S+) tier=(\w+) cpu=(\d+)% ram=(\d+)% gpu=(\d+)C/(\d+)%') {
        $ts = $matches[1]
        $tier = $matches[2]
        $cpu = [int]$matches[3]
        $ram = [int]$matches[4]
        $gpuT = [int]$matches[5]
        $bar = switch ($tier) {
            'green'    { '|=========' }
            'yellow'   { '|==========>' }
            'orange'   { '|=============>' }
            'red'      { '|=================>' }
            'critical' { '|=====================>!' }
            default { '|?' }
        }
        # CPU bar (10-char)
        $cb = ('=' * [int]($cpu/10))
        Write-Output ('{0,-24} {1,-8} {2,4} {3,4} {4,4}  {5}' -f $ts, $tier, $cpu, $ram, $gpuT, $bar)
    }
}

# Summary
$tierHist = $entries | ForEach-Object { if ($_ -match 'tier=(\w+)') { $matches[1] } } | Group-Object | Sort-Object Count -Descending
Write-Output ''
Write-Output 'Tier distribution:'
$tierHist | ForEach-Object { Write-Output ('  ' + $_.Name + ' = ' + $_.Count) }
