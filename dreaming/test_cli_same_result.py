"""Controls for the 'same result on record' line in cli_query (MATHBRAIN_SAME_RESULT).

 T1 cli_query builds the line and prints it under its card
 T2 a twin that is itself printed gets no line
 T3 only the 'same result on record' label makes a line, one per card
 T4 off (SAME_RESULT False) makes no line; a failing lookup makes none and raises nothing

    python test_cli_same_result.py      (under a second; ALL PASS at the end)
Runs the statements cut from cli_query against stubs.
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


BUILD, SHOW = "", ""
for fn in ast.parse(SRC).body:
    if isinstance(fn, ast.FunctionDef) and fn.name == "cli_query":
        for node in ast.walk(fn):
            if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "SAME_RESULT":
                BUILD = textwrap.dedent("\n".join(LINES[node.lineno - 1:node.end_lineno]))
            if isinstance(node, ast.If) and "twin_line" in ast.dump(node.test) and "lab" not in ast.dump(node.test):
                SHOW = textwrap.dedent("\n".join(LINES[node.lineno - 1:node.end_lineno]))
check(bool(BUILD and SHOW), "T1 cli_query builds and prints the same-result line")


def run(chosen_ids, found, on=True, boom=False):
    def pit(ids):
        if boom:
            raise RuntimeError("store busy")
        return found
    out = []
    g = {"SAME_RESULT": on, "_pitfalls_for": pit, "chosen": [NS(node=NS(id=i)) for i in chosen_ids],
         "twin_line": {}, "print": out.append}
    exec(BUILD, g)
    for i in chosen_ids:
        g["node"] = NS(id=i)
        exec(SHOW, g)
    return out


if BUILD and SHOW:
    F = {"a": [("same result on record", "z", "Twin theorem")], "b": [("same result on record", "a", "A")]}
    check(run(["a", "b"], F) == ["  same result on record: Twin theorem (z)"], "T2 a printed twin gets no line")
    G = {"a": [("depends on", "y", "Y"), ("same result on record", "z", "Z1"), ("same result on record", "w", "Z2")]}
    check(run(["a"], G) == ["  same result on record: Z1 (z)"], "T3 the label decides, one line per card")
    check(run(["a"], F, on=False) == [] and run(["a"], F, boom=True) == [], "T4 off or a failed lookup makes no line")
print("ALL PASS" if all(results) and len(results) == 4 else "FAILED %d of %d" % (4 - results.count(True), 4))
sys.exit(0 if all(results) and len(results) == 4 else 1)
