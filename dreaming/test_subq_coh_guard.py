"""Controls for the coherence guard on named and quoted-title sub-query blocks.

 G1 a scattered merged block falls back to the whole-question block when the pre-append merge is scattered too
 G2 on a named argument a coherent pre-append merge is served instead, and counts as an extra slot
 G3 a coherent merged block is left as it is
 G4 when the whole-question block would be cut as well, nothing changes
 G5 a quoted-title block (named=False) never takes the pre-append merge
 G6 a merged lead card titled with the result the prompt names is spared
 G7 the switches: MATHBRAIN_SUBQ_COH_GUARD=0, MATHBRAIN_SUBQ_APPEND_FALLBACK_MERGED=0, MATHBRAIN_COH_CUT=0
 G8 _named_slots_full returns the list before the append; _named_slots keeps its two values

    python test_subq_coh_guard.py      (under a second; ALL PASS at the end; a stub stands in for the vector store)
"""
import importlib
import os
import sys
import types

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
results = []


def check(ok, what):
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + what)


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


class Encoder:
    def embed(self, text):
        return unit([0, 0, 0, 1])


def install_stub(vectors):
    S = types.ModuleType("math_brain.semantic")
    S.QUERY_PREFIX = "q: "
    S._ids = np.array(list(vectors), dtype=object)
    S._vecs = np.stack([vectors[k] for k in vectors])
    S._load_artifact = lambda: None
    S._query_encoder = lambda: Encoder()
    pkg = sys.modules.get("math_brain") or types.ModuleType("math_brain")
    pkg.semantic = S
    sys.modules["math_brain"] = pkg
    sys.modules["math_brain.semantic"] = S


def fresh(env):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    sys.modules.pop("math_sense", None)
    return importlib.import_module("math_sense")


class Node:
    def __init__(self, nid, title):
        self.id, self.title, self.body, self.kind, self.metadata = nid, title, "", "claim", {}


class Hit:
    def __init__(self, nid, title="Some result"):
        self.node = Node(nid, title)


def hits(s):
    return [Hit(c) for c in s]


def ids(hs):
    return "".join(h.node.id for h in hs)


VEC = {
    "a": unit([1, 0, 0, 0]), "b": unit([0, 1, 0, 0]), "c": unit([0, 0, 1, 0]),
    "d": unit([1, 0.1, 0, 0]), "e": unit([1, 0, 0.1, 0]), "f": unit([0, 1, 0.1, 0]),
}
Q = "an unrelated question"


def main():
    install_stub(VEC)
    m = fresh({})
    ch, ex = m._subq_coh_guard(Q, hits("abc"), True, hits("ad"), hits("abf"), True)
    check(ids(ch) == "ad" and ex is False, "G1 whole-question fallback %s %r" % (ids(ch), ex))

    ch, ex = m._subq_coh_guard(Q, hits("abc"), False, hits("ad"), hits("ade"), True)
    check(ids(ch) == "ade" and ex is True, "G2 pre-append merge %s %r" % (ids(ch), ex))

    ch, ex = m._subq_coh_guard(Q, hits("ade"), True, hits("ab"), hits("abc"), True)
    check(ids(ch) == "ade" and ex is True, "G3 coherent kept %s" % ids(ch))

    ch, ex = m._subq_coh_guard(Q, hits("abc"), True, hits("bc"), hits("abf"), True)
    check(ids(ch) == "abc" and ex is True, "G4 both cut, unchanged %s" % ids(ch))

    ch, ex = m._subq_coh_guard(Q, hits("abc"), True, hits("ad"), hits("ade"), False)
    check(ids(ch) == "ad" and ex is False, "G5 quoted skips the merge %s" % ids(ch))

    named = [Hit("a", "Sylow Theorems"), Hit("b"), Hit("c")]
    ch, ex = m._subq_coh_guard("Sylow theorems.", named, True, hits("ad"), hits("ade"), True)
    check(ids(ch) == "abc", "G6 named lead spared %s" % ids(ch))

    off = fresh({"MATHBRAIN_SUBQ_COH_GUARD": "0"})
    g0 = ids(off._subq_coh_guard(Q, hits("abc"), True, hits("ad"), hits("ade"), True)[0])
    nf = fresh({"MATHBRAIN_SUBQ_APPEND_FALLBACK_MERGED": "0"})
    g1 = ids(nf._subq_coh_guard(Q, hits("abc"), True, hits("ad"), hits("ade"), True)[0])
    nc = fresh({"MATHBRAIN_COH_CUT": "0"})
    g2 = ids(nc._subq_coh_guard(Q, hits("abc"), True, hits("ad"), hits("ade"), True)[0])
    check(g0 == "abc" and g1 == "ad" and g2 == "abc", "G7 switches %s %s %s" % (g0, g1, g2))

    m = fresh({})
    names = ["Fatou's lemma", "Holder inequality", "Minkowski inequality", "Young's inequality", "Jensen's inequality"]
    b = [[Hit("n%d" % i, t)] for i, t in enumerate(names)]
    base = [Hit("w1", "Lebesgue dominated convergence"), Hit("w2", "Monotone convergence")]
    merged = m._merge_subquery_blocks(b, base, lead=True)
    ch, ex, cp = m._named_slots_full(b, merged, 4, base, True)
    two = m._named_slots(b, merged, 4, base, True)
    check([h.node.id for h in ch] == ["w1", "w2", "n0", "n1", "n2", "n3"]
          and [h.node.id for h in cp] == [h.node.id for h in merged[:4] if h is not None]
          and len(two) == 2 and [h.node.id for h in two[0]] == [h.node.id for h in ch],
          "G8 pre-append list %r" % [h.node.id for h in cp])
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
