# Recovery sweeper — rolls back stale mitigations.
# Why: mitigate.ps1 records actions in active-mitigations.json. The sentinel calls Rollback when
# de-escalating, but if a service restarts mid-cycle, mitigations can persist. This sweeper
# walks active-mitigations.json every 5 min and rolls back records older than 10 min IF tier is green.

[CmdletBinding()]
param([int]$IntervalSec = 300, [int]$MaxAgeSec = 600, [switch]$Once)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$activePath = Join-Path $CG_STATE 'active-mitigations.json'

CG-Log 'recovery' 'info' ('Recovery sweeper starting PID=' + $PID + ' interval=' + $IntervalSec + 's max-age=' + $MaxAgeSec + 's')

while ($true) {
    try {
        $sntHb = Get-Content (Join-Path $CG_STATE 'sentinel.heartbeat') -ErrorAction SilentlyContinue | ConvertFrom-Json
        $tier = if ($sntHb) { $sntHb.tier } else { 'unknown' }

        if ($tier -eq 'green' -and (Test-Path $activePath)) {
            $active = Get-Content $activePath -ErrorAction SilentlyContinue | ConvertFrom-Json
            if ($active -and $active.Count -gt 0) {
                $now = Get-Date
                $remaining = @()
                $rolledBack = 0
                foreach ($m in $active) {
                    if (-not $m.applied_at) { $remaining += $m; continue }
                    $age = ($now - (Get-Date $m.applied_at)).TotalSeconds
                    if ($age -gt $MaxAgeSec) {
                        # Roll back specific action
                        try {
                            switch ($m.action) {
                                'throttle' {
                                    $proc = Get-Process -Id $m.pid -ErrorAction SilentlyContinue
                                    $same = if ($proc) { CG-IsSameProcess $proc $m.name $m.process_started_at } else { $false }
                                    if ($proc -and $same) {
                                        $priority = CG-ResolvePriorityClass $m.before
                                        if ($null -eq $priority) {
                                            # Old corrupt records sometimes omitted before.
                                            # Normal is the safe recovery default for a process
                                            # CrashGuard itself demoted to BelowNormal.
                                            $priority = [System.Diagnostics.ProcessPriorityClass]::Normal
                                            CG-Log 'recovery' 'warn' ('Invalid prior priority for pid=' + $m.pid + '; restoring Normal')
                                        }
                                        $proc.PriorityClass = $priority
                                    } elseif ($proc) {
                                        CG-Log 'recovery' 'warn' ('Skipped stale PID-reused throttle record pid=' + $m.pid + ' expected=' + $m.name + ' actual=' + $proc.ProcessName)
                                    }
                                    $rolledBack++
                                }
                                'suspend' {
                                    $proc = Get-Process -Id $m.pid -ErrorAction SilentlyContinue
                                    if ($proc -and (CG-IsSameProcess $proc $m.name $m.process_started_at)) {
                                        [void](CG-Resume $m.pid)
                                    }
                                    $rolledBack++
                                }
                                'suspend_browser_gpu' {
                                    $proc = Get-Process -Id $m.pid -ErrorAction SilentlyContinue
                                    if ($proc -and (CG-IsSameProcess $proc $m.name $m.process_started_at)) {
                                        [void](CG-Resume $m.pid)
                                    }
                                    $rolledBack++
                                }
                                default {
                                    # gpu_power_limit / gpu_clock_lock / killed - leave in record but remove from active
                                    $rolledBack++
                                }
                            }
                            CG-Log 'recovery' 'info' ('Rolled back ' + $m.action + ' pid=' + $m.pid + ' age=' + [int]$age + 's')
                        } catch {
                            CG-Log 'recovery' 'error' ('Rollback failed: ' + $_)
                            $remaining += $m
                        }
                    } else {
                        $remaining += $m
                    }
                }
                if ($rolledBack -gt 0) {
                    CG-SaveJson $activePath @($remaining)
                    CG-Log 'recovery' 'warn' ('Recovery swept ' + $rolledBack + ' stale mitigations; remaining=' + $remaining.Count)
                }
            }
        }
        CG-Heartbeat 'recovery' @{ tier_at_check = $tier }
    } catch {
        CG-Log 'recovery' 'error' ('Iter exception: ' + $_)
    }
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSec
}
