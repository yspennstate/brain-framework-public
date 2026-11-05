# Startup marker — writes a file on each boot with timestamp + last clean shutdown info.
# postmortem.ps1 can check this to detect "we just rebooted from a BSOD" pattern.
# Designed to be run via scheduled task at user logon (will be installed by install.ps1).

[CmdletBinding()]
param()
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$markerPath = Join-Path $CG_STATE 'startup-marker.json'
$prev = if (Test-Path $markerPath) { Get-Content $markerPath -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json } else { $null }

$bootTime = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
$now = Get-Date

# Was last shutdown clean? Check for id 41 since last marker
$lastBSOD = $null
if ($prev) {
    $prevBoot = Get-Date $prev.boot_time
    if ($bootTime -gt $prevBoot) {
        # Reboot since last marker. Check for BSOD between
        $bsod = Get-WinEvent -FilterHashtable @{LogName='System'; ID=41; ProviderName='Microsoft-Windows-Kernel-Power'; StartTime=$prevBoot; EndTime=$bootTime} -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($bsod) {
            $lastBSOD = $bsod.TimeCreated
            CG-Log 'startup-marker' 'error' ('BSOD detected between last marker (' + $prevBoot + ') and current boot (' + $bootTime + '). Crash time: ' + $bsod.TimeCreated)

            # Auto-trigger post-mortem
            try {
                & (Join-Path $CG_BIN 'crashguard-postmortem.ps1') -CrashTime $bsod.TimeCreated -WindowSec 1200 | Out-Null
                CG-Log 'startup-marker' 'info' 'Auto-postmortem invoked'
            } catch {}

            $entry = [PSCustomObject]@{
                ts = CG-Now
                kind = 'post-bsod-boot'
                crash_time = $bsod.TimeCreated
                prev_boot = $prevBoot
                current_boot = $bootTime
            }
            ($entry | ConvertTo-Json -Compress) | Add-Content -Path (Join-Path $CG_TELEMETRY 'spike-log.jsonl') -Encoding utf8 -ErrorAction SilentlyContinue
        }
    }
}

$marker = [PSCustomObject]@{
    boot_time = $bootTime
    written_at = $now
    last_bsod = $lastBSOD
    prev_marker = $prev
}
$marker | ConvertTo-Json -Depth 4 | Out-File $markerPath -Encoding utf8 -Force
CG-Log 'startup-marker' 'info' ('Startup marker written: boot=' + $bootTime)
