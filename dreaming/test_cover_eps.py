"""Controls for the coverage tie-break in _select (MATHBRAIN_COVER_EPS).

 C1 the tie-break is in _select
 C2 off by default: the ranking is left as scored
 C3 on: among cards within eps of the top, more matched query words goes first
 C4 on: a card outside the eps band never moves ahead, however many words it matched

    python test_cover_eps.py      (under a second; ALL PASS at the end)
Runs the statements cut from _select against stub hits.
"""
if __name__ == "__main__" and __import__("sys").argv[1:2] in (["-h"], ["--help"]):
    print((__doc__ or "").strip() or "run with no arguments")
    raise SystemExit(0)

import ast
import io
import os
import sys
import textwrap
from types import SimpleNamespace as NS

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = io.open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read().replace("\r\n", "\n")
LINES = SRC.split("\n")
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def piece():
    for fn in ast.parse(SRC).body:
        if isinstance(fn, ast.FunctionDef) and fn.name == "_select":
            body = fn.body
            for i, st in enumerate(body):
                if isinstance(st, ast.Try) and "MATHBRAIN_COVER_EPS" in ast.dump(st):
                    nxt = body[i + 1]
                    return textwrap.dedent("\n".join(LINES[st.lineno - 1:nxt.end_lineno]))
    return ""


CODE = piece()
check(bool(CODE), "C1 the coverage tie-break is in _select")


def hit(nid, score, words):
    trace = [NS(channel=NS(value="lexical"), detail=w) for w in words]
    return NS(node=NS(id=nid), score=score, trace=trace)


def run(ranked, eps=None):
    os.environ.pop("MATHBRAIN_COVER_EPS", None)
    if eps is not None:
        os.environ["MATHBRAIN_COVER_EPS"] = eps
    g = {"os": os, "ranked": list(ranked), "_rs": lambda h: h.score}
    exec(CODE, g)
    return [h.node.id for h in g["ranked"]]


R = [hit("a", 1.00, ["x"]), hit("b", 0.97, ["x", "y", "z"]), hit("c", 0.50, ["x", "y", "z", "w"])]
try:
    if CODE:
        check(run(R) == ["a", "b", "c"], "C2 off by default")
        check(run(R, "0.05") == ["b", "a", "c"], "C3 the near-tied card with more matched words leads")
        check(run(R, "0.05")[2] == "c", "C4 a card outside the band stays where it was")
finally:
    os.environ.pop("MATHBRAIN_COVER_EPS", None)
print("ALL PASS" if all(results) and len(results) == 4 else "FAILED %d of %d" % (4 - results.count(True), 4))
sys.exit(0 if all(results) and len(results) == 4 else 1)
