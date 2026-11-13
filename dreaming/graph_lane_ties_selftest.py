"""graph_lane_ties_selftest.py - equal scores come out in one order whatever the process.

fuse() and combine() build their scores over a Python set, whose order follows string hashes, which Python randomises
per process; sorted by score alone, two recalls of one question showed tied memories in either order. Each check runs
in five processes with PYTHONHASHSEED 1-5 and must print the same order every time; the evaluation log (BRAIN_RECALL_LOG_FULL)
must keep full precision and the live log four places.

    python graph_lane_ties_selftest.py [PATH_TO_graph_lane.py]      (default: the one beside this file)
Exit 0 when every check passes.
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.abspath(sys.argv[1]) if sys.argv[1:] else os.path.join(HERE, "graph_lane.py")
NOWIN = 0x08000000 if os.name == "nt" else 0
NAMES = ["alpha_note", "bravo_note", "charlie_note", "delta_note", "echo_note", "foxtrot_note", "golf_note", "hotel_note"]

PROBE = r"""
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("gl", sys.argv[1]); gl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gl)
names = json.loads(sys.argv[2])
body = [("top_note", 10.0)] + [(n, 5.0) for n in names] + [("low_note", 1.0)]
graph = [("top_note", 1.0)] + [(n, 0.5) for n in names] + [("low_note", 0.1)]
fused = [s for s, _ in gl.fuse(body, graph, 0.2, "convex")]
comb = [s for s, _ in gl.combine([(n, 0.5) for n in names], [(n, 0.5) for n in names], 0.5)]
print(json.dumps({"fuse": fused, "combine": comb}))
"""


def probe(seed):
    env = dict(os.environ, PYTHONHASHSEED=str(seed))
    p = subprocess.run([sys.executable, "-X", "utf8", "-c", PROBE, TARGET, json.dumps(NAMES)], capture_output=True,
                       text=True, encoding="utf-8", env=env, creationflags=NOWIN, timeout=120)
    if p.returncode:
        raise SystemExit("probe failed: " + p.stderr[-800:])
    return json.loads(p.stdout.strip().splitlines()[-1])


def main():
    fails = []
    runs = [probe(s) for s in (1, 2, 3, 4, 5)]
    for key in ("fuse", "combine"):
        orders = {tuple(r[key]) for r in runs}
        if len(orders) != 1:
            fails.append("%s: %d different orders over 5 hash seeds" % (key, len(orders)))
        else:
            order = list(orders.pop())
            tied = [s for s in order if s in NAMES]
            if tied != sorted(NAMES):
                fails.append("%s: tied names not in name order" % key)
            if key == "fuse" and (order[0] != "top_note" or order[-1] != "low_note"):
                fails.append("fuse: untied items moved")
    # the log: full precision under BRAIN_RECALL_LOG_FULL=1, four places otherwise
    tmp = tempfile.mkdtemp(prefix="gl_ties_")
    code = (r"import importlib.util,sys;spec=importlib.util.spec_from_file_location('gl',sys.argv[1]);"
            r"gl=importlib.util.module_from_spec(spec);spec.loader.exec_module(gl);"
            r"gl.log_recall('q',0.1,[('a',1.23456789),('b',0.5)],[('a',0.987654321),('c',0.1)],['a','b'])")
    for full, want in (("1", 1.23456789), ("0", 1.2346)):
        path = os.path.join(tmp, "log_%s.jsonl" % full)
        env = dict(os.environ, BRAIN_RECALL_LOG="1", BRAIN_RECALL_LOG_PATH=path, BRAIN_RECALL_LOG_FULL=full)
        subprocess.run([sys.executable, "-X", "utf8", "-c", code, TARGET], env=env, creationflags=NOWIN, timeout=120)
        try:
            rec = json.loads(open(path, encoding="utf-8").read().splitlines()[-1])
            got = rec["body"][0][1]
            if got != want:
                fails.append("log FULL=%s: body score %r, want %r" % (full, got, want))
            if full == "1" and rec["bounds"][1] != 1.23456789:
                fails.append("log FULL=1: bounds rounded")
        except Exception as exc:
            fails.append("log FULL=%s: no record (%s)" % (full, exc))
    print("graph_lane ties selftest on %s: %s" % (TARGET, "PASS" if not fails else "FAIL"))
    for f in fails:
        print("  - " + f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
