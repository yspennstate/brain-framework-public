# Mechanism map: what the harvested literature says, what the brain already does, what was measured tonight

Harvest of 2026-09-02: 1,053 PDFs (351 neuroscience, 351 mathematics, 351 network science), arXiv API
over 88 domain queries, text extracted for all 1,053, BM25 lane `lit_recall.py` (155k vocabulary).
The automatic candidate list per component is `MECHANISM_MAP_candidates.md`; this file is the hand
reading of it against the brain's own components and the measurements taken on the brain's own data
the same night. A paper's id is its arXiv number in the manifest.

| brain component | mechanism in the literature | what the brain does today | measured tonight | verdict |
|---|---|---|---|---|
| deliberate recall, fusion of channels | convex combination of min-max normalised scores vs reciprocal rank fusion (Bruch, Gai, Ingber 2022, arXiv:2210.11934) | bm25-first order, graph and semantic tails, salience tilt | 100 real prompt->read episodes: bm25 hit@10 0.22 / MRR 0.140, live 0.22 / 0.114, RRF 0.18 / 0.105, convex 0.22 / 0.138 | keep bm25-first; RRF dilutes direct matches here; the live order trails raw bm25 by 0.026 MRR - a tail/policy effect to diff per query |
| semantic lane as an associative memory | modern Hopfield / dense associative memory: iterate a softmax-weighted update toward a stored pattern (Ramsauer et al. 2020; Hu et al. 2024, arXiv:2404.03827, arXiv:2410.23126) | one-step cosine to the seed centroid | Hopfield iterations 2-3 at beta 8-512 never beat one step (best 0.05 / 0.032 vs 0.10 / 0.054) | REFUTED for this corpus: 26 percent of memories have a neighbour above cosine 0.90, so the update slides into mixture states; the theory's separation condition fails. One step is right. |
| attachment lane in recall | BM25 length normalisation (Robertson-Zaragoza probabilistic relevance framework) | attachment matches were COUNTED and promoted above bm25 unconditionally | positive hit@1 5/14 -> 13/15 after IDF + length saturation + freshness gate | SHIPPED (brain.py, brain_node_documents.py) |
| proactive warnings (the reminds-me hook) | error monitoring / ERN as a conflict signal (candidates arXiv:2410.00028, 2504.096xx); salience-network switching | standout-or-nothing gate with mistake-severity tilt and cross-session habituation | ablation on 60 prompts: SAL_FLOOR and MARGIN bind (+14pp, +9pp), ACT/CAND floors do nothing; mistake-class ceiling ~17 percent on the current prompt mix; a surfacing is read later 1.7 percent of the time | do not buy fires with looser gates; the next lever is precision (judged fires), not volume |
| mistake-first surfacing | prediction-error-driven consolidation (arXiv:2006.12616); reconsolidation on error (arXiv:1703.01357) | severity map from scars, audits and reflex charge boosts conductance | the map had not been rebuilt since 08-22 (80 scars at weight zero); rebuild moved the mistake-class fire rate 3 -> 12-13 percent | SHIPPED: builder wired into the dream |
| consolidation (dream) | complementary learning systems, prioritized replay by surprise (CLS-TD arXiv:1905.02636; prediction-error replay arXiv:2006.12616) | nightly SWS/REM passes, gists, reflex compilation, calibration ledger | not changed tonight; the dream's own eval step was crashing nightly (fixed) | candidate: rank the dream agenda by the calibration ledger's surprise (already recorded per prediction) - one evening's work, unmeasured |
| forgetting / salience decay | power-law forgetting and spacing (Optimizing Human Learning arXiv:1712.01856; DAS3H arXiv:1905.06873) | recency kernel, salience with charge and fade, usage tilt in recall | eval_type_utility (re-read rate by memory type): project 5 percent, feedback 2 percent vs episodic baseline 10 percent - bulk types sit below the baseline | candidate: spacing-aware wake spotlight (show a memory when its forgetting-curve probability of recall drops below a threshold), unmeasured |
| growth / structure | multiscale community detection (Markov stability, arXiv:1708.03167, PyGenStability arXiv:2303.05385); link prediction by embedding | Louvain communities Q=0.83 over 49; orphan/series/keyword edges; wiki-links | near-duplicate density: 2.6 percent of memories have a twin above cosine 0.95 (audit waves, watchdog entries), 26 percent above 0.90 | candidate: a nightly near-duplicate report that proposes supersession links for pairs above 0.97 (pattern separation), unmeasured |
| capacity of the store | dense associative memory capacity grows with the separation of stored patterns (Hu et al. 2024) | 14,843 memories, 384-d embeddings | the density measurement above is the capacity limit in practice: retrieval by content saturates when neighbours crowd | consistent with the Hopfield refutation |
| the graph path's self-priming (fixed 09-02) | self-sustained recurrent activity needs inhibitory balance or it saturates (local random networks, arXiv:1911.06921; the attractor landscape of distributed working memory, arXiv:2209.05002); source monitoring - a system must know whether an activation came from outside or from itself | the reflex hook deposited every graph fire it computed into the buffer it read back as priming: 2 of 40 hand-judged graph fires on-domain, 39 of 40 impossible unprimed; the gut now deposits nothing there and only a conscious recall primes | analogy drawn after the fix, not a tested mechanism; the test is the graph-path fire count over the following day |  |

## The honest summary
Two mechanisms were shipped because the data supported them (attachment scoring; the severity map
and the tripwire floors). Two were measured and refuted on this corpus (RRF; Hopfield iteration).
Three are named candidates with a measurement design and no result yet (surprise-ranked dream
agenda; spacing-aware spotlight; near-duplicate supersession proposals). The literature lane itself
is live (`lit_recall.py`) so the next agent can pull the papers by question instead of by memory.

## The literature as store material, and what the ranking did with it
A notes arm can only help on subjects the store holds, and a lexical ranking only serves what the
query's words reach. Measured on a 240-item research bank: the served notes named the result an item
needed for 59 percent of items on the original store, still 59 percent after the first 443 nodes
carrying exactly those subjects (283 statements from open-licensed arXiv papers via
`literature_to_drafts.py` and `literature_license.py`, 160 encyclopedia leads), and 65 percent only
after a further 739 statements from a harvest aimed at the bank's own areas, because the runner's
token-overlap ranking lets common tokens outvote the rare ones that name a result. BM25 with the
claim plus three argument steps reached 61 and 64 percent on the two stores; sentence-embedding
cosine (bge-small, `b12_notes_embed.py`) reached 68 percent on the smaller store and 73 on the
larger, and an eyes-on read confirmed the notes were the right results. Two rules follow: grow the store in the
bank's areas before serving, and measure the served notes' coverage (`b12_notes_relevance.py`) beside
every grade, because a notes arm is a treatment of exactly its coverage. One metric scar rides along:
a relevance metric that wanted the whole target phrase scored every ranking at 34 to 36 percent and
would have called retrieval inert; six cases read by eye showed otherwise. Read the misses before
believing a metric. And the grade at the end of it: on that bank, with sterile receipts on every
verdict, neither the 59-percent notes nor the 73-percent notes beat no notes (-2.8 and -2.9 points,
p 0.49 and 0.46, the two note arms tied), with the room to fail stated beside the null; the
mechanism split (notes help where a theorem statement carries a dropped hypothesis, hurt where it
legitimises a wrong citation) is the next experiment, not a result.
