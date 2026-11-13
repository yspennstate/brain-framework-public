"""Controls for the math hook's signal-sidecar load.

 S1 the hook source opens <store>.signals.sqlite on the prefilter path (load_signal_db(signal_db_path(STORE_PATH)))
 S2 that call sits in the else-branch of the prefilter test, so the full-store path still loads the pickle cache
 S3 run as written against a stub retrieval module: with a prefilter, load_signal_db gets the sidecar path
 S4 without a prefilter, the pickle cache is loaded and the sidecar is not
 S5 a retrieval module without load_signal_db does not break the branch

    python test_signal_db_load.py      (under a second; ALL PASS at the end)
"""
if __name__ == "__main__" and __import__("sys").argv[1:2] in (["-h"], ["--help"]):
    print((__doc__ or "").strip() or "run with no arguments")
    raise SystemExit(0)

import ast
import io
import os
import sys
import textwrap
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = io.open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read().replace("\r\n", "\n")
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def branch():
    """The `if prefilter_ids is None: ... else: ...` statement inside _main_body, as source."""
    tree = ast.parse(SRC)
    lines = SRC.split("\n")
    for fn in tree.body:
        if isinstance(fn, ast.FunctionDef) and fn.name == "_main_body":
            for node in ast.walk(fn):
                if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                        and getattr(node.test.left, "id", "") == "prefilter_ids"
                        and isinstance(node.test.ops[0], ast.Is)
                        and any("load_signal_cache" in ast.dump(b) for b in node.body)):
                    return node, textwrap.dedent("\n".join(lines[node.lineno - 1:node.end_lineno]))
    return None, ""


node, code = branch()
check("load_signal_db(signal_db_path(STORE_PATH))" in SRC, "S1 the hook opens the signal sidecar")
check(node is not None and node.orelse and any("load_signal_db" in ast.dump(b) for b in node.orelse),
      "S2 the sidecar load is the else-branch of the prefilter test")

calls = []
stub = types.ModuleType("math_brain.retrieval")
stub.load_signal_cache = lambda p: calls.append(("cache", p))
stub.load_signal_db = lambda p: calls.append(("db", p))
stub.signal_db_path = lambda s: str(s) + ".signals.sqlite"
pkg = types.ModuleType("math_brain")
pkg.retrieval = stub
saved = {k: sys.modules.get(k) for k in ("math_brain", "math_brain.retrieval")}
sys.modules["math_brain"], sys.modules["math_brain.retrieval"] = pkg, stub
try:
    env = {"os": os, "STORE_PATH": os.path.join("store", "brain.sqlite"), "prefilter_ids": ["n1", "n2"]}
    if code:
        exec(code, env)
    check(calls == [("db", os.path.join("store", "brain.sqlite") + ".signals.sqlite")],
          "S3 with a prefilter the sidecar path is opened: %r" % calls)
    calls.clear()
    env["prefilter_ids"] = None
    if code:
        exec(code, env)
    check(len(calls) == 1 and calls[0][0] == "cache", "S4 without a prefilter only the pickle cache loads: %r" % calls)
    calls.clear()
    del stub.load_signal_db
    env["prefilter_ids"] = ["n1"]
    err = None
    try:
        if code:
            exec(code, env)
    except Exception as e:
        err = e
    check(bool(code) and err is None and calls == [], "S5 a retrieval module without the sidecar loader is harmless")
finally:
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v
print("ALL PASS" if all(results) else "FAILED %d of %d" % (results.count(False), len(results)))
sys.exit(0 if all(results) else 1)
