"""test_run_graph_lock.py - run_graph's lock is the operating system's.

    python test_run_graph_lock.py [--module RUN_GRAPH.py] [--old OLD_RUN_GRAPH.py]

The module's HOME and log are pointed at a throwaway folder.
 G1 two runs in one process: A holds, B does not
 G2 B with stale_s 0 still does not hold while A lives (a live run is never broken), and says how long A has held it
 G3 A leaving removes its note; the next run holds
 G4 a run in another PROCESS holds; this process cannot; the other process is killed; this process holds at once
 G5 a note naming a live process (a reused pid) with no lock held: the next run holds
 G6 a run leaving removes only its own note
With --old, the lock before this change refuses when its file names a live pid that holds nothing (the check can fail).
"""
import importlib.machinery
import importlib.util
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
MOD = os.path.abspath(ARGS[ARGS.index("--module") + 1]) if "--module" in ARGS else os.path.join(HERE, "run_graph.py")
OLD = os.path.abspath(ARGS[ARGS.index("--old") + 1]) if "--old" in ARGS else None
fails, ran = [], []
CHILD = r'''
import importlib.machinery, importlib.util, sys, time
path, home = sys.argv[1], sys.argv[2]
loader = importlib.machinery.SourceFileLoader("rg_child", path)
spec = importlib.util.spec_from_file_location("rg_child", path, loader=loader)
m = importlib.util.module_from_spec(spec); loader.exec_module(m)
m.HOME = home
m.log = lambda line: None
lk = m.Lock("graph").__enter__()
print("held" if lk.held else "not held", flush=True)
time.sleep(120)
'''


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def load(path, name):
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def fresh(path, tag):
    td = tempfile.mkdtemp(prefix="rg_lock_%s_" % tag)
    m = load(path, "rg_%s_%d" % (tag, len(ran)))
    m.HOME = td
    logs = []
    m.log = logs.append
    return m, td, logs


def unopenable(path, tag):
    """The lock's folder is a regular file, so the lock file cannot be made. Returns the fast run's
    exit code (None when it returned), its log, its status updates and (held, error) of a lock taken directly."""
    m, td, logs = fresh(path, tag)
    status = {}
    try:
        bad_home = os.path.join(td, "not_a_folder")
        with open(bad_home, "w", encoding="utf-8") as fh:
            fh.write("a file where the lock folder should be")
        m.HOME = bad_home
        lk = m.Lock("graph").__enter__()
        err = (lk.held, getattr(lk, "error", None))
        m.polite = lambda: None
        m.status_update = lambda **kw: status.update(kw)
        argv = sys.argv
        sys.argv = ["run_graph.py", "fast"]
        code = None
        try:
            m.main()
        except SystemExit as exc:
            code = exc.code
        finally:
            sys.argv = argv
        return code, logs, status, err
    finally:
        shutil.rmtree(td, ignore_errors=True)


def main():
    m, td, logs = fresh(MOD, "new")
    try:
        a = m.Lock("graph").__enter__()
        b = m.Lock("graph").__enter__()
        check((a.held, b.held) == (True, False), "G1 A holds, B does not: %s" % ((a.held, b.held),))
        time.sleep(0.05)
        b2 = m.Lock("graph", stale_s=0.0).__enter__()
        check(b2.held is False and any("held for" in x for x in logs),
              "G2 a live run is never broken, and the waiting run says so: %s, log %r" % (b2.held, logs[-1:]))
        a.__exit__(None, None, None)
        note_gone = not os.path.exists(a.who)
        c = m.Lock("graph").__enter__()
        check(note_gone and c.held, "G3 A leaving removed its note (%s); the next run holds (%s)" % (note_gone, c.held))
        c.__exit__(None, None, None)
        child_py = os.path.join(td, "child.py")
        with open(child_py, "w", encoding="utf-8") as fh:
            fh.write(CHILD)
        p = subprocess.Popen([sys.executable, "-s", child_py, MOD, td], stdout=subprocess.PIPE, text=True,
                             creationflags=0x08000000 if os.name == "nt" else 0)
        first = p.stdout.readline().strip()
        blocked = m.Lock("graph").__enter__()
        p.kill()
        p.wait(timeout=30)
        after = m.Lock("graph").__enter__()
        check(first == "held" and blocked.held is False and after.held is True,
              "G4 another process held (%r), this one could not (%s); killed, this one holds (%s)"
              % (first, blocked.held, after.held))
        after.__exit__(None, None, None)
        with open(os.path.join(td, "graph.lock.who"), "w", encoding="utf-8") as fh:
            fh.write("%d %f reused" % (os.getpid(), time.time()))
        d = m.Lock("graph").__enter__()
        check(d.held is True, "G5 a note naming a live pid, with no lock held, does not stop the next run: %s" % d.held)
        with open(d.who, "w", encoding="utf-8") as fh:
            fh.write("someone else's note")
        d.__exit__(None, None, None)
        check(os.path.exists(d.who), "G6 a run leaving does not remove a note that is not its own")
    finally:
        shutil.rmtree(td, ignore_errors=True)
    code, logs7, status7, err7 = unopenable(MOD, "open")
    check(err7 is not None and err7[0] is False and bool(err7[1]),
          "G7 a lock that cannot be opened (its folder is a file): held %s, error %r" % err7)
    check(code == 3 and any("could not be taken" in x for x in logs7) and not any("in progress" in x for x in logs7)
          and "lock_error" in status7,
          "G8 the runner reports it as a failure, not a run in progress: exit %r, log %r, status %s" % (
              code, logs7[-1:], sorted(status7)))
    old_src = open(OLD, encoding="utf-8").read() if OLD else ""
    if OLD and "_os_lock" in old_src and "self.error" not in old_src:
        # N2 is for the OS-lock runner before 29 Sep 19:2x, whose lock had no error field (the pid-file lock before it
        # raises on an unopenable path instead; N1 below is for that one - each negative control runs only on the
        # generation it was written for)
        code_o, logs_o, _s, _e = unopenable(OLD, "open_old")
        check(code_o is None and any("in progress" in x for x in logs_o),
              "N2 the runner before this change calls it a run in progress: exit %r, log %r (the check can fail)" % (
                  code_o, logs_o[-1:]))
    run_n1 = bool(OLD) and "_os_lock" not in old_src   # an OS-lock runner: N1 (the pid-file lock) does not apply
    if run_n1:
        o, otd, _ = fresh(OLD, "old")
        try:
            with open(os.path.join(otd, "graph.lock"), "w", encoding="utf-8") as fh:
                fh.write("%d %f reused" % (os.getpid(), time.time()))
            e = o.Lock("graph").__enter__()
            check(e.held is False, "N1 the lock before this change refuses when its file names a live pid that holds "
                  "nothing: held %s (the check can fail)" % e.held)
        finally:
            shutil.rmtree(otd, ignore_errors=True)
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
