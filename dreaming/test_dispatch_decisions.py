"""test_dispatch_decisions.py - the hook dispatcher keeps what its hooks decided, runs the hooks the harness would run,
and keeps every overflow record (29 Sep 2026, claude-825b1305; interactive audit BRAIN-INTERACTIVE-20260928-CRITICAL-PATH,
findings D01, D03, D04, D05 and D06).

    python test_dispatch_decisions.py [HOOK_DISPATCH.py] [--old OLD_HOOK_DISPATCH.py]

The module is loaded from the path given (default: hook_dispatch.py beside this file) with its log, overflow folder and
overflow archive pointed at a temporary folder; no hook is run.
 A1 a PreToolUse context alone (JSON or plain text) leaves with no permissionDecision
 A2 an explicit decision survives: allow alone, ask over allow, deny over ask and allow, in either order
 B1 a hook exits 2, then a sibling prints {"hookSpecificOutput": 42}: the event still blocks with the first one's reason
 B2 the malformed sibling first, then the exit 2: blocks
 B3 a malformed permissionDecision (a list) is dropped and named in the log; the other hook's context still leaves
 B4 a deny, then a sibling whose hookSpecificOutput is a string: the deny leaves
 B5 merge() itself raising inside dispatch(): an exit 2 still blocks, and without one a deny still leaves
 C1 the two project-memory hooks, forced onto their own process, are still recognised by name (string and argv forms)
 C2 on files that exist: the prompt hook and the strong-node runner stay forced onto their own process (as_python_argv
    answers None); so does the hot hook, unless the dispatcher lists it in STDIN_BYTES_OK (R25, 13 Oct 2026), and then
    its stdin carries the payload's bytes while every other hook still gets an io.StringIO
 C3 a headless worker under the diet skips them and still runs a hook outside the skip list
 D1 matchers, as Claude Code 2.1.284 reads them: empty and "*" match all; exact names, pipe and comma lists (no
    substring: Edit is not NotebookEdit, Edit|Write is not TodoWrite); legacy names (Task is Agent); case; a genuine
    regex tested unanchored; a broken regex matches nothing; SessionStart matches on its source; an event with no
    match value runs every block
 E1 two overflow writes in the same second for one session: two files, both texts kept
 E2 two sessions sharing their first eight characters: two files
 E3 an expired overflow file is archived (its text in the month's .jsonl.gz) and then removed
 E4 an expired file whose archiving fails is kept
 E5 another pruner's fresh lock: nothing is pruned; a lock older than ten minutes is cleared
With --old, the dispatcher before this change must fail A1, B1, C1, D1 and E1 (the checks can fail).
"""
import gzip
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import time

if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = sys.argv[1:]
OLD = os.path.abspath(ARGS[ARGS.index("--old") + 1]) if "--old" in ARGS else None
_pos = [a for i, a in enumerate(ARGS) if not a.startswith("--") and (i == 0 or ARGS[i - 1] != "--old")]
MOD = os.path.abspath(_pos[0]) if _pos else os.path.join(HERE, "hook_dispatch.py")
fails, ran = [], []


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def load(path, td):
    name = "hd_%d" % len(ran)
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    m.LOG = os.path.join(td, "dispatch.log")
    m.OVERFLOW_DIR = os.path.join(td, "overflow")
    if hasattr(m, "OVERFLOW_ARCHIVE_DIR"):
        m.OVERFLOW_ARCHIVE_DIR = os.path.join(td, "archive")
    m.LIST_UNREADABLE[:] = []
    return m


def merged(m, results, event="PreToolUse"):
    """(exit code, parsed JSON or None, stderr, raised?)"""
    try:
        code, out, err = m.merge(results, event)
    except Exception as e:
        return None, None, "%s: %s" % (type(e).__name__, e), True
    return code, (json.loads(out) if out else None), err, False


def hso(o):
    return (o or {}).get("hookSpecificOutput") or {}


def decision_controls(m):
    r = {}
    a = merged(m, [("note.py", 0, json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                                     "additionalContext": "check the units"}}), "")])
    b = merged(m, [("plain.py", 0, "a plain note", "")])
    r["A1"] = (not a[3] and not b[3] and "permissionDecision" not in hso(a[1]) and "permissionDecision" not in hso(b[1])
               and hso(a[1]).get("additionalContext") == "check the units" and hso(b[1]).get("additionalContext") == "a plain note",
               "A1 context alone: JSON %s, plain %s" % (hso(a[1]).get("permissionDecision"), hso(b[1]).get("permissionDecision")))

    def dec(*ds):
        res = [("h%d.py" % i, 0, json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": d,
                                                                      "permissionDecisionReason": "r%d" % i}}), "")
               for i, d in enumerate(ds)]
        return hso(merged(m, res)[1]).get("permissionDecision")
    got = [dec("allow"), dec("allow", "ask"), dec("ask", "allow"), dec("allow", "deny", "ask"), dec("deny", "allow")]
    r["A2"] = (got == ["allow", "ask", "ask", "deny", "deny"], "A2 explicit decisions: %s" % got)
    x = merged(m, [("gate.py", 2, "", "ACK GATE: answer the scar"), ("bad.py", 0, '{"hookSpecificOutput": 42}', "")])
    r["B1"] = (x[0] == 2 and "ACK GATE" in (x[2] or ""), "B1 exit 2 then a malformed sibling: exit %s, %r" % (x[0], (x[2] or "")[:60]))
    x = merged(m, [("bad.py", 0, '{"hookSpecificOutput": [1, 2]}', ""), ("gate.py", 2, "", "ACK GATE: answer the scar")])
    r["B2"] = (x[0] == 2 and "ACK GATE" in (x[2] or ""), "B2 a malformed sibling then exit 2: exit %s, %r" % (x[0], (x[2] or "")[:60]))
    x = merged(m, [("weird.py", 0, json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                                      "permissionDecision": ["deny"]}}), ""),
                   ("note.py", 0, json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                                     "additionalContext": "still here"}}), "")])
    logged = open(m.LOG, encoding="utf-8").read() if os.path.exists(m.LOG) else ""
    r["B3"] = (not x[3] and hso(x[1]).get("additionalContext") == "still here" and "permissionDecision" not in hso(x[1])
               and "weird.py" in logged,
               "B3 a list as permissionDecision: raised %s, context %r, decision %r, logged %s" % (
                   x[3], hso(x[1]).get("additionalContext"), hso(x[1]).get("permissionDecision"), "weird.py" in logged))
    x = merged(m, [("guard.py", 0, json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                                      "permissionDecisionReason": "no gpu training"}}), ""),
                   ("bad.py", 0, '{"hookSpecificOutput": "oops"}', "")])
    r["B4"] = (not x[3] and hso(x[1]).get("permissionDecision") == "deny", "B4 a deny then a malformed sibling: %s%s" % (
        hso(x[1]).get("permissionDecision"), " (merge raised: %s)" % x[2] if x[3] else ""))
    return r


def fallback_controls(m):
    """B5: merge() raising inside dispatch(), with the hooks and their runs faked."""
    r = {}
    real = {k: getattr(m, k) for k in ("hook_entries", "as_python_argv", "run_subprocess", "merge")}
    outs = {}

    def fake_entries(event, tool_name, settings_path=None, *a, **k):
        return [("python", [n]) for n in outs]

    def fake_sub(cmd, args, payload, budget=None):
        return outs[args[0]]

    def boom(*a, **k):
        raise RuntimeError("merge exploded (test)")
    try:
        m.hook_entries, m.as_python_argv, m.run_subprocess, m.merge = fake_entries, (lambda *a, **k: (None, [])), fake_sub, boom
        outs.clear()
        outs.update({"a.py": (0, "note", ""), "gate.py": (2, "", "BLOCKED BY GATE")})
        code1, out1, err1, _ = m.dispatch("PreToolUse", json.dumps({"tool_name": "Bash"}), "Bash")
        outs.clear()
        outs.update({"a.py": (0, "note", ""), "guard.py": (0, json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "not this"}}), "")})
        try:
            code2, out2, err2, _ = m.dispatch("PreToolUse", json.dumps({"tool_name": "Bash"}), "Bash")
            d2 = hso(json.loads(out2) if out2 else None).get("permissionDecision")
        except Exception as e:
            code2, d2 = "raised %s" % type(e).__name__, None
    except Exception as e:
        code1, err1, code2, d2 = "raised %s" % type(e).__name__, "", None, None
    finally:
        for k, v in real.items():
            setattr(m, k, v)
    r["B5"] = (code1 == 2 and "BLOCKED BY GATE" in (err1 or "") and code2 == 0 and d2 == "deny",
               "B5 merge raising in dispatch: exit %s %r; without exit 2: exit %s, decision %s" % (code1, (err1 or "")[:30], code2, d2))
    return r


# Native paths: os.path.basename splits on backslashes only on Windows, so a Windows-path literal fails C1 and C3 on a Mac
# while the dispatcher is right (5 Nov 2026). The dispatcher never sees a foreign path on its own machine.
NATIVE_HOOK = os.path.join(os.sep, "x", "hook.py")
NATIVE_OTHER = os.path.join(os.sep, "x", "other_hook.py")


def identity_controls(m):
    r = {}
    pm = os.path.expanduser(r"~/.claude/hooks/project_memory_hook.py")
    ph = os.path.expanduser(r"~/.claude/hooks/project_memory_hot_hook.py")
    names = [m._entry_basename('python -S -B "%s"' % pm, []), m._entry_basename("python", ["-S", "-B", ph]),
             m._entry_basename("python", ["-B", NATIVE_HOOK, "--hook-runtime", "claude"])]
    r["C1"] = (names == ["project_memory_hook.py", "project_memory_hot_hook.py", "hook.py"], "C1 names: %s" % names)
    # C2 runs on files that exist. as_python_argv tests os.path.exists after FORCE_SUBPROCESS, and the two paths above
    # have been gone since the hooks moved to .brain-framework\runtime, so on them it answered None whether or not a
    # name was forced (13 Oct 2026, claude-1789621d, front R25).
    cd = tempfile.mkdtemp(prefix="dispatch_c2_")
    try:
        xpm, xph, xh = (os.path.join(cd, n) for n in ("project_memory_hook.py", "project_memory_hot_hook.py", "hook.py"))
        for p in (xpm, xph, xh):
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("pass\n")
        got = [m.as_python_argv('python -S -B "%s"' % xpm, [], allow_all=True)[0], m.as_python_argv("python", ["-S", xph], allow_all=True)[0],
               m.as_python_argv("python", ["-B", xh, "--hook-runtime", "claude"], allow_all=True)[0]]
        got = [os.path.basename(x) if x else None for x in got]
        # The hot hook leaves the forced set only for a dispatcher that hands it its payload as bytes (STDIN_BYTES_OK):
        # its first line is sys.stdin.buffer.read(). Every other hook keeps the io.StringIO.
        # On 3 Nov 2026 the prompt hook joined it too (claude-baabdcd6), so each name is read from the list: a listed
        # hook runs in-process with bytes, an unlisted one stays forced with a StringIO, and hook.py is never listed.
        listed = getattr(m, "STDIN_BYTES_OK", ())
        hot_ok = "project_memory_hot_hook.py" in listed
        want = [n if n in listed else None for n in ("project_memory_hook.py", "project_memory_hot_hook.py")] + [None]
        stdin_ok = True
        if hot_ok:
            text = '{"prompt": "א › ok"}'
            for xp in (xpm, xph, xh):
                st = m._stdin_for(xp, text)
                try:
                    if os.path.basename(xp) in listed:
                        stdin_ok &= st.buffer.read() == text.encode("utf-8")
                    else:
                        stdin_ok &= isinstance(st, io.StringIO) and st.read() == text
                except AttributeError:          # a StringIO handed to a listed hook: the 15 Sep failure
                    stdin_ok = False
    finally:
        shutil.rmtree(cd, ignore_errors=True)
    r["C2"] = (got == want and stdin_ok, "C2 on files that exist, own process (None) or in-process: %s; the hot hook's stdin "
               "has the bytes: %s" % (got, "yes" if hot_ok and stdin_ok else ("NO" if hot_ok else "not listed")))
    real = {k: getattr(m, k) for k in ("hook_entries", "as_python_argv", "run_subprocess")}
    ran_ = []

    def fake_entries(event, tool_name, settings_path=None, *a, **k):
        return [("python", ["-S", "-B", pm]), ("python", ["-S", ph]), ("python", [NATIVE_OTHER])]

    def fake_sub(cmd, args, payload, budget=None):
        ran_.append(os.path.basename(args[-1]))
        return 0, "", ""
    saved = os.environ.get("BRAIN_HEADLESS_DIET")
    try:
        os.environ["BRAIN_HEADLESS_DIET"] = "1"
        m.hook_entries, m.as_python_argv, m.run_subprocess = fake_entries, (lambda *a, **k: (None, [])), fake_sub
        m.dispatch("UserPromptSubmit", json.dumps({"prompt": "[headless-worker] draw the next picture", "session_id": "hw-1"}), "")
    finally:
        for k, v in real.items():
            setattr(m, k, v)
        if saved is None:
            os.environ.pop("BRAIN_HEADLESS_DIET", None)
        else:
            os.environ["BRAIN_HEADLESS_DIET"] = saved
    r["C3"] = (ran_ == ["other_hook.py"], "C3 a headless worker under the diet ran %s" % ran_)
    return r


def matcher_controls(m):
    mt = m.matches
    new = "value" in m.hook_entries.__code__.co_varnames or hasattr(m, "match_value")
    cases = [((None, "Bash"), True), (("", "Bash"), True), (("*", "Bash"), True), ((".*", "Anything"), True),
             (("Bash", "Bash"), True), (("Bash", "BashOutput"), False), (("Edit", "NotebookEdit"), False),
             (("Edit|Write", "Edit"), True), (("Edit|Write", "Write"), True), (("Edit|Write", "TodoWrite"), False),
             (("Edit|Write|MultiEdit|NotebookEdit", "NotebookEdit"), True), (("Bash, Edit", "Edit"), True),
             (("Bash,Edit", "Bash"), True), (("Task", "Agent"), True), (("bash", "Bash"), False),
             (("mcp__.*__(?:create|update)(?:_|$).*", "mcp__docs__update_page"), True),
             (("mcp__.*__(?:create|update)(?:_|$).*", "mcp__docs__read_page"), False),
             (("Bash|mcp__.*__run", "NotebookBash"), True), (("(", "Bash"), False)]
    got = []
    for (pat, name), want in cases:
        try:
            v = mt(pat, name, "PreToolUse") if new else mt(pat, name)
        except Exception as e:
            v = "raised %s" % type(e).__name__
        got.append((pat, name, v, v is want))
    extra = []
    if new:
        extra = [("SessionStart startup/startup", mt("startup", m.match_value("SessionStart", {"source": "startup"}), "SessionStart"), True),
                 ("SessionStart startup/resume", mt("startup", m.match_value("SessionStart", {"source": "resume"}), "SessionStart"), False),
                 ("UserPromptSubmit any matcher", mt("Nothing", m.match_value("UserPromptSubmit", {"prompt": "x"}), "UserPromptSubmit"), True)]
    bad = [g for g in got if not g[3]] + [e for e in extra if e[1] is not e[2]]
    return {"D1": (not bad and bool(extra), "D1 matchers: %d cases, %d wrong%s" % (
        len(got) + len(extra), len(bad), (": " + "; ".join("%r on %r -> %r" % (g[0], g[1], g[2]) for g in bad[:6])) if bad else ""))}


def overflow_controls(m, td):
    r = {}
    items1, items2 = [("a.py", "first text " * 50)], [("a.py", "second text " * 50)]
    p1 = m._overflow_write("UserPromptSubmit", "abcdef12-0000-4000-8000-000000000001", items1)
    p2 = m._overflow_write("UserPromptSubmit", "abcdef12-0000-4000-8000-000000000001", items2)
    t1 = open(p1, encoding="utf-8").read() if p1 and os.path.exists(p1) else ""
    t2 = open(p2, encoding="utf-8").read() if p2 and os.path.exists(p2) else ""
    r["E1"] = (bool(p1) and bool(p2) and p1 != p2 and "first text" in t1 and "second text" in t2,
               "E1 same second, same session: %s file(s), first text kept %s" % (len({p1, p2} - {None}), "first text" in t1))
    q1 = m._overflow_write("UserPromptSubmit", "abcdef12-aaaa", items1)
    q2 = m._overflow_write("UserPromptSubmit", "abcdef12-bbbb", items2)
    r["E2"] = (bool(q1) and bool(q2) and q1 != q2, "E2 sessions sharing eight characters: %s" % ("two files" if q1 != q2 else "one file"))
    if not hasattr(m, "_overflow_prune"):
        return r
    old = os.path.join(m.OVERFLOW_DIR, "UserPromptSubmit_old00000_20260901_000000.txt")
    with open(old, "w", encoding="utf-8") as fh:
        fh.write("an old overflow text that a transcript still points to")
    t = time.time() - 49 * 3600
    os.utime(old, (t, t))
    m._overflow_prune(time.time())
    recs = []
    for fn in os.listdir(m.OVERFLOW_ARCHIVE_DIR) if os.path.isdir(m.OVERFLOW_ARCHIVE_DIR) else []:
        with gzip.open(os.path.join(m.OVERFLOW_ARCHIVE_DIR, fn), "rt", encoding="utf-8") as fh:
            recs += [json.loads(x) for x in fh if x.strip()]
    hit = [x for x in recs if x.get("name") == os.path.basename(old)]
    r["E3"] = (not os.path.exists(old) and len(hit) == 1 and "still points to" in hit[0].get("text", ""),
               "E3 an expired file: removed %s, archived %d time(s)" % (not os.path.exists(old), len(hit)))
    old2 = os.path.join(m.OVERFLOW_DIR, "UserPromptSubmit_old00002_20260901_000000.txt")
    with open(old2, "w", encoding="utf-8") as fh:
        fh.write("keep me")
    os.utime(old2, (t, t))
    saved = m.OVERFLOW_ARCHIVE_DIR
    blocker = os.path.join(td, "not_a_dir")
    open(blocker, "w").close()
    m.OVERFLOW_ARCHIVE_DIR = os.path.join(blocker, "archive")
    m._overflow_prune(time.time())
    m.OVERFLOW_ARCHIVE_DIR = saved
    r["E4"] = (os.path.exists(old2), "E4 archiving fails: the file is kept %s" % os.path.exists(old2))
    lock = os.path.join(m.OVERFLOW_DIR, ".prune.lock")
    open(lock, "w").close()
    m._overflow_prune(time.time())
    kept_locked = os.path.exists(old2)
    lt = time.time() - 700
    os.utime(lock, (lt, lt))
    m._overflow_prune(time.time())
    cleared = not os.path.exists(lock)
    m._overflow_prune(time.time())
    r["E5"] = (kept_locked and cleared and not os.path.exists(old2),
               "E5 a fresh lock holds the prune %s; an old lock cleared %s; then pruned %s" % (kept_locked, cleared, not os.path.exists(old2)))
    return r


def all_controls(path):
    td = tempfile.mkdtemp(prefix="dispatch_decisions_")
    try:
        m = load(path, td)
        r = {}
        for f in (decision_controls, fallback_controls, identity_controls, matcher_controls):
            try:
                r.update(f(m))
            except Exception as e:
                r["%s" % f.__name__] = (False, "%s raised %s: %s" % (f.__name__, type(e).__name__, e))
        try:
            r.update(overflow_controls(m, td))
        except Exception as e:
            r["overflow_controls"] = (False, "overflow_controls raised %s: %s" % (type(e).__name__, e))
        return r
    finally:
        shutil.rmtree(td, ignore_errors=True)


def main():
    res = all_controls(MOD)
    for k in sorted(res):
        check(res[k][0], res[k][1])
    if OLD:
        old = all_controls(OLD)
        for k in ("A1", "B1", "C1", "D1", "E1"):
            ok, text = old.get(k, (False, "%s did not run" % k))
            check(not ok, "N-%s the dispatcher before this change fails it (the check can fail): %s" % (k, text))
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
