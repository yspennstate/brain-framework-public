# Random-external three-arm engineering pilot v4

Status: protocol draft only. This document and its implementation are not a production
precommitment until their exact Git commit and a canonical public precommitment are pushed
and externally acknowledged before any solver reaches `STARTED`.

## Why this is a distinct lane

The curated body/card v4 lane is a terminal infrastructure/eligibility failure. Its frozen
review policy had no fallback, retry, partial fill, replacement, or top-up. No semantic
selection was recovered. The random-external lane therefore does not salvage, amend, or
pool with that lane. The final report must show the curated failure and this later lane as
separate protocols.

## Frozen design

- Twelve internal cases come from the separately blinded internal-author protocol, one in
  each audited `4 domains × 3 difficulty bands` cell.
- Twelve external cases come from the separately preregistered
  `MathNet-random/autoretrieval-v4` selector. It uses a future external randomness beacon,
  the pinned official MathNet source, the existing gold-free one-per-event-family ranking,
  and exactly twelve clusters. There is no reroll, top-up, replacement, or reselection.
- Random external cases have no audited semantic domain/difficulty labels. Those fields are
  null. They are never relabeled merely to satisfy the old v3 balance validator.
- The unit of independence is the problem/event-family cluster: 24 clusters total. The
  experiment creates 72 fresh one-shot solver processes, three paired arm assignments per
  cluster. “72 agents” is not treated as 72 independent statistical observations.
- Within each provenance source, all six BRAIN/SHAM/EMPTY service orders occur exactly
  twice. A separately committed allocation seed fixes case order, arm order, and opaque task
  identifiers before execution.
- BRAIN is deterministic frozen automatic retrieval from the pinned brain corpus. SHAM is a
  source-disjoint derangement with exact byte/token/format/shape matching. EMPTY is the empty
  context.
- Automatic retrieval is intention-to-treat. A preregistered abstention remains in the
  sample; it cannot remove or replace a case. For that case, BRAIN and SHAM receive empty
  context just like EMPTY. Retrieval coverage is reported.
- A later blinded relevance review is diagnostic only. It cannot gate packets, remove
  cases, change the selected card, rerun retrieval, or authorize a top-up.
- The old curated-review artifacts are invalid selector inputs for this lane.

## Execution gates

No solver may start until all of the following exact artifacts are cold-replayable and their
public digests are externally acknowledged:

1. failed curated-lane evidence;
2. random external selector precommitment, future-beacon specification, reveal, and rebuilt
   twelve-case bank, including the exact pre-beacon public-anchor evidence digest and the
   exact cold-replay selection capability digest. The
   replay gate must independently consume the externally committed precommitment and
   beacon-receipt bytes, both expected record digests, and the exact pinned source; a
   Python capability object's type, private marker, or `validate_current()` result is
   integrity evidence only and is not proof that cold replay occurred;
3. whole-lane blind external-gold audit with post-terminal gold opening, including the
   exact final Ed25519-authenticated signed-report digest (the unsigned content digest and
   vault digest are only intermediate integrity commitments);
4. complete 36-slot internal author ledger and two blinded selectors, including the exact
   cold-replay selection capability digest;
5. deterministic retrieval receipts and frozen brain packets;
6. sham matching and tokenizer-proxy attestations;
7. this 24-case plan, opaque task map, runtime policy, and 72-slot first-attempt ledger.

The selector precommitment must be posted unchanged as one dedicated public GitHub issue
comment before the committed drand round. GitHub's live REST representation must report
`created_at == updated_at`, with `created_at` at least 60 seconds before that round's exact
scheduled time. Every authority-bearing consumer must live-refetch the exact repository,
comment ID, actor, and body and independently match the committed anchor-evidence digest.
Cached JSON, a nominal evidence object, or an in-process identity check is not chronology
authority. A missing, edited, unreachable, or mismatched comment makes the lane infeasible.
No production comment is posted until the complete selector precommitment is frozen.

Any failed external gold check makes the entire random external lane infeasible. There is no
case-level top-up. Served-model/tokenizer identity remains unverified unless a later strict
attestation independently proves it; absent that proof, the study is an engineering proxy,
not confirmatory evidence.
Requested-model configuration, runner context, and solver self-report never count as served-model
evidence. Without authoritative response-side model metadata, every attempt retains
`model_identity_verified: false` even when all observed labels agree.

## Analysis contract

The primary descriptive estimand is the paired cluster-level solve-rate difference
`BRAIN − EMPTY` across all 24 cases. `BRAIN − SHAM` estimates relevance-specific value;
`SHAM − EMPTY` diagnoses generic context/placebo effects. Results are also reported by
provenance source, but random external cases are not stratified by invented domain or
difficulty labels.

Runner failure, invalid output, parser failure, or solver abstention remains a first-attempt
failure and contributes zero to the conservative intention-to-treat solve rate. The report
must include raw paired outcomes, discordant counts, exact paired/sign-test results, and
cluster bootstrap intervals. It must separately report retrieval abstentions, runtime/model
attestation limitations, every infeasible lane, and the earlier easy 10-pair v1 pilot. No
pilot threshold authorizes a confirmatory claim.

Before any outcome exists, the descriptive positive-signal threshold is frozen as follows:

- both `BRAIN − EMPTY` and `BRAIN − SHAM` must have mean paired differences of at
  least `0.05`, one-sided exact sign-test `p <= 0.05`, and deterministic 95% cluster
  bootstrap lower bounds strictly above zero;
- `SHAM − EMPTY` must be at least `-0.02` overall;
- neither provenance source may have a negative mean `BRAIN − EMPTY` or
  `BRAIN − SHAM` difference; and
- EMPTY solve rate must be between `0.15` and `0.85`, inclusive, both overall and
  within each provenance source, so a floor/ceiling task set is not called evidence.

The deterministic bootstrap uses 4,096 resamples of the 24 problem clusters, with an
isolated stream derived from the sealed schedule digest for every contrast. Ties remain in
the mean and bootstrap; the exact sign test discards ties in the standard paired way. The
same summaries are shown separately for the 12 internal and 12 external clusters. There is
no external semantic-stratum analysis.

The terminal outcome vocabulary is exact: `correct`, `incorrect`, `solver_abstention`,
`invalid_output`, `parser_failure`, or `runner_failure`. Only `correct` contributes one.
Every other disposition contributes zero and remains in its original ordered slot; none can
authorize a retry, replacement, top-up, or case deletion.

Core engineering validity and attestation limitations are reported separately. Broken
public-precommit anchor ordering, selector cold replay, future-round replay, two-relay
agreement, `randomness = hash(signature)` verification, post-terminal gold opening,
internal selection replay, retrieval ITT, packet matching, sterile execution,
first-attempt ledger, schedule, scorer, or cluster independence makes the analysis invalid.
Selector cold replay means a fresh rebuild from the committed raw records, expected record
digests, pinned pre-beacon anchor evidence, and pinned source followed by exact
capability-digest comparison; nominal Python object construction or marker validation
cannot satisfy that gate.
The selector does not self-certify authoritative external time: the anchor-order gate is a
separate audit over previously acknowledged public evidence. An otherwise coherent run may
still be reported descriptively when beacon BLS verification, served tokenizer identity, or
served model identity is unavailable, but each missing attestation is explicit and the
result remains an engineering proxy. Mixed served-model labels are a core consistency
failure, not merely a limitation.

Each scored slot, the independent integrity audit, and the final summary have distinct
precommitted Ed25519 authorities and domain-separated v4 signatures. Final verification
cold-rebuilds the allocation, demands the exact ordered 72-attempt universe, revalidates all
assignment and ledger bindings, recomputes the result manifest and paired statistics, and
then verifies the aggregate signature. Even a positive descriptive threshold is serialized
with `confirmatory_claim_authorized: false` and
`method_helped_confirmatory_claim: false`.
