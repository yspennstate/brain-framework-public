"""test_math_recall_server_posix.py - the resident math recall server end to end, on Linux/macOS.

    python framework/dreaming/test_math_recall_server_posix.py

Builds a five-claim store with math_brain.cli in a temporary folder, copies this folder's .py files beside it (so
endpoint.json and SERVER_STOP never touch the checkout), and checks: math_recall.py with the server disabled prints
results and its fast path is on (no "fast path OFF" line, which appears when a hash pin does not match the files
it loads); the server starts and publishes endpoint.json; the same call through the server prints the same text and
the client audit records that the server answered it; SERVER_STOP stops the server and removes endpoint.json.
Needs only the math_brain library (../mathbrain/src or ../src) and its dependency, cryptography. Skipped on Windows.
Takes about 15 seconds.
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import json, os, shutil, subprocess, sys, tempfile, time

if os.name == "nt":
    print("SKIP: POSIX only")
    sys.exit(0)

SRC = os.path.dirname(os.path.abspath(__file__))
BUILD = next(p for p in (os.path.join(os.path.dirname(SRC), "mathbrain", "src"),
                         os.path.join(os.path.dirname(SRC), "src")) if os.path.isdir(p))
WORK = tempfile.mkdtemp(prefix="mrs_posix_")
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
res = []


def ok(name, cond, extra=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (" " + extra if extra else ""), flush=True)


def run(args, env, timeout=120):
    t0 = time.time()
    r = subprocess.run([sys.executable] + args, cwd=WORK, env=env, capture_output=True, text=True, timeout=timeout)
    return r, time.time() - t0


r, _ = run(["-m", "math_brain.cli", "init", STORE], ENV)
ok("store created", r.returncode == 0 and os.path.exists(STORE), r.stderr.strip()[-200:])
for c in CLAIMS:
    r, _ = run(["-m", "math_brain.cli", "add-claim", STORE, c, "--kind", "concept", "--domain", "demo"], ENV)
    if r.returncode != 0:
        break
ok("claims added", r.returncode == 0, r.stderr.strip()[-200:])

tool = os.path.join(HERE, "math_recall.py")
off_env = dict(ENV, MATH_RECALL_SERVER_DISABLE="1")
run([tool] + QUERY, off_env)   # builds the prepared state once
direct, t_direct = run([tool] + QUERY, off_env)
ok("direct call prints a result", direct.returncode == 0 and "cyclic" in direct.stdout,
   "rc=%d %.2fs" % (direct.returncode, t_direct))
ok("fast path is on", "fast path OFF" not in direct.stdout + direct.stderr)

srv = subprocess.Popen([sys.executable, os.path.join(HERE, "recall_server.py"), "--store", STORE], cwd=WORK, env=ENV,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
endpoint = os.path.join(HERE, "endpoint.json")
deadline = time.time() + 60
while time.time() < deadline and not os.path.exists(endpoint) and srv.poll() is None:
    time.sleep(0.2)
if srv.poll() == 4:
    print("SKIP: the server's resource probe refused this machine (too little free memory)")
    shutil.rmtree(WORK, ignore_errors=True)
    sys.exit(0)
ok("server published endpoint.json", os.path.exists(endpoint), "rc=%s" % srv.poll())
if os.path.exists(endpoint):
    ep = json.load(open(endpoint, encoding="utf-8"))
    ok("endpoint is localhost", ep.get("host") == "127.0.0.1")

audit = os.path.join(HERE, "client_audit.json")
served, t_served = run([tool] + QUERY, dict(ENV, MATH_RECALL_CLIENT_AUDIT_FILE=audit))
ok("served call prints the same text", served.returncode == 0 and served.stdout == direct.stdout,
   "%.2fs served against %.2fs direct" % (t_served, t_direct))
try:
    rec = json.load(open(audit, encoding="utf-8"))
except Exception:
    rec = {}
ok("client audit says the server answered", rec.get("server") == 1, str(rec)[:120])

open(os.path.join(HERE, "SERVER_STOP"), "w").close()
try:
    srv.wait(timeout=30)
except subprocess.TimeoutExpired:
    srv.kill()
    srv.wait()
ok("SERVER_STOP stops the server", srv.returncode is not None and srv.returncode >= 0, "rc=%s" % srv.returncode)
ok("endpoint.json removed", not os.path.exists(endpoint))
shutil.rmtree(WORK, ignore_errors=True)
print("RESULT %d/%d" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
