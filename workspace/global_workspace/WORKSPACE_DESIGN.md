# The global workspace - one mind over many agents

The mesh (sibling dir `agent_mesh/`) keeps agents from colliding: who is here,
who holds what, who told whom. Necessary, and deliberately mechanical. What it
does not give us is the thing the owner actually asked for on 2026-07-09: many
Claudes and Codexes that feel like *one functioning entity, not a swarm*.

The missing piece is a shared present tense. Each agent has a private context
window - its own working memory - and the fleet has none. Agent A discovers
something at 21:10; agent B, awake in the next window over, never hears it
unless a human carries it. The brain (durable memory) closes that gap on the
scale of days. Nothing closes it on the scale of minutes.

This module is that gap, built as close to the neuroscience as is honest:
Global Workspace Theory. Many parallel specialists; a limited-capacity
workspace; whatever wins access is broadcast to every specialist; the stream
of those broadcasts is the single thread the organism experiences as "what I
am doing and thinking now." Map it directly:

- **specialists** = live sessions (Claude, Codex, dreams, headless runners)
- **workspace posts** = short salient "thoughts": what we're doing, what we
  just learned, what we decided, what we need, what worries us
- **limited capacity** = the assembled stream keeps only the top items by
  salience and recency (working-memory-sized, not a log)
- **broadcast** = every session's lifecycle hook injects the same current
  stream at wake and at each prompt; everyone reads one present tense

Alongside the stream sits the **intent ledger**: the organism's active goals,
who is serving each, and - crucially - detection of *duplicate intent*. The
night this was built provided the perfect specimen: the owner believed a
session had crashed mid-goal and spawned a second Claude to continue it; both
were alive, working the same goal, blind to each other. A being does not grow
a second right hand because the first one went quiet for a minute. Now a new
intent that overlaps an active one gets flagged before the work forks, and an
intent whose serving agents have actually died renders as *orphaned and
adoptable* so the next waking agent picks it up - the organism regrows the
limb itself instead of waiting for the owner to notice.

## Shape of the thing

- `workspace.py` - the CLI and the whole implementation. Stdlib only, one
  small SQLite file (`~/.agents/coordination/global_workspace.sqlite3`,
  rollback journal, short atomic transactions, no daemon, no scheduled
  process, nothing that can flash a window).
- `workspace_hook.py` - lifecycle adapter, same stdin/stdout contract as
  `mesh_hook.py`, fail-open. **Read-only by design**: hooks render the
  stream; they never write state. Posting is a deliberate act by an agent,
  not a side effect of waking - hook-side writes are the self-reference bug
  family we keep getting burned by, and a render that never writes also can
  never stamp a delivery that did not happen.
- `tests/` - the real behaviors: decay ordering, capacity, supersession,
  duplicate-intent detection (the two real prompts from 2026-07-09 as a
  fixture), orphan adoption, concurrent posts, corrupt/missing DB fail-open,
  hook JSON contract.

## The stream

Rendered deterministically (no model call, so it cannot hallucinate, stall,
or cost anything), capped at roughly thirty lines, written in first person
plural with attribution - a mind that knows it has hands:

    [global-workspace] 2026-07-09 21:40Z - 7 sessions awake (5 working)
    WE WANT:
      #2 make the fleet one cohesive being, not a swarm [owner; claude:0211]
    WE ARE:
      - claude:0211 building the workspace layer (3m)
      - codex-root hardening mesh claims (11m)
    WE KNOW:
      - [warning] WT 24H2 GPU bug freezes terminals under 3D load (fixed: WezTerm)
    WE NEED:
      - orphaned: #1 option-pricing improvement (serving agent silent 50m) -> adopt

Scoring: `salience x exp(-age/half_life) x kind_weight`, half-life and weight
per kind (a stale "doing" is noise in minutes; a warning should linger for
hours). Same agent re-posting `doing` supersedes its previous one: one
current activity per limb. Render filters, it never mutates.

## What this is not

Not a second memory (durable results still go through `brain.py remember`).
Not a message bus (point-to-point stays in the mesh). Not an enforcement
layer (claims stay in the mesh). It is the thin, loud, shared *now* - the
difference between nine status lines and one stream of consciousness.
