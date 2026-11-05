#!/usr/bin/env python3
"""eval_tilt_ablation.py - does the usage tilt cost the deliberate recall its MRR against raw BM25?

Measured 2026-09-02 (eval_fusion_live.py, 100 real prompt->read episodes): raw BM25 hit@10 0.22 / MRR 0.140,
the live bm25-first order hit@10 0.22 / MRR 0.114; 15 episodes worse, 1 better, the displacers long many-topic
documents (job readmes, canonical prompts). The live path multiplies its display score by a boost-only usage
tilt (1 + 0.15 * usage_norm) - and job readmes are the most-read documents in the corpus, so the tilt is the
first suspect. This harness runs the SAME live recall with the tilt on and off (BRAIN_RECALL_USAGE) over the
same episodes and reports both, plus the episode-level diff. Read-only. Usage: python eval_tilt_ablation.py [--n 100]
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
from bm25 import BM25  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--out", default=os.path.join(HERE, "eval_tilt_ablation.json"))
    a = ap.parse_args()
    t0 = time.time()
    emb = sr.embedding_artifact.load_verified_embeddings(sr.EMB, sr.EMB_AUTHORITY, sr.MEM, sr.EXPECTED_EMB_MODEL)
    sr._load_embeddings = lambda: emb
    eps = em.episodes()[: a.n]
    print("episodes:", len(eps))
    bm = BM25()
    arms = {"bm25": [], "live_usage_on": [], "live_usage_off": []}
    per = []
    for i, (sess, prompt, truth) in enumerate(eps):
        bm_list = [sl for sl, _ in bm.score(prompt, 60)]
        arms["bm25"].append((bm_list, truth))
        os.environ["BRAIN_RECALL_USAGE"] = "1"
        on = fl.live_order(prompt, 20)
        os.environ["BRAIN_RECALL_USAGE"] = "0"
        off = fl.live_order(prompt, 20)
        arms["live_usage_on"].append((on, truth))
        arms["live_usage_off"].append((off, truth))

        def rr(lst):
            for r, s in enumerate(lst):
                if s in truth:
                    return 1.0 / (r + 1)
            return 0.0
        per.append({"session": sess, "rr_bm25": rr(bm_list[:20]), "rr_on": rr(on), "rr_off": rr(off),
                    "top1_on": on[:1], "top1_off": off[:1]})
        if (i + 1) % 20 == 0:
            print("  ...", i + 1, flush=True)
    print("\n  %-16s  n   hit@5 hit@10 hit@20   MRR" % "arm")
    rec = {}
    for name in ("bm25", "live_usage_on", "live_usage_off"):
        h5, h10, h20, mrr = em._score(arms[name])
        rec[name] = {"n": len(arms[name]), "hit5": round(h5, 3), "hit10": round(h10, 3), "hit20": round(h20, 3), "mrr": round(mrr, 3)}
        print("  %-16s %3d   %.2f   %.2f   %.2f   %.3f" % (name, len(arms[name]), h5, h10, h20, mrr))
    better = sum(1 for p in per if p["rr_off"] > p["rr_on"])
    worse = sum(1 for p in per if p["rr_off"] < p["rr_on"])
    print("usage OFF vs ON per episode: better %d, worse %d, same %d" % (better, worse, len(per) - better - worse))
    gap_on = sum(1 for p in per if p["rr_on"] < p["rr_bm25"])
    gap_off = sum(1 for p in per if p["rr_off"] < p["rr_bm25"])
    print("episodes where live trails bm25: usage on %d, usage off %d" % (gap_on, gap_off))
    print("seconds", round(time.time() - t0, 1))
    with io.open(a.out, "w", encoding="utf-8") as f:
        json.dump({"when": time.strftime("%Y-%m-%d %H:%M"), "episodes": len(eps), "arms": rec,
                   "off_vs_on": {"better": better, "worse": worse}, "trails_bm25": {"on": gap_on, "off": gap_off},
                   "per_episode": per}, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
