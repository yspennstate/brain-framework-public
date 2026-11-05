#!/usr/bin/env python3
"""eval_prompt_recall_fullstack.py - the recall stack's truest, most COMPLETE test. eval_prompt_recall
measures the SEEDING arms (bm25 etc.) on real owner prompts; this measures the FULL live pipeline
brain.recall runs - seeds (lexical + bm25+spell) -> 2-hop spreading activation over the graph -> blend
(0.625*lex + 0.25*graph + 0.125*sem, x salience) - against the same real-prompt ground truth. It answers
the one thing all the proxy evals could not: does the GRAPH + ranking layer (which tonight's keyword edges
feed) actually improve real-prompt recall over the bm25 seeds alone, or are the seeds doing all the work?

Ground truth: each session's real first owner prompt (PROMPTS.jsonl) -> the memories that session then read
(retrievals.tsv). Negatives = the whole corpus. Full-slug keyed throughout; SANITY block prints first so a
mis-wired run can't masquerade. Read-only. Usage: python eval_prompt_recall_fullstack.py

VERDICT (2026-07-11): IMPORTANT - the deliberate CLI (brain.recall) UNDERPERFORMS its own bm25 seeds on real
prompts. On 49 real prompt->reads episodes: bm25-seeds-alone hit@10 0.59 / MRR 0.385, but the live full blend
(0.625/0.25/0.125, seeds->spread->blend) hit@10 0.51 / MRR 0.271, and EVEN the best blend on real prompts
(0.75/0.125/0.125) only reaches 0.51 hit@10 - still below bm25-alone. Mechanism: a real prompt is a
DIRECT-MATCH task (the owner asks about a specific thing), so bm25's raw content ranking nails the reads; the
blend normalises bm25 into a 0..1 lexical term (losing bm25's score granularity) and ADDS graph-spread +
semantic candidates (2-hop associations the prompt did not ask for) that dilute the direct hits. No blend
recovers bm25-alone. This is complementary to the two-mode finding: the DELIBERATE mode wants direct match
even MORE than the wiki-link eval implied (wiki-links reward association; real prompts reward direct match).
Tonight's reweight (more lexical) was directionally right but blend-tuning cannot fix the architecture.

CRUCIAL CAVEAT (adversarial check - do not act without it): the "reads" ground truth is DIRECT-ACCESS-BIASED.
A session reads what it grepped, navigated to, or lexically found - it does NOT read the associative memos it
never knew to look for. So bm25 (direct match) predicts reads partly BY CONSTRUCTION, while the blend's
graph-reach surfaces RELATED memos that weren't read - not because they're useless, but because the session
never went looking associatively. That reach is exactly the "surface what you didn't ask for" value the
two-mode split gives the proactive hook, and this ground truth is blind to it. So this is NOT a clean "the
blend is deficient": for PREDICTING DIRECT READS, bm25 wins; the blend's associative value is real but
uncaptured here. RECOMMEND (DEFERRED, HEDGED - do NOT rewrite brain.recall on this alone): IF the deliberate
CLI's goal is finding-what-you-asked (direct), rank bm25 seeds FIRST for the top-k and use graph-spread for
the TAIL (bm25's blind 2-hop reach) - that keeps the direct hits on top AND the association below. But confirm
the CLI's intended purpose first (direct-answer vs associative-surface), and measure with a less
access-biased ground truth. FOLLOW-UP DID THAT (eval_bm25first.py) and REFUTED the tradeoff: bm25-first +
graph-tail + semantic-tail beats the blend on the wiki-link/associative task TOO (hit@10 0.357 vs 0.252,
matched candidate universe, n=417). So bm25-first is a CLEAR WIN on BOTH deliberate ground truths - the blend
architecture dilutes bm25. brain.recall SHOULD be bm25-first (still deferred: high-blast-radius live-tool
rewrite; implement + verify in a deliberate session, not on my just-corrected judgment at 2am).

*** STALE HEADER CORRECTED 2026-08-22 (session 95e8c38d). IT IS NO LONGER DEFERRED AND HAS NOT BEEN
SINCE 2026-07-15. brain.py line 1160: "Ranking is BM25-FIRST since 2026-07-15 (OQ#1164)"; the blend
survives only behind the escape hatch BRAIN_RECALL_BLEND=1. This harness was never updated after its
own recommendation shipped, so for five weeks its SHIP=(0.625,0.25,0.125) arm has been labelled
"(live)" while measuring a code path that only runs under that env var. The mislabel is not cosmetic:
measured end-to-end on this file's own 143 episodes, the ACTUAL shipped path scores hit@10 0.32 /
MRR 0.148 - tied with raw bm25 (0.32 / 0.172), McNemar b=1 c=1 p=1.00, top-10 sets identical on 52
of 143 - whereas this file's "(live)" column reports 0.16. A reader taking that column at its word
concludes the shipped ranker halves its own seeds. It does not; it is bm25-first by design, which is
exactly why it tracks bm25. Per-episode dump: ~/.claude/mathbrain_loop/per_episode_143.jsonl.
The blend arm is still worth keeping as the pre-07-15 BASELINE - it is just not "live". ***"""
import os, io, re, json, sys
from collections import defaultdict
DR = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
sys.path.insert(0, DR)
import spread_recall as sr
import brain as B
from bm25 import BM25, tokens, SKIP_FILES

LEDGER = os.path.join(MEM, "prompts", "PROMPTS.jsonl")
TRACE = os.path.join(DR, "retrievals.tsv")
MAXSESS = 25


def episodes():
    reads = defaultdict(set)
    for i, ln in enumerate(io.open(TRACE, encoding="utf-8", errors="replace")):
        if i == 0:
            continue
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 4:
            slug, sess = p[0], p[3][:12]
            if slug not in SKIP_FILES and not slug.startswith("dream_"):
                reads[sess].add(slug)
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
    return [(s, prompts[s], reads[s]) for s in reads
            if 2 <= len(reads[s]) <= MAXSESS and s in prompts]


def episode_components(prompt, idx, adj, deg, sal, tier):
    """The blend-INDEPENDENT components (seeds, spread, semantic, salience) computed ONCE per prompt so the
    weight sweep is cheap. Returns [(ls, gs, ss, salb, slug)] or None if no lexical foothold."""
    words = sr._words(prompt)
    seeds, lex = sr._seeds_from_prompt(words, idx)   # lexical + bm25+spell arms, exactly the live seeder
    if not seeds:
        return None
    act = {}
    if adj:
        act, _ = sr._spread(seeds, adj, deg)
    sem = B._semantic_scores(seeds)
    lmax = max(seeds.values()) or 1.0
    amax = max(act.values()) if act else 1.0
    comps = []
    for slug in set(list(seeds) + list(act) + list(sem)):
        if slug in sr.SKIP:
            continue
        ls = seeds.get(slug, 0.0) / lmax
        gs = (act.get(slug, 0.0) / amax) if act else 0.0
        ss = sem.get(slug, 0.0)
        salb = 0.9 + 0.2 * (sal.get(slug, 50.0) / 100.0)
        if tier.get(slug) == "unconscious":
            salb *= 0.35
        comps.append((ls, gs, ss, salb, slug))
    return comps


def rank_blend(comps, wl, wg, ws):
    scored = sorted(((wl * ls + wg * gs + ws * ss) * salb, slug) for ls, gs, ss, salb, slug in comps)
    scored.reverse()
    return [s for _, s in scored]


def hits(ranked, truth):
    pos = {s: i for i, s in enumerate(ranked)}
    best = min((pos[t] for t in truth if t in pos), default=None)
    return best


def _score(ranked_lists):
    h5 = h10 = h20 = mrr = n = 0
    for ranked, truth in ranked_lists:
        pos = {s: i for i, s in enumerate(ranked)}
        best = min((pos[t] for t in truth if t in pos), default=None)
        n += 1
        if best is None:
            continue
        if best < 5: h5 += 1
        if best < 10: h10 += 1
        if best < 20: h20 += 1
        mrr += 1.0 / (best + 1)
    return (h5 / n, h10 / n, h20 / n, mrr / n) if n else (0, 0, 0, 0)


def main():
    _emb = sr._load_embeddings()
    sr._load_embeddings = lambda: _emb
    eps = episodes()
    print(f"eval_prompt_recall_fullstack: {len(eps)} episodes (real prompt -> session reads)")
    if len(eps) < 8:
        print("too few episodes; re-run as the archive grows."); return
    idx = B._archived_index_lines(); idx.update(sr._index_lines())
    adj, deg = sr._load_graph()
    sal = sr._load_salience(); tier = B._tiers()
    bm = BM25()

    data = []   # (comps, bm25_ranked, truth) - components computed ONCE per prompt
    for sess, prompt, truth in eps:
        comps = episode_components(prompt, idx, adj, deg, sal, tier)
        if comps:
            data.append((comps, [sl for sl, _ in bm.score(prompt, 60)], truth))

    c0, b0, t0 = data[0]
    print(f"SANITY: full-stack top {[s[:24] for s in rank_blend(c0, 0.625, 0.25, 0.125)[:3]]} | "
          f"bm25 top {[s[:24] for s in b0[:3]]} | a read {next(iter(t0))[:24]}")

    SHIP = (0.625, 0.25, 0.125)
    bm_s = _score([(b, t) for _, b, t in data])
    ship_s = _score([(rank_blend(c, *SHIP), t) for c, _, t in data])
    print("\n  arm                              hit@5 hit@10 hit@20   MRR")
    print("  bm25-seeds only                   %.2f   %.2f   %.2f   %.3f" % bm_s)
    print("  full blend 0.625/0.25/0.125 (live) %.2f  %.2f   %.2f   %.3f" % ship_s)

    # sweep the blend ON REAL PROMPTS - what weights maximise real-prompt read-prediction?
    best = None
    for i in range(9):
        for j in range(9 - i):
            wl, wg = i / 8.0, j / 8.0
            ws = 1.0 - wl - wg
            if ws < -1e-9:
                continue
            sc = _score([(rank_blend(c, wl, wg, ws), t) for c, _, t in data])
            if best is None or sc[1] > best[1][1] or (sc[1] == best[1][1] and sc[3] > best[1][3]):
                best = ((round(wl, 3), round(wg, 3), round(ws, 3)), sc)
    print("  best blend on REAL PROMPTS %-11s    %.2f   %.2f   %.2f   %.3f" % (best[0], best[1][0], best[1][1], best[1][2], best[1][3]))

    print(f"\n  live blend vs bm25-seeds: hit@10 {ship_s[1]-bm_s[1]:+.3f}. best-blend graph weight = {best[0][1]}.")
    # 2026-08-22: the old two-branch verdict had a catch-all else that printed "defensible"
    # whenever the graph-weight clause failed - including runs where the blend LOSES badly to its
    # own seeds. It printed "defensible" on a -0.161 hit@10 gap. The gap and the graph weight are
    # independent facts; report each on its own evidence and never let one mask the other.
    blend_loses = ship_s[1] < bm_s[1] - 0.02
    wants_less_graph = best[0][1] <= 0.125
    if blend_loses:
        print("  -> THE LIVE BLEND LOSES TO ITS OWN SEEDS on real prompts "
              f"(hit@10 {ship_s[1]:.2f} vs {bm_s[1]:.2f}). Whatever the graph weight does, the blend is "
              "throwing away retrieval its own seeding stage already had. Carry the direct-access-bias "
              "caveat in the module docstring with any use of this line. Measure-only; deliberate change.")
        if wants_less_graph:
            print("     ...and the real-prompt optimum wants LESS graph (spread dilutes direct hits): "
                  "consider ranking bm25 seeds first with graph as secondary reach.")
        else:
            print(f"     ...and the real-prompt optimum wants MORE graph ({best[0][1]}) than the live "
                  f"{SHIP[1]}, which is NOT an endorsement of the graph: it means the blend's own "
                  "lexical term is degraded (bm25 is normalised to 0..1, losing score granularity), so "
                  "graph out-carries it among blends while every blend still loses to raw bm25.")
    elif wants_less_graph:
        print("  -> the blend holds its seeds, but the real-prompt optimum wants LESS graph than the "
              "wiki-link eval implied. Measure-only; deliberate change.")
    else:
        print("  -> the live blend holds its own seeds on real prompts and the optimum does not want "
              "less graph. Nothing here argues for a reweight.")


if __name__ == "__main__":
    main()
