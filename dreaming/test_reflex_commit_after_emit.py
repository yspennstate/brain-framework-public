"""test_reflex_commit_after_emit.py - a reflex counts as fired, and enters its session backoff, only once its context
has been written.

    python test_reflex_commit_after_emit.py [--module INSTINCT_PRIME.py] [--old OLD_INSTINCT_PRIME.py]

Each arm loads the module afresh, points every file it writes (STATE, STATE_DIR, FIRELOG, SUPPRESSED_LOG, REFILL_LOG)
into a temporary folder, stubs the candidate step to one test reflex, and runs the real main() on a prompt event with a
uuid-shaped session (the fire log keeps real sessions only).
 E1  success: the context carries the reflex, its backoff is written, one fire row is logged
 E2  _render raises: nothing is written or logged, and the same reflex fires again on the next call
 E3  the write to stdout raises: nothing is written or logged, and the next call fires it again
With --old, the module before this change must leave a phantom in E2: the backoff written and the fire logged with no
context emitted, and the next call silenced by that backoff (the check can fail).
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import stat
import sys
import tempfile

if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.abspath(sys.argv[sys.argv.index("--module") + 1]) if "--module" in sys.argv else os.path.join(HERE, "instinct_prime.py")
OLD = os.path.abspath(sys.argv[sys.argv.index("--old") + 1]) if "--old" in sys.argv else None
SESSION = "0a1b2c3d-0000-4000-8000-00000000e2e7"
REFLEX = {"id": "test_commit_after_emit_reflex", "gut": "A test reflex that only this test can fire.",
          "sting": "a test scar", "source": "test", "mode": "fire", "charge": 0.5}
fails, ran = [], []


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def rmtree(path):
    def clear(func, name, _exc):
        os.chmod(name, stat.S_IWRITE)
        func(name)
    shutil.rmtree(path, onexc=clear)


def load(path, td):
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    loader = importlib.machinery.SourceFileLoader("instinct_prime_under_test", path)
    spec = importlib.util.spec_from_file_location("instinct_prime_under_test", path, loader=loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    m.STATE = os.path.join(td, "state.json")
    m.STATE_DIR = os.path.join(td, "state_dir")
    m.FIRELOG = os.path.join(td, "fires.tsv")
    m.SUPPRESSED_LOG = os.path.join(td, "suppressed.tsv")
    m.REFILL_LOG = os.path.join(td, "refills.tsv")
    m._load = lambda *a, **k: []
    m._fire = lambda *a, **k: [dict(REFLEX)]
    m._augment_graph = lambda text, data, instincts, fired: fired
    m._exact_clause_context = lambda *a, **k: ""
    return m


class Boom(io.StringIO):
    def write(self, s):
        raise OSError("stdout closed")


def call(m, stdout=None):
    payload = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": SESSION,
                          "prompt": "please check the test reflex that only this test can fire today"})
    old_in, old_out = sys.stdin, sys.stdout
    sys.stdin = io.StringIO(payload)
    sys.stdout = stdout if stdout is not None else io.StringIO()
    try:
        m.main()
        return sys.stdout.getvalue() if not isinstance(sys.stdout, Boom) else ""
    finally:
        sys.stdin, sys.stdout = old_in, old_out


def state_has(td):
    d = os.path.join(td, "state_dir")
    if not os.path.isdir(d):
        return False
    for fn in os.listdir(d):
        try:
            if any(REFLEX["id"] in k for k in json.load(open(os.path.join(d, fn), encoding="utf-8"))):
                return True
        except Exception:
            pass
    return False


def fire_rows(td):
    p = os.path.join(td, "fires.tsv")
    return sum(1 for l in open(p, encoding="utf-8") if REFLEX["id"] in l) if os.path.exists(p) else 0


def arm(path, mode):
    td = tempfile.mkdtemp(prefix="reflex_emit_%s_" % mode.lower())
    try:
        m = load(path, td)
        if mode == "E1":
            out = call(m)
            return {"shown": REFLEX["id"] in out or REFLEX["gut"][:20] in out, "state": state_has(td), "fires": fire_rows(td)}
        real_render = m._render
        if mode == "E2":
            def bad_render(fired):
                raise RuntimeError("render failed")
            m._render = bad_render
            out = call(m)
        else:
            out = call(m, stdout=Boom())
        after = {"shown": bool(out), "state": state_has(td), "fires": fire_rows(td)}
        m._render = real_render
        again = call(m)
        after["refires"] = REFLEX["gut"][:20] in again
        return after
    finally:
        rmtree(td)


def main():
    e1 = arm(MOD, "E1")
    check(e1["shown"] and e1["state"] and e1["fires"] == 1,
          "E1 success: context carries the reflex %s, backoff written %s, fire rows %d" % (e1["shown"], e1["state"], e1["fires"]))
    for mode, what in (("E2", "_render raises"), ("E3", "the write to stdout raises")):
        x = arm(MOD, mode)
        check(not x["state"] and x["fires"] == 0 and x["refires"],
              "%s %s: backoff written %s, fire rows %d, the next call fires it again %s" % (
                  mode, what, x["state"], x["fires"], x["refires"]))
    if OLD:
        x = arm(OLD, "E2")
        check(x["state"] and x["fires"] >= 1 and not x["refires"],
              "N1 the module before this change leaves a phantom when _render raises: backoff written %s, fire rows %d, "
              "the next call silenced %s (the defect B04 found)" % (x["state"], x["fires"], not x["refires"]))
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
