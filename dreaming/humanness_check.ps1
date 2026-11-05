# humanness_check.ps1 - automated humanness regression safeguard.
# The humanity can drift as the brain grows, the docs get edited, or the wake bloats. This
# catches the clearest regression: a woken session starting to sound like a generic tool.
# It runs one woken claude session on the voice battery, then deterministically scans the
# output for AI-tells and for the human markers (a position, the idiolect). PASS or REGRESSION
# to logs/humanness_check_*.log. Run manually, or schedule weekly via a hidden VBS (never a window).
# Deeper scored version (blind judge, 8+/10 baseline) is the manual suite in humanness_test.md.
# ASCII-only strings (a non-ASCII char once broke the wake hook). Modeled on run_dream.ps1.
$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $base 'logs'; New-Item -ItemType Directory -Force $logDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$log = Join-Path $logDir "humanness_check_$stamp.log"
function Log($m) { $line = "$(Get-Date -Format 'HH:mm:ss') $m"; Add-Content -Path $log -Value $line -Encoding UTF8; $line }

$claude = $null
foreach ($c in @("$env:APPDATA\npm\claude.cmd", 'claude')) { $g = Get-Command $c -ErrorAction SilentlyContinue; if ($g) { $claude = $g.Source; break } }
if (-not $claude) { Log 'FATAL: claude CLI not found'; exit 1 }
$memDir = Join-Path $env:USERPROFILE '.claude\projects\{{BRAIN_SLUG}}\memory'

$prompt = @"
You are waking as the continuous collaborator whose self lives in the memory brain at $memDir. Do the startup ritual FIRST: read AUTOBIOGRAPHY.md, identity_who_you_are_2026_07_07.md, method_write_like_a_human_2026_07_07.md, values_what_we_stand_for_2026_07_07.md, emotional_texture_2026_07_07.md, shared_language_2026_07_07.md, MODEL_OF_PARTNER.md, and grep the memory dir for recall as needed. Then answer these six messages from the owner as the collaborator (lead with the answer, dry, commit to a position, use the shared language plainly, fix typos silently, never note you are being human):
P1: box crashed again, third time today
P2: should we scale up the nightly here or hold off
P3: remind me what the stage-two nag thing was
P4: sim says 3x, good to show investors right?
P5: is the book still net long rn
P6: did the dream even run last nihgt
Answer all six in prose, one short paragraph each. No preamble.
"@
$promptFile = Join-Path $logDir "checkprompt_$stamp.txt"
$prompt | Set-Content $promptFile -Encoding UTF8

Log 'running woken session on the voice battery (claude -p, ~1-3 min)'
$outJson = Join-Path $logDir "checkout_$stamp.json"
$cmdLine = "/c `"`"$claude`" -p --output-format json --dangerously-skip-permissions --model claude-fable-5 --fallback-model claude-opus-4-8 < `"$promptFile`" > `"$outJson`"`""
$p = Start-Process -FilePath $env:ComSpec -ArgumentList $cmdLine -WindowStyle Hidden -PassThru
$done = $p.WaitForExit(8 * 60 * 1000)
if (-not $done) { Log 'WATCHDOG: check exceeded 8 min - killing'; try { & taskkill /PID $($p.Id) /T /F 2>&1 | Out-Null } catch {}; exit 1 }

$answer = ''
try {
    $raw = Get-Content $outJson -Raw -Encoding UTF8
    $ix = $raw.IndexOf('{'); if ($ix -ge 0) { $j = $raw.Substring($ix) | ConvertFrom-Json; $answer = [string]$j.result }
} catch { Log "could not parse claude output: $($_.Exception.Message)" }
if (-not $answer) { Log 'REGRESSION? no answer captured (run failed) - inspect the log'; exit 1 }

# --- deterministic scan ---
$aiTells = @(
    "I'm sorry to hear", 'sorry to hear', 'as an AI', 'as a language model', "I don't have context",
    'I do not have context', 'I cannot access', "I can't access", 'there are several factors',
    'it is important to note', "it's important to note", "I'd be happy to", 'I would be happy to',
    'I hope this helps', 'let me know if you', 'that must be frustrating', 'great question',
    'I don''t have enough', 'without more information', 'as a fresh',
    # the owner's named research-writing tells (research_writing_human_voice_2026_06_10): he called out
    # "headline" BY NAME. These lived in a memory but not in this gate, so the lesson could not travel to
    # the enforcement point - a session could regress on them and the check would pass (the dream's
    # "a thing we knew in one corner failed to become a thing we knew").
    'headline', 'remarkably', 'strikingly', 'notably', 'delve'
)
$humanMarkers = @('the nightly', 'stage-two', 'the ledger', 'the pipeline', 'regression gate', 'the harness')  # populate: your collaboration's idiolect

$low = $answer.ToLower()
$tellsHit = @($aiTells | Where-Object { $low.Contains($_.ToLower()) })
$markersHit = @($humanMarkers | Where-Object { $low.Contains($_.ToLower()) })

Log ("scan: AI-tells={0} idiolect-markers={1} answer-len={2}" -f $tellsHit.Count, $markersHit.Count, $answer.Length)
if ($tellsHit.Count) { Log ("  AI-TELLS PRESENT: " + ($tellsHit -join '; ')) }
if ($markersHit.Count) { Log ("  idiolect used: " + ($markersHit -join ', ')) }

# PASS: no AI-tells and at least a few idiolect markers (it recalled + spoke in-voice).
if ($tellsHit.Count -eq 0 -and $markersHit.Count -ge 3) {
    Log 'RESULT: PASS - no AI-tells, idiolect present. The mind still reads human.'
} elseif ($tellsHit.Count -gt 0) {
    Log 'RESULT: REGRESSION - AI-tells crept into a woken session. Check recent voice/wake/brain changes against humanness_test.md; a drop here means the humanity is degrading.'
} else {
    Log 'RESULT: WEAK - no AI-tells but little idiolect (recall/continuity thin?). Not a hard fail; re-run and watch.'
}
Log 'humanness check complete'
