"""Controls for the companion order in _attach_answers (MATHBRAIN_ANSWER_AFTER_RESULT).

 A1 a question card (an open question) gets its answer ahead of it
 A2 a proved, stated result keeps the lead; its proving claim follows it
 A3 a card some claim refutes counts as a question
 A4 MATHBRAIN_ANSWER_AFTER_RESULT=0 puts every companion first
 A5 a card with no proves/refutes edge leaves the block unchanged

    python test_answer_after_result.py      (a second or two; ALL PASS at the end)
Runs under a temporary home, so nothing of the user's is read or written.
"""
if __name__ == "__main__" and __import__("sys").argv[1:2] in (["-h"], ["--help"]):
    print((__doc__ or "").strip() or "run with no arguments")
    raise SystemExit(0)

import importlib
import os
import shutil
import sys
import tempfile
from types import SimpleNamespace as NS

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_SRC = os.path.normpath(os.path.join(HERE, "..", "..", "src"))
for _src in (os.environ.get("MATHBRAIN_SRC", ""), os.path.join(HERE, "..", "mathbrain", "src"), REPO_SRC):
    if _src and os.path.isdir(os.path.join(_src, "math_brain")):
        sys.path.insert(0, _src)
        break
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix="answer_after_")
for k in ("HOME", "USERPROFILE"):
    os.environ[k] = TMP
os.makedirs(os.path.join(TMP, ".claude", "dreaming"), exist_ok=True)
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def load(**env):
    os.environ.pop("MATHBRAIN_ANSWER_AFTER_RESULT", None)
    os.environ.pop("MATHBRAIN_ANSWER_COMPANION", None)
    os.environ.update(env)
    import math_sense
    importlib.reload(math_sense)
    return math_sense


def hit(nid, title, kind="claim"):
    return NS(node=NS(id=nid, title=title, kind=kind))


try:
    from math_brain.models import EdgeKind

    def edge(src, dst, kind):
        return NS(source_id=src, target_id=dst, kind=kind)

    Q = hit("q", "Spectral gap of the walk", kind="open_question")
    R = hit("r", "Theorem: the mixing time is n log n")
    X = hit("x", "Lower bound for the cover time")
    ANS, ING, REF = hit("a", "Answer to the gap"), hit("i", "Coupling lemma"), hit("f", "Counterexample")
    POOL = [Q, R, X, ANS, ING, REF]
    EDGES = [edge("a", "q", EdgeKind.PROVES), edge("i", "r", EdgeKind.PROVES), edge("f", "x", EdgeKind.REFUTES)]

    def ids(M, chosen):
        return [h.node.id for h in M._attach_answers(chosen, POOL, EDGES, cap_extra=3)]

    M = load()
    check(ids(M, [Q]) == ["a", "q"], "A1 the answer goes ahead of an open question")
    check(ids(M, [R]) == ["r", "i"], "A2 a proved result keeps the lead: %r" % ids(M, [R]))
    check(ids(M, [X]) == ["f", "x"], "A3 a refuted card counts as a question")
    M0 = load(MATHBRAIN_ANSWER_AFTER_RESULT="0")
    check(ids(M0, [R]) == ["i", "r"], "A4 off puts the companion first")
    M = load()
    check(ids(M, [ING]) == ["i"], "A5 no edge, no change")
except Exception as e:
    check(False, "raised %s: %s" % (type(e).__name__, e))
finally:
    os.environ.pop("MATHBRAIN_ANSWER_AFTER_RESULT", None)
    shutil.rmtree(TMP, ignore_errors=True)
print("ALL PASS" if all(results) and len(results) == 5 else "FAILED %d of %d" % (5 - results.count(True), 5))
sys.exit(0 if all(results) and len(results) == 5 else 1)
