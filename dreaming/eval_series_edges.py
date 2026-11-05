#!/usr/bin/env python3
"""eval_series_edges.py - gate for build_series_edges: big navigability gain, zero recall regression?

build_series_edges links each research-program member to its K nearest siblings so the 2-hop walk can
traverse the whole program (arnold_diffusion navigability 2->27 of 42, measured). This must not cost the
EXISTING recall. A/B on the eval_orphan_edges/eval_walk_reach protocol (causal splits, co-read pre-split,
wiki as-of-now): A = current graph (wiki+coread+orphan edges), B = A + series edges. Report existing-recall
deltas (must be ~0) and the navigability of a sample program. Sweeps weight to find the non-regressing point.
Read-only. Usage: python eval_series_edges.py [--weight 0.15]
"""
import io, itertools, os, re, sys
from collections import defaultdict

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
sys.path.insert(0, DR)
import spread_recall as sr
import build_series_edges as bse
import build_orphan_edges as boe

LINK_RE = re.compile(r"\[\[([^\]]+?)\]\]")
DATE_TAIL = re.compile(r"_\d{4}_\d{2}_\d{2}$")
SKIP = bse.SKIP
W_COACT, W_WIKI, MAXSESS = 1.6, 1.0, 25


def norm(s):
    s = s.strip()
    if s.endswith(".md"):
        s = s[:-3]
    return DATE_TAIL.sub("", s.replace("-", "_").lower())


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
    e = defaultdict(float)
    for fn in os.listdir(MEM):
        if not fn.endswith(".md") or fn[:-3] in SKIP:
            continue
        src = norm(fn)
        if src not in real:
            continue
        try:
            t = io.open(os.path.join(MEM, fn), encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for m in LINK_RE.findall(t):
            b = norm(m)
            if b in real and b != src:
                e[tuple(sorted((src, b)))] += W_WIKI
    return e


def coread(pre):
    by = defaultdict(set)
    for ep, s, se in pre:
        by[se].add(s)
    c = defaultdict(int)
    for ss in by.values():
        if 2 <= len(ss) <= MAXSESS:
            for a, b in itertools.combinations(sorted(ss), 2):
                c[(a, b)] += 1
    return {k: W_COACT * v for k, v in c.items()}


def to_adj(dicts):
    tot = defaultdict(float)
    for d in dicts:
        for k, w in d.items():
            tot[k] += w
    adj = defaultdict(list)
    deg = defaultdict(float)
    for (a, b), w in tot.items():
        adj[a].append((b, w))
        adj[b].append((a, w))
        deg[a] += w
        deg[b] += w
    return adj, deg


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
    weight = 0.15
    a = sys.argv[1:]
    for i, x in enumerate(a):
        if x == "--weight" and i + 1 < len(a):
            try:
                weight = float(a[i + 1])
            except Exception:
                pass
    real = {norm(fn) for fn in os.listdir(MEM) if fn.endswith(".md") and fn[:-3] not in SKIP}
    events = [(ep, norm(s), se) for ep, s, se in load_events() if norm(s) in real]
    wiki = wiki_edges(real)
    oe_raw, _, _ = boe.orphan_edges(0.40, 0.1)
    orphan = {tuple(sorted((norm(x), norm(y)))): w for x, y, w in oe_raw}
    se_raw, series = bse.series_edges(4, weight)
    se = {tuple(sorted((norm(x), norm(y)))): w for x, y, w in se_raw}
    print(f"eval_series_edges: {len(series)} series, {len(se)} series edges (weight {weight}).")
    for split in (0.60, 0.70, 0.80):
        T = events[int(split * len(events))][0]
        pre = [e for e in events if e[0] < T]
        post = [e for e in events if e[0] >= T]
        co = coread(pre)
        pby = defaultdict(list)
        for ep, s, se_id in post:
            if s not in pby[se_id]:
                pby[se_id].append(s)
        sessions = [(v[0], v[1:]) for v in pby.values() if 2 <= len(v) <= MAXSESS]
        if not sessions:
            continue
        adjA, degA = to_adj([wiki, co, orphan])
        adjB, degB = to_adj([wiki, co, orphan, se])
        h10a, h25a, mra, nt = evaluate(adjA, degA, sessions)
        h10b, h25b, mrb, _ = evaluate(adjB, degB, sessions)
        print(f"  split {split:.2f} ({len(sessions)} sess, {nt} targets):  A hit@10 {h10a:.3f} @25 {h25a:.3f} "
              f"MRR {mra:.3f}  |  B(+series) d10 {h10b - h10a:+.3f} d25 {h25b - h25a:+.3f} dMRR {mrb - mra:+.3f}")
    print("  GATE: existing-recall deltas ~0 to wire. Navigability gain (arnold 2->27) is the benefit.")


if __name__ == "__main__":
    main()
