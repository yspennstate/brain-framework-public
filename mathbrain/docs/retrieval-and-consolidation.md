# Retrieval and consolidation safety contract

This subsystem retrieves mathematical context and proposes follow-up reviews. It
does not decide mathematical truth. All examples and tests in the repository are
synthetic or elementary; no local research corpus is bundled.

## Retrieval signals

`index_signals` derives three in-memory channels:

1. Unicode-normalized lexical tokens;
2. abstract math tokens such as `op:forall`, `op:pow`, and `const:real`; and
3. SHA-256 fingerprints of notation-normalized, alpha-renamed formulae.

For example, `∀ y ∈ ℝ, y² ≥ 0` and
`\forall x \in \mathbb{R}, x^2 \geq 0` share the same mathematical signals.
Normalization is a retrieval heuristic, not symbolic equivalence or proof.

`retrieve` returns immutable hits plus a decision. Every hit separates lexical,
symbol, exact-formula, positive graph, and warning scores. Its trace records the
matched term or typed edge responsible for each contribution. Warning paths keep
a negative signed contribution but are given a positive surfacing score so they
cannot disappear inside an average relevance score.

The decision abstains with one of four explicit reasons:

- the query has no indexable signal;
- no corpus candidate matches;
- the best support is below the caller's threshold; or
- the best two candidates are closer than the caller's minimum margin.

`search` remains a compatibility wrapper that returns hits. New decision-making
code should call `retrieve` and must check `abstained`.

## Evaluation contamination guard

An evaluation must pass a `RetrievalGuard`. The guard is fail-closed when split
labels are required. It excludes:

- records outside the allowed training/reference splits;
- answer keys, solutions, fault annotations, and prior evaluation output;
- the active benchmark item and its paired variant;
- records sharing a prohibited source digest; and
- quarantined or denied records.

Filtering happens before scoring and graph propagation. Exclusions are returned
as structured reasons. The same guard is applied before consolidation, so a
test answer cannot re-enter through a dream-generated association.

## Signed mistake memory

`MistakeRecord` captures the exact failing step, cause, repair, affected
assumptions, optional counterexample, and prevention action. `ReflexRecord`
requires activation cues, ordered actions, required evidence, and a stop
condition. `memory_edges` links them with `fails_under` and `triggers` edges,
whose polarity is structurally negative.

## Quarantined replay

`consolidate` is a pure function. Slow-wave-style replay turns graph defects and
documented failures into specific review actions. REM-style replay compares only
abstract formula signals and may suggest an analogy review. Every result is a
deterministic `ConsolidationProposal` whose only allowed status is `quarantined`
and whose `can_promote_truth` value is always false.

A proposal is not `Evidence`, is never written as a verification event, and has
no promotion method. Attempting to treat it as verification evidence raises an
error. A human or independent verifier must perform the proposed action and feed
the resulting artifact through the ordinary evidence and promotion gates.
