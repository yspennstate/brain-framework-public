# Hard-v2 calibration preregistration bridge

`hard_calibration_v3` binds the fixed 320-case relational challenge bank to the generic
outcome-blind v3 protocol. It is the path used before any real brain-off response is read.

The bridge performs one deterministic sequence:

1. Generate all 320 cases under the sealed `orthogonal-320/3` schedule.
2. Re-run exact mathematical certification on every rendered case.
3. Build a v3 plan with four cross-domain derivation families, four discourse templates,
   one shared twelve-line layout, four mathematical domains, two fault mechanisms, two
   structural parameter cells, and eligible rank arity eight.
4. Bind a normalized source bundle for the bridge, generic plan/protocol, and calibration-case
   binder; also bind the generator source and callable bundle, derivation parameters, template
   fingerprints, generator-cell signatures, and all eight primary/independent verifier
   implementation digests.
5. Give every case a `hard-v3-...` identifier computed as a domain-separated HMAC of its
   canonical case digest under a caller-supplied, evaluator-held 32-byte key. Bind a one-way commitment
   to that key into the plan, but never place the key in a solver payload. Production callers
   must generate a fresh key with `os.urandom(32)`; the fixed test key is not suitable.
6. Commit one plan-wide implementation-digest-to-receipt-ID map. Each case separately commits
   the exact primary/independent pair for its mathematical domain. Neither record enters the
   solver view.
7. Ticket and validate the complete corpus with `build_preregistered_corpus`. Before attempts,
   publish only the safe plan and manifest/corpus commitments. Keep the seed and rank-bearing
   canonical manifest evaluator-only until every committed first attempt is terminal. Retain the
   HMAC key evaluator-side through binding so the bridge can reconstruct every keyed ID.

Use `HardCalibrationPreregistrationV3.public_commitment_payload()` for the pre-attempt public
artifact. It deliberately omits candidates, manifest rows, selection seed, fault ranks, stable
IDs, and the evaluator-held key. Never serialize the wrapper itself with `dataclasses.asdict`, pickle,
or a generic object codec: `repr=False` is display hygiene, not a secrecy boundary. The complete
manifest and key can be revealed only after all committed attempts are terminal if reproducible
post-hoc audit requires them.

After first-attempt responses exist, `bind_hard_calibration_outcomes_v3` requires one exact
boolean outcome for every opaque stable ID. It regenerates the sealed bank, reconstructs each
`CalibrationCase` under that opaque ID, issues the two digest-bound receipts named before the
outcomes, and calls the generic post-outcome binder. Missing cases, extra cases, integer values
masquerading as booleans, a changed map, a changed verifier pair, or changed rendered content
fail closed. The bridge pins both generic protocol callbacks loaded at import, canonicalizes the
returned artifact, and independently recomputes its complete linkage before returning it.

This bridge establishes deterministic content identity and design support. It does not make a
caller-issued receipt proof of organizational independence, prove that a Git timestamp preceded
model access, or turn synthetic boolean fixtures into difficulty evidence. Difficulty still
requires real first attempts collected only after the plan, sample, solver contract, and scoring
rule are committed.
