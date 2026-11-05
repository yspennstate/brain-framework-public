"""Controls for MATHBRAIN_TYPO_FIX in the math hook.

 T1 with the dial on, listed misspellings are corrected and case is kept
 T2 with MATHBRAIN_TYPO_FIX=0 the text is returned as typed
 T3 words that are not on the list, and words inside longer words, are left alone
 T4 a misspelt question opens the gate once corrected

    python test_typo_fix.py      (a few seconds; ALL PASS at the end)
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def run(expr, text, off=False):
    env = dict(os.environ)
    env.pop("MATHBRAIN_TYPO_FIX", None)
    if off:
        env["MATHBRAIN_TYPO_FIX"] = "0"
    env["PYTHONPATH"] = os.pathsep.join([HERE] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
    probe = "import sys, math_sense as m; t = sys.argv[1]; print(repr(" + expr + "))"
    r = subprocess.run([sys.executable, "-c", probe, text], cwd=HERE, env=env, capture_output=True, text=True,
                       timeout=120)
    return ((r.stdout + r.stderr).strip().splitlines() or [""])[-1]


def main():
    q = "the full proof of the 5 singualr value version for Gussian matricies"
    want = repr("the full proof of the 5 singular value version for Gaussian matrices")
    check(run("m._typo_fix(t)", q) == want, "T1 corrected with case kept")
    check(run("m._typo_fix(t)", q, off=True) == repr(q), "T2 dial off returns the text as typed")
    plain = "the kernel trick and a kernalized estimator"
    check(run("m._typo_fix(t)", plain) == repr(plain), "T3 unlisted words and longer words left alone")
    bad = "what is the singualr value distirbution of a ranodm matrix"
    check(run("bool(m._legacy_math_gate(m._typo_fix(t)))", bad) == "True", "T4 corrected question opens the gate")
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
