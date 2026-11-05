# Brain benefit study v3: sealed three-arm mixed-source protocol

Status: protocol under construction. No v3 solver outcome may be collected until this
document, the case-bank manifest, intervention packets, scorer manifest, runtime policy,
allocation seed commitments, and analysis code are committed and pushed. A pilot is
calibration only and can never be promoted into the confirmatory sample.

## Question and estimand

The experiment asks a deliberately narrow causal question: does giving a fresh math-solving
agent a task-relevant packet retrieved from the frozen shared brain improve first-attempt
mathematical correctness? It does **not** identify the effect of unrestricted live brain
access, write-back, tools, dreams, or collaboration. Those capabilities would make a clean
brain-off control impossible with the current OAuth runtimes and are therefore exploratory,
not part of the primary causal contrast.

Each problem is one independent cluster and is solved by three fresh, one-shot processes of
the same served model with the same runtime policy:

- `B` (brain): a frozen, task-relevant brain packet;
- `S` (sham): a frozen, relevance-disjoint packet matched to `B` in format, UTF-8 byte length,
  and every preregistered tokenizer count;
- `E` (empty): no retrieved packet.

The two primary estimands are the problem-cluster means `B - S` and `B - E` in binary full
first-attempt correctness. `B - S` isolates relevance beyond generic priming and context
cost; `B - E` checks net utility. `S - E` diagnoses sham harm but cannot establish benefit.

## Confirmatory sample

The confirmatory bank contains exactly 96 distinct problem clusters and therefore 288
fresh solver processes per served model:

- 48 fresh in-house problems generated after the frozen brain snapshot;
- 48 externally authored problems drawn by a committed deterministic sampler from an
  immutable official MathNet snapshot.

The two source lanes are balanced over Algebra, Number Theory, Discrete Mathematics, and
Geometry and three predeclared difficulty bands: every source/domain/difficulty cell contains
exactly four cases. Template or source-event families, not superficial parameter variants,
are the independence unit. Exact or near-duplicate bodies, translated duplicates, reused
solution skeletons, and siblings from the same source event are assigned to one cluster and
only one representative may enter the confirmatory bank.

MathNet is public and is not assumed contamination-free. The external lane therefore tests
performance on independently authored public problems, not novelty. Its snapshot revision,
56-shard artifact manifest, official-order 27,817-row digest, row identifiers, source-event
groups, rights records, selection exclusions, and sampling-seed commitment are retained
evaluator-side. The pinned artifact, rather than the drifting dataset card, fixes the physical
schema and literal `final answer only` label. LLM-assisted MathNet metadata such as
`problem_type` and `final_answer` is never the sole gold standard.

The acquisition implementation exposes two structurally different routes. Public synthetic
tests can create only a bank marked `synthetic_test`; official replay accepts only an exact
`VerifiedPinnedMathNetBankV3` emitted after remote-pin checks, local full-byte hashes, footer
and recursive-schema audits, a unique-ID scan, complete official-order row streaming, and a
final byte recheck. Tool versions are bound in a separate runtime-evidence digest so a patch
upgrade cannot change the stable source-byte identity. Cache-present mode is offline; the
download mode separately records whether live repository metadata was verified.

The first real cache materialization on 2026-07-15 verified all 353,156,621 bytes and 27,817
unique rows. It produced source-artifact manifest
`sha256:40607b3b74a2f4678cd3050d4ab1786fd2c60f57d8da3e47c090aec1504d81f0`
and official-order normalized-row digest
`sha256:0a50876d93212ad9a2388358ad4c238d0ffe10c533bd6ec7f88a1fa1d0f45f31`.
The actual adapter scan found 87 strict-English eligible rows. The acquisition smoke wrapper
deliberately reports `confirmatory_source_gate=false`: files are still reopened between hash,
footer, ID, and row passes, so no same-handle immutable-snapshot claim is made. These are
engineering acquisition facts, not solver outcomes and not confirmatory readiness.

Balanced pilot curation does not use that one-row-per-event smoke bank. A separate
full-source preparation pass now screens all 27,817 rows into a gold-free universe and a
private exact-rational vault. The universe retains original null-language state and publishes
only aggregate and gold-free commitments; membership is explicitly annotation-scoreability
gated, so it is not described as independent of the source annotations. The vault publishes
only a keyed aggregate commitment bound to the exact universe. Changing answer and solution
evidence while preserving eligibility leaves the universe and case coordinates unchanged but
changes the keyed vault commitment. Local validation still proves only self-consistency: a
key holder can re-key a wrong row-to-gold pairing, and only complete canonical source replay
rejects it.

Source-pinned preparation therefore returns two non-pickleable process-local results rather
than a combined material wrapper: `PreparedMathNetPilotUniverseV3` and evaluator-only
`PreparedMathNetPilotEvaluatorVaultV3`. Their builder and verifier hash all pinned bytes,
stream the complete canonical row order once, and hash the bytes again. Neither offers a
public combined-material accessor; the universe wrapper offers no public prompt-row accessor,
and the evaluator wrapper offers no public vault-entry accessor. Private attributes remain
reachable to same-process Python. Both hard-code `confirmatory_source_gate=false` and
`selector_handoff_authorized=false`, even if stronger runtime flags are presented. These
objects are not self-authenticating: their public payloads also state that creation identity,
preparation-code identity, and an external signature are unattested. A future
blinded audit layer must take the prepared source, both artifacts, and vault key; perform a
fresh full replay immediately before signing under an attested isolated runtime; then emit a
narrower projection before classification or selection. Python same-process introspection is
not a secrecy boundary, so those operations must also run without network, source-cache, or
vault access.

A cache-only integration, run BelowNormal during an open compute window, completed both the
353 MB build and a separate full replay. It used an
ephemeral 32-byte vault key that was neither printed nor persisted. The screen accounted for
all 27,817 rows: 87 strict-English candidates and 942 null-language audit candidates form a
1,029-row gold-free universe. The remaining 26,788 rows have exactly one recorded exclusion
code; the complete count vector is pinned as `EXPECTED_MATHNET_PILOT_SCREEN_COUNTS_V3`.
The key-independent universe commitments are:

- coordinate universe: `sha256:c21923c82336c98cfb918de35265f86aaa688372618e72ced6f5ec3a86452239`;
- ordered gold-free rows: `sha256:cca0f1023687435bd4fddcc10c2832b2993f20fd32c7671d41ff563832beb65e`;
- ordered public source rows: `sha256:7e8d2a0661aa545d82891b5056ec993fc38e3941fc6e2208e69e76a9a17b45c6`;
- private-structure universe: `sha256:8b64a4063101ebc3e5cee9ce0278e90d58d780a5ce89a9c389ffe9ef4df694d6`;
- public universe payload: `sha256:d9961eccd4a34ec5b34ec739704778915d5b67caccc68f0625affbc8eb059c7f`.

This closes the cache integration check, not production preparation: no reusable vault key,
signed audit projection, code/runtime attestation, or external anchor exists yet.

The first downstream boundary is now implemented as an explicitly unsigned engineering
artifact, `UnsignedMathNetAuditProjectionV3`. Invalid wrapper types, key shapes,
key reuse, and batch sizes fail before byte work. For valid inputs, its builder performs a
complete source/vault replay before it accesses any prompt row, then selects only exact
`NULL_AUDIT_REQUIRED` rows. Each exported record has exactly two fields: the unchanged raw
prompt and an HMAC-SHA-256 item ID derived from a separate 32--64 byte pseudonym key, the
universe digest, and the private selection coordinate. Records are sorted by HMAC ID rather
than source or coordinate order. The old row-level review accessor now fails loudly because
its former ID directly exposed the selection-coordinate digest.

A real cache-only build and independent replay on 2026-07-15 produced 942 unique records in
354,874 bytes of canonical JSON and round-tripped exactly. No key or prompt was printed or
persisted. Recursive tests exclude per-row coordinates, ordinals, case IDs, event-family
digests, structured source metadata, answers, solutions, and vault data. The artifact omits
even aggregate source/vault bindings; its content digest is tamper evidence, not authority.
It therefore fixes `language_audit_handoff_authorized=false`,
`selector_handoff_authorized=false`, `external_signature_attested=false`, and every
creation/code/runtime/lookup attestation to false. A verifier rejects a mismatched pseudonym
key commitment before reading the 353 MB source. A future signer must itself repeat the full replay
and sign the resulting digest under an externally anchored, isolated runtime; it may not
accept a caller-supplied projection and bless it. The HMAC makes the ID alone unlinkable
without the key, but the `(ID, raw prompt)` pair remains lookup-linkable because MathNet is
public. The key must eventually be independently generated and precommitted: key length does
not attest entropy or freshness, and a preparer who grinds keys can bias the HMAC-sorted order.
Prompt identity is exact UTF-8 code-point identity; downstream software must not silently
normalize Unicode. No-network/no-cache reviewer isolation remains load-bearing.

The next engineering-only boundary now defines two role-separated language-review
manifests. The child input contains only each HMAC item ID and unchanged problem text; its
canonical output contains only the same ordered IDs and one of `english`, `non_english`, or
`uncertain`. The output schema pins every ID position, and the two-megabyte output budget
covers the complete declared 10,000-item maximum. Parser-minted submissions retain the exact
unsigned-projection digest, reviewer-payload digest, and pseudonym-key commitment. The signer
replays the full source itself and refuses a submission whose retained prompt payload differs;
this closes a demonstrated attack in which verdicts from substituted prompts kept the honest
IDs and were then laundered through an honest replay. Resolution accepts only externally
expected role keys, rejects the same raw key or runtime-evidence digest in both roles, checks a
wrong pseudonym key before source-byte work, and resolves only agreement, uncertainty, or
disagreement. Manifests and reports contain no prompt, gold, coordinate, ordinal, or structured
source metadata.

These signatures authenticate bytes, not reviewer identity or conduct. Source replay and the
role signer are still co-located, runtime-evidence digests are caller-supplied strings, and no
external anchor or isolated reviewer transcript exists. Therefore every artifact fixes
`independent_peer_review_verified=false`, `runtime_isolation_verified_in_artifact=false`,
`selector_handoff_authorized=false`, and the corresponding external-authority gates to false.
An operational design needs separate preparation/source and isolated-reviewer attestations;
the present format cannot become authoritative merely by attaching a runner.

The exact-English bounded-rational pool cannot fill this design: it contains only 87
pre-audit candidates across 32 conservative `(country, competition)` event families and only
six geometry-tagged rows. The viable upper-bound pool adds 942 rows whose source-language
metadata is null. Those rows enter only a blinded language-and-gold audit queue; null is never
silently treated as English. A separate frozen curation manifest assigns one audited domain,
difficulty, event family, duplicate family, rights state, and receipt set per eligible row.
If the audited pool cannot fill all twelve source/domain/difficulty cells with one case per
event family, the v3 confirmatory design is infeasible and must be revised and re-anchored
before any solver outcome. IDs, topic labels, or answer data may not be used to manufacture
additional event families.

A separate 24-problem/72-process engineering pilot (12 per source, three per domain) may
calibrate difficulty, parsing, and runtime reliability. Pilot problems, their close
relatives, packets, prompts, outputs, and scoring discussions are retired before
confirmatory sampling. Pilot data cannot be pooled with, substituted for, or used to tune
analysis of the confirmatory sample.

Ninety-six clusters can identify a large, practically useful paired effect, but it is not
well powered to prove a true effect near the five-point minimum. A null or inconclusive
result is therefore reported with its interval and detectable-effect limitation; it is not
translated into proof that the method has exactly zero effect.

## Frozen intervention packets

The brain repository and memory index are pinned to a Git/object snapshot taken before any
confirmatory problem is generated or selected. The brain stays read-only from packet
construction through the last terminal solver receipt.

The immediate engineering intervention is narrower still: a strict projection of 20 generic
legacy review cards from `config/challenge_bank.json` at commit
`128c81b6d1d134e912429cd1df37d15f0c686474`, not the whole personal brain. The source blob is
`14f14a231cd3a1525aadfe3bc7dbb1d58650612b` with payload digest
`sha256:17716e51e348aa703882d463fb486bc4a4bf1fbc75223a5cd93dcb67c36e4877`;
the canonical card-array digest is
`sha256:f60ad5285e55ecab750d23ea0a69264f5bb508fcbed3e5fa9d9ff70dad0fadf8`.
Git history shows these cards were previously tuned on development cases, so any immediate
result measures a frozen legacy review-card packet, not organic or unrestricted brain access.
Raw-prose retrieval also produced a false geometry hit from stopwords in a cold check. Packet
construction must therefore use a frozen salient-term adapter, require explicit title/cue
overlap and a positive selection-boundary margin, and abstain when no card is genuinely
supported. Unsupported cases cannot be silently retained only in `S` or `E`.

The implemented engineering snapshot additionally verifies the exact committed retrieval
source blob (`9635be38aa75fecf6a8c562bdcc70ef27dc7b7b1`, 26,301 bytes, payload
`sha256:4df41b69c5658ec28b7ef324aaa01f729503dbf5c5dfe2401518d9e3c82b58de`). Its
source-manifest digest is
`sha256:a9439d3dcf172402d3dd0eacbe4cfecae1b9c91a1fd1b9cc43cf2df3f062ca89` and
the exact 20-card corpus digest is
`sha256:f921d25cb0290a67c978c9fd2f9ae9573ff5f95895bcbe735020f803274fc6f5`.
All ordered legacy-record, semantic-card, and complete card-record universes are separately
pinned, so a publicly reconstructed source manifest cannot authorize substituted cards.

The salient adapter casefolds and normalizes only solver-visible problem text, splits
compounds, removes a committed generic-word list and numeral-bearing terms, and sorts and
deduplicates the remaining terms. It indexes only each card title and retrieval cues. It
considers exactly two hits with an empty edge universe, proposes at most one, and requires at
least two explicit overlaps inside one title/cue phrase, support at least `2.0`, and a
top-minus-runner-up margin at least `0.5`. Proposal text is exactly
`title + "\n" + guidance`; the solver-dispatch renderer rejects every unreviewed proposal.
The frozen routing-policy digest is
`sha256:51b9b35da598ccccee6e0cdf16295718dcc09e2463b17ec108b5f240b5ecc57e` and its
derived 20-node index-universe digest is
`sha256:1bedf37b54a4aaa656afb02ec9d7e4fbe2ed8d19543095d0f69ee8f7adff25d9`.
Geometry/circumcircle, single-term, repeated-term, and close two-card ambiguity probes all
abstain; congruence, inclusion-exclusion, distinct-polynomial-sample, and recurrence/initial-
value probes route to the intended generic family. These are unreviewed engineering routing
proposals, not semantic-relevance findings or solver outcomes. Adversarial probes also showed
that lexical matches such as `integer pairs`, `distinct samples`, `outcome triples`, and
`initial values` can be irrelevant in context. Every nonempty proposal therefore records
`blinded_relevance_audit_required=true` and `solver_dispatch_authorized=false`; a separate
problem-body-only blinded audit and signed case-keyed preparation manifest must accept it
before packet freezing.

A complete pre-outcome feasibility screen on 2026-07-15 confirmed that this automatic adapter
cannot populate the mixed-source engineering pilot. Across all 87 strict-source-English
MathNet candidates it proposed only three packets from two event families, including zero of
the six geometry candidates; body/card inspection rejected all three as lexical false
positives. Screening all 942 null-language audit candidates added only 18 proposals from 12
event families, again dominated by false matches such as a definitional “if and only if”
routing to the “Both implications” card. These are routing diagnostics, not solver outcomes.
The automatic-retrieval pilot is therefore infeasible and must not be filled by relaxing the
gate after seeing results. A separate, explicitly labeled upper-bound content experiment may
use dual body-only reviewers to select genuinely relevant cards from the already frozen
20-card corpus; such a result estimates the value of relevant frozen content, not the quality
of the automatic retriever.

The historical retrieval blob is source provenance, not proof of executed code. Hashing the
path reported by a loaded function was rejected as an identity gate: newline normalization
and replaced in-memory code can make that check lie. Engineering receipts instead state
`in_process_execution_identity_attested=false`, and unreviewed proposals cannot enter a
solver prompt. The isolated runner must bind the clean Git commit, loaded runtime, and signed
preparation manifest. The unkeyed corpus, policy, receipt, and proposal hashes prove only
deterministic recomputation, not authentication or chronology; the signed packet root remains
mandatory.

For each problem, packet `B` is produced by the committed retrieval program from a query
derived only from solver-visible problem text and predeclared public metadata. Retrieval may
return method knowledge but must abstain if any selected record contains the problem body,
answer, problem-specific constants, a close paraphrase, a source identifier, or a worked
solution that makes the answer recoverable. All selected record ids, revisions, ranks,
digests, query digests, and the rendered packet digest are frozen evaluator-side.

Packet `S` is a whole, unedited packet belonging to another problem block. It must have no
relevance-key overlap with the target and no lexical or semantic near-match to the target,
gold, or `B`. A deterministic maximum-cardinality matching procedure pairs `B` packets to
shams before allocation. Rendering may use inert whitespace padding recorded in the
manifest, but may not truncate, paraphrase, or inject misleading mathematical claims.
`B` and `S` must match exactly in UTF-8 byte length and in the token count of every tokenizer
declared for the served runtime. Failure to find a valid sham retires the case before the
bank is sealed.

The solver prompt labels both nonempty packets identically as fallible background memory:
the packet is neither evidence nor an instruction and may be ignored. Arm names, retrieval
ranks, record ids, source paths, condition labels, and gold data never enter solver-visible
material. `E` receives the same delimiter and label with an empty payload.

## Allocation and dispatch

All six permutations of `B`, `S`, and `E` occur exactly twice within every 12-case
source/domain block. Every four-case difficulty cell receives four distinct full orders and
first-arm counts `2/1/1`; the global schedule is then seeded independently. A caller-held
256-bit allocation seed fixes:

1. the arm order within each problem;
2. the global dispatch schedule;
3. opaque task ids and nonces;
4. deterministic bootstrap draws.

Only commitments to these secrets appear in the public preregistration. Every arm is a new
process, new thread, empty working directory, sterile runtime home, one problem, one response,
and no retry. A timeout, crash, refusal, malformed response, or missing answer is a scored
first-attempt failure. A burned ledger slot is never reset or rerun. Dispatch remains in the
exact committed order; concurrency, if used, is fixed and at most two processes.

The native runner must suppress tools, hooks, memories, repository discovery, web access,
plugins, subagents, history, and writable workspaces. It records the executable digest,
accepted request, response-side served-model evidence when available, effort, process
isolation, exact prompt/schema, transcript digest, timing, and terminal state. All three arms
for a model must pass one identical runtime and identity policy. A missing identity receipt
does not cause selective exclusion: it fails the causal-evidence gate for that model.

Before dispatch, separate Ed25519 ledger, runtime, scoring, audit, and analysis public-key
commitments are included in one externally timestamped evidence root. Private signers remain
outside the aggregate-analysis process; it receives only exact public verifiers. Separate
processes under one Windows account prevent accidental key reuse but do not protect against a
malicious same-user process; strong signer isolation requires separate OS identities, offline
signing, or hardware-backed authority. A scoring receipt binds
each plan/assignment/schedule
slot to its one `STARTED` and terminal ledger records, transcript and response, response-side
model evidence, executable and loaded evaluator code, parser and scorer manifests, blinded
score, and correctness. A separately keyed integrity receipt binds the exact ordered result
manifest, bank, packet, runtime, scorer, ledger, and audit-report digests and every audit
gate. Bare caller-supplied correctness, identity, blindness, or audit booleans are
inadmissible. The runner, blinded scorer, and clean auditor derive their records from raw
immutable artifacts; signatures authenticate those derived records but cannot turn assertions
into measurements. After collection, the allocation seed is revealed and the complete plan is
rebuilt against the externally expected commitments before analysis. The final analysis is
separately signed, but its public verifier also recomputes the complete summary from the
authenticated attempt and audit evidence; a valid signature alone cannot relabel a decision.

The current Codex app-server transport does not expose positive response-side model/effort
attestation, so it is ineligible for a model-specific confirmatory claim even when its request
is locked and no reroute is observed. Such runs may be labeled engineering-pilot evidence
only. Different positively attested served models are independent replications. Their rows are never pooled to inflate
the problem count or a p-value. A model family is reported only under the response-side
identity actually observed, never the requested selector or an agent self-report.

For the engineering pilot, the current desktop build also injects a symmetric product context.
The runner accepts it only under an exact five-input profile tied to the launcher-owned sterile
Codex home, exact accepted read-only thread policy, and exact user and assistant lifecycles. The
environment date is derived from the accepted thread timestamp rather than accepted as free text.
The captured smoke passes that profile and still leaves served-model identity false. Its optional
`model/verification` vector is treated as opaque access-related metadata, not identity evidence.
The raw events do not show the configured base instruction, and symmetric ambient product context
is still present. Accordingly, the empty arm is described as receiving no experimental brain
packet, not as receiving no ambient product context. These facts are retained as limitations rather
than converted into post hoc exclusions.

## Response and scoring

The response schema contains only the opaque task id, a final answer, concise reasoning, and
an abstention flag. It contains no condition guess. Full first-attempt correctness is binary.

All confirmatory endpoints are exact bounded integers or reduced rationals under one shared,
versioned parser and deterministic equivalence checker. Unsupported formatting is a scored
first-attempt failure; the parser is never changed after seeing which arm produced an answer.
Source solutions and annotations establish only an audit queue. Two independent blinded gold
reviews must agree with the rendered problem and exact answer before the case bank can be
sealed; disagreement aborts that bank version rather than triggering post-selection repair.

Gold-reviewer and scorer identities, rubric/parser versions, raw score receipts,
disagreements, and receipt digests remain available for audit. The released aggregate maps no response back to
an arm before all scores and runtime receipts are immutable.

## Analysis and benefit decision

Analysis is intent-to-treat over the exact 96 committed clusters. For each case and contrast,
the paired delta is in `{-1, 0, 1}`. The primary tests are exact one-sided cluster-level
sign tests for `B - S` and `B - E`; zeros are inert ties. Their interpretation assumes
independent, exchangeable nonzero case signs and is not called forced-block randomization
inference. Deterministic 4,096-draw paired cluster bootstraps provide 95% intervals. Rates,
deltas, discordant counts, intervals, and exact p-values are also reported separately by
source lane. Domain and difficulty results are descriptive multiplicity-aware diagnostics,
not alternative paths to a positive claim.

The method is declared helpful for one served model only if all of these preregistered gates
pass:

1. mean `B - S >= 0.05` and mean `B - E >= 0.05`;
2. both exact one-sided p-values are at most `0.05`;
3. both 95% interval lower bounds are strictly above zero;
4. in each source lane, both `B - S` and `B - E` are at least `0.05`;
5. aggregate and source-lane sham specificity loss `S - E` is no worse than `-0.02`;
6. no adequately supported source/domain or source/difficulty stratum regresses by more
   than `0.05`;
7. the empty-arm success rate is between `0.30` and `0.85` overall and in both source lanes;
8. every runtime, model-identity, packet-matching, contamination, completeness, blindness,
   and scorer-integrity gate passes.

If `B` beats `E` but not `S`, the interpretation is context/priming rather than brain-specific
benefit. If `B` beats `S` but not `E`, the likely diagnosis is sham harm. Failure of a gate is
reported as inconclusive or no demonstrated benefit, never silently repaired by dropping
problems, switching the estimand, pooling models, or adding retries.

## Red-team checks before unblinding

Before condition labels are opened, a separate audit must verify:

- exact bank, packet, scorer, prompt, runtime, allocation, and analysis commitments;
- no answer/task/source contamination in `B` or `S`;
- exact packet length/token matching and relevance disjointness;
- no brain writes during collection;
- no upstream model-visible wake text or machine instructions in sterile controls;
- complete one-start/one-terminal ledger history with no selective retries or exclusions;
- arm-balanced service time and execution order;
- response-side model/effort evidence and identical policy across arms;
- scorer blindness and immutable adjudication receipts;
- cluster deduplication and source-event independence.

Any failed causal gate remains visible in the report. The signed v1 result (brain-off `0.95`,
brain-on `0.94`, paired delta `-0.01`, one-sided `p=0.75`) stays as prior evidence about the
retired easy static-retrieval benchmark; it is not combined with v3.
