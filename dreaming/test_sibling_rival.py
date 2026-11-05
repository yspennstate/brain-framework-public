"""Controls for sibling rivals in the margin test (named family, possessive family, vocabulary cards).

 S1 _nf_fold folds accents and case; MATHBRAIN_NAMED_FAMILY_FOLD=0 keeps the accents
 S2 the possessive pattern reads "Name's" with a straight or curly apostrophe, and nothing else
 S3 the rival choice skips named-family, possessive-family and vocabulary runner-ups, each behind its own switch

    python test_sibling_rival.py      (under a second; ALL PASS at the end)
"""
import ast
import importlib
import os
import re
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


def main():
    m = fresh({})
    check(m._nf_fold("Grönwall's Inequality") == "gronwall's inequality", "S1 accents and case fold by default")
    m = fresh({"MATHBRAIN_NAMED_FAMILY_FOLD": "0"})
    check(m._nf_fold("Grönwall") == "grönwall", "S1 fold off keeps the accent")

    src = open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read()
    pat = re.search(r"def _possessives\(title\):\n\s+return \{m\.casefold\(\) for m in re\.findall\((r\".*?\"), title", src)
    rx = ast.literal_eval(pat.group(1)) if pat else ""
    got = lambda s: {x.casefold() for x in re.findall(rx, s)}
    check(rx and got("Bernstein's Inequality for Sums") == {"bernstein"}
          and got("Bernstein’s inequality") == {"bernstein"} and not got("the Bernsteins inequality")
          and not got("Bernstein'sx"), "S2 possessive pattern")

    sel = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_select")
    body = ast.get_source_segment(src, sel)
    need = ["MATHBRAIN_NAMED_FAMILY\"", "MATHBRAIN_POSSESSIVE_FAMILY", "MATHBRAIN_VOCAB_NOT_RIVAL",
            "and not (_top_poss and _top_poss & _possessives(h.node.title))",
            "and not (not _top_vocab and _is_vocab(h.node))",
            "and not (_named and _named & set(re.findall(r\"[a-z0-9]+\", _nf_fold(h.node.title))))"]
    check(all(s in body for s in need), "S3 the rival choice carries all three sibling rules behind their switches")
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
