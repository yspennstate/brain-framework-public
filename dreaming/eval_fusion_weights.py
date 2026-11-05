#!/usr/bin/env python3
"""eval_fusion_weights.py - is brain.recall's hand-set blend 0.45*lex + 0.25*graph + 0.30*sem optimal?

VERDICT (2026-07-10): NO CHANGE to brain.recall - but the reason is a PRINCIPLED two-mode architecture,
not a dismissed number. The sweep says a graph-dominant blend (0.0/1.0/0.0) beats current on all 3 splits
(avg MRR 0.126 vs 0.101, +0.025). I ran the negative control (control_random_graph.py) to size the
shared-construction worry (graph is built FROM co-reads, ground truth IS co-reads): degree-preserving rewire
of the edges COLLAPSES the graph-dominant advantage from +0.058 (real) to -0.003 (rewired), while lexical-only
is identical on both graphs (0.0675 - the clean control). So the graph win is REAL held-out-co-read
prediction from the specific edges, NOT a degree/structure artifact. That means:
  (1) The LIVE PROACTIVE hook (spread_recall.main = activation*salience*cosine-damp, graph-dominant) is
      genuinely VALIDATED - past co-reads really do predict future ones, and it bets on that correctly.
  (2) The deliberate `brain.py recall "words"` CLI (the ONLY user of this blend, zero automated callers) is
      lexical-forward, and this eval's co-read ground truth CANNOT license reweighting it (co-reads are the
      proactive task). Its proper ground truth is held-out wiki-links - see eval_deliberate_recall.py, which
      DID find a robust 5-fold win and shipped a conservative reweight to 0.625/0.25/0.125 (more lexical,
      less semantic, graph unchanged). That STRENGTHENS the split below rather than contradicting it.
The recall stack correctly splits: proactive = graph-dominant (surface what you did NOT ask for), deliberate =
lexical-forward (find what you DID ask for). Both are right for their mode - and each mode's OWN ground truth
(co-reads / wiki-links) independently prefers its blend, which is the strongest evidence the split is real.


Reachability got memories INTO the candidate pool; this asks the next question - does the RANKING surface
the right one first? (live hit@10 ~0.27, MRR ~0.10 - often reachable but buried.) The three fusion weights
were hand-chosen, never learned. This sweeps them against causal ground truth and reports whether a
different blend wins ROBUSTLY (every split, no sign flip) or the current point already sits in the good basin.

Ground truth = co-reads, same protocol as the reach evals but on the FULL fusion, not just the walk. For
each post-split session (2..25 memos), the FIRST-read memo is the query context (its index-line words are
the query, exactly what a session reasoning about it would type); the other memos read that session are the
relevance targets. We reproduce recall()'s exact seeding + spread + semantic + salience tilt, vary only the
(wl, wg, ws) blend, and score MRR / hit@10 / hit@25 averaged over 3 causal splits (60/70/80th pct).

Discipline: don't chase a sharp peak on a non-stationary trace (that's overfit). Ship a change only if a
weighting beats current on ALL three splits AND sits in a robust plateau; else confirm current is fine.
Read-only. Usage: python eval_fusion_weights.py [--grid 6]
"""
import io, os, re, sys
from collections import defaultdict
HOME = os.path.expanduser("~"); DR = os.path.join(HOME, ".claude", "dreaming")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
sys.path.insert(0, DR)
import spread_recall as sr
import brain as B
DATE = re.compile(r"_\d{4}_\d{2}_\d{2}$")
MAXSESS = 25


def norm(s):
    s = s.strip(); s = s[:-3] if s.endswith(".md") else s
    return DATE.sub("", s.replace("-", "_").lower())


def sessions_from_trace(post_rows):
    """post_rows: list of (ts, slug, session_id). Group by session_id, order by ts, first=query."""
    by = defaultdict(list)
    for ts, slug, sess in post_rows:
        by[sess].append((ts, slug))
    out = []
    for sess, rows in by.items():
        rows.sort()
        slugs = []
        for _, s in rows:
            if s not in slugs:
                slugs.append(s)
        if 2 <= len(slugs) <= MAXSESS:
            out.append((slugs[0], slugs[1:]))
    return out


def rank_for_query(query_slug, idx, adj, deg, sal, tier, wl, wg, ws):
    """Reproduce brain.recall()'s scoring for a query = query_slug's index-line words. Return ranked slugs."""
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
    scored = []
    for slug in set(list(seeds) + list(act) + list(sem)):
        if slug in sr.SKIP or slug == query_slug:
            continue
        ls = seeds.get(slug, 0.0) / lmax
        gs = (act.get(slug, 0.0) / amax) if act else 0.0
        ss = sem.get(slug, 0.0)
        score = wl * ls + wg * gs + ws * ss
        salb = 0.9 + 0.2 * (sal.get(slug, 50.0) / 100.0)
        if tier.get(slug) == "unconscious":
            salb *= 0.35
        scored.append((score * salb, slug))
    scored.sort(reverse=True)
    return [s for _, s in scored]


def score_blend(sessions, idx, adj, deg, sal, tier, wl, wg, ws):
    h10 = h25 = mrr = n = 0
    for q, tgts in sessions:
        ranked = rank_for_query(q, idx, adj, deg, sal, tier, wl, wg, ws)
        if ranked is None:
            continue
        pos = {s: i for i, s in enumerate(ranked)}
        for t in tgts:
            n += 1
            i = pos.get(t)
            if i is None:
                continue
            if i < 10:
                h10 += 1
            if i < 25:
                h25 += 1
            mrr += 1.0 / (i + 1)
    return (h10 / n, h25 / n, mrr / n, n) if n else (0, 0, 0, 0)


def main():
    grid = 6
    a = sys.argv[1:]
    for i, x in enumerate(a):
        if x == "--grid" and i + 1 < len(a):
            try:
                grid = int(a[i + 1])
            except Exception:
                pass
    # session id: retrievals.tsv is (slug, ts, source, ??). The session column - reload with all cols.
    rows = []
    for i, ln in enumerate(io.open(os.path.join(DR, "retrievals.tsv"), encoding="utf-8", errors="replace")):
        if i == 0:
            continue
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 4:
            try:
                rows.append((float(p[1]), p[0], p[3]))   # ts, slug, session (cols: slug,epoch,iso,session)
            except Exception:
                pass
    rows.sort()
    idx = B._archived_index_lines()
    idx.update(sr._index_lines())
    adj, deg = sr._load_graph()
    sal = sr._load_salience()
    tier = B._tiers()

    # _load_embeddings() re-reads the .npz + recomputes bias on EVERY call; over ~2500 rank calls that is
    # thousands of disk reloads (the silent-slow trap). Load once, monkeypatch the cache so B._semantic_scores
    # runs its exact math but instant. Restore after (courtesy; process exits anyway).
    _emb_cache = sr._load_embeddings()
    _orig_load = sr._load_embeddings
    sr._load_embeddings = lambda: _emb_cache
    print("embeddings:", "cached %d vecs" % len(_emb_cache[0]) if _emb_cache else "SIDECAR ABSENT (semantic=0)")

    # candidate blends on the simplex (wl+wg+ws=1), plus the current point
    # STALE BY TWO GENERATIONS, annotated 2026-08-22 (session 95e8c38d). This constant is the
    # pre-2026-07-10 blend. It was superseded on 07-10 by 0.625/0.25/0.125 - which this file's OWN
    # docstring describes - and the whole blend architecture was retired on 2026-07-15, when
    # brain.py went BM25-FIRST (line 1160, OQ#1164); the blend now runs only under
    # BRAIN_RECALL_BLEND=1. So "is brain.recall's hand-set blend optimal?" is a question about a
    # code path that has not shipped in five weeks. The file's PROACTIVE conclusions are unaffected:
    # spread_recall.main is still graph-dominant and this eval's co-read ground truth is the right
    # one for it. Only the DELIBERATE-CLI half of the header is out of date.
    CUR = (0.45, 0.25, 0.30)   # pre-2026-07-10 blend; two supersessions behind the shipped ranker
    blends = set()
    for i in range(grid + 1):
        for j in range(grid + 1 - i):
            wl = i / grid
            wg = j / grid
            ws = 1.0 - wl - wg
            if ws < -1e-9:
                continue
            blends.add((round(wl, 3), round(wg, 3), round(ws, 3)))
    blends.add(CUR)
    blends = sorted(blends)

    splits = (0.60, 0.70, 0.80)
    ts_all = [r[0] for r in rows]
    per_split_sessions = []
    for sp in splits:
        T = ts_all[int(sp * len(ts_all))]
        post = [r for r in rows if r[0] >= T]
        per_split_sessions.append(sessions_from_trace(post))
    print("eval_fusion_weights: sessions per split =", [len(s) for s in per_split_sessions],
          "| current blend =", CUR)

    results = {}
    for b in blends:
        per = []
        for sess in per_split_sessions:
            per.append(score_blend(sess, idx, adj, deg, sal, tier, *b))
        avg_mrr = sum(p[2] for p in per) / len(per)
        avg_h10 = sum(p[0] for p in per) / len(per)
        results[b] = (avg_mrr, avg_h10, per)

    cur_mrr = results[CUR][0]
    # rank blends by avg MRR; show current + top few + require robust win over current on every split
    order = sorted(results, key=lambda b: -results[b][0])
    print(f"\ncurrent {CUR}: avg MRR {cur_mrr:.4f}  hit@10 {results[CUR][1]:.3f}  "
          f"per-split MRR {[f'{p[2]:.3f}' for p in results[CUR][2]]}")
    print("\ntop blends by avg MRR:")
    for b in order[:8]:
        mrr, h10, per = results[b]
        beats_all = all(per[k][2] > results[CUR][2][k][2] + 1e-6 for k in range(len(per)))
        tag = " <== current" if b == CUR else (" ROBUST-WIN" if beats_all else "")
        print(f"  {b}: avg MRR {mrr:.4f}  hit@10 {h10:.3f}  per-split {[f'{p[2]:.3f}' for p in per]}{tag}")
    winners = [b for b in order if b != CUR and
               all(results[b][2][k][2] > results[CUR][2][k][2] + 1e-6 for k in range(3))]
    print(f"\nROBUST winners over current (beat on ALL 3 splits): {len(winners)}")
    if winners:
        best = winners[0]
        print(f"  best robust: {best} avg MRR {results[best][0]:.4f} vs current {cur_mrr:.4f} "
              f"(+{results[best][0]-cur_mrr:.4f}). Check it sits in a plateau before shipping.")
    else:
        print("  none - the hand-set blend is in the robust basin; KEEP it (validated-optimal, not overfit).")


if __name__ == "__main__":
    main()
