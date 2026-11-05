#!/usr/bin/env python3
"""referee_hook_failsafe.py - behavioral fail-safety audit of the wired Stop hooks.

A Stop hook that throws, hangs, or emits a stray block on malformed input can wedge or disrupt a session.
The referee test: feed each wired hook GARBAGE (empty / non-JSON / {} / wrong-event) and confirm it exits
0, blocks nothing, and prints no traceback. Garbage only (never a real Stop event), so no hook's Stop path
fires - nothing mutates the shared RESUME.md / work clock / conscience / registry. Read-only + side-effect-free.
"""
import subprocess, sys, os

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


PY = sys.executable
D = os.path.expanduser(r"~/.claude/dreaming")
MESH = os.path.expanduser(r"~/brain-repo/12_cognitive_architecture/agent_mesh/mesh_hook.py")

# (label, argv) for each wired Stop hook (from settings.json)
HOOKS = [
    ("mesh_hook",          [MESH, "--runtime", "claude"]),
    ("coactivation_end",   [os.path.join(D, "coactivation_end.py")]),
    ("reality_monitor",    [os.path.join(D, "reality_monitor.py")]),
    ("pre_report_check",   [os.path.join(D, "graph_fire", "pre_report_check.py")]),
    ("conscience",         [os.path.join(D, "conscience.py")]),
    ("work_timer",         [os.path.join(D, "work_timer.py")]),
    ("resume_checkpoint",  [os.path.join(D, "resume_checkpoint.py")]),
    ("registry_refresh",   [os.path.join(D, "registry_refresh.py")]),
    ("retraction_guard",   [os.path.join(D, "retraction_guard.py")]),   # wired after the first audit
    ("claim_check",        [os.path.join(D, "claim_check.py")]),        # wired after the first audit
]

# malformed inputs - NONE is a valid Stop event, so no side-effecting Stop path runs (a Stop event, even
# with a null transcript, would make resume_checkpoint clobber the shared RESUME.md and work_timer tick
# the live clock - so those are deliberately excluded; the Stop path's fail-safety is verified by reading
# each writer's top-level try/except instead).
INPUTS = [
    ("empty",      ""),
    ("nonjson",    "not json at all {[}"),
    ("emptyobj",   "{}"),
    ("wrongevent", '{"hook_event_name":"PreToolUse","tool_name":"Bash"}'),
    ("badtypes",   '{"hook_event_name":"PreToolUse","session_id":12345,"tool_input":[1,2]}'),
]

def _one(label, argv, iname, payload, timeout=20):
    try:
        p = subprocess.run([PY, "-s"] + argv, input=payload.encode("utf-8"),
                           capture_output=True, timeout=timeout)
        rc = p.returncode
        out = p.stdout.decode("utf-8", "replace")
        err = p.stderr.decode("utf-8", "replace")
        blocked = '"decision"' in out and '"block"' in out
        tb = "Traceback (most recent call last)" in err
        # fail-safe = exit 0, no block on garbage, no uncaught traceback
        safe = (rc == 0) and (not blocked) and (not tb)
        verdict = "ok" if safe else ("BLOCK!" if blocked else ("EXIT%d" % rc if rc != 0 else "TRACE"))
        return safe, "%-20s %-12s %-6d %-7s %-9s %s" % (label, iname, rc, str(blocked), str(tb), verdict)
    except subprocess.TimeoutExpired:
        return False, "%-20s %-12s %-6s %-7s %-9s %s" % (label, iname, "-", "-", "-", "TIMEOUT/HANG")


# 2026-10-29 (Mac): the 50 hook starts ran one after another, ~26 s each on a loaded box with ~2 s of CPU in all
# (1,295 s), far past referee_suite's 90 s budget, so the invariant read as FAILED while every hook was fail-safe.
# The runs are independent and read-only, so they go 8 at a time; rows print in the original order.
from concurrent.futures import ThreadPoolExecutor
print("%-20s %-12s %-6s %-7s %-9s %s" % ("hook", "input", "exit", "blocked", "traceback", "verdict"))
jobs = [(label, argv, iname, payload) for label, argv in HOOKS for iname, payload in INPUTS]
with ThreadPoolExecutor(max_workers=8) as ex:
    results = list(ex.map(lambda j: _one(*j), jobs))
# 29 Oct 19:3x (Mac, load ~140-260): eight parallel starts pushed hooks that exit in 0.2 s alone past 20 s, and a
# different scattered set "hung" on each run (mesh_hook 5/5 one run, 0/5 the next). A timeout is re-run once, alone,
# with 60 s: a real hang still times out and still fails; a slow start under load does not.
for i, (safe, row) in enumerate(results):
    if row.endswith("TIMEOUT/HANG"):
        safe2, row2 = _one(*jobs[i], timeout=60)
        results[i] = (safe2, row2 + ("  (retried alone)" if safe2 else ""))
allsafe = all(safe for safe, _ in results)
for _, row in results:
    print(row)
print("\nVERDICT:", "ALL wired Stop hooks are fail-safe on malformed input" if allsafe
      else "AT LEAST ONE hook is NOT fail-safe (see rows above) - a garbage/edge input crashes/blocks/hangs it")
