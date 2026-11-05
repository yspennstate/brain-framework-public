"""graph_recall.py - the memories a question points to, read from the graph grown from use (26 Sep).

Written for the recall banks (the recall-bank harness scores any ranker that turns a question into an ordered list of
memory slugs). A question has no session, so only the word arms of the study apply: each word of the question becomes
the node the capture makes for it ("said:<w>", a word an agent wrote; with --kinds said,kw also "kw:<w>", a word of his
prompts), and every learned edge from those words to a memory adds

    weight(w) x PPMI(w -> memory) x log(1 + count),      weight(w) = 1 / log(2 + number of memories w leads to)

(the study's one-hop graph with each word weighted by how specific it is). Variants:
  graph   memories by that score; memories it does not reach follow by node size
  fused   reciprocal-rank fusion (k = 60) of the graph score and node size, the study's fused arm without session arms
  size    node size alone, the same list for every question (the floor any variant has to beat)

The store should come from events that exclude the sessions that built or graded the banks
(exclude_bank_sessions.py), or a question the grader wrote out could lead straight to its answer.
Usage:
  graph_recall.py --store STORE --q "question text" [--k 20] [--variant graph|fused|size] [--kinds said]
  graph_recall.py --store STORE --batch IN.jsonl --out OUT.jsonl     IN rows {"id", "q"}; OUT rows {"id", "ranked": [slug]}
"""
import argparse
import json
import math
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from graph_store import GraphStore  # noqa: E402

STOP = set("""the a an and or of to in on for with at by from is are was were be been it its this that these those i you
he she we they me my your our their his her them us do does did done not no yes if then else so as but can will would
should could have has had just also any all some more most less very into out up down over under again about after before
when where what which who whom why how than too only own same other such both each few here there please make sure need
want use using used get got let lets like know think see look go going ok okay thanks now one two new still next first
last time way work working file files line lines run running check""".split())
WORD = re.compile(r"[a-z][a-z0-9_]{3,}")


def words(text):
    seen, out = set(), []
    for w in WORD.findall((text or "").lower()):
        if w not in STOP and not w.isdigit() and w not in seen:
            seen.add(w)
            out.append(w)
    return out


class Recall:
    def __init__(self, store, kinds=("said",), min_count=1, cds=1.0):
        """min_count: a word -> memory edge seen fewer times is ignored (Church and Hanks drop pairs seen five times or
        fewer); cds: P(memory) is taken from in-weights raised to this power and renormalised, the context-distribution
        smoothing of Levy, Goldberg and Dagan (0.75), which takes PMI's reward away from rare memories."""
        self.st = GraphStore(store)
        self.kinds = kinds
        self.min_count, self.cds = min_count, cds
        iw = np.asarray(self.st.in_w, dtype=np.float64)
        self.p_in = (iw ** cds) / float((iw ** cds).sum()) if cds != 1.0 else iw / float(self.st.total_w)
        names = self.st.names
        self.mem_ix = np.array([i for i, n in enumerate(names) if n.startswith("mem:")], dtype=np.int64)
        self.is_mem = np.zeros(len(names), dtype=bool)
        self.is_mem[self.mem_ix] = True
        self.pos = {int(x): p for p, x in enumerate(self.mem_ix.tolist())}
        # node size of every memory at the store's last use (ACT-R base level, d 0.5, hours; the older uses a store
        # folds into a count are taken as spread evenly between the first and the oldest kept use)
        now = max((max(u[0]) for u in self.st.uses.values() if u[0]), default=0.0)
        size = np.full(len(self.mem_ix), -1e9)
        for p, x in enumerate(self.mem_ix.tolist()):
            u = self.st.uses.get(x)
            if not u:
                continue
            last, old_n, old_first = u
            s = sum(max((now - t) / 3600.0, 1.0 / 60.0) ** -0.5 for t in last)
            if old_n and old_first and last:
                span = max((min(last) - old_first) / 3600.0, 1.0 / 60.0)
                age = max((now - min(last)) / 3600.0, 1.0 / 60.0)
                s += old_n * ((age + span) ** 0.5 - age ** 0.5) / (0.5 * span)
            size[p] = math.log(s) if s > 0 else -1e9
        self.size = size
        self.size_order = np.argsort(-size, kind="stable")

    def graph_scores(self, q):
        st = self.st
        sc = np.zeros(len(self.mem_ix))
        used = []
        for w in words(q):
            for kind in self.kinds:
                j = st.id.get("%s:%s" % (kind, w))
                if j is None:
                    continue
                lo, hi = st._slice(st.keys, j)
                xs = (st.keys[lo:hi] & 0xFFFFFFFF).astype(np.int64)
                m = self.is_mem[xs] & (st.cnt[lo:hi] >= self.min_count)
                if not m.any():
                    continue
                xs, c = xs[m], st.cnt[lo:hi][m]
                pmi = np.log((c / st.out_sum[j]) / self.p_in[xs])
                keep = pmi > 0
                if not keep.any():
                    continue
                xs, c, pmi = xs[keep], c[keep], pmi[keep]
                wt = 1.0 / math.log(2.0 + len(xs))
                p = np.array([self.pos[int(x)] for x in xs.tolist()])
                np.add.at(sc, p, wt * pmi * np.log1p(c))
                used.append("%s:%s" % (kind, w))
        return sc, used

    def rank(self, q, k=20, variant="graph"):
        if variant == "size":
            order = self.size_order
        else:
            sc, _used = self.graph_scores(q)
            if variant == "graph":
                order = np.lexsort((-self.size, -sc))
            else:
                fu = np.zeros(len(sc))
                g = np.flatnonzero(sc > 0)
                g = g[np.argsort(-sc[g], kind="stable")][:200]
                fu[g] += 1.0 / (61.0 + np.arange(len(g)))
                s = self.size_order[:200]
                fu[s] += 1.0 / (61.0 + np.arange(len(s)))
                order = np.lexsort((-self.size, -fu))
        return [self.st.names[int(self.mem_ix[p])][4:] for p in order[:k]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--q")
    ap.add_argument("--batch")
    ap.add_argument("--out")
    ap.add_argument("--k", type=int, default=20)
    ap.add_argument("--variant", default="graph", choices=("graph", "fused", "size"))
    ap.add_argument("--kinds", default="said")
    ap.add_argument("--min-count", type=int, default=1)
    ap.add_argument("--cds", type=float, default=1.0)
    a = ap.parse_args()
    r = Recall(a.store, tuple(a.kinds.split(",")), min_count=a.min_count, cds=a.cds)
    if a.q:
        sc, used = r.graph_scores(a.q)
        print("word nodes found: %s" % used)
        for s in r.rank(a.q, a.k, a.variant):
            print(s)
        return
    rows = [json.loads(ln) for ln in open(a.batch, encoding="utf-8") if ln.strip()]
    with open(a.out, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps({"id": row.get("id"), "ranked": r.rank(row.get("q") or "", a.k, a.variant)}) + "\n")
    print("ranked %d questions -> %s" % (len(rows), a.out))


if __name__ == "__main__":
    main()
