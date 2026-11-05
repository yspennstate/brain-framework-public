"""Controls for the pasted-proof reading (_proof_text, _argument_shape).

 P1 a proof as a paper writes it (40+ words, LaTeX, "By Fatou's lemma") reads as an argument
 P2 the same length of text with Windows paths and no LaTeX does not
 P3 LaTeX and length but no citation does not; a curated name ("Tonelli") is a citation
 P4 numbered steps still read as an argument through ARGUMENT_SHAPE
 P5 MATHBRAIN_ARG_PROOFTEXT=0 turns the proof reading off and leaves the numbered steps alone
 P6 every argument test in the hook goes through _argument_shape (no bare ARGUMENT_SHAPE.search(prompt) left)

    python test_proof_text.py      (under a second; ALL PASS at the end)
"""
import importlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
for cand in (os.path.join(HERE, "..", "mathbrain", "src"), os.path.join(HERE, "..", "..", "mathbrain", "src")):
    if os.path.isdir(os.path.join(cand, "math_brain")):
        sys.path.insert(0, os.path.abspath(cand))
        break
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


PROOF = (r"Let $f_n \ge 0$ be measurable with $f_n \to f$ almost everywhere. We want to show that "
         r"$\int f \, d\mu \le \liminf_{n \to \infty} \int f_n \, d\mu$ holds for every such sequence. "
         r"By Fatou's lemma applied to the sequence $g_n = \inf_{k \ge n} f_k$, which increases to $f$, we get the bound "
         r"$\int f \le \sup_n \int g_n$, and since $g_n \le f_n$ for each $n$ the claim follows at once from monotonicity.")
NOCITE = PROOF.replace("By Fatou's lemma applied to", "Consider")
TONELLI = NOCITE + " Use Tonelli to exchange the two integrals."
PATHS = ("Copy the folder C:\\Users\\me\\Documents\\Reports\\Monthly into C:\\Backup\\Reports and open C:\\Logs\\Nightly\\run.txt "
         "and check that every line of the file was written before the job finished, and by Fatou's lemma nothing else "
         "needs to change in the folder or the log or the job that writes them, which runs each night at the same hour.")
STEPS = "Is the proof correct?\n1. a > 0\n2. so a^2 > 0\n3. hence a^3 > 0\n"


def main():
    m = fresh({})
    check(m._argument_shape(PROOF) and m._proof_text(PROOF), "P1 a pasted proof reads as an argument")
    check(m._argument_shape(PATHS) is None, "P2 backslash paths are not LaTeX")
    check(m._argument_shape(NOCITE) is None and m._argument_shape(TONELLI), "P3 no citation: no; a curated name: yes")
    check(m._argument_shape(STEPS) is not None and m._argument_shape("") is None and m._argument_shape(None) is None,
          "P4 numbered steps still count; empty input does not")

    m = fresh({"MATHBRAIN_ARG_PROOFTEXT": "0"})
    check(m._argument_shape(PROOF) is None and m._argument_shape(STEPS) is not None, "P5 switched off")

    src = open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read()
    bare = len(re.findall(r"ARGUMENT_SHAPE\.search\(prompt\)", src))
    calls = len(re.findall(r"_argument_shape\(prompt\)", src))
    check(bare == 0 and calls >= 7, "P6 no bare ARGUMENT_SHAPE test left (%d bare, %d via _argument_shape)" % (bare, calls))
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
