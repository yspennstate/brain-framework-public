#!/usr/bin/env python3
"""learn_probe.py - would a LEARNED model beat the graph-prior + calibration? An honest head-to-head.

The graph model builds each reflex's token weights by diffusing over the association graph (a structured
PRIOR), then calibrates thresholds on plentiful typical-action negatives. The alternative is to LEARN the
token weights from labelled data. This tests whether that helps, given the actual data regime: only 52
labelled corrections (prose, and leaky - the reflexes were written from them), against 700 typical actions.

Task: separate "a caution should fire" (the 52 mistakes) from "benign" (700 typical actions), using bag-of-
token features. Compare, at a matched false-positive rate on the typical negatives:
  - GRAPH: fire if any calibrated reflex fires (fire_graph_cal).
  - LEARNED: L2 logistic regression over token counts, honest 5-fold cross_val_predict (never scores its
    own training rows), threshold swept to the same FP.

Hypothesis: with ~52 sparse positives and hundreds of features, the learned model overfits / collapses and
does NOT beat the structured prior - i.e. the graph-prior + FP-budget calibration is the right design for
this regime. Small data, trivial compute. Prints the verdict.
"""
import os, io, sys, re
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eval_real as E


def main():
    posM = E.positives_mistakes()                      # [(text, expected_reflexes)]
    typ = [r for r in E.load_typical()]                # benign
    pos_txt = [t for t, _ in posM]
    neg_txt = [r["text"] for r in typ]
    texts = pos_txt + neg_txt
    y = np.array([1] * len(pos_txt) + [0] * len(neg_txt))

    # bag-of-token features (same tokenizer the graph model uses), min document frequency to bound dims
    from collections import Counter
    docs = [set(E.toks(t)) for t in texts]
    df = Counter()
    for d in docs:
        for w in d:
            df[w] += 1
    vocab = sorted([w for w, c in df.items() if c >= 2])
    vindex = {w: i for i, w in enumerate(vocab)}
    X = np.zeros((len(texts), len(vocab)), dtype=float)
    for i, d in enumerate(docs):
        for w in d:
            j = vindex.get(w)
            if j is not None:
                X[i, j] = 1.0
    print("examples: %d pos (mistakes), %d neg (typical) | features: %d" % (len(pos_txt), len(neg_txt), len(vocab)))

    # LEARNED: honest out-of-fold probabilities, then threshold to match the graph's FP
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_predict, StratifiedKFold
    best = None
    for C in (0.02, 0.1, 0.5):
        clf = LogisticRegression(C=C, class_weight="balanced", max_iter=2000)
        skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=0)
        proba = cross_val_predict(clf, X, y, cv=skf, method="predict_proba")[:, 1]
        # graph operating points (calibrated-alone) to match FP against
        for target_fp in (0.14, 0.30, 0.46):
            # choose threshold on NEG proba to hit target_fp, read recall on POS
            negp = np.sort(proba[y == 0])[::-1]
            k = min(len(negp) - 1, int(target_fp * len(negp)))
            thr = negp[k]
            rec = float(((proba[y == 1] >= thr).mean()))
            fp = float(((proba[y == 0] >= thr).mean()))
            row = (C, target_fp, rec, fp)
            if best is None or (abs(fp - 0.14) < 0.03 and rec > best[2]):
                best = row

    # GRAPH operating points (from the calibrated model), recall on the SAME mistakes / FP on the SAME typical
    cal = E.load_model(os.path.join(os.path.dirname(os.path.abspath(__file__)), "reflex_anchors_cal.json"))
    g_rec = np.mean([1.0 if (exp & E.fire_graph_cal(t, cal)) else 0.0 for t, exp in posM])
    g_fp = np.mean([1.0 if E.fire_graph_cal(r["text"], cal) else 0.0 for r in typ])

    print("\nGRAPH (calibrated, prior-based):  recall=%.2f  FP=%.2f" % (g_rec, g_fp))
    print("LEARNED (L2 logistic, 5-fold OOF), recall at matched FP:")
    for C in (0.02, 0.1, 0.5):
        clf = LogisticRegression(C=C, class_weight="balanced", max_iter=2000)
        skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=0)
        proba = cross_val_predict(clf, X, y, cv=skf, method="predict_proba")[:, 1]
        negp = np.sort(proba[y == 0])[::-1]
        thr = negp[min(len(negp) - 1, int(g_fp * len(negp)))]
        rec = float((proba[y == 1] >= thr).mean())
        print("  C=%-4s : recall=%.2f at FP=%.2f (matched to graph)" % (C, rec, g_fp))
    print("\nVerdict: if learned recall <= graph recall (%.2f) at the same FP, the structured graph prior"
          " wins for this sparse/leaky regime - which is the design in use." % g_rec)


if __name__ == "__main__":
    main()
