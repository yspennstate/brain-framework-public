"""Light probe: does candidate spread S separate answerable-narrow from vague?

From the diffusion analysis: committing to a recall carries excess variance over
scoring it, equal to the posterior spread of the candidates
    S = sum_i alpha_i |m_i - m_p|^2,   alpha_i the recall weights, m_p the weighted mean.
b_hat (evidence mass), coherence, and n_eff all measured the SEED side and failed to
separate the answerable-narrow 'drivefs' query (low b_hat but rank-1 answerable) from
vague queries. The theory says the act-reliability signal is candidate DISAGREEMENT.
Hypothesis to break: drivefs (answerable) has LOW S (its candidates agree); vague
queries have HIGH S. If S does not separate them, it joins the dead leads.

CONTROLS ONLY (~10 queries) - deliberately NOT the full wiki corpus (that scale run had
to be killed for compute distress). Writes nothing.
"""

from __future__ import annotations
import os

import sys

sys.path.insert(0, os.path.expanduser("~/.claude/dreaming"))
import numpy as np
import brain as B
import spread_recall as sr

CONFIDENT = [
    ("drivefs cache deletion while streaming poisons reads", "the b_hat/coherence FAILURE case"),
    ("selling longer dated puts against the gamma book calendar", "broad-confident"),
    ("real option bid ask only no synthetic prices rule", "broad-confident"),
    ("how do mistakes get through the reflex system", "narrow-confident"),
    ("premium neutral calendar scale cap", "narrow-confident"),
]
VAGUE = [
    ("improve the thing from before", "vague"),
    ("the problem with the number", "vague"),
    ("make it better than before", "vague"),
]


_IDX = None
_EMB = None


def _load_once():
    global _IDX, _EMB
    if _IDX is None:
        _IDX = B._archived_index_lines(); _IDX.update(sr._index_lines())
        _EMB = sr._load_embeddings()
    return _IDX, _EMB


def candidate_spread(query, topk=10):
    """S over the top-k recalled memories, weighted by softmax of their semantic scores.

    Vectors are the embedding rows mat[idx_e[slug]] - the SAME rows _semantic_scores ranks
    (brain.py line ~150), not the return of embedding_row_current (which is a freshness bool,
    the bug this replaces). Convention pinned explicitly: L2-normalize each row to a unit
    direction, so S is the spread of DIRECTIONS in the cosine geometry recall actually uses,
    not a raw-norm-inflated Euclidean spread. alpha_i = softmax(recall score, temp 0.1).
    """
    idx, emb = _load_once()
    idx_e, mat, _bias = emb                   # emb = (idx_e {slug: row}, mat, bias)
    words = sr._words(query)
    seeds, _ = sr._seeds_from_prompt(words, idx)
    if not seeds:
        return None
    sem = B._semantic_scores(seeds)          # {slug: score} over embedded memories
    if not sem:
        return None
    top = sorted(sem.items(), key=lambda t: -t[1])[:topk]
    rows, scores = [], []
    for slug, sc in top:
        r = idx_e.get(slug)
        if r is not None:
            rows.append(int(r)); scores.append(sc)
    if len(rows) < 2:
        return None
    V = np.asarray(mat[rows], dtype=float)
    V = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-9)   # unit directions
    a = np.array(scores, dtype=float)
    a = np.exp((a - a.max()) / 0.1)          # softmax weights alpha_i (temp 0.1, the sharp readout)
    a = a / a.sum()
    mbar = (V * a[:, None]).sum(axis=0)       # weighted mean m_p
    S = float((a * ((V - mbar) ** 2).sum(axis=1)).sum())   # sum_i alpha_i |Y_i - m_p|^2
    return S


print(f"{'S (candidate spread)':>22}  {'query':<52} note")
print("-- CONFIDENT / ANSWERABLE (expect LOW S)")
conf_S = []
for q, note in CONFIDENT:
    S = candidate_spread(q)
    conf_S.append(S)
    print(f"{('%.4f' % S) if S is not None else 'noseed':>22}  {q[:52]:<52} {note}")
print("-- VAGUE (expect HIGH S)")
vague_S = []
for q, note in VAGUE:
    S = candidate_spread(q)
    vague_S.append(S)
    print(f"{('%.4f' % S) if S is not None else 'noseed':>22}  {q[:52]:<52} {note}")

cs = [s for s in conf_S if s is not None]
vs = [s for s in vague_S if s is not None]
if cs and vs:
    print(f"\nconfident median S {sorted(cs)[len(cs)//2]:.4f}  vs  vague median S {sorted(vs)[len(vs)//2]:.4f}")
    drivefs = conf_S[0]
    print(f"the key case - drivefs (answerable, b_hat/coherence FAILED on it): S = "
          f"{('%.4f' % drivefs) if drivefs is not None else 'noseed'}")
    print("VERDICT: S separates IFF confident (esp. drivefs) is LOW and vague is HIGH. "
          "Controls only - a real test needs the wiki-link corpus (run on the box).")
