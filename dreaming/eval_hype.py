#!/usr/bin/env python3
"""eval_hype.py - does index-time question generation (HyPE) lift prompt->read recall?

HyPE (Hypothetical Prompt Embeddings, 2024-25; the index-side twin of HyDE) closes the asymmetry
between task-phrased queries ("check the broker", "why does theta keep restarting") and
result-phrased memory text ("FOUND+FIXED: ... probe URL 404s"). At index time a model writes the
questions each memory ANSWERS; queries then match question text instead of having to share the
memory's declarative vocabulary. It was the one un-built model-gated lever from the 2026-07-09
field scan (query rewriting was REJECTED for this corpus; HyPE moves the model cost offline).

Ground truth is the same replay eval_prompt_recall uses: a session's first real owner prompt ->
the memories that session actually read, negatives = the whole corpus. Arm A scores the live index
text; arm B scores the same text + generated-question tokens appended per hype_questions.jsonl
(append AFTER the 1500-token cap, so questions are never crowded out). Same scorer (bm25.BM25),
same episodes - the delta is the finding.

Honesty constraints: questions are generated from each memory's own description/content ONLY,
never from the replay prompts; the enriched subset is chosen by a date rule (2026-06+ and undated),
independent of which memories the episodes read. Coverage is reported so a subset pilot can't
masquerade as full-corpus enrichment. Usage: python eval_hype.py
"""
import os, io, re, json, sys
from collections import defaultdict

DR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DR)
from bm25 import BM25, tokens, read_docs, SKIP_FILES

MEM = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory")
TRACE = os.path.join(DR, "retrievals.tsv")
LEDGER = os.path.join(MEM, "prompts", "PROMPTS.jsonl")
QUESTIONS = os.path.join(DR, "hype_questions.jsonl")
MAXSESS = 25


def episodes():
    reads = defaultdict(set)
    for i, ln in enumerate(io.open(TRACE, encoding="utf-8", errors="replace")):
        if i == 0:
            continue
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 4 and p[0] not in SKIP_FILES and not p[0].startswith("dream_"):
            reads[p[3][:12]].add(p[0])
    first = {}
    recs = []
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
        if r["session"] not in first:
            first[r["session"]] = r["text"]
    return [(first[s], reads[s]) for s in reads if 2 <= len(reads[s]) <= MAXSESS and s in first]


def load_questions():
    qs = {}
    try:
        for ln in io.open(QUESTIONS, encoding="utf-8", errors="replace"):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r.get("slug") and r.get("q"):
                qs.setdefault(r["slug"], []).extend(q for q in r["q"] if q)
    except Exception:
        pass
    return qs


def measure(bm, eps):
    h5 = h10 = h20 = 0
    mrr = 0.0
    per = []
    for prompt, readset in eps:
        ranked = [s for s, _ in bm.score(prompt, 30)]
        pos = next((i for i, s in enumerate(ranked) if s in readset), None)
        per.append((pos, ranked[0] if ranked else None))
        if pos is not None:
            if pos < 5:
                h5 += 1
            if pos < 10:
                h10 += 1
            if pos < 20:
                h20 += 1
            mrr += 1.0 / (pos + 1)
    n = len(eps)
    return (h5 / n, h10 / n, h20 / n, mrr / n), per


def main():
    eps = episodes()
    qs = load_questions()
    if not qs:
        print("eval_hype: no hype_questions.jsonl - generate questions first.")
        return
    docs = read_docs()
    in_corpus = {s for s in qs if s in docs}
    docs_b = dict(docs)
    nq = 0
    for slug in in_corpus:
        qtok = tokens(" ".join(qs[slug]))
        if qtok:
            docs_b[slug] = docs[slug] + qtok
            nq += len(qs[slug])
    # arm C: questions in a SEPARATE field, combined by max - the length-normalization-clean HyPE
    # (concatenation in arm B inflates doc length, so BM25's B=0.75 penalty can push a memory DOWN for
    # queries that don't hit the questions; a separate field with max() removes that confound).
    qdocs = {slug: tokens(" ".join(qs[slug])) for slug in in_corpus if tokens(" ".join(qs[slug]))}
    bm_main, bm_q = BM25.from_docs(docs), BM25.from_docs(qdocs)

    def measure_max(eps):
        h5 = h10 = h20 = 0
        mrr = 0.0
        for prompt, readset in eps:
            sc = {}
            for s, v in bm_main.score(prompt, 60):
                sc[s] = v
            for s, v in bm_q.score(prompt, 60):
                sc[s] = max(sc.get(s, 0.0), v)   # a memory ranks by its BEST-matching field
            ranked = [s for s, _ in sorted(sc.items(), key=lambda kv: -kv[1])[:30]]
            pos = next((i for i, s in enumerate(ranked) if s in readset), None)
            if pos is not None:
                if pos < 5:
                    h5 += 1
                if pos < 10:
                    h10 += 1
                if pos < 20:
                    h20 += 1
                mrr += 1.0 / (pos + 1)
        n = len(eps)
        return h5 / n, h10 / n, h20 / n, mrr / n

    a, per_a = measure(BM25.from_docs(docs), eps)
    b, per_b = measure(BM25.from_docs(docs_b), eps)
    c = measure_max(eps)
    print("eval_hype: %d episodes; %d/%d question-enriched memories in corpus (%d questions)"
          % (len(eps), len(in_corpus), len(qs), nq))
    print("  %-24s %6s %6s %6s %7s" % ("arm", "hit@5", "hit@10", "hit@20", "MRR"))
    print("  %-24s %6.2f %6.2f %6.2f %7.3f" % ("A: live index text", a[0], a[1], a[2], a[3]))
    print("  %-24s %6.2f %6.2f %6.2f %7.3f" % ("B: + questions (concat)", b[0], b[1], b[2], b[3]))
    print("  %-24s %6.2f %6.2f %6.2f %7.3f" % ("C: + questions (max field)", c[0], c[1], c[2], c[3]))
    # per-episode flips, and whether the mover was an enriched memory (mechanism, not just delta)
    flips = []
    for i, ((pa, _), (pb, topb)) in enumerate(zip(per_a, per_b)):
        ra = 999 if pa is None else pa
        rb = 999 if pb is None else pb
        if (ra < 10) != (rb < 10):
            flips.append((i, ra, rb, topb))
    for i, ra, rb, topb in flips:
        print("  flip ep%02d: rank %s -> %s   (%s) %s"
              % (i, ra if ra < 999 else "-", rb if rb < 999 else "-",
                 "GAIN" if rb < ra else "LOSS", (eps[i][0] or "")[:60].replace("\n", " ")))
    if not flips:
        print("  no hit@10 flips either way.")
    print("  read: B-A on hit@10 is the gate; flips show WHICH prompts moved. A subset pilot only "
          "moves episodes whose targets fall in the enriched set - scale coverage before judging null.")
    # VERDICT (measured 2026-07-09, full June/July coverage = 490 memories / 1260 questions):
    # NULL. Arm C (clean separate-field max, no length confound) == baseline on every metric; arm B's
    # tiny deltas were pure BM25 length-normalization noise (concatenation inflates doc length). HyPE
    # adds nothing because BM25 already scores the query against each memory's full 1500-token body,
    # which already carries the answer vocabulary - the same reason query-rewriting was rejected for
    # this small+specific-query corpus. NOT wired. Third model-gated recall lever measured & rejected
    # (with cross-encoder re-rank and query rewriting). hype_questions.jsonl kept as the measured
    # artifact so this isn't re-run from scratch.


if __name__ == "__main__":
    main()
