"""Controls for the named-result slots: a fifth card for a fifth named result, and named cards appended after the
whole-question block.

 S1 five named blocks, cap 4, nothing chosen before: the fifth result gets the fifth slot and the budget widens
 S2 MATHBRAIN_NAMED_EXTRA_SLOT=0 keeps the cap
 S3 two blocks whose first cards share a title count as one result: no extra slot
 S4 a whole-question card at the cap's place is never promoted to the extra slot
 S5 with cards chosen before the sub-queries, they stay whole and up to MATHBRAIN_SUBQ_APPEND new titles follow
 S6 the append skips a card whose title the whole-question block already carries; MATHBRAIN_SUBQ_APPEND=0 is off
 S7 a prompt that named nothing (named=False) is served the first cap cards and the budget stays

    python test_named_slots.py      (under a second; ALL PASS at the end)
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
    def __init__(self, nid, title):
        self.id, self.title, self.body, self.kind, self.metadata = nid, title, "", "claim", {}


class Hit:
    def __init__(self, nid, title):
        self.node = Node(nid, title)


def fresh(env):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    sys.modules.pop("math_sense", None)
    return importlib.import_module("math_sense")


def ids(hits):
    return [h.node.id for h in hits]


NAMES = ["Fatou's lemma", "Holder inequality", "Minkowski inequality", "Young's inequality", "Jensen's inequality"]


def five_blocks():
    return [[Hit("n%d" % i, t)] for i, t in enumerate(NAMES)]


def main():
    m = fresh({})
    b = five_blocks()
    merged = m._merge_subquery_blocks(b, [])
    ch, ex = m._named_slots(b, merged, 4, [], True)
    check(ids(ch) == ["n0", "n1", "n2", "n3", "n4"] and ex is True, "S1 extra slot %r %r" % (ids(ch), ex))

    off = fresh({"MATHBRAIN_NAMED_EXTRA_SLOT": "0"})
    ch, ex = off._named_slots(b, off._merge_subquery_blocks(b, []), 4, [], True)
    check(ids(ch) == ["n0", "n1", "n2", "n3"] and ex is False, "S2 switch %r %r" % (ids(ch), ex))

    m = fresh({})
    b = [[Hit("a", "Fatou's lemma")], [Hit("b", "Fatou's Lemma")], [Hit("c", "Holder inequality")],
         [Hit("d", "Minkowski inequality")], [Hit("e", "Young's inequality")]]
    ch, ex = m._named_slots(b, m._merge_subquery_blocks(b, []), 4, [], True)
    check(ex is False and len(ch) <= 4, "S3 shared title is one result %r %r" % (ids(ch), ex))

    b = five_blocks()[:4]
    whole = [Hit("w1", "Lebesgue dominated convergence")]
    ch, ex = m._named_slots(b, m._merge_subquery_blocks(b, whole), 4, [], True)
    check("w1" not in ids(ch) and ex is False, "S4 whole-question card not promoted %r" % ids(ch))

    b = five_blocks()
    base = [Hit("w1", "Lebesgue dominated convergence"), Hit("w2", "Monotone convergence")]
    ch, ex = m._named_slots(b, m._merge_subquery_blocks(b, base, lead=True), 4, base, True)
    check(ids(ch) == ["w1", "w2", "n0", "n1", "n2", "n3"] and ex is True, "S5 append after the block %r" % ids(ch))

    base = [Hit("w1", "Holder inequality")]
    ch, ex = m._named_slots(b, m._merge_subquery_blocks(b, base, lead=True), 4, base, True)
    no = fresh({"MATHBRAIN_SUBQ_APPEND": "0"})
    ch0, _ = no._named_slots(b, no._merge_subquery_blocks(b, base, lead=True), 4, base, True)
    check(ids(ch)[0] == "w1" and "n1" not in ids(ch) and "w1" in ids(ch0) and len(ch0) <= 5,
          "S6 title skip %r / off %r" % (ids(ch), ids(ch0)))

    m = fresh({})
    b = five_blocks()
    ch, ex = m._named_slots(b, m._merge_subquery_blocks(b, []), 4, [Hit("w1", "Lebesgue")], False)
    check(ids(ch) == ["n0", "n1", "n2", "n3"] and ex is False, "S7 unnamed %r %r" % (ids(ch), ex))
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
