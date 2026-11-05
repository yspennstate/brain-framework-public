# Duplicate-observer auto-watcher — catches and suspends duplicate worker-process instances.
# Why: auto_observer_restart_daemon races with restart_all_observers.ps1 (and any future re-start),
# producing 2x instances of every observer. Each dup wastes ~50-200MB RAM and adds CPU pressure.
# Read-only on the watched processes — never edit those .py files. Suspend only.

[CmdletBinding()]
param([int]$IntervalSec = 30, [switch]$Once, [switch]$DryRun)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

CG-Log 'dedup-watcher' 'info' "Dedup watcher starting PID=$PID interval=${IntervalSec}s dryRun=$DryRun"

# Track which PIDs we've already suspended so we don't re-process them every iteration.
$alreadyHandled = New-Object 'System.Collections.Generic.HashSet[int]'

while ($true) {
    try {
        $pythons = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue
        $byScript = @{}
        foreach ($p in $pythons) {
            if ($p.CommandLine -match '(\w+_observer\.py|\w+_recorder\.py|\w+_streamer\.py|\w+_bot\.py|\w+_loop\.py|\w+_logger\.py|\w+_watchdog\.py)') {
                $s = $matches[1]
                if (-not $byScript.ContainsKey($s)) { $byScript[$s] = New-Object 'System.Collections.Generic.List[PSCustomObject]' }
                $byScript[$s].Add([PSCustomObject]@{ Pid=$p.ProcessId; Start=[datetime]$p.CreationDate; CmdShort=$p.CommandLine.Substring(0,[Math]::Min(80,$p.CommandLine.Length)) })
            }
        }

        $newSuspended = 0
        foreach ($s in $byScript.Keys) {
            $list = $byScript[$s] | Where-Object { -not $alreadyHandled.Contains($_.Pid) } | Sort-Object Start
            if ($list.Count -lt 2) { continue }
            # Keep oldest. Suspend the rest.
            $keep = $list[0]
            $toSuspend = $list | Select-Object -Skip 1
            foreach ($t in $toSuspend) {
                if (-not $DryRun) {
                    $ok = CG-Suspend $t.Pid
                    if ($ok) {
                        [void]$alreadyHandled.Add($t.Pid)
                        $newSuspended++
                        CG-Log 'dedup-watcher' 'warn' "Suspended duplicate $s pid=$($t.Pid) (kept oldest pid=$($keep.Pid))"
                    }
                } else {
                    CG-Log 'dedup-watcher' 'info' "DRY-RUN: would suspend $s pid=$($t.Pid)"
                }
            }
        }

        if ($newSuspended -gt 0) {
            $entry = [PSCustomObject]@{
                ts = CG-Now
                kind = 'dedup-auto-suspend'
                count = $newSuspended
            }
            ($entry | ConvertTo-Json -Compress) | Add-Content -Path (Join-Path $CG_TELEMETRY 'spike-log.jsonl') -Encoding utf8 -ErrorAction SilentlyContinue
        }

        CG-Heartbeat 'dedup-watcher' @{
            new_suspended_this_iter = $newSuspended
            total_handled = $alreadyHandled.Count
        }
    } catch {
        CG-Log 'dedup-watcher' 'error' "Iter exception: $_"
    }
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSec
}
