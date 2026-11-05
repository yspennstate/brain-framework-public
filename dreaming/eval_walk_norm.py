#!/usr/bin/env python3
"""eval_walk_norm.py - does a gentler intermediate-normalization lift the WALK's reach?

eval_linkpred found Adamic-Adar cleanly (if modestly) beats the live walk at ranking future co-read
PAIRS. The mechanism: the walk divides an intermediate's outflow by its total edge WEIGHT (linear,
spread_recall._spread: flow = a*w/deg), while AA down-weights a shared bridge by 1/log(#neighbours) -
gentler on promiscuous hubs. This tests whether that insight translates to the other axis, REACH:
seed the walk on a session's first read, does its top-K contain the rest of the session?

It A/Bs the intermediate normalizer norm(z) in flow = a * w(z,nb)/norm(z) * DECAY, over a small,
a-priori set (no hyperparameter fishing on a young graph - the non-stationary trap):
  linear   norm=deg_w(z)            (= the LIVE walk, baseline)
  sqrt     norm=deg_w(z)**0.5       (partial normalization)
  logw     norm=log(e+deg_w(z))     (log of weighted degree)
  logc     norm=log(e+deg_c(z))     (log of NEIGHBOUR COUNT - the closest analogue to Adamic-Adar)

Causal, eval_walk_reach protocol (60/70/80 splits, co-read graph pre-split, wiki as-of-now on all
arms). Ship the change to spread_recall._spread ONLY on a clean multi-split reach win that also holds
in eval_linkpred - two axes, or it's a snapshot artifact. Read-only. Usage: python eval_walk_norm.py

VERDICT 2026-07-10 (measured, 613-event trace, 3 splits): NOT SHIPPED - the axes DISAGREE. sqrt and logw
SIGN-FLIP on hit@10; logc flat; and every gentler normalizer loses hit@25 hard (-0.03 to -0.08 across
splits). So Adamic-Adar's log down-weight, which won by +0.01 on isolated PAIR ranking (eval_linkpred),
HURTS the walk's REACH: relaxing the linear /deg lets promiscuous hubs flood the 2-hop frontier and push
the session's real targets out of the top-25. The walk's current linear degree-normalization is doing
load-bearing work for reach and is correctly chosen. The lesson: a pair-discrimination win is NOT a walk
win - reach and pair-AUROC are different objectives and here they point opposite ways. Full story +
numbers: memory brain_graph_linkpred_2026_07_10. Filed so nobody re-tunes the normalization on a snapshot.
"""
import io, itertools, math, os, re, sys
from collections import defaultdict

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")

LINK_RE = re.compile(r"\[\[([^\]]+?)\]\]")
DATE_TAIL = re.compile(r"_\d{4}_\d{2}_\d{2}$")
SKIP = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS", "AUTOBIOGRAPHY",
        "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS", "MISTAKES", "CONSCIENCE", "CURIOSITY"}
W_COACT, W_WIKI = 1.6, 1.0
MAXSESS = 25
HOPS, DECAY = 2, 0.5    # match spread_recall


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


def build(wiki, cnt):
    w = defaultdict(float)
    for k, v in wiki.items():
        w[k] += v
    for k, v in cnt.items():
        w[k] += W_COACT * v
    adj = defaultdict(list)
    degw = defaultdict(float)
    degc = defaultdict(int)
    for (a, b), wt in w.items():
        adj[a].append((b, wt)); adj[b].append((a, wt))
        degw[a] += wt; degw[b] += wt
        degc[a] += 1; degc[b] += 1
    return adj, degw, degc


def spread(seeds, adj, degw, degc, mode):
    act = defaultdict(float)
    for s, a0 in seeds.items():
        act[s] += a0
    frontier = dict(seeds)
    for _ in range(HOPS):
        nxt = defaultdict(float)
        for src, a in frontier.items():
            if mode == "linear":
                nz = degw.get(src, 0.0)
            elif mode == "sqrt":
                nz = degw.get(src, 0.0) ** 0.5
            elif mode == "logw":
                nz = math.log(math.e + degw.get(src, 0.0))
            elif mode == "logc":
                nz = math.log(math.e + degc.get(src, 0))
            else:
                nz = degw.get(src, 0.0)
            if nz <= 0:
                continue
            for nb, w in adj.get(src, ()):
                flow = a * (w / nz) * DECAY
                if flow > 0:
                    nxt[nb] += flow
                    act[nb] += flow
        frontier = nxt
    return act


def evaluate(adj, degw, degc, sessions, mode):
    h10 = h25 = mrr = n = 0
    for seed, targets in sessions:
        act = spread({seed: 2.2}, adj, degw, degc, mode)
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
        print(f"eval_walk_norm: trace too thin ({len(events)} events).")
        return
    wiki = wiki_edges(real)
    modes = ["linear", "sqrt", "logw", "logc"]
    print(f"eval_walk_norm: {len(events)} events, {len(wiki)} wiki edges. Intermediate-normalization A/B "
          f"(baseline=linear=live walk).")
    d10 = defaultdict(list)
    for split in (0.60, 0.70, 0.80):
        T = events[int(split * len(events))][0]
        pre = [e for e in events if e[0] < T]
        post = [e for e in events if e[0] >= T]
        adj, degw, degc = build(wiki, coread_counts(pre))
        pby = defaultdict(list)
        for ep, slug, sess in post:
            if slug not in pby[sess]:
                pby[sess].append(slug)
        sessions = [(v[0], v[1:]) for v in pby.values() if 2 <= len(v) <= MAXSESS]
        if not sessions:
            continue
        base = None
        print(f"split {split:.2f} ({len(sessions)} sessions):")
        for mode in modes:
            h10v, h25v, mr, nt = evaluate(adj, degw, degc, sessions, mode)
            if mode == "linear":
                base = (h10v, h25v, mr)
                print(f"    linear  hit@10 {h10v:.3f}  hit@25 {h25v:.3f}  MRR {mr:.3f}  ({nt} targets)")
            else:
                dd = h10v - base[0]
                d10[mode].append(dd)
                print(f"    {mode:6s}  hit@10 {h10v:.3f}  hit@25 {h25v:.3f}  MRR {mr:.3f}   "
                      f"d10 {dd:+.3f} d25 {h25v - base[1]:+.3f} dMRR {mr - base[2]:+.3f}")
    print("\nverdict (clean reach win = d10>0 all splits, no flip):")
    for mode in modes[1:]:
        ds = d10[mode]
        clean = len(ds) == 3 and all(d > 0 for d in ds)
        flip = any(d > 0 for d in ds) and any(d < 0 for d in ds)
        print(f"  {mode:6s} d10 = {['%+.3f' % d for d in ds]}  -> "
              f"{'CLEAN reach win' if clean else ('SIGN FLIP - reject' if flip else 'no clean win')}")
    print("  NOTE: young trace; a reach win must ALSO hold in eval_linkpred (two axes) before wiring.")


if __name__ == "__main__":
    main()
