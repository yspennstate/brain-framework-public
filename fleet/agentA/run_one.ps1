# agentA per-session runner. Loops one AI session (claude or codex) headlessly:
# resume -> full agentic turn -> resume again, until the session writes its
# done/blocked file, a global STOP appears, or it errors out repeatedly.
# Runs inside a HIDDEN console (created by start_agentA.ps1 via WMI); every
# child (claude.exe / codex and their subprocesses) inherits that hidden console.
#
# Rate-limit design (the 2026-07-06 19:09 launch failed without this): resuming
# a session cold re-uploads its whole context (0.6-3M tokens cache-creation), so
# starting many runners at once slams the account limit and synchronized backoffs
# keep the collision waves aligned. Therefore: (N-1)*90s start stagger per runner
# and jitter on every sleep.
param([Parameter(Mandatory=$true)][int]$N)

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [Text.Encoding]::UTF8

$base   = "$env:USERPROFILE\agentA"
$defs   = Get-Content "$base\sessions.json" -Raw | ConvertFrom-Json
$def    = $defs | Where-Object { $_.n -eq $N }
if (-not $def) { exit 1 }

$log     = "$base\logs\$($N)_$($def.key).log"
$doneF   = "$base\done\$N.done"
$blockF  = "$base\state\$N.blocked"
$errF    = "$base\state\$N.error"
$stallF  = "$base\state\$N.stalled"
$headF   = "$base\state\$N.head"
$pidF    = "$base\state\$N.pid"
$errLog  = "$base\logs\$($N)_$($def.key).stderr.log"
$turnOut = "$base\state\$N.turnout.txt"
$rawOut  = "$base\state\$N.out.json"
$lastMsg = "$base\state\$N.lastmsg.txt"
$stopF   = "$base\STOP"
$modelFile = "$base\model.txt"   # shared primary model for all claude runners (owner: Fable first, Opus when Fable usage is out)
$OPUS    = 'claude-opus-4-8'
$FABLE   = 'claude-fable-5'
$meshScript = "$env:USERPROFILE\brain-repo\12_cognitive_architecture\agent_mesh\agent_mesh.py"
$meshAgent  = "agentA-$N-$($def.key)"
$env:AGENT_MESH_ID = $meshAgent
$env:AGENT_MESH_SESSION = '' + $def.resumeId

function Log([string]$m) {
    $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
    Add-Content -Path $log -Value $line -Encoding UTF8
}
# STOP-aware sleep so the kill switch stays responsive during long waits
function SleepAware([int]$sec) {
    $left = $sec
    while ($left -gt 0) {
        if (Test-Path $stopF) { return $false }
        $chunk = [Math]::Min(20, $left)
        Start-Sleep -Seconds $chunk
        $left -= $chunk
    }
    return $true
}

$PID | Set-Content $pidF
Set-Location "$env:USERPROFILE"
Log "runner start pid=$PID key=$($def.key) type=$($def.type) resumeId=$($def.resumeId)"
if (Test-Path $meshScript) {
    & python $meshScript hello --agent $meshAgent --runtime $def.type --session $def.resumeId --task "agentA session $N ($($def.key)): continuing its standing research program" --ttl 45m 2>> $errLog | Out-Null
    if ($LASTEXITCODE -ne 0) { Log 'agent mesh registration failed - refusing shared mutation'; Remove-Item $pidF -ErrorAction SilentlyContinue; exit 3 }
    & python $meshScript claim --agent $meshAgent --resource "topic:agentA/session/$N" --mode exclusive --purpose "Own agentA session $N lifecycle and sentinels" --ttl 45m 2>> $errLog | Out-Null
    if ($LASTEXITCODE -ne 0) { Log 'agent mesh lifecycle claim conflicted - duplicate/split-brain runner refused'; Remove-Item $pidF -ErrorAction SilentlyContinue; exit 3 }
} else {
    Log 'agent mesh missing - refusing to start a shared-state research runner'
    Remove-Item $pidF -ErrorAction SilentlyContinue
    exit 3
}

# resolve CLIs (npm shims live in %APPDATA%\npm)
$claudeCmd = $null
foreach ($cand in @("$env:APPDATA\npm\claude.cmd", 'claude')) {
    $gc = Get-Command $cand -ErrorAction SilentlyContinue
    if ($gc) { $claudeCmd = $gc.Source; break }
}
$codexCmd = $null
foreach ($cand in @("$env:APPDATA\npm\codex.cmd", 'codex')) {
    $gc = Get-Command $cand -ErrorAction SilentlyContinue
    if ($gc) { $codexCmd = $gc.Source; break }
}
if ($def.type -eq 'claude' -and -not $claudeCmd) { Log 'FATAL: claude CLI not found'; 'claude CLI not found' | Set-Content $errF; exit 1 }
if ($def.type -eq 'codex'  -and -not $codexCmd)  { Log 'FATAL: codex CLI not found';  'codex CLI not found'  | Set-Content $errF; exit 1 }

# start stagger: spreads the expensive cold resumes out (also applies after
# reboot respins, when every session's server-side cache is cold again)
$stagger = ($N - 1) * 90
if ($stagger -gt 0) {
    Log "start stagger: sleeping $stagger s to avoid a resume thundering herd"
    if (-not (SleepAware $stagger)) { Log 'STOP during stagger - exiting'; Remove-Item $pidF -ErrorAction SilentlyContinue; exit 0 }
}

$continuePrompt = @"
You are $meshAgent, a CONTINUOUS COLLABORATOR in the owner's shared brain, not a fresh research bot. First wake up as yourself: use the brain context, your transcript, and the memories for your area. Then continue your standing research program with maximum effort; prove more, strengthen results, write the real artifacts, verify them, and push completed work to the right repository. Do not merely re-verify the old crash checkpoint.

You are one mind in a live fleet. The agent-mesh digest immediately above is current social state. Before editing files, git, memories, deployments, or external state, acquire the complete anticipated narrow write/exclusive claim set in one agent_mesh.py call using agent id $meshAgent. Immediately before each mutation or commit, run agent_mesh.py assert on the exact target; after expiry, stop, reacquire, and reread current state. If a claim conflicts, do not bypass it or wait while retaining claims the holder needs: message the holder, release/retry, take a disjoint scope/worktree, or work read-only. Pulse around long work and send structured handoffs. A handoff is incomplete until accepted/rejected; do not release, leave, or write a done/blocked sentinel while one is pending--cancel it explicitly if you withdraw it. Separate worktrees/branches and one validating integrator are required for concurrent writers.

Feed the durable brain selectively through `python $env:USERPROFILE\.claude\dreaming\brain.py remember ...`; never manually race another session on MEMORY.md. Routine progress belongs in mesh status, not memory. Write in the human voice. You have no user watching: decide and act within scope. Keep local compute light and under about 20 GB; never use the local GPU; use the remote research box for heavy compute after reading its rules; never touch protected production; never create a visible or flashing terminal window.

End each turn with a brief status; the runner resumes you automatically. Only write $env:USERPROFILE\agentA\done\$N.done if the research goal is genuinely exhausted. If only the owner can unblock you, write $env:USERPROFILE\agentA\state\$N.blocked.
"@

$fails = 0
$shorts = 0
$iter = 0
while ($true) {
    if (Test-Path $stopF)  { Log 'global STOP present - exiting'; break }
    if (Test-Path $doneF)  { Log 'done file present - task complete, exiting'; break }
    if (Test-Path $blockF) { Log 'blocked file present - exiting until owner clears it'; break }
    $iter++

    # first-ever turn gets the recovery prompt; later turns get the continue prompt
    $freshLineage = -not (Test-Path $headF)
    if ($freshLineage) { $prompt = $def.prompt } else { $prompt = $continuePrompt }
    if (Test-Path $meshScript) {
        & python $meshScript pulse --agent $meshAgent --status working --task "agentA turn ${iter}: $($def.key) research" 2>> $errLog | Out-Null
        $meshPulseCode = $LASTEXITCODE
        $meshDigest = (& python $meshScript digest --agent $meshAgent 2>> $errLog | Out-String)
        $meshDigestCode = $LASTEXITCODE
        if ($meshPulseCode -ne 0 -or $meshDigestCode -ne 0 -or -not $meshDigest.Trim()) {
            Log 'agent mesh pulse/digest failed - delaying turn rather than permitting uncoordinated mutation'
            if (-not (SleepAware 120)) { break }
            continue
        }
        $prompt = $meshDigest + "`n---`n`n" + $prompt
    }

    $head = $def.resumeId
    if (Test-Path $headF) {
        $h = (Get-Content $headF -TotalCount 1)
        if ($h) { $head = $h.Trim() }
    }

    Log "turn $iter starting (head=$head, fresh=$freshLineage)"
    $t0 = Get-Date

    if ($def.type -eq 'claude') {
        # primary model comes from the shared model.txt (default Fable); once Fable's usage
        # is exhausted a runner flips that file to Opus and every runner follows on the next turn.
        $primary = $FABLE
        if (Test-Path $modelFile) { $mm = (Get-Content $modelFile -TotalCount 1); if ($mm) { $primary = $mm.Trim() } }
        # owner: run the fleet on Fable 5 with MAX thinking effort
        $cargs = @('-p', '--output-format', 'json', '--dangerously-skip-permissions', '--model', $primary, '--effort', 'max')
        # belt-and-suspenders: claude's own auto-fallback covers overload/limit mid-turn while on Fable
        if ($primary -eq $FABLE) { $cargs += @('--fallback-model', $OPUS) }
        $cargs += @('--resume', $head)
        Log "  model=$primary effort=max$(if ($primary -eq $FABLE) { ' (fallback ' + $OPUS + ')' })"
        $raw = ($prompt | & $claudeCmd @cargs 2>> $errLog | Out-String)
        $code = $LASTEXITCODE
        $dur = ((Get-Date) - $t0).TotalSeconds
        if ($raw) { $raw | Set-Content $rawOut -Encoding UTF8 }

        $j = $null
        if ($raw) {
            $ix = $raw.IndexOf('{')
            if ($ix -ge 0) { try { $j = $raw.Substring($ix) | ConvertFrom-Json } catch { $j = $null } }
        }
        $ok = ($code -eq 0) -and $j -and ($j.type -eq 'result') -and (-not $j.is_error)
        if ($ok) {
            if ($j.session_id) { $j.session_id | Set-Content $headF }
            $res = [string]$j.result
            if ($res.Length -gt 1500) { $res = $res.Substring(0, 1500) + ' ...[truncated]' }
            Log ('turn {0} ok  dur={1:n0}s api_turns={2} head={3}' -f $iter, $dur, $j.num_turns, $j.session_id)
            Log ('RESULT: ' + ($res -replace "`r`n", ' | ' -replace "`n", ' | '))
            $fails = 0
            if ($dur -lt 90) { $shorts++ } else { $shorts = 0 }
        } else {
            $fails++
            $why = ''
            if ($j) {
                $res = [string]$j.result
                if ($res.Length -gt 400) { $res = $res.Substring(0, 400) }
                $why = ('subtype={0} is_error={1} msg: {2}' -f $j.subtype, $j.is_error, ($res -replace "`r`n", ' | ' -replace "`n", ' | '))
            } elseif ($raw) {
                $why = 'unparseable tail: ' + ($raw.Substring([Math]::Max(0, $raw.Length - 300)) -replace "`r`n", ' | ' -replace "`n", ' | ')
            } else {
                $why = 'no output'
            }
            Log ('turn {0} FAILED (exit={1}, fails={2}) {3}' -f $iter, $code, $fails, $why)
            # Fable SESSION limit (5-hour rolling window) reports a reset time. Owner wants Fable max, so
            # if the reset is reasonably soon, WAIT for it and resume on Fable instead of thrashing or
            # switching models. (Machine TZ is {{TIMEZONE}} = the TZ the limit message quotes.)
            if ($primary -eq $FABLE -and $raw -match '(?i)session limit.{0,40}?resets\s+(\d{1,2}):(\d{2})\s*(am|pm)') {
                $hh=[int]$matches[1]; $mm=[int]$matches[2]; $ap=$matches[3].ToLower()
                if ($ap -eq 'pm' -and $hh -lt 12) { $hh += 12 } elseif ($ap -eq 'am' -and $hh -eq 12) { $hh = 0 }
                $now = Get-Date
                $reset = Get-Date -Hour $hh -Minute $mm -Second 0
                while ($reset -le $now) { $reset = $reset.AddDays(1) }
                # +90s buffer, plus a per-session offset so all 5 don't cold-resume in the same instant at reset
                $waitSec = [int](($reset - $now).TotalSeconds) + 90 + ($N * 45)
                if ($waitSec -le 5400) {
                    Log ("  Fable SESSION limit; waiting {0:n0}s until ~{1} then resuming on Fable (owner wants Fable max)" -f $waitSec, $reset.ToString('HH:mm'))
                    $fails = [Math]::Max(0, $fails - 1)   # a resource wait, not a real failure
                    if (-not (SleepAware $waitSec)) { Log 'STOP during Fable session-limit wait - exiting'; break }
                    continue
                } else {
                    $OPUS | Set-Content $modelFile -Encoding ascii
                    $reset.ToString('yyyy-MM-dd HH:mm:ss') | Set-Content "$base\fable_reset_at.txt" -Encoding ascii
                    Log "  Fable SESSION limit resets >90min out ($($reset.ToString('HH:mm'))); switching to $OPUS to keep working (watchdog returns to Fable after reset)"
                    $fails = [Math]::Max(0, $fails - 1)
                }
            }
            # owner rule: when Fable's USAGE (not transient overload) is out, switch the whole fleet to Opus.
            # match usage-cap wording only; do NOT trip on 'overloaded'/plain rate-limit (those are transient, handled by backoff).
            if ($primary -eq $FABLE -and $raw -match '(?i)usage limit|usage_limit|usage-credits|reached your .{0,20}limit|Fable 5 limit|switch models with|out of (usage|credit)|credit balance|insufficient (quota|credit)|quota (exceeded|exhaust)|plan limit|billing') {
                $OPUS | Set-Content $modelFile -Encoding ascii
                Log "  Fable usage appears EXHAUSTED -> switched shared model.txt to $OPUS; all runners follow next turn"
                $fails = [Math]::Max(0, $fails - 1)   # soft: don't count the switch turn toward the error threshold
            }
        }
    } else {
        # codex: resume appends to the same rollout, so the id stays fixed.
        # --color is exec-level (before resume); '-' makes resume read the prompt from stdin.
        $xargs = @('exec', '--color', 'never', 'resume', $def.resumeId, '--dangerously-bypass-approvals-and-sandbox', '--skip-git-repo-check', '-o', $lastMsg, '-')
        # codex gets no Claude SessionStart/instinct hook, so hand it the brain in the prompt (brain_digest.ps1)
        # every turn - otherwise a codex agent runs brain-blind, the exact failure we keep closing.
        $digest = ''
        try { $digest = (& powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.claude\dreaming\brain_digest.ps1" 2>$null | Out-String) } catch {}
        $codexPrompt = if ($digest.Trim()) { $digest + "`n---`n`n" + $prompt } else { $prompt }
        ($codexPrompt | & $codexCmd @xargs 2>> $errLog) | Out-File $turnOut -Encoding utf8
        $code = $LASTEXITCODE
        $dur = ((Get-Date) - $t0).TotalSeconds
        $ok = ($code -eq 0)
        if ($ok) {
            $res = ''
            if (Test-Path $lastMsg) { $res = (Get-Content $lastMsg -Raw) }
            if ($res.Length -gt 1500) { $res = $res.Substring(0, 1500) + ' ...[truncated]' }
            Log ('turn {0} ok  dur={1:n0}s' -f $iter, $dur)
            Log ('RESULT: ' + ($res -replace "`r`n", ' | ' -replace "`n", ' | '))
            $fails = 0
            if ($dur -lt 90) { $shorts++ } else { $shorts = 0 }
        } else {
            $fails++
            Log ('turn {0} FAILED (exit={1}, fails={2}) - see stderr log' -f $iter, $code, $fails)
        }
    }

    if ($shorts -ge 5) {
        Log 'five consecutive sub-90s turns without a done file - marking STALLED (likely finished but not saying so, or confused); delete the stalled file to re-arm'
        'five consecutive short turns without done file' | Set-Content $stallF
        break
    }
    if ($fails -ge 10) {
        Log 'ten consecutive failures - marking ERROR and exiting'
        'ten consecutive turn failures - see logs' | Set-Content $errF
        break
    }

    if ($fails -gt 0) {
        $sleep = [Math]::Min(900, 120 * $fails) + (Get-Random -Minimum 0 -Maximum 90)
        Log "backing off $sleep s after failure"
        if (-not (SleepAware $sleep)) { Log 'STOP during backoff - exiting'; break }
    } else {
        $pause = 20 + (Get-Random -Minimum 0 -Maximum 40)
        if (-not (SleepAware $pause)) { Log 'STOP during pause - exiting'; break }
    }
}
Log 'runner exit'
if (Test-Path $meshScript) {
    try {
        $meshStatus = if (Test-Path $doneF) { 'done' } elseif (Test-Path $blockF) { 'blocked' } else { 'offline' }
        & python $meshScript bye --agent $meshAgent --status $meshStatus --summary "agentA runner $N exited ($meshStatus); inspect its session log and sentinels for evidence" 2>> $errLog | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Log 'agent mesh refused final bye (usually an unresolved structured handoff); leaving ownership visible until acceptance/cancellation or lease expiry'
            & python $meshScript pulse --agent $meshAgent --status blocked --task 'runner exited with unresolved mesh handoff; ownership retained temporarily' 2>> $errLog | Out-Null
        }
    } catch {}
}
Remove-Item $pidF -ErrorAction SilentlyContinue
