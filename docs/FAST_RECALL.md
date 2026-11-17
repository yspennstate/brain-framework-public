# Fast recall

`dreaming/fastrecall.py` is the question-independent half of a `math_recall.py` search, prepared once and kept on
disk. `math_recall.py` uses it when it can and falls back to the ordinary path when it cannot; the command line does
not change.

## What it saves

A profile on 12 Oct 2026 found that a cold `math_recall.py` call spent about four fifths of its time on work that does
not depend on the question: reading every card and link from the store, computing each card's index terms, title
tokens and phrase bigrams, building the title and link maps, and creating the ONNX query encoder. `fastrecall.py`
does that work once and writes it to a marshal file keyed by the store state, the code and the `MATHBRAIN_*` /
`MATH_BRAIN_*` environment. Later calls load the file and score from it. The scores are the ones `retrieve()`
computes, with the same helpers, term by term, in the same order.

## When it steps aside

The fast path runs only on the `retrieve()` it was written against: `PIN_RETRIEVAL` holds the sha256 of that
`retrieval.py`, and `PINS_ALSO` lists other versions it was checked against, each by an A/B over the printed top eight. Any
other `retrieval.py`, a lexical document-frequency override, an expansion map or weight, a support
or margin floor, a guard, a store read that warned, or a prepared file that cannot be read sends the call down the
unchanged live path. marshal loads plain values only, never code.

At most `KEEP_FILES` (4) prepared states are kept, and at most `KEEP_MB` (800) megabytes of them; the newest is always
kept. One state was 370 MB on 28 Oct 2026.

| variable | effect |
|---|---|
| `MATH_RECALL_FAST=0` | always the live path |
| `MATH_RECALL_FAST_ENCODER=0` | fast search, query encoder built the usual way |
| `MATH_RECALL_CACHE_DIR=DIR` | where the prepared files live |
| `MATH_RECALL_FAST_KEEP_MB=N` | byte budget for kept states |
| `MATH_RECALL_FAST_DEBUG=1` | one line on stderr: fast or live, built or loaded, and why |

## Measured (17 Nov 2026, at commit c6a734a)

Sixty questions from a fixed recall bank, each run once on the live path (`MATH_RECALL_FAST=0`) and once on the fast
path, each call a fresh process on one machine. With the settings below, the 60 printed answers were identical
between the two paths and every fast call loaded its prepared state. Medians:

| call | live | fast |
|---|---:|---:|
| one wording | 7.6 s | 1.2 s |
| three wordings (`--or`) | 10.3 s | 1.5 s |
| `--id` | 1.9 s | 0.8 s |

The scope is that bank, with the other dials at their defaults.

## Running the same comparison

Two settings decide whether such a comparison measures what it claims to.

1. On POSIX, `recall_client.py` starts a resident `recall_server.py` the first time a fast call runs, and later fast
   calls in the same environment go to it. A job meant to time in-process recall must set `MATH_RECALL_SPAWN=0` and
   `MATH_RECALL_SERVER_DISABLE=1`, and should print the inherited `MATH*` / `BRAIN*` environment at its top.
2. When the prepared state exists, `math_recall.py` adds possessive-swap and number-word-swap wordings
   (`MATH_RECALL_POSSESSIVE` and `MATH_RECALL_NUMWORD`, both `swap` by default). The live path has no prepared state,
   so with the swaps on the two paths run different wordings and need not print the same answer. Set
   `MATH_RECALL_POSSESSIVE=0 MATH_RECALL_NUMWORD=0` on both sides. With the swaps off the live answers were the same
   as with them on, so the swaps touch only the fast path.

Leaving `MATH_BRAIN_SEMANTIC` unset does not turn the vector lane off here: both paths pass
`semantic_weight = MATHBRAIN_RECALL_SEM_WEIGHT` (default 2.0), and an explicit weight overrides
`semantic.enabled()`. To switch the lane off, set `MATHBRAIN_RECALL_SEM_WEIGHT=0`.
