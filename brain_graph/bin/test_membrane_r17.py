"""test_membrane_r17.py - live_membrane.run from load to save as one transaction:
parallel calls of one session could each serve the same line, a failed save still
returned the line, and a save failure outside the rename could leave a served row for a line that never printed.

    python test_membrane_r17.py [PATH_TO_live_membrane.py]

run() is driven end to end on a temp home (sessions, served.jsonl, fires.jsonl, logs); the evaluator and decide are
faked so one line, mem:x, is always a candidate and decide keeps a two-hour refractory in the session state as the real
one does. Controls:
 T0 in sequence: the first call serves the line (one served row), the second is inside the refractory, no lock left
 T1 two calls of one session at once (the evaluation takes 0.3 s): the line is shown at most once, one served row
 T2 every rename refused (PermissionError): no line, no served row, 'withheld' logged, state unchanged, no exception
 T3 the temporary file cannot be written (ENOSPC): no line, no served row, 'withheld' logged, no .tmp left
 T4 the rename fails with EIO: no line, no served row, 'withheld' logged
 T5 a lock left 120 s ago by a dead call: taken over, the line served, no lock left
 T6 a lock held right now by another call: no line within a second, that lock left in place (held through the module's
    own lock since the lock became the operating system's, 28 Sep)
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import errno
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import types

H = os.path.expanduser("~")
MEMB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(H, ".claude", "brain_graph", "bin", "live_membrane.py")
sys.path.insert(0, os.path.join(H, ".claude", "brain_graph", "bin"))
spec = importlib.util.spec_from_file_location("lm_r17", MEMB)
LM = importlib.util.module_from_spec(spec)
spec.loader.exec_module(LM)

tmp = tempfile.mkdtemp(prefix="membrane_r17_test_")
LM.HOME = tmp
LM.SESS = os.path.join(tmp, "sessions")
LM.SERVED = os.path.join(tmp, "served.jsonl")
LM.FIRES = os.path.join(tmp, "fires.jsonl")
LM.STOP = os.path.join(tmp, "STOP")
LM.SIDECAR = os.path.join(tmp, "sidecar.sqlite")
LM.PRECISION = os.path.join(tmp, "precision.json")
os.makedirs(os.path.join(tmp, "logs"), exist_ok=True)
open(LM.SIDECAR, "w").close()
TP = os.path.join(tmp, "transcript.jsonl")
with open(TP, "w", encoding="utf-8") as fh:
    fh.write("{}\n")

X = types.ModuleType("extract_touch_events")
X.seed_from_transcript = lambda tp, now=None, window=None, k=None, tail_bytes=None: [("mem:x", 1.0)]
X.his_text = lambda d, s: ""
X.words = lambda s, cap=40: []
sys.modules["extract_touch_events"] = X
DELAY = {"s": 0.0}


class FakeLive:
    def __init__(self):
        self.db, self.meta = None, {"built": 0}

    def evaluate(self, items, now, seen_slugs=None, pol=None, base_p=0.2):
        time.sleep(DELAY["s"])
        return {"mem": [], "err": [], "n_items": len(items)}

    def fix_memory(self, *a, **k):
        return None


def fake_decide(ev, now, state, pol):
    srv = state.setdefault("served", {})
    if now - srv.get("mem:x", 0) < 7200:
        return [], []
    srv["mem:x"] = now
    return ["[brain] x"], [("mem", "x", [])]


LM.Live = FakeLive
LM.decide = fake_decide
LM.receiver_kind = lambda st, tp: None
LM.order_query = lambda items, ask: ["q"]
LM.order_candidates = lambda *a, **k: []
LM.stale_orders = lambda *a, **k: []
LM.word_candidates = lambda *a, **k: []

fails = []


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    if not ok:
        fails.append(what)


def payload(sid):
    return {"session_id": sid, "transcript_path": TP, "hook_event_name": "PostToolUse"}


def rows(path, sid):
    if not os.path.exists(path):
        return []
    out = []
    for ln in open(path, encoding="utf-8"):
        r = json.loads(ln)
        if r.get("sid") == sid:
            out.append(r)
    return out


def call(sid):
    try:
        return LM.run(payload(sid), force=True)
    except Exception as e:                      # run() must never raise
        return "RAISED %s" % e


def lock_of(sid):
    return LM._state_path(sid) + ".lock"


def lock_held(sid):
    """True while some descriptor holds the session's lock. Windows removes the file at unlock; elsewhere the file
    stays (unlock_session explains why), so the lock itself is tried from a descriptor of our own."""
    p = lock_of(sid)
    if not os.path.exists(p):
        return False
    if os.name == "nt" or not hasattr(LM, "_os_lock"):
        return True
    fd = os.open(p, os.O_RDWR)
    try:
        LM._os_lock(fd, True)
    except OSError:
        os.close(fd)
        return True
    LM._os_lock(fd, False)
    os.close(fd)
    return False


def log_text():
    p = os.path.join(tmp, "logs", "live.log")
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


# T0
r1, r2 = call("s0"), call("s0")
check(r1 == "[brain] x" and r2 == "" and len(rows(LM.SERVED, "s0")) == 1 and not lock_held("s0"),
      "T0 in sequence: served once, refractory holds, no lock left (%r, %r, %d rows)" % (r1, r2, len(rows(LM.SERVED, "s0"))))

# T1
DELAY["s"] = 0.3
out = []
ts = [threading.Thread(target=lambda: out.append(call("s1"))) for _ in range(2)]
for t in ts:
    t.start()
for t in ts:
    t.join()
DELAY["s"] = 0.0
shown = sum(1 for r in out if r == "[brain] x")
check(shown == 1 and len(rows(LM.SERVED, "s1")) == 1 and not any(str(r).startswith("RAISED") for r in out),
      "T1 two calls at once: shown %d time(s), %d served row(s) (want 1 and 1)" % (shown, len(rows(LM.SERVED, "s1"))))

# T2-T4: a failure at a chosen point of the save
real_replace, real_dump = os.replace, json.dump


def failing_replace(exc):
    def f(a, b, *args, **kw):
        if os.path.dirname(os.path.abspath(b)) == os.path.abspath(LM.SESS) and b.endswith(".json"):
            raise exc
        return real_replace(a, b, *args, **kw)
    return f


def failing_dump(obj, fh, *args, **kw):
    if getattr(fh, "name", "").endswith(".tmp"):
        raise OSError(errno.ENOSPC, "No space left on device")
    return real_dump(obj, fh, *args, **kw)


for tag, sid, patch in (("T2 every rename refused", "s2", ("replace", PermissionError(13, "Access is denied"))),
                        ("T3 temp write on a full disk", "s3", ("dump", None)),
                        ("T4 the rename fails with EIO", "s4", ("replace", OSError(errno.EIO, "Input/output error")))):
    before = log_text().count("state write failed")
    if patch[0] == "replace":
        os.replace = failing_replace(patch[1])
    else:
        json.dump = failing_dump
    try:
        r = call(sid)
    finally:
        os.replace, json.dump = real_replace, real_dump
    ev = rows(LM.FIRES, sid)
    withheld = bool(ev) and bool(ev[-1].get("withheld")) and not ev[-1].get("served")
    left = [f for f in os.listdir(LM.SESS) if f.endswith(".tmp")] if os.path.isdir(LM.SESS) else []
    check(r == "" and not rows(LM.SERVED, sid) and withheld and not left and not os.path.exists(LM._state_path(sid))
          and log_text().count("state write failed") == before + 1 and not lock_held(sid),
          "%s: line %r, served rows %d, withheld logged %s, tmp left %d" % (
              tag, r, len(rows(LM.SERVED, sid)), withheld, len(left)))

# T5
os.makedirs(LM.SESS, exist_ok=True)
with open(lock_of("s5"), "w") as fh:
    fh.write("99999 0")
old = time.time() - 120
os.utime(lock_of("s5"), (old, old))
r = call("s5")
check(r == "[brain] x" and not lock_held("s5"), "T5 a dead call's lock is taken over (%r)" % r)

# T6. Since 28 Sep (interactive audit IA-20260928-03) the lock is the operating system's, held on an open file: a file on
# its own is no longer a lock, so a live holder is simulated by holding the session through the module's own lock_session
# (a second descriptor cannot take it, in this process or another). The old protocol's simulation stays for old modules.
held = None
if hasattr(LM, "_os_lock"):
    held = LM.lock_session("s6")
else:
    with open(lock_of("s6"), "w") as fh:
        fh.write("12345 0")
t0 = time.time()
r = call("s6")
dt = time.time() - t0
check(r == "" and dt < 1.0 and os.path.exists(lock_of("s6")) and not rows(LM.SERVED, "s6")
      and (held is not None or not hasattr(LM, "_os_lock")),
      "T6 a live lock: nothing served, gave way after %.2f s, the lock left in place" % dt)
if held:
    LM.unlock_session(held)

print("FAIL" if fails else "PASS", "- %d of 7 controls" % (7 - len(fails)))
sys.exit(1 if fails else 0)
