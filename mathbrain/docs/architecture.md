# Architecture

The system is organized as a sequence of independently auditable layers.

```text
explicit allowlist
      |
      v
privacy + provenance gate -----> quarantine (default)
      |
      v
structured mathematical records
      |
      +--> typed association/dependency graph
      +--> immutable claim/evidence ledger
      +--> mistake patterns and review triggers
      |
      v
verification router
  | numeric | symbolic | proof | Lean | citation | statement fidelity |
      |
      v
status promotion with scoped evidence
      |
      v
replay/consolidation: contradictions, stale claims, graph orphans, new tests
```

## 1. Privacy and provenance gate

Ingestion is deny-by-default. A strict run-local TOML manifest grants individual
source roots, never the home tree. The reader rejects symlinks, junctions, and
hardlinks, opens a stable descriptor, compares identity before reading, and
revalidates containment afterward. All derivations remain under the manifest's
gitignored audit root.

Publication is a second gate. Every public data artifact has a provenance
sidecar recording its hash, rights, concrete license, public source identifiers,
and human review. A context-aware scanner rejects local paths, credential
patterns, and forbidden categories without echoing matched values.

## 2. Structured records

The core record types are claim, definition, theorem, proof attempt, example,
counterexample, source, citation, error pattern, verification result, and open
question. Records use stable identifiers, schema versions, timestamps, and a
canonical content hash.

## 3. Typed graph

Edges have mathematical meaning: `depends_on`, `defines`, `proves`, `refutes`,
`generalizes`, `specializes`, `analogous_to`, `cites`, `verified_by`,
`fails_under`, and `triggers`. Retrieval combines lexical matches with bounded,
typed graph spreading. Negative edges such as `refutes` and `fails_under` are
first-class so a nearby warning cannot be averaged away as mere similarity.
Every edge also carries a polarity consistent with its type. Retrieval propagates
positive support and negative warning paths in separate channels. A negative
path has a negative signed trace while receiving a positive surfacing score, so
it is visible without being mistaken for evidence supporting the recalled claim.

Retrieval indexes three deterministic signal families: Unicode-normalized words,
abstract mathematical tokens, and SHA-256 fingerprints of notation-normalized,
alpha-renamed formulae. Results expose per-term and per-edge contributions.
The decision API abstains when nothing is indexable, no candidate matches, support
is too weak, or the top match is too ambiguous. Evaluation callers use a
fail-closed split guard; records without an allowed split label are unavailable.

## 4. Verification router

Claims are decomposed into obligations. The router selects evidence appropriate
to each obligation and preserves the distinction between:

- arithmetic reproduction;
- symbolic identity checking under explicit domains;
- randomized or exhaustive finite testing;
- human proof review;
- formal proof checking;
- formalization-fidelity review;
- citation entailment and locator review; and
- independent reproduction.

No single verifier is universal. Promotion rules require coverage at the claim's
actual scope.

## 5. Mistake memory and triggers

An error record stores the minimal failing step, cause, counterexample or repair,
affected assumptions, and a reusable trigger. Triggers are specific review
actions (for example, “division: prove denominator nonzero”), not vague reminders.
Their precision and measured lift are tracked; noisy triggers are revised.
Structured mistake records preserve a minimal failing step, cause, affected
assumptions, counterexample, repair steps, and prevention action. Reflex records
pair explicit activation cues with ordered actions, required evidence, and a
stop/abstention condition. Their graph links are typed negative warning edges;
neither record changes claim status.

## 6. Mathematical consolidation

A consolidation cycle does not invent facts. It replays verified and failed
work to propose bounded tasks: contradiction checks, missing-edge checks,
counterexample searches, stale-citation reviews, proof compression attempts,
and benchmark mutations. New claims remain unverified until ordinary gates pass.
Slow-wave-style replay prioritizes contradictions, missing evidence, graph
orphans, hub audits, and concrete reflex rehearsal. REM-style association uses
only abstract formula structure to suggest bounded analogy reviews. Both phases
return deterministic `quarantined` proposal objects. The proposal type rejects
truth-promotion capability and deliberately raises if treated as evidence.

## 7. Evaluation

The benchmark runner pre-registers seeds, item strata, solver budgets, and
metrics. Brain-on and brain-off conditions are paired and blinded. Primary
metrics include fault localization, fault classification, corrected conclusion,
false-positive rate on clean solutions, calibrated confidence, latency, and cost.
