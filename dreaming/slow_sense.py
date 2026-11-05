"""slow_sense.py - Claude Code / Codex hook: when the laptop's slowness probe has tripped, tell the agent WHAT TO CHECK
and WHAT TO DO, once per session per half hour. Silent when the box is fine. Never blocks, never exits non-zero.

Reads ~/.claude/compute/slow_state.json, written every 10 min by slow_probe.py (task SlowProbe10m).
Owner order 2026-09-15 ("write hooks for agents for what to do when things are slow and what to check").
Numbers and the fixes come from memory the_slow_laptop_was_the_on_access_scanner_2026_09_15 and the 09-14 slow-machine memories.
"""
import json, os, sys, time

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


STATE = os.environ.get("SLOW_SENSE_STATE") or os.path.expanduser(r"~/.claude/compute/slow_state.json")   # env override = the test seam
MARKS = os.environ.get("SLOW_SENSE_MARKS") or os.path.expanduser(r"~/.claude/compute/slow_sense_marks")
STALE_S = 35 * 60          # the probe runs every 10 min; older than this means the probe itself is down
REPEAT_S = 30 * 60         # once per session per half hour (the brain fires less on purpose, owner 2026-09-02)
# FULL ONCE, THEN THE FIRST SENTENCE (2026-09-23). The advisories run to 1-6.6 KB each and most of their length is history
# kept so the next reader does not re-make an old misdiagnosis - worth reading once, not every half hour. A long session
# was getting the same 5-14 KB twice an hour (measured in one ab30 study session: the reconciliation advisory alone is
# 6,613 chars and fired for hours). So a verdict's full text goes out the first time it fires in a session and again
# after FULL_EVERY_S (a compacted context loses it); the repeats in between carry its first sentence - which holds the
# live numbers and the instruction - and say when the full text was given.
FULL_EVERY_S = 6 * 3600
FIRST_SENTENCE_MAX = 320


def _first_sentence(text):
    """The first sentence of a formatted advisory, at most FIRST_SENTENCE_MAX chars, cut at a word."""
    t = " ".join(str(text).split())
    for sep in (". ", "; so ", " - "):
        i = t.find(sep)
        if 0 < i < FIRST_SENTENCE_MAX:
            return t[:i + 1].rstrip()
    if len(t) <= FIRST_SENTENCE_MAX:
        return t
    return t[:FIRST_SENTENCE_MAX].rsplit(" ", 1)[0] + " ..."

class _Forgiving(dict):
    """A mapping for str.format_map that names what is missing instead of throwing or printing a raw brace."""

    def __missing__(self, key):
        return "(not measured)"


def _ps_distribution(recent=12):
    """Shape of the PowerShell start samples the probe has already recorded, long-run AND right now.

    The probe takes ONE sample per run and the advice used to quote it as the machine's property.
    Two days of samples say a start here is bimodal - fast four times in five, with a tail reaching
    two minutes - so a single sample is as likely to report 82 s as 0.7 s and the reader cannot
    tell which they got. But the long-run median alone would mislead in the other direction: the
    stalls arrive in WINDOWS, and during one the box genuinely is slow for an hour at a stretch.
    So report both, and let the reader see which they are in.

    Strings, not floats: they go into a format_map whose templates carry no format specs, and
    'unmeasured' prints better than a missing-key marker.
    """
    out = {"ps_med": "unmeasured", "ps_p90": "unmeasured", "ps_over4": "unmeasured", "ps_n": "0",
           "ps_recent_med": "unmeasured", "ps_recent_slow": "?", "ps_recent_n": str(recent),
           "cmd_fast_ms": "unmeasured", "cmd_stalled_ms": "unmeasured"}
    try:
        vals, fast_cmd, stalled_cmd = [], [], []
        with open(os.path.join(os.path.dirname(STATE), "slow_history.jsonl"), encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                w = d.get("ps_start_wall_s")
                if not isinstance(w, (int, float)):
                    continue
                vals.append(w)
                c = d.get("cmd_start_ms")
                if isinstance(c, (int, float)):
                    if w > 30:
                        stalled_cmd.append(c)
                    elif w < 2:
                        fast_cmd.append(c)
        if not vals:
            return out
        tail = vals[-recent:]
        s = sorted(vals)
        out["ps_n"] = str(len(s))
        out["ps_med"] = "%.2f" % s[len(s) // 2]
        out["ps_p90"] = "%.1f" % s[min(len(s) - 1, int(0.9 * (len(s) - 1)))]
        out["ps_over4"] = "%.0f" % (100.0 * sum(1 for v in s if v > 4) / len(s))
        st = sorted(tail)
        out["ps_recent_med"] = "%.1f" % st[len(st) // 2]
        out["ps_recent_slow"] = str(sum(1 for v in tail if v > 30))
        out["ps_recent_n"] = str(len(tail))
        if fast_cmd:
            out["cmd_fast_ms"] = "%.0f" % sorted(fast_cmd)[len(fast_cmd) // 2]
        if stalled_cmd:
            out["cmd_stalled_ms"] = "%.0f" % sorted(stalled_cmd)[len(stalled_cmd) // 2]
    except Exception:
        pass
    return out

def _last_probe_s():
    """How long the probe's most recent COMPLETED run took, and its long-run median.

    The probe writes its own duration as probe_s on every history row. When the published state is
    old, this is what distinguishes "the scheduled task is dead" from "a run is still going" - and
    the second is far more common here, because probe duration tracks machine load with a 60x range
    (median 12 s, and 715 s tonight under the dream) while CPU per cent barely moves.

    No psutil: this module runs under -s and cannot import it.
    """
    try:
        vals = []
        with open(os.path.join(os.path.dirname(STATE), "slow_history.jsonl"), encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                v = d.get("probe_s")
                if isinstance(v, (int, float)):
                    vals.append(v)
        if not vals:
            return None, None
        return vals[-1], sorted(vals)[len(vals) // 2]
    except Exception:
        return None, None

ADVICE = {
    "renders_not_pooled": (
        "{renders_outside_batch} of {renders_total} film renders are sitting OUTSIDE the batch cores. A render runs as python -m manim, so it is classified by its command line, not its executable name, and when that stops working the renders land on whatever mask their build shell had - usually the owner's own cores. It is silent: they still render, just in his way and on a quarter of the processor. CHECK: core_steer is alive (one process, not two) and core_steer_state.json is fresh; read the `pool` label it recorded for a render. DO: restart it via its STOP file and the VBS launcher, never two at once."),
    "open_latency_disk_contention": (
        "cold OPEN is slow (scanned {open_ms_scanned} ms, excluded {open_ms_excluded} ms) while MsMpEng sits at {msmpeng_pct} pct - "
        "so this is NOT the scanner and re-running bin\\defender_speedup.vbs would be a no-op. An excluded path is never scanned, so "
        "when both trees rise together the cause is under both of them: the disk. THIS VERDICT HAS two shapes and they need opposite "
        "investigations - read disk_read_mb_s, which is {disk_read_mb_s} right now, before you start looking. ABOVE ~50 MB/s it is a "
        "BULK READ: something is genuinely moving gigabytes, rank processes by BYTES over a 5 s interval (never cumulative), and it is "
        "usually a build, a dream pass or a reconciliation - it clears when that ends. BELOW ~10 MB/s nothing is reading in bulk and "
        "ranking by bytes will clear every process on this box, which has already happened twice: it is a METADATA STORM, so rank by "
        "OPERATIONS instead - AND THE PROBE NOW PUBLISHES THAT RANKING, so do not go and take your own pass over the process "
        "table for it: read top_ops in slow_state.json. Each row carries ops_s (read+write+other per second, the formula this "
        "line used to ask you to compute), rw_s (read+write ONLY) and what (the .py or command line, because 'python.exe' is "
        "not an answer and these processes exit before you can ask). BLIND SPOT, AND IT IS THE IMPORTANT ONE: top_ops "
        "CANNOT SEE PROTECTED SERVICE PROCESSES. It samples through OpenProcess, which is DENIED on a service svchost, so "
        "the single largest operations consumer on this box is absent from its own ranking. Measured 19:36 on 09-18: the "
        "entire visible top-8 totalled 247 read+write ops a second while invsvc_ops_s read 1,501.8 - InventorySvc alone, "
        "six times everything we can see, with cold opens at 168 ms. READ invsvc_ops_s BESIDE top_ops, always: an empty "
        "or quiet top_ops is NOT evidence that nothing is storming the disk, and concluding 'no offender' from it is the "
        "mistake this line exists to stop. Same mechanism as the governor proc_count floor - a protected process refuses "
        "the handle and silently leaves the sample. A RATE NEEDS A PHASE: this list is a 2 s window, so a row "
        "that lands on a FRESH process reports its startup burst as if it were steady state - a yt_dlp worker read 124,061 "
        "ops in its first 3.6 s and 1,110 in its whole life by 122 s. When an interval and lifetime-over-age disagree by "
        "orders of magnitude the process has phases; say which one you measured. READ rw_s BEFORE BLAMING THE DISK: OtherOperationCount "
        "counts sockets too, and the first live ranking put yt_dlp on top at 20,905 ops/s with 380 rw_s - a NETWORK storm that "
        "would have been reported as a metadata storm. The ranking is free because it reuses handles the CPU sample already "
        "opens; your own pass is not, on the box whose measured failure is that the process table stalls. A tree walk, a hardlink pass or a Windows inventory "
        "service can issue 26,000 ops/s while moving 40 KB/s and is invisible to every byte-ranked census. THE STANDING ONE HERE IS "
        "InventorySvc, the Inventory and Compatibility Appraisal service: measured 09-17 it had run 83.8 h and issued "
        "830,967,121 operations reading 290.8 GB and WRITING NOTHING - 9.9 million ops an hour at 0.96 MB/s, which is the "
        "signature exactly. IT DOES NOT APPEAR UNDER ITS OWN NAME: it runs inside svchost.exe -k InvSvcGroup, so a search of "
        "process NAMES finds nothing and reads as absent - that false negative cost me a wrong report on 09-17. Find it with "
        "psutil.win_service_iter() and read the io of the pid the service object names. Stopping it is one elevated, "
        "reversible command and it is decision 7 in his disk memo; it is HIS call, not a cleanup to do unasked. Either way: shed your own "
        "file-heavy work first, and count a bounded survey of your own as one of the offenders."),
    "open_latency_scanned": (
        "cold file OPEN on scanned files is {open_ms_scanned} ms (good: under 1 ms; 77 ms on 09-15). With the disk idle that is the "
        "on-access scanner saturated by our own file-open storm. THE EXCLUSIONS ARE NOT THE CAUSE HERE and re-running "
        "bin\\defender_speedup.vbs would be a no-op: this verdict is measured on WinSxS, a tree that is DELIBERATELY scanned and "
        "must never be excluded, so a missing exclusion cannot make it slow. Lost exclusions surface as open_latency_excluded, "
        "on the excluded tree. CHECK: MsMpEng CPU share, and WHICH of our processes is opening files in bulk. DO: fewer "
        "concurrent file-heavy jobs. Do NOT diagnose from aggregate CPU or LoadPercentage."),
    # UNREACHABLE SINCE SOMETIME BETWEEN 03:16 AND 05:12 ON 2026-09-16 - the two timestamps actually
    # measured (its last fire, and the oldest surviving slow_probe.py copy with no emitter). Not '~04:00':
    # that would be a guess sitting between two facts. It was NOT always dead - which is why it is kept
    # rather than
    # deleted. It fired 15 times between 09-15 15:20 and 09-16 03:16, including through the bad 18:00-20:00
    # window, and was then deliberately superseded: slow_probe now returns open_latency_disk_contention when
    # the EXCLUDED tree is slow, because an excluded path is not scanned at all, so this verdict's old advice
    # sent the reader to re-run the exclusion script - a no-op that would have been recorded as a fix. Every
    # copy of slow_probe.py from the 05:12 backup onward has zero references to this key. The Get-MpPreference
    # lesson that used to live here has moved into scanner_hot, which does fire; a lesson written into an
    # unreachable branch is a lesson deleted, and no selftest would have said so.
    "open_latency_excluded": (
        "cold OPEN inside the EXCLUDED agent tree is {open_ms_excluded} ms - the Defender exclusions may have been dropped (a "
        "platform update can do it) or the disk is genuinely stalled. CHECK the list from the EVENT LOG, which needs no admin: "
        "Get-WinEvent -FilterHashtable @{{LogName='Microsoft-Windows-Windows Defender/Operational'; Id=5007}}, matching on "
        "Exclusions\\Paths. DO NOT judge this from Get-MpPreference: unelevated it does not fail, it returns the one "
        "string 'N/A: Must be an administrator to view exclusions' in place of EACH array - which reads as '1 exclusion "
        "configured' and as every path being absent. Also `Avg. Disk sec/Read`. DO: re-run bin\\defender_speedup.vbs elevated only if the "
        "log shows the paths really are gone."),
    "reconciliation_not_completing": (
        "the hourly project-memory reconciliation has not refreshed its contract cache in {contract_cache_age_h} h. "
        "READ THIS FIRST, 2026-09-18 18:4x: IT IS NO LONGER A READER AT ALL, and the paragraph below is kept only as "
        "history. Every hydrate progress line of the live 18:00 pass says 0 MB READ, full reads claude 0 / replay-only 0 / "
        "codex 0 - the 09-17 byte-offset fix removed the I/O completely. IT IS ALSO NOT CPU-STARVED, and I said it was "
        "in this advisory an hour before measuring it - corrected here. The live pass at 46 minutes old: nice 16384, "
        "LIFETIME CPU 0.02 PCT OF ITS AGE (0.10 pct of one core over a 15 s window), 214 read operations and 2.5 MB in "
        "total. A BelowNormal process that WANTS cpu still accumulates it - the library pusher measured the same day used "
        "36 cpu-seconds in 113 minutes, twenty-five times more. This one is BLOCKED, not starved: it advances its line "
        "index while using neither cpu nor disk, which is the signature of waiting on a lock or a serialized resource. "
        "WHAT IT WAITS ON IS NOT YET ESTABLISHED - do not guess it from this line. FIVE CAUSES ARE ELIMINATED, each "
        "measured 2026-09-18 19:3x, so do not spend a tick re-running them: (1) bulk I/O - every hydrate line says "
        "0 MB read and the live pass moved 2.5 MB in 46 min; (2) a slow disk - the probe read cold opens at 0.48 ms "
        "scanned and 0.21 ms excluded while the pass crawled, so its ~110 ms per operation is NOT service time; "
        "(3) lock contention - the two live processes are run_bounded and its own child, not rival passes, and the "
        "contract-failure counter holds exactly ONE database-is-locked row all day; (4) a blocked stdout pipe - "
        "run_bounded gives the child stdout=(out_fh or DEVNULL), a file handle, so there is no pipe to fill; "
        "(5) a deliberate pacing sleep - there is no time.sleep anywhere in the hydrate path. What IS measured, over "
        "eight 15 s windows of one pass: cpu 0.00/0.21/0.42 pct of a core, a steady ~9 read+write ops a second, and "
        "WAL commits in bursts (one 173 KB commit in two minutes). Establishing the rest needs a stack sample from "
        "inside the process, which is the subsystem owner's tool, not an external observer's. The presence gate is still real and "
        "still logged on every pass ('owner present - the NORMAL raise is honoured only while he is away'), but raising "
        "the class cannot speed up a process that is not asking for cpu. So do NOT go looking for a disk offender here, and "
        "do not quote the gigabyte figures below as current - they were true before the fix and are now the single most "
        "misleading line in this advisory. The remaining decision (raise the class, or keep the presence gate) belongs to "
        "the subsystem, but the DISK objection to raising it is gone: a job reading 0 MB cannot compete for the disk. "
        "HISTORY, no longer current: it was measured as the box's largest reader TWICE on 09-17: at 09:3x it read "
        "15.58 GB in 36.8 min (16.5 MB/s, about half the box) and at 12:3x it read 44.65 MB/s with 10,901 ops/s - "
        "63 pct of the machine's 71.1 MB/s, twice the next process. Both are five-second interval measurements. "
        "THE METHOD POINT THAT STILL STANDS, because it is what produced the wrong verdict this line used to carry: "
        "the 61 MB/s once quoted here was prose and never a reading, and the 0.026 MB/s that replaced it came from "
        "LIFETIME io divided by process age, which is a different quantity from a rate - it smears a burst across "
        "every idle minute before it. Measure over an INTERVAL, per process, and rank by bytes only when "
        "disk_read_mb_s is above ~50; below ~10 rank by operations instead. (History: the top reader on 09-16 was "
        "another session's bulk job at 27 MB/s.) The COMPLETION problem below is real and unchanged. Measured "
        "2026-09-16 it had completed ZERO of 57 attempts in 56 hours: 42 killed at its 2700 s cap, 14 crashed on "
        "RuntimeError: historical_authoritative_event_requires_reclassification (store.py:393). This is why every "
        "session is told the signed contract cache is stale. NOTE THE CACHE IS WRITTEN MID-PASS, NOT ON "
        "COMPLETION - observed 2026-09-16 11:13:59, refreshed by the 11:00 pass while it was still running and "
        "while completed_at still read 05:03:51 - so this verdict means no pass has even REACHED that stage in "
        "hours, which is a weaker claim than no pass finishing. It is NOT idle "
        "waste: prompt ingestion advances every pass, only the reconciliation never lands, so do NOT simply disable "
        "the task. CHECK the FULL child output in scheduler/project_memory/hourly_bounded.log.child - the supervisor "
        "log truncates to 1,200 chars and the .child file is written for exactly this. ROOT CAUSE, named "
        "09-16 21:1x with EXPLAIN QUERY PLAN: sources.py:551, run by scan_sources EVERY pass, scans every "
        "clause_assignments row (961,840 rows - not 8.3M, that is the clauses count) and does one random 4 KB seek into clauses per row, into two "
        "temp b-trees - O(store) per hour whether or not a prompt is new; measured 1,424 MB read in 30 s at "
        "4.0 KB per op. That is why passes cap even with the box at 30 pct load; on a saturated box the "
        "BelowNormal worker also gets ~0.4 pct of a core, which stacks on top. The shipped remedy "
        "(reclassify-prepare/apply) CANNOT run beside it: every project_memory_cli subcommand takes the same "
        "single-runner lock the pass holds :00-:45, and prepare reads every transcript (4 GB in 7 min, still "
        "scanning at 13), so it needs the hourly task paused for a cycle - the owner's call. The FIX is the "
        "query (event_id on clause_assignments, or an incremental event->project table), the subsystem "
        "owner's design; do not raise priority and expect completion. Reports: "
        "audits/reports/2026/09/16/hourly_reconciliation_never_completes (with the evening addendum). "
        "UPDATE 2026-09-17 02:2x: three of the pass's grinds are FIXED in the runtime - the sources.py:551 derivation "
        "is cached in a sidecar (0.3 s a pass, live from 01:00), the publisher's per-span check is memoised per event "
        "(it had probed 142,840 spans a window, twenty windows an hour), and hydration seeks source lines by a "
        "remembered byte offset instead of reading 13 GB of transcripts whole (live from the 03:00 pass, index built "
        "as it goes). So a pass capped after 09-17 03:00 is NEWS, not the old story: read its exit in "
        "hourly_bounded.log and the 'project-memory hydrate' lines in the .child file before repeating any of the above. "
        "What still ends a pass early when it fires is the reclassification refusal at store.py:393; that, its "
        "prepare/apply remedy, and why an unchanged window is re-published at all remain the subsystem's (tasks 931, 933). "
        "Memories: the_hourly_hydrate_query_read_nine_gigabytes_an_hour_2026_09_17, "
        "the_span_check_probed_every_span_of_every_window_every_hour_2026_09_17."),
    "scanner_hot": (
        "MsMpEng is {msmpeng_pct} pct of the whole box (good: under 3). Something outside the exclusions is opening files in bulk - a "
        "download tree, Downloads, a new project folder. BEFORE YOU CONCLUDE THE EXCLUSIONS WERE DROPPED, read them from the "
        "EVENT LOG, which needs no admin: Get-WinEvent -FilterHashtable @{{LogName='Microsoft-Windows-Windows Defender/Operational'; "
        "Id=5007}} matching on Exclusions. DO NOT judge it from Get-MpPreference: unelevated it does not fail, it returns the single "
        "string 'N/A: Must be an administrator to view exclusions' in place of EACH array, so the count reads 1 and every path reads "
        "absent - the exact shape of 'they were all wiped'. That cost an hour on 2026-09-16; the 49 exclusions were intact. "
        "CHECK: which of OUR processes is opening files in bulk (per-process I/O rate). DO: move "
        "the work under an excluded tree or add the tree (elevated)."),
    "powershell_no_native_image": (
        "a bare PowerShell start burns {ps_start_cpu_s} s of CPU (good: 0.3). The .NET native images are gone again (Windows servicing "
        "drops them; there is no NGEN service on this build). DO: run bin\\ngen_powershell.vbs elevated once (~20 min, hidden); until "
        "then prefer python -s over powershell for helpers."),
    "powershell_start_slow_queue": (
        "a bare PowerShell start takes {ps_start_wall_s} s wall at {ps_start_cpu_s} s CPU, and every usable core is busy - this is the RUN QUEUE of a box doing the work it was asked to do, not a fault in the machine. Nothing here needs fixing and the scanner is not involved. What it does mean: every hook and task launch on this box is paying that wait right now, so DO batch helper work into one process and prefer python -s over a fresh powershell, and do NOT read any other timing taken in this state as evidence about the disk or the scanner."),
    "powershell_start_slow": (
        "this sample's PowerShell start took {ps_start_wall_s} s wall at {ps_start_cpu_s} s CPU. Read it against the shape, not alone: over "
        "the probe's own {ps_n} samples a start here is median {ps_med} s, p90 {ps_p90} s, only {ps_over4} pct over 4 s - but over the last "
        "{ps_recent_n} samples the median is {ps_recent_med} s with {ps_recent_slow} of them over 30 s. Stalls come in WINDOWS: if that recent "
        "median is high you are inside one and it will cost you all hour; if it is low this was a one-off and the box is fine. "
        "IT IS NOT THE SHELL AND NOT AMSI: in stalled samples a plain cmd.exe start goes {cmd_fast_ms} ms -> {cmd_stalled_ms} ms, file opens "
        "slow ~7x on EXCLUDED paths as well as scanned ones, and MsMpEng reads {msmpeng_pct} pct - process CREATION is what stalls. So do not "
        "rewrite helpers to avoid powershell; the stall lands on whatever you start instead. The lever that works is fewer starts of ANY kind "
        "- AND WE ARE THE LARGEST SOURCE OF THEM. Attributed 2026-09-18 20:1x by walking the parent chain of every "
        "bash/python/conhost/powershell process under 30 minutes old: 87 of them, of which 38 trace to a claude.exe "
        "ancestor (44 pct) and 22 to a wscript launcher, across SIX live claude sessions. Not the renders - agent tool "
        "calls are the single biggest contributor to process creation on the box whose defining failure IS process "
        "creation. That is one 30-minute window and not a distribution, so treat the share as an order of magnitude, "
        "not a constant. Two earlier attributions of mine were wrong before the parent chain settled it: 'the films' "
        "(unmeasured) and then 'mostly me' (the shell-snapshot marker tags only the wrapper, not its children, so it "
        "attributed 3 of 102). Walk the PPID chain; do not read the command line."
        "- AND EVERY PIPE STAGE IS ONE. Measured 2026-09-18 on this box in a non-stalled moment, 5 samples each: bare echo 0.102 s, "
        "echo|tail 0.212 s, echo|grep|tail 0.342 s - about 0.11 s per stage, and each stage is a process creation, so inside a stall "
        "window it pays the same 20-35 s as anything else you start. Agents pipe reflexively to trim output the harness already "
        "truncates and persists. Drop the |tail and |head, ask the tool for the lines you want, and put multi-step helper work in ONE "
        "python -s process instead of a shell pipeline. "        "- batch helper work into one process - and then look for what is doing bulk file work: a metadata storm (a dedup/hardlink pass, a "
        "tree walk over 100k files) moves almost no bytes and still does this, so disk_read_mb_s will look innocent. Ours count too."),
    "cores_pinned": (
        "{cores_pinned} of {cores} cores are pinned over 90 pct while the average reads {core_avg} pct - aggregate CPU hides a saturated "
        "subset. CHECK per-process CPU as a RATE over 5 s (never cumulative), attribute ffmpeg/manim/python to their parent command "
        "line, and test the suspect for PRODUCTION (files written in the last 10 min) before touching it. Usually his films: that is a "
        "scheduling decision for him, not a cleanup. Shed YOUR load first."),
    "commit_high": (
        "commit charge is {commit_pct} pct of {commit_limit_gb} GB. Near the limit allocations stall while CPU reads idle. CHECK: "
        "idle ComfyUI holding paged-out commit (curl 127.0.0.1:8188/queue), dead orphans with big private bytes (a 3.5 GB dead push on "
        "09-15), Chrome. DO NOT stop them to free commit - the owner banned shedding load on 2026-09-18 (\"we just power "
        "through it\"), because a killed render starts over and costs him more than the commit is worth. Report the holders "
        "and carry on; disk under 40 GB also freezes pagefile growth. "
        "WHY THE LIMIT MOVES, corrected 2026-09-18: the pagefile is configured initial 32768 MB / maximum "
        "98304 MB, but it is NOT pinned at the initial size and the commit limit is NOT the fixed 95.4 GB this "
        "advice used to assert. Windows grows the file toward its 96 GB maximum under demand and shrinks it "
        "again: measured over one hour the limit read 95.4, 102.5, 106.8, 105.6 then 99.4 GiB, and by 13:52 on "
        "09-18 it was 108.3 GiB on a 44.9 GiB pagefile. The percentage above already divides by the LIVE limit, "
        "so never re-derive it against 95.4 - that understated the charge by about ten points this afternoon. "
        "AND THE FILE IS MOSTLY UNTOUCHED: at 44.9 GiB grown, only 1.44 GiB was in use, so commit is a promise "
        "and the pagefile is the spend - a high percentage is reservations, not paging. The growth itself is "
        "the stall this verdict names, and it costs disk: C: free fell 176.9 -> 157.9 GiB across the growth. "
        "Raising the INITIAL "
        "size removes the stall and costs that many GB of disk immediately, so it trades directly against free "
        "space and needs a restart to take effect - the owner's call, not a thing to do mid-session. Before "
        "blaming a process, check whether the holders are actually his work: on 09-16 the 84 GB was concurrent "
        "film renders, his terminal and Chrome, with no idle model server and no orphan over 0.5 GB."),
    "ram_low": ("physical RAM free is {ram_free_gb} GB. Name the biggest holders, do NOT stop them - the owner banned "
                "shedding load on 2026-09-18 (\"we just power through it\"). Avoid launching anything large yourself."),
    "disk_low": (
        "C: has {disk_free_gb} GB free. Under ~32 GB the pagefile cannot grow and commit stalls (09-14). CHECK Temp\\claude session "
        "scratch ({scratch_gb} GB now; TempJanitorDaily removes dirs untouched 3 days), superseded renders, abandoned push staging. "
        "Never delete simulation inputs or protected data."),
    "exited_pinned_high": (
        "{exited_pinned} exited process objects are pinned by open handles - Get-Process lists the dead, Stop-Process 'succeeds' on them, "
        "taskkill answers 'no running instance'. Re-count after any reap; only a reboot releases them."),
    "hung_producer": (
        "{hung_producers} orphaned producer(s) have not written ONE BYTE across two probe cycles - about twenty minutes. Worst: {hung_producer_note}. This is a LOOK, not a kill order. CHECK, in this order: the newest file in its own output directory (an 11-hour dvisvgm on 2026-09-16 had never written its SVG while its siblings finished at 06:19), its parent (gone = nothing is waiting on it), and its LIFETIME io (a daemon that has written megabytes and is merely between ticks is not hung - core_steer.py was this detector's first false positive, orphaned by design because a VBS launched it). DO: if it has produced nothing and nothing waits on it, kill it by explicit PID and its children, never by name. DO NOT assume: a producer legitimately blocked on a long remote call looks identical, which is why this reports rather than reaps, and why hook_reaper deliberately leaves python daemons alone."),
    "orphan_fragments": (
        "{orphan_fragments} orphaned pipeline fragments (sleep/grep/sed/head/tail with a dead parent). Reap by explicit PID, never by "
        "a name pattern (it matches your own shell)."),
    "scratch_big": "Temp\\claude holds {scratch_gb} GB of session scratch. Let TempJanitorDaily run or remove dirs older than 3 days.",
    "reservation_pending_restart": (
        "Cores {reserved_cores} are still held out by the RUNNING kernel; the registry value is already gone, so this "
        "waits on a restart and is not a fault to chase. core_steer bridges it by explicit affinity. AT THE RESTART it "
        "retires the BRIDGE but KEEPS RUNNING, pooling renders only across [2..9] and [14,15] - logical 14 and 15 were "
        "in no mask at all and sat at 0-1 pct while processes holding all sixteen cores ran elsewhere, and the restart "
        "does not change that. SEPARATELY, and do not confuse the two: 14 and 15 reading 0 pct TODAY is the load "
        "balancer, not a fault - measured 2026-09-16, the LP-E pool takes its first chain only at the NINTH "
        "concurrent render chain (it delivers 0.54 cores against 1.99 and 2.77, so a chain parked there runs ~3.4x "
        "slower and would set the makespan). Count LIVE RENDER CHAINS before calling it broken. Expect one "
        "core_steer process after the boot; bin/AFTER_THE_RESTART.md is the runbook. "
        "Do NOT re-apply anything, and do not read a slow open or process start here as a scanner problem."),
    "cores_reserved_from_scheduler": (
        "CORES {reserved_cores} ARE HELD OUT OF THE WINDOWS SCHEDULER. Nothing will run on them unless a process asks "
        "for them by explicit affinity, so if nothing does, that much of the machine is simply unavailable. This is the "
        "fault that cost him half his laptop for two days in September 2026: a reserved set applied on the 5th, live from "
        "the restart on the 13th, with the guard that was meant to use those cores dead since the 8th. He ordered the "
        "reservation reversed on the 15th. CHECK: HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Kernel\\"
        "ReservedCpuSets - if it exists again, somebody or something re-applied it. DO: unless he asked for it in the "
        "current session, remove it with bin\\remove_cpu_reservation.vbs (elevated, keeps a backup) and tell him. A "
        "reservation only takes effect at a restart, so a fresh one may be invisible until the next boot."),
    "cpu_starved": (
        "THE MACHINE IS STARVED, not slow in any other sense: {cores_pinned} of {cores} cores are at 100 pct and a bare "
        "PowerShell start waits {ps_start_wall_s} s while using only {ps_start_cpu_s} s of processor time. Waiting, not "
        "working. Every other timing on this box - file opens, hook latency, build steps - is inflated by the run queue "
        "right now, so do NOT diagnose the scanner or the disk from numbers taken in this state. CHECK: how many cores "
        "are actually usable (a reserved CPU set removes cores from the scheduler; read cpu_set_flags in slow_state.json), "
        "and per-process CPU as a RATE. This verdict now REQUIRES idle capacity beside the saturation - at least four usable cores under 10 pct - so it no longer fires on a box that is simply full of real work while he is away, which is what he asked for. It firing means capacity is being WASTED, not merely used. DO: find what is holding those cores out (cpu_set_flags, an affinity mask nobody meant to set) and give them back; reducing concurrency is the second choice, not the first. The 2026-09-15 case was an "
        "eight-core reservation that nothing was using, with ten cores idle while six ran flat out - and the 2026-09-16 reading that looked identical on every other signal was the opposite: every usable core at 100 pct, nothing idle, nothing wrong."),
    "hook_dispatch_broken": (
        "THE HOOK DISPATCHER IS WIRED BUT CANNOT WORK, so every hook of a wired event is silent and nothing else says so. "
        "Details in slow_state.json under 'dispatcher'. DO, immediately: "
        "python -X utf8 ~/.claude/dreaming/hook_dispatch_wire.py --off "
        "(restores the exact original hook blocks from hook_dispatch_list.json and removes the enable flag). Then fix, "
        "re-run hook_dispatch_control.py until it prints PASS three times, and only then --on again."),
    "comfy_listening": (
        "ComfyUI is listening on 8188. If its queue is empty it is an idle commit holder (14 GB on 09-14): "
        "`curl -s --max-time 15 http://127.0.0.1:8188/queue`, then stop it and its supervisor if idle (owner order 09-14)."),
}

GENERAL = ("Order of operations when the box feels slow: (1) this probe's numbers - cold OPEN latency and MsMpEng first; (2) disk free "
           "in GB and Temp\\claude; (3) per-core max, not the average - but a BUSY CPU IS NOT THE CAUSE HERE: cold-open latency correlates +0.73 with process start time and per-core CPU -0.31, and this box's fastest samples were at 75-82 pct rendering film. It stalls on the disk with idle cores. Scanned AND excluded open both high = the disk, and the offender is usually one of OURS doing bulk file work, not the renders [[slow_is_the_disk_not_the_cpu_2026_09_16]]; (4) per-process CPU RATE + parent + 'is it producing'; "
           "(5) BelowNormal costs a background job a factor that depends on what else is runnable - measured 2.36x under 25 renders, not the 40-135x this line once asserted; re-measure before quoting one. Do NOT raise pooled renders to Normal: BelowNormal is what makes them yield to his browser on the shared cores; (6) shed your own load before "
           "anyone else's; (7) write what you found to the brain. Re-run the probe on demand: "
           "python -X utf8 ~/.claude/dreaming/slow_probe.py --print")


def main():
    try:
        raw = sys.stdin.read()
        i = raw.find("{")
        hook = json.loads(raw[i:]) if i >= 0 else {}
    except Exception:
        hook = {}
    session = str(hook.get("session_id") or os.environ.get("CODEX_THREAD_ID") or "nosession")[:64]
    event = hook.get("hook_event_name", "")
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return
    now = time.time()
    verdict = [v for v in st.get("verdict", []) if v in ADVICE]
    if "powershell_start_slow" in verdict:
        st.update(_ps_distribution())
    stale = now - float(st.get("at_epoch", 0)) > STALE_S
    if not verdict and not stale:
        return
    os.makedirs(MARKS, exist_ok=True)
    mark = os.path.join(MARKS, "".join(c if c.isalnum() or c in "-_" else "_" for c in session))
    sig = ("stale" if stale else "") + ",".join(sorted(verdict))
    full_at = {}
    try:
        prev = json.load(open(mark, encoding="utf-8"))
        if prev.get("sig") == sig and now - prev.get("at", 0) < REPEAT_S:
            return
        full_at = {k: float(v) for k, v in (prev.get("full") or {}).items() if now - float(v) < FULL_EVERY_S}
    except Exception:
        pass
    lines = []
    if stale:
        _last, _med = _last_probe_s()
        if _last is not None and _last > 600:
            lines.append("[slow-sense] the numbers below are OLD (probe %s). The task is not down: its last completed run took "
                         "%.0f s against a 600 s schedule, so runs are queueing behind each other. Probe duration is this "
                         "box's most sensitive load signal - median %.0f s - so a run this long IS the finding: something "
                         "heavy is heavy right now, and every number below lags by up to that much. Do NOT restart the task."
                         % (st.get("at"), _last, _med or 12))
        else:
            lines.append("[slow-sense] the slowness probe is STALE (last run %s) - task SlowProbe10m may be down; run it by hand: "
                         "python -X utf8 ~/.claude/dreaming/slow_probe.py --print" % st.get("at"))
    if verdict:
        lines.append("[slow-sense] the laptop is measurably slow (probe %s): %s" % (st.get("at"), ", ".join(verdict)))
        any_full = False
        for v in verdict:
            try:
                # format_map with a forgiving mapping, NOT format(**state): a probe that fails partway writes a
                # partial state, and with format() one missing key throws and the whole line falls back to the raw
                # template - the reader then sees a literal {reserved_cores} in the middle of a sentence and stops
                # trusting the instrument. This way every number that IS present still renders.
                body = ADVICE[v].format_map(_Forgiving(st))
            except Exception:
                body = ADVICE[v]
            if v in full_at:
                lines.append("  - " + _first_sentence(body) + " (full advisory given this session at %s)"
                             % time.strftime("%H:%M", time.localtime(full_at[v])))
            else:
                lines.append("  - " + body)
                full_at[v] = now
                any_full = True
        if any_full:
            lines.append("  " + GENERAL)
    text = "\n".join(lines)
    try:
        json.dump({"sig": sig, "at": now, "full": full_at}, open(mark, "w", encoding="utf-8"))
    except Exception:
        pass
    out = {"hookSpecificOutput": {"hookEventName": event or "UserPromptSubmit", "additionalContext": text}}
    if event == "PreToolUse":
        out["hookSpecificOutput"]["permissionDecision"] = "allow"
    sys.stdout.write(json.dumps(out))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
