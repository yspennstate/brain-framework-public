# autorecover.ps1 - bring the fleet back after the COMPUTER died, and only then.
# Runs at logon (via autorecover.vbs, windowless). Four gates, in order, before any medicine:
#   1. opt-in flag       fleet\AUTORECOVER_ON must exist (owner can turn the whole thing off)
#   2. once per boot     a marker keyed to LastBootUpTime, written BEFORE launching so a crash
#                        mid-recovery can never loop the medicine
#   3. unclean boot      EventLog 6008 (unexpected shutdown) or Kernel-Power 41 logged at this
#                        boot - a clean reboot/logon does NOTHING
#   4. relaunch's own guards - Test-Alive (skips live sessions) + the recovery pass-lock
# Doctrine: the 2026-07-12 duplicates came from recovery machinery, not crashes. This script
# prefers doing nothing; every exit path logs why. ASCII only.
$ErrorActionPreference = 'Continue'
$fleet = "$env:USERPROFILE\.claude\fleet"
$log = Join-Path $fleet 'watch\autorecover.log'
function Log($m) {
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
    Add-Content -Path $log -Value $line -Encoding UTF8
}
Log "invoked (interactive=$([Environment]::UserInteractive))"

# gate 1: opt-in
if (-not (Test-Path (Join-Path $fleet 'AUTORECOVER_ON'))) { Log 'gate1: flag off -> no-op'; exit 0 }

# gate 2: once per boot (marker written before any action)
$boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
$marker = Join-Path $fleet 'watch\autorecover_lastboot.txt'
$bootStamp = $boot.ToString('o')
$prev = ''
if (Test-Path $marker) { $prev = (Get-Content $marker -Raw -Encoding UTF8).Trim() }
if ($prev -eq $bootStamp) { Log "gate2: already ran for boot $bootStamp -> no-op"; exit 0 }
Set-Content -Path $marker -Value $bootStamp -Encoding UTF8

# gate 3: was this boot preceded by a crash? 6008 and Kernel-Power 41 are logged AT the boot
# that follows the crash, so look for them within 10 minutes after boot time.
$unclean = $null
try {
    $win = $boot.AddMinutes(10)
    $unclean = Get-WinEvent -FilterHashtable @{LogName = 'System'; Id = 6008, 41 } -MaxEvents 40 -ErrorAction Stop |
        Where-Object { $_.TimeCreated -ge $boot.AddMinutes(-2) -and $_.TimeCreated -le $win } |
        Select-Object -First 1
} catch { $unclean = $null }
if ($null -eq $unclean) { Log "gate3: boot $bootStamp looks CLEAN (no 6008/41 at boot) -> no-op"; exit 0 }
Log ("gate3: UNCLEAN boot confirmed (event {0} at {1})" -f $unclean.Id, $unclean.TimeCreated)

# settle: let the desktop, network and credential vault come up before opening terminals
Start-Sleep -Seconds 45

# medicine: one faithful relaunch pass. Its Test-Alive skips anything already running and its
# pass-lock absorbs any overlapping manual recovery. Output goes to the log.
Log 'running relaunch.ps1 -Faithful'
try {
    $out = & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $fleet 'relaunch.ps1') -Faithful 2>&1 | Out-String
    Log ("relaunch output: " + ($out -replace "\r?\n", ' | '))
} catch {
    Log ("relaunch FAILED: " + $_.Exception.Message)
}
Log 'done'
exit 0
