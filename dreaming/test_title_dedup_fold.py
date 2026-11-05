"""Controls for MATHBRAIN_TITLE_DEDUP_FOLD: _select's title keys keep accents by default and fold them when it is on.

 T1 default: an accented and a plain title are two keys for _select, one key for the final twin pass (fold=True)
 T2 MATHBRAIN_TITLE_DEDUP_FOLD=1: they are one key for _select too
 T3 every title key in _select goes through _title_key (no inlined copy of the normalisation is left there)

    python test_title_dedup_fold.py      (a few seconds; ALL PASS at the end)
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


PROBE = ("import math_sense as m; a, b = 'H\\u00f6lder inequality', 'Holder Inequality'; "
         "print(m._title_key(a) == m._title_key(b), m._title_key(a, fold=True) == m._title_key(b, fold=True))")


def run(val):
    env = dict(os.environ)
    env.pop("MATHBRAIN_TITLE_DEDUP_FOLD", None)
    if val is not None:
        env["MATHBRAIN_TITLE_DEDUP_FOLD"] = val
    env["PYTHONPATH"] = os.pathsep.join([HERE] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=HERE, env=env, capture_output=True, text=True, timeout=120)
    return ((r.stdout + r.stderr).strip().splitlines() or [""])[-1]


def main():
    d, on = run(None), run("1")
    check(d == "False True", "T1 default: _select keeps accents, the twin pass folds (%r)" % d)
    check(on == "True True", "T2 MATHBRAIN_TITLE_DEDUP_FOLD=1 folds in _select too (%r)" % on)
    src = open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read()
    body = src[src.index("\ndef _select("):]
    body = body[:body.index("\ndef ", 1)]
    inlined = re.findall(r're\.sub\(r"\[\^a-z0-9\]\+", " ",\s*\((?:ranked\[0\]|h|hit|node)\.', body)
    uses = body.count("_title_key(")
    check(not inlined and uses >= 5, "T3 _select uses _title_key (%d inlined, %d uses)" % (len(inlined), uses))
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
