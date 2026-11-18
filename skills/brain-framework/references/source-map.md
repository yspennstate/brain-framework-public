# Original-source resolution

Authority: [brain-framework-public at ca3d7bae9f2d4d22ab91a23005ce85364ddebbf7](https://github.com/yspennstate/brain-framework-public/tree/ca3d7bae9f2d4d22ab91a23005ce85364ddebbf7). `upstream-lock.json` records all Git blob hashes. Inside a checkout of this repository the checkout itself is that source, checked file by file against the lock; outside one, use `--source` or `--network`. Either way it is the public tree without lived state. Keep this revision stable across the task and workers.

Run the installed `scripts/brainctl.py status` to locate an existing deployment's original `source`. `brainctl.py source` acquires/verifies pinned source from the bundle, or, when the skill ships inside the public repository (`skills/brain-framework/`, or `framework/skills/brain-framework/` in math-brain-public) and no bundle is present, from that checkout, file by file against the lock (`scripts/repin_lock.py --check` shows drift); `source --network` fetches GitHub and verifies the same hashes; `source --source PATH` accepts a matching authorized checkout. Acquisition is source access, not runtime installation. Do not trust an unverified cache, branch label or installer exit as source evidence.

At activation, read original `hooks/brain_startup.template.md` and `docs/METHOD.md`. Paths below are relative to the verified source root; online, resolve against the pinned GitHub blob URL. Originals supply method; authorized deployment state supplies the user's history. Upstream omits private identities, corpora, learned state and cloud trust material.

| Task/faculty | Original sources |
|---|---|
| Architecture/discovery | `docs/ARCHITECTURE.md`, `docs/FULL_BRAIN_MAP.md`, `README.md` |
| Install/port | `bootstrap/README.md`, `bootstrap/install.py`, `docs/PORTING.md`. On the Mac, also read `docs/MACOS_INSTALL.md` and inspect the actual installed runtime. |
| Isolated exercises | `docs/EXERCISING.md`, `scripts/smoke_framework.py` |
| Identity, voice, creativity, activation | `docs/COGNITIVE_ARCHITECTURE.md`, `bootstrap/templates/CLAUDE.template.md`, `bootstrap/templates/seed_memories/` |
| Recall/weak matches | `dreaming/brain.py`, `docs/RECALL_LEVERS.md` |
| Semantic publication | `dreaming/build_memory_embeddings.py`, `dreaming/publish_memory_embeddings.py`, `dreaming/embedding_coverage.py`; the publication document mentioned by the builder is absent from this public tree |
| Memory/corrections | `docs/MEMORY_CONVENTIONS.md`, `docs/NOTES_ARE_HINTS.md` |
| Reflexes/danger/gates | `docs/heeding-gates.md`, `dreaming/instincts.py`, `dreaming/codex_reflex_hook.py`, `dreaming/danger_mode.py`, `dreaming/graph_fire/ack_gate.py`, `dreaming/graph_fire/heed_gate.py`; module docstrings outrank stale summaries |
| Graph/capture/learning | `docs/FULL_BRAIN_MAP.md`, `docs/capture-boundaries.md`, `network/DESIGN.md`, `brain_graph/bin/` entry points |
| Dreams/measured improvement | `dreaming/README.md`, `dreaming/run_dream.py`, `docs/BRAIN_SCIENCE_AUDIT.md`, `docs/LITERATURE_MECHANISM_MAP.md` |
| Checklist/owner context | `hooks/README.md`, `dreaming/task_checklist_hook.py`, `dreaming/owner_prompts_hook.py` |
| Compaction/closeout | `docs/COMPACTION.md` sections 3, 6, 9; `dreaming/codex_manual_compact.py`, `dreaming/job_cycle/` |
| Workers/model/outcome learning | `delegation/DELEGATION.md`, `docs/MULTI_AGENT_OPERATIONS.md`, `delegation/verify_model.py`, `delegation/ledger.py`, `delegation/route.py` |
| Claims/handoffs/workspace | `mesh/PROTOCOL.md`, `mesh/README.md`, `workspace/global_workspace/README.md` |
| Compute/body/heavy work | `governor/COMPUTE_DOCTRINE.md`, `docs/UNDER_SATURATION.md`, actual governor/body entry points and target policy |
| Fleet/long-running agents | `docs/AGENTIC.md`, `fleet/` launch/supervision entry points |
| Common-sense hooks | `common_sense/README.md` |
| Health/cache/viewer | `docs/FULL_BRAIN_MAP.md`, `dreaming/check_brain_recall_health.py`, `dreaming/hook_liveness.py`, `viewer/README.md` |
| Cross-machine state/publication | `dreaming/memory_pull.py`, `dreaming/fleet_board.py`, `dreaming/prepush_secret_gate.py`, `dreaming/scan_secrets.py`; Mac guide's shared-ledger cautions |
| Predictions/calibration | `dreaming/predict.py`, `docs/COGNITIVE_ARCHITECTURE.md`; preserve the append-only prediction ledger and independently inspect derived scores |
| Literature/separate paper store | `dreaming/literature_harvest.py`, `dreaming/literature_extract.py`, `dreaming/literature_recall.py`, `dreaming/literature_license.py`, `dreaming/literature_to_drafts.py` |
| Prospective control/shadow experiment | `workspace/prospective_control/README.md`, `DESIGN.md`, `prospective_control.py` and its tests; never auto-integrate a promotion candidate |
| Backup/restore | `resilience/README.md`, `resilience/trust/README.md`, `resilience/restore_drill.py`; operator supplies original cloud identifiers/trust keys |
| Math/film/research | `docs/MATH_BRAIN_SETUP.md`, `mathbrain/README.md`, `filmbrain/README.md`, `methods/proofswarm/README.md`; separate stores/artifacts/evidence |

For bootstrap validation, compare the dry run with the applied files in a separate HOME. Check the selected executable, existing corpus binding, `SessionStart` matcher and deadline, and original lexical index publication before treating a merge as usable. Exercise a nonzero builder and a builder that exits zero without an index. Require the actual query encoder to work when meaning recall is selected. Execute the installed wake command and inspect its explicit alert on a degraded wake. Earlier public bootstrap versions can merge prompt and tool hooks while omitting `SessionStart`. Run `conformance/test_codex_bootstrap.py` when the public checkout supplies it, then prove delivery in a fresh native receiver. The local candidate and the pinned live source have separate revisions.

The `conformance/` suite supplied with the local public materials declares the math brain and a public export manifest. Run it only against that matching math checkout and store, using a separate HOME for test telemetry. Its served, expected-silence and fault cases supply controls for designing main-brain checks. Its score applies to the tested math cases.

## Standing activation

`standing_activation.py enable|status|disable` manages its marked global instruction block, preserving existing bytes and effective override selection. Deployment health is separate. [Codex instruction discovery](https://learn.chatgpt.com/docs/agent-configuration/agents-md) and [hook review/delivery](https://learn.chatgpt.com/docs/hooks) define the host contract. The adapter never fabricates persisted hook trust.

Inspect startup from the receiver with `codex debug prompt-input`, then prove source-backed behavior in a fresh root and authorized worker. Parent handoff remains necessary across profiles, machines and harnesses.

## Delegation and adaptation

Use [handoff.md](handoff.md) for the bounded source/recall/evidence contract. Extend the actual faculty entry point; use `docs/PORTING.md` to distinguish reusable mechanisms from private state and deployment policy. Name user-authorized exceptions. A revision change requires rereading affected sections, rechecking overlays and refreshing receiver evidence.
