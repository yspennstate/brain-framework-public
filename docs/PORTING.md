# What was left behind, and the choices made

This repository was extracted from a working private deployment on 2026-07-15. Every
file was classified before copying (machinery / machinery-with-personal-content /
personal content / excluded domain), personal constants were parameterized, and lived
payloads were replaced with synthetic equivalents. The record below is what a future
maintainer needs in order to not re-import what was deliberately left out.

## Excluded outright

- All lived content: memories, dreams, journals, transcripts, session registries and
  checkpoints, learned state files (instinct corpora, salience scores, prediction
  ledgers, embeddings), logs, and the owner-model documents. The formats ship; the
  contents do not. `bootstrap/templates/seed_memories/` re-authors the seed set blank.
- Everything in the source deployment's excluded domain (its production stack
  and anything coupled to it), including: a crash-notifier whose transport was that
  stack's mailer, an infrastructure prober for its data hub, snapshot/zip utilities
  hardwired to its trees, a disk-hygiene tool bound to its data directories and archive
  bucket, and a self-healing supervisor whose watch list was that stack. Where the
  underlying pattern was worth keeping (preserve-before-rotate, alert-only guardianship,
  tiered mitigation), the pattern is described in the docs even though that
  implementation stayed behind.
- Per-agent restore wrappers containing real session ids and task prompts; dated one-off
  experiment workspaces; generated policy/routing files (regenerate from your own
  ledger); vendor plugin caches.

## Parameterized at install time

Three tokens run through the tree and are expanded by `bootstrap/install.py`:
`{{BRAIN_SLUG}}` (the memory-corpus project slug), `{{USERPROFILE}}` (the home
directory), `{{TIMEZONE}}`. PowerShell resolves `$env:USERPROFILE` at runtime instead;
VBS launchers expand `%USERPROFILE%` via `ExpandEnvironmentStrings`. Cloud identifiers
in the resilience layer (`<aws-account-primary>`, `<kms-key-id-1>`, bucket names, pinned
key fingerprints) are placeholders you must replace with your own before that subsystem
can run; its pinned trust-root public keys were removed and must be regenerated for your
keys.

## Kept, with a documented judgment call

Detector regexes keep their vocabulary even where that vocabulary names public financial
instruments: a reflex whose job is to catch "a daily-reset leveraged ETF treated as a
clean linear multiple" cannot detect that failure class without the tickers people
actually write. These are detection mechanisms over public words, not deployment data.
Every fixture, example, and test that carried the source deployment's actual claims,
figures, or idiolect was replaced with synthetic equivalents of the same shape.

## Verification that gated the export

Mechanical scrubbing was driven by a watch-list scanner that had to reach zero
unexplained lines; every PowerShell file passes `[Parser]::ParseFile`; every Python file
compiles; JSON parses; the whole tree was swept for secret shapes (key prefixes, PEM
blocks, high-entropy assignments) and for the source machine's identifiers (names,
handles, hosts, addresses, account numbers). The repository history is fresh: nothing
before the initial commit exists to leak.
