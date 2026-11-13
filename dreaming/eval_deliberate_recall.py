#!/usr/bin/env python3
"""eval_deliberate_recall.py - the eval I said didn't exist. Does brain.recall's 0.45/0.25/0.30 blend serve
the DELIBERATE word-query task well, on ground truth that is NOT co-reads?

VERDICT (2026-07-10): SHIPPED a conservative reweight 0.45/0.25/0.30 -> 0.625/0.25/0.125. On held-out
wiki-links (5-fold, ~1200 queries) the robust basin (9 blends beat current on ALL folds) sits at high-lexical
/ low-semantic; current was outside it. Shipped an interior point, not the argmax (0.875/0.125/0): graph kept
at 0.25, lexical raised, semantic kept POSITIVE (0.125) to hedge the one ground-truth bias - wiki-links are
often cross-topic, where cosine underrates the true partner, so a zero-semantic optimum would overfit that.
+0.010 MRR robust across all 5 folds. This is the OPPOSITE direction from the co-read eval (which favours
graph) - each mode's own ground truth prefers its own blend, confirming the two-mode split is real.

HOP-DEPTH (2026-07-11, --hops): tested whether the deliberate CLI - which, unlike the fast proactive hook,
can afford a deeper walk - benefits from 3 hops. It does NOT meaningfully: HOPS=3 gave +0.0014 MRR over
HOPS=2 (0.1220 vs 0.1206), consistent on all 5 folds but negligible (~1%), at a much larger walk. So the
2-hop horizon is confirmed sufficient even for deliberate recall - the local structure holds the retrievable
signal, the tail beyond 2 hops is genuinely hard to reach usefully (consistent with brain_graph_structure's
"2-hop captures 76% of behaviour" and the rejected semantic-candidate reach). Kept HOPS=2.


eval_fusion_weights punted on the deliberate CLI ("no clean causal ground truth - no logged queries"). But
hand-authored wiki-links ([[B]] in A's body) are a human assertion "A relates to B" that is INDEPENDENT of
the co-read graph, so they break the graph<-co-read circularity. Protocol: K-fold hold out wiki-links,
rebuild the graph WITHOUT the held-out ones (co-reads + orphan/series/keyword + the OTHER wiki-links stay),
then for each held-out (A,B): query = A's index-line words (what you'd type to find A's neighbourhood),
target = B. A blend that ranks B well is finding the human-asserted relative from words alone - the deliberate
task. Wiki-links carry no timestamp, so K-fold cross-validation (not a causal split) is the right guard here;
the fusion weights don't depend on time, so there's no temporal leakage.

Ship only a ROBUST win over current on every fold; else the current lexical-forward blend is confirmed for
the deliberate mode. Read-only. Usage: python eval_deliberate_recall.py [--folds 5] [--sample 80] [--grid 4]
"""
import io, os, random, re, sys
from collections import defaultdict
sys.path.insert(0, os.path.expanduser(r"~/.claude/dreaming"))
import spread_recall as sr
import brain as B
import eval_fusion_weights as E

random.seed(2718)
HOME = os.path.expanduser("~"); DR = os.path.join(HOME, ".claude", "dreaming")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
LINK_RE = re.compile(r"\[\[([^\]]+?)\]\]")
DATE = re.compile(r"_\d{4}_\d{2}_\d{2}$")
SKIP = sr.SKIP
W_COACT, W_WIKI = 1.6, 1.0


def norm(s):
    s = s.strip(); s = s[:-3] if s.endswith(".md") else s
    return DATE.sub("", s.replace("-", "_").lower())


def load_real():
    return {norm(fn): fn[:-3] for fn in os.listdir(MEM) if fn.endswith(".md") and fn[:-3] not in SKIP}


def wiki_pairs(real):
    """[(A_full, B_full)] hand-authored links, both ends real, deduped undirected."""
    seen = set(); out = []
    for fn in os.listdir(MEM):
        if not fn.endswith(".md") or fn[:-3] in SKIP:
            continue
        src = norm(fn)
        if src not in real:
            continue
        try:
            body = io.open(os.path.join(MEM, fn), encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for m in LINK_RE.findall(body):
            b = norm(m)
            if b in real and b != src:
                k = tuple(sorted((src, b)))
                if k not in seen:
                    seen.add(k)
                    out.append((real[src], real[b]))   # full slugs
    return out


def tsv_edges(path, wfac=1.0):
    e = []
    if os.path.exists(path):
        for ln in io.open(path, encoding="utf-8", errors="replace"):
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 3 and p[0] != "a":
                try:
                    e.append((p[0], p[1], float(p[2]) * wfac))
                except Exception:
                    pass
    return e


def base_non_wiki_edges(real):
    """co-read + orphan + series + keyword edges as (Afull, Bfull, w) - constant across folds."""
    out = []
    for a, b, w in tsv_edges(os.path.join(DR, "coactivation.tsv")):
        na, nb = norm(a), norm(b)
        if na in real and nb in real and na != nb:
            out.append((real[na], real[nb], W_COACT * w))
    for name in ("orphan_edges.tsv", "series_edges.tsv", "keyword_edges.tsv"):
        for a, b, w in tsv_edges(os.path.join(DR, name)):
            na, nb = norm(a), norm(b)
            if na in real and nb in real and na != nb:
                out.append((real[na], real[nb], w))
    return out


def to_adj(edges):
    tot = defaultdict(float)
    for a, b, w in edges:
        k = (a, b) if a < b else (b, a)
        tot[k] += w
    adj = defaultdict(list); deg = defaultdict(float)
    for (a, b), w in tot.items():
        adj[a].append((b, w)); adj[b].append((a, w)); deg[a] += w; deg[b] += w
    return adj, deg


def pair_components(query_slug, idx, adj, deg, sal, tier):
    """The BLEND-INDEPENDENT part of the ranking (seeds, spread, semantic, salience) computed ONCE per
    query, so the weight sweep is nearly free. Mirrors brain.recall / eval_fusion_weights.rank_for_query
    exactly; returns [(ls, gs, ss, salb, slug)] or None if the query has no lexical foothold."""
    line = idx.get(query_slug, "") + " " + query_slug
    words = sr._words(line)
    seeds, _lex = sr._seeds_from_prompt(words, idx)
    if not seeds and words:
        need = max(1, len(words) // 3)
        slugs = B._corpus_slugs()
        lines = {s: (idx.get(s, "") + " " + s).lower() for s in slugs}
        import math
        df = {w: sum(1 for s in slugs if w in lines[s]) for w in words}
        cand = []
        for slug in slugs:
            hits = [w for w in words if w in lines[slug]]
            if len(hits) >= need:
                cand.append((sum(math.log(1.0 + len(slugs) / (1.0 + df[w])) for w in hits), slug))
        cand.sort(reverse=True)
        seeds = {slug: ov for ov, slug in cand[:25]}
    if not seeds:
        return None
    act = {}
    if adj:
        act, _ = sr._spread(seeds, adj, deg)
    sem = B._semantic_scores(seeds)
    lmax = max(seeds.values()) or 1.0
    amax = max(act.values()) if act else 1.0
    comps = []
    for slug in set(list(seeds) + list(act) + list(sem)):
        if slug in sr.SKIP or slug == query_slug:
            continue
        ls = seeds.get(slug, 0.0) / lmax
        gs = (act.get(slug, 0.0) / amax) if act else 0.0
        ss = sem.get(slug, 0.0)
        salb = 0.9 + 0.2 * (sal.get(slug, 50.0) / 100.0)
        if tier.get(slug) == "unconscious":
            salb *= 0.35
        comps.append((ls, gs, ss, salb, slug))
    return comps


def target_rank(comps, target, wl, wg, ws):
    """Rank of `target` under blend (wl,wg,ws) over precomputed components. None if absent."""
    scored = sorted(((wl * ls + wg * gs + ws * ss) * salb, slug) for ls, gs, ss, salb, slug in comps)
    scored.reverse()
    for i, (_sc, slug) in enumerate(scored):
        if slug == target:
            return i
    return None


def main():
    folds = 5; sample = 80; grid = 4
    a = sys.argv[1:]
    for i, x in enumerate(a):
        if x == "--folds" and i + 1 < len(a): folds = int(a[i + 1])
        if x == "--sample" and i + 1 < len(a): sample = int(a[i + 1])
        if x == "--grid" and i + 1 < len(a): grid = int(a[i + 1])
        if x == "--hops" and i + 1 < len(a): sr.HOPS = int(a[i + 1])   # test walk depth (default 2)

    _emb = sr._load_embeddings()
    sr._load_embeddings = lambda: _emb   # cache; avoid per-call .npz reloads
    print("embeddings:", "cached %d" % len(_emb[0]) if _emb else "ABSENT")

    real = load_real()
    idx = B._archived_index_lines(); idx.update(sr._index_lines())
    sal = sr._load_salience(); tier = B._tiers()
    base = base_non_wiki_edges(real)
    wiki = wiki_pairs(real)
    wiki_edges_all = [(a, b, W_WIKI) for a, b in wiki]
    print(f"eval_deliberate_recall: {len(wiki)} wiki-links, {len(base)} non-wiki edges, "
          f"{folds}-fold, sample {sample}/fold, blend grid {grid}")

    order = list(range(len(wiki)))
    random.shuffle(order)
    fold_of = {order[i]: i % folds for i in range(len(wiki))}

    # STALE LABEL CORRECTED 2026-08-22 (session 5e55000b): this was the deployed blend from
    # 2026-07-10 to 2026-07-15 only. brain.py line 1160 - "Ranking is BM25-FIRST since 2026-07-15
    # (OQ#1164)" - and the blend now runs only under BRAIN_RECALL_BLEND=1. So CUR is a HISTORICAL
    # BASELINE, not "reality": a candidate that beats it has beaten the pre-07-15 ranker, which is
    # not the same as beating what ships. Measured end-to-end 2026-08-22 on 143 real owner prompts:
    # the shipped path scores hit@10 0.32 / MRR 0.148, tied with raw bm25, while a reconstruction of
    # this blend scores 0.16. Do not read a win over CUR as a win over the live tool.
    CUR = (0.625, 0.25, 0.125)   # pre-2026-07-15 deployed blend; NOT the current shipped ranker
    blends = set([CUR])
    for i in range(grid + 1):
        for j in range(grid + 1 - i):
            wl, wg = i / grid, j / grid
            ws = 1.0 - wl - wg
            if ws >= -1e-9:
                blends.add((round(wl, 3), round(wg, 3), round(ws, 3)))
    blends = sorted(blends)

    # per-blend per-fold MRR. Compute the blend-INDEPENDENT components (spread+semantic, the expensive
    # part) ONCE per pair, then sweep every blend cheaply over the cache (~len(blends)x less compute).
    agg = {b: [] for b in blends}
    for f in range(folds):
        held = [k for k in range(len(wiki)) if fold_of[k] == f]
        held_set = set(held)
        keep_wiki = [wiki_edges_all[k] for k in range(len(wiki)) if k not in held_set]
        adj, deg = to_adj(base + keep_wiki)
        random.shuffle(held)
        pair_comps = []
        for k in held[:sample]:
            A, Bt = wiki[k]
            if not idx.get(A):
                continue
            comps = pair_components(A, idx, adj, deg, sal, tier)
            if comps is not None:
                pair_comps.append((comps, Bt))
        if not pair_comps:
            continue
        for b in blends:
            tot = 0.0
            for comps, Bt in pair_comps:
                r = target_rank(comps, Bt, *b)
                tot += (1.0 / (r + 1)) if r is not None else 0.0
            agg[b].append(tot / len(pair_comps))
        print(f"  fold {f}: {len(pair_comps)} test links ranked")

    results = {b: sum(v) / len(v) if v else 0.0 for b, v in agg.items()}
    order_b = sorted(results, key=lambda b: -results[b])
    cur = results[CUR]
    print(f"\ncurrent {CUR}: mean MRR {cur:.4f}  per-fold {[f'{m:.3f}' for m in agg[CUR]]}")
    print("\ntop blends by mean MRR (held-out wiki-link recall):")
    for b in order_b[:8]:
        per = agg[b]
        beats_all = len(per) == len(agg[CUR]) and all(per[k] > agg[CUR][k] + 1e-6 for k in range(len(per)))
        tag = " <== current" if b == CUR else (" ROBUST-WIN" if beats_all else "")
        print(f"  {b}: mean MRR {results[b]:.4f}  per-fold {[f'{m:.3f}' for m in per]}{tag}")
    winners = [b for b in order_b if b != CUR and len(agg[b]) == len(agg[CUR])
               and all(agg[b][k] > agg[CUR][k] + 1e-6 for k in range(len(agg[b])))]
    print(f"\nROBUST winners over current (beat on ALL {folds} folds): {len(winners)}")
    if winners:
        print(f"  best: {winners[0]} mean {results[winners[0]]:.4f} vs current {cur:.4f} "
              f"(+{results[winners[0]]-cur:.4f}). Deliberate mode - consider a MODEST move if in a plateau.")
    else:
        print("  none - the lexical-forward blend is confirmed for the deliberate word-query task "
              "on independent (wiki-link) ground truth. eval_fusion_weights' two-mode verdict holds.")


if __name__ == "__main__":
    main()
