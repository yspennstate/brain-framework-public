"""test_r9_kinds.py - the fast layer leaves test workers out by the store's rule, and the kinds cache keeps every writer's
kinds. Temp files only.

    python test_r9_kinds.py [PATH_TO_live_delta.py] [PATH_TO_session_kinds.py]

Controls:
 1. kind_resolver over a cache of whole ids answers 8-character keys: test, his; two sessions of different kinds under one
    key -> unknown; an unknown key -> unknown
 2. read_events with that cache leaves out the events of a test session keyed by its first 8 characters, keeps his
 3. with the default cache the fast layer asks session_kinds.kind, the store's own call (monkeypatched here)
 4. four processes that each load the cache, wait, add 25 kinds and save: all 100 plus the 2 already there survive
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time

H = os.path.expanduser("~")
BIN = os.path.join(H, ".claude", "brain_graph", "bin")
LD = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BIN, "live_delta.py")
SK = sys.argv[2] if len(sys.argv) > 2 else os.path.join(BIN, "session_kinds.py")
tmp = tempfile.mkdtemp(prefix="r9_kinds_test_")
fails = []


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


sk = load("session_kinds", SK)          # live_delta's `import session_kinds` gets this copy
ld = load("live_delta_under_test", LD)

T_ID = "abcdef12-0000-4000-8000-000000000001"
H_ID = "0123abcd-0000-4000-8000-000000000002"
C1, C2 = "feedface-0000-4000-8000-000000000003", "feedface-0000-4000-8000-000000000004"
cache = os.path.join(tmp, "kinds.json")
json.dump({T_ID: "test", H_ID: "his", C1: "his", C2: "test"}, open(cache, "w", encoding="utf-8"))

# 1
k = ld.kind_resolver(cache)
got = (k(T_ID[:8]), k(H_ID[:8]), k("feedface"), k("99999999"), k(T_ID))
if got != ("test", "his", "unknown", "unknown", "test"):
    fails.append("1 resolver: %r" % (got,))

# 2
ev = os.path.join(tmp, "events.jsonl")
T0 = 1_790_000_000.0
with open(ev, "w", encoding="utf-8") as fh:
    for i in range(30):
        fh.write(json.dumps({"ts": T0 + i, "session": T_ID[:8], "kind": "file", "id": "/w/test_%d.py" % i}) + "\n")
        fh.write(json.dumps({"ts": T0 + i, "session": H_ID[:8], "kind": "file", "id": "/w/his_%d.py" % i}) + "\n")
evs, _rd = ld.read_events({"until": T0 - 1.0, "hubs": set()}, path=ev, kinds_cache=cache, start=0, mode="bytes")
sess = {e[0] for e in evs}
if sess != {H_ID[:8]}:
    fails.append("2 read_events kept sessions %r" % (sess,))

# 3
calls = []
real_kind = sk.kind
sk.kind = lambda s: (calls.append(s), "test" if s == H_ID[:8] else "his")[1]
evs3, _ = ld.read_events({"until": T0 - 1.0, "hubs": set()}, path=ev, kinds_cache=ld.KINDS_CACHE, start=0, mode="bytes")
sk.kind = real_kind
if not (calls and {e[0] for e in evs3} == {T_ID[:8]} and len(set(calls)) == len(calls)):
    fails.append("3 default path: calls=%r kept=%r" % (calls[:5], {e[0] for e in evs3}))

# 4
shared = os.path.join(tmp, "shared_kinds.json")
json.dump({"seed-a": "his", "seed-b": "script"}, open(shared, "w", encoding="utf-8"))
child = os.path.join(tmp, "child.py")
with open(child, "w", encoding="utf-8") as fh:
    fh.write(
        "import importlib.util, sys, time\n"
        "spec = importlib.util.spec_from_file_location('session_kinds', sys.argv[1])\n"
        "sk = importlib.util.module_from_spec(spec); spec.loader.exec_module(sk)\n"
        "sk.CACHE = sys.argv[2]; sk.reset()\n"
        "c = sk._cache()\n"
        "time.sleep(max(0.0, float(sys.argv[4]) - time.time()))\n"
        "for i in range(25):\n"
        "    c['w%s-%02d' % (sys.argv[3], i)] = 'test'\n"
        "sk._STATE['dirty'] = True\n"
        "sk.save()\n")
start_at = time.time() + 1.5
procs = [subprocess.Popen([sys.executable, child, SK, shared, str(n), str(start_at)]) for n in range(4)]
for p in procs:
    p.wait()
final = json.load(open(shared, encoding="utf-8"))
want = {"seed-a", "seed-b"} | {"w%d-%02d" % (n, i) for n in range(4) for i in range(25)}
lost = sorted(want - set(final))
if lost or os.path.exists(shared + ".lock"):
    fails.append("4 lost %d kinds (e.g. %r); lock left: %r" % (len(lost), lost[:4], os.path.exists(shared + ".lock")))

print("FAIL" if fails else "PASS", "- 4 controls;", "; ".join(fails) if fails else
      "8-character keys resolve, test workers left out, the store's call used, no kinds lost across 4 writers")
sys.exit(1 if fails else 0)
