# Mistake-first recall

Recall ranks memories by relevance, and relevance alone treats a scar like any
other note. That is the wrong default for a corpus whose most expensive
memories are records of things going wrong: when a prompt wanders near
territory that has burned before, the audit or scar memory should be competing
at the front of the delivered list, not sitting just below the cut. This
document records the severity-weighted tilt that implements that preference,
and the two structural failures it is built to avoid.

> **Where the code is in this repository.** This page describes the packaged library
> edition (`brain/mistake_weights.py`, `brain/recall.py`, `tests/test_mistake_weights.py`),
> which is not shipped here. In this repository the builder is
> `dreaming/build_mistake_weights.py` and its consumer is the spreading walk in
> `dreaming/spread_recall.py`, which reads `mistake_weights.tsv` to raise a scar's inflow,
> relax its margin when severity is 0.8 or more, and floor its habituation, rather than
> multiplying a blended score. The severity rules differ slightly (an `audit_` node is
> `0.35 + 0.65 * charge`); the builder's docstring is the current contract. Both
> versions fail open: no artifact means no change to recall.

## Design

An offline builder (`build_mistake_weights.py` here) derives a severity map
`slug -> [0, 1]` over mistake-class memories from signals the corpus already
keeps, with no new judgment and no new metadata:

- filename prefix (`audit_` / `mistake_` / `scar_`, separators folded), ramped
  by the salience layer's affective charge for that memory: severity
  `0.5 + 0.5 * charge`;
- being the source of a compiled reflex row in `dreaming/instincts.tsv`, read
  through the firing contract (`brain/instincts.fire_rows`): severity is that
  row's charge;
- one curated hop: direct wiki-link neighbours of a strong node
  (severity >= 0.8) inherit 0.6 times its severity. Links are read with the
  corpus's weight-aware parser, so a declared `[[slug|80]]` resolves to its
  plain target. Inheritance never cascades.

Special and ledger pages (the `brain/graph_edges.SKIP` set) are never mapped;
mapped severities are floored at 0.20. The artifact,
`dreaming/mistake_weights.tsv`, lives beside the corpus's other derived
sidecars and is written atomically under the shared memory write lock. The
build is deterministic: the same corpus and sidecars produce a byte-identical
artifact.

At recall time the migrated pipeline multiplies each candidate's blended score
by `1 + 0.10 * severity` (`MISTAKE_TILT` in `brain/recall.py`) and reranks.
Delivery obeys two contracts:

- **Fail-open identity.** With no artifact, a corrupt artifact, or an
  artifact naming no live memory, the delivered ranking is byte-identical to
  the pre-tilt pipeline. The tilt path is guarded on a non-empty severity map,
  and a severity of zero multiplies a score by exactly 1.0.
- **No-eviction floor.** Every member of the unweighted top-K is always
  delivered. A tilted newcomer earns delivery only by ranking inside the
  tilted top-K, and at most two newcomers extend the delivered list
  (`MISTAKE_EXTRA_SLOTS`); floor members are never pushed out to make room.

The floor must survive delivery, not just ranking: the CLI and MCP paths wrap
recall in the context governor's selector, whose hard cap now honors the same
extension allowance — trimming back to the bare limit there would have
re-evicted exactly the hits the floor protects. The governor's byte ceiling
still bounds the delivered context, and its relative-evidence pruning keeps
its existing authority over weak-scored tails. The pre-migration legacy
scorer is untouched.

## Why the floor rule exists

The naive version of this feature — boost scar scores and take the top K — was
measured on the reference deployment before this port, replaying real prompts
against the same corpus with the boost on and off. It failed in two ways:

- **Displacement.** A boosted scar outranked an ordinary relevant memory and
  pushed it out of the delivered budget. The prompt still got an answer, but a
  weaker one, and nothing recorded the loss.
- **Silencing.** In the worst cases the displaced memory was the one hit that
  mattered, so a good recall disappeared entirely — a regression caused by a
  layer whose purpose is to prevent regressions repeating.

A measurable fraction of real prompts lost a good hit this way. With the
no-eviction floor and the bounded extension in place, the same replay showed
every change to a delivered list was a pure addition. That result is the
reason the floor is a contract here rather than a tuning choice.

## Status

Using the readiness vocabulary of `docs/SECURITY-READINESS.md` (Proven,
Partial, Open, Contradicted):

- **Proven — fail-open identity.** With the artifact absent, corrupt, or
  irrelevant, delivered output is byte-identical to the pre-tilt pipeline.
  Evidence: `tests/test_mistake_weights.py`, `TestFailOpenIdentity` (both the
  migrated and legacy paths).
- **Proven — no-eviction floor.** Under adversarial weights (severity far
  above 1, NaN, negative), every unweighted top-K hit survives, the list
  extends by at most two, and newcomers must earn a tilted top-K rank. The
  extension also survives the context governor's delivery cap. Evidence:
  `tests/test_mistake_weights.py`, `TestNoEvictionFloor`, including a direct
  contract test on `_tilted_selection` and an end-to-end test through
  `select_relevant_hits`.
- **Partial — severity derivation.** The map is deterministic, bounded, and
  explainable (each row records its winning signal), but the sources are
  heuristics: a filename convention, an affective-charge estimate, reflex-row
  charge, and one hop of link inheritance. Nothing has judged whether 0.9
  versus 0.6 is the right ordering of two particular scars.
- **Open — live precision of the added surfacings.** The tilt adds at most
  two memories per recall; whether those additions are worth their context
  cost on this repository's own corpus has not been measured. That requires a
  judged evaluation over real prompts here, and until it exists the tilt must
  stay in its current shadow posture — reorder and extend, never gate —
  consistent with the stance `brain/reflexes.py` takes for reflexes: surfaced
  for judgment, never blocking.
