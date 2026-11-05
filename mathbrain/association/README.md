# Association layer (growth) for the math brain

This is the same association-network engine the general brain uses, dropped into the math
brain as an ADDITIVE layer that grows from mathematical usage — claim accesses, retrievals,
tool calls, proof attempts — the way the general brain grows from agent activity.

It is deliberately kept OUT of `src/math_brain/`. The math brain's authority is its typed
claim graph with evidence-specific verification (`models.py`, `retrieval.py`,
`verification.py`) and its quarantined SWS/REM consolidation (`consolidation.py`), which
proposes but never establishes truth. This association layer never feeds that pipeline: it
is a fast, lossy, usage-weighted index for "what tends to come up together and what
situation preceded which query," not a source of mathematical truth. A pattern completion
here is a hint to retrieve, never evidence a theorem holds.

Two growth mechanisms therefore run side by side in the math brain:

- **Native (authoritative):** `consolidation.py` replays the typed graph in SWS/REM style
  and emits quarantined review proposals (orphans, contradictions, missing evidence,
  high-degree hubs, reflex rehearsal, associations). This is how the *verified* graph grows
  — through human-promoted, evidence-checked review.
- **Associative (this layer):** `dream_grow.py` folds new math-usage events into the
  association network so recall gets faster and situation→query completion improves over
  time. Canonical nodes, aggregated intensity, shadow-first, causally evaluated — same
  discipline as the general brain (`DESIGN.md`).

To grow it from the math brain's own records, point `adapt_live.py` at the math brain's
usage logs (retrievals, attempt ledger) and run `dream_grow.py` each consolidation cycle.
`python -m unittest test_network` covers the engine on a synthetic corpus.
