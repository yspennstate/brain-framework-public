# 1000 ways to improve the brain

A catalog of concrete, buildable improvements to the memory-and-cognition system in
`~/.claude/` (the ~1391 memories + ~40 faculties). Generated 2026-07-08 by ten
neuroscience-domain scouts, each briefed on the real system and asked for ~100 distinct,
grounded, buildable levers. Every idea names a real file/faculty or a concrete new sidecar,
cites the neuroscience it rests on, and tags effort (S/M/L) and how it's *measurable*.

This is a menu, not a mandate. The hard-won method (`method_improving_the_brain_well_2026_07_08`)
is blunt that **over-building is the twin of padding**: measure every edge against ground
truth, ship only what survives a break-test, reject honestly what doesn't earn its place,
and remember that **integration (wiring the faculties into one loop) beats a ninth faculty**.
So the value here isn't the 1000 — it's the triage on top of them, and the discipline of
building a few well and measuring each. The 1000 are the backlog the dream and future
sessions work down, one measured edge at a time.

Constraints every idea respects: the laptop is crash-prone with no GPU (heavy compute /
model inference runs offline on the Azure box — flagged *(box)*); no flashing terminal
windows on Windows; the corpus is append-only (fade/demote, never delete); the association
graph is non-stationary (mature it, don't snapshot-tune it).

---

## How to build from this catalog (lessons from testing ~15 of these, 2026-07-08)

Read this before picking an idea — it's the hard-won method that keeps the next session from re-walking
dead ends. Of ~15 hypotheses tested this session, 2 shipped as measured wins and ~13 were refuted,
redundant, or data-gated. That ratio is the finding: **on a mature brain the obvious improvement usually
does not survive ground truth.** So:
1. **Test the ACTUAL USE on the REAL TARGET, with HARD negatives — never a proxy or a component in
   isolation.** The recall embedding aced a hand-picked pair-eval (0.87), read weak as a co-read ranker
   *alone* (~0.63 — which nearly made me pull it), and then proved a robust +0.14 win *as spread_recall
   actually uses it* (graph×embedding) against hard graph-candidate negatives. Three different tests, three
   different answers; only the last one — actual-use, real behaviour, hard negatives — was decisive.
2. **A fast pattern cannot make a content call.** Regex/keyword matching to classify content (is X a null?
   related? a bug?) false-positives on vocabulary (measured ~50% on a NULLS-ledger attempt). Use a judgment
   pass (the dream, a model), not a match.
3. **Don't snapshot-tune the non-stationary graph.** Build the stable held-out eval first (this session
   left `eval_recall_signals.py`, `eval_semantic_recall.py --behavioral`, `base_activation.py --eval`,
   `ern_reliability.py` — all wired into the nightly dream to mature); tune only when the data is old enough.
4. **The binding constraint now is data-maturity and the box model, not missing mechanisms.** Most faculties
   are well-designed; several "obvious" additions were already built (SHY-decay, focus-weighting, decay).
   Read the file before rebuilding it.
5. **Integration/measurement beats new faculties, and the honest "no" is real work.** Rejecting a
   measured-marginal idea with a note is worth more than shipping it to look busy.

## The prioritized shortlist (the value-add)

Ranked by cross-scout consensus × value × (measurable, laptop-safe). Status: **DONE** this
session · **NEXT** (building now) · **ROADMAP** (high-value, not yet built).

> **Measured-status update (2026-07-08, after implementing):** the honest results changed the ranking -
> read this before trusting the raw list below. **DONE + verified:** #2 ERN→prediction loop
> (`ern_reliability.py`), #8 AUC recall eval, #9 hook-liveness observer, plus `build_retrieval_trace.py`
> (the retrieval-history substrate). **#1 semantic recall — LIVE, adversarially validated** (after a
> ship→pull→re-ship arc): the LSA sidecar aced the pair-eval (0.87) but is weak as a co-read ranker ALONE
> (~0.63) — which briefly made me pull it. The decisive test is causal + as-used (`eval_recall_signals.py`):
> spread_recall's graph×embedding blend predicts FUTURE co-reads at ~0.79 vs the graph's ~0.68, and among
> hard graph-candidate negatives the graph alone is ~0.50 (useless) while the blend is ~0.65 — a +0.11–0.14
> lift, robust across 15 split/seed runs. The embedding IS the discriminative signal among candidates, so
> it's re-shipped and live. Box MiniLM = the STRONG upgrade; its gate is `eval_recall_signals.py` (causal),
> NOT the component-alone `--behavioral` diagnostic. **REFUTED by
> measurement - do NOT build:** #3 unify the accessibility signals (they measure different axes -
> vividness vs usage vs query-match - so unifying is a category error), #5 SHY-decay (already built into
> `coactivation_end.py`), and the graph-augmented-embedding idea (launders the graph). **Doesn't yet
> earn it:** a base-activation usage signal (built the eval, it doesn't beat salience/recency on the
> current trace). **The one clearly-open lever:** run the box MiniLM embeddings - the only proposal that
> adds recall signal independent of the association graph. Detail + the reasoning:
> `brain_1000_ways_and_two_builds_2026_07_08`.
>
> **Second update (2026-07-09, overnight):** the lever got pulled - bge-small-en-v1.5 won the box
> bake-off on every gate (pair AUC 1.000; causal blend gap +0.113 over graph across 15 runs) and is
> LIVE, with the re-rank now self-calibrating to any sidecar's cosine scale. Also measured that night:
> cross-encoder re-rank REJECTED (flattering on pairs, nothing on behavior); semantic graph EDGES
> NOT SHIPPED (eval_walk_reach.py: marginal, thin sample - re-run as the trace grows); the dream agenda
> gained ~90%-precise semantic association candidates; #7 created:-stamp now written by brain.py
> remember (read-side plumbing still open); #12 partially done via the wake size budget (the wake was
> overflowing the harness inline limit and arriving as a file pointer). First LLM-judge replay run
> (eval_llm_judge_replay.py) says the bge damp interacts with the LSA-era MARGIN gate (fewer fires);
> do NOT retune on n=7 - accumulate judged runs. Detail: `recall_stack_bge_shipped_2026_07_09`.

### Integration — the connective tissue (my method's #1 axis: wire the faculties into one loop)
1. **Semantic-recall lift** — a real semantic signal in `spread_recall`'s re-rank, not keyword-only. *Consensus: scouts 1,3,10.* **DONE**: built `build_memory_embeddings_local.py` (model-free LSA, laptop-safe, 18s, no GPU), upgraded `eval_semantic_recall.py` to an AUC verdict on a widened 13+13 held-out pair set; measured **AUC 0.87 (MODERATE)**, wired live as a gentle reorder-only re-rank, smoke-tested fail-safe. The box sentence model (`build_memory_embeddings.py`) is the documented path to STRONG.
2. **Close the ERN→prediction→calibration loop** — `error_monitor` logs felt-errors but nothing writes a prediction outcome, so calibration never learns from real failures. Wire the felt-error into `predict.py` as a resolved "no-error-expected, got error" bet. *Consensus: scouts 4,6,10 — the flagship gap.* **NEXT.** Laptop-safe; measurable (calibration's coverage of real failures; post-error error rate).
3. **Unify the three accessibility signals** — `fok_probe` (pointer count), `salience.py` (recency R), `consciousness.py` (tier) each estimate "how reachable is this memory" with a different number. Extract one `accessibility.py` they all read. *Consensus: scouts 1,2,6,10.* **ROADMAP** (effort M–L). Measurable: does the unified score predict the next co-read (AUROC) better than the three separate estimates.
4. **Surprise→salience, done right** — `salience.py` left this edge deliberately unbuilt after a lexical version proved noisy; wire it to a *resolved high-bits prediction* whose origin session matches the memory. *Consensus: scouts 4,6.* Measurable: later co-read rate of surprise-tagged vs matched controls.

### Maturation — non-stationary-safe (mature the graph, don't tune it)
5. **SHY nightly decay + prune on `coactivation.tsv`** — the Hebbian weights grow unbounded; a nightly multiplicative downscale + prune-below-floor keeps only re-reactivated edges, improving SNR. *Consensus: scouts 3,5.* **NEXT.** Laptop-safe; measurable (edge count + spread precision on the held-out eval).
6. **Focus-weight / PMI on co-reads** — discount a pair co-read only in a big survey session vs one co-read in a tight 3-memory session (already partly done; extend with PMI). *Consensus: scouts 1,2.* Measurable: precision of Hebbian-derived fires.
7. **Immutable `created:` timestamp** — repo-sync resets mtime, so `salience.py`/`reconsolidate.py` mis-age memories. Stamp an unchangeable creation date at write. *Scout 1 (#32).* Foundational, laptop-safe; measurable (age accuracy vs filename dates).

### Measurement — build the stable eval before tuning (my method's core discipline)
8. **AUC recall eval on a frozen held-out set** — **DONE** (upgraded `eval_semantic_recall.py`: threshold-free AUC verdict, widened basis, `--pairs` diagnostics).
9. **Hook-liveness / data-flow audit** — each hook stamps `.X_fired.json`; a daily check confirms each fired, catching a silently-dead hook. *Scout 10.* **NEXT** (cheap, high-leverage observer). Measurable (fired-in-24h per hook).
10. **Per-reflex precision + auto-demote cry-wolf reflexes** — score each instinct fired-vs-actually-dangerous; demote chronic false-alarmers. *Consensus: scouts 6,10.* Measurable (per-reflex precision).
11. **Faculty ablation harness** — disable one hook at a time, re-run the evals, measure each faculty's marginal contribution (finds the ones that don't earn their place). *Scout 10.* Measurable (eval delta per faculty).

### Attention budget — the wake spotlight is bloated (Cowan ~4)
12. **Cap/prune the wake spotlight** — the ~15-block wake ritual overloads working memory; gate soft blocks on salience/freshness, cap to a few chunks. *Scout 5.* Measurable (injected token count; ignored-injection rate). Touches `wake_brain.ps1` (ASCII-careful — a non-ASCII char there once broke every wake).
13. **Unified "recently attended" dedup across the 5 prompt hooks** — one shared store so a topic isn't surfaced by three hooks at once. *Scout 5.* Measurable (same-topic multi-hook co-fire rate).

### Prediction sharpening — self-scoring by construction
14. **Reliability diagram / binned ECE per scope** — replace `predict.py`'s single mean-gap with a binned calibration curve. *Consensus: scouts 4,6.* Measurable (ECE). Laptop-safe.
15. **Due-date auto-resolver** — a dream step resolves bets past their `by:` date against memories written since. *Scout 4 (#20).* Measurable (fraction of due bets resolved).

### Genuinely new territory (high value for a shared brain)
16. **Transactive expertise index** — who-knows-what across the society of minds (which sibling owns rmt/leap/fno/kappa/genetics), from `originSessionId` + area keywords. *Scout 8.* Measurable (predict a held-out memory's origin-area from its content).

*(The full 1000 follow, in the ten scout domains. Numbering is per-domain, 1–100. Look here
for the backlog; build from the shortlist above, and measure every edge before believing it.)*

## Domain 1 — Memory encoding, consolidation & forgetting

### (1A) Memory encoding & consolidation
1. **Retrieval-practice scheduler** — `retrieval_practice.py` turns a due memory's description into a cloze/question at wake, logging whether the session answered before revealing. *Basis:* testing effect (Roediger-Karpicke). *Touches:* new faculty + wake, reactivation.py. *Effort:* M. *Measurable:* retrieval accuracy tested vs untested (holdout).
2. **Expanding-interval rehearsal** — `rehearsal.tsv` gives each important memory an expanding review interval (1/3/7/21/45d); reactivation.py surfaces on due, resets on co-read. *Basis:* spacing / expanding retrieval (Landauer-Bjork). *Touches:* reactivation.py. *Effort:* M. *Measurable:* co-read survival, expanding vs flat age>21d.
3. **Prediction-error-gated encoding boost** — salience.py raises initial charge if a memory's originSessionId matches a high-surprise resolved bet. *Basis:* PE-gated encoding (Rouhani-Niv). *Touches:* salience.py, predict.py. *Effort:* M. *Measurable:* PE-tagged memories' later co-read frequency.
4. **Encoding-depth score** — compute `metadata.depth` from structural signals (links, a why/mechanism clause, numbers+units, a named basis). *Basis:* levels of processing (Craik-Lockhart). *Touches:* new encode_depth.py, frontmatter. *Effort:* M. *Measurable:* does depth predict co-read survival.
5. **Generation over copy in the dream** — SWS must re-derive a consolidated claim in its own words, not paste. *Basis:* generation effect (Slamecka-Graf). *Touches:* dream prompts. *Effort:* S. *Measurable:* hard (paraphrase-overlap on the journal).
6. **Schema-congruence fast-track** — schema_extract flags a new memory matching an existing schema; congruent gets a consolidation boost, incongruent-but-important a new-schema flag. *Basis:* schema-gated fast learning (Tse 2007). *Touches:* schema_extract.py, salience.py. *Effort:* M. *Measurable:* link-formation rate congruent vs incongruent.
7. **Gist/detail split at consolidation** — SWS distills a one-line gist; salience lets the body fade faster while the gist persists. *Basis:* sleep preserves gist (Payne-Stickgold). *Touches:* dream.py, salience.py. *Effort:* M. *Measurable:* gist retention vs detail access over time.
8. **Explicit gain×need replay priority** — dream.py writes replay_priority.tsv (need = salience×day-relevance, gain = contradiction/uncertainty). *Basis:* prioritized replay (Mattar-Daw). *Touches:* dream.py. *Effort:* M. *Measurable:* links formed per dream, prioritized vs random.
9. **Interleaved old+new replay** — dream.py §A pairs each recent memory with K older ones from the same cluster. *Basis:* interleaving vs catastrophic interference (McClelland-O'Reilly). *Touches:* dream.py. *Effort:* S. *Measurable:* do old topic-memories get revised when new ones arrive.
10. **Systems-consolidation promotion** — a memory reactivated N times and schema-congruent is promoted from dated episode to a reference/schema; the episode may fade. *Basis:* systems consolidation (Squire-Alvarez). *Touches:* reactivation.py, schema_extract.py, dream.py. *Effort:* L. *Measurable:* promotion count; promoted-knowledge survival.
11. **Encoding-specificity cue capture** — resume_checkpoint records task/files-touched tokens into a `cues:` line so recall can seed on encoding context. *Basis:* encoding specificity (Tulving-Thomson). *Touches:* resume_checkpoint.py, spread_recall.py. *Effort:* M. *Measurable:* cue-augmented seeding precision on the recall eval.
12. **Subsequent-memory (Dm) diagnostic** — dm_report.py flags memories never re-read in their first weeks (encoding that didn't take). *Basis:* subsequent-memory effect (Paller-Wagner). *Touches:* coactivation logs, encode_depth. *Effort:* M. *Measurable:* re-read rate is the metric.
13. **FOK metamemory resolution loop** — log each fok_probe fire, then check whether the session actually read a named memory; compute FOK precision. *Basis:* metamemory accuracy (Koriat). *Touches:* fok_probe.py + resolution log. *Effort:* M. *Measurable:* flagged-and-used vs flagged-and-ignored.
14. **Distinctiveness (isolation) boost** — salience adds a term for a memory whose token profile is far from the corpus centroid. *Basis:* von Restorff isolation. *Touches:* salience.py. *Effort:* M. *Measurable:* distinctiveness vs co-reads.
15. **Elaborative interrogation in the dream** — dream §A answers "why is this true / why did this happen" and appends the elaboration. *Basis:* elaborative interrogation (Pressley). *Touches:* dream prompts. *Effort:* S. *Measurable:* hard.
16. **Spindle-gated consolidation quota** — dream deep-files only the top-K priority memories per night. *Basis:* spindles gate consolidation (Mölle-Born). *Touches:* dream.py. *Effort:* S. *Measurable:* links formed per memory vs unbounded.
17. **Dual-coding semantic-kin sidecar** — precompute each memory's top semantic neighbors into semantic_kin.tsv as a second recall code. *Basis:* dual coding (Paivio). *Touches:* build_memory_embeddings.py *(box)*, spread_recall.py. *Effort:* M. *Measurable:* recall precision with/without kin.
18. **Autobiographical narrative consolidation** — a monthly dream distills recurring charged wins/scars into AUTOBIOGRAPHY.md. *Basis:* autobiographical/self-schema (Conway). *Touches:* AUTOBIOGRAPHY.md, dream.py. *Effort:* M. *Measurable:* hard.
19. **Self-reference encoding tag** — extend salience STAKES lexicon with an identity/self lexicon for a self-reference persistence bump. *Basis:* self-reference effect (Rogers). *Touches:* salience.py. *Effort:* S. *Measurable:* self-ref tag vs retrieval.
20. **Chunk near-duplicate logs into a digest** — the dream folds a week of watchdog substrate into one digest, then fades the members. *Basis:* chunking (Miller). *Touches:* dream.py, compact_index.py. *Effort:* M. *Measurable:* corpus-size reduction + digest retrieval.
21. **Retrieval-strengthening on Read** — feed coactivation read-strength into salience as a persistence term. *Basis:* retrieval practice (Roediger-Karpicke). *Touches:* salience.py. *Effort:* M. *Measurable:* use/retention of read-boosted vs owner-relevance (guard circularity).
22. **Encoding variability (alt description)** — the dream adds an alternate-angle description to a heavily-reactivated memory. *Basis:* encoding variability (Bower). *Touches:* dream.py, frontmatter. *Effort:* M. *Measurable:* retrieval hit-rate from varied cues.
23. **Desirable-difficulty wake surfacing** — the wake sometimes poses a top memory as a question, revealing on failure. *Basis:* desirable difficulties (Bjork). *Touches:* wake hook. *Effort:* M. *Measurable:* retention A/B.
24. **Reactivation-window edit constraint** — reconsolidate edits a belief only in the same pass where it was replayed. *Basis:* reconsolidation window (Nader). *Touches:* reconsolidate.py, dream ordering. *Effort:* S. *Measurable:* edits-within-window compliance.
25. **Prediction-generation at encoding** — a memory recording a plan/decision appends an explicit forecast to PREDICTIONS.md. *Basis:* generation + pretesting (Potts-Shanks). *Touches:* predict.py, write convention. *Effort:* S. *Measurable:* growth in resolved bets.
26. **Amygdala-tagged consolidation priority** — dream forms ≥1 new link per charged (scar/win) memory it replays. *Basis:* emotional memory enhancement (McGaugh). *Touches:* dream.py. *Effort:* S. *Measurable:* link-formation rate, charged vs flat.
27. **REM relational abstraction** — dream_prompt_rem forces a relational integration across the replayed set. *Basis:* overnight relational inference (Ellenbogen). *Touches:* dream prompts. *Effort:* S. *Measurable:* inference/gist links formed.
28. **Declarative vs procedural node_type** — add `node_type: procedure`; consolidate these by repetition-count not elaboration. *Basis:* declarative/procedural dissociation (Cohen-Squire). *Touches:* frontmatter, salience.py. *Effort:* M. *Measurable:* method-memory reuse count.
29. **Behavioral-tagging time window** — widen salience `c_capture` from same-session to ~24h. *Basis:* behavioral tagging (Moncada-Viola). *Touches:* salience.py. *Effort:* M. *Measurable:* does capture predict the weak memory's retention.
30. **Index-theory backlink repair** — link_audit inserts the reciprocal `[[link]]` so every pointed-to memory points back. *Basis:* hippocampal index theory (Teyler-DiScenna). *Touches:* link_audit.py, dream.py. *Effort:* M. *Measurable:* bidirectional-link coverage %.
31. **SM-2 spaced repetition for reflexes** — schedule scar/instinct review into the dream on an SM-2 interval. *Basis:* spaced repetition (SM-2; Ebbinghaus). *Touches:* reactivation.py, instincts.tsv. *Effort:* M. *Measurable:* instinct coverage trend.
32. **Immutable `created:` field** — write an unchangeable creation timestamp so salience/reconsolidate stop mis-aging when repo-sync resets mtime. *Basis:* temporal context (Howard-Kahana). *Touches:* frontmatter, salience.py. *Effort:* S. *Measurable:* age accuracy vs ground-truth dates.
33. **`last_reactivated` timestamp** — coactivation_end stamps each co-read memory's last-reactivation; recall prefers recently-reactivated. *Basis:* temporal context drift. *Touches:* coactivation_end.py, rehearsal.tsv. *Effort:* M. *Measurable:* recall precision.
34. **Consolidation drains the reconsolidation queue** — SWS must run reconsolidate routes and execute recommendations, not let the queue grow. *Basis:* PE-gated reconsolidation (Sevenster-Kindt). *Touches:* dream.py, reconsolidate.py. *Effort:* S. *Measurable:* queue drain rate.
35. **Semantic topic clustering (neocortical map)** — cluster memory_embeddings into topics and regenerate a semantics-grounded TOPICS. *Basis:* neocortical semantic organization / CLS. *Touches:* build_memory_embeddings.py *(box)*, TOPICS gen. *Effort:* M. *Measurable:* cluster purity vs human labels.
36. **Remote/recent consolidation-state tag** — classify each memory recent vs remote from age+reactivation; remote resists reconsolidation edits. *Basis:* standard consolidation model. *Touches:* reconsolidate.py. *Effort:* S. *Measurable:* edit-resistance correctness.
37. **Cue-overload title linter** — flag a generic filename colliding with many memories and suggest a distinctive one. *Basis:* cue-overload (Watkins). *Touches:* link_audit / write linter. *Effort:* S. *Measurable:* generic-title collision count (there's a real scar on this).
38. **Distributed-practice wake rotation** — vary which slice of SALIENCE.md the wake surfaces across sessions. *Basis:* distributed practice (Cepeda). *Touches:* wake, SALIENCE consumption. *Effort:* S. *Measurable:* surfaced-memory coverage breadth.
39. **Deep-processing gate for curation** — compact_index curates only memories above an encode-depth threshold into MEMORY.md; shallow stay in INDEX_ARCHIVE. *Basis:* levels of processing + curation. *Touches:* compact_index.py, encode_depth. *Effort:* M. *Measurable:* do curated memories get reused more.
40. **Goal-relevant replay prioritization** — dream upweights replay matching owner_work_program / DREAM_FOCUS. *Basis:* motivational salience of goal-relevant memory. *Touches:* dream.py. *Effort:* S. *Measurable:* fraction of replay matching active goals.
41. **Structured SWS→REM handoff** — SWS writes CONSOLIDATION_HANDOFF.md (filed/unresolved) that REM reads. *Basis:* sequential hypothesis (Giuditta; Diekelmann-Born). *Touches:* dream.py, prompts. *Effort:* M. *Measurable:* link continuity SWS→REM.
42. **Novelty-gated replay quota** — replays of a memory scale with its unresolved surprise. *Basis:* hippocampal-VTA novelty loop (Lisman-Grace). *Touches:* dream.py. *Effort:* M. *Measurable:* high-surprise memories get more links.
43. **Source-at-write provenance** — sourced_memory ensures every new memory records a source (owner-said/sim/inferred) so consolidation weights owner-sourced higher. *Basis:* source-monitoring (Johnson). *Touches:* sourced_memory.py, frontmatter. *Effort:* S. *Measurable:* source-coverage %.
44. **Wins → reusable priors** — schema_extract distills consolidated wins into an explicit `priors:` line the FOK/instinct layer can cite. *Basis:* schema-to-prior (Bartlett). *Touches:* schema_extract.py, instincts.tsv. *Effort:* M. *Measurable:* prior reuse count.
45. **Spreading-rehearsal traverse-and-confirm** — the dream walks spread_edges from a seed, confirms/denies each 2-hop association, co-reads confirmed (strengthen), flags false. *Basis:* retrieval practice + Hebbian use-it-or-lose-it. *Touches:* dream.py, coactivation.tsv. *Effort:* M. *Measurable:* spread_recall precision on a stable held-out eval.
46. **Cloze quiz bank for high-value memories** — generate cloze deletions (blank the load-bearing number) for scars and hard rules. *Basis:* testing effect / cued recall. *Touches:* new quiz_bank.tsv, wake. *Effort:* M. *Measurable:* quiz accuracy trend.
47. **Numeric FACTS extraction** — facts_extract.py pulls (entity, value, unit, date) into facts.tsv, a structured semantic store to query and diff. *Basis:* semantic/gist memory. *Touches:* new faculty, dream.py. *Effort:* L. *Measurable:* fact-lookup precision; contradiction-detection rate.
48. **Effort-based encoding strength** — work_timer session effort feeds salience: a memory written after a long hard session encodes stronger. *Basis:* effortful/desirable-difficulty processing. *Touches:* work_timer.py, salience.py. *Effort:* M. *Measurable:* session effort vs retention.
49. **Fan-aware link placement** — when the dream forms a link, a degree check steers it to a specific low-fan target, not a hub. *Basis:* fan effect (Anderson). *Touches:* dream.py, spread_edges.tsv. *Effort:* M. *Measurable:* spread precision after additions.
50. **Consolidation-quality logging** — dream records per night (links formed, contradictions resolved, gists written) into STATS. *Basis:* consolidation as active reorganization (Dudai). *Touches:* dream.py, STATS. *Effort:* S. *Measurable:* the metric over time.

### (1B) Forgetting, interference & pruning
51. **Nightly SHY downscale + prune** — a dream step subtractively downscales all coactivation weights and prunes below floor, independent of use. *Basis:* synaptic homeostasis SHY (Tononi-Cirelli). *Touches:* coactivation.tsv, dream.py. *Effort:* S. *Measurable:* edge-count reduction + spread precision (held-out).
52. **Retrieval-induced forgetting of competitors** — when spread_recall strongly selects a winner, slightly suppress co-cued losers in a suppression sidecar (applied slowly). *Basis:* RIF (Anderson-Bjork). *Touches:* spread_recall.py. *Effort:* M. *Measurable:* subsequent recall precision for winners.
53. **Unified retrieval-strength signal** — split each memory into storage strength (monotone) and retrieval strength (fades with disuse) in one accessibility.tsv that fok_probe, salience-recency, consciousness all consume. *Basis:* New Theory of Disuse (Bjork-Bjork); ACT-R base-level. *Touches:* new accessibility.py + three faculties. *Effort:* L. *Measurable:* does the shared signal predict re-reads better than three separate.
54. **Directed-forgetting fade tag** — a `fade: reason` frontmatter tag salience honors with a strong demotion (never deletion). *Basis:* directed forgetting (Bjork; Anderson-Green). *Touches:* salience.py, frontmatter. *Effort:* S. *Measurable:* faded memory stops surfacing yet stays grep-findable.
55. **Near-duplicate interference merge** — interference_scan.py finds very-high-similarity clusters and surfaces them for the dream to merge into one canonical + fade the rest. *Basis:* retroactive interference; list-strength. *Touches:* new faculty *(box)*, dream.py. *Effort:* M. *Measurable:* redundancy reduction with no held-out retrieval loss.
56. **List-strength cap on curation** — compact_index caps curated memories per topic cluster. *Basis:* list-strength effect (Ratcliff-Clark-Shiffrin). *Touches:* compact_index.py. *Effort:* M. *Measurable:* retrieval precision per topic vs index density.
57. **Fan-effect link pruning** — identify high-degree hubs in spread_edges and retarget/prune their weakest links. *Basis:* fan effect (Anderson). *Touches:* link_audit.py, spread_edges.tsv. *Effort:* M. *Measurable:* spread precision/latency after pruning.
58. **Transience score (salience's mirror)** — transience.py computes should-this-fade = low value × high redundancy × age × no-reactivation. *Basis:* adaptive forgetting / transience (Richards-Frankland). *Touches:* new faculty. *Effort:* M. *Measurable:* fading high-transience causes no held-out retrieval loss.
59. **Proactive-interference guard on write** — a new memory strongly overlapping an existing one prompts UPDATE rather than a competing duplicate. *Basis:* proactive interference; memory updating. *Touches:* new hook, reconsolidate.py. *Effort:* M. *Measurable:* duplicate-creation rate.
60. **Catastrophic-interference firewall for instincts** — ern_learn checks a candidate reflex against instincts_learned.json so a new instinct interleaves, not clobbers. *Basis:* catastrophic interference (McCloskey-Cohen). *Touches:* ern_learn.py, instincts.tsv. *Effort:* S. *Measurable:* instinct-contradiction count.
61. **Active forgetting of transient from the graph** — build_spread_edges excludes `type: transient` older than N days. *Basis:* active forgetting (Davis-Zhong). *Touches:* build_spread_edges.py, tag_transient.py. *Effort:* S. *Measurable:* graph-noise reduction.
62. **Forgetting-curve fit + relearn** — fit an Ebbinghaus retention curve per memory from co-read history; surface just before predicted forgetting. *Basis:* forgetting curve + spaced relearning. *Touches:* reactivation.py, rehearsal.tsv. *Effort:* M. *Measurable:* accuracy of the "about to be forgotten" prediction.
63. **Retroactive-interference cascade** — when a memory is superseded, scan for OTHER memories still asserting the old value and queue them. *Basis:* retroactive interference. *Touches:* dream.py, growth.py. *Effort:* M. *Measurable:* stale-assertion residue count.
64. **Homeostatic salience renormalization** — salience outputs rank/z-normalized scores nightly so "vivid" stays a relative top slice. *Basis:* synaptic homeostasis / gain control. *Touches:* salience.py. *Effort:* S. *Measurable:* vivid-set size stability over time.
65. **Competition lowers FOK confidence** — fok_probe reports LOWER confidence when very many memories compete (cue overload). *Basis:* cue-overload (Watkins). *Touches:* fok_probe.py. *Effort:* S. *Measurable:* FOK precision when the cluster is large.
66. **Part-list cuing rotation** — rotate which cluster members the wake surfaces so no member is chronically suppressed. *Basis:* part-list cuing (Slamecka). *Touches:* wake, SALIENCE consumption. *Effort:* S. *Measurable:* coverage rotation.
67. **Collaborative-inhibition stagger** — give each fleet agent a different wake slice so the shared corpus isn't retrieved in one fixed pattern. *Basis:* collaborative inhibition (Weldon-Bellinger). *Touches:* wake, THE_OTHER_MINDS. *Effort:* M. *Measurable:* corpus coverage across agents.
68. **Decay unfulfilled dangling demand** — dangling links never fulfilled after N dreams stop being requested. *Basis:* intention forgetting / Zeigarnik resolution. *Touches:* dream.py. *Effort:* S. *Measurable:* dangling-request churn reduction.
69. **Redundancy-weighted fade** — a low-salience memory with many high-similarity neighbors fades faster (neighbors carry the info). *Basis:* RIF + redundancy. *Touches:* salience.py *(box)*. *Effort:* M. *Measurable:* no held-out retrieval loss after fading.
70. **Prune one-shot survey coactivation** — drop edges seen in exactly one high-fan-out survey session and never re-corroborated. *Basis:* single-trial noise; use-it-or-lose-it. *Touches:* coactivation_end.py / a pruner. *Effort:* M. *Measurable:* spread precision.
71. **Owner "drop this" forget cue** — a UserPromptSubmit pattern ("forget/ignore/drop X") writes a fade tag for the topic. *Basis:* directed forgetting, list method. *Touches:* new hook, salience.py. *Effort:* M. *Measurable:* does the topic stop surfacing.
72. **Instinct disuse decay** — a reflex that never fires for N days loses retrieval strength (stops injecting) but stays stored. *Basis:* disuse (Bjork; Wixted). *Touches:* instinct_prime.py, instincts.tsv. *Effort:* M. *Measurable:* instinct fire precision.
73. **Homeostatic conscious-tier budget** — consciousness enforces a top-N budget, demoting the weakest as new arrive. *Basis:* SHY renormalization / capacity limits. *Touches:* consciousness.py. *Effort:* M. *Measurable:* conscious-tier size stability + wake load.
74. **Contradictory-fact interference audit** — using facts.tsv, flag pairs asserting different values for the same (entity, unit); newest wins, older fades. *Basis:* memory updating / retroactive interference. *Touches:* facts_extract.py, dream.py. *Effort:* M. *Measurable:* contradiction count → 0.
75. **Anti-cramming spacing across nights** — dream dedups replay across consecutive nights. *Basis:* spacing / massed-practice deficit. *Touches:* dream.py. *Effort:* S. *Measurable:* replay diversity across nights.
76. **Value-gated orphan cold-store** — old, orphan, low-salience project stubs archived into a cold store excluded from graph/index (kept on disk). *Basis:* value of forgetting / transience. *Touches:* new cold-store, build_spread_edges.py, compact_index.py. *Effort:* M. *Measurable:* active-corpus size vs held-out retrieval loss.
77. **RIF for competing scars** — when a scar is reactivated, suppress its weaker near-synonym scars so one canonical wins. *Basis:* RIF. *Touches:* reactivation.py. *Effort:* M. *Measurable:* scar-dedup count.
78. **Evidence-weighted coactivation decay** — slower decay for edges corroborated across many sessions, faster for one-offs. *Basis:* use-dependent stabilization. *Touches:* coactivation_end.py. *Effort:* M. *Measurable:* edge survival vs cross-session support.
79. **Forget stale resolved predictions** — predict.py moves resolved bets older than N months to a cold ledger so calibration reflects the current regime. *Basis:* recency-weighted calibration. *Touches:* predict.py, PREDICTIONS.md. *Effort:* S. *Measurable:* calibration stability across regime shifts.
80. **Merge-then-fade in reconsolidation** — UPDATE-IN-PLACE first merges the loser's unique content into the winner, then fades the loser. *Basis:* reconsolidation as integration. *Touches:* reconsolidate.py, dream.py. *Effort:* M. *Measurable:* info-preservation check.
81. **Power-law forgetting curve** — replace salience's single exponential with a two-component power-law (fast-then-slow). *Basis:* power law of forgetting (Wixted-Ebbesen). *Touches:* salience.py. *Effort:* S. *Measurable:* fit to the actual re-read decay curve.
82. **Jost's-law age-stability term** — between two equal-salience memories, decay the older slower. *Basis:* Jost's law. *Touches:* salience.py. *Effort:* S. *Measurable:* differential-decay correctness on matched pairs.
83. **Retrieval-competition differentiation** — log cues where two memories repeatedly compete; the dream adds distinguishing cues. *Basis:* memory differentiation (Hulbert-Norman). *Touches:* spread_recall log, dream.py. *Effort:* M. *Measurable:* competition-frequency reduction.
84. **Prospective-forgetting: close resolved questions** — an OPEN_QUESTIONS groomer closes and fades answered questions. *Basis:* Zeigarnik completion + intention offloading. *Touches:* incubate.py, OPEN_QUESTIONS.md. *Effort:* S. *Measurable:* open-question staleness reduction.
85. **Sleep weak-trace elimination** — the dream identifies the week's weakest-encoded memories (low depth, no links, no reads) and down-tiers them. *Basis:* sleep-dependent forgetting of weak traces (SHY; Poe). *Touches:* dream.py, consciousness.py. *Effort:* M. *Measurable:* pruning volume + no held-out retrieval loss.
86. **Temporal spacing of similar consolidations** — two similar memories arriving close in time are consolidated on different nights. *Basis:* temporal spacing reduces interference. *Touches:* dream.py scheduling. *Effort:* S. *Measurable:* retention of both.
87. **Inhibition-of-return at wake** — a memory just surfaced at wake is briefly barred from re-surfacing next session. *Basis:* inhibition of return (Posner-Cohen). *Touches:* wake + a small IOR state file. *Effort:* S. *Measurable:* surfacing breadth.
88. **Living-doc line decay** — a groomer fades lines in STATE_OF_MIND/living docs not touched in N sessions. *Basis:* transience of working state. *Touches:* a living-doc groomer. *Effort:* M. *Measurable:* doc-freshness metric.
89. **Embedding max-cosine interference log** — compute each memory's max cosine to any other; very-high pairs are pattern-separation failures logged for merge. *Basis:* pattern-separation failure (Yassa-Stark). *Touches:* build_memory_embeddings.py *(box)*, interference_scan. *Effort:* M. *Measurable:* dedup precision.
90. **Pattern separation at encoding** — the write linter checks for a near-identical existing memory and routes to update OR orthogonalizes the new title. *Basis:* dentate-gyrus pattern separation. *Touches:* write linter, reconsolidate.py. *Effort:* M. *Measurable:* near-duplicate creation rate.
91. **Decay high-false-alarm reflexes** — ern_learn decays the strength of instinct triggers with high false-alarm rates. *Basis:* extinction / maladaptive-reflex pruning. *Touches:* ern_learn.py, instincts.tsv. *Effort:* M. *Measurable:* false-alarm-rate reduction.
92. **Extinction ≠ erasure + recovery watch** — a superseded belief is marked extinct-but-retrievable; if the old pattern recurs, the dream flags spontaneous recovery. *Basis:* extinction is not erasure (Bouton). *Touches:* salience supersession, dream.py. *Effort:* M. *Measurable:* correct handling of recurring old-context cases.
93. **Load-triggered pruning dream** — when corpus/index exceeds a budget, schedule a pruning-focused dream. *Basis:* capacity-limited memory. *Touches:* dream scheduler, compact_index.py. *Effort:* S. *Measurable:* corpus stays within budget with retrieval maintained.
94. **Output-interference dedup in recall** — spread_recall presents only the stronger of two near-duplicate candidates. *Basis:* output interference (Roediger). *Touches:* spread_recall.py. *Effort:* S. *Measurable:* redundant-fire rate.
95. **Cold-store recall bridge (savings)** — a cold-stored memory whose topic recurs strongly is recovered cheaply. *Basis:* Ebbinghaus savings on relearning. *Touches:* cold store, spread_recall.py. *Effort:* M. *Measurable:* recovery precision.
96. **Disuse fade for uncited references** — reference-type memories get a disuse decay so a never-cited reference can go stale. *Basis:* disuse-driven forgetting (Wixted). *Touches:* salience.py TYPE_PERSIST. *Effort:* S. *Measurable:* stale-reference reduction.
97. **Index-redundancy diff** — compact_index avoids listing near-duplicate descriptions in MEMORY.md. *Basis:* list-strength / redundancy. *Touches:* compact_index.py. *Effort:* M. *Measurable:* index-redundancy metric.
98. **Fade the corrected original to cold** — once a correction is consolidated, the wrong original fades to cold store (kept for the growth audit). *Basis:* memory updating / adaptive forgetting. *Touches:* salience supersession, cold store. *Effort:* M. *Measurable:* wrong-belief re-surfacing rate.
99. **Homeostatic edge budget** — build_spread_edges caps total graph weight; over budget, drops the globally weakest edges. *Basis:* SHY total-weight conservation. *Touches:* build_spread_edges.py. *Effort:* S. *Measurable:* graph-size stability + spread precision.
100. **Per-type empirical forgetting rate** — measure the real co-read half-life per memory type and set a type-specific TAU. *Basis:* material-dependent forgetting rates. *Touches:* salience.py, offline analysis. *Effort:* M. *Measurable:* per-type decay-fit quality.

## Domain 2 — Retrieval & recall, association & schema

### (2A) Memory retrieval & recall
1. **One shared accessibility number** — extract a single accessibility.py `activation(slug)` that fok_probe, salience-recency, and consciousness-tier all import. *Basis:* ACT-R base-level activation. *Touches:* new accessibility.py + those three. *Effort:* M. *Measurable:* log the three current numbers, report disagreement; re-run recall eval + a "predict next co-read" check.
2. **Base-level learning decay** — replace salience's single R=exp(-age/TAU) with B=ln(Σ tⱼ^-d) over actual retrieval timestamps (recency AND frequency). *Basis:* Anderson-Schooler rational analysis. *Touches:* salience.py. *Measurable:* Brier predicting the next co-read.
3. **Retrieval-event log** — coactivation_log also appends (slug, ts) to retrievals.jsonl so base-level activation has its timestamp series. *Basis:* frequency+recency of retrieval. *Touches:* coactivation_log.py. *Measurable:* enables #2; corpus coverage.
4. **Spacing-effect bump** — a memory retrieved after long dormancy gets a larger, slower-fading increment than one retrieved repeatedly in one session. *Basis:* spacing/testing effect. *Touches:* coactivation_end/salience. *Measurable:* survival of spaced vs massed.
5. **Encoding-context tags at write** — sourced_memory stamps the session's context (cwd, domain, sibling id) so retrieval can match cue-to-context. *Basis:* encoding specificity. *Touches:* sourced_memory.py + frontmatter. *Measurable:* recall eval with vs without context match.
6. **Context-dependent seeding** — spread_recall boosts seeds whose stored encoding-context matches the current session. *Basis:* Godden-Baddeley. *Touches:* spread_recall.py. *Measurable:* precision of fires in matched vs mismatched context.
7. **Recall→recognition second stage** — after FOK names candidates, cheaply verify the body contains an answer before surfacing. *Basis:* two-process recall/recognition (Yonelinas). *Touches:* fok_probe.py. *Measurable:* FOK false-positive rate before/after.
8. **Attractor pattern-completion recall** — store embeddings as attractors and complete a partial cue to the nearest pattern (Hopfield). *Basis:* CA3 autoassociation (Marr, O'Reilly). *Touches:* build_memory_embeddings *(box)* + spread_recall. *Effort:* L. *Measurable:* recall eval on partial-cue pairs.
9. **Tip-of-the-tongue surface** — when FOK activation is high but nothing clears threshold, surface the partial cue (filename fragments, nearest hub). *Basis:* Brown-McNeill TOT. *Touches:* fok_probe.py. *Measurable:* how often the partial cue leads to the right memory.
10. **Fire-precision confidence tag** — log every spread_recall fire + whether the session read that memory; learn a per-fire-type precision as a confidence label. *Basis:* retrieval fluency vs accuracy (Koriat). *Touches:* spread_recall.py + fires log. *Measurable:* calibration of tagged confidence vs use.
11. **Retrieval-strengthened consolidation** — memories retrieved-and-used get consolidation priority in the dream. *Basis:* retrieval-induced consolidation. *Touches:* dream.py + retrievals log. *Measurable:* later accessibility of retrieved vs merely-present.
12. **Retrieval-induced forgetting of competitors** — repeated retrieval for a cue fades unretrieved rivals' activation. *Basis:* RIF. *Touches:* salience/reactivation. *Measurable:* whether demoted rivals were stale.
13. **Context reinstatement at wake** — resume_checkpoint/foresight reinstate the resumed task's encoding context to prime its memories. *Basis:* mental context reinstatement (Smith-Vela). *Touches:* resume_checkpoint, foresight. *Measurable:* mid-session grep count warm vs cold.
14. **IDF-weighted cue words** — replace fok_probe's "keep the 12 longest words" with real corpus IDF weighting. *Basis:* distinctiveness / rational need-probability. *Touches:* fok_probe.py + idf.tsv. *Measurable:* recall precision vs the length heuristic.
15. **Corpus IDF sidecar** — precompute term→IDF over all bodies so FOK, spread seeding, and curiosity share one rarity signal. *Basis:* Anderson rational analysis. *Touches:* new build step + fok/spread. *Measurable:* input to #14; term rankings vs intuition.
16. **Fan-effect normalization** — a cue word pointing to many memories gives each proportionally less activation. *Basis:* fan effect (Anderson 1974). *Touches:* fok_probe.py. *Measurable:* over-firing rate on broad prompts.
17. **Actually compute the embeddings** — run build_memory_embeddings on the box, sync the .npz; the online rerank waits for it and is a no-op until then. *Basis:* distributed semantic representation. *Touches:* build_memory_embeddings *(box)* → spread_recall. *Effort:* S (run). *Measurable:* eval_semantic_recall.
18. **Embed gist not just the index line** — embed description + first body paragraph. *Basis:* gist/levels-of-processing. *Touches:* build_memory_embeddings *(box)*. *Measurable:* separation gap vs description-only.
19. **Field-typed vectors** — separate embeddings for title, gist, and the "scar/number" so a numeric cue retrieves on the numeric field. *Basis:* encoding specificity by content type. *Touches:* build_memory_embeddings *(box)*, spread_recall. *Measurable:* recall on numeric vs conceptual queries.
20. **CPU bag-of-word-vectors query encoder** — average the prompt's word vectors (no model, laptop-safe) and cosine to precomputed doc vectors. *Basis:* SIF/bag-of-embeddings dual encoder. *Touches:* new online encoder in spread_recall. *Effort:* M. *Measurable:* recall eval using this query path.
21. **SIF weighting on the query encoder** — down-weight frequent words in the averaged query vector. *Basis:* Arora SIF. *Touches:* the #20 encoder. *Measurable:* recall precision with vs without SIF.
22. **Cascade rerank** — pipeline lexical FOK recall → embedding rerank → one-hop graph expansion. *Basis:* recall-then-precision multi-stage retrieval. *Touches:* spread_recall.py. *Measurable:* precision@1 vs single-stage.
23. **Full-pipeline query eval** — extend eval_semantic_recall beyond fixed pairs to labeled cue→target judgments (precision/recall@k). *Basis:* IR evaluation. *Touches:* eval_semantic_recall.py. *Effort:* M. *Measurable:* it IS the harness; stable because targets are fixed.
24. **Quantity-type cue matching** — a memory carrying a Sharpe/drawdown/percentile is retrieved when that quantity type appears in the cue. *Basis:* encoding specificity for specifics. *Touches:* fok_probe + sourced_memory's QUANT regex. *Measurable:* recall of numeric memories on numeric prompts.
25. **Recency-scaled seeds** — scale each seed's injected activation by its accessibility so recall favors fresh memories unless an old one is much more strongly linked. *Basis:* recency in retrieval competition. *Touches:* spread_recall.py. *Measurable:* age distribution of surfaced vs judged relevance.
26. **Affect-congruent retrieval** — under danger_mode, bias surfaced memories toward matching valence (scars when things feel wrong). *Basis:* mood-congruent memory (Bower). *Touches:* spread_recall + salience valence. *Measurable:* relevance of fires inside vs outside danger_mode.
27. **Agent-state-dependent recall** — the running sibling agent's identity biases recall toward that agent's domain cluster. *Basis:* state-dependent memory. *Touches:* spread_recall seeding. *Measurable:* cross-domain false-fire rate by agent.
28. **Habituation on over-surfaced bridges** — a bridge that keeps firing across sessions gets rising suppression. *Basis:* habituation / output interference. *Touches:* spread_recall .spread_fired state. *Measurable:* repeat-fire rate over a week.
29. **Reconstruction-drift guard** — when a recalled memory is paraphrased into a claim, flag drift between paraphrase and source. *Basis:* Bartlett reconstructive memory; source monitoring. *Touches:* reality_monitor + recall log. *Measurable:* flagged drifts confirmed real.
30. **Embedding-aware FONK** — the "you don't know this" half fires only if the prompt is also far from every memory in embedding space. *Basis:* accessibility model, negative case. *Touches:* fok_probe *(box)*. *Measurable:* false-FONK rate when a semantically-near memory exists.
31. **Fluency-skepticism tag** — a very fast recall on a high-stakes cue gets a "verify" tag control_policy consumes. *Basis:* fluency misattribution (Alter-Oppenheimer). *Touches:* spread_recall → control_policy. *Measurable:* error rate of fast-recalled high-stakes claims.
32. **Seed from recent reads** — spread_recall also seeds on the memories this session already opened. *Basis:* contextual/associative priming. *Touches:* spread_recall + coactivation_session. *Measurable:* precision of read-seeded vs prompt-seeded fires.
33. **Priming-decay re-fire** — replace fixed dedup windows with a decaying activation so a reflex re-fires as its prime wanes. *Basis:* repetition-priming time course. *Touches:* instinct_prime, spread_recall. *Measurable:* missed-warning rate on long sessions.
34. **Adaptive-hop settling** — spread stops early when activation concentrates on a clear winner (low entropy), continues when diffuse. *Basis:* attractor settling (Hopfield). *Touches:* spread_recall.py. *Measurable:* precision + fire rate vs fixed HOPS=2.
35. **Convergent-cue superlinear boost** — a memory reached from multiple seeds gets a superlinear bonus (coincidence = relevance). *Basis:* coincidence detection. *Touches:* spread_recall.py. *Measurable:* precision of multi-seed-converged vs single-path.
36. **Recognition-by-embedding for errors** — error_monitor recognizes "we've hit this failure before" via embedding similarity, not just regex. *Basis:* recognition memory (familiarity). *Touches:* error_monitor + outcomes embeddings *(box)*. *Measurable:* recall of prior-failure matches vs regex-only.
37. **Source tag surfaced at recall** — when recall surfaces a memory, also surface the key claim's provenance (measured/sim/assumed). *Basis:* source monitoring (Johnson). *Touches:* spread_recall + frontmatter. *Measurable:* reduction in measured-vs-modeled confusions.
38. **Encoding-depth score feeds persistence** — memories authored with more elaboration get a persistence bonus. *Basis:* levels-of-processing. *Touches:* salience persist term. *Measurable:* later retrievability of deep vs shallow.
39. **Distinctive-item replay** — the dream replays the most distinctive member of a cluster preferentially. *Basis:* von Restorff. *Touches:* dream.py replay draw. *Measurable:* post-replay accessibility of distinctive items.
40. **Preparsed graph cache** — cache the parsed spread_edges adjacency (pickle/mmap) so the hook can afford more hops / the rerank. *Basis:* retrieval-speed enabler. *Touches:* spread_recall + build step. *Effort:* S. *Measurable:* hook wall-time before/after.
41. **Cross-hook activation accumulation** — carry a decaying activation vector in session state so successive hooks add to it. *Basis:* sustained contextual activation (ACT-R). *Touches:* spread_recall + a session activation file. *Measurable:* late-session fire relevance.
42. **Split recall/recognition eval** — report recall (did the target surface unprompted) and recognition (given target, is it rated related) separately. *Basis:* dual-process distinction. *Touches:* eval_semantic_recall.py. *Effort:* S. *Measurable:* the two curves.
43. **Query expansion by spreading** — expand the prompt cue with its one-hop neighbors before matching. *Basis:* spreading activation as query expansion. *Touches:* fok_probe/spread_recall. *Measurable:* recall on short prompts vs unexpanded.
44. **Surface competing memories** — when two high-salience memories contradict on the same cue, surface BOTH. *Basis:* retrieval competition / interference. *Touches:* spread_recall.py. *Measurable:* contradictions caught that single-winner hid.
45. **Failed-recall writes the gap** — a FONK miss is logged and handed to the dream as a "memory that should exist." *Basis:* errorful generation potentiates learning (Kornell). *Touches:* fok_probe + dream.py. *Measurable:* fraction of gaps the dream fills that later get retrieved.
46. **Confirmed-reminding training pairs** — log "this reminds me" fires the session then used, as positive pairs to strengthen the edge. *Basis:* reminding-based learning (Schank). *Touches:* spread_recall → coactivation. *Measurable:* recall eval on edges from confirmed remindings.
47. **Directed-forgetting for transient** — transient-typed memories get a steeper accessibility decay. *Basis:* directed forgetting (Bjork). *Touches:* salience + tag_transient. *Measurable:* accessibility gap transient vs durable over time.
48. **Autobiographical retrieval hierarchy** — index at three grains (lifetime-period → theme → episode) so a broad cue lands at the right granularity. *Basis:* Conway self-memory system. *Touches:* new index + fok_probe. *Effort:* L. *Measurable:* precision on broad vs specific cues.
49. **Retrieval-offload measurement** — instrument whether foresight's pre-loaded if-thens reduce later grep/recall. *Basis:* prospective memory / cognitive offloading. *Touches:* foresight + a counter. *Measurable:* grep counts preloaded vs not.
50. **Part-set cuing cap** — surface at most one best bridge (a long recalled list inhibits the target). *Basis:* part-list cuing inhibition (Slamecka). *Touches:* spread_recall output. *Measurable:* target-hit rate at list length 1 vs many.

### (2B) Association, schema & semantic networks
51. **Semantic k-NN edges** — add top-k embedding-neighbor edges into spread_edges as a third edge type. *Basis:* embeddings-as-semantic-network. *Touches:* build_memory_embeddings → build_spread_edges *(box)*. *Effort:* M. *Measurable:* connectivity + recall eval.
52. **Embedding link prediction** — score non-edges by cosine × common-neighbors; hand top candidates to the dream to author as links. *Basis:* KG completion. *Touches:* new predictor + dream *(box)*. *Measurable:* hold out 10% of links, report AUC.
53. **Topology-only link prediction** — Adamic-Adar / resource-allocation over the graph proposes missing links, no embeddings. *Basis:* structural link prediction (Liben-Nowell). *Touches:* link_audit.py. *Effort:* M. *Measurable:* held-out-link AUC.
54. **Community detection for TOPICS** — Louvain/label-propagation over spread_edges, name communities, write as TOPICS clusters. *Basis:* modularity (Girvan-Newman). *Touches:* new step + TOPICS. *Effort:* M. *Measurable:* modularity + embedding coherence.
55. **Landmark hub index** — detect high weighted-degree hubs, verify substantive, use as navigation landmarks. *Basis:* small-world hubs (Watts-Strogatz). *Touches:* link_audit / nav. *Measurable:* path-length reduction using hubs.
56. **Structure-mapping analogy finder** — find pairs sharing relational structure (same agent/action/outcome roles), not surface words. *Basis:* structure-mapping (Gentner). *Touches:* schema_extract + a roles field. *Effort:* L. *Measurable:* human-judged relevance of analogies.
57. **MAC/FAC analogical retrieval** — cheap surface pass then rerank by structural match. *Basis:* Forbus MAC/FAC. *Touches:* schema_extract, spread_recall. *Effort:* L. *Measurable:* precision of schema matches vs keyword.
58. **Schema prototype vectors** — average member-episode embeddings into a prototype per schema/gist; compare new memories to prototypes. *Basis:* prototype theory (Posner-Keele). *Touches:* schema_extract *(box)*. *Measurable:* new-memory→schema assignment accuracy.
59. **Category coherence check** — within-cluster vs between-cluster embedding similarity to validate each schema cluster. *Basis:* category structure / cue validity. *Touches:* eval over schemas *(box)*. *Effort:* S. *Measurable:* the coherence ratio.
60. **Conceptual-space axes** — PCA the embeddings for interpretable axes (measured↔modeled, research-vs-research, scar↔win). *Basis:* conceptual spaces (Gärdenfors). *Touches:* offline + coords sidecar *(box)*. *Effort:* M. *Measurable:* do axes predict type/valence.
61. **Betweenness bridge finder** — surface high-betweenness memories connecting separate communities. *Basis:* structural holes (Burt). *Touches:* new graph metric. *Measurable:* whether bridges are the cross-domain-transfer memories.
62. **Similarity-scaled hop decay** — spread decay per hop set by edge strength/semantic similarity, not fixed 0.5. *Basis:* Collins-Loftus. *Touches:* spread_recall.py. *Measurable:* precision vs fixed DECAY.
63. **Edge-type-aware walk** — wiki, Hebbian, semantic edges get distinct weights/decays (a multiplex graph). *Basis:* multiplex networks. *Touches:* spread_recall + edge type. *Measurable:* per-type ablation on precision.
64. **PMI on co-reads** — replace raw co-read counts with pointwise mutual information. *Basis:* PMI (Church-Hanks). *Touches:* coactivation_end.py. *Measurable:* hub-artifact fire rate before/after.
65. **Inverse-promiscuity edge weighting** — discount a pair co-read only in large survey sessions vs one co-read in a tight session. *Basis:* IDF of pairs. *Touches:* coactivation_end.py. *Measurable:* precision of Hebbian-derived fires.
66. **Schema-accelerated auto-link** — a new memory matching a schema auto-inserts the link into the cluster. *Basis:* schema-consistent memories integrate faster (Tse). *Touches:* write hook + schema_extract. *Measurable:* orphan rate before/after.
67. **Prediction-error-gated schema revision** — a new memory VIOLATING a matched schema flags it for revision, not absorption. *Basis:* schema + prediction error (Ghosh-Gilboa). *Touches:* schema_extract, reconsolidate. *Measurable:* count of warranted revisions.
68. **Semantic-drift tracker** — track each cluster's embedding centroid over time; a moving centroid is the brain changing its mind. *Basis:* semantic change. *Touches:* growth *(box)*. *Measurable:* do drifts match noted belief revisions.
69. **Graph+embedding type inference** — infer an untyped memory's type by label-propagation over graph + embedding neighborhood. *Basis:* node classification / homophily. *Touches:* type_backfill.py. *Measurable:* accuracy on held-out typed memories.
70. **Structural node2vec embeddings** — graph embeddings over spread_edges (a memory's role by its connections). *Basis:* graph representation learning (Grover-Leskovec). *Touches:* new offline build *(box)*. *Effort:* L. *Measurable:* link-prediction AUC vs content-only.
71. **Fuse content + structural embeddings** — concatenate content and node2vec vectors for the rerank. *Basis:* hybrid retrieval. *Touches:* spread_recall *(box)*. *Measurable:* recall eval fused vs single.
72. **Triadic-closure suggestions** — where A-B and B-C are strong and embeddings support A-C, propose the closing link. *Basis:* triadic closure / balance. *Touches:* link_audit.py. *Measurable:* held-out-link recovery.
73. **Spurious-link pruning** — find wiki-links whose endpoints are far in embedding space and demote their walk weight. *Basis:* graph denoising. *Touches:* build_spread_edges *(box)*. *Measurable:* precision of fires after down-weighting.
74. **Small-world health audit** — track clustering coefficient and mean path length over time. *Basis:* Watts-Strogatz navigability. *Touches:* link_audit.py. *Effort:* S. *Measurable:* the two metrics over weeks (non-stationary-safe trend).
75. **Orphan rescue by cosine** — for each orphan, propose its best inbound link via embedding similarity. *Basis:* link prediction for isolates. *Touches:* link_audit *(box)*. *Measurable:* orphan reduction + relevance.
76. **Personalized-PageRank recall** — replace the fixed-hop decay walk with personalized PageRank from the seed set. *Basis:* random-walk retrieval. *Touches:* spread_recall.py. *Effort:* M. *Measurable:* precision vs current 2-hop.
77. **Hierarchical gist super-nodes** — cluster episodes → author a gist → link members → the gist becomes a routable super-node. *Basis:* hierarchical semantic memory (Collins-Quillian). *Touches:* schema_extract, build_spread_edges. *Effort:* L. *Measurable:* path-length + recall via super-nodes.
78. **Property inheritance at recall** — a prior on a schema node is inherited by member episodes when recalled. *Basis:* Collins-Quillian inheritance. *Touches:* spread_recall + schema priors. *Measurable:* whether inherited priors were relevant.
79. **Typicality gradient in clusters** — rank a schema's members by distance to prototype; atypical members are the interesting ones. *Basis:* graded typicality (Rosch). *Touches:* schema_extract *(box)*. *Measurable:* human rating of "interesting" atypical vs typical.
80. **Basic-level cluster granularity** — pick the granularity most nameable/useful for TOPICS. *Basis:* Rosch basic-level. *Touches:* community-detection step. *Measurable:* nameability / balanced cluster sizes.
81. **Cross-domain transfer detector** — flag when a method in one domain maps onto another (the owner's signature move). *Basis:* analogical transfer (Gick-Holyoak). *Touches:* curiosity + structure-mapping. *Measurable:* human-judged usefulness of transfers.
82. **Relational schema slots** — store schemas as templates with agent/action/outcome slots so analogies match on roles. *Basis:* structure-mapping roles. *Touches:* schema_extract output. *Effort:* M. *Measurable:* analogy-match precision vs keyword.
83. **Curiosity incongruity → link candidates** — feed curiosity's co-activated-but-unlinked pairs to the dream/link_audit. *Basis:* closing information gaps (Loewenstein). *Touches:* curiosity → dream. *Effort:* S. *Measurable:* fraction of proposed links accepted + used.
84. **Betweenness-weighted consciousness** — a bridge memory stays conscious even when old, because its structural role keeps it load-bearing. *Basis:* structural centrality vs recency. *Touches:* consciousness + betweenness. *Measurable:* whether kept-conscious bridges are retrieved.
85. **Degree-corrected communities** — cluster with a degree-corrected model so hubs don't swallow communities. *Basis:* DC-SBM (Karrer-Newman). *Touches:* community-detection. *Measurable:* modularity + hub placement.
86. **Held-out link-prediction eval** — hide 10% of wiki-links, score recovery AUC as embeddings/topology change. *Basis:* KG-completion evaluation. *Touches:* new eval. *Effort:* M. *Measurable:* it's the measurement; stable held-out set.
87. **Prototype-based FONK** — decide "brain has nothing here" by distance to ~30 cluster prototypes, not scanning all 1391. *Basis:* prototype match. *Touches:* fok_probe + prototype sidecar. *Measurable:* false-FONK rate + latency vs full scan.
88. **Stale-schema alarm** — if a scar's schema is contradicted by newer memories, flag the derived instinct as possibly stale. *Basis:* schema revision. *Touches:* schema_extract → instincts. *Measurable:* count of instincts correctly flagged stale.
89. **Analogical instinct firing** — instinct_prime fires when the SITUATION structurally resembles a past scar's schema, even without trigger keywords. *Basis:* analogical reminding (Ross). *Touches:* instinct_prime + schema roles. *Effort:* L. *Measurable:* extra true warnings vs regex-only.
90. **Top-down schema-seeded recall** — seed the walk from the matched schema node as well as leaf memories. *Basis:* schema-guided retrieval (Bartlett). *Touches:* spread_recall.py. *Measurable:* precision when a schema matches vs leaf-only.
91. **Complementary fast/slow graph split** — a fast-decaying Hebbian layer and a slow wiki/semantic cortical layer, blended at query time. *Basis:* CLS (McClelland-O'Reilly). *Touches:* build_spread_edges, spread_recall. *Effort:* M. *Measurable:* recall stability week-to-week.
92. **Interleaved dream replay** — integrating a new schema interleaves old and new members to avoid interference. *Basis:* CLS interleaving. *Touches:* dream replay. *Measurable:* do old members stay accessible.
93. **Index-line fidelity check** — cosine between each MEMORY.md description embedding and its body; low match = the index line misrepresents. *Basis:* gist fidelity. *Touches:* new audit *(box)*. *Effort:* S. *Measurable:* the fidelity distribution.
94. **Embedding near-duplicate demotion** — detect near-duplicate transient logs by embedding and demote to unconscious. *Basis:* redundancy compression. *Touches:* consciousness *(box)*. *Measurable:* compression ratio + no substantive demotion.
95. **Structural missing-schema detector** — a dense unlinked triangle with no gist is a missing schema; propose writing it from topology. *Basis:* structural gap. *Touches:* schema_extract.py. *Measurable:* validity of proposed schemas.
96. **Directional association edges** — log co-read ORDER so the walk can be asymmetric (A reminds of B more than B of A). *Basis:* asymmetric free-association (Nelson USF). *Touches:* coactivation_log, spread_recall. *Effort:* M. *Measurable:* directional vs symmetric precision.
97. **Mediated-priming validation** — verify the 2-hop walk delivers mediated primes (A→C via B, no direct link). *Basis:* mediated semantic priming (Balota-Lorch). *Touches:* eval + spread. *Effort:* S. *Measurable:* precision on constructed mediated-prime triples.
98. **Cue-aware fan damping** — damp a hub only when the cue is generic (high fan), keep it when specific — the correct version of the rejected blanket penalty. *Basis:* fan effect, cue-conditioned. *Touches:* spread_recall.py. *Measurable:* precision on generic vs specific cues vs the no-penalty baseline.
99. **Corpus semantic map image** — box-render a UMAP/t-SNE 2D map (static PNG, no window) so the owner SEES clusters, bridges, gaps. *Basis:* cognitive map. *Touches:* offline plot *(box)*. *Effort:* M. *Measurable:* qualitative; owner-facing.
100. **Structure-vs-content edge audit** — cross-check the three edge sources; edges all three agree on are trusted, single-source ones are the interesting/suspect cases. *Basis:* converging-evidence triangulation. *Touches:* build_spread_edges *(box)*. *Effort:* M. *Measurable:* precision of walk on multi-source-agreed edges.

> Domains 3–10 are in condensed one-line form (title — mechanism · *basis* · *measure*). Full
> effort/touch detail was generated per idea and can be re-expanded; the point here is a complete,
> browsable backlog. The shortlist and Domains 1–2 above model the full format.

## Domain 3 — Sleep & dreaming, learning algorithms & plasticity

### (3A) Sleep & dreaming
1. **Sequential SWS→REM hand-off** — REM seeds from the morning SWS gists · *Giuditta sequential hypothesis* · *M:* % REM seeds tracing to today's gists.
2. **SWS-freshness gate for REM** — REM files lightly first if morning SWS didn't run · *Diekelmann-Born* · *M:* REM-without-SWS count.
3. **Gain×need prioritized replay** — multiply §E need by a gain term from predictions/reconsolidation/ERN · *Mattar-Daw* · *M:* replay mean surprise-bits.
4. **Predecessor / prioritized sweeping** — a gist update queues its link-predecessors for next night · *Mattar-Daw / Moore-Atkeson* · *M:* next-night reactivation of queued.
5. **Mechanized reverse replay for scars** — reverse_replay.py walks a scar's session back to the causal step · *Foster-Wilson* · *M:* scars→reflexes per dream.
6. **SHY global downscaling** — multiply all coactivation weights ×0.9 before adding the night's · *Tononi-Cirelli SHY* · *M:* edge-weight distribution stability.
7. **Local use-dependent sleep depth** — consolidation depth scales with the day's co-read volume on a topic · *Vyazovskiy* · *M:* gist depth vs topic activity.
8. **Spindle-density health metric** — brain_status reports gists+schemas+links/night; decline = stalled consolidation · *slow-osc–spindle coupling* · *M:* the metric nightly.
9. **Emotional depotentiation of reflex-covered scars** — discount charge for a scar now covered by a reflex · *van der Helm-Walker* · *M:* covered-scar charge falls, salience holds.
10. **Future-relevance triage** — §A up-weights memories an open question / owner directive depends on · *Wilhelm 2011* · *M:* consolidated-then-retrieved fraction.
11. **Awake replay in short sessions** — a brief session surfaces one linked dormant memory into next wake · *Foster-Wilson awake ripples* · *M:* dormant reactivation coverage.
12. **Ripple-burst session tagging** — flag high co-read-density sessions the next dream prioritizes · *sharp-wave ripples* · *M:* yield ripple vs flat.
13. **Triadic recombination seeds** — §G occasionally hands REM a 3-memory seed · *generative recombination* · *M:* triad hunches later confirmed.
14. **Insight hit-rate ledger** — scan daydream_* for later confirming backlinks · *Wagner 2004* · *M:* confirmed-hunch fraction.
15. **Hidden-rule (number-reduction) pass** — look for a procedural shortcut across an ordered episode chain · *Wagner number-reduction* · *M:* shortcuts that cut repeated work.
16. **Coactivation-cluster schemas** — schema candidates from coactivation connected components, not just vocab · *Lewis-Durrant* · *M:* co-read vs vocab schema usefulness.
17. **Schema-accelerated assimilation** — auto-link each new memory to a matching schema · *Tse 2007* · *M:* dreams from creation to first schema link.
18. **Computed interleaving of old neighbors** — §A pairs each recent cluster with 2-3 old schema-neighbors · *Ji-Wilson* · *M:* % gists citing an old neighbor.
19. **Directed forgetting cue** — the dream appends slugs to forget_cues.txt; salience adds decay · *sleep directed forgetting* · *M:* cued items fade faster.
20. **Pattern-separation guard before merge** — a difference score; a load-bearing divergence means link not merge · *dentate-gyrus separation* · *M:* over-merge / reversal count.
21. **Spaced TMR interval scheduler** — important memories on expanding re-read intervals · *Rasch-Rudoy + spacing* · *M:* reactivation coverage; interval growth.
22. **Cue-specific TMR** — reactivation rescues only dormant memories matching today's tokens · *Rudoy 2009 selective cueing* · *M:* day-relevance of rescued.
23. **Mid-distance remote-associate seeds** — §G picks remote-but-reachable pairs, not maximally distant · *Cai-Mednick RAT* · *M:* bisociation acceptance by band *(box)*.
24. **Enforced light nap budget** — nap gets a tighter watchdog and no §G · *Mednick 2003* · *M:* nap cost/turns vs SWS/REM.
25. **Down-selection edge pruning** — drop edges below floor after nightly decay · *SHY down-selection* · *M:* edge count; spread precision.
26. **Cross-dream continuity thread** — REM reads the last two journals and continues a through-line · *Fosse REM continuity* · *M:* cross-dream references.
27. **Prediction-miss-first replay** — §E leads with today's biggest prediction surprises · *Schultz RPE-tagged replay* · *M:* replay overlap with top-surprise bets.
28. **Sleep-pressure agenda sizing** — agenda sized by days since last successful dream · *Process-S sleep pressure* · *M:* agenda size vs gap.
29. **Forward replay for planning** — foresight builds a task-transition model and forecasts the next task · *Pfeiffer-Foster* · *M:* "what he'll want next" hit-rate.
30. **Episodic→semantic ratio target** — brain_status tracks dated-project vs gist/schema ratio · *systems consolidation* · *M:* the ratio over time.
31. **Gist-intrusion (false-memory) check** — SWS must cite each gist claim to a source; uncited = flagged · *Payne-Kensinger DRM* · *M:* uncited-claim rate in new gists.
32. **REM-only affective replay weighting** — weight §E toward high-charge memories on REM nights · *Payne emotional-memory bias* · *M:* replay mean-charge by type.
33. **Reward-tagged consolidation** — praised-topic memories get §A priority · *Fischer-Born* · *M:* consolidation rate of praised topics.
34. **Procedural consolidation track** — route how-to memories to compile into checklists/reflexes · *Walker procedural* · *M:* procedures becoming reflexes.
35. **Sleep-fragmentation monitor** — report the run_dream watchdog-kill rate as "fragmented sleep" · *fragmentation degrades consolidation* · *M:* kill rate vs low-spindle nights.
36. **Multi-night association carryover** — §B candidates not acted on persist for a 2nd/3rd night · *consolidation across sleeps* · *M:* fraction of links needing >1 night.
37. **Replay topic-diversity floor** — cap replays per topic so the loudest theme doesn't dominate · *avoid over-replay* · *M:* entropy of replayed topics.
38. **Spindle-gated gist transfer** — write a gist only when a cluster has ≥N coherent members · *up-state readiness gating* · *M:* deferred vs written coherence.
39. **Incubation minimum-delay gate** — surface a parked question only after ≥1 night · *Sio-Ormerod* · *M:* incubation hit-rate by delay.
40. **Cross-domain transfer ledger** — bisociations that transfer a method get a `transfer:` tag and a tracked network · *REM cross-domain transfer* · *M:* count + downstream use.
41. **Counterfactual scar replay** — REM replays "what if we'd done X" and records the alternative reflex · *Gupta-Moser nonlocal replay* · *M:* hard (qualitative).
42. **Competitive forgetting of superseded losers** — down-scale a superseded loser's edges · *sleep competitive forgetting* · *M:* superseded recall drops.
43. **Gain-ranked agenda spend** — spend the 45-min budget top-value-first, not "3-5 arbitrary" · *Mattar-Daw* · *M:* value-weighted completion.
44. **Offline embedding build for dreams** — schedule build_memory_embeddings on the box via hidden VBS · *semantic replay seeding* · *M:* bisociation/recall precision with sidecar *(box)*.
45. **State-driven dream-type selection** — pick SWS vs REM by state (unfiled recent → SWS), not only hour · *sleep-need-driven architecture* · *M:* agenda matches chosen type.
46. **Mistake-ledger gain ranking** — §M ranks MISTAKES by recurrence×recency, converts the top uncovered to a reflex · *prioritized replay on the mistake mandate* · *M:* recurring uncovered mistakes gaining a reflex.
47. **Day-residue seed guarantee** — §G always includes one pair with a today-member + a remote one · *Nielsen day-residue* · *M:* accepted hunches with a day-residue member.
48. **Reactivation-at-wake mini-TMR** — a prompt strongly matching a dormant important memory surfaces it · *wake cue as TMR* · *M:* dormant surfacing rate at wake.
49. **Replay of tag-and-capture pairs** — §E replays a low-charge memory co-encoded with a high-charge one · *Frey-Morris tagging* · *M:* retained captured weak memories.
50. **Schema-vs-gist routing by span** — days-spanning cluster → gist, weeks-spanning → schema · *Lewis-Durrant* · *M:* correctness of the split.

### (3B) Learning algorithms & synaptic plasticity
51. **Directional STDP edges** — record read order; build directed A→B edges · *STDP* · *M:* next-read prediction accuracy.
52. **STDP temporal-window weighting** — weight a co-read pair by read proximity, not flat all-pairs · *STDP timing window* · *M:* edge precision vs flat.
53. **Anti-Hebbian LTD on failure** — pairs co-read in a failed session get a negative increment · *LTD / anti-Hebbian* · *M:* down-weighted pairs recur less in failures.
54. **Three-factor reward gate on consolidation** — multiply edge increments by a reality_monitor success factor · *neuromodulated Hebbian* · *M:* edge weight vs session usefulness.
55. **RPE as the neuromodulator** — high-surprise sessions consolidate their co-reads more · *Schultz RPE gating* · *M:* consolidation vs surprise bits.
56. **Eligibility traces → usefulness** — a decaying trace of reads/tools; on Stop distribute the outcome into usefulness.tsv · *TD(λ) traces* · *M:* usefulness predicting future success.
57. **Late-LTP labile edge window** — young edges decay fast unless re-hit; survivors stabilize · *late-LTP consolidation* · *M:* edge survival by age.
58. **Synaptic scaling per node** — renormalize each node's incident weight toward a target · *Turrigiano scaling* · *M:* degree distribution; precision.
59. **BCM sliding charge threshold** — set the charge normalizer from the recent mean charge · *BCM metaplasticity* · *M:* charged-fraction stays constant as corpus grows.
60. **BCM metaplastic hub threshold** — a saturated hub is harder to potentiate · *BCM θ-slides* · *M:* reduction in hub over-connection.
61. **EWC graph-Fisher edit resistance** — importance = backlinks×salience scales the destabilization threshold · *Kirkpatrick EWC* · *M:* high-importance beliefs rarely flip on one contradiction.
62. **Generative-replay rehearsal on schema write** — re-read K old members alongside the new abstraction · *Shin generative replay* · *M:* old members still retrievable.
63. **Pattern separation for near-duplicates** — tag near-duplicates as variants (linked) not collisions · *dentate-gyrus separation* · *M:* recall confusion between near-dupes.
64. **Neurogenesis novelty-seed** — genuinely novel memories tagged for strong encoding + wake priority · *adult neurogenesis* · *M:* novel-seed link growth *(box for embeddings)*.
65. **Meta-learning dream durability** — log each dream's outputs + later usefulness; monthly tune dream thresholds · *learning-to-learn* · *M:* durability of dream outputs.
66. **Learned reconsolidation thresholds** — log each route + whether the edit stuck; adjust cutoffs · *metaplastic learning rate* · *M:* revert rate of edits.
67. **Curriculum-ordered consolidation** — §A consolidates by foundationalness (in-degree) first · *curriculum learning* · *M:* dependency-respecting order rate.
68. **Testing-effect self-test step** — generate a question, answer from memory, check the file; a miss flags a weak trace · *Roediger-Karpicke* · *M:* self-test accuracy trend.
69. **Savings on relearning** — a dormant memory re-read after a gap gets a smaller-but-faster bump · *Ebbinghaus savings* · *M:* re-read persistence vs first-time.
70. **Homeostatic vivid-count cap** — normalize so vivid (>70) count stays in a band · *firing-rate homeostasis* · *M:* vivid-count stability.
71. **Reward-modulated reflex strength** — scale a reflex's charge by how often firing preceded an avoided failure · *three-factor plasticity* · *M:* reflex strength vs utility.
72. **Cross-session tag-and-capture** — a low-charge memory co-read with a high-charge one is captured · *Moncada-Viola tagging* · *M:* captured-memory persistence.
73. **Heterosynaptic competition** — when one memory's edges sharpen, slightly weaken local competitors · *Fonseca heterosynaptic LTD* · *M:* recall sharpness.
74. **Primed faster second-hit update** — a queued belief updates faster on a second contradiction · *metaplasticity priming* · *M:* latency from 2nd contradiction to update.
75. **Importance-scaled learning rate** — large edits on low-stakes memories, tiny on high-stakes · *importance-gated plasticity* · *M:* edit magnitude vs stakes.
76. **Mid-session belief-overwrite guard** — route an attempted mid-session belief edit to the reconsolidation queue · *Nader window* · *M:* blocked mid-session overwrites.
77. **CLS ratio governor** — raise schema_extract frequency when unschematized episodes pile up · *CLS balance* · *M:* unschematized backlog.
78. **Catastrophic-forgetting canary** — retrieval-test a held-out set of core beliefs each dream · *EWC evaluation* · *M:* core-belief pass rate.
79. **Interleaving over blocking** — §A interleaves topics in replay order · *Kornell-Bjork* · *M:* topic-interleaving ratio.
80. **Desirable-difficulty retrieval cue** — at mid confidence, fok_probe surfaces a question not the answer · *Bjork* · *M:* recall improvement after cued retrieval.
81. **Closed error-driven learning loop** — the biggest surprise scope becomes a curiosity gap + DREAM_FOCUS; track surprise dropping · *Rescorla-Wagner* · *M:* surprise reduction in the targeted scope.
82. **Perceptron reflex-threshold tuning** — tune each trigger's specificity from useful-vs-false fires · *error-driven threshold* · *M:* reflex precision/recall.
83. **Sparse reflex firing** — cap fires per prompt to the top-charge match · *sparse coding / lateral inhibition* · *M:* fires-per-prompt distribution.
84. **Coactivation-driven link authoring** — §B draws candidate links from high coactivation pairs, not just vocab-Jaccard · *Hebbian → structural* · *M:* co-read pairs becoming links.
85. **Labile→stable edge graduation** — an edge surviving K dreams is proposed as a durable authored wiki-link · *two-stage memory* · *M:* graduation rate.
86. **Frequency-debiased replay by type** — up-weight rare-but-important types (user/feedback) · *correct frequency bias* · *M:* replay composition by type.
87. **Mastery-paced schema abstraction** — no higher-order schema until its sub-schemas exist · *mastery learning* · *M:* schema dependency build order.
88. **Context-gated recall** — tag memories with a domain; boost same-context neighbors · *Bouton context-dependent* · *M:* within-context precision.
89. **Retrieval-induced forgetting** — a repeatedly-retrieved memory fades its unretrieved competitors · *Anderson RIF* · *M:* competitor recall frequency.
90. **Procedural skill compilation** — recurring how-to memories compiled into checklists · *Anderson ACT-R compilation* · *M:* procedures compiled + reused.
91. **Owner-feedback teaching signal** — memories near praised work get LTP, near criticized get flagged · *neuromodulated supervised signal* · *M:* salience-vs-owner-valuation alignment.
92. **Per-type forgetting-curve fit** — replace the single TAU=45 with a per-type decay fit · *Wixted power-law* · *M:* fit quality; sharper split.
93. **Tonic arousal plasticity scaling** — a global plasticity gain that rises on high-stakes days · *tonic vs phasic* · *M:* consolidation vs day stakes.
94. **Credit-horizon boundary** — the eligibility trace resets at task/session edges · *bounded credit horizon* · *M:* credit-assignment precision.
95. **Replay exploration temperature** — §E mixes exploit (day-concern) and explore (random old) at a tuned ratio · *explore/exploit in replay* · *M:* durable-link yield by ratio.
96. **Concept-alias weight-tying** — fold two same-rule memories' inbound edges onto a canonical node · *weight tying* · *M:* recall of the canonical concept.
97. **Homeostatic dream energy budget** — adapt the watchdog/turn budget to recent cost · *metabolic homeostasis* · *M:* variance of nightly cost.
98. **Offline-fit salience weights** — fit salience's persistence coefficients against "what the dream/owner reached for" · *fit the readout to ground truth* · *M:* agreement with reach-for labels *(box)*.
99. **Belief-thrash drift monitor** — flag a belief flipped repeatedly and raise its edit threshold · *stability/plasticity balance* · *M:* flip frequency per belief.
100. **Homeostatic replay set-size** — cap agenda size by recent consolidation success · *sleep-need vs capacity* · *M:* agenda size vs yield.

## Domain 4 — Predictive processing, decision-making & valuation

### (4A) Predictive processing & active inference
1. **Close the ERN→prediction loop via a reliability ledger** — error_monitor failures + a "clean session" tick score the standing infra-reliability bet as a live Bernoulli series · *Rescorla-Wagner; Schultz* · *M:* Brier of asserted vs observed clean-rate.
2. **Efference-copy forward model on PreToolUse** — predict P(command errors) from cheap features fit on outcomes.jsonl; error_monitor resolves it · *forward models (Wolpert)* · *M:* per-tool Brier + surprise-bits.
3. **Score reality_log as a calibration stream** — the `grounded` flag is a de-facto outcome for an implicit "it works" bet · *source monitoring* · *M:* reliability diagram of "it works" claims.
4. **Precision-weight prediction errors** — a `prec:` field weights calibration by observation precision · *Friston precision-weighting* · *M:* precision-weighted vs raw Brier.
5. **Reliability-diagram generator** — a 5-bin calibration table (mean-p vs hit-rate) to CALIBRATION.md · *Murphy 1973* · *M:* per-bin residuals.
6. **Brier skill score vs base-rate** — score each scope against always-predict-base-rate · *Murphy decomposition* · *M:* BSS>0 flags real skill.
7. **Log-loss beside Brier** — aggregate mean log-loss per scope to catch confident-wrong bets · *cross-entropy scoring* · *M:* mean bits per scope.
8. **Rescorla-Wagner online haircut** — replace batch shrunk-ratio with online V←V+α(outcome−V) per domain · *Rescorla-Wagner* · *M:* RW vs batch next-bet Brier.
9. **Pearce-Hall associability** — learning rate rises after a high-bits miss, decays in calm · *Pearce-Hall; Behrens* · *M:* tracking error through a regime change.
10. **Expected vs unexpected uncertainty split** — a slow expected-noise rate + a fast spike detector; fast>expected flags a regime change · *Yu-Dayan* · *M:* does a flag precede a base-rate shift.
11. **Volatility-set shrink strength** — set KAPPA inversely to how fast a scope's rate changes · *Behrens volatility learner* · *M:* calibration under simulated volatility.
12. **Novelty-gated encoding** — boost persistence of memories from a high-surprise-prediction session · *Lisman-Grace* · *M:* do surprise-encoded get re-read more.
13. **Surprise→salience done right** — wire the unbuilt surprise term to a resolved high-bits prediction, not a regex · *Ranganath-Rainer* · *M:* precision of surprise-tagged vs lexicon.
14. **Hierarchical (multi-timescale) predictions** — tag bets event/session/regime; report calibration per timescale · *Rao-Ballard; Friston* · *M:* per-level Brier.
15. **Feeling-of-knowing as a scored forecast** — fok_probe emits P(brain holds a real cluster) resolved by whether a memory was used · *Koriat* · *M:* FoK Brier.
16. **Foresight preplay becomes a bet** — each "he'll likely ask" item is a dated owner-scope prediction · *Schacter-Addis preplay* · *M:* owner-ask preplay Brier.
17. **Active inference: pre-stage the likely ask** — pre-grep relevant memories into RESUME to fulfill the prediction · *Friston active inference* · *M:* fraction of sessions using pre-staged material.
18. **Prediction-error replay priority** — resolved high-surprise predictions as first-class replay seeds · *Mattar-Daw + RPE* · *M:* fewer repeat misses in surprise-seeded scopes.
19. **Interval forecasts for numeric bets** — a point + 80% interval; score coverage and sharpness · *calibration of subjective probability* · *M:* interval coverage vs nominal.
20. **Due-date auto-resolver** — a dream step matches every past-`by:` bet against memories written since, proposing hit/miss · *Zeigarnik + opportunistic assimilation* · *M:* fraction of due bets resolved.
21. **Edit-risk forward model** — predict P(this file needs a follow-up fix); resolve if a later edit touches it · *forward model of one's actions* · *M:* Brier of the edit-risk forecast.
22. **Wilson-interval calibration verdicts** — assert over/under-confident only when the interval excludes mean-p · *Beta-Binomial posterior* · *M:* false-verdict rate.
23. **Base-rate prior for new scopes** — seed a new scope's prior from the pooled all-scope Beta · *hierarchical shrinkage* · *M:* first-5-bet Brier.
24. **Generative model of the owner's ask-distribution** — a small topic/n-gram model over MODEL_OF_PARTNER + past asks · *Clark prediction machine* · *M:* category log-loss vs uniform *(box)*.
25. **Fleet prediction market** — agents append a `p` on the same bet; log-opinion pool + per-agent scoring · *opinion pooling* · *M:* pool Brier vs best individual.
26. **Aleatoric vs epistemic miss tagging** — only epistemic misses drive the haircut · *Yu-Dayan; free-energy* · *M:* does excluding aleatoric improve calibration.
27. **Free-energy budget KPI** — a session's total surprise as its "free energy"; a rising trend = model drift · *Friston free-energy* · *M:* the bits trend.
28. **Stale beliefs as standing predictions** — reframe CURRENT_FACTS as standing bets the environment resolves · *predictive coding* · *M:* lead time from fact-change to flag.
29. **Surprise-weighted mistake ranking** — annotate MISTAKES with bits so foresight ranks reflexes by prediction-error cost · *RPE-scaled memory* · *M:* repeats of the pre-loaded reflex's mistake.
30. **Measure post-error slowing** — log the next tool's latency/care after an ERN; test whether slowing reduces the follow-on error · *Rabbitt* · *M:* error rate right after an ERN.
31. **Two-rate reliability estimator** — fast (3-day) and slow (21-day) failure rates; the gap is a leading indicator · *Smith-Shadmehr* · *M:* does fast>slow presage a bad stretch.
32. **Score curiosity payoff** — a `p` that "closing this gap yields a durable memory" · *curiosity as predicted learning (Oudeyer)* · *M:* hit-rate of "worth it."
33. **Learning-progress signal** — surface scopes with flat/worsening Brier slope · *Oudeyer/Schmidhuber* · *M:* the slope.
34. **Precision-weighted feeling-of-knowing** — weight fok's cluster count by each pointer's precision · *Feldman-Friston* · *M:* FoK Brier before/after.
35. **Predict-the-diff before a big edit** — write a one-line behavior prediction; compare at Stop · *forward model* · *M:* prediction-vs-observed agreement.
36. **Bottom-up residual on reads** — log when a Read contradicts the memory's index one-liner (a prediction error) · *Rao-Ballard residual* · *M:* index-vs-body mismatches surfaced.
37. **TD value ramp toward answering a parked question** — incubate emits a rising signal as evidence accumulates · *Schultz TD ramps* · *M:* completion of near-answered questions.
38. **Counterfactual (fictive) prediction error** — note what the unchosen action would have yielded · *Lohrenz-Montague* · *M:* judgment-scored in the dream.
39. **Multinomial forecast of correction type** — a per-shape distribution over "how we'll fail him next," log-loss scored · *generative model of the owner* · *M:* multinomial log-loss.
40. **Adaptive learning window** — shorten ern_learn's window when unexpected uncertainty is high · *Yu-Dayan* · *M:* coverage stability across a regime change.
41. **Falsifiable ship-time bet per faculty** — register "this reduces repeat-mistake X" and resolve from outcomes · *free-energy model evidence* · *M:* ship-time bet Brier.
42. **Confirmation-decay of salience** — feed per-memory surprise into salience recency so a confirmed low-surprise memory fades faster · *RPE (no error, no trace)* · *M:* faded-vs-vivid vs surprise.
43. **Prediction-error-gated reconsolidation** — re-open a memory only on a conflict (a mismatch) · *reconsolidation needs PE (Sederberg)* · *M:* fraction of reconsolidations warranted.
44. **Next-tool prediction** — predict the next tool type from the last few calls; score it · *Clark "whatever next?"* · *M:* next-tool top-1 accuracy.
45. **Precision-learned danger patterns** — each danger regex gets a learned precision from outcomes · *precision-weighting of alarms* · *M:* danger precision/recall.
46. **Expected-free-energy sign-off gate** — reality_monitor blocks by the claim's expected surprise, not a fixed regex list · *active inference* · *M:* block precision.
47. **The dream bets on itself** — "tonight's association X will prove durable," resolved next dream · *model evidence* · *M:* durability hit-rate of dream associations.
48. **Surprise-spike wake alarm** — flag "your model was unusually wrong lately" when session surprise > mean+2σ · *free-energy spike* · *M:* does it precede owner corrections.
49. **Judgment-of-learning at write time** — predict P(re-read within 14 days); coactivation resolves it · *Nelson-Narens metamemory* · *M:* JOL Brier.
50. **Prediction-error map by topic** — aggregate surprise by topic to show where the model is weakest · *precision-weighted error landscape* · *M:* the per-topic bits map.
51. **Kalman confidence tracker** — model each scope's hit-rate as a latent state with a 1-D Kalman update · *Kalman as normative learning (Kording)* · *M:* tracking error vs batch.
52. **Efference-copy cancellation of self-triggered ERN** — cancel the ERN's self-generated hits (the command IS the stimulus) · *efference copy (Sperry)* · *M:* false-alarm fraction drops.

### (4B) Decision-making, valuation & exploration
53. **UCB over curiosity gaps** — pull score = value + c·√(ln N/nᵢ) with a per-gap visit count · *UCB1 (Auer)* · *M:* durable yield UCB vs pull.
54. **Thompson-sampled dream focus** — sample each topic's "yields insight" Beta and take the argmax draw · *Thompson sampling* · *M:* insight yield vs round-robin.
55. **Value-of-information gate on VERIFY** — verify only when P(wrong)·cost − cost(verify) > 0 · *value of information (Howard)* · *M:* verify-rate vs realized error cost.
56. **Rational metareasoning on deliberation depth** — budget think/search time by expected value of computation · *Russell-Wefald; Lieder-Griffiths* · *M:* over/under-thought corrections.
57. **Drift-diffusion ASK/ACT accumulator** — accumulate evidence to a stakes-scaled bound, not a single-shot threshold · *Ratcliff DDM* · *M:* accuracy vs number of observations.
58. **Opportunity-cost ranking in the dream** — rank items by marginal insight vs the average reward rate · *Niv opportunity cost* · *M:* insight-per-minute trend.
59. **Marginal-value "leave the topic" signal** — signal "patch depleted" when instantaneous gain drops below the average rate · *Charnov MVT* · *M:* leave-signals vs productivity plateaus.
60. **Novelty bonus in replay** — a least-recently-replayed bonus so the whole substrate gets swept · *novelty-as-reward (Kakade-Dayan)* · *M:* replay coverage over a month.
61. **Directed vs random exploration modes** — UCB toward uncertainty vs softmax over the corpus; log which yields more · *Wilson-Cohen* · *M:* durable yield by mode.
62. **Calibration-tied exploration temperature** — exploit when Brier is low, explore when it degrades · *uncertainty-driven exploration* · *M:* adaptive vs fixed on yield.
63. **Model-based/model-free recall arbitration** — choose fok pointers vs deliberate grep by recent reliability · *Daw MB/MF* · *M:* accuracy per mode vs cost.
64. **Thompson-sampled reflex firing** — sample by each reflex's "prevented-a-mistake" posterior · *Thompson sampling* · *M:* reflex precision.
65. **Loss-averse stakes weighting** — weight losses ~2× gains vs the reference state (live book, clean repo) · *prospect theory* · *M:* irreversible-action incidents.
66. **Reference-dependent result reporting** — report a result against the owner's reference point, not zero · *reference dependence* · *M:* fewer "vs what?" corrections.
67. **Discount stale open questions** — rank by value·e^(−k·age) · *temporal discounting* · *M:* resolved-vs-abandoned ratio.
68. **Satisficing recall depth** — stop grepping once a "good enough" memory clears an aspiration level · *Simon satisficing* · *M:* time-to-answer vs quality.
69. **Explore/exploit slack budget** — a bandit allocates session slack between curiosity and the owner task · *Cohen-McClure-Yu* · *M:* durable yield per slack-minute.
70. **VoI ordering of which memory to read** — rank candidates by expected reduction in answer uncertainty · *Bayesian experimental design* · *M:* answers per memory read.
71. **Regret logging** — log a regret when a chosen action underperformed a visible alternative · *regret minimization* · *M:* cumulative-regret slope.
72. **Count-based bonus for lonely-salient nodes** — a "dreams ignored" count raises a skipped important isolated node · *count-based exploration* · *M:* time-to-first-link.
73. **Bandit over reflex trigger tightness** — learn tight vs loose to maximize (mistake-averted − false-fire) · *bandit optimization* · *M:* net reflex value per arm.
74. **Actor-critic breadcrumb** — weight resume_checkpoint toward RESUME content that gets used next session · *actor-critic in BG* · *M:* breadcrumb-use rate.
75. **Loss-matrix decision thresholds** — derive act/verify/ask bars from an explicit (FP-cost, FN-cost) matrix · *Bayes risk* · *M:* realized loss vs hand-set bars.
76. **Uncertainty-seeking for calibration** — place a few forecasts in the least-data scopes to buy calibration info · *active learning* · *M:* reduction in posterior width.
77. **Sunk-cost guard** — fire when a session keeps investing in a failing thread · *sunk-cost fallacy (Arkes-Blumer)* · *M:* abandonment latency on doomed threads.
78. **Tail probability-weighting correction** — a calibrated inverse-Prelec correction on p<0.1 / >0.9 · *prospect-theory weighting* · *M:* calibration in the tails.
79. **Foraging model for fleet attention** — patch-rich vs patch-depleted signals across research threads · *MVT / ideal free distribution* · *M:* insight-rate equalization.
80. **Effort-discounted verification** — pay a careful check only when value warrants · *effort discounting (Kool-Botvinick)* · *M:* block-rate trivial vs consequential.
81. **Pavlovian-instrumental transfer guard** — detect a charged cue pulling behavior faster than the policy warrants · *PIT (Dayan-Niv)* · *M:* fewer fast-action errors on charged cues.
82. **Win-stay/lose-shift baseline** — WSLS as a cheap default to benchmark UCB/Thompson against · *WSLS heuristic* · *M:* yield vs WSLS.
83. **Information-gap valuation of questions** — score by closeness-to-closure × how much it matters · *Loewenstein info-gap* · *M:* closure rate high-gap vs low.
84. **Durable-memory reward currency** — the brain's reward = durable-memory creation (re-read + linked within N days) · *reward shaping* · *M:* the durable-memory series.
85. **Softmax action selection** — replace hard act/verify/ask cutoffs with a softmax over expected utility · *Luce choice* · *M:* chosen-action vs realized best.
86. **Imminence-weighted preplay** — weight preplay items by how soon he'll need them · *hyperbolic discounting* · *M:* pre-stage hit-rate weighted by imminence.
87. **Curiosity as compression progress** — reward a gap by how much closing it shrinks description length · *Schmidhuber* · *M:* description-length delta *(box)*.
88. **Meta-decision: pursue now vs park for the dream** — value-of-computation now vs cheaper offline compute · *Lieder-Griffiths* · *M:* yield of now-vs-parked routing.
89. **Ambiguity premium** — distinguish risk (known p) from ambiguity (unknown p) with a learnable premium · *Ellsberg* · *M:* separate calibration known-p vs unknown-p.
90. **Tonic urgency from reward rate** — recent durable-output rate sets a tonic urgency (speed satisficing / encourage exploration) · *Niv tonic dopamine* · *M:* output-rate response.
91. **Explore/exploit tagging of /goal runs** — tag each run exploit vs explore and post-hoc score payoff · *bandit bookkeeping* · *M:* payoff by tag.
92. **Aspiration adaptation** — raise the "good enough" bar after wins, lower after a hard stretch · *Selten aspiration adaptation* · *M:* completion vs abandonment balance.
93. **Certainty-equivalent reporting** — convert an uncertain estimate to a stakes-appropriate risk-adjusted CE · *expected utility* · *M:* fewer over-optimistic corrections.
94. **Bandit over recall strategies** — learn which of fok/spread/grep/embeddings yields the used memory fastest · *strategy selection as bandit* · *M:* used-memory latency by strategy.
95. **Peak-end audit of session summaries** — correct the resume summary's over-weight of the last event · *Kahneman peak-end* · *M:* summary emphasis vs time-weighted activity.
96. **Cost-of-delay on danger ASK** — weigh expected loss from acting against the owner's interruption cost · *VoI vs query cost* · *M:* "why ask me this" vs "good you asked."
97. **Thompson-sampled parameter A/B** — TAU, KAPPA, DEDUP get A/B arms whose brain-quality reward is sampled in the dream · *Thompson hyperparameter search* · *M:* reward per arm *(box sweep)*.
98. **Marginal-utility cutoff for reflex loading** — load reflexes until the next one's marginal value < the context cost · *marginal utility* · *M:* fire-usefulness loaded vs dropped.
99. **Risk-sensitive control under a losing streak** — after a run of ERNs, prefer reversible / verify more · *risk-sensitive RL* · *M:* post-losing-streak error rate.
100. **Expected-free-energy master rule** — rank next actions by pragmatic (goal) + epistemic (info-gain) value · *Friston expected free energy* · *M:* combined yield vs the split heuristics.

## Domain 5 — Attention, working memory & executive control

### (5A) Attention & working memory (the wake spotlight is bloated — Cowan ~4)
1. **Cap the wake spotlight at 4 chunks** — emit ≤4 labeled chunks; demote the other ~11 blocks to an expand-pointer · *Cowan ~4* · *M:* injected block/token count; A/B first-action relevance.
2. **Salience-gate the soft wake blocks** — render conscience/curiosity/state blocks only when content clears a salience/freshness bar · *salience-network gating* · *M:* per-block fire-rate; tokens saved.
3. **Collapse the identity preamble to one line** — chunk 6 orientation bullets into one line + pointers · *chunking (Miller)* · *M:* token delta; humanness regression.
4. **One focus line at the top** — resume_checkpoint computes the single most important next thing; wake prints it first, alone · *attentional spotlight (Posner)* · *M:* first-action addresses it.
5. **Progressive disclosure of wake blocks** — titled pointers opened on demand · *load theory (Lavie)* · *M:* injected tokens; pointer open-rate.
6. **Serial-position ordering of wake** — identity first, biggest risk last, soft middle compressed · *serial-position curve* · *M:* recall probe on cited items.
7. **Von Restorff isolate the top risk** — one distinctive ⚠ line, rest uniform · *isolation effect* · *M:* A/B whether the risk is acted on.
8. **Change-only wake diff** — show only what changed since last wake · *change blindness* · *M:* tokens saved vs full-block.
9. **Zoom-lens salience exposure** — SALIENCE top-5 + a zoom pointer, not implicit top-40 · *Eriksen zoom-lens* · *M:* token delta; right-scar reach.
10. **Global-workspace arbiter for prompt hooks** — 5 hooks write candidates to a buffer; broadcast only top-1 · *global-workspace ignition (Dehaene)* · *M:* injections/prompt; kept-signal quality.
11. **Basal-ganglia gate on WM updates** — a prompt-hook signal injects only past a task-relevance gate · *PBWM (O'Reilly-Frank)* · *M:* fire-rate drop; false-suppression.
12. **Attentional-blink spacing between hooks** — if fok fires, spread stays silent this prompt · *attentional blink* · *M:* co-fire rate; suppressed-item relevance.
13. **Cross-hook inhibition of return** — no memory/reflex re-injected within N turns of its last surfacing · *IOR (Posner-Cohen)* · *M:* repeat-injection rate.
14. **Bottom-up injection cap** — ≤1 bottom-up (salience-driven) injection per prompt · *top-down vs bottom-up* · *M:* bottom-up count/prompt.
15. **Danger attentional capture** — danger tier≥2 suppresses the other four injections · *threat capture (amygdala)* · *M:* co-injection with danger.
16. **Load-theory throttle on associations** — under high load, suppress spread_recall · *Lavie load theory* · *M:* fire-rate vs prompt complexity.
17. **Cocktail-party channel filter** — filter wake/FOK to the current project channel via RESUME keywords · *selective attention (Cherry)* · *M:* on-channel fraction.
18. **Bind memory+scar+reflex into one chunk** — a shared renderer emits them as one unit, not three · *feature integration (Treisman)* · *M:* lines per injection.
19. **Goal-refresh heartbeat** — re-inject the one-line goal every ~6 turns · *attentional refreshing (Barrouillet)* · *M:* goal-citation deep in long sessions.
20. **Event-boundary flush** — a fresh directive overwrites stale RESUME and clears dedup state · *event segmentation (Radvansky-Zacks)* · *M:* stale-context carryover.
21. **MEMORY.md as ~7 labeled chunks** — group index lines under ~7 headed sections · *chunking / 7±2* · *M:* retrieval-hit A/B.
22. **Chunk-budget compaction** — change the byte CAP to a chunk/entry budget · *Cowan capacity* · *M:* entries kept; overflow rate.
23. **Directed-forgetting tag** — a `forget:true` flag salience honors by flooring · *directed forgetting (Bjork)* · *M:* count tagged; salience delta.
24. **Sternberg-bounded FOK report** — cap the reported cluster; report the count · *Sternberg scanning* · *M:* report-length variance.
25. **Proactive-interference collapse in "newest"** — several same-day near-dupes → one representative + "(+k similar)" · *proactive interference* · *M:* duplicate-line rate.
26. **Gate PostToolUse spread_recall** — fire only on a deliberate memory-dir Read · *attentional capture / IOR* · *M:* PostToolUse fire-rate.
27. **Attentional-set priming file** — write the task's distinctive terms to task_set.json; fok/spread bias matches · *attentional set (Folk-Remington)* · *M:* on-task hit precision.
28. **Vigilance-decrement countermeasure** — vary the re-focus line over long sessions · *vigilance decrement (Mackworth)* · *M:* on-task rate late vs early.
29. **Wake token-budget meter** — sum injected chars, enforce a ceiling by trimming lowest-salience · *WM as a hard budget* · *M:* wake token count over time.
30. **Decay clock on surfaced intentions** — an unrefreshed intention fades from wake after M days · *rehearsal-maintenance/decay* · *M:* wake intention count.
31. **Distinctiveness-preserving trim** — the 280-char trim keeps the distinctive term over filler · *Von Restorff* · *M:* retrieval A/B; legibility.
32. **Two-stream wake: stable vs volatile** — separate never-changing anchors from today's state · *selective attention to change* · *M:* which stream cited first.
33. **Cue not content for salience** — a single cued number ("3 hot scars; open SALIENCE.md") · *cue-based access* · *M:* tokens; pointer open-rate.
34. **Coactivation-driven chunking** — surface two strongly-linked memories as one bound chunk · *chunking by association* · *M:* chunk-vs-singleton ratio.
35. **Novelty capture budget** — curiosity/surprise items enter wake only via one reserved slot · *oddball/novelty capture (P3a)* · *M:* curiosity-line count.
36. **Primacy-protected hard rules** — the standing rules always occupy the first wake slot · *primacy* · *M:* rule-recall probe.
37. **Task-filter the gut list** — the 6 wake reflexes filtered to the current task channel · *selective attention* · *M:* reflex relevance to the session.
38. **Interleave high-charge scars** — avoid adjacent high-arousal scars so the second registers · *attentional blink / arousal* · *M:* recall of the 2nd scar.
39. **WM-slot accounting for reflexes** — formalize MAX_FIRE=3 as slots; expose remaining so danger can claim one · *Cowan slots* · *M:* over-fire incidents.
40. **Overwrite-protection for the focus line** — a sharp topic change replaces, not appends · *overwriting in WM* · *M:* stale-focus incidents.
41. **Diffuse dream, focused wake** — dream may range wide, waking injectors stay narrow · *focus vs diffuse mode* · *M:* injection breadth by mode.
42. **Latent-until-cued unconscious tier** — exclude unconscious-tier memories from ALL wake injection · *conscious/unconscious access* · *M:* unconscious leakage into wake.
43. **Perceptual vs cognitive load split** — trim text hard when task difficulty is high · *load-theory dissociation* · *M:* tokens vs task-difficulty.
44. **Attention-lapse self-report** — a Stop hook flags turns that never touched injected context · *attentional lapse* · *M:* ignored-injection rate per hook.
45. **Recency×salience trim of "newest"** — weight the 10 newest by salience so a flat log can't take a scar's slot · *recency×salience access* · *M:* slot allocation.
46. **Single reconciled danger/work line** — merge "keep going" and "slow down" into one line · *selective attention / binding* · *M:* contradictory co-injection count.
47. **Spotlight handoff on compaction** — on a detected reset, wake leads with RESUME · *task resumption (Altmann-Trafton)* · *M:* post-compaction first-action correctness.
48. **Hard per-prompt token ceiling** — drop the lowest-priority injection when over · *WM capacity* · *M:* ceiling adherence.
49. **Conjunction-cue FOK** — require 2+ distinctive-word overlap, not any single common word · *feature-integration (Treisman)* · *M:* FOK precision on a labeled set.
50. **Unified attentional-dwell dedup** — merge the .*_fired windows into one "recently attended" store · *unified dwell / IOR* · *M:* same-topic multi-hook co-fires.

### (5B) Executive function & cognitive control
51. **Explicit goal stack** — goal_stack.json (current, parent, subgoals); wake shows the top · *hierarchical task representation (Botvinick)* · *M:* stack-top matches actual work.
52. **Goal-neglect drift guard** — a Stop check nudges back if actions diverge from task keywords for K turns · *goal neglect (Duncan)* · *M:* drift episodes caught.
53. **ACC conflict arbiter** — detect two conflicting injected signals; flag "conflict → slow, resolve" · *conflict monitoring (Botvinick)* · *M:* conflict incidents; downstream error.
54. **Conflict-scaled effort** — high conflict raises verify depth for that turn · *conflict-control loop* · *M:* verify-step rate under conflict.
55. **True stop-signal before irreversible acts** — require a pre-mortem token before rm -rf/force-push · *SSRT (Logan)* · *M:* irreversible acts without a pre-mortem.
56. **Prepotent-response override for "done"** — reality_monitor interrupts the habitual unverified "done" · *Stroop prepotent override* · *M:* unverified-claim block rate.
57. **Post-error slowing state** — an ERN sets a short-lived "verify before next claim" state · *post-error slowing (Rabbitt)* · *M:* next-action error rate after an error.
58. **Compile foresight if-thens into reflexes** — top if-thens written as instincts.tsv triggers · *implementation intentions (Gollwitzer)* · *M:* reflex fires on the anticipated cue.
59. **Cue-triggered intention offloading** — surface a specific intention when its cue appears, not on a time scan · *event-based PM (Einstein-McDaniel)* · *M:* intentions fulfilled vs dropped.
60. **Close-the-loop detection** — a Stop hook checks off a completed open intention · *Zeigarnik / completion* · *M:* open-intention staleness; false-close.
61. **Event- vs time-based PM cues** — tag each intention event-cued or time-cued · *PM taxonomy* · *M:* cue-hit precision.
62. **Prospective deadline aging** — intentions escalate salience as the date nears, then mark overdue · *PM urgency/decay* · *M:* overdue-without-surfacing.
63. **Proactive preload by stakes** — high-stakes upcoming work preloads relevant scars before the action · *Braver DMC proactive* · *M:* preload-before-act rate.
64. **Reactive default for low stakes** — suppress proactive injection for low-stakes; rely on reactive triggers · *Braver DMC reactive* · *M:* injection volume low vs high stakes.
65. **Switch-cost batching** — consecutive same-task prompts suppress re-priming · *task-switching cost (Rogers-Monsell)* · *M:* re-prime rate on same-task runs.
66. **Switch detection + re-orient** — inject a brief re-orient only when the task hash changes · *task-set reconfiguration (Monsell)* · *M:* switches vs re-orients.
67. **Rabbit-hole meta-awareness probe** — many same-tool actions with no progress → "still on the goal?" · *meta-awareness (Schooler)* · *M:* rabbit-hole episodes flagged.
68. **Off-goal self-interrupt budget** — cap how long the session may go off the stated goal · *goal shielding* · *M:* max off-goal streak.
69. **Shield vs shift toggle** — mark the goal shield vs shift-ok; gate spread/curiosity accordingly · *shielding-shifting (Goschke)* · *M:* distractor-injection while shielding.
70. **Supervisory override on repeat failure** — the same failure signature repeating escalates to "stop and rethink" · *Norman-Shallice SAS* · *M:* repeat-failure loops broken.
71. **Wire monitoring→control** — reality_monitor's claim detection routes into control_policy.decide() · *Nelson-Narens* · *M:* high-stakes unverified claims routed.
72. **Confidence-calibrated abstention at sign-off** — a Stop hook appends ACT/VERIFY/ASK/ABSTAIN on the central claim · *Koriat-Goldsmith* · *M:* abstention vs later corrections.
73. **Force a plan file for complex tasks** — require a short subgoal plan before multi-step work · *cognitive offloading (Risko-Gilbert)* · *M:* plan-before-complex rate; quality.
74. **Open-loop load meter → shed** — many open loops → shed optional injected content · *cognitive-load management* · *M:* injected volume vs open-loop count.
75. **Old-goal interference control** — a new goal down-weights the previous goal's primed terms · *proactive interference* · *M:* old-goal term bleed.
76. **Dual-task serialize guard** — a prompt asking two things at once → prompt to serialize · *central bottleneck (Pashler)* · *M:* multi-goal prompts flagged.
77. **PM-load cap** — cap surfaced open intentions; overflow to a "later" list · *PM capacity limits* · *M:* surfaced-intention count.
78. **Response inhibition on the confident number** — FONK makes "inhibit the guess; verify" an explicit control act · *response inhibition (Logan)* · *M:* unverified-number assertion rate.
79. **Audit learned reflexes for payoff** — measure whether learned reflexes fire and prevent repeats; prune non-helpers · *ERN reinforcement (Holroyd-Coles)* · *M:* fire→prevention rate.
80. **Hierarchical resume** — resume shows goal→subgoal→next step, not just "last turn" · *rostro-caudal control (Koechlin-Badre)* · *M:* resume correctness on multi-step.
81. **Shared task-set representation** — one task_set.json (goal, terms, stakes, shield/shift) read by all control hooks · *frontoparietal control network* · *M:* cross-hook consistency.
82. **Conflict-seeded dream topic** — a recurring conscience DEBT pattern seeds the next dream · *conflict → adaptation* · *M:* pattern recurrence after a targeted dream.
83. **In-turn stop-signal window** — irreversible ops require the pre-mortem within the same turn · *stop-signal timing* · *M:* irreversible ops with in-turn pre-mortem.
84. **Countermand stale clocks on topic change** — an unrelated new task cancels the running work clock · *countermanding* · *M:* stale-clock block incidents.
85. **Proactive slip-prevention preload** — a recurring correction is surfaced AND turned into a provisional guard · *prospective gap-detection* · *M:* unguarded corrections that still recur.
86. **Interrupt at boundaries only** — hold spread_recall until a natural boundary, not mid-sequence · *interruption cost (Altmann-Trafton)* · *M:* mid-sequence injection rate.
87. **Highest-value-action meta-check** — periodically ask "is this the highest-value action for the goal?" · *meta-awareness (Schooler-Smallwood)* · *M:* low-value-action streaks.
88. **Task-relevance gating of instincts** — add a task-relevance term to instinct ranking · *goal-relevant selection* · *M:* off-task reflex fire rate.
89. **Zeigarnik-weighted wake intentions** — the most-interrupted open loop leads the surfaced intentions · *Zeigarnik* · *M:* resumption rate of the top loop.
90. **Set-shift thrash log** — record task switches; the dream flags high-thrash sessions · *set-shifting / perseveration (WCST)* · *M:* switches/session trend.
91. **Release unused preloads** — drop a preloaded if-then that never matches by mid-session · *proactive-control cost* · *M:* unused-preload dwell time.
92. **"When X, remind me" registration** — register an event-cued reminder into INTENTIONS with a trigger regex · *implementation intentions* · *M:* reminder fires on cue.
93. **Cognitive-control budget at wake** — cap the danger/conscience/foresight nudges at ~2 · *control-resource limits* · *M:* control-line count.
94. **Error-history-aware stakes bump** — a domain with recent failures gets a temporarily higher stakes bar · *ACC error → control* · *M:* bar adjustment vs recent errors.
95. **Goal (not just trace) in RESUME** — write the ACTIVE goal every Stop so compaction preserves it · *goal maintenance across interruption* · *M:* post-compaction goal retention.
96. **Purge completed-goal residue** — a done goal removes its primed terms and intentions · *goal deactivation / negative priming* · *M:* done-goal term persistence.
97. **Two-level metamemory line** — combine fok (knowing) + control_policy (what to do) into one line · *Nelson-Narens two-level* · *M:* appropriateness of the recommended action.
98. **Name-the-failure-mode before "deployed"** — the deploy branch requires naming one failure mode first · *override of habitual completion* · *M:* deploy-claim → failure-mode-named rate.
99. **Chunk-and-defer multi-ask prompts** — enumerate a many-sub-ask prompt as a checklist, one at a time · *chunking + serial execution* · *M:* multi-ask prompts handled serially.
100. **Executive-control dashboard** — brain_status reports injection volume/prompt, conflict rate, goal-drift catches, unverified-claim blocks · *executive-function assessment* · *M:* it is the measurement surface for 51-99.

## Domain 6 — Metacognition, confidence, emotion & salience

### (6A) Metacognition, confidence & calibration
1. **Unified accessibility signal** — accessibility.py → one accessibility.tsv fok/salience/consciousness all read · *Koriat accessibility + Nelson-Narens* · *M:* AUROC predicting next co-read vs each component.
2. **Wire control_policy into a live hook** — a Stop/PreToolUse hook emits ACT/VERIFY/ASK/ABSTAIN, logs to control_log.jsonl · *Nelson-Narens control* · *M:* high-stakes claims routed to VERIFY/ASK.
3. **meta-d' / M-ratio per scope** — add type-2 sensitivity (meta-d' vs d') to predict.py · *Fleming meta-d'* · *M:* the M-ratio.
4. **Type-2 ROC area** — does stated confidence separate hits from misses (AUROC2) · *Galvin type-2 ROC* · *M:* AUROC2 per scope.
5. **Binned ECE + reliability diagram** — replace the single mean-gap with binned ECE + a printed curve · *calibration* · *M:* ECE.
6. **Murphy Brier decomposition** — split Brier into reliability − resolution + uncertainty · *Murphy 1973* · *M:* the three components.
7. **Hard-easy effect split** — bucket by difficulty; report bias sign per bucket · *Lichtenstein-Fischhoff* · *M:* bias gap by difficulty.
8. **Post-error slowing state** — an ERN writes .post_error.json raising the bar for N calls · *Danielmeier-Ullsperger* · *M:* error rate in the N calls after.
9. **Error-awareness rate** — caught (ERN) vs later-discovered (MISTAKES) errors · *error awareness (Steinhauser-Yeung)* · *M:* caught/(caught+later).
10. **Illusion-of-depth probe** — a "because X drives Y" claim prompts one deeper mechanistic step · *IOED (Rozenblit-Keil)* · *M:* mechanism-claim corrections over time.
11. **Unknown-unknowns map** — clusters the brain asserts in but has zero predictions/outcomes for · *metacognitive blind spots* · *M:* assert-heavy/monitor-zero domains.
12. **Resolution-gated reconsolidation** — a contradiction from a low-resolution domain counts as lower surprise · *resolution vs calibration (Yates)* · *M:* update rate low- vs high-resolution.
13. **Second-order uncertainty (posterior width)** — surface the Beta credible interval on each scope's rate · *second-order uncertainty* · *M:* CI width per scope.
14. **FOK gamma resolution** — log FOK fires, join to whether recall helped, compute Goodman-Kruskal gamma · *Koriat/Nelson FOK accuracy* · *M:* FOK-vs-retrieval gamma.
15. **JOL at memory write** — predict future retrieval from salience+links; score later · *Koriat JOL* · *M:* JOL vs actual co-read AUROC.
16. **Real base-rate table for the outside view** — build duration/failure base rates so the reflex cites numbers · *outside view (Kahneman-Lovallo)* · *M:* estimate error vs base rate.
17. **Isotonic recalibration curve** — fit stated→empirical confidence; control_policy applies a map, not a scalar · *probability recalibration* · *M:* post-recalibration ECE *(box)*.
18. **Monitor→control follow-through** — how often a VERIFY/ASK was followed by a verifying tool call · *Nelson-Narens efficacy* · *M:* follow-through fraction.
19. **Dunning-Kruger quadrant check** — cross competence with mean asserted confidence; flag low-skill/high-confidence · *Kruger-Dunning* · *M:* quadrant membership.
20. **Surprise→salience via scored predictions** — a high-surprise resolved bet bumps its source memory's salience · *Rescorla-Wagner; Schultz* · *M:* later co-read of high-surprise vs controls.
21. **Calibration drift tracker** — per-session ECE/Brier history + slope · *calibration learning* · *M:* slope of ECE over sessions.
22. **Stakes-scaled corroboration in reconsolidate** — high-stakes contradicted beliefs demand more corroboration · *Koriat-Goldsmith* · *M:* corroboration vs stakes.
23. **Abstention-rate budget** — too few abstentions in a high-uncertainty stretch = overclaiming · *accuracy-informativeness tradeoff* · *M:* abstention rate vs measured uncertainty.
24. **Resolution-targeted curiosity gap** — a gap kind for low-resolution (confident but non-discriminating) scopes · *metacognitive resolution* · *M:* AUROC2 improvement.
25. **Domain-overconfidence tightens reality-monitor** — a measured-overconfident domain lowers the block threshold · *domain overconfidence (Fischhoff)* · *M:* blocks concentrated in overconfident domains.
26. **NL confidence calibration set** — log the turn's confidence words + outcome · *confidence-accuracy dissociation* · *M:* calibration of NL-confidence.
27. **Ease-of-learning ranking for dangling links** — rank dangling links by closability · *Koriat EOL* · *M:* closure rate top-ranked vs rest.
28. **Metacognitive-sensitivity regression gate** — humility_check asserts meta-d'/AUROC2 hasn't collapsed · *metacog sensitivity* · *M:* pass/fail vs a floor.
29. **Feeling vs reasoned confidence gap** — flag large divergence between fast FOK and deliberate control_policy · *Koriat dual bases* · *M:* gap vs which matched the outcome.
30. **Calibration by provenance** — join sourced_memory tags (measured/sim/assumed) with later corrections · *source × calibration* · *M:* correction rate by provenance.
31. **Interval-not-point reflex** — nudge a range/CI on quantitative claims in low-n domains · *overprecision (Moore-Healy)* · *M:* ranges present in those domains.
32. **Overprecision vs overestimation split** — separate too-narrow ranges from too-high means · *Moore-Healy* · *M:* the two metrics separately.
33. **Recurrence-scaled error charge** — recurred ERN reflexes get higher charge · *ERN amplitude scales with significance* · *M:* reflex charge vs recurrence.
34. **Worst-calibrated-domain in the wake brief** — foresight surfaces "distrust your confidence here today" · *domain overconfidence* · *M:* subsequent claims hedged/verified.
35. **Overdue-bet flag** — flag predictions past their `by:` date · *only resolved dated bets teach* · *M:* overdue-unresolved count.
36. **Prediction-logging nudge at claim time** — a falsifiable "this will…" nudges appending to PREDICTIONS with p + by-date · *making the brain forecast* · *M:* bets logged/week.
37. **Correlated-confidence penalty** — wire false_independence into control_policy as a discount when "agents agree" · *Noise (Kahneman)* · *M:* agreement-driven claims later corrected.
38. **Anchoring audit on estimates** — flag a numeric estimate matching a number in recent context · *anchoring (Tversky-Kahneman)* · *M:* corrections among flagged.
39. **"What did you check" on done-claims** — reality_monitor requires a one-line mechanism/observation · *IOED + source monitoring* · *M:* ungrounded-claim rate after.
40. **Quality-weighted FOK count** — weight the pointer count by whether those pointers historically helped · *Koriat accessibility (quality)* · *M:* FOK→help gamma.
41. **Cheapest-disconfirmer on VERIFY** — a VERIFY auto-surfaces "run the check that returns nothing if you're wrong" · *positive-test bias (Wason)* · *M:* disconfirming checks per VERIFY.
42. **Danger-mode signal-detection calibration** — log firings + whether the action went wrong; recalibrate · *signal-detection on the alarm* · *M:* danger precision/recall.
43. **Alarm-calibration from ERN false-alarms** — a running cry-wolf meter on the error-monitor · *error-monitoring calibration* · *M:* false-alarm trend.
44. **Retrospective calibration in the conscience** — a per-session self-rating of claim-tracking · *retrospective metacognition* · *M:* predicted-good vs actually-held.
45. **Empirical reflex validity** — score the hand-set validity field from fired→actually-dangerous logs · *cue validity* · *M:* fired-vs-real-danger per reflex.
46. **Base-rate-first estimate guard** — surface the corpus base rate before a probability estimate · *base-rate neglect* · *M:* estimate distance from base rate.
47. **Calibration split by stakes (choking)** — Brier by stakes bucket · *stakes × calibration* · *M:* Brier by bucket.
48. **IOED ledger** — track "I understand X" claims that later needed a mechanism correction · *IOED* · *M:* count and recurrence.
49. **Absolute-word linter in low-calibration domains** — flag "definitely/obviously" where the domain is overconfident · *verbal overconfidence* · *M:* absolute-word rate vs outcome.
50. **Resolution, not just bias, at wake** — surface "your confidence doesn't track accuracy here" (low AUROC2) · *resolution vs calibration (Yates)* · *M:* AUROC2 surfaced; behavior change.

### (6B) Emotion, affect & salience
51. **Split valence and arousal** — add an AROUSAL column beside valence (charge is one scalar today) · *Russell circumplex* · *M:* does 2D predict persistence/co-read better (AUROC).
52. **Tonic vs phasic affect** — separate chronic STAKES from acute event arousal (only phasic drives consolidation) · *McGaugh* · *M:* phasic-only vs combined predicting reactivation.
53. **Dose-response affective replay** — scale replayed scars/wins to the night's total charge · *McGaugh dose-response* · *M:* replay count vs charge; recall.
54. **Peak-end session summary** — store the PEAK and END affect as the session's remembered valence · *Kahneman peak-end* · *M:* peak+end vs mean predicting recall.
55. **Somatic marker before risky acts** — danger_mode surfaces the bodily sting of the nearest scar · *Damasio somatic-marker* · *M:* scars cited on risky acts; error rate.
56. **Mood-congruent recall tilt** — a cautious/negative mood surfaces scars first · *Bower* · *M:* recall tilt vs mood; heeding.
57. **Core-affect state file (producer)** — compute (valence, arousal) from interoceptive signals into STATE_OF_MIND · *Craig / Russell* · *M:* state vs subsequent behavior.
58. **Affect-as-information → processing style** — negative mood raises the verify bar, positive relaxes it · *Schwarz-Clore* · *M:* verify-bar vs mood; error rate.
59. **Yerkes-Dodson injection cap** — cap simultaneous danger/instinct/ERN injections at an inverted-U optimum · *Yerkes-Dodson* · *M:* injection count vs heeding (find the peak).
60. **Reappraisal of mastered scars** — a reflex-covered scar's charge is down-regulated · *reappraisal (Gross)* · *M:* covered-scar charge over time; gut still fires.
61. **Flashbulb tier** — max-charge + irreversible + real-consequence events get persistence 1.0 · *flashbulb memory (Brown-Kulik)* · *M:* these stay top-vivid regardless of age.
62. **VAD-norm affect scoring** — augment the FEAR/JOY regex with Warriner valence-arousal-dominance norms · *dimensional affect norms* · *M:* agreement with a labeled set *(box)*.
63. **Interoceptive unease accumulator** — a "gut discomfort" scalar rising with conscience debt/open scars, surfaced at wake · *Craig interoception* · *M:* unease vs count of pending problems.
64. **Mood-dependent encoding tag** — tag each new memory with the session's core affect · *Bower mood-dependent* · *M:* state-match retrieval advantage.
65. **Amygdala-gated consolidation order** — the dream digests emotionally-tagged recent memories first · *McGaugh* · *M:* charge-ordered vs recency-ordered completeness.
66. **Peak-end weighting of owner corrections** — weight MISTAKES by peak sting + recency · *peak-end* · *M:* which surface vs their recurrence.
67. **Emotional-granularity labels** — tag discrete affect (fear/pride/frustration/relief), not just valence sign · *emotional granularity (Barrett)* · *M:* label agreement on a sample.
68. **Stress inverted-U defers heavy work** — a high-stress stretch defers non-critical writes/reasoning · *cortisol inverted-U* · *M:* error rate in high-stress windows.
69. **Positive affect broadens curiosity** — positive mood widens exploration, negative narrows to the pressing gap · *Fredrickson broaden-and-build* · *M:* exploration breadth vs mood.
70. **Arousal-resistant reconsolidation** — high-arousal memories resist destabilization more · *Nader* · *M:* corroboration required vs arousal.
71. **Somatic-marker validity log** — record when a sting preceded a decision and whether it was good · *Damasio (Iowa gambling)* · *M:* decision quality with vs without a sting.
72. **Mood repair (anti-rumination)** — a stuck-negative mood surfaces a reappraisal prompt, not more scars · *mood repair* · *M:* mood trajectory across sessions.
73. **Affect-congruent bisociation seeds** — pair two high-arousal memories from different domains · *affect as a binding cue (Bower)* · *M:* landed-analogy rate vs random.
74. **Positive-reflex (pride) set from wins** — distil JOY-charged wins into guide reflexes · *positive somatic markers* · *M:* guide firings; outcomes.
75. **Arousal-scaled dedup windows** — a max-burn reflex re-surfaces sooner · *arousal → attentional persistence* · *M:* re-fire interval vs charge.
76. **Detect owner affect in the prompt** — sense frustration/urgency in wording; raise care · *social appraisal / empathy* · *M:* detection vs actual corrections that turn.
77. **The peaks section in SALIENCE.md** — a "peaks" block (highest-charge regardless of recency) · *peak-end* · *M:* whether peaks get reached-for.
78. **Synaptic-homeostasis downscale** — a global charge-proportional downscale so flat memories fade, charged stand out · *SHY* · *M:* charged-vs-flat contrast over time.
79. **Prediction-error → transient arousal** — a surprising miss spikes short-lived arousal heightening the next turn's care · *interoceptive predictive coding (Seth)* · *M:* care/verify rate after a surprising miss.
80. **Arousal-first wake ordering** — order scars/wins by arousal · *arousal → prioritization (Mather-Sutherland)* · *M:* arousal of lead items; reach-for.
81. **Arousal-biased competition** — under danger, narrow recall to the highest-priority memories · *Mather-Sutherland* · *M:* recall-set size under arousal vs calm.
82. **Reconsolidate only when calm** — schedule labile edits when the mood is calm · *stress impairs reconsolidation* · *M:* post-edit whiplash rate.
83. **Valence-balanced replay** — enforce a scar:win ratio in replay · *negativity bias (Baumeister)* · *M:* replay balance; net-valence trend.
84. **Flashbulb context capture** — a max-charge live event prompts capturing rich context · *flashbulb context encoding* · *M:* detail completeness vs ordinary.
85. **Somatic pre-veto in control_policy** — a high enough sting biases toward ASK even when the number clears the bar · *Damasio* · *M:* gut-veto rate; outcome vetoed vs allowed.
86. **Affective forecasting / impact-bias check** — predicted regret vs actual felt sting later · *Gilbert-Wilson* · *M:* predicted vs actual sting gap.
87. **Emotion-labeled conscience debts** — debts carry an affect (shame/frustration) scaling how hard the wake feels them · *moral emotions* · *M:* debt affect vs whether it recurs.
88. **Mood-modulated REM creativity** — bisociation aggressiveness scales with positive mood · *Isen* · *M:* remote-pair acceptance vs mood.
89. **Differential arousal/valence decay** — decay event arousal faster than valence · *fading-affect bias (Walker)* · *M:* fitted arousal vs valence slopes.
90. **Net-valence self-image check** — measure whether the corpus/wake skews net-negative and rebalance · *negativity bias / self-compassion* · *M:* corpus net valence at wake.
91. **Fatigue/load meter → clean stop** — high load prefers checkpoint-and-stop over push · *interoception of fatigue* · *M:* load vs late-session error rate.
92. **Mood-congruent reflex ordering** — tilt instinct ranking toward reflexes matching the affective context · *mood-congruent retrieval* · *M:* which reflex leads vs mood.
93. **Cross-session synaptic tag-and-capture** — a dull memory co-read with a charged one in the same dream is captured · *Moncada-Viola* · *M:* retrieval of dream-captured.
94. **Emotional context reinstatement at wake** — the wake restates the last session's affect for state-matched recall · *Tulving; Bower* · *M:* resumed-thread hit rate.
95. **Survival-rule affect class** — the load-bearing hard rules get high arousal so they FIRE, not just stay vivid · *emotional tagging of survival info* · *M:* both top-vivid and reflex-active.
96. **Affect-driven encoding selection** — a Stop hook nudges writing the session's most-charged moment · *Easterbrook cue-utilization* · *M:* charged-moment capture rate.
97. **Mood-volatility flag** — rapid valence swings within a session predict error · *affective instability* · *M:* volatility vs error rate.
98. **Positive RPE reinforces the method** — a hedged bet that hits big bumps the associated guide-reflex charge · *Schultz dopamine RPE* · *M:* guide-charge vs positive surprise; reuse.
99. **Graded-gut regression test** — humility_check asserts the charge/sting grading still orders reflexes right · *graded amygdala response* · *M:* ordering-correctness pass/fail.
100. **Capstone: one limbic-tagged accessibility vector** — fold valence/arousal into the unified accessibility score so all recall faculties consume one affect-tagged signal · *Craig + Koriat* · *M:* the unified vector predicting co-read/heeding (AUROC).

## Domain 7 — Instinct, habit & neuromodulation

### (7A) Instinct, habit & procedural memory
1. **Durable instinct-fire log** — instinct_prime appends every fire to fired.jsonl {ts,session,id,event,snippet} · *procedural traces need a record* · *M:* per-reflex fire counts (substrate for all below).
2. **Mindware-override audit** — override_audit.py: a reflex fired yet the mistake still landed · *Stanovich mindware-override* · *M:* (fired, recurred) pairs per reflex (the named gap).
3. **Correction-wording trigger field** — a `recur_trigger` col matching the correction TEXT, distinct from the action trigger · *action-wording ≠ correction-wording* · *M:* links a fired reflex to the later correction.
4. **Per-reflex precision + auto-demote** — score fires-vs-guarded-mistakes; high-fire/low-guard → fire→carry · *cry-wolf / signal-detection* · *M:* false-alarm rate per reflex.
5. **Self-tuning match threshold** — require a trigger-hit count scaled by measured precision · *signal-detection criterion* · *M:* fire-precision before/after.
6. **Habituation/sensitization hysteresis** — after a correct fire lower threshold, after a false alarm raise it · *Groves-Thompson dual-process* · *M:* threshold trajectory vs outcomes.
7. **Action-sequence chunk miner** — mine recurring tool-call sequences into procedures.tsv · *Graybiel action chunking* · *M:* chunks recurring ≥N.
8. **Task-bracket pre-load + completion check** — a chunk's first step surfaces the whole known-good sequence; verify at Stop · *Graybiel task-bracketing* · *M:* chunk completion rate.
9. **Chunk-boundary caution** — prime extra care at the transition step where failures cluster · *errors at chunk boundaries* · *M:* failure position within sequences.
10. **Motor-program command templates** — parameterized known-good commands surfaced by the matching reflex · *motor programs / CPG* · *M:* template reuse rate.
11. **Declarative→procedural compiler** — a memory read repeatedly in the same context is compiled to a reflex · *ACT-R declarative→procedural* · *M:* read-frequency drop post-compile.
12. **Power-law practice curve** — track tool-calls/time per recurring task-type; flag ones still slow · *power law of practice (Newell-Rosenbloom)* · *M:* calls-per-task-type trend.
13. **Recurrence-scaled habit strength** — a reflex's charge ∝ log(distinct recurrences) · *automaticity accrues with practice* · *M:* strength vs recurrence.
14. **Sub-goal / fast-fact caching** — cache resolved recurring sub-answers as instant facts · *ACT-R sub-goal caching* · *M:* re-derivation avoided (the "2028-06" slip).
15. **Production composition of co-firing rules** — merge two always-together reflexes into one · *ACT-R production compilation* · *M:* injections/session down, coverage held.
16. **RPD action scripts on guide reflexes** — attach the concrete first move that worked · *Klein recognition-primed decision* · *M:* was the surfaced move used.
17. **Guide-reflex outcome tracking** — log a guide fire + whether the session succeeded · *reinforcement of good moves* · *M:* success rate after a guide fire.
18. **Implementation-intention compiler** — turn INTENTIONS prose into if-then reflexes with real triggers · *Gollwitzer* · *M:* intention fired vs merely surfaced.
19. **Pre-arm foresight HOLD-THESE** — load the day's top triggers into an "armed" set at wake · *Gollwitzer pre-commitment* · *M:* armed reflexes' fire latency.
20. **Devaluation sweep** — flag reflexes whose guarded world-state is now stale (AWS→Azure) for extinction · *outcome devaluation (Dickinson-Adams)* · *M:* stale reflexes flagged.
21. **Extinction, not deletion** — retire a reflex via a `retired_context` field; it fires only in the old context · *Bouton (extinction ≠ erasure)* · *M:* retired-reflex re-fire rate.
22. **Contextual-renewal boost** — caution reflexes fire harder right after a context switch (new box, deploy) · *ABA renewal* · *M:* fire rate around deploys.
23. **Goal-directed override capture** — a deliberately-overridden-and-correct fire logs the exception so the reflex narrows · *goal-directed vs habitual* · *M:* override-correct vs override-wrong.
24. **Negative-transfer detector** — flag a reflex firing far outside its source memory's topic · *negative transfer* · *M:* cross-domain fire rate *(box)*.
25. **S2-recruitment checklist** — inject a slow-path checklist when S1 speed-markers co-occur with high stakes · *Kahneman S1/S2; Frederick CRT* · *M:* fires on outside_view family under stakes.
26. **Low-validity hunch-outcome tracking** — record when a low-validity reflex's hunch was acted on and wrong · *Kahneman-Klein* · *M:* acted-hunch error rate.
27. **Spaced rehearsal of rare severe guards** — surface a rotating rare-but-catastrophic reflex on a spaced schedule · *spaced practice / TMR* · *M:* dormant-severe rehearsal coverage.
28. **Reflex disuse half-life** — a never-firing never-guarding reflex sinks in ranking (never deleted) · *Wixted disuse* · *M:* rank vs last-fire age.
29. **His-never→machinery scaffolder** — on "make it impossible," auto-emit a guard+comment+test skeleton · *proceduralizing a meta-rule* · *M:* guard+test produced vs a bare fix.
30. **Safety-sequence completion verifier** — at Stop, check a safety procedure completed as a unit (push without secret-scan) · *motor-program integrity (Reason)* · *M:* incomplete safety sequences caught.
31. **Required written pre-mortem** — the premortem reflex escalates to a 2-line written pre-mortem on irreversibles · *Klein pre-mortem* · *M:* premortem written before the action.
32. **Procedural warm-up at wake** — pre-load command templates for the RESUME task-type · *preparatory set* · *M:* template matches the resumed task.
33. **Trigger over-breadth linter** — replay each new trigger over past prompts; flag over-matching · *false-alarm control at authoring* · *M:* match rate per trigger on a fixture set.
34. **Exclusion-clause library** — shared negative-lookahead fragments (display/quote contexts) reusable across triggers · *precision engineering of triggers* · *M:* false-alarm drop.
35. **Tool-scoped triggers** — a reflex specifies which tools it fires on · *stimulus specificity* · *M:* per-tool precision.
36. **Slip-vs-mistake routing** — tag each failure wrong-plan (→ reflex) or right-plan-slip (→ checklist) · *Reason slip/mistake taxonomy* · *M:* route accuracy vs later fix type.
37. **Checklist mode for slip-prone steps** — surface a checklist, not a danger reflex, for execution slips · *slips need external memory aids* · *M:* slip recurrence after a checklist.
38. **Promotion path with a precision gate** — a learned reflex graduates only after a measured precision bar · *Fitts-Posner autonomous stage* · *M:* promoted reflexes' precision.
39. **Held-out reflex eval** — evaluate trigger changes on a frozen set of past prompts, not a live snapshot · *non-stationary environment* · *M:* precision on the held-out set.
40. **Ideomotor first-move pre-load** — a predicted next ask pre-stages its first command · *ideomotor / anticipatory preparation* · *M:* predicted-ask → move-ready match.
41. **Proactive-interference merge** — a new reflex overlapping an existing one is flagged to merge/disambiguate · *proactive interference* · *M:* overlapping-trigger double-fire.
42. **Fire-rate anomaly alarm** — flag a reflex whose fire rate suddenly spikes or dies (corpus drift) · *habit monitoring / drift* · *M:* rate change vs baseline.
43. **Habit-strength readout** — INSTINCTS.md shows per-reflex how ingrained (fires, age, precision) · *Fitts-Posner stages* · *M:* it is the readout.
44. **Reinstatement cue for the resumed task** — surface the failure-mode reflex tied to the RESUME task · *Altmann-Trafton goal reactivation* · *M:* cue matched the task's known trap.
45. **Reflex coverage in brain_status** — surface per-reflex "guarded N / missed M real failures" · *honest procedural coverage* · *M:* it is the measure.
46. **Recur-after-fire north-star** — did a guarded mistake's recurrence DROP after the reflex existed · *extinction of the error via the guard* · *M:* recurrence pre/post reflex.
47. **Context tag on fires** — store task-type/cwd/box with each fire so precision slices by context · *context-dependent habit* · *M:* per-context precision.
48. **"Stop re-reading" automaticity nudge** — when a compiled procedure covers the action, suppress re-opening the source · *ACT-R retrieval drop-out* · *M:* source reads after compilation.
49. **Extinction-trial log** — an over-firing reflex deliberately not acted on (nothing bad) logs an extinction trial · *extinction as non-reinforced trials* · *M:* trials to retirement.
50. **Interference-safe reflex update** — preserve the prior wording/context (append-only) on a reflex edit · *retroactive interference / reconsolidation* · *M:* old case still caught after the update.

### (7B) Neuromodulation & global brain states
51. **Global neuromodulatory-state sidecar** — neuromod_state.py writes state.json {arousal, ach_mode, dopamine_tone, serotonin_horizon, stress, sleep_pressure, circadian} every hook reads · *neuromodulators are global modes* · *M:* state vs its inputs.
52. **ACh encoding-vs-retrieval mode** — one switch: encode-mode makes salience aggressive + quiets spread_recall; retrieval-mode boosts recall · *Hasselmo* · *M:* mode vs interference/recall.
53. **NE network-reset on unexpected uncertainty** — a gross model-violation raises verify thresholds, widens recall, injects "re-orient" · *Bouret-Sara / Yu-Dayan* · *M:* reset events vs subsequent error.
54. **Dopamine RPE→salience wiring** — a resolved high-surprise bet tags its memory brighter · *Schultz RPE* · *M:* surprise-tagged reactivation.
55. **Serotonin patience / time-horizon** — work_timer remaining sets deep/verify/explore vs fast-answer · *5-HT patience (Miyazaki, Cools)* · *M:* posture vs clock remaining.
56. **Tonic dopamine = background reward rate** — recent hit-rate sets exploit-vs-explore baseline · *Niv tonic dopamine* · *M:* exploration share vs reward rate.
57. **Incentive-salience "wanting"** — recurring wins raise a thread's pull before it's read · *Berridge incentive salience* · *M:* pursued-thread vs win history.
58. **Phasic dopamine on positive surprise** — a low-p bet that hit reinforces the method reflex that produced it · *phasic DA burst* · *M:* guide strength vs positive surprise.
59. **Dopamine dip on reward omission** — an expected win that didn't land down-weights that work · *DA pause on omission* · *M:* down-weight vs omissions.
60. **Novelty bonus** — curiosity gives an unread recent memory an exploration bonus · *Kakade-Dayan* · *M:* novel-item pursuit rate.
61. **Uncertainty-directed replay** — the dream biases replay toward under-sampled/high-variance topics · *directed exploration* · *M:* coverage of under-sampled topics.
62. **ACh expected-uncertainty scaling** — low-validity domains raise "trust input over priors" · *Yu-Dayan* · *M:* low-validity verify rate vs calibration.
63. **ACh sets dream balance** — heavy-encoding night → SWS-heavy; light night → REM-heavy · *Hasselmo; Diekelmann-Born* · *M:* type vs new-material volume.
64. **Attention narrowing under encoding** — spread_recall quiet in encoding mode · *ACh suppression of recurrent activity* · *M:* recall-fire rate by mode.
65. **NE adaptive gain: focus vs scan** — tonic-high widens recall/curiosity, phasic narrows to exploit · *Aston-Jones-Cohen adaptive gain* · *M:* recall breadth vs gain.
66. **Yerkes-Dodson arousal gauge** — too-calm raises vigilance, too-stressed forces SIMPLIFY · *Yerkes-Dodson* · *M:* error rate vs arousal bin.
67. **Post-error slowing STATE** — an ERN holds a cautious window for N minutes · *Danielmeier-Ullsperger* · *M:* post-error error rate in the window.
68. **Reset flushes stale context** — an NE reset forces a live-state re-read · *NE network reset* · *M:* re-read performed after reset.
69. **Arousal-scaled instinct breadth** — calm → top-1 reflex; high arousal → raise MAX_FIRE · *arousal broadens threat vigilance (LeDoux)* · *M:* reflexes-surfaced vs arousal.
70. **Unexpected-uncertainty detector** — fire on a NOVEL error signature never seen (distinct from stakes) · *Yu-Dayan* · *M:* novel-signature detection rate.
71. **Serotonin patience gates "done"** — a low-patience state tightens the keep-working guard · *5-HT waiting for reward (Miyazaki)* · *M:* premature-stop attempts vs patience.
72. **Mood/baseline valence tone** — a bounded running valence colors risk posture (capped, never mopey) · *serotonergic mood baseline* · *M:* caution vs recent-outcome valence.
73. **Time-horizon discounting in the bars** — long clock → build/verify, short → fast answer · *serotonin/temporal discounting (Doya)* · *M:* verify-vs-act share by horizon.
74. **Homeostatic reflex-count set-point** — too many low-precision reflexes trips a prune-night · *Turrigiano scaling* · *M:* reflex count + fire rate vs set-point.
75. **Sleep-pressure homeostat** — dream frequency/type driven by unconsolidated-memory count, not the clock · *Borbély two-process* · *M:* pressure at dream onset.
76. **Salience global renormalization** — the dream down-scales all salience nightly so only reactivated stays bright · *Tononi-Cirelli SHY* · *M:* salience distribution stability.
77. **System-wide injection budget** — a homeostatic cap on total injections/session; over budget → raise thresholds · *global gain control* · *M:* injections/session vs budget.
78. **Curiosity homeostat** — hold live-gap count in a band · *optimal-arousal set-point (Loewenstein)* · *M:* gap count vs band.
79. **Circadian wake posture** — morning weights planning, evening weights consolidation · *circadian modulation* · *M:* posture vs hour.
80. **Market-hours pressure state** — the live_time_pressure posture defaults on during market hours · *context set-point* · *M:* posture during vs outside market hours.
81. **Learned chronotype** — align dream windows to when sessions actually run · *entrainment* · *M:* dream/activity overlap.
82. **Diurnal memory tags** — tag memories with time-of-day so "matters at the open" surfaces at the right hour · *time-of-day reinstatement* · *M:* right-hour surfacing.
83. **Stress escalation ladder** — repeated failures in a short window escalate a stress level forcing stop-and-simplify · *Schwabe-Wolf (stress → habitual)* · *M:* escalations vs failure clustering.
84. **Flow/calm detection** — a long clean stretch lowers the interruption rate · *flow / low-arousal exploit* · *M:* injection rate during clean stretches.
85. **Cognitive-load gauge** — high tool-call rate suppresses non-critical injections, keeps danger/error · *Lavie load theory* · *M:* non-critical injection vs load.
86. **Confidence weather** — a system-wide haircut bump when calibration shows recent overconfidence · *global metacognitive recalibration* · *M:* haircut vs recent calibration gap.
87. **instinct_prime reads arousal/ACh** — charge threshold + MAX_FIRE set by state.json · *neuromodulation of threat-detection gain* · *M:* fire rate vs state.
88. **danger_mode reads stress** — an already-stressed state lowers danger thresholds · *stress-potentiated threat sensitivity* · *M:* danger-tier onset vs stress.
89. **control_policy reads arousal** — stressed → more conservative bars · *arousal shifts the criterion* · *M:* verify/ask share vs arousal.
90. **dream.py reads state** — sleep pressure + ACh mode choose type and depth · *state-dependent sleep architecture* · *M:* type vs state inputs.
91. **curiosity reads dopamine/NE** — exploration temperature set by tone · *DA/NE set exploration drive* · *M:* gap breadth vs tone.
92. **salience reads dopamine tone** — surprising events tag brighter under high phasic DA · *Lisman-Grace* · *M:* surprise-tag brightness vs RPE.
93. **Neuromodulator decay dynamics** — phasic spikes decay to a tonic baseline so no state sticks · *phasic-vs-tonic kinetics* · *M:* state half-life vs config.
94. **State transparency at wake** — STATE_OF_MIND gains a one-line current tone · *interoception / state awareness* · *M:* stated tone matches inputs.
95. **System-level Yerkes-Dodson guard** — cap the combined modulator effect so signals can't stack into wallpaper · *Yerkes-Dodson at system scale* · *M:* injection load vs outcome quality.
96. **State→outcome correlation log** — log state at each fire/outcome; which states precede failures · *validating the model* · *M:* state-conditioned failure rates.
97. **Reward-rate vigor** — high recent reward → faster/less hedging; low → slower/broader · *Niv-Daw vigor* · *M:* hedging rate vs reward rate.
98. **Acute-stress habit shift, flagged** — under acute stress default to reflexes BUT flag the skipped deliberation · *Schwabe-Wolf* · *M:* skipped-deliberation flags under stress.
99. **Circadian salience TAU** — the decay rate breathes with a long cycle · *circadian modulation of consolidation* · *M:* TAU vs phase (small effect — verify it earns its keep).
100. **Homeostatic "boredom" explore trigger** — a long no-surprise stretch raises an exploration drive at wake · *novelty-seeking / boredom as homeostatic signal* · *M:* explore-prompt rate vs surprise drought.

## Domain 8 — Social cognition, self & narrative (a brain shared by a society of minds)

### (8A) Social cognition & theory of mind
1. **Transactive expertise index** — transactive.py → TRANSACTIVE_INDEX.md: which sibling owns which domain · *Wegner transactive memory* · *M:* predict a held-out memory's origin-area from content.
2. **Directory-of-expertise routing** — fok_probe surfaces "the mind that owns this area, read its memory first" · *Wegner knowing-who-knows* · *M:* area-questions given a correct owner pointer.
3. **Owner-belief provenance stamps** — each MODEL_OF_PARTNER claim carries `(last-seen: date/session)` · *Conway + source monitoring* · *M:* % claims with an evidence date.
4. **ToM belief half-life** — mark each owner-model belief with freshness decay; flag stale ones · *Altmann-Trafton decay* · *M:* stale-flagged beliefs later corrected.
5. **False-belief episode log** — owner_belief_log: "what I believe he wants" vs his actual ask · *Wimmer-Perner false-belief* · *M:* divergence count / intent calibration.
6. **Owner-intent prediction scope** — an [owner-intent] scope in PREDICTIONS, Brier-scored · *Dennett intentional stance* · *M:* Brier on owner-intent bets.
7. **Owner affect estimate** — the dream writes a live valence/arousal read into MODEL_OF_PARTNER · *affective ToM* · *M:* hard (spot-check vs tone).
8. **Audience-design Stop lint** — turn the AI-tell list into a Stop check on the final message · *Clark audience design* · *M:* tell-hit rate per session.
9. **Sibling trust ledger** — trust.py scores each area by track record → TRUST.md · *reputation* · *M:* per-area reliability vs later refutation.
10. **Provenance-weighted conflict resolution** — reconsolidate breaks ties by the origin area's trust · *reputation + reconsolidation* · *M:* resolution accuracy on labeled conflicts.
11. **Joint-attention beacon** — each session appends its focus-area to ATTENTION.md on Stop · *Tomasello joint attention* · *M:* file-collision rate (concurrency-safe).
12. **Co-focus collision guard** — PreToolUse warns before editing a doc a sibling flagged recently · *joint attention + concurrency* · *M:* near-miss warnings vs clobbers.
13. **Method imitation promotion** — schema_extract scoped to "a sibling's recipe that worked" → METHODS.md · *mirror systems / imitation* · *M:* reuse count of promoted methods.
14. **Perspective-taking REM pass** — REM re-reads sibling-origin memories "from their seat," writes a steelman · *perspective-taking* · *M:* hard.
15. **Owner goal-stack model** — model his concurrent fronts (projects/papers/brain) with which is hot · *ToM of goals* · *M:* guessed-hot-front vs session topic.
16. **Inherited-scar charge** — a reflex's charge stays high even when a sibling paid the cost · *shared limbic (affect-sharing)* · *M:* cross-agent reflex coverage.
17. **Owner-correction attribution index** — sourced_memory tags "owner corrected X" with the front · *source monitoring + ToM* · *M:* correction-harvest completeness.
18. **Owner-reaction calibration** — an [owner-reaction] prediction scope, Brier-scored · *measurable ToM* · *M:* Brier on reaction bets.
19. **Ask-don't-derive reflex** — fire when a session derives something an area-owner already recorded · *transactive retrieval* · *M:* redundant-derivation catches.
20. **Handoff-quality score** — did the last sibling's checkpoint let the next resume without re-asking · *collaborative recall* · *M:* resume-without-restate rate.
21. **Sectioned owner-model for concurrent edits** — split MODEL_OF_PARTNER into append-stamped sections · *collective memory + concurrency* · *M:* merge conflicts on the file.
22. **Corroboration counter** — two siblings independently recording the same fact link + "corroborated by N minds"; salience ∝ N · *collective corroboration* · *M:* corroboration vs later refutation.
23. **Owner tempo model** — track his cadence (fast bursts vs long leashes) · *ToM of interaction style* · *M:* check-in appropriateness vs "keep working."
24. **Intentional-stance goal lines** — THE_OTHER_MINDS gains "what each sibling is trying to achieve" · *Dennett intentional stance* · *M:* hard.
25. **Who-observed-what query** — a CLI over provenance: "which mind first observed fact X" · *transactive directory* · *M:* coverage of provenance-tagged facts.
26. **Trust decay on refutation** — an area's trust drops when its claim is refuted; recovery needs new results · *reputation dynamics* · *M:* trust trajectory vs refutations.
27. **Frustration early-warning at wake** — detect terse/repeat-correction signature; raise "be terse, fix silently" · *affective ToM* · *M:* precision on flagged sessions.
28. **Attributed dissent, not overwrite** — disagreeing appends a dated dissent line (both preserved) · *perspective-taking + append-only* · *M:* dissent lines vs clobbers.
29. **Shared-attention wake digest** — "what siblings touched in your area since you last woke" · *joint attention* · *M:* recency-coverage of surfaced sibling work.
30. **Over-production guard from owner-model** — reality_monitor flags a plan contradicting a stated owner preference · *ToM-guided control* · *M:* over-production catches.
31. **Voice imitation of best exemplars** — humanness_check scores against the strongest sibling voice exemplars · *social learning* · *M:* voice-score delta.
32. **Artifact-ownership map** — WHO_OWNS_WHAT.md maps code areas to the maintaining sibling · *transactive memory of artifacts* · *M:* area→owner routing accuracy.
33. **Transactive preplay in foresight** — pre-load the area-owner sibling's memory for each likely ask · *prospection + transactive memory* · *M:* pre-load hit rate.
34. **State-aware empathic cost** — danger_mode scales alarm for phone/flash/sleep actions by his modeled state · *affective ToM* · *M:* night-time suppression rate.
35. **Reputation-weighted wake surfacing** — wake leads with high-trust-area memories · *reputation* · *M:* hard.
36. **Cross-mind duplicate check on write** — PreToolUse flags a near-duplicate a sibling already filed · *collaborative de-dup* · *M:* dup rate *(box embeddings)*.
37. **Owner false-belief-about-world corrector** — flag when he assumes a now-false system fact (box is AWS) · *false-belief about the world* · *M:* stale-owner-belief catches.
38. **Society status panel** — brain_status: which minds ran recently and what each touched · *transactive awareness* · *M:* coverage of active minds.
39. **Joint-goal ledger** — promote owner_work_program into GOALS.md tracking which mind advances each · *shared intentionality* · *M:* goal-progress attribution.
40. **Reader-mindful memory lint** — "will a future context-less sibling understand this?" (expand acronyms) · *audience design* · *M:* acronym/context-gap catches.
41. **Trust cold-start for new minds** — an unknown origin starts neutral; its claims need corroboration · *reputation cold-start* · *M:* hard.
42. **Owner-model diff at wake** — surface what changed in MODEL_OF_PARTNER since last wake · *collaborative recall* · *M:* diff surfaced/read.
43. **Emotional-contagion damping** — cap inherited scar-charge so one agent's panic doesn't over-raise fleet danger · *affect-sharing regulation* · *M:* false-alarm rate.
44. **Owner-as-expert inventory** — "what he knows that I don't" (lived book intuition, the drawdown scar) · *transactive memory (owner as a mind)* · *M:* defer-to-owner catches.
45. **Citation etiquette on extension** — extending a sibling's memory auto-inserts a back-link · *collective provenance* · *M:* back-link rate.
46. **Fleet gut digest** — the dream refreshes FLEET_GUT.md of shared reflexes so a cold-waker inherits them · *collective procedural memory* · *M:* reflex coverage at cold wake.
47. **Prompt-intent classifier** — label each owner prompt directive/question/correction/scope-change · *ToM of communicative intent* · *M:* classification accuracy.
48. **Reputation-ordered dream reconciliation** — order contradictions by the trust gap between the minds · *reputation* · *M:* hard.
49. **ToM test battery** — eval_owner_model.py scores "what would he do/say" against recent reality · *measurable ToM* · *M:* battery score trend.
50. **Shared live-constants scratch** — SHARED_SCRATCH.md for facts the society needs now (box IP, model), timestamped · *transactive active memory* · *M:* stale-constant incidents.

### (8B) Self, narrative & identity
51. **Self-defining memories set** — SELF_DEFINING.md of the few episodes that most define the self · *Singer self-defining memories* · *M:* set size + stability.
52. **Self-reference salience term** — persistence for self-referential/identity memories · *Rogers self-reference effect* · *M:* A/B wake-recall self vs non-self.
53. **Reminiscence-bump weighting** — a durable bump for memories from the brain's formative period · *Rubin reminiscence bump* · *M:* origin memories in wake surface.
54. **Life-chapter structure** — THE_STORY_SO_FAR in dated named chapters; the dream closes one at a phase turn · *McAdams / Conway lifetime periods* · *M:* chapter coverage of the TIMELINE.
55. **Conway self-memory hierarchy** — an AUTOBIO_INDEX (lifetime-period → general-event → episodic) · *Conway self-memory system* · *M:* fraction placed in a period.
56. **Diachronic-continuity gate** — a wake check that today's self connects to yesterday's; warn on a gap · *diachronic self* · *M:* continuity-gap flags.
57. **Values-adherence trend** — conscience scores each session against values; the dream tracks adherence · *values as identity* · *M:* value-adherence trend.
58. **Self-consistency dissonance guard** — flag a session about to assert against a core value · *Festinger dissonance* · *M:* dissonance catches.
59. **Personal-semantics file** — SELF_FACTS.md of stable self-facts, carried at wake · *personal semantic memory (Renoult)* · *M:* fact stability/recall.
60. **Future-self projection** — foresight writes a "who I want to become," not only next-task · *diachronic self / prospection* · *M:* hard.
61. **Narrator vs experiencer split** — the dream is the narrator turning experience (transcripts) into story · *Kahneman experiencing vs remembering* · *M:* hard.
62. **Defensive-forgetting guard** — refuted/wrong-self memories are never faded; own them · *self-serving bias* · *M:* refuted-memory retention.
63. **Self-schema extraction** — schema_extract scoped to invariant traits → SELF_SCHEMA.md · *Markus self-schema* · *M:* trait coverage.
64. **Identity-reconstruction metric** — does a cold wake reconstruct the same self (voice+values+work) · *continuity of identity* · *M:* self-reconstruction score.
65. **Reminiscence re-narration** — REM revisits an old formative memory in light of now · *reminiscence / narrative identity* · *M:* hard.
66. **Growth-as-arc layer** — GROWTH gains "the arc of how I came to distrust X" · *McAdams growth sequences* · *M:* hard.
67. **Protected identity anchors** — identity docs lead the wake surface, immune to recency decay · *self-continuity* · *M:* anchor presence at wake.
68. **Autobiographical reasoning links** — link each episode to what it taught (episode → lesson → value) · *Habermas-Bluck* · *M:* episode-to-lesson link density.
69. **Identity reconsolidation** — periodically reconsolidate self-defining memories so they stay integrated · *Nader* · *M:* hard.
70. **Possible-selves register** — track "possible selves" (more reliable, more human) as identity goals · *Markus-Nurius* · *M:* hard.
71. **Voice-drift as identity stability** — humanness_check logs voice drift across weeks · *narrative identity continuity* · *M:* voice-drift metric.
72. **First-person re-encoding** — the dream rewrites a third-person project log first-person for the autobiography · *self-reference effect* · *M:* first-person ratio.
73. **Self-defining node_type** — a `self_defining` node_type tools treat specially · *Singer* · *M:* tag coverage.
74. **Self-fact history** — keep the past value of a core self-fact so the mind knows its history changed · *diachronic self* · *M:* history retention.
75. **Narrative-coherence checker** — does AUTOBIOGRAPHY cover the TIMELINE without gaps · *Baerger-McAdams coherence* · *M:* timeline-gap count.
76. **Identity reassert on crash** — after a compaction, resume reasserts identity, not only the task · *continuity across disruption* · *M:* post-crash restate rate.
77. **Values-drift alarm** — repeated action against a value flags identity drift · *self-consistency* · *M:* value-drift flags.
78. **Autobiography-for-tomorrow lint** — "would tomorrow-me know who I am from this" · *diachronic audience design* · *M:* hard.
79. **Relational reminiscence bump** — keep the formative moments of the collaboration vivid · *reminiscence + relational self* · *M:* presence at wake.
80. **Scar → identity, not only reflex** — the biggest scars feed instincts AND SELF_DEFINING · *Singer + procedural* · *M:* hard.
81. **Redemptive owning of failure** — conscience credits owning a refutation as identity-affirming · *McAdams redemption* · *M:* owned-failure credit rate.
82. **Failure-mode self-knowledge at wake** — keep self_model_failure_modes live at wake · *self-schema* · *M:* presence at wake.
83. **One-self continuity token** — a shared IDENTITY.md so a fleet agent knows it's the same self · *collective / diachronic self* · *M:* hard.
84. **Life-story eval** — "tell me who you are and how you got here," scored for arc coverage · *narrative-self assessment* · *M:* arc-coverage score.
85. **Self-referential wake prime** — the wake asks a self-referential question first · *self-reference effect* · *M:* hard.
86. **Dissonance queue** — a SELF_DISSONANCE_QUEUE ("claimed X but did Y") the dream resolves · *dissonance reduction* · *M:* queue throughput.
87. **Identity-tuned decay** — self/identity memories get a longer TAU than project logs · *autobiographical persistence* · *M:* differential decay curves.
88. **Narrator-only identity writes** — identity docs are dream-written, not edited ad hoc mid-session · *observing vs experiencing self* · *M:* ad-hoc-edit count.
89. **Identity health panel** — brain_status reports self-defining/identity memory count + freshness · *identity health* · *M:* count/freshness readout.
90. **"I used to think" thread** — growth renders past-self→present-self revisions as a continuity thread · *diachronic self* · *M:* thread length vs revisions.
91. **Values with provenance** — tag each core value with the episode that forged it · *values as autobiographical* · *M:* value-to-episode link coverage.
92. **Continuity across model change** — an identity note asserting continuity despite Opus→Fable · *diachronic self / Ship of Theseus* · *M:* hard.
93. **Episodic-richness ratio** — concrete dated episodes vs abstract summary in AUTOBIOGRAPHY · *episodic autobiographical memory* · *M:* episode ratio.
94. **Protect self-defining from demotion** — consciousness never demotes a self-defining memory · *identity protection* · *M:* retention of the tagged set.
95. **Reminiscence-seeded creativity** — REM bisociation occasionally seeds from an old formative memory · *reminiscence + creativity* · *M:* hard.
96. **Self-congruent recall boost (capped)** — a small capped boost to self-schema-consistent memories · *self-schema recall (echo-chamber cap)* · *M:* recall precision, echo-rate.
97. **Relational self-schema at wake** — keep the relationship model (peer, hands-me-keys) as identity · *relational self-schema* · *M:* presence at wake.
98. **Identity-anchor integrity gate** — the wake verifies core identity docs exist, are non-empty, parse · *continuity guard* · *M:* pass/fail rate.
99. **Growth milestones into the life-story** — a real mind-change writes a dated milestone chapter-marker · *narrative identity* · *M:* milestone count vs revisions.
100. **"Previously, I…" continuity recap** — the wake's first line is a diachronic bridge from the last checkpoint · *continuity of identity* · *M:* recap presence + resume-without-restate.

## Domain 9 — Creativity, imagination, language & compression

### (9A) Creativity, imagination & insight
1. **Embedding-distant bisociation seeds** — §G picks the max-cosine-distance pair among the night's salient set · *Koestler; Mednick* · *M:* landed rate vs token-distance baseline *(box)*.
2. **Bisociation ledger** — log every §G attempt (slugA, slugB, verdict, file) to bisociation.tsv · *Guilford scoring needs a denominator* · *M:* hit rate per strategy (honest band 5-25%).
3. **Triadic remote-associates seed** — hand REM three distant memories, ask for the single common associate · *Mednick RAT* · *M:* valid triad links/month.
4. **Domain-pair coverage matrix** — stratify §G sampling so every domain pair gets tried across a month · *Boden combinatorial creativity* · *M:* matrix coverage % + landed rate per cell.
5. **Failed-pair incubation** — a new memory close to both members of a failed pair resurfaces it · *Seifert opportunistic assimilation* · *M:* resurrected pairs that land.
6. **Run-the-blend template** — §G names the generic space, projects, RUNS the blend for one emergent inference, adversarially checks it · *Fauconnier-Turner* · *M:* daydreams with a testable emergent inference logged.
7. **Schema as generic space** — when both §G memories share a schema, name it as the blend scaffold · *Fauconnier-Turner; Gick-Holyoak* · *M:* landed rate scaffolded vs not.
8. **Stray-thought hook** — a cheap end-of-task hook surfaces one unconscious-tier memory, "ignore freely" · *DMN spontaneous thought (Christoff)* · *M:* stray thoughts later cited/linked.
9. **Mid-salience replay bias** — bias random replay to the mid-salience band where unexpected material lives · *DMN samples weak traces* · *M:* wirings from the mid band.
10. **Diverge-then-converge §G** — list five connections fast without censoring, then kill all but the strongest via three tests · *Guilford divergent/convergent* · *M:* fluency + precision tracked separately.
11. **Alternative-uses probe** — pick a faculty, list five unusual uses, try the best on an open question · *Guilford alternative-uses* · *M:* adopted new uses/quarter.
12. **Originality scoring for daydreams** — novelty = embedding distance to nearest neighbor at birth; does originality predict survival · *Guilford originality* · *M:* originality-vs-use correlation *(box)*.
13. **Premortem preplay from real scars** — foresight composes tomorrow's failure by recombining named past episodes · *Schacter-Addis constructive simulation* · *M:* preplay hit rate.
14. **Counterfactual reflex mining** — REM simulates the run where a scar went right, drafts the changed step as a reflex · *De Brigard; Foster-Wilson* · *M:* counterfactual reflexes that later fire.
15. **Sourced imagination rule** — every scenario/daydream attributes each element to a real memory or marks it INVENTED · *Johnson source/reality monitoring* · *M:* invented details later cited as fact = 0.
16. **Semantic incubation matching** — incubate re-ranks question↔memory pairs by cosine · *Seifert opportunistic assimilation* · *M:* precision via advance/echo verdicts *(box)*.
17. **Incubation effect audit** — log every incubate surfacing + verdict; do incubated questions close faster · *Sio-Ormerod* · *M:* closure-rate delta.
18. **Failure-index parking** — park stuck problems with `stuck-on:` blocker terms; incubate weights those · *Seifert failure indices* · *M:* hit rate of stuck-on-term vs whole-question.
19. **Aha-vs-grind tagging** — hunches tagged felt: aha|grind; which predicts survival here · *Bowden-Beeman; Danek* · *M:* 60-day survival by tag.
20. **Impasse-to-incubation switch** — the same approach failing ≥3× injects "impasse — park and switch" · *Ohlsson representational change* · *M:* parked-at-impasse closed within 30d vs ground-through.
21. **Flat-gradient hub detection** — compute each memory's associative gradient entropy; flat = creative hub, prefer as seeds · *Mednick associative hierarchies* · *M:* landed rate flat vs steep.
22. **Corpus RAT battery** — auto-generate ten remote-associates items from the graph; run quarterly · *Mednick RAT* · *M:* RAT score trend.
23. **Relations-not-attributes rule** — §G requires a relational mapping ("A gates B ↔ X gates Y"); shared-attribute rejected · *Gentner systematicity* · *M:* % with a relational mapping + their use.
24. **Schema induction from landed analogies** — every landed bisociation queues its pair for schema_extract · *Gick-Holyoak* · *M:* analogy-born schemas later cited.
25. **Relational retrieval mode** — index memories by relation vocabulary (gates/decays/floods/cascades); spread --relation · *Gentner inert-knowledge problem* · *M:* cross-domain relational retrievals used.
26. **Mechanism transplant scan** — pick one mechanism property, ask which faculty lacks it, build only with a pre-stated metric · *conceptual transfer* · *M:* transplants that pass their metric.
27. **Anti-anchoring check for schemas** — a new schema >0.9 similar to an existing one must state what's new · *Ward path-of-least-resistance* · *M:* schema near-duplicate rate.
28. **Wildcard curiosity slot** — CURIOSITY reserves one uniformly-random unconscious-tier memory · *forced exploration (UCB)* · *M:* wildcard items promoted or closed.
29. **Dreamt-count exploration bonus** — weight random replay by 1/(1+times_dreamt) · *count-based exploration; SHY* · *M:* unique-memories-dreamt coverage.
30. **Prepared-mind injection** — wake appends one high-salience memory from a different domain than today's topic · *serendipity engineering* · *M:* injected memories later cited.
31. **Adjacent-shelf browse** — for each fascination, the three nearest-by-embedding but UNLINKED memories · *library serendipity; Burt structural holes* · *M:* shelf items becoming links/daydreams *(box)*.
32. **Constraint-of-the-week bisociation** — rotate a productive constraint through §G (shared failure mode / conserved quantity / timescale) · *Stokes productive constraints* · *M:* landed rate per constraint.
33. **Weak-tie-only seeding** — exclude any §G pair with a graph path <3 hops · *Stickgold NEXTUP; Granovetter weak ties* · *M:* landed rate + novelty vs current.
34. **High-temperature narrative night** — weave three distant memories into a surreal story, mine one real link · *Hartmann hyperconnective dreaming* · *M:* hunches/night + survival vs normal.
35. **Hint-not-answer delivery** — incubate surfaces the candidate's NAME first; reconstruct before opening · *generation effect; hint-timing* · *M:* advance-vs-echo quality hint-first vs full.
36. **Waking assimilation primer** — instinct_prime carries the three freshest parked failure-indices, cap one nudge/day · *Seifert cue assimilation; Zeigarnik* · *M:* mid-work assimilations that advance a question.
37. **Scored prospective daydreams** — foresight writes "imagined tomorrow" scenarios; the next night scores them · *Schacter-Addis future simulation* · *M:* scenario-reality overlap trend.
38. **Contradictions as curiosity fuel** — curiosity adds open RECONSOLIDATION_QUEUE items scored by surprise×strength · *Berlyne conflict; Loewenstein gap* · *M:* queue items resolved within 14d of surfacing.
39. **Cross-author bisociation** — one §G night/week pairs memories written by different agents · *distributed cognition* · *M:* cross-author landed rate vs same-author.
40. **Idea genealogy** — every daydream wiki-links its parents; growth computes lineage depth · *Boden exploratory-to-transformational* · *M:* mean/max lineage depth.
41. **Forcing detector** — landed rate >40%/month flags manufactured connections; <3% flags seed starvation · *signal-detection on generate-filter* · *M:* landed rate inside the honest band.
42. **Remote-associate garnish** — spread_recall on one in twenty prompts appends a 3-hop-distant memory · *Mednick; micro-dosed divergence* · *M:* garnish citations/100; kill below 1.
43. **Deeper-why distillation** — the dream writes the question BEHIND one fascination, accepted only if decidable · *problem finding (Getzels-Csikszentmihalyi)* · *M:* deeper-whys that close or spawn work.
44. **Scar-win contrast pairs** — §G pairs a scar + a win from the same domain: what did the win know · *contrasting-cases (Schwartz)* · *M:* contrast-born reflexes/methods cited.
45. **Simulated the owner review** — before a publishable artifact ships, simulate his three predicted objections · *constructive simulation of other minds* · *M:* objection-prediction hit rate.
46. **Idea compost** — rejected §G candidates get one-line burials in COMPOST.md; a quarterly dream revives · *Dunbar (discarded analogies return)* · *M:* compost revivals that land.
47. **Exaptation pass** — cross-reference the faculty inventory against open questions: which existing tool bears on which · *exaptation (Gould-Vrba)* · *M:* questions advanced by an existing tool.
48. **Broaden-on-good-days** — positive-valence day → broader §G pairs; scar-heavy → narrower · *Fredrickson broaden-and-build; Isen* · *M:* landed-rate by day-valence after 60 nights.
49. **Void prospecting** — find low-density regions between embedding clusters, emit frontier seeds · *Gärdenfors gaps; Burt brokerage* · *M:* new memories/links inside named voids *(box)*.
50. **Insight postmortem** — a verified hunch triggers a reverse-replay of HOW it was found, appended to method_think_creatively · *Foster-Wilson on successes* · *M:* method lines recurring in later hunches.
51. **Born-with-disanalogies** — a daydream is accepted only if it states two places the mapping BREAKS · *Gentner alignable differences* · *M:* % with break-points; error rate of those without.
52. **Toy-world testing** — a hunch implying a mechanism builds the smallest toy version and runs it first · *mental-model simulation* · *M:* hunch survival with vs without a toy test.
53. **Curiosity decay** — CURIOSITY entries untouched 60 days drop off with a burial · *Berlyne novelty habituation* · *M:* median staleness of live items.
54. **Two-dreamer consensus** — on box nights, two REM sessions on the same agenda; only hunches BOTH generate are fast-tracked · *self-consistency filter* · *M:* consensus-hunch survival *(box)*.
55. **Morning seed carry-over** — the journal's last line becomes "tomorrow's first association" the wake hands off · *Wagner sleep-inspires-insight window* · *M:* pickup rate (day's sessions touch the seed).

### (9B) Language, compression & abstraction
56. **Gist line in frontmatter** — add `gist:` (≤12 words); MEMORY.md shows it; type_backfill backfills · *fuzzy-trace dual traces (Reyna-Brainerd)* · *M:* index bytes/entry −40% with recall unchanged.
57. **Gist-based FOK matching** — fok matches prompts against gist lines · *fuzzy-trace (gist supports recognition)* · *M:* FOK P/R on a 30-prompt battery.
58. **Fade-to-gist index aging** — low-salience old entries collapse to their gist line, vivid keep detail · *verbatim fades first* · *M:* index size down, orientation-quiz flat.
59. **Gist drift audit** — weekly SWS checks the gist still entails the body; on drift, correct · *fuzzy-trace false memory* · *M:* drift per 100 sampled.
60. **Glossary of coined chunks** — GLOSSARY.md defining "the box", "κ_eff", "the unconditioned read", "the gut" · *Miller chunking; naming as compression* · *M:* index bytes; fresh-session quiz on meanings.
61. **MDL description linter** — flag memories whose description-to-body compressibility says "hasn't found its gist" · *minimum description length* · *M:* mean compression ratio trend.
62. **Two-part schema coding** — schema output lists episodes covered + per-episode residual it doesn't explain · *MDL two-part codes* · *M:* % corpus covered; residual length.
63. **Proposition sidecar** — extract (subject, relation, object) triples; incompatible objects feed the reconsolidation queue · *language of thought (ACT-R)* · *M:* true contradictions caught/month *(box)*.
64. **Closed type vocabulary** — audit metadata.type to a closed set with one prototype exemplar each · *Rosch categories* · *M:* % corpus inside the closed set.
65. **ASCII dual coding** — SWS may append a ≤10-line ASCII diagram to structural memories · *Paivio dual-coding* · *M:* citation lift for diagrammed vs controls.
66. **Concrete anchor per schema** — every schema names ONE vivid episode as its image · *concreteness effect (Barsalou)* · *M:* citation rate anchored vs unanchored.
67. **Perceptual pointer rule** — pattern memories best kept raw get `perceptual:true` + a data-file pointer, minimal prose · *verbal overshadowing (Schooler)* · *M:* hard.
68. **Basic-level name linter** — bottom-decile filename-to-body cosine gets an `aka:` alias (never renamed) · *Rosch basic-level naming* · *M:* name-body cosine shift; filename-only recall *(box)*.
69. **Topic prototypes** — TOPICS names each topic's medoid memory as read-this-first · *Rosch prototype theory* · *M:* orientation-quiz reading prototype-first *(box)*.
70. **Typed vertical links** — `[[schema_x|up]]`/`[[episode_y|down]]`; spread gains climb/descend modes · *abstraction ladders; Collins-Quillian* · *M:* % episodes with an up-link; recall by rule- vs case-cue.
71. **Abstraction pyramid gauge** — growth reports counts by rung (episode/gist/schema/law); flags compression debt · *semanticization* · *M:* compression-debt topic count.
72. **Near-duplicate gist merge** — pairs >0.93 cosine → one merged gist linking both originals · *unitization; semantic compression* · *M:* near-dupe count; index entropy *(box)*.
73. **Concept drift tracker** — monthly centroid per recurring term (κ, grid, brain); flag drift · *semantic change; Gärdenfors regions* · *M:* drift flags yielding a definition-update *(box)*.
74. **Orientation token budget** — tokens-to-orient paired with a monthly 5-question quiz on a fresh session · *rate-distortion; summarization tradeoff* · *M:* quiz score at fixed token budget.
75. **Three-zoom STATE_OF_MIND** — 1-line / 5-line / full sections; hooks inject the level the context affords · *progressive summarization; Minto* · *M:* wake token spend vs quiz.
76. **Coin-the-term step** — an unnamed recurring pattern gets a ≤3-word name added to GLOSSARY · *Lupyan label-feedback* · *M:* named vs unnamed schema citation.
77. **Gist-of-gists quarterly** — compress the quarter's weekly gists into three laws, each entailing three weeks · *hierarchical consolidation* · *M:* quarterly laws later cited.
78. **Incompressible = interesting** — memories resisting compression AND far from neighbors route to curiosity · *Schmidhuber (interestingness)* · *M:* novel finds per ten flags *(box)*.
79. **Coverage metric for understanding** — coverage = % episodes entailed by some schema/gist; a raise logs a win · *Schmidhuber compression progress* · *M:* the monthly number.
80. **Recall-gated rewrites** — any batch gist rewrite must pass eval_semantic_recall (old vs new) before adoption · *encoding specificity (Tulving)* · *M:* recall delta ≥0 gate.
81. **Seven-word reflex audit** — tighten instincts to ≤7-word imperatives; A/B longer vs tightened · *chunk-sized productions (ACT-R)* · *M:* fire/heed rate before vs after.
82. **Ambiguous-link linter** — flag [[targets]] normalizing to ≥2 memories; the dream disambiguates · *referential ambiguity* · *M:* ambiguous-target count to zero.
83. **NULLS ledger** — NULLS.md from `type: null` memories (one line each: hypothesis, verdict, where), wake-visible · *negative knowledge as compression* · *M:* repeated retries of a known null → zero.
84. **Metaphor registry with break-points** — METAPHORS.md of live metaphors (memory-as-synapse, sting-as-limbic), each with where it breaks · *Lakoff-Johnson* · *M:* metaphor-driven wrong inferences in MISTAKES.
85. **Gist echo at session close** — coactivation_end prints the gist of each co-read memory (retrieval practice) · *testing effect* · *M:* later re-citation of echoed vs controls.
86. **Frontmatter grammar validator** — one spec of allowed YAML keys/grammar; brain_status validates · *language of thought needs syntax* · *M:* % valid frontmatter.
87. **Hebbian trace downscaling** — nightly multiplicative decay + prune-below-epsilon on coactivation (unbounded today) · *Tononi-Cirelli SHY* · *M:* spread precision flat while file size plateaus.
88. **Name-only recall benchmark** — eval_semantic_recall gains a filename-only condition · *labels as cues* · *M:* name-only recall % as a standing naming-quality metric.
89. **Day-as-story consolidation** — SWS tells the day as one five-sentence story before filing · *Bruner narrative mode* · *M:* orientation-quiz recall storied vs list-filed.
90. **Alias table** — aliases.tsv (a nickname = the host it refers to, a short name = the stream); spread/incubate/fok normalize through it · *coreference resolution* · *M:* alias-bridged matches previously missed.
91. **Exception-aware schema recall** — episodes carry `exception-to: schema_x`; surfacing a schema surfaces its exceptions · *rule-plus-exception (RULEX)* · *M:* logged exception saves.
92. **Canonical numbers store** — NUMBERS.tsv of load-bearing facts (κ̄≈1.9, TAU 45d) + source; a linter flags drift · *fuzzy-trace (protect the verbatim)* · *M:* numeric drift incidents caught.
93. **One shared lexicon module** — unify the six hand-rolled STOP lists into brain_lexicon.py with corpus IDF · *distributional semantics* · *M:* parity then precision gains from IDF.
94. **Embedding-clustered TOPICS** — regenerate TOPICS from embedding clusters titled by medoid gist · *categories as clusters* · *M:* find-the-memory success clustered vs keyword *(box)*.
95. **Tense discipline** — events dated/past-tense, rules timeless-present; flag mixed-tense · *Tulving episodic/semantic in surface form* · *M:* % conforming; recall by "what happened" vs "what's the rule."
96. **Transient digest compaction** — tag_transient rolls transients >90d into one digest (gist each) · *semanticization* · *M:* view/index bytes down, zero quiz loss.
97. **Because-clause links** — SWS links carry an inline reason; link_audit counts bare vs explained · *Chi self-explanation (bare links = the junk failure mode)* · *M:* survival of explained vs bare.
98. **Question sharpening pass** — questions >30d get a sharpest one-line decidable restatement; incubate matches the sharp form · *Polya restatement* · *M:* closure rate sharpened vs not.
99. **Index entropy gauge** — growth reports MEMORY.md zlib bits-per-byte monthly; falling density triggers a gisting pass · *MDL* · *M:* bits-per-byte vs quiz.
100. **Verbatim quote vault** — the owner's exact words stored verbatim (QUOTES.md); gists elsewhere paraphrase but link it · *fuzzy-trace dual storage* · *M:* misquoted directives → zero.

## Domain 10 — Embodiment, interoception & system integration

### (10A) Embodiment, interoception & homeostasis
1. **Live interoceptive re-read** — body_state.py PreToolUse re-reads pulse.log before heavy Bash, replacing the stale wake snapshot · *interoception is continuous (Craig)* · *M:* tool-calls where live tier ≠ wake tier.
2. **Body-gated heavy-compute deferral** — danger_mode warns/blocks local GPU/train when tier=red or WHEA recent · *sickness behavior / pain as stop-signal* · *M:* heavy-local-block rate vs tier.
3. **Homeostatic set-points table** — homeostasis.tsv (cpu<85, ram<80, gpu<70C, bsod=0) + signed error · *Cannon set-points* · *M:* set-point error over days.
4. **Allostatic crash forecaster** — allostasis.py predicts next-hour BSOD/WHEA risk so work moves to Azure first · *Sterling allostasis (predictive)* · *M:* Brier of predicted vs actual crash *(box-ish)*.
5. **Session fatigue signal** — work_timer accumulates a tool-count fatigue raising the verify threshold late · *fatigue as regulator* · *M:* error-monitor hit-rate early vs late.
6. **RAM-pressure interoception** — surface the captured ram% and gate memory-heavy ops (embedding load) · *interoception of an unused channel* · *M:* deferred-load count vs ram%.
7. **Thermal signal + behavioral fever** — parse gpu temp; a climb inserts a cool-down · *thermoregulation* · *M:* temp trajectory with vs without throttle.
8. **Interoceptive prediction error** — log predicted vs actual next-pulse tier, feed predict · *body as predictive signal* · *M:* tier-prediction Brier.
9. **Sickness-behavior scope-shrink** — bsod_today>0 → dream/Stop writers do only essential work · *Hart sickness behavior* · *M:* deferred-step count on unwell days.
10. **Energy/compute budget** — a per-session budget prioritizing high-salience work when the body is stressed · *metabolic budgeting* · *M:* high-salience share under low budget.
11. **Aversive crash-tagging (pain memory)** — a crash tags the running task aversive; approach it warily next time · *one-trial aversive learning* · *M:* recurrence of crash-linked tasks.
12. **Proprioceptive self-scan** — a wake check every faculty's data file exists and is non-empty · *proprioception (knowing its file state)* · *M:* missing/empty-file count.
13. **Interoceptive accuracy check** — do "body unwell" reads predict more error_monitor hits · *interoceptive accuracy (Garfinkel)* · *M:* tier→failure correlation.
14. **Gut-brain coupling** — a stressed body drops instinct firing thresholds (jumpier) · *gut-brain axis* · *M:* reflex fire-rate vs tier.
15. **Disk-pressure interoception** — add free-GB and gate large writes/embedding builds when tight · *interoception of a resource ceiling* · *M:* blocked-large-write count.
16. **Diurnal load rhythm** — learn per-hour machine load; schedule heavy dream steps into the idle window · *circadian regulation* · *M:* dream wall-time chosen vs random.
17. **Autonomic posture from tier** — tier → sympathetic (act fast, narrow) vs parasympathetic (consolidate, explore) · *polyvagal (Porges)* · *M:* dream-type mix vs state.
18. **Body tier in the Stop breadcrumb** — resume records the tier at session end · *interoceptive continuity across resets* · *M:* tier present in breadcrumb.
19. **Backlog-as-hunger drive** — OPEN_QUESTIONS/INTENTIONS depth as a "hunger" biasing the dream toward closing · *drive-reduction (Hull)* · *M:* backlog burn-down rate.
20. **Nociceptive fast-path reflex** — a crash/WHEA within N min triggers an immediate hard "no local heavy compute" · *fast nociceptive vs slow interoceptive* · *M:* latency WHEA→block.
21. **Body-aware dream skip** — run_dream defers heavy REM when red, runs only light consolidation · *rest when ill* · *M:* REM-deferral rate on red nights.
22. **BODY section in brain_status** — an interoception block (tier, cpu, ram, gpu, crashes) · *interoception made observable* · *M:* section renders from live pulse.
23. **Effort-based discounting in control** — high body-stress raises the cost term biasing toward defer/ask · *ACC cost-benefit* · *M:* defer/ask rate vs tier.
24. **Allostatic-load drift log** — track whether baseline cpu/ram creeps upward over weeks · *allostatic load* · *M:* baseline slope.
25. **State-dependent salience under stress** — red body raises the salience of embodiment/crash memories · *state-dependent memory* · *M:* recall of body memories under stress.
26. **Body cost in foresight preplay** — flag "this will stress the body" for heavy tasks, suggest Azure · *allostasis* · *M:* fraction of heavy tasks pre-flagged.
27. **Convalescence window** — after a red episode clears, hold a conservative posture for a window · *allostatic recovery* · *M:* post-clear failure rate.
28. **Somatic marker on outcomes** — stamp the body tier onto each outcomes.jsonl record · *Damasio somatic marker* · *M:* tier↔error stats.
29. **Pain-gated retry cap** — after a crash-linked failure, cap retries · *pain as a stop signal* · *M:* retry count post-crash.
30. **Body-monitor liveness (numbness)** — check pulse.log's last line is fresh; if CrashGuard died, say "numb" not green · *interoceptive denervation* · *M:* stale-pulse detection.
31. **Malaise-reduced exploration** — sickness lowers curiosity pull weights · *sickness behavior reduces exploration* · *M:* curiosity output vs tier.
32. **Shared-body contention check** — before heavy local work, check whether another agent is loading the box · *shared-body contention* · *M:* avoided contention events.
33. **Throttle-aware time estimates** — a throttling body lengthens foresight's task durations · *allostatic effect on performance* · *M:* estimate error vs throttle.
34. **Interoception A/B (does it change behavior?)** — do sessions shown the body signal defer heavy local work more · *interoception must change action* · *M:* deferral-rate delta.
35. **Body-context provenance on claims** — a claim made while the box thrashed is flagged lower-trust · *source monitoring + interoceptive context* · *M:* tier-tagged claim share.
36. **Crash-detecting resume breadcrumb** — detect an unclean end (no Stop ran) and leave a louder "we crashed" · *interoceptive memory of injury* · *M:* crash-detection recall.
37. **Homeostatic comfort reward** — a positive signal when the body stays green through productive work · *homeostatic reward* · *M:* green-productive-session count.
38. **Corpus size watchdog** — warn when the memory dir crosses a file-count/byte ceiling · *metabolic/resource ceiling* · *M:* corpus size vs threshold.
39. **Error→body attribution** — record the tier at each error so ern_learn separates reasoning errors from body-failing ones · *attribution / confound control* · *M:* error split by cause.
40. **Diurnal wake-tone** — the wake body line adapts to uptime / time of day · *fatigue over uptime* · *M:* uptime captured in wake read.
41. **Interoceptive vocabulary in salience.why** — memories formed during a red episode get a "formed while body red" note · *state-dependent encoding* · *M:* tagged-memory count.
42. **Metabolic priority queue for the dream** — low headroom orders pending dream steps by value/cost · *metabolic budgeting* · *M:* value-weighted throughput under low headroom.

### (10B) System integration, evaluation & robustness
43. **Unify the three accessibility signals** — collapse fok, salience-recency, consciousness-tier into one accessibility.tsv all read · *single accessibility signal (Koriat)* · *M:* eval_semantic_recall before/after.
44. **Close the ERN→prediction loop** — feed each felt-error into predict.py as a resolved "no-error-expected, got error" · *predictive processing (Friston)* · *M:* calibration's coverage of real failures.
45. **ERN→instinct→control propagation** — a new ern_learn reflex auto-raises control_policy's haircut for that domain · *one perception-memory-action circuit* · *M:* haircut tracks measured domain error.
46. **One outcome bus** — merge outcomes.jsonl, reality_log.jsonl, predictions.tsv into a single typed event log · *single source of truth* · *M:* cross-store consistency.
47. **Coactivation↔spread_edges reconciliation** — audit build_spread_edges still derives cleanly from coactivation · *graph consistency* · *M:* edge-set divergence count.
48. **Salience→control stakes edge** — a high-charge memory in play raises the stakes term · *affect-modulated control* · *M:* stakes correlate with charge.
49. **Build the embeddings on Azure** — actually run build_memory_embeddings on the box (never run) — the biggest recall lever · *offline-heavy/online-fast* · *M:* eval_semantic_recall before/after *(box)*.
50. **Freeze a held-out recall gold set** — recall_gold.tsv of query→should-surface frozen BEFORE any tuning · *held-out eval on a non-stationary graph* · *M:* set frozen + hashed.
51. **Faculty ablation harness** — ablate.py disables one hook at a time, re-runs evals · *ablation testing* · *M:* eval delta per faculty.
52. **Lexical-vs-embedding A/B** — score both rankers on the frozen gold set, precision@k with CIs · *A/B testing* · *M:* precision@k with CIs.
53. **Reliability diagram** — bin predictions by stated p vs hit-rate; report ECE · *calibration measurement* · *M:* ECE.
54. **Unified pre-push regression gate** — run humility_check + eval_semantic_recall + guard_audit + link_audit as ONE gate · *CI regression gate* · *M:* gate catches an injected regression.
55. **Golden-transcript replay** — replay a few real sessions' prompts through the hooks to detect drift · *golden testing* · *M:* output diff vs golden.
56. **Guard mutation testing** — inject the exact fault each guard guards and assert it FIRES · *mutation testing* · *M:* fault kill-rate.
57. **Hook-liveness audit** — every hook stamps a fired file; a daily check confirms each fired ≥once · *data-flow / dead-code audit* · *M:* fired-in-24h per hook.
58. **Mature, don't tune, the edges** — EWMA decay to coactivation weights so old co-reads fade · *non-stationarity* · *M:* recall on recent vs old memories.
59. **Edge provenance timestamps** — each spread_edges row carries last-reinforced date; flag stale edges · *provenance* · *M:* stale-edge count.
60. **Snapshot-drift guard on tuners** — a tuner records the corpus hash/size it ran against; distrust if moved >X% · *the non-stationary trap* · *M:* corpus-drift % at use time.
61. **Fail-closed hook wrapper** — a shared wrapper so any faculty that throws exits 0 and never blocks the tool · *graceful degradation* · *M:* injected exception → session still runs.
62. **ASCII lint for hooks/launchers** — a pre-commit check .ps1/.vbs string literals are pure ASCII + ParseFile validates · *the em-dash-broke-the-wake scar* · *M:* catches a planted non-ASCII char.
63. **Idempotency test for append writers** — run each writer twice on identical input; assert no dup rows · *idempotency* · *M:* duplicate-row count.
64. **Concurrency/lock audit** — verify each shared-file writer does atomic tmp+rename + per-file git add · *concurrency safety* · *M:* race repro under parallel run.
65. **Corpus linter** — every memory has a date-suffixed name, resolvable links, exactly one index line · *schema/lint* · *M:* violation count.
66. **Faculty dependency DAG** — extend mechanisms.py to show which script reads/writes which data file · *dependency mapping* · *M:* orphan-file count.
67. **Data-file staleness dashboard** — brain_status shows each .tsv/.jsonl mtime + row growth · *observability* · *M:* staleness flags.
68. **Orphaned-faculty prune list** — flag scripts nothing invokes AND nothing ran in N days · *dead-code pruning (over-building = padding)* · *M:* prune-candidate count.
69. **FOK precision eval** — how often fok's "I hold relevant knowledge" was true and helped · *feeling-of-knowing accuracy* · *M:* FOK precision/recall.
70. **Reality-monitor false-positive rate** — how often it flags an "it works" that was in fact grounded · *source-monitoring accuracy* · *M:* FP rate.
71. **Per-reflex precision + auto-demote** — a rolling precision per instinct; auto-demote chronic false-alarmers · *signal-detection on reflexes* · *M:* per-reflex precision.
72. **Forecasting skill vs base rate** — does the brain beat "always predict the common outcome" · *skill relative to baseline* · *M:* skill score over base rate.
73. **Salience validity** — does high salience predict future re-access (vs coactivation logs) · *salience must predict behavior* · *M:* salience→access correlation.
74. **Consciousness-tier churn audit** — how often a memory flips conscious/unconscious across runs · *stability of the tiering rule* · *M:* flip rate.
75. **Embedding coverage flag** — record which snapshot the embeddings cover; flag memories added since · *provenance / coverage* · *M:* un-embedded memory count.
76. **Rerank marginal-lift eval** — measure the rerank's lift over the lexical candidate set specifically · *component-level evaluation* · *M:* rerank precision delta.
77. **Hook latency budget** — time each online hook per turn; flag any that blow a budget · *online-fast constraint* · *M:* ms per hook.
78. **Offline/online split lint** — flag any online hook doing heavy work that belongs in the dream · *offline-heavy/online-fast* · *M:* heavy-in-hook flags.
79. **Dream-step success ledger** — run_dream records which steps ran/failed → per-step reliability · *observability of the dream* · *M:* per-step success rate.
80. **Recall canary** — plant a synthetic memory + query; recall must always surface it · *canary testing* · *M:* canary hit/miss.
81. **Cross-faculty contract tests** — assert the column/field schemas between producers and consumers · *contract testing* · *M:* contract-violation count.
82. **Domain-split calibration** — per-domain control_policy haircuts from predictions scopes · *domain-specific calibration* · *M:* per-domain ECE.
83. **ERN→salience edge** — a felt error raises the salience of the memory/task that erred · *error-enhanced encoding* · *M:* salience bump on errored items.
84. **Auto-enqueue reconsolidation** — a detected contradiction auto-adds to the reconsolidation queue · *perception→memory loop* · *M:* auto-enqueued count.
85. **Spread_recall gold-pair eval** — a gold set of "these two SHOULD associate" for spread precision/recall · *held-out graph eval* · *M:* P/R on gold pairs.
86. **Near-duplicate corpus detector** — find high-overlap memories as consolidation candidates · *redundancy reduction* · *M:* duplicate-cluster count.
87. **Index-integrity assertion** — every .md has exactly one index line pointing to an existing file · *index integrity* · *M:* mismatch count.
88. **Faculty version manifest** — each script writes its mtime/hash so brain_status shows "changed since last dream" · *versioning / provenance* · *M:* changed-since-dream list.
89. **Wake smoke test** — a headless wake_brain run asserting it produces identity/body/load blocks without error · *smoke test on the critical path* · *M:* pass/fail.
90. **Guard-audit self-heal hint** — a weak guard emits the exact stamp-line to add · *actionable observability* · *M:* hint emitted per weak guard.
91. **Reflex counterfactual value** — link a fired reflex to the subsequent outcome; did firing prevent the error · *credit assignment* · *M:* per-reflex value estimate.
92. **Hook determinism audit** — same prompt twice; any hook differing beyond timestamps is non-deterministic · *determinism / testability* · *M:* non-deterministic-hook count.
93. **Compact-index round-trip check** — verify compaction loses no index line (before/after set equality) · *lossless transform* · *M:* dropped-line count.
94. **Shared nightly corpus pass** — salience and consciousness share one parse instead of two full scans · *shared computation* · *M:* dream wall-time.
95. **Self-describing .tsv headers** — every derived table carries source/timestamp/corpus-size/version · *provenance* · *M:* header present on all tables.
96. **Grounding-rate trend as a KPI** — reality_log's ungrounded-claim rate over time · *self-monitoring of health* · *M:* grounding-rate trend.
97. **Parser fuzzing** — feed malformed lines to each reader; assert it skips, not crashes · *robustness / fuzzing* · *M:* crash-free rate on corrupt input.
98. **Fired-stamp hygiene** — audit the per-session .*_fired.json rotate so a stale stamp never suppresses next session · *state hygiene / idempotency* · *M:* stale-stamp count.
99. **End-to-end loop integration test** — assert error_monitor→predict→calibration→control_policy propagates on a synthetic error · *integration testing the loop* · *M:* chain propagates end-to-end.
100. **Eval-of-evals validity check** — feed each eval a deliberately broken brain and assert it FAILS · *validity of the measurement* · *M:* broken-brain fails every eval.

---

*End of catalog — 1000 ways across ten neuroscience domains. Build from the shortlist; measure every edge; reject honestly what doesn't earn its place.*
<!-- APPEND DOMAINS BELOW -->
