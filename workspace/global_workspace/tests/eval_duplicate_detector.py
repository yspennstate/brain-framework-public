#!/usr/bin/env python3
"""Eval: duplicate-intent detection against realistic goal prompts.

Provenance: the numbers recorded here were first measured 2026-07-10 against
verbatim prompts from the live 2026-07 prompt archive (10,558 prompts; 70
directive-shaped hand-labeled). Because this repo syncs off-machine, the
committed fixtures are structure-preserving PARAPHRASES of those prompts:
every load-bearing feature is retained - the typo families (cluade, agetn,
forze, memeory, "jeep working"), the continuation shapes with their word
gaps, and the token-overlap geometry of each pair. The eval re-measured
identically on the paraphrases before commit.

Two questions, measured separately:

1. shape-recall - of prompts that semantically continue ANOTHER session's
   work, how many does looks_like_continuation() recognize? The first,
   stricter pattern set scored 1/6 here; that miss motivated the current set.
2. shape-precision - of standalone directives, how many are wrongly called
   continuations? (A false shape only matters when topic overlap >= 0.15
   with a live agent also holds, but keep the shape itself honest.)

Plus the restatement rule: a same-evening re-issued goal pair (the owner
really did issue near-identical 10-hour goals three minutes apart on
2026-07-09) must clear the containment threshold; unrelated pairs must not.

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
    # the night's live incident (owner believed a session crashed)
    "there was a cluade working on getting a new console and some other bits, it was "
    "here a moment agao, but i think when it downaloded th console it forze and "
    "crashed, jeep working on what it was doing",
    # "there was just now a claude open with it" (word-gap + typo variant)
    "i was working on this matrix probelm try to show that in the large limit the "
    "exponent goes to 2, there was just now ca claude open with it, but the last part "
    "it still oepn, keep working until you solve this full thing",
    # work running on another machine
    "i was working on this conjecture it has parts running on the microsot box, how is "
    "the computation going? just tell me that nothing else",
    # "last night i had you start work on ..."
    "last night i had you start work on trying to prove a new open problem in math, "
    "keep working on the one we can prove theoretically",
    # misdirected prompt referencing another agent
    "sorry that was meant for another agetn keep working on improivng the brain",
    # owner's frozen window while an agent runs the same lane
    "open a codex for me, also i was trying to open one on a console and it forze, "
    "make sure i can open it",
]

# --- standalone directives: must NOT be called continuations ---
STANDALONE_NEGATIVES = [
    "/goal keep working for 10 hours to improve the AI tooling and memeory stuff",
    "/goal keep working on improing the AI memeory and toolisng stuff, work on "
    "this for 10 hours",
    "improve the memory recall attribution and check the observer data flow quality tonight",
    "keep working on this full tie for 8 hours, there are telgram bots that were not "
    "touched, and messages that look old, find every single bot, every single observer",
    "/goal I just downaloded a matrix paper. appearnetly in the general case, there is a "
    "gap in the proof to get to the uniform case. can you find that gap and close it",
    "/goal I was working on this paper where we have that evvery initial condition decays "
    "under itirations of the network. I wanted to extend it to many layers",
    "/goal find an open problem in math, prferble in probabilityu theory or deep "
    "learning, one that is famous, and solve it",
    "/goal keep workng, amkeu sure the observers are running smothly, work on this for 10 hours",
    "/goal come up with a list of 1000 way to improve the brain on this computer and then "
    "work on implementing them, use state of art understanding of how the brain works",
    "look at the last prompts I gave you and the brain and determine where i would like "
    "you to keep working, keep working for 8 hours, I just want you thinking",
    "/goal keep working on building a more human system, every single part of what makes "
    "a human human, work on this for the next 8 hours",
    "/goal keep working on the mathmatical theory for pricing futures, based on all of "
    "the simulaitons and data that we have, for pricing long dated contracts",
    "make sure that data is flowing though the obsever correct and is not stale",
    "can you try to get to 1.8 or the strongest number you can, what is the strongest "
    "number you can get? take a look at the refrence paper carfully as well",
    "cut the abstract by half, and read everything, if something is too wordy, condense it",
    "/goal find something in the futures pricing theory and applicaiton that you think is "
    "relitivem, take a look at the brain, and keep working on improving it for 10 hours",
    "/goal go though every observer in the stack, and every single watchgod, and "
    "audit and improve them, fix any bugs",
    "/goal take a look ath the memeory and helpers AI repo, and keep working on "
    "improving it, keep working for the next 8 hours without stoping",
    "try to get a stronger forcaster, use the gpu on this locla computer, and then "
    "save the papramters, get something exeptional",
    "/goal make sure that the rebalancing bots and observers are ready for the close, "
    "and that they are ready to trade",
]

# --- restatement pair: re-issued goal, minutes apart (paraphrased pair) ---
RESTATEMENT_PAIR = (STANDALONE_NEGATIVES[0], STANDALONE_NEGATIVES[1])
UNRELATED_PAIRS = [
    (STANDALONE_NEGATIVES[0], STANDALONE_NEGATIVES[11]),   # AI-memory vs pricing theory
    (STANDALONE_NEGATIVES[4], STANDALONE_NEGATIVES[16]),   # math paper vs observers audit
    (STANDALONE_NEGATIVES[6], STANDALONE_NEGATIVES[19]),   # open problem vs unrelated close
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
