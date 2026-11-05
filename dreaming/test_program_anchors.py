"""Controls for MATHBRAIN_PROGRAM_ANCHORS in the math gate.

 P1 with the dial on, questions naming the programs open the gate
 P2 with MATHBRAIN_PROGRAM_ANCHORS=0 the same questions are refused again
 P3 plain non-mathematical text stays refused with the dial on

    python test_program_anchors.py      (a few seconds; ALL PASS at the end)
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
results = []

OPEN = ["What FEI constants are proved for read-k DNFs",
        "Is the KKL bound tight for LTFs",
        "the total influence of a threshold function"]
SILENT = ["please restart the backup job tonight and tell me when it is done",
          "what time does the train to the airport leave"]


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def gate(text, off=False):
    env = dict(os.environ)
    env.pop("MATHBRAIN_PROGRAM_ANCHORS", None)
    if off:
        env["MATHBRAIN_PROGRAM_ANCHORS"] = "0"
    env["PYTHONPATH"] = os.pathsep.join([HERE] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
    probe = "import sys, math_sense as m; print(bool(m._legacy_math_gate(sys.argv[1])))"
    r = subprocess.run([sys.executable, "-c", probe, text], cwd=HERE, env=env, capture_output=True, text=True,
                       timeout=120)
    return ((r.stdout + r.stderr).strip().splitlines() or [""])[-1]


def main():
    for text in OPEN:
        check(gate(text) == "True", "P1 dial on: %r opens the gate" % text)
        check(gate(text, off=True) == "False", "P2 dial off: %r is refused" % text)
    for text in SILENT:
        check(gate(text) == "False", "P3 stays refused: %r" % text)
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
