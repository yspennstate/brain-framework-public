#!/usr/bin/env python3
"""eval_prompt_recall.py - the actual-use gate for the recall stack's SEEDING arms.

Question: given the owner's real opening prompt, which ranker best predicts the memories that
session actually went on to read? This became measurable on 2026-07-09 when the prompt archive
landed: PROMPTS.jsonl supplies each session's real first prompt; retrievals.tsv (the rebuilt
retrieval trace) supplies what that session then read. Ground truth is behaviour, negatives are
the entire corpus - the actual-use, hard-negative shape the method demands (13 of 15 "obvious"
ideas died on gates like this).

Arms:
  lexical  - the live seeder's logic: word-overlap vs MEMORY.md index lines + filenames
  bm25     - ranked full-text keyword arm (bm25.py; content the live seeder cannot see)
  rrf      - reciprocal-rank fusion of the two (1/(60+rank))

Caveats stated up front: all arms rank TODAY's corpus (the index a past session saw differed);
memories younger than the session can steal rank symmetrically across arms; wake-injected reads
inflate ground truth with hubs, also symmetrically. Read-only; no tuning knobs (k1/b at defaults -
the non-stationary lesson says no snapshot fits).

Usage: python eval_prompt_recall.py
"""
import os, io, re, json
from collections import defaultdict

DR = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
TRACE = os.path.join(DR, "retrievals.tsv")
LEDGER = os.path.join(MEM, "prompts", "PROMPTS.jsonl")
import sys
sys.path.insert(0, DR)
from bm25 import BM25, tokens, SKIP_FILES

MAXSESS = 25
WORD = re.compile(r"[a-zA-Z0-9_]{3,}")


def _index_lines():
    out = {}
    try:
        for ln in io.open(os.path.join(MEM, "MEMORY.md"), encoding="utf-8", errors="replace"):
            m = re.match(r"- \[([^\]]+?)\.md\]", ln)
            if m:
                out[m.group(1)] = ln.strip().lower()
    except Exception:
        pass
    return out


def lexical_rank(prompt, idx, all_slugs, topk=20):
    """The live seeder's scoring, reproduced: overlap counts on index lines + filename substrings."""
    words = [w.lower() for w in WORD.findall(prompt)]
    if len(words) < 2:
        return []
    words = set(sorted(set(words), key=len, reverse=True)[:12])
    scores = {}
    for slug, line in idx.items():
        ov = sum(1 for w in words if w in line)
        if ov >= 2:
            scores[slug] = float(ov)
    for slug in all_slugs:
        if slug not in scores:
            ov = sum(1 for w in words if len(w) >= 4 and w in slug.lower())
            if ov >= 2:
                scores[slug] = float(ov)
    return sorted(scores.items(), key=lambda kv: -kv[1])[:topk]


def rrf(*rankings, topk=20, k=60):
    s = defaultdict(float)
    for ranking in rankings:
        for r, (slug, _) in enumerate(ranking):
            s[slug] += 1.0 / (k + r + 1)
    return sorted(s.items(), key=lambda kv: -kv[1])[:topk]


def main():
    # sessions -> read slugs (2..MAXSESS distinct, skip substrate pages)
    reads = defaultdict(set)
    first_ts = {}
    for i, ln in enumerate(io.open(TRACE, encoding="utf-8", errors="replace")):
        if i == 0:
            continue
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 4:
            slug, sess = p[0], p[3][:12]
            if slug not in SKIP_FILES and not slug.startswith("dream_"):
                reads[sess].add(slug)
                first_ts.setdefault(sess, p[2][:16])
    # sessions -> first substantive owner prompt
    prompts = {}
    if os.path.exists(LEDGER):
        recs = []
        for ln in io.open(LEDGER, encoding="utf-8", errors="replace"):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r.get("origin", "owner") == "owner" and r.get("session") and r.get("text"):
                recs.append(r)
        recs.sort(key=lambda r: r.get("ts") or "")
        for r in recs:
            s = r["session"]
            if s not in prompts and len(tokens(r["text"])) >= 6:
                prompts[s] = r["text"]

    episodes = [(s, prompts[s], reads[s]) for s in reads
                if 2 <= len(reads[s]) <= MAXSESS and s in prompts]
    if len(episodes) < 8:
        print("eval_prompt_recall: only %d joinable episodes - too few; re-run as trace+archive grow." % len(episodes))
        return

    idx = _index_lines()
    bm = BM25()
    all_slugs = list(bm.tf.keys())
    from bm25 import QueryNormalizer
    qn = QueryNormalizer(bm.df)   # the LIVE spread_recall seed path spell-normalizes; measure it here
    arms = {"lexical": [], "bm25": [], "bm25+spell": [], "rrf": []}
    for sess, prompt, truth in episodes:
        lr = lexical_rank(prompt, idx, all_slugs)
        br = bm.score(prompt, 20)
        bs = bm.score(qn.normalize(prompt), 20)   # typo-corrected query (what the live seeder now does)
        rr = rrf(lr, br)
        for name, ranking in (("lexical", lr), ("bm25", br), ("bm25+spell", bs), ("rrf", rr)):
            rank_of_hit = next((i for i, (slug, _) in enumerate(ranking) if slug in truth), None)
            top10 = [slug for slug, _ in ranking[:10]]
            arms[name].append({
                "hit5": rank_of_hit is not None and rank_of_hit < 5,
                "hit10": rank_of_hit is not None and rank_of_hit < 10,
                "hit20": rank_of_hit is not None,
                "rec10": (len(truth.intersection(top10)) / len(truth)) if truth else 0.0,
                "mrr": (1.0 / (rank_of_hit + 1)) if rank_of_hit is not None else 0.0,
            })
    print("eval_prompt_recall: %d episodes (real first prompt -> memories the session read; "
          "negatives = all %d docs)" % (len(episodes), len(all_slugs)))
    print("  %-8s %6s %6s %6s %7s %6s" % ("arm", "hit@5", "hit@10", "hit@20", "rec@10", "MRR"))
    for name, rows in arms.items():
        n = float(len(rows))
        print("  %-8s %6.2f %6.2f %6.2f %7.3f %6.3f" % (
            name, sum(r["hit5"] for r in rows) / n, sum(r["hit10"] for r in rows) / n,
            sum(r["hit20"] for r in rows) / n, sum(r["rec10"] for r in rows) / n,
            sum(r["mrr"] for r in rows) / n))
    print("  (gate: bm25 or rrf must beat lexical on hit@10 to justify live wiring)")


if __name__ == "__main__":
    main()
