"""Controls for spread_recall._RunIndex.sync.

 R1 the same list objects a second time: nothing is re-added (identity skip)
 R2 equal but new lists: nothing is re-added either
 R3 a changed text: its key moves to the new words and leaves the old ones
 R4 a dropped key leaves the postings, and hits() answers in key order
 R5 a control: a mutated copy is not skipped (new list objects are compared, not trusted)

    python test_runindex_sync.py      (well under a second; ALL PASS at the end)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import spread_recall as SR

results = []
N_CHECKS = 5


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


try:
    ri = SR._RunIndex()
    adds = []
    real_add = ri._add
    ri._add = lambda k, t: (adds.append(k), real_add(k, t))
    keys = ["a", "b", "c"]
    texts = ["spectral graph bound", "random walk limit", "graph walk mixing"]
    ri.sync(keys, texts)
    first = len(adds)
    ri.sync(keys, texts)
    check(first == 3 and len(adds) == 3, "R1 same objects: no re-add (%d then %d)" % (first, len(adds)))
    ri.sync(list(keys), list(texts))
    check(len(adds) == 3, "R2 equal new lists: no re-add")
    k2, t2 = ["a", "b", "c"], ["spectral graph bound", "random walk limit", "coupling time"]
    ri.sync(k2, t2)
    check(len(adds) == 4 and "c" not in ri.post.get("mixing", set()) and "c" in ri.post["coupling"],
          "R3 a changed text moves its key to the new words")
    k3, t3 = ["a", "b"], ["spectral graph bound", "random walk limit"]
    ri.sync(k3, t3)
    h = ri.hits(["graph", "spectral"], need=2)
    check(all("c" not in s for s in ri.post.values()) and h == [("a", 2)], "R4 a dropped key leaves; hits %s" % h)
    t3b = list(t3)
    t3b[1] = "random graph spectral"
    ri.sync(k3, t3b)
    h = ri.hits(["graph", "spectral"], need=2)
    check(h == [("a", 2), ("b", 2)], "R5 control: a changed copy is compared and applied; hits %s" % h)
except Exception as e:
    check(False, "raised %s: %s" % (type(e).__name__, e))

ok = all(results) and len(results) == N_CHECKS
print("ALL PASS" if ok else "FAILED %d of %d" % (N_CHECKS - results.count(True), N_CHECKS))
sys.exit(0 if ok else 1)
