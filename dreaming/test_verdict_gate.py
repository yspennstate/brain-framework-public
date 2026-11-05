"""Controls for the pasted-verdict gate (_verdict_text).

 V1 a reviewer's verdict in the first 400 characters silences the prompt and names the words in the note
 V2 "UNSAT verdict" (a solver's verdict) and a verdict word past 400 characters do not
 V3 MATHBRAIN_VERDICT_GATE=0 switches it off
 V4 the gate runs in _main_body right after the non-math gate

    python test_verdict_gate.py      (under a second; ALL PASS at the end)
"""
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def fresh(env):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    sys.modules.pop("math_sense", None)
    return importlib.import_module("math_sense")


VERDICT = "Executive verdict: potentially publishable after a major revision. Fix the bound in Lemma 3 and resubmit."
SOLVER = "The SAT solver returned an UNSAT verdict for n = 7, so the Ramsey colouring does not exist."
LATE = "Let x be a real number. " * 20 + "Is this publishable?"


def main():
    m = fresh({})
    notes = []
    m._silence_note = lambda data, reason, harness_only=False: notes.append(reason)
    check(m._verdict_text(VERDICT, {}) and notes and notes[0].startswith("verdict_text:")
          and "executive verdict" in notes[0], "V1 a verdict silences the prompt: %r" % notes[:1])
    check(not m._verdict_text(SOLVER, {}) and not m._verdict_text(LATE, {}), "V2 a solver verdict and a late word pass")

    m = fresh({"MATHBRAIN_VERDICT_GATE": "0"})
    check(not m._verdict_text(VERDICT, {}), "V3 switched off")

    src = open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read()
    a = src.index("        if _nonmath_text(prompt, data):\n            return\n")
    b = src.index("        if _verdict_text(prompt, data):\n            return\n")
    check(0 < b - a < 120, "V4 runs right after the non-math gate")
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
