# Repository rules for every agent

This repository is exclusively for pure and applied mathematics research that
is safe to isolate from the rest of the machine.

## Scope boundary

- Never read, ingest, summarize, copy, link to, or infer from trading systems,
  financial/options research, company material, credentials, client work,
  personal archives, or unrelated projects.
- Local mathematical work is not automatically public. Read only an explicit
  per-run allowlist. Keep inputs, derived excerpts, transcripts, and reports in
  gitignored `local/` storage until a human performs provenance review.
- Do not weaken a deny rule to make a scan succeed. Ambiguity means quarantine.
- Do not put absolute local paths, usernames, hostnames, tokens, account data,
  or private repository names in committed artifacts.

## Mathematical claims

- Separate conjecture, heuristic, numerical evidence, proof sketch, proof, and
  machine-checked theorem.
- State assumptions and quantifiers. Check boundary cases and degenerate cases.
- Recompute numerics through an independent path when they support a conclusion.
- For proofs, trace each dependency and try to construct a counterexample before
  promotion to `verified`.
- A Lean check verifies the formal statement and kernel proof, not the fidelity
  of the translation from prose. Record a separate statement-fidelity review.
- A citation is verified only when the exact source, locator, and supporting
  passage or theorem have been checked.

## Benchmark integrity

- Benchmark items must have deterministic generation metadata and redistributable
  provenance.
- A faulty solution contains exactly one intentional semantic fault site. Pure
  formatting changes do not count. Validators must reject accidental second faults.
- Brain-on and brain-off solvers receive identical problems, budgets, tools, and
  scoring. Randomization, item assignment, and exclusions are committed before
  outcomes are observed.
- Keep answer keys hidden from solvers and run evaluation in fresh contexts.

## Engineering

- Prefer standard-library, deterministic components in the trusted core.
- Every persisted record is schema-versioned and content-hashed.
- Never silently overwrite evidence or verification history.
- Run privacy and secret scans before every commit and push.
- Treat a green test as evidence only for the behavior it actually covers.
