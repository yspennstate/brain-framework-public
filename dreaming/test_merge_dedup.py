"""Controls for the math hook's sub-query merge: one card per title, a dropped duplicate keeps its place, and a named
argument keeps the whole-question lead first.

 M1 two blocks carrying "Holder's Inequality" and "Hölder's inequality" give one card, not two
 M2 a statement replaces an encyclopedia opening of the same title, in the opening's place
 M3 MATHBRAIN_MERGE_DUP_NO_REFILL: the dropped duplicate leaves an empty place (the block carries one card fewer);
    off, the next card moves up
 M4 lead=True puts the first whole-question card ahead of the blocks; lead=False keeps it behind them;
    MATHBRAIN_SUBQ_LEAD_KEEP=0 switches the lead off
 M5 MATHBRAIN_MERGE_TITLE_DEDUP=0 joins by node id only, as before
 M6 a repeated node id is merged once either way

    python test_merge_dedup.py      (under a second; ALL PASS at the end)
"""
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
results = []


def check(ok, what):
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + what)


class Node:
    def __init__(self, nid, title, body=""):
        self.id, self.title, self.body, self.kind, self.metadata = nid, title, body, "claim", {}


class Hit:
    def __init__(self, nid, title, body=""):
        self.node = Node(nid, title, body)


def fresh(env):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    sys.modules.pop("math_sense", None)
    return importlib.import_module("math_sense")


def ids(hits):
    return [h.node.id if h is not None else None for h in hits]


LEDE = "In mathematics, Holder's inequality is a basic inequality between integrals."
STMT = "For p, q > 1 with 1/p + 1/q = 1, the integral of |fg| is at most the product of the norms."


def main():
    m = fresh({})
    a = [Hit("h1", "Holder's Inequality", STMT), Hit("a2", "Young's inequality")]
    b = [Hit("h2", "Hölder's inequality", STMT), Hit("b2", "Minkowski inequality")]
    got = [x for x in ids(m._merge_subquery_blocks([a, b], [])) if x]
    check(got.count("h1") + got.count("h2") == 1 and "a2" in got and "b2" in got, "M1 one card per title %r" % got)

    a = [Hit("h1", "Holder's Inequality", LEDE)]
    b = [Hit("h2", "Hölder's inequality", STMT)]
    got = ids(m._merge_subquery_blocks([a, b], []))
    check(got[0] == "h2", "M2 statement replaces the opening %r" % got)

    a = [Hit("h1", "Holder's Inequality", STMT), Hit("a2", "Young's inequality")]
    b = [Hit("h2", "Hölder's inequality", STMT), Hit("b2", "Minkowski inequality")]
    on = ids(m._merge_subquery_blocks([a, b], []))
    off = ids(fresh({"MATHBRAIN_MERGE_DUP_NO_REFILL": "0"})._merge_subquery_blocks([a, b], []))
    check(on == ["h1", None, "a2", "b2"] and off == ["h1", "a2", "b2"], "M3 no refill %r / %r" % (on, off))

    m = fresh({})
    blocks = [[Hit("s1", "Aubin-Lions lemma")], [Hit("s2", "Caccioppoli inequality")]]
    whole = [Hit("w1", "Compactness for parabolic equations"), Hit("w2", "Energy estimates")]
    led = ids(m._merge_subquery_blocks(blocks, whole, lead=True))
    plain = ids(m._merge_subquery_blocks(blocks, whole, lead=False))
    nolead = ids(fresh({"MATHBRAIN_SUBQ_LEAD_KEEP": "0"})._merge_subquery_blocks(blocks, whole, lead=True))
    check(led == ["w1", "s1", "s2", "w2"] and plain == ["s1", "s2", "w1", "w2"] and nolead == plain,
          "M4 lead %r / %r / %r" % (led, plain, nolead))

    a = [Hit("h1", "Holder's Inequality", STMT)]
    b = [Hit("h2", "Hölder's inequality", STMT)]
    old = fresh({"MATHBRAIN_MERGE_TITLE_DEDUP": "0"})
    got = ids(old._merge_subquery_blocks([a, b], []))
    check(old.MERGE_TITLE_DEDUP is False and got == ["h1", "h2"], "M5 switch %r" % got)

    m = fresh({})
    same = [Hit("x", "Fatou's lemma")]
    got = ids(m._merge_subquery_blocks([same, same], same))
    check(got == ["x"], "M6 node id once %r" % got)
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
