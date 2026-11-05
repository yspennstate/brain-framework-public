# agentA sweep launcher. Idempotent: starts a hidden runner for every session
# that is not already running and not done/blocked/errored/stalled.
# Fired by the AgentA scheduled task (at logon + every 30 min) via launch_agentA.vbs,
# so it doubles as the self-healing watchdog. Safe to run manually (hidden consoles only).
$ErrorActionPreference = 'Continue'
$base = "$env:USERPROFILE\agentA"
$log  = "$base\logs\launcher.log"
function Log([string]$m) {
    $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
    Add-Content -Path $log -Value $line -Encoding UTF8
}

if (Test-Path "$base\STOP") { Log 'sweep skipped: STOP file present (run spin_up_agentA.ps1 to re-arm)'; exit 0 }
Log 'sweep start'

# auto-return to Fable once its session window has reset (owner wants Fable max; Opus is only the stopgap)
$fra = "$base\fable_reset_at.txt"
if ((Test-Path $fra) -and (Test-Path "$base\model.txt")) {
    $cur = (Get-Content "$base\model.txt" -TotalCount 1).Trim()
    $rt = $null; try { $rt = [datetime](Get-Content $fra -TotalCount 1) } catch {}
    if ($cur -eq 'claude-opus-4-8' -and $rt -and ((Get-Date) -ge $rt)) {
        'claude-fable-5' | Set-Content "$base\model.txt" -Encoding ascii
        Remove-Item $fra -Force -ErrorAction SilentlyContinue
        Log "Fable window reset ($($rt.ToString('HH:mm'))) passed -> switched model.txt back to claude-fable-5"
    }
}

# standing rule: the deployment's protected production senders must never run on this machine
Get-CimInstance Win32_Process -Filter "name='python.exe' OR name='pythonw.exe'" |
    Where-Object { $_.CommandLine -match 'your_protected_service|your_critical_process' } |
    ForEach-Object { Log "killing stray protected-process pid=$($_.ProcessId)"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$defs = Get-Content "$base\sessions.json" -Raw | ConvertFrom-Json
$psExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$proj  = "$env:USERPROFILE\.claude\projects\{{BRAIN_SLUG}}"
$codexDir = "$env:USERPROFILE\.codex\sessions"
$HANG_MIN = 45
$launched = 0

# --- hang-sweep: catch a STUCK turn (which the crash-watchdog can't see, since the pid is alive) ---
# A runner whose current-turn transcript has been silent > HANG_MIN while a matching claude/codex
# child is still alive is a hung turn. Kill just that runner tree so the launch loop relaunches it
# fresh. Backoff/pause sleeps have NO child process, so they are never mistaken for hangs.
foreach ($def in $defs) {
    $n = $def.n
    if (Test-Path "$base\done\$n.done")     { continue }
    if (Test-Path "$base\state\$n.blocked") { continue }
    $pidF = "$base\state\$n.pid"
    if (-not (Test-Path $pidF)) { continue }
    $rpid = 0; try { $rpid = [int](Get-Content $pidF -TotalCount 1) } catch {}
    if ($rpid -le 0) { continue }
    $rp = Get-Process -Id $rpid -ErrorAction SilentlyContinue
    if (-not ($rp -and $rp.ProcessName -eq 'powershell')) { continue }

    $head = $def.resumeId
    if ($def.type -eq 'claude') {
        $hf = "$base\state\$n.head"; if (Test-Path $hf) { $h = (Get-Content $hf -TotalCount 1); if ($h) { $head = $h.Trim() } }
        $tf = Join-Path $proj "$head.jsonl"
    } else {
        $tf = (Get-ChildItem $codexDir -Recurse -Filter "*$($def.resumeId)*.jsonl" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
    }
    if (-not $tf -or -not (Test-Path $tf)) { continue }
    $ageMin = ((Get-Date) - (Get-Item $tf).LastWriteTime).TotalMinutes
    if ($ageMin -lt $HANG_MIN) { continue }

    $child = Get-CimInstance Win32_Process | Where-Object {
        ($_.Name -eq 'claude.exe' -or $_.Name -eq 'node.exe' -or $_.Name -match '^codex') -and
        $_.CommandLine -and ($_.CommandLine -match [regex]::Escape($head) -or $_.CommandLine -match [regex]::Escape($def.resumeId))
    } | Select-Object -First 1
    if (-not $child) { continue }  # no live child = healthy backoff/pause, leave it

    Log "HANG: runner $n ($($def.key)) transcript silent $([Math]::Round($ageMin))m with live child pid=$($child.ProcessId); killing runner tree for relaunch"
    taskkill /T /F /PID $rpid 2>$null | Out-Null
    Remove-Item $pidF -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

foreach ($def in $defs) {
    $n = $def.n
    if (Test-Path "$base\done\$n.done")      { continue }
    if (Test-Path "$base\state\$n.blocked")  { continue }
    if (Test-Path "$base\state\$n.error")    { continue }
    if (Test-Path "$base\state\$n.stalled")  { continue }

    $pidF = "$base\state\$n.pid"
    if (Test-Path $pidF) {
        $rpid = 0
        try { $rpid = [int](Get-Content $pidF -TotalCount 1) } catch {}
        if ($rpid -gt 0) {
            $p = Get-Process -Id $rpid -ErrorAction SilentlyContinue
            # verify by COMMAND LINE, not just name: Windows reuses PIDs, so a dead runner's
            # pid can be held by an unrelated powershell.exe and make us skip a down session.
            if ($p -and $p.ProcessName -eq 'powershell') {
                $cl = (Get-CimInstance Win32_Process -Filter "ProcessId=$rpid" -ErrorAction SilentlyContinue).CommandLine
                if ($cl -match "run_one\.ps1 -N $n\b") { continue }  # genuinely this session's runner
            }
            Remove-Item $pidF -ErrorAction SilentlyContinue  # stale/reused pid -> fall through and relaunch
        }
    }

    # WMI process creation: hidden console at creation (ShowWindow=0, no flash) and
    # parented outside any caller job object, so runners survive the launching shell/task.
    $si = ([wmiclass]'Win32_ProcessStartup').CreateInstance()
    $si.ShowWindow = 0
    $cmd = "$psExe -NoProfile -ExecutionPolicy Bypass -File $base\run_one.ps1 -N $n"
    $r = ([wmiclass]'Win32_Process').Create($cmd, "$env:USERPROFILE", $si)
    if ($r.ReturnValue -eq 0) {
        Log "launched runner $n ($($def.key)) pid=$($r.ProcessId)"
        $launched++
    } else {
        Log "FAILED to launch runner $n ($($def.key)) wmi rc=$($r.ReturnValue)"
    }
    Start-Sleep -Seconds 5
}
Log "sweep done, launched $launched"
(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | Set-Content "$base\state\last_sweep.txt"
