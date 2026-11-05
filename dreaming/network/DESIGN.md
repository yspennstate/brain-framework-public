# The big network: every tool, keyword, and prompt as a node

The owner's ask (2026-07-15): grow the association network two orders of magnitude — every
tool, every keyword, every prompt becomes a node — and make it fire the way real
associative memory fires: when a handful of nodes light up in a pattern that historically
preceded the owner writing a particular prompt, the agent should get that prompt handed
to it. This document is the method; the code beside it is the engine.

## What already exists, and what this adds

The brain already runs a memory-level association graph (spreading activation over
~10^3 memory nodes, Hebbian co-activation edges, salience decay) and a reflex-firing
layer (`dreaming/graph_fire/`) that matches actions against anchor token sets with
habituation and calibrated thresholds. The typed event graph (`dreaming/error_graph/`)
records per-session behavioral events (tool calls, errors, outcomes) in SQLite with
hashed/tokenized content. What none of them do:

- treat TOOLS, KEYWORDS, PROMPTS, FILES, and MEMORIES as one node universe (~10^5 nodes
  instead of ~10^3);
- store EPISODES — the sparse set of nodes active in the window before each archived
  owner prompt — and retrieve the prompt by pattern completion when the present starts
  to look like that past;
- do it with the standard machinery of network science and neuroscience rather than
  ad-hoc rules.

## Node universe (the 100x)

| class | source | expected count |
|---|---|---|
| tool | every distinct tool name + every distinct script/command basename invoked | 10^2 - 10^3 |
| prompt | every archived owner prompt (the prompt archive; one node each) | 10^4 |
| keyword | content vocabulary from prompts + memories, selected by document frequency band (drop stopwords and hapax; keep df in [3, 30%]) with IDF stored per node | 10^4 - 10^5 |
| file | files read/edited (path-normalized) | 10^4 |
| memory | memory slugs | 10^3 |
| reflex | compiled instincts | 10^2 |

Node ids are namespaced (`tool:Edit`, `kw:backtest`, `prompt:2026-07-01T12:33`,
`file:src/engine.py`, `mem:some_slug_2026_01_01`, `reflex:sim_flatters`). The store is
SQLite (nodes, edges, episodes) plus an npz adjacency cache for the hot path.

## Edges

Two edge families, both learned from the event stream, both weight-normalized:

1. **Co-activation** (undirected): nodes active inside the same event window (a turn, or
   a 120 s span, whichever is shorter — event boundaries follow the session stream). The
   weight is positive PMI, not raw counts: `w(a,b) = max(0, log p(a,b) - log p(a) - log
   p(b))`, so ubiquitous nodes (the `Bash` tool, the word "file") don't dominate. This is
   the standard cure for hub domination in semantic networks.
2. **Sequence** (directed, lagged): `a -> b` when b's window follows a's within k=3
   windows, weighted by discounted frequency. This is what lets a cascade anticipate
   rather than merely associate.

Homeostasis: per-node out-degree is pruned to the top-N edges by weight (N=50), the
network-science equivalent of synaptic scaling; without it the graph densifies into
uselessness as it grows.

## Firing: spreading activation with pattern completion

The runtime keeps a decaying **activation buffer** (leaky integrator): each observed
event injects activation into its node, activation decays exponentially (half-life a few
events), and one hop of spreading activation pushes a fraction along the strongest edges
(bounded frontier, k-winners-take-all — only the top-K active nodes propagate, K=32; the
cascade is bounded by design, per the standing directive that cascades must be bounded).

**Pattern completion** is the new retrieval mode, modeled on hippocampal CA3 / modern
Hopfield attractor retrieval: every archived prompt defines an episode pattern — the
IDF-weighted sparse set of nodes active in the window that PRECEDED that prompt. At
runtime the current buffer is compared against stored patterns by weighted overlap
(cosine over sparse IDF vectors). When overlap crosses the calibrated threshold AND at
least `k_min` (default 5) distinct pattern nodes are active, the episode completes: the
engine surfaces the prompt that followed that pattern — "last time the world looked like
this, this is what the owner asked for."

Retrieval discipline, learned from the reflex layer's scars:
- **Habituation / inhibition of return**: a retrieved prompt is suppressed for a cooldown
  after firing (no floods), and patterns that fire on every turn get their thresholds
  raised automatically (the aggressive-watcher lesson: a high intervention count means
  suspect the watcher).
- **Specificity gate**: overlap counts only above-median-IDF nodes toward `k_min` — five
  generic nodes are not evidence; the match must include the RARE conjunction.
- **Shadow first**: like graph_fire, the engine ships in shadow mode — it logs what it
  WOULD have surfaced (`network_shadow.tsv`) until replay evaluation shows precision
  worth paying attention for. A retrieval layer that cries wolf gets ignored, which is
  worse than absent.

## Learning rules

- Hebbian co-activation updates with exponential forgetting (recent regime outweighs
  stale regime; the same forgetting-kernel that works in the memory graph).
- Eligibility traces: when a surfaced prompt is USED (the owner or agent acts on it),
  edges along the completing pattern get a reward bump (credit assignment to what
  actually helped, not what merely fired) — the existing `eligibility_credit` pattern,
  generalized.
- Nightly consolidation: the dream cycle rebuilds IDF, re-prunes edges, re-fits firing
  thresholds from the shadow ledger (calibration: choose the threshold that would have
  achieved target precision on the trailing window), and archives episodes whose prompts
  were superseded.

## Evaluation (a pass that can fail)

`eval_replay.py` replays held-out sessions chronologically: build the buffer from events,
ask the engine to predict which archived prompt (if any) the owner writes next, score
top-k hit rate and precision-at-fire against what he actually wrote. Baselines: recency
(most recent prompt), frequency (most common prompt), and BM25 over the current window's
keywords. The engine earns live mode only when it beats all three on held-out data —
agreement that could have diverged.

## Scaling to the full node universe

The first live build (prompt archive + tool outcomes) lands at ~15k nodes — an order of
magnitude over the memory graph, one short of the target. The rest of the universe is
already recorded and waits on adapters:

1. The typed event graph's per-session stores (`error_graph_runtime/sessions/*.sqlite3`)
   hold every tool call and result as hashed events with stable action fingerprints —
   ingest `events(kind, surface, action_fingerprint, event_ts, session_hash)` as
   `tool:<fingerprint>` nodes (verify fingerprint stability across sessions on a real
   pair first; hashed labels are fine — pattern completion needs identity, not
   readability).
2. `retrievals.tsv` and the memory index give `mem:` nodes and their access times.
3. File-touch events from transcripts give `file:` nodes.
4. Widen the keyword band (df_min 2, df_max 40%) once the window count grows to match.

Together these put the universe in the 10^5 range the design targets.

## Growth is the standing task, not a one-time build

The directive is explicit that the graph must keep growing from real use — "new tools
invoked, new prompts written, new keywords and memories created" — rather than freeze at
its first build. Two mechanisms carry that:

- `grow.py` folds only events past the store's `grow_hwm` water mark into the existing
  graph. Nodes stay canonical (one per id); a repeated node accumulates access intensity
  (df, edge counts) instead of duplicating — exactly the directive's rule that
  "duplicating every line or keyword per event would make the graph unusable." Re-running
  on the same stream is a no-op; keyword bands (min and max document frequency) are judged
  against the whole corpus, not the increment, so a small nightly fold doesn't mistake its
  own vocabulary for ubiquitous.
- `dream_grow.py` is the dream-cycle entry point: it rebuilds the event stream from the
  deployment's current prompt archive, tool-outcome log, memory corpus, and retrieval log,
  then calls `grow.py`. Wire it into the nightly dream so every cycle enlarges the network
  by what the day's agents did. It runs at low priority and is safe to call every dream.

**The standing instruction for every agent** (embed in your doctrine): when you create a
tool, write a prompt, coin a keyword, or file a memory, that is new graph substrate. Do not
special-case it — just keep the event streams flowing (the hooks already log tool use and
prompts; `brain.py remember` writes memories; retrievals are logged), and let
`dream_grow.py` fold it in. The graph grows because the work grows.

## Returning the information, not just the node

`FireEngine.complete_with_content()` returns, for each completed pattern, the retrievable
payload of the node the cascade reached — the prompt's text, the memory's first paragraph
— so "a big enough signal fires the node and you get its information" is literal: the node
content table (`node_content`) holds it, populated for prompt and memory nodes at build and
growth time.

## Privacy

The engine and its formats ship here; the DATA never does. Episode stores and node-content
tables built from a real archive are deployment-local state (same class as the memory
corpus). The repo's tests use synthetic corpora only.
