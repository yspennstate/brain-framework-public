"""context_rank.py - what the graph grown from use would bring in at one moment of a session.

Four arms of the walk-forward study (study/eval_nightly.py) for ONE context, read from a built store, so another test
(the judged-relevance lane, a shadow membrane, a hook) can score a moment with them. They follow the store's
definitions, which the membrane shares; the study computed P(candidate) from target uses rather than incoming weight,
so its hebb and actr numbers are close to these, not identical:

  size    node size: ACT-R base level at time t, ln sum over the node's uses before t of age^-0.5 (hours)
  hebb    one learned hop: sum over context items of w(item) x PPMI(item -> candidate) x ln(1 + count)
  actr    size + the mean over context items of max(0, ln P(candidate | item) / P(candidate))
  fused   reciprocal-rank fusion (k = 60, top 200 of each) of size and hebb

P(candidate | item) is the count over the item's whole outgoing weight and P(candidate) the candidate's share of all
incoming weight, the store's own sums, so a capped row still gives the right association.

NO FUTURE: a store holds every event before its `until` (graph_store.build(events, out, until=D)); edges cannot be cut by
time afterwards, so score a moment at t only with a store whose until <= t. score() refuses otherwise. Uses after t are
never counted in node size. For a set of moments spread over days, build one store per day (until = that day's start,
about four minutes each here) or one store with until = the earliest moment (leak-free, staler for later moments).

    from context_rank import ContextRanker
    cr = ContextRanker(STORE)                        # loads in a few seconds
    cr.rank(["file:~/x.py", "tool:Bash:ssh", ...], t, kind="mem", arm="fused", k=40)   # -> [(name, score), ...]
    cr.score(items, t, kind="mem")                   # -> {"size": vec, "hebb": vec, "actr": vec, "fused": vec}, cr.cands["mem"]
Items are node names as the capture writes them ("kind:id"); newest first if `ages` is not given (the study weights
each item 0.5 ** (age / 150 s) - pass ages in seconds to reproduce it). Hub items have no rows in the store and add
nothing, as in the study.

    python context_rank.py --store STORE --t EPOCH --kind mem --item file:~/a.py --item tool:Bash:ssh [--arm fused]
"""
import argparse
import collections
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from graph_store import GraphStore  # noqa: E402

NEG = -1e30
ARMS = ("size", "hebb", "actr", "fused")


class ContextRanker:
    def __init__(self, store, row_cap=300):
        self.st = GraphStore(store) if isinstance(store, str) else store
        self.row_cap = row_cap
        self.until = float(self.st.meta.get("until") or 0.0)
        by_kind = collections.defaultdict(list)
        for i, n in enumerate(self.st.names):
            by_kind[n.split(":", 1)[0]].append(i)
        self.cands = {k: np.array(v, dtype=np.int64) for k, v in by_kind.items()}
        self.kind_of = np.zeros(len(self.st.names), dtype=np.int64)
        self.kinds = sorted(by_kind)
        for ki, k in enumerate(self.kinds):
            self.kind_of[self.cands[k]] = ki
        self._rows = {}

    def _row(self, j):
        r = self._rows.get(j)
        if r is None:
            st = self.st
            xs, w = st.rows(j, self.row_cap)
            if len(xs) and st.out_sum[j] > 0:
                pmi = np.log((w / st.out_sum[j]) / np.maximum(st.in_w[xs] / st.total_w, 1e-300))
                keep = pmi > 0
                r = (xs[keep], pmi[keep], np.log1p(w[keep]))
            else:
                r = (xs[:0], np.zeros(0), np.zeros(0))
            self._rows[j] = r
        return r

    def size(self, kind, t):
        """ACT-R base level of every candidate of `kind` at time t (NEG for a node with no use before t)."""
        c = self.cands.get(kind, np.zeros(0, np.int64))
        out = np.full(len(c), NEG)
        for p, x in enumerate(c.tolist()):
            u = self.st.uses.get(x)
            if not u:
                continue
            last, old_n, old_first = u
            s = sum(max((t - ts) / 3600.0, 1.0 / 60.0) ** -0.5 for ts in last if ts < t)
            if old_n and old_first and last and old_first < t:
                span = max((min(last) - old_first) / 3600.0, 1.0 / 60.0)
                age = max((t - min(last)) / 3600.0, 1.0 / 60.0)
                s += old_n * ((age + span) ** 0.5 - age ** 0.5) / (0.5 * span)
            if s > 0:
                out[p] = math.log(s)
        return out

    def score(self, items, t, kind="mem", ages=None, half_life=150.0):
        if self.until and t < self.until - 1.0:
            raise ValueError("store holds events up to %.0f, after the moment %.0f: it would read the future" % (
                self.until, t))
        c = self.cands.get(kind, np.zeros(0, np.int64))
        nc = len(c)
        k_i = self.kinds.index(kind) if kind in self.kinds else -1
        ids = [self.st.id[x] for x in dict.fromkeys(items) if x in self.st.id]
        if ages is None:
            wts = [1.0] * len(ids)
        else:
            age_of = dict(zip(items, ages))
            wts = [0.5 ** (max(0.0, age_of.get(self.st.names[j], 0.0)) / half_life) for j in ids]
        h1 = np.zeros(nc)
        pm_sum = np.zeros(nc)
        for j, wgt in zip(ids, wts):
            xs, pmi, lc = self._row(j)
            m = self.kind_of[xs] == k_i
            if not m.any():
                continue
            p = np.minimum(np.searchsorted(c, xs[m]), nc - 1)
            ok = c[p] == xs[m]
            np.add.at(h1, p[ok], (pmi[m] * lc[m])[ok] * wgt)
            np.add.at(pm_sum, p[ok], pmi[m][ok] / max(1, len(ids)))
        size = self.size(kind, t)
        sc = {"size": size, "hebb": np.where(h1 > 0, h1, NEG)}
        sc["actr"] = np.where((size > NEG / 2) | (pm_sum > 0), np.where(size > NEG / 2, size, -5.0) + pm_sum, NEG)
        fu = np.zeros(nc)
        for arm in ("size", "hebb"):
            v = sc[arm]
            good = np.flatnonzero(v > NEG / 2)
            if len(good):
                top = good[np.argsort(-v[good], kind="stable")[:200]]
                fu[top] += 1.0 / (61.0 + np.arange(len(top)))
        sc["fused"] = np.where(fu > 0, fu, NEG)
        return sc

    def rank(self, items, t, kind="mem", arm="fused", k=40, ages=None, exclude=()):
        v = self.score(items, t, kind, ages)[arm]
        c = self.cands.get(kind, np.zeros(0, np.int64))
        ex = set(exclude)
        out = []
        for p in np.argsort(-v, kind="stable").tolist():
            if v[p] <= NEG / 2:
                break
            nm = self.st.names[int(c[p])]
            if nm in ex:
                continue
            out.append((nm, float(v[p])))
            if len(out) == k:
                break
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--t", type=float, required=True)
    ap.add_argument("--kind", default="mem")
    ap.add_argument("--item", action="append", default=[])
    ap.add_argument("--arm", default="fused", choices=ARMS)
    ap.add_argument("--k", type=int, default=10)
    a = ap.parse_args()
    cr = ContextRanker(a.store)
    for nm, s in cr.rank(a.item, a.t, a.kind, a.arm, a.k):
        print("%.4f  %s" % (s, nm))


if __name__ == "__main__":
    main()
