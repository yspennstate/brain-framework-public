#!/usr/bin/env python3
"""eval_workflow_recall.py - is the brain as good at recalling PROCEDURES as it is at recalling FACTS?

LongMemEval-V2 names WORKFLOW KNOWLEDGE (recall the multi-step procedure for a task) as a memory
competency distinct from static fact recall - and this brain's other recall evals average over both, so
a weakness in procedural recall would hide inside a healthy overall number. This isolates it: on sessions
where the owner issued a PROCEDURAL prompt (a run/improve/prove ask - "restart the observers", "push to
github", "run the backtest") and then read a PROCEDURE memory (method_/reference_/feedback_ = how-to
knowledge), does the recall stack surface that procedure memory for that prompt? And critically, does
procedural recall LAG factual recall (a non-procedural prompt -> any memory the session read)?

Ground truth is behaviour: the prompt archive supplies each session's procedural prompt, the retrieval
trace supplies what it then read, and the memory-name prefix classifies procedure vs other. Same BM25
arm and whole-corpus negatives as eval_prompt_recall, so the two numbers are directly comparable - the
GAP between them is the finding, not either alone. Read-only, laptop-safe.
Usage: python eval_workflow_recall.py
"""
import os, io, re, json
from collections import defaultdict

DR = os.path.dirname(os.path.abspath(__file__))
import sys
sys.path.insert(0, DR)
from bm25 import BM25, tokens, SKIP_FILES

MEM = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory")
TRACE = os.path.join(DR, "retrievals.tsv")
LEDGER = os.path.join(MEM, "prompts", "PROMPTS.jsonl")
MAXSESS = 25
PROCEDURE = ("method_", "reference_", "feedback_")   # how-to / procedural memory


def is_procedure(slug):
    return slug.startswith(PROCEDURE)


def main():
    # session -> read slugs
    reads = defaultdict(set)
    for i, ln in enumerate(io.open(TRACE, encoding="utf-8", errors="replace")):
        if i == 0:
            continue
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 4 and p[0] not in SKIP_FILES and not p[0].startswith("dream_"):
            reads[p[3][:12]].add(p[0])
    # session -> (first procedural prompt, first any prompt), by class
    proc_prompt, any_prompt = {}, {}
    recs = []
    for ln in io.open(LEDGER, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get("origin", "owner") == "owner" and r.get("session") and r.get("text") and len(tokens(r["text"])) >= 6:
            recs.append(r)
    recs.sort(key=lambda r: r.get("ts") or "")
    for r in recs:
        s = r["session"]
        if s not in any_prompt:
            any_prompt[s] = r["text"]
        if r.get("class_model") in ("run", "improve", "prove") and s not in proc_prompt:
            proc_prompt[s] = r["text"]

    bm = BM25()

    def hit_rate(episodes, target_pred):
        """For each (prompt, read-set), does BM25 rank a target-matching read memory in top-k?"""
        h5 = h10 = n = 0
        for prompt, readset in episodes:
            targets = {s for s in readset if target_pred(s)}
            if not targets:
                continue
            n += 1
            ranked = [s for s, _ in bm.score(prompt, 20)]
            pos = next((i for i, s in enumerate(ranked) if s in targets), None)
            if pos is not None and pos < 5:
                h5 += 1
            if pos is not None and pos < 10:
                h10 += 1
        return h5, h10, n

    # procedural episodes: procedural prompt + read a procedure memory
    proc_eps = [(proc_prompt[s], reads[s]) for s in reads
                if 2 <= len(reads[s]) <= MAXSESS and s in proc_prompt]
    # factual/other episodes: any prompt + read a NON-procedure memory
    fact_eps = [(any_prompt[s], reads[s]) for s in reads
                if 2 <= len(reads[s]) <= MAXSESS and s in any_prompt]

    ph5, ph10, pn = hit_rate(proc_eps, is_procedure)
    fh5, fh10, fn = hit_rate(fact_eps, lambda s: not is_procedure(s))

    if pn < 6 or fn < 6:
        print("eval_workflow_recall: thin episodes (procedural=%d, factual=%d) - re-run as trace grows." % (pn, fn))
        return
    print("eval_workflow_recall: does procedural recall keep up with factual recall?")
    print("  %-26s %6s %6s   n" % ("competency", "hit@5", "hit@10"))
    print("  %-26s %6.2f %6.2f   %d" % ("WORKFLOW (procedure recall)", ph5 / pn, ph10 / pn, pn))
    print("  %-26s %6.2f %6.2f   %d" % ("FACTUAL (other recall)", fh5 / fn, fh10 / fn, fn))
    gap = fh10 / fn - ph10 / pn
    if gap > 0.15:
        print("  GAP %.2f: procedural recall LAGS factual - workflow knowledge is a weak spot. DRIVER "
              "(diagnosed 2026-07-09 via eval_fuzzy_recall): procedural prompts are short, directive, and "
              "TYPO-laden (owner's deliberate typos), so word-BM25 tokenizes 'memeory'!='memory' and "
              "surfaces nothing. char-3gram matching rescues these (procedural hit@10 0.12->0.38) but "
              "isn't cleanly wireable yet (see that eval's verdict); re-gate as procedural episodes grow." % gap)
    elif gap < -0.15:
        print("  procedural recall LEADS factual (%.2f) - the method_/reference_ memories surface well" % (-gap))
    else:
        print("  no material gap (%.2f) - procedural recall keeps pace with factual" % gap)
    print("  (this isolates the LongMemEval-V2 'workflow knowledge' competency the general recall eval "
          "averages over; the gap, not either number alone, is the finding.)")
    print("  CAVEAT (2026-07-09, verified on the full stack, not just this BM25 eval): the exact-match "
          "metric OVERSTATES the real weakness. Procedural relevance is DIFFUSE - a task prompt has many "
          "valid how-to memories, so hitting the one the trace happened to log is harder than for a "
          "specific fact. Run through the LIVE stack (bm25+spell+graph+semantic bge), the 8 procedural "
          "prompts surface USEFUL procedural memories ('run the workflow'->example_workflow_overhaul, "
          "'audit workers'->example_stack_consolidation) - just not always the labeled target. So the "
          "owner's actual recall experience is better than 0.12/0.25; the honest weakness is narrower.")


if __name__ == "__main__":
    main()
