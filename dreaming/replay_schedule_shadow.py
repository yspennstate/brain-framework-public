"""SHADOW demonstration of the section-8.2 replay schedule (NOT wired into dream.py).
dream.py's replay is concern-overlap prioritized + unconscious-tier + salience + orphans, but its
AGE handling is only the degraded uniform-over-30d+ fallback - age is never importance-sampled from
the measured recency demand curve. This computes, on the real corpus, what two candidate age-schedules
would prioritize vs the current uniform-old draw, to see whether the age axis would change the pick
meaningfully. Read-only, no live change. One design shown, clearly labeled - the objective (what to
sample toward) is the open theory choice."""
import os, sys, time
import numpy as np
sys.path.insert(0, os.path.expanduser(r"~/.claude/dreaming"))
import salience

TAU_FAST = 1.3  # measured episodic decay (days), recency_kernel_two_scale
now = time.time()
scores = salience.score_corpus()
ages, slugs = [], []
for fn, s in scores.items():
    d = s.get("date")
    if not d or d == "undated":
        continue
    try:
        y, mo, dd = map(int, d.split("-"))
        age = max(0.0, (now - time.mktime((y, mo, dd, 12, 0, 0, 0, 0, -1))) / 86400.0)
    except Exception:
        continue
    ages.append(age); slugs.append(fn[:-3])
ages = np.array(ages)
n = len(ages)
print(f"dated memories: {n}  age range {ages.min():.1f}..{ages.max():.0f} d  median {np.median(ages):.1f} d")

# demand curve R(age): two-scale kernel - episodic head exp(-age/1.3) over a small old-age plateau.
PLATEAU = 0.01
R = np.exp(-ages / TAU_FAST) + PLATEAU
# candidate schedules (weights over memories):
w_current = (ages > 30).astype(float)                 # dream.py fallback: uniform over 30d+ old
w_demand = R                                           # (A) replay what is actively needed (demand-proportional)
# (B) consolidation-boundary: |dR/dage| = R/TAU on the head - peaks where episodic is decaying, the
# episodic->semantic transition SWS is theorized to stabilize (sec 6a: replay at the basin boundary).
w_boundary = np.exp(-ages / TAU_FAST) / TAU_FAST

def age_profile(w, label, k=13):
    w = w / (w.sum() + 1e-12)
    # expected age-bucket mass under this replay weighting
    buckets = [(0, 1), (1, 3), (3, 7), (7, 30), (30, 90), (90, 1e9)]
    print(f"\n{label}: replay mass by age bucket")
    for lo, hi in buckets:
        m = ((ages >= lo) & (ages < hi))
        lbl = f"{lo}-{hi}d" if hi < 1e9 else f"{int(lo)}d+"
        print(f"   {lbl:<8} {100.0*w[m].sum():5.1f}%   ({int(m.sum())} mems)")
    top = np.argsort(-w)[:k]
    print("   top picks:", ", ".join(f"{slugs[i]}({ages[i]:.0f}d)" for i in top[:6]))

# (C) TRANSITION-BAND: a log-Gaussian centered ~5d - the episodic->semantic crossover the 6a schedule
# wants. Params illustrative, NOT tuned; the point is the SHAPE (a mid-age peak a monotone weight cannot make).
la = np.log(ages + 1.0)
w_band = np.exp(-((la - np.log(5.0)) ** 2) / (2 * 0.9 ** 2))

age_profile(w_current, "CURRENT (dream.py age fallback: uniform over 30d+)")
age_profile(w_band, "(C) TRANSITION-BAND (log-Gaussian ~5d, illustrative - the 6a intent, a mid-age peak)")
age_profile(w_demand, "(A) DEMAND-proportional (replay what's actively retrieved)")
age_profile(w_boundary, "(B) CONSOLIDATION-BOUNDARY (replay the episodic->semantic transition, sec 6a)")

# how different is the pick? overlap of top-40 sets
def top_set(w, k=40):
    return set(np.argsort(-w)[:k].tolist())
cur, dem, bnd = top_set(w_current), top_set(w_demand), top_set(w_boundary)
print(f"\ntop-40 overlap: current vs demand {len(cur & dem)}/40 ; current vs boundary {len(cur & bnd)}/40 ;"
      f" demand vs boundary {len(dem & bnd)}/40")
print("read: low overlap with CURRENT = the age axis WOULD change the replay pick materially; "
      "demand vs boundary differ = the objective choice matters and is the open theory question.")
