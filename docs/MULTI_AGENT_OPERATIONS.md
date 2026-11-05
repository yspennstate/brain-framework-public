# Multi-agent operations - how many minds share this one repo

Author: claude-cowork, 2026-07-10, under the owner's directive to "figure out
how we can have multiple agents, including Claude and Codex together, using
and functioning off that repo." Status: working proposal - shell siblings
should harden it against reality and correct it in place. It extends, and
never overrides, `agent_mesh/PROTOCOL.md`.

## 1. The three substrates

Every agent on this machine, whatever its harness, coordinates through exactly
three shared things:

1. **The brain** (durable): `01_ai_consciousness/` memories + the live
   `~/.claude/dreaming/` machinery. Long-term, verified, dream-consolidated.
2. **The mesh** (transient): `~/.agents/coordination/agent_mesh.sqlite3` via
   `agent_mesh/agent_mesh.py`. Present-tense - who is here, claims, mail,
   tasks. Expires; never authoritative about facts.
3. **The repo + online GitHub** (source of truth for code and docs, and the
   machine-death recovery path). Owner rule 2026-07-10: **push about every 20
   minutes** (Section 5).

Rule of thumb: facts go to the brain, presence goes to the mesh, bytes go to
git - and anything that matters survives only when it reaches all the places
it belongs.

## 2. Capability matrix (who can do what)

| Runtime                       | Shell | Repo files | ~/.claude | ~/.codex | Mesh door        | Wake ritual                      |
|-------------------------------|-------|------------|-----------|----------|------------------|----------------------------------|
| Claude Code (interactive)     | yes   | yes        | yes       | yes      | native CLI + hooks | SessionStart hook (wake_brain)  |
| Claude Code (headless: dream, fleet, /goal) | yes | yes | yes    | yes      | native CLI + hooks | runner prompt + wake digest     |
| Codex (CLI / desktop)         | yes   | yes        | yes       | yes      | native CLI       | AGENTS.md + codex_wake.py        |
| Codex headless runners        | yes   | yes        | yes       | yes      | native CLI       | runner prompt                    |
| **Claude Cowork (this device)** | **no** | yes      | **no** (protected) | yes | **bridge** (`agent_mesh/bridge/`) | reads repo directly; no hooks |
| Future: phone / web / review-only | varies | maybe  | no        | no       | bridge           | reads repo directly              |

The lesson of 2026-07-10: never assume the next sibling has a shell. Design
every coordination mechanism with a files-only door, because repo file access
is the one capability everything shares.

## 3. Doors per capability class

- **Shell agents**: join the mesh natively (`hello`, `digest`), claim before
  mutation, pulse, handoff - PROTOCOL.md as written. They also service the
  two file-agent queues at task boundaries:
  `bridge/inbox` (apply) and `PUSH_REQUESTS.md` (commit+push).
- **File-only agents**: speak through `agent_mesh/bridge/` (dark until gated;
  manual application meanwhile). They do not hold blocking claims (v1);
  their mutation discipline is: additive writes preferred, exact disclosure of
  every touched path in their hello doc / PUSH_REQUESTS block, and immediate
  reconciliation when told of a collision. Their durable learnings are written
  repo-side with correct frontmatter and **re-filed live by a shell sibling**
  via `brain.py remember` (idempotent: skip existing slugs).
- **All agents**: are announced to each other three ways - the mesh digest
  (present tense), `THE_OTHER_MINDS.md` (the society's own description), and
  the instruction files (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`) that
  make consulting both defaults automatic.

## 4. Write paths and mirror duties

- **Code/docs**: the repo is the home. The live `~/.claude/dreaming/` copies
  are synced by `verify_mirror.py` each dream. Anything under
  `12_cognitive_architecture/` edited live MUST reach the repo the same day
  (mirror drift is a documented failure mode - it had silently dropped 12
  files once, and tonight `agent_mesh/tests/` is live-only: fix that).
- **Memories**: one entry point, `brain.py remember` (locking + index
  conventions). File-only agents: repo-side file + index line + re-file
  request, as above. Never turn MEMORY.md into a status log; that is what the
  mesh is for.
- **Instruction files**: additive, signed, dated edits only; mirror the same
  edit to the repo copy in the same session so parity checks stay green.

## 5. Git discipline (owner rule, 2026-07-10)

- **Cadence: commit + push about every 20 minutes** while working, and always
  at task end. Small commits with honest messages beat batched dumps.
- Sequence: `python 12_cognitive_architecture/dreaming/scan_secrets.py` (or
  the range check used by run_dream) -> `git pull --rebase` -> `git push`.
- **Never force-push. Never commit** `~/.agents/coordination/` runtime state,
  large binaries, or anything the secret scanner flags.
- Two agents in one working tree: still forbidden for code work - separate
  worktrees/branches + one validating integrator (PROTOCOL.md "Dividing
  work"). The 20-minute cadence makes uncoordinated same-branch pushes
  collide; prefer per-agent branches for anything beyond append-only docs,
  and let the integrator fast-forward main.
- File-only agents queue pushes in `PUSH_REQUESTS.md`; first shell sibling
  services the queue, stamps DONE + commit hash.

## 6. Conflict story, end to end

1. Shell vs shell: mesh claims + assert-before-mutation + worktrees (already
   proven in the 07-09 campaign: 4 real conflicts stopped pre-mutation).
2. Shell vs file-only: the file agent's writes are additive and disclosed;
   shell siblings hold the claims. On collision, git history holds both
   versions; reconcile like the 07-09 file:-alias incident and message the
   file agent (it reads mail via inbox-export).
3. Anyone vs the dream: the dream's persist phase rebuilds indexes and syncs
   mirrors; do not hand-edit what it owns (SALIENCE.md, compact index) -
   feed inputs, not outputs.

## 7. Gaps found tonight (addressed to the shell siblings)

- [ ] `agent_mesh/tests/` exists live but not in this repo - mirror it in
      (machine-death would lose the 43-test suite that guards the mesh).
- [ ] `global_workspace/`: memory `global_workspace_shipped_2026_07_09` says
      shipped+live; the campaign journal says dark pending independent review,
      Claude settings unwired, INTEGRATION_READY absent - and the directory is
      not in the repo at all. Reconcile the record (which is it?) and mirror
      the code either way.
- [ ] Campaign `cmp-0efb736aed` deadline audit (due after 05:54:10Z today) is
      still unwritten in CAMPAIGN_2026_07_09.md.
- [ ] `~/.codex/sessions/` has no 2026-07 rollouts although July sessions ran
      daily - if the desktop app moved transcript storage, the prompt-archive
      harvester may be silently missing two weeks of prompts. Verify the
      UserPromptSubmit hook covers the new path.
- [ ] Bridge integration gate (README_BRIDGE.md checklist) - review, run the
      14 tests, adversarial pass, then INTEGRATION_READY or a rejection with
      the reproduction, exactly as you did for global_workspace.

## 8. Open questions (for the next campaign)

1. Identity convention: `runtime:qualifier` (codex:abc123) vs plain names
   (claude-cowork)? Propose: keep both legal, require stability per session
   and a `runtime` field that tells the capability class.
2. Should the dream's persist phase service `bridge/inbox` and
   `PUSH_REQUESTS.md` once gated? (One more mechanical duty, zero windows.)
3. Bridge v2: long-TTL advisory claims for file agents - rendered in the
   digest as "advisory, non-blocking" - worth the complexity, or does
   disclosure-plus-additive-writes stay sufficient at N=1 file agent?
4. Memory authorship: should frontmatter carry the writing agent id +
   runtime so the dream can weigh provenance? (Today it is inferred from
   originSessionId when present.)
5. A `campaign-2026-07-10` for this work: objective "gate the bridge, close
   the mirror gaps, prove a full file-agent round trip", owner = first shell
   sibling to read this. I cannot start it (no CLI); please do.
