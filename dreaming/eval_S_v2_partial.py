"""S vs recall, the DEFINITIVE disentangling: partial correlation, not just a band-split.

eval_S_predicts_recall.py answers "does S predict hit@10 within n_eff bands" by splitting.
That is coarse (a band still has residual n_eff variance, and if S co-varies with n_eff the
within-band S range shrinks, losing power). The clean test of "does S add signal BEYOND query
breadth" is a partial correlation: regress the binary hit@10 on standardized S and n_eff
together (a linear probability model), and read S's PARTIAL coefficient. If S alone predicts
but its partial coefficient collapses toward 0 once n_eff is in the model, S was breadth in
disguise; if the partial S coefficient stays clearly negative (higher spread -> lower recall)
and comparable to n_eff's, S is a genuine concentration signal n_eff is not.

Also saves the raw per-query (S, n_eff, rank, is_seed) to eval_S_rows.npy so any further
analysis needs no 36-min re-run. Reuses eval_S_predicts_recall.candidate_S (verified API).
Writes eval_S_rows.npy only.
"""

from __future__ import annotations
import os

import sys

sys.path.insert(0, os.path.expanduser("~/.claude/dreaming"))
import numpy as np
import brain as B
import spread_recall as sr
import eval_deliberate_recall as ED
import eval_S_predicts_recall as E


def _z(x):
    x = np.asarray(x, dtype=float)
    return (x - x.mean()) / (x.std() + 1e-9)


def _lpm(y, cols):
    """Linear probability model y ~ 1 + cols (standardized). Returns coef dict + R^2."""
    X = np.column_stack([np.ones(len(y))] + [_z(c) for c in cols])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    yhat = X @ beta
    ss_res = float(((y - yhat) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum()) + 1e-12
    return beta, 1.0 - ss_res / ss_tot


def main() -> int:
    real = ED.load_real()
    wiki = ED.wiki_pairs(real)
    idx = B._archived_index_lines(); idx.update(sr._index_lines())
    emb = sr._load_embeddings()
    if not emb:
        print("no embedding sidecar; cannot run"); return 1
    idx_e, mat, _bias = emb

    rows = []  # (S, n_eff, rank, is_seed)
    skipped = 0
    for A, Bt in wiki:
        seeds, _ = sr._seeds_from_prompt(sr._words(idx.get(A, "") + " " + A), idx)
        if not seeds:
            skipped += 1; continue
        diag = {}
        sem = B._semantic_scores(seeds, diag=diag)
        n_eff = diag.get("n_eff")
        if n_eff is None or not sem:
            skipped += 1; continue
        S = E.candidate_S(sem, idx_e, mat, A)
        if S is None:
            skipped += 1; continue
        scored = sorted(((s, sc) for s, sc in sem.items() if s != A), key=lambda t: -t[1])
        rank = next((i + 1 for i, (s, _) in enumerate(scored) if s == Bt), None)
        if rank is None:
            skipped += 1; continue
        rows.append((float(S), float(n_eff), int(rank), 1 if Bt in seeds else 0))

    n = len(rows)
    print(f"wiki pairs: {len(wiki)}; scored {n}; skipped {skipped}", flush=True)
    if n < 60:
        print("too few scored pairs"); return 0

    arr = np.array(rows, dtype=float)
    np.save(os.path.expanduser("~/.claude/dreaming/eval_S_rows.npy"), arr)
    print("saved raw rows -> eval_S_rows.npy", flush=True)

    nonseed = arr[arr[:, 3] == 0]
    S, ne = nonseed[:, 0], nonseed[:, 1]
    y = (nonseed[:, 2] <= 10).astype(float)
    print(f"\nNON-SEED n={len(y)}  base hit@10={y.mean():.3f}  "
          f"S range [{S.min():.4f},{S.max():.4f}] std {S.std():.4f}  "
          f"n_eff range [{ne.min():.2f},{ne.max():.2f}]")
    print(f"corr(S, n_eff) = {np.corrcoef(S, ne)[0,1]:+.3f}   (if ~0, S and breadth are independent)")

    b_s, r2_s = _lpm(y, [S]);            print(f"\nS alone:        hit ~ {b_s[0]:+.3f} {b_s[1]:+.3f}*zS                 R2={r2_s:.4f}")
    b_n, r2_n = _lpm(y, [ne]);           print(f"n_eff alone:    hit ~ {b_n[0]:+.3f} {b_n[1]:+.3f}*zNeff              R2={r2_n:.4f}")
    b_b, r2_b = _lpm(y, [S, ne]);        print(f"both (partial): hit ~ {b_b[0]:+.3f} {b_b[1]:+.3f}*zS {b_b[2]:+.3f}*zNeff  R2={r2_b:.4f}")

    s_uni, s_par = b_s[1], b_b[1]
    print("\nVERDICT (partial): S's coefficient goes "
          f"{s_uni:+.3f} (alone) -> {s_par:+.3f} (controlling n_eff). "
          + ("S SURVIVES as signal beyond breadth" if (s_par < 0 and abs(s_par) > 0.3 * abs(b_b[2]) + 1e-9)
             else "S COLLAPSES -> it was breadth (retire like n_eff)")
          + ". Negative coef = higher spread predicts lower recall, the concentration direction.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
