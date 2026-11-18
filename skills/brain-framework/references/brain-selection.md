# Designated main and project brains

The public framework omits lived identity and memories (`docs/PORTING.md`). A freshly seeded corpus is not proof of the user's main brain. These bindings are an explicit connector overlay implementing this user's main-always rule; they do not weaken upstream project-signature/capture gates.

## Establish identity, then prove readiness

Inspect existing connection/configuration evidence without changing it. Ask only for a genuinely missing owner designation or access. After the owner identifies an existing connector deployment:

```
python3 SKILL/scripts/brainctl.py --profile MAIN-PROFILE bind-main --authorization "owner request/session evidence" 
python3 SKILL/scripts/brainctl.py --profile MAIN-PROFILE bind-main --authorization "owner request/session evidence" --apply
python3 SKILL/scripts/brainctl.py --profile MAIN-PROFILE --workspace ACTUAL-WORKSPACE resolve
python3 SKILL/scripts/brainctl.py --profile MAIN-PROFILE --workspace ACTUAL-WORKSPACE admit --session ACTUAL-SESSION
```

`SKILL`, profiles, workspaces and session ids are placeholders for verified actual values. Binding writes only `brain-framework/brain-bindings.json` in the selected profile; it never creates/imports a corpus. Claim/assert that exact destination before applying. Authorization is a provenance pointer, not a cryptographic permission token; the agent still needs actual user authority. The descriptor binds the deployment id, interpreter, runtime, source revision, canonical HOME/slug/corpus, resolved paths and explicit workspace roots. Main identity, canonical markers, disconnect and path drift are rechecked before CLI memory operations and hook execution. Lost registry, changed deployment/source/paths or inaccessible corpus fails closed. Do not automatically regenerate/rebind after a migration; inspect continuity and obtain authorization where the existing request does not cover the exact migration.

A remote/existing brain outside this connector requires its actual authenticated supported connection and identity/health evidence. This package currently implements local connector-profile bindings, not arbitrary remote transport or adoption of an unmanaged private deployment. Diagnose the missing adapter/access explicitly; never initialize a local replacement to make remote access appear complete. Portable installation controls do not prove a different OS or runtime.

The invoking receiver must itself be the qualified designated main host or selected project host. An empty/unrelated profile cannot borrow another profile's green health checks. Admission and native mutation validation include that receiver's identity, installed skill, instructions, hooks, configuration and evidence fingerprint. A shared-main binding alone is connection provenance, not host qualification; a receiver without an authorized supported runtime connection stays blocked. Never create a substitute corpus to satisfy this constraint.

## Add a project only when required

Use an existing, authorized, separate project deployment. An owner-authorized fresh project setup follows the upstream domain setup and its private inputs; it does not copy main into a new store. Domain stores such as Math/Film retain their native schemas and proof/signature admission in addition to this connector registry. For a project exposed as a connector profile:

```
python3 SKILL/scripts/brainctl.py --profile MAIN-PROFILE bind-project --id PROJECT-ID --target-profile PROJECT-PROFILE --root PROJECT-WORKSPACE --authorization "owner project scope evidence" --apply
python3 SKILL/scripts/brainctl.py --profile MAIN-PROFILE --brain PROJECT-ID --workspace PROJECT-WORKSPACE resolve
python3 SKILL/scripts/brainctl.py --profile MAIN-PROFILE --brain PROJECT-ID --workspace PROJECT-WORKSPACE admit --session ACTUAL-SESSION
```

Main is always validated first. The project must have a distinct corpus and explicit existing workspace roots. Never choose among overlapping mappings by path length or recency: select the actual project explicitly. A mapped workspace without selection is blocked at admission, while main recall remains available for calibration. Main/project access does not widen a worker's permitted nodes or authorize cross-project disclosure. A project profile needs its own runtime qualification and hooks; the registry does not install or qualify it. Before its native hooks run, use `bind-main --target-profile MAIN-PROFILE` in that project profile to designate the same actual main, and bind the project id/workspace there too. This is an explicit local connection; it neither copies main nor supplies remote transport. Its hook validates main plus the uniquely mapped project against the actual event workspace.

## Route normal operations

```
python3 SKILL/scripts/brainctl.py --profile MAIN-PROFILE recall "task and failure history"
python3 SKILL/scripts/brainctl.py --profile MAIN-PROFILE native brain node SLUG
python3 SKILL/scripts/brainctl.py --profile MAIN-PROFILE --brain PROJECT-ID --workspace PROJECT-WORKSPACE recall "domain evidence"
python3 SKILL/scripts/brainctl.py --profile MAIN-PROFILE --brain PROJECT-ID --workspace PROJECT-WORKSPACE native brain remember SLUG ...
```

Overarching identity/preferences/working method belong to main. Project-specific findings belong to that project's native store. Coordinate each native index and node write with claims; confirm node/recall effects afterward. Never mirror private project content into main by default. Connector receipts/cache logs inside the designated deployment are permitted runtime state; scratch files, user deliverables and local checkpoints are never substitute brain memories.

## Admission and the maintenance boundary

`admit` saves a separate receipt under `brain-framework/activations/` keyed by actual session, workspace and brain selection, so concurrent roots/workers cannot overwrite one another's admission. Claim/assert the exact receipt destination before mutation (the deterministic `activation_path` helper returns it). Normal native memory mutations require global `--session SESSION --workspace WORKSPACE` and an unexpired passing receipt for the same binding/configuration; a changed evidence ledger also invalidates mutation admission. `--maintenance setup|repair|qualification|skill-maintenance` is limited to the explicitly assigned repair and retains all failed gates.

The caller's `--session` must also match the invoking runtime's native identity signal. On this Mac/Codex receiver that observed signal is `CODEX_THREAD_ID`; the report records it separately from the supplied argument. A child cannot reuse its parent's receipt, a receipt predating this check cannot authorize mutations, and missing identity keeps admission blocked even when the other diagnostics pass. Missing identity does not authorize guessing from a parent environment variable or searching for a convenient green receipt. An environment signal is a cooperative runtime boundary, not protection against deliberate environment spoofing by unrestricted code. Other runtimes still need their own native identity/delivery qualification; naming an environment variable or passing a unit test does not qualify them.

The independent native observer uses the host-delivered JSON event's session/workspace through `require_hook_admission`. Native hook subprocesses on this Mac inherit a parent's `CODEX_THREAD_ID`; it is not their event identity. This observer entry point still requires a passing, current receipt produced by that actual session's own admission and all the same binding/configuration checks. Missing event fields, a parent's receipt or an unverified admission context fail. CLI mutation dispatch continues through `require_admission` and its own native tool environment; it cannot select the observer entry point or supply an alternate event identity through CLI arguments. These are cooperative host entry points, not a kernel boundary against unrestricted code.

`admit` does not reuse a green activation receipt: it executes the original host/smoke/recall checks, validates actual source/code/configuration/receiver evidence, inspects all source-backed setup obligations and tests main wake-node readability. Its JSON retains every failure; exit 2 blocks ordinary work. Counts come from `mechanisms.json`, not a promise that future revisions have the same count. All applicable obligations must have current discriminating evidence; a scope exception must retain its authority, reason and actual applicable test. The adapter derives required faculties from explicit deployment selection plus registered reflex/checklist/continuity/compute hook hosts, and always requires core. Each selection basis is recorded. A selected project additionally requires S23. Unselected faculties remain visibly unverified in the complete inventory; unknown status in a required row blocks admission. No missing required input is silently waived. Do not edit evidence JSON simply to clear a gate.

An admission failure permits diagnosis/authorized repair/qualification and skill maintenance only. Native reads/writes needed for that repair use the valid designated brain. If binding itself is missing, only the minimum direct original-native setup/mesh operations needed to establish the explicitly authorized binding may run; they must name the exact existing deployment, never guess another location. No repair-lane flag grants permission for general work. Admission is a tested connector/instruction contract, not a kernel lock over arbitrary agent tools or hidden reasoning. Host hooks must separately prove their actual blocking behavior where a policy requires it.
