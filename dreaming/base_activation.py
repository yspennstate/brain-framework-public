#!/usr/bin/env python3
"""base_activation.py - the USAGE signal the brain was missing, and its causal validation.

The retrieval-trace validity check (see brain_1000_ways_and_two_builds_2026_07_08) found the gap: salience
scores affective VIVIDNESS and is mildly ANTI-correlated with how often a memory is actually retrieved
(the most-read memories are low-charge workhorses); only the binary consciousness tier captures usage at
all. So the honest move is not to unify the signals (they measure different axes) but to ADD the missing
one: a continuous usage score.

ACT-R base-level activation (Anderson & Schooler 1991; the rational analysis of memory): a memory's
availability rises with the RECENCY and FREQUENCY of its past retrievals,
    B_i(T) = ln( sum_j (T - t_j)^(-d) )   over retrievals t_j < T, decay d ~ 0.5,
which is the log-odds that an item is needed now given how human environments actually re-present
information. Computed here from retrievals.tsv (build_retrieval_trace.py), written to base_activation.tsv.

And it is VALIDATED, not asserted: `--eval` does a CAUSAL split - compute B from reads strictly before a
split time T, then measure how well B(T) predicts which memories are re-read in (T, T_end] - and compares
it to the recency-only and salience baselines by AUROC. No peeking past T (the lookahead scar). Honest
about the thin trace: it reports n and treats a small test set as provisional.

Read-only over the trace + salience.tsv. Usage:  python base_activation.py [--eval] [--d 0.5]
"""
import os, io, sys, math
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
TRACE = os.path.join(HERE, "retrievals.tsv")
SAL = os.path.join(HERE, "salience.tsv")
OUT = os.path.join(HERE, "base_activation.tsv")
DAY = 86400.0


def _load_trace():
    ev = []  # (epoch, slug)
    try:
        for i, ln in enumerate(io.open(TRACE, encoding="utf-8", errors="replace")):
            if i == 0:
                continue
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 2:
                try:
                    ev.append((float(p[1]), p[0]))
                except Exception:
                    pass
    except Exception:
        pass
    return sorted(ev)


def base_activation(ev, T, d=0.5):
    """B_i(T) from retrievals strictly before T (causal). Returns {slug: B}."""
    times = defaultdict(list)
    for ep, slug in ev:
        if ep < T:
            times[slug].append(ep)
    B = {}
    for slug, ts in times.items():
        s = sum(max((T - t) / DAY, 1e-6) ** (-d) for t in ts)
        B[slug] = math.log(s) if s > 0 else None
    return B


def _auroc(scores, labels):
    """AUROC = P(score of a positive > score of a negative). Ties count 0.5."""
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return None
    c = sum((1.0 if p > n else 0.5 if p == n else 0.0) for p in pos for n in neg)
    return c / (len(pos) * len(neg))


def _load_salience():
    sal = {}
    try:
        for i, ln in enumerate(io.open(SAL, encoding="utf-8", errors="replace")):
            if i == 0:
                continue
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 2:
                slug = p[0][:-3] if p[0].endswith(".md") else p[0]
                try:
                    sal[slug] = float(p[1])
                except Exception:
                    pass
    except Exception:
        pass
    return sal


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    d = 0.5
    do_eval = "--eval" in sys.argv[1:]
    a = sys.argv[1:]
    for i, x in enumerate(a):
        if x == "--d" and i + 1 < len(a):
            try:
                d = float(a[i + 1])
            except Exception:
                pass
    ev = _load_trace()
    if len(ev) < 10:
        print("base_activation: retrieval trace too thin - run build_retrieval_trace.py first.")
        return
    t_end = ev[-1][0]

    # write the current usage signal (B computed to now)
    B_now = base_activation(ev, t_end + 1.0, d)
    try:
        with io.open(OUT, "w", encoding="utf-8") as f:
            f.write("slug\tbase_activation\tn_retrievals\n")
            cnt = defaultdict(int)
            for _, s in ev:
                cnt[s] += 1
            for slug, b in sorted(B_now.items(), key=lambda kv: -(kv[1] if kv[1] is not None else -9)):
                f.write(f"{slug}\t{b:.3f}\t{cnt[slug]}\n")
    except Exception as e:
        print("base_activation: write failed:", e)
        return
    print(f"base_activation: wrote {OUT} ({len(B_now)} memories with >=1 retrieval, decay d={d})")

    if not do_eval:
        print("  (run with --eval for the causal predictive validation)")
        return

    # CAUSAL predictive eval: split at the 70th-percentile event time
    T = ev[int(0.70 * len(ev))][0]
    future = set(s for ep, s in ev if ep >= T)          # ground truth: read in (T, end]
    B = base_activation(ev, T, d)                        # usage signal, causal (reads < T only)
    last = {}
    for ep, s in ev:
        if ep < T:
            last[s] = max(last.get(s, 0), ep)            # recency baseline: time of last read < T
    sal = _load_salience()
    # universe = memories seen at least once before T (the ones B can score); predict re-read after T
    uni = sorted(B.keys())
    if len(uni) < 8:
        print("  eval: too few pre-split memories to judge (provisional).")
        return
    labels = [1 if s in future else 0 for s in uni]
    auc_B = _auroc([B[s] for s in uni], labels)
    auc_rec = _auroc([last.get(s, 0) for s in uni], labels)
    auc_sal = _auroc([sal.get(s, 50.0) for s in uni], labels)
    npos = sum(labels)
    print(f"  CAUSAL eval (split at 70th pct; {len(uni)} pre-split memories, {npos} re-read after):")
    print(f"    AUROC base-activation (usage) = {auc_B:.3f}" if auc_B is not None else "    base: n/a")
    print(f"    AUROC recency-only            = {auc_rec:.3f}" if auc_rec is not None else "    recency: n/a")
    print(f"    AUROC salience                = {auc_sal:.3f}" if auc_sal is not None else "    salience: n/a")
    if auc_B is not None and auc_sal is not None:
        best = max((auc_B, "usage"), (auc_rec or 0, "recency"), (auc_sal, "salience"))
        prov = " (PROVISIONAL - thin trace)" if (npos < 15 or len(uni) < 30) else ""
        print(f"    -> best predictor of re-read: {best[1]}{prov}. If usage wins, it is the missing axis worth wiring.")


if __name__ == "__main__":
    main()
