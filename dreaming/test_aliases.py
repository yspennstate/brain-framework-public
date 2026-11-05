"""Controls for acronyms on the retrieval query (_expand_aliases) and the expansion loader (_load_expansion_once).

 A1 an acronym adds its spelled-out form; the spelled-out form adds the acronym
 A2 a prompt with both forms, or with none, is returned unchanged; MATHBRAIN_ALIASES=0 returns it unchanged
 A3 the prompt path and the agent recall path rank the expanded query and load the sidecar first
 A4 no sidecar beside the store: the loader stays unloaded and does not raise; MATHBRAIN_EXPANSION=0 never loads

    python test_aliases.py      (a few seconds; ALL PASS at the end)
"""
import importlib
import os
import sys
import tempfile

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


def main():
    store = os.path.join(tempfile.mkdtemp(), "store.sqlite")
    m = fresh({"MATHBRAIN_STORE": store})
    a = m._expand_aliases("What is the best known FEI lower bound?")
    b = m._expand_aliases("Kahn-Kalai-Linial for monotone functions")
    check("Fourier entropy influence" in a and a.startswith("What is the best known FEI lower bound?"), "A1 acronym -> words")
    check(b.endswith("(KKL)"), "A1 words -> acronym")
    s = "KKL, that is Kahn Kalai Linial"
    check(m._expand_aliases(s) == s and m._expand_aliases("the sunflower bound") == "the sunflower bound",
          "A2 both forms or none: unchanged")
    m0 = fresh({"MATHBRAIN_STORE": store, "MATHBRAIN_ALIASES": "0"})
    check(m0._expand_aliases("FEI") == "FEI", "A2 switch off")

    src = open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read()
    check("math_prefilter.select(STORE_PATH, _expand_aliases(prompt), k=PREFILTER_K)" in src
          and "_load_expansion_once()\n        result = retrieve(\n            _expand_aliases(prompt)," in src
          and "result = retrieve(\n            _expand_aliases(query), nodes=nodes" in src, "A3 both paths wired")

    m = fresh({"MATHBRAIN_STORE": store})
    m._load_expansion_once()
    check(m._EXPANSION_LOADED is False, "A4 no sidecar: not loaded, no raise")
    m = fresh({"MATHBRAIN_STORE": store, "MATHBRAIN_EXPANSION": "0"})
    check(m.EXPANSION_ON is False, "A4 switch off")
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
