"""Controls for the sub-query off switch (MATHBRAIN_SUBQ_OFF): with it set, no sub-query runs.

 O1 the switch is read in _main_body
 O2 MATHBRAIN_SUBQ_OFF=1 empties the sub-query list
 O3 unset or 0 leaves the list alone

    python test_subq_off.py      (under a second; ALL PASS at the end)
Runs the hook's own statement, cut from _main_body, against stubs.
"""
if __name__ == "__main__" and __import__("sys").argv[1:2] in (["-h"], ["--help"]):
    print((__doc__ or "").strip() or "run with no arguments")
    raise SystemExit(0)

import ast
import io
import os
import sys
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = io.open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read().replace("\r\n", "\n")
LINES = SRC.split("\n")
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def piece():
    for fn in ast.parse(SRC).body:
        if isinstance(fn, ast.FunctionDef) and fn.name == "_main_body":
            for node in ast.walk(fn):
                if isinstance(node, ast.If) and "MATHBRAIN_SUBQ_OFF" in ast.dump(node.test):
                    return textwrap.dedent("\n".join(LINES[node.lineno - 1:node.end_lineno]))
    return ""


OFF = piece()
check(bool(OFF), "O1 the off switch is in _main_body")


def run(val):
    os.environ.pop("MATHBRAIN_SUBQ_OFF", None)
    if val is not None:
        os.environ["MATHBRAIN_SUBQ_OFF"] = val
    g = {"os": os, "subs": ["a", "b"]}
    exec(OFF, g)
    return g["subs"]


try:
    if OFF:
        check(run("1") == [], "O2 MATHBRAIN_SUBQ_OFF=1 runs no sub-query")
        check(run(None) == ["a", "b"] and run("0") == ["a", "b"], "O3 unset or 0 keeps the sub-queries")
finally:
    os.environ.pop("MATHBRAIN_SUBQ_OFF", None)
print("ALL PASS" if all(results) and len(results) == 3 else "FAILED %d of %d" % (3 - results.count(True), 3))
sys.exit(0 if all(results) and len(results) == 3 else 1)
