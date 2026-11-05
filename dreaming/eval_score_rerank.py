#!/usr/bin/env python3
"""eval_score_rerank.py - is the diffusion free-energy readout a better semantic scorer
than the seed-centroid cosine in brain.recall?

THE QUESTION. brain._semantic_scores ranks every embedded memory by cosine to the
UNWEIGHTED MEAN of the seed vectors. In the diffusion-kernel model the seed-centroid readout is the tau->infty / population limit of
the exact finite-temperature object: on unit vectors the Gaussian pattern weight
exp(-|x-y|^2/2tau) equals exp(cos(x,y)/tau) up to a constant, so the principled score
of a candidate m against seeds s_1..s_k with strengths p_nu is the weighted
log-partition (free energy)

    score_tau(m) = tau * log( sum_nu p_nu exp( cos(m, s_nu)/tau ) ).

tau->infty recovers the centroid (mean pooling); tau->0 gives max_nu cos (nearest
seed). The centroid's predicted failure is heterogeneous seed sets: the mean of two
topic clusters sits between them and matches neither. The free energy scores
proximity to EITHER basin. The paper's own moment-matching counterexample (Original
Prop 11.7) warns that an asymptotically clean improvement need not help at finite
sample, so this ships on measurement only.

PROTOCOL. The established deliberate ground truth (eval_deliberate_recall /
eval_bm25first): hand-authored wiki-links [[B]] in A's body as human assertions "A
relates to B", 5-fold split identical to eval_bm25first (seed 11). Query = A's index
line + slug, target = B.
  1. COMPONENT level: rank ALL embedded memories (minus A) by each semantic scorer
     alone. Fold-independent (no graph). Also reported on the semantically-hard
     subset: B not a lexical seed and not in the query's bm25 top-40 (the cases the
     semantic channel exists for in the bm25-first architecture).
  2. PIPELINE level: the live bm25-first ranking (bm25 block, graph tail, semantic
     tail) with only the semantic component swapped, per fold on that fold's graph
     (held-out wiki edges removed), sample 120 pairs/fold.
  3. POSITIVE CONTROLS: known query -> known memory pairs; the variant must not lose
     a memory the live path finds in the top 8.

Ranks are invariant to the (cos-bias)/(1-bias) calibration (monotone), so raw scores
are compared; live wiring keeps the calibration for display. Read-only.

VERDICT (2026-07-23, run 2 after the active-row masking fix; 6,165 component queries,
2,306 hard, 596 pipeline, 5 folds): TWO REGIMES. All pairs: sharp free energy wins big
(lse0.05 MRR 0.1075 vs centroid 0.0463, hit@10 0.217 vs 0.094, positive 5/5 folds) -
but that is the topical regime the bm25 head already owns in-pipe. Hard subset (target
not a seed, not in bm25 top-40 - the semantic tail's actual job): sharp pooling LOSES
to the centroid (0.0068 vs 0.0111, negative 5/5) while the strength-weighted centroid
wins on BOTH regimes on EVERY fold (all 0.0693, hard 0.0163 vs 0.0111, hard hit@10
0.020->0.030); warm lse (tau 0.2-0.4) ties it on hard. Pipeline end-to-end unchanged
(MRR 0.1548 = 0.1548, n=596; bm25 dominates the head). Controls all green.
SHIPPED: wcentroid (strength-weighted seed centroid) as brain._semantic_scores
default, no tuned constant, escape hatch BRAIN_SEM_CENTROID=1 (verified to reproduce
pre-patch output exactly; six live control queries rank 1 post-patch). NOT shipped: a
temperature - the optimum is regime-dependent and choosing one here would be
argmax-on-noise (roll_cadence_argmax_is_noise). Iteration 2: two-stage annealed
readout (sharp head, warm tail) against a regime-separating ground truth; the
two-regime inversion above is the paper's annealing structure appearing in live
retrieval data. Program doc: the diffusion notes; memory node
diffusion_foundations_program_2026_07_23.

VERDICT 2, iteration 2 (2026-07-23 evening, run 3; 6,171 component queries, 2,310
hard, 597 pipeline): the two-stage annealed readouts were built and REFUTED by the
pre-registered rule. anneal_maxz recovers the sharp channel's topical gain on all
pairs (MRR 0.1071 vs wcentroid 0.0692, positive 5/5 folds) but LOSES the hard subset
on every fold (0.0068 vs 0.0164, 0/5; anneal_merge 0.0106, also 0/5), and the
pipeline is identical for all three (MRR 0.1554 - the bm25 head owns the topical
regime the union would add). The stratification killed the iteration-1 conjecture
with a mechanism: within the HARD subset wcentroid wins every quartile of
target-to-nearest-seed cosine (Q1-Q4: 0.0014/0.0067/0.0188/0.0387; lse0.05 collapses
to 0.0144 even in the embedding-NEAR quartile) - a hard target is near via the
aggregate topic, not via one dominant seed, so nearest-basin sharpness locks onto
the wrong memory and the z-max union inherits its false positives. NOT SHIPPED;
wcentroid confirmed as the local optimum on both the temperature axis and the union
axis. Controls all green. The annealing story survives where it belongs: the sharp
channel duplicates the bm25 head in-pipe, so recall's architecture (bm25 head + warm
semantic tail) already IS the two-stage readout, factored across channels.

Usage: python eval_score_rerank.py [--sample 120] [--folds 5]
"""
import os, random, sys
from collections import defaultdict

sys.path.insert(0, os.path.expanduser(r"~/.claude/dreaming"))
import numpy as np
import spread_recall as sr
import brain as B
import eval_deliberate_recall as ED
from bm25 import BM25

TAUS = (0.05, 0.10, 0.20, 0.40)
NAMES = ["centroid", "wcentroid", "maxcos"] + ["lse%.2f" % t for t in TAUS]

# Iteration 2 (2026-07-23): two-stage annealed readouts - the union of a sharp head
# (lse0.05, wins when the target is embedding-near one seed) and a warm tail
# (wcentroid, wins cross-topic). anneal_maxz = elementwise max of per-query z-scores
# of the two channels (the union-of-basins reading); anneal_merge = alternating
# rank-interleave, sharp first (rank aggregation, no scale assumption). Both
# parameter-free. Pre-registered ship rule: anneal_maxz replaces wcentroid only if it
# beats it on hard-subset MRR >=4/5 folds AND all-pairs >=4/5 AND pipeline mean >= live
# AND all positive controls stay green.
ANNEAL = ("anneal_maxz", "anneal_merge")


def zmask(v, mask):
    a = v[mask]
    mu = float(a.mean()); sd = float(a.std()) + 1e-12
    z = (v - mu) / sd
    z[~mask] = -np.inf
    return z


def seed_strengths(query_slug, idx):
    """The exact seed logic pair_components/brain.recall use, returning ({slug: strength}, lex)."""
    line = idx.get(query_slug, "") + " " + query_slug
    words = sr._words(line)
    seeds, lex = sr._seeds_from_prompt(words, idx)
    if not seeds and words:
        import math
        need = max(1, len(words) // 3)
        slugs = B._corpus_slugs()
        lines = {s: (idx.get(s, "") + " " + s).lower() for s in slugs}
        df = {w: sum(1 for s in slugs if w in lines[s]) for w in words}
        cand = []
        for slug in slugs:
            hits = [w for w in words if w in lines[slug]]
            if len(hits) >= need:
                cand.append((sum(math.log(1.0 + len(slugs) / (1.0 + df[w])) for w in hits), slug))
        cand.sort(reverse=True)
        seeds = {slug: ov for ov, slug in cand[:25]}
        lex = set(seeds)
    return seeds, lex


def component_scores(mat, rows, strengths, which=None):
    """Score vectors over all embedded rows for each variant (or just `which`)."""
    S = mat[rows]                          # k x d unit rows
    p = np.asarray(strengths, dtype=float)
    p = p / p.sum()
    out = {}
    todo = [which] if which else NAMES
    cosm = None
    if any(n.startswith(("lse", "maxcos")) for n in todo):
        cosm = mat @ S.T                   # n x k
    if "centroid" in todo:
        c = S.mean(axis=0); c /= (np.linalg.norm(c) + 1e-9)
        out["centroid"] = mat @ c
    if "wcentroid" in todo:
        wc = (S * p[:, None]).sum(axis=0); wc /= (np.linalg.norm(wc) + 1e-9)
        out["wcentroid"] = mat @ wc
    if "maxcos" in todo:
        out["maxcos"] = cosm.max(axis=1)
    logp = np.log(p)
    for tau in TAUS:
        n = "lse%.2f" % tau
        if n in todo:
            z = cosm / tau + logp[None, :]
            zmax = z.max(axis=1)
            out[n] = tau * (zmax + np.log(np.exp(z - zmax[:, None]).sum(axis=1)))
    return out


def rank_in_vector(v, trow, exclude_rows):
    """0-based rank of row trow under scores v (desc), ignoring exclude_rows."""
    ts = v[trow]
    higher = int((v > ts).sum())
    for r in exclude_rows:
        if r != trow and v[r] > ts:
            higher -= 1
    return higher


def merge_rank(v_sharp, v_warm, trow, exclude, cap=400):
    """Exact 0-based rank of trow in the alternating dedup interleave of the two
    descending orders (sharp first). None past cap (contribution ~0 to MRR)."""
    os_, ow = np.argsort(-v_sharp), np.argsort(-v_warm)
    seen = set(); pos = 0; i = j = 0
    excl = set(int(e) for e in exclude)
    for _ in range(2 * cap + len(excl) + 4):
        for order, idx in ((os_, "i"), (ow, "j")):
            k = i if idx == "i" else j
            while k < len(order) and (int(order[k]) in seen or int(order[k]) in excl
                                      or not np.isfinite((v_sharp if idx == "i" else v_warm)[int(order[k])])):
                k += 1
            if idx == "i":
                i = k
            else:
                j = k
            if k >= len(order):
                continue
            row = int(order[k])
            if idx == "i":
                i = k + 1
            else:
                j = k + 1
            seen.add(row)
            if row == trow:
                return pos
            pos += 1
            if pos > cap:
                return None
    return None


def main():
    sample, folds = 120, 5
    a = sys.argv[1:]
    for i, x in enumerate(a):
        if x == "--sample" and i + 1 < len(a): sample = int(a[i + 1])
        if x == "--folds" and i + 1 < len(a): folds = int(a[i + 1])

    emb = sr._load_embeddings()
    if not emb:
        print("no embedding sidecar - eval cannot run"); return 1
    sr._load_embeddings = lambda: emb
    idx_e, mat, bias = emb
    inv_rows = {i: s for s, i in idx_e.items()}
    # the matrix can hold rows not in the active slug map (publication masks); rank
    # only among active rows or a dead row can crowd out real ones (KeyError'd run 1)
    act_mask = np.zeros(mat.shape[0], dtype=bool)
    act_mask[[int(i) for i in idx_e.values()]] = True
    print("embeddings: %d vectors (%d matrix rows), bias %.3f"
          % (len(idx_e), mat.shape[0], bias))

    real = ED.load_real()
    idx = B._archived_index_lines(); idx.update(sr._index_lines())
    wiki = ED.wiki_pairs(real)
    bm = BM25()
    print("wiki-links: %d pairs" % len(wiki))

    random.seed(11)
    order = list(range(len(wiki))); random.shuffle(order)
    fold_of = {order[i]: i % folds for i in range(len(wiki))}

    # ---------- component level ----------
    ALLN = NAMES + list(ANNEAL)
    tot = {n: dict(h10=0, h25=0, mrr=0.0) for n in ALLN}
    hard = {n: dict(h10=0, h25=0, mrr=0.0) for n in ALLN}
    per_pair = []            # (fold, is_hard, {name: mrr_contribution})
    strat = []               # (max_cos_target_to_seeds, contrib) for hard pairs
    n_all = n_hard = 0
    seeds_cache = {}
    bmtop_cache = {}
    for k, (A, Bt) in enumerate(wiki):
        if not idx.get(A):
            continue
        if A not in seeds_cache:
            seeds_cache[A] = seed_strengths(A, idx)
        seeds, _lex = seeds_cache[A]
        pairs = [(s, w) for s, w in seeds.items() if s in idx_e]
        if not pairs or Bt not in idx_e:
            continue
        rows = [idx_e[s] for s, _ in pairs]
        strengths = [w for _, w in pairs]
        variants = component_scores(mat, rows, strengths)
        for v in variants.values():
            v[~act_mask] = -np.inf
        if A not in bmtop_cache:
            line = idx.get(A, "") + " " + A
            bmtop_cache[A] = {s for s, _ in bm.score(line, 40)}
        is_hard = (Bt not in seeds) and (Bt not in bmtop_cache[A])
        n_all += 1
        n_hard += 1 if is_hard else 0
        excl = [idx_e[A]] if A in idx_e else []
        v_sharp = zmask(variants["lse0.05"], act_mask)
        v_warm = zmask(variants["wcentroid"], act_mask)
        variants["anneal_maxz"] = np.maximum(v_sharp, v_warm)
        contrib = {}
        for n in ALLN:
            if n == "anneal_merge":
                r = merge_rank(v_sharp, v_warm, idx_e[Bt], excl)
            else:
                r = rank_in_vector(variants[n], idx_e[Bt], excl)
            contrib[n] = 0.0 if r is None else 1.0 / (r + 1)
            for d, use in ((tot[n], True), (hard[n], is_hard)):
                if not use:
                    continue
                if r is not None and r < 10: d["h10"] += 1
                if r is not None and r < 25: d["h25"] += 1
                d["mrr"] += contrib[n]
        per_pair.append((fold_of[k], is_hard, contrib))
        if is_hard:
            strat.append((float((mat[rows] @ mat[idx_e[Bt]]).max()), contrib))

    def rep(d, n):
        n = max(1, n)
        return d["h10"] / n, d["h25"] / n, d["mrr"] / n

    print("\nCOMPONENT level - rank the target among all %d embedded memories" % len(idx_e))
    print("  all pairs (n=%d):" % n_all)
    for n in ALLN:
        print("    %-12s hit@10 %.3f  hit@25 %.3f  MRR %.4f" % ((n,) + rep(tot[n], n_all)))
    print("  semantically-hard subset - target not a seed, not in bm25 top-40 (n=%d):" % n_hard)
    for n in ALLN:
        print("    %-12s hit@10 %.3f  hit@25 %.3f  MRR %.4f" % ((n,) + rep(hard[n], n_hard)))

    print("\n  per-fold MRR deltas against the live baseline (wcentroid):")
    for label, subset in (("all", None), ("hard", True)):
        for n in ("anneal_maxz", "anneal_merge", "lse0.05"):
            fd = defaultdict(lambda: [0.0, 0])
            for f, is_h, contrib in per_pair:
                if subset and not is_h:
                    continue
                fd[f][0] += contrib[n] - contrib["wcentroid"]
                fd[f][1] += 1
            wins = sum(1 for f, (d, c) in fd.items() if d > 0)
            print("  %s (%s - wcentroid): %s  [positive %d/%d]" % (
                label, n, "  ".join("f%d %+.4f" % (f, d / max(1, c))
                                    for f, (d, c) in sorted(fd.items())), wins, len(fd)))

    # regime separation by construction: hard pairs stratified by how embedding-near
    # the target is to its closest seed (quartiles of max-cos)
    if strat:
        mcs = sorted(m for m, _ in strat)
        qs = [mcs[int(len(mcs) * q)] for q in (0.25, 0.50, 0.75)]
        print("\n  hard subset stratified by target-to-nearest-seed cosine "
              "(quartile cuts %.3f/%.3f/%.3f):" % tuple(qs))
        for qi in range(4):
            lo = mcs[0] if qi == 0 else qs[qi - 1]
            hi = qs[qi] if qi < 3 else mcs[-1] + 1e-9
            rows_q = [c for m, c in strat if lo <= m < hi] if qi < 3 else \
                     [c for m, c in strat if m >= qs[2]]
            if not rows_q:
                continue
            line = "    Q%d (n=%d, embedding-%s): " % (
                qi + 1, len(rows_q), "far" if qi == 0 else ("near" if qi == 3 else "mid"))
            line += "  ".join("%s %.4f" % (n, sum(c[n] for c in rows_q) / len(rows_q))
                              for n in ("centroid", "wcentroid", "lse0.05", "lse0.20",
                                        "anneal_maxz", "anneal_merge"))
            print(line)

    challenger = "anneal_maxz"     # the pre-registered ship candidate

    # ---------- pipeline level ----------
    base = ED.base_non_wiki_edges(real)
    wiki_edges = [(a, b, ED.W_WIKI) for a, b in wiki]

    def semantic_variant(seeds, which):
        """Mirror B._semantic_scores' shape (top-256, calibrated) with a swapped scorer."""
        pairs = [(s, w) for s, w in seeds.items() if s in idx_e]
        if not pairs:
            return {}
        rows = [idx_e[s] for s, _ in pairs]
        if which == "anneal_maxz":
            two = component_scores(mat, rows, [w for _, w in pairs])
            v = np.maximum(zmask(two["lse0.05"], act_mask), zmask(two["wcentroid"], act_mask))
            top = np.argsort(-v)[:256]
            fin = [int(i) for i in top if np.isfinite(v[int(i)])]
            if not fin:
                return {}
            vmin, vmax = float(v[fin[-1]]), float(v[fin[0]])
            span = (vmax - vmin) or 1.0
            # display map: monotone min-max over the surfaced block (z-scores are not cosines)
            return {inv_rows[i]: 0.05 + 0.95 * (float(v[i]) - vmin) / span for i in fin}
        v = component_scores(mat, rows, [w for _, w in pairs], which)[which]
        v = v.copy(); v[~act_mask] = -np.inf
        out = {}
        for i in np.argsort(-v)[:256]:
            if not np.isfinite(v[int(i)]):
                break
            out[inv_rows[int(i)]] = max(0.0, (float(v[int(i)]) - bias) / (1.0 - bias))
        return out

    bm_rank_cache = {}

    def bm25first_rank(A, seeds, adj, deg, which, query=None):
        act = {}
        if adj:
            act, _ = sr._spread(seeds, adj, deg)
        sem = semantic_variant(seeds, which) if which else B._semantic_scores(seeds)
        amax = max(act.values()) if act else 1.0
        cand = set(list(seeds) + list(act) + list(sem))
        q = query if query is not None else (idx.get(A, "") + " " + A)
        if q not in bm_rank_cache:
            bm_rank_cache[q] = [s for s, _ in bm.score(q, 40)]
        top = [s for s in bm_rank_cache[q] if s != A]
        seen = set(top)
        gtail = sorted((((act.get(s, 0.0) / amax), s) for s in cand
                        if s not in seen and s not in sr.SKIP and s != A
                        and act.get(s, 0.0) > 0), reverse=True)
        seen.update(s for _, s in gtail)
        stail = sorted(((sem.get(s, 0.0), s) for s in cand
                        if s not in seen and s not in sr.SKIP and s != A
                        and sem.get(s, 0.0) > 0), reverse=True)
        return top + [s for _, s in gtail] + [s for _, s in stail]

    pipe_variants = [None] + sorted({challenger, "wcentroid"})
    res = {w: dict(h10=0, h25=0, mrr=0.0, n=0) for w in pipe_variants}
    pf = {w: defaultdict(lambda: [0.0, 0]) for w in pipe_variants}
    for f in range(folds):
        held = [k for k in range(len(wiki)) if fold_of[k] == f]
        keep = [wiki_edges[k] for k in range(len(wiki)) if fold_of[k] != f]
        adj, deg = ED.to_adj(base + keep)
        random.shuffle(held)
        for k in held[:sample]:
            A, Bt = wiki[k]
            if not idx.get(A):
                continue
            seeds, _ = seeds_cache.get(A) or seed_strengths(A, idx)
            if not seeds:
                continue
            for w in pipe_variants:
                ranked = bm25first_rank(A, seeds, adj, deg, w)
                pos = {s: i for i, s in enumerate(ranked)}
                d = res[w]; d["n"] += 1
                i = pos.get(Bt)
                m = 0.0
                if i is not None:
                    if i < 10: d["h10"] += 1
                    if i < 25: d["h25"] += 1
                    m = 1.0 / (i + 1)
                    d["mrr"] += m
                pf[w][f][0] += m
                pf[w][f][1] += 1

    print("\nPIPELINE level - live bm25-first architecture, semantic component swapped")
    for w in pipe_variants:
        d = res[w]; n = max(1, d["n"])
        print("  %-10s hit@10 %.3f  hit@25 %.3f  MRR %.4f  (n=%d)" %
              ("live" if w is None else w, d["h10"] / n, d["h25"] / n, d["mrr"] / n, d["n"]))
    print("  per-fold pipeline MRR delta vs live:")
    for f in range(folds):
        dl, nl = pf[None][f]
        if not nl:
            continue
        parts = []
        for w in pipe_variants[1:]:
            dc, nc = pf[w][f]
            if nc:
                parts.append("%s %+.4f" % (w, dc / nc - dl / nl))
        print("    f%d  live %.4f  %s" % (f, dl / nl, "  ".join(parts)))

    # ---------- positive controls ----------
    CONTROLS = [
        ("flashing terminal windows scheduled task", "no_flashing_terminal_windows_2026_06_07"),
        ("never start the local protected production stack", "never_start_local_privileged_stack"),
        ("research writing human voice no AI tells", "research_writing_human_voice_2026_06_10"),
        ("check the brain before you simulate", "scar_check_the_brain_before_you_simulate_2026_07_08"),
    ]
    print("\nPOSITIVE CONTROLS - variant must not lose a memory live finds in the top 8")
    adj, deg = sr._load_graph()

    def control_rank(query, target, which):
        words = sr._words(query)
        seeds, _ = sr._seeds_from_prompt(words, idx)
        if not seeds:
            return None
        ranked = bm25first_rank("", seeds, adj, deg, which, query=query)
        pos = {s: i for i, s in enumerate(ranked)}
        return pos.get(target)

    ok = True
    for query, target in CONTROLS:
        r_live = control_rank(query, target, None)
        r_new = control_rank(query, target, challenger)
        live_hit = r_live is not None and r_live < 8
        new_hit = r_new is not None and r_new < 8
        if live_hit and not new_hit:
            status = "REGRESSION"; ok = False
        elif not live_hit:
            status = "weak (live also misses)"
        else:
            status = "ok"
        print("  %-46s live %-5s new %-5s %s" %
              (query[:46], r_live, r_new, status))
    print("\ncontrols: %s" % ("ALL GREEN" if ok else "REGRESSION - do not ship"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
