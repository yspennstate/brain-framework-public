"""Recall's display floor skips a low row; it does not end the list (28 Sep 2026, claude-825b1305; scheduled audit
account01/B02 R1, the 0.02 display cutoff).

On the bm25-first path recall's list runs in segment order - the BM25 block (floored at 0.05), then the lexical, graph
and semantic tails - not by display score. The display loop used to end at the first row scoring 0.02 or less.
 A1 a tail row at or below the floor that precedes a row above it: the later row is shown
 A2 at most n rows, in the order given
 A3 rows at or below the floor are never shown
 A4 with floor_break=True (BRAIN_RECALL_FLOOR_BREAK=1) the old stop returns, and fails A1 (the check can fail)
 A5 the rows before the first low row are the same under both rules, and the new rule only adds rows after them
 A6 recall() itself uses _display_rows (the function under test is the one that runs)

    python test_recall_display_floor.py [BRAIN.py]
"""
import importlib.machinery
import importlib.util
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "brain.py")
sys.path.insert(0, HERE)
spec = importlib.util.spec_from_file_location("brain_under_test", PATH,
                                              loader=importlib.machinery.SourceFileLoader("brain_under_test", PATH))
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)
fails, ran = [], []


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def row(sc, slug):
    return (sc, 0.0, 0.0, 0.0, slug, "legacy")


# the bm25 block (>= 0.05), a lexical tail ending in a weak row, then a graph tail above the floor
scored = [row(1.0, "b1"), row(0.4, "b2"), row(0.05, "b3"), row(0.3, "l1"), row(0.01, "l2"), row(0.2, "g1"), row(0.15, "g2")]
new = [r[4] for r in B._display_rows(scored, 8)]
old = [r[4] for r in B._display_rows(scored, 8, floor_break=True)]
check(new == ["b1", "b2", "b3", "l1", "g1", "g2"], "A1 the graph-tail rows after a weak lexical row are shown: %s" % new)
check([r[4] for r in B._display_rows(scored, 3)] == ["b1", "b2", "b3"], "A2 at most n rows, in the order given")
check("l2" not in new and all(r[0] > 0.02 for r in B._display_rows(scored, 8)), "A3 no row at or below the floor is shown")
check(old == ["b1", "b2", "b3", "l1"], "A4 floor_break=True stops at the weak row, hiding g1 and g2: %s" % old)
rng = random.Random(7)
ok = True
for _ in range(2000):
    lst = [row(round(rng.choice([rng.random(), rng.random() * 0.04]), 4), "s%d" % i) for i in range(rng.randint(0, 30))]
    n = rng.randint(1, 12)
    a = [r[4] for r in B._display_rows(lst, n, floor_break=True)]
    b = [r[4] for r in B._display_rows(lst, n)]
    ok &= b[:len(a)] == a and len(b) >= len(a)
check(ok, "A5 on 2,000 random lists the new rule keeps the old rows as its prefix and only adds after them")
src = open(PATH, encoding="utf-8").read()
check("in _display_rows(scored, n, floor_break=_floor_break)" in src, "A6 recall() reads its rows through _display_rows")
print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
sys.exit(1 if fails else 0)
