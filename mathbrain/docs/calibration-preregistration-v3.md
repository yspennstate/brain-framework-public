# Calibration preregistration v3

This protocol fixes a public calibration corpus before any solver outcome is read. It is the
causal front half of [public calibration v2](public-calibration-v2.md): this module binds what
will be scored; calibration v2 later measures the already-bound cases.

The lifecycle is deliberately one-way:

1. Review and hash the generator source, generator bundle, evaluator protocol, derivation
   algorithm, derivation parameters, template implementations, structural generator cells,
   and independent verifier implementations.
2. Build a `CalibrationPlan` with the sealed calibration-profile digest, exact case count,
   axis cardinalities, and every eligible arity. Construction rejects both continuous lower
   bounds and discrete-impossible family-template totals. The latter uses a bounded exact
   dynamic program over realizable integer rank allocations; for example, an arity-four
   stratum cannot hide a remainder behind a rounded maximum-share check.
3. Generate the exact candidate population without running a solver. Give every case a stable
   ID, a unique generator counter, and separate digests for its complete outcome-free content,
   semantic program, and solver-visible view.
4. Derive each `selection_ticket` from the exact plan, seed, generator counter, stable ID, and
   all three content digests. Sort rows by ticket, assign contiguous public case indices, and
   build the `SelectionManifest`.
5. Publish the canonical plan plus the manifest digest and `preregistration_commitment` through
   an externally timestamped channel before collecting a solver result. Keep the canonical
   manifest and its seed evaluator-only until collection closes: each row contains the true
   fault rank, so giving it to a solver would disclose the answer when joined to the stable ID.
6. Project the generated corpus to `MaterializedCaseBinding` records and call
   `validate_materialization`. This checks exact identities, tickets, coordinates, labels,
   template and cell fingerprints, verifier digests, eligible ranks, and the complete sealed
   support and position-balance design.
7. Only after validation may the corpus enter the calibration-v2 scoring lifecycle.

The concrete rendering and post-outcome linkage are specified in
[calibration outcome binding v3](calibration-outcome-binding-v3.md). That layer derives the
manifest's solver-view and outcome-free digests from exact typed renderings, then proves that
calibration v2 scored those same cases rather than a substituted corpus.

The manifest has no solver-response, prediction, accuracy, or success field. A materialized
binding does contain oracle-side eligible lines and the true fault line because it must prove
that the preregistered rank was actually realized. It is therefore verifier input, not a solver
payload, and both it and the rank-bearing manifest must remain on the evaluator side of that
boundary until every committed first attempt is terminal. The full canonical manifest may then
be revealed for audit against the previously timestamped digest and commitment.

Every family-template pair, structural generator cell, and domain-fault cell must contain every
declared eligible arity and observe every eligible rank at least twice. The same
outcome-independent diversity and position gates used by calibration v2 are checked globally and
within family, shared template, structural generator cell, family-template, and domain-fault
partitions. Each family also needs at least eight cases in every declared domain-fault joint cell.
Eligible and actual fault lines are bounded to the supported interior solver-trace range 2–11.
These are design checks; no observed success can rescue a layout that fails them.

All consuming entry points reconstruct exact built-in dataclass values before using them. This
makes `object.__setattr__` tampering, record and collection subclasses, booleans masquerading as
indices, reversed tickets, and recomputed duplicate identities fail closed. JSON arrays are
capped before tuple copying, and counters and indices are bounded before hashing or iteration.

The protocol makes tampering detectable, but hashes do not prove meaning. It cannot establish
that a claimed domain is mathematically distinct, that two verifier digests came from independent
implementations, that a template fingerprint is position-blind, or that publication really
preceded outcome observation. Those claims still require source review, independent replay, and
an external timestamp. A seed ticket also does not make a malicious candidate generator honest;
the reviewed source and its bound parameters remain part of the trusted base.
