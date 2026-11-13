"""math_answer_closure_selftest.py - the math hook's answer closure never serves an old revision as current.

The answer closure in math_sense._prefilter_revision_nodes walks revisions, then adds proof/refutation sources for
the lexical questions, and does not walk again. Without a guard, an old answer added there arrives without its newer
revision and without the SUPERSEDES edge that marks it historical, so retrieve() ranks it as current (a refuted card
whose revision says CANDIDATE REFUTATION reads as a live refutation). The guard: answer closure loads no source that
has a newer revision.

    python math_answer_closure_selftest.py        exit 0 all pass, 1 a failure

Fixtures on a fake store (no store needed), then the live store read-only: for every proves/refutes target, the pool
the hook builds from that target alone holds no old revision without its newer one. A control shows the checker
flags a pool that does.
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import math_sense as MS  # noqa: E402

sys.path.insert(0, MS.BUILD_SRC)
from math_brain.models import EdgeKind  # noqa: E402

P, R, S = EdgeKind.PROVES, EdgeKind.REFUTES, EdgeKind.SUPERSEDES
fails = 0


def check(label, ok):
    global fails
    fails += 0 if ok else 1
    print(("PASS " if ok else "FAIL ") + label)


class FakeStore:
    def __init__(self, ids, edges):
        self.ids, self.edges = set(ids), edges

    def iter_edges(self):
        return iter(self.edges)

    def get_nodes(self, ids):
        return {i: types.SimpleNamespace(id=i) for i in ids if i in self.ids}


def E(kind, s, t):
    return types.SimpleNamespace(kind=kind, source_id=s, target_id=t)


def pool(store, pf):
    return {n.id for n in MS._prefilter_revision_nodes(store, pf)[0]}


def stale_in(pool_ids, edges):
    """old revisions in the pool whose newer revision is not (the SUPERSEDES edge is cut, so nothing marks them)"""
    newer = {}
    for e in edges:
        if e.kind is S:
            newer.setdefault(e.target_id, set()).add(e.source_id)
    return {n for n in pool_ids if n in newer and not (newer[n] & pool_ids)}


F1 = [E(R, "O", "Q"), E(S, "N", "O")]
check("an old refutation whose newer revision has no relation is not loaded; the question is served alone",
      pool(FakeStore({"Q", "O", "N"}, F1), ["Q"]) == {"Q"})
check("a revised answer with its own relation loads the newer revision only",
      pool(FakeStore({"Q", "O", "N"}, F1 + [E(R, "N", "Q")]), ["Q"]) == {"Q", "N"})
check("a current proof source still loads", pool(FakeStore({"Q", "C"}, [E(P, "C", "Q")]), ["Q"]) == {"Q", "C"})
F4 = [E(R, "O1", "Q"), E(R, "O2", "Q"), E(R, "O3", "Q"), E(R, "C", "Q"),
      E(S, "N1", "O1"), E(S, "N2", "O2"), E(S, "N3", "O3")]
check("old revisions ahead in store order do not take the cap's slots from the current answer",
      pool(FakeStore({"Q", "O1", "O2", "O3", "N1", "N2", "N3", "C"}, F4), ["Q"]) == {"Q", "C"})
check("a lexical old revision still brings its newer revision (the revision walk is unchanged)",
      pool(FakeStore({"O", "N"}, [E(S, "N", "O")]), ["O"]) == {"O", "N"})
check("control: the checker flags an old answer riding without its newer revision", stale_in({"Q", "O"}, F1) == {"O"})

if os.path.exists(MS.STORE_PATH):
    from math_brain.store import ResearchStore
    with ResearchStore.open_read_only(MS.STORE_PATH) as store:
        store.connection.execute("BEGIN")
        edges = list(store.iter_edges())
        targets = sorted({e.target_id for e in edges if e.kind in (P, R)})
        bad = [t for t in targets if stale_in({n.id for n in MS._prefilter_revision_nodes(store, [t])[0]}, edges)]
    check("live store: none of the %d answer targets' own pools holds an old revision without its newer one"
          % len(targets), not bad)
else:
    print("SKIP live store (not found: %s)" % MS.STORE_PATH)
print("math_answer_closure_selftest: %s" % ("all pass" if not fails else "%d FAILED" % fails))
sys.exit(1 if fails else 0)
