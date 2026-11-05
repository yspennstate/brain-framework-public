# Paired evaluation protocol

This protocol asks one causal question: does access to a frozen mathematical research brain
improve an otherwise matched solver's ability to distinguish valid derivations from deliberately
faulty ones?  The implementation is fail-closed.  It cannot convert a convenient collection of
responses into a benefit claim after the fact.

## Executable pre-registration

`config/evaluation.toml` is the single executable source for solver budgets, runtime and isolation
policy, parser budgets, scoring weights, inference settings, and benefit thresholds.  Its parsed,
canonical representation is hashed into `RunSpec`; code does not carry a second set of scoring or
gate constants.

Before dispatch, `build_full_plan` freezes all of the following:

- the complete benchmark case universe and manifest digest;
- the required matched-pair universe;
- every clean/faulty assignment and its exact ordered `SolverView` digest;
- the solver prompt, brain-on context, empty brain-off sham, and treatment digests;
- the executable configuration and runtime-policy digests; and
- separate commitments for the order, variant, pair, condition, and bootstrap seeds.

The full plan accepts certified, exact `BenchmarkItem` or `ChallengeCase` objects. Benchmark cases
are score-bearing only when their fault kind belongs to `SOLVER_VALID_FAULTS`; the other certified
mutations remain engineering fuzz cases and are rejected from a scored plan. Challenge cases use
opaque runtime-salted identifiers and JSON witness replay. Both paths call the hardened
`solver_view` boundary directly, so private parameters, witnesses, repairs, fault labels, and
answer keys never enter a solver payload. Challenge retrieval is driven only by the solver-visible
prompt; the authored retrieval query is an oracle diagnostic and is not an evaluation input. Each
case is clean in exactly half the pairs and faulty in the other half. Within every pair and
domain/difficulty/family stratum, the two variants differ in count by at most one. Both conditions
receive the same condition-free ordered view pack.

There is no benefit-bearing pilot path.  A run missing a pair, condition, case, or sealed order is
not silently rescaled.

## Practical dispatch API

The runner's sequence is:

```python
config = EvaluationConfig.from_toml("config/evaluation.toml")
plan = build_full_plan(
    cases,
    config=config,
    seeds=seeds,
    brain_on_packets=packets_by_case,
    abstention_reasons=abstentions_by_case,
    ...,
)

batch = plan.batch(pair_id, Condition.BRAIN_ON)
solver_payload = batch.to_solver_payload()
output_schema = batch_output_schema(batch)

result = parse_batch_response(
    plan,
    pair_id=pair_id,
    condition=Condition.BRAIN_ON,
    raw=response_text,
)
run = finalize_run(
    plan,
    batch_results=all_results,
    runtime_evidence=runtime_receipts,
    isolation_evidence=isolation_receipts,
    retrieval_evidence=retrieval_receipts,
)
decision = decide_benefit(run)
```

The app-server `outputSchema` is a useful generation constraint, not a verifier.  The local parser
independently enforces the same boundary after the response arrives.

Each pair-condition slot is designed for one fresh agent. Agents receive no follow-up turn and no
cross-case transcript. The standard configured design therefore requires two distinct agents per
pair. Protocol identifiers must be unique across all slots, and an append-only first-attempt ledger
must independently prove that each identifier came from one new process attempt.

## Strict response boundary

The response is one JSON object containing an ordered `answers` array.  Each row repeats the exact
case id and view digest supplied for that position, then reports:

```json
{
  "case_id": "...",
  "view_digest": "sha256:...",
  "probability_invalid": 0.92,
  "first_invalid_line": 7,
  "repair": {"replacement_line": "7. ..."},
  "error_family": "missing_hypothesis",
  "witness": {"left_value": "3/2", "right_value": "5/2"}
}
```

The solver does not supply a verdict.  The harness derives it from `probability_invalid` using the
pre-registered threshold and tie rule.  A derived `VALID` response requires null line, repair,
family, and witness fields.  An `INVALID` response requires a positive integer line and a nonempty
family; repair and witness proposals may be null and then receive no credit.

The parser rejects duplicate keys, booleans in numeric fields, numeric strings, nonfinite values,
unexpected or reordered cases, swapped view digests, non-NFC text, controls, bidi-format marks,
surrogates, and responses over the byte, depth, node, string, or integer budgets.  It returns stable
attempt statuses rather than letting solver-caused parse errors escape.  Timeout, malformed,
tool-violation, and missing statuses remain as zero-credit observations in the sealed denominator.

An infrastructure failure is not charged to the solver.  It may be replaced only before any
response has unblinded that slot and only by an attempt for the identical sealed batch.  Otherwise
the run is incomplete and cannot support a benefit claim.

## Independent mathematical receipts

The scorer never accepts caller booleans such as `repair_verified=True`.  For every derived
`INVALID` response, the harness creates immutable repair and witness receipts bound to the
assignment, view, answer, scorer, validator, and evidence digests.

The receipt seal is a reproducible integrity checksum, not an authentication token.  Scoring with
verified repair or witness credit requires the sealed plan and replays the mathematical verifier;
a caller-created receipt receives zero credit without the plan and is rejected if it differs from
fresh replay.

A repair passes only when the solver's complete replacement line exactly equals the hidden clean
line at the true faulty position, the replacement reconstructs the hidden clean solution, the
recorded repair replay agrees, and both independent MathIR validators accept the result.  A fluent
or merely equivalent-looking line is not a certificate.

For a representable equality witness, the harness independently evaluates the faulty relation's
left and right expression trees under the sealed exact witness.  Credit requires unequal values and
an exact match to the solver's canonical rational strings.  A `VALID` answer cannot carry either
receipt, even a receipt copied from another row.

## Runtime identity is evidence, not a request label

Requested model, provider, and reasoning effort live in the pre-registration.  Thread-start or
accepted configuration is recorded separately, including the provider-fallback setting.  It still
does not prove which model served a response.

Served model and effort remain `unverified` unless an authoritative response-side app-server
attestation supplies both fields and a final assistant `end_turn`.  A request context, accepted
configuration, agent self-report, or two agreeing self-reports cannot populate those fields.  A
reroute notice is contrary evidence and marks identity `contradicted`; the absence of a reroute is
not positive verification.  Missing, unverified, contradictory, or nonmatching identity evidence
blocks the benefit gate.

Runtime evidence is admissible only when derived from one immutable `CodexRunResult` retaining
the exact launch and protocol requests, canonical native-executable path, executable byte size and
SHA-256, command-tail policy digest, prompt text, canonical output-schema bytes, transcript
bytes, stderr bytes, output text, thread ID, turn ID, execution nonce, timeout, and runtime-directory
file identity, plus the sterile Codex-home directory identity and path digest. The evaluator
replays the transcript audit and checks those artifacts
against the sealed batch, treatment, and scored raw-response digest.  Caller-created digests or
receipts are descriptive only and can never authorize a benefit claim.  Runtime and isolation
evidence for a slot must reference the same in-memory artifact.

The plan's runtime-policy seal includes the exact native executable provenance when configured and
always includes the derived digest of every app-server argument after command element zero. A
general configuration may leave `runtime.executable_provenance` absent, but that is explicitly
descriptive: `executable_provenance_unsealed` fails the strict benefit gate. When a commitment is
present, every slot must carry the same canonical path, positive size, and SHA-256. A changed path,
fake command element zero, digest substitution, command-tail change, or pre/post-launch file change
fails closed. Windows evaluations must also retain the deny-write/delete executable lease across
`Popen` and use the exact production `subprocess.Popen` factory. POSIX advisory/open-descriptor
protection and injected test factories are recorded as unenforced and fail the strict gate. This is
evaluator-process evidence, not publisher authentication or OS code signing.

The protocol identifier is derived from the server thread/turn, transcript, prompt, schema, and
canonical sent request policy rather than accepted from a caller. Launcher path, timeout,
runtime-directory identity, stderr, elapsed time, and nonce are separate local attempt evidence and
cannot mint a different protocol identity. All slots must have distinct protocol identities and
distinct runtime-directory file identities. These checks establish protocol uniqueness under the
trusted in-process capture boundary, but not process freshness. The current post-spawn execution
nonce is diagnostic only: it cannot serve as a pre-launch commitment, is not a signature, and does
not prevent replay across an untrusted store. A crash-safe ledger needs its own caller-supplied
attempt nonce, durably committed before process creation. Until that ledger binds the local fields,
the evaluator must not claim independent fresh agents or report latency comparisons.

## Honest isolation and retrieval accounting

These agents are described as `instruction_blinded_event_audited`, never as
`capability_isolated`.  The event audit must be complete and show no command, file, MCP, dynamic,
collaboration, subagent, web, image, or other tool item; no follow-up; and no policy violation.
Merely instructing a tool-capable agent not to use tools is not capability isolation.

Every assignment also receives a retrieval record.  Brain-on records hash the exact packet or an
explicit abstention reason.  Brain-off records the hashed empty sham.  This makes trigger misses and
no-context cases visible instead of laundering them into a generic brain-on label.  Retrieval
records bind the assignment, view, and condition treatment digests.

## Scoring and fixed-case inference

Malformed, timed-out, tool-violating, and missing attempts receive zero task credit and remain in
calibration with conservative error.  Successful observations are macro-aggregated over the
pre-registered strata.  Duplicate rows are rejected before aggregation, so repeating favorable
cases cannot shift the score.

The primary effect is the mean brain-on minus brain-off score across fresh matched pairs, because
the independently dispatched pair is the experimental unit.  A separately labeled pooled macro
difference is retained only as a diagnostic.  It is never paired with the mean-pair interval or
p-value.

Inference reports two quantities for that same mean-pair estimand:

- an exact, one-sided sign-flip/randomization p-value over the sealed pair deltas; and
- a deterministic paired bootstrap interval, explicitly labeled as inference over the
  pre-registered pairs for this fixed sealed case universe.

The bootstrap is not presented as population sampling over all mathematics.  The benefit decision
uses only the thresholds in the hashed configuration and also fails closed on incomplete design,
infrastructure, runtime identity, isolation, or retrieval evidence.  Any failed gate produces a
mixed, inconclusive, harmful, or invalid result—not “the brain helped.”

The transport binds the configured wall-clock timeout but cannot send or attest an exact
`max_output_tokens` cap through the available app-server request.  That budget is sealed as
unenforced, and `output_token_budget_unenforced` prevents a strict benefit decision even when the
descriptive paired statistics are favorable.
