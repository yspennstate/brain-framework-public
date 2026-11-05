# Hard challenge v3: leakage-resistant successor contract

Status: **design and release gates in progress; no v3 selected collection is authorized.**

Hard challenge v2 and hard-blind pilot v1 remain immutable audit artifacts. They are not a
base to silently patch and resume. A solver-view-only relational join localized the faulty
line and classified the mechanism on all 320 public-development cases without mathematical
recomputation. The grouped, learned reproduction in `relational_leakage_v3.py` retains
320/320 line and 320/320 mechanism accuracy while leaving each of the 20 numeric-instance
groups out in turn. It is also invariant to a complete cyclic renaming of the public aliases.

A separate public-schedule decoder is stronger still. Case tag or alias-permutation fingerprint,
visible scaffold, target-role order, and published schedule tables recover the exact v2 seed and
therefore the complete expected solver JSON on 320/320 cases. Role order alone reaches 80.0% line
accuracy with a fixed tie rule. V3 must defeat both the learned relational audit and this
generator-inversion threat.

The claim is pinned by `tests/test_hard_v2_public_schedule_decoder.py`: both the tag route and an
explicitly tag-redacted legend route recover all 320 seeds, labels, and verifier-accepted complete
outputs even when every displayed trace expression is replaced with a redaction marker.

The v1 selected ledger was still 64 `PENDING`, zero `STARTED`, and zero terminal attempts at
disposition. That clean zero-start boundary is part of the evidence and must be preserved.

## Threat model

An adversary may train on every labeled public-development case and use any relationship
among strings a solver receives: the prompt, alias legend, case tag, candidate order,
candidate targets, candidate bases, trace scaffold, and output contract. The adversary may
join fields within a case and aggregate them across the whole trace. It wins without doing
the mathematics if it can recover the first invalid line, the mechanism, or a completion
template above a preregistered limit on held-out generator groups.

Random token names, low character similarity, and line-local classifiers are therefore not
security arguments. They remain useful diagnostics, but only after relational decoders pass.

## Construction invariants

1. **New immutable namespace.** V3 gets a new generator version, schema, commitments, and
   final material. V2 bytes and the superseded v1 preregistration are never rewritten.
2. **Matched source-role foils.** Every semantic source that could identify a mechanism has
   matched clean uses in the same principal syntactic roles within each case. Source-role
   assignment follows a balanced Latin-square design independent of the fault line and
   mechanism. Merely adding zero-valued decorative terms does not count as matching.
3. **Relational rather than source-substitution faults.** The wrong line changes a subtle
   mathematical relation among otherwise ordinary sources. No source, operator, arity,
   target role, or basis depth is unique to the root after conditioning on the case.
4. **Neutral public semantics.** Legend descriptions state exact quantities without words
   such as “duplicating,” “twice-reduced,” “applying twice,” or “unadjusted” that announce a
   candidate mechanism. Neutral wording alone is insufficient without matched roles.
5. **Nonrecoverable final schedule.** A public tag, instance surface, template order, or
   published generator constant cannot determine the final fault line or mechanism. Final
   schedule material is sampled separately, committed before collection, and revealed only
   as allowed by the blinded protocol.
6. **Real dependency depth.** Candidate lines form a derivation with meaningful downstream
   uses, not eight independent transforms whose only consumer is a checksum. The first
   invalid line must matter to at least two later mathematical transitions.
7. **Witness diversity.** Numeric instances vary proof structure and witness shape as well as
   parameters. A fixed four-vertex path or fixed recurrence coefficient tuple cannot serve as
   a universal answer-completion template.
8. **Fresh clusters.** Final difficulty and benefit material use generator clusters disjoint
   from public development. Multiple axis variants from one numeric instance are one cluster,
   not independent evidence.

### Candidate three-source claim core (typed recurrence integration implemented)

For each domain, define a canonical source `C` and two pairwise-distinct alternatives `A,B`.
Every candidate target uses all three with nonzero, pairwise-distinct coefficients `(wC,wA,wB)`
plus its genuine mathematical offset. One mechanism swaps the coefficient roles of `C,A`; the
other swaps `C,B`. Clean and faulty roots then have identical alias counts, operators, arity,
depth, and principal source roles, while the swapped relation is nonzero because both the source
values and exchanged weights differ.

In the intended generator, weights and term order come from independently committed,
domain-separated schedule material keyed by an answer-free generator group and claim index. They
must be independent of public tag, root position, template, mechanism, and visible scaffold.
Target legend definitions state the correct target quantity independently of basis order.
Declarations are evaluated from the semantic AST instead of maintained as parallel hand-coded
values.

An affine-null mask that merely makes source counts equal is explicitly rejected: its seven clean
coefficient vectors can lie on one common plane, allowing a rational RANSAC-style outlier decoder
to find the root without domain mathematics. The relational audit must therefore include
coefficient/source positions and a coefficient-aware outlier model, not only occurrence counts.

`source_role_v3.py` now implements this exact-arithmetic core for eight-claim banks. It requires
three pairwise-distinct, nonzero `Fraction` sources; gives every claim three pairwise-distinct,
nonzero integer coefficients; derives the clean target value from the preregistered offset and
all three weighted sources; and represents both mechanisms only as coefficient-role swaps. Every
coefficient orientation in the full `S3` orbit must keep clean and both fault values distinct, and
no two rows may share the same unordered coefficient orbit.

Rendered term positions use all six three-source orders plus a keyed deranged pair, then a keyed
shuffle. That balanced schedule depends only on the private seed and group binding, not on
coefficient rejection or bank nonce. The affine guard is also fault-invariant: it enumerates the
six coordinate permutations of each row and rejects a bank if any orientation could put seven
row orbits on one plane. Earlier clean-coordinate and rendered-coordinate gates were removed after
an exact counterexample showed that their pass/fail pattern itself decoded the mechanism and
narrowed the root. Those ordered checks remain diagnostics; they never condition construction,
validation, or seed replay.

A private 32--64 byte seed is domain-separated by group, bank nonce, claim index, retry nonce, and
purpose. The serialized bank binds its source values, targets, offsets, derived targets, claims,
group digest, semantic-input digest, nonce, and contextual seed commitment. Seed verification
requires the externally preregistered input digest, reconstructs the complete bank, and exact-
compares it. This blocks target/offset reassignment and source relabeling even if an unkeyed payload
digest is recomputed after the fact.

`recurrence_binding_v3.py` now closes that provenance gap for one fixed recurrence domain. Its only
caller inputs are exact integers `(r,c0,c1)` with `2 <= r <= 7` and `-9 <= c0,c1 <= 9`. A closed
semantic program fixes the recurrence
`a[n+2] = 2*r*a[n+1] - r^2*a[n] + r^n`, the three operator-multiplicity source expressions, and
the eight offset nodes `a0` through `a7`. Source values are dispatched from expression kind;
offsets are dispatched from the typed term index and independently cross-checked against the exact
closed form. There is no caller-provided group digest, source bank, target list, offset list,
answer axis, surface axis, seed, or retry counter in the binding API.

The program and exact execution receipts have separate contextual digests. Strict parsing rebuilds
the program, sources, and recurrence terms before accepting either digest, and rejects duplicate or
noncanonical JSON, coerced scalar types, foreign same-valued enums, tuple subclasses, and frozen
nested mutation. The schedule adapter accepts only the typed binding, private schedule seed, and an
externally expected binding digest; it checks that expected digest before entering low-level
schedule selection. Regression tests cover seed noninterference, tamper plus attacker rehash,
semantic transplant, exact replay, and the residual identity
`target - sum(weight*source) == a_i` for every claim.

The generic `source_role_v3.py` replay primitive remains public for domain-core testing; the
recurrence guarantee applies to the typed recurrence façade, not to every package import. Any
future recurrence collector must construct and verify through the typed manifest façade described
below and reject a bank that has not passed its contextual verifier.

`recurrence_manifest_v3.py` provides the pre-axis binding-bank artifact. It accepts only full typed
bindings, rebuilds them, sorts them by binding digest, assigns contiguous canonical indices, and
persists the complete replayable records. Duplicate instances, binding digests, and program digests
are forbidden. The manifest itself contains no schedule seed, fault, mechanism, template, surface,
or outcome field. Before any scheduler access, the schedule façade requires an externally expected
manifest digest. Its low-level group context is derived from the manifest digest, canonical bank
index, and typed binding digest, so a valid claim bank cannot be transplanted to another manifest
that happens to contain the same binding.

`recurrence_axis_v3.py` separates the answer axes from both the binding manifest and the
source-role schedule. Given the externally expected manifest digest, an independent axis seed,
and the externally expected commitment to that seed, it emits the full Cartesian product of eight
claim positions and two mechanisms for every manifest entry. The seed commitment is checked before
any axis is ranked. The builder has no claim-position or mechanism argument. Each per-entry block
is complete and balanced, and each axis identity binds the manifest and entry context but not the
axis seed or source-role claim bank. Changing a source-role schedule therefore cannot change an
axis identity; changing the axis seed changes only the private order and plan commitment.

The plan must itself match an externally expected digest before its seed is replayed. The supported
replay façades return distinct `ReplayCheckedRecurrenceAxisPlanV3` and
`ReplayCheckedRecurrenceClaimBankV3` runtime markers instead of their raw inputs. These nominal
types prevent accidental unchecked composition; they are not signed receipts and hostile Python
code can forge them. External immutable publication is the authority for chronology. The future
corpus builder must also consume every slot in each committed block; this module does not make a
cherry-picked subset representative merely because its parent plan is balanced.

This is still not a completed domain generator or a leakage result. A typed record proves
functional independence from the schedule seed, but it cannot prove wall-clock precedence: an
external immutable publication must timestamp a concrete manifest digest before answer axes and
schedule seeds are derived. A self-reported timestamp inside the JSON would not prove that order.
Other domains still need equally closed typed programs. The relational audit now joins ordered
solver-visible rows and legends across training cases, but the public adapter still needs a typed,
versioned representation of the complete trace before newly generated public-development clusters
can establish anything about leakage.

The current hard-v2 bridge supplies only trace lines 3 through 10. Lines 1, 2, 11, and 12 account
for 95,360 raw UTF-8 bytes across the 320 cases: 36.1650% of trace bytes and 9.4740% of prompt-plus-
trace bytes when separators are excluded. Those omitted lines form 160 distinct tuples. Therefore
the current `1.00/1.00` blocker is decisive for the surface it audits, but it is not evidence of
complete solver-visible coverage. A successor adapter must retain and project every trace line;
the fixed request envelope must also be commitment-bound before claiming coverage of every byte a
solver receives.

## Mandatory release gates

All gates are preregistered and evaluated before any selected model call.

- Exact semantic replay and two independently named verifier receipts must agree for every
  clean and faulty view.
- Existing position, line-local bag/character, prompt-only, and mechanism gates remain.
- `evaluate_grouped_relational_leakage_v3` uses only solver-visible features at prediction
  time and leaves complete generator groups out. Fault-line accuracy must be at most `0.15`;
  mechanism accuracy must be at most `0.55`. Both pooled and worst-group summaries are
  retained. V2 scores `1.00` and therefore fails.
- A separate public-surface schedule decoder tests case tag, discourse scaffold, target-role
  order, and template identifiers. It uses the same `0.15`/`0.55` limits.
- A template-completion decoder is scored on the full normalized repair and witness, not only
  localization. Fixed witness skeletons must not yield a high exact-answer rate.
- Clean controls must reject invented faults. A hard faulty-only bank cannot validate clean
  specificity or the combined evaluator endpoint.
- Any gate implementation, thresholds, split groups, and input digests are committed before
  inspecting its successor final-bank result.

Passing these tests is necessary, not proof that the benchmark is shortcut-free. A newly
discovered solver-visible decoder supersedes the bank even if every registered gate is green.

## Two disjoint empirical lanes

Difficulty and brain benefit answer different questions and must not share selected attempts.

### Lane A: brain-off difficulty

- Exactly 64 faulty cases from 64 distinct fresh generator clusters, one selected case per
  cluster and 16 clusters per domain.
- Outcome-free interleaving across domain, proof structure, mechanism, and fault position.
- Dispatch is 16 rounds of four; every four-slot block contains every domain exactly once.
- One exact served-model identity and fresh nonselected smoke before the first dispatch and
  after every recovery.
- Sequential dispatch. The first quota, transport, identity, persistence, or parse failure
  stops the run immediately and leaves every later slot `PENDING`.
- Operational failure allowance is zero. Difficulty is not scored from a denominator diluted
  by failed calls.

### Lane B: paired brain benefit and clean specificity

- At least 40 fresh clusters disjoint from Lane A and public development.
- Matched clean/faulty arms plus active controls.
- Brain-off/brain-on condition order is sealed per pair, balanced AB/BA, and included in the
  design commitment and dispatch receipts. The twenty collection pairs use exactly ten
  brain-on→brain-off and ten brain-off→brain-on orders, with adjacent fresh-process conditions.
- Pair inference is cluster-aware and retains the existing crossed case/verifier integrity
  checks. Condition order, warmup, and wall-clock position are explicit analysis axes.
- The same fail-stop, fresh-smoke, exact-identity, and zero-operational-failure rules apply.

## Definition of ready

V3 is ready for selected collection only when all of the following are true:

1. Generator, solver-view adapter, relational audit, schedule audit, and template-completion
   audit have focused tests and a clean full-suite run.
2. Independent red teams fail to find an above-threshold non-mathematical decoder.
3. Final clusters are generated after the public design is frozen and are digest-bound without
   exposing answer-bearing schedule material.
4. Lane A and Lane B preregistrations, dispatch orders, condition orders, stop rules, and
   scoring denominators are pushed to the private remote before any selected call.
5. A fresh nonselected smoke verifies the exact route and served identity. A reroute,
   unverified identity, or quota failure cannot be waived into eligibility.

Until then, the v2 collector kill switch stays false.

## Implemented foundation at this checkpoint

- `relational_leakage_v3.py` implements deterministic leave-generator-group-out Bernoulli
  classifiers over a separate exact solver-view capability. Feature extraction cannot receive
  groups, bindings, families, mechanisms, or fault labels. The immutable cache includes the old
  legend relationships plus exact displayed coefficients, source attachment, rendered position,
  declaration value and rank, prompt tags, scaffold tokens, and exact rational plane-outlier
  diagnostics. Its bounded expression parser accepts the renderer's parenthesized sums and products,
  rejects malformed syntax, and retains displayed scalar coefficients in nonlinear alias products.
  Ordered line, target, declaration, coefficient, and exact legend associations are hashed into
  bounded features. Exact association hashes must recur eight times and complete non-legend prompt
  features four times in the training fold, so held-out cases cannot choose either vocabulary.
  Declaration rank-cycle and adjacent-order features generalize across numeric rotations. Prompt,
  coefficient, linear and nonlinear order, numeric-legend, rotated-declaration, prompt-tail, affine
  plane, nonlinear plane, and balanced ordered-binding canaries now block release; hard v2 remains a
  `1.00/1.00` blocker. The report binds the same feature bundles used for fitting and prediction as
  well as the labeled input population. The evaluator rejects duplicate views and case bindings,
  enforces a group-name/binding bijection, retains every held-group fold, and fixes the pooled and
  worst-group thresholds.
- The relational split has its own exact, externally committed group manifest. Its public builder
  accepts only label-free membership records, and every record joins an upstream group binding to
  both case and solver-view digests. The expected manifest is checked before labeled examples are
  iterated; folds are derived from the manifest, and both the input commitment and report bind its
  digest. This blocks live regrouping and case/view transplantation. It does not by itself prove
  that an opaque group-binding digest was generated without outcomes: successor generators must
  supply typed pre-axis bindings and publish the concrete manifest before labels are materialized.
- `successor_dispatch_v2.py` independently HMAC-ranks pair chronology and condition balance,
  binds twenty matched-batch digests, forces exact 10/10 AB/BA order, and derives forty adjacent
  condition slots. Every serialization, seed verification, and slot derivation revalidates nested
  records and the payload digest; seed verification reconstructs the exact schedule rather than
  checking the seed commitment alone.
- `source_role_v3.py` implements exact three-source claims whose two faults swap coefficient roles
  without changing source count, operator count, arity, or term order. Eight-claim banks are
  private-seed reproducible, group-bound, semantic-input-bound, payload-bound, and resistant to
  commitment transplant. Term positions are exactly balanced, coefficient orbits are unique and
  mechanism-separating under every orientation, and the affine-plane gate is invariant under every
  fault, repair, and rendered coordinate permutation. Focused tests also verify the two symbolic
  error identities, exact source dependence, strict type boundaries, nested tamper rejection, and
  a deterministic seed/group sweep.
- `recurrence_binding_v3.py` replaces the opaque group digest for the fixed forced-recurrence
  slice with an answer-free typed instance, a schema-fixed semantic program, and re-executed source
  and offset receipts. Its safe adapter requires a preregistered binding digest before schedule
  selection; adversarial tests reject forged receipts, attacker-rehashed AST mutations, same-valued
  type coercions, raw-axis injection through the recurrence façade, cross-binding transplant, and
  private-seed influence on binding bytes.
- `recurrence_manifest_v3.py` freezes complete recurrence bindings in canonical digest order with
  no schedule or answer axes. Every schedule consumer must present an expected manifest digest
  before index or seed validation. A manifest-specific entry context prevents both cross-entry and
  cross-manifest claim-bank transplant, while strict replay rejects reorder, reindex, nested
  mutation, and forged outer digests.
- `recurrence_axis_v3.py` builds a complete balanced Root x Mechanism design without accepting a
  raw answer axis. It binds every slot to its manifest entry and checks external manifest, axis-seed,
  and plan commitments around generation and replay. Distinct nominal runtime markers keep raw
  banks and plans out of the supported composition path without claiming signed provenance.
  Focused tests cover cross-entry and cross-manifest transplant, seed substitution, structurally
  valid attacker-rehashed plan permutations, exact-type coercion, canonical JSON, and
  schedule/axis noninterference.

These are release-gate, claim-layer, and protocol primitives, not a completed v3 generator or
collector. External publication of concrete binding-bank and axis-plan digests, typed programs for
remaining domains, the typed versioned full-trace adapter, withheld final material, fail-stop
operational state, and final preregistrations remain open.
