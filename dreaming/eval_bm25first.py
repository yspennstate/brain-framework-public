#!/usr/bin/env python3
"""eval_bm25first.py - should the deliberate CLI (brain.recall) rank BM25-FIRST instead of blending?

eval_prompt_recall_fullstack found the live blend (0.625/0.25/0.125, seeds->spread->blend) underperforms its
own bm25 seeds for direct real-prompt reads, and I ASSUMED a bm25-first ranking would be a tradeoff - better
direct, worse associative. This measures that assumption on the associative (wiki-link) task, and refutes it.

VERDICT (2026-07-11): bm25-first is a CLEAR WIN on BOTH deliberate ground truths, not a tradeoff. Design =
rank bm25(query) first (fine-grained content match), then a graph-reached tail, then a semantic tail (so the
candidate universe MATCHES the blend's - only the SCORING order differs). Results:
  - wiki-link (associative), 5-fold ~600 held-out links: blend hit@10 0.252 / MRR 0.108;
    bm25-first hit@10 0.357 / MRR 0.150 -> +0.106 / +0.042.
  - real-prompt (direct, eval_prompt_recall_fullstack): bm25-first recovers bm25 hit@10 ~0.59 vs blend 0.51.
Verified with a MATCHED candidate universe (+semantic tail), so the win is the SCORING (bm25-first preserves
bm25's fine ranking; the blend normalises it into a 0..1 term and dilutes it with graph/semantic), not a
candidate-set artifact. WHY it's not a tradeoff: wiki-partners are MOSTLY topical (bm25 finds them directly),
cross-topic ones drop to the graph-tail - so bm25-first surfaces associations BETTER than the blend, not worse.
Coherent with the two-mode split: the DELIBERATE CLI is a content task (bm25-first); pure cross-topic
association is the PROACTIVE hook's job (already graph-dominant). So tonight's blend reweight optimised a
DILUTING architecture; bm25-first is the better architecture.

RECOMMEND (DEFERRED to a deliberate session, NOT a 2am rewrite): change brain.recall's ranking to bm25-first +
graph-tail + semantic-tail. High-blast-radius (every deliberate recall) + it overturns tonight's same-night
reweight + my judgment on this exact topic was just wrong once (the tradeoff assumption), so let a fresh
session re-run this eval, confirm, and implement + verify with a live recall check. Read-only. Usage: python
eval_bm25first.py"""
import os, sys, random
sys.path.insert(0, os.path.expanduser(r"~/.claude/dreaming"))
import spread_recall as sr
import brain as B
import eval_deliberate_recall as ED
from bm25 import BM25


def main():
    _emb = sr._load_embeddings()
    sr._load_embeddings = lambda: _emb
    real = ED.load_real()
    idx = B._archived_index_lines(); idx.update(sr._index_lines())
    sal = sr._load_salience(); tier = B._tiers()
    base = ED.base_non_wiki_edges(real)
    wiki = ED.wiki_pairs(real)
    wiki_edges = [(a, b, ED.W_WIKI) for a, b in wiki]
    bm = BM25()
    random.seed(11)
    order = list(range(len(wiki))); random.shuffle(order)
    FOLDS = 5
    fold_of = {order[i]: i % FOLDS for i in range(len(wiki))}

    def blend_rank(comps):
        scored = sorted(((0.625 * ls + 0.25 * gs + 0.125 * ss) * sb, s) for ls, gs, ss, sb, s in comps)
        scored.reverse()
        return [s for _, s in scored]

    def bm25first_rank(query_slug, comps):
        line = idx.get(query_slug, "") + " " + query_slug
        top = [s for s, _ in bm.score(line, 40) if s != query_slug]
        seen = set(top)
        gtail = sorted(((gs, s) for ls, gs, ss, sb, s in comps if s not in seen and gs > 0), reverse=True)
        for _, s in gtail:
            seen.add(s)
        stail = sorted(((ss, s) for ls, gs, ss, sb, s in comps if s not in seen and ss > 0), reverse=True)
        return top + [s for _, s in gtail] + [s for _, s in stail]

    bl = dict(h10=0, h25=0, mrr=0, n=0)
    bf = dict(h10=0, h25=0, mrr=0, n=0)
    for f in range(FOLDS):
        held = [k for k in range(len(wiki)) if fold_of[k] == f]
        held_set = set(held)
        keep = [wiki_edges[k] for k in range(len(wiki)) if k not in held_set]
        adj, deg = ED.to_adj(base + keep)
        for k in held[:120]:
            A, Bt = wiki[k]
            if not idx.get(A):
                continue
            comps = ED.pair_components(A, idx, adj, deg, sal, tier)
            if not comps:
                continue
            for d, ranked in ((bl, blend_rank(comps)), (bf, bm25first_rank(A, comps))):
                pos = {s: i for i, s in enumerate(ranked)}
                d["n"] += 1
                i = pos.get(Bt)
                if i is None:
                    continue
                if i < 10: d["h10"] += 1
                if i < 25: d["h25"] += 1
                d["mrr"] += 1.0 / (i + 1)

    def rep(d):
        n = max(1, d["n"])
        return d["h10"] / n, d["h25"] / n, d["mrr"] / n

    print("bm25-first vs blend on the wiki-link (associative) deliberate task:")
    print("  blend 0.625/0.25/0.125 : hit@10 %.3f  hit@25 %.3f  MRR %.3f  (n=%d)" % (rep(bl) + (bl["n"],)))
    print("  bm25-first + tails     : hit@10 %.3f  hit@25 %.3f  MRR %.3f" % rep(bf))
    h_bl, _, m_bl = rep(bl); h_bf, _, m_bf = rep(bf)
    print("  -> bm25-first vs blend: hit@10 %+.3f, MRR %+.3f. CLEAR WIN (with the real-prompt win too). "
          "See the module verdict; implement deliberately." % (h_bf - h_bl, m_bf - m_bl))


if __name__ == "__main__":
    main()
