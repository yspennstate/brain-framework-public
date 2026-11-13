"""hook_dispatch.py - run every hook of one event in ONE python process instead of one process per hook.

WHY (measured 2026-09-15 on <hostname>): a Bash tool call fires 12 PreToolUse + 5 PostToolUse hooks, each a separate
python start. On a quiet box that set costs 4.7 s of CPU; under the day's real load the same set measured 70.8 s, with
single hooks at 4-6 s. Nearly all of it is interpreter startup and re-importing the same modules (json, psutil, the brain
libraries) seventeen times. One process imports them once.

USAGE (the wiring, once proven):
    python -s ~/.claude/dreaming/hook_dispatch.py --event PreToolUse
replaces every individual entry for that event in settings.json. The dispatcher reads the SAME settings.json, so the hook
list stays where it has always been - it just runs the entries itself.

FAITHFUL SEMANTICS (the whole point of the equivalence control beside it, hook_dispatch_control.py):
  - stdin is replayed to every hook, exactly as the harness would
  - stdout: each hook's stdout is parsed; JSON hookSpecificOutput objects are merged (additionalContext joined with a
    blank line, in hook order); plain text is appended as-is. A hook that prints nothing contributes nothing.
  - permissionDecision: deny wins over ask wins over allow; the first denying hook's reason is used
  - exit code 2 (block) from any hook -> the dispatcher exits 2 with that hook's stderr, which is how every gate on this
    box (the ack-gate, proof_deny) stops a tool call. Any other non-zero exit is non-blocking: logged, never propagated.
  - a hook that raises, or calls sys.exit(n), is caught: SystemExit(0/None) is success, SystemExit(2) blocks, anything
    else is a non-blocking error. A crashing hook can never take the others down.
  - isolation between hooks: os.environ, sys.argv, sys.path and the working directory are saved and restored around each
    one. sys.modules is deliberately NOT reset - the shared import cache is the saving.
  - a hook whose command is not a simple "python <file>" form (the error_graph -c payload) is run as a subprocess, as before.

SAFETY: the dispatcher only runs when ~/.claude/dreaming/HOOK_DISPATCH_ENABLED exists; without it, it exits 0
silently and the individually-wired hooks keep running. Log: ~/.claude/compute/hook_dispatch.log (errors and
timings only, one line per invocation).
"""
import time
_T_IMPORT = time.perf_counter()      # module start, before the imports below: the sampled profile's clock
# THE IMPORT LINE IS PAID ON EVERY TOOL CALL (14 Oct 2026-r, front R31). Each PreToolUse and
# PostToolUse starts this file in a new process, and this line used to import traceback, subprocess and shlex too.
# Under Python 3.14 traceback alone brings in _colorize, dataclasses, inspect, dis, ast, annotationlib, tokenize, copy
# and textwrap: 25 ms of every dispatch, measured in fresh processes over 15 alternated rounds (34 ms with subprocess
# and shlex; primary_directive/improve_1012/fronts/R31/runs/bench_1.txt), for four error paths that almost never run.
# Each is now imported where it is used: traceback in _format_exc, subprocess in run_subprocess, shlex in script_argv
# and _label_from_cmd. A hook that needs one imports it itself, as it must when it runs on its own.
import io, json, marshal, os, runpy, sys, time
_T_BODY = time.perf_counter()        # end of the import line: imp_ms in the sampled profile


def _format_exc(path=None):
    """traceback.format_exc() for the exception being handled, with traceback imported only now. Never raises: if the
    import fails the answer is the exception's type and value. `path` is the sys.path to import it under:
    run_in_process calls this while the hook's own folder is still first on sys.path, and a traceback.py in that
    folder must not be the module that formats the hook's error."""
    et, ev = sys.exc_info()[:2]
    try:
        tb = sys.modules.get("traceback")
        if tb is None:
            held = list(sys.path)
            if path is not None:
                sys.path[:] = list(path)
            try:
                import traceback as tb
            finally:
                sys.path[:] = held
        return tb.format_exc()
    except Exception:
        return "%s: %s\n" % (getattr(et, "__name__", "Exception"), ev)

# Once wired, settings.json holds only the dispatcher, so the real hook list lives here (written by hook_dispatch_wire.py,
# and the same file --off restores from). Before wiring, this file does not exist and settings.json is the list.
LIST = os.path.expanduser("~/.claude/dreaming/hook_dispatch_list.json")
SETTINGS = os.path.expanduser("~/.claude/settings.json")
ENABLED = os.path.expanduser("~/.claude/dreaming/HOOK_DISPATCH_ENABLED")
LOG = os.path.expanduser("~/.claude/compute/hook_dispatch.log")
SELF = os.path.basename(__file__).lower()
CREATE_NO_WINDOW = (0x08000000 if os.name == "nt" else 0)
PER_HOOK_BUDGET_S = 120.0

# THE BUDGET IS PER EVENT, BECAUSE THE EVENTS ARE NOT COMPARABLE (2026-09-18). One flat 120 s governed a
# path this dispatcher has taken 12,272 times. Read out of its OWN log, 22,009 lines:
#
#     event              calls   median     p90       max   total
#     PreToolUse        12,272    1.65s   3.46s   221.52s   26,757s
#     UserPromptSubmit     790   12.43s  32.91s   425.89s   14,416s
#     PostToolUse        8,502    0.65s   1.58s    71.17s    7,531s
#     Stop                 397    1.98s   3.87s    71.76s    1,072s
#                                                    TOTAL   13.8 HOURS of hook time
#
# So the hot path finishes in under 4 s at p90 and the 120 s ceiling only ever applies to a hook that has
# already failed - and when it applies to PreToolUse, a tool call appears to hang for nearly four minutes.
# The per-hook failure lines in that log are all the same number: project_memory_hook.py 17 of 17 at a
# median 120.07 s, project_memory_hot_hook.py 12 of 12 at 120.06, error_monitor.py 120.02. They are not
# slow, they are timing out as their normal behaviour, and the budget is what sets the price of that.
# SessionStart keeps the full 120 s ON PURPOSE: it fires once and it is where the brain is loaded, so
# cutting it wakes a session without its identity - a different and worse failure than a lost note.
#
# EVERY CAP WAS CHECKED AGAINST HOW MANY REAL RUNS IT WOULD CUT, and that check overturned my first
# choice. I had written 30 s for UserPromptSubmit on the assumption that its 32.6 s p90 was inflated by
# the timeouts themselves. It is not: with every run over 100 s removed, 779 of 792 remain and their p90
# is still **29.84 s**, p99 71.09, max 97.54. A 30 s cap would have cut 89 of 792 runs - more than one
# prompt in nine losing its hook output - because this event genuinely takes 12-30 s. The prompt path
# being that slow is a finding of its own; it is not a reason to throw its work away. 90 s sits above the
# clean maximum and only touches runs already past anything that has ever completed.
# What each cap actually cuts, measured on 22,009 log lines:
#     PreToolUse       15 s   60 of 12,281 runs (0.49%)   saves   932 s
#     PostToolUse      15 s    8 of  8,512 runs (0.09%)   saves    91 s
#     Stop             20 s    3 of    397 runs (0.76%)   saves    70 s
#     UserPromptSubmit 90 s   13 of    792 runs (1.64%)   the ones over the clean max of 97.5 s
EVENT_BUDGET_S = {
    "PreToolUse": 15.0,          # median 1.65s  p90 3.46s  p99 10.60s
    "PostToolUse": 15.0,         # median 0.65s  p90 1.58s  p99 3.81s
    "UserPromptSubmit": 90.0,    # median 12.46s p90 32.62s p99 116.11s - clean p99 is 71.09s
    "Stop": 20.0,                # median 1.98s  p90 3.87s  p99 13.57s
    "SessionStart": 120.0,       # once per session, loads the brain - unchanged
}


def budget_for(event):
    return EVENT_BUDGET_S.get(event, PER_HOOK_BUDGET_S)


# Per-hook rows are written above this, even on success, so the event totals become attributable.
# WHO WRITES THESE ROWS: every session on this machine shares this log, and on 2026-09-18 that was
# eleven of them - the owner's interactive work and a number of agent loops, which do not have the
# same hook mix or the same prompt shape. So SPLIT BY SESSION BEFORE QUOTING A RATE from these rows;
# a pooled median here is the median of a population nobody uses. The same defect once made a silence
# log read as "the math brain dies on the clock" when 243 of its 256 rows were the measurement
# harness itself. The rows are honest about WHICH HOOK is slow, which is what they are for; they are
# not evidence about how often a human waits.
SLOW_HOOK_S = 1.0
# Claude Code persists ONE hook command's context above this many chars to a file and shows the model a 2 KB
# preview (its persist threshold, read out of the bundle 2026-09-20; 16.1/36.1/65.6 KB merged outputs all spilled).
# The dispatcher is one command, so its MERGED output has to stay under it; the owner-prompts hook, whose block
# alone is that big, is wired as its own command in settings.json for its own ceiling.
HARNESS_INLINE_MAX = 10000

# Hooks that MUST keep their own process. Measured by hook_dispatch_control.py on 2026-09-15: 40 of 43 hook-runs are
# byte-identical in-process, these two are not. Both are the brain-framework project-memory hooks, wired with
# "python -S -B <file>"; run in-process (with site loaded and a different sys.path) the hot hook exits 1 and says
# nothing where its own process returns context, and the prompt hook returns context where its own process is silent.
# Never in-process without a fresh passing control: a hook that changes what it says is a changed brain, not a speedup.
# 13 Oct 2026 (front R25): the hot hook's exit 1 was its first line, sys.stdin.buffer.read(), meeting
# the io.StringIO below, which has no .buffer - the AttributeError is raised outside its try. Given the bytes its own
# process would have read (STDIN_BYTES_OK) it says exactly what its own process says: 331 payloads (320 real tool
# calls from 30 sessions, 11 edge cases) gave the same rc and stdout and left the same state files, where this
# file's StringIO gives rc 1 and silence; 116 ms a call saved, 204.6 -> 88.2 ms median (primary_directive/
# improve_1012/fronts/R25/runs/control_hot_1.txt). It left this set for the allowlist on that control. The prompt
# hook (a different difference) and the strong-node runner stay.
FORCE_SUBPROCESS = {# project_memory_hook.py left on 3 Nov for STDIN_BYTES_OK (below): its difference was the stdin
                    # the strong-nodes runtime hook: caused the 2026-09-15 fleet-wide block. Passing its argv fixed
                    # the argparse exit 2, and it still returns rc 1 with a traceback in-process where its own
                    # process returns 0 silently. Belt and braces with the allowlist below - it fails the control,
                    # so it could not join the fast path anyway; this makes it never even be attempted.
                    "hook.py"}

# Hooks that read their payload as BYTES (sys.stdin.buffer) and were proven exact in-process with a stdin that has
# them: the same bytes run_subprocess would send, behind a text layer, so .buffer.read() and .read() both work. Every
# other hook keeps the io.StringIO it has always had, byte for byte; hook_dispatch_extend.py's stdin probe relies on
# that. A name joins this set only with its own control.
STDIN_BYTES_OK = frozenset({"project_memory_hot_hook.py",
                            # 3 Nov 2026: the prompt hook's 15 Sep difference was the same stdin. On APFS
                            # clones of its store, 8 of 8 prompts gave the same rc, stdout and rows (only time-derived
                            # ids differ); with the StringIO it printed a failed-closed note and wrote no row at all.
                            # In its own process it logged 962 s of slow prompts on 3 Nov, 70 runs at 5 s or more, for
                            # 0.1-0.4 s of work (brain_improvement/hook_inproc_2026_11_03).
                            "project_memory_hook.py"})


def _stdin_for(path, payload):
    if os.path.basename(path).lower() in STDIN_BYTES_OK:
        return io.TextIOWrapper(io.BytesIO(payload.encode("utf-8", "surrogateescape")), encoding="utf-8",
                                errors="surrogateescape", newline="\n")
    return io.StringIO(payload)

# THE ALLOWLIST, and why it replaced a denylist.
# A denylist asks me to remember every hook that misbehaves shared. On 2026-09-15 that memory failed twice in one
# hour: the strong-nodes hook moved in-process when a peer improved the argv parsing, exited 2 on a missing argument,
# and blocked every tool call in every session on the machine until the owner ran the rollback by hand. Passing its
# argv through fixed the argument error and it STILL fails in-process for a second reason.
# So the default is now the old, safe path - a subprocess - and a hook earns the fast path only by being proven
# equivalent. hook_dispatch_control.py writes this file after a passing run, listing the hooks whose in-process
# output matched their own subprocess exactly. An unknown hook, a new hook, or a hook edited since the last control
# run is simply not in the list, and runs the way it always did.
ALLOW = os.path.expanduser("~/.claude/dreaming/hook_dispatch_allow.json")


_ALLOW_STALE = [False]          # set by _allowlist(), read once per dispatch so the log says it ONCE


# THE HOOK CODE CACHE (13 Oct 2026, front R24). runpy.run_path keeps no bytecode: for every in-process
# hook on every call it asked each sys.path hook for an importer (the zip importer opens the file and reads its tail),
# read the file twice and compiled the whole source. Measured 13 Oct 20:15 under a dream's load: 166 ms of a Bash
# PreToolUse across its 205 in-process hooks (191 of them film hooks) and 372 ms of a UserPromptSubmit, against 10 and
# 17 ms from kept code objects (primary_directive/improve_1012/fronts/R24/runs/measure_compile_1.txt). So each event
# keeps its hooks' code objects in one file in the __pycache__ beside this one, keyed on the exact path string and its
# abspath, the file's size and mtime in ns; the file name carries the interpreter (hexversion, -O) so two Pythons never
# overwrite each other's. A kept object is the one compile(source, path, "exec") returned for that file, and the hook
# runs through runpy._run_module_code - the function run_path itself ends in - with the arguments run_path gives it. A
# miss does what run_path does first (no importer for the path, not a compiled file), compiles, and is kept. Anything
# unusual before the hook starts gives None and runpy.run_path runs exactly as before. One difference, by design: a
# compile-time SyntaxWarning prints on the dispatch that compiles the hook, as an import's does, not on every call (the
# merge reads stderr only from a blocking hook). Like a .pyc, a kept object goes stale only if a file is rewritten at the
# same size within one clock tick of a dispatch that compiled it. HOOK_CODE_CACHE=0 turns the cache off.
_CODE_DIR = os.environ.get("HOOK_CODE_CACHE_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "__pycache__")
_RUN_MODULE_CODE = getattr(runpy, "_run_module_code", None)
if getattr(getattr(_RUN_MODULE_CODE, "__code__", None), "co_varnames", ())[:6] != (
        "code", "init_globals", "mod_name", "mod_spec", "pkg_name", "script_name"):
    _RUN_MODULE_CODE = None          # a Python whose private helper changed shape: run_path for every hook
_CODE_STAMP = (1, sys.hexversion, marshal.version, sys.flags.optimize)
_CODE = {"event": None, "kept": {}, "new": {}, "hits": 0, "misses": 0}


def _code_file(event):
    return os.path.join(_CODE_DIR, "hook_code_%s.%x.o%d.marshal" % (event, sys.hexversion, sys.flags.optimize))


def _code_read(event):
    """The event's kept code objects, {key: (mtime_ns, size, marshalled code)}; {} when absent or unreadable."""
    try:
        with open(_code_file(event), "rb") as fh:
            d = marshal.loads(fh.read())
        if isinstance(d, dict) and d.get("stamp") == _CODE_STAMP and isinstance(d.get("code"), dict):
            return d["code"]
    except Exception:
        pass
    return {}


def _hook_code(path):
    """The code object runpy.run_path(path) would compile, kept from an earlier dispatch while the file is unchanged.
    None when the cache is off or anything is unusual; the caller then runs runpy.run_path itself."""
    event = _CODE["event"]
    if not event or _RUN_MODULE_CODE is None or not isinstance(path, str):
        return None
    try:
        full = os.path.abspath(path)
        st = os.stat(full)
        key = path + "\0" + full
        kept = _CODE["kept"].get(event)
        if kept is None:
            kept = _CODE["kept"][event] = _code_read(event)
        e = kept.get(key)
        if e is not None and e[0] == st.st_mtime_ns and e[1] == st.st_size:
            code = marshal.loads(e[2])
            _CODE["hits"] += 1
            return code
        import pkgutil                   # run_path's own first steps, so a miss runs what run_path would run
        if pkgutil.get_importer(path) is not None:
            return None
        with io.open_code(full) as f:
            if pkgutil.read_code(f) is not None:
                return None
            f.seek(0)
            src = f.read()
            st2 = os.fstat(f.fileno())
        code = compile(src, path, "exec", dont_inherit=True)
        _CODE["misses"] += 1
        if st2.st_mtime_ns == st.st_mtime_ns and st2.st_size == st.st_size == len(src):
            _CODE["new"].setdefault(event, {})[key] = (st.st_mtime_ns, st.st_size, marshal.dumps(code))
        return code
    except Exception:
        return None


def _code_flush():
    """Keep what this dispatch compiled. Best effort: when another process holds the file (Windows refuses the
    replace) or anything else fails, the old file stays and a later dispatch compiles again."""
    event = _CODE["event"]
    new = _CODE["new"].pop(event, None) if event else None
    if not new:
        return
    target = _code_file(event)
    tmp = "%s.%d.tmp" % (target, os.getpid())
    try:
        os.makedirs(_CODE_DIR, exist_ok=True)
        merged = _code_read(event)       # what is on disk now, so a peer's fresh entries are not dropped
        merged.update(new)
        with open(tmp, "wb") as fh:
            fh.write(marshal.dumps({"stamp": _CODE_STAMP, "code": merged}))
        os.replace(tmp, target)
        _CODE["kept"][event] = merged
        log("%s CODE CACHE kept %d hook(s) compiled this dispatch, %d in the file" % (event, len(new), len(merged)))
    except Exception as e:
        log("%s CODE CACHE not written (%s: %s)" % (event, type(e).__name__, str(e)[:120]))
        try:
            os.remove(tmp)
        except Exception:
            pass


def _allowlist():
    """The hooks proven equivalent in-process. Empty means every hook spawns.

    THE STALENESS RULE IS RIGHT AND ITS SILENCE IS NOT (2026-09-18). An allowlist older than the
    dispatcher proves nothing about the dispatcher running now, so emptying it is correct. But it
    emptied SILENTLY, and the cost is invisible and large: with 33 hooks allowed, a prompt-plus-tool
    cycle runs them in this process; with the list empty it spawns **46** of them - 18 on
    UserPromptSubmit, 12 on PreToolUse, 14 on Stop, 2 on PostToolUse - on the one box whose measured
    defining problem is that CreateProcess stalls, 82 ms healthy against 19,931 ms inside a stall
    window. Nothing in the log said so; the only symptom was every hook reading SUBPROCESS in a
    census nobody runs.
    This has now fired twice in two days, both times from an edit to THIS file that could not affect
    in-process behaviour at all - a logging change on 09-17 and a subprocess-timeout change on 09-18,
    both of mine. [[editing_a_file_can_disable_an_optimisation_keyed_on_its_mtime_2026_09_17]]
    So it still empties, and now it SAYS so, once per dispatch, with both timestamps. Re-signing is
    `python hook_dispatch_allowlist.py --resign` or simply touching the json AFTER reading the diff
    and satisfying yourself the change cannot alter what a hook does when run in-process.
    """
    try:
        d = json.load(open(ALLOW, encoding="utf-8"))
        a_m, d_m = os.path.getmtime(ALLOW), os.path.getmtime(os.path.abspath(__file__))
        if a_m < d_m:
            _ALLOW_STALE[0] = (a_m, d_m)
            return set()
        return {str(x).lower() for x in d.get("in_process_ok", [])}
    except Exception:
        return set()


def log(s):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "  " + s + "\n")
    except Exception:
        pass


# THE MATCHER IS THE HARNESS'S OWN RULE (29 Sep, interactive audit BRAIN-INTERACTIVE-20260928-CRITICAL-PATH, D05). Read out
# of Claude Code 2.1.284 (its hook matcher and the value each event matches): an empty matcher or "*" matches everything;
# a matcher of letters, digits, "_" and "|" only - and ",", " " and "-" on the events in _COMMA_EVENTS - is a list of
# exact names, split on "|" (and ","), with the legacy tool names mapped to today's; anything else is a regular expression
# tested unanchored, against the name and its legacy names. Events with no match value (UserPromptSubmit, Stop) run every
# block. This used re.search for everything, so "Edit|Write" also ran on NotebookEdit and TodoWrite.
_LEGACY_TOOLS = {"Task": "Agent", "KillShell": "TaskStop", "KillBash": "TaskStop", "ListPeers": "ListAgents",
                 "Brief": "SendUserMessage", "ListMcpResources": "ListMcpResourcesTool",
                 "ReadMcpResource": "ReadMcpResourceTool", "ReadMcpResourceDir": "ReadMcpResourceDirTool"}
_COMMA_EVENTS = frozenset({"PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest", "PermissionDenied",
                           "UserPromptExpansion", "SessionStart", "SessionEnd", "Setup", "PreCompact", "PostCompact",
                           "PreModelSwitch", "PostModelSwitch", "Notification", "SubagentStart", "SubagentStop",
                           "Elicitation", "ElicitationResult", "ConfigChange", "InstructionsLoaded", "DirectoryAdded"})
_TOOL_EVENTS = ("PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest", "PermissionDenied")
_MATCH_FIELD = {"UserPromptExpansion": "command_name", "SessionStart": "source", "Setup": "trigger",
                "PreCompact": "trigger", "PostCompact": "trigger", "Notification": "notification_type",
                "SessionEnd": "reason", "StopFailure": "error", "SubagentStart": "agent_type", "SubagentStop": "agent_type"}


def match_value(event, hook):
    """What the harness matches a block's matcher against for this event; None when the event takes no matcher."""
    if event in _TOOL_EVENTS:
        return str(hook.get("tool_name") or "")
    field = _MATCH_FIELD.get(event)
    return None if field is None else str(hook.get(field) or "")


def matches(matcher, value, event="PreToolUse"):
    if value is None or not matcher or matcher == "*":
        return True
    import re
    value = str(value)
    comma = event in _COMMA_EVENTS
    if re.fullmatch(r"[a-zA-Z0-9_|, -]+" if comma else r"[a-zA-Z0-9_|]+", matcher):
        names = {_LEGACY_TOOLS.get(n.strip(), n.strip()) for n in re.split(r"[|,]" if comma else r"\|", matcher)}
        return value in names
    try:
        rx = re.compile(matcher)
    except Exception:
        return False
    return rx.search(value) is not None or any(rx.search(old) for old, new in _LEGACY_TOOLS.items() if new == value)


LIST_UNREADABLE = []          # set by hook_entries; surfaced once per run where the session can see it
_TOOL_NAME = object()         # hook_entries' default: match tool events on tool_name, run every block of the others


def hook_entries(event, tool_name, settings_path=SETTINGS, value=_TOOL_NAME):
    """The hook commands the harness itself would run for this event, minus the dispatcher. `value` is what the
    matchers are tested against (match_value); by default the tool name on a tool event, nothing on the others."""
    if value is _TOOL_NAME:
        value = (tool_name or "") if event in _TOOL_EVENTS else None
    out = []
    try:
        if settings_path == SETTINGS and os.path.exists(LIST):
            blocks_by_event = json.load(open(LIST, encoding="utf-8"))
        else:
            blocks_by_event = json.load(open(settings_path, encoding="utf-8")).get("hooks", {})
    except Exception as e:
        # A LOG IS NOT A REPORT. Measured 2026-09-21 on a fresh Windows install: the install
        # wrote a hook_dispatch_list.json whose expanded paths were not valid JSON, so this
        # branch ran on every hook of every session - exit 0, no output, nothing to see. The
        # dead-man behaviour is right (a hook must never take a session down) but the silence
        # is not: the one person who could repair it never learns. Say it once, in the session.
        log("hook list unreadable: %r" % (e,))
        LIST_UNREADABLE.append("%s: %s" % (os.path.basename(LIST), e))
        return out
    for block in blocks_by_event.get(event, []):
        if not matches(block.get("matcher"), value, event):
            continue
        for h in block.get("hooks", []):
            cmd, args = h.get("command", ""), h.get("args") or []
            if SELF in (cmd + " " + " ".join(args)).lower():
                continue
            if _entry_flag_off(h):
                FLAG_SKIPPED[0] += 1
                continue
            out.append((cmd, args))
    return out


# AN ENTRY CAN NAME ITS OWN SWITCH (2026-09-24, from the ab30 study). "enabled_if": "<file>" on an entry in this list -
# which the harness never reads - skips the entry before any process starts while that file is absent, and counts it
# in the " flag off: N" tally beside the --enabled form above. It exists for entries that are a command STRING (an
# inline python -c), where an --enabled argument would reach the hook's own parser. First user: the shadow error
# graph, wired as `python -s -B -c "...runpy..."` on PreToolUse, PostToolUse, UserPromptSubmit and Stop, which cost
# 162,000 s of hook time in the 26 h to 09-24 01:50 (48 pct of all tool-call hook time: 35 pct of PreToolUse, 80 pct
# of PostToolUse, 3.8 s per slow call) and 131 timeouts, while writing 572 MB a day for a reader that was never
# switched on (the_error_graph_writes_23gb_for_a_consumer_never_switched_on_2026_08_22). Its switch is
# error_graph_runtime/ENABLED; the corpus it already wrote is kept. Never raises; any doubt runs the hook.
FLAG_SKIPPED = [0]


def _entry_flag_off(h):
    try:
        f = h.get("enabled_if")
        return isinstance(f, str) and bool(f.strip()) and not os.path.exists(os.path.expanduser(f.strip()))
    except Exception:
        return False


# Interpreter flags that consume the FOLLOWING token, so it is not the script name.
_VALUE_FLAGS = ("-W", "-X", "--check-hash-based-pycs")


def script_argv(cmd, args):
    """('python [-flags] file.py [its own args...]') -> (path, argv_tail), else (None, []): WHICH script an entry runs,
    whatever process it will run in.

    Scans left to right and keeps ORDER: the script is the first token that is not an
    interpreter flag and ends in .py, and everything after it - flags included - is the
    script's own argv. Splitting flags from positionals first would reorder a hook's
    arguments (strong_nodes is invoked as `hook.py --hook-runtime claude --enabled <path>`,
    where two of the four argv entries begin with a dash).
    """
    try:
        if args:
            parts = [cmd] + [str(a).strip('"') for a in args]
        else:
            import shlex
            parts = [p.strip('"') for p in shlex.split(cmd, posix=False)]
        if not parts:
            return None, []
        if not os.path.basename(parts[0]).lower().strip('"').startswith("python"):
            return None, []
        i = 1
        while i < len(parts):
            p = parts[i]
            if p in ("-c", "-m"):
                return None, []          # not a file invocation; leave it to a subprocess
            if p in _VALUE_FLAGS:
                i += 2
                continue
            if p.startswith("-"):
                i += 1
                continue
            break
        if i >= len(parts) or not parts[i].lower().endswith(".py"):
            return None, []
        return parts[i], parts[i + 1:]
    except Exception:
        return None, []


def as_python_argv(cmd, args, allow_all=False, memo=None):
    """(path, argv_tail) when the entry may run in this process, else (None, []): a python file invocation that is not
    forced onto its own process, is on the proven list (or allow_all) and exists.

    `memo` is the dict dispatch() hands every entry of one dispatch: the list is read at the first entry that asks, as
    before, and that answer serves the rest of the dispatch, as the hook list itself is read once per dispatch. Until
    13 Oct every entry re-read the 13 KB json and stat'ed two files: 64 ms of every PreToolUse over its ~205 entries,
    113-125 ms under a dream's load (primary_directive/improve_1012/fronts/R24/runs/
    measure_overhead_1.txt and probe_rest_1.txt). Without a memo every call reads the files, exactly as it did."""
    try:
        path, tail = script_argv(cmd, args)
        if not path:
            return None, []
        base = os.path.basename(path).lower()
        if base in FORCE_SUBPROCESS:
            return None, []
        if not allow_all:
            if memo is None:
                names = _allowlist()
            elif "allow" in memo:
                names = memo["allow"]
            else:
                names = memo["allow"] = _allowlist()
            if base not in names:
                return None, []                  # not proven equivalent -> its own process, as always
        if not os.path.exists(path):
            return None, []
        return path, tail
    except Exception:
        return None, []


def as_python_file(cmd, args, allow_all=False):
    """Return the .py path if this entry is a plain 'python [-flags] file.py ...' invocation."""
    return as_python_argv(cmd, args, allow_all)[0]


def run_in_process(path, payload, argv_tail=()):
    """Returns (rc, stdout, stderr). Never raises.

    argv_tail is the hook's OWN arguments. Dropping them caused a fleet-wide outage on 2026-09-15 18:5x: the
    strong-nodes hook is wired as `hook.py --hook-runtime claude --enabled <path>`, ran here with argv=[path],
    and argparse exits 2 on a missing required argument - 2 being the code that BLOCKS a tool call. Every tool
    call in every session on the machine was refused until the owner ran the rollback by hand. A hook's argv is
    part of how it is wired; pass it or do not run it in-process.
    """
    saved_env = dict(os.environ)
    saved_argv = list(sys.argv)
    saved_path = list(sys.path)
    saved_cwd = os.getcwd()
    so, se = io.StringIO(), io.StringIO()
    real_in, real_out, real_err = sys.stdin, sys.stdout, sys.stderr
    rc = 0
    try:
        sys.stdin = _stdin_for(path, payload)
        sys.stdout, sys.stderr = so, se
        sys.argv = [path] + [str(a) for a in argv_tail]
        d = os.path.dirname(os.path.abspath(path))
        if d not in sys.path:
            sys.path.insert(0, d)
        code = _hook_code(path)
        if code is None:
            runpy.run_path(path, run_name="__main__")
        else:
            _RUN_MODULE_CODE(code, None, "__main__", pkg_name="", script_name=path)
    except SystemExit as e:
        c = e.code
        rc = 0 if c is None else (c if isinstance(c, int) else 1)
    except BaseException:
        rc = 1
        try:
            se.write(_format_exc(saved_path))
        except Exception:
            pass
    finally:
        sys.stdin, sys.stdout, sys.stderr = real_in, real_out, real_err
        sys.argv = saved_argv
        sys.path[:] = saved_path
        # 7 Oct 2026: restore only when the hook changed it. A full restore is one Windows call per
        # variable, 0.76 ms a hook, and with his 150 film hooks in-process that was 0.11 s of every tool call.
        if os.environ != saved_env:
            os.environ.clear(); os.environ.update(saved_env)
        try:
            os.chdir(saved_cwd)
        except Exception:
            pass
    return rc, so.getvalue(), se.getvalue()


def run_subprocess(cmd, args, payload, budget=None):
    """Bytes in, bytes out, UTF-8 both ways. 2026-09-20: with text=True the child's stdin was ENCODED and its stdout
    DECODED in the locale code page (cp1252 under `python -s`), so a prompt holding one character cp1252 cannot encode
    (his Codex chevron U+276F, Hebrew) raised inside communicate() and the child sat on an unfed stdin until the
    budget expired - 90 s per hook, every hook, for that prompt; and a hook OUTPUT holding such a byte killed the
    reader thread, p.stdout came back None and the hook's whole context vanished with rc 0 and no log row. The
    owner-prompts block carried the chevron from 2026-09-18 on, so not one delivered block reached a transcript."""
    budget = PER_HOOK_BUDGET_S if budget is None else budget
    data = payload.encode("utf-8", "surrogateescape") if isinstance(payload, str) else (payload or b"")
    try:
        import subprocess            # here, not on the import line: most dispatches start no process (R31)
    except Exception as e:
        return 1, "", "hook_dispatch: %r" % (e,)
    try:
        if args:
            p = subprocess.run([cmd] + args, input=data, capture_output=True,
                               timeout=budget, creationflags=CREATE_NO_WINDOW)
        else:
            p = subprocess.run(cmd, shell=True, input=data, capture_output=True,
                               timeout=budget, creationflags=CREATE_NO_WINDOW)
        out = (p.stdout or b"").decode("utf-8", "replace")
        err = (p.stderr or b"").decode("utf-8", "replace")
        return p.returncode, out, err
    except subprocess.TimeoutExpired:
        return 1, "", "hook_dispatch: timed out after %gs" % budget
    except Exception as e:
        return 1, "", "hook_dispatch: %r" % (e,)


# THE HOOKS THAT RUN AS THEIR OWN PROCESS NO LONGER WAIT IN LINE. The loop ran every hook one after another, so on
# UserPromptSubmit the six hooks that spawn cost the sum of their walls - 16.5 s of a 54 s median loop in one day's
# profile (129 dispatches) - while each mostly waits on its own interpreter start and disk. They are now all started at
# the top of the loop, BEFORE any in-process hook can change the cwd or the environment (so each child gets exactly the
# cwd and env it got before), fed and read on their own threads, and collected after the in-process hooks; their
# results go back into the settings order, so merge() is unchanged. Off for one dispatch: HOOK_PARALLEL_SUBPROC=0.
PARALLEL_SUBPROC_EVENTS = {"UserPromptSubmit", "Stop", "SessionStart"}
PARALLEL_AB_FLAG = os.path.expanduser("~/.claude/compute/hook_parallel_ab.on")


class _Spawned(object):
    """One hook started at the top of the loop; result() waits for it and returns (rc, out, err, seconds)."""

    def __init__(self, cmd, args, payload, budget):
        self.t0 = time.perf_counter()
        self.t1 = None
        self.res = None
        self.budget = PER_HOOK_BUDGET_S if budget is None else budget
        data = payload.encode("utf-8", "surrogateescape") if isinstance(payload, str) else (payload or b"")
        try:
            import subprocess
            import threading
            self._sp = subprocess
            if args:
                self.p = subprocess.Popen([cmd] + args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
            else:
                self.p = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
            if getattr(self.p, "_handle", None):
                _unthrottle(self.p._handle)
            self.th = threading.Thread(target=self._talk, args=(data,), daemon=True)
            self.th.start()
        except Exception as e:
            self.p, self.th = None, None
            self.res = (1, "", "hook_dispatch: %r" % (e,))
            self.t1 = time.perf_counter()

    def _talk(self, data):
        try:
            out, err = self.p.communicate(input=data, timeout=max(0.1, self.budget - (time.perf_counter() - self.t0)))
            self.res = (self.p.returncode, (out or b"").decode("utf-8", "replace"), (err or b"").decode("utf-8", "replace"))
        except self._sp.TimeoutExpired:
            try:
                self.p.kill()
                self.p.communicate(timeout=5)
            except Exception:
                pass
            self.res = (1, "", "hook_dispatch: timed out after %gs" % self.budget)
        except Exception as e:
            self.res = (1, "", "hook_dispatch: %r" % (e,))
        self.t1 = time.perf_counter()

    def result(self):
        if self.th is not None:
            self.th.join(max(0.1, self.budget + 10 - (time.perf_counter() - self.t0)))
            if self.res is None:                      # the thread is stuck past its budget: give up on it, never hang
                try:
                    self.p.kill()
                except Exception:
                    pass
                self.res = (1, "", "hook_dispatch: timed out after %gs" % self.budget)
                self.t1 = time.perf_counter()
        rc, out, err = self.res
        return rc, out, err, (self.t1 or time.perf_counter()) - self.t0


RANK = {"allow": 0, "ask": 1, "deny": 2}

# THE MERGED CONTEXT IS CUT TO WHAT THE MODEL CAN RECEIVE. Claude Code hands the model at most HARNESS_INLINE_MAX chars
# of one hook command's context; past that it writes the text to a file and shows a 2 KB preview - the first 2 KB of
# whichever hook happened to print first, and nothing of the rest. Measured 2026-09-20 over the log since 09-18 18:00:
# 646 of 1,009 UserPromptSubmit merges (64 percent, median 53 KB) were past the limit, so for two days the reflexes, the
# checklist digest and the associations mostly never reached a session. Now the budget is shared: when the hooks' texts
# fit, nothing changes; when they do not, each hook keeps its whole text if it is under an equal share and the large
# ones are cut to that share at a line boundary, the whole untrimmed text goes to a file named in the first line, and
# the log says so (FIT). INLINE_CAP sits under the harness number because it counts UTF-16 units and we count code points.
INLINE_CAP = 9800
OVERFLOW_DIR = os.path.expanduser("~/.claude/compute/hook_overflow")
OVERFLOW_KEEP_S = 48 * 3600
_LAST_FIT = [None]        # (total_chars, n_hooks, n_cut, path) of the last merge that had to cut, for the dispatch log


# EVERY OVERFLOW FILE IS ITS OWN, AND NONE IS LOST (29 Sep, critical-path audit D06). The name carried the event, eight
# characters of the session and the second, and the file was opened "w": two cuts in one second for one session (or two
# sessions sharing eight characters) wrote the same file, and the pointer the first one had already delivered named the
# second one's text. Each call now adds its process id and a random id and creates the file exclusively. And an expired
# file was simply removed after 48 hours, so every pointer older than that named nothing (1,660 cuts in the log to 29
# Sep, 134 files left): it is now appended to its month's archive under the brain graph before it goes (brain-data law,
# 26 Sep), one pruner at a time, and a file whose archiving fails is kept.
OVERFLOW_ARCHIVE_DIR = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "live", "hook_overflow_archive")


def _overflow_archive(path):
    """Append one overflow file to its month's archive (a gzip member holding one JSON line). True once written."""
    import gzip
    st = os.stat(path)
    with open(path, "rb") as f:
        body = f.read()
    rec = {"name": os.path.basename(path), "mtime": round(st.st_mtime, 3), "bytes": len(body),
           "text": body.decode("utf-8", "replace")}
    os.makedirs(OVERFLOW_ARCHIVE_DIR, exist_ok=True)
    dest = os.path.join(OVERFLOW_ARCHIVE_DIR, time.strftime("%Y-%m", time.localtime(st.st_mtime)) + ".jsonl.gz")
    with open(dest, "ab") as out:
        out.write(gzip.compress((json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")))
        out.flush()
        os.fsync(out.fileno())
    return True


def _overflow_prune(now):
    """Archive then remove the overflow files past OVERFLOW_KEEP_S. One pruner at a time (a lock file; one left by a
    crash is cleared after ten minutes). Never raises."""
    lock = os.path.join(OVERFLOW_DIR, ".prune.lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            if now - os.path.getmtime(lock) > 600:
                os.remove(lock)
        except Exception:
            pass
        return
    except Exception:
        return
    try:
        for fn in os.listdir(OVERFLOW_DIR):
            p = os.path.join(OVERFLOW_DIR, fn)
            try:
                if fn.endswith(".txt") and now - os.path.getmtime(p) > OVERFLOW_KEEP_S and _overflow_archive(p):
                    os.remove(p)
            except Exception:
                pass
    finally:
        os.close(fd)
        try:
            os.remove(lock)
        except Exception:
            pass


def _overflow_write(event, session, items):
    """The untrimmed texts, one section per hook, in a file of its own the model can read; expired files archived, then
    pruned. Never raises."""
    try:
        import uuid
        os.makedirs(OVERFLOW_DIR, exist_ok=True)
        now = time.time()
        _overflow_prune(now)
        uid = uuid.uuid4().hex[:10]
        name = "%s_%s_%s_%d_%s.txt" % (event, (session or "nosession")[:8], time.strftime("%Y%m%d_%H%M%S"), os.getpid(), uid)
        path = os.path.join(OVERFLOW_DIR, name)
        with open(path, "x", encoding="utf-8", newline="\n") as f:
            f.write("hook-dispatch overflow: %s, session %s, %s, id %s - every hook's whole text for this prompt\n\n"
                    % (event, session or "?", time.strftime("%Y-%m-%d %H:%M:%S"), uid))
            for label, text in items:
                f.write("==== %s (%d chars) ====\n%s\n\n" % (label, len(text), text))
        return path.replace("\\", "/")
    except Exception:
        return None


# THE HOOKS THAT CARRY WHAT THE BRAIN KNOWS ABOUT THE WORK GET THREE SHARES (2026-09-24, from a brain-on/off study). With equal
# shares, a maths session's 10,000 characters went as much to the process census, the slow-machine probe and the task
# board as to the math and proof cards: in the study's brain-on arms the hooks wrote 12-22 K characters on a proof, the
# proof card was cut on every one of the nine proofs where it fired, and the checks it had picked for the argument never
# reached the model once (replayed from the untrimmed overflow files). A hook named here gets FIT_WORK_WEIGHT
# shares to every other hook's one; the order of the texts and the cut at a line boundary are as before.
FIT_WORK_HOOKS = frozenset({"math_sense.py", "proof_sense.py", "instinct_prime.py", "spread_recall.py", "fok_probe.py",
                            "movie_alert_hook.py"})
FIT_WORK_WEIGHT = 3


def _fit_weight(label):
    return FIT_WORK_WEIGHT if os.path.basename(str(label)) in FIT_WORK_HOOKS else 1


def fit_contexts(items, cap, event, session):
    """items: [(label, text)] in hook order. Returns the joined additionalContext of at most `cap` chars.
    Under the cap: the plain join, as before. Over it: weighted water-filling - hooks are visited in order of length
    per weight, each keeps its whole text when that is within its weighted share of what is left, a larger one is cut
    to that share at a line boundary with a note saying how much more is in the overflow file - so no single loud hook
    can push the quiet ones out of the model's view, and the hooks about the work outweigh the housekeeping ones."""
    joined = "\n".join(t for _, t in items)
    if len(joined) <= cap:
        return joined
    path = _overflow_write(event, session, items)
    head = ("[hook-dispatch] the hooks wrote %d chars this prompt, past the %d the harness hands the model inline: each is "
            "cut to its share below; the whole text is in %s" % (len(joined), HARNESS_INLINE_MAX, path or "(no file: the write failed)"))
    note_cost = 96
    remaining = cap - len(head) - 1 - len(items)
    weights = [_fit_weight(label) for label, _ in items]
    order = sorted(range(len(items)), key=lambda i: len(items[i][1]) / float(weights[i]))
    keep, w_left, n_cut = {}, sum(weights), 0
    for i in order:
        label, t = items[i]
        share = max(0, (remaining * weights[i]) // max(1, w_left))
        if len(t) <= share:
            keep[i] = t
        else:
            k = max(0, share - note_cost)
            cut = t[:k]
            if "\n" in cut and cut.rfind("\n") > k // 2:
                cut = cut[:cut.rfind("\n")]
            cut = cut.rstrip() + "\n  ... [%s: %d more chars, in the file above]" % (label, len(t) - len(cut))
            keep[i] = cut
            n_cut += 1
        remaining -= len(keep[i])
        w_left -= weights[i]
    _LAST_FIT[0] = (len(joined), len(items), n_cut, path)
    out = head + "\n" + "\n".join(keep[i] for i in range(len(items)))
    return out[:cap]


# 1000-ways #13 (2026-10-30): one "recently attended" store across the hooks. Measured by eval_hook_cofire.py on 30 h
# of Mac transcripts: 71.5% of all hook text was a block the session had been shown, numbers aside, within the last
# 10 min - [prompt-contract] 91% repeats, [project-context] 97.5% - while the memory-naming hooks almost never repeat
# (unconscious 0.8%, exact-name 0%) and never named one slug twice in an event. So only those two tags are folded: a
# block whose number-blind text this session saw within DEDUP_WINDOW_S is dropped; it shows again once the window has
# passed, and any change of substance is new text and shows at once. Tool and prompt events only; HOOK_DEDUP_OFF=1
# turns it off.
DEDUP_TAGS = ("prompt-contract", "project-context")
DEDUP_WINDOW_S = 600
DEDUP_EVENTS = ("PreToolUse", "PostToolUse", "UserPromptSubmit")
DEDUP_DIR = os.path.expanduser("~/.claude/compute/hook_seen")


def dedup_repeats(items, event, session, now=None):
    """items: [(label, text)]. Returns them with the repeated DEDUP_TAGS blocks removed (an item left empty goes)."""
    if event not in DEDUP_EVENTS or not session or os.environ.get("HOOK_DEDUP_OFF") == "1":
        return items
    if not any("[%s]" % t in text for _, text in items for t in DEDUP_TAGS):
        return items
    import hashlib, re
    now = time.time() if now is None else now
    path = os.path.join(DEDUP_DIR, (re.sub(r"[^0-9A-Za-z_-]", "", session) or "x")[:64] + ".json")
    try:
        with open(path) as f:
            seen = {k: v for k, v in json.load(f).items() if now - v <= DEDUP_WINDOW_S}
    except (OSError, ValueError, AttributeError):
        seen = {}
    tag_at = re.compile(r"^\[([a-z][a-z0-9_-]{1,30})\]", re.M)
    out, changed = [], False
    for label, text in items:
        starts = [m.start() for m in tag_at.finditer(text)]
        if not starts:
            out.append((label, text))
            continue
        pieces = [text[:starts[0]]] + [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]
        kept = []
        for p in pieces:
            m = tag_at.match(p)
            if m and m.group(1) in DEDUP_TAGS:
                key = hashlib.md5(re.sub(r"\d+", "#", " ".join(p.split())).encode()).hexdigest()[:16]
                if key in seen:
                    changed = True
                    continue
                seen[key] = now
                changed = True
            kept.append(p)
        t = "".join(kept).rstrip("\n")
        if t.strip():
            out.append((label, t))
    if changed:
        try:
            os.makedirs(DEDUP_DIR, exist_ok=True)
            tmp = "%s.%d.tmp" % (path, os.getpid())
            with open(tmp, "w") as f:
                json.dump(seen, f)
            os.replace(tmp, path)
        except OSError:
            pass
    return out


TOP_LEVEL_EVENTS_WITHOUT_HSO = ("Stop", "SubagentStop")


def _merge_top(top, obj):
    """Fold one hook's top-level control fields into `top`, by the rule each field needs: a block, a stop and a
    suppression win over their absence; reasons, stop reasons and system messages are joined in hook order; any other
    top-level key keeps its first value. Nothing here is moved into hookSpecificOutput."""
    blocked = obj.get("decision") == "block"
    for k, v in obj.items():
        if k == "hookSpecificOutput":
            continue
        if k == "decision":
            if v == "block" or "decision" not in top:
                top["decision"] = v
        elif k == "reason":
            if blocked:
                top["_block_reasons"] = top.get("_block_reasons", []) + [str(v)]
            else:
                top.setdefault("_other_reason", str(v))
        elif k == "continue":
            if v is False:
                top["continue"] = False
                if obj.get("stopReason"):
                    top["_stop_reasons"] = top.get("_stop_reasons", []) + [str(obj["stopReason"])]
            else:
                top.setdefault("continue", v)
        elif k == "stopReason":
            if obj.get("continue") is not False:
                top.setdefault("_loose_stop_reason", str(v))
        elif k == "systemMessage":
            top["_messages"] = top.get("_messages", []) + [str(v)]
        elif k == "suppressOutput":
            top["suppressOutput"] = bool(top.get("suppressOutput")) or bool(v)
        else:
            top.setdefault(k, v)


def _finish_top(top):
    out = {k: v for k, v in top.items() if not k.startswith("_")}
    if top.get("_block_reasons") and out.get("decision") == "block":
        out["reason"] = "\n\n".join(top["_block_reasons"])
    elif top.get("_other_reason"):
        out["reason"] = top["_other_reason"]
    if out.get("continue") is False:
        if top.get("_stop_reasons"):
            out["stopReason"] = "\n\n".join(top["_stop_reasons"])
    elif top.get("_loose_stop_reason"):
        out["stopReason"] = top["_loose_stop_reason"]
    if top.get("_messages"):
        out["systemMessage"] = "\n".join(top["_messages"])
    return out


def merge(results, event, session=""):
    """results: list of (label, rc, stdout, stderr). Returns (exit_code, stdout_text, stderr_text).

    TOP-LEVEL FIELDS STAY TOP-LEVEL (28 Sep, interactive audit IA-20260928-07). A hook's JSON has two layers: the
    universal fields (continue, stopReason, suppressOutput, systemMessage) and, on Stop, UserPromptSubmit and PostToolUse,
    decision and reason, all at the top; and hookSpecificOutput for the event's own fields. This function used to fold
    every top-level key into hookSpecificOutput, so a Stop hook's {"decision": "block"} left as
    {"hookSpecificOutput": {"hookEventName": "Stop", "decision": "block"}} and no block reached a session: from the day
    the dispatcher took the Stop chain (15 Sep) the work timer, conscience, claim_check, retraction_guard,
    report_guards, pre_report_check, reality_monitor, summary_hook, mailbox and campaign_keep_working could not stop a
    turn by JSON, while exit code 2 still could. Top-level fields are now merged at the top (_merge_top), and a Stop or
    SubagentStop answer carries no hookSpecificOutput, which those events do not define; a context a Stop hook printed
    was never given to the model there, and it is logged instead."""
    contexts, decision, reason, blocked_err = [], None, None, []
    extra = {}
    top = {}
    blockers, malformed = [], []
    for label, rc, out, err in results:
        if rc == 2:
            blocked_err.append((err or "").strip() or ("%s blocked" % label))
        text = (out or "").strip()
        if not text:
            continue
        obj = None
        if text.startswith("{"):
            try:
                obj = json.loads(text)
            except Exception:
                obj = None
        if not isinstance(obj, dict):
            contexts.append((label, text))
            continue
        # ONE HOOK'S BAD JSON CANNOT UNDO ANOTHER'S ANSWER (29 Sep, critical-path audit D03). A hookSpecificOutput that
        # is not an object, or a permissionDecision that is not allow/ask/deny, raised here, merge() never reached the
        # exit-2 check below, and the caller's except turned the whole event into exit 0: a veto already made by an
        # earlier hook was lost to a later hook's typo. Such a hook's JSON is now dropped whole and named in the log,
        # and everything else merges as before.
        h = obj.get("hookSpecificOutput")
        d = h.get("permissionDecision") if isinstance(h, dict) else None
        if (h is not None and not isinstance(h, dict)) or (d is not None and (not isinstance(d, str) or d not in RANK)):
            malformed.append(label)
            continue
        h = h or {}
        try:
            c = h.get("additionalContext")
            if c:
                contexts.append((label, c if isinstance(c, str) else json.dumps(c)))
            if d and (decision is None or RANK[d] > RANK[decision]):
                decision, reason = d, h.get("permissionDecisionReason")
            for k, v in h.items():
                if k not in ("hookEventName", "additionalContext", "permissionDecision", "permissionDecisionReason"):
                    extra[k] = v
            _merge_top(top, obj)
            if obj.get("decision") == "block" or obj.get("continue") is False:
                blockers.append(label)
        except Exception:
            malformed.append(label)
    if malformed:
        log("%s: malformed JSON dropped from %s" % (event, ",".join(malformed)))
    if blocked_err:
        return 2, "", "\n".join(blocked_err)
    if LIST_UNREADABLE:
        contexts.insert(0, ("hook-dispatch",
                            "[hook-dispatch] the hook list could not be read, so NO hook ran for this "
                            "event: " + LIST_UNREADABLE[0] + ". The session is unharmed and unhooked; "
                            "repair that file."))
    hso = {"hookEventName": event}
    try:
        contexts = dedup_repeats(contexts, event, session)
    except Exception:
        log("dedup failed, contexts kept whole: " + _format_exc().replace("\n", " | ")[:300])
    if contexts:
        try:
            hso["additionalContext"] = fit_contexts(contexts, INLINE_CAP, event, session)
        except Exception:
            # the fit must never cost a prompt its context: fall back to the plain join the harness will then spill
            log("fit failed, plain join used: " + _format_exc().replace("\n", " | ")[:300])
            hso["additionalContext"] = "\n".join(t for _, t in contexts)
    # A decision leaves only when a hook made one. Until 29 Sep a PreToolUse event with any context and no decision was
    # given "allow" here, which skips the permission prompt: every reflex or note on a tool call approved that call
    # (critical-path audit D01). Claude Code delivers PreToolUse context without a decision (2.1.284, proved on 29 Sep
    # with a one-hook headless session: the context reached the model either way).
    if decision:
        hso["permissionDecision"] = decision
        if reason:
            hso["permissionDecisionReason"] = reason
    hso.update(extra)
    out = _finish_top(top)
    if blockers:
        log("%s: top-level block or stop from %s" % (event, ",".join(blockers)))
    if event in TOP_LEVEL_EVENTS_WITHOUT_HSO:
        if len(hso) > 1:
            log("%s: context from %s not delivered (the event defines no hookSpecificOutput): %d chars"
                % (event, ",".join(l for l, _ in contexts) or "-", len(hso.get("additionalContext") or "")))
    elif len(hso) > 1:
        out["hookSpecificOutput"] = hso
    if not out:
        return 0, "", ""
    return 0, json.dumps(out), ""


def _restrictive_fallback(results, event):
    """When merge() itself fails: keep what restricts and drop the rest (critical-path audit D03). An exit 2 blocks as
    before; else an explicit deny on PreToolUse, or a top-level block or stop, leaves as the only answer; else nothing.
    Never raises."""
    try:
        vetoes = [(err or "").strip() or ("%s blocked" % label) for label, rc, out, err in results if rc == 2]
        if vetoes:
            return 2, "", "\n".join(vetoes)
        for label, rc, out, err in results:
            try:
                obj = json.loads((out or "").strip())
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            h = obj.get("hookSpecificOutput")
            if event == "PreToolUse" and isinstance(h, dict) and h.get("permissionDecision") == "deny":
                return 0, json.dumps({"hookSpecificOutput": {"hookEventName": event, "permissionDecision": "deny",
                                                             "permissionDecisionReason": str(h.get("permissionDecisionReason")
                                                                                             or "%s denied" % label)}}), ""
            if obj.get("decision") == "block":
                return 0, json.dumps({"decision": "block", "reason": str(obj.get("reason") or "%s blocked" % label)}), ""
            if obj.get("continue") is False:
                return 0, json.dumps({"continue": False, "stopReason": str(obj.get("stopReason") or "%s stopped" % label)}), ""
    except Exception:
        pass
    return 0, "", ""


def _label_from_cmd(cmd, args=None):
    """Name the SCRIPT, not the interpreter.

    The fallback here used to be cmd[:40] + "...". Every subprocess-run hook on this box begins
    with the same interpreter, so all of them logged an identical, useless label - "python..." or
    "C:\\Python314\\python.exe...". Measured 2026-09-17: six 120-second hook TIMEOUTS sit in
    hook_dispatch.log, three of them that same day, and not one can be attributed to a hook,
    because the only field naming the culprit was the interpreter they all share. A timeout is
    exactly when the name matters: it is a two-minute stall on a single tool call.

    SEARCH ARGS TOO. The first version of this looked only at `cmd` and fixed 2 of the 12
    PreToolUse hooks; the other ten carry the interpreter in `cmd` and the script in `args`
    (cmd="python", args=["-s", "...\\instinct_prime.py"]), so they kept logging "python...".
    Ten hooks whose names were still hidden: instinct_prime, danger_mode, sourced_memory,
    compute_sense, mailbox_hook, gap_watch_hook, proof_deny_hook, project_memory_hot_hook and
    two strong-node runners.

    A bare "hook.py" is not a name either - the strong-node runners are all called that and differ
    only by a 64-char hash directory - so that case keeps a short slice of the parent directory.
    """
    toks = []
    try:
        import shlex
        toks.extend(shlex.split(cmd, posix=False))
    except Exception:
        toks.append(cmd)
    toks.extend(args or [])
    for tok in toks:
        t = str(tok).strip('"').strip("'")
        if t.lower().endswith(".py"):
            base = os.path.basename(t)
            if base.lower() in ("hook.py", "__main__.py", "main.py", "run.py"):
                parent = os.path.basename(os.path.dirname(t))
                if parent:
                    return "%s/%s" % (parent[:12], base)
            return base
    # The error-graph runner passes its path hex-encoded inside -c "...bytes.fromhex('...')...runpy...", so no token
    # ends in .py; on 29 Oct it logged 4,266 rows (70 of them timeouts) as "/Library/Frameworks/Python.framework/Ve...".
    import re
    m = re.search(r"fromhex\(\s*['\"]([0-9a-fA-F]{8,})['\"]\s*\)", cmd)
    if m:
        try:
            return os.path.basename(bytes.fromhex(m.group(1)).decode("utf-8").rstrip("/\\")) or (cmd[:40] + "...")
        except ValueError:
            pass
    return (cmd[:40] + "...")


# A SUBAGENT'S COMPLETION IS NOT HIS QUESTION (2026-09-23, round 5). When a background agent finishes, the harness hands
# its report to the parent session as a UserPromptSubmit whose prompt is a <task-notification> envelope. Counted over
# the last three days of transcripts: 699 such envelopes against ~296 prompts he typed, across 42 sessions - seven
# prompt events in ten. Every content hook below answered them as if he had asked: the math hook served an arXiv
# "memory strength" definition beside a subagent's report, danger mode announced a LIVE production action because the
# report used the word, and each of those fires was logged where the reflex and association precision numbers are
# computed. The archive and the checklist already strip the envelope (prompt_archive._NOISE_MARKERS,
# task_checklist_hook._NOISE); these hooks never did. So for a UserPromptSubmit carrying an envelope, the CONTENT hooks
# get the payload with every envelope removed - his own words, when he typed some beside it - and are skipped when
# nothing of his is left. Every other hook (the archive, the checklist, the board, compute, waste, timers, mailboxes)
# still receives the payload unchanged. HOOK_DISPATCH_ENVELOPE=0 restores the old behaviour.
CONTENT_HOOKS = frozenset({"instinct_prime.py", "fok_probe.py", "spread_recall.py", "canonical_route.py", "math_sense.py",
                           "danger_mode.py", "proof_sense.py", "movie_alert_hook.py"})
_ENVELOPE_OPEN = "<task-notification>"

# THE HEADLESS DIET (2026-09-23, from the ab30 brain-on/off token study). A headless worker - a launcher's session
# whose first prompt starts with [headless-worker], or a picture worker - is a machine session: nobody reads its
# reports, it does not push, it manages no box and it answers no scheduled task. Measured on the first ab30 brain-on
# run: of 45,227 characters of hook text, the math card that carried the answer was 1,818; slow-sense wrote 14,010,
# owner-board 2,079, push-reminder 1,889 (telling a test-taker to push to GitHub) and waste-sense 1,674. While the
# switch file HEADLESS_DIET_ON exists beside this file, the hooks below are skipped for such a session; everything
# that bears on the work itself (the math and proof hooks, the reflexes, danger and compute sense, the report guards,
# the archive) still runs. Skipping a hook cannot change what any other hook does in process. Delete the switch
# file to turn it off.
HEADLESS_DIET_SWITCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "HEADLESS_DIET_ON")
HEADLESS_SKIP = frozenset({"slow_sense.py", "waste_sense.py", "owner_board_hook.py", "push_reminder.py",
                           "movie_alert_hook.py", "work_timer.py", "summary_hook.py", "canonical_route.py",
                           "project_memory_hook.py", "project_memory_hot_hook.py"})
_HEADLESS_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".owner_prompts_hook_state")


def _headless_session(event, payload, session):
    """True when the diet is on for this session and it is a headless worker. Never raises.

    The diet is on when the switch file exists (every headless session on the machine) or when the session's own
    environment carries BRAIN_HEADLESS_DIET=1 - a launcher's opt-in for its own workers, such as a study that measures
    what the diet saves, which leaves every other session exactly as it was."""
    try:
        if not (os.path.exists(HEADLESS_DIET_SWITCH) or os.environ.get("BRAIN_HEADLESS_DIET") == "1"):
            return False
        import re as _re
        if event == "UserPromptSubmit":
            p = str(json.loads(payload).get("prompt") or "")
            if _re.match(r"^\s*(?:﻿)?(?:\[headless-worker\]|You are (?:a|the) picture maker for [^.\n]{0,40}?\bdocumentar(?:y|ies)\b)",
                         p, _re.I):
                return True
        # the prompts hook marks a session headless on its first prompt; tool and stop events read that mark
        if session and _re.match(r"^[A-Za-z0-9._-]{6,120}$", session):
            st = os.path.join(_HEADLESS_STATE, session + ".json")
            if os.path.exists(st):
                with open(st, encoding="utf-8") as f:
                    return bool(json.load(f).get("headless"))
    except Exception:
        return False
    return False


def _owner_words(payload):
    """(automated, content_payload). automated is True when the prompt carries a task-notification envelope;
    content_payload is the payload with the envelopes removed, or None when nothing of his remains. Never raises:
    any failure answers (False, payload), which is the old behaviour."""
    try:
        d = json.loads(payload)
        p = str(d.get("prompt") or "")
        if _ENVELOPE_OPEN not in p:
            return False, payload
        import re as _re
        rest = _re.sub(r"<task-notification>.*?</task-notification>", " ", p, flags=_re.S)
        if _ENVELOPE_OPEN in rest:               # an envelope cut off before its closing tag: the rest is its body
            rest = rest.split(_ENVELOPE_OPEN)[0]
        rest = rest.strip()
        if len(rest) < 3:
            return True, None
        d["prompt"] = rest
        return True, json.dumps(d)
    except Exception:
        return False, payload


def _entry_basename(cmd, args):
    """The script file an entry runs, whatever process runs it (a content hook must be recognised on either path). Until
    29 Sep this asked as_python_argv, which answers None for a hook forced onto its own process, so the two project-memory
    hooks in HEADLESS_SKIP were never recognised and never skipped (critical-path audit D04)."""
    try:
        return os.path.basename(script_argv(cmd, args)[0] or "").lower()
    except Exception:
        return ""


def _flag_file_absent(args):
    """True when an entry is wired `... --enabled <flag file>` and that file does not exist.

    The strong-node runtime hook is the one entry wired this way, and while its ENABLED file is absent it prints
    nothing and exits 0 (core._enabled_registry returns None, the advisory is empty). It is forced onto its own
    process (FORCE_SUBPROCESS), so every tool call in every session paid a process start for that silence: on
    2026-09-23 it was 2,431 of the slow PreToolUse rows between 12:00 and 21:10, 6,042 s in all, while its release
    waited for review. The day the flag file appears the hook runs again with no change here. Never raises; any
    doubt answers False and the hook runs as it always did."""
    try:
        a = [str(x).strip('"') for x in (args or [])]
        if "--enabled" in a:
            i = a.index("--enabled")
            return i + 1 < len(a) and bool(a[i + 1]) and not os.path.exists(a[i + 1])
    except Exception:
        pass
    return False


# A SAMPLED PER-HOOK PROFILE, OFF UNLESS ITS FLAG FILE IS THERE (14 Oct 2026, front R30). The log
# names a hook only when it fails or takes SLOW_HOOK_S, so what a whole event costs was never split by hook: PreToolUse
# runs ~222 hooks in 0.34 s (median, R26 harness) to 0.65-0.72 s (this log, 14 Oct 06:45), and nobody could say whether
# a few hooks carry that or a long tail does. Running the real hooks on made-up payloads to find out would write fires,
# judgments and logs into the brain, so the dispatcher measures the real calls instead. While PROFILE_FLAG exists and
# is younger than PROFILE_MAX_AGE_S, every prompt event (seconds each, ~70 an hour) and about one tool event in four
# (a byte of os.urandom: the dispatches are separate processes with no counter to share; ~1,900 PreToolUse an hour)
# appends one ASCII JSON line to PROFILE_LOG: the event, the tool, eight
# characters of the session id, the phases (module start to dispatch, of which imp_ms is the import line since R31;
# list read; the hook loop; flush and merge), the
# merged output's length, and per hook its label, milliseconds, "i" in this process or "s" its own, how many modules
# it added to sys.modules (time that is first-import cost shows here), its exit code and its output's length. Never a
# payload, a hook's text or a prompt. With the flag absent the cost is one os.stat per event; nothing is written past
# PROFILE_MAX_BYTES; nothing here raises. On: create the flag (an empty file). It lapses three hours after its mtime.
# Read the lines with primary_directive/improve_1012/fronts/R30/analyse_r30.py.
PROFILE_FLAG = os.path.expanduser("~/.claude/compute/hook_profile.on")
PROFILE_LOG = os.path.expanduser("~/.claude/compute/hook_profile.jsonl")
PROFILE_MAX_AGE_S = 3 * 3600
PROFILE_MAX_BYTES = 64 * 1024 * 1024
PROFILE_RATE_256 = 64                # sampled when os.urandom(1)[0] < this: 64 of 256, one dispatch in four
PROFILE_RATE_BY_EVENT = {"UserPromptSubmit": 256}


def _profile_gate(event):
    """[] when this dispatch is sampled (dispatch puts one row per hook in it), else None. Never raises."""
    try:
        st = os.stat(PROFILE_FLAG)
    except OSError:
        return None
    try:
        rate = PROFILE_RATE_BY_EVENT.get(event, PROFILE_RATE_256)
        if time.time() - st.st_mtime > PROFILE_MAX_AGE_S or os.urandom(1)[0] >= rate:
            return None
        try:
            if os.path.getsize(PROFILE_LOG) > PROFILE_MAX_BYTES:
                return None
        except OSError:
            pass
        return []
    except Exception:
        return None


def _profile_write(rows, event, tool_name, session, t0, t1, t2, out_chars):
    """One line for a sampled dispatch. Never raises: a line that cannot be written is not written."""
    try:
        t3 = time.perf_counter()

        def ms(s):
            return round(s * 1e3, 2)
        rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "pid": os.getpid(), "ev": str(event),
               "tool": str(tool_name or ""), "sid": str(session or "")[:8], "n": len(rows), "out": out_chars,
               "start_ms": ms(t0 - _T_IMPORT), "imp_ms": ms(_T_BODY - _T_IMPORT), "pre_ms": ms(t1 - t0),
               "loop_ms": ms(t2 - t1), "post_ms": ms(t3 - t2),
               "hooks_ms": ms(sum(r[1] for r in rows)),
               "h": [[lab, ms(el), how, mods, rc, oc] for lab, el, how, mods, rc, oc in rows]}
        line = (json.dumps(rec, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")
        with open(PROFILE_LOG, "ab") as fh:
            fh.write(line)
    except Exception:
        pass


def dispatch(event, payload, tool_name, settings_path=SETTINGS, force=False, session=""):
    results = []
    _LAST_FIT[0] = None
    t0 = time.perf_counter()
    prof = _profile_gate(event)      # None unless this dispatch is sampled (PROFILE_FLAG)
    _CODE["event"] = event if str(event).isidentifier() and os.environ.get("HOOK_CODE_CACHE", "1") != "0" else None
    automated, content_payload = False, payload
    if event == "UserPromptSubmit" and os.environ.get("HOOK_DISPATCH_ENVELOPE", "1") != "0":
        automated, content_payload = _owner_words(payload)
    content_seen = 0
    headless, diet_skipped = _headless_session(event, payload, session), 0
    flag_off = 0
    FLAG_SKIPPED[0] = 0
    try:
        mv = match_value(event, json.loads(payload))
    except Exception:
        mv = (tool_name or "") if event in _TOOL_EVENTS else None
    entries = hook_entries(event, tool_name, settings_path, value=mv)
    flag_off += FLAG_SKIPPED[0]
    t1 = time.perf_counter()
    allow_memo = {}                  # the allowlist, read once for this dispatch (as_python_argv)
    plan = []
    for cmd, args in entries:
        if headless and _entry_basename(cmd, args) in HEADLESS_SKIP:
            diet_skipped += 1
            continue
        if _flag_file_absent(args):
            flag_off += 1
            continue
        hook_payload = payload
        if automated and _entry_basename(cmd, args) in CONTENT_HOOKS:
            content_seen += 1
            if content_payload is None:
                continue
            hook_payload = content_payload
        path, argv_tail = as_python_argv(cmd, args, memo=allow_memo)
        label = (os.path.basename(path or "") or _label_from_cmd(cmd, args)).strip()
        plan.append((cmd, args, hook_payload, path, argv_tail, label))
    spawned = {}
    # A/B under the same load: while PARALLEL_AB_FLAG exists, half the profiled dispatches run the old sequential loop
    # (their rows carry "s" where the parallel ones carry "p"). Remove the flag to end the test.
    ab_seq = prof is not None and os.path.exists(PARALLEL_AB_FLAG) and os.urandom(1)[0] < 128
    if event in PARALLEL_SUBPROC_EVENTS and os.environ.get("HOOK_PARALLEL_SUBPROC", "1") != "0" and not ab_seq \
            and sum(1 for x in plan if not x[3]) > 1:
        for i, (cmd, args, hook_payload, path, argv_tail, label) in enumerate(plan):
            if not path:
                spawned[i] = _Spawned(cmd, args, hook_payload, budget_for(event))
    slots = [None] * len(plan)
    for i, (cmd, args, hook_payload, path, argv_tail, label) in enumerate(plan):
        if i in spawned:
            continue
        m0 = len(sys.modules)
        t = time.perf_counter()
        if path:
            rc, out, err = run_in_process(path, hook_payload, argv_tail)
        else:
            rc, out, err = run_subprocess(cmd, args, hook_payload, budget_for(event))
        slots[i] = (label, rc, out, err, time.perf_counter() - t, "i" if path else "s", len(sys.modules) - m0)
    for i, sp in spawned.items():
        rc, out, err, el = sp.result()
        slots[i] = (plan[i][5], rc, out, err, el, "p", 0)
    for label, rc, out, err, el, how, mods in slots:
        results.append((label, rc, out, err))
        if prof is not None:
            prof.append((label, el, how, mods, rc, len(out or "")))
        # A SLOW HOOK THAT SUCCEEDS WAS INVISIBLE. This line used to fire only on a bad return code, so
        # every per-hook row in the log was a failure - and all of them were the same 120 s timeout. The
        # cost that actually lands on the owner had no rows at all: UserPromptSubmit runs 18 hooks for a
        # MEDIAN of 12.46 s, measured over 792 invocations, and nothing said which of the eighteen owns
        # it. An event total with no breakdown is the probe's stage table before it had stages
        # [[forty_four_percent_of_the_probe_was_in_no_stage_at_all_2026_09_18]].
        # The threshold is 1.0 s against a mean of 0.69 s per hook on that event and 0.14 s on
        # PreToolUse, so a normal hook writes nothing and only real contributors appear. Success rows
        # are marked ok= so they cannot be mistaken for the failure rows already in the file.
        if rc not in (0, 2):
            log("%s %s rc=%d %.2fs %s" % (event, label, rc, el, (err or "")[:160].replace("\n", " ")))
        elif el >= SLOW_HOOK_S:
            log("%s %s ok=%d %.2fs slow" % (event, label, rc, el))
    t2 = time.perf_counter()
    _code_flush()
    try:
        code, out, err = merge(results, event, session)
    except Exception:
        log("%s merge failed, restrictive answers kept: %s" % (event, _format_exc().replace("\n", " | ")[:300]))
        code, out, err = _restrictive_fallback(results, event)
    log("%s hooks=%d %.2fs exit=%d out=%d%s%s" % (event, len(results), time.perf_counter() - t0, code, len(out),
                                                (" envelope: %d content hooks %s" % (content_seen, "skipped" if content_payload is None else "got his words only"))
                                                if automated else "",
                                                ((" headless diet: %d skipped" % diet_skipped) if diet_skipped else "")
                                                + ((" flag off: %d" % flag_off) if flag_off else "")))
    if _LAST_FIT[0]:
        total, n_hooks, n_cut, path = _LAST_FIT[0]
        log("%s FIT: %d hooks wrote %d chars; %d cut to share the %d-char inline budget; whole text in %s"
            % (event, n_hooks, total, n_cut, INLINE_CAP, path))
    if out and len(out) > HARNESS_INLINE_MAX + 200:
        big = max(results, key=lambda r: len(r[2] or ""))
        log("%s SPILL: merged output %d chars > %d - the harness persists it to a file and the model sees a 2 KB preview; largest hook %s at %d chars"
            % (event, len(out), HARNESS_INLINE_MAX, big[0], len(big[2] or "")))
    if _ALLOW_STALE[0]:
        a_m, d_m = _ALLOW_STALE[0]
        log("%s ALLOWLIST STALE - every hook spawned this event. allow=%s dispatcher=%s. "
            "Re-sign after checking the dispatcher diff cannot change in-process behaviour."
            % (event, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(a_m)),
               time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(d_m))))
    if prof is not None:
        _profile_write(prof, event, tool_name, session, t0, t1, t2, len(out or ""))
    return code, out, err, results


def _fit_selftest():
    """Controls for the inline fit: under the cap nothing changes; over it every hook keeps a share, the quiet ones
    whole, the total stays under the cap, and the whole text is on disk. Exit 0 on pass."""
    small = [("a.py", "alpha " * 20), ("b.py", "beta\n" * 30)]
    same = fit_contexts(small, INLINE_CAP, "PreToolUse", "selftest")
    ok1 = same == "\n".join(t for _, t in small)
    loud = small + [("loud.py", "\n".join("loud line %d of a very long dump" % i for i in range(3000))),
                    ("louder.py", "x" * 40000)]
    out = fit_contexts(loud, INLINE_CAP, "UserPromptSubmit", "selftest")
    ok2 = len(out) <= INLINE_CAP and out.startswith("[hook-dispatch] the hooks wrote")
    ok3 = ("alpha " * 20) in out and ("beta\n" * 30).strip() in out
    ok4 = "[loud.py:" in out and "[louder.py:" in out and "loud line 0 of" in out
    path = (_LAST_FIT[0] or (None, None, None, None))[3]
    ok5 = bool(path) and os.path.exists(path) and "==== louder.py (40000 chars) ====" in open(path, encoding="utf-8").read()
    if path:
        try:
            os.remove(path)
        except Exception:
            pass
    # the work hooks get FIT_WORK_WEIGHT shares: two equally loud texts, one a card about the work, one housekeeping
    work = "\n".join("proof check line %d" % i for i in range(2000))
    chores = "\n".join("census line %d" % i for i in range(2000))
    both = [("C:\\x\\proof_sense.py", work), ("waste_sense.py", chores)]
    out2 = fit_contexts(both, INLINE_CAP, "UserPromptSubmit", "selftest")
    kept_w = len(out2.split("\n  ... [C:\\x\\proof_sense.py:")[0])
    kept_c = len(out2.split("census line 0")[1].split("\n  ... [waste_sense.py:")[0]) if "census line 0" in out2 else 0
    ok6 = len(out2) <= INLINE_CAP and kept_c > 0 and 2.5 <= kept_w / float(kept_c) <= 3.6
    # a card longer than an equal share but inside its weighted share stays whole
    card = "\n".join("math card line %d" % i for i in range(260))          # ~5,000 chars
    three = [("math_sense.py", card), ("slow_sense.py", "s" * 6000), ("owner_board_hook.py", "b" * 6000)]
    out3 = fit_contexts(three, INLINE_CAP, "UserPromptSubmit", "selftest")
    ok7 = card in out3 and len(out3) <= INLINE_CAP
    for p in [(_LAST_FIT[0] or (None,) * 4)[3]]:
        try:
            p and os.remove(p)
        except Exception:
            pass
    for name, ok in (("under the cap the join is untouched", ok1), ("over the cap the output fits and says so", ok2),
                     ("the quiet hooks keep their whole text", ok3), ("the loud hooks are cut with a note", ok4),
                     ("the whole text is on disk", ok5),
                     ("a work card keeps about three times a chore's share", ok6),
                     ("a work card inside its weighted share stays whole", ok7)):
        print("%s %s" % ("PASS" if ok else "FAIL", name))
    return 0 if all((ok1, ok2, ok3, ok4, ok5, ok6, ok7)) else 1


def _flag_selftest():
    """Controls for _flag_file_absent: a missing flag file skips, a present one runs, no flag runs, junk never raises."""
    import tempfile
    fd, present = tempfile.mkstemp(prefix="flag_selftest_")
    os.close(fd)
    missing = present + ".absent"
    try:
        checks = [
            (_flag_file_absent(["-B", "hook.py", "--hook-runtime", "claude", "--enabled", missing]), True, "missing flag skips"),
            (_flag_file_absent(["-B", "hook.py", "--hook-runtime", "claude", "--enabled", present]), False, "present flag runs"),
            (_flag_file_absent(["-B", "hook.py", "--hook-runtime", "claude"]), False, "no flag runs"),
            (_flag_file_absent(["hook.py", "--enabled"]), False, "a dangling --enabled runs"),
            (_flag_file_absent(None), False, "no args runs"),
            (_entry_flag_off({"command": "x", "enabled_if": missing}), True, "enabled_if missing skips"),
            (_entry_flag_off({"command": "x", "enabled_if": present}), False, "enabled_if present runs"),
            (_entry_flag_off({"command": "x"}), False, "no enabled_if runs"),
            (_entry_flag_off({"command": "x", "enabled_if": ""}), False, "an empty enabled_if runs"),
            (_entry_flag_off({"command": "x", "enabled_if": 7}), False, "a non-string enabled_if runs"),
            (_flag_file_absent([None, 3, "--enabled", ""]), False, "junk never raises and runs"),
        ]
    finally:
        os.remove(present)
    bad = [name for got, want, name in checks if got != want]
    print("hook_dispatch flag selftest: %s (%d checks%s)" % ("FAIL" if bad else "PASS", len(checks),
                                                             ": " + ", ".join(bad) if bad else ""))
    return 1 if bad else 0


def _code_selftest():
    """Controls for the hook code cache on a temporary hook, in its own directory under its own event name (the live
    files and the log are not touched): a miss runs and is kept, a hit gives the same answer, an edit of the same size
    is seen, a broken cache file and HOOK_CODE_CACHE off fall back, a blocking exit blocks from a hit as from a miss,
    and a syntax error fails as before. 0 = pass."""
    global _CODE_DIR, log
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="hd-codecache-")
    saved_dir, saved_log = _CODE_DIR, log
    saved = {k: (dict(v) if isinstance(v, dict) else v) for k, v in _CODE.items()}
    hook, ev, fails, n = os.path.join(tmp, "probe_hook.py"), "SelftestCodeCache", [], [0]
    probe = 'import sys\nprint("%s|" + __name__ + "|" + str(__file__ == sys.argv[0]) + "|" + sys.stdin.read())\n'

    def put(text, mtime_ns=None):
        with open(hook, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        if mtime_ns:
            os.utime(hook, ns=(mtime_ns, mtime_ns))

    def check(label, want_rc, want_out, want_err=None, hits=None):
        n[0] += 1
        _CODE["kept"].pop(ev, None)                # every run reads the file, as a fresh dispatcher does
        h0 = _CODE["hits"]
        rc, out, err = run_in_process(hook, "x", ())
        _code_flush()
        if rc != want_rc or out.strip() != want_out or (want_err is not None and want_err not in err):
            fails.append("%s: rc %s out %r err %r" % (label, rc, out.strip(), err[-160:]))
        if hits is not None and _CODE["hits"] - h0 != hits:
            fails.append("%s: %d cache hits, expected %d" % (label, _CODE["hits"] - h0, hits))

    try:
        _CODE_DIR, log = os.path.join(tmp, "cache"), (lambda s: None)
        _CODE["event"] = ev
        put(probe % "one")
        check("first run, a miss", 0, "one|__main__|True|x", hits=0)
        check("second run, a hit", 0, "one|__main__|True|x", hits=1)
        put(probe % "two", os.stat(hook).st_mtime_ns + 1000000)
        check("an edit of the same size", 0, "two|__main__|True|x", hits=0)
        check("the edit kept, a hit", 0, "two|__main__|True|x", hits=1)
        with open(_code_file(ev), "wb") as fh:
            fh.write(b"not marshal")
        check("a broken cache file", 0, "two|__main__|True|x", hits=0)
        _CODE["event"] = None
        check("the cache off", 0, "two|__main__|True|x", hits=0)
        _CODE["event"] = ev
        put('import sys\nsys.stderr.write("held")\nsys.exit(2)\n')
        check("a blocking hook, a miss", 2, "", "held", hits=0)
        check("a blocking hook, a hit", 2, "", "held", hits=1)
        put("def broken(:\n")
        check("a syntax error", 1, "", "SyntaxError", hits=0)
    finally:
        _CODE_DIR, log = saved_dir, saved_log
        _CODE.clear()
        _CODE.update(saved)
        shutil.rmtree(tmp, ignore_errors=True)
    print("hook_dispatch code cache selftest: %s (%d checks%s)" % ("FAIL" if fails else "PASS", n[0],
                                                                   ": " + "; ".join(fails) if fails else ""))
    return 1 if fails else 0


# Windows 11 power-throttles (EcoQoS) a windowless process by default, and every hook runs windowless. A/B on a probe's
# own work in a CREATE_NO_WINDOW child, 6 alternating pairs: wall median 10.4 s default vs 6.4 s opted out, 5 of 6
# pairs faster, cpu and answers identical. The dispatcher opts itself out at start and each hook process it starts
# right after launch; children do not inherit it. Priority is untouched. HOOK_UNTHROTTLE=0 turns it off. Elsewhere
# than Windows it does nothing.
def _unthrottle(handle=None):
    if os.name != "nt" or os.environ.get("HOOK_UNTHROTTLE", "1") == "0":
        return False
    try:
        import ctypes

        class _PPT(ctypes.Structure):
            _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong), ("StateMask", ctypes.c_ulong)]

        k = ctypes.windll.kernel32
        if handle is None:
            k.GetCurrentProcess.restype = ctypes.c_void_p
            handle = k.GetCurrentProcess()
        fn = k.SetProcessInformation
        fn.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
        fn.restype = ctypes.c_int
        st = _PPT(1, 1, 0)          # PROCESS_POWER_THROTTLING_EXECUTION_SPEED in the mask, cleared in the state
        return bool(fn(ctypes.c_void_p(int(handle)), 4, ctypes.byref(st), ctypes.sizeof(st)))
    except Exception:
        return False


def main():
    _unthrottle()
    event = "PreToolUse"
    if "--selftest" in sys.argv:
        return _fit_selftest() or _flag_selftest() or _code_selftest()
    if "--event" in sys.argv:
        event = sys.argv[sys.argv.index("--event") + 1]
    # The payload is UTF-8 JSON from the harness; read it as bytes so a non-cp1252 character in his prompt cannot
    # raise here (a raised read left raw="" and the event fell back to PreToolUse). The merged output is written
    # as UTF-8 bytes for the same reason (2026-09-20).
    try:
        buf = getattr(sys.stdin, "buffer", None)
        raw = buf.read().decode("utf-8", "replace") if buf is not None else sys.stdin.read()
    except Exception:
        raw = ""
    if not os.path.exists(ENABLED) and "--force" not in sys.argv:
        return 0
    i = raw.find("{")
    payload = raw[i:] if i >= 0 else "{}"
    try:
        hook = json.loads(payload)
    except Exception:
        hook = {}
    event = hook.get("hook_event_name") or event
    code, out, err, _ = dispatch(event, payload, hook.get("tool_name", ""), session=str(hook.get("session_id") or ""))
    if out:
        _write_utf8(sys.stdout, out)
    if err:
        _write_utf8(sys.stderr, err)
    return code


def _write_utf8(stream, text):
    """The harness reads UTF-8; a cp1252 text stream would raise on the first chevron and lose every hook's context."""
    buf = getattr(stream, "buffer", None)
    if buf is not None:
        buf.write(text.encode("utf-8", "replace"))
        buf.flush()
    else:
        stream.write(text)
        stream.flush()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log("fatal: " + _format_exc().replace("\n", " | ")[:400])
        sys.exit(0)          # a broken dispatcher must never block a tool call
