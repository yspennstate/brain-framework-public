"""test_membrane_save.py - live_membrane.save_state under the Windows rename race. Another hook
call of the same session holding the state file open made os.replace raise Access denied, and the exception aborted the
hook (17 'hook error: PermissionError' lines 26-27 Sep, each on an evaluation that served nothing). Temp dirs only.

    python test_membrane_save.py [PATH_TO_live_membrane.py]

Controls:
 A. no contention: True, the file holds the new state, no tmp file left
 B. the file held open for 60 ms: True after retrying, new state, no tmp left
 C. the file held open throughout: False within a second, no exception, old state intact, tmp removed, one log line
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time

H = os.path.expanduser("~")
MEMB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(H, ".claude", "brain_graph", "bin", "live_membrane.py")
sys.path.insert(0, os.path.join(H, ".claude", "brain_graph", "bin"))
spec = importlib.util.spec_from_file_location("lm_under_test", MEMB)
LM = importlib.util.module_from_spec(spec)
spec.loader.exec_module(LM)
tmp = tempfile.mkdtemp(prefix="membrane_save_test_")
LM.SESS = os.path.join(tmp, "sessions")
LM.HOME = tmp
os.makedirs(os.path.join(tmp, "logs"), exist_ok=True)
fails = []
sid = "save-test-session"
p = LM._state_path(sid)


_held = []
if os.name != "nt":
    # POSIX lets a rename replace a file another handle has open, so the Windows sharing violation the save retries
    # through is stood in for: the rename onto the state file raises Access denied while the hold lasts.
    _real_replace = os.replace

    def _replace(src, dst, *a, **k):
        if _held and os.path.abspath(dst) == os.path.abspath(p):
            raise PermissionError(13, "Access is denied")
        return _real_replace(src, dst, *a, **k)

    LM.os.replace = _replace


def _release(fh):
    _held.clear()
    fh.close()


def state_on_disk():
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def tmp_left():
    return [f for f in os.listdir(LM.SESS) if f.endswith(".tmp")]


# A
ok = LM.save_state(sid, {"calls": 1})
if not (ok is True and state_on_disk().get("calls") == 1 and not tmp_left()):
    fails.append("A: ok=%r state=%r tmp=%r" % (ok, state_on_disk(), tmp_left()))

# B
fh = open(p, encoding="utf-8")
_held.append(1)
threading.Timer(0.06, _release, (fh,)).start()
t0 = time.time()
ok = LM.save_state(sid, {"calls": 2})
dt = time.time() - t0
if not (ok is True and state_on_disk().get("calls") == 2 and not tmp_left()):
    fails.append("B: ok=%r state=%r tmp=%r" % (ok, state_on_disk(), tmp_left()))

# C
fh = open(p, encoding="utf-8")
_held.append(1)
t0 = time.time()
try:
    ok = LM.save_state(sid, {"calls": 3})
    raised = None
except Exception as e:
    ok, raised = None, e
dtc = time.time() - t0
_release(fh)
log = open(os.path.join(tmp, "logs", "live.log"), encoding="utf-8").read() if os.path.exists(
    os.path.join(tmp, "logs", "live.log")) else ""
if not (ok is False and raised is None and dtc < 1.0 and state_on_disk().get("calls") == 2 and not tmp_left()
        and log.count("state write failed") == 1):
    fails.append("C: ok=%r raised=%r dt=%.2f state=%r tmp=%r log=%r" % (ok, raised, dtc, state_on_disk(), tmp_left(), log))

print("FAIL" if fails else "PASS", "- 3 controls (B waited %.2f s, C gave up after %.2f s);" % (dt, dtc),
      "; ".join(fails) if fails else "saves, retries through a 60 ms hold, gives up cleanly on a permanent one")
sys.exit(1 if fails else 0)
