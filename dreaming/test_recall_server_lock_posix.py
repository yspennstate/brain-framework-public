"""test_recall_server_lock_posix.py - the resident recall server's single-instance lock and posix_proc, on Linux/macOS.

    python test_recall_server_lock_posix.py

Copies brain_recall_server.py and posix_proc.py into a temporary folder (so no real server starts), then checks:
posix_proc reads the process table; a second server exits 0 after its ~40 s retry window while another process holds
the flock; after that holder is killed with SIGKILL the kernel drops the lock and the next server gets past it at
once (it then stops in main() because brain.py is deliberately absent from the folder). Skipped on Windows, where
the lock is msvcrt's. Takes about 45 seconds.
"""
import os, shutil, signal, subprocess, sys, tempfile, time

if os.name == "nt":
    print("SKIP: POSIX only")
    sys.exit(0)

SRC = os.path.dirname(os.path.abspath(__file__))
HERE = tempfile.mkdtemp(prefix="brs_lock_")
for name in ("brain_recall_server.py", "posix_proc.py"):
    shutil.copy(os.path.join(SRC, name), HERE)
sys.path.insert(0, HERE)
import posix_proc as pp

res = []


def ok(name, cond, extra=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (" " + extra if extra else ""), flush=True)


t = pp.table()
ok("table has this pid", os.getpid() in t, "rows=%d" % len(t))
ok("table row shape", len(t.get(os.getpid(), ())) == 4)
ok("parent_map ppid", pp.parent_map().get(os.getpid(), (None,))[0] == os.getppid())
ok("alive(self)", pp.alive(os.getpid()))
ok("alive(pid 1) (EPERM counts)", pp.alive(1))
ok("dead pid not alive", not pp.alive(4194300))
ok("idle_seconds is a float", isinstance(pp.idle_seconds(), float))

state = os.path.join(HERE, "brain_recall_server")
os.makedirs(state, exist_ok=True)
lockp = os.path.join(state, "server.lock")
holder_src = ("import fcntl,time;f=open(%r,'a+b');fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB);"
              "print('held',flush=True);time.sleep(120)" % lockp)
h = subprocess.Popen([sys.executable, "-c", holder_src], stdout=subprocess.PIPE, text=True)
ok("holder took the lock", h.stdout.readline().strip() == "held")
srv = os.path.join(HERE, "brain_recall_server.py")
t0 = time.time()
r = subprocess.run([sys.executable, srv], capture_output=True, text=True, timeout=90)
dt = time.time() - t0
ok("second server exits 0 while the lock is held", r.returncode == 0 and "Traceback" not in r.stderr,
   "rc=%d %.1fs" % (r.returncode, dt))
ok("it waited the ~40 s retry window", 38 <= dt <= 60, "%.1fs" % dt)
os.kill(h.pid, signal.SIGKILL)
h.wait()
t0 = time.time()
r = subprocess.run([sys.executable, srv], capture_output=True, text=True, timeout=90)
dt = time.time() - t0
ok("after SIGKILL the next server takes the lock at once and reaches main()",
   "brain" in r.stderr and r.returncode != 0 and dt < 10, "rc=%d %.1fs" % (r.returncode, dt))
shutil.rmtree(HERE, ignore_errors=True)
print("RESULT %d/%d" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
