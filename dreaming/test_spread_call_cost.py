"""Controls for the spread server's slow-call instruments (open log, call cost, fault sampler, working-set floor).

 C1 open_summary counts opens by directory and lists the longest gaps after an open, longest first
 C2 the audit hook records opens of the traced thread only, and nothing when no thread is traced
 C3 call_cost gives (CPU s, page faults, working set MB, io counters); the CPU and fault counts never go down
 C4 Windows: io_counts and avail_mb give numbers; elsewhere None
 C5 Windows: the fault sampler charges a call's faults to the serving thread's frames and reports a summary
 C6 ws_floor reports what it did and never raises

    python test_spread_call_cost.py      (a few seconds; ALL PASS at the end)
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("SPREAD_SERVER_OPEN_LOG_MS", "1")
import spread_server as ss

results = []


def check(ok, what):
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + what)


ops = [(0.0, "/a/x.md", "open"), (0.5, "/a/y.md", "open"), (0.6, "/b", "os.listdir")]
s = ss.open_summary(ops, 2.0)
check(s["n"] == 3 and s["dirs"] == {"/a": 2, "/b": 1} and s["gaps"][0] == (1400, "b", "os.listdir")
      and s["gaps"][1] == (500, "x.md", "open"), "C1 opens by directory, longest gap first")

tmp = tempfile.mkdtemp(prefix="call_cost_")
p = os.path.join(tmp, "f.txt")
with open(p, "w", encoding="utf-8") as fh:
    fh.write("x")
if ss.OPEN_LOG_MS > 0:
    ss._TRACE["ops"], ss._TRACE["thr"] = [], threading.current_thread()
    open(p, encoding="utf-8").close()
    other = threading.Thread(target=lambda: open(p, encoding="utf-8").close())
    other.start()
    other.join()
    mine = list(ss._TRACE["ops"])
    ss._TRACE["thr"] = None
    open(p, encoding="utf-8").close()
    check(len(mine) == 1 and mine[0][1] == p and len(ss._TRACE["ops"]) == 1,
          "C2 only the traced thread's opens, and none once tracing stops")
else:
    print("SKIP  C2: the open log is off in this process")

c0 = ss.call_cost()
sum(range(200000))
_ = [bytearray(4096) for _ in range(2000)]
c1 = ss.call_cost()
check(len(c0) == 4 and c1[0] >= c0[0] and c1[1] >= c0[1] and c0[2] >= 0, "C3 call_cost shape, monotone counters")

if os.name == "nt":
    io = ss.io_counts()
    am = ss.avail_mb()
    check(io is not None and len(io) == 3 and isinstance(am, int) and am > 0, "C4 io counters and free RAM read")

    def work():
        bufs = [bytearray(1 << 20) for _ in range(60)]
        time.sleep(0.15)
        del bufs

    th = threading.current_thread()
    fs = ss.FaultSampler(th)
    fs.start()
    work()
    fs.done.set()
    fs.join(1.0)
    sm = fs.summary()
    check(sm["samples"] > 0 and isinstance(sm["top"], list) and "peak_priv_mb" in sm
          and all(len(row) == 3 for row in sm["top"]), "C5 the sampler charges faults to frames")
else:
    check(ss.io_counts() is None and ss.avail_mb() is None, "C4 no counters off Windows")
    print("SKIP  C5: the fault sampler is Windows only")

r = ss.ws_floor(0)
check(isinstance(r, str), "C6 ws_floor reports: " + r)

print("ALL PASS" if all(results) else "FAILED %d of %d" % (results.count(False), len(results)))
sys.exit(0 if all(results) else 1)
