"""Controls for MATHBRAIN_ANCHORS_4, MATHBRAIN_ANCHORS_5 and MATHBRAIN_ANCHORS_POSSESSIVE in the math gate.

 A1 with the dials on, a plural, a singular-value and a possessive-inequality question open the gate
 A2 with each dial off, its question is refused again
 A3 plain non-mathematical text stays refused with every dial on

    python test_anchors_4_5_possessive.py      (a few seconds; ALL PASS at the end)
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

CASES = [
    ("MATHBRAIN_ANCHORS_4", "the real eigenvalues of the real Ginibre ensemble"),
    ("MATHBRAIN_ANCHORS_5", "the case where the fifth singular value is bigger than 1"),
    ("MATHBRAIN_ANCHORS_POSSESSIVE", "State Young's inequality for convolutions"),
]
SILENT = ["please restart the backup job tonight and tell me when it is done",
          "Young's band played a long set at the festival"]


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def gate(text, off=None):
    env = dict(os.environ)
    for k, _ in CASES:
        env.pop(k, None)
    if off:
        env[off] = "0"
    env["PYTHONPATH"] = os.pathsep.join([HERE] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
    probe = "import sys, math_sense as m; print(bool(m._legacy_math_gate(sys.argv[1])))"
    r = subprocess.run([sys.executable, "-c", probe, text], cwd=HERE, env=env, capture_output=True, text=True,
                       timeout=120)
    return ((r.stdout + r.stderr).strip().splitlines() or [""])[-1]


def main():
    for dial, text in CASES:
        check(gate(text) == "True", "A1 %s on: %r opens the gate" % (dial, text))
        check(gate(text, off=dial) == "False", "A2 %s=0: %r is refused" % (dial, text))
    for text in SILENT:
        check(gate(text) == "False", "A3 stays refused: %r" % text)
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
