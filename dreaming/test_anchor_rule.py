"""Controls for the math hook's anchor rule: on a long prompt a card must share content words with it.

 A1 a short question is exempt (k = 0); a 12-word prompt needs 2; a 40-word argument needs 3
 A2 LaTeX command names and stop words are not content words; an en dash folds to a hyphen
 A3 a card whose title the prompt names is kept; a card sharing three content words is kept; an off-domain card goes
 A4 _select on a long prompt drops the off-domain card without refilling its slot
 A5 MATHBRAIN_ANCHOR_SHARED=0 switches the rule off

    python test_anchor_rule.py      (under a second; ALL PASS at the end)
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


def fresh(env):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    sys.modules.pop("math_sense", None)
    return importlib.import_module("math_sense")


LONG = ("Let u be a weak solution of the elliptic equation on a bounded Lipschitz domain. By the Poincare inequality "
        "the gradient controls the mean oscillation, and Cauchy–Schwarz bounds the bilinear form; coercivity "
        "follows, so Lax-Milgram gives existence and uniqueness of the weak solution in the Sobolev space, with the "
        "energy estimate depending only on the ellipticity constant, the domain diameter and the boundary regularity "
        "of the coefficients appearing in the operator. Density of smooth functions, a trace theorem, Rellich "
        "compactness and a duality pairing with the dual space complete the variational argument.")


def main():
    m = fresh({})
    n_long = len(m._content_words(LONG))
    check(m._anchor_min_shared("what is the Poincare inequality") == 0
          and m._anchor_min_shared(" ".join("alpha beta gamma delta epsilon zeta theta kappa lambda sigma omega "
                                            "tau upsilon".split())) == 2
          and n_long >= 40 and m._anchor_min_shared(LONG) == 3,
          "A1 tiers: short 0, medium 2, long %d words -> 3" % n_long)
    w = m._content_words(r"\frac{a}{b} \leq the Cauchy" + "–" + "Schwarz bound")
    check("frac" not in w and "leq" not in w and "the" not in w and "cauchy-schwarz" in w,
          "A2 content words %r" % sorted(w))
    pw = m._anchor_words(LONG)
    named = Node("n", "Lax-Milgram theorem")
    shared = Node("s", "An existence result", "coercivity of the bilinear form on the Sobolev space")
    off = Node("o", "Johnson-Lindenstrauss lemma", "random projections preserve pairwise distances")
    check(m._anchored(named, pw, 3) and m._anchored(shared, pw, 3) and not m._anchored(off, pw, 3),
          "A3 named kept, shared kept, off-domain dropped")

    class Hit:
        def __init__(self, node, s):
            self.node = node
            self.lexical_score, self.symbol_score, self.formula_score, self.graph_score = s, 0.0, 0.0, 0.0

    hits = [Hit(off, 9.0), Hit(named, 8.0), Hit(shared, 7.0)]
    got = [h.node.id for h in (m._select(hits, apply_gates=False, prompt=LONG) or [])]
    check("o" not in got and "n" in got and len(got) <= 2, "A4 select on the argument keeps %r" % got)

    m = fresh({"MATHBRAIN_ANCHOR_SHARED": "0"})
    got = [h.node.id for h in (m._select(hits, apply_gates=False, prompt=LONG) or [])]
    check(m._anchor_min_shared(LONG) == 0 and "o" in got, "A5 switch off keeps %r" % got)
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
