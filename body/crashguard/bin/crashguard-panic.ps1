# Panic button — user runs this when they suspect something is wrong.
# Captures full forensic dump + applies max mitigations immediately.

[CmdletBinding()]
param([string]$Reason = 'user-invoked-panic')

. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

Write-Output ('=== PANIC TRIGGERED at ' + (CG-Now) + ' ===')
Write-Output ('Reason: ' + $Reason)
Write-Output ''

# 1. Forensic dump
Write-Output 'Step 1: forensic dump...'
$dumpDir = & (Join-Path $CG_BIN 'crashguard-forensic-dump.ps1') -Reason ('PANIC: ' + $Reason) 2>&1 | Select-Object -Last 1
Write-Output ('  ' + $dumpDir)

# 2. Trigger critical mitigations
Write-Output 'Step 2: critical mitigations...'
& (Join-Path $CG_BIN 'crashguard-mitigate.ps1') -Tier critical -Reasons ('PANIC: ' + $Reason)

# 3. Log to spike-log
$entry = [PSCustomObject]@{
    ts = CG-Now
    kind = 'PANIC'
    reason = $Reason
    forensic_dir = $dumpDir
}
($entry | ConvertTo-Json -Compress) | Add-Content -Path (Join-Path $CG_TELEMETRY 'spike-log.jsonl') -Encoding utf8 -ErrorAction SilentlyContinue

Write-Output ''
Write-Output 'PANIC actions applied. Review the forensic dump above.'
Write-Output 'To resume normal operation: sentinel will de-escalate when load drops.'
