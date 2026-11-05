#!/usr/bin/env python3
"""eval_lift_ablation.py - is the graph/semantic lift in the bm25-first display score what costs the live
recall order its MRR against raw BM25?

Measured 2026-09-02: raw BM25 hit@10 0.22 / MRR 0.140 on 100 real prompt->read episodes, the live order
0.22 / 0.114; the usage tilt was cleared (identical on and off). The remaining suspect is the display
score max(0.5 * lnorm, 0.5 * graph + 0.25 * semantic): a long many-topic document with strong graph and
semantic activation can climb above a sharper lexical match. This runs the same live recall with the
graph spread and the semantic scores monkeypatched to nothing (so only the lexical term survives) and
reports both orders, plus the episode-level diff. Read-only. Usage: python eval_lift_ablation.py [--n 100]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eval_prompt_recall_fullstack as em  # noqa: E402
import eval_fusion_live as fl  # noqa: E402
import spread_recall as sr  # noqa: E402
import brain as B  # noqa: E402
from bm25 import BM25  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--out", default=os.path.join(HERE, "eval_lift_ablation.json"))
    a = ap.parse_args()
    t0 = time.time()
    emb = sr.embedding_artifact.load_verified_embeddings(sr.EMB, sr.EMB_AUTHORITY, sr.MEM, sr.EXPECTED_EMB_MODEL)
    sr._load_embeddings = lambda: emb
    eps = em.episodes()[: a.n]
    print("episodes:", len(eps))
    bm = BM25()
    real_spread = sr._spread
    real_sem = B._semantic_scores
    arms = {"bm25": [], "live": [], "live_no_lift": []}
    per = []
    for i, (sess, prompt, truth) in enumerate(eps):
        bm_list = [sl for sl, _ in bm.score(prompt, 60)]
        arms["bm25"].append((bm_list, truth))
        sr._spread, B._semantic_scores = real_spread, real_sem
        live = fl.live_order(prompt, 20)
        # the ablation: no association-graph activation, no semantic scores -> only the lexical term remains
        sr._spread = lambda seeds, adj, deg: ({}, {})
        B._semantic_scores = lambda seeds: {}
        nolift = fl.live_order(prompt, 20)
        sr._spread, B._semantic_scores = real_spread, real_sem
        arms["live"].append((live, truth))
        arms["live_no_lift"].append((nolift, truth))

        def rr(lst):
            for r, s in enumerate(lst):
                if s in truth:
                    return 1.0 / (r + 1)
            return 0.0
        per.append({"session": sess, "rr_bm25": rr(bm_list[:20]), "rr_live": rr(live), "rr_nolift": rr(nolift)})
        if (i + 1) % 20 == 0:
            print("  ...", i + 1, flush=True)
    print("\n  %-14s  n   hit@5 hit@10 hit@20   MRR" % "arm")
    rec = {}
    for name in ("bm25", "live", "live_no_lift"):
        h5, h10, h20, mrr = em._score(arms[name])
        rec[name] = {"n": len(arms[name]), "hit5": round(h5, 3), "hit10": round(h10, 3), "hit20": round(h20, 3), "mrr": round(mrr, 3)}
        print("  %-14s %3d   %.2f   %.2f   %.2f   %.3f" % (name, len(arms[name]), h5, h10, h20, mrr))
    better = sum(1 for p in per if p["rr_nolift"] > p["rr_live"])
    worse = sum(1 for p in per if p["rr_nolift"] < p["rr_live"])
    print("no-lift vs live per episode: better %d, worse %d, same %d" % (better, worse, len(per) - better - worse))
    print("seconds", round(time.time() - t0, 1))
    with io.open(a.out, "w", encoding="utf-8") as f:
        json.dump({"when": time.strftime("%Y-%m-%d %H:%M"), "episodes": len(eps), "arms": rec,
                   "nolift_vs_live": {"better": better, "worse": worse}, "per_episode": per}, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
