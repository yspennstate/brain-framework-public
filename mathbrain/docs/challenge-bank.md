# Sealed challenge bank

The v1 challenge bank is a model-free evaluation fixture for mathematical error detection. It
contains twenty development cases and a commitment to twenty evaluator-only holdout cases. The
splits use the same ordered family contract, with ten medium and ten hard cases each. Every case
has one clean numbered trace, one replacement that creates exactly one semantic fault, an exact
repair, a JSON-bound witness verifier, and one generic reference card.

The committed descriptor is `config/challenge_bank.json`. It contains C01-C20 and D01-D20, but no
holdout case identifiers, prompts, parameters, traces, repairs, witnesses, or relevance labels.
Before the R1 freeze, the complete holdout payload remains below gitignored `local/evaluation/`.
The public descriptor commits its payload digest and a split seal containing only aggregate
counts, a manifest digest, and a Merkle root.

## Role boundary

| Role | May receive | Must not receive |
| --- | --- | --- |
| Improvement work | `DevelopmentWorkbench`, generic cards, development manifest | Holdout payload or unsealed evaluator |
| Evaluator | Explicitly unsealed holdout after every commitment check | Payloads that do not open the frozen seal |
| Solver | The shared `math_brain.benchmark.SolverView` only | Split, family, difficulty, truth, card, repair, witness, query, or internal case id |

`SealedChallengeBank` does not retain hidden holdout cases. `unseal_holdout` requires both a
validated `HoldoutUnsealRequest` and separate payload bytes. It recomputes the payload digest,
every case replay, the complete manifest, the split seal, distinctness from development cases,
and the reference-retrieval gate before returning `HoldoutEvaluator`. This is an API and process
boundary, not encryption: disclosing the evaluator payload discloses its contents.

## Solver and evaluator interfaces

`ChallengeCase.solver_view(use_faulty)` returns the exact shared `benchmark.SolverView` type. Its
serialization has only the existing schema fields, prompt, and numbered solution. The evaluator
keeps salted SHA-256 identifiers outside that payload:

```python
from math_brain.challenge_bank import load_challenge_bank, opaque_case_id, opaque_view_digest

bank = load_challenge_bank("config/challenge_bank.json")
case = bank.development.case("D01")
view = case.solver_view(use_faulty=False)
runtime_salt = b"an-evaluator-generated-secret-salt"
public_case_id = opaque_case_id(case, runtime_salt)
public_view_id = opaque_view_digest(case, view, runtime_salt)
```

The salt must be evaluator-generated and at least sixteen bytes. Neither helper bakes in case
ordering or variant rotation, so the evaluator can apply a separately frozen assignment design.
`render_opaque_assignment` is the convenience path for the bank's balanced ten-pair rotation:
the selected variant is clean exactly when `((case_index + pair_id) % 10) < 5`.

The narrow adapter surface is immutable and model-free: `case_id`, `family`, `digest()`,
`solver_view(use_faulty)`, `fault_line`, `error_family`, `clean_replacement_line`, `certify()`,
`verify_repair(replacement_line)`, and `verify_witness(json_payload)`. The witness payload is one
JSON-decoded value, not an already-normalized Python object. Its registered
`ReplacementNormalizer` converts arrays to bounded tuples, rational strings to `Fraction`, and
other JSON types to the exact internal representation before pure replay. Float aliases and
Python's boolean/integer type aliases fail this boundary. Repair and witness checks return a
boolean and a content-only evidence digest, never answer-bearing evidence text.

## Pure replay

The trusted replay core uses integers, `Fraction`, family-specific finite bounds, closed-form
lattice and union counts, exact Gaussian elimination, and an allowlisted rational-expression AST.
Bounds are checked before range construction or list allocation. It never calls `eval`, a model,
a CAS, the network, or another local project. Registered verifier types cover:

- diagonal witnesses and quantifier dependence;
- denominator/domain checks and polynomial candidate replay;
- induction residues, finite lattice/union counts, and exact rational AST evaluation;
- matrix-vector norms, two-point squared ratios, and feasible support points;
- finite truth-table probabilities and congruence/divisibility checks;
- exact rank-nullity, finite probability identities, and recurrence sufficiency;
- sign intervals, aggregate counterexamples, and polynomial degree thresholds.

Certification independently accepts the clean trace, rejects the faulty trace at its sole changed
line, and accepts the exact repair. The exact-repair adapter requires the committed replacement
string; semantic replay may accept an equivalent JSON formatting at the registered claim line.
A change at any other line fails.

## Generic references and retrieval

C01-C20 are principle-level cards. They contain no case constants, case identifiers, repairs, or
witnesses, and the seal rejects any contiguous twelve-token overlap with a case prompt. The
declared primary route is `ReferenceCorpus.retrieve_for_solver_view(view, limit=3)`, which uses
only `view.prompt`. `ReferenceCorpus.retrieve(query, limit=3)` is the lower-level lexical method.
Both score query text against card titles, guidance, and retrieval cues; neither reads `gold_card`,
family labels from a case, or answer metadata. Scores are deterministic and ties break by card id.

Each case also retains an authored `retrieval_query` because it is part of the frozen commitment.
Passing that value to `retrieve` is an **oracle-routed diagnostic only**, exposed explicitly as
`oracle_retrieval_query(case_id)`; it is not primary challenge evidence. The historical seal gate
confirms that this authored diagnostic places the registered generic card in the top three. On the
committed development split, the prompt-driven primary route places that card top-one for 20/20,
top-three for 20/20, and never abstains after development-only cue expansion. This is fitted
development coverage, not held-out retrieval evidence. These are retrieval-coverage measurements,
not mathematical correctness scores. `ShamCorpus.retrieve_for_solver_view` records the same prompt
boundary with empty cards, scores, and context.

Retrieval audit records persist only digests of the query, trigger decision, candidates, returned
cards, scores, abstention, and rendered context. `ShamCorpus` accepts the same natural query but
always returns an empty tuple and an empty-context abstention record.

## Challenge-evaluation score policy

The recommended primary comparison is paired within the same case, trace variant, and assignment
pair: prompt-driven reference context versus prompt-driven sham context. Score mathematical
decision and repair correctness only. Give fault localization **weight 0**: every frozen v1 trace
has three lines and every registered fault is on line 3, so localization is a structural constant,
not a measured capability. Do not make a medium-versus-hard subgroup claim: the first ten families
are all medium and the second ten are all hard, so difficulty is inseparable from family. Report an
overall paired treatment contrast and, if useful, descriptive per-family results. Keep authored
query routing as a separately labeled oracle upper bound, never folded into the primary lift.

## Manifests and sealing

All JSON is parsed with duplicate-key rejection, non-finite-number rejection, and byte budgets.
Supplied content digests are checked before case generation and replay; committed payloads are
also matched to their precommitted digest before expensive sealing. Runtime records are
schema-versioned and content-hashed over canonical UTF-8 JSON. Ordered case records become RFC
6962-style SHA-256 Merkle leaves; a manifest binds case order, family order, difficulty balance,
and record digests. A split seal omits manifest entries, while the bank seal commits the card set
and both split seals.

Changing a prompt, trace, parameter, repair, verifier, card, order, or difficulty invalidates at
least one committed digest. Recomputing a new seal is an authoring operation, not authorization to
replace a preregistered bank.
