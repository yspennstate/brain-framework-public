"""Controls for the math hook's use-grown layer (brain_graph/bin/math_use.py wired into math_sense.py).

 U1 a card used more than the one above it, at a close score, moves up one place; the card set is unchanged
 U2 a card whose score is far below its neighbour's stays below it, however much it was used
 U3 MATHBRAIN_USE_WEIGHT=0 leaves the order as it was
 U4 _use_begin loads the cards this session was already served; a new prompt forgets the last prompt's scores
 U5 the hook finds math_use.py in a clone (framework/brain_graph/bin) and log_serve appends the serve to the live log

    python test_use_layer.py      (under a second; ALL PASS at the end)
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import importlib
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
results = []


def check(ok, what):
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + what)


class Node:
    def __init__(self, nid):
        self.id, self.title, self.body = nid, nid, ""


class Hit:
    def __init__(self, nid, score):
        self.node = Node(nid)
        self.lexical_score, self.symbol_score, self.formula_score, self.graph_score = score, 0.0, 0.0, 0.0


class FakeUse:
    def __init__(self, table):
        self.table = table

    def scores(self, sid, ids, served=()):
        return {i: self.table.get(i, 0.0) for i in ids}


def fresh(env, home):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    os.environ["HOME"] = os.environ["USERPROFILE"] = home
    for name in ("math_sense", "math_use"):
        sys.modules.pop(name, None)
    return importlib.import_module("math_sense")


def main():
    home = tempfile.mkdtemp()
    m = fresh({}, home)
    hits = {"a": Hit("a", 10.0), "b": Hit("b", 9.8), "c": Hit("c", 3.0)}
    pieces = [("a", "[1] A"), ("b", "[2] B"), ("c", "[3] C")]
    m._use_module = lambda: FakeUse({"b": 1.0, "c": 1.0})
    m._use_begin("s1")
    got = [p[0] for p in m._use_order_pieces(pieces, ["a", "b", "c"], hits)]
    check(got[:2] == ["b", "a"] and sorted(got) == ["a", "b", "c"], "U1 used card moves up one place: %r" % got)
    check(got[2] == "c", "U2 a far lower card stays below: %r" % got)

    m = fresh({"MATHBRAIN_USE_WEIGHT": "0"}, home)
    m._use_module = lambda: FakeUse({"b": 1.0})
    got = m._use_order_pieces(pieces, ["a", "b", "c"], hits)
    check(got is pieces, "U3 weight 0 keeps the order")

    m = fresh({}, home)
    os.makedirs(os.path.dirname(m._session_shard("s2")), exist_ok=True)
    with io.open(m._session_shard("s2"), "w", encoding="utf-8") as f:
        json.dump({"ids": ["x", "y"]}, f)
    m._USE_CTX["cache"]["stale"] = 0.9
    m._use_begin("s2")
    check(m._USE_CTX["served"] == {"x", "y"} and m._USE_CTX["cache"] == {},
          "U4 served set %r, cache cleared" % sorted(m._USE_CTX["served"]))

    bin_dir = os.path.join(HERE, "..", "brain_graph", "bin")
    if os.path.isfile(os.path.join(bin_dir, "math_use.py")):
        m = fresh({}, home)
        mu = m._use_module()
        mu.log_serve("s3", ["p", "q"])
        log = os.path.join(home, ".claude", "brain_graph", "live", "math_serves.jsonl")
        rows = [json.loads(x) for x in open(log, encoding="utf-8")] if os.path.isfile(log) else []
        check(os.path.dirname(os.path.abspath(mu.__file__)) == os.path.abspath(bin_dir)
              and rows and rows[-1]["ids"] == ["p", "q"], "U5 clone math_use found and serve logged: %r" % rows[-1:])
    else:
        print("SKIP  U5 (no brain_graph/bin beside this folder)")
    shutil.rmtree(home, ignore_errors=True)
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
