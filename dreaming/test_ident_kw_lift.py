"""Controls for the keyword-identifier lift in _select (MATHBRAIN_IDENT_KW_BOOST, relative, default 1.0).

 K1 the boost defaults to 1.0 and 0 switches it off
 K2 only an identifier named with a keyword counts ("Problem 4.45"); a bare decimal ("0.65") does not
 K3 the card whose title carries the named problem is lifted past a higher-scored card; a card with the same number
    under another source's names is not
 K4 with the boost at 0 the plain lexical order stands

    python test_ident_kw_lift.py      (under a second; ALL PASS at the end)
"""
import importlib
import os
import sys
from types import SimpleNamespace as NS

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


def hit(nid, title, lex):
    node = NS(id=nid, title=title, kind="claim", statement="", body="", summary="", source="", tags=[], metadata={})
    return NS(node=node, lexical_score=lex, symbol_score=0.0, formula_score=0.0, graph_score=0.0, score=lex)


def order(m, prompt):
    hits = [hit("b", "Mixing times of lazy walks", 10.0),
            hit("a", "Lyons-Peres Open Problem 4.45", 3.0),
            hit("c", "Aldous-Fill Problem 4.45", 5.0)]
    return [h.node.id for h in m._select(hits, apply_gates=False, cap=3, prompt=prompt, anchor=False)]


def main():
    m = fresh({})
    on = m._ident_kw_boost()
    off = fresh({"MATHBRAIN_IDENT_KW_BOOST": "0"})._ident_kw_boost()
    check(on == 1.0 and off == 0.0, "K1 default 1.0, 0 is off")

    m = fresh({})
    check(m._kw_idents("Open Problem 4.45, with f at 0.65") == {"4.45"}
          and m._kw_idents("set the dial to 0.65") == set(), "K2 keyword identifiers only")

    prompt = "In Lyons and Peres, Open Problem 4.45 asks whether the walk mixes in order n log n."
    got = order(fresh({}), prompt)
    check(got[0] == "a" and got.index("c") > got.index("b"), "K3 named problem lifted, other source not: %r" % got)

    got0 = order(fresh({"MATHBRAIN_IDENT_KW_BOOST": "0"}), prompt)
    check(got0 == ["b", "c", "a"], "K4 boost 0 keeps the lexical order: %r" % got0)
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
