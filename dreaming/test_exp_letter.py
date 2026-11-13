"""Controls for MATHBRAIN_EXP_LETTER: a single-letter exponent counts as notation in the legacy math gate.

 E1 the dial: on by default, off for 0
 E2 the pattern takes 3^m, (-1)^m and x^{t}, and not a caret inside a word or a two-letter exponent
 E3 two letter-exponent notations alone pass the structural path; with the dial off they do not
 E4 one notation alone ("x^y") stays refused

    python test_exp_letter.py      (a few seconds; ALL PASS at the end)
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


PROBE = r"""
import math_sense as m
print(int(m.EXP_LETTER), int(m._legacy_math_gate("count 3^m + (-1)^m")), int(m._legacy_math_gate("x^y")))
"""


def run(val):
    env = dict(os.environ)
    env.pop("MATHBRAIN_EXP_LETTER", None)
    if val is not None:
        env["MATHBRAIN_EXP_LETTER"] = val
    env["PYTHONPATH"] = os.pathsep.join([HERE] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=HERE, env=env, capture_output=True, text=True, timeout=120)
    return ((r.stdout + r.stderr).strip().splitlines() or [""])[-1]


def main():
    on, off = run(None), run("0")
    check(on.startswith("1 ") and off.startswith("0 "), "E1 dial: default=%r off=%r" % (on, off))
    sys.path.insert(0, HERE)
    import math_sense as m
    hits = [bool(m.EXP_LETTER_RE.search(s)) for s in ("3^m", "(-1)^m", "x^{t}")]
    misses = [bool(m.EXP_LETTER_RE.search(s)) for s in ("abc^m", "x^yz")]
    check(all(hits) and not any(misses), "E2 pattern hits=%s misses=%s" % (hits, misses))
    check(on.split()[1:2] == ["1"] and off.split()[1:2] == ["0"], "E3 two letter exponents: on=%r off=%r" % (on, off))
    check(on.split()[2:3] == ["0"], "E4 one notation alone stays refused (%r)" % on)
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
        print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
        raise SystemExit(0)
    sys.exit(main())
