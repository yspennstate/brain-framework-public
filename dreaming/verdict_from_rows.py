"""Compute the S-vs-recall partial-correlation VERDICT from the box-completed rows
(eval_S_rows.npy, cols S/n_eff/rank/is_seed), reproducing eval_S_v2_partial.main's verdict
block verbatim, plus a bootstrap CI on the partial S coefficient (break-it-before-believe-it).
No embeddings, no re-run - the heavy row generation already finished on the box at 06:21 UTC."""
import os
import numpy as np

ROWS = os.path.expanduser("~/AppData/Local/Temp/claude/{{BRAIN_SLUG}}/731a0c26-f54e-48ff-bc32-a9f1d4cb4d97/scratchpad/eval_S_rows.npy")


def _z(x):
    x = np.asarray(x, dtype=float)
    return (x - x.mean()) / (x.std() + 1e-9)


def _lpm(y, cols):
    X = np.column_stack([np.ones(len(y))] + [_z(c) for c in cols])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    yhat = X @ beta
    ss_res = float(((y - yhat) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum()) + 1e-12
    return beta, 1.0 - ss_res / ss_tot


arr = np.load(ROWS)
print("rows shape", arr.shape, "(cols: S, n_eff, rank, is_seed)")
print("is_seed counts:", {int(v): int((arr[:, 3] == v).sum()) for v in np.unique(arr[:, 3])})

nonseed = arr[arr[:, 3] == 0]
S, ne = nonseed[:, 0], nonseed[:, 1]
y = (nonseed[:, 2] <= 10).astype(float)
print(f"\nNON-SEED n={len(y)}  base hit@10={y.mean():.3f}  "
      f"S range [{S.min():.4f},{S.max():.4f}] std {S.std():.4f}  "
      f"n_eff range [{ne.min():.2f},{ne.max():.2f}]")
print(f"corr(S, n_eff) = {np.corrcoef(S, ne)[0,1]:+.3f}   (if ~0, S and breadth are independent)")

b_s, r2_s = _lpm(y, [S])
b_n, r2_n = _lpm(y, [ne])
b_b, r2_b = _lpm(y, [S, ne])
print(f"\nS alone:        hit ~ {b_s[0]:+.3f} {b_s[1]:+.3f}*zS                 R2={r2_s:.4f}")
print(f"n_eff alone:    hit ~ {b_n[0]:+.3f} {b_n[1]:+.3f}*zNeff              R2={r2_n:.4f}")
print(f"both (partial): hit ~ {b_b[0]:+.3f} {b_b[1]:+.3f}*zS {b_b[2]:+.3f}*zNeff  R2={r2_b:.4f}")

s_uni, s_par, n_par = b_s[1], b_b[1], b_b[2]
survives = (s_par < 0 and abs(s_par) > 0.3 * abs(n_par) + 1e-9)
print("\nVERDICT (pre-registered): S's coefficient goes "
      f"{s_uni:+.3f} (alone) -> {s_par:+.3f} (controlling n_eff). "
      + ("S SURVIVES as signal beyond breadth" if survives
         else "S COLLAPSES -> it was breadth (retire like n_eff)")
      + ". Negative coef = higher spread predicts lower recall (concentration direction).")

# break-it: bootstrap CI on the partial S coefficient (is it robustly < 0, or noise?)
rng = np.random.default_rng(12345)
N = len(y)
boot = []
for _ in range(2000):
    ix = rng.integers(0, N, N)
    bb, _ = _lpm(y[ix], [S[ix], ne[ix]])
    boot.append(bb[1])
boot = np.array(boot)
lo, hi = np.percentile(boot, [2.5, 97.5])
print(f"\nbootstrap (2000x) partial zS coef: mean {boot.mean():+.4f}  95% CI [{lo:+.4f}, {hi:+.4f}]  "
      f"P(coef<0)={float((boot<0).mean()):.3f}")
print("robust negative" if hi < 0 else ("robust positive" if lo > 0 else "CI straddles 0 - not distinguishable from noise"))
