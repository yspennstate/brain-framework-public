"""Controls for the math hook's same-result pairs: one card per result on a block, and the kept card names the other.

 S1 a same_result edge ties its two ends both ways; a card carrying the edge's ledger id joins the group
 S2 a block holding both cards keeps one, at the place of the first, and it is the edge's source card
 S3 the kept card's record line names the CURRENT card for the ledger id, not a superseded revision
 S4 a retracted same_result edge ties nothing
 S5 MATHBRAIN_SAME_RESULT=0 leaves the block and the record lines as they were

    python test_same_result.py      (under a second; ALL PASS at the end)
"""
import importlib
import json
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


class Node:
    def __init__(self, nid):
        self.id = nid


class Hit:
    def __init__(self, nid):
        self.node = Node(nid)


def build_store(path, retract=False):
    con = sqlite3.connect(path)
    con.executescript("""
        create table nodes (id text primary key, title text, metadata_json text);
        create table edges (id text primary key, kind text, source_id text, target_id text, metadata_json text);
        create table edge_retractions (edge_id text primary key, reason text);
    """)
    lid = json.dumps({"logical_id": "L1"})
    con.executemany("insert into nodes values (?, ?, ?)", [
        ("mem", "Working-note card for the result", "{}"),
        ("led1", "Ledger card, first version", lid),
        ("led2", "Ledger card, current version", lid),
        ("x", "An unrelated card", "{}")])
    con.executemany("insert into edges values (?, ?, ?, ?, ?)", [
        ("t1", "semantic_association", "mem", "led1", json.dumps({"relation": "same_result", "ledger_logical_id": "L1"})),
        ("t2", "supersedes", "led2", "led1", "{}")])
    if retract:
        con.execute("insert into edge_retractions values ('t1', 'wrong')")
    con.commit()
    con.close()


def fresh(env):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    sys.modules.pop("math_sense", None)
    return importlib.import_module("math_sense")


def ids(hits):
    return [h.node.id for h in hits]


def main():
    tmp = tempfile.mkdtemp()
    store = os.path.join(tmp, "brain.sqlite3")
    build_store(store)
    m = fresh({"MATHBRAIN_STORE": store})
    tw = m._same_result_twins(["mem", "led1", "led2", "x"])
    check(tw.get("mem") == {"led1", "led2"} and "mem" in tw.get("led2", set()) and "x" not in tw,
          "S1 twins: %r" % {k: sorted(v) for k, v in tw.items()})
    kept = ids(m._drop_later_twins([Hit("led1"), Hit("x"), Hit("mem")]))
    check(kept == ["mem", "x"], "S2 block led1, x, mem keeps %r" % kept)
    got = m._pitfalls_for(["mem"]).get("mem", [])
    check(got == [("same result on record", "led2", "Ledger card, current version")], "S3 record line: %r" % got)

    store2 = os.path.join(tmp, "retracted.sqlite3")
    build_store(store2, retract=True)
    m = fresh({"MATHBRAIN_STORE": store2})
    kept = ids(m._drop_later_twins([Hit("led1"), Hit("mem")]))
    check(m._same_result_twins(["mem", "led1"]) == {} and kept == ["led1", "mem"], "S4 retracted edge: kept %r" % kept)

    m = fresh({"MATHBRAIN_STORE": store, "MATHBRAIN_SAME_RESULT": "0"})
    kept = ids(m._drop_later_twins([Hit("led1"), Hit("mem")]))
    check(kept == ["led1", "mem"] and m._pitfalls_for(["mem"]) == {}, "S5 switch off: kept %r" % kept)
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
