#!/usr/bin/env python3
"""eval_learned_ranker.py - does a LEARNED ranker beat the hand-tuned deliberate blend (0.625*lex +
0.25*graph + 0.125*sem, x salience)?

VERDICT (2026-07-11): NO - KEEP the hand blend; the ranking investigation is closed. A logistic ranker over
6 features (the blend's 3 + salience + recency + degree) does NOT robustly beat it on held-out wiki-links:
mean MRR 0.097 vs 0.091 (+0.006) but it LOSES fold 1, so no robust win. Recency and degree learn ~0 weight -
they add no signal, so there's nothing to gain by extending brain.recall's feature set. The pre-mortem check
earned its keep: the learned weights are lexical -20 / graph +28 (STABLE across folds), which looks like it
contradicts eval_deliberate_recall (blend sweep: high-lexical is best). It does NOT - it's a MULTICOLLINEARITY
artifact, VERIFIED not asserted: lexical~graph correlation is +0.92, and lexical fit ALONE has coef +12.85
(positively predicts the target) but -22.54 IN THE FULL model - the classic sign-flip a 0.92-correlated
partner induces. So lexical genuinely helps; the negative full-model weight is the logistic offloading lex's
signal onto its graph twin. For RANKING, lexical helps (blend sweep measures this reliably); the collinear
logistic weight-signs mislead. Lesson: on correlated recall features, trust the blend SWEEP (vary one axis,
measure ranking) over a learned model's weight signs. The 3-feature hand blend is near-optimal, and the
ls~gs=0.92 correlation also explains why the exact lex/graph split is not critical (they carry overlapping
signal) - what matters is both present + semantic lower, which the 0.625/0.25/0.125 blend has.
 Tonight (07-11) I hand-tuned that linear blend on held-out wiki-links;
this asks the deeper question: if we LEARN the weights over RICHER features (add recency + candidate degree),
does a logistic model win? A logistic ranker is still a weighted sum, so a win is directly deployable in
brain.recall (just more terms). A loss confirms the 3-feature hand blend is near-optimal - a real close to
the ranking investigation either way.

Protocol (reuses eval_deliberate_recall): held-out wiki-links, leave-one-FOLD-out CV. For each fold, features
for its held-out (query A -> target B) pairs are computed on THAT fold's graph (which excludes the fold's own
links, so no target leakage). Each (pair, candidate) is a row; label = 1 iff candidate is the target. Train a
pointwise LogisticRegression on the other folds, rank the test fold's candidates by predicted P(target),
score MRR/hit@k, and compare to the hand blend on the SAME candidates. Features per candidate: ls, gs, ss
(the blend's three), salience, recency (1/(1+age_days)), log1p(degree). Read-only. Usage:
   python eval_learned_ranker.py [--folds 3] [--sample 120]
"""
import io, os, re, sys, math
from collections import defaultdict
sys.path.insert(0, os.path.expanduser(r"~/.claude/dreaming"))
import spread_recall as sr
import brain as B
import eval_deliberate_recall as ED
import numpy as np

TODAY = None  # stamped from newest memory date below (Date.now-free)
DATE = re.compile(r"_(\d{4})_(\d{2})_(\d{2})")


def age_days(slug, ref):
    m = DATE.search(slug)
    if not m or ref is None:
        return 60.0
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    from datetime import date
    try:
        return max(0.0, (ref - date(y, mo, d)).days)
    except Exception:
        return 60.0


def main():
    folds = 3
    sample = 120
    a = sys.argv[1:]
    for i, x in enumerate(a):
        if x == "--folds" and i + 1 < len(a): folds = int(a[i + 1])
        if x == "--sample" and i + 1 < len(a): sample = int(a[i + 1])

    _emb = sr._load_embeddings()
    sr._load_embeddings = lambda: _emb
    print("embeddings:", "cached %d" % len(_emb[0]) if _emb else "ABSENT")

    real = ED.load_real()
    idx = B._archived_index_lines(); idx.update(sr._index_lines())
    sal = sr._load_salience(); tier = B._tiers()
    base = ED.base_non_wiki_edges(real)
    wiki = ED.wiki_pairs(real)
    wiki_edges_all = [(x, y, ED.W_WIKI) for x, y in wiki]

    # newest memory date as the recency reference (no Date.now; derive from the corpus itself)
    from datetime import date
    newest = None
    for x, y in wiki:
        for s in (x, y):
            m = DATE.search(s)
            if m:
                dt = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                if newest is None or dt > newest:
                    newest = dt
    ref = newest
    print(f"{len(wiki)} wiki-links; recency ref = {ref}; {folds}-fold, sample {sample}/fold")

    # deterministic fold assignment (index-based; no RNG)
    fold_of = {k: k % folds for k in range(len(wiki))}

    # per fold: extract (X rows, labels, group id) for held-out pairs on that fold's graph
    FEATS = ["ls", "gs", "ss", "sal", "recency", "logdeg"]
    fold_rows = {f: {"X": [], "y": [], "grp": []} for f in range(folds)}
    gid = 0
    for f in range(folds):
        held = [k for k in range(len(wiki)) if fold_of[k] == f]
        held_set = set(held)
        keep = [wiki_edges_all[k] for k in range(len(wiki)) if k not in held_set]
        adj, deg = ED.to_adj(base + keep)
        take = held[:sample]
        for k in take:
            A, Bt = wiki[k]
            if not idx.get(A):
                continue
            comps = ED.pair_components(A, idx, adj, deg, sal, tier)
            if not comps:
                continue
            for ls, gs, ss, salb, slug in comps:
                m = DATE.search(slug)
                rec = 1.0 / (1.0 + age_days(slug, ref))
                dg = len(adj.get(slug, ()))
                row = [ls, gs, ss, sal.get(slug, 50.0) / 100.0, rec, math.log1p(dg)]
                fold_rows[f]["X"].append(row)
                fold_rows[f]["y"].append(1 if slug == Bt else 0)
                fold_rows[f]["grp"].append(gid)
            gid += 1

    from sklearn.linear_model import LogisticRegression

    def rank_scores(scores, groups, labels):
        by = defaultdict(list)
        for s, g, y in zip(scores, groups, labels):
            by[g].append((s, y))
        h10 = h25 = mrr = n = 0
        for g, lst in by.items():
            lst.sort(key=lambda t: -t[0])
            for i, (s, y) in enumerate(lst):
                if y == 1:
                    n += 1
                    if i < 10: h10 += 1
                    if i < 25: h25 += 1
                    mrr += 1.0 / (i + 1)
        return (h10 / n, h25 / n, mrr / n, n) if n else (0, 0, 0, 0)

    # STALE LABEL CORRECTED 2026-08-22 (session 5e55000b): the hand-tuned deliberate blend this
    # compares against stopped shipping on 2026-07-15, when brain.py went BM25-FIRST (line 1160,
    # OQ#1164); it now runs only under BRAIN_RECALL_BLEND=1. A learned ranker beating CUR has beaten
    # the pre-07-15 ranker, not the live tool. To ask the live question, score against the shipped
    # path (import brain; brain.recall) rather than against this constant.
    CUR = (0.625, 0.25, 0.125)   # pre-2026-07-15 deployed blend; NOT the current shipped ranker
    learned, hand = [], []
    for tf in range(folds):
        Xtr = [r for f in range(folds) if f != tf for r in fold_rows[f]["X"]]
        ytr = [r for f in range(folds) if f != tf for r in fold_rows[f]["y"]]
        Xte = fold_rows[tf]["X"]; yte = fold_rows[tf]["y"]; gte = fold_rows[tf]["grp"]
        if not Xte or sum(ytr) == 0:
            continue
        clf = LogisticRegression(max_iter=1000, C=1.0, class_weight="balanced")
        clf.fit(np.array(Xtr), np.array(ytr))
        pl = clf.predict_proba(np.array(Xte))[:, 1]
        # hand blend on the same rows: (wl*ls + wg*gs + ws*ss) * salb-ish (sal feature stands in for salb)
        ph = [CUR[0] * r[0] + CUR[1] * r[1] + CUR[2] * r[2] for r in Xte]
        ph = [p * (0.9 + 0.2 * r[3]) for p, r in zip(ph, Xte)]   # reapply the gentle salience tilt
        learned.append(rank_scores(pl, gte, yte))
        hand.append(rank_scores(ph, gte, yte))
        w = dict(zip(FEATS, clf.coef_[0]))
        print(f"  fold {tf}: learned MRR {learned[-1][2]:.3f}  hand MRR {hand[-1][2]:.3f}  "
              f"| weights " + " ".join(f"{k}={v:+.2f}" for k, v in w.items()))

    # VERIFY the multicollinearity explanation for the lex-negative weight (don't just assert it):
    # if lexical is POSITIVE fit alone but NEGATIVE in the full model, that's the collinearity sign-flip.
    allX = np.array([r for f in range(folds) for r in fold_rows[f]["X"]])
    allY = np.array([r for f in range(folds) for r in fold_rows[f]["y"]])
    if len(allX) > 100 and allY.sum() > 5:
        def corr(i, j):
            return float(np.corrcoef(allX[:, i], allX[:, j])[0, 1])
        print(f"\nfeature corr: ls~gs {corr(0,1):+.2f}  ls~ss {corr(0,2):+.2f}  gs~ss {corr(1,2):+.2f}")
        c_ls = LogisticRegression(max_iter=1000, class_weight="balanced").fit(allX[:, [0]], allY).coef_[0][0]
        c_full = LogisticRegression(max_iter=1000, class_weight="balanced").fit(allX, allY).coef_[0][0]
        print(f"lexical coef ALONE {c_ls:+.2f}  vs IN FULL model {c_full:+.2f}  "
              f"({'collinearity sign-flip CONFIRMED' if c_ls > 0 > c_full else 'not the expected flip - reconsider'})")

    if not learned:
        print("no usable folds"); return
    lm = sum(x[2] for x in learned) / len(learned)
    hm = sum(x[2] for x in hand) / len(hand)
    lh10 = sum(x[0] for x in learned) / len(learned)
    hh10 = sum(x[0] for x in hand) / len(hand)
    print(f"\nLEARNED ranker: mean MRR {lm:.4f}  hit@10 {lh10:.3f}")
    print(f"HAND blend    : mean MRR {hm:.4f}  hit@10 {hh10:.3f}")
    robust = all(learned[i][2] > hand[i][2] + 1e-6 for i in range(len(learned)))
    print(f"delta MRR {lm-hm:+.4f}; learned beats hand on ALL folds: {robust}")
    if robust and lm - hm > 0.005:
        print("  -> a learned/extended blend is worth deploying (recency+degree add signal). Check weights are stable.")
    else:
        print("  -> the hand-tuned 3-feature blend is near-optimal; richer features + learning don't robustly beat it. KEEP it.")


if __name__ == "__main__":
    main()
