"""Complete + cross-validate the flag-thread result on the recovered rows (eval_S_rows.npy).
(1) Reproduce the node's binned n_eff hit@10 curve - does the box 06:21 run agree with the node's
    independent 22:38 run (0.391/0.347/0.202/0.156/0.079)? Two runs agreeing = corroboration that
    could have diverged.
(2) Characterize n_eff's real predictive power linearly vs via log(n_eff) (the range is 1..367, so a
    linear coef understates a monotone-but-curved relationship).
(3) Confirm S adds nothing even with log(n_eff) in the model (the verdict, re-checked nonlinearly)."""
import os
import numpy as np

ROWS = os.path.expanduser("~/.claude/dreaming/eval_S_rows.npy")


def _z(x):
    x = np.asarray(x, float)
    return (x - x.mean()) / (x.std() + 1e-9)


def _lpm(y, cols):
    X = np.column_stack([np.ones(len(y))] + [_z(c) for c in cols])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    yhat = X @ beta
    ss_res = float(((y - yhat) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum()) + 1e-12
    return beta, 1.0 - ss_res / ss_tot


arr = np.load(ROWS)
ns = arr[arr[:, 3] == 0]           # non-seed
S, ne = ns[:, 0], ns[:, 1]
y = (ns[:, 2] <= 10).astype(float)
print(f"non-seed n={len(y)}  base hit@10={y.mean():.3f}")

print("\n(1) n_eff binned hit@10 (cross-check vs node's 22:38 run):")
print("   bin        n     hit@10   node's run")
node = {"0-3": 0.391, "3-5": 0.347, "5-10": 0.202, "10-25": 0.156, "25+": 0.079}
edges = [(0, 3), (3, 5), (5, 10), (10, 25), (25, 1e9)]
for lo, hi in edges:
    m = (ne >= lo) & (ne < hi)
    lbl = f"{lo}-{hi}" if hi < 1e9 else f"{int(lo)}+"
    lbl = {"0-3": "0-3", "3-5": "3-5", "5-10": "5-10", "10-25": "10-25", "25+": "25+"}.get(lbl, lbl)
    hitr = y[m].mean() if m.sum() else float("nan")
    print(f"   {lbl:<8} {int(m.sum()):>5}   {hitr:.3f}     {node.get(lbl,'?')}")
mono = all(y[(ne >= a) & (ne < b)].mean() >= y[(ne >= c) & (ne < d)].mean()
           for (a, b), (c, d) in zip(edges[:-1], edges[1:]) if ((ne >= a) & (ne < b)).sum() and ((ne >= c) & (ne < d)).sum())
print(f"   monotone decreasing across bins: {mono}")

print("\n(2) n_eff predictive power, linear vs log:")
b_lin, r2_lin = _lpm(y, [ne])
b_log, r2_log = _lpm(y, [np.log(ne)])
print(f"   linear n_eff : coef {b_lin[1]:+.3f}*zNeff       R2={r2_lin:.4f}")
print(f"   log n_eff    : coef {b_log[1]:+.3f}*z(log neff)  R2={r2_log:.4f}   (higher R2 = the relationship is curved)")

print("\n(3) does S add anything with log(n_eff) in the model? (verdict, nonlinear re-check):")
b_both, r2_both = _lpm(y, [S, np.log(ne)])
print(f"   S + log(n_eff): {b_both[0]:+.3f} {b_both[1]:+.3f}*zS {b_both[2]:+.3f}*z(log neff)  R2={r2_both:.4f}")
print(f"   S's partial coef with log-neff control: {b_both[1]:+.4f}  (was +0.008 with linear n_eff)")
print("   -> " + ("S still adds nothing; verdict robust to the nonlinear control" if abs(b_both[1]) < 0.02
                  else "S coef moved materially - re-examine"))
