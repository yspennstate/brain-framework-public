"""Controls for the math hook's final title dedup: one card per accent-folded title on the finished block.

 T1 two cards whose titles differ only in accents, case and punctuation: the first keeps its place, the second goes
 T2 when the first carries an encyclopedia opening and the second a statement, the statement takes the first's place
 T3 different titles are all kept, in order
 T4 MATHBRAIN_FINAL_TITLE_DEDUP=0 leaves the block as it was

    python test_title_twins.py      (under a second; ALL PASS at the end)
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
        self.id, self.title, self.body = nid, title, body


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
    return [h.node.id for h in hits]


def main():
    m = fresh({})
    block = [Hit("a", "Poincaré Inequality"), Hit("x", "Sobolev Embedding"), Hit("b", "poincare inequality.")]
    got = ids(m._drop_title_twins(block))
    check(got == ["a", "x"], "T1 accent and case twins: kept %r" % got)

    lede = "In mathematics, the Poincare inequality is a result in the theory of Sobolev spaces."
    stmt = "Let D be a bounded Lipschitz domain; then ||u - u_avg|| <= C ||grad u|| for some C."
    block = [Hit("a", "Poincare Inequality", lede), Hit("x", "Sobolev Embedding"), Hit("b", "Poincaré inequality", stmt)]
    got = ids(m._drop_title_twins(block))
    check(got == ["b", "x"], "T2 the statement replaces the lede at its place: %r" % got)

    block = [Hit("a", "Lax-Milgram Theorem"), Hit("b", "Riesz Representation"), Hit("c", "Banach Fixed Point")]
    got = ids(m._drop_title_twins(block))
    check(got == ["a", "b", "c"], "T3 different titles kept: %r" % got)

    m = fresh({"MATHBRAIN_FINAL_TITLE_DEDUP": "0"})
    block = [Hit("a", "Poincaré Inequality"), Hit("b", "Poincare Inequality")]
    got = ids(m._drop_title_twins(block))
    check(got == ["a", "b"], "T4 switch off: kept %r" % got)
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
