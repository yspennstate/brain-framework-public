# The AI-memory-engineering state-of-art vs this brain (2026-07-08)

The 1000-ways catalog grounded improvement in the neuroscience of *biological* memory. But this brain is
an *engineered* system — a persistent memory-and-cognition layer for LLM agents — so the other relevant
literature is the engineering state-of-art of AI memory systems (RAG and its descendants, agentic memory
frameworks, the Generative-Agents line, retrieval evaluation). This note maps that literature onto what
the brain already does, and the conclusion is worth stating plainly: **the brain independently implements
most of the current engineering state-of-art, and the genuine gaps are all MODEL-gated — which is the same
"the box model is the lever" conclusion the measurement work reached, now confirmed from the engineering
side.** It also enriches the box-run plan: that run should enable a model-powered retrieval *stack*, not
just static embeddings.

## What the brain already has (validated against the engineering state-of-art)
- **RAG / dense retrieval** → embeddings + `spread_recall`'s cosine re-rank (LSA now, MiniLM on the box).
- **GraphRAG / knowledge-graph retrieval** → `spread_edges` (Hebbian co-reads + wiki-links) walked by
  spreading activation. Arguably *ahead* of vanilla GraphRAG: the edges are usage-learned (Hebbian), not
  just extracted, and the measurement showed the graph reaches 76% of future co-reads within 2 hops.
- **Generative-Agents retrieval (importance × recency × relevance)** → this is *exactly* `salience`
  (importance/vividness) + recency term + relevance re-rank. The brain reinvented Park et al. 2023's core
  retrieval function independently, and grounded it in neuroscience.
- **Agentic memory (MemGPT/Letta: tiers, self-editing, paging)** → consciousness tiers (conscious/
  unconscious), `compact_index` (paging the index under a size budget), and the dream as the self-editor.
- **Reflection / self-improvement (Reflexion; Generative-Agents reflection)** → the nightly dream
  (consolidation + bisociation + conscience), `growth.py`, `reconsolidate.py`.
- **Recursive summarization / hierarchical memory** → `gist_*`/`schema_extract` (episodes → gists → schemas).
- **Retrieval evaluation (RAGAS-style)** → the eval harnesses built this session (`eval_semantic_recall`
  pair + `--behavioral`, `eval_recall_signals` causal, `base_activation --eval`). Most agent-memory systems
  ship with *no* retrieval eval; this one now has a maturing suite wired to the dream.
- **Forgetting / memory management** → salience decay, `tag_transient`, the SHY nightly decay in
  `coactivation_end` (multiplicative decay + prune, already built).

That is a nearly complete stack. The honest read: this brain is at or ahead of the typical 2025-2026
agent-memory system on architecture. Its weaknesses are not missing components — they're the *quality* of
the model-powered pieces, because the live hooks can't call a model (the crash-prone laptop).

## The genuine gaps — all MODEL-gated (so all box-run work)
Every technique the engineering state-of-art would add here needs model inference the laptop can't run:
1. **A real embedding model** (bi-encoder) — MiniLM on the box, replacing the LSA baseline. Already the
   filed lever; the causal gate (`eval_recall_signals`) and pair-eval are its validation.
2. **Cross-encoder re-ranking** — the current re-rank is a bi-encoder cosine (embed each memory once,
   compare vectors). A cross-encoder (jointly encode query+memory) is materially stronger for the final
   re-rank of the top-k candidates. The offline/online split still works: the *dream* could cross-encode
   the day's actual query→candidate pairs and cache the scores; the hook stays fast.
3. **Query rewriting / expansion** — modern RAG rewrites the raw prompt into a better retrieval query
   before matching. `spread_recall` currently seeds on the prompt's literal words. A rewrite step (an LLM
   turning "how do I think about X" into the concepts to retrieve) would lift recall on lexically-thin
   prompts — but it's an LLM call, so it belongs to the dream/foresight (pre-expand likely queries) or a
   model-gated path, not the live hook.
4. **LLM-as-judge retrieval eval** — the built evals are structural (AUROC on pairs/co-reads). An
   LLM-judge ("did this retrieved memory actually help answer the query?") is the RAGAS-grade eval and
   would close the 24% task-contingent ceiling the co-read metric can't see. The dream (already an LLM
   session) is the natural home.

## So the box run should be scoped bigger than "compute embeddings"
The filed OPEN_QUESTIONS lever is right but narrow. When the box is used, the highest-value sequence is:
(a) MiniLM embeddings + validate on both gates (done-design); (b) a dream-side cross-encoder re-rank of the
day's real query→candidate pairs, cached for the fast hook; (c) an LLM-judge retrieval eval as the honest
ground truth that supersedes the structural co-read proxy. Each is independently shippable and independently
gated. None needs a laptop change; all need the box (or the dream's model budget).

The throughline with the measurement work: the architecture is done; the remaining recall lever is
model-quality, and it lives on the box. Two literatures — biological neuroscience and AI-memory engineering
— converge on the same answer.

## Addendum, 2026-07-09 (afternoon): the mid-2026 field scan — five web scouts, mapped and acted on
(provenance: five Fable scouts swept Feb–Jul-2026 literature by web search on 2026-07-09 — systems,
retrieval, evals, experience-accumulation, neuro-transfer — ~60 findings judged against this brain's
built/refuted lists; the raw findings are in the session journals. What follows is the triage and what
actually shipped the same day.)

**Externally validated (no build needed).** The 2026 literature converged on choices this brain made
early: append-only episodic storage with periodic (not per-turn) consolidation — a 2026 result shows
continuous LLM rewriting of memory degrades it BELOW no-memory, accelerating with each cycle; tiered
memory + NREM/REM-phased offline consolidation (TiMem and successors reinvent the SWS/nap/REM split);
and the "converged production recipe" of multi-signal retrieval (semantic + keyword + entity) that
mem0's 2026 field survey names — this stack's bge + Hebbian-graph + wiki-link lanes are exactly that
shape, minus the keyword arm (see below). MemOS-style KV-scheduling stays non-transferable here; DMR
is saturated as a benchmark and retired from consideration.

**Shipped same-day, behind a gate.** The missing keyword arm: a pure-python BM25 index over the full
memory text (the live seeder saw only index lines + filenames — content was invisible to it). Gate =
actual use: real archived first-prompts replayed against what each session then read (36 episodes,
whole corpus as negatives): BM25 hit@10 0.53 vs the live seeder's 0.22, MRR 2.4x; RRF fusion added
nothing over BM25 alone (the old arm is too weak to contribute), so BM25 joined the seed set directly
(fail-open, nightly rebuild in the dream persist). Also shipped: counterfactual shadow logging on the
fire gate (champion MARGIN=1.35 vs shadow 1.15; suppressed would-have-fires logged, scored later by
future co-reads — the honest path to the bge-band MARGIN retune that n=7 judged fires couldn't
justify), and a prompt archive (every owner prompt ever given an agent, harvested from all Claude +
Codex transcripts, deduped, secret-redacted, hook-fed in perpetuity — 10.5k prompts; it immediately
enabled the BM25 gate above, which is the point of substrate: evals compound).

**Queued with gates (the honest backlog, in value order).**
1. HyPE / reverse-HyDE (Vake-Vicic-Tosic 2025) - **researched 2026-07-09, the next build.** Index each
   memory under the QUESTIONS it answers: the dream writes an `answers:` block (2-3 questions), the box
   embeds them as extra sidecar rows pointing at the memory, and spread_recall matches a prompt against
   those question-embeddings (question-question matching). The web read settled two things: (a) HyPE, not
   query-time HyDE - HyDE hallucinates a confident wrong-neighbourhood answer on HETEROGENEOUS corpora
   (this one spans several domains), spiking false-positives, and adds 25-60% latency on a small LLM,
   which the hooks can't pay; HyPE is index-time, zero hook latency, +42pp context precision / +45pp
   recall reported. (b) Query REWRITING is rejected for this brain - the literature is explicit that on a
   small corpus with already-specific queries it brings no benefit (it helps INCOMPLETE queries), and the
   owner's prompts are specific. So the open "query rewriting/expansion" lane resolves to HyPE, doc-side.
   Model-gated (dream generates, box embeds); gate when built: recall@5 on traced prompts vs bge-alone.
2. Stale-fact/conflict handling (Zep bi-temporal; Supersede; MemConflict) - **BUILT 2026-07-09.**
   `schema_supersede.py` stamps a machine-readable `superseded_by:` field (explicit "supersedes
   [[slug]]" links auto-applied, direction-aware; 40 prose-correction pairs queued for the dream to
   place). `eval_stale_fact.py` reproduced the Supersede finding on this corpus: relevance alone is
   stale-blind - a topic query surfaced both members of an (old, correction) pair 4/5 times, and in
   4/4 collisions ranked the OUTDATED memory at-or-above its terse correction. The fix shipped is
   deterministic and model-free: spread_recall drops any candidate whose `superseded_by` points to a
   memory also in play (never serves a stale fact beside its known correction). Matures as the dream
   backfills the prose pairs nightly.
3. Sleep-time compute cards (Letta line): the dream pre-answers <=5 anticipated next-session
   questions inside the wake budget. Gate: >=20% of next-day first-prompts materially covered, 2 weeks.
4. Admission-gated reflexes (Meta-Policy Reflexion 2025): a new gut reflex ships only with measured
   would-have-fired precision >=0.6 on >=50 past sessions — the admission-side complement to the
   per-reflex demotion lane (34/93 signatures are false alarms today).
5. Memory-outcome attribution (CUE utilization; CBR retain-with-outcome; MemSyco 2026: ~61% of
   memory errors happen AFTER correct retrieval; the Misattribution Gap) - **BUILT 2026-07-09.**
   spread_recall and fok_probe append a durable surface-log (surfaced.tsv); `attribution.py` joins it
   against reads (retrievals.tsv) and felt-errors (outcomes.jsonl) per session: per memory it reports
   used-rate (the ~40% utilization the co-read proxy misses), flags surfaced-but-never-used as fade
   candidates (Retrieved-but-Unused), and flags surfacings that cluster with errors as mislead
   SUSPECTS for the dream to read (not blame - the Misattribution Gap is that a bad memory looks like
   model failure). Unit-tested on all three outcomes; matures as the surface-log accumulates.
6. Prioritized consolidation queue (PER->2026 crystallization): order dream work by surprise x error
   x sting instead of chronology on budget-truncated nights.

## Addendum, 2026-07-09: the box run happened — scoreboard
(provenance: every number below is MEASURED, that night — vectors computed on the Azure box, all
evals run locally against the live retrieval trace and the fixed pair basis; nothing simulated)

(a) **Embeddings: done and live.** bge-small-en-v1.5 beat MiniLM-L6, mpnet-base and the LSA baseline on
every gate (pair AUC 1.000 vs LSA 0.870; causal blend-over-graph +0.113 mean across 15 split/seed runs vs
LSA's +0.051; embedding-alone now beats the Hebbian graph, 0.686 vs 0.653). One wiring lesson worth
keeping: bge's cosine band is compressed (~0.68 random-pair baseline), so spread_recall recentres by the
sidecar's own baseline — model swaps are now scale-safe by construction.
(b) **Cross-encoder re-rank: measured and REJECTED.** ms-marco-MiniLM aces the hand-picked pairs (AUC
1.0) but adds nothing over bge on behavioral ground truth (0.612 vs 0.633 alone); stsb-roberta was worse
than chance behaviorally (0.473). The dream-side cross-encoder cache is not being built — the flattering
proxy said yes, the honest one said no.
(c) **Query rewriting and (d) LLM-judge eval: still open**, unchanged in design; (c) belongs to the
dream/foresight path (resolved to HyPE, see the queued-levers list above), (d) to the dream's model budget.

## Addendum, 2026-07-09 (later): LLM-judge eval - the design settled by research
`eval_llm_judge_replay.py` ran once at n=7 and (correctly) refused to conclude. A web read of the 2026
LLM-as-judge literature settles how to make it trustworthy, and quantifies why n=7 was meaningless:
- SAMPLE SIZE is the binding constraint: measured mean flip-rate ~14% (max 56%), and ~11-15 judged
  trials are needed for 95% consensus fidelity (15+ for high-variance items). n=7 is far below the
  floor - so the standing "do NOT retune on n=7" note isn't caution, it's arithmetic. The eval must
  accumulate to >=15 judged runs before any ranking is read, and the dream should keep judging a few
  per night, not re-run and re-decide.
- POINTWISE absolute rubric, NOT pairwise: pairwise preferences flip ~35% of the time vs ~9% for an
  absolute 1-5 score, and pairwise is least stable exactly on the close calls that matter. The judge
  should score each surfaced memory's helpfulness on a graded rubric (G-Eval chain-of-thought), not
  compare two. (This also sidesteps position bias, which is a pairwise problem; if any pairwise sneaks
  in, swap-augment A-then-B / B-then-A and discard disagreements.)
- LENGTH/verbosity bias is real (judges prefer longer): a longer memory must not score more helpful for
  its length - normalise or regress it out, or the utilization signal rewards verbose memories.
- Even at scale, 1-5 helpfulness inter-rater reliability is only ~0.45-0.60, so treat the judge as a
  COARSE signal with a required margin, never a fine one - it agrees with the causal co-read gate or it
  doesn't ship. This is the RAGAS-grade honest ground truth that supersedes the co-read proxy, but only
  once it has the n and the bias controls above.


## Addendum, 2026-07-09 (later still): eval-suite coverage vs the SOTA competency taxonomy
LongMemEval-V2 (arXiv 2605.12493) names five agent-memory competencies; mapping this brain's evals onto
them shows what's covered, what today filled, and the two real gaps - a directed list for the next
eval-building session, not a build now:
- STATIC state recall (a fact that doesn't change): COVERED - eval_semantic_recall (pair AUC),
  eval_prompt_recall (BM25 seeder), eval_recall_signals (causal future-co-read).
- DYNAMIC state tracking (use the CURRENT value of a fact that changed): COVERED AS OF TODAY -
  eval_stale_fact + the superseded_by demotion. This competency was UNCOVERED before 2026-07-09; the
  stale-fact lever filled a real taxonomy hole, not just a nice-to-have.
- WORKFLOW knowledge (multi-step procedures / how-to): GAP - no eval tests procedural recall, though the
  method_* memories and the prompt archive's "run"/"improve" classes are the raw material. A build lane:
  replay a "how do we do X" prompt, check the right method_/procedure memory surfaces and is followed.
- ENVIRONMENT gotchas (the traps/quirks): PARTIAL - the scars + gut reflexes ARE gotcha memory (flash
  windows, phone-flood, crash-under-GPU), and reflex_audit tests trigger coverage, but no eval measures
  "when a known gotcha applies, does the brain surface it." reflex_audit is the closest; extend it.
- PREMISE awareness (the query ASSUMES something false/outdated): GAP - fok abstention (feeling-of-NOT-
  knowing) is adjacent but different: abstention = "I hold nothing here"; premise-awareness = "I hold
  something that CONTRADICTS your premise." The superseded_by graph is the raw material (a query built on
  a superseded fact has a false premise) - the un-built lane is a probe that flags it.
The throughline: today's six levers cover the RELEVANCE/utilization axis and just added dynamic-state
tracking; the named gaps (workflow, premise-awareness) are the next eval frontier, each with its raw
material already in the corpus.
