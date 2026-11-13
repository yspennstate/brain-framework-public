"""run_dream starts a heavy step's cap when the step's waiter holds heavy_job.lock, and defers a step that never gets it.

Points run_step at a throwaway folder with a fake heavy-lock runner (a zsh script that writes "<its pid> <what>" to
a test lock, or never does, then runs the command) and a step script that sleeps. Cases: the lock is taken, the
step outlives its cap counted from launch but not from the lock: ok. The lock never comes: exit 75 and a STEP
DEFERRED line. DREAM_HEAVY_WAIT_S=0: the old rule, the cap counts the wait: exit 124. A step not in HEAVY_STEPS
never waits.
Run: python3 test_run_dream_heavy_wait.py (exit 0 = pass)
"""
import importlib
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

RUNNER = """#!/bin/zsh
what=$1; shift
sleep ${FAKE_DELAY:-0}
if [ -n "$FAKE_GRAB" ]; then print -r -- "$$ $what" > "$HEAVY_LOCK"; fi
"$@"
rc=$?
rm -f "$HEAVY_LOCK"
exit $rc
"""


def main():
    if os.name == "nt":
        print("SKIP run_dream.py is the Mac dream runner (fcntl, zsh): nothing to test on Windows")
        return 0
    tmp = tempfile.mkdtemp(prefix="heavywait_")
    fails = []
    try:
        lock = os.path.join(tmp, "heavy_job.lock")
        os.environ["HEAVY_LOCK"] = lock
        rd = importlib.import_module("run_dream")
        if not hasattr(rd, "_holds_heavy_lock"):
            print("FAIL run_dream has no _holds_heavy_lock")
            return 1
        runner = os.path.join(tmp, "fake_heavy.sh")
        with open(runner, "w") as f:
            f.write(RUNNER)
        with open(os.path.join(tmp, "nap.py"), "w") as f:
            f.write("import sys, time\ntime.sleep(float(sys.argv[1]))\n")
        rd.BASE = tmp
        rd.LOG = os.path.join(tmp, "dream.log")
        rd.HEAVY_RUN = runner
        rd.HEAVY_LOCK = lock
        heavy = sorted(rd.HEAVY_STEPS)[0]

        def run(name, nap, cap, wait, grab, delay):
            rd.HEAVY_WAIT_S = wait
            os.environ["FAKE_DELAY"] = str(delay)
            if grab:
                os.environ["FAKE_GRAB"] = "1"
            else:
                os.environ.pop("FAKE_GRAB", None)
            t = time.time()
            rc = rd.run_step(name, ["nap.py", str(nap)], cap=cap)
            return rc, time.time() - t

        # lock comes after 4 s, the step then runs 3 s under a 5 s cap: 7 s from launch, ok from the lock
        rc, dt = run(heavy, 3, 5, 20, True, 4)
        if rc != 0:
            fails.append("lock taken late, step inside its cap from the lock: rc %s, want 0 (%.1fs)" % (rc, dt))
        # the lock never comes: deferred after the wait, not capped, not failed
        rc, dt = run(heavy, 1, 60, 4, False, 30)
        if rc != 75:
            fails.append("lock never taken: rc %s, want 75" % rc)
        if dt > 40:
            fails.append("lock never taken: took %.1fs, the wait is 4 s" % dt)
        # wait switched off: the cap counts the queue, as before
        rc, dt = run(heavy, 3, 5, 0, True, 4)
        if rc != 124:
            fails.append("DREAM_HEAVY_WAIT_S=0: rc %s, want 124 (cap counts the wait)" % rc)
        # a step outside HEAVY_STEPS runs straight away under its cap
        rc, dt = run("not heavy", 1, 10, 4, False, 30)
        if rc != 0 or dt > 8:
            fails.append("non-heavy step: rc %s in %.1fs, want 0 at once" % (rc, dt))
        text = open(rd.LOG, encoding="utf-8").read()
        if "STEP DEFERRED: %s never got heavy_job.lock" % heavy not in text:
            fails.append("no STEP DEFERRED line in the log")
        if "got heavy_job.lock after" not in text:
            fails.append("no 'got heavy_job.lock after' line in the log")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for f in fails:
        print("FAIL " + f)
    print("RESULT %s (%d failures)" % ("PASS" if not fails else "FAIL", len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    # Discovery is not execution: a help request exits before any work.
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print(__doc__ or os.path.basename(__file__))
        raise SystemExit(0)
    sys.exit(main())
