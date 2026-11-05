r"""Controls for the math hook's LaTeX accent fold: a proof pasted from its source reaches the names it cites.

 A1 the symbol accents inside a word fold: H\"older -> Holder, Poincar\'e -> Poincare, Poincar\'{e} -> Poincare
 A2 the letter accents with one braced letter fold anywhere: Erd\H{o}s -> Erdos, \v{C}ech -> Cech
 A3 the braced form {\"o} folds: H{\"o}lder -> Holder
 A4 a JSON-style \" at the start of a word is left alone, and text with no backslash is returned as it was
 A5 MATHBRAIN_LATEX_ACCENTS=0 turns the fold off

    python test_latex_accents.py      (under a second; ALL PASS at the end)
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


def fresh(env):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    sys.modules.pop("math_sense", None)
    return importlib.import_module("math_sense")


def main():
    m = fresh({})
    f = m._latex_accents
    got = [f(r"By H\"older's inequality"), f(r"Poincar\'e"), f(r"Poincar\'{e}")]
    check(got == ["By Holder's inequality", "Poincare", "Poincare"], "A1 symbol accents: %r" % got)
    got = [f(r"Erd\H{o}s"), f(r"\v{C}ech cohomology")]
    check(got == ["Erdos", "Cech cohomology"], "A2 letter accents: %r" % got)
    got = f(r'H{\"o}lder')
    check(got == "Holder", "A3 braced accent: %r" % got)
    s = r'say \"hello\" now'
    plain = "Holder and Minkowski"
    check(f(s) == s and f(plain) == plain, "A4 a word-start quote and plain text unchanged: %r" % f(s))
    m = fresh({"MATHBRAIN_LATEX_ACCENTS": "0"})
    check(m._latex_accents(r'H\"older') == r'H\"older', "A5 the switch turns it off")
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
