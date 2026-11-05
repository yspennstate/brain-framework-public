# Full uninstall — stops all CrashGuard services + removes scheduled tasks.
# Does NOT delete the project folder or logs (preserves diagnostics).

[CmdletBinding()]
param([switch]$Confirm)

. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$cgProcs = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'crashguard-' }
$tasks = @('CrashGuard-Sentinel','CrashGuard-Watchdog','CrashGuard-ProcessWatcher','CrashGuard-StateExporter','CrashGuard-StartupMarker')

Write-Output 'CrashGuard uninstall preview:'
Write-Output ('  Will kill ' + (@($cgProcs).Count) + ' running CrashGuard processes')
Write-Output ('  Will remove ' + $tasks.Count + ' scheduled tasks')
Write-Output ''

if (-not $Confirm) {
    Write-Output 'Re-run with -Confirm to actually uninstall'
    return
}

# Kill processes
foreach ($p in $cgProcs) {
    try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch {}
}
Write-Output ('Stopped ' + (@($cgProcs).Count) + ' processes')

# Remove tasks
foreach ($t in $tasks) {
    schtasks /Delete /TN $t /F 2>&1 | Out-Null
}
Write-Output 'Removed scheduled tasks'

Write-Output ''
Write-Output "CrashGuard uninstalled. Project folder preserved at $env:USERPROFILE\CrashGuard for diagnostics."
Write-Output "To re-install: powershell $env:USERPROFILE\CrashGuard\bin\crashguard-launch-all.ps1"
