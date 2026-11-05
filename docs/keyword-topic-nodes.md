# Keyword topic nodes

The association graph connects memories to memories. That leaves a gap: the words the user
actually types are not objects in the graph, so a prompt about a recurring subject reaches only
the memories whose text happens to overlap it. This layer makes the subject itself a node.

## What a topic node is

`build_keyword_nodes.py` writes `keyword_nodes.tsv`, one row per subject:

```
keyword	kind	weight	members
leverage	member	0.137	slug-a,slug-b,...
levrage	alias	0.137	leverage
```

Candidate vocabulary comes from the user's own prompts — a word they never type is not their
keyword, however common it is in the corpus. Membership comes from the memory files, matched on
**token boundaries**. Weight is `1/sqrt(members)`, so a broad subject cannot out-shout a sharp
one.

Membership was originally a substring test. On a ~2,700 memory corpus that put 57% of
memberships on memories that never used the word: `aws` matched "laws" and "flaws", `grid`
matched "gridlock", and three-letter subjects like `eat` and `pos` each reached several hundred
unrelated memories. Every test passed, because the tests checked that membership existed rather
than that it was right. A subject pointing at the whole corpus carries no information, and this
layer amplifies, so the noise was multiplied rather than diluted.

## Graded diffusion

During recall, a subject the text names boosts its members. The boost is graded:

| what it boosts | multiplier |
|---|---|
| a subject's mistakes | 3.0 |
| a subject's ordinary information | 1.7 |

A flat 3.0 across both measured **backwards** — mistake-class fires fell from 118 to 104 on 193
real prompts, because a subject's ordinary bulk out-competed its scars. Splitting the gain fixed
it: mistake-class fires went 107 → 129.

Two caps keep a broad subject from flattening the walk. `TOPIC_TOP_K` bounds how many members one
subject may light, and `TOPIC_MAX_UNION` bounds the union across every subject a text names — the
per-subject cap alone let a prompt naming many subjects reach an 843-slug seed set, which lights
everything and therefore distinguishes nothing.

## Keyword nodes inside the walk

`kw:<term>` nodes also join the association graph, but as **routers, not destinations**: they
relay activation between the memories under a subject and are excluded from the candidate set,
because "this reminds me of kw:volume" is not a memory.

Wiring all of them in at once made recall worse — it silenced ten good fires per 196 prompts,
because high-degree keyword nodes reroute the walk instead of extending it. Two changes earned
the layer back:

1. **A no-silencing floor.** The keyword arm may only speak where the memory-only graph was
   already going to speak, or was silent. Fires become a superset; losses are zero by
   construction rather than by luck.
2. **An on-demand overlay.** Only subjects the text actually names get their edges attached.
   Merging all 87k keyword edges into an 8.2k memory graph cost ~680ms per recall against a
   ~123ms baseline, and the subjects the user was not talking about contributed nothing but that
   cost.

Result on 200 real prompts: fires 165 → 171, mistake-class 124 → 129, **zero lost**.

## Misspellings

The user's spelling and the corpus's spelling are often different — they type `levrage`, the
memories say `leverage`. Alias rows bridge that.

Aliases are limited to spellings the user has explicitly supplied. This is deliberate and it
replaced two earlier attempts:

- Requiring the alias to be a word the corpus never uses admitted `apple→apply`,
  `baking→waking`, `accent→accept` — real words the corpus simply never happened to contain.
- Restricting to typo *shapes* (adjacent transpositions and interior insertions/deletions) killed
  every counterexample in that list, and still admitted `bride→bridge` and `arise→raise`. A shape
  rule does not remove the failure, it moves the boundary.

A heuristic that survives every counterexample its author thought of is evidence about the
author's imagination. Adding an alias now requires an observed spelling plus a regression control.

## Checking it

`brain_regression_gate.py` verifies the properties rather than the status lights: membership is
token-exact when recomputed from the memory files (not from the artifact's own rows), aliases are
legal, recall still fires, and the keyword arm silences nothing. It reports PASS, FAIL, or
INCOMPLETE — a check that cannot say "I could not inspect this" reports its blind spots as
success.
