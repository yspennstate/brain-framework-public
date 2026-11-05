"""Controls for named results of a wider kind (MATHBRAIN_SUBQ_KINDS2, MATHBRAIN_GATE_NAMED_KIND).

 K1 the name extractor reads "Fredholm alternative", "Neumann series", "Moser iteration"; =0 restores the old kinds
 K2 a lowercase "upper bound" is never a name
 K3 with a tiny store, the gate opens for a named result whose card the store holds, and only for that one
 K4 MATHBRAIN_GATE_NAMED_KIND=0 closes that route; an all-caps name never counts

    python test_named_kind.py      (under a second; ALL PASS at the end)
"""
import importlib
import os
import sqlite3
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


def names(m, s):
    return [(g.group(1), g.group(2)) for g in m._NAMED_RESULT_RE.finditer(s) if g.group(1)]


def main():
    d = tempfile.mkdtemp()
    store = os.path.join(d, "store.sqlite")
    con = sqlite3.connect(store)
    con.execute("create table nodes (id text, title text)")
    con.executemany("insert into nodes values (?, ?)", [("n1", "The Fredholm Alternative: compact perturbations"),
                                                          ("n2", "Neumann series for (I - T)^-1 in a Banach algebra"),
                                                          ("n3", "Moser iteration")])
    con.commit()
    con.close()

    m = fresh({"MATHBRAIN_STORE": store})
    got = names(m, "Apply the Fredholm alternative, then the Neumann series and Moser iteration.")
    check(("Fredholm", "alternative") in got and ("Neumann", "series") in got and ("Moser", "iteration") in got,
          "K1 wider kinds after a proper name")
    m0 = fresh({"MATHBRAIN_STORE": store, "MATHBRAIN_SUBQ_KINDS2": "0"})
    check(not names(m0, "Apply the Fredholm alternative."), "K1 KINDS2=0 restores the old kinds")
    check(not any(n == "upper" for n, _ in names(m, "an upper bound holds")), "K2 lowercase 'upper bound' is not a name")

    m = fresh({"MATHBRAIN_STORE": store})
    check(m._named_kind_card("What does the Fredholm alternative say about compact operators?")
          and m._named_kind_card("When does the Neumann series converge?")
          and not m._named_kind_card("What does the Riesz alternative say?")
          and not m._named_kind_card("What is the weather today?"), "K3 opens only for a card the store holds")
    check(m._math_gate("What does the Fredholm alternative say about compact operators?") is True,
          "K3 the gate admits it")
    m = fresh({"MATHBRAIN_STORE": store, "MATHBRAIN_GATE_NAMED_KIND": "0"})
    check(not m.GATE_NAMED_KIND and not m._math_gate("What does the Fredholm alternative say about compact operators?"),
          "K4 switch off closes the route")
    m = fresh({"MATHBRAIN_STORE": store})
    check(not m._named_kind_card("What does the MOSER iteration give?"), "K4 an all-caps name never counts")
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
