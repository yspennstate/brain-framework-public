"""Controls for _revision_edge_reader and the superseded-source filter in _prefilter_revision_nodes.

 R1 on a small sqlite store the fast read returns the same nodes and the same pool edges, in the same order, as the
    whole-edge read; retracted edges are left out of both
 R2 an edge kind outside EdgeKind, a store with no row reader, or a failing query falls back to the whole-edge read
 R3 a superseded source is never loaded as an answer; the current revision's own answer still is

    python test_revision_edge.py      (a second or two; ALL PASS at the end)
"""
import importlib
import os
import sqlite3
import sys
from collections import namedtuple
from types import SimpleNamespace as NS

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def fresh(env):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    sys.modules.pop("math_sense", None)
    return importlib.import_module("math_sense")


from math_brain.models import EdgeKind  # noqa: E402

Edge = namedtuple("Edge", "id kind source_id target_id")


class Store:
    def __init__(self, edges, nodes, retracted=(), reader=True):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("CREATE TABLE edges (id TEXT, kind TEXT, source_id TEXT, target_id TEXT, created_at TEXT)")
        for i, (eid, kind, s, d) in enumerate(edges):
            self.connection.execute("INSERT INTO edges VALUES (?,?,?,?,?)", (eid, kind, s, d, "2026-01-%02d" % (30 - i)))
        self.nodes = set(nodes)
        self.retracted = frozenset(retracted)
        self.whole_reads = 0
        if not reader:
            self._edge_from_row = None

    def _edge_from_row(self, row, skip_unknown, warned):
        return Edge(row["id"], EdgeKind(row["kind"]), row["source_id"], row["target_id"])

    def retracted_edge_ids(self):
        return self.retracted

    def iter_edges(self):
        self.whole_reads += 1
        rows = self.connection.execute("SELECT * FROM edges ORDER BY created_at, id").fetchall()
        return iter([self._edge_from_row(r, False, set()) if callable(self._edge_from_row) else
                     Edge(r["id"], EdgeKind(r["kind"]), r["source_id"], r["target_id"])
                     for r in rows if r["id"] not in self.retracted])

    def get_nodes(self, ids):
        return {i: NS(id=i) for i in ids if i in self.nodes}


def run(m, store, ids):
    nodes, edges = m._prefilter_revision_nodes(store, ids)
    return [n.id for n in nodes], [tuple(e) for e in edges]


def main():
    m = fresh({})
    E = [("e1", "supersedes", "b2", "b1"), ("e2", "proves", "a1", "q"), ("e3", "cites", "b2", "q"),
         ("e4", "depends_on", "a1", "b2"), ("e5", "refutes", "z9", "q"), ("e6", "cites", "q", "a1"),
         ("e7", "depends_on", "x", "y")]
    N = ["b1", "b2", "a1", "q", "z9", "x", "y"]
    fast = Store(E, N, retracted={"e6"})
    got = run(m, fast, ["b1", "q"])
    slow = Store(E, N, retracted={"e6"}, reader=False)
    want = run(m, slow, ["b1", "q"])
    check(got == want, "R1 fast read matches the whole-edge read (nodes, edges, order)")
    check(fast.whole_reads == 0 and slow.whole_reads == 1, "R1 the fast path never reads every edge")
    check(all(e[0] != "e6" for e in got[1]) and "b2" in got[0] and "a1" in got[0],
          "R1 retracted edge left out; newer revision and answer loaded")

    odd = Store(E + [("e8", "mystery", "x", "y")], N)
    check(m._revision_edge_reader(odd, EdgeKind) is None, "R2 an unknown edge kind falls back")
    check(m._revision_edge_reader(slow, EdgeKind) is None, "R2 no row reader falls back")
    broken = Store(E, N)
    broken.connection.execute("DROP TABLE edges")
    broken.connection.execute("CREATE TABLE edges (other_col TEXT)")
    check(m._revision_edge_reader(broken, EdgeKind) is None, "R2 a failing query falls back")
    check(m._revision_edge_reader(NS(), EdgeKind) is None, "R2 a store with no connection falls back")

    # R3: a1 was superseded by a2; both prove q. Only a2 may come in as the answer.
    E3 = [("s1", "supersedes", "a2", "a1"), ("p1", "proves", "a1", "q"), ("p2", "proves", "a2", "q")]
    ids, _ = run(m, Store(E3, ["a1", "a2", "q"]), ["q"])
    check("a2" in ids and "a1" not in ids, "R3 superseded source not loaded; current answer is")
    E4 = [("s1", "supersedes", "a2", "a1"), ("p1", "proves", "a1", "q")]
    ids, _ = run(m, Store(E4, ["a1", "a2", "q"]), ["q"])
    check(ids == ["q"], "R3 a relation bound only to an old revision serves the question alone")
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
