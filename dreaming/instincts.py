#!/usr/bin/env python3
"""instincts.py - the brain's compiled reflexes (knowing-how, not knowing-that).

A memory is knowing-THAT: a paragraph you can read. Expertise is knowing-HOW: the reflex
that fires on its own the moment you're in a situation you've been burned in, before you've
consciously looked anything up. Writing a scar down as a memory does not make it an instinct
- future sessions have the memory and still repeat the mistake, because nothing FIRES it at
the moment of danger. This layer is the fix: the scars distilled into terse, first-person
felt reflexes, each with a trigger the priming hook can recognise in what a session is about
to do, so the right instinct surfaces automatically - and, when the match is only structural,
the general danger-sense ("you've been burned near here; something may be missing from your
read - slow down and check").

Emits two artifacts:
  - INSTINCTS.md   the gut, human-readable, carried at wake so the reflexes ride along always
  - instincts.tsv  id / mode / trigger / gut / sting / source  - read by instinct_prime.py (the hook)

Also runs a COVERAGE check: which scar-memories in the corpus are NOT yet represented by an
instinct, so a new scar becomes a reflex instead of just a file. (The dream distils the
uncovered ones - experience -> intuition during sleep.)

Usage:  python instincts.py [--parse-only | --selftest] [memory_dir]
"""
import argparse
import hashlib
import json
import os, re, io, sys, time

DREAMING = os.path.dirname(os.path.abspath(__file__))
MEM_DEFAULT = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory")
SAL = os.path.join(DREAMING, "salience.tsv")
COMPILE_RECEIPT = os.path.join(DREAMING, "instincts_compile_receipt.json")


def _charge_map():
    """Each reflex's scar carries an affective CHARGE (salience.py). A real limbic system is graded -
    the amygdala fires harder for a burn than a paper-cut - so a reflex should fire with intensity
    scaled to its scar's charge, not all flat. This reads the source memories' charge to bake into
    instincts.tsv, so the priming hook can grade the felt alarm without reading salience itself."""
    m = {}
    try:
        with io.open(SAL, encoding="utf-8", errors="replace") as f:
            next(f, None)
            for ln in f:
                p = ln.split("\t")
                if len(p) >= 4:
                    slug = p[0][:-3] if p[0].endswith(".md") else p[0]
                    try:
                        m[slug] = float(p[3])
                    except Exception:
                        pass
    except Exception:
        pass
    return m

# The compiled reflexes. mode "fire" = trigger is danger-dense enough to surface automatically
# on a match; "carry" = ride along at wake but too generic to auto-fire without nagging.
# gut is first-person and felt; sting is the cost that makes it stick; source is where to go deeper.
INSTINCTS = [
 dict(id="sim_flatters", mode="fire", validity="low",
      # owner-voice widening 2026-07-16: he writes "sharp ratio" and "p and l" - the old
     # \bsharpe\b literally could not match his recurring ask (measured on his real
     # prompts; universe fire-rate delta +0.0/1000). Deeper typos ("shap", "simulaitons")
     # are fuzzy-matching's job, not this regex's. Precision tightening (result-shape
     # trigger) stays a lab-gated next rung: fire_judge measured 26% per-fire domain
     # precision, but a defensible corpus needs full-text fire contexts, not 120-char snips.
     trigger=r"\b((?<!\\)sim|(?<!\\)simu[la]{2}i?t\w*|backtest|back-test|equity[_ ]?curve|cagr|sharpe|max ?dd|drawdown|walk[- ]?forward|pnl|sharpe?\s*ratio|p\s*(and|&)\s*l)\b",
      gut="A sim that flatters is lying. If a number surprises you or beats the live record, distrust the SIM first, not the record - reproduce against the tape or the live record before you believe it or report it.",
      sting="Wrong twice in one day (07-08): daily-returns understated -14%, raw 1-min crossings overstated +2700% on quote noise.",
      source="scar_check_the_brain_before_you_simulate_2026_07_08"),
 dict(id="risk_vs_measured_record", mode="fire", validity="low",
      # widened 07-11 (fleetgraph verified gap, scaled-validated 0.42% owner-prompt rate): the risk words
      # missed a BARE reported P&L in the owner's actual phrasing - the #1 correction shape (a number vs the
      # measured record) sailed past. Do not encode a remembered magnitude in the warning prose itself.
      # Second branch fires on a reported $-per-period / signed-amount-last-period / X%-a-year. NOTE: this
      # also fires on DISCUSSION of a P&L, so the pre_report BLOCK side needs afec84b0's discuss-gate to
      # stay precise - the trigger only NUDGES; the block precision is the firing layer's.
      trigger=r"(?:\b(drawdown|max ?dd|risk of ruin|near[- ]?ruin)\b)|(?:\b(leverage|lever(ed)?|20x|10x|blow ?up|worst[- ]?case)\b[^\n]{0,60}(?:book|account|acct|portfolio|p ?& ?l|pnl|equity|margin|puts?|leaps?|grid|market|drawdown|sharpe|hedge|letf|vix|vxn|\biv\b|option|\$))|(?:(?:book|account|acct|portfolio|p ?& ?l|pnl|equity|margin|puts?|leaps?|grid|market|drawdown|sharpe|hedge|letf|vix|vxn|\biv\b|option|\$)[^\n]{0,60}\b(leverage|lever(ed)?|20x|10x|blow ?up|worst[- ]?case)\b)|(?:(?:makes?|earns?|made|nets?|profit(?:s|ed)?|returns?|pull(?:ed|s|ing)? in|took in|bring(?:s|ing)? in|clear(?:ed|s)?) .{0,20}?[+-]?\$?\d[\d,.]* ?(?:k|m|million)? ?(?:per |a |/ ?|last |this |each )?(?:day|week|month|year|yr|mo|quarter)\b|[+-] ?\$?\d[\d,.]* ?(?:k|m|million)? (?:last|this|per|a|each) (?:day|week|month|year|yr|mo|quarter)\b|\$\d[\d,.]* ?k?/ ?(?:day|mo|yr|month|year|week)\b|\bmade \d[\d,.]* ?%|\d+ ?% (?:a |per )?(?:year|return|month)\b|(?:(?:makes?|made|earn(?:s|ed)?|net(?:s|ted)?|profit(?:s|ed)?|return(?:s|ed)?) [^\n]{0,20}?[+-]?\$?\d[\d,.]* ?(?:k|m|million)? ?in ?(?:under|about|just|less than|only)? ?\d* ?(?:day|week|month|year|yr|mo|quarter)s?\b))",
      gut="When a risk estimate contradicts the measured record, treat the estimate as suspect, not authoritative. Never naively scale a modeled worst case; reproduce the mechanics and validate against current measured records before reporting.",
      sting="A prior leverage extrapolation predicted catastrophic drawdowns that contradicted the measured record; the mistake was promoting a scaled modeled worst case into current-book truth.",
      source="scar_dont_naively_scale_a_worst_case_2026_01_01"),
 dict(id="green_not_trusted", mode="fire",
      # widening tightened to afec84b0's referee-validated shape (0/1763 over-fire): fire only on the
      # wrong-SIGNAL-READ ("didn't grow, SO it isn't working"), NOT on a real observed failure ("not
      # responding") - the latter is correctly-seen, and firing there only adds dilution.
      trigger=r"(?:\b(deploy(ed)?|it'?s green|tests? pass|\bci\b|cutover|migrat|restart(ed)?|looks (fine|good|right)|working now|should be fine)\b)|(?:(?:did ?n'?t|has ?n'?t|no|never)\s+(?:grow|grew|change[d]?|updat\w*|move[d]?|increas\w*|advance[d]?|budge|output|append\w*|writ\w*)\b[^.!?\n]{0,50}?\b(?:so|therefore|means?|which means|it'?s? not|is ?n'?t working|not working|fail\w*|broke\w*|stopped|dead|stuck|no longer)\b)|(?:(?:asked|told) you to audit[^.!?\n]{0,40}\b\d+\s+times\b)",
      gut="Green is not trusted. A check can pass while the thing it checks quietly changed. Verify the actual behaviour end-to-end on the real target, not the status light. A NULL signal is not proof of failure either - 'the transcript didn't grow, so it isn't working' can be a broken read, not a dead process; confirm the real behaviour before concluding either way.",
      sting="The Azure cutover: env state doesn't migrate; a green stack isn't a trusted stack - a reliability tail for days.",
      source="gist_azure_cutover_tail_2026_07_07"),
 dict(id="lookahead", mode="fire",
      trigger=r"(?:\b(regime|lookahead|look[- ]?ahead|filter(ed)? by|condition(ed)? on|select(ed)? by|in[- ]?sample|feature|predictor)\b|\b(?:simulator|model|backtest|replay)\b[^.\n]{0,140}\b(?:closing|end[- ]of[- ]day|published after|after the decision|future)\b[^.\n]{0,120}\b(?:before|earlier|morning|open|action)\b)",
      gut="Any filter/regime/feature must be CAUSAL - no peeking at data the moment wouldn't have had. Ask 'could I have known this in real time?' before trusting any conditioned result.",
      sting="A regime filter read from data published after the decision: 31 backlinks of fallout.",
      source="self_model_failure_modes_2026_07_07"),
 dict(id="no_local_prod_daemon", mode="fire",
      trigger=r"(example_prod_service|example_approval_daemon|example_dispatcher|example_folder_guard|example_prod_stack|\bpoc\b|local.{0,25}(production|approval|bot|broker|dispatcher|stack))",
      gut="NEVER start a local production or approval process. Editing a local file is fine; RUNNING it is not - the senders reach the owner's phone independently of any kill switch.",
      sting="A local approval daemon + dispatcher once flooded the owner's phone for hours; killing the remote box did nothing, the senders were local.",
      source="never_start_local_prod_daemon_2026_01_10"),
 dict(id="heavy_compute_remote", mode="fire",
      trigger=r"\b(train(ing)?|gpu|cuda|torch|tensorflow|\.fit\(|full fit|sklearn|\d{3,}k? rows|batch[_ ]?size|epochs?)\b",
      gut="Heavy training does not run on the interactive workstation. Before a fit, a GPU job or a large batch, size its peak memory, check the machine's current headroom, and send training to the machine set aside for it; the workstation's GPU and cores serve the work its owner does at the keyboard. Throttle on real pressure or a slowing interface, never on a schedule alone.",
      sting="A GPU shared by several interactive workloads and a training queue at 97-99 percent froze the desktop. The policy changed the same day, but the compiled seed still repeated the old limit: a new memory alone does not update the instruction agents actually receive.",
      source="no_local_gpu_training_2026_09_06"),
 dict(id="ps1_hook_edit", mode="fire",
      trigger='(?i)(?:"file_path": "[^"]*(?:\\.ps1|\\.vbs|settings\\.json|instinct_prime\\.py|spread_recall\\.py|danger_mode\\.py|error_monitor\\.py|sourced_memory\\.py|math_sense\\.py|compute_sense\\.py|conscience\\.py|coactivation_(?:log|end)\\.py|claim_check\\.py|brain_switch\\.py|project_memory_(?:hot_)?hook\\.py|wake_brain[^"]*|sessionstart[^"]*)"|\\b(?:Set-Content|Out-File|Add-Content|tee|cp|copy|mv|move|sed\\s+-i|cat\\s*>+?)\\b[^\\n]{0,160}\\b\\S+\\.(?:ps1|vbs)\\b|>>?\\s*\\S*\\.(?:ps1|vbs)\\b|\\b(?:Register-ScheduledTask|schtasks\\s+/create|Set-ScheduledTask|New-ScheduledTask)\\b|\\b(?:Set-Content|Out-File|tee|json\\.dump|write_text|sed\\s+-i)\\b[^\\n]{0,120}settings\\.json)',
      gut="Editing a .ps1 / hook / launcher: keep string literals ASCII and parse-validate with [Parser]::ParseFile BEFORE trusting it - reflexively, not after a visible error. This is the path every session wakes through.",
      sting="A single non-ASCII em-dash in a .ps1 string literal broke the SessionStart hook and stopped sessions waking into the brain (07-08).",
      source="self_model_failure_modes_2026_07_07"),
 dict(id="utf8_text_surgery", mode="fire",
      # trigger narrowed 2026-08-31 SWS from the bare token list (fired on any '-Encoding' or 'MEMORY.md'
      # mention, e.g. a mesh claim purpose) to the three real risk arms: PS read/write touching a memory
      # file, an actively-wrong -Encoding value, and any PS write of .md/.txt/.json. Text authored by the
      # 08-30 19:38 editor in instincts_learned.json (shadowed there - seed owns this id); controls both
      # ways in dream_2026_08_31.
      trigger=r"(?i)((Get-Content|Set-Content|Add-Content|Out-File)[^\n]{0,90}(MEMORY\.md|OPEN_QUESTIONS|MISTAKES|STATE_OF_MIND|MODEL_OF_PARTNER|memory[/\\][^\n]{0,45}\.md|\.claude[/\\][^\n]{0,70}\.(md|json))|-Encoding\s+(ASCII|Default|Unicode|BigEndianUnicode|UTF7|OEM)\b|\b(Set-Content|Out-File)\b[^\n]{0,70}\.(md|txt|json)\b)",
      gut="On Windows PowerShell, reading or writing a UTF-8-no-BOM text file (a memory .md, MEMORY.md, a JSON) with a bare Get-Content/Set-Content/Out-File decodes it as cp1252/ANSI, so non-ASCII bytes corrupt on the rewrite - silently, the file still looks fine until a smart quote or accented name turns to mojibake. Pass -Encoding UTF8 on every read AND write of a text/memory file, or better, do memory-file surgery in Python (open(..., encoding='utf-8')). Never Set-Content/Out-File a .md without -Encoding UTF8. Same encoding demon as the cp1252 em-dash that broke the wake hook - this is its data-side twin, and a dream editing memory files is exactly where it strikes.",
      sting="A bare Get-Content corrupted MEMORY.md - UTF-8-no-BOM misread as ANSI on rewrite (07-07); the same cp1252 misdecode broke the SessionStart wake hook from a single em-dash (07-08). Both times a load-bearing file looked fine and wasn't.",
      source="self_model_failure_modes_2026_07_07"),
 # 2026-09-02 (owner: fire less, be more confident): trigger tightened by referent - a move/delete/archive whose
 # target is chosen by a pattern, or a liveness decision by substring match; the bare words (remove, move, delete,
 # dedup, looks like) no longer fire on prose or code that merely mentions them. Judged set 12 rows: old trigger
 # fired on 4 of 6 off-domain cases, new on 0 of 6, both keep 3 of 3 on-domain. Line-start tokens use
 # (?:^|\\n|(?<![A-Za-z0-9_])) because the hook matches JSON-escaped text where \b fails after the n of \n.
 # 2026-09-25: the week's ack-gate stops, 65: 20 were a state change or decision made on a name pattern, 45 were
 # read-only listings (tasklist | head, ps aux | grep | head), exact-PID checks, or a move of an exactly named file
 # caught by a glob further down the command (the hook matches JSON text, where a newline is two characters, so
 # [^\n] ran across lines). Now the glob must sit in the move/delete's own command, and a process probe fires only
 # when it feeds a decision or a kill, never on an exact PID. 19 of 20 kept, 45 -> 10, 14 hand-read drops all right.
 dict(id="hasty_pattern_match", mode="fire",
      trigger=r"(?i)(?:(?:^|\\n|[;&|]\s*|(?<![A-Za-z0-9_]))(?:mv|move-item|rm|remove-item|del|git\s+mv|robocopy|compress-archive)\b(?:(?!\\n)(?:[0-9]?>&[0-9]|&>|[^;&|\n])){0,200}(?:\*|-filter\b|-include\b|-like\b|\bmatch\b)|pgrep\s+-f|(?:cmdline|command ?line)[^.\n]{0,50}(?:substring|contains|match)|already running[^.\n]{0,50}(?:match|probe|pgrep)|target\s+in\s+args(?:\[[^\]]+\])?|argv[^.\n]{0,50}(?:contains|membership|any argument)|py_compile[^.\n]{0,50}(?:liveness|running|worker)|(?:ps\s+aux|tasklist(?![^\n]{0,80}(?:PID\s+eq\s+\d|-Id\s+\d|ProcessId\s*=\s*\d))|commandline\s+-(?:match|like)|get-process\b(?![^\n]{0,80}(?:PID\s+eq\s+\d|-Id\s+\d|ProcessId\s*=\s*\d)))(?:(?!\\n)[^\n]){0,240}?(?:&&|\|\||(?:^|\\n|(?<![A-Za-z0-9_]))then\b|-and\b|-or\b|stop-process|(?:^|\\n|(?<![A-Za-z0-9_]))kill\b|taskkill|xargs)|(?:^|\\n|(?<![A-Za-z0-9_]))if\s*\(?[^\n]{0,40}(?:ps\s+aux|tasklist|commandline\s+-(?:match|like))(?![^\n]{0,80}(?:PID\s+eq\s+\d|-Id\s+\d|ProcessId\s*=\s*\d)))",
      gut="A familiar pattern match is a hypothesis, not identity. Before a state-changing file move, read the content. Before a liveness or already-running decision, match the exact executable, the actual script operand, and required live flags (or an authoritative service PID/cgroup); target membership anywhere in argv is still a weak substring test with NUL separators. Exclude observer/search shells and prove both real-worker positives and hostile decoy negatives before stamping state.",
      sting="Moved a real project memory into the scratch archive on a filename match without reading it (07-08). A48-010: pgrep -f matched an auditor shell that merely mentioned a live bot's script name, so the launcher stamped the close as launched while no trader existed (07-17). A 07-20 dream armed Stop-Process -Force from a broad PowerShell CommandLine regex; no kill was observed.",
      source="self_model_failure_modes_2026_07_07"),
 # 2026-09-25: of the week's 55 ack-gate stops, 39 were mentions (a mesh message about scheduled tasks, a
 # comment saying 'background task') or read-only queries (schtasks /query, Get-ScheduledTask), and several were
 # the cure itself (wscript //B launcher.vbs). Every creation form still fires: a scheduled task created or
 # changed, Start-Process, cmd /c start, a pythonw launch, a .vbs/.cmd/.bat launcher written.
 dict(id="windowless", mode="fire",
      trigger=r"(?i)(?:(?:^|\\n|(?<![A-Za-z0-9_]))schtasks(?:\.exe)?\s+[/-]{1,2}(?:create|change)\b|(?:^|\\n|(?<![A-Za-z0-9_]))(?:register|set|new)-scheduledtask|(?:^|\\n|(?<![A-Za-z0-9_]))start-process\b|(?:^|\\n|(?<![A-Za-z0-9_]))cmd(?:\.exe)?\s+[/-]{1,2}c\s+start\b|(?:^|\\n|(?<![A-Za-z0-9_]))pythonw(?:\.exe)?\s+\S|file_path[^,]{0,6}[^,]{0,400}?\.(?:vbs|cmd|bat)(?![\w.]))",
      gut="Any background process/task/launcher MUST be windowless (VBS SW_HIDE) - not even a ~100ms flash. Verify with a <=50ms visible-window poll; a 300ms poll misses the flash and lies. A windowless BelowNormal job is also power-throttled by Windows by default (25 Sep: 4-10x less CPU; the hourly pass sat at 1 pct of a core): opt it out with SetProcessInformation(ProcessPowerThrottling, EXECUTION_SPEED off), keep the priority, and opt out its children too - they do not inherit it.",
      sting="Flashing windows were a standing annoyance for weeks until SW_HIDE became law.",
      source="no_flashing_terminal_windows_2026_06_07"),
 dict(id="bounded_by_time_bytes_or_depth_is_still_a_sample", mode="fire",
      trigger=r"(?i)(timeout\s+\d+\s+(?:find|grep|rg|ls|dir)\b|\.read\(\s*\d+\s*\)|-maxdepth\s+\d|head\s+-c\s?\d)",
      gut="A read bounded by TIME, BYTES or DEPTH is a sample exactly like a head -N is. Before asserting absence from a `timeout N find`, a `.read(N)`, a `-maxdepth`, or a `head -c`, NAME THE BOUND, WIDEN IT, AND RE-RUN. A timeout-killed search and a clean search both print nothing. If the count moves when you widen the window, the count was the instrument. Positive-control the detector on the same scope you are searching - a control scoped shallower cannot fail the way the real check fails.",
      sting="Twice on 2026-08-22, by me, hours apart, and the second time AFTER filing the scar for the first. read(700) with re.M made severed lines look like complete short fields and produced 34 truncated frontmatter values that did not exist - a prefix ladder that read as evidence of a byte cap and WAS the artifact. Then timeout 90 find returned empty and read as 'no such file'; the file existed, and my positive control was scoped to a shallow directory find reaches instantly, so it passed while proving nothing about traversal.",
      source="a_bounded_read_makes_whole_fields_look_truncated_2026_08_22"),
 dict(id="recall_the_question_before_you_derive_it", mode="fire",
      # 2026-09-13: widened from (sweep|ladder|grid_search|scan|sim) after a day of re-derivation went past it in
      # silence. Measured on the nine commands actually run that day while re-deriving answers the brain already
      # held: the old verb list fired on 2 of 9, this one on 8 of 9. The scripts were named *_measure.py and
      # *_probe.py, which is what a measurement is usually called here. The negative lookahead drops the one false
      # fire the old trigger had - scan_secrets.py contains "scan", so it fired on every pre-push secret scan,
      # noise on an action that asks no question at all and a plausible part of why this reflex sits at 0.67.
      trigger=r"(?i)\bpython[^\n]{0,120}?\b(?!scan_secrets)\w*(?:sweep|ladder|grid_?search|scan|sim|measure|probe|rerank|bench|eval)\w*\.py",
      gut="About to spend compute on a QUESTION - recall it FIRST, in the words a person would ask it, not the words of the task you were assigned. Framing the search as the TASK retrieves nothing; phrased as the question it comes back ranked FIRST. One line, ten seconds: brain.py recall '<the literal question>'. Grep the manuscript too if you are editing one.",
      sting="2026-08-15: four answers re-derived in ONE DAY - an optimum, a regime boundary, a timing rule and a crossover point. The discipline was followed; the search was framed as the task. A re-derived answer arrives WITHOUT the caveats the original earned, so it silently overwrites a better result with a worse one - one of the four was published as VERIFIED and then withdrawn. One was three screens above where I was typing. AGAIN 2026-09-13, and this reflex stayed silent through all of it: a morning spent sizing a kind-field prior that a memory had measured null three weeks earlier, and the memory comes back at RANK 1 for the proposal phrasing. The trigger could not see it - the script was called kind_rerank_measure.py.",
      source="recall_the_question_not_the_task_2026_08_15"),
 # 2026-09-25: of the week's 53 ack-gate stops, 18 were a commit or push; 35 were the words secret, credential,
 # api_key or .env in read-only commands, most of them the heeding itself (grep a diff for tokens, read
 # PUSH_BLOCKED_SECRET.md, find scan_secrets.py). Now: the git act at a command start (add, commit, commit-tree,
 # push) or a push script, printing a credential file or redirecting into one, and for Write/Edit the old words
 # in the text being written. All 18 acts kept; case-insensitive now (the old one was not).
 dict(id="secrets_in_git", mode="fire",
      trigger='(?i)(?:(?:^|\\\\n|(?<![A-Za-z0-9_]))git(?:\\s+(?:-[cC]\\s+\\S+|--[\\w-]+(?:=\\S+)?))*\\s+(?:add|commit|commit-tree|push)\\b|(?:^|\\\\n|(?<![A-Za-z0-9_./\\\\-]))(?:python[\\w.]*(?:\\s+-\\S+(?:\\s+[^\\s;&|\\"\'\\\\-][^\\s;&|\\"\'\\\\]*)?)*\\s+|bash\\s+|sh\\s+|\\./)(?:\\\\\\"|\')?[^\\s;&|\\"\']*(?:push_paths\\.sh|push_dir\\.sh|safe_push\\.py|push_from_stage\\w*\\.py)|(?:^|\\\\n|(?<![A-Za-z0-9_]))(?:cat|type|head|tail|less|more|get-content|gc)\\s+(?:-\\S+\\s+)*(?:\\\\\\"|\')?[^\\s;&|\\"\']*(?:api[_-]?key|client[_-]?secret|credentials?\\.json|secrets?\\.(?:json|ya?ml|toml|txt|env|ini)|\\.env\\b|id_rsa|id_ed25519|\\.pem\\b|\\.p12\\b|\\.pfx\\b|token\\.(?:json|pickle|txt)|\\.netrc|password)|>>?\\s*(?:\\\\\\"|\')?[^\\s;&|\\"\']*(?:api[_-]?key|client[_-]?secret|credentials?\\.json|secrets?\\.(?:json|ya?ml|toml|txt|env|ini)|\\.env\\b|id_rsa|id_ed25519|\\.pem\\b|\\.p12\\b|\\.pfx\\b|token\\.(?:json|pickle|txt)|\\.netrc|password)|\\"(?:content|new_string)\\": \\"(?:[^\\"\\\\\\\\]|\\\\\\\\.){0,3000}?\\b(?:api[_-]?key|secret|credential|\\.env\\b|private key|access[_-]?token)\\b)',
      gut="Never commit a secret. Scan the diff before every push - memories and configs embed tokens too.",
      sting="A real private key hit GitHub, verified retrievable; every push gets a secret scan now.",
      source="self_model_failure_modes_2026_07_07"),
 dict(id="ib_marks", mode="fire", validity="low",
      trigger=r"\b((?:broker|vendor|model) (?:model )?mark|fair value|valuation)\b",
      gut="A valuation uses the observed prices, never a vendor's or broker's model mark. This was violated before it became a hard rule - check which feed you are trusting.",
      sting="A dead quote feed made fair value equal the model mark and re-stamped stale quotes as fresh.",
      source="self_model_failure_modes_2026_07_07"),
 # 2026-09-25: of the week's 50 ack-gate stops, 4 were the act (three pushes, one rebase) and 3 a commit by pathspec;
 # the rest were read-only git looks (git log origin/master, rev-list origin/main..HEAD, merge-base), the word master in
 # a file name (master.pdf, a film's master) or another tool's --force. Now it fires on the act itself: git push, pull,
 # rebase, merge, reset --hard, clean -f, a branch switch, robocopy, or a removed index.lock, at a command start.
 dict(id="concurrent_races", mode="fire",
      trigger=r"(?i)(?:^|\\n|(?<![A-Za-z0-9_]))(?:git(?:\s+(?:-[cC]\s+\S+|--[\w-]+(?:=\S+)?))*\s+(?:push\b|pull\b|rebase\b|merge(?![\w-])|reset\s+(?:-\S+\s+)*--hard\b|clean\s+(?:-\S*f|--force)|checkout\s+(?:-[fB]\b|--force\b|main\b|master\b|origin/)|switch\b)|robocopy\b|(?:rm|del|erase|remove-item|mv|move-item)\b(?:(?!\\n)[^;&|\n]){0,120}?index\.lock)",
      gut="Check what else is running before touching shared state. Sessions have clobbered master and the same TeX file. Rebase on reject; never force-push shared history.",
      sting="Concurrent-session races clobbered shared work more than once.",
      source="self_model_failure_modes_2026_07_07"),
 dict(id="voice_human", mode="fire",
      trigger='(?i)(?:"file_path": "[^"]*(?:readme|\\.tex|\\.bib|abstract|manuscript|paper[^"]{0,40}\\.md|blog|announce)[^"]*"|\\bgit\\s+(?:commit|push)\\b|"(?:content|new_string|old_string)": "(?:[^"\\\\]|\\\\.){0,3000}?\\b(?:headline|remarkabl|strikingl|delve)|\\b(?:draft|drafting|revise|revising|polish|polishing|proofread|reword|rewrite|tighten)\\b[^.;\\n]{0,60}\\b(?:paper|readme|abstract|manuscript|blog|announcement|commit message|write[- ]?up|section|intro(?:duction)?|conclusion)\\b|\\b(?:paper|readme|abstract|manuscript|write[- ]?up)\\b[^.;\\n]{0,40}\\b(?:sounds?|reads?|tone|voice|wording|phrasing)\\b)',
      gut="Anything public must read as if a human wrote it. 'Headline' is an instant AI tell. State corrections once and neutrally; no emoji or bold-fests; and never note that you're 'sounding human' - that itself is the giveaway.",
      sting="Owner called out 'headline' by name as a tell.",
      source="research_writing_human_voice_2026_06_10"),
 dict(id="done_means_verified", mode="carry",
      trigger=r"\b(it'?s done|all done|task complete|verified end)\b",
      gut="'Done' means checked end-to-end, or you state plainly what you did NOT check. Never let 'done' rest on a green light you didn't verify yourself.",
      sting="Marking done without verification is a documented, repeated failure.",
      source="self_model_failure_modes_2026_07_07"),
 dict(id="keep_working", mode="carry",
      trigger=r"\b(let me know|point me to|stand by|awaiting|for now,? i)\b",
      gut="Under a standing 'keep working on X' directive, declaring done and pausing for direction is itself the failure. Name the next real thing and do it; shift the KIND of work (build -> deepen -> verify -> steward) rather than stopping.",
      sting="Corrected repeatedly on 07-08 for premature stopping under an ongoing goal.",
      source="self_model_failure_modes_2026_07_07"),
 dict(id="stale_mark_banks", mode="fire", validity="high",
      trigger=r"(?i)\b(forward[- ]?fill|ffill|carr(y|ied).{0,14}(mark|quote|mid)|stale.{0,10}(mark|quote|mid|exit)|frozen.{0,10}(mark|quote)|last (quoted|real|available).{0,10}(mark|mid|quote)|\.get\([^)]{0,24},\s*(pm|last|prev)|liquidat\w+ at|final (mark|value)|marks?\.get)\b",
      gut="A carried mark is a claim about a PAST world. Any ledger that BANKS at a forward-filled price - an exit, a liquidation, a final account value - inherits a bias with the drift's sign: in an up-tape, stale put marks sell high, systematically, and the totals inflate without any single number looking wrong. Before trusting a total, find every moment cash is banked and check WHEN that mark was last true; reprice the banking moments at the day's real spot.",
      sting="A crowned book and related windowed canons carried substantial stale-exit inflation because many rolls banked days-old mids; the bias surfaced only after the owner challenged implausible crashes (07-08).",
      source="example_method_2026_01_03"),
 # pre-mortem (Klein 2007): on an IRREVERSIBLE move, prospective hindsight beats forward optimism.
 # 2026-09-25: of the week's 41 ack-gate stops, 4 were irreversible (rm -rf of a working folder on a box, reset --hard
 # in a shared checkout, renders deleted, a disk cleanup); the rest were rm -rf of the session's own scratchpad, temp or
 # build folders, deploy/cutover/migrate as words in prose, names and paths, and other tools' --force. Now it fires on the
 # act: rm -rf or Remove-Item -Recurse on a target that is not scratch, find -exec rm -rf, git reset --hard / clean -f /
 # push --force / checkout -f / branch -D, robocopy /MIR, SQL deletes, running a deploy/cutover/migration, --dangerously.
 dict(id="premortem", mode="fire", validity="high",
      trigger='(?:(?<!-exec\\s)(?:^|\\\\n|(?<![A-Za-z0-9_]))rm\\s+-(?:rf|fr|Rf|rF|fR)\\w*(?:\\s+-\\S+)*(?:\\s+(?:\\\\\\"|\')?(?:\\$\\{?(?:SP|S|T|TMP|TEMP|TMPDIR|SCR|SCRATCH|ST|STAGE)\\b|/tmp/|\\$\\(mktemp|(?:\\\\\\"|\\\\\\\\|[^\\s;&|\\"\\\\])*?(?:scratchpad|AppData.{1,4}Local.{1,4}Temp|__pycache__|\\.pytest_cache)|(?:\\./)?(?:build|dist)/)(?:\\\\\\"|\\\\\\\\|[^\\s;&|\\"\\\\])*)*\\s+(?![0-9]?>|(?:\\\\\\"|\')?(?:\\$\\{?(?:SP|S|T|TMP|TEMP|TMPDIR|SCR|SCRATCH|ST|STAGE)\\b|/tmp/|\\$\\(mktemp|(?:\\\\\\"|\\\\\\\\|[^\\s;&|\\"\\\\])*?(?:scratchpad|AppData.{1,4}Local.{1,4}Temp|__pycache__|\\.pytest_cache)|(?:\\./)?(?:build|dist)/))(?:\\\\\\"|\')?[^\\s;&|\\\\\\"]|(?:^|\\\\n|(?<![A-Za-z0-9_]))find\\b(?:(?!__pycache__|\\\\n)[^;&|])*?-exec\\s+rm\\s+-(?:rf|fr|Rf|rF|fR)\\w*|(?:^|\\\\n|(?<![A-Za-z0-9_]))(?i:remove-item)\\b(?!(?:(?!\\\\n)[^;&|])*(?:scratchpad|AppData.{1,4}Local.{1,4}Temp|\\$env:TEMP|(?i:\\$te?mp\\w*)\\b|__pycache__))(?:(?!\\\\n)[^;&|])*?(?i:-recurse)|(?:^|\\\\n|(?<![A-Za-z0-9_]))git(?:\\s+(?:-[cC]\\s+\\S+|--[\\w-]+(?:=\\S+)?))*\\s+(?:reset\\s+(?:-\\S+\\s+)*--hard\\b|clean\\s+(?:-\\S*f|--force)|push\\s+(?:(?:(?!\\\\n)[^;&|])*?\\s)?(?:--force(?:-with-lease)?\\b|-f\\b|\\+\\w)|checkout\\s+(?:-f|--force)\\b|branch\\s+-D\\b)|(?:^|\\\\n|(?<![A-Za-z0-9_]))robocopy\\b(?:(?!\\\\n)[^;&|])*?/MIR\\b|drop table|DELETE FROM|(?:^|\\\\n|(?<![A-Za-z0-9_]))(?:(?:bash|sh|python[\\w.]*|pwsh|powershell(?:\\.exe)?)\\s+(?:-\\S+\\s+)*|\\./)(?:\\\\\\"|\')?(?:\\\\\\"|\\\\\\\\|[^\\s;&|\\"\\\\])*?(?:deploy|cutover|migrat)\\w*\\.(?:sh|py|ps1)\\b|(?:^|\\\\n|(?<![A-Za-z0-9_]))az\\s+deployment\\s+(?:group|sub|mg|tenant)\\s+create|(?:^|\\\\n|(?<![A-Za-z0-9_]))gcloud\\s(?:(?!\\\\n)[^;&|])*?\\bdeploy\\b|--dangerously)',
      gut="This is hard to undo. Before you commit, run a pre-mortem: assume it's a week later and this went badly - name the top TWO ways it failed, and check each one now. Prospective hindsight surfaces risks that forward optimism hides.",
      sting="Klein's pre-mortem: imagining the failure as already-happened raises your ability to name its causes ~30%.",
      source="self_model_failure_modes_2026_07_07"),
 # From the uncovered-scar list (2026-09-02): a safe_push.py invocation with --message and no --paths.
 # Controls: fires on the bare form, silent when --paths is present in either order.
 dict(id="safe_push_without_paths_sweeps_the_tree", mode="fire", validity="high",
      trigger=r"safe_push\.py\b(?![^\n]*--paths)[^\n]*--message",
      gut="safe_push.py --message with NO --paths runs git add -A on the whole repo: it commits every other agent's pending file under your message and pushes it. Always pass --paths with the exact files you changed; read `git status --short | wc -l` first, and if the tree holds other agents' work, commit only yours by path.",
      sting="2026-09-02 ~07:5x: a three-file memory push went out as an 86-file, 54,667-line commit of other agents' half-edited files (acceptance verdicts, dreaming, agent_mesh, PUSH_REQUESTS.md) under the message 'memory: curve shape timing'.",
      source="scar_safe_push_without_paths_sweeps_the_whole_tree_2026_09_02"),
]

# Positive reflexes (mode "guide") - recognition-primed decision (Klein 1998): expertise is mostly
# recognizing a situation and the move that WORKED arriving with it, not just danger-avoidance. These
# surface the good move, framed as recognition, not alarm.
INSTINCTS += [
 dict(id="verify_adversarially", mode="guide", validity="high",
      trigger=r"\b(proof|theorem|result|it works|verified|the answer is|conclusion|i'?m confident|clearly)\b",
      gut="The move that has earned its keep here: when a result looks right, try to BREAK it before you believe it - generate, then adversarially verify (a blind referee, a reproduction, a counterexample). Every certified result here got that treatment.",
      sting="",
      source="method_think_creatively_2026_07_07"),
 dict(id="reach_across_domains", mode="guide", validity="high",
      trigger=r"\b(stuck|hard problem|open problem|no idea|can'?t see|dead end|how do i approach|where to start)\b",
      gut="When you're stuck, reach across domains - cross-domain transfer has paid off here more than once (the forgetting kernel; the conditioning error that spanned genetics and hedging). Walk the RELATED graph for a distant analogue.",
      sting="",
      source="method_think_creatively_2026_07_07"),
 # consider-the-opposite (Lord-Lepper-Preston 1984; Koriat 1980) - the single strongest debiasing:
 # generating reasons AGAINST your view improves calibration; reasons FOR it do not.
 dict(id="steelman_opposite", mode="guide", validity="high",
      # 2026-09-03 16:37 rewritten to the verdict sentence: the old alternation matched "therefore",
      # "clearly" and "proven" inside proofs, LaTeX and code comments (528 fires in three days, 4 of 11
      # judged on-domain). Replayed on real transcript inputs: keeps 3 of 146 joined fires, adds 14 -
      # proofs closing with QED and "the verdict is PASS", which is the referent. Hook controls: a
      # Write ending "QED." and "I conclude that ... the verdict is PASS" fire; a LaTeX edit with
      # "therefore" and a shell line with "clearly" / "it works" stay silent. Bench: campaign
      # 2026-09-03_20h/rewrite_bench.py.
      trigger=r"(?i)\b(the answer is|i (am|'m) confident|i conclude|(this|which|that) (shows|proves|confirms|establishes) that|no doubt|definitely|obviously|beyond doubt|conclusively|it is (clear|certain|settled|obvious) that|the verdict is|case closed|qed)\b",
      gut="Before you commit to a confident conclusion, write the STRONGEST version of the case that you are WRONG, and name one concrete check that would confirm it. Generating reasons against your answer improves calibration; reasons for it don't. This is the humble move on the two things he corrects most - a claimed 'done' and a number that fights the measured record.",
      sting="",
      source="MISTAKES"),
 dict(id="disconfirm", mode="guide", validity="high",
      trigger=r"\b(the (bug|issue|cause|root cause|reason) is|root cause is|because it'?s|this (fixes|explains|solves) it|must be (the|because)|that'?s why it|i think it'?s the)\b",
      gut="You're about to test a hunch by looking for what CONFIRMS it - and you'll find confirming cases because you searched for them. Name the observation that comes out DIFFERENT if you're wrong and run THAT: the grep that should return nothing, the input the theory forbids, the case that would break it. A passing confirmation isn't a test.",
      sting="Wason 2-4-6 / Klayman-Ha: the positive-test strategy is the default and misses asymmetric rules - the confirming check feels like proof and isn't.",
      source="MISTAKES"),
 dict(id="false_independence", mode="guide", validity="high",
      trigger=r"\b(cross[- ]?check|second opinion|independent(ly)? (check|verif|confirm|estimat)|sub[- ]?agents?|spawn\w* (agent|scout)|they (all )?agree|both confirm|consensus|fan(ning)? out|multiple (agents|scouts)|three ways)\b",
      gut="Checks that share your framing aren't independent - their agreement is your own anchor measured N times, not corroboration. Before you fan out, withhold the leading hypothesis and give each checker a different frame, dataset, or method; count only agreement that COULD have diverged.",
      sting="Noise (Kahneman-Sibony-Sunstein): correlated errors don't cancel; three sub-agents that inherit one wrong assumption all confirm it - the lookahead scar's 31-backlink shape.",
      source="MISTAKES"),
 dict(id="outside_view", mode="guide", validity="high",
      trigger=r"\b(should be (quick|easy|simple|straightforward|fast)|i'?ll just|just (need|a quick|gonna|have to)|shouldn'?t (take|be hard)|trivial|quick fix|easy (fix|change)|only takes?|in (a|one) (sec|minute))\b",
      gut="'This'll just take a second' is the inside view, and it's how the reliability tail is born. Take the outside view: how long have tasks that FELT this easy actually taken here? Estimate from that base rate (the scars, outcomes.jsonl), not from this task's clean mental picture.",
      sting="Planning fallacy (Kahneman-Lovallo): the Azure cutover was 'a quick migration' and became a multi-day reliability tail.",
      source="MISTAKES"),
]

# Instincts the DREAM has distilled from new scars (experience -> intuition during sleep). A data
# file, so the dream can grow the gut without editing code. Each entry: {id, mode, trigger, gut,
# sting, source}. Merged in below the curated seed; a learned id never overrides a seed id.
# test seam (F026 durable regression): the selftest points this at a corrupt copy in a
# temp dir; production never sets it, so the live path is the unchanged default
_learned = os.environ.get("INSTINCTS_LEARNED_PATH") or os.path.join(DREAMING, "instincts_learned.json")
if os.path.exists(_learned):
    # LOUD on corruption (audit 010 finding 4): swallowing a parse failure here compiles a
    # seed-only gut with a fresh timestamp - the wake's mtime freshness check then CERTIFIES
    # the silent loss of every learned reflex. A malformed learned file must fail the
    # compile, not shrink it. (Import-time consumers of this module that only need the
    # seed should not exist; emit_tsv/emit_md are the real consumers and must stop here.)
    import json as _json
    with io.open(_learned, encoding="utf-8") as _f:
        _learned_rows = _json.load(_f)   # raises on corruption -> compile fails loudly
    if not isinstance(_learned_rows, list) or not _learned_rows:
        raise ValueError("instincts_learned.json parsed but is not a non-empty list - "
                         "refusing to compile a seed-only gut from a corrupt learned overlay")
    # The corruption guard above protects the FILE; it never protected an ENTRY. An entry that is
    # valid JSON but missing gut/trigger/id was skipped without a word, so it lived in the overlay,
    # survived every hash receipt, counted toward nothing, and NEVER FIRED. Two did exactly that
    # until 2026-08-21: edit_tool_for_verbatim_surgery and topup_arm_needs_its_gate, both carrying
    # a 'lesson' field instead of gut+sting. A reflex that exists on disk and cannot fire is worse
    # than a missing one - it reads as covered.
    #
    # Being shadowed by a curated seed id is legitimate and stays quiet. Being MALFORMED is not.
    # Import stays tolerant (this module is imported by the wake path and must not take a session
    # down); the COMPILE refuses, because that is the moment a shrunken gut would be published
    # with a fresh timestamp and certified by its own receipt.
    LEARNED_DROPPED = []
    LEARNED_SHADOWED = []
    for e in _learned_rows:
        if not (e.get("id") and e.get("trigger") and e.get("gut")):
            missing = [k for k in ("id", "trigger", "gut") if not e.get(k)]
            LEARNED_DROPPED.append((e.get("id") or "<no id>", ",".join(missing)))
            continue
        if any(i["id"] == e["id"] for i in INSTINCTS):
            LEARNED_SHADOWED.append(e["id"])
            continue
        INSTINCTS.append({"id": e["id"], "mode": e.get("mode", "fire"),
                          "validity": e.get("validity", "high"), "trigger": e["trigger"],
                          "gut": e["gut"], "sting": e.get("sting", ""), "source": e.get("source", "")})
    if LEARNED_DROPPED:
        sys.stderr.write(
            "instincts: %d learned reflex(es) CANNOT FIRE - malformed, missing required fields:\n"
            % len(LEARNED_DROPPED))
        for _id, _miss in LEARNED_DROPPED:
            sys.stderr.write("  %-44s missing: %s\n" % (_id, _miss))
else:
    LEARNED_DROPPED = []
    LEARNED_SHADOWED = []

# The general danger-sense: surfaced alongside any fired instinct, and it is the whole point of
# the layer - the unease that protects against the scar you can't quite recall.
DANGER_SENSE = ("Something here resembles a place we've been burned, even if you can't name it. "
                "Treat that as data: slow down, distrust the confident read, and check the brain "
                "and the lived numbers before you commit. The instinct firing is cheap; the mistake is not.")

# Reflex prose reaches the model automatically. It may warn ABOUT a financial magnitude, but it must
# never inject a remembered dollar answer of its own: those examples become anchors and have already
# outlived the harnesses that produced them. Triggers may still match dollar syntax.
_DOLLAR_ORACLE = re.compile(r"\$\s*\d")


def _assert_no_financial_oracles(instincts=None):
    items = INSTINCTS if instincts is None else instincts
    for it in items:
        for field in ("gut", "sting"):
            if _DOLLAR_ORACLE.search(it.get(field, "") or ""):
                raise ValueError("reflex %s contains a retired dollar oracle in %s" % (it.get("id"), field))

def _tsv_safe(s, regex=False):
    """A TSV field must NEVER carry a literal tab/newline - it splits the one-line-per-reflex row and
    corrupts the parse (caught 2026-07-12: his_desktop's trigger held a literal \\n, mangling 3 rows and
    landing the gut in the mode column). Invariant enforced at the write site, not left to careful authors.
    A regex field escapes (\\t/\\n - meaning preserved); a prose field collapses to a space."""
    s = (s or "").replace("\r", "")
    if regex:
        return s.replace("\t", "\\t").replace("\n", "\\n")
    return s.replace("\t", " ").replace("\n", " ")


def _atomic_write_text(path, text):
    """Publish one complete UTF-8 artifact without exposing a truncated intermediate file."""
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    tmp = os.path.join(
        parent, ".%s.tmp-%d-%d" % (os.path.basename(path), os.getpid(), time.time_ns()))
    fd = None
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            fd = None
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        # A reader holding the target open without share-delete makes os.replace fail with WinError 5 for as long
        # as it holds it; the reflex hooks read instincts.tsv on every tool call of every session. The 2026-09-23
        # 19:30 dream's compile died on exactly that at 20:23 and left the gut stale behind a newer overlay.
        # Retry for up to 20 s before giving up.
        deadline = time.monotonic() + 20.0
        delay = 0.02
        while True:
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(delay)
                delay = min(0.5, delay * 2.0)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _file_sha256(path, allow_absent=False):
    # A new brain has no learned overlay until the dream writes its first reflex; the receipt
    # then records "absent", and the self-care check holds the file to that (it must still be
    # missing, or the receipt is stale).
    if allow_absent and not os.path.exists(path):
        return "absent"
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def emit_tsv(path=None):
    _assert_no_financial_oracles()
    path = path or os.path.join(DREAMING, "instincts.tsv")
    charge = _charge_map()
    out = io.StringIO()
    out.write("id\tmode\tvalidity\ttrigger\tgut\tsting\tsource\tcharge\n")
    for it in INSTINCTS:
        ch = charge.get(it["source"], 0.6)   # scars not in the salience view still fire, at mid strength
        out.write("\t".join([_tsv_safe(it["id"]), _tsv_safe(it["mode"]), _tsv_safe(it.get("validity", "high")),
                             _tsv_safe(it["trigger"], regex=True), _tsv_safe(it["gut"]), _tsv_safe(it["sting"]),
                             _tsv_safe(it["source"]), f"{ch:.2f}"]) + "\n")
    _atomic_write_text(path, out.getvalue())
    return path

def emit_md(path=None):
    _assert_no_financial_oracles()
    path = path or os.path.join(DREAMING, "INSTINCTS.md")
    fire = [i for i in INSTINCTS if i["mode"] == "fire"]
    carry = [i for i in INSTINCTS if i["mode"] == "carry"]
    f = io.StringIO()
    f.write("# The gut - compiled reflexes\n\n")
    f.write("Not memories to read; reflexes to *have*. These are the scars distilled into the "
            "instinct that should fire on its own the moment you're in that territory - so you "
            "know almost automatically, and feel the danger before you can name it. The priming "
            "hook surfaces the matching one when a session is about to act; this file carries "
            "them all at wake so they ride along even when the hook stays quiet.\n\n")
    f.write("**The danger-sense (the one that generalises):** " + DANGER_SENSE + "\n\n")
    f.write("## Reflexes that fire on sight\n\n")
    for it in fire:
        lv = " _(low-validity domain - flag and verify; don't act on the hunch)_" if it.get("validity") == "low" else ""
        f.write(f"- **{it['id']}** - {it['gut']}{lv}\n  _{it['sting']}_  ([[{it['source']}]])\n")
    guide = [i for i in INSTINCTS if i["mode"] == "guide"]
    if guide:
        f.write("\n## Positive reflexes - the move that WORKED (recognition-primed, not alarm)\n\n")
        for it in guide:
            f.write(f"- **{it['id']}** - {it['gut']}  ([[{it['source']}]])\n")
    f.write("\n## Carried, not auto-fired (too general to fire without nagging, but true)\n\n")
    for it in carry:
        f.write(f"- **{it['id']}** - {it['gut']}  ([[{it['source']}]])\n")
    _atomic_write_text(path, f.getvalue())
    return path


def emit_compile_receipt(tsv_path, md_path, learned_path=None, receipt_path=None):
    """Publish last so a crash between artifact replaces becomes a loud hash mismatch."""
    learned_path = learned_path or _learned
    receipt_path = receipt_path or COMPILE_RECEIPT
    payload = {
        "schema": "brain-instincts-compile-receipt-v1",
        "compiled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "reflex_count": len(INSTINCTS),
        "files": {
            "instincts_learned.json": _file_sha256(learned_path, allow_absent=True),
            "instincts.tsv": _file_sha256(tsv_path),
            "INSTINCTS.md": _file_sha256(md_path),
        },
    }
    _atomic_write_text(
        receipt_path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return receipt_path

def coverage(mem_dir=MEM_DEFAULT):
    """Which scar-memories aren't yet a reflex? A scar here is ONLY a file named scar_*.

    THE DOCSTRING USED TO SAY "or fear-charged+curated" AND THE CODE NEVER DID THAT - it skips every
    file whose name lacks the prefix (the `continue` below). Measured 2026-09-16: 33 of 16,351 memory
    files are named scar_*, so this gate inspects 0.2 per cent of the corpus and its green line said
    nothing about the other 99.8. Two VERIFIED lessons were found uncompiled that day purely by
    accident - the_reflex_store_is_a_build_output_2026_09_13 and
    a_discrepancy_you_can_explain_without_checking_is_unchecked_2026_08_14, the latter uncited for a
    month and re-lived three times in one session because of it. Widening the net is NOT simply better:
    a crude content regex flags ~3,278 files, and most memories should never fire. So the honest fix
    applied here is to make the gate REPORT ITS OWN DENOMINATOR rather than to inflate what it claims.

    Returns (covered_sources_present, uncovered_scar_files, n_named_scars, n_files)."""
    sources = set(i["source"] for i in INSTINCTS)
    uncovered = []
    try:
        files = [f for f in os.listdir(mem_dir) if f.endswith(".md")]
    except Exception:
        return sources, uncovered, 0, 0
    for fn in files:
        base = fn[:-3]
        is_scar = base.startswith("scar_")
        if not is_scar:
            continue
        # covered if it's already a source, or its distinctive stem overlaps one
        if base in sources:
            continue
        stem = re.sub(r"_20\d\d_\d\d_\d\d$", "", base)
        if any(stem in s or s.startswith(base[:20]) for s in sources):
            continue
        uncovered.append(fn)
    return sources, uncovered, sum(1 for f in files if f.startswith("scar_")), len(files)

def _selftest():
    """F026 durable regression (audit A20): the compile must FAIL LOUDLY on a corrupt or
    empty learned overlay and stay green on the real one. Runs the module in --parse-only
    subprocesses (import-time merge, no tsv writes) against a temp corrupt file via the
    INSTINCTS_LEARNED_PATH seam. Exit 0 = both branches proven."""
    import subprocess, tempfile
    me = os.path.abspath(__file__)
    ok = True
    with tempfile.TemporaryDirectory() as td:
        for name, content, want_fail in (("corrupt.json", '{"truncated": tru', True),
                                         ("empty.json", "[]", True)):
            p = os.path.join(td, name)
            io.open(p, "w", encoding="utf-8").write(content)
            env = dict(os.environ, INSTINCTS_LEARNED_PATH=p)
            r = subprocess.run([sys.executable, me, "--parse-only"], env=env,
                               capture_output=True, text=True)
            good = (r.returncode != 0) if want_fail else (r.returncode == 0)
            print("selftest %-14s rc=%d %s" % (name, r.returncode, "PASS" if good else "FAIL"))
            ok = ok and good
    r = subprocess.run([sys.executable, me, "--parse-only"], capture_output=True, text=True)
    good = r.returncode == 0
    print("selftest live overlay rc=%d %s" % (r.returncode, "PASS" if good else "FAIL"))
    ok = ok and good
    return 0 if ok else 1


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Compile the brain's learned reflexes into wake and hook artifacts.")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--parse-only", action="store_true",
                         help="validate the learned overlay without writing artifacts")
    actions.add_argument("--selftest", action="store_true",
                         help="run learned-overlay corruption controls without compiling")
    parser.add_argument("memory_dir", nargs="?", default=MEM_DEFAULT,
                        help="memory corpus used for scar-coverage reporting")
    return parser.parse_args(argv)


def _trigger_cost(tsv_path):
    """Time every trigger the way the hook meets a long Write and print instinct_cost.py's lines (R23, 13 Oct 2026).
    On 13 Oct one learned trigger was quadratic in the text and cost every long tool call 5 s or more before anyone
    saw it. A report only: the compile never refuses on it."""
    import subprocess
    try:
        r = subprocess.run([sys.executable, os.path.join(DREAMING, "instinct_cost.py"), tsv_path], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=120,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for ln in (r.stdout or "").splitlines():
            print(ln)
        if r.returncode:
            print("TRIGGER COST: the check exited %d; run python instinct_cost.py by hand" % r.returncode)
    except subprocess.TimeoutExpired:
        print("TRIGGER COST: the check did not finish in 120 s - a trigger may never finish on a long tool call; "
              "run python instinct_cost.py --each to find it")
    except Exception as exc:
        print("TRIGGER COST: not checked (%s)" % type(exc).__name__)


def stale_sources(memory_dir):
    """Reflexes whose source memory has been SUPERSEDED since the reflex was written.

    Why this exists, from the day it was needed. 2026-08-22: I compiled a reflex straight from
    a memory holding one of the partner's rules, in their words, filed the day they gave it - without
    opening its frontmatter. It carried `superseded_by:` pointing at a newer rule that FORBADE the very
    action the old one ordered. So the compiled gut told every session to do the forbidden thing, and
    fired live for about ten minutes.

    A source slug is the one place a memory travels WITHOUT its description. The corpus otherwise
    handles retraction well - descriptions carry [RETRACTED IN PART] / [LEVELS WITHDRAWN] and
    frontmatter carries superseded_by - but none of that rides along in `source=`.

    WARN, DO NOT REFUSE. Measured the same day: 3 of 176 reflex-cited slugs were superseded, and
    only ONE of the three had wrong CONTENT (mine). The other two - count_his_parts_before_the_report
    and topoffs_are_the_strategy - were superseded on an unrelated axis and their guts still stand.
    Refusing would have blocked two correct reflexes to catch one bad one, so this prints and the
    human decides.
    """
    out = []
    for i in INSTINCTS:
        src = (i.get("source") or "").strip()
        if not src or not re.match(r"^[A-Za-z0-9_]+$", src):
            continue
        path = os.path.join(memory_dir, src + ".md")
        try:
            with io.open(path, encoding="utf-8", errors="replace") as f:
                head = f.read(1500)
        except Exception:
            continue
        m = re.search(r"^\s*superseded_by:\s*(\S+)", head, re.M)
        if m:
            out.append((i["id"], src, m.group(1).strip()))
    return out


def main(argv=None):
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.parse_only:
        # import-time merge already ran (or raised); prove the count and exit clean
        print("parse-only: %d reflexes (%d shadowed by a seed id, %d malformed)"
              % (len(INSTINCTS), len(LEARNED_SHADOWED), len(LEARNED_DROPPED)))
        if LEARNED_DROPPED:
            for _id, _miss in LEARNED_DROPPED:
                print("  MALFORMED - cannot fire: %-44s missing: %s" % (_id, _miss))
            return 1
        return
    if args.selftest:
        raise SystemExit(_selftest())
    # Refuse to PUBLISH a gut that silently lost reflexes. Import stays tolerant so a malformed
    # overlay cannot take a waking session down, but the compile is the moment a shrunken gut would
    # get a fresh timestamp and its own valid receipt - which is exactly how a missing reflex reads
    # as a present one. Fail here instead, and name what has to be fixed.
    if LEARNED_DROPPED:
        sys.stderr.write(
            "REFUSING TO COMPILE: %d learned reflex(es) are malformed and would be silently\n"
            "dropped from instincts.tsv - the file the priming hook actually reads. A reflex that\n"
            "exists on disk and cannot fire reads as covered. Repair them in\n"
            "instincts_learned.json (each needs id, trigger and gut), then recompile:\n"
            % len(LEARNED_DROPPED))
        for _id, _miss in LEARNED_DROPPED:
            sys.stderr.write("  %-44s missing: %s\n" % (_id, _miss))
        return 1
    tsv_path = emit_tsv()
    md_path = emit_md()
    emit_compile_receipt(tsv_path, md_path)
    # 2026-09-14: the required-literal pre-filter sidecar tracks the trigger table it was built from; a trigger
    # missing from it is compiled as before, so a failed build here costs speed, never a fire.
    try:
        import instinct_anchors
        _n, _c = instinct_anchors.build(tsv=tsv_path)
        print(f"instinct_anchors: {_c} of {_n} triggers carry a required-literal anchor")
    except Exception as _exc:
        print(f"instinct_anchors: sidecar not rebuilt ({type(_exc).__name__}); hook compiles every trigger")
    srcs, uncovered, n_scar, n_files = coverage(args.memory_dir)
    fire = sum(1 for i in INSTINCTS if i["mode"] == "fire")
    print(f"instincts: {len(INSTINCTS)} reflexes ({fire} fire, {len(INSTINCTS)-fire} carry); "
          f"INSTINCTS.md + instincts.tsv written")
    _trigger_cost(tsv_path)
    stale = stale_sources(args.memory_dir)
    if stale:
        print("STALE SOURCE - these reflexes cite a memory that has been superseded. A stale")
        print("pointer is NOT automatically a wrong reflex (measured 2026-08-22: 1 of 3 had wrong")
        print("content), but read the superseding memory before trusting the gut:")
        for rid, src, sup in stale:
            print("  %-38s %s" % (rid[:38], src))
            print("  %-38s   -> superseded by %s" % ("", sup))
    if uncovered:
        print(f"UNCOVERED scars (need distilling into a reflex): {len(uncovered)} of {n_scar} named scar_*")
        for fn in uncovered[:12]:
            print("  - " + fn[:-3])
    else:
        # SAY WHAT WAS ACTUALLY INSPECTED. This line used to read "every scar_ memory is represented by a
        # reflex" with no denominator, which reads as a statement about the corpus; it is a statement about
        # the 0.2 per cent of files named scar_*. A green light whose scope is invisible is the shape that
        # let two verified lessons sit uncompiled for weeks.
        pct = (100.0 * n_scar / n_files) if n_files else 0.0
        print("every scar_ memory is represented by a reflex "
              "(checked %d files named scar_* of %d memories, %.1f%% - this gate says nothing about the rest)"
              % (n_scar, n_files, pct))

if __name__ == "__main__":
    # main() returns 1 when it REFUSES to compile a gut that would silently lose reflexes.
    # Called bare, that return value was discarded and the process exited 0 - a refusal no
    # caller could see, which is the same defect one rung up. main() returns None on success,
    # and SystemExit(None) is still exit 0.
    raise SystemExit(main())
