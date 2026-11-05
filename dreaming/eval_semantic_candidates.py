#!/usr/bin/env python3
"""eval_semantic_candidates.py - would injecting SEMANTIC candidates lift recall of what sessions read?

Structural finding (2026-07-10): the association graph is 98.9% connected but mean path length is 6.37
hops, and the 2-hop walk reaches only ~4% of the graph from a node. So a content-related memory more than
2 hops away NEVER becomes a candidate, and the embedding re-rank (which only re-orders the walk's
candidates) can't surface it. That caps cross-domain transfer and recall of recent, graph-sparse
memories. This tests the fix: add the top-k memories by cosine to the seed to the candidate pool, so a
semantically-related memory can fire even when the walk doesn't reach it.

A/B on eval_walk_reach's causal protocol (60/70/80 splits, co-read pre-split, wiki as-of-now):
  A = walk candidates only (today).
  B = walk candidates UNION top-k cosine-to-seed memories (given a small synthetic activation).
Both ranked by the same score (activation * salience-ish * calibrated-cosine damp). Report hit@10/@25/MRR
on the sessions' actual future reads. A clean multi-split lift = semantic candidates rescue real recall;
ship into spread_recall's candidate generation. No lift, or a hit@10 drop (noise crowding) = reject.
Read-only. Usage: python eval_semantic_candidates.py [--k 8]

VERDICT 2026-07-10 (measured, 3 splits): REJECTED - hurts recall badly. hit@10 dropped -0.10/-0.09/-0.16,
hit@25 and MRR down too, every split. Why: a content-similar memory that the walk did NOT reach is
usually NOT behaviourally connected (not co-read), so injecting the top-k cosine candidates adds far more
noise (high-cosine non-reads) than signal, crowding the session's real targets DOWN the ranking. The
premise (33% of missed reads are high-cosine to the seed) is true but misleading - the high-cosine
NON-reads swamp them. This restates the walk's core design and the eval_walk_reach semantic-EDGE
rejection: content similarity != behavioural relevance; the embedding earns its keep in RANKING (re-order
the walk's behavioural candidates, +0.11 causal) but NOT in candidate GENERATION (it generates noise). The
cross-domain limitation (a related memory >2 hops away never fires) is real, but this fix is worse than
the disease. The walk-generates-candidates / embedding-re-ranks split is correct. Filed so nobody re-tries it.
"""
import io, itertools, os, re, sys
import numpy as np
from collections import defaultdict

HOME = os.path.expanduser("~"); DR = os.path.join(HOME, ".claude", "dreaming")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
sys.path.insert(0, DR)
import spread_recall as sr
import embedding_artifact

LINK_RE = re.compile(r"\[\[([^\]]+?)\]\]"); DATE = re.compile(r"_\d{4}_\d{2}_\d{2}$")
SKIP = {"MEMORY","INDEX_ARCHIVE","PREDICTIONS","STATE_OF_MIND","OPEN_QUESTIONS","AUTOBIOGRAPHY",
        "MODEL_OF_PARTNER","FASCINATIONS","THE_OTHER_MINDS","MISTAKES","CONSCIENCE","CURIOSITY"}
W_COACT, W_WIKI, MAXSESS = 1.6, 1.0, 25


def norm(s):
    s = s.strip(); s = s[:-3] if s.endswith(".md") else s
    return DATE.sub("", s.replace("-", "_").lower())


def load_events():
    ev = []
    for i, ln in enumerate(io.open(os.path.join(DR, "retrievals.tsv"), encoding="utf-8", errors="replace")):
        if i == 0: continue
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 4:
            try: ev.append((float(p[1]), norm(p[0]), p[3]))
            except: pass
    ev.sort(); return ev


def wiki_edges(real):
    e = defaultdict(float)
    for fn in os.listdir(MEM):
        if not fn.endswith(".md") or fn[:-3] in SKIP: continue
        src = norm(fn)
        if src not in real: continue
        try: t = io.open(os.path.join(MEM, fn), encoding="utf-8", errors="replace").read()
        except: continue
        for m in LINK_RE.findall(t):
            b = norm(m)
            if b in real and b != src: e[tuple(sorted((src, b)))] += W_WIKI
    return e


def coread(pre):
    by = defaultdict(set)
    for ep, s, se in pre: by[se].add(s)
    c = defaultdict(int)
    for ss in by.values():
        if 2 <= len(ss) <= MAXSESS:
            for a, b in itertools.combinations(sorted(ss), 2): c[(a, b)] += 1
    return {k: W_COACT * v for k, v in c.items()}


def to_adj(dicts):
    tot = defaultdict(float)
    for d in dicts:
        for k, w in d.items(): tot[k] += w
    adj = defaultdict(list); deg = defaultdict(float)
    for (a, b), w in tot.items():
        adj[a].append((b, w)); adj[b].append((a, w)); deg[a] += w; deg[b] += w
    return adj, deg


def main():
    k = 8
    a = sys.argv[1:]
    for i, x in enumerate(a):
        if x == "--k" and i + 1 < len(a):
            try: k = int(a[i + 1])
            except: pass
    real = {norm(fn) for fn in os.listdir(MEM) if fn.endswith(".md") and fn[:-3] not in SKIP}
    events = [e for e in load_events() if e[1] in real]
    wiki = wiki_edges(real)
    canonical = os.path.join(DR, "memory_embeddings.npz")
    authority = os.path.join(DR, ".embedding_current.json")
    loaded = embedding_artifact.load_verified_embeddings(
        canonical, authority, MEM, "BAAI/bge-small-en-v1.5", verify_all_rows=True
    )
    if loaded is None:
        raise SystemExit("eval_semantic_candidates: no verified active embedding authority")
    raw_idx, mat, bias = loaded
    idx = {norm(str(s)): i for s, i in raw_idx.items()}
    _, receipt = embedding_artifact.resolve_authority_sidecar(canonical, authority)
    print("embedding_generation=%s model=%s" %
          (receipt["sidecar_sha256"][:12], receipt["model"]))
    sal = sr._load_salience()

    def rank(seed, adj, deg, semantic):
        act, _ = sr._spread({seed: 2.2}, adj, deg)
        cand = dict(act)
        if semantic and seed in idx:
            sims = mat @ mat[idx[seed]]
            order = np.argsort(-sims)
            inv = {i: s for s, i in idx.items()}
            added = 0
            for i in order:
                s = inv.get(int(i))
                if s and s != seed and s in real and s not in cand:
                    c = (float(sims[int(i)]) - bias) / (1.0 - bias)
                    if c < 0.30: break
                    cand[s] = 0.20 * c          # synthetic activation from calibrated cosine
                    added += 1
                    if added >= k: break
        scored = []
        for s, av in cand.items():
            if s == seed: continue
            sc = av * (0.6 + sal.get(s, 40.0) / 250.0)
            if seed in idx and s in idx:
                cs = (float(mat[idx[s]] @ mat[idx[seed]]) - bias) / (1.0 - bias)
                sc *= (0.4 + 0.6 * max(0.0, cs))
            scored.append((sc, s))
        scored.sort(reverse=True)
        return [s for _, s in scored]

    def evaluate(adj, deg, sessions, semantic):
        h10 = h25 = mrr = n = 0
        for seed, targets in sessions:
            ranked = rank(seed, adj, deg, semantic)
            pos = {s: i for i, s in enumerate(ranked)}
            for t in targets:
                n += 1; i = pos.get(t)
                if i is None: continue
                if i < 10: h10 += 1
                if i < 25: h25 += 1
                mrr += 1.0 / (i + 1)
        return (h10 / n, h25 / n, mrr / n, n) if n else (0, 0, 0, 0)

    print(f"eval_semantic_candidates (k={k}): does adding top-cosine candidates lift recall of real reads?")
    d10 = []
    for split in (0.60, 0.70, 0.80):
        T = events[int(split * len(events))][0]
        pre = [e for e in events if e[0] < T]; post = [e for e in events if e[0] >= T]
        adj, deg = to_adj([wiki, coread(pre)])
        pby = defaultdict(list)
        for ep, s, se in post:
            if s not in pby[se]: pby[se].append(s)
        sessions = [(v[0], v[1:]) for v in pby.values() if 2 <= len(v) <= MAXSESS]
        if not sessions: continue
        h10a, h25a, mra, nt = evaluate(adj, deg, sessions, False)
        h10b, h25b, mrb, _ = evaluate(adj, deg, sessions, True)
        d10.append(h10b - h10a)
        print(f"  split {split:.2f} ({len(sessions)} sess, {nt} targets):  A hit@10 {h10a:.3f} @25 {h25a:.3f} "
              f"MRR {mra:.3f}  |  B(+sem) d10 {h10b-h10a:+.3f} d25 {h25b-h25a:+.3f} dMRR {mrb-mra:+.3f}")
    clean = len(d10) == 3 and all(x > 0 for x in d10)
    flip = any(x > 0 for x in d10) and any(x < 0 for x in d10)
    print(f"  verdict: d10 {['%+.3f' % x for x in d10]} -> "
          f"{'CLEAN LIFT - candidate to wire into spread_recall candidates' if clean else ('SIGN FLIP - reject' if flip else 'no clean lift')}")


if __name__ == "__main__":
    main()
