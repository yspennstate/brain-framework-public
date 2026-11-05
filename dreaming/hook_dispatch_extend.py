"""Extend the dispatcher's in-process list with hooks proven equivalent; never removes a name (claude-1789621d, 13 Oct 2026).

WHY. hook_dispatch.py runs a hook in its own process unless hook_dispatch_allow.json names it, and only
hook_dispatch_control.py wrote that file: a full run over every hook of seven events, which deletes the list when a
dispatcher-level control fails. Nobody re-ran it after the film agents wired MR166-MR193 (8-13 Oct), so those 28 hooks
started one Python process each on every Bash/Edit tool call and every prompt, in every session. Measured 13 Oct (front
R21, primary_directive/improve_1012/fronts/R21): the film group alone 2.29 -> 0.12 s a tool call and 6.00 -> 0.30 s a
prompt with identical output; the dispatcher's PreToolUse median 6.03 s over the hour before the list was extended and
0.62 s after.

WHAT. For every wired hook that is not on the list (and not in FORCE_SUBPROCESS), the test of the control's part A,
unchanged: two runs in its own process with fresh session ids must agree (return code and stdout), and one in-process run
must equal them exactly, under every control event that wires it. A hook that passes under all of them is added; a hook
that fails stays where it was; nothing on the list is touched. A stale list (older than hook_dispatch.py) is refused:
that needs the full control.
--film keeps it to film hooks of the canonical shape - a docstring, one literal HOOK dict and
`if __name__ == "__main__": import film_hook_core; film_hook_core.run(HOOK)` - which is the code path every listed film
hook already runs in-process, so the test confirms what the shape implies. Without --film the same exact test runs on
any unlisted hook; for a hook with code of its own the full control remains the better bar.

THE STDIN PROBE (13 Oct, R22). In-process the dispatcher hands a hook an io.StringIO for stdin, and a StringIO has no
.buffer. The exact test above passed compact_hooks.py and compact_on_task_end.py, which read sys.stdin.buffer with no
fallback: in-process both would die on AttributeError into their own fail-open except on every call, and on the
control's payloads that is silent, the same as their silent subprocess runs. The same blind spot left prompt_archive.py
dead for five days in July (brain_switch.py's comment). So the in-process run now uses a stdin that records the
attributes a hook looked for and did not find: a hook that asked for one and then never read its payload fails
(STDIN-UNREAD). Hooks with the fallback (task_checklist_hook, his_voice_hook, frontier_hook) look up .buffer, read the
text instead, and pass.

USE. Claim hook_dispatch_allow.json on the mesh, then:
    python -X utf8 hook_dispatch_extend.py --film            test and add
    python -X utf8 hook_dispatch_extend.py --film --dry-run  test only
    --allow PATH   work on a copy of the list instead of the live one (tests)
    --only A.py,B.py   test only these unlisted hooks;  --rounds N   a hook must be exact in all N rounds
    --help             print the options and stop
An argument that is not one of these is refused before anything is tested or changed. Until 13 Oct 2026 it was ignored:
a session that asked for --help got a full pass over every unlisted hook and one hook added to the live list.
The film hooks are silent on the control's payloads ("echo hi", C:\\tmp\\x.txt, a non-film prompt) and write nothing.
"""
import ast
import io as _io
import json
import os
import shutil
import sys
import time
import types
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hook_dispatch as HD            # noqa: E402
import hook_dispatch_control as CTL   # noqa: E402

FILM_DIR = os.path.normcase(os.path.join(os.path.expanduser("~"), ".claude", "movie_sentinel", "film_hooks"))
LOCK_STALE_S = 900
LIVE_ALLOW = HD.ALLOW
DISPATCHER = os.path.abspath(HD.__file__)


class _Probe(_io.StringIO):
    """io.StringIO that records the attributes a hook asked for and did not find (sys.stdin.buffer, say)."""
    made = None

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        if _Probe.made is not None:
            _Probe.made.append(self)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        self.__dict__.setdefault("_missing", []).append(name)
        raise AttributeError(name)


def run_in_process_probed(path, payload, tail):
    """The dispatcher's own run_in_process, with its io.StringIO swapped for _Probe for the length of one call.
    Returns (rc, stdout, why); why is '' or the reason the in-process run cannot be trusted."""
    shim = types.ModuleType("io")
    shim.__dict__.update(_io.__dict__)
    shim.StringIO = _Probe
    real, _Probe.made = HD.io, []
    HD.io = shim
    try:
        rc, out, _ = HD.run_in_process(path, payload, tail)
        made = _Probe.made
    finally:
        HD.io, _Probe.made = real, None
    # A hook in STDIN_BYTES_OK (R25, 13 Oct) is handed a bytes-backed stdin with a .buffer, not a StringIO: two streams
    # are made, and there is no missing attribute to probe for.
    want = 2 if os.path.basename(path).lower() in getattr(HD, "STDIN_BYTES_OK", ()) else 3
    if len(made) != want:               # stdout, stderr, then stdin: the order run_in_process makes them
        return rc, out, "PROBE-UNAVAILABLE: %d streams, want %d (the dispatcher changed; use the full control)" % (
            len(made), want)
    if want == 2:
        return rc, out, ""
    stdin = made[2]
    try:
        pos = stdin.tell()
    except ValueError:                  # the hook closed it, so it had its turn at reading
        pos = -1
    missing = sorted(set(stdin.__dict__.get("_missing", [])))
    if missing and pos == 0:
        return rc, out, ("STDIN-UNREAD: looked up stdin.%s, which the in-process stdin lacks, and never read the payload"
                         % "/".join(missing))
    return rc, out, ""


def _arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


FLAGS = ("--film", "--dry-run")                 # options that stand alone
VALUED = ("--allow", "--only", "--rounds")      # options followed by one value
USAGE = """usage: python -X utf8 hook_dispatch_extend.py [--film] [--dry-run] [--only A.py,B.py] [--rounds N] [--allow PATH]
    (no option)        test every wired hook that is not on the in-process list and add the ones that pass
    --film             only film hooks of the canonical shape
    --dry-run          test, add nothing
    --only A.py,B.py   only these unlisted hooks
    --rounds N         a hook must be exact in all N rounds (default 1)
    --allow PATH       work on a copy of the list, not the live one
    --help, -h         print this and stop
Claim hook_dispatch_allow.json on the mesh before a run that can add names."""


def _bad_args(argv):
    """What is wrong with the arguments, or '' when every one is an option this tool knows."""
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in FLAGS:
            i += 1
        elif a in VALUED:
            if i + 1 >= len(argv) or argv[i + 1].startswith("-"):
                return "%s needs a value" % a
            if a == "--rounds" and not (argv[i + 1].isdigit() and int(argv[i + 1]) >= 1):
                return "--rounds needs a whole number of 1 or more, not %r" % argv[i + 1]
            i += 2
        else:
            return "unknown argument %r" % a
    return ""


def film_shape(path):
    """(True, '') when the file is a docstring, one literal HOOK dict and the standard main block; else (False, why)."""
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except Exception as e:  # noqa: BLE001
        return False, "unparsable: %s" % type(e).__name__
    body = list(tree.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    if len(body) != 2:
        return False, "%d top-level statements after the docstring, want 2" % len(body)
    a, m = body
    if not (isinstance(a, ast.Assign) and [getattr(t, "id", None) for t in a.targets] == ["HOOK"]):
        return False, "first statement is not HOOK = ..."
    try:
        if not isinstance(ast.literal_eval(a.value), dict):
            return False, "HOOK is not a dict"
    except Exception:  # noqa: BLE001
        return False, "HOOK is not a literal"
    # node by node, not an ast.dump string: 3.13+ leaves empty fields out of the dump, so a fixed string matched nothing
    t = getattr(m, "test", None)
    main_test = (isinstance(m, ast.If) and not m.orelse and isinstance(t, ast.Compare)
                 and isinstance(t.left, ast.Name) and t.left.id == "__name__" and len(t.ops) == 1
                 and isinstance(t.ops[0], ast.Eq) and len(t.comparators) == 1
                 and isinstance(t.comparators[0], ast.Constant) and t.comparators[0].value == "__main__")
    b = m.body if isinstance(m, ast.If) else []
    imp = (len(b) == 2 and isinstance(b[0], ast.Import) and len(b[0].names) == 1
           and b[0].names[0].name == "film_hook_core" and b[0].names[0].asname is None)
    c = getattr(b[1], "value", None) if len(b) == 2 and isinstance(b[1], ast.Expr) else None
    call = (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "run"
            and isinstance(c.func.value, ast.Name) and c.func.value.id == "film_hook_core" and not c.keywords
            and len(c.args) == 1 and isinstance(c.args[0], ast.Name) and c.args[0].id == "HOOK")
    if not (main_test and imp and call):
        return False, "the main block is not the standard film_hook_core.run(HOOK)"
    return True, ""


def candidates(allow, film_only):
    force = {x.lower() for x in HD.FORCE_SUBPROCESS}
    out, skipped = {}, {}
    for event, tool in CTL.EVENTS:
        for cmd, args in HD.hook_entries(event, tool):
            path, tail = HD.as_python_argv(cmd, args, allow_all=True)
            if not path:
                continue
            base = os.path.basename(path).lower()
            if base in allow or base in force:
                continue
            if film_only:
                if os.path.normcase(os.path.dirname(os.path.abspath(path))) != FILM_DIR:
                    continue
                ok, why = film_shape(path)
                if not ok:
                    skipped[base] = why
                    continue
            out.setdefault(base, []).append((event, tool, cmd, args, path, tail))
    return out, skipped


def unlisted_film_hooks():
    """Wired film hooks of the canonical shape that the live list does not hold ([] when none); None when the list is
    stale or missing (the wake check reports that case on its own line). Reads only; for hook_liveness.py."""
    allow = HD._allowlist()
    if HD._ALLOW_STALE[0] or not allow:
        return None
    return sorted(candidates(allow, True)[0])


def test(cands):
    passed, failed, rows = [], [], []
    for base in sorted(cands):
        ok = True
        for event, tool, cmd, args, path, tail in cands[base]:
            rc1, o1 = CTL.run_sub(cmd, args, CTL.payload(event, tool, "ctlA1-" + uuid.uuid4().hex[:8]))
            rc2, o2 = CTL.run_sub(cmd, args, CTL.payload(event, tool, "ctlA2-" + uuid.uuid4().hex[:8]))
            rc3, o3, why = run_in_process_probed(path, CTL.payload(event, tool, "ctlB-" + uuid.uuid4().hex[:8]), tail)
            if (rc1, o1) != (rc2, o2):
                verdict = "NOT-DETERMINISTIC"
            elif why:
                verdict = why
            elif (rc3, o3) != (rc1, o1):
                verdict = "MISMATCH"
            else:
                verdict = "exact"
            rows.append("%s %s/%s %s" % (base, event, tool or "-", verdict))
            ok &= verdict == "exact"
        (passed if ok else failed).append(base)
    return passed, failed, rows


def _lock(path):
    lk = path + ".extend.lock"
    try:
        if os.path.exists(lk) and time.time() - os.path.getmtime(lk) > LOCK_STALE_S:
            os.remove(lk)
        fd = os.open(lk, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, ("%d %s" % (os.getpid(), time.strftime("%Y-%m-%d %H:%M:%S"))).encode())
        os.close(fd)
        return lk
    except FileExistsError:
        return None


def apply(passed, who):
    d = json.load(open(HD.ALLOW, encoding="utf-8"))
    old = [str(x) for x in d.get("in_process_ok", [])]
    add = [x for x in passed if x not in {o.lower() for o in old}]
    if not add:
        return 0, ""
    bak = HD.ALLOW + ".bak_extend_" + time.strftime("%Y%m%dT%H%M%S")
    shutil.copy2(HD.ALLOW, bak)
    d["in_process_ok"] = sorted(set(old) | set(add))
    d["written_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    d["dispatcher_mtime"] = os.path.getmtime(DISPATCHER)
    d["extended"] = (d.get("extended") or [])[-49:] + [{"at": d["written_at"], "by": who, "added": add,
                                                       "test": "hook_dispatch_extend.py: control A exact per wired event, stdin probe"}]
    tmp = HD.ALLOW + ".extend.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, HD.ALLOW)
    return len(add), bak


def main():
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print(USAGE)
        return 0
    bad = _bad_args(argv)
    if bad:
        print("refused: %s. Nothing was tested or changed." % bad)
        print(USAGE)
        return 2
    if _arg("--allow"):
        HD.ALLOW = os.path.abspath(_arg("--allow"))
    film_only, dry = "--film" in sys.argv, "--dry-run" in sys.argv
    allow = HD._allowlist()
    if HD._ALLOW_STALE[0] or not allow:
        print("refused: %s is %s - re-sign it with the full control (python -X utf8 hook_dispatch_control.py)"
              % (HD.ALLOW, "stale" if HD._ALLOW_STALE[0] else "missing or empty"))
        return 3
    cands, skipped = candidates(allow, film_only)
    only = {x.strip().lower() for x in (_arg("--only") or "").split(",") if x.strip()}
    if only:                            # a named subset, e.g. leaving out a hook seen NOT-DETERMINISTIC before
        cands = {k: v for k, v in cands.items() if k in only}
    print("list: %d names; unlisted %s hooks wired: %d%s" % (len(allow), "film" if film_only else "", len(cands),
          ("; not the film shape, left alone: " + ", ".join("%s (%s)" % kv for kv in sorted(skipped.items()))) if skipped else ""))
    if not cands:
        return 0
    lk = None if dry else _lock(HD.ALLOW)
    if not dry and not lk:
        print("refused: another extend holds %s.extend.lock" % HD.ALLOW)
        return 3
    try:
        t0 = time.perf_counter()
        rounds = max(1, int(_arg("--rounds", "1")))
        ok_all, bad_any, rows = set(cands), set(), []
        for k in range(rounds):         # a hook joins only if it is exact in every round
            p, f, rr = test(cands)
            ok_all &= set(p)
            bad_any |= set(f)
            rows += [("round %d: " % (k + 1) if rounds > 1 else "") + x for x in rr]
        passed, failed = sorted(ok_all - bad_any), sorted(bad_any)
        for r in rows:
            print("   " + r)
        print("tested %d in %.0f s: %d pass, %d fail%s" % (len(cands), time.perf_counter() - t0, len(passed), len(failed),
              (" (" + ", ".join(failed) + ")") if failed else ""))
        if dry or not passed:
            return 0 if not failed else 1
        HD._ALLOW_STALE[0] = False
        if not HD._allowlist() or HD._ALLOW_STALE[0]:
            print("refused: the list went stale or empty while testing")
            return 3
        who = os.environ.get("AGENT_MESH_ID") or os.environ.get("MESH_AGENT") or "hook_dispatch_extend.py"
        n, bak = apply(passed, who)
        HD._ALLOW_STALE[0] = False
        now = HD._allowlist()
        print("added %d (backup %s); list now %d names, stale=%s" % (n, os.path.basename(bak) if bak else "-", len(now),
              bool(HD._ALLOW_STALE[0])))
        if n and os.path.normcase(HD.ALLOW) == os.path.normcase(LIVE_ALLOW):     # a test on a copy stays out of the shared log
            HD.log("EXTEND +%d hooks in-process by %s (exact control A): %s" % (n, who, ", ".join(passed)[:300]))
        return 0 if not failed else 1
    finally:
        if lk:
            try:
                os.remove(lk)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
