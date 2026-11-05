"""Does candidate spread S predict recall reliability - and BEYOND query breadth?

The decider for the flag thread. n_eff (seed count) predicts wiki-link hit@10 monotonically
but orders the answerable-vs-vague controls backwards, so its signal is probably query
BREADTH, not reliability (READING_NOTES + diffusion_selection_variance_candidate_spread).
Chapter 9's exact quantity is the CANDIDATE spread S = sum_i alpha_i |Y_i - m_p|^2 = 1 - ||m_p||^2
(unit-normalized), a candidate-side coherence deficit read DOWNSTREAM of retrieval. This tests it
at scale on the same wiki-link ground truth as eval_neff, and is built to REFUTE:

  1. Does hit@10 fall as S rises (non-seed targets)? If flat, S is dead like coherence.
  2. THE decider: does S predict hit@10 WITHIN n_eff bands? If S only tracks hit@10 because it
     co-varies with n_eff (breadth), it adds nothing; if S separates hit@10 at FIXED n_eff, it is
     a genuine concentration signal that n_eff/breadth is not. Withhold belief until this passes.

Reuses eval_neff's wiki scaffold and probe_candidate_spread's S (verified embedding API:
emb = (idx_e, mat, bias); vector = mat[idx_e[slug]]; unit-normalized). Writes nothing.
"""

from __future__ import annotations
import os

import sys

sys.path.insert(0, os.path.expanduser("~/.claude/dreaming"))
import numpy as np
import brain as B
import spread_recall as sr
import eval_deliberate_recall as ED

TOPK = 10
TEMP = 0.1  # softmax temperature on recall scores -> alpha_i (the sharp readout)


def candidate_S(sem, idx_e, mat, self_slug):
    """S = sum_i alpha_i |Y_i - m_p|^2 over the top-TOPK recalled memories (excl. the query self),
    unit-normalized directions, alpha = softmax(score/TEMP). None if <2 usable candidates."""
    top = sorted(((s, sc) for s, sc in sem.items() if s != self_slug), key=lambda t: -t[1])[:TOPK]
    rows, scores = [], []
    for slug, sc in top:
        r = idx_e.get(slug)
        if r is not None:
            rows.append(int(r)); scores.append(sc)
    if len(rows) < 2:
        return None
    V = np.asarray(mat[rows], dtype=float)
    V = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-9)
    a = np.array(scores, dtype=float)
    a = np.exp((a - a.max()) / TEMP)
    a = a / a.sum()
    mbar = (V * a[:, None]).sum(axis=0)
    return float((a * ((V - mbar) ** 2).sum(axis=1)).sum())


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
        line = idx.get(A, "")
        seeds, _lex = sr._seeds_from_prompt(sr._words(line + " " + A), idx)
        if not seeds:
            skipped += 1; continue
        diag = {}
        sem = B._semantic_scores(seeds, diag=diag)
        n_eff = diag.get("n_eff")
        if n_eff is None or not sem:
            skipped += 1; continue
        S = candidate_S(sem, idx_e, mat, A)
        if S is None:
            skipped += 1; continue
        scored = sorted(((s, sc) for s, sc in sem.items() if s != A), key=lambda t: -t[1])
        rank = next((i + 1 for i, (s, _) in enumerate(scored) if s == Bt), None)
        if rank is None:
            skipped += 1; continue
        rows.append((float(S), float(n_eff), rank, Bt in seeds))

    n = len(rows)
    print(f"wiki pairs: {len(wiki)}; scored {n}; skipped {skipped}", flush=True)
    if n < 40:
        print("too few scored pairs for a stable read"); return 0

    nonseed = [r for r in rows if not r[3]]
    Svals = sorted(r[0] for r in nonseed)
    qs = [Svals[int(q * (len(Svals) - 1))] for q in (0.2, 0.4, 0.6, 0.8)]
    edges = [-1e9] + qs + [1e9]

    def hit_at(sub):
        return (sum(1 for _, _, rk, _ in sub if rk <= 10) / len(sub)) if sub else float("nan")

    print(f"\nNON-SEED targets (the flag's regime), n={len(nonseed)}; S quintiles:")
    print(f"{'S bin':>16} {'n':>5} {'hit@10':>7} {'medrank':>8}")
    for lo, hi in zip(edges[:-1], edges[1:]):
        b = [r for r in nonseed if lo <= r[0] < hi]
        if not b:
            continue
        mr = sorted(rk for _, _, rk, _ in b)[len(b) // 2]
        print(f"{lo:7.4f}-{hi if hi < 1e8 else 999:7.4f} {len(b):>5} {hit_at(b):>7.3f} {mr:>8}")

    # THE decider: S vs hit@10 WITHIN n_eff bands. If S separates hit@10 at fixed n_eff, it is
    # signal beyond breadth; if flat within bands, S was breadth all along (like n_eff).
    neffs = sorted(r[1] for r in nonseed)
    nmed = neffs[len(neffs) // 2]
    print(f"\nDECIDER - S vs hit@10 WITHIN n_eff bands (n_eff median {nmed:.2f}):")
    for label, band in (("LOW n_eff", [r for r in nonseed if r[1] < nmed]),
                        ("HIGH n_eff", [r for r in nonseed if r[1] >= nmed])):
        if len(band) < 20:
            print(f"  {label}: n={len(band)} too small"); continue
        bs = sorted(r[0] for r in band)
        mid = bs[len(bs) // 2]
        loS = [r for r in band if r[0] < mid]
        hiS = [r for r in band if r[0] >= mid]
        dlt = hit_at(loS) - hit_at(hiS)
        print(f"  {label} (n={len(band)}): lowS hit@10 {hit_at(loS):.3f} vs highS {hit_at(hiS):.3f} "
              f"(delta {dlt:+.3f})  {'S SEPARATES here' if dlt > 0.03 else 'flat -> S is breadth in this band'}")
    print("\nVERDICT: S is a real concentration signal ONLY IF lowS beats highS INSIDE both n_eff "
          "bands. Whole-column monotonicity alone can be breadth. Do not wire a flag until this holds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
