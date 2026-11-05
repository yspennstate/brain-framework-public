# Construction and audit log

This is a public-safe evidence ledger for the initial hardening campaign. It
records reproducible outcomes, not local file paths, source excerpts, machine
names, credentials, or private mathematical content. Machine-local manifests
and detailed findings remain under the gitignored `local/` tree.

## Campaign contract

- Started: 2026-07-13 11:08:11 UTC.
- Scheduled end: 2026-07-13 21:08:11 UTC.
- Scope: a mathematical-research memory and verification system only.
- Excluded: finance, trading, company, client, personal, credential, and
  unrelated-project material.
- Publication rule: an allowlisted local source is eligible for review, never
  automatically eligible for copying or publication.

The campaign remains open until the scheduled end. A completed component is not
evidence that the full timed contract is complete.

## Evidence ledger

### Privacy-isolated core

- Typed mathematical records and graph edges are implemented.
- Claim promotion is scoped to evidence kind; examples and numerical checks do
  not verify universal theorems.
- Retrieval, mistake triggers, citations, formal proof receipts, and
  consolidation are separate layers so agreement cannot be mistaken for
  independence merely because one component called another.
- The initial core and its tests were committed as `4c2cbf3`.

### Privacy and publication hardening

- Allowlist manifests now use a strict schema, exact run-local location, opaque
  source identifiers, explicit rights and content classes, and a mandatory
  local-only derivation policy.
- Approved reads use stable file descriptors with repeated containment,
  identity, link, and size checks; hardlinks, reparse points, home recursion,
  alternate streams, and check/open swaps fail closed.
- Local derivations are append-only. Public artifacts require a separate
  provenance sidecar, a reviewed redistributable license, source references,
  reviewer identity, a matching artifact digest, and a clean privacy scan.
- A combined-tree regression fixed a scanner ambiguity between UNC paths and
  LaTeX regular expressions while retaining a real UNC canary.
- The integrated hardening is commit `5b5d582`. At that checkpoint, 132 tests
  pass, 3 privilege-dependent link tests skip on this Windows host, all 53
  tracked files produce zero privacy findings, and the secret scan is clean.
- These gates are necessary, not sufficient: metadata is not a cryptographic
  attestation, and final human review remains mandatory.

### Guarded retrieval and mathematical consolidation

- Formula, symbol, lexical, and typed-graph signals have separate score traces.
- Retrieval explicitly abstains on empty, weak, or ambiguous matches and keeps
  negative edges visible as warnings rather than evidence for a claim.
- Evaluation retrieval rejects test-split, answer-role, active-item,
  active-pair, source-matched, denied, and quarantined records.
- Structured mistake records activate concrete reflexes with ordered actions,
  required evidence, and a stop condition.
- SWS-style replay and REM-style association emit deterministic quarantined
  proposals. The proposal type has no conversion path to verification evidence.
- The integrated component was committed as `ac13684`; the full suite at this
  checkpoint passes 86 tests.
- Red-team limitation: the evaluation guard trusts metadata assigned at
  ingestion. A malicious record falsely labeled as a training claim can pass
  the guard, so only provenance-validated records may enter an evaluation index.

### Paired evaluation protocol

- Ten matched brain-on/brain-off pairs are pre-registered in code.
- Assignment, strict solver-output parsing, macro scoring, deterministic paired
  bootstrap intervals, and benefit gates are tested.
- The protocol records model identity as unverified unless it is supported by a
  response-side receipt; request labels and solver self-reports are not treated
  as proof of the served model.
- The protocol was committed as `9894c2e`.

### Lean verification gate

- The project pins Lean 4.30.0 and records the toolchain, command, source hash,
  exit status, and captured output for every check.
- `sorry`, `admit`, and `sorryAx` are rejected even when a permissive invocation
  would otherwise accept them.
- Kernel acceptance and prose-to-formal-statement fidelity remain separate
  decisions.
- The gate and sample project were committed as `8f284ed`.
- Local execution is currently unavailable because the operating-system
  application policy blocks the Lean executables. This is an environment
  limitation, not a successful formal verification result.

### Exact-arithmetic benchmark baseline

- Commit `9bdcb82` generates exactly 10,000 deterministic MathIR records with
  unique coordinate-derived identifiers.
- Each record has a clean solution, a faulty solution, one recorded bad line,
  and a replayable one-line repair.
- The baseline spans five elementary or undergraduate exact-arithmetic
  families. It is a valid baseline, not yet the requested broad benchmark of
  qualifying-exam, hard, and research-status material.
- A parent-side referee evaluated the canonical JSON without calling either
  production validator. All 10,000 clean traces passed, all 10,000 faulty traces
  had exactly one false assertion at the recorded line, and all repairs exactly
  recovered the clean trace.
- Full-bank SHA-256, with one canonical JSON record plus newline per item:
  `a82199d658f95f18016f3c402923cb71afd7eef63ca8a2ed7df534dfcf404872`.
- Independent RFC 6962 Merkle root:
  `sha256:a62815f9597df3500e275b1a264776cef1cfcc80ac8b0b438be8f22c0c8b2505`.

An adversarial audit found that the baseline is mathematically correct but not
yet safe or broad enough for solver evaluation:

- for the pinned `public-10000-regression` seed, only 4,039 distinct problem
  bodies occur; 5,961 records repeat a problem;
- the current problem serialization exposes answer, witness, and parameter data;
- both validators ignore the prompt, stated answer, and solution conclusion;
- malformed JSON and manifests lack depth, size, and generation-count budgets;
- identifiers bind generation coordinates rather than record content;
- provenance is unstructured free text; and
- one declared fault class is never generated.

These findings supersede any reading of “10,000 records” as “10,000 distinct
problems.” A separate hardening task is repairing the solver boundary and input
budgets before the breadth expansion or paired evaluation.

### Benchmark trust-boundary hardening

Commits `779c48e` and `9762e58` close the stop-run security findings without
claiming that the five-family bank is already broad enough:

- solvers receive only an immutable, size-bounded prompt and numbered solution;
- prompt, answer, witness, conclusion, both variants, fault, and repair are
  bound into a content digest and deterministic coordinate replay;
- item identifiers have a fenced v2 namespace;
- exact-fraction evaluation has depth, node, integer, numerator, denominator,
  and power-preflight budgets;
- fault labels and descriptions, provenance, license, and generator-source hash
  must match trusted deterministic regeneration; and
- malformed Unicode is converted to a stable boundary error.

Independent replay rejected same-family coordinate transplantation, fault
relabeling, self-asserted provenance, a multi-million-bit power expression, and
unpaired Unicode. A 120,143-byte solver view round-tripped inside the aggregate
budget. The combined main-branch verification passed 155 tests with 3
platform-specific symlink skips; 53 tracked files produced zero privacy
findings and the secret scan was clean.

The remaining breadth defect is explicit: the current deterministic generator
still lacks the fourth declared fault mechanism and still repeats semantic
problems. A collision-free balanced generator is being built on top of this
accepted boundary before any confirmatory evaluation.

### Local mathematical audit

- Explicitly allowlisted roots: 5.
- Files discovered: 1,168.
- Eligible for local review under the strict policy: 636.
- Quarantined under the strict policy: 467.
- Unsupported: 64.
- Oversize: 1.
- Proof-gap markers requiring contextual review: 2.
- Parse failures: 0.

The original 575/527 classification came from a legacy runner that reused path
category words as prose filters and recorded one racy read. The migrated runner
separates path and content policy, reads through the stable-descriptor gate, and
records every discovered file. It wrote an immutable 1,168-record local
manifest, then a second full pass verified all three output files byte-for-byte
instead of replacing them. The strict local manifest digest is
`05f34ed7ce3983b8482291ddd18104e465460d6d2fd052de30dbabf018cb332e`.

One existing mathematical release candidate contains an infrastructure-role
label in 457 verification records, 3,301 occurrences total, confined to two
runtime-host fields. The label belongs to an excluded operational domain. No
literal label or affected record was copied here. Those records are blocked
from publication until the field is removed or replaced with a neutral,
non-identifying execution-class label.

The strict scanner independently blocks that candidate: 459 files contain
absolute-local path material and 458 contain an excluded category marker. No
credential-shaped finding was emitted. These are aggregate local-only findings;
matched values and private paths remain outside the tracked repository.

The audit initially reported two Python syntax failures. Independent
reproduction showed that the parser, not the source, was wrong: it had not
handled an initial UTF-8 BOM as Python's normal file loader does. The auditor
was corrected and the full scan rerun before these counts were recorded.

### Proof-level audit results

Four independent proof audits deliberately separated verified reductions from
completion claims:

- The finite-group model's central local-order implication and algebraic
  obstruction survived; a uniform-buffer quantifier and one theorem citation
  need repair before the strongest wording is justified.
- The ReLU reduction remains conditional, but positive-sphere duality fails for
  all-negative vectors and a radial threshold was overstated as a global
  Lipschitz statement.
- The same-map work is a useful partial ledger, not a completed proof. An exact
  positive two-by-two matrix refutes the claimed monotonic envelope equivalence,
  and the fixed-depth and quenched limits must remain separate conjectures.
- The Ising/perceptron moment reduction, inball, localization, and near-one
  closure survived independent canaries. Its evidence bundle mixes two verifier
  generations, however, so it remains strong source-bound evidence pending one
  frozen release manifest and a fresh full numerical replay.

No local manuscript text or private path was copied into the tracked
repository. Detailed line-level findings remain under the ignored local audit
tree.

### Hard-v2 red team and halted blind pilot

- Evaluation v2 now binds every outcome to the whole design commitment and
  exact case record, rejects cloned solver views and identical controls, caps
  aggregate candidate material, attributes summary v4 to its exact inputs, and
  requires crossed case and collection-pair gates. A three-batch shock that
  looked decisive under case-only inference now fails at pair p=1/8.
- A crash-safe 64-slot faulty-case pilot was preregistered outcome-free. Its
  source, native executable, request policy, private state, attempt universe,
  and pristine ledger were bound before any selected request. A quota-blocked
  nonselected smoke produced no mathematical answer; all 64 slots stayed
  pending.
- A subsequent red team falsified the bank's difficulty claim before quota
  reset. Joining prompt alias meanings to candidate-basis alias counts predicts
  fault line and mechanism on 320/320 public cases without mathematical replay.
  Line-local and whole-document n-gram canaries missed this relationship.
- A grouped learned version of that join, leaving each of the 20 numeric
  instances out in turn, also obtains 320/320 for both targets and survives a
  complete cyclic alias renaming. It is now a reusable release-gate module with
  pooled and worst-group limits of 0.15 for line and 0.55 for mechanism.
- A second solver-view-only decoder inverts the public case tag (or, after tag
  removal, the alias-permutation fingerprint), visible scaffold, target-role
  order, and schedule tables. It recovers line, mechanism, exact seed, and full
  expected JSON on 320/320 cases. Role order alone reaches 80.0% localization.
  This also exposes fixed path and recurrence witness templates.
- That schedule inversion is now executable regression evidence. Both the tag
  path and an explicitly tag-redacted alias-fingerprint path recover 320/320
  seeds and verifier-accepted full outputs after every displayed arithmetic
  expression is replaced by a redaction marker. The canonical record digest is
  `sha256:7f9ffd0baa5d943ff4c78c3d10f5acb23e4ab2932bf723227bd126efb3d5e935`.
- The pilot is superseded with zero selected starts, and its collector now has
  a code-level kill switch. The immutable preregistration remains as chronology,
  while a separate disposition records the 320/320 blocker and replacement
  requirements.
- The complete integrated tree immediately before this red-team guard passed
  723 tests with five expected platform/private skips. The guard and relational
  canary receive their own focused and neighboring regression runs before the
  next checkpoint.
- The first v3 foundations are now implemented: a strict grouped relational
  report with pooled/worst-group gates, and an outcome-free successor dispatch
  plan that binds twenty batches, independently ranks chronology and condition
  assignment, forces exact 10/10 AB/BA, and derives forty adjacent slots.
  Their 15 focused tests plus the hard-v2 neighbors pass 52/52.
- Independent audit caught two pre-checkpoint enforcement gaps: the dispatch
  seed commitment was not replay-bound to its payload, and the relational gate
  trusted cloned views/free-form groups and caller-relaxable thresholds. The
  implementation now reconstructs the schedule at every seed check, revalidates
  frozen nested records at consumers, binds group/case/input digests, rejects
  duplicate solver views, and fixes the release thresholds.
- The first source-role construction primitive is now executable. Each of eight
  exact claims genuinely uses three distinct nonzero sources and three distinct
  nonzero coefficients; the two mechanisms only swap canonical/alternative
  coefficient roles. Term positions use an independently keyed balanced schedule;
  coefficient triples are unique by their full permutation orbit and must separate
  both mechanisms in every orientation. A first clean-coordinate affine guard was
  itself leaky: one deterministic bank exposed the mechanism and left only five
  root candidates. It was replaced before checkpoint by a full-orbit exact gate
  that rejects any possible orientation with seven coplanar rows and is invariant
  under faults, repairs, and rendered term order. The contextual seed commitment,
  semantic-input digest, and exact reconstruction now reject target/offset and
  source-role reassignment. This remains a claim-bank core, not a completed domain
  generator or a passed leakage gate.
- The forced-recurrence slice now has a typed answer-free group binding instead of an
  opaque caller-supplied digest. Exact `(r,c0,c1)` inputs re-execute fixed operator-
  multiplicity sources and the schema-fixed recurrence terms `a0` through `a7`; the
  iterative evaluator is independently checked against its closed form. The safe
  schedule adapter requires an externally expected binding digest before selection.
  Strict canonical parsing and adversarial regressions reject residual-offset inputs,
  raw-axis injection through the recurrence façade, tamper plus rehash, same-valued
  enum and bool coercions, nested container subclasses, seed influence on binding
  bytes, and cross-binding transplant. The generic source-role replay core remains
  public; a future collector must require the typed recurrence verifier. This
  establishes typed provenance for this domain only, not chronology, solver-view
  neutrality, or a passed relational gate.
- A separate pre-axis recurrence manifest now rebuilds full typed bindings, sorts them
  by digest, assigns contiguous canonical indices, and rejects duplicate instances or
  program/binding digests. It stores no seed, fault, mechanism, template, surface, or
  outcome field. Schedule construction checks an externally expected manifest digest
  before touching the index or seed, then binds the claim bank to a context derived
  from the manifest, index, and binding. This prevents transplant between entries or
  between manifests that share a valid binding. The artifact fixes content identity;
  only an external immutable publication can establish that the freeze happened first.
- The recurrence answer axes now have a separate committed plan. For every manifest entry the
  plan contains each of the eight root positions under both mechanisms exactly once; its builder
  accepts neither axis as an argument. Axis identity is independent of the source-role schedule,
  and the builder checks an externally expected axis-seed commitment before ordering any slot.
  Replay also checks externally expected manifest and plan digests. Successful plan and bank
  replays return distinct nominal runtime markers, so supported later code can reject an unchecked
  raw object. The marker is not a signed receipt and can be forged by hostile in-process code;
  external publication remains the authority for chronology. A later corpus builder must consume
  complete committed blocks rather than choose slots post hoc.
- The relational gate no longer hands labeled examples to feature extraction. An exact solver-view
  type carries only the prompt and candidate lines, and the report binds the immutable public-only
  feature bundles actually used by the grouped models. The features now retain displayed
  coefficient value, sign, source attachment, term position, declaration rank, prompt tags, and
  exact rational plane-outlier diagnostics. A bounded expression parser covers the renderer's
  nested parentheses and retains displayed coefficients even when aliases multiply. Exact
  candidate, coefficient, target, and raw-legend hashes require eight recurrences in the training
  fold; complete non-legend prompt features require four. Neither vocabulary can be selected by the
  held-out fold. Low-card declaration rank cycles generalize across unseen numeric rotations.
  Prompt, coefficient, linear and nonlinear term-order, numeric-legend, rotated-declaration,
  prompt-tail, and balanced 32-case ordered-binding canaries all block release. Affine and nonlinear
  plane canaries both find their seven-row consensus and one outlier. The hard-v2 320-case
  reproduction remains 1.00/1.00. A typed adapter for the complete public trace remains open.
- Group splits now come from a separate typed manifest rather than the labeled example objects.
  Its public builder accepts only label-free group entries; each membership binds both a case
  digest and the corresponding solver-view digest. Evaluation checks the externally expected
  manifest before consuming the example iterable, cross-binds that manifest into the input and
  report, and derives every held-out fold from the manifest lookup. Regrouping and solver-view
  transplants fail even when a caller recomputes the live input digest. This freezes an assignment;
  an opaque group-binding digest still needs an upstream typed pre-axis generator binding and
  external publication to prove outcome-free chronology.

## Open work at this checkpoint

- Choose a concrete recurrence binding bank and publish its manifest digest immutably. Derive an
  independent axis seed, publish its commitment before ordering slots, then build and publish the
  complete axis plan. Integrate the replay-checked nominal types into a neutral solver-view
  adapter. Build equally closed typed programs for the remaining domains.
  Add a typed, versioned full-trace adapter to the relational audit, then require accuracy at most
  0.15/0.55 before any pilot.
- Build a successor fail-stop collector with outcome-free domain interleaving,
  a predeclared operational-failure validity cap, and fresh-instance cluster
  inference. Keep the public faulty-only pilot separate from benefit evidence.
- Require 64 distinct fresh clusters for the difficulty lane and at least 40
  disjoint fresh clusters for benefit; the old 64 cases contain only 20
  generator clusters and therefore cannot support case-independent inference.
- Integrate the sealed 10 AB / 10 BA primitive and multiple active-control maps
  into evaluation v2, then connect it to a crash-safe fail-stop collector.
- Complete the collision-free 10,000-item breadth expansion with all four
  structurally certified fault mechanisms.
- Complete proof-level and citation-level reviews of the allowlisted local work.
- Author genuinely new private templates before any confirmatory solver study.
- Preserve a reviewed 10,000-record artifact and manifest under `data/public/`.
- Finish independent privacy, security, reproducibility, and mathematical
  red-team reviews before any public-release decision.
