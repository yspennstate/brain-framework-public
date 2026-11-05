#!/usr/bin/env python3
"""eval_fuzzy_recall.py - does character-ngram (typo-robust) matching lift recall over word-BM25?

The owner types with deliberate, heavy typos (fix_owner_spelling_automatically) - "memeory", "improinb",
"functionalist". Word-BM25 tokenizes on exact terms, so a typo'd term matches NOTHING; a whole prompt of
them surfaces nothing (visible in the workflow-recall gap: procedural prompts full of typos miss their
how-to memory). Character 3-grams degrade gracefully under a typo ("memeory" and "memory" still share
'mem','emo','mor'...), so a fuzzy arm should recover on-topic-but-misspelled targets.

Measured against the SAME replay ground truth as eval_prompt_recall (first owner prompt -> memories the
session read; negatives = the whole corpus), plus the procedural subset (eval_workflow_recall's episodes).
Arms: word-BM25 (baseline), char-3gram cosine, and RRF fusion of the two (scale-free hybrid, the standard
recipe). The fusion vs baseline delta on hit@10 is the gate. Laptop-safe (no model). Read-only.
Usage: python eval_fuzzy_recall.py
"""
import io, os, re, sys, json, math
from collections import defaultdict, Counter

DR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DR)
from bm25 import BM25, tokens, read_docs, SKIP_FILES

MEM = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory")
TRACE = os.path.join(DR, "retrievals.tsv")
LEDGER = os.path.join(MEM, "prompts", "PROMPTS.jsonl")
PROC = ("method_", "reference_", "feedback_")
MAXSESS = 25
RRF_K = 60


def _trigrams(toks):
    s = " " + " ".join(toks) + " "
    return Counter(s[i:i + 3] for i in range(len(s) - 2))


class CharNgram:
    """char-3gram cosine over the same token stream BM25 indexes (typo-robust lexical arm)."""
    def __init__(self, docs):
        self.prof = {sl: _trigrams(t) for sl, t in docs.items()}
        self.norm = {sl: math.sqrt(sum(v * v for v in p.values())) or 1.0 for sl, p in self.prof.items()}

    def score(self, query, topk=30):
        q = _trigrams(tokens(query))
        qn = math.sqrt(sum(v * v for v in q.values())) or 1.0
        sc = {}
        for sl, p in self.prof.items():
            dot = 0
            for g, v in q.items():
                w = p.get(g)
                if w:
                    dot += v * w
            if dot:
                sc[sl] = dot / (qn * self.norm[sl])
        return sorted(sc.items(), key=lambda kv: -kv[1])[:topk]


def rrf(*rankings, k=RRF_K, topk=30):
    agg = defaultdict(float)
    for r in rankings:
        for i, (s, _) in enumerate(r):
            agg[s] += 1.0 / (k + i)
    return sorted(agg.items(), key=lambda kv: -kv[1])[:topk]


def episodes():
    reads = defaultdict(set)
    for i, ln in enumerate(io.open(TRACE, encoding="utf-8", errors="replace")):
        if i == 0:
            continue
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 4 and p[0] not in SKIP_FILES and not p[0].startswith("dream_"):
            reads[p[3][:12]].add(p[0])
    first, proc_prompt, recs = {}, {}, []
    for ln in io.open(LEDGER, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get("origin", "owner") == "owner" and r.get("session") and r.get("text") \
                and len(tokens(r["text"])) >= 6:
            recs.append(r)
    recs.sort(key=lambda r: r.get("ts") or "")
    for r in recs:
        s = r["session"]
        if s not in first:
            first[s] = r["text"]
        if r.get("class_model") in ("run", "improve", "prove") and s not in proc_prompt:
            proc_prompt[s] = r["text"]
    allep = [(first[s], reads[s], None) for s in reads if 2 <= len(reads[s]) <= MAXSESS and s in first]
    procep = [(proc_prompt[s], {x for x in reads[s] if x.startswith(PROC)}, None)
              for s in reads if 2 <= len(reads[s]) <= MAXSESS and s in proc_prompt
              and any(x.startswith(PROC) for x in reads[s])]
    return allep, procep


def hit_at(ranker, eps):
    h5 = h10 = n = 0
    mrr = 0.0
    for prompt, targets, _ in eps:
        if not targets:
            continue
        n += 1
        ranked = [s for s, _ in ranker(prompt)]
        pos = next((i for i, s in enumerate(ranked) if s in targets), None)
        if pos is not None:
            if pos < 5:
                h5 += 1
            if pos < 10:
                h10 += 1
            mrr += 1.0 / (pos + 1)
    return (h5 / n, h10 / n, mrr / n, n) if n else (0, 0, 0, 0)


def main():
    docs = read_docs()
    bm, cn = BM25(), CharNgram(docs)
    allep, procep = episodes()

    def word(q):
        return bm.score(q, 30)

    def char(q):
        return cn.score(q, 30)

    def fused(q):
        return rrf(bm.score(q, 30), cn.score(q, 30))

    def oov_rate(q):
        ts = [t for t in tokens(q)]
        if not ts:
            return 0.0
        return sum(1 for t in ts if t not in bm.df) / len(ts)

    # CONDITIONAL fuse: only reach for the typo-robust arm when the prompt is lexically off the corpus
    # (many out-of-vocab tokens = typos or novel wording). Clean prompts keep pure BM25 precision.
    OOV = 0.34

    def cond(q):
        return fused(q) if oov_rate(q) >= OOV else bm.score(q, 30)

    # AUGMENT-TAIL: keep BM25's ranking untouched (so hit@5/MRR can't regress - its head is unchanged),
    # then APPEND char-ngram's top candidates that BM25 missed entirely. Pure addition to the pool -
    # helps hit@10/recall (and the seeder, which takes the pool) without reordering the precise head.
    def augment(q, keep=8, add=6):
        w = bm.score(q, 30)
        wl = [s for s, _ in w]
        seen = set(wl[:keep])
        extra = [s for s, _ in cn.score(q, 30) if s not in seen][:add]
        return [(s, 0.0) for s in wl[:keep]] + [(s, 0.0) for s in extra] + [(s, 0.0) for s in wl[keep:]]

    # is OOV a clean discriminator? report mean OOV on each set
    for name, eps in (("ALL (factual+proc)", allep), ("PROCEDURAL only", procep)):
        movv = sum(oov_rate(p) for p, _, _ in eps) / len(eps) if eps else 0
        nfire = sum(1 for p, _, _ in eps if oov_rate(p) >= OOV)
        print("eval_fuzzy_recall: %s  (%d episodes; mean OOV %.2f; %d>=%.2f trigger fuzzy)"
              % (name, len(eps), movv, nfire, OOV))
        print("  %-24s %6s %6s %7s" % ("arm", "hit@5", "hit@10", "MRR"))
        for arm, fn in (("word-BM25 (baseline)", word), ("char-3gram", char),
                        ("RRF fuse (always)", fused), ("augment-tail", augment)):
            h5, h10, mrr, n = hit_at(fn, eps)
            print("  %-24s %6.2f %6.2f %7.3f" % (arm, h5, h10, mrr))
        print()
    print("  gate: lift PROCEDURAL hit@10 with NO ALL-set regression (hit@5/MRR).")
    # VERDICT (2026-07-09, 36 ALL + 8 procedural episodes): char-ngram is a REAL typo/morphology-robust
    # signal - always-on fusion lifts procedural hit@10 0.12->0.38 (rescues "memeory"->"memory",
    # "dreams"->"dreaming"). BUT it costs clean-prompt precision (ALL MRR 0.337->0.257), and no clean
    # trigger separates the prompts that need it: OOV rate is 0.08-0.10 on BOTH sets (only 1-2 procedural
    # prompts are truly typo-heavy), and BM25 top-score doesn't discriminate hits from misses (the one
    # HIT had the LOWEST top-score). The precision-safe 'augment-tail' (append char-rescues after BM25's
    # untouched head) can't regress and gives a small ALL gain (+0.02 hit@10) but doesn't reach the
    # procedural win (rescues land past position 10). NOT WIRED: the only variant that captures the
    # procedural lift regresses ALL precision, and tuning keep/add to fix that on n=8 is the
    # non-stationary-snapshot trap (method_improving_the_brain_well). Re-run as the procedural episode
    # count grows (same gate as the class-boost lever); ship augment-tail if its +hit@10 holds. The
    # mechanism is filed, the eval is the gate - consistent with the walk-reach / cross-encoder rejects.
    #
    # WHAT SHIPPED INSTEAD (same night): the SURGICAL cousin - bm25.QueryNormalizer, edit-distance-1
    # spell-correction of only the OUT-OF-VOCAB query tokens ('mathmatically'->'mathematical') before
    # scoring. Fixes the SAME typo-driven miss but a clean prompt is BYTE-IDENTICAL (cannot be demoted),
    # where char-ngram fusion reorders every prompt's head. eval_prompt_recall bm25+spell arm lifts every
    # POOL-inclusion metric (hit@5 0.47=, hit@10 0.56->0.61, hit@20 0.75->0.81, rec@10 up); MRR dips
    # 0.333->0.327 (noise, and irrelevant to the seeder's top-5 POOL). edit1-only (edit2 was a hook-
    # latency bomb + the source of most mis-corrections); wired live into spread_recall's seed at ~0.7ms.
    # Lesson: for a typo problem, correct the QUERY surgically - don't blur the whole match with fuzzy
    # scoring (char-ngram) or add index vocabulary (HyPE); the narrowest fix that hits the failure won.


if __name__ == "__main__":
    main()
