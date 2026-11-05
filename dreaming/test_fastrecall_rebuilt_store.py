"""test_fastrecall_rebuilt_store.py - the fast path never answers from a state the store has left behind.

    python test_fastrecall_rebuilt_store.py

Builds a three-claim store in a temporary folder, runs math_recall.py once so the fast path prepares its state,
then deletes the store and builds a one-claim store with different text at the same path. The next call must not
print a claim that is no longer in the store, and its output must match the call with the fast path off
(MATH_RECALL_FAST=0). A second check covers the ordinary case: one claim appended to the store is found by the
next call. HOME points into the temporary folder, so the prepared states never touch the real cache.
Needs only the math_brain library and its dependency, cryptography. Takes about 20 seconds.
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
UP = os.path.dirname(HERE)
BUILD = next((p for p in (os.path.join(UP, "mathbrain", "src"), os.path.join(UP, "src"))
              if os.path.isdir(os.path.join(p, "math_brain"))), None)
if BUILD is None:
    print("SKIP: math_brain library not found beside this folder")
    sys.exit(0)
WORK = tempfile.mkdtemp(prefix="fr_rebuilt_")
STORE = os.path.join(WORK, "st.sqlite3")
ENV = dict(os.environ, MATHBRAIN_BUILD_SRC=BUILD, MATHBRAIN_STORE=STORE, PYTHONPATH=BUILD,
           HOME=WORK, USERPROFILE=WORK, MATH_RECALL_SERVER_DISABLE="1")
for k in [k for k in ENV if k.startswith("MATH_RECALL_") and k != "MATH_RECALL_SERVER_DISABLE"]:
    del ENV[k]
OLD = ["Every finite group of prime order is cyclic.",
       "Every cyclic group of prime order is simple and abelian.",
       "A compact subset of a Hausdorff space is closed."]
NEW = ["Every bounded monotone sequence of real numbers converges."]
ADDED = "The fundamental group of the circle is the integers."
TOOL = os.path.join(HERE, "math_recall.py")
res = []


def ok(name, cond, extra=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (" " + extra if extra else ""), flush=True)


def run(args, env=ENV):
    return subprocess.run([sys.executable] + args, cwd=WORK, env=env, capture_output=True, text=True, timeout=300)


def make_store(claims):
    if os.path.exists(STORE):
        os.remove(STORE)
    r = run(["-m", "math_brain.cli", "init", STORE])
    for c in claims:
        if r.returncode == 0:
            r = run(["-m", "math_brain.cli", "add-claim", STORE, c, "--kind", "concept", "--domain", "demo"])
    return r.returncode == 0


ok("first store built", make_store(OLD))
first = run([TOOL, "group of prime order is cyclic"])
ok("first call finds the cyclic claim", first.returncode == 0 and "prime order is cyclic" in first.stdout)

ok("store rebuilt smaller at the same path", make_store(NEW))
fast = run([TOOL, "group of prime order is cyclic"])
slow = run([TOOL, "group of prime order is cyclic"], dict(ENV, MATH_RECALL_FAST="0"))
ok("no claim from the old store is printed", fast.returncode == 0 and "prime order" not in fast.stdout,
   fast.stdout.strip()[:160])
ok("fast output matches the fast path off", fast.stdout == slow.stdout)

run(["-m", "math_brain.cli", "add-claim", STORE, ADDED, "--kind", "concept", "--domain", "demo"])
grown = run([TOOL, "fundamental group of the circle"])
ok("a claim appended afterwards is found", grown.returncode == 0 and "circle is the integers" in grown.stdout)

shutil.rmtree(WORK, ignore_errors=True)
print("RESULT %d/%d" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
