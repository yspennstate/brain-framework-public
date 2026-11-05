# The network science and mathematics of the brains — audit and 5-day development program

This is a deep audit of the algorithms and mathematics the three brains run — the general
memory brain, the math brain, and the association network — measured against current
network science, graph theory, and computational neuroscience. It exists because the owner
asked for exactly this: keep developing the underlying methods from new data and from how
the system actually performs (subagent outcomes, mistake-catch rate, firing sequences),
with the focus on four functions he named — subconscious→conscious interaction, mistake
finding, memory building, and dreaming.

It is written to be acted on over a multi-day program, not read once. Each section states
what is running now, what the state of the art is, where the gap is, and the concrete next
experiment. Report every result in literal units, kept separate: examples, contexts,
attempts, model calls, sessions, and independent agents are different evidence and must
never be summed into one flattering number.

## The four functions, and where the math lives

The owner's design is a two-system mind: a fast associative substrate (the "subconscious")
that fires without deliberation, and a deliberate layer (the "conscious" agent) that reads
what surfaced and decides. The whole program is about making the first serve the second
well. Each named function maps to specific machinery:

- **subconscious→conscious**: the instinct-priming and danger-mode hooks, the graph-fire
  reflex engine, and the association network's pattern completion — all of which surface a
  memory, reflex, or prior prompt into the agent's context the moment a situation recurs.
- **mistake finding**: the mistake graph (Louvain communities over corrections), the
  reflex-firing calibration, and the prospective error graph — turning past errors into
  early warnings.
- **memory building**: the recall stack (BM25 + spreading activation + embeddings), the
  Hebbian co-activation graph, salience weighting, and supersession — deciding what is
  stored, connected, weighted, and retired.
- **dreaming**: the SWS/REM consolidation cycle that replays, connects, reconciles, and
  compiles — the offline process that reorganizes the store and grows the network.

## Layer 1 — memory building and recall

**Running now.** Retrieval fuses three channels: BM25 lexical scoring (Robertson–Spärck
Jones term weighting with length normalization), spreading activation over a Hebbian
co-activation graph (edges laid down when two memories are read in one window, decayed with
a forgetting kernel), and cosine over an offline embedding sidecar. Salience weights
memories by valence and charge so scars stay vivid. Supersession retires stale facts
without deleting them. The association network adds PPMI-weighted co-activation and lagged
sequence edges over a 20k-node universe (prompts, keywords, memories, tools, files) with
per-node out-degree pruning as synaptic scaling.

**State of the art.** Modern retrieval-augmented memory (dense bi-encoders, ColBERT-style
late interaction, GraphRAG's community-summarized retrieval, HippoRAG's personalized
PageRank over an entity graph) has converged on exactly this hybrid: a lexical channel for
precision, a dense channel for semantics, and a graph channel for multi-hop association.
Personalized PageRank (Haveliwala 2002) is the principled version of spreading activation;
PPMI (Church & Hanks 1990; Levy & Goldberg 2014) is the principled version of raw
co-occurrence weighting. Both are already in use here.

**Gaps and next experiments.** (1) Spreading activation is a bounded one-hop frontier;
personalized PageRank with a restart probability would give a mathematically stationary,
multi-hop importance that is provably convergent — swap the ad-hoc frontier for a power
iteration on the pruned adjacency and measure recall@k against the current fusion. (2)
Fusion weights across the three channels are hand-set; learn them with a small logistic
model on held-out recall judgments (the eval harness already produces the labels). (3) The
forgetting kernel is exponential; test a power-law kernel (Wixted & Ebbesen 1991), which
fits human forgetting better and would keep old scars reachable longer. (4) Embedding
coverage lags new memories; the coverage canary exists — close the loop so a dream always
re-embeds the tail before recall trusts the dense channel.

## Layer 2 — the subconscious→conscious bridge

**Running now.** Reflexes are compiled from lived mistakes into an anchor-token model; the
graph-fire engine keeps a decaying activation buffer, matches the current action against
anchor sets with a k-winners-take-all frontier, applies habituation (inhibition of return)
so nothing floods, and fires the matching instinct into the agent's context. The
association network generalizes this: any activation pattern — not just a hand-written
anchor set — can complete a stored episode and surface the prior prompt or memory that
situation once preceded. The evidence gate is strict: only directly observed nodes count
toward the k-of-n threshold, so a cascade can never supply its own evidence.

**State of the art.** This is content-addressable attractor memory. The clean formalism is
the modern Hopfield network (Ramsauer et al. 2020), whose update rule is softmax attention
and which retrieves an exponential number of stored patterns with one-step convergence;
biologically it is CA3 pattern completion in the hippocampus, where a partial cue settles
into a stored attractor. The k-winners-take-all frontier is the standard sparse-coding
control on runaway excitation; habituation/inhibition-of-return is well documented in
attention. Dual-process framing (fast System-1 pattern completion feeding slow System-2
deliberation; Kahneman 2011) is the right cognitive model for the whole bridge.

**Measured finding (2026-07-15), retrieval sparsity.** Causal held-out replay on the live
graph (train-era episodes only, test-era prompts, usefulness = token overlap of the
surfaced prompt vs the actual next prompt, recency baseline 0.309): single-episode cosine
completion fires on 0% of test prompts at thresholds 0.05–0.30. The decay falsifier came back
NEGATIVE: a follow-up A/B at half-life 25 (447 subsampled held-out prompts) still fired 0% for
cosine, and modern-Hopfield softmax retrieval ALSO fired 0% — two independent methods that
could have diverged both silent. So the 0% is not a decay-tuning artifact and not a scorer
problem; single-episode prompt→prompt completion is fundamentally too sparse for the real
archive, where a new situation rarely matches one past prompt's exact preceding node-set
closely enough. The positive control still holds (an exactly recurring situation completes at
~0.8 and returns real text), which locates the issue precisely: the mechanism is correct, the
TASK is wrong. Next direction (retracting the "Hopfield fixes it" hypothesis, which the data
killed): topic-level memory retrieval — surface relevant memory nodes for the current
situation — rather than predicting the exact next prompt; and/or coarser episode patterns
(fewer, higher-IDF anchor nodes) so loosely-similar situations can match. This is the measured
loop working: a built method (Hopfield) was killed by held-out data before it was wired.

**Measured finding (2026-07-15), the reframe validated.** Seeding the buffer with a
situation's keyword/tool nodes and spreading activation reliably surfaces topically relevant
MEMORY nodes (`fire.recall_memories`). On live queries: an options/hedging seed pulls the
options-choice, delta-hedge, calendar-carry, and grid-hedge memories (top activation 0.68);
a proofs seed pulls the theorem/certificate-audit memories; a brain seed pulls the
mistake-hardening and heeding memories (with some noise — a GPU-account reference leaked in).
A synthetic discrimination test confirms the on-topic memory outranks an off-topic one. So
the network's retrieval value is topic-level memory recall — "what do I know that bears on
this situation" — not next-prompt prediction. That is the capability to build on, though the
next hypothesis for cutting its noise did not survive contact with the data.

**Measured finding (2026-07-15), personalized PageRank did NOT reduce the noise.** Local PPR
(Andersen-Chung-Lang forward-push) was implemented and A/B'd against the buffer-spread recall
on the live graph. For the strong topic (options) PPR is comparable and surfaces a couple of
extra relevant memories; for the noisy topic (brain) it does NOT help - the off-topic
`runpod_account` stays the top memory under both methods, and PPR flattens all masses to an
undifferentiated ~8e-4, which is worse for discrimination, not better. Diagnosis: the noise
is not a graph-walk problem PPR can fix; it is keyword POLYSEMY - "network" links the
association-network topic to an unrelated GPU/runpod "network", so both methods pull the same
off-topic memory through the shared token. The real fix, confirmed against the edges,
is a precision gate: the off-topic memory reaches the topic through exactly ONE polysemous
seed keyword (kw:network -> runpod, edge weight 6.554, the only brain-seed keyword touching
it), so requiring a memory to connect to at least two distinct seed keywords filters it while
keeping genuinely on-topic memories (which connect through several). `recall_memories`
now takes `min_anchors` (default 2); on the live graph it drops the GPU-network memory from
the association-network topic, and a direct unit test proves the gate fires. Sense-aware nodes
(bigram / context-scoped keywords) remain the deeper fix and would need a rebuild. PPR is kept
as a principled alternative but not promoted - the second built method this session the
held-out data declined to crown (after Hopfield). Two killed hypotheses and one confirmed fix
in a turn is the measured loop doing its job: build, measure, keep only what beats the data. `complete_hopfield`
(modern-Hopfield softmax retrieval) is implemented as the smoother alternative and needs a
live A/B — softmax over ~4,400 episodes still demands strong concentration, so it is a
candidate, not a proven fix.

**Measured finding (2026-07-15), live firing latency.** Live per-tool firing — "a tool call
fires the correct nodes" synchronously — is not feasible as a per-call settings-hook: a completion costs ~40ms
warm on the live graph, but the dominant cost is the 156ms episode load, and a settings-hook
runs a fresh process per tool call, paying that load every time. The right design is the one
deployed (fold at dream time, query on demand) plus, for true live firing, a resident daemon
that loads episodes once and answers off the tool-call critical path. A firing engine on the
critical path of every action must cost microseconds, not milliseconds.

**Gaps and next experiments.** (1) The completion score is cosine over sparse IDF vectors;
the modern-Hopfield softmax retrieval (with an inverse-temperature that sharpens as
evidence accumulates) is a drop-in upgrade with a convergence guarantee — implement it in
`fire.py` behind a flag and A/B it against cosine on the replay eval. (2) The specificity
gate uses a median-IDF cutoff; replace with an explicit surprise/information weighting
(−log p of the conjunction) so a rare co-activation counts far more than several common
ones — this is the mathematically correct "big enough signal." (3) Firing is currently
evaluated on retrieval usefulness (token overlap of the surfaced prompt vs what the owner
actually wrote next) against a recency baseline; extend to a heldout precision/recall over
labeled near-mistake contexts so the bridge is judged on prevented errors, not just
relevance.

## Layer 3 — mistake finding

**Running now.** Corrections are nodes in a mistake graph; Louvain community detection (at
a resolution above 1.0 to get fine, actionable families) clusters them into recurring
failure shapes, and each family compiles into a reflex with a calibrated firing threshold.
The prospective error graph records per-session behavioral events (tool calls, outcomes)
and learns which patterns precede errors. The reflex layer ships in shadow mode and is
calibrated so its fire threshold hits a target precision on the trailing window — a pass
that can fail.

**State of the art.** Community detection has moved from Louvain to Leiden (Traag et al.
2019), which guarantees well-connected communities and fixes Louvain's occasional
disconnected clusters — worth adopting for the family catalog. Framing mistake-catching as
early-warning classification connects it to conformal prediction (Vovk), which gives
distribution-free error bounds on when to raise a warning — the principled way to set the
"error on the side of caution" threshold the owner asked for. Temporal graph methods
(TGN, Rossi et al. 2020) are the state of the art for learning on exactly this kind of
timestamped event stream.

**Gaps and next experiments.** (1) Swap Louvain for Leiden and measure family purity
against the hand-labeled mistake taxonomy. (2) Replace the trailing-window precision
threshold with a conformal calibration so the false-warning rate carries a guarantee, not
just an empirical estimate — the owner's standing instruction is to err toward caution, and
conformal prediction makes "how much caution" a controlled quantity. (3) Learn edge weights
in the prospective error graph from intervention outcomes (heeded→success vs
ignored→same-failure vs false-positive) with an eligibility-trace credit assignment, so the
graph sharpens on warnings that actually prevented recurrence. (4) Mine sequential mistake
patterns (episode → error) as frequent subsequences, not just co-occurrence communities —
the sequence, not the bag, is what predicts.

## Cascade completion — the "a pattern of nodes fires the missing node" vision, measured

**Measured finding (2026-07-15).** The intended behaviour - a subset of reflexes firing
predicts and surfaces the associated one - is real in the firing log. Association-rule mining
(Agrawal 1993) over 16,673 co-firing sets finds 280 rules at confidence >= 0.6 and lift >=
1.2. The strongest are genuine predictive cascades to RARE consequents:
`{green_not_trusted, his_hand_in_the_tape} -> pinned_file_two_tripwires` at 1.00 confidence
and 18.6x lift; `{hasty_pattern_match, ps1_hook_edit, windows_backslash_path} -> premortem`
at 15.3x. The caveat that keeps this honest: many rules funnel into a few high-frequency
consequents where the lift is partly base-rate, so rank cascades by lift-to-a-rare-consequent,
not confidence alone. `cascade_rules.py` mines these each dream. This is the empirical
foundation for cascade completion: when a rule's antecedent fires, surface its consequent
early - and, crucially, the surprise-weighted gate above ensures the surfaced consequent is a
rare high-value reflex, not a ubiquitous one. `CascadeCompleter` realizes this and was
demonstrated on the real reflexes: active {green_not_trusted, his_hand_in_the_tape} surfaces
pinned_file_two_tripwires (19x lift, 1.00 confidence); active {stat_before_you_stake,
verify_worker_model} surfaces his_call_is_final (7x); and it stays silent when no rule clears
the lift floor. CRITICALLY, unlike memory retrieval (156ms episode load -> needs a daemon),
cascade completion is CHEAP - a few hundred mined rules, subset checks per fire, microseconds -
so it is the ONE capability light enough to wire into a live per-fire hook without a daemon.
That makes it the highest-leverage next live integration: mine the rules each dream, check
them on each reflex fire, surface the predicted high-value consequent.

## Layer 4 — dreaming (offline consolidation)

**Running now.** A nightly cycle runs SWS-style filing (consolidate recent, make missed
associations, reconcile stale beliefs, wire dangling links) followed by REM-style
recombination (replay from salience, sit with what carried feeling, try the most distant
bisociations). It rebuilds the recall indexes, re-fits reflex thresholds from the shadow
ledger, re-embeds new memories, and now folds the day's new events into the association
network. The math brain's consolidation is stricter: quarantined replay that proposes
review tasks (orphans, contradictions, missing evidence, high-degree hubs) but can never
establish truth.

**State of the art.** This mirrors the complementary learning systems account of sleep
(McClelland, O'Reilly 1995; Diekelmann & Born 2010): slow-wave sleep replays and integrates
into the neocortical store, REM does schema-level recombination (Lewis & Durrant 2011).
Targeted memory reactivation and the sequential-replay literature (Giuditta's sequential
hypothesis) support the SWS-before-REM ordering the cycle already uses. Memory
reconsolidation (Nader et al. 2000) is the biological license for supersession — a recalled
memory becomes labile and can be rewritten.

**Gaps and next experiments.** (1) Replay is currently recency- and salience-driven; add
prioritized replay (Schaul et al. 2016) weighted by prediction error, so the dream spends
its budget on the memories the system was most wrong about — the highest-value
consolidation. (2) The REM bisociation picks distant pairs by graph distance; score
candidate pairs by the structural-hole / betweenness gain of connecting them (Burt 1992),
so the connections a dream proposes are the ones that most reduce the graph's fragmentation.
(3) Close the dream→growth→eval loop: every dream folds new events, re-fits thresholds, and
writes an eval receipt, so the network's development is measured cycle over cycle rather
than assumed.

## The development loop (what "keep developing based on new data" means, concretely)

Each dream cycle should execute one turn of a measured loop:

1. **Grow** — fold new tools, prompts, keywords, memories, and outcomes into the network
   (`dream_grow.py`), canonical nodes accumulating intensity.
2. **Measure** — run the replay eval (situation→query usefulness vs baselines), the reflex
   shadow eval (precision/recall on near-mistake contexts), and the firing-sequence audit
   (which cascades fired, which were heeded, which prevented a repeat).
3. **Compare** — a change earns live status only when it beats the current method on
   held-out data by a margin larger than path noise; agreement that could not have diverged
   is not corroboration.
4. **Record** — write the receipt with literal units separated (examples, contexts,
   attempts, sessions, agents), so scale is never overstated.

The three inputs the owner named are exactly the loop's signals: *how subagents are doing*
(delegation outcomes and verified served-model receipts), *how well we catch mistakes*
(reflex precision/recall on the day's real corrections), and *how firing sequences are
going* (the cascade→heed→outcome chain). None of these is a self-report to be trusted;
each is a measured receipt.

## Honest current scale

The live association network stands at 20,351 canonical nodes and 1.84M edges over 15,082
usage events — roughly an order of magnitude above the ~1,900-memory association graph, not
yet the aspirational 100×. It should reach that scale by growth from real use, never by
duplicating code lines or keywords per event (the owner's explicit constraint: that would
make the graph unusable). The growth engine is what climbs it; this audit is what keeps the
mathematics underneath it honest as it does.
