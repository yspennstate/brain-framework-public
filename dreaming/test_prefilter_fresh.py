"""Controls for the prefilter's fresh-node lane (nodes filed after the index was built).

 F1 a node added after the build is found by select() and ranked with the index's statistics (words the index does
    not carry score nothing, so it may rank below an indexed card); LAST_FRESH counts it
 F2 an indexed node is never scored twice: fresh rows already in the index are skipped
 F3 the scan is skipped when the store has not been written since the index file (no rows, no query)
 F4 MATHBRAIN_PREFILTER_FRESH=0 restores the snapshot-only ranking
 F5 an index with no built_at stamp, or a store that cannot be opened, gives no fresh rows and no error
 F6 MATHBRAIN_PREFILTER_FRESH_MAX bounds the rows, newest first

    python test_prefilter_fresh.py      (a few seconds; ALL PASS at the end)
"""
import importlib
import os
import sys
import tempfile
import time

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


def fresh_module(env):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ["MATHBRAIN_EXPANSION"] = "0"
    os.environ.update(env)
    sys.modules.pop("math_prefilter", None)
    return importlib.import_module("math_prefilter")


def add(path, nid, title, body):
    from math_brain.models import GraphNode, NodeKind
    from math_brain.store import ResearchStore
    with ResearchStore(path) as store:
        store.add_node(GraphNode(nid, NodeKind.CLAIM, title, body))
        store.connection.commit()   # the store closes without committing


def built_store(env):
    d = tempfile.mkdtemp()
    path = os.path.join(d, "store.sqlite3")
    add(path, "card-a", "Hall marriage theorem", "a bipartite graph has a perfect matching")
    add(path, "card-b", "Konig theorem", "maximum matching equals minimum vertex cover in bipartite graphs")
    add(path, "card-c", "Menger theorem", "vertex connectivity equals the number of disjoint paths")
    m = fresh_module(env)
    m.build(path)
    return m, path


def main():
    m, path = built_store({})
    time.sleep(1.1)   # created_at has second resolution; the fresh node must be strictly newer than built_at
    add(path, "card-new", "Tutte theorem", "a graph has a perfect matching iff the Tutte condition holds for every subset")
    ids = m.select(path, "Tutte condition perfect matching")
    check(ids and "card-new" in ids and m.LAST_FRESH == 1 and m.LAST_SCORES.get("card-new", 0) > 0
          and list(m.LAST_SCORES) == ids, "F1 fresh node found and merged %r fresh %d" % (ids, m.LAST_FRESH))

    data = m.load(path)
    rows = [("card-a", "Hall marriage theorem", "a bipartite graph has a perfect matching")]
    check(m._fresh_scores(data, "perfect matching", rows) == {}, "F2 an indexed node is not scored again")

    m3, path3 = built_store({})
    os.utime(m3.index_path(path3), None)
    future = time.time() + 5
    os.utime(m3.index_path(path3), (future, future))
    check(m3._store_changed_since_index(path3) is False and m3.fresh_nodes(path3) == [],
          "F3 an unchanged store skips the scan")

    m4, path4 = built_store({"MATHBRAIN_PREFILTER_FRESH": "0"})
    time.sleep(1.1)
    add(path4, "card-new", "Tutte theorem", "a graph has a perfect matching iff the Tutte condition holds")
    ids4 = m4.select(path4, "Tutte condition perfect matching")
    check("card-new" not in (ids4 or []) and m4.LAST_FRESH == 0, "F4 off: snapshot only %r" % ids4)

    data5 = dict(m.load(path))
    data5.pop("built_at", None)
    missing = os.path.join(tempfile.mkdtemp(), "none.sqlite3")
    check(m.fresh_nodes(path, data5) == [] and m.fresh_nodes(missing) == [], "F5 no stamp or no store: no rows")

    m6, path6 = built_store({"MATHBRAIN_PREFILTER_FRESH_MAX": "2"})
    time.sleep(1.1)
    for i in range(4):
        add(path6, "late-%d" % i, "Late card %d" % i, "matching card number %d" % i)
        time.sleep(1.1)
    rows6 = m6.fresh_nodes(path6)
    check(m6.FRESH_MAX == 2 and [r[0] for r in rows6] == ["late-3", "late-2"], "F6 bounded newest first %r"
          % [r[0] for r in rows6])
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
