"""Controls for recall_levers.py (lever 10 of the resident recall server).

 L1 the numpy spread gives the same activations and bridges, in the same order, as spread_recall._spread on random graphs
 L2 a call with a mistake-weight map, keywords or an overlay goes to the original code
 L3 the version gate: the spread function of this tree is admitted, an unchecked function is not
 L4 BRS_EXACT_NP=0 installs nothing

    python test_recall_levers.py      (a few seconds; ALL PASS at the end; needs numpy)
"""
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def graph(rng, n_nodes, n_edges):
    names = ["m%d" % i for i in range(n_nodes)] + ["kw:k%d" % i for i in range(max(1, n_nodes // 5))]
    adj, deg = {}, {}
    for _ in range(n_edges):
        a, b = rng.choice(names), rng.choice(names)
        if a == b:
            continue
        w = rng.choice([0.25, 0.5, 1.0, 1.5, rng.random() * 3])
        adj.setdefault(a, []).append((b, w))
        adj.setdefault(b, []).append((a, w))
        deg[a] = deg.get(a, 0.0) + w
        deg[b] = deg.get(b, 0.0) + w
    seeds = {}
    for s in rng.sample(names, min(len(names), rng.randint(1, 6))):
        seeds[s] = rng.choice([1.0, 0.5, rng.random()])
    if rng.random() < 0.3:
        seeds["unseen_%d" % rng.randint(0, 9)] = 0.7   # a seed with no edges at all
    return adj, deg, seeds


def main():
    import numpy as np
    import spread_recall as sr
    import recall_levers as rl
    real = sr._spread
    gen = [0]
    g = {"_fc_ident": lambda p: gen[0]}     # a new edge generation per random graph
    rl._install_spread(sr, np, g)
    fast = sr._spread
    try:
        rng = random.Random(20261105)
        same = 0
        trials = 60
        for t in range(trials):
            gen[0] += 1
            adj, deg, seeds = graph(rng, rng.randint(5, 120), rng.randint(5, 600))
            a1, p1 = real(dict(seeds), adj, deg)
            a2, p2 = fast(dict(seeds), adj, deg)
            if list(dict(a1).items()) == list(dict(a2).items()) and list(dict(p1).items()) == list(dict(p2).items()):
                same += 1
        check(same == trials, "L1 numpy spread equals the original on %d of %d random graphs" % (same, trials))
        served = rl.STATS["spread"][0]
        check(served == trials, "L1 every one of those calls was served by the numpy form (%d)" % served)
        before = rl.STATS["spread"][1]
        gen[0] += 1
        adj, deg, seeds = graph(rng, 40, 200)
        any_nb = next(iter(adj))
        fast(dict(seeds), adj, deg, mw={any_nb: 1.0})
        fast(dict(seeds), adj, deg, kw={any_nb})
        fast(dict(seeds), adj, deg, overlay={any_nb: [("m0", 1.0)]})
        check(rl.STATS["spread"][1] - before == 3, "L2 mw, kw and overlay calls go to the original code")
        check(rl._proven("spread", real), "L3 this tree's spread_recall._spread is a proven version")
        check(not rl._proven("spread", lambda *a: None), "L3 an unchecked function is refused")
        check(not rl._proven("bm25", real), "L3 a function is judged against its own lever's list only")
    finally:
        sr._spread = real
    os.environ["BRS_EXACT_NP"] = "0"
    try:
        check(rl.install(object()) == [], "L4 BRS_EXACT_NP=0 installs nothing")
    finally:
        os.environ.pop("BRS_EXACT_NP", None)
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
