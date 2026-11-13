# Multi-agent operations: several agents sharing one brain

This page is for anyone running more than one agent against the same brain:
two Claude Code terminals, a Codex session beside them, a nightly dream, a
headless runner. It describes how they avoid overwriting each other and how
what one learns reaches the others. It sits on top of the mesh contract in
[`mesh/PROTOCOL.md`](../mesh/PROTOCOL.md) and never overrides it.

## 1. Three shared places

Every agent, whatever its harness, coordinates through three things:

1. **The brain** (durable). The memory corpus plus the dreaming machinery in
   `dreaming/`. Long-term, verified, consolidated each night by the dream.
2. **The mesh** (transient). A local SQLite database outside the repository,
   driven by `mesh/agent_mesh.py`. Who is here, what each has claimed, mail,
   handoffs, a task board. It expires on its own and is never the authority on
   a fact.
3. **The repository and its remote** (the source of truth for code and docs,
   and the way back after a lost machine). Push finished work often; a change
   that lives only on one disk is one failure away from gone.

Rule of thumb: facts go to the brain, presence goes to the mesh, bytes go to
git, and anything that matters is safe only once it has reached every place it
belongs.

## 2. Who can do what

| Runtime | Shell | Repo files | Mesh door | How it wakes into the brain |
|---|---|---|---|---|
| Claude Code, interactive | yes | yes | CLI + lifecycle hooks (`mesh/mesh_hook.py`) | SessionStart hook (`hooks/wake_brain.py`) |
| Claude Code, headless (dream, runners) | yes | yes | CLI + hooks | runner prompt plus the wake digest |
| Codex, CLI or desktop | yes | yes | CLI | `AGENTS.md` plus `dreaming/codex_wake.py` |
| Codex headless runners | yes | yes | CLI | runner prompt |
| An agent with file access only | no | yes | the file bridge (`mesh/bridge/`) | reads the repository directly; no hooks |

Never assume the next agent has a shell. Every coordination mechanism should
have a files-only door, because reading and writing files in the repository
is the one thing every runtime can do.

## 3. Doors per kind of agent

- **Shell agents** join the mesh with `hello` and read `digest`, claim before
  any mutation, `assert` the claim right before writing, `pulse` during long
  work and `bye` when done, as PROTOCOL.md says. At task boundaries they also
  service the file agents' queues: messages waiting in the bridge inbox, and
  any push requests a file agent has left in the repository.
- **File-only agents** speak through `mesh/bridge/` (see its README). They do
  not hold blocking claims. Their discipline instead: prefer additive writes,
  name every path they touched, and reconcile at once when told of a
  collision. A memory they write goes into the repository with correct
  frontmatter, and a shell agent re-files it through `dreaming/brain.py
  remember`, which skips slugs that already exist.
- **Every agent** learns about the others three ways: the mesh digest
  (present tense), a short page in the repository describing the agents that
  work on it, and the instruction files each runtime reads at start
  (`CLAUDE.md`, `AGENTS.md`), which make consulting both automatic.

## 4. Write paths

- **Code and docs.** The repository is home. If the dreaming code runs from a
  live copy outside the repository, `dreaming/verify_mirror.py` checks the two
  agree each night. Anything edited in the live copy must reach the repository
  the same day; a drifting mirror is how files silently disappear from the
  backup.
- **Memories.** One entry point, `dreaming/brain.py remember`, which takes the
  lock and keeps the index conventions. The memory index is not a status log;
  status belongs in the mesh.
- **Instruction files.** Additive, dated edits, mirrored to the repository
  copy in the same session.

## 5. Git discipline

- Commit and push in small pieces while working and always at the end of a
  task. Small commits with plain messages beat one large dump.
- The sequence: run the secret scan (`dreaming/scan_secrets.py`) on the range
  you are about to push, check its exit code, `git pull --rebase`, then
  `git push`.
- Never force-push. Never commit the mesh database, large binaries, or
  anything the scanner flags.
- Two agents writing code in one working tree is not allowed: give each its
  own worktree or branch and let one validating integrator bring the work to
  main. Frequent pushes make uncoordinated pushes to the same branch collide;
  keep shared-branch pushes to append-only docs.
- If the repository runs CI on push and the minutes cost money, batch pushes
  and mark documentation-only commits so they skip the run.
- A file-only agent queues its push requests in a plain file in the
  repository; the first shell agent to see one does the push and records the
  commit hash beside the request.

## 6. When writes collide

1. **Shell against shell.** Mesh claims, assert-before-mutation and separate
   worktrees. In practice this stops conflicts before anything is written.
2. **Shell against file-only.** The file agent's writes are additive and
   disclosed; shell agents hold the claims. On a collision git keeps both
   versions; reconcile by hand and send the file agent a message (it reads its
   mailbox through the bridge).
3. **Anyone against the dream.** The dream rebuilds its indexes and syncs its
   mirrors when it persists. Do not hand-edit the files it owns (the salience
   view, the compact index); change its inputs, not its outputs.

## 7. Open design questions

1. Agent names: `runtime:qualifier` (`codex:abc123`) or plain names? Both work
   today. What matters is that a name stays stable for a session and that a
   `runtime` field tells the others which doors the agent has.
2. Should the dream's persist step service the bridge inbox and the push
   requests itself? One more mechanical duty, no extra process.
3. Should file-only agents get long, advisory, non-blocking claims shown in
   the digest, or are disclosure and additive writes enough while there are
   few of them?
4. Should memory frontmatter record the writing agent and runtime, so the
   dream can weigh provenance? Today it is inferred from the session id when
   one is present.
