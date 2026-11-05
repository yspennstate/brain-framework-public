# Hard blind pilot v1

**Disposition: superseded before selected collection.** A solver-view-only relational decoder
recovered both fault line and mechanism on all 320 public-development cases without doing the
mathematics. The executable collector now fails closed, and the immutable disposition is recorded
in [`hard-blind-pilot-v1-disposition.json`](hard-blind-pilot-v1-disposition.json). Do not resume
this pilot after quota reset.

This pilot measures first-attempt full-answer accuracy on a fixed 64-case subset of the
hard-v2 public-development bank. It is empirical difficulty evidence for this particular
instruction-blinded run, not a holdout claim and not proof of the served model's identity.

## Outcome-free commitment

Before any selected-case response is requested, the evaluator must externally timestamp:

- the source revision containing the frozen sample, output schema, scorer, and runner policy;
- the hard-calibration public commitment, with the rank-bearing manifest, seed, HMAC key,
  candidates, and stable IDs withheld from the solver;
- the frozen sample commitment and HMAC-bound attempt-universe commitment, but no selected
  prompt, case id, or answer;
- the append-only attempt-ledger precommit digest, which HMAC-binds every exact prompt,
  output schema, request policy, nonce, and slot;
- the requested model, provider, reasoning effort, timeout, instruction digests, native
  executable SHA-256 and size, and the no-tool process-policy digests; and
- the scoring denominator and all difficulty thresholds below.

The frozen sample has 64 cases: 16 per mathematical domain, 16 per derivation family,
16 per discourse template, 32 per fault mechanism, 32 per parameter cell, and eight at
each eligible fault line. Selection is outcome-free. A development smoke case outside the
64 may test transport before collection; no selected response may be used to tune the prompt,
schema, scorer, or bank.

## One-way attempt lifecycle

Every slot starts pending. The ledger durably publishes `STARTED` before invoking a runner,
then permits exactly one selected returned artifact or sanitized failure. A started slot is
burned forever even if the process crashes, times out, emits invalid JSON, fails its event audit,
or cannot be scored. There are no retries and no replacement cases.

Each slot launches one fresh native Codex app-server process and one fresh thread in an empty
runtime directory outside the repository. Tools, web search, plugins, skills, hooks, memory,
history persistence, and project instructions are disabled. On Windows the child is created
with no window and BelowNormal priority. Selected execution is fixed to exactly one worker;
the policy and executor reject every other concurrency value.

The complete run artifact is retained evaluator-side. It includes the raw response, transcript,
stderr, executable provenance, requested and accepted configuration, process/isolation evidence,
and event audit. A safe published result contains only aggregate counts, rates, status classes,
and predeclared strata.

## Scoring rule

The parser requires one exact `SolverOutputV2` object. Full-case correctness requires the exact
first invalid line, normalized one-line repair, and mathematical witness under the bound hard-v2
verifier. Every one of the 64 committed slots remains in the primary denominator. Runner failure,
timeout, ineligible event audit, missing output, invalid JSON, schema failure, or verifier failure
scores incorrect; operational categories are also reported separately.

The requested configuration is `gpt-5.6-sol`, provider `openai`, effort `ultra`, with a
180-second per-slot timeout. Accepted configuration and reroutes are audited, but current
app-server events do not positively attest the served model binary. Results must therefore be
described as requested-model first attempts with unverified served identity.

The target full-case accuracy interval is 45% through 75%. A rate above 85% is a ceiling-effect
failure: version 1 is burned and future work must create harder material without tuning or retrying
these cases. A rate below 30% is a floor-effect failure. Rates in (75%, 85%] or [30%, 45%) are
usable but outside target and require diagnosis. The old 95% regime is never accepted as hard.

## Frozen run values

The canonical outcome-free payload is
[`hard-blind-pilot-v1-preregistration.json`](hard-blind-pilot-v1-preregistration.json).
It was reconstructed from the private state after ledger creation and passed the public-payload
allowlist. Its canonical digest is
`sha256:cfa9d6e86b3be3441cf35f6ccf8db1be485a96389397ba0d88be61cbbe2fa210`.

- source revision: `718f50f2f0c86028ff1f5de538dc42c61aaff533`;
- sample commitment: `sha256:1e19233204d206bdc17361ce41a014c599b2230874f8d313405f85087ad5f8d6`;
- attempt-universe commitment: `sha256:0f1abfa5b0d3e3f4401a69c9a6bd46885720c8180759fd88ec289d8c3b7241aa`;
- pristine ledger digest: `sha256:ed8e134c76ca8bf42fda619468824ee8ee83297bdbe986867001a82894090369`;
- run-spec digest: `sha256:09468047278a01785680e860bed778de5ce1aac362b6602de7c72e0f0bf954ac`;
- response-contract digest: `sha256:36c57d084ecb85939dcb5455213e2259a59a1d356d4dd20165f7a91a060c6934`;
- native executable digest: `sha256:2b3c18d9393ed794531ae3da13f43a6de3bcd91dc577222bd31a17c59f7de0aa`
  over 341,251,888 bytes; and
- preregistration state at publication: 64 `PENDING`, zero `STARTED`, zero terminal.

The payload and this chronology must be committed and pushed before the first selected slot enters
`STARTED`. A standalone smoke artifact is diagnostic evidence only: each call to the selected
executor performs a new eligible nonselected smoke before it can inspect or burn slot zero. Do not
enter selected execution unless service capacity can support that smoke plus all 64 sequential
first attempts; after the smoke gate opens, later capacity failures still burn selected slots.

No collection result is reported until every committed slot has left `PENDING`. The local private
state and ledger are intentionally outside Git and are not part of the public payload.

## Transport status after publication

After remote verification of preregistration commit `0e27ecacc460eb56a44038b5c88b42b133542938`,
a standalone nonselected smoke was attempted on 2026-07-14. The protocol reported the requested
model, provider, and effort as accepted with no reroute, but this is not served-model identity
attestation. The service returned `usageLimitExceeded` after 2.71 seconds, with a displayed reset
time of July 19, 2026 at 11:27 PM. There was no solver output, so the smoke was ineligible and
provides no mathematical-accuracy observation.

The complete private smoke artifact is retained outside Git; its serialized SHA-256 is
`d2ea402877b37216ef63036a62aa7cb66c270d8eb6b79eb3448c1ac5957dcca1` over 78,250 bytes.
The standalone smoke path never receives the ledger, so the preregistered state remains 64
`PENDING`, zero `STARTED`, and zero terminal. Selected execution was not entered: it is unsafe to
open its smoke gate unless capacity can plausibly cover that fresh smoke plus all 64 no-retry
attempts.

That capacity block was subsequently overtaken by a mathematical-design blocker. A deterministic
decoder used only the prompt's case-permuted alias legend and the aliases occurring in candidate
right-hand sides. In every case, exactly one of two overtly described suspicious source roles
appeared more often than its clean baseline. That source identified the mechanism, and its extra
occurrence identified the faulty line: 320/320 for each target. Shuffling alias names defeats
document-level n-grams but not this within-case relational join.

Version 1 therefore yields no difficulty estimate and is permanently superseded with all selected
slots untouched. A replacement must give suspicious sources matched legitimate principal-basis
uses within every case, neutralize announcing prose, use a source-role schedule not recoverable
from public surface fields, and pass solver-view-only relational gates of at most 0.15 line accuracy
and 0.55 mechanism accuracy. Its collector must also interleave domains outcome-free and stop on
the first operational failure instead of burning later pending slots.
