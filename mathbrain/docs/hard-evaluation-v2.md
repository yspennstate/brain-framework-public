# Hard mathematical evaluation v2

The first sealed challenge study was useful as an engineering test, but not as a
serious capability test. Brain-off scored 0.95, seven of ten paired effects were
ties, every trace had three lines, every fault was the final claim, and several
prompts named the relevant pitfall. The old bank and its aggregate result remain
immutable historical evidence. Its opened holdout is permanently retired from
model selection, calibration, and future benefit claims.

V2 is designed to measure whether retrieved mathematical memory helps with
plausible, locally coherent mistakes when there is enough headroom to observe an
effect. It is a new schema and study, not a relabeling of v1.

## Data boundary

V2 has three disjoint layers:

1. **Development** contains public examples, generator tests, and intentionally
   visible debugging fixtures.
2. **Calibration** contains 200--400 nonfinal cases used only to estimate
   difficulty, specificity, and shortcut susceptibility at the generator-cell
   level.
3. **Final holdout** is created only after the generator, independent verifiers,
   retrieval policy, response schema, scorer, runtime policy, and statistical
   gates are frozen. It contains new proof templates, not merely new constants.

The former holdout cannot enter any of these layers. A final holdout in the same
shared Windows environment is only procedurally hidden; a strong confidentiality claim
requires a separately controlled evaluator or human-held seed.

## What qualifies as a hard case

A score-bearing trace must satisfy all of the following:

- six to twelve meaningful dependency steps;
- one causal invalid inference at a precommitted interior position;
- a coherent downstream suffix recomputed from the faulty intermediate state;
- an exact one-line repair that restores the proof;
- a witness distinct from the repair, such as a second CRT solution, deficient
  Hall subset, cheaper path, failed recurrence residual, or omitted interval;
- a clean foil containing the same suspicious notation or operation under
  hypotheses that make it valid;
- certification by two algorithmically independent checks where feasible;
- no prompt text that announces the likely mistake;
- no one-to-one mapping from domain, family, position, or surface template to
  the error label.

Initial public-development families are generalized CRT uniqueness periods,
bounded compositions with inclusion-exclusion, directed shortest paths with a
late negative-edge improvement, and forced recurrences with resonant particular
solutions. Candidate extensions include absorbing chains, Jordan structure,
Hall witnesses, rational sign charts, integer optimization, and conditional
finite probability.

## Difficulty and shortcut admission gates

Calibration operates on generator cells (family, mechanism, and structural
features), never on hand-picked final items. A proposed bank is rejected when
estimated brain-off full-case success is above 0.85 or below 0.30. The target
range is 0.45--0.75, with approximately 10--15% anchors, 60--70% target cases,
and 20--25% very hard cases.

Before a generator cell can become score-bearing:

- template-disjoint prompt, location, length, numeric-summary, token, and
  character baselines must have balanced accuracy at most 0.55 and a
  conservative upper bound at most 0.60;
- no fault position may exceed 25%, and position entropy must remain close to
  the balanced maximum;
- topic or domain alone must not predict the mechanism above 0.55;
- at least 70% of faulty traces must pass registered cheap checks such as type,
  range, parity, feasibility, or direct substitution;
- clean and faulty traces must remain semantically and stylistically matched;
- poisoned generator oracles, changed repairs, and changed witnesses must be
  rejected by the independent verifier.

## Primary endpoint and paired design

The primary endpoint is balanced full-case success, not a weighted sum of
duplicated partial credit.

- A clean case succeeds only with the exact valid verdict.
- A faulty case succeeds only with the invalid verdict, causal root line, error
  family, independently verified repair, and independently verified witness.

Component metrics remain diagnostic: balanced validity, localization, family,
repair, witness, Brier score, clean specificity, and faulty sensitivity.

The default design uses twenty matched brain-on/brain-off process pairs. Every
case is clean in ten pairs and faulty in ten, while per-batch faulty prevalence
varies between 35% and 65% so the solver cannot exploit a fixed quota. The two
conditions receive byte-identical cases, variants, ordering, schema, and runtime
policy. Brain-off receives a precommitted, relevance-disjoint, length-matched
active control; empty-context and oracle-context arms are calibration
diagnostics, not the primary comparison.

The study uses a fixed sample size and no outcome peeking. Starting gates are:

- paired full-case delta at least 0.04;
- exact one-sided sign-flip p-value at most 0.05;
- paired interval lower bound above zero;
- positive median pair effect;
- at least twelve of twenty pairs strictly positive, with ties not counted;
- clean-specificity loss at most 0.02;
- no adequately populated mechanism or difficulty cell regressing by more than
  0.05;
- complete ledger, runtime, retrieval, contamination, and isolation evidence.

The exact gates may change only from public calibration and power simulation,
before the final seed exists. A failed slot is replaceable only for a
demonstrable pre-unblinding infrastructure failure using the identical sealed
commitment; otherwise it is burned.
