#!/usr/bin/env python3
"""eval_walk_reach.py - causal gate for adding semantic edges to the association graph.

Question: if the walk's graph gains top-k cosine-neighbor edges from the sidecar, does spreading
activation REACH more of what a session actually goes on to read - without drowning in fan-out?

Method (causal, recommender-style): split the retrieval trace at the 60/70/80th-pct event. Build
the co-read half of the graph ONLY from pre-split events (the trace is the timestamped source of
the Hebbian signal); wiki-links are as-of-now on BOTH arms (the leak is identical on A and B, and
the verdict is the A-vs-B delta). For each post-split session with 2..25 memory reads: seed the
walk with the session's FIRST-read memory, spread on graph A (baseline) and B (A + semantic
edges), and score whether the walk's top-K contained the memories the session went on to read
(hit-rate@10/25, MRR). Deterministic. Read-only.

VERDICT 2026-07-09 (measured, 291 targets over 3 splits): NOT SHIPPED. Best config (k=8,
floor 0.45, w 0.7) lifts hit@10 by only +0.008/+0.021/+0.014 across splits with a SIGN FLIP on
hit@25 at the 0.60 split, for 2.2x the edge count. The walk already reaches densely where
behavior lives (76% of future co-reads within 2 hops, measured 07-08); semantic edges add reach
where behavior isn't. The sidecar's proven value is RANKING (causal blend +0.113 over graph) and
the dream agenda's semantic candidates - not topology. RE-RUN as the trace grows (it roughly
doubles in weeks; a thin 69-128-target sample decided this); ship only on a clean multi-split win.
"""
import io
import itertools
import os
import re
import sys
from collections import defaultdict

import numpy as np

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
sys.path.insert(0, DR)
import spread_recall as sr
import embedding_artifact

_EMBEDDING_VIEW = None


def embedding_view():
    global _EMBEDDING_VIEW
    if _EMBEDDING_VIEW is None:
        canonical = os.path.join(DR, "memory_embeddings.npz")
        authority = os.path.join(DR, ".embedding_current.json")
        loaded = embedding_artifact.load_verified_embeddings(
            canonical, authority, MEM, "BAAI/bge-small-en-v1.5", verify_all_rows=True
        )
        if loaded is None:
            raise RuntimeError("no verified active embedding authority")
        _, receipt = embedding_artifact.resolve_authority_sidecar(canonical, authority)
        print("embedding_generation=%s model=%s" %
              (receipt["sidecar_sha256"][:12], receipt["model"]))
        _EMBEDDING_VIEW = loaded
    return _EMBEDDING_VIEW

LINK_RE = re.compile(r"\[\[([^\]]+?)\]\]")
DATE_TAIL = re.compile(r"_\d{4}_\d{2}_\d{2}$")
SKIP = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS", "AUTOBIOGRAPHY",
        "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS", "MISTAKES", "CONSCIENCE", "CURIOSITY"}
W_COACT, W_WIKI = 1.6, 1.0


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
                key = tuple(sorted((src, b)))
                edges[key] += W_WIKI
    return edges


def sem_edges(real, k, floor, w_sem):
    loaded = embedding_view()
    raw_idx, vecs, bias = loaded
    keep = [(i, norm(s)) for s, i in raw_idx.items() if norm(s) in real]
    idxs = np.array([i for i, _ in keep])
    names = [n for _, n in keep]
    V = vecs[idxs]
    cal = (V @ V.T - bias) / (1.0 - bias)
    np.fill_diagonal(cal, -1)
    edges = defaultdict(float)
    for r in range(len(names)):
        top = np.argsort(-cal[r])[:k]
        for c in top:
            v = float(cal[r, c])
            if v < floor:
                break
            key = tuple(sorted((names[r], names[int(c)])))
            w = w_sem * v
            if w > edges[key]:
                edges[key] = w
    return edges


def to_adj(edge_dicts):
    adj = defaultdict(list); deg = defaultdict(float)
    tot = defaultdict(float)
    for ed in edge_dicts:
        for (a, b), w in ed.items():
            tot[(a, b)] += w
    for (a, b), w in tot.items():
        adj[a].append((b, w)); adj[b].append((a, w))
        deg[a] += w; deg[b] += w
    return adj, deg, len(tot)


def evaluate(adj, deg, sessions):
    hits10 = hits25 = mrr = n_targets = 0
    for seed, targets in sessions:
        act, _ = sr._spread({seed: 2.2}, adj, deg)
        ranked = [s for s, _ in sorted(act.items(), key=lambda kv: -kv[1]) if s != seed]
        pos = {s: i for i, s in enumerate(ranked)}
        for t in targets:
            n_targets += 1
            i = pos.get(t)
            if i is None:
                continue
            if i < 10:
                hits10 += 1
            if i < 25:
                hits25 += 1
            mrr += 1.0 / (i + 1)
    return (hits10 / n_targets, hits25 / n_targets, mrr / n_targets, n_targets)


def main():
    real = {norm(fn) for fn in os.listdir(MEM) if fn.endswith(".md") and fn[:-3] not in SKIP}
    events = [(ep, norm(slug), sess) for ep, slug, sess in load_events() if norm(slug) in real]
    wiki = wiki_edges(real)
    configs = [("k8_f45_w07", 8, 0.45, 0.7), ("k5_f55_w05", 5, 0.55, 0.5)]
    sems = {name: sem_edges(real, k, f, w) for name, k, f, w in configs}
    print("wiki edges: %d;  semantic edge sets: %s" % (
        len(wiki), {n: len(e) for n, e in sems.items()}))

    for split in (0.60, 0.70, 0.80):
        T = events[int(split * len(events))][0]
        pre = [e for e in events if e[0] < T]
        post = [e for e in events if e[0] >= T]
        co = defaultdict(int)
        by = defaultdict(set)
        for ep, slug, sess in pre:
            by[sess].add(slug)
        for slugs in by.values():
            if 2 <= len(slugs) <= 25:
                for a, b in itertools.combinations(sorted(slugs), 2):
                    co[(a, b)] += 1
        coed = {k: W_COACT * v for k, v in co.items()}
        # post-split sessions: seed = first-read, targets = the rest
        pby = defaultdict(list)
        for ep, slug, sess in post:
            if slug not in pby[sess]:
                pby[sess].append(slug)
        sessions = [(v[0], v[1:]) for v in pby.values() if 2 <= len(v) <= 25]
        adjA, degA, nA = to_adj([wiki, coed])
        h10, h25, mr, nt = evaluate(adjA, degA, sessions)
        print("split %.2f  A(baseline)   edges=%6d  hit@10 %.3f  hit@25 %.3f  MRR %.3f  (%d targets, %d sessions)"
              % (split, nA, h10, h25, mr, nt, len(sessions)))
        for name, _, _, _ in configs:
            adjB, degB, nB = to_adj([wiki, coed, sems[name]])
            h10b, h25b, mrb, _ = evaluate(adjB, degB, sessions)
            print("           B(+%s) edges=%6d  hit@10 %.3f  hit@25 %.3f  MRR %.3f   d10 %+.3f d25 %+.3f dMRR %+.3f"
                  % (name, nB, h10b, h25b, mrb, h10b - h10, h25b - h25, mrb - mr))


if __name__ == "__main__":
    main()
