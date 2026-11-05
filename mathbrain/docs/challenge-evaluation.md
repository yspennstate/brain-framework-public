# Sealed challenge evaluation

The challenge study is a ten-pair, twenty-process comparison of the frozen
prompt-driven reference brain against an empty sham. Each process receives the same ordered set of
twenty opaque cases for its matched pair. The only treatment difference is the precommitted
per-case reference packet; case views, order, model request, instructions, schema, and runtime
policy are matched.

`config/challenge_evaluation.toml` is the tracked preregistration template. It requests
`gpt-5.6-sol` from `openai` at `ultra` effort, disables provider fallback and solver tools, requires
ten even pairs, and assigns primary weights 0.45 validity, 0.25 verified repair, 0.20 verified
witness, 0.10 exact error family, and 0.00 localization. Localization is deliberately unscored:
the fixed challenge generator always mutates line 3, so a localization treatment claim would be
confounded. Difficulty is likewise fixed with family and is not interpreted as a subgroup effect.

## Freeze and unseal order

The evaluator first binds the clean Git revision, exact native executable path/size/SHA-256,
runtime policy, solver prompt, challenge and holdout seals, five seed commitments, and opaque-id
salt commitment. It writes this public-only preregistration to an exclusive file and fsyncs both
the file and its directory before the CLI reads the private payload for the first time. Only the
digest of that evaluator manifest is then supplied to an explicit evaluator-role request that
acknowledges the holdout will not be used for improvement. The CLI verifies the exact clean
revision again immediately before the payload read and before solver dispatch. After unsealing it
persists all twenty opaque HMAC slots before launching any solver. At that point the reference
cards and retrieval code remain frozen at the preregistered revision.

Prompt-only top-three retrieval runs on the solver-visible problem prompt. The clean and faulty
trace variants must produce byte-identical retrieval results or preparation fails. Brain-off gets
the hashed empty sham. The private precommitment contains digests and an evaluator-only slot map,
not holdout prompts, numbered solutions, answer keys, or raw reference content.

## First-attempt execution

Every one of the twenty opaque slots HMAC-commits the exact run spec, batch, rendered prompt,
canonical output schema, request policy, and dispatch nonce. The ledger re-verifies all six fields
before allocating a runtime directory and again before atomically publishing `STARTED`. A fresh
empty directory, fresh hidden BelowNormal Codex process, and fresh ephemeral thread are used once.
There are no retries. A crash after `STARTED` burns the slot.

Each solver also receives a unique auth-only `CODEX_HOME` and a minimal child environment. The
credential-bearing home is purged before a successful artifact can return. The raw event audit
fails closed on any model-visible developer, system, or user input not independently supplied by
the harness. Such runs may still contribute descriptive parser scores after every slot is durable,
but they cannot pass the strict isolation gate.

Raw `CodexRunResult` evidence is canonical private JSON, never pickle. It retains every constructor
field, raw transcript and stderr bytes, requests, executable provenance, audit, and output. The
runner caps each raw JSONL transcript at 8,000,000 bytes so the canonical duplicated evidence stays
inside the ledger's 64 MiB artifact envelope; an overflow is a sanitized infrastructure failure,
not a retry. The HMAC ledger selects one content-addressed outcome before publishing a terminal transition. No
response is parsed, compared across treatments, or scored until all twenty slots have durable
`RETURNED` or `FAILED` records. Scoring then reloads artifacts from the ledger and reruns all
transport, parser, repair, and witness invariants.

The CLI requires explicit private paths, tracked configuration files, code loaded from the named
repository, and an exact clean revision with no tracked or untracked changes:

```powershell
$env:PYTHONPATH = 'src'
python examples/run_challenge_study.py `
  --repository-root C:/path/to/math-research-brain `
  --challenge-config config/challenge_bank.json `
  --evaluation-config config/challenge_evaluation.toml `
  --holdout-payload C:/private/challenge_bank_holdout.json `
  --executable C:/absolute/native/codex.exe `
  --codex-auth-file C:/private/codex/auth.json `
  --brain-revision <clean-git-object-id> `
  --workspace-parent C:/private/evaluations `
  --max-workers 1
```

The workspace must be outside every Git repository. It contains plaintext private raw evidence and
caller-held control material and must never be committed or published. The numerical summary is
privacy-safe, but publication still requires the normal privacy and provenance review.

## Interpretation boundary

Accepted request selectors are not proof of the model that served a response. The current app
server has no authoritative response-side model attestation and does not enforce the configured
output-token cap. Consequently a positive descriptive paired delta remains scientifically useful,
but the strict benefit gate must report those missing guarantees and cannot claim a fully attested
benefit. The Windows executable lease attests the launched native image inside the trusted
evaluator process; it is not publisher signing, DLL-set attestation, or hostile-kernel proof.
