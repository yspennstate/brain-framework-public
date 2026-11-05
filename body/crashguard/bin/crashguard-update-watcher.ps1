# Update-watcher — alerts on Windows Update package installs/stages.
# Per investigation 2026-05-27: KB5087051 + KB5089549 (failed) staged during today's BSOD storm.
# Future crashes correlated with Windows Update activity become easier to root-cause.

[CmdletBinding()]
param([int]$IntervalSec = 60, [switch]$Once)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$lastSeenFile = Join-Path $CG_STATE 'update-watcher.last-seen.txt'
$lastSeen = if (Test-Path $lastSeenFile) { Get-Date (Get-Content $lastSeenFile) } else { (Get-Date).AddMinutes(-2) }

CG-Log 'update-watcher' 'info' "Update watcher starting PID=$PID interval=${IntervalSec}s"

while ($true) {
    try {
        $events = Get-WinEvent -LogName 'Setup' -ErrorAction SilentlyContinue | Where-Object { $_.TimeCreated -gt $lastSeen -and $_.Id -in 1,2,3,4 }
        if ($events) {
            foreach ($e in $events | Sort-Object TimeCreated) {
                $msg = if ($e.Message) { ($e.Message -replace '\r?\n',' ').Substring(0, [Math]::Min(150, $e.Message.Length)) } else { '' }
                $idLabel = switch ($e.Id) { 1 {'INIT'}; 2 {'SUCCESS'}; 3 {'FAILED'}; 4 {'REBOOT-NEEDED'}; default {"id$($e.Id)"} }
                CG-Log 'update-watcher' 'info' "Setup-$idLabel at $($e.TimeCreated): $msg"
                $entry = [PSCustomObject]@{
                    ts = CG-Now
                    kind = 'windows-update-activity'
                    setup_event_id = $e.Id
                    setup_event_label = $idLabel
                    event_time = $e.TimeCreated
                    message = $msg
                }
                ($entry | ConvertTo-Json -Compress) | Add-Content -Path (Join-Path $CG_TELEMETRY 'spike-log.jsonl') -Encoding utf8 -ErrorAction SilentlyContinue
            }
            $lastSeen = ($events | Sort-Object TimeCreated | Select-Object -Last 1).TimeCreated
            Set-Content -Path $lastSeenFile -Value $lastSeen.ToString('o') -Encoding utf8 -Force
        }
        CG-Heartbeat 'update-watcher' @{ last_seen = $lastSeen.ToString('o') }
    } catch {
        CG-Log 'update-watcher' 'error' "Iter exception: $_"
    }
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSec
}
