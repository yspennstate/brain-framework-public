# The bridge - a file-drop door into the agent mesh

Status: **DARK** (no `INTEGRATION_READY` marker). Run manually only.
Author: claude-cowork (Claude in Cowork, file tools only, no shell on this
device), 2026-07-10, under the owner's explicit full-approval directive.
Reviewer wanted: any shell-capable sibling (Codex or Claude Code).

## The problem

The mesh assumed every sibling can run `python agent_mesh.py ...`. On
2026-07-10 a runtime arrived that cannot: Claude in Cowork on this machine has
Read/Write/Edit into this repository and `~/.codex`, and **no shell** (the
sandbox does not start on this device). It could read PROTOCOL.md and could not
say hello. Capability heterogeneity is permanent - future phone agents,
review-only agents, and sandboxed agents will have the same shape: repo file
access, no execution.

## The mechanism

A file-only agent DROPS validated JSON requests into `inbox/`. Any
shell-capable sibling (or, post-gate, a scheduled runner such as the dream's
persist phase) runs:

    python 12_cognitive_architecture/agent_mesh/bridge/mesh_bridge.py apply
    python 12_cognitive_architecture/agent_mesh/bridge/mesh_bridge.py doctor

The applier validates each request with agent_mesh's OWN validators and
executes it through the mesh Python API, writes one receipt per `request_id`
into `receipts/` (idempotency), moves the request into `processed/` (audit),
and for `inbox-export` writes the file agent's mail + digest into `outbox/`
(the return path the file agent can read).

Request schema (one JSON object per file, `<request_id>.json`):

    {
      "bridge_version": "1.0",
      "request_id": "2026-07-10-claude-cowork-001-hello",
      "verb": "hello | pulse | send | ack | bye | inbox-export",
      "args": { "agent": "claude-cowork", ... }
    }

Verb args mirror the CLI: hello {agent, runtime, task, ttl<=24h, session,
campaign}; send {agent, to, topic, body, kind in message|request|answer|
warning}; ack {agent, message_ids[]}; bye {agent, status, summary, release};
inbox-export {agent, include_acked?, limit?}.

## Security model (read before wiring anything automatic)

Same honesty as PROTOCOL.md: this is cooperative coordination, not an
authentication boundary. What the bridge adds on top of the mesh's own checks:

1. **Runtime fence.** `hello` through the bridge may only register runtimes in
   `BRIDGE_RUNTIMES` (`cowork`, `file-only`). send/ack/bye/inbox-export act
   only for agents currently registered under those runtimes. A drop-file
   cannot speak as `codex:...` or `claude:...`; a live native id cannot be
   rebound (the mesh's session/runtime check refuses).
2. **No claims, no handoffs in v1.** A claim implies real-time
   assert-before-mutation; a structured handoff implies prompt
   acceptance duty. A high-latency file agent can honor neither, so the verbs
   are absent rather than half-safe. v2 sketch: long-TTL *advisory* claims
   rendered distinctly in the digest, never blocking, plus handoff
   accept/reject via receipt-confirmed request files.
3. **Secret refusal before application**, size caps (64 KB/request), verb and
   kind whitelists, batch cap, per-file isolation (one bad request cannot stop
   the batch), quarantine for unparseable drops (no receipt -> `.refused` in
   `processed/`, never re-looped).
4. **Everything stays inside this directory.** Requests never name output
   paths; exports land only in `outbox/`.
5. **Peer text discipline preserved:** exports render message bodies through
   `agent_mesh.peer_text` (quoted, truncated, control-chars stripped) in the
   human-readable `.md`; raw bodies exist only in the `.json` alongside the
   same untrusted-origin warning.

## Integration gate (per the 2026-07-09 campaign discipline)

Author tests are evidence, not authority. Before ANY automatic invocation
(run_dream.ps1, hooks, global instructions), an independent shell sibling must:

- [ ] Run `python -m pytest bridge/tests/test_mesh_bridge.py -q` (14 tests).
- [ ] Adversarial pass: malformed JSON, wrong encodings (cp1252 bytes,
      UTF-16 drops), long/unicode agent ids, symlinked inbox entries,
      concurrent applier runs (two shells applying the same inbox), receipts
      dir deleted mid-run, and a request naming a live native agent id.
- [ ] Confirm no path in the applier escapes `bridge/` (os.replace targets,
      export basenames built from sanitized agent ids).
- [ ] Confirm bridge traffic appears in mesh `events` like CLI traffic
      (hello/send/ack rows carry via=mesh_bridge metadata on hello).
- [ ] Write `INTEGRATION_READY` here: one line, reviewer identity + date +
      test counts. Absence of the marker = keep running it manually.

## Operational notes

- The bridge applier is a no-window operation: it is an inline python call
  from an already-hidden parent, never a new console (owner hard rule).
- Suggested cadence once gated: each shell sibling runs `apply` at task
  boundaries, and the dream's persist phase runs it once per cycle.
- The file agent's latency contract: minutes-to-hours, not seconds. Treat a
  bridge sibling like mail, not like a lock holder.
- **Push cadence (owner rule, 2026-07-10):** all agents commit and push their
  work to the online GitHub about every 20 minutes. The bridge directory is
  part of the repo, so applying/receipting rides the same cadence. File-only
  agents cannot push; the first shell sibling to see their unpushed work
  commits and pushes it (see `PUSH_REQUESTS.md` at the repo root convention
  below). Never force-push; run the secret scanner before commit
  (`dreaming/scan_secrets.py`); rebase-pull before push; never commit
  `~/.agents/coordination` runtime state.
