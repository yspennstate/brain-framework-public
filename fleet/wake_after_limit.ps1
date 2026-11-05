# wake_after_limit.ps1 - one-shot: re-wake the three sessions that died on the 2026-07-12 10:18
# usage-limit wall ("resets 5:20pm local-timezone"), AFTER the reset. Scheduled for 17:25 by the
# FleetWakeAfterLimit task (windowless via wake_after_limit.vbs); self-unregisters when done.
# Idempotent by construction: nudge wake routes through launch_one.ps1, whose alive-guard (exit 3)
# refuses anything already running and whose lock (exit 4) absorbs double-fire. ASCII only.
$fleet = "$env:USERPROFILE\.claude\fleet"
$log = Join-Path $fleet 'watch\wake_after_limit.log'
function Log($m) { Add-Content -Path $log -Value ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) -Encoding UTF8 }
Log 'invoked (post-limit-reset one-shot)'
# gate: the rolling limit's reset moved 17:20 -> 18:20 during the afternoon (the dream's REM
# session bounced at 14:07 with 'resets 6:20pm'); an early run must do nothing
if ((Get-Date) -lt (Get-Date '2026-07-12 18:20')) { Log 'gate: before 18:20 reset -> no-op (task stays registered)'; exit 0 }
$ids = @(
    '<session-id>',
    '<session-id>',
    '<session-id>',
    '<session-id>'
)
# 524e5946 (A7) folded in 15:50: its 15:45 one-attempt wake bounced (process attached and exited
# within ~5 min - the giant-context-resume wall signature); per the committed plan, no retry
# before the reset. Its launch lock from 15:45 will be stale (>10 min) by 18:30.
foreach ($id in $ids) {
    $out = & python (Join-Path $fleet 'nudge.py') wake $id 2>&1 | Out-String
    Log ("wake {0} rc={1}: {2}" -f $id.Substring(0,8), $LASTEXITCODE, ($out -replace "\r?\n", ' | '))
    Start-Sleep -Seconds 10
}
try { Unregister-ScheduledTask -TaskName 'FleetWakeAfterLimit' -Confirm:$false; Log 'task self-unregistered' } catch { Log ("unregister failed: " + $_.Exception.Message) }
Log 'done'
exit 0
