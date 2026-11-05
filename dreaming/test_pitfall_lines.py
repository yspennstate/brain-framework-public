"""Controls for the math hook's record lines: a served card names its pitfall, a corrected premise, an answered problem.

 P1 a DEPENDS_ON edge marked premise_corrected gives "corrected since"; a FAILS_UNDER edge gives "on record"
 P2 a DEPENDS_ON edge into an open problem that a card REFUTES gives "answered since"; into a theorem it gives nothing
 P3 a retracted edge and a superseded target are never reported
 P4 a line is added only while every card still fits, at most two per block, one record shown once per block
 P5 MATHBRAIN_PITFALL_LINES=0 is honoured by the caller and MATHBRAIN_CORRECTION_LINES=0 keeps only the pitfalls

    python test_pitfall_lines.py      (under a second; ALL PASS at the end)
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
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + what)


def build_store(path):
    con = sqlite3.connect(path)
    con.executescript("""
        create table nodes (id text primary key, title text);
        create table edges (id text primary key, kind text, source_id text, target_id text, metadata_json text);
        create table edge_retractions (edge_id text primary key, reason text);
    """)
    nodes = [("a", "Result A"), ("b", "Result B"), ("c", "Result C"), ("pa", "Premise of A, corrected statement"),
             ("op", "Open problem: is every widget blue"), ("ans", "Every widget is blue"),
             ("th", "A true theorem"), ("scar", "A scar that refutes the theorem"),
             ("pit", "The bound needs bounded variance"), ("pit2", "Old pitfall"), ("pit3", "Retracted pitfall"),
             ("new", "Replacement of the old pitfall")]
    con.executemany("insert into nodes values (?, ?)", nodes)
    edges = [("e1", "depends_on", "a", "pa", '{"premise_corrected": true}'),
             ("e2", "fails_under", "a", "pit", "{}"),
             ("e3", "depends_on", "b", "op", "{}"),
             ("e4", "refutes", "ans", "op", "{}"),
             ("e5", "depends_on", "b", "th", "{}"),
             ("e6", "refutes", "scar", "th", "{}"),
             ("e7", "fails_under", "c", "pit2", "{}"),
             ("e8", "supersedes", "new", "pit2", "{}"),
             ("e9", "fails_under", "c", "pit3", "{}"),
             ("e10", "fails_under", "b", "pit", "{}")]
    con.executemany("insert into edges values (?, ?, ?, ?, ?)", edges)
    con.execute("insert into edge_retractions values ('e9', 'wrong')")
    con.commit()
    con.close()


def fresh(env):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    sys.modules.pop("math_sense", None)
    return importlib.import_module("math_sense")


def main():
    tmp = tempfile.mkdtemp()
    store = os.path.join(tmp, "brain.sqlite3")
    build_store(store)
    m = fresh({"MATHBRAIN_STORE": store})
    got = m._pitfalls_for(["a", "b", "c"])
    a = sorted(lab for lab, _, _ in got.get("a", []))
    check(a == ["corrected since", "on record"], "P1 card A: %r" % a)
    b = sorted((lab, pid) for lab, pid, _ in got.get("b", []))
    check(("answered since", "ans") in b and not any(pid == "scar" for _, pid in b), "P2 card B: %r" % b)
    check("c" not in got, "P3 retracted and superseded records dropped: %r" % got.get("c"))

    pieces = [("a", "[1] card A " + "x" * 60), ("b", "[2] card B " + "y" * 60), ("c", "[3] card C " + "z" * 60)]
    header = "HEADER"
    roomy, ids = m._with_pitfall_lines(header, pieces, ["a", "b", "c"], 2000, "corrected premise")
    n_lines = roomy.count("since:") + roomy.count("on record:")
    tight_budget = len(m._assemble_note_block(header, pieces, 2000)[0])
    tight, ids_t = m._with_pitfall_lines(header, pieces, ["a", "b", "c"], tight_budget, "corrected premise")
    check(ids == ["a", "b", "c"] and n_lines == 2 and "corrected since: Premise of A" in roomy
          and ids_t == ["a", "b", "c"] and "since:" not in tight,
          "P4 roomy block %d lines, ids %r; tight block keeps the cards and adds none" % (n_lines, ids))
    # two cards tied to one pitfall: the second card does not repeat it
    p2 = [("a", "[1] card A"), ("b", "[2] card B")]
    os.environ["MATHBRAIN_CORRECTION_LINES"] = "0"
    sys.modules.pop("math_sense", None)
    m2 = importlib.import_module("math_sense")
    dd, _ = m2._with_pitfall_lines(header, p2, ["a", "b"], 2000, "")
    check(dd.count("The bound needs bounded variance") == 1, "P4 one record shown once per block: %r" % dd)

    m = fresh({"MATHBRAIN_STORE": store, "MATHBRAIN_PITFALL_LINES": "0", "MATHBRAIN_CORRECTION_LINES": "0"})
    got = m._pitfalls_for(["a", "b"])
    labs = sorted(set(lab for v in got.values() for lab, _, _ in v))
    check(m.PITFALL_LINES is False and labs == ["on record"], "P5 switches: PITFALL_LINES %s, labels %r"
          % (m.PITFALL_LINES, labs))
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
