# HID flap watcher — detects HID device disconnect/reconnect cycles.
# Per investigation 2026-05-27: HID device flapping (especially HID-compliant headset VHF&Col02)
# is the root-cause-of-root-causes for today's BSOD chain.

[CmdletBinding()]
param([int]$IntervalSec = 30, [int]$WindowMin = 30, [int]$FlapThreshold = 3, [switch]$Once)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

CG-Log 'hid-watch' 'info' "HID-flap watcher starting PID=$PID window=${WindowMin}min threshold=$FlapThreshold"

while ($true) {
    try {
        $cutoff = (Get-Date).AddMinutes(-$WindowMin)
        # ID 410/420 = device arrival/removal in Kernel-PnP (modern Windows uses 11000/12000 in DeviceInstall too)
        $events = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-PnP'; StartTime=$cutoff} -ErrorAction SilentlyContinue | Where-Object { $_.Id -in 400,410,420,430 -or $_.Id -eq 219 }

        if ($events) {
            $byDevice = @{}
            foreach ($e in $events) {
                try {
                    $xml = [xml]$e.ToXml()
                    $drv = ($xml.Event.EventData.Data | Where-Object { $_.Name -eq 'DriverName' -or $_.Name -eq 'DeviceInstanceId' -or $_.Name -eq 'DeviceName' }) | Select-Object -First 1
                    $devKey = if ($drv) { $drv.'#text' } else { 'unknown' }
                    if (-not $byDevice.ContainsKey($devKey)) { $byDevice[$devKey] = 0 }
                    $byDevice[$devKey]++
                } catch {}
            }
            $flappers = $byDevice.GetEnumerator() | Where-Object { $_.Value -ge $FlapThreshold } | Sort-Object Value -Descending
            if ($flappers) {
                $top = $flappers | Select-Object -First 3 | ForEach-Object { "$($_.Key)=$($_.Value)" }
                CG-Log 'hid-watch' 'error' "HID flapping detected in last ${WindowMin}min: $($top -join ', ')"
                $entry = [PSCustomObject]@{
                    ts = CG-Now
                    kind = 'hid-flap'
                    window_min = $WindowMin
                    flap_count = ($flappers | Measure-Object).Count
                    top_flapper = $flappers[0].Key
                    top_count = $flappers[0].Value
                    all_flappers = ($flappers | ForEach-Object { @{ device = $_.Key; count = $_.Value } })
                }
                ($entry | ConvertTo-Json -Compress -Depth 5) | Add-Content -Path (Join-Path $CG_TELEMETRY 'spike-log.jsonl') -Encoding utf8 -ErrorAction SilentlyContinue
                # Trigger ORANGE precursor mitigations
                try { & (Join-Path $CG_BIN 'crashguard-mitigate.ps1') -Tier orange -Reasons "hid-flap: $($flappers[0].Key)" } catch {}
            }
        }
        CG-Heartbeat 'hid-watch' @{ last_check = (Get-Date).ToString('o') }
    } catch {
        CG-Log 'hid-watch' 'error' "Iter exception: $_"
    }
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSec
}
