# Public difficulty and shortcut calibration v2

Calibration v2 is a public-data gate for candidate hard-case generators. It does not call a
model, read the private challenge bank, or emit prompts, traces, answers, case identifiers,
fault lines, or oracle receipts. Its digest-bound output is aggregate evidence only. The
current report/input/case schema revisions are 5/4/3; the sealed profile remains named v2.
A freeze-ready input contains 200–400 cases; binding, loading, and report generation all
reject smaller diagnostic samples and oversized banks.

The outcome-blind selection and materialization step that precedes this scoring lifecycle is
specified in [calibration preregistration v3](calibration-preregistration-v3.md).

The intended lifecycle is one way:

1. Construct a `CalibrationFreeze.draft()`. Freeze-bearing evidence uses the sealed built-in
   profile; callers cannot relax thresholds and still produce an accepted frozen artifact.
2. Call `commit_seed(seed)` before binding or inspecting the calibration outcomes.
3. Run two genuinely separate public trace verifiers. Issue one digest-bound receipt for each
   implementation, using distinct verifier IDs, then admit the `CalibrationCase`. The distinct-ID
   check is an integration gate, not cryptographic proof of independence. A caller can forge two
   structurally distinct receipts. Therefore a hard-case adapter must recompute both verifier
   results over the bound case content; it must never forward stored receipts as evidence that
   independent execution occurred. `receipts_attest_semantic_independence` is always false.
4. Call `bind_cases(cases)` to commit the exact public set.
5. Run `generate_calibration_report(cases, state=..., seed=..., config=...)`.
6. Call `state.freeze(report, cases=cases, seed=seed, config=config)`. Freeze recomputes the
   complete report from the exact bound public inputs; it never treats caller-supplied metrics
   or gate rows as authority. A failed report cannot be frozen, and a frozen state cannot be
   mutated or unsealed.

The default full-case brain-off target is 0.45–0.75. Scores below 0.30 or above 0.85 are
explicit hard rejects; values in the intervening bands are reported as borderline and also
cannot freeze. Fault positions are evaluated by rank within each trace's eligible interior
lines declared explicitly by each case, not by raw line number or an assumption that every
interior line is eligible. Conditional rank entropy must be at least 0.75, the largest rank
share at most 0.25, and weighted total variation from the arity-specific uniform distribution
at most 0.25. Every eligible rank must have at least two actual observations in every audited
scope and arity; this is the minimum observed rank count, never `support / arity`. At least 70%
of faulty traces must survive cheap surface plausibility checks.

Every case declares an answer-free structural generator cell. Difficulty, position,
plausibility, and minimum-support/diversity gates run over five separately indexed partitions:
structural generator cell, family, family×template, shared template, and domain×fault mechanism.
The joint partition rejects Latin-square layouts whose family and template marginals are each
uniform while their pair fixes the answer rank. The aggregate
report emits only canonical indices and counts, never caller-controlled domain, mechanism,
family, template, or cell strings. The sealed profile requires 4–8 families, 4–16 templates,
2–4 structural cells, at least 40 cases per family/cell/domain×fault stratum, and at least 20
cases per shared template, plus cross-family/domain/mechanism diversity. The declared
domain×fault cells must equal the complete Cartesian product, and each family must contain at
least eight cases from every declared domain and fault class. Score-bearing traces must have
6–12 lines, at least four sorted unique eligible interior lines, and a fault among them.

Five bounded-feature standard-library Bernoulli baselines make exactly one top-ranked
faulty-line localization per case from prompt-only, location, length, numeric-summary, and
bag/character features. Every baseline runs twice: leave-family-out, and
within-family leave-template-out. The report stores case-level top-1 accuracy and the explicit
chance rate `mean(1 / eligible_arity)`. Pooled excess over chance must be at most 0.05 and the
worst observed family-fold excess at most 0.10. Global and opaque family-fold arity histograms
make both chance rates independently checkable. The latter is a deterministic engineering
envelope, not a confidence interval; Wilson or population-bound claims are intentionally absent.
Prompt-only features are crossed with each candidate position so a position encoded in prompt
text cannot disappear merely because the unconditioned prompt is constant within a case.
A bounded topic/domain classifier also predicts the multiclass fault mechanism under a
leave-family-out split. It is gated on excess macro recall over the `1/K` chance rate,
both pooled and in the worst family, so four-class accuracy of 0.50 cannot pass as if chance
were 0.50. Normalized-template collisions under different IDs and exact duplicate surfaces are
rejected before scoring. Receipt order, case order, hashed feature budgets, text sizes, family
count, and template count are canonical or bounded. Python entry points also require exact
built-in and calibration-record types rather than accepting behavior-changing subclasses.
Caller iterables use one-over-limit readers, so an unbounded case, trace, eligible-line, or
receipt stream fails at its sealed budget instead of being consumed without bound.

The command-line tool consumes a digest-bound canonical public input document, a data-bound
state, and a UTF-8 seed file. JSON rejects duplicate keys, non-NFC strings, reordered cases,
noncanonical bytes, and any payload whose complete input digest does not match:

```text
math-brain calibrate-v2 PUBLIC_INPUT.json \
  --state DATA_BOUND_STATE.json \
  --seed-file SEED.txt \
  --bundle CALIBRATION.bundle.json
```

It writes one deterministic bundle containing the report and either its matching frozen state
or `null` for a rejected run. Exit status 0 means every gate passed; exit status 2 means the
bundled report failed at least one gate.

The bundle is flushed and strictly reloaded under a same-directory temporary name before an
atomic no-replace link creates the final path. A crash before that link leaves no final artifact.
An identical rerun is an idempotent resume; a different or malformed existing destination fails
closed. `verify-calibration-v2 --bundle ...` strictly reloads and recomputes the entire bundle.
A local seed commitment and two caller-issued verifier IDs still do not prove external chronology or independent execution;
strong preregistration and independence claims require an externally timestamped plan and
separately controlled verifier artifacts.
