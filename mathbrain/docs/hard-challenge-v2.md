# Public hard challenges, version 2

Hard challenge v2 is public development material. It does not read, replace,
compare against, or expose the historical sealed challenge bank. The module has
no holdout reader, retrieval path, unsealing path, or model runner. Its cases are
generated from public seeds and certified with exact mathematics.

```python
from math_brain.hard_challenge_v2 import (
    FamilyV2,
    generate_development_case_v2,
    generate_public_calibration_bank_v2,
)

case = generate_development_case_v2(FamilyV2.GENERALIZED_CRT_PERIOD, 24)
passed, evidence = case.verify_solver_output(case.expected_solver_output)
calibration_cases = generate_public_calibration_bank_v2()
```

## Six independent design coordinates

A development seed uses a mixed-radix encoding:

```text
position                 = seed % 8
discourse template       = (seed // 8) % 4
derivation family        = (seed // 32) % 4
fault mechanism          = (seed // 128) % 2
parameter-construction   = (seed // 256) % 2
numeric instance         = seed // 512
root line                = 3 + position
```

`encode_development_seed_v2` is the checked inverse constructor for these
coordinates. Changing position, discourse template, derivation family, or fault
mechanism cannot change the answer-free parameter record. Changing the
parameter-construction cell changes the mathematical instance, not just a label.
The selected wrong claim is deliberately absent from `case.parameters`; every
oracle derives it from the sealed fault coordinate.

The word *family* has two distinct meanings and the API keeps them separate:

- `FamilyV2` is the mathematical domain/error family: generalized CRT, bounded
  compositions, negative-edge shortest paths, or forced recurrence resonance.
- `DerivationFamilyV2` is a cross-domain organization of a typed derivation:
  `forward_invariant`, `endpoint_first`, `alternating_cross_check`, or
  `reverse_back_check`.

The four derivation families genuinely reorder the mandatory and detail
transitions in the semantic program. They are not aliases for a math topic, a
discourse renderer, or metadata pasted onto the same trace. The four discourse
templates remain a separate axis: numbered records, ledger entries, imperative
register updates, and postfix checkpoints.

Each trace also contains eight distinct, solver-visible relational claim roles.
The selected corrupted role is determined from numeric instance, derivation
family, and discourse template through a fixed shift table; it never consults
the position or fault-mechanism coordinate. All eight roles remain in every
trace. Consequently, moving the root does not move one recognizable “final
answer” role, and neither fault mechanism selects a privileged target name.

The parameter cells are `table_driven` and `constructive`:

| Domain | Table-driven cell | Constructive cell |
| --- | --- | --- |
| Generalized CRT | Select a vetted non-coprime modulus pair | Build moduli from a shared factor and a coprime reduced pair |
| Bounded compositions | Select the first and second bounds from crossed tables | Construct the bounds from independent quotient/remainder coordinates |
| Negative-edge shortest path | Start from direct labels and derive the improving edge | Start from the true improved label and construct the direct labels around it |
| Forced recurrence | Use roots 2–4 with one initial-value family | Use roots 5–7 with a disjoint initial-value family |

The ordinary public bank accepts one to 96 distinct seeds and crosses them with
the four mathematical domains. The default 64 seeds therefore produce 256
cases; `range(96)` produces 384. This convenience bank is not the calibration
design and should not be treated as balanced across the higher seed bits.

## Fixed 320-case calibration schedule

`generate_public_calibration_bank_v2` implements the versioned schedule
`public-hard-v2/orthogonal-320/3`. It contains 320 unique cases:

```text
4 mathematical domains
× 4 cross-domain derivation families
× 4 discourse templates
× 5 repetitions
= 320 cases
```

The mechanism flip mask, root-permutation table, and relational-role shifts are
fixed constants, not a runtime search. `calibration_schedule_digest_v2()` binds
those constants, the root groups, both semantic-organization tables, and the
schedule version. The preregistered digest is:

```text
sha256:a221e9a48c653e8446bae3f2f0b3260726f7a2dec20f8fe6fdfaf36e11ff1d2b
```

The structural balance is checked exactly:

- every domain/mechanism cell has 40 cases;
- every domain/mechanism/root cell has support between two and ten;
- every derivation-family/template stratum has 20 cases;
- each such stratum has 10 cases per mechanism and 10 per parameter cell;
- every domain/derivation-family/template cell has five cases and contains all
  four mechanism/parameter-cell combinations;
- every derivation-family/template/root bucket has support two or three and
  contains both mechanisms and both parameter cells; and
- within each derivation-family/template stratum, the maximum root share is
  0.15 and normalized root entropy exceeds 0.99.

The leakage canary trains majority lookup tables only on the permitted training
fold. Across every nonempty subset of the other low-order coordinates, maximum
mechanism accuracy is at most 0.55 under leave-family-out,
leave-template-out, and joint leave-family-and-template-out evaluation. Root
prediction is at most 0.15 under the same held-out designs. This is a held-out
design guarantee, not a claim of unconditional statistical independence: an
in-sample two-coordinate lookup reaches 0.575 for template plus root. That
limitation is pinned in the regression test and must stay visible.

### Live calibration adapter and rendered gates

`adapt_calibration_case_v2` binds one exactly certified hard case to the live
`CalibrationCase` schema. `adapt_public_calibration_bank_v2` requires an exact
string-to-boolean mapping with one brain-off outcome for every fixed case ID;
missing, extra, subclassed, or non-boolean values fail closed. The adapter uses
the cross-domain derivation family as calibration family, keeps discourse
template separate, declares root lines 3–10 eligible, and binds oracle receipts
to the primary and independent implementation digests. As calibration-v2
documents, caller-issued receipts do not by themselves prove organizational or
execution independence.

The regression passes the 320 rendered traces through the real calibration-v2
implementation, not a metadata proxy. All ten shortcut baselines pass their
sealed pooled and worst-family thresholds: prompt-only, location, length,
numeric-summary, and bag/character models under both leave-family-out and
within-family leave-template-out evaluation. The domain/topic mechanism
predictor and every position/support stratum gate pass as well.

That regression supplies a deterministic, position-balanced correctness
fixture solely so the calibration report can exercise its non-surface gates. It
is not a model run and is not empirical difficulty evidence. A real calibration
must pass actual brain-off outcomes through the adapter.

This narrow module currently adapts directly to `CalibrationCase`; construction
of `PreregistrationCandidate` is owned by the generic integration layer. That bridge must use
the full canonical plan-wide `implementation_digest -> verifier_id` mapping,
ordered by implementation digest, and an exact sorted case-specific subset of
at least two executed implementation digests. Both records are pre-outcome,
evaluator-only metadata. Neither belongs in `solver_view_payload`.

## Two shared fault mechanisms

`single_source_shortcut` and `duplicated_correction` exist in every mathematical
domain. Each chooses a different typed surface expression, wrong declaration,
and repair description.

| Domain | Single-source shortcut | Duplicated correction | Correct core used by the selected role |
| --- | --- | --- | --- |
| Number theory | Use the raw modulus product | Divide out the shared factor twice | Least common multiple |
| Enumerative combinatorics | Use one single-bound shift as a joint overlap | Add the joint-overlap contribution twice | One joint-overlap contribution |
| Directed graph algorithms | Keep the direct tentative label | Apply the alternate-path improvement twice | One complete relaxation |
| Linear recurrences | Use trial degree one | Apply the resonance-degree increment twice, giving degree three | Degree two |

The selected claim role applies the same visible transformation to the correct
core or to the mechanism's wrong core. Thus repairs can be transformed values
(for example, `x0+lcm`, not always the bare lcm) while the witness still refutes
the underlying shortcut. The mechanism name, seed coordinates, derivation
family, parameter cell, generator cell, repair description, and schedule
version are excluded from the solver view. Public-view token balance is checked
over the full 320-case schedule.

## Semantic program before prose

Each case contains a version-2 `SemanticProgramV2`. Its initial state uses
canonical integers or reduced rationals. All twelve lines are typed transitions
over a closed expression AST. A transition binds its executable expression,
public target alias, public basis expression, and canonical display. The
program has one clean checked assignment and one faulty declaration for the
same root transition; all other transitions are shared.

The canonical renderer owns the visible line text. A transition whose display
does not exactly match its typed target and basis AST is rejected during
construction. The program also executes both clean and faulty surface
trajectories over public aliases: every displayed basis must arithmetically
support its declared value using only prior public surface state. Lines 3–10 are eight checked equalities with the same `record`
scaffold and fixed-width value field. Their mathematical roles differ, but all
eight target aliases occur exactly once and all eight feed the same public
checksum on line 11. The root therefore cannot be found merely by spotting the
only target used downstream.

Target and basis aliases use forms such as `v-03`. The mapping is a deterministic
bijection keyed only by mathematical domain and numeric instance. It is
therefore identical across every position, mechanism, template, derivation
family, and parameter-cell variant of that base instance; none of those schedule
coordinates can be encoded through the alias permutation. The next numeric
instance receives a different permutation. Every target, source, and downstream
state is defined in the prompt's complete case-permuted legend. The target
definitions are explicit mathematical formulas—for example, the next CRT solution is
`x0+lcm(m1,m2)`—so a bad equality is false from the solver-visible prompt, not
merely from evaluator metadata. The hyphen is intentional: calibration's number
normalizer erases the numeric alias suffix, while a mathematical solver can
follow the full legend and relational basis.

The two fault mechanisms substitute different legitimate source quantities
into whichever claim role was selected. The visible target remains unchanged,
the source expression has the same structural arity as its clean counterpart,
and the declared value is the exact value of that wrong source relation. This
makes the line locally plausible but mathematically false: its basis arithmetic
can be true while its target equality is not.

Each prompt also carries a fixed-width decimal case tag. Its hash input is
restricted to generator version plus answer-free coordinates: mathematical
family and numeric instance only. Position, mechanism, template, derivation
family, and parameter cell are never included. An exhaustive canary requires
every non-instance schedule variant—including all 16 position-by-mechanism
variants—to share one tag. Calibration's prompt normalizer erases the tag as one
number before mechanism features are built.

The tag is only a deterministic public identifier; it is not claimed to be a
privacy boundary or proof against memorizing this public generator. Full
solver-view uniqueness is established separately from the relational target,
basis, ordering, and fault content, not by injecting an answer-bearing tag. The
distinct-view regression and live prompt-only gates both run after tag
construction.

Certification executes both trajectories:

1. The clean prefix, root, and suffix must succeed with no invalid line.
2. The faulty prefix must succeed and the root alone must fail.
3. The declared faulty value is injected, and the identical suffix must still
   execute successfully from both root states.
4. Clean rendering must reproduce the stored clean trace; faulty rendering must
   change exactly one line; replaying the clean root is the sole exact textual
   restoration.

The two final-state digests are part of certification evidence. A suffix whose
displayed basis no longer supports its declaration is rejected at program
construction; other poisoning still fails deterministic regeneration or
semantic certification.

## One solver-output contract

Every prompt exposes the same schema, value kinds, error code, and full
witness-code vocabulary. No mathematical family gets a custom answer shape.
The prompt also says explicitly that `repair.target` is the literal string
`"value"`; this is not an undocumented verifier constant.

```json
{
  "schema": "org.math-research-brain/hard-challenge-answer",
  "schema_version": 2,
  "first_invalid_line": 7,
  "error_code": "invalid_transition",
  "repair": {
    "target": "value",
    "value": {"kind": "integer", "payload": {"value": 36}}
  },
  "witness": {
    "code": "solution_pair",
    "values": [
      {"kind": "integer_vector", "payload": {"items": [29, 65]}}
    ]
  }
}
```

The normalized value kinds are `integer`, `rational`, `integer_vector`, and
`symbol_vector`. Rationals are reduced with positive denominators; an integer
cannot be encoded as a denominator-one rational. The shared witness codes are
`solution_pair`, `feasible_assignment`, `directed_path`, and
`polynomial_identity`. Every prompt defines their exact value-list arity, kind,
and order:

- `solution_pair`: one integer vector containing the first and next solution;
- `feasible_assignment`: the joint shifted-violation assignment vector, then
  the correct bounded count (the shifted vector is not claimed feasible under
  the original upper bounds);
- `directed_path`: the symbol vector of vertices, then its integer length; and
- `polynomial_identity`: the rational particular coefficient, integer vector
  of second-difference coefficients, integer vector of scaled residual
  coefficients, then the integer faulty residual.

`verify_repair`, `verify_witness`, and `verify_solver_output` recompute exact
mathematics from the case parameters. They do not accept generator prose as
mathematical authority.

## Exact mathematical checks

| Mathematical domain | Primary oracle | Independent oracle | Universal witness |
| --- | --- | --- | --- |
| Generalized CRT | Bounded residue enumeration | Bézout/generalized CRT construction | Two solutions one true period apart |
| Bounded compositions | Inclusion-exclusion | Bounded coefficient dynamic program | Shifted overlap assignment and exact count |
| Directed shortest path | Bellman-Ford relaxation | Exhaustive simple-path enumeration | Shorter directed path and length |
| Forced recurrence | Symbolic second finite difference | Characteristic-polynomial derivative identity | Exact polynomial coefficients and residual |

For the recurrence case, the primary oracle computes the full coefficient
vector of

```text
(n+2)^d - 2(n+1)^d + n^d.
```

At degree two this is the constant polynomial `2`; with
`K = 1/(2r^2)`, the residual is identically `r^n`. This is a symbolic identity,
not evidence from a finite prefix.

## Verifier and serialization boundaries

Each case binds the exact module-source digest, an explicit code-object artifact
digest for normalization, strict JSON parsing, interpretation, repair, witness,
and family verifier callables, separate primary/independent/witness oracle digests,
and the interpreter schema. Replacing one of those enumerated callables makes
verification fail closed, with expected and observed artifact digests in the evidence.
This is reproducibility and targeted replacement evidence, not attestation of Python's
complete live dependency graph: arbitrary in-process mutation of referenced globals or
standard-library objects remains outside the claim and requires a fresh-process boundary.

Case JSON is canonical, sorted, compact UTF-8 and content-digest bound. Reloading
also regenerates the public case from its mathematical family and seed.
Duplicate keys, floats, non-finite numbers, unknown fields, noncanonical
rationals, invalid UTF-8, and oversized records are rejected.

Public JSON, text, integer, collection, and typed-record boundaries require
exact built-in or exact record types. This matters for the byte budget: a
`str` subclass can override `encode()` and falsely report a tiny payload. The
guard rejects the subclass before calling it, and the hostile regression proves
that the override is never reached. Replay accepts only an already bounded
exact `list` or `tuple`; it does not materialize arbitrary iterables. The one API
that intentionally accepts a seed iterable consumes at most the 96-case limit
plus one sentinel item, so an infinite source is stopped after exactly 97
values.

## Audit canaries and limits

The focused suite pins the failures that motivated this redesign:

- the fixed schedule tables have a literal digest regression;
- derivation families must produce four distinct typed transition orders;
- parameter cells must change bound mathematical content, while every
  non-parameter axis leaves the parameter record unchanged;
- all schedule balance, entropy, support, and held-out lookup limits are checked
  at their conditioning level rather than only in aggregate;
- complete public solver views contain no hidden coordinate or mechanism
  metadata and no stable mechanism token;
- aliases are invariant across every non-instance schedule axis, selected claim
  roles are invariant under position and fault-axis flips, and alias
  permutations change between numeric instances;
- every public state token is present in the prompt legend, all eight candidate
  targets are distinct, and every one is linked into the public checksum;
- a public-view-only mathematical canary recomputes each selected target from
  prompt data in all 320 cases, accepts the clean equality, and rejects the
  faulty equality;
- every prompt specifies the literal repair target and the exact arity, kinds,
  and ordering of all four witness value arrays;
- the historical family markers and first-token locator fail on 8,000 public
  random cases;
- all eight checked claim roles share one renderer scaffold, with no
  high-precision surface token that uniquely marks the root;
- no supported faulty solver view has conflicting labels;
- replacement of an enumerated verifier or parser callable invalidates the artifact binding;
- semantic suffix poisoning is rejected at program construction; and
- hostile subclasses, oversized records, and unbounded iterables fail closed.

## Exploratory surface-leakage audit

The root edit is also surface-local. Across all 320 cases, Python `SequenceMatcher` gives a
mean character similarity of 96.0% between the clean and faulty root lines. Tokenizing words,
integers, rationals, and punctuation gives mean similarity 91.8%, with every case between
90.5% and 93.3%; the eight domain-by-mechanism cells have means between 91.5% and 92.0%.
This quantifies that the injected edit is small and one-line-local. It does not by itself show
that detecting the mathematical consequence is difficult.

On source revision `4fd0c22`, an outcome-aware evaluator trained deliberately shallow text
classifiers on all 320 public cases. Five-fold splits grouped cases by mathematical domain and
numeric instance (20 groups), so no test fold reused an instance from training. The input mask
removed opaque case tags, integers, rationals, and uppercase symbolic tokens. A class-balanced
scikit-learn 1.8.0 logistic regression over character TF-IDF 3-grams through 5-grams produced:

| Solver-visible text | Eight-way fault-line accuracy | Balanced accuracy | Two-way mechanism accuracy |
| --- | ---: | ---: | ---: |
| Prompt only | 14.37% | 14.22% | 50.00% |
| Faulty trace only | 10.31% | 10.36% | 50.31% |
| Prompt and trace | 13.75% | 13.58% | 50.31% |

The corresponding chance levels are about 12.5% and 50%. This exploratory check found no useful
masked lexical shortcut on unseen instances. It is not a model-difficulty result: the classifier
cannot do the mathematical recomputation that a capable solver should perform, and the public
bank remains unsuitable as a private holdout.

A stricter ablation retained every solver-visible integer, rational, punctuation mark, and symbolic
token, replacing only the opaque case tag. The raw and tag-replaced inputs produced identical
predictions under the same grouped folds and classifier family:

| Solver-visible text | Correct fault lines | Balanced accuracy | Correct mechanisms |
| --- | ---: | ---: | ---: |
| Prompt only | 45/320 (14.06%) | 13.91% | 160/320 (50.00%) |
| Faulty trace only | 41/320 (12.81%) | 12.80% | 161/320 (50.31%) |
| Prompt and trace | 38/320 (11.88%) | 11.67% | 153/320 (47.81%) |

Thus the chance-level result is not an artifact of deleting the numerical quantities that a real
solver sees. It still rules out only this shallow character model and these grouped public cases;
it does not rule out a different shortcut learner or substitute for first-attempt solver evidence.

These checks are public-development evidence. They do not establish empirical
model difficulty, private-holdout performance, or resistance to a classifier
trained on this public bank. Any future private evaluation must use newly
authored material and a fresh seal, without tuning against an old holdout.

### Relational shortcut falsification

The character models above were too weak. A later solver-view-only canary joined each prompt's
case-permuted alias definitions to alias occurrences in candidate right-hand sides. Each domain
overtly describes two suspicious source roles. Seven clean roles use their ordinary source count;
the faulty root alone adds one of the suspicious aliases. Comparing each alias's observed count
with its clean baseline therefore identifies the active mechanism, and the extra occurrence
identifies the line.

On all 320 public cases, this deterministic decoder obtained:

| Target | Correct | Accuracy |
| --- | ---: | ---: |
| Fault line | 320/320 | 100% |
| Fault mechanism | 320/320 | 100% |

It evaluated no mathematical claim. Alias names change across instances, which explains why
whole-document n-grams stayed at chance; the relationship between the legend meaning and the
candidate basis did not change. Surface similarity near 96% is therefore not difficulty evidence.
The v2 public bank and its zero-start pilot are superseded for difficulty measurement, and selected
collection is disabled in code.

A successor must balance every suspicious source with matched legitimate uses in the same
principal syntactic role, use a source-role schedule not recoverable from public tags or scaffolds,
and remove announcing phrases such as “duplicating” or “applying twice.” Wording changes alone are
insufficient. A mandatory within-case relational decoder must stay at or below 0.15 fault-line
accuracy and 0.55 mechanism accuracy on held-out generator instances before another pilot can be
preregistered. New material should also add genuine derivational dependencies: the current eight
candidate transforms are largely parallel and feed only a checksum, making full witness templates
cheap once the root is localized.

### Public schedule inversion and answer completion

An independent red team then found a second solver-view-only decoder. The public 20-digit case tag
identifies the numeric instance by brute-forcing the published tag hash. The visible discourse
scaffold identifies the template, and the target-alias order on lines 3–10 identifies the
derivation family and root position through the published role-order tables. Public schedule
formulas then recover the mechanism, parameter cell, and exact seed. Regenerating that public seed
returns the complete expected repair and witness without evaluating any displayed equality.

| Target | Correct | Accuracy |
| --- | ---: | ---: |
| Fault line | 320/320 | 100% |
| Fault mechanism | 320/320 | 100% |
| Exact seed | 320/320 | 100% |
| Exact complete solver JSON | 320/320 | 100% |

Removing the tag is insufficient: the complete alias permutation is itself a unique public
instance fingerprint in 320/320 cases. Role order alone uniquely localized 194 roots, a fixed tie
rule reached 256/320 (80.0%), and a leave-instance-out candidate-set majority reached 245/320
(76.56%). Witness structure is also fixed enough to complete cheaply: all 80 path cases use
`["s","b","a","t"]`, all 80 recurrence cases use coefficient vectors `[2]` and `[1]`, and the
recurrence residual is mechanism-coded (`0` versus `3`).

A successor release gate must therefore test public-surface schedule inversion and full-output
completion as well as relational alias use. On held-out instances and a withheld schedule seed,
fault-line, exact-seed, and exact-output accuracies must each be at most 0.15, while mechanism
accuracy must be at most 0.55. Final labels must be sampled independently after visible trace order
is fixed; neither tags nor alias permutations may index an answer-bearing public schedule.
