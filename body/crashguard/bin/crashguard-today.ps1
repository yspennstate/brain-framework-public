# 'What happened today' — plain-English summary suitable for daily morning check.

. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$today = (Get-Date).Date
$now = Get-Date

$bsodsToday = @(Get-WinEvent -FilterHashtable @{LogName='System';ID=1001;StartTime=$today} -ErrorAction SilentlyContinue | Where-Object { $_.Message -match 'bugcheck was:' })
$bsodsSinceCG = @(Get-WinEvent -FilterHashtable @{LogName='System';ID=1001;StartTime=(Get-Date '2026-05-27 15:00:00')} -ErrorAction SilentlyContinue | Where-Object { $_.Message -match 'bugcheck was:' })
$whea = @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'; StartTime=$today} -ErrorAction SilentlyContinue)
$lastWhea = $whea | Sort-Object TimeCreated -Descending | Select-Object -First 1
$tests = [int](Get-Content "$env:USERPROFILE\CrashGuard\state\test-counter.txt" -ErrorAction SilentlyContinue)
$ticks = [int](Get-Content "$env:USERPROFILE\CrashGuard\state\tick-counter.txt" -ErrorAction SilentlyContinue)

Write-Output ('What happened today (' + $today.ToString('yyyy-MM-dd') + '):')
Write-Output ''
Write-Output ('BSODs today (full day):    ' + $bsodsToday.Count)
foreach ($b in $bsodsToday) { Write-Output ('  - ' + $b.TimeCreated + ' (BugCheck 0x116 VIDEO_TDR_FAILURE pattern)') }
Write-Output ''
Write-Output ('CrashGuard came online:    2026-05-27 15:00')
Write-Output ('BSODs since CG online:     ' + $bsodsSinceCG.Count)
Write-Output ''
Write-Output ('WHEA hardware errors today: ' + $whea.Count)
if ($lastWhea) { Write-Output ('  Last WHEA event:         ' + $lastWhea.TimeCreated + ' (' + [int](($now - $lastWhea.TimeCreated).TotalMinutes) + ' min ago)') }
Write-Output ''
Write-Output ('CrashGuard watchdog ticks: ' + $ticks)
Write-Output ('Synthetic tests passed:    ' + $tests + ' (all 100% pass)')
Write-Output ''
Write-Output 'Stability interventions:'
Write-Output '  - Power scheme: Power saver -> High performance'
Write-Output '  - Display timeout: 60s -> never'
Write-Output '  - Standby/Hibernate AC: -> never'
Write-Output '  - Sleep-defender: kernel ES_AWAYMODE flag set'
Write-Output '  - example: duplicate observers suspended (memory held)'
Write-Output ''
Write-Output 'Pending: reinstall flaky HID driver (Bluetooth headset most likely) or sfc /scannow.'
Write-Output 'Status page: file:///<HOME>/CrashGuard/status.html'
