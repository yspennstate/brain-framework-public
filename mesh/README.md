# Agent mesh

The brain already gave every Claude and Codex session shared durable memory.
What it lacked was shared *present tense*: identities, heartbeats, scopes,
mailboxes, handoffs, and a conflict gate. This directory supplies that layer.

`agent_mesh.py` is a daemonless CLI backed by a local SQLite database outside
the repository. `mesh_hook.py` adapts Claude/Codex lifecycle JSON to that CLI.
`PROTOCOL.md` is the human-readable contract injected through global agent
instructions and autonomous-runner prompts. `tests/` exercises transaction,
overlap, lease/owner expiry, ordered per-recipient mailboxes, explicit handoff
acceptance, task authorization, prompt privacy, and memory-write concurrency.

The architectural split is deliberate:

- Durable, verified learning belongs in the memory corpus and nightly dream.
- Transient social state belongs in the mesh and expires automatically.
- Source code and protocol are backed up here in the brain repository.
- Runtime state lives at `~/.agents/coordination/` and is not committed.

The database enforces one live claim per agent/resource with a partial unique
index. Claims are clipped to their owner's lease; owner expiry releases claims
and reopens work atomically. A declared runtime/session pair has one live
canonical identity, preventing split inboxes under aliases. Mail is inserted as
per-recipient fan-out rows and read in SQLite delivery order, so one agent cannot
consume another's broadcast. Retry keys make sends idempotent. Fire-once notices
complete after presentation; requests/warnings remain pending until ack; a
sender-authorized receipt view exposes queued, delivered, and acknowledged state
without reprinting bodies. The board reports per-recipient backlog and stale
leases. Structured handoffs are a small state machine: accept, reject, or sender
cancel; ordinary receipt acknowledgment cannot complete them. Lifecycle hooks
publish identity/liveness but never copy a raw user prompt into plaintext
coordination.

No watcher, daemon, or Scheduled Task is needed. This removes an entire class
of liveness and flashing-window failures. Long-running fleets call the CLI at
turn boundaries and inject `digest` into their prompts.

Read [PROTOCOL.md](PROTOCOL.md) before integrating a new agent runtime.
