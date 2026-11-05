#!/usr/bin/env python3
"""eval_fusion_live.py - fusion functions and Hopfield-style semantic retrieval, on real prompt->read episodes.

Literature the 2026-09-02 harvest surfaced: Bruch, Gai, Ingber (2022) "An Analysis of Fusion Functions for
Hybrid Retrieval" (convex combination of normalised scores vs reciprocal rank fusion, RRF); Ramsauer et al.
(2020) / Hu et al. (2024) modern Hopfield retrieval (softmax-weighted iterative update converging to a stored
pattern = pattern completion from a partial cue). This harness asks the brain's own data whether either
mechanism would move the deliberate recall's real-prompt hit rates, BEFORE anything is changed in brain.py.

Ground truth: eval_prompt_recall_fullstack.episodes() - each session's first owner prompt and the memories
that session then read (retrievals.tsv). Arms:
  bm25            BM25 over the corpus, top 60 (the live bm25-first block's head)
  live            the deliberate recall's actual order (brain.recall, captured), top 20
  rrf             reciprocal rank fusion (k = 60) of bm25 rank, graph-spread rank, semantic rank
  convex          min-max normalised convex combination bm25/graph/semantic at the eval's shipped weights
  sem1 / sem2 / sem3  semantic lane alone: cosine to the seed centroid after 1, 2, 3 Hopfield updates
                  c <- normalise(sum_i softmax(beta <v_i, c>) v_i), beta in {8, 16}
Metrics hit@5/10/20 and MRR over the episodes. Read-only. Usage: python eval_fusion_live.py [--n 120]
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

import eval_prompt_recall_fullstack as em  # noqa: E402
import spread_recall as sr  # noqa: E402
import brain as B  # noqa: E402
from bm25 import BM25  # noqa: E402

RANK_RE = re.compile(r"^\s+(\d\.\d\d)\s+\[([^\]]*)\]\s+(\S+)")


def live_order(query, n=20):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            B.recall(query, n=n)
        except SystemExit:
            pass
    out = []
    for ln in buf.getvalue().splitlines():
        if ln.startswith("--- AUTO-READ") or ln.startswith("read one WITH"):
            break
        m = RANK_RE.match(ln)
        if m:
            out.append(m.group(3))
    return out


def rrf(rank_lists, k=60):
    s = defaultdict(float)
    for lst in rank_lists:
        for r, slug in enumerate(lst):
            s[slug] += 1.0 / (k + r + 1)
    return [slug for slug, _ in sorted(s.items(), key=lambda kv: -kv[1])]


def minmax(d):
    if not d:
        return {}
    lo, hi = min(d.values()), max(d.values())
    if hi <= lo:
        return {k: 1.0 for k in d}
    return {k: (v - lo) / (hi - lo) for k, v in d.items()}


def hopfield_rank(seeds, emb, beta, steps, top=60):
    idx_e, mat, bias = emb
    rows = [(idx_e[s], w) for s, w in seeds.items() if s in idx_e]
    if not rows:
        return []
    M = mat if hasattr(mat, "dtype") else np.asarray(mat)
    c = np.zeros(M.shape[1], dtype=np.float32)
    for r, w in rows:
        c += w * M[r]
    c /= (np.linalg.norm(c) + 1e-9)
    for _ in range(steps - 1):
        sims = M @ c
        z = beta * (sims - sims.max())
        p = np.exp(z); p /= p.sum()
        c = p @ M
        c /= (np.linalg.norm(c) + 1e-9)
    sims = M @ c
    order = np.argsort(-sims)[:top]
    inv = {v: k for k, v in idx_e.items()}
    return [inv[i] for i in order if i in inv]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    t0 = time.time()
    # the deliberate path's own numpy loader (the hook loader returns a numpy-free packed matrix)
    emb = sr.embedding_artifact.load_verified_embeddings(sr.EMB, sr.EMB_AUTHORITY, sr.MEM, sr.EXPECTED_EMB_MODEL)
    if not emb:
        print("no verified embedding sidecar; semantic arms skipped")
    sr._load_embeddings = lambda: emb
    eps = em.episodes()[: a.n]
    print("episodes:", len(eps))
    idx = B._archived_index_lines(); idx.update(sr._index_lines())
    adj, deg = sr._load_graph()
    sal = sr._load_salience(); tier = B._tiers()
    bm = BM25()
    arms = defaultdict(list)
    for i, (sess, prompt, truth) in enumerate(eps):
        comps = em.episode_components(prompt, idx, adj, deg, sal, tier)
        bm_list = [sl for sl, _ in bm.score(prompt, 60)]
        arms["bm25"].append((bm_list, truth))
        arms["live"].append((live_order(prompt, 20), truth))
        if comps:
            # 2026-09-13: the live branch only. What stood here was `<dead expression> if False else <this>`, and
            # the dead half referenced an undefined `c` - a NameError waiting for anyone who flipped the guard.
            graph_list = [c[4] for c in sorted(comps, key=lambda c: -c[1]) if c[1] > 0][:60]
            sem_list = [c[4] for c in sorted(comps, key=lambda c: -c[2]) if c[2] > 0][:60]
            arms["rrf"].append((rrf([bm_list, graph_list, sem_list]), truth))
            # convex combination of min-max normalised scores (bm25 score included as a proper channel)
            bsc = minmax({sl: sc for sl, sc in bm.score(prompt, 60)})
            gsc = minmax({c[4]: c[1] for c in comps}); ssc = minmax({c[4]: c[2] for c in comps})
            keys = set(bsc) | set(gsc) | set(ssc)
            conv = sorted(keys, key=lambda k: -(0.625 * bsc.get(k, 0) + 0.25 * gsc.get(k, 0) + 0.125 * ssc.get(k, 0)))
            arms["convex"].append((conv[:60], truth))
            words = sr._words(prompt)
            seeds, _lex = sr._seeds_from_prompt(words, idx)
            if emb and seeds:
                for beta in (8.0, 16.0):
                    for steps in (1, 2, 3):
                        arms["sem%d_b%d" % (steps, int(beta))].append((hopfield_rank(seeds, emb, beta, steps), truth))
        if (i + 1) % 20 == 0:
            print("  ...", i + 1, flush=True)
    print("\n  %-14s  n   hit@5 hit@10 hit@20   MRR" % "arm")
    rec = {}
    for name in ["bm25", "live", "rrf", "convex", "sem1_b8", "sem2_b8", "sem3_b8", "sem1_b16", "sem2_b16", "sem3_b16"]:
        if name not in arms:
            continue
        h5, h10, h20, mrr = em._score(arms[name])
        rec[name] = {"n": len(arms[name]), "hit5": round(h5, 3), "hit10": round(h10, 3), "hit20": round(h20, 3), "mrr": round(mrr, 3)}
        print("  %-14s %3d   %.2f   %.2f   %.2f   %.3f" % (name, len(arms[name]), h5, h10, h20, mrr))
    print("seconds", round(time.time() - t0, 1))
    if a.out:
        with io.open(a.out, "w", encoding="utf-8") as f:
            json.dump({"when": time.strftime("%Y-%m-%d %H:%M"), "episodes": len(eps), "arms": rec}, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
