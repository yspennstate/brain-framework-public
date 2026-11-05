# Calibration outcome binding v3

Calibration preregistration and calibration scoring have different information boundaries.
The selection manifest must be fixed before any solver result exists, while a calibration-v2
case necessarily contains `brain_off_correct` and verifier receipts. The outcome-binding
protocol proves that the later outcome-bearing input uses the exact prompt, faulty trace,
eligible lines, fault coordinate, labels, and reviewed implementations that were committed
before outcomes were collected.

The lifecycle is:

1. Render each candidate as a `PreregistrationCandidate`. This exact frozen record contains
   evaluator-side structure and the faulty coordinate, but has no response, prediction,
   correctness, or receipt field.
2. `build_preregistered_corpus(plan, seed, candidates)` derives two separate digests. The
   solver-view digest covers only the stable ID, prompt, faulty trace, and eligible lines. The
   outcome-free content digest additionally covers every coordinate, label, structural
   fingerprint, semantic-program digest, and the fault line. It also commits the complete
   reviewed implementation-digest-to-receipt-ID map and the exact subset of at least two
   implementations that must run for that case. This supports family-specific verifier pairs
   without allowing an operator to permute implementation identities after outcomes are known.
   Every candidate must carry the same plan-wide identity map; only its executed subset may vary.
3. The builder derives selection tickets, sorts by ticket, assigns contiguous public indices,
   constructs the materialized bindings and manifest rows, and reruns all v3 support,
   diversity, position, identity, and coordinate checks. Publish the returned commitment
   through an external timestamped channel before collecting solver outcomes.
4. Give blind solvers only `candidate.solver_view_payload()`. Keep the complete candidate,
   materialized binding, manifest, and oracle coordinate on the evaluator side.
5. After collecting results and independently replaying the declared verifiers, construct the
   exact calibration-v2 cases. Supply the already-preregistered implementation-to-receipt-ID
   map as `VerifierIdentityBinding` records; it is checked against every candidate rather than
   accepted as a new post-outcome declaration.
6. `bind_calibration_outcomes(...)` strictly reconstructs every typed input, matches each v2
   case to its preregistered rendering, invokes the sealed v2 design validator, and emits a
   canonical `CalibrationOutcomeBinding`. That artifact binds the plan, manifest,
   preregistration commitment, materialization, verifier identities, v2 case set, and complete
   v2 input digest.
7. Persist the canonical JSON and use `verify_calibration_outcome_binding(...)` to strictly
   reload and deterministically recompute it from the source artifacts.

Changing a prompt, rendered line, eligible coordinate, structural label, verifier mapping, or
case membership after preregistration fails. Changing a solver correctness outcome is allowed
only as post-outcome data and produces a different calibration-case, case-set, input, and
outcome-binding digest while leaving the earlier preregistration commitment unchanged.

The protocol establishes content identity, not chronology or meaning. A local digest does not
prove that publication preceded observation, that a template fingerprint is genuinely
position-blind, that two verifier implementations are independent, or that a declared semantic
program represents the intended mathematics. Those claims still require external timestamping,
reviewed source, and independent replay.
