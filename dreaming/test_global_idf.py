"""Controls for the store-wide IDF installer (MATHBRAIN_GLOBAL_IDF, off by default).

 G1 unset, the ranker keeps pool statistics (GLOBAL_LEXICAL_DF is None)
 G2 mode 1 or 2 with <store>.lexdf.pkl present installs df, n and the mode
 G3 a missing or unreadable table, or an unknown mode, leaves GLOBAL_LEXICAL_DF None and raises nothing
 G4 the installer is called in _main_body after the store import and before the non-mathematics check

    python test_global_idf.py      (under a second; ALL PASS at the end)
"""
import ast
import importlib
import os
import pickle
import sys
import tempfile

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
    m = importlib.import_module("math_sense")
    import math_brain.retrieval as R
    R.GLOBAL_LEXICAL_DF, R.GLOBAL_LEXICAL_N, R.GLOBAL_LEXICAL_MODE = None, 0, 2
    return m, R


def main():
    d = tempfile.mkdtemp()
    store = os.path.join(d, "store.sqlite3")
    with open(store + ".lexdf.pkl", "wb") as fh:
        pickle.dump({"df": {"matching": 3, "graph": 7}, "n": 40}, fh)

    m, R = fresh({"MATHBRAIN_STORE": store})
    m._install_global_idf()
    check(R.GLOBAL_LEXICAL_DF is None, "G1 off by default")

    ok2 = True
    for mode in ("1", "2"):
        m, R = fresh({"MATHBRAIN_STORE": store, "MATHBRAIN_GLOBAL_IDF": mode})
        m._install_global_idf()
        ok2 = ok2 and R.GLOBAL_LEXICAL_DF == {"matching": 3, "graph": 7} and R.GLOBAL_LEXICAL_N == 40 \
            and R.GLOBAL_LEXICAL_MODE == int(mode)
    check(ok2, "G2 modes 1 and 2 install the table")

    bad = os.path.join(d, "bad.sqlite3")
    with open(bad + ".lexdf.pkl", "wb") as fh:
        fh.write(b"not a pickle")
    outs = []
    for env in ({"MATHBRAIN_STORE": os.path.join(d, "none.sqlite3"), "MATHBRAIN_GLOBAL_IDF": "2"},
                {"MATHBRAIN_STORE": bad, "MATHBRAIN_GLOBAL_IDF": "2"},
                {"MATHBRAIN_STORE": store, "MATHBRAIN_GLOBAL_IDF": "7"}):
        m, R = fresh(env)
        R.GLOBAL_LEXICAL_DF = {"stale": 1}
        m._install_global_idf()
        outs.append(R.GLOBAL_LEXICAL_DF)
    check(outs == [None, None, None], "G3 missing, unreadable, unknown mode: %r" % outs)

    src = open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read()
    body = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_main_body")
    seg = lambda n: ast.get_source_segment(src, n) or ""
    calls = {}
    for n in ast.walk(body):
        if isinstance(n, ast.Call):
            calls.setdefault(seg(n.func), n.lineno)
    imp = [n.lineno for n in ast.walk(body) if isinstance(n, ast.ImportFrom) and n.module == "math_brain.store"]
    ok4 = ("_install_global_idf" in calls and "_nonmath_text" in calls and imp
           and min(imp) < calls["_install_global_idf"] < calls["_nonmath_text"])
    check(ok4, "G4 installed after the store import, before the non-mathematics check")
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
