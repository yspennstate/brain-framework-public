"""test_task_checklist_primed_state.py - a prompt parses state.json once, not twice, and never reuses a stale parse
(5 Nov 2026).

    python test_task_checklist_primed_state.py [--module HOOK.py] [--old OLD_HOOK.py]

hook_main reads state.json to find the session's mesh agent, then record_prompt reads it again under the lock. The
first parse is now handed to the second when the file still has the same inode, size and mtime.
 P1 the hand-off: a primed load_state returns the same state without parsing the file
 P2 a save between the two reads changes the file key, so the second load parses the new file (its new entry is there)
 P3 a primed state is used once: the load after it parses again
 P4 the whole hook on one prompt parses state.json once (from the first read on); the selftest store is a temp folder
With --old, the hook before this change must parse twice on P4 (N1). Each run uses a throwaway TASK_CHECKLIST_HOME.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = sys.argv[1:]
MOD = os.path.abspath(ARGS[ARGS.index("--module") + 1]) if "--module" in ARGS else os.path.join(HERE, "task_checklist_hook.py")
OLD = os.path.abspath(ARGS[ARGS.index("--old") + 1]) if "--old" in ARGS else None
fails, ran = [], []


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def load(path, home):
    os.environ["TASK_CHECKLIST_HOME"] = home
    spec = importlib.util.spec_from_file_location("tc_under_test_%d" % len(ran), path)
    m = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(path))
    spec.loader.exec_module(m)
    return m


class Counting:
    """Stands in for the module's json: counts the parses of state.json (json.load of an open file)."""
    def __init__(self, real):
        self.real, self.n = real, 0

    def load(self, fh, *a, **k):
        if os.path.basename(getattr(fh, "name", "") or "") == "state.json" or "state.json" in str(getattr(fh, "name", "")):
            self.n += 1
        return self.real.load(fh, *a, **k)

    def __getattr__(self, name):
        return getattr(self.real, name)


def seed(home, entries=None):
    os.makedirs(home, exist_ok=True)
    with open(os.path.join(home, "state.json"), "w", encoding="utf-8") as fh:
        json.dump({"entries": {"T-1": {"id": "T-1", "text": "seed"}} if entries is None else entries, "sessions": {},
                   "seq": {}}, fh)


def unit(path):
    home = tempfile.mkdtemp(prefix="tc_primed_")
    try:
        seed(home)
        m = load(path, home)
        if not hasattr(m, "_PRIMED"):
            return None
        cj = Counting(json)
        m.json = cj
        m._LAST_KEY[:] = []
        st0 = m.load_state()
        m._PRIMED[:] = [(m._LAST_KEY[0], st0)]
        n0 = cj.n
        st1 = m.load_state()
        check(st1 is st0 and cj.n == n0, "P1 a primed load returns the same state with no parse (%d parse)" % (cj.n - n0))
        n1 = cj.n
        st2 = m.load_state()
        check(cj.n == n1 + 1, "P3 the load after it parses again (%d parse)" % (cj.n - n1))
        m._LAST_KEY[:] = []
        st3 = m.load_state()
        m._PRIMED[:] = [(m._LAST_KEY[0], st3)]
        new = dict(st3, entries=dict(st3["entries"], **{"T-2": {"id": "T-2", "text": "written between"}}))
        m.save_state(new)
        st4 = m.load_state()
        check(st4 is not st3 and "T-2" in st4["entries"], "P2 a save between the reads is seen: T-2 %s"
              % ("present" if "T-2" in st4["entries"] else "missing"))
        return True
    finally:
        shutil.rmtree(home, ignore_errors=True)


DRIVER = r'''
import io, json, os, sys
path = sys.argv[1]
import importlib.util
spec = importlib.util.spec_from_file_location("tc_driver", path)
m = importlib.util.module_from_spec(spec)
sys.path.insert(0, os.path.dirname(path))
spec.loader.exec_module(m)
n = [0]
real = m.json
class C:
    def load(self, fh, *a, **k):
        if "state.json" in str(getattr(fh, "name", "")):
            n[0] += 1
        return real.load(fh, *a, **k)
    def __getattr__(self, k):
        return getattr(real, k)
m.json = C()
m.mesh_task_for = lambda session: (None, None)
payload = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
                      "prompt": "please check the parity log and push the fix"})
sys.stdin = io.TextIOWrapper(io.BytesIO(payload.encode("utf-8")), encoding="utf-8")
out, sys.stdout = sys.stdout, io.StringIO()
try:
    m.hook_main("claude")
finally:
    sys.stdout = out
print("PARSES", n[0])
print("ENTRIES", len(real.load(open(m.STATE, encoding="utf-8"))["entries"]))
'''


def whole_hook(path):
    home = tempfile.mkdtemp(prefix="tc_primed_hook_")
    try:
        seed(home, entries={})
        env = dict(os.environ, TASK_CHECKLIST_HOME=home, TASK_CHECKLIST_ALLOW_HARNESS="1", TASK_CHECKLIST_AGENT="claude-test0001")
        p = subprocess.run([sys.executable, "-s", "-c", DRIVER, path], env=env, capture_output=True, text=True, timeout=120)
        got = dict(line.split() for line in p.stdout.splitlines() if line.startswith(("PARSES ", "ENTRIES ")))
        if "PARSES" not in got:
            return None, p
        # a prompt that never reached the ledger would parse once too: count it only when its entry was written
        return (int(got["PARSES"]) if int(got.get("ENTRIES", 0)) >= 1 else -1), p
    finally:
        shutil.rmtree(home, ignore_errors=True)


def main():
    if unit(MOD) is None:
        check(False, "the hook has the hand-off (_PRIMED)")
    n, p = whole_hook(MOD)
    check(n == 1, "P4 one prompt through hook_main parses state.json %s time(s)%s"
          % (n, "" if n is not None else ": " + (p.stderr or "")[-300:]))
    if OLD:
        on, op = whole_hook(OLD)
        check(on is not None and on >= 2, "N1 the hook before this change parses it %s times" % on)
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
