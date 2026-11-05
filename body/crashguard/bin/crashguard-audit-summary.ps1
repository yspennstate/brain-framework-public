# Audit summary — aggregates audits/results/audit_*.json into a single overview.

. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$dir = "$env:USERPROFILE\CrashGuard\audits\results"
if (-not (Test-Path $dir)) { Write-Output 'no results dir'; return }

$files = Get-ChildItem $dir -Filter 'audit_*.json' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime
$totalRounds = 0
$totalPassed = 0
$totalFailed = 0

foreach ($f in $files) {
    try {
        $j = Get-Content $f.FullName -Raw | ConvertFrom-Json
        $totalRounds += $j.rounds
        $totalPassed += $j.passed
        $totalFailed += $j.failed
    } catch {}
}

Write-Output ('=== Audit aggregate (' + $files.Count + ' result files) ===')
Write-Output ('Total rounds: ' + $totalRounds)
Write-Output ('Total passed: ' + $totalPassed)
Write-Output ('Total failed: ' + $totalFailed)
if ($totalRounds -gt 0) {
    $rate = [math]::Round($totalPassed / [double]$totalRounds * 100, 3)
    Write-Output ('Pass rate:   ' + $rate + '%')
}
Write-Output ''
Write-Output ('Live counter (test-counter.txt): ' + (Get-Content "$env:USERPROFILE\CrashGuard\state\test-counter.txt" -ErrorAction SilentlyContinue))
