# Brain resilience backup

This is an independent disaster-recovery path for the shared brain. It does
not replace the canonical private repository or the nightly dream push.

## What is backed up

1. `<github-user>/ai-brain-resilience-backup` receives a private, fresh-history,
   text-only operational snapshot. Every included current file is sanitized,
   the staged tree and all reachable history are scanned again, and the push is
   fast-forward only. Raw transcripts, credentials, binaries, and oversize
   files remain outside this layer.
2. Two independent S3 vaults receive the exact private recovery state. The
   primary account uses 30-day Governance Object Lock; the secondary account
   uses 90-day Compliance Object Lock. Every artifact is encrypted locally with
   AES-256-GCM, its one-time data key is wrapped for KMS keys in both accounts,
   and ciphertext is stored as exact-version 512 MiB chunks in both vaults.
3. Each completed run has a receipt signed by pinned RSA KMS keys in both AWS
   accounts. Signed receipts form a single predecessor chain. A mutable
   `latest.json` pointer is only a convenience; receipt discovery inventories
   all immutable versions, rejects missing history/forks/rollback disagreement,
   and verifies the exact manifest and recovery dependencies.

The S3 layer captures every ref under `refs/`, the checked-out source HEAD,
staged and unstaged binary patches, and all non-ignored untracked files. Dirty
state in other linked worktrees is not captured; their committed refs are.
The source worktree and index are never mutated by a backup run.

## One-time setup

Use the agent-mesh claims required by the machine-wide instructions. The AWS
CLI profiles must have access to the already-created encryption/signing KMS
keys, and the two pinned public-key files under `trust/` must match them.

```powershell
Set-Location <HOME>\brain-repo\12_cognitive_architecture\resilience

# Primary account, us-east-1, Governance 30 days (the script defaults).
.\bootstrap_s3.ps1

# Secondary account, us-west-2, Compliance 90 days.
.\bootstrap_s3.ps1 `
  -Bucket <github-user>-ai-brain-backup-secondary-<aws-account-secondary> `
  -Region us-west-2 -Profile secondary -LockMode COMPLIANCE -LockDays 90

# The private GitHub repository must exist, be empty, and be unarchived.
python .\backup_brain.py seed-github

# Install only after a manual backup and restore drill both pass.
.\install_task.ps1
```

The scheduled task runs daily at 04:35 through `wscript.exe //B` and
`launch_backup.vbs`, which creates the PowerShell process hidden at launch.
The installer pins the current Python executable in local runtime state. The
hidden wrapper runs BelowNormal and waits (without doing backup work) until the
compute governor reports `open` or `owner_away_open`; the Python runner checks
the governor again before capture, bundling, and upload.
Verify the installed task with `<HOME>\flash_hunt.ps1` at a polling
interval of 50 ms or less before calling it safe.

## Back up and verify

```powershell
python .\backup_brain.py run
python .\backup_brain.py verify
python .\backup_brain.py status
```

`run` refreshes the sanitized GitHub layer, captures a stable source state,
uploads client-encrypted artifacts to both accounts, verifies exact version,
checksum, retention, KMS recipients, and bucket controls, then publishes the
dual-signed receipt and pointers. `last_success.json` is written only after both
vaults resolve to the same authenticated tip. Local state is a cache; the
immutable signed remote chain is authoritative.

## Exact encrypted restore

Do not manually download a bundle or run `aws s3 sync`: S3 contains encrypted,
versioned chunks, and later runs may be incremental, ref-only, or reference
runs. Use the clean-room restore command from a trusted checkout containing the
pinned public keys:

```powershell
python .\restore_drill.py `
  --vault auto `
  --destination C:\restore\brain-drill
```

`auto` verifies both receipt chains when both accounts are reachable and fails
closed if their signed tips disagree. If an exact primary artifact is missing,
it retries the complete restore from secondary replicas. On a clean machine,
AWS profile names can be remapped:

```powershell
python .\restore_drill.py `
  --vault secondary `
  --secondary-profile recovery-account `
  --destination C:\restore\brain-drill
```

If an offline or previously recorded receipt hash is available, add
`--expected-receipt-sha256 <sha256>` for an external rollback anchor.

The command walks and verifies every dual-signed receipt, downloads exact S3
versions, reassembles and authenticates ciphertext, decrypts through either KMS
account, reconstructs full/incremental/ref-update history, asserts every ref,
runs `git fsck --full`, and reproduces staged, unstaged, and untracked state. It
writes exact signed receipts/manifests under `evidence/` and a local
`RESTORE_RECEIPT.json`. Re-verify the finished restore without AWS access:

```powershell
python .\restore_drill.py --verify-existing C:\restore\brain-drill
```

That second pass independently verifies both pinned signatures, the complete
receipt chain, signed manifest hashes, exact refs and HEAD, `git fsck`, and the
signed working-state hash. The restored repository is under `repository.git`;
the detached recovered worktree is under `worktree`. Follow the repository-root
`RESTORE.md` only after this verifier passes.

Logs, staging, sanitized-snapshot state, and the local success cache live under
`<HOME>\.brain-resilience`. A backup is not healthy merely because an
upload command returned zero: clean GitHub privacy, two immutable ciphertext
replicas, both receipt signatures, a contiguous history, and an independently
verified clean-room restore must all pass.
