#!/usr/bin/env python3
"""eval_pmi_edges.py - does PMI-reweighting the co-read edges lift the walk's reach?

The named, still-open failure mode of spread_recall is attractor-pull toward high-connectivity
"gist" hubs (two blind judges, 07-08). A node-degree penalty on the WALK was tried and REJECTED
(made precision worse, 45%->33%, spread_recall.py comment) - because a hub and a tangential
neighbour look identical by node degree. This tests the SAME intuition at the EDGE level, where it
belongs and is unmeasured: reweight each co-read edge by its pointwise mutual information, so an
edge between two rarely-read memories that nonetheless co-occur (a real, specific association)
outweighs an edge to a memory that co-occurs with everything (a hub, high marginal). PMI is the
collaborative-filtering standard for exactly this "popular item dominates" problem.

Causal, recommender-style, and copied from eval_walk_reach's method so the verdict is comparable:
split the trace at 60/70/80th pct; build the co-read graph from PRE-split events only; wiki-links
are as-of-now on every arm (identical leak, cancels in the A-vs-B delta). For each post-split
session (2..25 reads) seed the walk on the first read and score whether the walk's top-K reached
the rest (hit@10/@25, MRR). The live walk (spread_recall._spread) is the propagation on every arm;
only the co-read EDGE WEIGHTS differ.

Fair-test guard: each arm's co-read weights are rescaled to the SAME total mass as the raw-count
baseline, so the only thing that varies is the DISTRIBUTION of weight across edges (the reshaping),
not a global scale that would just change how much wiki dominates. Edge counts are printed too, since
PPMI drops sub-independence edges (that is part of what it does).

Arms:  RAW (baseline, = build_spread_edges today)  |  PPMI (drop pmi<=0)  |  NPMI01 (keep all, graded)
Ship only on a CLEAN multi-split win (all splits positive, no sign flip) - the eval_walk_reach bar.
Read-only. Usage: python eval_pmi_edges.py

VERDICT 2026-07-10 (measured, 613-event trace, 3 splits): NOT SHIPPED. PPMI d10 = -0.006/+0.008/+0.000
(SIGN FLIP); NPMI01 d10 = +0.000/+0.008/+0.000 (flat), and hit@25 mostly went NEGATIVE on both. Why it
adds nothing: the live walk already fan-out-normalizes every step (spread_recall._spread: flow = a*w/deg),
which divides a hub's outflow by its large degree - so the walk ALREADY damps hubs at the source, and an
edge-level PMI reweight is largely redundant with that, while PPMI's dropping of sub-independence edges
slightly cuts reach. The node-degree penalty was rejected for the wrong-level reason (07-08); this shows
the RIGHT level was already handled by the transition normalization. Corollary for the next lever: the
open value is in RANKING the reached candidates (eval_recall_signals: embedding blend +0.11 over graph),
not in reshaping topology - the walk's reach is not the bottleneck. Filed so nobody re-runs this dead end.
Scope caveat (steelman): this rejects PMI for REACH. The judges' hub complaint was about the PRECISION of
what FIRES (which single candidate wins the margin gate), which stays unlabelable on the young graph - the
same ceiling that halted the fire-tuning. So PMI-for-reach is out; PMI-for-fire-precision is untested, not
disproven - but reach going slightly NEGATIVE argues it is not silently helping there either.
"""
import io, itertools, math, os, re, sys
from collections import defaultdict

import numpy as np

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
sys.path.insert(0, DR)
import spread_recall as sr

LINK_RE = re.compile(r"\[\[([^\]]+?)\]\]")
DATE_TAIL = re.compile(r"_\d{4}_\d{2}_\d{2}$")
SKIP = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS", "AUTOBIOGRAPHY",
        "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS", "MISTAKES", "CONSCIENCE", "CURIOSITY"}
W_COACT, W_WIKI = 1.6, 1.0
MAXSESS = 25


def norm(slug):
    s = slug.strip()
    if s.endswith(".md"):
        s = s[:-3]
    s = s.replace("-", "_").lower()
    return DATE_TAIL.sub("", s)


def load_events():
    ev = []
    with io.open(os.path.join(DR, "retrievals.tsv"), encoding="utf-8", errors="replace") as f:
        for i, ln in enumerate(f):
            if i == 0:
                continue
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 4:
                try:
                    ev.append((float(p[1]), p[0], p[3]))
                except Exception:
                    pass
    ev.sort()
    return ev


def wiki_edges(real):
    edges = defaultdict(float)
    for fn in os.listdir(MEM):
        if not fn.endswith(".md") or fn[:-3] in SKIP:
            continue
        src = norm(fn)
        if src not in real:
            continue
        try:
            text = io.open(os.path.join(MEM, fn), encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for m in LINK_RE.findall(text):
            b = norm(m)
            if b in real and b != src:
                edges[tuple(sorted((src, b)))] += W_WIKI
    return edges


def coread_counts(pre):
    by = defaultdict(set)
    for ep, slug, sess in pre:
        by[sess].add(slug)
    cnt = defaultdict(int)
    for slugs in by.values():
        if 2 <= len(slugs) <= MAXSESS:
            for a, b in itertools.combinations(sorted(slugs), 2):
                cnt[(a, b)] += 1
    return cnt


def reweight(cnt, mode):
    """Return {pair: weight}. RAW = count; PPMI = max(0, pmi); NPMI01 = (npmi+1)/2 (all edges kept).
    Each non-raw arm is rescaled so its total mass equals the raw arm's - isolate reshaping, not scale."""
    if not cnt:
        return {}
    if mode == "RAW":
        return {k: W_COACT * v for k, v in cnt.items()}
    T = float(sum(cnt.values()))
    marg = defaultdict(float)
    for (a, b), v in cnt.items():
        marg[a] += v
        marg[b] += v
    w = {}
    for (a, b), v in cnt.items():
        p_ab = v / T
        p_a, p_b = marg[a] / T, marg[b] / T
        pmi = math.log(p_ab / (p_a * p_b)) if p_a > 0 and p_b > 0 and p_ab > 0 else 0.0
        if mode == "PPMI":
            val = max(0.0, pmi)
        elif mode == "NPMI01":
            npmi = pmi / (-math.log(p_ab)) if p_ab > 0 and p_ab < 1 else 0.0
            val = (npmi + 1.0) / 2.0   # [-1,1] -> [0,1], keeps every edge graded
        else:
            val = float(v)
        if val > 0:
            w[(a, b)] = val
    # rescale to the raw arm's total mass so wiki:coread balance is identical across arms
    raw_total = W_COACT * T
    s = sum(w.values())
    if s > 0:
        f = raw_total / s
        w = {k: v * f for k, v in w.items()}
    return w


def to_adj(edge_dicts):
    tot = defaultdict(float)
    for ed in edge_dicts:
        for (a, b), wt in ed.items():
            tot[(a, b)] += wt
    adj = defaultdict(list)
    deg = defaultdict(float)
    for (a, b), wt in tot.items():
        adj[a].append((b, wt))
        adj[b].append((a, wt))
        deg[a] += wt
        deg[b] += wt
    return adj, deg, len(tot)


def evaluate(adj, deg, sessions):
    h10 = h25 = mrr = n = 0
    for seed, targets in sessions:
        act, _ = sr._spread({seed: 2.2}, adj, deg)
        ranked = [s for s, _ in sorted(act.items(), key=lambda kv: -kv[1]) if s != seed]
        pos = {s: i for i, s in enumerate(ranked)}
        for t in targets:
            n += 1
            i = pos.get(t)
            if i is None:
                continue
            if i < 10:
                h10 += 1
            if i < 25:
                h25 += 1
            mrr += 1.0 / (i + 1)
    if n == 0:
        return (0.0, 0.0, 0.0, 0)
    return (h10 / n, h25 / n, mrr / n, n)


def main():
    real = {norm(fn) for fn in os.listdir(MEM) if fn.endswith(".md") and fn[:-3] not in SKIP}
    events = [(ep, norm(s), sess) for ep, s, sess in load_events() if norm(s) in real]
    if len(events) < 40:
        print(f"eval_pmi_edges: trace too thin ({len(events)} events).")
        return
    wiki = wiki_edges(real)
    arms = ["RAW", "PPMI", "NPMI01"]
    print(f"eval_pmi_edges: {len(events)} in-corpus events, {len(wiki)} wiki edges, walk=spread_recall._spread "
          f"(HOPS={sr.HOPS}, DECAY={sr.DECAY})")
    wins = defaultdict(list)
    for split in (0.60, 0.70, 0.80):
        T = events[int(split * len(events))][0]
        pre = [e for e in events if e[0] < T]
        post = [e for e in events if e[0] >= T]
        cnt = coread_counts(pre)
        pby = defaultdict(list)
        for ep, slug, sess in post:
            if slug not in pby[sess]:
                pby[sess].append(slug)
        sessions = [(v[0], v[1:]) for v in pby.values() if 2 <= len(v) <= MAXSESS]
        if not sessions:
            print(f"split {split:.2f}: no post-split sessions")
            continue
        base = None
        for mode in arms:
            cw = reweight(cnt, mode)
            adj, deg, ne = to_adj([wiki, cw])
            h10, h25, mr, nt = evaluate(adj, deg, sessions)
            if mode == "RAW":
                base = (h10, h25, mr)
                print(f"split {split:.2f}  RAW    edges={ne:5d}  hit@10 {h10:.3f}  hit@25 {h25:.3f}  MRR {mr:.3f}  "
                      f"({nt} targets, {len(sessions)} sess)")
            else:
                d10, d25, dm = h10 - base[0], h25 - base[1], mr - base[2]
                wins[mode].append(d10)
                print(f"           {mode:6s} edges={ne:5d}  hit@10 {h10:.3f}  hit@25 {h25:.3f}  MRR {mr:.3f}   "
                      f"d10 {d10:+.3f} d25 {d25:+.3f} dMRR {dm:+.3f}")
    print("\nverdict (clean multi-split win = all 3 splits d10>0, no sign flip):")
    for mode in arms[1:]:
        ds = wins[mode]
        clean = len(ds) == 3 and all(d > 0 for d in ds)
        flip = any(d > 0 for d in ds) and any(d < 0 for d in ds)
        print(f"  {mode:6s} d10 per split = {['%+.3f' % d for d in ds]}  -> "
              f"{'CLEAN WIN - candidate to ship' if clean else ('SIGN FLIP - reject' if flip else 'no clean win - hold')}")
    print("  NOTE: 613-event trace is young; treat any win as PROVISIONAL and re-run as the nightly trace grows.")


if __name__ == "__main__":
    main()
