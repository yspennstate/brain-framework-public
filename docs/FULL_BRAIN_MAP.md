# The whole brain, part by part

Every working part of the brain is in this repository. This page lists the parts, the files
that make up each one, and the test that checks it. Paths are relative to `framework/`
unless they start with `agent_tools/` or `src/`. How each part works is in
`docs/ARCHITECTURE.md` and `docs/COGNITIVE_ARCHITECTURE.md`; how to run a copy safely
is in `docs/EXERCISING.md`.

## The memory store and recall

| Part | Files | Checked by |
|---|---|---|
| One fact per file, with frontmatter | `docs/MEMORY_CONVENTIONS.md`, `dreaming/brain.py` (`remember`, `recall`, `neighbors`) | `scripts/smoke_framework.py` |
| Lexical recall (BM25) | `dreaming/bm25.py`, `bm25_append.py`, `bm25_refresh_if_stale.py` | `dreaming/bm25_lane_selftest.py`, `dreaming/eval_bm25_postings.py` |
| Semantic recall | `dreaming/build_memory_embeddings_local.py`, `query_embed.py`, `embedding_artifact.py` | `dreaming/eval_semantic_recall.py` |
| Fusion of the lanes | `dreaming/brain.py`, `recall_policy.json` | `dreaming/recall_policy_regression.py`, `dreaming/eval_fusion_weights.py` |
| Salience (what is vivid) | `dreaming/salience.py`, `build_mistake_weights.py` | `dreaming/eval_S_predicts_recall.py` |
| Mistake-first recall | `dreaming/mistake_graph.py`, `dreaming/error_graph/`, `docs/mistake-first-recall.md` | `dreaming/error_graph_gate.py`, `test_mistake_graph_dated.py` |

## The weighted graph

| Part | Files | Checked by |
|---|---|---|
| Graph store and capture of what each session touched | `brain_graph/bin/graph_store.py`, `extract_touch_events.py`, `extract_codex_events.py`, `session_kinds.py` | `brain_graph/bin/test_session_kinds.py`, `test_codex_capture_boundaries.py` |
| Learned edges (weights move with use) | `brain_graph/bin/graph_learn.py`, `live_delta.py`, `dreaming/coactivation_log.py`, `coactivation_end.py` | `brain_graph/bin/test_graph_learn_*.py`, `dreaming/test_coactivation_partition.py` |
| Graph recall and the live sidecar | `brain_graph/bin/graph_recall.py`, `live_sidecar.py`, `live_membrane.py`, `brain_graph/membrane.py` | `brain_graph/bin/test_sidecar_publication.py`, `test_run_graph_lock.py` |
| Built edges (spread, keywords, series, orphans) | `dreaming/build_spread_edges.py`, `build_keyword_edges.py`, `build_series_edges.py`, `build_orphan_edges.py` | `dreaming/eval_keyword_edges.py`, `eval_series_edges.py`, `eval_orphan_edges.py` |
| Keyword topic nodes | `dreaming/build_keyword_nodes.py`, `keyword_corpus_cache.py`, `docs/keyword-topic-nodes.md` | `python dreaming/test_keyword_corpus_cache.py` |
| Spreading recall | `dreaming/spread.py`, `spread_recall.py`, `graph_lane.py` | `dreaming/measure_spread_reach.py` |
| The big association network | `network/` (`build_graph.py`, `fire.py`, `ppr_local.py`, `DESIGN.md`) | `network/test_network.py` |

## Recall speed

| Part | Files | Checked by |
|---|---|---|
| Resident memory-recall server | `dreaming/brain_recall_server.py`, `brain_recall_client.py`, `check_brain_recall_health.py` | `dreaming/test_brain_recall_liveness.py` |
| Resident math-recall server, client and keeper | `dreaming/recall_server.py`, `recall_client.py`, `recall_protocol.py`, `recall_keeper.py`, `server_safety.py` | `dreaming/test_recall_server_lock_posix.py`, `test_math_recall_server_posix.py` (Linux or macOS) |
| Fast path over a cached store state | `dreaming/fastrecall.py`, `fastrecall_delta.py`, `math_recall_prebuild.py` | `dreaming/test_fastrecall_rebuilt_store.py` |
| The levers measured, kept and dropped | `docs/RECALL_LEVERS.md` | |

The math store ships with its own copies of the server, keeper and fast path in
`agent_tools/`, set up to use this checkout's `src/` and `export/` with no configuration.
Run their tests from a clean clone on Linux or macOS:

```
python agent_tools/test_math_recall_server_posix.py
python agent_tools/test_recall_keeper_posix.py
python agent_tools/test_fastrecall_rebuilt_store.py
```

The fast path checks the SHA-256 of `src/math_brain/retrieval.py` and
`agent_tools/query_embed.py` before it trusts its cache. If either file changes, it turns
itself off and `agent_tools/math_recall.py` prints `fast path OFF` with the reason.

## Working while a session runs

| Part | Files | Checked by |
|---|---|---|
| Hook wiring and the dispatcher | `dreaming/hook_dispatch.py`, `hook_dispatch_list.json`, `hooks/settings.hooks.template.json`, `hooks/hooks.codex.template.json` | `dreaming/test_dispatch_decisions.py`, `test_verify_hooks.py`, `test_hook_stdin_utf8.py` |
| Waking up (the SessionStart ritual) | `hooks/wake_brain.py`, `hooks/brain_startup.template.md` | `scripts/smoke_framework.py` |
| Reflexes compiled from mistakes | `dreaming/instincts.py`, `instinct_prime.py`, `codex_reflex_hook.py`, `reflex_precision.py` | `dreaming/test_codex_reflex_hook.py`, `test_reflex_precision_paths.py`, `test_reflex_commit_after_emit.py` |
| Danger sense and command guards (refuses `python -`, a mesh lease on a literal `$name`; reads and notes are not actions) | `dreaming/danger_mode.py` | `dreaming/test_python_dash_guard.py`, `test_mesh_literal_dollar.py` |
| Notes as hints | `docs/NOTES_ARE_HINTS.md` | `dreaming/test_judge_reasons.py` |
| Liveness (a faculty that quietly stops) | `dreaming/hook_liveness.py`, `brain_health.py` | `dreaming/test_brain_recall_liveness.py` |

## Between sessions

| Part | Files | Checked by |
|---|---|---|
| The dream (nightly consolidation) | `dreaming/dream.py`, `dream_prompt*.md`, `run_dream.ps1`, `dream_requests.py` | `dreaming/test_append_journal.py` |
| Compactions kept as nodes | `dreaming/compact_hooks.py`, `compact_nodes.py`, `compact_index.py`, `docs/COMPACTION.md` | `dreaming/test_compact_nodes_agent.py`, `test_codex_manual_compact.py` |
| Closing out a job | `dreaming/job_cycle/` | |

## Many agents at once

| Part | Files | Checked by |
|---|---|---|
| Agent mesh (claims, leases, handoffs) | `mesh/agent_mesh.py`, `claim_gate.py`, `PROTOCOL.md` | `mesh/tests/`, `scripts/smoke_framework.py` |
| Task checklist | `dreaming/checklist.py`, `task_checklist_hook.py` | |
| Secret scan before every push (install as a clone's `.git/hooks/pre-push`) | `dreaming/scan_secrets.py`, `prepush_secret_gate.py` | `dreaming/test_prepush_new_ref.py` |
| Pushing named paths onto a busy main (scanned first, one commit, a lost race retried on the new tip) | `dreaming/push_paths.sh`, `push_paths_gitdata.py` | `dreaming/test_push_paths_gitdata.py` |
| One brain on several computers (fleet inbox, memories copied across, insert-only) | `dreaming/fleet_board.py`, `fleet_inbox_hook.py`, `memory_pull.py` | `dreaming/test_memory_pull.py` |
| Fleet, governor, delegation | `fleet/`, `governor/`, `delegation/` | `python -m pytest governor -q` (from `framework/`) |

## Running everything

From `framework/`:

```
python scripts/preflight.py
python scripts/smoke_framework.py
python -m pytest dreaming mesh governor methods -q
```

Run the directories one by one; a whole-tree `pytest` run fails on two `test_network.py`
files with the same name.

Most of the tests above are plain scripts: `python <path>` from `framework/` runs them on a
fresh clone, with no installed brain and an empty home folder. A few need something first:

- `mesh/tests/`, `governor/`, `network/test_network.py`: `pip install pytest`.
- `brain_graph/bin/test_sidecar_publication.py`: `pip install numpy`.
- `brain_graph/bin/test_codex_capture_boundaries.py`: run it as
  `python brain_graph/bin/test_codex_capture_boundaries.py --implementation brain_graph/bin/extract_codex_events.py --run-dir <new folder>`.
  The folder must not exist yet; every fixture and result stays in it.
- `dreaming/bm25_lane_selftest.py`: needs a built index over a memory folder
  (`python dreaming/bm25.py build`). It refreshes the live index it finds, so run it on an
  installed brain while no other agent is writing memories.
- `dreaming/test_recall_server_lock_posix.py`, `test_math_recall_server_posix.py` and the
  three `agent_tools/` tests above run on Linux or macOS. `docs/RUNNING_THE_EVALUATORS.md` says which `eval_*` scripts pass
on a new install and what each of the others needs first.
