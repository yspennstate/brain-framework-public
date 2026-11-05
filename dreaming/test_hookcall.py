"""Controls for hookcall.py: the cap follows the hook's budget, a stall is asked once more, a second stall is marked.

    python test_hookcall.py      (about 10 s; prints PASS / FAIL per control and ALL PASS at the end)
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hookcall import cap_for, run_hook  # noqa: E402

FAKE = r'''
import os, sys, time
data = sys.stdin.read()
flag = os.environ.get("FAKE_FLAG")
mode = os.environ.get("FAKE_MODE", "echo")
if mode == "stall":
    time.sleep(30)
if mode == "stall_once" and flag and not os.path.exists(flag):
    open(flag, "w").close()
    time.sleep(30)
sys.stdout.write("got:" + data)
'''

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + ("  | " + detail if detail else ""))


def main():
    d = tempfile.mkdtemp(prefix="hookcall_test_")
    fake = os.path.join(d, "fake_hook.py")
    open(fake, "w", encoding="utf-8").write(FAKE)
    base = {k: v for k, v in os.environ.items() if not k.startswith("MATHBRAIN_")}

    c = cap_for(dict(base, MATHBRAIN_TIME_BUDGET_S="300", MATHBRAIN_INTEGRITY_GRACE_S="60"))
    check("cap: a rehearsal's 300 s budget and 60 s grace give 420 s", c == 420.0, "%.0f" % c)
    c = cap_for(dict(base))
    check("cap: an interactive prompt with no env gets 30 + 2 + 60", c == 92.0, "%.0f" % c)
    c = cap_for(dict(base), prompt="[headless-worker] prove it")
    check("cap: a headless-worker prompt gets 50 + 8 + 60", c == 118.0, "%.0f" % c)
    c = cap_for(dict(base, MATHBRAIN_TIME_BUDGET_S="5", MATHBRAIN_INTEGRITY_GRACE_S="1"))
    check("cap: never under the 90 s floor", c == 90.0, "%.0f" % c)
    c = cap_for(dict(base, MATHBRAIN_TIME_BUDGET_S="not a number"))
    check("cap: a malformed budget falls back to the default", c == 92.0, "%.0f" % c)

    r = run_hook([sys.executable, fake], "hello", dict(base, FAKE_MODE="echo"), cap=20)
    check("fast call: output back, one attempt, not timed out",
          r.stdout == "got:hello" and r.attempts == 1 and not r.timed_out and r.returncode == 0, repr(r.row_fields()))

    r = run_hook([sys.executable, fake], "x", dict(base, FAKE_MODE="stall"), cap=2)
    check("stall twice: marked timed out after two attempts, no output",
          r.timed_out and r.attempts == 2 and r.stdout == "" and r.returncode is None, repr(r.row_fields()))

    flag = os.path.join(d, "stalled_once")
    r = run_hook([sys.executable, fake], "again", dict(base, FAKE_MODE="stall_once", FAKE_FLAG=flag), cap=3)
    check("stall once: the second try answers and the row says it was retried",
          r.stdout == "got:again" and r.attempts == 2 and r.retried and not r.timed_out, repr(r.row_fields()))

    r = run_hook([sys.executable, fake], "x", dict(base, FAKE_MODE="stall"), cap=2, retries=0)
    check("retries=0: one attempt only", r.timed_out and r.attempts == 1, repr(r.row_fields()))

    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
