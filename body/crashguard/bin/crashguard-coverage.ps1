# Coverage report — maps each precursor detection service to its audit-scenario coverage.

. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

# Detector services → expected audit scenario names (or null if precursor is event-log-based and not synth-able)
$coverage = @(
    @{ detector='sentinel-tier-eval';       scenarios=@('gpu-ramping-to-crash','thermal-runaway','ram-exhaustion','cpu-saturation-noisy','transient-spike','mixed-cooldown','ollama-runaway','ram-monotonic-creep','idle-clean','gentle-game') }
    @{ detector='pcie-watch (WHEA-17)';      scenarios=@(); note='event-log driven; no synth scenario (real WHEA injection not possible)' }
    @{ detector='driver-health (nvlddmkm 14)'; scenarios=@(); note='event-log driven; verified by crisis-correlator self-test' }
    @{ detector='power-watch (display-cycle)'; scenarios=@(); note='Kernel-Power 506/507 driven; not in synth scenarios' }
    @{ detector='wudfrd-watch (id 219)';      scenarios=@(); note='Kernel-PnP 219 driven' }
    @{ detector='hid-watch (HID flap)';       scenarios=@(); note='Kernel-PnP arrive/remove driven' }
    @{ detector='trend-analyzer (slow creep)'; scenarios=@('ram-monotonic-creep'); note='partial coverage; CPU-sustained pattern only emerges in live audits' }
    @{ detector='crisis-correlator (stack)';  scenarios=@(); note='covered by tests\unit\test-crisis-correlator.ps1' }
    @{ detector='dedup-watcher (HID dupe)';   scenarios=@(); note='runtime-only' }
)

# Read available scenarios from audit-runner.ps1
$auditFile = "$env:USERPROFILE\CrashGuard\audits\audit-runner.ps1"
$auditScens = (Select-String -Path $auditFile -Pattern "^\s+'([\w-]+)'\s*=\s*@\{" -AllMatches).Matches | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique
$edgeFile = "$env:USERPROFILE\CrashGuard\audits\audit-edge-cases.ps1"
$edgeScens = (Select-String -Path $edgeFile -Pattern "^\s+'([\w-]+)'\s*=\s*@\{" -AllMatches).Matches | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique

Write-Output '=== CrashGuard Coverage Report ==='
Write-Output ''
Write-Output ('audit-runner scenarios: ' + ($auditScens -join ', '))
Write-Output ''
Write-Output ('audit-edge-cases scenarios: ' + ($edgeScens -join ', '))
Write-Output ''
Write-Output 'Detector -> scenario coverage:'

$gap = 0
foreach ($c in $coverage) {
    $present = @()
    $missing = @()
    foreach ($s in $c.scenarios) {
        if ($auditScens -contains $s -or $edgeScens -contains $s) { $present += $s } else { $missing += $s }
    }
    $status = if ($missing.Count -eq 0) { 'OK' } else { 'GAP'; $gap++ }
    $line = '  [' + $status + '] ' + $c.detector
    if ($c.scenarios.Count -gt 0) { $line += ' present=[' + ($present -join ', ') + ']' }
    if ($missing.Count -gt 0) { $line += ' MISSING=[' + ($missing -join ', ') + ']' }
    if ($c.note) { $line += ' note=' + $c.note }
    Write-Output $line
}
Write-Output ''
Write-Output ('Total detector gaps: ' + $gap)
