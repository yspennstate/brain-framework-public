# First-attempt evaluation ledger

`math_brain.attempt_ledger` is the dispatch boundary for a sealed evaluation. Its
purpose is narrower than general event logging: once a solver slot might have run,
the slot must never be run again. A convenient retry after seeing an answer would
invalidate the pairing and the preregistration.

## What is committed

Before any runner is launched, the caller creates one ledger with the exact ordered
slot list. Slot IDs are opaque `slot_<128-bit hex>` values. The precommitment stores
only domain-separated HMAC commitments to:

- the run specification;
- the batch;
- the exact prompt;
- the exact output schema;
- the request policy; and
- the dispatch nonce.

It stores no condition label, pair label, prompt text, challenge text, answer, or
HMAC key. The caller supplies at least 32 bytes of key material on every create/open
operation and owns key generation, access control, backup, and destruction.

The ordered commitment is `precommit.json`. Its exact canonical bytes, including its
HMAC, define the ledger digest used by every later record.

## State machine and write order

Each transition is its own immutable JSON file:

```text
PENDING -> STARTED -> OUTCOME_SELECTED -> RETURNED
                                      -> FAILED
```

There is no transition back to `PENDING`. A final record name is never used as the
write-in-progress path. Each record is first created under `staging/` with `O_CREAT |
O_EXCL`, written fully, fsynced, and closed. The complete staged inode is then
published at the final name with an atomic hard-link operation that fails if the
name already exists. Only after that no-replace publication is the final parent
directory fsynced. Concurrent readers therefore see either no final record or all
of its authenticated bytes; they never parse a zero-length or partially written
final file. Abandoned staging files are not transitions and are ignored.

A reader validates against a stable final-name inventory spanning both transitions
and outcome objects. If an atomic publication overlaps the parse, the reader retries
the whole snapshot; it never treats a mixture of the before/after histories as
corruption. Retries are bounded, a quiescent valid inventory is parsed once, and a
quiescent invalid inventory fails immediately.

The exact runner order is:

1. re-present the exact in-memory `DispatchMaterial`, recompute all six HMAC
   commitments, and reject any difference before allocating a directory;
2. allocate a random `run_<128-bit hex>` empty directory outside every Git repo and
   outside the ledger itself;
3. append and fsync `STARTED` with that directory's opaque ID and device/inode;
4. return from `start`; only now may the caller invoke the runner, which receives the
   same verified `DispatchMaterial` object;
5. compute the outcome object's content address and race one fixed per-STARTED
   `OUTCOME_SELECTED` path using the atomic no-replace publication primitive; a
   losing writer stops before creating an object;
6. persist and fsync the selected returned artifact or sanitized failure as a signed,
   content-addressed object; and
7. append and fsync `RETURNED` or `FAILED`, referring to both the immutable selection
   and its object.

The fixed selection path closes a check-then-create race that content-derived object
names cannot close. Two concurrent different outcomes cannot both become candidates:
exactly one creates `OUTCOME_SELECTED`, and the loser writes no object.

`execute_first_attempt` applies this order around a caller-supplied synchronous
runner. It catches ordinary `Exception` values, commits only an opaque HMAC of the
exception type (never the exception message), writes a failure object, and then
writes `FAILED`. Process death, `KeyboardInterrupt`, and other `BaseException`
events leave the already-fsynced `STARTED` record in place.

JSON artifacts must themselves use the module's exact canonical JSON encoding:
sorted keys, compact separators, ASCII escapes, NFC strings, no duplicate keys, and
no non-finite numbers. Binary evidence can instead use a non-JSON media type and is
base64-wrapped inside the canonical outcome object.

## Crash recovery

Recovery never removes or overwrites a record.

| Last durable boundary | Recovery behavior |
| --- | --- |
| No `STARTED` | Slot is still pending; the runner could not have been invoked through this API. |
| `STARTED` only | Slot remains burned and is reported as `burned_without_outcome`; it is never retried. |
| `OUTCOME_SELECTED` but no object | Slot remains burned. This is the conservative result of a crash after winning selection but before the object fsync. |
| Selected object but no terminal record | The object is an orphan. It is finalized only if the selection HMAC, object HMAC, content address, ledger digest, slot ordinal, slot ID, and exact parent `STARTED` digest all validate. |
| Terminal record | State is returned idempotently; the file is not rewritten. |

An object from another ledger, an object without the exact fixed selection and
surviving `STARTED` parent, both terminal kinds, an unknown file, a duplicate JSON
key, or any non-canonical encoding makes recovery fail closed.

## Minimal use

```python
from math_brain.attempt_ledger import ArtifactPayload, AttemptLedger, DispatchMaterial

dispatch = DispatchMaterial(
    run_spec_digest=run_spec.digest,
    batch_digest=batch.digest,
    prompt=exact_prompt,
    output_schema_json=canonical_schema_bytes,
    request_policy_digest=runtime_policy_digest,
    dispatch_nonce=batch.dispatch_nonce,
)
slot = dispatch.seal(
    key=hmac_key,
    slot_id=opaque_slot_id,
)
ledger = AttemptLedger.create(ledger_path, key=hmac_key, slots=(slot,))

terminal = ledger.execute_first_attempt(
    slot.slot_id,
    runtime_parent=outside_repo_runtime_parent,
    repository_root=repository_root,
    dispatch=dispatch,
    runner=lambda runtime_dir, exact: ArtifactPayload(canonical_artifact_bytes),
)
```

The evaluation orchestrator must serialize and immediately persist the full runner
artifact, including the raw output and protocol evidence needed for later scoring.
The ledger deliberately does not import or launch Codex and does not decide what
artifact fields are sufficient. After restart, `selected_outcome(slot_id)` recovers
the durable metadata and `load_selected_artifact(slot_id)` returns the exact selected
bytes through the public API.

## Limits and trust boundary

- HMAC integrity is not non-repudiation. A holder of the key can author records.
- A same-user attacker can delete the complete ledger or delete a transition. For
  adversarial deletion resistance, externally anchor the precommit and transition
  inventory in write-once or separately controlled storage.
- File `fsync` is mandatory. Python/Windows does not expose a portable directory
  `fsync`; directory-entry flushing is therefore best effort on Windows. NTFS and
  the host's storage stack remain part of the crash-durability assumption.
- Atomic publication requires same-volume hard-link support in the ledger
  filesystem. The implementation fails closed if that exclusive final-name
  primitive is unavailable; it does not fall back to exposing a partial final file.
- The conservative boundary can burn a slot when the process died after `STARTED`
  but before the runner actually began. That loss of data is intentional; retrying
  would be less trustworthy than retaining a missing first attempt in the
  denominator.
- Runtime directories are opaque and outside Git, but cleanup and secure deletion
  are caller responsibilities after evidence has been persisted.
- An interruption or persistence error can leave an abandoned staging name containing
  zero, partial, or complete outcome bytes. If it happens after publication, that name
  can be hard-linked to a final record. Staging is never interpreted as history; cleanup
  must unlink names only (never overwrite their contents), under the same private-evidence
  retention policy as the selected artifact store.
- Payload serialization is caller-owned. A terminal record proves which bytes were
  committed, not that those bytes include every fact required by the evaluator.
