# Running the evaluators on a new install

`bootstrap/install.py` copies about ninety scripts named `eval_*.py` and `test_*.py` into
`~/.claude/dreaming`. They are of two kinds. The `test_*` scripts are controls: each builds its
own fixture in a temporary folder, checks a mechanism both ways and prints PASS or OK. The
`eval_*` scripts measure the brain you have: they read its memories, its recall trace and its
logs, and most of them decide whether a proposed change should ship. On a brain that was
installed a minute ago there is little for the second kind to read, so most of them stop or
report that they have nothing yet. That is the expected result, not a broken install.

The table below is from running every script with no arguments on a fresh install (Windows,
Python 3.13, no optional packages), then building the word index once. Run them from
`~/.claude/dreaming`; each one's `--help` prints its purpose and exits without doing anything.

## Controls that pass on a new install

Run these first. A failure here is a real fault in the copy.

```
test_append_journal.py              test_dispatch_decisions.py        test_reflex_commit_after_emit.py
test_attachment_isolation.py        test_exact_name.py                test_reflex_precision_paths.py
test_brain_recall_liveness.py       test_help_flag_is_inert.py        test_scan_secrets_bearer.py
test_codex_manual_compact.py        test_hook_stdin_utf8.py           test_supersession_producers.py
test_codex_opaque_boundary.py       test_judge_reasons.py             test_supersession_qualified_pointer.py
test_codex_reflex_hook.py           test_push_with_resync_lease.py    test_verify_hooks.py
test_codex_sweep_identity.py        test_correction_ingestion_status.py
test_compact_nodes_agent.py         test_coactivation_partition.py    (about four minutes; the others take seconds)
```

Three `eval_*` scripts are controls too, despite their names, and pass on an empty brain:
`eval_retrieval_trace_integrity.py` (the recall trace is built deterministically and fails
closed), `eval_memory_access.py` (reads, searches and surfaced notes are told apart in Claude and
Codex transcripts) and `eval_coactivation_decay.py` (the decay of unused links is one
transaction).

## What each of the others needs

| Needs | Scripts | How to get it |
|---|---|---|
| Nothing; reports that there is no data yet and exits 0 or 2 | `eval_eligibility_credit`, `eval_event_boundaries`, `eval_fok_abstention`, `eval_premise_check`, `eval_premise_follow`, `eval_recall_signals`, `eval_semantic_recall`, `eval_shadow_fires`, `eval_surface_conversion`, `eval_type_utility`, `eval_stale_fact`, `eval_hook_fire_reasons`, `eval_deliberate_recall`, `eval_pre_report_overfire`, `eval_reasoning_guards` | Use the brain for a few weeks and rerun. The last three already print something useful on a new brain. |
| Enough history to calibrate; they say how much is missing and exit 1 | `eval_temporal_context` (20 sessions), `eval_bhat_flag` (seeded queries), `eval_mistake_surfacing` (run `build_mistake_weights.py` first) | As above. |
| The word index, `bm25_index.json.gz` | `eval_bm25first`, `eval_bm25_postings`, `eval_deliberate_spellfix`, `eval_fuzzy_recall` | `python bm25.py refresh` builds it, even with a handful of memories; `brain.py remember` refreshes it afterwards. `eval_bm25first` then runs on six memories. |
| The recall trace, `retrievals.tsv` | `eval_denoise`, `eval_fusion_weights`, `eval_hype`, `eval_keyword_edges`, `eval_linkpred`, `eval_orphan_edges`, `eval_proactive_coeffs`, `eval_prompt_recall`, `eval_prompt_recall_fullstack`, `eval_recall_real_prompts`, `eval_recency_kernel`, `eval_series_edges`, `eval_walk_norm`, `eval_workflow_recall` | `python build_retrieval_trace.py` once there are session transcripts to read. These score recall against what agents actually asked, so they mean little before a few hundred prompts. |
| numpy | `eval_embedding_refresh`, `eval_fusion_live`, `eval_learned_ranker`, `eval_lift_ablation`, `eval_neff_predicts_recall`, `eval_pmi_edges`, `eval_S_predicts_recall`, `eval_S_v2_partial`, `eval_score_rerank`, `eval_semantic_candidates`, `eval_tilt_ablation`, `eval_walk_reach` | `pip install numpy` (the `semantic` extra in `pyproject.toml`). Most of them also need the recall trace. |
| An embedding index | `eval_llm_judge_replay`, and `eval_semantic_recall` for a real answer | Build a sidecar and publish it with `publish_memory_embeddings.py`. |
| The math store and its package | `eval_math_answerable`, `eval_math_selfretrieval`, `eval_math_fresh`, `eval_math_probes` | Set `MATH_BRAIN_SRC` to the package's `src` folder and `MATH_BRAIN_STORE` to a store (the export in this repository builds one). The labelled tiers `eval_math_probes` scores by default are not shipped, so with no arguments it stops on a missing file; run it with `--sets unanswerable` (false fires on questions whose theorem the store lacks) and `--sample 40` for a first look, since each probe takes seconds. |
| Arguments | `eval_math_hookpath`, `eval_math_hookpath_frozen`, `eval_math_hookpath_ident`, `eval_value_in_block` (`--out`); `eval_ownvoice`, `eval_ownvoice_frozen` (`--store`, `--out`) | Read `--help`. They also need the math store. |
| Time-zone data | `eval_memory_age` | On Windows, `pip install tzdata`. |
| Decision gates that refuse on an empty brain | `eval_domain2`, `eval_recency_ship` | They print FAIL or DO NOT SHIP when there is not enough to measure. That is the gate working. |
| A question bank kept private | `eval_locate_reach`, `eval_paraphrase_reach`, `eval_archive_seed_replay` | Not runnable outside the source deployment; read them as worked examples of a bank-driven gate. |
| The source deployment's git history | `test_foresight_dated` | It compares the current foresight code with a dated older version. Pass the old file's path as its argument if you have one. |

## Building your own evaluators

The pattern worth copying is the gate. Each `eval_*` script answers one question about a
proposed change, on the brain's own records, and prints a verdict before anything is switched
on: `eval_recency_ship` keeps the old recency rule unless every gate passes, `eval_domain2`
prints "do not enable", `eval_bm25first` calls a win only when it holds on real prompts as well.
A brain built for another field (a company's documents, a film studio's craft notes) needs its
own bank of questions whose answers it holds, and its own gates; the scripts above show how
to keep the measurement separate from the thing measured, and how to refuse when the data is
too thin.
