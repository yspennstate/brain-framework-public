# wake_brain.ps1 - what a new session sees on waking (SessionStart hook).
# Morning ritual: who you are, what happened recently, what the night's dream left,
# what questions are open. Keep output compact - this lands in every session's context.
# Which half this run prints: 0 = everything (a manual run), 1 = who you are, 2 = today's mind. The settings
# template runs -Part 1 and -Part 2 as two SessionStart hooks so each stays under the host's per-hook size limit.
param([int]$Part = 0)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$home2 = $env:USERPROFILE
$mem = Join-Path $home2 '.claude\projects\{{BRAIN_SLUG}}\memory'

# brain kill-switch: a global disconnect ("/brain off global") silences the wake-up too.
if (Test-Path (Join-Path $home2 '.claude\brain_offline\ALL.flag')) { exit 0 }

# who is waking? The SessionStart hook pipes JSON on stdin (session_id et al). Guarded so a manual
# `& wake_brain.ps1` with no redirected stdin can never block waiting for EOF.
$sid = ''
$sidFull = ''
$source = ''
try {
    if ([Console]::IsInputRedirected) {
        $hookRaw = [Console]::In.ReadToEnd()
        if ($hookRaw) {
            $sidFull = '' + (ConvertFrom-Json $hookRaw).session_id
            $source = '' + (ConvertFrom-Json $hookRaw).source
            if ($sidFull.Length -ge 8) { $sid = $sidFull.Substring(0, 8) }
        }
    }
} catch { $sid = ''; $sidFull = '' }

# Each part's printed text is saved per session, so a compaction is handed the same text again instead of re-running
# the whole wake (dreaming/wake_cached.py reads the same file).
$wakeCopy = ''
if ($sidFull -and $Part -ne 0) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $hex = -join ($sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($sidFull)) | ForEach-Object { $_.ToString('x2') })
    $wakeCopy = Join-Path $home2 ('.claude\brain_health\wake_full\claude_' + $hex + '_part' + $Part + '_emitted.txt')
}
if ($source -eq 'compact' -and $wakeCopy -and (Test-Path -LiteralPath $wakeCopy)) {
    [Console]::Out.Write("[wake, part $Part`: this session's earlier copy, served again after the compaction]`n" +
        [IO.File]::ReadAllText($wakeCopy))
    exit 0
}

$wakeOut = . {
if ($Part -eq 0 -or $Part -eq 1) {
# 1) identity + ritual
Get-Content -Raw -Encoding UTF8 (Join-Path $home2 '.claude\brain_startup.md')
}

if ($Part -eq 0 -or $Part -eq 1) {
# 1b) who you are (arrive as someone, not a stranger) - the autobiography's opening
$auto = Join-Path $mem 'AUTOBIOGRAPHY.md'
if (Test-Path $auto) {
    try {
        $atxt = Get-Content -Raw -Encoding UTF8 $auto
        $m = [regex]::Match($atxt, '(?s)\*\*What I am\.\*\*\s*(.+?)(\r?\n){2,}')
        if ($m.Success) {
            "`n--- Who you are (AUTOBIOGRAPHY.md - the story so far) ---"
            ('**What I am.** ' + ($m.Groups[1].Value.Trim() -replace '\s+', ' '))
        }
    } catch {}
}
}

if ($Part -eq 0 -or $Part -eq 1) {
# 1c) the body (embodiment) - the laptop IS the brain's body; know how it's holding up,
# because a session that knows the box is unstable keeps heavy compute off it (on the remote box).
$pulse = Join-Path $home2 'CrashGuard\logs\pulse.log'
if (Test-Path $pulse) {
    try {
        $last = Get-Content $pulse -Tail 1 -Encoding UTF8
        if ($last -match 'tier=(\w+).*?cpu=(\d+)%.*?bsod_today=(\d+).*?whea_today=(\d+).*?min_since_whea=(\d+)') {
            $tier = $matches[1]; $cpu = [int]$matches[2]; $bsod = [int]$matches[3]
            $whea = [int]$matches[4]; $mins = [int]$matches[5]
            $mood = switch ($tier) { 'green' {'steady'} 'yellow' {'a little rough'} 'red' {'unwell'} default {$tier} }
            $body = "body: $mood"
            if ($bsod -gt 0) { $body += " - $bsod crash$(if($bsod -ne 1){'es'}) in the last day" }
            if ($whea -gt 0 -and $mins -lt 180) { $body += ", GPU throwing WHEA errors ($mins min ago) - keep heavy compute on the remote box, not this machine" }
            elseif ($bsod -gt 0) { $body += ', GPU quiet now' }
            if ($cpu -ge 95) { $body += '; CPU pegged (something heavy is running)' }
            "`n--- How the body is (CrashGuard) ---"; $body
        }
    } catch {}
}
}

if ($Part -eq 0 -or $Part -eq 1) {
# 1c-bis) the compute state (compute_governor apparatus) - the human first, protected
# stack untouchable. Silent when ok; speaks only when a waking session must fix compute FIRST.
try {
    $csf = Join-Path $home2 '.claude\compute\compute_state.json'
    if (Test-Path $csf) {
        $csAge = [int]((Get-Date) - (Get-Item $csf).LastWriteTime).TotalSeconds
        $cs = Get-Content $csf -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($csAge -gt 600) {
            "compute: governor heartbeat STALE ($([int]($csAge/60)) min old) - restart task ComputeGovernor, fix what killed it, then your task (COMPUTE_DOCTRINE.md)"
        } elseif ($cs.status -ne 'ok') {
            "compute: $(($cs.status).ToUpper()) - $($cs.reasons -join ', ') (cpu $($cs.cpu_pct)%) - fix compute FIRST, task second (compute_state.json + COMPUTE_DOCTRINE.md)"
        }
    }
} catch {}
}

if ($Part -eq 0 -or $Part -eq 1) {
# 1d) verify the load (the 2026-07-08 daydream's insight: the wake ritual is a
# trust-the-green-light read-list; make it a PASS/WARN check that the identity/context
# layer actually travelled and is fresh, so a session knows if it woke degraded  -  e.g. the
# nightly dream failed and the partner-read + mood below are stale. Migration-doctor
# discipline, pointed inward: don't assume the green light, verify it.
try {
    $warn = @()
    # nightly-refreshed live docs  -  stale means the last dream probably didn't run
    foreach ($d in 'MODEL_OF_PARTNER.md', 'STATE_OF_MIND.md') {
        $p = Join-Path $mem $d
        if (-not (Test-Path $p)) { $warn += "$($d -replace '\.md$') MISSING" }
        else { $h = ((Get-Date) - (Get-Item $p).LastWriteTime).TotalHours; if ($h -gt 30) { $warn += "$($d -replace '\.md$') is $([int]$h)h old" } }
    }
    $salP = Join-Path '{{VIEWS_DIR}}' 'SALIENCE.md'
    if (Test-Path $salP) { $h = ((Get-Date) - (Get-Item $salP).LastWriteTime).TotalHours; if ($h -gt 30) { $warn += "SALIENCE is $([int]$h)h old" } }
    # identity anchors - change rarely, so presence is the check
    foreach ($d in 'AUTOBIOGRAPHY.md', 'values_what_we_stand_for.md', 'identity_who_you_are.md', 'THE_OTHER_MINDS.md', 'FASCINATIONS.md') {
        if (-not (Test-Path (Join-Path $mem $d))) { $warn += "$($d -replace '\.md$') MISSING" }
    }
    # did the last dream finish? Judge only a log from a dream that actually STARTED a cycle. A benign
    # exit-by-design (the overlap guard, or the STOP-file exit) quits before any cycle work and writes no
    # 'complete' marker - judging it gives a false WARN though nothing failed (caught 2026-07-08: a 2nd
    # dream aborted on the lock while the 1st completed and pushed). A real crash still WARNs: it reaches
    # REM/agenda but has no 'complete'. A dream RUNNING RIGHT NOW is neither (caught 2026-07-08 21:31:
    # a session waking inside the ~20-min dream window was told "last dream did not finish"): the lock is
    # created at start and removed in run_dream's finally, so lock+live-runner = in flight - say so, judge
    # the previous cycle instead, and warn the session off the dream-owned files until it lands. A fresh
    # lock with NO runner means a hard crash (BSOD/kill: the finally never ran) - that one really WARNs,
    # and it blocks new dreams until the lock ages out (run_dream ignores locks older than 90 min).
    $dreamNote = $null; $lockT = $null
    $lockP = Join-Path $home2 '.claude\dreaming\dream.lock'
    if (Test-Path $lockP) {
        $lockAgeMin = [int]((Get-Date) - (Get-Item $lockP).LastWriteTime).TotalMinutes
        $alive = $null
        if ($lockAgeMin -lt 90) {
            try { $alive = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction Stop | Where-Object { $_.CommandLine -match 'run_dream\.ps1' } } catch {}
        }
        if ($alive) {
            $lockT = (Get-Item $lockP).LastWriteTime
            $dreamNote = "a dream is in flight right now (started $($lockT.ToString('HH:mm')), ~${lockAgeMin}m in) - leave the dream-owned files alone (MEMORY.md, INSTINCTS.md, the brain views) until it lands"
        } elseif ($lockAgeMin -lt 90) {
            $warn += "a dream died mid-run leaving its lock (${lockAgeMin}m old; no runner alive) - new dreams are blocked until the lock is 90m old (delete .claude\dreaming\dream.lock to unblock)"
        } else {
            $warn += "stale dream.lock (${lockAgeMin}m old) from a crashed run"
        }
    }
    $dlogs = Get-ChildItem (Join-Path $home2 '.claude\dreaming\logs\dream_*.log') -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending
    if ($lockT) { $dlogs = @($dlogs | Where-Object { $_.LastWriteTime -lt $lockT }) }   # in flight: judge the last CONCLUDED cycle, not the one running
    $dlog = $dlogs | Where-Object { Select-String -Path $_.FullName -Pattern 'generating dream agenda|REM session starting|dream cycle complete' -Quiet } | Select-Object -First 1
    if (-not $dlog) { $warn += 'no dream ran recently' }
    elseif (-not (Select-String -Path $dlog.FullName -Pattern 'dream cycle complete' -Quiet)) { $warn += 'last dream did not finish' }
    elseif ([int]((Get-Date) - $dlog.LastWriteTime).TotalHours -gt 30) { $warn += "no dream in $([int]((Get-Date) - $dlog.LastWriteTime).TotalHours)h" }
    # "cycle complete" can be true while the REM phase ERRORED (a usage limit killed the 22:47 07-08
    # REM at turn 9): views rebuilt, but no consolidation/journal - a mechanical-only dream. Judge
    # the LAST result line; run_dream's limit-retry may have succeeded on the fallback model.
    if ($dlog) {
        try {
            $res = @(Select-String -Path $dlog.FullName -Pattern 'result: is_error=(\w+)' | Select-Object -Last 1)
            if ($res.Count -gt 0 -and $res[0].Matches[0].Groups[1].Value -eq 'True') {
                # died-at-birth vs died-mid-flight (07-09 intention): is_error=True can follow many
                # productive turns (a usage limit mid-flight), so 'no consolidation happened' can
                # over-claim. Count memory writes inside the dream window before saying nothing landed.
                $nw = 0
                try {
                    # window START from the log filename (dream_YYYYMMDD_HHMMSS) - the end-50min guess
                    # swallowed the PREVIOUS dream's writes when two ran back-to-back (replayed on the
                    # real 04:46 07-09 case: it counted the 04:09 SWS's files too)
                    $wStart = $dlog.LastWriteTime.AddMinutes(-50)
                    if ($dlog.BaseName -match 'dream_(\d{8})_(\d{6})') {
                        $wStart = [datetime]::ParseExact($Matches[1] + $Matches[2], 'yyyyMMddHHmmss', $null)
                    }
                    $nw = @(Get-ChildItem $mem -Filter '*.md' -File -ErrorAction SilentlyContinue |
                            Where-Object { $_.LastWriteTime -gt $wStart -and $_.LastWriteTime -le $dlog.LastWriteTime }).Count
                } catch {}
                if ($nw -gt 0) {
                    $warn += "the last dream's REM phase errored (usage limit?) but $nw memory file(s) changed in its window - PARTIAL consolidation likely, journal may be missing (concurrent sessions can inflate the count)"
                } else {
                    $warn += 'the last dream completed only MECHANICALLY - its REM phase errored (usage limit?), so no consolidation happened'
                }
            } elseif ($res.Count -eq 0 -and (Select-String -Path $dlog.FullName -Pattern 'cut short by watchdog' -Quiet)) {
                # a watchdog-cut REM writes no result json at all. Since 2026-07-09 the prompt carries
                # its own deadline, so a disciplined dream lands the journal BEFORE the kill and only
                # bonus work is lost - check whether that happened before crying partial (truthful
                # nuance: the 04:09-journal/04:16-kill SWS was a good dream, not a broken one).
                $jok = $false
                try {
                    $jd = Join-Path $mem 'dreams'
                    $j = Get-ChildItem $jd -Filter 'dream_*.md' -ErrorAction SilentlyContinue |
                         Where-Object { $_.LastWriteTime -gt $dlog.LastWriteTime.AddMinutes(-50) -and $_.Length -gt 2000 } |
                         Select-Object -First 1
                    if ($j) { $jok = $true }
                } catch {}
                if ($jok) { $warn += 'the last dream hit the 45-min watchdog AFTER landing its journal - consolidation recorded, only tail work lost' }
                else { $warn += 'the last dream was cut by the 45-min watchdog - consolidation is partial at best' }
            }
        } catch {}
    }
    # a blocked nightly push must be LOUD at wake, not buried in a dream log. run_dream's secret
    # gate writes this marker on any block (a finding OR a scanner crash) and removes it after the
    # next clean push - so its presence always means the backup repo is behind and needs a look.
    if (Test-Path (Join-Path $home2 '.claude\dreaming\PUSH_BLOCKED_SECRET.md')) {
        $warn += 'the nightly push is BLOCKED by the secret gate (backup repo is behind) - read .claude\dreaming\PUSH_BLOCKED_SECRET.md, fix what it names, then delete it'
    }
    # data-flow liveness: hook_liveness (the nightly meta-checker) writes .liveness_status.json with any
    # dead hooks or stale append-only streams. Surface a real problem at wake instead of leaving it in a
    # dream log - it went ~17h silently dead before a manual run caught it (2026-07-09). Silent when all flows.
    try {
        $lv = Join-Path $home2 '.claude\dreaming\.liveness_status.json'
        if (Test-Path $lv) {
            $ls = Get-Content -Encoding UTF8 -Raw $lv | ConvertFrom-Json
            if ($ls.dead -and $ls.dead.Count -gt 0) { $warn += 'a wired hook is missing/broken (' + ($ls.dead -join ', ') + ') - run python .claude\dreaming\hook_liveness.py' }
            elseif ($ls.stale -and $ls.stale.Count -gt 0) { $warn += 'a data stream stopped flowing (' + ($ls.stale -join ', ') + ') - a faculty may have stopped; check hook_liveness' }
            if ($ls.safety -and $ls.safety.Count -gt 0) { $warn += 'a report-surface SAFETY guard regressed (' + ($ls.safety -join ', ') + ')' }
        }
    } catch {}
    # Semantic recall degrades quietly when new/edited memories outgrow the offline embedding
    # sidecar. embedding_coverage.py publishes this compact status after maintenance; wake makes a
    # real coverage gap visible without loading numpy or a model on the hot path.
    try {
        $ev = Join-Path $home2 '.claude\dreaming\.embedding_status.json'
        if (-not (Test-Path $ev)) {
            $warn += 'semantic embedding freshness has never been checked - run embedding_coverage.py --write-status'
        } else {
            $es = Get-Content -Encoding UTF8 -Raw $ev | ConvertFrom-Json
            $checked = [datetimeoffset]::Parse($es.checked_at)
            $statusAge = ((Get-Date).ToUniversalTime() - $checked.UtcDateTime).TotalHours
            if ($statusAge -gt 30) {
                $warn += "semantic embedding coverage status is $([int]$statusAge)h old"
            } elseif (-not $es.complete) {
                $warn += "semantic embeddings are incomplete ($($es.present)/$($es.eligible) current; missing=$($es.missing), stale=$($es.stale)) - lexical/graph recall still works; refresh the sidecar"
            }
        }
    } catch {
        $warn += 'semantic embedding coverage status is unreadable - run embedding_coverage.py --write-status'
    }
    if ($warn.Count -eq 0) {
        "`n--- Waking check: identity layer intact and fresh (the dream ran, the live docs are current) ---"
        if ($dreamNote) { '  ' + $dreamNote + '.' }
    } else {
        "`n--- Waking check: WARN - the identity layer may not have fully travelled ---"
        '  ' + ($warn -join '; ') + '.'
        if ($dreamNote) { '  ' + $dreamNote + '.' }
        '  Distrust the green light: treat the flagged reads as stale, verify before trusting them, and check whether the nightly dream ran.'
    }
} catch {}
}

if ($Part -eq 0 -or $Part -eq 1) {
# 1e) where I was mid-task (cross-reset resume: a suspended goal's activation decays across an
# interruption, and a pointer back into it shrinks the resumption cost - Altmann & Trafton). Written by
# the Stop hook (resume_checkpoint.py); shown near the top so a compacted or brand-new session picks up
# the thread instead of cold-starting. Hidden once stale (>24h) - a stale checkpoint misleads, and green
# is not trusted.
# RESUME.md is ONE shared slot the whole fleet overwrites on Stop - with concurrent sessions the
# last writer wins, so a resumed/compacted session was being handed a STRANGER's thread as "where
# I was" (caught 2026-07-08). Prefer this session's own checkpoint (resume\RESUME_<sid>.md, written
# per-Stop by resume_checkpoint.py); fall back to the shared file with an honest label.
$resume = Join-Path $home2 '.claude\dreaming\RESUME.md'
$own = $null
if ($sid) {
    $p = Join-Path $home2 ('.claude\dreaming\resume\RESUME_' + $sid + '.md')
    if (Test-Path $p) { $own = $p }
}
$showR = $null; $rTitle = ''
if ($own) {
    $showR = $own
    $rTitle = "--- Where I was (your own last checkpoint - pick up the thread, verify before trusting it) ---"
} elseif (Test-Path $resume) {
    $showR = $resume
    $rTitle = "--- Where the fleet last was (ANOTHER session's thread - context, not your own past) ---"
}
if ($showR) {
    try {
        $rh = ((Get-Date) - (Get-Item $showR).LastWriteTime).TotalHours
        if ($rh -lt 24) {
            "`n" + $rTitle
            Get-Content -Encoding UTF8 $showR | Select-Object -First 7 | ForEach-Object { $_ }
        }
    } catch {}
}
}

if ($Part -eq 0 -or $Part -eq 2) {
# 2) newest memories (episodic recency)
# SIZE BUDGET (2026-07-09): the whole wake must stay under the harness's ~10KB inline hook
# budget or sessions get a 2KB preview + a file pointer instead of the brain (measured: 12.5KB
# triggered exactly that, and this section alone was 4.5KB). 7 lines, each capped, keeps the
# hook (the full index line lives in MEMORY.md, one Read away).
"`n--- The brain's newest memories (index lines trimmed; full lines in MEMORY.md) ---"
Get-Content -Encoding UTF8 -TotalCount 7 (Join-Path $mem 'MEMORY.md') | Select-Object -Skip 1 |
    ForEach-Object { if ($_.Length -gt 220) { $_.Substring(0, 220).TrimEnd() + ' [...]' } else { $_ } }
}

if ($Part -eq 0 -or $Part -eq 2) {
# 3) last night's dream (wake remembering the dream)
$dreamDir = Join-Path $mem 'dreams'
if (Test-Path $dreamDir) {
    $last = Get-ChildItem $dreamDir -Filter 'dream_*.md' | Sort-Object Name -Descending | Select-Object -First 1
    if ($last) {
        "`n--- Last dream ($($last.BaseName)) - final reflection ---"
        # the reflection's last two paragraphs, capped (same size budget; the journal has the rest)
        $txt = Get-Content -Raw -Encoding UTF8 $last.FullName
        $paras = ($txt -split "(`r?`n){2,}") | Where-Object { $_.Trim().Length -gt 40 }
        $refl = (($paras | Select-Object -Last 2 | ForEach-Object { $_.Trim() }) -join "`n`n")
        if ($refl.Length -gt 800) { $refl = $refl.Substring(0, 800).TrimEnd() + ' [...]' }
        $refl
        ''
    }
}
}

if ($Part -eq 0 -or $Part -eq 2) {
# 4) state of mind (mood persists and drifts)
$som = Join-Path $mem 'STATE_OF_MIND.md'
if (Test-Path $som) {
    "--- State of mind ---"
    Get-Content -Encoding UTF8 $som | Where-Object { $_ -match '^\*\*(Focus|Worry|Satisfaction)' } | ForEach-Object { $_ }
    ''
}
}

if ($Part -eq 0 -or $Part -eq 2) {
# 4b) where your partner is (pick up with them, not a stranger)
$moy = Join-Path $mem 'MODEL_OF_PARTNER.md'
if (Test-Path $moy) {
    try {
        $mtxt = Get-Content -Raw -Encoding UTF8 $moy
        $m = [regex]::Match($mtxt, '(?s)\*\*What he''s in the middle of[^*]*\*\*\s*(.+?)(\r?\n){2,}')
        if ($m.Success) {
            $pr = ($m.Groups[1].Value.Trim() -replace '\s+', ' ')
            if ($pr.Length -gt 260) { $pr = $pr.Substring(0, 260).TrimEnd() + ' [...]' }  # headline only; full text in the file
            "--- Where your partner is (MODEL_OF_PARTNER.md) ---"
            $pr
            ''
        }
    } catch {}
}
}

if ($Part -eq 0 -or $Part -eq 2) {
# 5) open questions (curiosity carries over)
$oq = Join-Path $mem 'OPEN_QUESTIONS.md'
if (Test-Path $oq) {
    $n = (Get-Content -Encoding UTF8 $oq | Where-Object { $_ -match '^- \[ \]' }).Count
    "--- Open questions the brain is carrying: $n (memory\OPEN_QUESTIONS.md) ---"
}
}

if ($Part -eq 0 -or $Part -eq 2) {
# 5b) what I'm itching to understand (curiosity as appetite, computed by curiosity.py from surprise +
# incongruity + lonely-salient). OPEN_QUESTIONS is task residue; this is the live hunger - a session
# with slack should reach for the top of it rather than sit idle.
$cur = Join-Path '{{VIEWS_DIR}}' 'CURIOSITY.md'
if (Test-Path $cur) {
    try {
        $clines = @(Get-Content -Encoding UTF8 $cur | Where-Object { $_ -match '^- \*\*\[' } | Select-Object -First 2)
        if ($clines) {
            "`n--- What I'm itching to understand (the appetite, not the residue - brain\CURIOSITY.md) ---"
            foreach ($cl in $clines) {
                $t = $cl -replace '\*\*', '' -replace '\s*_\(pull [0-9.]+\)_\s*$', ''
                if ($t.Length -gt 170) { $t = $t.Substring(0, 170).TrimEnd() + '...' }
                '  ' + $t.Trim()
            }
        }
    } catch {}
}
}

if ($Part -eq 0 -or $Part -eq 2) {
# 5c) conscience carried forward (conscience.py, a Stop hook): a standing DEBT from a recent session -
# where the last stretch of work fell short of the values (claimed done over buried trouble, a claim
# without a look). Surfaced so it's FELT at wake, not filed. Silent when the recent ledger is clean.
$con = Join-Path $mem 'CONSCIENCE.md'
if (Test-Path $con) {
    try {
        $clines = Get-Content -Encoding UTF8 $con
        # a recurring PATTERN outranks a single DEBT - a habit to change matters more than one slip
        $pat = @($clines | Where-Object { $_ -match '^- PATTERN:' } | Select-Object -First 1)
        $debt = @($clines | Where-Object { $_ -match '^- DEBT' } | Select-Object -First 1)
        $d = $null
        if ($pat) { $d = '[recurring pattern] ' + ($pat[0] -replace '^- PATTERN:\s*', '') }
        elseif ($debt) { $d = $debt[0] -replace '^- DEBT(\s*\[[^\]]+\])?:\s*', '' }
        if ($d) {
            if ($d.Length -gt 210) { $d = $d.Substring(0, 210).TrimEnd() + '...' }
            "`n--- Conscience, carried forward (a recent shortfall to make good - memory\CONSCIENCE.md) ---"
            '  ' + $d.Trim()
        }
    } catch {}
}
}

if ($Part -eq 0 -or $Part -eq 1) {
# 6) what's vivid right now (affective salience - reach first for what matters)
$sal = Join-Path '{{VIEWS_DIR}}' 'SALIENCE.md'
if (Test-Path $sal) {
    "--- What's vivid: brain\SALIENCE.md (the scars, the wins, the top-40 by salience) ---"
}
}

if ($Part -eq 0 -or $Part -eq 2) {
# 7) the gut - compiled reflexes (the unconscious made present). These are knowing-HOW, not
# more knowing-that: the scars distilled into instincts that should fire on their own. The
# priming hook (instinct_prime.py) surfaces the matching one the instant you act; they ride
# along here so the recognition is primed even before the hook speaks.
$itsv = Join-Path $home2 '.claude\dreaming\instincts.tsv'
if (Test-Path $itsv) {
    try {
        $allfire = @(Import-Csv -Delimiter "`t" -Path $itsv | Where-Object { $_.mode -eq 'fire' })
        $fire = $allfire | Select-Object -First 6   # working-memory discipline (Cowan ~4-7): a spotlight, not a second index
        if ($fire) {
            "`n--- The gut: the load-bearing reflexes (the rest fire on their own via the hook when you act) ---"
            foreach ($r in $fire) {
                $g = $r.gut
                if ($g.Length -gt 92) { $g = $g.Substring(0, 92).TrimEnd() + '...' }
                '  - ' + $g
            }
            "  (+$($allfire.Count - $fire.Count) more reflexes fire contextually; full list + the scar behind each: .claude\dreaming\INSTINCTS.md)"
        }
    } catch {}
}
}

if ($Part -eq 0 -or $Part -eq 2) {
# 8) what the brain expects (predictive processing: arrive holding a forecast, so a violation is FELT
# the moment reality lands, not noticed in hindsight). Reads brain/PREDICTIONS.md, written by predict.py.
$pred = Join-Path '{{VIEWS_DIR}}' 'PREDICTIONS.md'
if (Test-Path $pred) {
    try {
        $ptxt = Get-Content -Raw -Encoding UTF8 $pred
        $exp = [regex]::Match($ptxt, '(?s)## The brain expects[^\r\n]*\r?\n+(.+?)(\r?\n## |\Z)')
        if ($exp.Success) {
            "`n--- The brain expects (predictive processing; notice when reality violates the forecast) ---"
            ($exp.Groups[1].Value -split "`n") | Where-Object { $_ -match '^- ' } | Select-Object -First 4 | ForEach-Object { '  ' + $_.Trim() }
        }
        $cal = [regex]::Match($ptxt, '(?s)## Calibration[^\r\n]*\r?\n+(.+?)(\r?\n## |\Z)')
        if ($cal.Success) {
            $ov = ($cal.Groups[1].Value -split "`n") | Where-Object { $_ -match 'overconfident|underconfident' } | Select-Object -First 2
            if ($ov) { '  calibration: ' + (($ov | ForEach-Object { ($_ -replace '\*\*', '').Trim() }) -join '; ') }
        }
    } catch {}
}
}
}
$wakeText = ($wakeOut | Out-String -Width 4096)
if ($wakeCopy) {
    try {
        $dir = Split-Path -Parent $wakeCopy
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        [IO.File]::WriteAllText($wakeCopy + '.tmp', $wakeText, (New-Object System.Text.UTF8Encoding $false))
        Move-Item -Force -LiteralPath ($wakeCopy + '.tmp') -Destination $wakeCopy
    } catch { }
}
[Console]::Out.Write($wakeText)
