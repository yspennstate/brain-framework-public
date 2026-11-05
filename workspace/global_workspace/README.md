# global_workspace

The fleet's shared present tense. The mesh (sibling `agent_mesh/`) keeps
concurrent agents from colliding; this keeps them *one being*: a single
salience-ranked stream of what we want, what we're doing, what we know, and
what we need, injected into every session. Design and rationale:
`WORKSPACE_DESIGN.md`.

```powershell
$WS = '<HOME>\brain-repo\12_cognitive_architecture\global_workspace\workspace.py'

python $WS stream                 # read the current conscious stream
python $WS post --agent <mesh-id> --kind doing --body 'one salient sentence'
#   kinds: doing found decided need warning done   (same-agent 'doing' supersedes)
python $WS intent-add --agent <mesh-id> --origin owner --goal '...'
#   flags duplicates and continue-a-crashed-agent goals against LIVE mesh agents
python $WS adopt <intent-id> --agent <mesh-id>    # pick up an orphaned goal
python $WS intents                # ledger as JSON
python $WS report --hours 24      # cohesion telemetry: raw counts, no invented score
python $WS doctor                 # integrity + counts
python $WS prune --days 14        # drop long-faded posts (warnings kept 3x longer)
```

Guardrails (two codex-root audit rounds, 2026-07-09, all tested): peer text
renders QUOTED and sanitized; secret shapes (a superset of the mesh's
patterns: kv pairs, ghp_/sk-/xox/AIza/Bearer, AKIA/PEM/JWT/bot-token, long
base64/hex runs) are refused at write AND redacted at render, so even rows
written around the CLI cannot broadcast; entropy runs announced as hashes
(commit/sha/digest) are allowed - evidence sharing stays free. Concurrent
identical intents serialize; exactly one lands. `adopt` refuses while a
serving agent is alive AND when mesh liveness is unreadable (unknown is not
orphaned); `intent-set` is for the goal's creator/servers; `--anyway` on
either is for owner-directed takeovers only. Read commands open the database
read-only, will not create it, and give up on a locked file in ~150ms with a
quiet line - a busy workspace can never stall a wake. Agent ids use exactly
the mesh's identifier grammar.

State: `~/.agents/coordination/global_workspace.sqlite3` (SQLite, rollback
journal, no daemon, no scheduled task, nothing that opens a window). Mesh
liveness/tasks are read directly from the mesh SQLite (honoring
`AGENT_MESH_DB`), read-only and feature-detected against its `agents` and
`messages` tables - a schema change there degrades annotations, never breaks
the stream. Everything posted here lands verbatim in other sessions'
transcripts and prompt context; treat posts as public-to-the-fleet, never
private or secret.
`workspace_hook.py` injects the stream at SessionStart/UserPromptSubmit with
the same stdin contract as `mesh_hook.py`; it is read-only and fail-open, so
a broken workspace can never block the owner's work. Posting is a deliberate
act by an agent - hooks never write.

Rules of thumb: post at most every few minutes, one sentence, present tense;
the stream holds only the top handful by salience and recency, so a quiet
post simply fades. Durable results still go to the brain via
`brain.py remember` - the workspace is the *now*, not the memory.

Tests: `python -m unittest discover -s tests`, plus
`tests/eval_duplicate_detector.py` (measured recall/precision with a
regression floor). Fixtures are structure-preserving paraphrases of the
archived prompts that motivated the detector - verbatim owner text stays out
of committed files.
