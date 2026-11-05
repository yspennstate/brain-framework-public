"""graph_lane.combine: a source at weight zero takes no part, and a constant list normalizes as fuse_scores does
(29 Sep 2026; interactive audit BM-20260929-01).

    python test_graph_lane_combine.py [GRAPH_LANE.py] [--old OLD_GRAPH_LANE.py]

G1-G2 are the audit's fixture at mix 0 and mix 1; G3 perturbs the inactive source's candidates (add, remove, reorder,
rescore) and requires the same fused ranking; G4 the constant-list rule; G5 that mix 0.5 with distinct scores ranks as
before the change; G6 the empty cases. N1 (with --old) runs G1 on the code before the change and shows the winner move.
"""
import importlib.util
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FAILS = []


def check(name, ok, detail=""):
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, (" - " + detail) if detail else ""))
    if not ok:
        FAILS.append(name)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BODY = [("lexical", 10.0), ("noise", 9.6), ("target", 9.5), ("tail", 0.0)]
USE = [("target", 1.0)]
LINKS = [("noise", 1.0)]


def order(g, use, links, mix, lam=0.1):
    return [s for s, _ in g.fuse_scores(BODY, g.combine(use, links, mix) or [("none", 0.0)], lam)]


def main():
    args = sys.argv[1:]
    old = None
    if "--old" in args:
        i = args.index("--old")
        old = args[i + 1]
        del args[i:i + 2]
    path = args[0] if args else os.path.expanduser(r"~/.claude/dreaming/graph_lane.py")
    g = load(path, "graph_lane_under_test")
    print("graph_lane under test: %s" % path)

    alone, withsrc = order(g, USE, [], 0.0), order(g, USE, LINKS, 0.0)
    check("G1 at mix 0 an inactive link source does not change the fused ranking", alone == withsrc,
          "%s vs %s" % (alone[:3], withsrc[:3]))
    alone, withsrc = order(g, [], LINKS, 1.0), order(g, USE, LINKS, 1.0)
    check("G2 at mix 1 an inactive use source does not change the fused ranking", alone == withsrc,
          "%s vs %s" % (alone[:3], withsrc[:3]))

    rng = random.Random(29)
    ok = True
    for trial in range(200):
        use = [("u%d" % k, rng.random()) for k in range(rng.randint(0, 6))]
        base = [("l%d" % k, rng.random()) for k in range(rng.randint(0, 6))]
        variants = [
            base,
            base + [("extra", rng.random())],
            base[1:],
            list(reversed(base)),
            [(s, rng.random() * 5) for s, _ in base],
            [("u0", 3.0)] + base,
            [(s, 1.0) for s, _ in base],
        ]
        for mix, make in ((0.0, lambda v: (use, v)), (1.0, lambda v: (v, use))):
            ref = g.combine(*make([]), mix)
            for v in variants:
                if g.combine(*make(v), mix) != ref:
                    ok = False
                    break
    check("G3 adding, removing, reordering or rescoring an inactive source's candidates changes nothing (200 draws)", ok)

    single = g.combine([("a", 0.7)], [], 0.0)
    tied = g.combine([("a", 2.0), ("b", 2.0)], [], 0.0)
    check("G4 a constant list normalizes to 1, as fuse_scores does",
          single == [("a", 1.0)] and tied == [("a", 1.0), ("b", 1.0)], "%s %s" % (single, tied))

    if old:
        o = load(old, "graph_lane_before")
        same = True
        for trial in range(300):
            use = [("u%d" % k, rng.random()) for k in range(rng.randint(2, 8))]
            links = [("l%d" % k if rng.random() < 0.7 else "u%d" % k, rng.random()) for k in range(rng.randint(2, 8))]
            if g.combine(use, links, 0.5) != o.combine(use, links, 0.5):
                same = False
                break
        check("G5 at mix 0.5 with distinct scores the ranking is the one before the change (300 draws)", same)

    check("G6 empty cases",
          g.combine([], [], 0.5) == [] and g.combine([], [("x", 1.0)], 0.0) == []
          and g.combine([], [("x", 1.0), ("y", 0.0)], 0.5) == [("x", 1.0), ("y", 0.0)])

    if old:
        o = load(old, "graph_lane_before_n1")
        a, b = order(o, USE, [], 0.0), order(o, USE, LINKS, 0.0)
        check("N1 before the change the inactive link source moves the winner (the finding)", a[0] != b[0],
              "%s vs %s" % (a[:3], b[:3]))
    print("%d failed" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
