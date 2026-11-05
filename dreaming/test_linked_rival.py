"""Controls for linked rivals (_set_pool_adjacency, _linked_to).

 L1 a direct edge links two cards; SUPERSEDES edges and self-loops do not count
 L2 mode 2 (default): a shared neighbour of small degree links them; a hub above MATHBRAIN_LINK_HUB_MAX does not
 L3 MATHBRAIN_LINKED_RIVAL=1 keeps direct edges only; =0 links nothing
 L4 the rival choice skips linked cards, and every pool build sets the adjacency (no stale links between prompts)

    python test_linked_rival.py      (under a second; ALL PASS at the end)
"""
import importlib
import os
import sys
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


def e(a, b, kind="depends_on"):
    return NS(source_id=a, target_id=b, kind=NS(value=kind))


EDGES = [e("a", "b"), e("c", "d", "supersedes"), e("x", "x"),
         e("p", "m"), e("q", "m"),                                   # m: small shared neighbour
         e("r", "h"), e("s", "h")] + [e("h", "z%d" % n) for n in range(10)]   # h: a hub of degree 12


def main():
    m = fresh({})
    m._set_pool_adjacency(EDGES)
    check(m._linked_to("a", "b") and m._linked_to("b", "a") and not m._linked_to("c", "d")
          and not m._linked_to("x", "y"), "L1 direct edge yes; supersedes and self-loop no")
    check(m._linked_to("p", "q") and not m._linked_to("r", "s"), "L2 small shared neighbour yes, hub no")
    os.environ["MATHBRAIN_LINK_HUB_MAX"] = "20"
    check(m._linked_to("r", "s"), "L2 a raised hub cap admits the hub")

    m = fresh({"MATHBRAIN_LINKED_RIVAL": "1"})
    m._set_pool_adjacency(EDGES)
    one = m._linked_to("a", "b") and not m._linked_to("p", "q")
    m = fresh({"MATHBRAIN_LINKED_RIVAL": "0"})
    m._set_pool_adjacency(EDGES)
    check(one and not m._linked_to("a", "b"), "L3 mode 1 direct only, mode 0 nothing")

    src = open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read()
    builds = src.count("edges = list(store.iter_edges())") - 1 + src.count("_prefilter_revision_nodes(store, ")
    sets = src.count("_set_pool_adjacency(edges)") + src.count("_set_pool_adjacency(())")
    check("and not _linked_to(ranked[0].node.id, h.node.id)" in src and sets >= builds,
          "L4 rival skips linked cards; %d pool builds, %d adjacency sets" % (builds, sets))
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
