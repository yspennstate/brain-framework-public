# WUDFRd / Kernel-PnP id=219 watcher — catches the touchpad/HID driver-load failure pattern
# that preceded today's WHEA-17 storm and BSODs.
# Specific root cause found 2026-05-27: HID\HID_DEVICE_SYSTEM_VHF&Col02 (Precision Touchpad / Lenovo HID)
# failing repeatedly with status 0xC0000225 STATUS_NOT_FOUND.

[CmdletBinding()]
param([int]$IntervalSec = 15, [int]$ClusterThreshold = 3, [switch]$Once)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$lastSeenFile = Join-Path $CG_STATE 'wudfrd-watch.last-seen.txt'
$lastSeen = if (Test-Path $lastSeenFile) { Get-Date (Get-Content $lastSeenFile) } else { (Get-Date).AddMinutes(-5) }

CG-Log 'wudfrd-watch' 'info' "WUDFRd watcher starting PID=$PID interval=${IntervalSec}s cluster-threshold=$ClusterThreshold"

while ($true) {
    try {
        $events = Get-WinEvent -FilterHashtable @{LogName='System'; ID=219; ProviderName='Microsoft-Windows-Kernel-PnP'; StartTime=$lastSeen.AddSeconds(1)} -ErrorAction SilentlyContinue
        if ($events) {
            $byDriver = @{}
            foreach ($e in $events) {
                $xml = [xml]$e.ToXml()
                $drv = ($xml.Event.EventData.Data | Where-Object { $_.Name -eq 'DriverName' }).'#text'
                $status = ($xml.Event.EventData.Data | Where-Object { $_.Name -eq 'Status' }).'#text'
                $key = "$drv (status $status)"
                if (-not $byDriver.ContainsKey($key)) { $byDriver[$key] = 0 }
                $byDriver[$key]++
            }
            $maxCount = ($byDriver.Values | Measure-Object -Maximum).Maximum

            CG-Log 'wudfrd-watch' 'warn' "WUDFRd failure batch detected: $($events.Count) failures across $($byDriver.Count) drivers"
            $entry = [PSCustomObject]@{
                ts = CG-Now
                kind = 'wudfrd-failure'
                count = $events.Count
                top_driver = ($byDriver.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 1).Key
                by_driver = $byDriver
            }
            ($entry | ConvertTo-Json -Compress -Depth 5) | Add-Content -Path (Join-Path $CG_TELEMETRY 'spike-log.jsonl') -Encoding utf8 -ErrorAction SilentlyContinue

            if ($maxCount -ge $ClusterThreshold) {
                CG-Log 'wudfrd-watch' 'error' "WUDFRd cluster: $maxCount failures of same driver - WHEA precursor pattern. Triggering RED mitigations."
                try { & (Join-Path $CG_BIN 'crashguard-mitigate.ps1') -Tier red -Reasons "WUDFRd-cluster: $($events.Count) failures" } catch {}
            }
            $lastSeen = ($events | Sort-Object TimeCreated | Select-Object -Last 1).TimeCreated
            Set-Content -Path $lastSeenFile -Value $lastSeen.ToString('o') -Encoding utf8 -Force
        }
        CG-Heartbeat 'wudfrd-watch' @{ last_seen = $lastSeen.ToString('o') }
    } catch {
        CG-Log 'wudfrd-watch' 'error' "Iter exception: $_"
    }
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSec
}
