"""test_math_recall_on_demand_posix.py - the math recall client starts its own server on Linux/macOS.

    python framework/dreaming/test_math_recall_on_demand_posix.py

Builds the five-claim store test_math_recall_server_posix.py uses, in a temporary folder with this folder's .py files
copied beside it, and checks: the first call with no server runs cold, prints the same text as a call with the server
disabled, and starts a server; a second call inside the minute starts no second one; the started server publishes
endpoint.json and a later call through it prints the same text; with MATH_RECALL_SPAWN=0 nothing starts; a server
started with --idle-exit leaves and removes endpoint.json; a server started with --exit-on-stale leaves once a code
file changes. Skips (exit 0) on Windows and when the server's own probe refuses the box (memory or disk floor).
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import json, os, shutil, subprocess, tempfile, time

if os.name == "nt":
    print("SKIP: POSIX only")
    sys.exit(0)

SRC = os.path.dirname(os.path.abspath(__file__))
BUILD = next(p for p in (os.path.join(os.path.dirname(SRC), "mathbrain", "src"),
                         os.path.join(os.path.dirname(SRC), "src")) if os.path.isdir(p))
WORK = tempfile.mkdtemp(prefix="mrs_ondemand_")
HERE = os.path.join(WORK, "dreaming")
os.makedirs(HERE)
for name in os.listdir(SRC):
    if name.endswith(".py") and not name.startswith("test_"):
        shutil.copy(os.path.join(SRC, name), HERE)
STORE = os.path.join(WORK, "st.sqlite3")
ENV = dict(os.environ, MATHBRAIN_BUILD_SRC=BUILD, MATHBRAIN_STORE=STORE, PYTHONPATH=BUILD)
for k in [k for k in ENV if k.startswith("MATH_RECALL_")]:
    del ENV[k]
CLAIMS = [
    "Every finite group of prime order is cyclic.",
    "The fundamental group of the circle is the integers.",
    "A compact subset of a Hausdorff space is closed.",
    "Every bounded monotone sequence of real numbers converges.",
    "The Euler characteristic of a closed orientable surface of genus g is 2 - 2g.",
]
QUERY = ["group of prime order is cyclic", "--or", "finite cyclic group"]
TOOL = os.path.join(HERE, "math_recall.py")
ENDPOINT = os.path.join(HERE, "endpoint.json")
STAMP = os.path.join(HERE, "spawn.stamp")
res = []


def ok(name, cond, extra=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (" " + extra if extra else ""), flush=True)


def run(args, env, timeout=180):
    t0 = time.time()
    r = subprocess.run([sys.executable] + args, cwd=WORK, env=env, capture_output=True, text=True, timeout=timeout)
    return r, time.time() - t0


def servers():
    """pids of recall_server.py processes serving this test's folder"""
    out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
    return [int(l.split()[0]) for l in out.splitlines() if os.path.join(HERE, "recall_server.py") in l]


def wait(cond, secs):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.25)
    return cond()


def gate_open():
    """the server's own probe (memory and disk floors), read the way the server reads it"""
    r = subprocess.run([sys.executable, "-c", "import server_safety as s; print(s.resources()['allowed'])"], cwd=HERE,
                       env=ENV, capture_output=True, text=True)
    return r.stdout.strip() == "1"


def ok_or_gate(name, cond, extra=""):
    """a start the floor refused is a skip, not a failure (the box filled up during the test)"""
    if not cond and not gate_open():
        print("SKIP " + name + " (the server's probe refuses this box now: memory or disk floor)", flush=True)
        return
    ok(name, cond, extra)


def stop_all():
    open(os.path.join(HERE, "SERVER_STOP"), "w").close()
    wait(lambda: not servers(), 30)
    for pid in servers():
        os.kill(pid, 9)
    os.remove(os.path.join(HERE, "SERVER_STOP"))


try:
    if not gate_open():
        print("SKIP: the server's probe refuses this box (memory or disk floor)")
        sys.exit(0)
    r, _ = run(["-m", "math_brain.cli", "init", STORE], ENV)
    for c in CLAIMS:
        r, _ = run(["-m", "math_brain.cli", "add-claim", STORE, c, "--kind", "concept", "--domain", "demo"], ENV)
    ok("store built", r.returncode == 0 and os.path.exists(STORE), r.stderr.strip()[-200:])
    off = dict(ENV, MATH_RECALL_SERVER_DISABLE="1")
    run([TOOL] + QUERY, off)                       # builds the prepared state once
    direct, t_direct = run([TOOL] + QUERY, off)
    ok("direct call prints a result", direct.returncode == 0 and "cyclic" in direct.stdout, "%.2fs" % t_direct)
    ok("server disabled: nothing started", not servers() and not os.path.exists(STAMP))

    r, _ = run([TOOL] + QUERY, dict(ENV, MATH_RECALL_SPAWN="0"))
    ok("MATH_RECALL_SPAWN=0: same text, nothing started", r.stdout == direct.stdout and not servers()
       and not os.path.exists(STAMP))

    first, t_first = run([TOOL] + QUERY, ENV)
    ok("first call with no server prints the same text", first.returncode == 0 and first.stdout == direct.stdout,
       "%.2fs" % t_first)
    ok("first call started a server", wait(lambda: bool(servers()) or os.path.exists(ENDPOINT), 10),
       "pids %s" % servers())
    stamp0 = os.stat(STAMP).st_mtime_ns if os.path.exists(STAMP) else None
    published = wait(lambda: os.path.exists(ENDPOINT), 90)
    if not published and not servers() and os.path.exists(STAMP):
        log = open(os.path.join(HERE, "recall_server_stdio.log")).read()[-300:] if os.path.exists(
            os.path.join(HERE, "recall_server_stdio.log")) else ""
        print("SKIP: the started server left without publishing (its probe refuses this box: memory or disk floor) "
              + log)
        sys.exit(0)
    ok("started server published endpoint.json", published)
    os.rename(ENDPOINT, ENDPOINT + ".hidden")       # look like no server for one call
    r, _ = run([TOOL] + QUERY, ENV)
    os.rename(ENDPOINT + ".hidden", ENDPOINT)
    time.sleep(1)
    ok("a second call inside the minute starts no second server",
       r.stdout == direct.stdout and len(servers()) == 1 and os.stat(STAMP).st_mtime_ns == stamp0,
       "pids %s" % servers())
    served, t_served = run([TOOL] + QUERY, dict(ENV, MATH_RECALL_CLIENT_AUDIT_FILE=os.path.join(HERE, "audit.json")))
    try:
        audit = json.load(open(os.path.join(HERE, "audit.json")))
    except Exception:
        audit = {}
    ok("served call prints the same text", served.returncode == 0 and served.stdout == direct.stdout
       and audit.get("server") == 1, "%.2fs served against %.2fs direct" % (t_served, t_direct))

    # --exit-on-stale: change a code file's mtime; the server leaves within its 30 s check
    pid = servers()[0]
    t = time.time() + 5
    os.utime(os.path.join(HERE, "math_recall.py"), (t, t))
    gone = wait(lambda: pid not in servers(), 75)
    ok("on-demand server leaves once its code is stale", gone and not os.path.exists(ENDPOINT))
    os.remove(STAMP)                                 # skip the one-minute limit for the next start
    run([TOOL] + QUERY, ENV)
    ok_or_gate("the next call starts a current server", wait(lambda: os.path.exists(ENDPOINT), 90))
    stop_all()

    # --idle-exit on its own
    srv = subprocess.Popen([sys.executable, os.path.join(HERE, "recall_server.py"), "--store", STORE, "--idle-exit", "4"],
                           cwd=WORK, env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    up = wait(lambda: os.path.exists(ENDPOINT), 90)
    t_up = time.time()
    try:
        srv.wait(timeout=60)
    except subprocess.TimeoutExpired:
        srv.kill()
        srv.wait()
    ok_or_gate("--idle-exit 4 leaves after about 4 s idle and removes endpoint.json",
       up and srv.returncode == 0 and not os.path.exists(ENDPOINT), "%.1f s after publishing" % (time.time() - t_up))
finally:
    try:
        if servers():
            stop_all()
    except Exception:
        pass
    shutil.rmtree(WORK, ignore_errors=True)
print("RESULT %d/%d" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
