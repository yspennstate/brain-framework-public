# run_dream.ps1 - one full dream cycle for the brain.
# Generates the dream agenda (dream.py), runs a headless Claude REM session with a
# hard watchdog (kill if it stalls), refreshes the Codex wake file, logs everything.
# Launched hidden (launch_dream.vbs / inherited hidden console). Never opens a window.
$ErrorActionPreference = 'Continue'
try { [Diagnostics.Process]::GetCurrentProcess().PriorityClass = 'BelowNormal' } catch {}
# Unattended run: never let a git call block on an interactive credential prompt
# (ls-remote/push both hung on git-credential-manager, 2x tonight 2026-08-24).
# GIT_TERMINAL_PROMPT alone is not enough - GCM has its own interactive gate.
# Verified 2026-08-24: both together turn the hang into an instant, clean exit 128.
$env:GIT_TERMINAL_PROMPT = '0'
$env:GCM_INTERACTIVE = 'never'
$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$secretGateHelpersReady = $false
$secretGateHelper = Join-Path $base 'invoke_secret_scan.ps1'
if (Test-Path -LiteralPath $secretGateHelper -PathType Leaf) {
    try {
        . $secretGateHelper
        $loaded = $?
        if ($loaded -and
            (Get-Command Invoke-SecretScan -CommandType Function -ErrorAction SilentlyContinue) -and
            (Get-Command Get-AdvertisedPushMain -CommandType Function -ErrorAction SilentlyContinue)) {
            $secretGateHelpersReady = $true
        }
    } catch {}
}
$logDir = Join-Path $base 'logs'
New-Item -ItemType Directory -Force $logDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$log = Join-Path $logDir "dream_$stamp.log"
function Log($m){ "$(Get-Date -Format 'HH:mm:ss') $m" | Add-Content $log -Encoding UTF8 }
# 2026-09-25: Windows power-throttles this windowless BelowNormal tree by default (EcoQoS). Measured that night: the view
# build over 3,000 memories 414 s and 146 s throttled against 36 s and 42 s opted out, the staged secret scan 0.05 cpu-s
# per 30 s against 23.28; the live view build hit its 2400 s cap. A windowless helper opts this process and every
# descendant out for as long as this process lives. Priority stays BelowNormal, so his foreground work still comes first.
try {
    $unthrottle = Join-Path $base 'dream_unthrottle.py'
    $pythonw = Join-Path (Split-Path -Parent (Get-Command python.exe -ErrorAction Stop).Source) 'pythonw.exe'
    if ((Test-Path -LiteralPath $unthrottle) -and (Test-Path -LiteralPath $pythonw)) {
        $uh = Start-Process -FilePath $pythonw -ArgumentList @('-X', 'utf8', "`"$unthrottle`"", "$PID") -WindowStyle Hidden -PassThru
        Log "power throttling: helper pid $($uh.Id) keeps this tree opted out (priority unchanged)"
    }
} catch { Log "power throttling: helper not started ($($_.Exception.GetType().Name)); steps run throttled" }
# 2026-09-20: the persist step brings local main up to the advertised push main before it stages (dream_git_sync.ps1);
# without it every dream from 09-19 19:30 to 09-20 11:30 committed locally and its exact-SHA push was rejected.
. (Join-Path $base 'dream_git_sync.ps1')
. (Join-Path $base 'rem_watch.ps1')
# The mechanical tail holds dream.lock; on 2026-09-13 it held it 18 hours (the BrainDream task's own
# 2-hour stop kills only wscript, the powershell tree lives on) and two naps were refused. Heavy tail
# steps ask TailOver first: past the budget they are skipped and named, so the lock is released and
# the next nap or the next night rebuilds them.
$dreamT0 = Get-Date
$tailBudgetHours = 6
function TailOver($step) {
    $h = ((Get-Date) - $dreamT0).TotalHours
    if ($h -gt $tailBudgetHours) { Log ("TAIL BUDGET: {0:N1} h since start exceeds {1} h - skipping {2}" -f $h, $tailBudgetHours, $step); return $true }
    return $false
}
# Run a nightly python step with its exit code CHECKED (audit 010 finding 8): PowerShell's
# try/catch does not throw on a native nonzero exit, so a failing compiler/measurer was a
# log line nobody read while the cycle continued (the 07-16 gut compile was red ~10h this
# way). Output still goes to the log; a nonzero exit now gets its own loud line.
# EVERY step gets a clock (2026-09-14). TailOver decides whether a step STARTS; nothing decided when one
# STOPS, so a step that began before the budget expired ran forever. On 09-14 the 11:30 nap sat in
# build_retrieval_trace for two and a half hours - zero cpu, disk reads still climbing but decelerating 6.7x -
# held dream.lock for over eight hours and ate the 19:30 cycle. An uncapped dream does not just overrun
# itself, it cancels its successor.
#
# Why this is not simply "wrap it in run_bounded": RunPy pipes the step's output INLINE into $log, while
# run_bounded writes the child to "<log>.child" and never streams it back - deliberately, because a piped
# stdout read only at exit deadlocks a long job (its own header records that scar). Wrapping naively would
# move fifteen steps' output out of the log this very defect was diagnosed from. So run_bounded keeps its
# contract, and the caller reconstructs the inline log: append <log>.child after the step returns, then
# truncate it. The only cost is that a step's output lands at its end rather than as it runs, which for a
# nightly log nobody tails live is not a cost.
$stepCapSeconds = [int]($env:DREAM_STEP_CAP_S | ForEach-Object { if ($_) { $_ } else { 2700 } })
function RunPy($name, [string[]]$argv){
    # STOP IS HONOURED BETWEEN STEPS (2026-09-14). Before today the STOP file was checked in exactly two
    # places - once at line 103 before the dream starts, and inside the REM model-retry and backoff waits.
    # The TAIL had none at all: an unbounded search of the 976-line runner finds every STOP check below
    # line 400, and all fifteen RunPy steps live above it. So once a dream was past its REM phase the
    # owner's documented way to stop it did nothing, for any dream type - and the 11:30 nap then walked
    # its tail for ten hours holding dream.lock with no graceful way to end it but killing the tree,
    # which is the exact thing a STOP file exists to avoid. A clock is a default; STOP is his decision,
    # and a decision that is silently ignored is worse than a missing default.
    # Skipping the remaining STEPS is deliberate rather than exiting: the persist/commit block further
    # down is inline git, not a RunPy call, so the dream still saves what it did, commits it, and
    # releases the lock on its way out.
    # $script:RunPyExit CARRIES THE STEP'S VERDICT OUT (2026-09-15). Several steps in the tail gate a
    # second step on the first one's exit code - eval_memory_access must pass before build_memory_access
    # writes - and RunPy returned nothing, so those blocks had to stay raw `& python` and therefore
    # uncapped. build_memory_access is the one the comment further down records holding dream.lock for
    # TWELVE HOURS on 2026-09-13 (2,771 transcript files, 8.3 GB) despite its own --max-seconds budget,
    # which is exactly the case a wall clock exists for. Exposing the code lets those blocks keep their
    # gating and gain the cap. 0 means the step ran and succeeded; anything else means do not proceed,
    # and a STOP skip is deliberately non-zero so a stopped dream does not run the gated half either.
    $script:RunPyExit = 199
    if (Test-Path -LiteralPath (Join-Path $base 'STOP')) {
        Log "STOP file present - skipping $name (owner requested stop; persist and lock release still run)"
        return
    }
    try {
        if ($stepCapSeconds -gt 0) {
            $childLog = "$log.child"
            Remove-Item -LiteralPath $childLog -Force -ErrorAction SilentlyContinue
            & python (Join-Path $base 'run_bounded.py') $stepCapSeconds $log python @argv | Out-Null
            $code = $LASTEXITCODE
            if (Test-Path -LiteralPath $childLog) {
                Get-Content -LiteralPath $childLog -ErrorAction SilentlyContinue | Add-Content $log
                Remove-Item -LiteralPath $childLog -Force -ErrorAction SilentlyContinue
            }
            $script:RunPyExit = $code
            if ($code -eq 124) { Log "STEP CAPPED: $name exceeded ${stepCapSeconds}s and was killed (output above)" }
            elseif ($code -ne 0) { Log "STEP FAILED: $name exited $code (output above)" }
        } else {
            & python @argv 2>&1 | Add-Content $log
            $script:RunPyExit = $LASTEXITCODE
            if ($LASTEXITCODE -ne 0) { Log "STEP FAILED: $name exited $LASTEXITCODE (output above)" }
        }
    } catch { Log "STEP FAILED: $name threw: $($_.Exception.Message)" }
}
if (-not $secretGateHelpersReady) { Log 'FATAL: secret-gate helpers unavailable; fail-closed'; exit 1 }
$atomicIoHelpersReady = $false
$atomicIoHelper = Join-Path $base 'dream_atomic_io.ps1'
if (Test-Path -LiteralPath $atomicIoHelper -PathType Leaf) {
    try {
        . $atomicIoHelper
        $loaded = $?
        if ($loaded -and
            (Get-Command Open-DreamLease -CommandType Function -ErrorAction SilentlyContinue) -and
            (Get-Command Write-AtomicUtf8NoBomLines -CommandType Function -ErrorAction SilentlyContinue)) {
            $atomicIoHelpersReady = $true
        }
    } catch {}
}
if (-not $atomicIoHelpersReady) { Log 'FATAL: atomic-I/O helpers unavailable; fail-closed'; exit 1 }

# retention: the logs dir had none (every cycle adds ~3 files, humanness more) - keep 30 days
try {
    Get-ChildItem $logDir -File | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
        Remove-Item -Force -ErrorAction SilentlyContinue
} catch {}

# guards: owner kill-switch + no overlapping dreams
if (Test-Path (Join-Path $base 'STOP')) { Log 'STOP file present - not dreaming'; exit 0 }
$lock = Join-Path $base 'dream.lock'
$dreamLease = $null
try {
    $dreamLease = Open-DreamLease -Path $lock
} catch {
    Log "FATAL: could not acquire the dream lease; fail-closed: $($_.Exception.Message)"
    exit 1
}
if ($null -eq $dreamLease) { Log 'another dream is in progress - exiting'; exit 0 }
try {
    $durationRequest = $null
    $durationHelper = Join-Path $base 'dream_duration.ps1'
    if (Test-Path -LiteralPath $durationHelper) {
        . $durationHelper
        $durationRequest = Read-DreamDurationRequest -Base $base -Stamp $stamp
    } elseif (Test-Path -LiteralPath (Join-Path $base 'DREAM_REQUEST_NEXT.json')) {
        throw 'Duration request exists but its helper is missing'
    }
    if ($null -ne $durationRequest) { Log ('owner duration request consumed: ' + $durationRequest.duration_minutes + ' min, no subagents') }
    # 1. mechanical phase: refresh conscious/unconscious tiers, then tonight's agenda.
    # Different kinds of dreaming (sleep architecture): SWS 'filer' early, a light nap midday, REM
    # 'connector' in the evening - consolidation precedes and feeds creative recombination
    # (Diekelmann-Born 2010; Giuditta sequential hypothesis 1995). Hour-of-day selects the type.
    $h = (Get-Date).Hour
    $typeNextF = Join-Path $base 'DREAM_TYPE_NEXT.txt'
    if (Test-Path $typeNextF) {
        $dreamType = (Get-Content $typeNextF -TotalCount 1 -Encoding UTF8).Trim()   # one-shot manual override
        Move-Item $typeNextF (Join-Path $base 'DREAM_TYPE_NEXT.last') -Force
    } else {
        $dreamType = if ($h -lt 9) { 'sws' } elseif ($h -lt 16) { 'nap' } else { 'rem' }
    }
    if ($dreamType -notin @('sws', 'nap', 'rem', 'mixed')) { $dreamType = 'mixed' }
    $env:DREAM_TYPE = $dreamType
    Log "refreshing consciousness tiers + generating dream agenda (type=$dreamType)"
    # remember whether a focus existed NOW - only a focus that was folded into THIS agenda may be
    # retired in 3b. One placed mid-dream (a wake session steering the NEXT dream) must survive,
    # or it gets consumed without ever steering anything (caught 2026-07-08).
    $focusWasPresent = Test-Path (Join-Path $base 'DREAM_FOCUS.md')
    # BOUNDED (2026-09-14). Neither of these had a time cap, and the 45-minute watchdog below covers only
    # the REM phase - so a stall HERE holds dream.lock with no deadline at all. That is not theoretical:
    # the 11:30 nap froze inside consciousness.py, 26 minutes at 1.86 seconds of CPU with zero read and
    # zero write operations on an idle disk, and it would have held the lock until somebody noticed. It is
    # the same shape as the transcript scan that held the lock for twelve hours on 09-13, at a different
    # step. A cap does not need to know WHY a step froze. consciousness.py read 16,026 memories in about
    # two minutes on the 03:30 run and dream.py built the agenda in the rest of that step's fourteen; the
    # caps below are generous multiples, and an expiry is logged by name and the dream carries on with the
    # previous generation's tiers, which is exactly what the catch below already did.
    # The supervisor captures the child's own stdout to "<log>.child" (a pythonw child has no console, so
    # its traceback would otherwise go nowhere). Nothing PARSES these two steps' output - hook_liveness
    # reads this log only for the runner's own "git said:" line - but a person reading the dream log would
    # lose the "consciousness: N memories conscious=..." summary, so say once where it went.
    Log ("step output for consciousness + agenda goes to " + (Split-Path $log -Leaf) + ".child")
    # THE CORPUS WALKERS RUN AT NORMAL PRIORITY (2026-09-14, measured). This runner sets itself BelowNormal at
    # the top and every step inherits it. On this box that is not politeness, it is starvation: the brain
    # measured 8.0 s against 1082.7 s for the same ffmpeg pass on 09-13, and today four dream steps in a row
    # read as frozen - the graph rebuild managed 3.28 seconds of CPU in 154 minutes. The control that settles
    # it: an identical walk of the same 16,089 memory files, run by hand at Normal in the same window,
    # finished in 493.7 s at a mean of 30.7 ms a file. Only the three steps that walk the whole corpus are
    # raised; everything else the dream does stays BelowNormal.
    #
    # THAT LAST SENTENCE IS NOT WHAT HAPPENS, measured 2026-09-16 - comment corrected, behaviour left
    # alone deliberately. This is a PROCESS-SCOPE environment variable, set once here and never cleared,
    # and every step after this line reaches run_bounded through the RunPy helper - so ALL of them are
    # raised, not three. The 11:30 dream logged 38 'raised to NORMAL priority (ok)' lines covering bm25,
    # brain_regression_gate, bridge_health, build_keyword_edges/nodes, build_mistake_weights,
    # build_near_duplicate_report, build_orphan_edges, build_programs_view, build_prompt_views,
    # build_reflex_ranking, build_retrieval_trace and more; a running child's own environment confirms
    # RUN_BOUNDED_NORMAL_PRIORITY=1. An env var is being used where a per-call argument was meant.
    # WHICH IS RIGHT IS THE OWNER'S CALL, not a passing agent's: the whole dream at Normal may well be
    # what he wants given the starvation measured above and his 2026-09-09 order weakening the guards.
    # If the three-step scope IS the intent, clear the variable after the third corpus walker (or pass
    # it per invocation); if the broad scope is the intent, the sentence above should say so.
    $env:RUN_BOUNDED_NORMAL_PRIORITY = '1'
    $cRc = (& python (Join-Path $base 'run_bounded.py') 900 $log python (Join-Path $base 'consciousness.py'))
    if ($LASTEXITCODE -eq 124) { Log 'STEP CAPPED: consciousness.py hit its 900s cap - tiers stay at the previous generation' }
    elseif ($LASTEXITCODE -ne 0) { Log ('STEP FAILED: consciousness.py exit ' + $LASTEXITCODE + ' (see the bounded log)') }
    $dRc = (& python (Join-Path $base 'run_bounded.py') 1800 $log python (Join-Path $base 'dream.py'))
    if ($LASTEXITCODE -eq 124) { Log 'STEP CAPPED: dream.py hit its 1800s cap - the agenda check below decides whether to abort' }
    if (-not (Test-Path (Join-Path $base 'DREAM_AGENDA.md'))) { Log 'agenda missing - abort'; exit 1 }

    # 1c. COMPUTE PREFLIGHT (2026-08-17). The scheduled runner had none, so when the governor hit
    # DISTRESS the whole task was disabled BY HAND - and that took the cheap half down as
    # collateral: the BM25 lexical index froze for 25.8h and 136 memories (99 of them the owner's
    # 08-16 campaign day) went invisible to the lexical arm of recall, while the semantic arm
    # stayed fresh and hid it. So the preflight skips ONLY the expensive model phase. Everything
    # below it - bm25, index maintenance, hook_liveness, the graph rebuilds - is text work with no
    # model inference and keeps running, which is exactly what was lost last time.
    # Fail-OPEN: an unreadable/absent/stale state file must not silently stop dreaming forever.
    $skipRem = $false
    try {
        $csf = Join-Path $env:USERPROFILE '.claude\compute\compute_state.json'
        if (Test-Path -LiteralPath $csf) {
            $cs = Get-Content $csf -Raw -Encoding UTF8 | ConvertFrom-Json
            $csAgeMin = ((Get-Date) - [datetime]$cs.ts).TotalMinutes
            # Gate on LOCAL pressure only. The governor raises status=distress for azure_pressure
            # (the protected production stack), which says nothing about whether THIS laptop can dream - the dream
            # runs entirely here. Caught 2026-08-17 within minutes of writing the naive version:
            # reasons was exactly 'azure_pressure' while this machine sat at 52% CPU and 18.7GB
            # free, so the naive gate would have skipped REM that night and every night the box
            # was busy - re-creating the no-dream outage this preflight exists to prevent.
            # A distress with NO reasons is malformed; treat it as local and skip (owner first).
            $localReasons = @($cs.reasons | Where-Object { $_ -notmatch '^azure' })
            if ($csAgeMin -gt 30) {
                Log "compute preflight: state file is $([int]$csAgeMin) min stale - treating as unknown, REM proceeds"
            } elseif ($cs.status -eq 'distress' -and ($localReasons.Count -gt 0 -or @($cs.reasons).Count -eq 0)) {
                $skipRem = $true
                Log "COMPUTE PREFLIGHT: governor reports LOCAL DISTRESS (mode=$($cs.mode); $($localReasons -join '; ')) - skipping the REM model phase tonight; the mechanical/index steps still run"
            } elseif ($cs.status -eq 'distress') {
                Log "compute preflight: status=distress but the only reasons are remote ($($cs.reasons -join '; ')) - that is the protected production stack, not this machine; REM proceeds"
            } else {
                Log "compute preflight: status=$($cs.status) mode=$($cs.mode) age=$([int]$csAgeMin)m - REM phase cleared"
            }
        } else {
            Log 'compute preflight: no compute_state.json found - proceeding (fail-open)'
        }
    } catch { Log "compute preflight could not read the governor state (nonfatal, proceeding): $($_.Exception.Message)" }

    # 2. REM phase: headless Claude with a 45-minute watchdog
    $claudeCmd = $null
    foreach ($cand in @("$env:APPDATA\npm\claude.cmd", 'claude')) {
        $gc = Get-Command $cand -ErrorAction SilentlyContinue
        if ($gc) { $claudeCmd = $gc.Source; break }
    }
    if (-not $claudeCmd -and -not $skipRem) { Log 'FATAL: claude CLI not found'; exit 1 }
    $promptF = Join-Path $base "dream_prompt_$dreamType.md"
    if (-not (Test-Path $promptF)) { $promptF = Join-Path $base 'dream_prompt.md' }
    # The REM session, with a transient-error fallback: --fallback-model only covers server OVERLOAD, not
    # a subscription cap - the 22:47 07-08 dream died at turn 9 on "You've reached your Fable 5 limit"
    # and the night got a mechanical-only dream (no consolidation). On a TRANSIENT-shaped error - a usage
    # cap, OR a connection blip like the 19:30 07-10 "Unable to connect to API (ConnectionRefused)" which
    # got NO retry under the old limit-only match and lost that night's consolidation (the 11:30 and 00:45
    # dreams succeeded on the same driver, so it was transient, not config) - retry ONCE on the fallback
    # model after a short pause. A non-transient error (e.g. "Not logged in") keeps the old behavior
    # (nonfatal, persist runs). Bounded to one retry by the 2-model array.
    # 2026-09-14: the second entry used to be the SAME model as the first, so the 'fallback' retried
    # straight back into the limit that had just refused it. The 11:30 nap's REM died twice that way:
    # attempt 1 'You've reached your Fable limit. Switch to another model', retry 'You've hit your
    # weekly limit - resets Sep 17, 9pm' - turns=1, cost=0, no journal, and every dream until the reset
    # would have lost its consolidation the same way. A subscription cap is PER MODEL: claude-opus-5[1m]
    # answered normally in the same minute the Fable arm was refused (tested before this edit).
    $models = @('claude-fable-5-1[1m]', 'claude-opus-5[1m]')
    $wdMin = 45   # watchdog minutes; the prompt header below tells the dream its own deadline
    $remStart = Get-Date   # for died-at-birth vs died-mid-flight (memory writes since this moment)
    $extendedDream = $null -ne $durationRequest
    $remSessionId = $null
    $workDeadline = if ($extendedDream) { $remStart.AddMinutes($durationRequest.duration_minutes) } else { $null }
    if ($extendedDream) { Log ('timed dream work start=' + $remStart.ToString('o') + ' deadline=' + $workDeadline.ToString('o')) }
    for ($mi = 0; (-not $skipRem) -and ($(if ($extendedDream) { (Get-Date) -lt $workDeadline } else { $mi -lt $models.Count })); $mi++) {
        if ($extendedDream -and (Test-Path -LiteralPath (Join-Path $base 'STOP'))) { Log 'STOP file present - owner requested dream stop'; break }
        $model = $models[$mi % $models.Count]
        $suffix = if ($mi -eq 0) { '' } else { ".retry$mi" }
        $outJson = Join-Path $logDir "dream_$stamp$suffix.out.json"
        $errLog  = Join-Path $logDir "dream_$stamp$suffix.err.log"
        # deadline-aware prompt: the 07-08 23:00 REM burned its whole 45 min mid-arc and the
        # watchdog killed it - the journal/reflection (the tail) is what gets lost. So each attempt
        # gets a copy of the prompt with its OWN hard deadline stamped on top, and budget rules.
        # ASCII only (wake-path discipline).
        $deadline = if ($extendedDream) { $workDeadline.AddMinutes(6) } else { (Get-Date).AddMinutes($wdMin) }
        if ($extendedDream) { $wdMin = [Math]::Max(1, [Math]::Ceiling(($deadline - (Get-Date)).TotalMinutes)) }
        $promptRun = Join-Path $logDir "dream_$stamp$suffix.prompt.md"
        # WORKING TIME, NOT LAUNCH TIME (2026-09-26): on a loaded laptop the start-up hooks of a headless session can
        # take most of a 45-minute window before the model says a word - the 25 Sep 20:21 retry reached its first turn
        # 27 minutes in and was killed after three turns. A non-timed dream now gets $wdMin minutes from its FIRST
        # TURN (rem_watch.ps1), never later than launch + 2 x $wdMin. A session with no turn at launch + $wdMin is
        # killed then, exactly as before (dead at birth - the 09-15 case - and retried): the hook caps alone (three
        # SessionStart and two prompt hooks at 600 s each) can pass 35 minutes, so an earlier cut could kill a live
        # session. No case gets less time than the launch clock gave it. Its prompt carries the hard cap as the mark
        # that finds its own transcript. Timed dreams keep the launch-based clock.
        $remCapAt = $null
        $remMark = "A watchdog kills this session at $($deadline.ToString('HH:mm')) local time"
        if (-not $extendedDream) {
            $remCapAt = (Get-Date).AddMinutes(2 * $wdMin)
            $remMark = "and in any case no later than $($remCapAt.ToString('HH:mm')) local time"
            $budgetLines = @(
              "A watchdog kills this session $wdMin minutes after your FIRST turn - start-up hooks on a busy machine can take",
              "many minutes and do not count - $remMark.",
              "Read the clock at your first step (a quick shell 'date' / Get-Date): your deadline is that time plus $wdMin minutes.",
              "The kill is silent and loses whatever is unwritten - usually the journal and final reflection,",
              "the most valuable part. Budget: check the clock as you work; do not start a new agenda item in the last 12",
              "minutes before your deadline; 6 minutes before it, stop whatever is open and write the journal + reflection.",
              "A shorter dream that lands its reflection beats a deeper one that gets cut mid-thought.")
        } else {
            $budgetLines = @(
              "A watchdog kills this session at $($deadline.ToString('HH:mm')) local time ($wdMin minutes from launch).",
              "The kill is silent and loses whatever is unwritten - usually the journal and final reflection,",
              "the most valuable part. Budget: check the clock as you work (a quick shell 'date' / Get-Date);",
              "do not start a new agenda item in the last 12 minutes; by $($deadline.AddMinutes(-6).ToString('HH:mm')) stop whatever is open and",
              "write the journal + reflection. A shorter dream that lands its reflection beats a deeper one",
              "that gets cut mid-thought.")
        }
        @("## Hard time budget (mechanical header, injected by run_dream.ps1)") + $budgetLines + @(
          "",
          "OPEN THE JOURNAL FIRST, THEN APPEND TO IT. The watchdog is not the only silent kill: on",
          "2026-09-14 this dream's REM hit the API usage limit at turn 22 with real work already done -",
          "six memory files changed - and the journal, written last, was never written at all, so the",
          "night read as 'no dream ran'. A usage limit arrives with NO warning and no clock to budget",
          "against. So: before the first agenda item, create the journal file with its date heading and",
          "one line saying what you are about to do; then APPEND each section as you finish it, not at",
          "the end. A journal that ends mid-thought is worth everything; a journal that was never",
          "opened is worth nothing.", "") + $(if ($extendedDream) { Get-DreamDurationPrompt -Request $durationRequest -WorkDeadline $workDeadline } else { @() }) + (Get-Content $promptF -Encoding UTF8) |
            Set-Content $promptRun -Encoding UTF8
        # stdin-redirect via cmd so we can Start-Process (PassThru) and enforce a timeout
        $soloArgs = if ($extendedDream) { ' --disallowedTools "Agent,Task,TeamCreate,TeamDelete"' } else { '' }
        $resumeArgs = if ($extendedDream -and $remSessionId) { " --resume $remSessionId" } else { '' }
        $cmdLine = "/c `"`"$claudeCmd`" -p --output-format json --dangerously-skip-permissions --model $model --effort high$soloArgs$resumeArgs < `"$promptRun`" > `"$outJson`" 2> `"$errLog`"`""
        if ($remCapAt) { Log "REM session starting on $model (watchdog: $wdMin min of working time from its first turn, or $wdMin min from launch with no turn, hard cap $($remCapAt.ToString('HH:mm')))" }
        else { Log "REM session starting on $model (watchdog: $wdMin min, deadline $($deadline.ToString('HH:mm')) in the prompt)" }
        $t0 = Get-Date
        $p = Start-Process -FilePath "$env:ComSpec" -ArgumentList $cmdLine -WorkingDirectory $env:USERPROFILE -WindowStyle Hidden -PassThru
        try { $p.PriorityClass = 'BelowNormal' } catch {}
        if ($remCapAt) {
            # a plain block: called from Wait-RemWorkingTime it resolves Log, $t0 and $wdMin through the caller's scope
            # (test_onturn_scope.ps1, 25 Sep: the plain and the GetNewClosure() forms both log the first turn)
            $onTurn = { param($at) Log ("REM first model turn at $($at.ToString('HH:mm:ss')) (" + [int](($at - $t0).TotalSeconds) + "s after launch); working-time deadline $($at.AddMinutes($wdMin).ToString('HH:mm'))") }
            $done = Wait-RemWorkingTime -Process $p -Launch $t0 -WorkMin $wdMin -Mark $remMark -BirthMin $wdMin -CapMin (($remCapAt - $t0).TotalMinutes) -OnFirstTurn $onTurn
        } else {
            $done = $p.WaitForExit($wdMin * 60 * 1000)
        }
        if (-not $done) {
            Log "WATCHDOG: dream reached its limit ($wdMin min of working time, or no first turn in $wdMin min, or the hard cap) - killing process tree"
            try { & taskkill /PID $($p.Id) /T /F 2>&1 | Out-Null } catch {}
            Log 'dream cut short by watchdog (nonfatal - agenda re-runs tomorrow)'
        }
        $dur = [int]((Get-Date) - $t0).TotalSeconds
        Log "REM session finished in ${dur}s (completed=$done)"

        # 3. surface the result line for quick health checks; decide whether a limit retry is due
        $isErr = $false; $resTxt = ''; $parsedResult = $false
        try {
            $raw = Get-Content $outJson -Raw -Encoding UTF8 -ErrorAction Stop
            $ix = $raw.IndexOf('{')
            if ($ix -ge 0) {
                $j = $raw.Substring($ix) | ConvertFrom-Json
                Log ("result: is_error=$($j.is_error) turns=$($j.num_turns) cost=$($j.total_cost_usd)")
                $isErr = [bool]$j.is_error
                $resTxt = '' + $j.result
                $parsedResult = $true
                if ($extendedDream -and ([string]$j.session_id -match '^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')) { $remSessionId = [string]$j.session_id }
            }
        } catch { Log 'could not parse result json (nonfatal)' }
        if ($extendedDream) {
            if (-not $done) { break }
            if ((Get-Date) -ge $workDeadline) {
                Log ('timed dream duration elapsed: ' + [int]((Get-Date) - $remStart).TotalSeconds + 's; final_success=' + ($parsedResult -and -not $isErr))
                break
            }
            if (-not $parsedResult -or $isErr) {
                Log 'timed dream model unavailable/error; retaining one runner and retrying after bounded backoff'
                $backoffEnd = (Get-Date).AddMinutes(5)
                while ((Get-Date) -lt $backoffEnd -and (Get-Date) -lt $workDeadline -and -not (Test-Path -LiteralPath (Join-Path $base 'STOP'))) { Start-Sleep -Seconds 5 }
            } else {
                if (-not $remSessionId) { Log 'FATAL: timed dream cannot resume without a validated session id'; break }
                Log ('timed dream returned before deadline; resuming same session ' + $remSessionId)
            }
            continue
        }
        # retry transient failures only (usage cap OR a connection/overload/rate blip); a non-transient
        # error like "Not logged in" won't match and correctly falls through to break (no retry).
        # A WATCHDOG KILL THAT PRODUCED NOTHING IS A LOST WINDOW, NOT A FINISHED DREAM (2026-09-15).
        # Measured that day: the 11:31 REM wrote 0 bytes to stdout, 0 bytes to stderr and left no
        # session transcript at all in 47 minutes. The result json was therefore empty, the parse
        # above failed, $isErr stayed $false - and this gate's `break` sent the cycle on as though
        # the dream had simply finished, writing a partial stub instead of retrying. Three of the
        # last twenty cycles died exactly that way and all three were the midday one; the 03:30
        # cycles complete. Headless latency on this box is what makes it midday: an identical
        # trivial prompt measured 57s and 1295s the same afternoon, so a fixed 45-minute watchdog
        # sits inside the variance rather than outside it.
        # So: treat "the watchdog fired and the session produced nothing at all" as transient and
        # spend the one remaining model on it. A session that produced ANY result json is left
        # exactly as before - it did work, and retrying would duplicate it.
        # BUT an empty result json does not mean an empty session (2026-09-25): --output-format json writes the
        # result only when a session ENDS, so every watchdog kill leaves it at 0 bytes - including the 19:35 REM
        # that worked 44 minutes (34 turns, journal sections E-G appended, H onward staged in a part-file) and was then called "no turns" and
        # retried from scratch on the other model. Look at the work product first: this attempt's own transcript,
        # found by the deadline stamp its prompt carries, and its assistant turns. No transcript, or a transcript
        # with no turn (the 09-15 case), is still a lost window and is still retried.
        $producedNothing = $false
        if ((-not $done) -and (-not $parsedResult)) {
            $outLen = 0
            try { $outLen = (Get-Item -LiteralPath $outJson -ErrorAction Stop).Length } catch { $outLen = 0 }
            if ($outLen -eq 0) {
                $remTurns = 0
                try {
                    $mark = $remMark
                    $proj = Join-Path $env:USERPROFILE '.claude\projects\{{BRAIN_SLUG}}'
                    foreach ($tf in @(Get-ChildItem $proj -Filter '*.jsonl' -File -ErrorAction SilentlyContinue |
                                      Where-Object { $_.CreationTime -ge $t0.AddSeconds(-5) })) {
                        $head = Get-Content -LiteralPath $tf.FullName -TotalCount 40 -Encoding UTF8 -ErrorAction SilentlyContinue
                        if (($head -join "`n").Contains($mark)) {
                            $remTurns = @(Select-String -LiteralPath $tf.FullName -Pattern '"type":"assistant"' -SimpleMatch -ErrorAction SilentlyContinue).Count
                            break
                        }
                    }
                } catch {}
                if ($remTurns -gt 0) { Log "REM was cut by the watchdog after $remTurns assistant turn(s) of real work (its transcript) - not retried; the agenda re-runs tomorrow" }
                else { $producedNothing = $true }
            }
        }
        if ($producedNothing) { Log 'REM produced nothing at all before the watchdog (0-byte result, no turns) - counting it as transient so the window is retried, not lost' }
        if ((-not $producedNothing) -and (-not ($isErr -and $resTxt -match 'limit|usage|credit|connect|overload|timeout|timed out|unavailable|502|503|500|429|temporarily'))) { break }
        if ($mi -lt $models.Count - 1) {
            # A session-limit refusal names its reset ("You've hit your session limit - resets 7:30am"); a
            # retry 30 s later meets the same wall (09-13 03:30: 04:17:40 refused, 04:18:20 refused again,
            # mechanical-only night). When the text carries a reset clock under four hours away, wait for it
            # (STOP file honoured every minute); otherwise keep the old 30 s pause for connection blips.
            $pauseSec = 30
            if ($resTxt -match 'resets\s+(\d{1,2}):(\d{2})\s*(am|pm)') {
                try {
                    $hh = [int]$Matches[1]; $mm = [int]$Matches[2]; $ap = $Matches[3].ToLower()
                    if ($ap -eq 'pm' -and $hh -lt 12) { $hh += 12 }
                    if ($ap -eq 'am' -and $hh -eq 12) { $hh = 0 }
                    $reset = (Get-Date).Date.AddHours($hh).AddMinutes($mm)
                    if ($reset -le (Get-Date)) { $reset = $reset.AddDays(1) }
                    $waitSec = [int](($reset - (Get-Date)).TotalSeconds) + 90
                    if ($waitSec -le 4 * 3600) { $pauseSec = $waitSec }
                    else { Log ("REM limit resets at {0:HH:mm} - more than four hours away, not waiting" -f $reset) }
                } catch {}
            }
            Log "REM hit a transient error on $model - pausing ${pauseSec}s then retrying once on $($models[$mi+1])"
            $slept = 0
            while ($slept -lt $pauseSec) {
                if (Test-Path (Join-Path $base 'STOP')) { Log 'STOP file appeared during the limit wait - not retrying'; break }
                $slice = [Math]::Min(60, $pauseSec - $slept); Start-Sleep -Seconds $slice; $slept += $slice
            }
            if (Test-Path (Join-Path $base 'STOP')) { break }
        }
        else {
            # died-at-birth vs died-mid-flight (07-09 intention): is_error=True can follow many
            # productive turns (the 04:46 07-09 run wrote artifacts for 24 turns before its limit),
            # so 'mechanical-only' would over-claim. Count memory writes in the REM window first;
            # concurrent sessions can inflate the count, so the message says 'likely', not 'did'.
            $nWrites = 0
            try {
                $memCnt = Join-Path $env:USERPROFILE '.claude\projects\{{BRAIN_SLUG}}\memory'
                $nWrites = @(Get-ChildItem $memCnt -Filter '*.md' -File -ErrorAction SilentlyContinue |
                             Where-Object { $_.LastWriteTime -ge $remStart }).Count
            } catch {}
            if ($nWrites -gt 0) { Log "REM hit a transient error on the fallback too - but $nWrites memory file(s) changed during its window: PARTIAL consolidation likely (journal may be missing; concurrent sessions can inflate the count)" }
            else { Log 'REM hit a transient error on the fallback too - mechanical-only dream tonight' }
        }
    }

    # 3a. JOURNAL ASSERTION (2026-08-17). The 03:30 cycle completed REM cleanly - 48 turns,
    # end_turn, no error - and filed its narrative as a job_cycle README instead of a
    # memory\dreams\dream_*.md journal, so dreams\ got nothing from a fully successful dream.
    # That matters because the wake's "last dream" section AND brain_selfcare's freshness check
    # read ONLY memory\dreams\: a run like that leaves both aging as though no dream happened,
    # which is exactly the phantom staleness that cost the owner an alert today. This cannot
    # write the journal - only the dream can - so it makes the omission LOUD instead of silent.
    if (-not $skipRem) {
        try {
            $memJ = Join-Path $env:USERPROFILE '.claude\projects\{{BRAIN_SLUG}}\memory'
            $fresh = @(Get-ChildItem (Join-Path $memJ 'dreams') -Filter 'dream_*.md' -ErrorAction SilentlyContinue |
                       Where-Object { $_.LastWriteTime -ge $remStart })
            if ($fresh.Count -gt 0) {
                Log ("journal check: " + $fresh.Count + " journal(s) written this cycle - " + (($fresh | ForEach-Object { $_.Name }) -join ', '))
            } else {
                Log 'JOURNAL MISSING: REM ran but wrote no memory\dreams\dream_*.md this cycle. The wake last-dream section and the freshness check read ONLY that directory, so both will age as if no dream ran. If the narrative went to a job_cycle readme instead, copy it into dreams\ by hand.'
                # 2026-09-14: write a MECHANICAL STUB rather than leave the night looking un-dreamed. The
                # 03:30 REM died at the API usage limit on turn 22 with six memory files already written;
                # the narrative was lost, but "no dream ran" is a worse lie than "a dream ran and could not
                # finish its journal". The stub is labelled as the runner's, never as the dream's own voice,
                # names every memory the REM window touched so the work is findable, and is written ONLY
                # when the window actually changed something - a REM that did nothing still reads as nothing.
                try {
                    $touched = @(Get-ChildItem $memJ -Filter '*.md' -File -ErrorAction SilentlyContinue |
                                 Where-Object { $_.LastWriteTime -ge $remStart } |
                                 Sort-Object LastWriteTime)
                    if ($touched.Count -gt 0) {
                        $stub = Join-Path (Join-Path $memJ 'dreams') ("dream_" + (Get-Date -Format 'yyyy_MM_dd') + "_partial.md")
                        if (-not (Test-Path $stub)) {
                            $lines = @(
                                "# Dream " + (Get-Date -Format 'yyyy-MM-dd') + " (PARTIAL - mechanical stub written by run_dream.ps1, not by the dream)",
                                "",
                                "The REM session ran and then died before it wrote a journal (see " + (Split-Path $log -Leaf) + " for the",
                                "result line: a usage limit, a connection error or the watchdog). Its narrative and reflection are",
                                "LOST - this file exists so the wake's last-dream section and the freshness check see the truth",
                                "(a dream ran, partially) instead of reading the night as though nothing happened.",
                                "",
                                "WHAT THE REM WINDOW TOUCHED (" + $touched.Count + " memory file(s) changed at or after " + $remStart.ToString('HH:mm:ss') + ";",
                                "concurrent sessions can inflate this list - a name here is a lead, not proof the dream wrote it):"
                            )
                            foreach ($t in $touched) { $lines += ("  - " + $t.BaseName + "  (" + $t.LastWriteTime.ToString('HH:mm:ss') + ")") }
                            $lines += @("", "Nothing here is consolidation. The next full dream should re-read these and do the filing.")
                            $lines | Set-Content $stub -Encoding UTF8
                            Log ("wrote a PARTIAL stub journal naming " + $touched.Count + " touched memor(y|ies): " + (Split-Path $stub -Leaf))
                        }
                    } else {
                        Log 'no memory file changed in the REM window either - nothing to stub; the night was mechanical-only and reads that way honestly'
                    }
                } catch { Log "partial stub journal failed (nonfatal): $($_.Exception.Message)" }
            }
        } catch { Log "journal check failed (nonfatal): $($_.Exception.Message)" }
    }

    # 3b. one-shot dream focus: if it was folded into tonight's agenda, retire it now (rename,
    #     never delete) so a focus steers exactly one dream unless it is re-written. A focus that
    #     appeared MID-dream was never folded - leave it for the next dream.
    try {
        $focusFile = Join-Path $base 'DREAM_FOCUS.md'
        if ($focusWasPresent -and (Test-Path $focusFile)) { Move-Item $focusFile (Join-Path $base 'DREAM_FOCUS.last') -Force; Log 'retired DREAM_FOCUS.md -> .last' }
        elseif (Test-Path $focusFile) { Log 'DREAM_FOCUS.md appeared mid-dream - left for the next dream' }
    } catch { Log "focus retire failed (nonfatal): $($_.Exception.Message)" }

    # 4. refresh the Codex wake file (mechanical - newest index lines + last reflection)
    try {
        $mem = Join-Path $env:USERPROFILE '.claude\projects\{{BRAIN_SLUG}}\memory'
        $rb  = Join-Path $env:USERPROFILE '.codex\RECENT_BRAIN.md'
        $indexReader = Join-Path $base 'supersession.py'
        $head = @(& python $indexReader current-index --memory-dir $mem --limit 10 2>&1)
        if ($LASTEXITCODE -ne 0) { throw 'current-index reader failed' }
        $lines = @('# Recent brain (auto-refreshed nightly by the dream cycle)', '')
        $lines += $head
        $dd = Join-Path $mem 'dreams'
        # Same-day journals have suffixes (rem, rem_connector, sws), so lexical name order
        # does not represent recency. Use the filesystem evidence from the completed run.
        $lastDream = Get-ChildItem $dd -Filter 'dream_*.md' -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
        if ($lastDream) {
            $lines += @('', "## Last dream ($($lastDream.BaseName)) - reflection tail", '')
            $txt = Get-Content $lastDream.FullName -Raw -Encoding UTF8
            $paras = ($txt -split "(`r?`n){2,}") | Where-Object { $_.Trim().Length -gt 40 }
            $lines += ($paras | Select-Object -Last 2 | ForEach-Object { $_.Trim() })
        }
        Write-AtomicUtf8NoBomLines -Path $rb -Lines $lines
        Log 'refreshed ~/.codex/RECENT_BRAIN.md'
    } catch { Log "RECENT_BRAIN refresh failed: $($_.Exception.Message)" }

    # 5. PERSIST mechanically (NOT inside the REM turn budget - that's how the
    #    2026-07-07 evening dream's work got stranded when its own push errored).
    try {
        # keep MEMORY.md under its read limit forever (dream flagged the locked-door risk)
        RunPy 'compact_index apply' @((Join-Path $base 'compact_index.py'), 'apply')
    } catch { Log "index compaction failed (nonfatal): $($_.Exception.Message)" }
    try {
        $repo = Join-Path $env:USERPROFILE 'brain-repo'
        $repoMem = Join-Path $repo '01_ai_consciousness\claude\memory'
        if (Test-Path $repo) {
            # The prompt ledger is EXCLUDED from the mirror and published as immutable monthly
            # shards instead (next line). It had reached 56.9 MB against GitHub's 50 MB warning
            # and 100 MB hard reject, and this robocopy stored another whole copy of the monolith
            # in the repo on every cycle. Its .bak is a pre-08-05-repair snapshot whose every
            # prompt was verified already present in the live ledger, so git needs neither.
            robocopy $mem $repoMem /E /XF *.pdf PROMPTS.jsonl *.jsonl.bak /NFL /NDL /NJH /NJS /R:0 /W:0 | Out-Null
            # 2026-09-02: robocopy's exit code is a bitmask - 0..7 are success variants, 8 and above mean files
            # could not be copied, 16 is fatal. Unchecked, a partial mirror gets committed as if it were the corpus
            # (same family as the six-day silent persist deferral fixed the same morning). Log it; never abort.
            $mirrorCode = $LASTEXITCODE
            if ($mirrorCode -ge 8) { Log "WARNING: memory mirror robocopy exit=$mirrorCode (>=8 means some files were NOT copied) - the repo memory mirror may be partial this cycle" }
            # Lossless by construction: the publisher compares shards to the source as a multiset
            # both ways and refuses to write if a single line is unaccounted for.
            RunPy 'prompt ledger monthly shards' @((Join-Path $base 'shard_prompt_archive.py'))
            Push-Location $repo
            if (-not (TailOver 'build_brain_architecture')) {
                Log 'step: build_brain_architecture (RELATED graph over every memory; 20+ min at Normal priority, hours when starved at BelowNormal - measured 2026-09-13)'
                # BOUNDED (2026-09-14): this froze for 154 minutes on 3.28 seconds of CPU during the 11:30 nap -
                # the THIRD step that day to stop dead inside a read of the memory corpus, after consciousness.py
                # and dream.py. The tail budget above only decides whether a step STARTS; nothing stopped this one
                # once it had, and it held dream.lock for two and a half hours. 40 minutes is twice what the step
                # takes when it actually runs.
                & python (Join-Path $base 'run_bounded.py') 2400 $log python (Join-Path $repo 'manifests\build_tools\build_brain_architecture.py') $repo
                if ($LASTEXITCODE -eq 124) { Log 'STEP CAPPED: build_brain_architecture hit its 2400s cap - the RELATED graph keeps its previous generation' }
                elseif ($LASTEXITCODE -ne 0) { Log ('STEP FAILED: build_brain_architecture exited ' + $LASTEXITCODE + ' (see the bounded log)') }
            }
            # THESE TWO WERE THE LAST UNBOUNDED STEPS, AND ON 2026-09-15 growth.py HUNG THE DREAM
            # (2026-09-15). The header above this file already records why every step got a cap:
            # "an uncapped dream does not just overrun itself, it cancels its successor" - it holds
            # dream.lock, and the next scheduled cycle logs "another dream is in progress - exiting"
            # and dies. Measured across 123 cycles: 16 (13 pct) were skipped exactly that way, five
            # of them the 19:30 slot, because the midday dream was still running.
            # Fifteen steps were converted to RunPy then; these two kept the raw
            # `& python ... 2>&1 | Add-Content $log` shape - which is the very pattern run_bounded's
            # own header calls a scar ("a piped stdout read only at exit hangs a long job", 09-12).
            # Caught live: growth.py sat 73 minutes at 6.3 s of CPU - 0.1 pct, blocked on its output
            # pipe, not computing - while the log stayed silent and the 19:30 cycle came closer.
            # RunPy caps them and routes output through the sidecar, so a wedged view refresh costs
            # one step instead of the night and the cycle that follows it.
            RunPy 'salience view refresh' @((Join-Path $base 'salience.py'), $mem, (Join-Path $repo '01_ai_consciousness\brain'))
            RunPy 'growth view refresh' @((Join-Path $base 'growth.py'), $mem, (Join-Path $repo '01_ai_consciousness\brain'))
            # refresh the gut (compiled reflexes): picks up any instinct the dream distilled from a new scar tonight
            # F026 durable regression first: prove the corrupt-overlay guard still fires
            RunPy 'instincts selftest' @((Join-Path $base 'instincts.py'), '--selftest')
            RunPy 'instincts compile' @((Join-Path $base 'instincts.py'), $mem)
            # 2026-09-03: rotate the hook's append-only fire logs (rows older than 21 days move to monthly
            # archives beside the log; nothing is deleted). Safe under the writers' lock, verified row-for-row,
            # restores its own pre-rotation copy on any mismatch and removes the previous night's copy only
            # after tonight's rotation verified. A night with nothing older than the window moves nothing.
            RunPy 'rotate hook fire logs' @((Join-Path $base 'rotate_logs.py'), '--apply')
            # Strong-node >10 recurrence watch, OBSERVE-ONLY (2026-09-05): runs the reviewed,
            # hash-pinned scanner read-only and writes strong_nodes/WATCH_LATEST.json for the
            # agenda's M1 section. It never places ENABLED and never promotes; the July release
            # stays parked until the owner completes its review. Scan takes 1-4 minutes, BelowNormal.
            RunPy 'strong-node observe watch' @((Join-Path $base 'strong_node_watch.py'))
            # brain_improvement dashboard data-path self-test (2026-09-05, canonical contract gate 7):
            # reads STATUS/DASHBOARD and every source they name, writes DASHBOARD_SELFTEST.json;
            # a hard failure exits 1 and is logged as STEP FAILED, never fatal. Seconds, no network.
            RunPy 'brain_improvement dashboard selftest' @('$env:USERPROFILE\brain-repo\12_cognitive_architecture\brain_improvement\dashboard_selftest.py')
            # 2026-09-14: REGENERATE the dashboard, not only self-test it. The selftest had printed "dashboard
            # generated 56.0 h ago (refresh_dashboard.py has not run)" for two nights because nothing ran it.
            # refresh_dashboard.py asserts mesh claims on the four DASHBOARD.* targets under its --agent name, so
            # the runner claims them as brain-dream first and releases after; a denied claim (another agent is
            # regenerating it) skips the step and says so. Nonfatal throughout; ~2 minutes, no network.
            if (-not (TailOver 'brain_improvement dashboard refresh')) {
                try {
                    $biDir = '$env:USERPROFILE\brain-repo\12_cognitive_architecture\brain_improvement'
                    $meshPy = '$env:USERPROFILE\brain-repo\12_cognitive_architecture\agent_mesh\agent_mesh.py'
                    $biRes = @("$biDir\DASHBOARD.json", "$biDir\DASHBOARD.json.codex-part", "$biDir\DASHBOARD.md", "$biDir\DASHBOARD.md.codex-part")
                    & python $meshPy hello --agent brain-dream --runtime brain --task 'nightly dream: dashboard refresh' 2>&1 | Out-Null
                    $claimArgs = @('claim', '--agent', 'brain-dream', '--mode', 'write', '--purpose', 'dream tail: regenerate the brain-improvement dashboard')
                    foreach ($r in $biRes) { $claimArgs += @('--resource', $r) }
                    & python $meshPy @claimArgs 2>&1 | Out-Null
                    if ($LASTEXITCODE -eq 0) {
                        Log 'step: brain_improvement dashboard refresh'
                        Push-Location $biDir
                        try {
                            & python (Join-Path $biDir 'refresh_dashboard.py') --agent brain-dream 2>&1 | Select-Object -Last 3 | Add-Content $log
                            if ($LASTEXITCODE -ne 0) { Log ('STEP FAILED: refresh_dashboard exited ' + $LASTEXITCODE + ' (output above)') }
                        } finally { Pop-Location }
                        $relArgs = @('release', '--agent', 'brain-dream')
                        foreach ($r in $biRes) { $relArgs += @('--resource', $r) }
                        & python $meshPy @relArgs 2>&1 | Out-Null
                    } else {
                        Log ('dashboard refresh skipped: the DASHBOARD targets are claimed by another agent (claim exit ' + $LASTEXITCODE + ')')
                    }
                } catch { Log "dashboard refresh failed (nonfatal): $($_.Exception.Message)" }
            }
            # Advance the recurrence observer only after the compiled reflex source above is current.
            # Python owns status/stdout/snapshot ordering; this runner captures one invocation and
            # logs its native exit without adding another publication boundary.
            try {
                RunPy 'mistake_graph watch' @((Join-Path $base 'mistake_graph.py'), '--watch')
                $mistakeWatchCode = $script:RunPyExit
                if ($mistakeWatchCode -ne 0) {
                    Log "mistake graph watch failed (nonfatal, exit=$mistakeWatchCode)"
                }
            } catch {
                Log "mistake graph watch failed (nonfatal): $($_.Exception.Message)"
            }
            # learn from real failures: coverage of the gut over actual errors + uncovered recurring shapes (needs instincts.tsv above)
            RunPy 'ern_learn' @((Join-Path $base 'ern_learn.py'))
            # score the felt-error stream into the calibrated reliability series (built 07-08 as the
            # ERN->prediction solder but never invoked by anything - hook_liveness caught the missing
            # producer 07-09; a loop only counts once something actually runs it)
            RunPy 'ern_reliability' @((Join-Path $base 'ern_reliability.py'))
            # refresh the IDF surface weights the priming hook ranks with (the fire distribution is
            # non-stationary; a static snapshot buries newly-chronic reflexes' rare siblings - 07-15)
            RunPy 'reflex_ranking refresh' @((Join-Path $base 'build_reflex_ranking.py'), '--write', '--quiet')
            # grow the association network from real use since the last dream (owner standing
            # directive: the network keeps growing from lessons/prompts/memories; idempotent past
            # its water mark, BelowNormal. Was built 07-15 but never invoked - wired same day)
            RunPy 'network dream_grow' @((Join-Path $base 'network\dream_grow.py'))
            # heeding trend: did fired reflexes change behavior (OQ#1179 downstream half)?
            # Read-only over fire log + transcripts; --jsonl makes the lift series MACHINE-readable
            # (heed_trend.jsonl), not just printed into this log (added 07-16; the series existed
            # only as log text before - honest but unreadable by the dreams)
            RunPy 'heed_audit trend' @((Join-Path $base 'heed_audit.py'), '--days', '1', '--jsonl', (Join-Path $base 'heed_trend.jsonl'))
            # fire-surface precision: where do reflexes fire vs where the fleet acts (the
            # context_damp's measurement side; build_reflex_ranking consumes the same modules)
            RunPy 'precision_audit' @((Join-Path $base 'precision_audit.py'), '--days', '2', '--exposure')
            # per-layer fire attribution (regex|graph|ff col since 07-16): which mechanism
            # produces each reflex's fires - opposite repair paths need opposite evidence
            RunPy 'precision mechanism split' @((Join-Path $base 'precision_audit.py'), '--days', '1', '--by-mechanism')
            # score the brain's predictions (surprise = the learning signal); refresh the "brain expects" wake view
            RunPy 'predict' @((Join-Path $base 'predict.py'), $mem)
            # refresh the prompt-archive strata (every owner prompt, saved + classified; owner directive 2026-07-09)
            RunPy 'build_prompt_views' @((Join-Path $base 'build_prompt_views.py'))
            # keep the index complete: any memory in neither MEMORY.md nor INDEX_ARCHIVE gets an
            # archive line (the 26%-gap class, closed 07-15; append-only under the shared write lock)
            RunPy 'ensure_index' @((Join-Path $base 'ensure_index.py'), '--write')
            # generate low-weight attachment edges for the ~23% orphan memories (degree 0 = invisible to
            # the walk) BEFORE the graph rebuild merges them; eval-gated non-regression (eval_orphan_edges,
            # 2026-07-10). Separate file, never edits a memory body. Absent sidecar -> no-op.
            RunPy 'orphan_edges build' @((Join-Path $base 'build_orphan_edges.py'), '--quiet')
            # link research-PROGRAM members to nearest siblings so the walk traverses the whole program,
            # not just reaches it (arnold_diffusion 2->27 navigability); eval-gated 0.000 regression
            RunPy 'build_series_edges' @((Join-Path $base 'build_series_edges.py'), '--quiet')
            # BM25 fallback for the SHORT high-salience memos cosine can't embed - mostly the owner's isolated
            # FEEDBACK - so his corrections surface by association, not only a direct query; eval-gated 0.000
            # regression (eval_keyword_edges). Uses the prior night's bm25 index (rebuilt below); fine - near-stationary
            RunPy 'keyword_edges build (+component bridges)' @((Join-Path $base 'build_keyword_edges.py'), '--quiet')
# his vocabulary grows: new words he starts using become topic nodes, new memories join the
# topics they belong to, and fresh typos earn aliases - without this the topic layer is a
# one-shot snapshot that silently ages out of his language
RunPy 'keyword topic nodes (owner vocabulary + graph edges)' @((Join-Path $base 'build_keyword_nodes.py'))
# Runs AFTER the rebuild, so it inspects the artifact the next session will actually use.
# Answers the two failures of 07-25: enforcement silently switched to shadow, and a keyword
# artifact whose memberships were 57% words the memories never used. Both passed every test.
RunPy 'regression gate (enforcement live, artifact sane, recall still fires)' @((Join-Path $base 'brain_regression_gate.py'))
            # rebuild the association graph the spreading-activation recall walks (Hebbian co-reads + wiki-links + orphan + series + keyword edges)
            RunPy 'build_spread_edges' @((Join-Path $base 'build_spread_edges.py'))
            # rebuild the mistake severity map the proactive hook uses for mistake-first surfacing.
            # Nothing invoked this builder: the table sat at its 2026-08-22 build for eleven days while
            # 80 new scars carried weight zero and the gate's mistake-class fire rate read 3-10 percent.
            RunPy 'build_mistake_weights' @((Join-Path $base 'build_mistake_weights.py'))
            # near-duplicate report (pattern separation): proposals only, applied by nobody automatically.
            # 2026-09-02: 4,836 pairs above cosine 0.97 in a 14.8k corpus, 23 of them already linked.
            RunPy 'build_near_duplicate_report' @((Join-Path $base 'build_near_duplicate_report.py'))
            # reflex precision (owner 2026-09-02: fire less, be more confident when it fires): one number per
            # reflex from judged_nightly.jsonl - how often a judge found the fire on-domain - read by the
            # priming hook to rank fires, silence a recognition cue judged off-domain, and print the
            # 'judged on-domain a of n' hint under each fire. Runs after the judge pass so tonight's rows count.
            RunPy 'reflex_precision' @((Join-Path $base 'reflex_precision.py'))
            # bridge-integrity health check: betweenness centrality flags any SUPERSEDED/stale memory that
            # sits on the top recall paths (recall routing through outdated content); read-only, logs
            RunPy 'bridge_health' @((Join-Path $base 'bridge_health.py'), '--samples', '150')
            # suggest missing wiki-links the hand-authored backbone lacks (Adamic-Adar link prediction,
            # causally validated 2026-07-10); writes read-only link_suggestions.md for the dream/owner to
            # author by hand - NEVER auto-adds edges (mutating the non-stationary graph is the churn to avoid)
            RunPy 'suggest_links' @((Join-Path $base 'suggest_links.py'), '--quiet')
            # map the brain's major work programs (multi-memory series) to a PROGRAMS.md nav view
            RunPy 'build_programs_view' @((Join-Path $base 'build_programs_view.py'), '--quiet')
            # connect the ~23% of memories that are graph ORPHANS (degree 0 = invisible to the walk):
            # writes read-only orphan_links.md (each orphan + its content-nearest anchors) for the dream to
            # author. Same discipline - never auto-adds (2026-07-10 orphan-reachability find)
            RunPy 'suggest_orphan_links' @((Join-Path $base 'suggest_orphan_links.py'), '--quiet')
            # rebuild the BM25 keyword index the seeder uses (gated live 2026-07-09: hit@10 0.53 vs 0.22)
            if (-not (TailOver 'bm25 build')) {
                Log 'step: bm25 build'
                RunPy 'bm25' @((Join-Path $base 'bm25.py'), 'build')
            }
            # rebuild the retrieval trace mechanically (was an SWS-prompt duty only - model-dependent,
            # skipped on nap nights and REM deaths; the evals and shadow scoring read it, 2026-07-09)
            # CAPPED 2026-09-15 - THIS IS THE STEP THE HEADER ABOVE IS ABOUT, AND IT DID IT AGAIN.
            # The 09-14 note at the top of this file records build_retrieval_trace sitting two and a
            # half hours at zero cpu, holding dream.lock for over eight hours and eating the 19:30
            # cycle. Steps were converted to the capped RunPy because of it - but this one was left
            # raw, because it captured its output into $traceOut. On 09-15 it hung again: 31 minutes
            # at 1 pct cpu and silent, with the 19:30 trigger 100 minutes away.
            # The capture bought nothing. $traceOut was used for exactly one thing, piping to $log,
            # and $traceCode for exactly one more, logging a nonzero exit - and RunPy already does
            # both. So the conversion is semantically identical and adds the clock.
            # The general defect this is one instance of: about twenty step launches in this file are
            # still raw `& python`, against seventeen through RunPy, and any one of them can hold the
            # lock for hours. The task's own PT8H limit does not reach them - it kills the wscript,
            # not the powershell underneath (the same trap the hourly job's VBS documents) - and a
            # normal cycle has no overall deadline of its own. Converting the rest deserves its own
            # pass with the output-capture cases checked one at a time, as this one was.
            RunPy 'retrieval trace rebuild' @((Join-Path $base 'build_retrieval_trace.py'))
            # Derive the usage signal from the trace just rebuilt. base_activation.tsv is what
            # brain.py's recall tilt actually ranks with, and until 2026-08-22 nothing refreshed
            # it mechanically - it was rebuilt only when a dream prompt happened to ask, so it sat
            # 41 hours behind its own trace with three memories still carrying the maximum usage
            # weight the live trace had already decayed to ~0.2. Invisible, because the loader
            # failed open and a stale table looks exactly like a fresh one. Must follow
            # build_retrieval_trace.py; nonfatal on its own (the loader now fails closed and says
            # so rather than ranking on a stale signal).
            RunPy 'base_activation' @((Join-Path $base 'base_activation.py'))
            # A separate typed ledger closes the blind spot around shell reads and Codex sessions while
            # preserving retrievals.tsv as narrow explicit-Read ground truth. Promote only after its
            # metadata-only/read-vs-surface contract passes. The seven-day window is no longer ~40s: on
            # 2026-09-13 it was 2,771 transcript files / 8.3 GB and the scan held dream.lock for TWELVE
            # HOURS before dying in a MemoryError (two naps were refused meanwhile), so the scan now runs
            # under its own budget (exit 3 = budget exhausted, nothing written, prior sidecar kept).
            try {
                RunPy 'eval_memory_access' @((Join-Path $base 'eval_memory_access.py'))
                $accessEvalCode = $script:RunPyExit
                if ($accessEvalCode -eq 0) {
                    # CAPPED 2026-09-15, surgically: this is the step the comment above records holding
                    # dream.lock for twelve hours on 09-13, and its own --max-seconds 1800 budget did not
                    # stop it. Only this line changes - the gating around it, and the exit-3 "budget
                    # exhausted, prior sidecar kept" branch below, keep working because $script:RunPyExit
                    # carries the same code $LASTEXITCODE did. The surrounding nest is left alone
                    # deliberately: restructuring three levels of gating buys nothing the clock does.
                    RunPy 'build_memory_access (ledger promotion)' @((Join-Path $base 'build_memory_access.py'), '--write', '--max-seconds', '1800')
                    $accessWriteCode = $script:RunPyExit
                    if ($accessWriteCode -eq 3) {
                        Log "memory-access ledger scan hit its 30-minute budget (nonfatal); prior sidecar preserved - the transcript window has outgrown the budget, shrink --days or raise --max-seconds"
                    } elseif ($accessWriteCode -ne 0) {
                        Log "memory-access ledger promotion failed (nonfatal, exit=$accessWriteCode)"
                    } else {
                        # Read-only attentional-gating observer: do surfaced slugs convert to later
                        # content reads above chronological availability/popularity controls?
                        RunPy 'eval_surface_conversion' @((Join-Path $base 'eval_surface_conversion.py'))
                        $surfaceEvalCode = $script:RunPyExit
                        if ($surfaceEvalCode -ne 0) { Log "surface-conversion eval failed (nonfatal, exit=$surfaceEvalCode)" }
                    }
                } else {
                    Log "memory-access ledger eval failed (exit=$accessEvalCode); prior sidecar preserved"
                }
            } catch { Log "memory-access ledger eval/promotion failed (nonfatal): $($_.Exception.Message)" }
            # Presence is not freshness: publish a compact observer marker so wake can warn when new
            # or edited memories outgrow the offline semantic sidecar. Incomplete is status, not failure.
            RunPy 'embedding_coverage' @((Join-Path $base 'embedding_coverage.py'), '--write-status')
            # Event-bounded three-factor eligibility is SHADOW ONLY: compile a secret-safe derived
            # sidecar only AFTER regression-checking boundaries/math. No consumer reads this file; the live
            # data must clear its explicit maturity/effect gates before recall or salience can change.
            try {
                RunPy 'eval_eligibility_credit' @((Join-Path $base 'eval_eligibility_credit.py'))
                $eligEvalCode = $script:RunPyExit
                if ($eligEvalCode -eq 0) {
                    RunPy 'eligibility_credit write' @((Join-Path $base 'eligibility_credit.py'), '--write')
                    $eligWriteCode = $script:RunPyExit
                    if ($eligWriteCode -ne 0) { Log "eligibility shadow promotion failed (nonfatal, exit=$eligWriteCode)" }
                } else {
                    Log "eligibility shadow eval failed (exit=$eligEvalCode); prior sidecar preserved"
                }
            } catch { Log "eligibility shadow eval/promotion failed (nonfatal): $($_.Exception.Message)" }
            RunPy 'eval_event_boundaries' @((Join-Path $base 'eval_event_boundaries.py'))
            # Immutable memory-age regression gate: exact transcript creates + synthetic contracts +
            # dream recent/old counterfactuals. Read-only; a failure is logged with Python's native exit.
            RunPy 'eval_memory_age' @((Join-Path $base 'eval_memory_age.py'))
            # Transaction regression for the live coactivation hook. It operates entirely in a
            # temporary directory: singleton sessions must not decay graph bytes, while real folds
            # decay exactly once and retain source logs if atomic promotion fails.
            RunPy 'eval_coactivation_decay' @((Join-Path $base 'eval_coactivation_decay.py'))
            # refresh the superseded-fact demotion index (stamp explicit links, list prose pairs for the
            # dream); spread_recall reads superseded.tsv to never serve a stale fact beside its correction
            RunPy 'schema_supersede' @((Join-Path $base 'schema_supersede.py'), '--apply')
            # per-type recall utility (smarter-vs-bigger, Wilson CIs): logs whether dream-products earn
            # their wake slot as the trace matures - a measurement into the log, not a live gate yet
            RunPy 'eval_type_utility' @((Join-Path $base 'eval_type_utility.py'))
            # fok abstention/false-recall (LongMemEval): watches the concentration fix keep confabulation
            # low as the index grows; a regression here means fok is over-firing on familiar-word noise
            RunPy 'eval_fok_abstention' @((Join-Path $base 'eval_fok_abstention.py'))
            # prompt-recall gate (the SHIPPED spell-normalization lever's regression check, 2026-07-09):
            # bm25+spell must keep beating plain bm25 on hit@10/@20 as the corpus + trace grow; a drop
            # means a change quietly hurt the seeder. A measurement into the log, like the two above.
            RunPy 'eval_prompt_recall' @((Join-Path $base 'eval_prompt_recall.py'))
            # utilization/attribution (CUE, MemSyco): did surfaced memories get USED, ignored, or mislead?
            # matures as surfaced.tsv accumulates; surfaces noise (surfaced-never-used) + mislead suspects
            RunPy 'attribution' @((Join-Path $base 'attribution.py'))
            # premise-check follow-through (scores PREDICTIONS p31): a premise fire without a same-session
            # read of the correction is a pointer that did not pay the reading cost - logged nightly so
            # the dream can resolve the bet on real data (zero-state prints honestly until fires exist)
            RunPy 'eval_premise_follow' @((Join-Path $base 'eval_premise_follow.py'))
            # reflex-trigger audit (per-reflex precision, #10): replays each gut trigger vs the prompt
            # archive + tool commands - catches a broken/over-broad trigger (would have caught measure_one)
            RunPy 'reflex_audit' @((Join-Path $base 'reflex_audit.py'))
            # prediction calibration (#9): does an OPEN bet sit in a scope/confidence cell the
            # ledger has already measured as overconfident? Dreams are what WRITE bets - there is
            # no placement-time hook anywhere, because the author is a model following prose - so
            # this runs where they are written. Measured 2026-08-17 over 140 scored bets: the
            # 0.60-0.70 band is overconfident in every scope, and owner scope stays overconfident
            # below that too. Prints the resolved-but-unscoreable ids so they stop being invisible.
            RunPy 'prediction_calibration_check' @((Join-Path $base 'prediction_calibration_check.py'))
            # meta-checker: do all wired hooks still compile + is every append-only stream flowing? It was
            # never scheduled (only ran on manual invocation), so its OWN silent crash went ~17h unnoticed
            # 2026-07-09 - the watchman must be watched. Writes .liveness_status.json for the wake to read.
            RunPy 'hook_liveness' @((Join-Path $base 'hook_liveness.py'))
            # recompute the live curiosity (surprise + incongruity + lonely-salient); needs predictions + spread_edges above
            RunPy 'curiosity' @((Join-Path $base 'curiosity.py'), $mem, (Join-Path $repo '01_ai_consciousness\brain'))
            # machinery mirror: live code/prompts -> repo, so the restore path can't rot (2026-07-09:
            # 12 files were missing from the mirror; hand-syncing is the failure, this is the fix)
            RunPy 'verify_mirror' @((Join-Path $base 'verify_mirror.py'), '--sync-to-repo')
            # Persist owns the memory corpus + generated brain views, not the entire shared worktree.
            # `git add -A -f` once swept a concurrent agent's UNVERIFIED machinery plus an ignored
            # __pycache__ into d089434. Never force ignored files, never auto-commit pre-staged work,
            # and leave machinery mirrors dirty for an intentional waking-session verification/commit.
            $ownedPaths = @('01_ai_consciousness/brain', '01_ai_consciousness/claude/memory')
            $preStagedNames = @(& git diff --cached --name-only 2>$null)
            $preStagedCode = $LASTEXITCODE
            if ($extendedDream -and $durationRequest.defer_publish -eq $true) {
                Log 'persist commit deferred: this owner-requested run is local-only pending signed-contract reconciliation; preserved work requires an explicit later publication'
            } elseif ($preStagedCode -ne 0) {
                Log "persist commit deferred: could not inspect the staged index (git diff exit=$preStagedCode)"
            } elseif ($preStagedNames.Count -gt 0) {
                Log "persist commit deferred: $($preStagedNames.Count) path(s) were already staged by another session; memory/mirror work remains local"
            } else {
                # 2026-09-02: EVERY dream since 2026-08-25 deferred here with exit=128 and the reason unreadable -
                # Out-Null swallowed git's stderr, and no err log carries it. The reflog shows no dream commit landed
                # after 2026-08-27 20:25. Keep git's own message in the log, and give a transient index.lock (another
                # agent's git holding the lock for a few seconds) one retry after 20 s. Nothing else changes.
                # A STALE index.lock is the one cause that makes every write fail with exit=128 for days while
                # every read (status, diff, dry-run add) still passes: a git killed mid-write leaves it behind.
                # .git already holds index.lock.stale_removed_20260817 / _20260830 - agents removed such locks by
                # hand twice. This is that removal, guarded the way a healer must be: the lock must be older than
                # 10 minutes AND no git process may be alive on the machine; otherwise it is left alone. Once per
                # dream, logged, and set aside (renamed) rather than deleted.
                $lockPath = Join-Path $repo '.git\index.lock'
                if (Test-Path $lockPath) {
                    $lockAgeMin = ((Get-Date) - (Get-Item $lockPath).LastWriteTime).TotalMinutes
                    $gitAlive = @(Get-Process -Name git -ErrorAction SilentlyContinue).Count
                    if ($lockAgeMin -gt 10 -and $gitAlive -eq 0) {
                        $setAside = $lockPath + '.stale_removed_' + (Get-Date -Format 'yyyyMMdd_HHmm')
                        try {
                            Move-Item -LiteralPath $lockPath -Destination $setAside -Force
                            Log ("persist: set aside a STALE .git/index.lock ({0:N0} min old, no git process alive) -> {1}" -f $lockAgeMin, (Split-Path $setAside -Leaf))
                        } catch { Log "persist: could not set aside the stale index.lock: $($_.Exception.Message)" }
                    } else {
                        Log ("persist: .git/index.lock present ({0:N1} min old, git processes alive: {1}) - leaving it; the add below will say so if it blocks" -f $lockAgeMin, $gitAlive)
                    }
                }
                # 2026-09-20: bring local main up to the advertised push main BEFORE staging, so the exact-SHA push
                # below cannot be rejected as a non-fast-forward. Waking sessions push from worktrees, so this
                # checkout falls behind; three consolidation commits (09-19 19:30 .. 09-20 11:30) sat local for a day
                # with pushed=False and the reason swallowed. The sync rebases in a detached temporary worktree,
                # resolves memory-mirror conflicts with the live memory file, moves the checkout with reset --keep
                # (never over a locally modified file) and reports every outcome here. A failed sync does not stop
                # tonight's commit: the commit is a local snapshot either way and hook_liveness reports it unpushed.
                try {
                    $advBefore = Get-AdvertisedPushMain
                    if ($null -ne $advBefore -and "$($advBefore.Oid)" -match '^[0-9a-f]{40}$') {
                        $memLive = Join-Path $env:USERPROFILE '.claude\projects\{{BRAIN_SLUG}}\memory'
                        $syncOk = Sync-LocalMainToAdvertised -Repo $repo -PushUrl $advBefore.PushUrl -Oid $advBefore.Oid -MemoryDir $memLive -Logger ${function:Log}
                        if (-not $syncOk) { Log "persist: local main could not be brought up to the advertised main; committing anyway - the push will be refused tonight and retried next cycle" }
                    } else {
                        Log "persist: advertised push main unreadable before staging; skipping the sync"
                    }
                } catch {
                    Log "persist: sync threw $($_.Exception.Message); continuing without it"
                }
                $addOut = @(& git add -A -- $ownedPaths 2>&1)
                $addCode = $LASTEXITCODE
                if ($addCode -ne 0 -and (($addOut | ForEach-Object { "$_" }) -join ' ') -match 'index\.lock') {
                    Log "persist: scoped git add hit index.lock (exit=$addCode) - retrying once in 20 s"
                    Start-Sleep -Seconds 20
                    $addOut = @(& git add -A -- $ownedPaths 2>&1)
                    $addCode = $LASTEXITCODE
                }
                if ($addCode -ne 0) {
                    $addMsg = (($addOut | ForEach-Object { "$_" }) -join ' | ')
                    if ($addMsg.Length -gt 600) { $addMsg = $addMsg.Substring(0, 600) }
                    Log "persist commit deferred: scoped git add failed (exit=$addCode); work remains local; git said: $addMsg"
                } else {
                    $ownedStagedNames = @(& git diff --cached --name-only -- $ownedPaths 2>$null)
                    $stagedListCode = $LASTEXITCODE
                    if ($stagedListCode -ne 0) {
                        Log "persist commit deferred: could not verify scoped staged paths (git diff exit=$stagedListCode)"
                    } elseif ($ownedStagedNames.Count -gt 0) {
                        $staged = $ownedStagedNames.Count
                # pre-push SECRET GATE (scar: a real private key hit GitHub, verified retrievable; the
                # redaction rule then sat six weeks with no machinery and the token leak was the cost).
                # Scan complete staged index objects and fail CLOSED: on any nonzero exit (a real finding OR the
                # scanner failing to run), do NOT commit or push, unstage, and log it. A skipped nightly
                # push is cheap and self-heals next cycle; a pushed credential is not.
                $marker = Join-Path $base 'PUSH_BLOCKED_SECRET.md'
                # 2026-09-25: the scan fingerprints the SHARED index and HEAD and re-checks them before each git call,
                # so a peer's git status/diff/add/commit in this checkout during the four-minute scan refuses it
                # (exit 2, "Git repository identity changed during the scan"). Two of the four dreams 09-23..09-24
                # aborted this way with nothing found. Such a refusal is retried twice, re-staging first; a finding
                # or any other refusal is not. The refusal lines themselves are logged (they carry no content).
                $scanCode = 2
                $scanOut = @()
                $scanAttempts = 1
                $scanLastRace = $false
                try {
                    if (Get-Command Invoke-SecretScanRetryingRaces -CommandType Function -ErrorAction SilentlyContinue) {
                        $scanResult = Invoke-SecretScanRetryingRaces -ScannerPath (Join-Path $base 'scan_secrets.py') -Arguments @('--staged') `
                            -Label 'persist: staged secret scan' -Logger ${function:Log} `
                            -BeforeRetry { $null = @(& git add -A -- $ownedPaths 2>&1) }
                    } else {
                        $scanResult = Invoke-SecretScan -ScannerPath (Join-Path $base 'scan_secrets.py') -Arguments @('--staged')
                    }
                    if ($null -ne $scanResult -and $scanResult.PSObject.Properties['Code'] -and
                        $scanResult.Code -in @(0, 1, 2)) {
                        $scanCode = [int] $scanResult.Code
                        $scanOut = @($scanResult.Output)
                        if ($scanResult.PSObject.Properties['Attempts']) { $scanAttempts = [int] $scanResult.Attempts }
                        if ($scanResult.PSObject.Properties['LastWasRace']) { $scanLastRace = [bool] $scanResult.LastWasRace }
                    }
                } catch {}
                if ($scanCode -ne 0) {
                    # 2026-09-22: exit 1 and exit 2 mean OPPOSITE things and this log used to print the same
                    # sentence for both. scan_secrets.py returns 1 when it FOUND something and 2 when it
                    # COULD NOT RUN (bad arguments, inherited Git env overrides, a git failure). The 09-21
                    # dream aborted on exit=2 and the line read "ABORTED by secret gate", so consolidation
                    # sat blocked for 30 hours while the marker told its reader to "mask/rotate what it
                    # found" - a credential that was never there. Both owned trees scanned clean by hand.
                    # A gate whose refusal is indistinguishable from its finding sends every reader hunting
                    # the wrong thing, so say which one happened. Only FINDINGS output stays suppressed; a
                    # refusal message names no secret and is safe in an unattended log.
                    if ($scanCode -eq 1) {
                        $why = "a secret was FOUND in the staged content (details suppressed from unattended logs)"
                        $clear = "To clear: mask or rotate what it found, verify with"
                    } else {
                        $refusal = ''
                        try { $refusal = ((Get-SecretScanRefusalLines -Output $scanOut) -join ' | ') } catch {}
                        $why = "the scanner COULD NOT RUN (exit=$scanCode after $scanAttempts attempt(s)) - this is NOT a secret finding"
                        if ($refusal) { $why += "; it said: $refusal" }
                        if ($scanLastRace) {
                            $clear = "Another git process wrote this checkout's index or HEAD during every attempt (a peer's status, diff, add or commit); the content is not the problem and the next dream retries. To check by hand, verify with"
                        } else {
                            $clear = "To clear: fix why the scanner refused to run (bad arguments, an inherited GIT_* override in the environment, or a git failure), verify with"
                        }
                    }
                    Log ("PERSIST ABORTED by secret gate (fail-closed): " + $why)
                    & git reset -q -- $ownedPaths 2>&1 | Out-Null
                    $resetCode = $LASTEXITCODE
                    if ($resetCode -ne 0) { Log "WARNING: scoped git reset after secret-gate failure also failed (exit=$resetCode)" }
                    Write-AtomicUtf8NoBomLines -Path $marker -Lines @("# Nightly persist BLOCKED by the secret gate ($stamp)", "",
                      "The STAGED scan exited $scanCode - owned memory/brain paths were unstaged, nothing committed or pushed.",
                      "WHAT THAT CODE MEANS: $why",
                      "  exit 1 = a real finding.  exit 2 = the scanner could not run; there may be no secret at all.",
                      "Any unrelated paths staged concurrently were deliberately left staged.", "",
                      $clear,
                      "  python scan_secrets.py --staged   (after the scoped git add in run_dream.ps1)",
                      "then delete this file. The next dream re-stages and pushes everything.")
                } else {
                    $parentOut = @(& git rev-parse HEAD 2>$null)
                    $parentCode = $LASTEXITCODE
                    $parentBefore = if ($parentOut.Count -gt 0) { $parentOut[-1].Trim() } else { '' }
                    if ($parentCode -ne 0 -or $parentBefore -notmatch '^[0-9a-f]{40}$') {
                        Log "persist commit deferred: could not capture the pre-commit HEAD (git rev-parse exit=$parentCode)"
                    } else {
                        $commitMessage = "Nightly dream: consolidation ($stamp)"
                        $commitOut = @(& git commit --only -m $commitMessage -- $ownedPaths 2>&1)
                        $commitCode = $LASTEXITCODE
                        if ($commitCode -ne 0) {
                            Log "persist commit failed (exit=$commitCode); nothing will be pushed"
                            $commitOut | Add-Content $log
                        } else {
                            # HEAD is shared with waking sessions. Capture and validate THIS commit before
                            # scanning it; if another commit moved HEAD during our commit window, fail closed.
                            $shaOut = @(& git rev-parse HEAD 2>$null)
                            $shaCode = $LASTEXITCODE
                            $commitSha = if ($shaOut.Count -gt 0) { $shaOut[-1].Trim() } else { '' }
                            $subjectOut = @(& git show -s --format=%s $commitSha 2>$null)
                            $subjectCode = $LASTEXITCODE
                            $commitSubject = ($subjectOut -join "`n").Trim()
                            $commitParentOut = @(& git rev-parse "$commitSha^" 2>$null)
                            $commitParentCode = $LASTEXITCODE
                            $commitParent = if ($commitParentOut.Count -gt 0) { $commitParentOut[-1].Trim() } else { '' }
                            if ($shaCode -ne 0 -or $commitSha -notmatch '^[0-9a-f]{40}$' -or
                                $subjectCode -ne 0 -or $commitSubject -cne $commitMessage -or
                                $commitParentCode -ne 0 -or $commitParent -cne $parentBefore) {
                                Log "PUSH BLOCKED: could not prove the exact nightly commit SHA/parent (possible concurrent HEAD move)"
                            } else {
                                # Bind certification to the server advertised by the one configured push URL.
                                # A local origin/main ref can be stale or falsely ahead and omit wire objects.
                                $remoteState = Get-AdvertisedPushMain
                                if ($null -eq $remoteState) {
                                    Log "PUSH BLOCKED: could not bind the advertised push remote main; commit is LOCAL ONLY"
                                    Write-AtomicUtf8NoBomLines -Path $marker -Lines @("# Nightly push BLOCKED before secret certification ($stamp)", "",
                                      "The exact local commit $commitSha was NOT pushed because the configured push",
                                      "endpoint/main OID could not be uniquely and safely resolved.",
                                      "The next dream retries after remote access/configuration recovers.")
                                } else {
                                    # Scan immutable objects newly reachable relative to the actual advertised OID.
                                    $rangeSpec = "$($remoteState.Oid)..$commitSha"
                                    $rangeCode = 2
                                    $haveRetry = [bool] (Get-Command Invoke-SecretScanRetryingRaces -CommandType Function -ErrorAction SilentlyContinue)
                                    try {
                                        if ($haveRetry) {
                                            $rangeResult = Invoke-SecretScanRetryingRaces -ScannerPath (Join-Path $base 'scan_secrets.py') -Arguments @('--range', $rangeSpec) `
                                                -Label 'persist: whole-range secret scan' -Logger ${function:Log}
                                        } else {
                                            $rangeResult = Invoke-SecretScan -ScannerPath (Join-Path $base 'scan_secrets.py') -Arguments @('--range', $rangeSpec)
                                        }
                                        if ($null -ne $rangeResult -and $rangeResult.PSObject.Properties['Code'] -and
                                            $rangeResult.Code -in @(0, 1, 2)) {
                                            $rangeCode = [int] $rangeResult.Code
                                        }
                                    } catch {}
                                    if ($rangeCode -eq 2) {
                                        $rangeRefusal = ''
                                        try { $rangeRefusal = ((Get-SecretScanRefusalLines -Output @($rangeResult.Output)) -join ' | ') } catch {}
                                        Log ("persist: whole-range secret scan refused (exit=2): " + $(if ($rangeRefusal) { $rangeRefusal } else { 'no refusal line' }) + " - trying each outgoing commit")
                                    }
                                    # 2026-09-09: exit 2 is a scanner ERROR, not a finding. The one seen live was
                                    # "Git objects exceeded the bounded total scan size" (256 MB): every dream commit
                                    # carries ~70 MB of regenerated brain views, so once local main is more than
                                    # three commits ahead of the advertised OID the whole-range scan cannot run and
                                    # the push deferred for days (16 commits, 09-05..09-09). Fall back to scanning
                                    # each outgoing commit against its own parent; the push is allowed only if
                                    # EVERY commit scans clean (exit 0). A finding (1) or a second error still blocks.
                                    if ($rangeCode -eq 2) {
                                        $perCommitOk = $false
                                        try {
                                            $outgoing = @(& git rev-list --reverse $rangeSpec 2>$null)
                                            if ($LASTEXITCODE -eq 0 -and $outgoing.Count -gt 0) {
                                                $perCommitOk = $true
                                                foreach ($oc in $outgoing) {
                                                    $ocParent = (& git rev-parse "$oc^" 2>$null)
                                                    if ($LASTEXITCODE -ne 0 -or -not $ocParent) { $perCommitOk = $false; break }
                                                    if ($haveRetry) {
                                                        $ocResult = Invoke-SecretScanRetryingRaces -ScannerPath (Join-Path $base 'scan_secrets.py') -Arguments @('--range', "$ocParent..$oc") `
                                                            -Label ("persist: per-commit secret scan of " + $oc.Substring(0, 12)) -Logger ${function:Log}
                                                    } else {
                                                        $ocResult = Invoke-SecretScan -ScannerPath (Join-Path $base 'scan_secrets.py') -Arguments @('--range', "$ocParent..$oc")
                                                    }
                                                    $ocCode = 2
                                                    if ($null -ne $ocResult -and $ocResult.PSObject.Properties['Code'] -and $ocResult.Code -in @(0, 1, 2)) { $ocCode = [int] $ocResult.Code }
                                                    if ($ocCode -ne 0) {
                                                        $perCommitOk = $false
                                                        $ocSaid = ''
                                                        if ($ocCode -eq 2) { try { $ocSaid = '; it said: ' + ((Get-SecretScanRefusalLines -Output @($ocResult.Output)) -join ' | ') } catch {} }
                                                        Log ("persist: per-commit secret scan of " + $oc.Substring(0, 12) + " exited " + $ocCode + $ocSaid)
                                                        break
                                                    }
                                                }
                                            }
                                        } catch { $perCommitOk = $false }
                                        if ($perCommitOk) {
                                            Log ("persist: whole-range secret scan was refused (exit=2); all " + $outgoing.Count + " outgoing commit(s) scanned clean one by one - proceeding")
                                            $rangeCode = 0
                                        }
                                    }
                                    if ($rangeCode -ne 0) {
                                        Log ("PUSH BLOCKED by secret gate on server-bound exact range (exit=" + $rangeCode + ") - commit is LOCAL ONLY")
                                        Write-AtomicUtf8NoBomLines -Path $marker -Lines @("# Nightly push BLOCKED by the secret gate ($stamp)", "",
                                          "The server-bound OUTGOING-RANGE scan exited $rangeCode - tonight's exact commit",
                                          "$commitSha exists locally but was NOT pushed.",
                                          "Scanner output is deliberately suppressed from unattended logs.", "",
                                          "To clear: mask/rotate what it found (history surgery if it sits in an older local",
                                          "commit), rerun the range scanner after resolving the advertised push main OID,",
                                          "then delete this file. The next dream pushes only its newly verified commit.")
                                    } else {
                                        # Re-read endpoint and OID after the scan. A changed endpoint invalidates this proof.
                                        # 2026-10-04: a main that moved DURING the scan now goes to Push-WithResync, as a main
                                        # that moves after it always did. The push is leased to the certified base, so it is
                                        # refused; the function then re-syncs onto the new main, certifies that exact new range
                                        # and pushes (its FORWARD case in test_push_with_resync_lease.py). Before, this case
                                        # left the night's commit local (15, 17, 24 Sep and 3 Oct 19:30), because agents push
                                        # to main minutes apart. Without Push-WithResync a moved main still blocks.
                                        $remoteCheck = Get-AdvertisedPushMain
                                        $endpointMoved = ($null -eq $remoteCheck -or $remoteCheck.PushUrl -cne $remoteState.PushUrl -or
                                            "$($remoteCheck.Oid)" -notmatch '^[0-9a-f]{40}$')
                                        $mainMoved = (-not $endpointMoved -and "$($remoteCheck.Oid)" -cne "$($remoteState.Oid)")
                                        $canResync = [bool] (Get-Command Push-WithResync -CommandType Function -ErrorAction SilentlyContinue)
                                        if ($endpointMoved -or ($mainMoved -and -not $canResync)) {
                                            Log "PUSH BLOCKED: advertised push endpoint/main changed during certification"
                                            Write-AtomicUtf8NoBomLines -Path $marker -Lines @("# Nightly push BLOCKED after secret certification ($stamp)", "",
                                              "The exact local commit $commitSha was NOT pushed because the advertised",
                                              "push endpoint or main OID changed while it was being certified.",
                                              "The next dream re-resolves and re-scans the exact outgoing object set.")
                                        } else {
                                            if ($mainMoved) {
                                                Log ("persist: main moved during certification (" + "$($remoteState.Oid)".Substring(0, 7) + " -> " +
                                                     "$($remoteCheck.Oid)".Substring(0, 7) + "); the leased push will re-sync and re-certify")
                                            }
                                            if (Test-Path $marker) { Remove-Item $marker -Force -ErrorAction SilentlyContinue }
                                            $pushed = $false
                                            if (Get-Command Push-WithResync -CommandType Function -ErrorAction SilentlyContinue) {
                                                # 2026-09-25: a push refused because main moved after the certification brings the
                                                # local commits onto the newly advertised main, certifies that exact new range the
                                                # same way as above (whole range, else every commit clean one by one) and pushes the
                                                # new commit. Re-pushing the same commit cannot succeed: the 13:56 nap's three plain
                                                # retries were all refused and left 9cc3fe9 local. The scriptblocks run inside
                                                # Push-WithResync's scope, hence the pwr-prefixed names.
                                                $pwrScanner = Join-Path $base 'scan_secrets.py'
                                                $pwrGetAdv = { Get-AdvertisedPushMain }
                                                $pwrCertify = {
                                                    param([string] $PwrRange)
                                                    $pwrCode = 2
                                                    try {
                                                        $pwrRes = Invoke-SecretScanRetryingRaces -ScannerPath $pwrScanner -Arguments @('--range', $PwrRange) `
                                                            -Label 'persist: re-sync range secret scan' -Logger ${function:Log}
                                                        if ($null -ne $pwrRes -and $pwrRes.PSObject.Properties['Code'] -and $pwrRes.Code -in @(0, 1, 2)) { $pwrCode = [int] $pwrRes.Code }
                                                    } catch {}
                                                    if ($pwrCode -eq 2) {
                                                        $pwrList = @(& git rev-list --reverse $PwrRange 2>$null)
                                                        if ($LASTEXITCODE -eq 0 -and $pwrList.Count -gt 0) {
                                                            $pwrCode = 0
                                                            foreach ($pwrC in $pwrList) {
                                                                $pwrP = (& git rev-parse "$pwrC^" 2>$null)
                                                                if ($LASTEXITCODE -ne 0 -or -not $pwrP) { $pwrCode = 2; break }
                                                                $pwrOne = 2
                                                                try {
                                                                    $pwrRes = Invoke-SecretScanRetryingRaces -ScannerPath $pwrScanner -Arguments @('--range', "$pwrP..$pwrC") `
                                                                        -Label ("persist: per-commit re-sync scan of " + $pwrC.Substring(0, 12)) -Logger ${function:Log}
                                                                    if ($null -ne $pwrRes -and $pwrRes.PSObject.Properties['Code'] -and $pwrRes.Code -in @(0, 1, 2)) { $pwrOne = [int] $pwrRes.Code }
                                                                } catch {}
                                                                if ($pwrOne -ne 0) { $pwrCode = $pwrOne; break }
                                                            }
                                                        }
                                                    }
                                                    Log ("persist: re-sync secret scan of " + $PwrRange + " exited " + $pwrCode)
                                                    return ($pwrCode -eq 0)
                                                }
                                                $pwr = Push-WithResync -Repo $repo -PushUrl $remoteState.PushUrl -CommitSha $commitSha -BaseOid $remoteState.Oid `
                                                    -GetAdvertised $pwrGetAdv -Certify $pwrCertify -MemoryDir (Join-Path $env:USERPROFILE '.claude\projects\{{BRAIN_SLUG}}\memory') `
                                                    -Logger ${function:Log}
                                                $pushed = [bool] $pwr.Pushed
                                                if ("$($pwr.Commit)" -match '^[0-9a-f]{40}$' -and "$($pwr.Commit)" -cne $commitSha) {
                                                    Log ("persist: the certified commit " + $commitSha.Substring(0, 7) + " was re-synced onto the moved main as " + "$($pwr.Commit)".Substring(0, 7))
                                                    $commitSha = [string] $pwr.Commit
                                                }
                                            } else {
                                                $pushRef = "${commitSha}:refs/heads/main"
                                                for ($try = 1; $try -le 3 -and -not $pushed; $try++) {
                                                    $global:LASTEXITCODE = 199
                                                    # 2026-09-20: keep git's own words on a refused push (Out-Null hid a day of
                                                    # non-fast-forward rejections); the 20 s retry stays.
                                                    $pushOut = @(& git -c push.followTags=false -c push.recurseSubmodules=no push --no-follow-tags --no-tags --recurse-submodules=no -- $remoteState.PushUrl $pushRef 2>&1)
                                                    $pushCode = $global:LASTEXITCODE
                                                    if ($pushCode -eq 0) {
                                                        $pushed = $true
                                                    } else {
                                                        Log ("persist: push attempt $try exited $pushCode; git said: " + (($pushOut | Select-Object -Last 2 | ForEach-Object { "$_" }) -join ' | '))
                                                        Start-Sleep -Seconds 20
                                                    }
                                                }
                                            }
                                            $persistHow = if ($pushed) { '(secret gates: clean, exact SHA, advertised push endpoint)' } else { '(not pushed - the lines above say why; the commit stays local for the next cycle)' }
                                            Log "persisted: staged=$staged pushed=$pushed commit=$($commitSha.Substring(0, 7)) $persistHow"
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                    } else { Log 'persist: no memory/brain-view changes to commit' }
                }
            }
            Pop-Location
        } else { Log 'persist: backup repo not found - dream work is local only' }
    } catch { Log "persist failed (work is local): $($_.Exception.Message)" }
} finally {
    if ($null -ne $dreamLease) { $dreamLease.Dispose() }
}
Log 'dream cycle complete'
