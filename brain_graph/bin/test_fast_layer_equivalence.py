"""test_fast_layer_equivalence.py - the five-minute fast layer (live_delta) keeps the targets the six-hour store
(graph_store) keeps (27 Sep, claude-825b1305), after account01/B20 R12: the two differed on first touches outside the fast
layer's 8 MiB look-back and on which tenth of the tool targets they sampled.

    python test_fast_layer_equivalence.py [PATH_TO_graph_store.py PATH_TO_live_delta.py]

Controls:
 E1 the two samplers agree on 1,000 synthetic tool events at the live rate (0.1)
 E2 at the live tool rate the fast layer's pairs equal the store's on the same synthetic log (the selftest's control 1
    runs at rate 1, which hid the sampler)
 E3 a folder a session touched more than 8 MiB of log before the store's end, touched again after it: the store lists it
    in first_touch.json, and with that list the fast layer makes it no target; without the list it would (the old rule,
    shown so the control can fail)
 E4 first_touch.json lists only sessions active in the last seven days of the store, and only first-touch kinds
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import importlib.util
import json
import os
import random
import sys
import tempfile

H = os.path.expanduser("~")
BIN = os.path.join(H, ".claude", "brain_graph", "bin")
GS = sys.argv[1] if len(sys.argv) > 2 else os.path.join(BIN, "graph_store.py")
LD = sys.argv[2] if len(sys.argv) > 2 else os.path.join(BIN, "live_delta.py")
sys.path.insert(0, BIN)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


gs = load("graph_store", GS)
ld = load("live_delta", LD)
fails = []


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    if not ok:
        fails.append(what)


tmp = tempfile.mkdtemp(prefix="fast_layer_equiv_")
T = 1790000000.0

# E1
rnd = random.Random(11)
agree = 0
for i in range(1000):
    s = "s%d" % rnd.randint(0, 9)
    ts = T + rnd.random() * 86400
    x = "tool:Bash:%d" % rnd.randint(0, 50)
    a = gs.keep_tool(s, ts, x, 0.1) if hasattr(gs, "keep_tool") else None
    b = ld.keep_tool(s, ts, x, 0.1)
    agree += a == b
check(agree == 1000, "E1 the two samplers agree on %d of 1,000 tool events" % agree)

# E2
p = os.path.join(tmp, "events.jsonl")
ev = []
items = ["file:/a/%d.py" % i for i in range(12)] + ["dir:/a", "dir:/b", "mem:m1", "mem:m2", "err:Bash:x", "cmd:ls",
                                                     "tool:Bash:grep", "tool:Read:f", "tool:Edit:g", "host:h1"]
for s in ("s1", "s2", "s3"):
    t = T
    for _ in range(600):
        t += float(rnd.randint(1, 90))
        ev.append({"ts": round(t, 3), "session": s, "kind": "", "id": items[rnd.randrange(len(items))]})
for e in ev:
    e["kind"], e["id"] = e["id"].split(":", 1)
ev.sort(key=lambda e: e["ts"])
with open(p, "w", encoding="utf-8") as fh:
    for e in ev:
        fh.write(json.dumps(e) + "\n")
gs.build(p, os.path.join(tmp, "store"), window=ld.WINDOW, k=ld.K_CTX, hub=1.01, tool_rate=0.1)
st = gs.GraphStore(os.path.join(tmp, "store"))
want = {(st.names[key >> 32], st.names[key & 0xFFFFFFFF]): c for key, c in zip(st.keys.tolist(), st.cnt.tolist())}
evs, _rd = ld.read_events({"until": T - 1.0, "hubs": set()}, path=p, kinds_cache=os.path.join(tmp, "none.json"),
                          start=0, mode="bytes")
got_e, *_ = ld.pairs_of(evs, set(), T + 1e6, 0.0)
got = {k: v[0] for k, v in got_e.items()}
tools_w = sum(1 for (_c, d) in want if d.startswith("tool:"))
check(got == want and tools_w > 0, "E2 same pairs at tool rate 0.1: %s (%d pairs here, %d in the store, %d to a tool)" % (
    got == want, len(got), len(want), tools_w))

# E3 and E4
p3 = os.path.join(tmp, "events3.jsonl")
lines = []
t = T
lines.append({"ts": t, "session": "sA", "kind": "mem", "id": "m0"})
lines.append({"ts": t + 5, "session": "sA", "kind": "dir", "id": "/old"})
filler = "x" * 80
for i in range(110000):                  # about 11 MiB of another session's events
    t += 1.0
    lines.append({"ts": t, "session": "sB", "kind": "file", "id": "/f/%d_%s" % (i % 5000, filler)})
t_end = t
lines.append({"ts": T - 30 * 86400, "session": "sOld", "kind": "dir", "id": "/ancient"})
lines.sort(key=lambda e: e["ts"])
with open(p3, "w", encoding="utf-8") as fh:
    for e in lines:
        fh.write(json.dumps(e) + "\n")
boundary = os.path.getsize(p3)
with open(p3, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"ts": t_end + 100, "session": "sA", "kind": "mem", "id": "m1"}) + "\n")
    fh.write(json.dumps({"ts": t_end + 110, "session": "sA", "kind": "dir", "id": "/old"}) + "\n")
gs.build(p3, os.path.join(tmp, "store3"), until=t_end + 1, window=ld.WINDOW, k=ld.K_CTX, hub=1.01, tool_rate=1.0)
ftp = os.path.join(tmp, "store3", "first_touch.json")
ft = json.load(open(ftp, encoding="utf-8")) if os.path.exists(ftp) else {}
sess = ft.get("sessions") or {}
check("dir:/old" in (sess.get("sA") or []) and "sOld" not in sess
      and all(n.split(":", 1)[0] in ("file", "dir", "proj", "repo", "host") for v in sess.values() for n in v),
      "E4 first_touch.json: sA lists dir:/old, the month-old session is not listed, only first-touch kinds (%d sessions)"
      % len(sess))
evs3, _ = ld.read_events({"until": t_end + 1, "hubs": set()}, path=p3, kinds_cache=os.path.join(tmp, "none.json"),
                         start=boundary, mode="bytes")
new_old = [e for e in evs3 if e[2] == "dir:/old" and e[3]]
with_list, *_ = ld.pairs_of(evs3, set(), t_end + 1e4, 0.0, first_seen=sess)
without, *_ = ld.pairs_of(evs3, set(), t_end + 1e4, 0.0, first_seen=None)
tgt_with = any(d == "dir:/old" for (_c, d) in with_list)
tgt_without = any(d == "dir:/old" for (_c, d) in without)
check(bool(new_old) and not tgt_with and tgt_without,
      "E3 an old folder touched again after the store: a target with the list %s (want False), without it %s (want True)"
      % (tgt_with, tgt_without))

print("FAIL" if fails else "PASS", "- %d of 4 controls" % (4 - len(fails)))
sys.exit(1 if fails else 0)
