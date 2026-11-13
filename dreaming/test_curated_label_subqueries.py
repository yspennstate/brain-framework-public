"""Controls for the math hook's curated-name sub-queries: a name a proof uses without a kind word is still asked for.

 C1 a curated name in running text ("by Fubini we swap") is asked for under its card's name
 C2 a curated name in a LaTeX reference key (\\eqref{eq:plancherel}) is asked for where the reference sits;
    MATHBRAIN_CURATED_LABELS=0 switches that off
 C3 an extracted name that is part of a curated one gives way to it in place ("value decomposition" -> SVD)
 C4 "Weyl" in a reference key is Weyl's inequality only when the proof writes singular values or eigenvalues
 C5 a sentence word is not part of the name: "By Fatou's lemma" asks for Fatou's lemma
 C6 a double-barrelled name after a citation verb ("apply Cauchy--Schwarz") is asked for

    python test_curated_label_subqueries.py      (under a second; ALL PASS at the end)
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


def load(**env):
    for k in ("MATHBRAIN_CURATED_LABELS", "MATHBRAIN_LABEL_CONTEXT", "MATHBRAIN_CURATED_SUPERSEDE"):
        os.environ.pop(k, None)
    os.environ.update(env)
    import math_sense
    return importlib.reload(math_sense)


def low(qs):
    return [q.lower() for q in qs]


M = load()
q = M._named_result_subqueries("Then by Fubini we swap the two integrals and the bound follows for every x.")
check(any("fubini" in s for s in low(q)), "C1 'by Fubini' asks for Fubini's theorem: %r" % q)

p2 = "Taking norms, \\eqref{eq:plancherel} gives the L2 bound, and the series converges in the mean."
q = M._named_result_subqueries(p2)
check(any("plancherel" in s for s in low(q)), "C2 a reference key names the Plancherel theorem: %r" % q)
M = load(MATHBRAIN_CURATED_LABELS="0")
q = M._named_result_subqueries(p2)
check(not any("plancherel" in s for s in low(q)), "C2 MATHBRAIN_CURATED_LABELS=0 leaves the key alone: %r" % q)

M = load()
q = M._named_result_subqueries("Write A by its singular-value decomposition and apply the bound to each factor.")
check(any("singular value decomposition" in s.replace("-", " ") for s in low(q)) and not any(s == "value decomposition" for s in low(q)),
      "C3 the curated name supersedes its fragment: %r" % q)

pw = "By \\ref{lem:weyl} the singular values s_k of A+B are within the norm of B of those of A."
q = M._named_result_subqueries(pw)
check(any("weyl's inequality" in s for s in low(q)), "C4 lem:weyl with singular values is Weyl's inequality: %r" % q)
q = M._named_result_subqueries("By \\ref{lem:weyl} the weak solution is smooth in the interior of the domain.")
check(not any("weyl's inequality" in s for s in low(q)), "C4 lem:weyl without that notation is not: %r" % q)
M = load(MATHBRAIN_LABEL_CONTEXT="0")
q = M._named_result_subqueries(pw)
check(not any("weyl's inequality" in s for s in low(q)), "C4 MATHBRAIN_LABEL_CONTEXT=0 switches it off: %r" % q)

M = load()
q = M._named_result_subqueries("By Fatou's lemma the liminf of the integrals bounds the integral of the liminf.")
check(any(s.startswith("fatou") for s in low(q)) and not any(s.startswith("by ") for s in low(q)),
      "C5 'By Fatou's lemma' asks for Fatou's lemma: %r" % q)

q = M._named_result_subqueries("We apply Cauchy--Schwarz in the law of X to bound the cross term by the product.")
check(any("cauchy" in s and "schwarz" in s for s in low(q)), "C6 'apply Cauchy--Schwarz' is asked for: %r" % q)

for k in ("MATHBRAIN_CURATED_LABELS", "MATHBRAIN_LABEL_CONTEXT"):
    os.environ.pop(k, None)
print("ALL PASS" if all(results) else "FAILED %d of %d" % (results.count(False), len(results)))
sys.exit(0 if all(results) else 1)
