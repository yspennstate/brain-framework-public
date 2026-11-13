"""test_membrane_session_lock.py - the membrane's per-session lock holds for as long as its holder lives, is released when
the holder dies, and cannot be released by anyone else.

    python test_membrane_session_lock.py [--module LIVE_MEMBRANE.py] [--old OLD_LIVE_MEMBRANE.py]

Real separate processes, each importing the module under test with its session folder pointed at a throwaway directory:
 L1 A holds the session; B cannot take it
 L2 A holds it and the lock file is aged 61 seconds, the age the old lock treated as dead; B still cannot take it
 L3 A ends without unlocking; B takes the session at once
 L4 A holds, B waits; A releases and B takes it; A's second release, and a release by a process that never held it, leave
    B's lock alone: C cannot take the session
With --old, the membrane before this change must let B take a live A's aged lock (N1) and, after A's release deletes B's
lock, let C take it too while B still holds it (N2) (the check can fail).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = sys.argv[1:]
MOD = os.path.abspath(ARGS[ARGS.index("--module") + 1]) if "--module" in ARGS else os.path.join(HERE, "live_membrane.py")
OLD = os.path.abspath(ARGS[ARGS.index("--old") + 1]) if "--old" in ARGS else None
SID = "lockfixture-0000-4000-8000-000000000000"
fails, ran = [], []

HELPER = r'''
import importlib.machinery, importlib.util, os, sys, time
mod, sess, sid, tag, mode, wait = sys.argv[1:7]
sys.path.insert(0, os.path.dirname(mod))
ld = importlib.machinery.SourceFileLoader("lm_" + tag, mod)
sp = importlib.util.spec_from_file_location("lm_" + tag, mod, loader=ld)
m = importlib.util.module_from_spec(sp); ld.exec_module(m)
m.SESS = sess
flag = lambda name, text="": open(os.path.join(sess, "%s.%s" % (tag, name)), "w").write(text)
if mode == "release_only":                      # a process that never held the lock calls unlock
    m.unlock_session(m._state_path(sid) + ".lock")
    flag("done"); sys.exit(0)
p = m.lock_session(sid, wait=float(wait))
flag("result", "got" if p else "none")
if not p:
    sys.exit(0)
if mode == "die":
    os._exit(0)                                 # ends holding the lock, no unlock
while not os.path.exists(os.path.join(sess, tag + ".release")):
    time.sleep(0.05)
m.unlock_session(p)
flag("released")
if mode == "hold_twice":                        # a second release after someone else took the session
    while not os.path.exists(os.path.join(sess, tag + ".release2")):
        time.sleep(0.05)
    m.unlock_session(p)
    flag("released2")
'''


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


class Box:
    def __init__(self, mod):
        self.mod = mod
        self.dir = tempfile.mkdtemp(prefix="membrane_lock_")
        self.helper = os.path.join(self.dir, "helper.py")
        with open(self.helper, "w", encoding="utf-8") as fh:
            fh.write(HELPER)
        self.sess = os.path.join(self.dir, "sessions")
        os.makedirs(self.sess)
        self.procs = []

    def start(self, tag, mode="hold", wait=0.3):
        flags = 0x08000000 if os.name == "nt" else 0          # CREATE_NO_WINDOW
        p = subprocess.Popen([sys.executable, self.helper, self.mod, self.sess, SID, tag, mode, str(wait)],
                             creationflags=flags)
        self.procs.append(p)
        return p

    def result(self, tag, timeout=20.0):
        f = os.path.join(self.sess, tag + ".result")
        end = time.time() + timeout
        while time.time() < end:
            if os.path.exists(f):
                time.sleep(0.05)
                with open(f, encoding="utf-8") as fh:
                    return fh.read()
            time.sleep(0.05)
        return "timeout"

    def wait_flag(self, name, timeout=20.0):
        end = time.time() + timeout
        while time.time() < end:
            if os.path.exists(os.path.join(self.sess, name)):
                return True
            time.sleep(0.05)
        return False

    def release(self, tag, which="release"):
        open(os.path.join(self.sess, "%s.%s" % (tag, which)), "w").close()

    def age_lock(self, seconds):
        p = os.path.join(self.sess, "%s.json.lock" % SID)
        t = time.time() - seconds
        os.utime(p, (t, t))

    def close(self):
        for p in self.procs:
            try:
                p.kill()
            except Exception:
                pass
        for p in self.procs:
            try:
                p.wait(timeout=10)
            except Exception:
                pass
        shutil.rmtree(self.dir, ignore_errors=True)


def cases(mod):
    r = {}
    b = Box(mod)
    try:
        b.start("A")
        r["A1"] = b.result("A")
        b.start("B")
        r["L1"] = b.result("B")
        b.age_lock(61)
        b.start("B2")
        r["L2"] = b.result("B2")
        b.release("A")
        b.wait_flag("A.released")
    finally:
        b.close()
    b = Box(mod)
    try:
        a = b.start("A", mode="die")
        r["A3"] = b.result("A")
        a.wait(timeout=20)
        b.start("B")
        r["L3"] = b.result("B")
        b.release("B")
    finally:
        b.close()
    b = Box(mod)
    try:
        b.start("A", mode="hold_twice")
        r["A4"] = b.result("A")
        b.start("B", wait=15.0)                 # waits for A's release
        time.sleep(0.5)
        b.release("A")
        r["L4_B"] = b.result("B")
        b.release("A", "release2")              # A releases again
        b.wait_flag("A.released2")
        b.start("X", mode="release_only")        # and a process that never held it releases
        b.wait_flag("X.done")
        b.start("C")
        r["L4_C"] = b.result("C")
        b.release("B")
    finally:
        b.close()
    return r


def old_cases(mod):
    r = {}
    b = Box(mod)
    try:
        b.start("A")
        r["A1"] = b.result("A")
        b.age_lock(61)
        b.start("B")
        r["N1"] = b.result("B")                 # the old lock takes over a live holder's aged lock
        b.release("A")                          # A's release removes whatever sits at the path: B's lock
        b.wait_flag("A.released")
        b.start("C")
        r["N2"] = b.result("C")                 # and C takes the session while B still holds it
        b.release("B")
    finally:
        b.close()
    return r


def main():
    r = cases(MOD)
    check(r["A1"] == "got" and r["L1"] == "none", "L1 A holds the session (%s); B cannot take it (%s)" % (r["A1"], r["L1"]))
    check(r["L2"] == "none", "L2 the lock file aged 61 s while A lives: B still cannot take it (%s)" % r["L2"])
    check(r["A3"] == "got" and r["L3"] == "got", "L3 A ended holding the lock (%s): B takes it at once (%s)" % (
        r["A3"], r["L3"]))
    check(r["A4"] == "got" and r["L4_B"] == "got" and r["L4_C"] == "none",
          "L4 B takes the session after A releases (%s); A's second release and a stranger's release leave it held: C "
          "cannot take it (%s)" % (r["L4_B"], r["L4_C"]))
    if OLD:
        o = old_cases(OLD)
        check(o["N1"] == "got", "N1 the membrane before this change lets B take a live A's aged lock (%s) (the check can "
              "fail)" % o["N1"])
        check(o["N2"] == "got", "N2 ...and after A's release deletes B's lock, C takes the session while B holds it (%s)"
              % o["N2"])
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
