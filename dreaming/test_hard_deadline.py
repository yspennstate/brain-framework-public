"""Controls for the math hook's hard wall-clock ceiling: a slow body is abandoned and can write nothing afterwards.

 D1 the ceiling is 60 s for an ordinary prompt, 85 s for a headless-worker prompt, and 0 (off) when the env says 0
 D2 a body that outlives the ceiling: main() returns "abandoned" at the ceiling, and the late write is refused
 D3 a body that finishes in time: its one write is delivered and main() returns None
 D4 with the ceiling off the body runs on the calling thread, as before the ceiling existed

    python test_hard_deadline.py      (about three seconds; ALL PASS at the end)
"""
import importlib
import io
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
results = []


def check(ok, what):
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + what)


def fresh(env):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    sys.modules.pop("math_sense", None)
    return importlib.import_module("math_sense")


def run(m, body, prompt="a prompt about the central limit theorem"):
    """Run main() with `body` in place of the hook body; returns (rc, seconds, captured output)."""
    m._main_body = body
    m._silence_note = lambda *a, **k: None
    old_in, old_out = sys.stdin, sys.stdout
    cap = io.StringIO()
    sys.stdin, sys.stdout = io.StringIO(json.dumps({"prompt": prompt, "session_id": "test"})), cap
    t0 = time.time()
    try:
        rc = m.main()
    finally:
        sys.stdin, sys.stdout = old_in, old_out
    return rc, time.time() - t0, cap


def main():
    m = fresh({})
    got = (m._hard_deadline_s("an ordinary prompt"), m._hard_deadline_s("[headless-worker] a task"))
    m = fresh({"MATHBRAIN_HARD_DEADLINE_S": "0"})
    off = m._hard_deadline_s("an ordinary prompt")
    check(got == (60.0, 85.0) and off == 0.0, "D1 ceilings %r, off %r" % (got, off))

    m = fresh({"MATHBRAIN_HARD_DEADLINE_S": "0.5"})
    done = threading.Event()
    late = {}

    def slow(data, out):
        time.sleep(1.5)
        late["written"] = m._emit(out, "LATE")
        done.set()

    rc, dt, cap = run(m, slow)
    done.wait(5)
    check(rc == "abandoned" and dt < 1.2 and late.get("written") is False and cap.getvalue() == "",
          "D2 slow body: rc %r after %.2f s, late write refused %s, output %r"
          % (rc, dt, late.get("written") is False, cap.getvalue()))

    m = fresh({"MATHBRAIN_HARD_DEADLINE_S": "5"})
    rc, dt, cap = run(m, lambda data, out: m._emit(out, "BLOCK"))
    check(rc is None and cap.getvalue() == "BLOCK", "D3 body in time: rc %r, output %r" % (rc, cap.getvalue()))

    m = fresh({"MATHBRAIN_HARD_DEADLINE_S": "0"})
    seen = {}

    def body(data, out):
        seen["thread"] = threading.current_thread() is threading.main_thread()
        m._emit(out, "BLOCK")

    rc, dt, cap = run(m, body)
    check(seen.get("thread") is True and cap.getvalue() == "BLOCK",
          "D4 ceiling off: body on the calling thread %s, output %r" % (seen.get("thread"), cap.getvalue()))

    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
