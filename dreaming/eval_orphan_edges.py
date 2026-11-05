#!/usr/bin/env python3
"""eval_orphan_edges.py - the gate before wiring build_orphan_edges into the live graph.

Adding cosine-attachment edges makes the 334 orphans reachable, but it must NOT degrade recall for the
already-connected memories (the ones the trace can actually measure). This is the non-regression gate,
plus a constructive reachability check:

  A/B, eval_walk_reach protocol (60/70/80 causal splits, co-read pre-split, wiki as-of-now):
    A = wiki + co-read              (the live graph today)
    B = wiki + co-read + orphan_edges (build_orphan_edges.orphan_edges, calibrated cos >= threshold)
  Report hit@10/@25/MRR deltas on the EXISTING post-split sessions (must be ~0 = no regression), and
  the degree-0 orphan count before vs after (must drop = orphans now reachable).

Ship the merge into build_spread_edges ONLY if existing reach is non-regressed (no consistent negative)
AND the orphan count drops materially. Read-only. Usage: python eval_orphan_edges.py [--min-cos 0.40]
"""
import io, itertools, os, re, sys
from collections import defaultdict

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
sys.path.insert(0, DR)
import spread_recall as sr
import build_orphan_edges as boe

LINK_RE = re.compile(r"\[\[([^\]]+?)\]\]")
DATE_TAIL = re.compile(r"_\d{4}_\d{2}_\d{2}$")
SKIP = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS", "AUTOBIOGRAPHY",
        "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS", "MISTAKES", "CONSCIENCE", "CURIOSITY"}
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
    min_cos = 0.40
    weight = 0.8
    a = sys.argv[1:]
    for i, x in enumerate(a):
        if x == "--min-cos" and i + 1 < len(a):
            try:
                min_cos = float(a[i + 1])
            except Exception:
                pass
        if x == "--weight" and i + 1 < len(a):
            try:
                weight = float(a[i + 1])
            except Exception:
                pass
    real = {norm(fn) for fn in os.listdir(MEM) if fn.endswith(".md") and fn[:-3] not in SKIP}
    events = [(ep, norm(s), se) for ep, s, se in load_events() if norm(s) in real]
    wiki = wiki_edges(real)
    # orphan edges as (norm a, norm b): weight, computed from the CURRENT graph (stable orphan set)
    oe_raw, _, deg_now = boe.orphan_edges(min_cos=min_cos, weight=weight)
    oe = {}
    for a_, b_, w in oe_raw:
        oe[tuple(sorted((norm(a_), norm(b_))))] = w
    orphans_before = sum(1 for s in real if deg_now.get(s, 0) == 0)
    attached = set()
    for (a_, b_) in oe:
        attached.add(a_ if deg_now.get(a_, 0) == 0 else b_)
    print(f"eval_orphan_edges: {orphans_before} degree-0 orphans; {len(oe)} attachment edges "
          f"(calibrated cos>={min_cos}) -> {len(attached)} become reachable, "
          f"{orphans_before - len(attached)} stay orphaned (weak anchor).")
    for split in (0.60, 0.70, 0.80):
        T = events[int(split * len(events))][0]
        pre = [e for e in events if e[0] < T]
        post = [e for e in events if e[0] >= T]
        co = coread(pre)
        pby = defaultdict(list)
        for ep, s, se in post:
            if s not in pby[se]:
                pby[se].append(s)
        sessions = [(v[0], v[1:]) for v in pby.values() if 2 <= len(v) <= MAXSESS]
        if not sessions:
            continue
        adjA, degA = to_adj([wiki, co])
        adjB, degB = to_adj([wiki, co, oe])
        h10a, h25a, mra, nt = evaluate(adjA, degA, sessions)
        h10b, h25b, mrb, _ = evaluate(adjB, degB, sessions)
        print(f"  split {split:.2f} ({len(sessions)} sess, {nt} targets):  "
              f"A hit@10 {h10a:.3f} @25 {h25a:.3f} MRR {mra:.3f}  |  "
              f"B d10 {h10b - h10a:+.3f} d25 {h25b - h25a:+.3f} dMRR {mrb - mra:+.3f}")
    print("  GATE: existing-recall deltas must be ~0 (no consistent negative) to wire. Orphans now "
          "reachable by construction (degree 0 -> >0).")


if __name__ == "__main__":
    main()
