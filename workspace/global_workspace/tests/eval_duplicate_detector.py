#!/usr/bin/env python3
"""Eval: duplicate-intent detection against realistic goal prompts.

Provenance: the floors recorded here were first measured 2026-07-10 on a
hand-labelled set of directive-shaped prompts. The committed fixtures are
invented sentences that keep the features the detector depends on: the
misspelled agent and verb forms the patterns accept, the continuation shapes
with their word gaps, and the token-overlap geometry of each pair.

Two questions, measured separately:

1. shape-recall - of prompts that semantically continue ANOTHER session's
   work, how many does looks_like_continuation() recognize? An earlier,
   stricter pattern set scored 1/6 on the labelled set; that miss motivated
   the current set.
2. shape-precision - of standalone directives, how many are wrongly called
   continuations? (A false shape only matters when topic overlap >= 0.15
   with a live agent also holds, but keep the shape itself honest.)

Plus the restatement rule: a goal re-issued minutes later in slightly
different words must clear the containment threshold; unrelated pairs must not.

Run: python eval_duplicate_detector.py   (prints a report, exits 1 on
regression below the recorded floor)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workspace import (looks_like_continuation, intent_overlap,  # noqa: E402
                       DUPLICATE_THRESHOLD)

# --- continuation positives: reference another session's / agent's work ---
CONTINUATION_POSITIVES = [
    # a session the user believes crashed (misspelled agent word)
    "there was a cluade tidying the build scripts earlier, i think it crashed halfway "
    "through unpacking, please take over what it was doing",
    # an agent named a few words after "there was" (word gap)
    "the boundary lemma draft has a hole near the end, there was just now a claude "
    "editing that file, carry the argument through",
    # work running on another machine
    "my parameter sweep has some shards running on the other box, give me one line "
    "on its progress",
    # "... i had you begin work on ..."
    "yesterday evening i had you begin work on bounding that sequence, finish the "
    "provable version",
    # misdirected prompt referencing another agent (misspelled agent word)
    "ignore my last message, it belonged to another agetn, carry on with the parser tests",
    # a frozen window while an agent runs the same lane (misspelled verb)
    "please launch a fresh terminal, the launcher hung and then it forze when i "
    "clicked it twice",
]

# --- standalone directives: must NOT be called continuations ---
STANDALONE_NEGATIVES = [
    "/goal spend the next 10 hours making the note search, the index and the memory tools better",
    "/goal for 10 hours, polish the memory tools and the search quality",
    "tonight, tune how recall ranks results and audit the refresh step of the index",
    "for 8 hours straight, hunt down scheduled jobs nobody has looked at and logs that "
    "seem dated, list every one",
    "/goal a graph theory preprint I saved claims a uniform bound but the general case "
    "skips a step. locate the missing step and repair it",
    "/goal my draft shows every starting point shrinks when the map is applied "
    "repeatedly. extend that result to stacked layers",
    "/goal pick a famous unsolved question, ideally in probability or combinatorics, "
    "and settle it",
    "/goal for 10 hours, keep the nightly jobs healthy and catch any that fail",
    "/goal draft a thousand ideas for a better memory system, then build them one by "
    "one, guided by recent research",
    "read my recent prompts and the notes, choose the most useful place to spend "
    "effort, and stay on it for 8 hours",
    "/goal for 8 hours, work on prose that sounds natural, covering every part of what "
    "makes writing feel human",
    "/goal develop the theory of random matrix spectra in high dimension from the "
    "experiments and tools we already have",
    "check that the index refreshes properly and nothing in it is stale",
    "can the constant be pushed to 1.8, or as far as it will go? go over the "
    "reference paper slowly",
    "halve the abstract, read all of it, and tighten anything wordy",
    "/goal search the spectral theory literature for something relevant, compare it "
    "with the notes, and refine it for 10 hours",
    "/goal review each hook in the framework and each watchdog, then audit, improve "
    "and debug them",
    "/goal spend the next 8 hours on the memory tools repo, improving it without a break",
    "fit a stronger ranking model, store the parameters, and aim for something exceptional",
    "/goal before the weekend, get the release scripts and the changelog ready",
]

# --- restatement pair: the same goal re-issued minutes apart ---
RESTATEMENT_PAIR = (STANDALONE_NEGATIVES[0], STANDALONE_NEGATIVES[1])
UNRELATED_PAIRS = [
    (STANDALONE_NEGATIVES[0], STANDALONE_NEGATIVES[11]),   # memory tools vs matrix theory
    (STANDALONE_NEGATIVES[4], STANDALONE_NEGATIVES[16]),   # math paper vs hooks audit
    (STANDALONE_NEGATIVES[6], STANDALONE_NEGATIVES[19]),   # open problem vs release scripts
]

RECALL_FLOOR = 5 / 6            # recorded 2026-07-10; regression fails the eval
MAX_FALSE_SHAPES = 2            # of the 20 negatives


def main():
    hits = [p for p in CONTINUATION_POSITIVES if looks_like_continuation(p)]
    misses = [p for p in CONTINUATION_POSITIVES if not looks_like_continuation(p)]
    false_shapes = [n for n in STANDALONE_NEGATIVES if looks_like_continuation(n)]
    recall = len(hits) / len(CONTINUATION_POSITIVES)

    print("continuation shape-recall: %d/%d = %.2f"
          % (len(hits), len(CONTINUATION_POSITIVES), recall))
    for m in misses:
        print("  MISS: %s..." % m[:90])
    print("false shapes on %d standalone: %d" % (len(STANDALONE_NEGATIVES), len(false_shapes)))
    for f in false_shapes:
        print("  FALSE: %s..." % f[:90])

    ov = intent_overlap(*RESTATEMENT_PAIR)
    print("restatement pair containment: %.2f (threshold %.2f) -> %s"
          % (ov, DUPLICATE_THRESHOLD, "FLAGGED" if ov >= DUPLICATE_THRESHOLD else "missed"))
    unrelated_bad = 0
    for a, b in UNRELATED_PAIRS:
        o = intent_overlap(a, b)
        if o >= DUPLICATE_THRESHOLD:
            unrelated_bad += 1
            print("  UNRELATED PAIR WRONGLY FLAGGED (%.2f): %s.. / %s.." % (o, a[:40], b[:40]))

    ok = (recall >= RECALL_FLOOR and len(false_shapes) <= MAX_FALSE_SHAPES
          and ov >= DUPLICATE_THRESHOLD and unrelated_bad == 0)
    print("eval: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
