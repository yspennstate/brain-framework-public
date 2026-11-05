#!/usr/bin/env python3
"""eval_proactive_coeffs.py - audit the PROACTIVE hook's cosine-damp floor (the one every-session ranker
coefficient never swept). spread_recall.main scores a candidate:
    score = activation * (0.6 + salience/250) * (0.4 + 0.6*cos_calibrated)
The 0.4 is the cosine-damp FLOOR: how much a graph-reached-but-semantically-FAR candidate keeps (0.4=40%;
0.0=fully suppress the far; 1.0=ignore cosine, pure graph). Ground truth = co-reads (the proactive task).
CAUSAL: graph = wiki + orphan/series/keyword + PRE-split co-reads; test on POST-split co-read sessions.

CORRECTNESS (the last build had a keying bug that would silently produce garbage): EVERYTHING is keyed by
FULL slugs - trace slugs are full filenames, wiki_pairs/tsv edges are full slugs, the .npz embeddings are
full slugs, sr._spread walks full-slug adj. Plus SANITY CHECKS printed up front (sessions>0, current-floor
MRR in a sane range, a sample ranking) so a mis-wired run can't masquerade as a result. MEASURE ONLY - a
change here is high-blast-radius (fires every session), so this recommends; it does not deploy.
Usage: python eval_proactive_coeffs.py [--sample 200]

VERDICT (2026-07-11): CONFIRMED - keep floor 0.4; ranking audit now complete for BOTH rankers. On causal
co-reads, MRR improves MONOTONICALLY as the floor drops (0.0=0.121 best, 0.4=0.117 current, 1.0=0.100 worst
- ignoring cosine entirely hurts most, so the semantic damp genuinely earns its place). But floor 0.0 does
NOT robustly beat 0.4 (it LOSES split 1, ties split 2, +0.004 avg) and is an extreme (fully suppress the
semantically-far - risky over-suppression on an every-session hook). So 0.4 is defensibly in the good range.
The monotone trend hints the floor could lean slightly more aggressive (~0.2-0.3), but not robustly enough to
touch a high-blast-radius hook - if ever revisited, deploy deliberately after re-verify, never auto-ship. The
SANITY block did its job: seed law_of_robustness ranked least_sv_submatrix / perceptron / eoc top (sensible
topical neighbours), proving the full-slug keying is correct and the sweep is real, not garbage.
"""
import io, os, re, sys, itertools
from collections import defaultdict
sys.path.insert(0, os.path.expanduser(r"~/.claude/dreaming"))
import spread_recall as sr
import brain as B
import eval_deliberate_recall as ED

HOME = os.path.expanduser("~"); DR = os.path.join(HOME, ".claude", "dreaming")


def load_trace_rows(real):
    rows = []
    for i, ln in enumerate(io.open(os.path.join(DR, "retrievals.tsv"), encoding="utf-8", errors="replace")):
        if i == 0:
            continue
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 4:
            try:
                rows.append((float(p[1]), p[0], p[3]))   # ts, FULL slug, session
            except Exception:
                pass
    rows.sort()
    return rows


def coread_edges_fullslug(pre_rows):
    """Causal co-read edges (full slug) from pre-split events: memos read in the same session."""
    by = defaultdict(set)
    for ts, slug, sess in pre_rows:
        by[sess].add(slug)
    w = defaultdict(float)
    for ss in by.values():
        if 2 <= len(ss) <= 25:
            for a, b in itertools.combinations(sorted(ss), 2):
                w[(a, b)] += 1.6   # W_COACT, same as build_spread_edges
    return [(a, b, v) for (a, b), v in w.items()]


def post_sessions(post_rows):
    by = defaultdict(list)
    for ts, slug, sess in post_rows:
        if slug not in by[sess]:
            by[sess].append(slug)
    return [(v[0], v[1:]) for v in by.values() if 2 <= len(v) <= 25]


def score_candidates(query, adj, deg, sal, cos, floor, slope):
    act, _ = sr._spread({query: 2.2}, adj, deg)
    out = {}
    for slug, a in act.items():
        if slug == query or slug in sr.SKIP:
            continue
        s = a * (0.6 + sal.get(slug, 40.0) / 250.0)
        c = cos.get(slug)
        if c is not None:
            s *= (floor + slope * max(0.0, c))
        out[slug] = s
    return out


def reach(sessions, adj, deg, sal, floor, slope, sample_rank=False):
    h10 = h25 = mrr = n = 0
    sample = None
    for q, tgts in sessions:
        cos = B._semantic_scores({q: 1.0})
        sc = score_candidates(q, adj, deg, sal, cos, floor, slope)
        ranked = [s for s, _ in sorted(sc.items(), key=lambda kv: -kv[1])]
        if sample_rank and sample is None and ranked:
            sample = (q, ranked[:4])
        pos = {s: i for i, s in enumerate(ranked)}
        for t in tgts:
            n += 1
            i = pos.get(t)
            if i is None:
                continue
            if i < 10: h10 += 1
            if i < 25: h25 += 1
            mrr += 1.0 / (i + 1)
    return (h10 / n, h25 / n, mrr / n, n, sample) if n else (0, 0, 0, 0, sample)


def main():
    sample = 200
    a = sys.argv[1:]
    for i, x in enumerate(a):
        if x == "--sample" and i + 1 < len(a):
            sample = int(a[i + 1])

    _emb = sr._load_embeddings()
    sr._load_embeddings = lambda: _emb
    print("embeddings:", "cached %d" % len(_emb[0]) if _emb else "ABSENT")

    real = ED.load_real()                       # {norm: fullslug}
    fulls = set(real.values())                  # full slugs of real memories
    wiki = [(x, y, ED.W_WIKI) for x, y in ED.wiki_pairs(real)]      # full-slug wiki edges
    # orphan/series/keyword edges (full slug) - reuse ED's tsv reader, drop coactivation (causal)
    osk = []
    for name, wf in (("orphan_edges.tsv", 1.0), ("series_edges.tsv", 1.0), ("keyword_edges.tsv", 1.0)):
        for x, y, w in ED.tsv_edges(os.path.join(DR, name), wf):
            if x in fulls and y in fulls:
                osk.append((x, y, w))
    sal = sr._load_salience()

    rows = [r for r in load_trace_rows(real) if r[1] in fulls]
    ts_all = [r[0] for r in rows]
    splits = (0.60, 0.70, 0.80)

    # SANITY: build the 0.70-split once and check sessions>0, spread reaches, sample ranking is sensible
    T0 = ts_all[int(0.70 * len(ts_all))]
    pre0 = [r for r in rows if r[0] < T0]; post0 = [r for r in rows if r[0] >= T0]
    adj0, deg0 = ED.to_adj(wiki + osk + coread_edges_fullslug(pre0))
    sess0 = post_sessions(post0)
    h10, h25, mrr, n, samp = reach(sess0[:sample], adj0, deg0, sal, 0.4, 0.6, sample_rank=True)
    print(f"SANITY: {len(sess0)} post-split co-read sessions; current floor 0.4 -> MRR {mrr:.3f} @10 {h10:.3f} "
          f"({n} targets)")
    if samp:
        print(f"  sample seed {samp[0][:40]} -> top {[s[:34] for s in samp[1]]}")
    if n == 0 or mrr == 0:
        print("  ABORT: zero reach - keying or sessions broken, not trusting the sweep."); return

    print("\ncosine-damp floor sweep (slope = 1 - floor), 3 causal splits:")
    results = {}
    for floor in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0):
        mrrs, h10s = [], []
        for sp in splits:
            T = ts_all[int(sp * len(ts_all))]
            pre = [r for r in rows if r[0] < T]; post = [r for r in rows if r[0] >= T]
            adj, deg = ED.to_adj(wiki + osk + coread_edges_fullslug(pre))
            sess = post_sessions(post)[:sample]
            if not sess:
                continue
            hh10, hh25, m, nn, _ = reach(sess, adj, deg, sal, floor, 1.0 - floor)
            mrrs.append(m); h10s.append(hh10)
        am = sum(mrrs) / len(mrrs) if mrrs else 0.0
        ah = sum(h10s) / len(h10s) if h10s else 0.0
        results[floor] = (am, ah, mrrs)
        tag = "  <== CURRENT" if abs(floor - 0.4) < 1e-9 else ""
        print(f"  floor {floor:.1f}: MRR {am:.4f}  hit@10 {ah:.3f}  per-split {[f'{m:.3f}' for m in mrrs]}{tag}")

    cur = results[0.4][0]
    best = max(results, key=lambda k: results[k][0])
    print(f"\ncurrent floor 0.4: MRR {cur:.4f}   best floor {best}: MRR {results[best][0]:.4f} (+{results[best][0]-cur:.4f})")
    robust = best != 0.4 and results[best][0] - cur > 0.003 and \
        all(results[best][2][i] > results[0.4][2][i] + 1e-6 for i in range(len(results[best][2])))
    if robust:
        print(f"  -> floor {best} robustly beats current. RECOMMEND (measure-only; the hook fires every session - "
              f"deploy deliberately after re-verify, do NOT auto-ship).")
    else:
        print("  -> the current cosine-damp floor 0.4 is optimal or within noise. Proactive hook semantic "
              "weighting CONFIRMED. Ranking audit now complete for BOTH rankers (deliberate + proactive).")


if __name__ == "__main__":
    main()
