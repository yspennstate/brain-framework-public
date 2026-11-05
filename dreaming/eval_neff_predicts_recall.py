"""Does effective seed count n_eff predict recall success? A break-it test.

The evidence-mass flag (b_hat) was declined because it conflates few-but-coherent
seeds with incoherent ones; the coherence follow-up was falsified. The corrected lead
(READING_NOTES_20260723.md) is n_eff = (sum p)^2 / sum p^2, the effective seed count.
This tests it at scale on the established wiki-link ground truth, and is built to
REFUTE: if hit@10 does not rise with n_eff, n_eff is dead like coherence.

For each hand-authored wiki pair (A -> B): query = A's index line, target = B.
n_eff comes from the query's seed set (computable at query time, no leakage). Target
component rank is under the SHIPPED strength-weighted semantic scorer, over all
embedded memories minus A. Writes nothing.
"""

from __future__ import annotations
import os

import sys
from collections import defaultdict

sys.path.insert(0, os.path.expanduser("~/.claude/dreaming"))
import numpy as np
import brain as B
import spread_recall as sr
import eval_deliberate_recall as ED


def main() -> int:
    real = ED.load_real()
    wiki = ED.wiki_pairs(real)
    idx = B._archived_index_lines(); idx.update(sr._index_lines())
    emb = sr._load_embeddings()
    if not emb:
        print("no embedding sidecar; cannot run")
        return 1

    rows = []          # (n_eff, target_rank, target_is_seed)
    skipped = 0
    for A, Bt in wiki:
        line = idx.get(A, "")
        words = sr._words(line + " " + A)
        seeds, _lex = sr._seeds_from_prompt(words, idx)
        if not seeds:
            skipped += 1
            continue
        diag = {}
        sem = B._semantic_scores(seeds, diag=diag)   # shipped strength-weighted centroid
        n_eff = diag.get("n_eff")
        if n_eff is None or not sem:
            skipped += 1
            continue
        # component rank of the target among all embedded memories, excluding the query self A
        scored = [(s, sc) for s, sc in sem.items() if s != A]
        scored.sort(key=lambda t: -t[1])
        rank = next((i + 1 for i, (s, _) in enumerate(scored) if s == Bt), None)
        if rank is None:
            skipped += 1
            continue
        rows.append((float(n_eff), rank, Bt in seeds))

    n = len(rows)
    print(f"wiki pairs: {len(wiki)}; scored {n}; skipped {skipped}")
    if n < 30:
        print("too few scored pairs for a stable read")
        return 0

    # Bin by n_eff; report hit@10 (target in top 10) and median target rank per bin.
    bins = [(0, 3), (3, 5), (5, 10), (10, 25), (25, 1e9)]
    print(f"{'n_eff bin':>10} {'n':>4} {'hit@10':>7} {'medrank':>8} {'seed%':>6}")
    hit_by_bin = []
    for lo, hi in bins:
        b = [r for r in rows if lo <= r[0] < hi]
        if not b:
            continue
        hit = sum(1 for _, rank, _ in b if rank <= 10) / len(b)
        medrank = sorted(rank for _, rank, _ in b)[len(b) // 2]
        seedpct = sum(1 for _, _, s in b if s) / len(b)
        hit_by_bin.append((lo, hit, len(b)))
        print(f"{lo:>4}-{hi if hi < 1e9 else 'inf':<5} {len(b):>4} {hit:>7.3f} {medrank:>8} {seedpct:>6.2f}")

    # Break-it: Spearman-ish monotonicity of hit@10 across bins, and the honest
    # confound - target_is_seed rate rises with n_eff too (more seeds = more chance
    # the target IS a seed = trivially rank 1). Recompute hit@10 on NON-seed targets.
    nonseed = [r for r in rows if not r[2]]
    print(f"\nNON-SEED targets only (the flag's real regime), n={len(nonseed)}:")
    print(f"{'n_eff bin':>10} {'n':>4} {'hit@10':>7} {'medrank':>8}")
    ns_hits = []
    for lo, hi in bins:
        b = [r for r in nonseed if lo <= r[0] < hi]
        if not b:
            continue
        hit = sum(1 for _, rank, _ in b if rank <= 10) / len(b)
        medrank = sorted(rank for _, rank, _ in b)[len(b) // 2]
        ns_hits.append((lo, hit, len(b)))
        print(f"{lo:>4}-{hi if hi < 1e9 else 'inf':<5} {len(b):>4} {hit:>7.3f} {medrank:>8}")

    if len(ns_hits) >= 3:
        lo_hit, hi_hit = ns_hits[0][1], ns_hits[-1][1]
        delta = hi_hit - lo_hit                      # signed: >0 rises with n_eff, <0 falls
        seq = [h for _, h, _ in ns_hits]
        mono_down = all(seq[i] >= seq[i + 1] for i in range(len(seq) - 1))
        mono_up = all(seq[i] <= seq[i + 1] for i in range(len(seq) - 1))
        # A relationship in EITHER direction is signal; only |delta| < 0.05 is dead.
        # (The old verdict only asked 'rises?' and mislabeled a strong inverse fall as
        # FLAT/NOISE - the exact silent-false-negative the positive-control rule warns of.)
        if abs(delta) < 0.05:
            tag = "FLAT/NOISE (n_eff dead like coherence)"
        elif delta < 0:
            tag = (f"FALLS with n_eff{' (monotonic)' if mono_down else ''} - CONCENTRATION signal: "
                   f"low n_eff (concentrated recall) is RELIABLE, high n_eff (diffuse) is not. "
                   f"Same family as ch9 candidate spread S; sign is INVERSE to the thin-evidence guess.")
        else:
            tag = f"RISES with n_eff{' (monotonic)' if mono_up else ''} (evidence-accumulation signal)"
        print(f"\nVERDICT (non-seed): hit@10 low-bin {lo_hit:.3f} -> high-bin {hi_hit:.3f} "
              f"(delta {delta:+.3f}) - {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
