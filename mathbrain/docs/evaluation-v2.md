# Evaluation v2: sealed repeated-measures protocol

Evaluation v2 is a harder, binary endpoint for measuring whether the brain helps on
subtle mathematical-error cases. A case succeeds only when the complete decision is
correct. Faulty traces additionally require the exact root and error family plus
accepted repair-replay and independent-witness receipts. Partial credit is not an
endpoint.

## Sealed design

The evaluator commits the case and context universes, gate configuration, receipt
authority, and two independent design seeds before solver dispatch. The commitment
also contains digests of the fully materialized condition-free assignment payload and
the exact active-control map. Each assignment-payload record binds the case, pair,
order, clean/faulty variant, opaque solver id, and exact rendered-view digest. Plan
construction recomputes those records and rejects any assignment whose rendered view
is not the committed clean or faulty view for that case. Every assignment also binds
the digest of the whole design commitment, so an outcome from a plan with different
contexts, controls, gates, seeds, cases, or authority cannot be replayed into another
plan even when a local case id happens to match.

There are twenty matched repeated-measure pairs. Every case is clean in ten and faulty
in ten. Pair-level faulty prevalence varies deterministically within 35–65%, while the
brain-on and brain-off condition for a pair receive byte-identical case views, opaque
ids, variants, and ordering.

Brain-off receives a whole-card active control. The map is a source permutation and a
derangement. Every target/source edge must preserve exact UTF-8 byte length and have
disjoint canonical relevance keys, and its source text must differ from the relevant
text. The plan recomputes the target and source text digests and checks the
precommitted map; replacing the map with a post-hoc but well-shaped permutation is
rejected.

Persisting the commitment before dispatch is an operational chronology requirement.
The data structures bind what was committed, but cannot by themselves prove when an
operator persisted it.

## Independent unit and benefit gates

The cases and the twenty collection pairs form crossed repeated-measure axes. Neither
axis may borrow the other's nominal observation count. The analysis computes one
brain-on minus brain-off mean for each case across pairs and one such mean for each
pair across cases, with separate all-trace and faulty-only versions. It then applies
exact one-sided sign flips and deterministic percentile bootstraps to both axes.
Case gates prevent twenty repeats from masquerading as new cases; pair gates prevent a
few shared collection or runtime shocks from masquerading as effects on forty
independent cases. Zero effects are inert ties in every exact test. The sealed defaults
require:

- at least 40 distinct cases;
- mean case benefit at least 0.04;
- at least 12 strictly positive cases, positive median, 95% interval lower bound above
  zero, and exact one-sided case-cluster p-value at most 0.05;
- the same mean, positive-case, median, interval, and exact-test requirements computed
  separately on each case's ten faulty-trace observations; and
- on all traces and again on faulty traces alone, mean pair benefit at least 0.04, at
  least 12 of 20 strictly positive pairs, positive median, 95% interval lower bound
  above zero, and exact one-sided pair p-value at most 0.05; and
- aggregate clean-trace specificity loss no greater than 0.02.

Clean gains can therefore never compensate for faulty-trace degradation. The public
summary contains only aggregates and never case ids, assignments, receipts, roots,
families, or stratum labels. Its v4 envelope attributes those aggregates to the exact
design commitment, plan, gate configuration, and order-independent canonical result
universe digests.

## Difficulty and subgroup validity

Brain-off success must lie in `[0.30, 0.85]` both across all assignments and across
faulty assignments alone. The all-assignment interval is also enforced for each bare
difficulty stratum and every adequately supported domain×difficulty,
mechanism×difficulty, error-family×difficulty, and template×difficulty stratum. Those
four adequately supported subgroup families have a second, faulty-only floor and
ceiling. Each gated stratum needs at least eight distinct cases; twenty repeats of one
case still count as one. Any declared stratum below that support fails closed.
Adequately supported faulty strata may regress by at most 0.05 under brain-on.

This prevents a global rate such as 0.50 from hiding stratum rates `[1, 1, 0, 0]`.
The faulty-only checks also reject the deceptive mixture in which clean brain-off
accuracy is 100%, faulty brain-off accuracy is 0%, and their combined accuracy is
0.50. Sparse or repeated observations cannot masquerade as independent support.

## Canonical and resource bounds

All text is valid NFC UTF-8. Cases, contexts, assignments, control bindings, relevance
keys, and solution lines use canonical container ordering. Inputs are bounded at 512
cases, 256 solution lines per view, 64 relevance keys per context, 256,000 context
bytes per card, 16,000,000 context bytes per universe, and 64,000,000 rendered-view
bytes per case universe. Exact duplicate clean/faulty solver-view pairs are rejected,
even under different case ids, so cloned views cannot inflate the case count.
Validated record fields require exact built-in scalar and
container types, so a subclass cannot redefine encoding, length, comparison, or
iteration while retaining a misleading canonical digest. Because Python's frozen
records can still be changed through low-level attribute mutation, every dispatch,
verification, and scoring boundary recursively revalidates the exact nested records
and recomputes their semantic digests before use. Public convenience lookups
authenticate the exact original outer assignment, case, and commitment containers and
their recorded digest scalars, then authenticate every returned or queried assignment
and case record against its state seal or per-case anchor. Thus valid cells cannot be
transplanted between plans, case semantics cannot change under a returned slice, and
duplicate, omitted, or reordered case universes fail closed. A convenience lookup does
not rehash nonselected cells; any later dispatch, verification, or scoring consumer
revalidates those cells at its own boundary before use.

Repair and witness candidates have a separate strict JSON domain. It accepts only
exact dictionaries, lists, nulls, booleans, integers up to 4,096 bits, finite floats,
and NFC strings, with exact string object keys. A container may have at most 4,096
items; a candidate may have depth 32, 32,768 total nodes, and 1,000,000 canonical
UTF-8 bytes. The same validation runs before both digesting and verifier replay. The
outcome stream is bounded while it is consumed: all candidate material together may
use at most 64,000,000 canonical UTF-8 bytes and 2,097,152 JSON nodes. The plan is
revalidated after arbitrary iterators finish, and the retained universe is recounted.
Malformed, noncanonical, oversized, duplicate, incomplete, or extra outcome universes
fail closed. Bootstrap repetitions are an exact sealed constant of 4,096, not an
operator-adjustable compute knob.

Verifier construction is an evaluator-only capability, never a solver-parsed input.
A bound verifier accepts only an exact Python function or bound method. Its callback,
function, code-object, globals-dictionary, and bound-case identities plus the
bound-case digest are authenticated at binding, then rechecked before and after every
replay. Mutable values reachable through an otherwise unchanged function global or
closure remain explicitly trusted evaluator runtime state; their integrity comes from
the separately precommitted verifier source, artifact, interpreter, and oracle digests,
not from this record seal. Receipt issuance rejects replay-time mutation of the
candidate, case, assignment, authority, or bound verifier identity.
Each signed receipt binds the exact canonical case-record digest and a keyed digest of
the replay capability's origin, in addition to the assignment, solver answer,
candidate, verifier, and evidence digests. Repair and witness receipts must have both
distinct verifier digests and distinct immediate replay origins. Issuing two receipts
separately and manually assembling an outcome therefore cannot reuse the literal same
function or bound method as both checks.

Replay-origin separation does not prove semantic independence: two distinct wrapper
functions can still call the same downstream oracle or consult shared mutable state.
The distinct precommitted verifier artifacts and their operational isolation therefore
remain a trusted evaluator boundary; the origin check is a literal capability-reuse
defense, not an independence attestation.

## Remaining statistical limits

The case sign-flip assumes independent, sign-exchangeable case effects; the crossed
pair sign-flip separately assumes sign-exchangeable collection-pair effects. Cases
generated from a shared template or mathematical family can remain correlated, and
the 40-case minimum does not cure that dependence. The same active-control mapping is
currently reused across all twenty pairs, so control-source sensitivity is not itself
crossed by pair. The records bind design content but cannot prove wall-clock
chronology, condition isolation, or that an external runtime honored the randomized
order. The bootstrap is a deterministic uncertainty calculation keyed by the sealed
plan, not another random assignment. Reported evidence should therefore include
generator-family diversity, paired-batch provenance, execution chronology, and
control-map sensitivity, and should not describe the repeats as added sample size.
