# ProofSwarm

ProofSwarm is a local multi-agent mathematical review and discovery system. Its
normal and only enabled execution path has zero marginal API cost:

1. role prompts are sent to an OpenAI-compatible model server on loopback;
2. proof tickets are stored as bounded, revision-checked local JSON;
3. Lean source is compiled by the local Lean/mathlib toolchain;
4. numerical and symbolic checks run in local Python;
5. every positive verdict must be backed by mechanical evidence.

Vertex AI, Cloud Run, Firestore, GCS, and automatic cloud fallback are disabled.
Every script in `infra/` is now an executable tombstone that exits before any
remote command; the current Python package does not import cloud SDKs or expose a
cloud provider.
A future cloud path would require a separate, bounded preflight that proves the
entire run has zero marginal charge. Unknown or delayed billing data means deny.

## Safety boundary

- `PROOFSWARM_INFERENCE_BACKEND` and `PROOFSWARM_STORAGE_BACKEND` must both be
  `local`.
- The legacy `PROOFSWARM_BACKEND` selector is accepted only when its value is
  exactly `local`; values such as `vertex` and `firestore` fail at import.
- `PROOFSWARM_LOCAL_URL` must resolve syntactically to `localhost`, `127.0.0.1`,
  or another loopback IP. Remote hosts and embedded credentials are rejected.
- Ticket, trace, calibration, and Lean roots must be absolute local-disk paths;
  UNC paths and mapped network drives are rejected.
- ProofSwarm does not start Ollama, LM Studio, llama.cpp, or another model server.
  The owner starts the chosen local server separately.
- Before every real model or Lean call, ProofSwarm rereads the compute governor.
  It runs only while the owner is away, mode is open, telemetry is fresh, and
  memory headroom is safe. Long Lean runs recheck during compilation and stop
  their hidden process tree if the governor closes.
- Windows Lean children use no-window, BelowNormal creation flags. Current model
  work is serialized until fleet-wide memory reservations exist.
- Ticket writes use per-ID machine locks and optimistic revisions; competing or
  stale local swarm writers stop instead of overwriting evidence.
- Every real execution surface requires a fresh 24-hour calibration receipt
  bound to the exact local model, loopback endpoint, full role-prompt bundle,
  and known-sound, known-false, boundary, and malformed controls. Missing,
  stale, changed, or contradictory evidence stops before ticket mutation.

## Quick start

The deterministic smoke test makes no model or Lean call:

```powershell
cd %USERPROFILE%\proofswarm
python -m proofswarm.orchestrator --mock --rounds 5
```

For a real local run, first start an already-installed OpenAI-compatible server
without using ProofSwarm as its launcher, then set the loopback endpoint and model:

```powershell
$env:PROOFSWARM_INFERENCE_BACKEND = "local"
$env:PROOFSWARM_STORAGE_BACKEND = "local"
$env:PROOFSWARM_LOCAL_URL = "http://127.0.0.1:11434/v1"
$env:PROOFSWARM_LOCAL_MODEL = "qwen2.5-coder"
python -m proofswarm.orchestrator --calibrate orchestrator
python -m proofswarm.orchestrator --rounds 12
```

If the server is absent, the owner returns, the governor throttles, or any required
worker fails, the run stops. It never tries a paid provider.

Paper review and the autonomous solver have distinct prompt bundles. Calibrate
them explicitly with `--calibrate paper_review` or `--calibrate autonomous`;
`--calibrate all` refreshes all three receipts serially. Calibration retains each
exact synthetic role output and stops on the first bad control.

## Lean verification contract

`proofswarm.tools.lean_verify` writes one unique temporary `.lean` file inside the
local `lean_worker` project and runs `lake env lean` on that exact UTF-8 source. The
result carries its SHA-256. The verifier rejects `sorry`, `admit`, newly declared
axioms/constants, native trust shortcuts, and source-level metaprogramming or
execution commands before launch. Source size and import count are fixed-bounded.
Lean output is capped at 8 MiB. A complete proof source can no longer silently
replace or discard a separately submitted statement.

Kernel compilation proves only the exact Lean source. It does not prove that an
informal statement was translated faithfully; statement-fidelity review remains a
separate obligation. Consequently, a clean kernel result yields `needs_human`, not
`lean_verified`, until a human review record is hash-bound to both the complete
informal statement and the exact compiled Lean source. Prover output is preserved
separately, and the autonomous workflow still runs its adversarial and referee
passes after a clean compile.

## Tests

```powershell
python -m pytest -q
```

The policy tests include negative controls for every former cloud selector, remote
model URLs, missing verification evidence, source-discarding Lean requests, and
trust bypasses.

## Repository map

- `proofswarm/` — local orchestration, schemas, prompts, storage, and tools.
- `lean_worker/` — pinned Lean project and the optional local FastAPI shim.
- `problems/` — mathematical problem cards.
- `tests/` — local policy, verifier, numerical, and mock-lifecycle tests.
- `traces/` — private local run artifacts.
- `infra/` — disabled historical GCP proof-of-concept scripts.
