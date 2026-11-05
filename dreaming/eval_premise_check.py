#!/usr/bin/env python3
"""eval_premise_check.py - would an ACTIVE superseded_by follow interrupt usefully, or be noise?

The gap it targets (measured in eval_stale_fact, 2026-07-09): when a query ASSERTS a stale premise
(phrased in the superseded memory's own words), the correction reaches the candidate pool only ~half
the time - the other half the session sees only the stale memory and nothing flags the false premise.
The fix is deterministic: when a prompt's STRONGEST seed is a memory carrying superseded_by:X and X
is not already in view, surface X alongside ("what you're assuming was corrected"). Given a stale
top-seed, the correction then surfaces 100% by construction - so the lift needs no eval. What DOES
need one is the interruption cost: how often does that rule fire on real prompts, and are the fired
cases ones where the flag would have been right?

Method: replay the archived owner prompts (memory/prompts/PROMPTS.jsonl, origin=owner) through the
LIVE seed computation (spread_recall._seeds_from_prompt - index lines + filenames + BM25 arm), apply
the firing rule, and print every fire for human judgment. Also counts the Read-path equivalent from
retrievals.tsv (a session reading a superseded memory gets the pointer to its correction).

Caveat (same as measure_mention_fires): the replay runs against TODAY's corpus/index, not the corpus
as of each prompt's date - a directional noise-rate read, not a time-machine. Read-only.
Usage: python eval_premise_check.py

VERDICT 2026-07-09 (measured, 3149 prompts x the then-10 pairs): SPLIT DECISION.
  PROMPT-PATH HELD: TOP-1 fires 10/3149 (0.32%) - cheap, but only ~2/10 would have helped; the other
  8 were weak-topical BM25 tops on vague prompts ("the IV surface we created" firing a May pair).
  Seed strength saturates at the BM25-arm cap, so "top seed" means top-RANKED, not LANDED-ON - the
  FoK concentration lesson again. TOP-2 doubles fires (0.70%), adds no useful ones. Re-gate only with
  a real concentration bar (absolute overlap/score floor), not rank.
  READ-PATH SHIPPED instead (spread_recall PostToolUse-Read): the session just read the stale memory,
  so the flag is unambiguous by construction; trace base rate 6/459 reads. Break-tested 4 cases.
  BONUS: the replay's noise exposed a real stamper bug - a THIRD-PARTY report ("[[B]] superseded by
  [[X]]" inside memory A) stamped the REPORTER A; schema_supersede now routes those to the judgment
  queue (writing-about is not being - the vocabulary-gates law, in machinery).

CONCENTRATION BAR RE-GATE: MEASURED, INSUFFICIENT (2026-07-09, same night, 9-pair set): line-overlap
  is 0 for EVERY fire (all fires come from the BM25 full-text arm, never index lines) so the narrow
  bar suppresses signal and noise alike; body-overlap runs 2-4 indiscriminately (noise "change it to
  extrahigh agent" scores 4, useful levered-grid case scores 3) - generic words drive body overlap as
  much as premise words. NO lexical bar separates useful from noise on this corpus; the only viable
  bar is SEMANTIC (prompt-embedding vs stale-memory cosine), which needs a model at hook time - not
  laptop-viable; box-side only. Prompt-path stays HELD. (Fire-set details shift with the index - the
  non-stationary snapshot caveat - but the no-separation verdict held across two snapshots.)
"""
import io, json, os, sys

DR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DR)
MEM = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory")
LEDGER = os.path.join(MEM, "prompts", "PROMPTS.jsonl")

import spread_recall as sr


def load_owner_prompts():
    out = []
    try:
        for ln in io.open(LEDGER, encoding="utf-8", errors="replace"):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r.get("origin", "owner") == "owner" and r.get("text"):
                out.append(r["text"])
    except Exception:
        pass
    return out


def main():
    superseded = sr._load_superseded()
    if not superseded:
        print("eval_premise_check: no superseded.tsv pairs - nothing to evaluate")
        return
    idx = sr._index_lines()
    prompts = load_owner_prompts()
    print("eval_premise_check: %d superseded pairs vs %d archived owner prompts" % (len(superseded), len(prompts)))

    fires_top1, fires_top2 = [], []
    for p in prompts:
        words = sr._words(p)
        if len(words) < 2:
            continue
        seeds, lex = sr._seeds_from_prompt(words, idx)
        if not seeds:
            continue
        ranked = sorted(seeds.items(), key=lambda kv: -kv[1])
        visible = set(seeds) | set(lex)
        for rank, (slug, strength) in enumerate(ranked[:2]):
            corr = superseded.get(slug)
            if not corr or corr in visible:
                continue
            rec = (slug, corr, strength, ranked[0][1], p.replace("\n", " ")[:160])
            if rank == 0:
                fires_top1.append(rec)
            fires_top2.append(rec)

    for name, fires in (("TOP-1 (strongest seed is stale)", fires_top1),
                        ("TOP-2 (stale in two strongest)", fires_top2)):
        print("\n%s: %d fires / %d prompts (%.2f%%)" % (name, len(fires), len(prompts),
                                                        100.0 * len(fires) / max(1, len(prompts))))
        for slug, corr, strength, top, p in fires:
            print("  stale=%s  ->  %s   (seed %.1f vs top %.1f)" % (slug, corr, strength, top))
            print("    prompt: %s" % p)

    # CONCENTRATION BAR (the verdict's named re-gate condition): does an ABSOLUTE landed-on requirement
    # - >= K of the prompt's words carried by the stale memory itself (FoK's concentration principle,
    # one layer up) - separate the useful fires from the weak-topical noise? Two strictness surfaces:
    # the memory's index/description line (narrow, what FoK uses) and its full-body BM25 token set
    # (generous). Printed per TOP-1 fire so a human judges whether any bar keeps signal and kills noise.
    try:
        from bm25 import BM25
        bm = BM25()
        print("\nCONCENTRATION BAR (per TOP-1 fire: prompt-word overlap with the stale memory)")
        for slug, corr, strength, top, p in fires_top1:
            pw = sr._words(p)
            line_w = sr._words(idx.get(slug, ""))
            body_w = set(bm.tf.get(slug, {}).keys())
            ov_line = len(pw & line_w)
            ov_body = len(pw & set(w for w in body_w if len(w) >= 3))
            print("  line-overlap %2d   body-overlap %2d   stale=%s" % (ov_line, ov_body, slug[:56]))
            print("      prompt: %s" % p[:120])
    except Exception as e:
        print("concentration-bar arm failed: %r" % e)

    # Read-path: how often does a session READ a superseded memory (each such read would get the note)?
    n_reads = 0
    hits = {}
    try:
        with io.open(os.path.join(DR, "retrievals.tsv"), encoding="utf-8", errors="replace") as f:
            next(f, None)
            for ln in f:
                parts = ln.rstrip("\n").split("\t")
                if len(parts) >= 4:
                    n_reads += 1
                    slug = parts[0][:-3] if parts[0].endswith(".md") else parts[0]
                    if slug in superseded:
                        hits[slug] = hits.get(slug, 0) + 1
    except Exception:
        pass
    print("\nREAD-PATH: %d/%d retrieval-trace reads were of superseded memories (each would get the pointer)"
          % (sum(hits.values()), n_reads))
    for s, c in sorted(hits.items(), key=lambda kv: -kv[1]):
        print("  %3d  %s  ->  %s" % (c, s, superseded[s]))


if __name__ == "__main__":
    main()
