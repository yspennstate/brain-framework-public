# Hard evaluation v2 acceptance matrix

This matrix turns the v2 design into falsifiable release gates. A feature is not
accepted because its implementation exists or its ordinary tests pass. It is
accepted only when the named evidence can be reproduced from public development
or calibration data without opening a final holdout.

## Case construction

| Risk | Required invariant | Adversarial evidence |
| --- | --- | --- |
| Short traces | Every score-bearing trace has 6--12 meaningful lines | Boundary tests reject 5 and 13 lines; no padding-only line is admitted |
| Position shortcut | The causal fault is interior and its line is not inferable from the family | Position histogram has maximum share at most 0.25; poisoned final-line-only banks fail |
| Broken suffix | Lines after the fault remain coherent under the faulty state | Replacing only the root line restores exact replay; mutating a downstream dependency fails |
| Announced trap | Prompt and trace do not name the target mechanism | Registered token and character baselines remain below the shortcut ceiling |
| Repair/witness duplication | A repair is a replacement line; a witness is independent disconfirming evidence | Copying the repair into the witness field fails, and vice versa |
| Weak oracle | Two independent checks agree on validity, repair, and witness where feasible | Poisoned oracle, repair, and witness fixtures are rejected by the independent path |
| Clean-case asymmetry | Clean foils retain the suspicious operation under hypotheses that justify it | A rule that marks the operation itself as faulty loses specificity on matched foils |
| Serialization escape | Case documents are strict, bounded, canonical, and duplicate-key rejecting | Oversize strings, extra keys, nonfinite numbers, duplicate keys, and stale digests fail closed |

The structural validator must inspect dependency content, not just count strings.
At minimum, each line has an explicit output symbol and dependency set; the fault
has at least one downstream dependent; and the final line transitively depends on
the fault. A trace made longer by splitting one algebraic identity into empty
fragments is not evidence of increased difficulty.

## Batch design

| Risk | Required invariant | Adversarial evidence |
| --- | --- | --- |
| Fixed faulty quota | Faulty prevalence varies from 0.35 through 0.65 across batches | Every case is clean and faulty equally often over 20 pairs while batch totals vary |
| Condition mismatch | Brain-on and brain-off see byte-identical cases and ordering | Any changed view, variant, order, schema, or runtime digest rejects the pair |
| Empty-control cue | Brain-off receives a length-matched, relevance-disjoint active control | Empty, shorter, answer-bearing, or topically overlapping control packets fail |
| Partial-credit ceiling | Primary success is all-or-nothing per complete case | Correct verdict with wrong root, family, repair, or witness scores zero on a faulty case |
| Pseudoreplication | Support counts only strict positive case-cluster effects | Repeating two effective cases across twenty pairs still supplies only two independent units; zero deltas never increment the positive-case gate |
| Missing-output bias | Timeouts and malformed or missing outputs remain in the denominator | Dropping a failed case or pair invalidates completeness rather than raising the score |

## Calibration

Calibration is generator-cell based. It cannot cherry-pick individual cases and
cannot tune on a failed final study.

| Gate | Admission region | Mandatory rejection fixture |
| --- | --- | --- |
| Brain-off full-case success | target 0.45--0.75; hard reject above 0.85 or below 0.30 | trivially easy and effectively impossible synthetic banks |
| Surface baselines | case-level top-1 excess over `mean(1 / eligible arity)` at most 0.05 pooled and 0.10 in the worst family fold | label token, location, length, number-pattern, and formatting leaks |
| Position balance | conditional rank entropy at least 0.75, maximum share at most 0.25, total variation at most 0.25, and every eligible rank observed at least twice | all-final, family-fixed, and family-template-fixed position banks |
| Domain prediction | leave-family-out macro-recall excess over `1 / mechanism classes` at most 0.10 pooled and in the worst family | one-domain-per-mechanism bank |
| Cheap plausibility | at least 0.70 of faulty traces pass registered checks | obvious parity, range, feasibility, type, and substitution violations |
| Duplicate surfaces | no reused surface or template across required disjoint folds | paraphrase-free duplicates and template identifiers shared across folds |

The calibration report is aggregate-only. It may contain counts, rates, bounds,
digests, and commitments. It must reject prompt text, trace lines, case identifiers,
oracle payloads, answer keys, repairs, and witnesses. A report that can be used to
reconstruct a calibration answer is a data-boundary failure.

## Freeze sequence

The final seed must not exist before all earlier stages are immutable:

1. Freeze generator code and public generator-cell definitions.
2. Freeze independent verifiers and canonical serialization.
3. Freeze retrieval policy and the active-control construction.
4. Freeze solver prompt, response schema, scorer, runtime policy, and statistical gates.
5. Bind the passing public calibration report and all preceding digests.
6. Commit a one-way seed commitment.
7. Materialize the final holdout under the separately controlled evaluator.

Reordering these steps, regenerating after an unfavorable result, or improving
anything after the final seed commitment burns the study. Infrastructure retries
are permitted only under the preregistered rule, before outcome access, against
the identical sealed commitment.

## Benefit claim

A benefit claim requires all gates together at the independent-case level: mean
full-case delta at least 0.04, exact one-sided case sign-flip p-value at most 0.05,
case-bootstrap interval lower bound above zero, positive median case delta, and at
least 12 strictly positive cases. The same mean, sign-flip, interval, median, and
strict-positive gates run separately on faulty-case outcomes so clean gains cannot
hide lost fault detection. Clean-specificity loss must be at most 0.02, and no
sufficiently populated preregistered domain, mechanism, error-family, template, or
difficulty stratum may regress beyond its sealed limit or hide an easy/hard ceiling.
Completeness, isolation, retrieval, contamination, identity, and runtime evidence
are conjunctive gates rather than footnotes.

Passing public generators and calibration only establishes readiness to run a new
study. It does not establish that retrieval helps. The opened v1 holdout and its
0.95 brain-off result remain historical evidence and can never be promoted into a
v2 final result.
