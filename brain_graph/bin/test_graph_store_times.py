"""Edge times in the store (graph_store.py, 27 Sep) against the fast layer's own computation (live_delta.pairs_of) on the
same synthetic log, and through the sidecar (live_sidecar_times.py) into its edge table."""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile

import numpy as np

SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("BRAIN_GRAPH_BIN", os.path.dirname(os.path.abspath(__file__))))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


BIN = os.environ.get("BRAIN_GRAPH_BIN", os.path.dirname(os.path.abspath(__file__)))
GS = load("graph_store", os.path.join(BIN, "graph_store.py"))
LS = load("live_sidecar", os.path.join(BIN, "live_sidecar.py"))
LD = load("live_delta", os.path.join(BIN, "live_delta.py"))

tmp = tempfile.mkdtemp(prefix="store_times_")
p = os.path.join(tmp, "events.jsonl")
T = 1790000000.0
rng = np.random.default_rng(5)
items = ["file:/a/%d.py" % i for i in range(10)] + ["dir:/a", "dir:/b", "mem:m1", "mem:m2", "err:Bash:x", "cmd:ls",
                                                    "tool:Bash:grep", "said:hello", "host:h1", "kw:brain"]
ev = []
for s in ("s1", "s2", "s3", "s4"):
    t = T + float(rng.integers(0, 5000))
    for _ in range(300):
        t += float(rng.integers(1, 120))
        k, i = items[int(rng.integers(0, len(items)))].split(":", 1)
        ev.append({"ts": t, "session": s, "kind": k, "id": i})
ev.sort(key=lambda e: e["ts"])
with open(p, "w", encoding="utf-8", newline="") as fh:
    for e in ev:
        fh.write(json.dumps(e) + "\n")
ok = True
for hl in (None, 14):
    GS.build(p, os.path.join(tmp, "store%s" % hl), window=600.0, k=40, hub=1.01, tool_rate=1.0, half_life_days=hl)
    st = GS.GraphStore(os.path.join(tmp, "store%s" % hl))
    tf, tl = st.times(np.arange(len(st.keys)))
    got = {(st.names[k >> 32], st.names[k & 0xFFFFFFFF]): (a, b) for k, a, b in zip(st.keys.tolist(), tf.tolist(),
                                                                                     tl.tolist())}
    evs, _ = LD.read_events({"until": T - 1.0, "hubs": set()}, path=p, kinds_cache=os.path.join(tmp, "x.json"), start=0,
                            mode="bytes")
    LD.TOOL_RATE = 1.0
    want_e, *_ = LD.pairs_of(evs, set(), T + 1e6, 0.0)
    want = {k: (v[1], v[2]) for k, v in want_e.items()}
    same_keys = set(got) == set(want)
    worst = max(max(abs(got[k][0] - want[k][0]), abs(got[k][1] - want[k][1])) for k in want) if same_keys else None
    good = same_keys and worst is not None and worst <= 1.0      # float32 seconds after t_base
    print("half-life %s: %d edges, same edges %s, largest time difference %.3f s -> %s" % (
        hl, len(got), same_keys, worst if worst is not None else -1, "PASS" if good else "FAIL"))
    ok &= good
    first_before_last = bool(np.all(tf <= tl))
    print("   every first <= last: %s" % first_before_last)
    ok &= first_before_last
# through the sidecar
side = os.path.join(tmp, "side.sqlite")
LS.MEM = os.path.join(tmp, "nomem")
os.makedirs(LS.MEM, exist_ok=True)
LS.build(os.path.join(tmp, "store14"), out=side)
db = sqlite3.connect(side)
n, n_t = db.execute("SELECT COUNT(*), SUM(t_first IS NOT NULL AND t_last IS NOT NULL AND t_first <= t_last) FROM edge").fetchone()
print("sidecar edges %d, with first <= last times %d -> %s" % (n, n_t, "PASS" if n and n == n_t else "FAIL"))
ok &= bool(n) and n == n_t
# an old store (no times) still builds a sidecar, with empty times
z = dict(np.load(os.path.join(tmp, "store14", "edges.npz")))
for k in ("p_first", "p_last", "t_base"):
    z.pop(k)
np.savez(os.path.join(tmp, "store14", "edges.npz"), **z)
side2 = os.path.join(tmp, "side2.sqlite")
LS.build(os.path.join(tmp, "store14"), out=side2)
n2, nn = sqlite3.connect(side2).execute("SELECT COUNT(*), SUM(t_first IS NULL) FROM edge").fetchone()
print("an older store without times: %d edges, %d with empty times -> %s" % (n2, nn, "PASS" if n2 == nn == n else "FAIL"))
ok &= n2 == nn == n
print("ALL PASS" if ok else "SOME FAIL")
