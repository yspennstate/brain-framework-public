#!/usr/bin/env python3
"""test_handover.py - the /clear handover: does the RIGHT readme come back, and for a structural reason?

Two generations of bug live in here as executable controls.

The first, measured 2026-07-26: /clear ends the session and starts a new one, so the reseed hook is handed
an id that has never filed anything, session_file() cannot hit, and the original code fell through to the
machine-wide LAST_JOB.md every time. old_algorithm() reproduces that and the suite asserts it serves the
WRONG agent on the same fixture where the pointer serves the right one.

The second is the reason this file was rewritten. The pointer ladder still ended in two TEMPORAL rungs -
`recency` (newest readme anyone filed) and the shared LAST_JOB.md - and when the clear was finally tested
against the live machine the nearest rival pointer landed in the SAME MINUTE as ours. The pid rung won, so
the answer was right, but one rung down it was a coin flip. Owner: "can you make it that this does not work
with timing, it works with how the terminal are built?"

So the tests that matter now are the ones that come out DIFFERENT if time is consulted at all:

  test_recycled_pid_is_not_my_predecessor   a pid alone is not an identity - it gets reused
  test_no_structural_match_serves_nothing   an unresolved match must return nothing, not the newest
  test_newer_rival_is_never_served          the live scenario: rival is NEWER and still must not win
  test_ancient_pointer_with_live_process    the inverse of the old six-hour TTL: age must not disqualify
  test_terminal_app_is_not_a_lane           every tab shares WindowsTerminal.exe - matching there is wrong

    python test_handover.py

Runs against a temp JOB_CYCLE_STATE and a FAKE process tree. It must never touch the live state dir; a
fixture that writes the real LAST_JOB.md is how the fleet lost RESUME.md on 07-09.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RESEED = os.path.join(HERE, "job_reseed.py")
READMECLI = os.path.join(HERE, "job_readme.py")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s%s" % ("ok  " if cond else "FAIL", name, ("  <- " + str(detail)) if (detail and not cond) else ""))


# ---------------------------------------------------------------- the fake machine
#
# Measured shape of the real thing, 2026-07-26, six live claude.exe on this box: one WindowsTerminal.exe
# over many powershell.exe, one claude.exe under each. The terminal application is SHARED and therefore
# useless as an identity; the host shell is not.
#
#   explorer 14644
#     WindowsTerminal 900                  <- shared by every tab
#       powershell 1001 -> claude 1000     <- lane A (mine)
#       powershell 1002 -> claude 9999     <- lane B (a rival agent)

LANE_A_CLAUDE, LANE_A_HOST = 1000, 1001
LANE_B_CLAUDE, LANE_B_HOST = 9999, 1002


def tree(over=None):
    """The fake process table. `over` is a dict of pid -> (ppid, name, created) to add or replace.

    Takes a dict rather than **kwargs on purpose: pids are ints, and ** unpacking demands string keys.
    """
    t = {14644: (0, "explorer.exe", 100),
         900: (14644, "WindowsTerminal.exe", 900000),
         LANE_A_HOST: (900, "powershell.exe", 1001000),
         LANE_A_CLAUDE: (LANE_A_HOST, "claude.exe", 111000),
         LANE_B_HOST: (900, "powershell.exe", 1002000),
         LANE_B_CLAUDE: (LANE_B_HOST, "claude.exe", 999000)}
    for k, v in (over or {}).items():
        t[int(k)] = v
    return t


def be(J, pid, table=None):
    """Become the session hosted by `pid`, on the given fake process tree."""
    J.P.TABLE = tree() if table is None else table
    os.environ["CLAUDE_PID"] = str(pid)
    os.environ.pop("JOB_PROC_TABLE", None)


def fresh_state():
    d = tempfile.mkdtemp(prefix="jobcycle_test_")
    os.environ["JOB_CYCLE_STATE"] = d
    for m in ("job_common", "job_readme", "job_reseed", "job_proc"):
        sys.modules.pop(m, None)
    sys.path.insert(0, HERE)
    import job_common
    job_common.P.TABLE = None
    return d, job_common


def put_readme(J, session, text):
    J._ensure(J.PERSESSION)
    p = J.session_file(session)
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write("# t\nslug: t\nsession: %s\nfiled_at: %.3f\n\n%s\n" % (session, time.time(), text))
    with io.open(J.LAST_JOB, "w", encoding="utf-8", newline="\n") as f:
        f.write("# t\nslug: t\nsession: %s\n\n%s\n" % (session, text))
    return p


def run_hook(state, payload, pid=None, table=None, script=RESEED):
    env = dict(os.environ)
    env["JOB_CYCLE_STATE"] = state
    if pid is None:
        env.pop("CLAUDE_PID", None)
    else:
        env["CLAUDE_PID"] = str(pid)
    tbl = tree() if table is None else table
    env["JOB_PROC_TABLE"] = json.dumps({str(k): list(v) for k, v in tbl.items()})
    r = subprocess.run([sys.executable, script], input=json.dumps(payload).encode("utf-8"),
                       capture_output=True, env=env, timeout=60)
    out = (r.stdout or b"").decode("utf-8", "replace").strip()
    if not out:
        return r.returncode, None
    try:
        return r.returncode, json.loads(out)
    except Exception:
        return r.returncode, {"_unparsed": out}


def ctx_of(obj):
    if not isinstance(obj, dict):
        return ""
    return ((obj.get("hookSpecificOutput") or {}).get("additionalContext")) or ""


def old_algorithm(J, session):
    """What the code did before the pointer: own session file, else the shared newest-from-anyone.

    Kept as an executable negative control. A regression test that passes against the buggy code it was
    written for proves nothing, and this is the one test that has to bite.
    """
    p = J.session_file(session)
    return p if os.path.exists(p) else (J.LAST_JOB if os.path.exists(J.LAST_JOB) else None)


# ---------------------------------------------------------------- the fixture itself

def test_fixture_is_shaped_like_the_real_machine():
    """Positive control on the fake tree. An absence proved with a broken detector is worth nothing, so
    check the fixture really contains the two things the design turns on before trusting any negative."""
    st, J = fresh_state()
    be(J, LANE_A_CLAUDE)
    t = tree()
    check("fixture: the two lanes have DIFFERENT host shells",
          t[LANE_A_CLAUDE][0] != t[LANE_B_CLAUDE][0], (t[LANE_A_CLAUDE], t[LANE_B_CLAUDE]))
    check("fixture: both lanes SHARE the terminal application",
          t[t[LANE_A_CLAUDE][0]][0] == t[t[LANE_B_CLAUDE][0]][0] == 900)
    ident = J.P.identity()
    check("identity finds my claude.exe", ident["claude_pid"] == LANE_A_CLAUDE, ident)
    check("identity records its creation time", ident["claude_started"] == 111000, ident)
    check("identity finds my host shell", ident["host_pid"] == LANE_A_HOST, ident)
    check("identity does NOT record the terminal app as the lane", ident["host_pid"] != 900, ident)
    shutil.rmtree(st, ignore_errors=True)


def test_resolve_claude_without_the_env_var():
    """CLAUDE_PID is undocumented, so the ladder must not depend on it. Walking our own parents finds
    claude.exe with no environment at all - which is structural in the strictest sense."""
    st, J = fresh_state()
    t = tree()
    t[os.getpid()] = (LANE_A_CLAUDE, "python.exe", 7)     # this test process, as a hook child would be
    J.P.TABLE = t
    os.environ.pop("CLAUDE_PID", None)
    pid, started, how = J.P.resolve_claude()
    check("claude found by ancestry with no CLAUDE_PID", pid == LANE_A_CLAUDE, (pid, how))
    check("ancestry route is labelled", how == "ancestry", how)
    check("creation time still recorded", started == 111000, started)
    shutil.rmtree(st, ignore_errors=True)


def test_env_var_naming_a_non_claude_pid_is_refused():
    """An unconfirmed env var is just a number. If CLAUDE_PID points at something that is not a live
    claude.exe, it must not become an identity - that is how a recycled pid would sail through."""
    st, J = fresh_state()
    J.P.TABLE = tree()
    os.environ["CLAUDE_PID"] = str(LANE_A_HOST)           # powershell.exe, not claude.exe
    pid, _s, how = J.P.resolve_claude()
    check("env var pointing at a non-claude process is rejected", pid != LANE_A_HOST, (pid, how))
    shutil.rmtree(st, ignore_errors=True)


# ---------------------------------------------------------------- unit: the pointer

def test_pointer_carries_the_structure():
    st, J = fresh_state()
    be(J, LANE_A_CLAUDE)
    r = put_readme(J, "sess-A", "A body")
    p = J.write_pointer("sess-A", r, transcript="/x/projects/projA/sess-A.jsonl", title="A")
    check("pointer written", bool(p) and os.path.exists(p))
    d = json.load(io.open(p, encoding="utf-8"))
    check("pointer carries the claude pid", d.get("claude_pid") == LANE_A_CLAUDE, d)
    check("pointer carries the creation time", d.get("claude_started") == 111000, d)
    check("pointer carries the host shell", d.get("host_pid") == LANE_A_HOST, d)
    check("pointer carries the readme path", d.get("readme") == r)
    shutil.rmtree(st, ignore_errors=True)


def test_process_match_beats_a_newer_stranger():
    """THE original bug. A parallel agent files a NEWER readme; the cleared session must still get its own.

    The stranger overwrites LAST_JOB.md here because that is what really happens - job_readme.py write
    rewrites the shared file every time anyone files, which is how the novel session took it over within a
    minute of the cycle going live on 07-26.
    """
    st, J = fresh_state()
    be(J, LANE_A_CLAUDE)
    mine = put_readme(J, "pred", "PREDECESSOR BODY")
    J.write_pointer("pred", mine, transcript="/x/projects/projA/pred.jsonl")
    time.sleep(0.02)
    be(J, LANE_B_CLAUDE)                       # a different claude.exe in another tab = another agent
    other = put_readme(J, "novel", "NOVEL BODY")   # also clobbers LAST_JOB.md, as the real CLI does
    J.write_pointer("novel", other, transcript="/x/projects/novel/novel.jsonl")

    # Negative control: the pre-pointer code hands the cleared session the stranger's readme.
    old = old_algorithm(J, "brand-new-id")
    check("CONTROL: old algorithm serves the wrong agent",
          old == J.LAST_JOB and "NOVEL BODY" in io.open(old, encoding="utf-8").read(), old)

    be(J, LANE_A_CLAUDE)                       # the clear: same process, new session id
    path, info = J.take_pointer("brand-new-id", transcript="/x/projects/projA/brand-new-id.jsonl")
    check("process rule fires", info.get("rule") == "process", info)
    check("predecessor readme served, not the newer stranger",
          path == mine and "PREDECESSOR" in io.open(path, encoding="utf-8").read(), path)
    shutil.rmtree(st, ignore_errors=True)


def test_recycled_pid_is_not_my_predecessor():
    """A pid on its own is not an identity: Windows reuses them, and pointers outlive processes.

    This is the case the old six-hour TTL was really guarding, badly - a timer cannot tell a recycled pid
    from a live one. Same number, different process: it must not be treated as a predecessor, and the
    pointer should be collected rather than left to mislead someone later.
    """
    st, J = fresh_state()
    be(J, LANE_A_CLAUDE)
    r = put_readme(J, "pred", "BODY FROM THE PROCESS THAT DIED")
    p = J.write_pointer("pred", r, transcript="/x/projects/projA/pred.jsonl")
    d = json.load(io.open(p, encoding="utf-8"))
    check("setup: pointer recorded the original creation time", d.get("claude_started") == 111000, d)

    # Same pid 1000, but a brand new claude.exe now owns it.
    recycled = tree({LANE_A_CLAUDE: (LANE_A_HOST, "claude.exe", 555555)})
    be(J, LANE_A_CLAUDE, table=recycled)
    path, info = J.take_pointer("brand-new-id", transcript="/x/projects/projA/brand-new-id.jsonl")
    check("recycled pid does NOT match on the pid alone", path is None, (path, info))
    check("and it is reported absent, not guessed", info.get("rule") == "absent", info)
    check("the dead process's pointer is collected", not os.path.exists(p))
    shutil.rmtree(st, ignore_errors=True)


def test_no_structural_match_serves_nothing():
    """The rung that used to be `recency`. A rival's pointer is waiting and nothing of mine is: the answer
    must be nothing, said out loud - not the newest readme on the machine."""
    st, J = fresh_state()
    be(J, LANE_B_CLAUDE)
    rival = put_readme(J, "rival", "RIVAL BODY - another agent's work")
    J.write_pointer("rival", rival, transcript="/x/projects/projA/rival.jsonl")

    old = old_algorithm(J, "brand-new-id")
    check("CONTROL: old algorithm would serve the rival",
          old and "RIVAL BODY" in io.open(old, encoding="utf-8").read(), old)

    be(J, LANE_A_CLAUDE)
    path, info = J.take_pointer("brand-new-id", transcript="/x/projects/projA/brand-new-id.jsonl")
    check("no structural match -> nothing served", path is None, path)
    check("reported as absent", info.get("rule") == "absent", info)
    check("says how many were waiting", info.get("candidates") == 1, info)
    check("the rival's pointer is left for its own session", os.path.exists(J.pointer_file("rival")))
    shutil.rmtree(st, ignore_errors=True)


def test_newer_rival_is_never_served():
    """The live 07-26 scenario, made deterministic: the rival files LATER than my predecessor, in the SAME
    project folder. Under the old ladder that was a coin flip decided by mtime; here it is not a factor."""
    st, J = fresh_state()
    be(J, LANE_A_CLAUDE)
    mine = put_readme(J, "pred", "MY PREDECESSOR")
    pm = J.write_pointer("pred", mine, transcript="/x/projects/projA/pred.jsonl")
    be(J, LANE_B_CLAUDE)
    rival = put_readme(J, "rival", "RIVAL - SAME FOLDER, FILED LATER")
    pr = J.write_pointer("rival", rival, transcript="/x/projects/projA/rival.jsonl")

    a = json.load(io.open(pm, encoding="utf-8"))
    b = json.load(io.open(pr, encoding="utf-8"))
    check("setup: the rival really is the newer one", b["filed_at"] > a["filed_at"], (a, b))
    check("setup: both are in the same project folder", a["project"] == b["project"], (a, b))

    be(J, LANE_A_CLAUDE)
    path, info = J.take_pointer("brand-new-id", transcript="/x/projects/projA/brand-new-id.jsonl")
    check("the newer rival does not win", path == mine, (path, info))
    check("matched on the process, not the clock", info.get("rule") == "process", info)
    shutil.rmtree(st, ignore_errors=True)


def test_ancient_pointer_with_live_process_still_serves():
    """The exact inverse of the retired POINTER_TTL. Thirty days old, process still alive: still mine.

    Age was never the question. If the claude.exe that wrote the pointer is still running, a clear inside
    it is still a legitimate handover however long the owner left the session sitting there.
    """
    st, J = fresh_state()
    be(J, LANE_A_CLAUDE)
    r = put_readme(J, "pred", "OLD BUT STILL MINE")
    p = J.write_pointer("pred", r, transcript="/x/projects/projA/pred.jsonl")
    d = json.load(io.open(p, encoding="utf-8"))
    d["filed_at"] = time.time() - 30 * 86400
    J._json_dump(p, d)
    path, info = J.take_pointer("brand-new-id", transcript="/x/projects/projA/brand-new-id.jsonl")
    check("a 30-day-old pointer is still served while its process lives", path == r, (path, info))
    check("still the process rule", info.get("rule") == "process", info)
    shutil.rmtree(st, ignore_errors=True)


def test_dead_process_pointer_is_collected():
    """Liveness replaces the timer: the claude.exe is gone from the tree, so nobody can claim this."""
    st, J = fresh_state()
    be(J, LANE_A_CLAUDE)
    r = put_readme(J, "pred", "FROM A DEAD PROCESS")
    p = J.write_pointer("pred", r, transcript="/x/projects/projA/pred.jsonl")
    gone = tree()
    del gone[LANE_A_CLAUDE]                              # that claude.exe exited
    gone[LANE_B_CLAUDE] = (LANE_B_HOST, "claude.exe", 999000)
    be(J, LANE_B_CLAUDE, table=gone)
    path, info = J.take_pointer("new", transcript="/x/projects/projA/new.jsonl")
    check("dead process's pointer is not served", path is None, (path, info))
    check("dead process's pointer is deleted", not os.path.exists(p))
    shutil.rmtree(st, ignore_errors=True)


def test_terminal_rung_rescues_a_pointer_with_no_creation_time():
    """What the terminal rung is actually FOR: the process is alive but unprovable.

    If GetProcessTimes could not be read when the pointer was written, claude_started is 0, so
    same_process() refuses to claim a match it cannot demonstrate. The host shell still identifies the lane,
    so the handover survives without anybody pretending to more certainty than they have.
    """
    st, J = fresh_state()
    be(J, LANE_A_CLAUDE)
    r = put_readme(J, "pred", "PREVIOUS RUN IN THIS TAB")
    p = J.write_pointer("pred", r, transcript="/x/projects/projA/pred.jsonl")
    d = json.load(io.open(p, encoding="utf-8"))
    d["claude_started"] = 0                              # creation time was unreadable at write time
    J._json_dump(p, d)
    path, info = J.take_pointer("new", transcript="/x/projects/projA/new.jsonl")
    check("an unprovable process does not claim the exact rung", info.get("rule") != "process", info)
    check("terminal rule rescues it", info.get("rule") == "terminal", info)
    check("terminal rule serves the lane's readme", path == r, path)
    shutil.rmtree(st, ignore_errors=True)


def test_relaunch_after_the_process_died_gets_nothing():
    """The conservative half of the same rule, and it is deliberate rather than an oversight.

    A /clear happens INSIDE a live claude.exe - the reseed hook only sees source=clear from a process that
    is still running. So if claude.exe exited and was relaunched in the same tab, that is a startup, not a
    handover, and the dead process's pointer is collected rather than served to its successor. Liveness
    beats the terminal rung here on purpose: the alternative is handing a fresh session a readme from a
    conversation that ended, which is the same misinformation this rewrite removed one rung up.
    """
    st, J = fresh_state()
    be(J, LANE_A_CLAUDE)
    r = put_readme(J, "pred", "FROM A SESSION THAT ENDED")
    p = J.write_pointer("pred", r, transcript="/x/projects/projA/pred.jsonl")
    relaunched = tree({1234: (LANE_A_HOST, "claude.exe", 333000)})
    del relaunched[LANE_A_CLAUDE]                        # the old claude exited; a new one took its place
    be(J, 1234, table=relaunched)
    path, info = J.take_pointer("new", transcript="/x/projects/projA/new.jsonl")
    check("a relaunch is not handed the dead session's readme", path is None, (path, info))
    check("reported absent", info.get("rule") == "absent", info)
    check("the dead process's pointer is collected", not os.path.exists(p))
    shutil.rmtree(st, ignore_errors=True)


def test_terminal_app_is_not_a_lane():
    """Both tabs hang off ONE WindowsTerminal.exe. Matching there would unify every session on the box."""
    st, J = fresh_state()
    be(J, LANE_B_CLAUDE)
    rival = put_readme(J, "rival", "OTHER TAB")
    J.write_pointer("rival", rival, transcript="/x/projects/projA/rival.jsonl")
    be(J, LANE_A_CLAUDE)
    path, info = J.take_pointer("new", transcript="/x/projects/projA/new.jsonl")
    check("a sibling tab under the same terminal app is NOT my lane", path is None, (path, info))
    check("reported absent", info.get("rule") == "absent", info)
    shutil.rmtree(st, ignore_errors=True)


def test_write_pointer_clears_a_same_lane_duplicate():
    """One live claude.exe has exactly one handover to give. Enforcing that at write time is what lets
    take_pointer() refuse to tie-break - and a tie-break is where a clock would sneak back in."""
    st, J = fresh_state()
    be(J, LANE_A_CLAUDE)
    a = put_readme(J, "first", "FIRST")
    p1 = J.write_pointer("first", a, transcript="/x/projects/projA/first.jsonl")
    b = put_readme(J, "second", "SECOND")
    p2 = J.write_pointer("second", b, transcript="/x/projects/projA/second.jsonl")
    check("the earlier same-process pointer is removed", not os.path.exists(p1), p1)
    check("the new one stands", os.path.exists(p2))
    shutil.rmtree(st, ignore_errors=True)


def test_ambiguous_lane_refuses_to_guess():
    """If two pointers ever claim one lane, say so. Picking the newer is exactly the habit being removed."""
    st, J = fresh_state()
    be(J, LANE_A_CLAUDE)
    a = put_readme(J, "one", "ONE")
    b = put_readme(J, "two", "TWO")
    for sess, readme in (("one", a), ("two", b)):
        J._ensure(J.PENDING)
        J._json_dump(J.pointer_file(sess),
                     {"session": sess, "readme": readme, "project": "projA", "filed_at": time.time(),
                      "claude_pid": LANE_A_CLAUDE, "claude_started": 111000,
                      "host_pid": LANE_A_HOST, "host_started": 1001000})
    path, info = J.take_pointer("new", transcript="/x/projects/projA/new.jsonl")
    check("two pointers on one lane -> nothing served", path is None, path)
    check("and it is called ambiguous", info.get("rule") == "ambiguous", info)
    shutil.rmtree(st, ignore_errors=True)


def test_consume_once():
    st, J = fresh_state()
    be(J, LANE_A_CLAUDE)
    r = put_readme(J, "pred", "body")
    J.write_pointer("pred", r, transcript="/x/projects/projA/pred.jsonl")
    p1, i1 = J.take_pointer("new1", transcript="/x/projects/projA/new1.jsonl")
    p2, i2 = J.take_pointer("new2", transcript="/x/projects/projA/new2.jsonl")
    check("first take gets it", p1 == r, i1)
    check("second take gets nothing (consumed once)", p2 is None, (p2, i2))
    shutil.rmtree(st, ignore_errors=True)


def test_compact_does_not_consume():
    st, J = fresh_state()
    be(J, LANE_A_CLAUDE)
    r = put_readme(J, "pred", "body")
    J.write_pointer("pred", r, transcript="/x/projects/projA/pred.jsonl")
    J.take_pointer("new1", transcript="/x/projects/projA/new1.jsonl", consume=False)
    p2, _ = J.take_pointer("new2", transcript="/x/projects/projA/new2.jsonl")
    check("consume=False leaves the pointer for the real clear", p2 == r, p2)
    shutil.rmtree(st, ignore_errors=True)


def test_never_serves_my_own():
    st, J = fresh_state()
    be(J, LANE_A_CLAUDE)
    r = put_readme(J, "me", "body")
    J.write_pointer("me", r, transcript="/x/projects/projA/me.jsonl")
    p, _ = J.take_pointer("me", transcript="/x/projects/projA/me.jsonl")
    check("a session is never handed its own pointer", p is None, p)
    shutil.rmtree(st, ignore_errors=True)


def test_pointer_to_missing_readme_ignored():
    st, J = fresh_state()
    be(J, LANE_A_CLAUDE)
    r = put_readme(J, "gone", "body")
    J.write_pointer("gone", r, transcript="/x/projects/projA/gone.jsonl")
    os.remove(r)
    path, _ = J.take_pointer("new", transcript="/x/projects/projA/new.jsonl")
    check("pointer to a vanished readme is ignored", path is None, path)
    shutil.rmtree(st, ignore_errors=True)


# ---------------------------------------------------------------- end to end: the hook

def test_hook_clear_serves_predecessor():
    st, J = fresh_state()
    be(J, LANE_A_CLAUDE)
    mine = put_readme(J, "pred", "PREDECESSOR BODY")
    J.write_pointer("pred", mine, transcript="/x/projects/projA/pred.jsonl")
    be(J, LANE_B_CLAUDE)
    other = os.path.join(J.PERSESSION, "novel.md")
    with io.open(other, "w", encoding="utf-8", newline="\n") as f:
        f.write("NOVEL BODY\n")
    J.write_pointer("novel", other, transcript="/x/projects/novel/novel.jsonl")

    rc, obj = run_hook(st, {"source": "clear", "session_id": "brand-new",
                            "transcript_path": "/x/projects/projA/brand-new.jsonl"}, pid=LANE_A_CLAUDE)
    ctx = ctx_of(obj)
    check("hook exits 0 on clear", rc == 0, rc)
    check("hook injects additionalContext", bool(ctx), obj)
    check("hook serves the predecessor body", "PREDECESSOR BODY" in ctx, ctx[:200])
    check("hook does NOT serve the other agent", "NOVEL BODY" not in ctx)
    check("hook names the process match", "claude.exe process itself" in ctx, ctx[:400])
    check("hook names the pid it matched", str(LANE_A_CLAUDE) in ctx, ctx[:400])
    shutil.rmtree(st, ignore_errors=True)


def test_hook_says_so_when_nothing_matches():
    """The replacement for the borrow. A rival pointer is waiting; the hook must report an empty handover
    rather than inject it, and must not be silent - silence is indistinguishable from a dead hook."""
    st, J = fresh_state()
    be(J, LANE_B_CLAUDE)
    rival = put_readme(J, "rival", "RIVAL BODY")
    J.write_pointer("rival", rival, transcript="/x/projects/projA/rival.jsonl")
    rc, obj = run_hook(st, {"source": "clear", "session_id": "brand-new",
                            "transcript_path": "/x/projects/projA/brand-new.jsonl"}, pid=LANE_A_CLAUDE)
    ctx = ctx_of(obj)
    check("hook exits 0", rc == 0, rc)
    check("hook does NOT inject the rival's readme", "RIVAL BODY" not in ctx, ctx[:300])
    check("hook says no handover was available", "No handover was available" in ctx, ctx[:300])
    check("hook explains it does not match on recency", "most recently" in ctx, ctx[:400])
    check("hook reports why", "none from this claude.exe" in ctx, ctx[:400])
    shutil.rmtree(st, ignore_errors=True)


def test_hook_compact_uses_own_and_keeps_pointer():
    st, J = fresh_state()
    be(J, LANE_A_CLAUDE)
    own = put_readme(J, "sameid", "OWN BODY")
    other = os.path.join(J.PERSESSION, "pred.md")
    with io.open(other, "w", encoding="utf-8", newline="\n") as f:
        f.write("PRED BODY\n")
    p = J.write_pointer("pred", other, transcript="/x/projects/projA/pred.jsonl")
    rc, obj = run_hook(st, {"source": "compact", "session_id": "sameid",
                            "transcript_path": "/x/projects/projA/sameid.jsonl"}, pid=LANE_A_CLAUDE)
    ctx = ctx_of(obj)
    check("compact serves the session's own readme", "OWN BODY" in ctx, ctx[:200])
    check("compact adds no handover caveat", "Handover from session" not in ctx)
    check("compact leaves the pointer alone", os.path.exists(p))
    check("own readme still on disk", os.path.exists(own))
    shutil.rmtree(st, ignore_errors=True)


def test_hook_silent_on_startup():
    st, J = fresh_state()
    be(J, LANE_A_CLAUDE)
    r = put_readme(J, "pred", "BODY")
    J.write_pointer("pred", r, transcript="/x/projects/projA/pred.jsonl")
    rc, obj = run_hook(st, {"source": "startup", "session_id": "new",
                            "transcript_path": "/x/projects/projA/new.jsonl"}, pid=LANE_A_CLAUDE)
    check("startup injects nothing", obj is None, obj)
    check("startup exits 0", rc == 0, rc)
    shutil.rmtree(st, ignore_errors=True)


def test_hook_survives_bom_payload():
    """The 07-26 bug: a BOM made json.load raise, the fail-safe swallowed it, the hook died silently."""
    st, J = fresh_state()
    be(J, LANE_A_CLAUDE)
    r = put_readme(J, "pred", "BOM BODY")
    J.write_pointer("pred", r, transcript="/x/projects/projA/pred.jsonl")
    env = dict(os.environ)
    env["JOB_CYCLE_STATE"] = st
    env["CLAUDE_PID"] = str(LANE_A_CLAUDE)
    env["JOB_PROC_TABLE"] = json.dumps({str(k): list(v) for k, v in tree().items()})
    payload = json.dumps({"source": "clear", "session_id": "new",
                          "transcript_path": "/x/projects/projA/new.jsonl"})
    raw = b"\xef\xbb\xbf" + payload.encode("utf-8")
    r2 = subprocess.run([sys.executable, RESEED], input=raw, capture_output=True, env=env, timeout=60)
    out = (r2.stdout or b"").decode("utf-8", "replace")
    check("BOM payload still produces the injection", "BOM BODY" in out, out[:200])
    shutil.rmtree(st, ignore_errors=True)


def test_hook_with_no_state_reports_absence():
    """Empty state on a clear: nothing to hand over, and the hook says that instead of going quiet."""
    st, _J = fresh_state()
    empty = tempfile.mkdtemp(prefix="jobcycle_empty_")
    rc, obj = run_hook(empty, {"source": "clear", "session_id": "new",
                               "transcript_path": "/x/projects/projA/new.jsonl"}, pid=LANE_A_CLAUDE)
    ctx = ctx_of(obj)
    check("no state -> exit 0", rc == 0, rc)
    check("no state -> says no handover was available", "No handover was available" in ctx, ctx[:200])
    check("no state -> names the reason", "no handover pointer is waiting" in ctx, ctx[:300])
    shutil.rmtree(empty, ignore_errors=True)
    shutil.rmtree(st, ignore_errors=True)


def test_cli_write_creates_pointer():
    st, J = fresh_state()
    body = os.path.join(st, "body.md")
    with io.open(body, "w", encoding="utf-8", newline="\n") as f:
        f.write("did a thing\n")
    J._json_dump(J.CURRENT, {"session": "cli-sess", "ask": "do it",
                             "transcript": "/x/projects/projA/cli-sess.jsonl"})
    env = dict(os.environ)
    env["JOB_CYCLE_STATE"] = st
    env["CLAUDE_PID"] = str(LANE_A_CLAUDE)
    env["JOB_PROC_TABLE"] = json.dumps({str(k): list(v) for k, v in tree().items()})
    r = subprocess.run([sys.executable, READMECLI, "write", "--title", "T", "--body-file", body],
                       capture_output=True, text=True, env=env, timeout=120)
    try:
        got = json.loads(r.stdout)
    except Exception:
        got = {"_raw": r.stdout, "_err": r.stderr}
    check("write reports the pointer", bool(got.get("pointer")), got)
    check("write records the resolved claude pid", got.get("claude_pid") == LANE_A_CLAUDE, got)
    check("write records the creation time", got.get("claude_started") == 111000, got)
    check("write records the host shell", got.get("host_pid") == LANE_A_HOST, got)
    check("pointer file is really there", bool(got.get("pointer")) and os.path.exists(got["pointer"]))
    shutil.rmtree(st, ignore_errors=True)


def test_identity_from_env_not_shared_current():
    """The shared current.json race, caught live 2026-07-26 by the pid field itself.

    A neighbouring session's Stop hook overwrote current.json between this session's hook writing it and
    the CLI reading it, so the readme filed under 05bac1b4 - clobbering the novel session's handover and
    leaving a pointer claiming this session's pid for its work. Identity must come from the environment.
    """
    st, J = fresh_state()
    rival_readme = put_readme(J, "rival-session", "RIVAL OWN HANDOVER - must survive")
    # the shared file says the rival stopped most recently, which is exactly the race
    J._json_dump(J.CURRENT, {"session": "rival-session", "ask": "rival ask",
                             "transcript": "/x/projects/projA/rival-session.jsonl"})
    body = os.path.join(st, "b.md")
    with io.open(body, "w", encoding="utf-8", newline="\n") as f:
        f.write("MY WORK\n")
    env = dict(os.environ)
    env["JOB_CYCLE_STATE"] = st
    env["CLAUDE_CODE_SESSION_ID"] = "my-session"
    env["CLAUDE_PID"] = str(LANE_A_CLAUDE)
    env["JOB_PROC_TABLE"] = json.dumps({str(k): list(v) for k, v in tree().items()})
    r = subprocess.run([sys.executable, READMECLI, "write", "--title", "T", "--body-file", body],
                       capture_output=True, text=True, env=env, timeout=120)
    try:
        got = json.loads(r.stdout)
    except Exception:
        got = {"_raw": r.stdout, "_err": r.stderr}
    check("filed under MY session, not the shared file's", got.get("session") == "my-session", got)
    check("pointer is mine", "my-session" in (got.get("pointer") or ""), got)
    check("rival's own handover is untouched",
          "RIVAL OWN HANDOVER" in io.open(rival_readme, encoding="utf-8").read())
    check("no pointer forged for the rival", not os.path.exists(J.pointer_file("rival-session")))
    check("rival's ask is not borrowed into my readme",
          "rival ask" not in io.open(J.session_file("my-session"), encoding="utf-8").read())
    shutil.rmtree(st, ignore_errors=True)


def test_no_timing_left_in_the_match():
    """Source-level guard: no clock may take part in the DECISION inside take_pointer().

    A behavioural test can pass while a clock quietly survives as a tie-break, which is how the previous
    version drifted, so read the function too. Carrying filed_at into the reported info is fine and even
    useful - a human reading the handover wants to know when it was filed - so the check is not "the token
    is absent" but "no time value reaches a comparison, sort or selection".
    """
    st, J = fresh_state()
    src = io.open(os.path.join(HERE, "job_common.py"), encoding="utf-8").read()
    body = src.split("def take_pointer(")[1].split("\ndef ")[0]
    code = "\n".join(ln for ln in body.split("\n") if not ln.strip().startswith("#"))
    code = code.split('"""')[0] + '"""'.join(code.split('"""')[2:])   # drop the docstring

    # Tokens that would mean a clock is being consulted at all.
    for token in ("getmtime", "POINTER_TTL", "newest", "time.time", "sorted("):
        check("take_pointer does not use %s" % token, token not in code,
              [ln for ln in code.split("\n") if token in ln][:2])

    # filed_at may only be COPIED into the reported info, never compared or used to choose.
    DECIDERS = (">", "<", "sorted", "max(", "min(", "key=", "if ")
    bad = [ln.strip() for ln in code.split("\n")
           if "filed_at" in ln and any(d in ln for d in DECIDERS)]
    check("filed_at never reaches a comparison or a sort", not bad, bad)
    hits = [ln.strip() for ln in code.split("\n") if "filed_at" in ln]
    check("filed_at appears only where the result is reported",
          all("info" in ln for ln in hits), hits)
    check("POINTER_TTL is gone from the module entirely", "POINTER_TTL =" not in src)

    # Positive control on this detector: it must FIND the clock in a line that really uses one.
    planted = 'rule, pick = "recency", sorted(cands, key=lambda d: d["filed_at"])[-1]'
    check("CONTROL: the detector catches a real timing tie-break",
          any(d in planted for d in DECIDERS) and "sorted" in planted)
    shutil.rmtree(st, ignore_errors=True)


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution. This is a hand-rolled runner, not unittest.main(),
        # so without this a help request runs all 28 tests and writes their fixtures.
        print(__doc__ or os.path.basename(__file__))
        return 0
    for fn in (test_fixture_is_shaped_like_the_real_machine,
               test_resolve_claude_without_the_env_var,
               test_env_var_naming_a_non_claude_pid_is_refused,
               test_identity_from_env_not_shared_current,
               test_pointer_carries_the_structure,
               test_process_match_beats_a_newer_stranger,
               test_recycled_pid_is_not_my_predecessor,
               test_no_structural_match_serves_nothing,
               test_newer_rival_is_never_served,
               test_ancient_pointer_with_live_process_still_serves,
               test_dead_process_pointer_is_collected,
               test_terminal_rung_rescues_a_pointer_with_no_creation_time,
               test_relaunch_after_the_process_died_gets_nothing,
               test_terminal_app_is_not_a_lane,
               test_write_pointer_clears_a_same_lane_duplicate,
               test_ambiguous_lane_refuses_to_guess,
               test_consume_once, test_compact_does_not_consume, test_never_serves_my_own,
               test_pointer_to_missing_readme_ignored,
               test_hook_clear_serves_predecessor,
               test_hook_says_so_when_nothing_matches,
               test_hook_compact_uses_own_and_keeps_pointer,
               test_hook_silent_on_startup, test_hook_survives_bom_payload,
               test_hook_with_no_state_reports_absence,
               test_cli_write_creates_pointer,
               test_no_timing_left_in_the_match):
        print("\n-- %s" % fn.__name__)
        try:
            fn()
        except Exception as e:
            check(fn.__name__ + " (raised)", False, e)
    print("\n%d/%d passed" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print("failed: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
