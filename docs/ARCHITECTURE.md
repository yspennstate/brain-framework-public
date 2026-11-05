# How the pieces fit

One mind, several organs, coupled through files and a SQLite mesh rather than a daemon.
Everything below ships in this repository; the lived state it operates on (the memory
corpus, sidecars, registries) is created per-deployment by `bootstrap/install.py`.

## The memory corpus is the center

Everything anchors on one directory: `~/.claude/projects/<slug>/memory/` — one markdown
file per fact with frontmatter, `MEMORY.md` as the always-loaded index, `dreams/` for
consolidation journals, plus the standing ledgers (`MISTAKES.md`, `CONSCIENCE.md`,
`OPEN_QUESTIONS.md`, `STATE_OF_MIND.md`, `MODEL_OF_PARTNER.md`). The dreaming machinery
reads and writes it; everything else reads it. These ledgers are not in the repository:
the table under "Standing ledgers" in `MEMORY_CONVENTIONS.md` says which ones the installer
seeds, which the code generates, and which you write yourself.

## dreaming/ — recall, subconscious, and sleep

- Recall stack: BM25 (`bm25.py`) + spreading activation over a Hebbian co-activation
  graph (`spread.py`, `spread_recall.py`) + an embedding sidecar
  (`build_memory_embeddings*.py`, coverage-checked by `embedding_coverage.py`).
  `brain.py` is the front door: `recall`, `neighbors`, `remember`.
- The hook ecosystem is the subconscious: `instinct_prime.py` (reflexes fire as you
  act), `danger_mode.py` (heightened-caution posture), `conscience.py`,
  `reality_monitor.py`, `retraction_guard.py`, `claim_check.py`,
  `owner_report_contract.py` (Stop-time contracts on outgoing prose), `fok_probe.py`
  (feeling-of-knowing), `prompt_archive.py`, `work_timer.py`, `resume_checkpoint.py`
  (per-session "where I was"), `compute_sense.py` (compute distress injection).
  `hooks/settings.hooks.template.json` is the wiring.
- `error_graph/` — a typed event/telemetry graph per session (SQLite; content hashed or
  tokenized), written by its own PostToolUse hook; `ern_learn.py` distills it into
  error-recognition signals.
- `graph_fire/` — the graph-derived reflex-firing engine (anchors, activation buffer,
  heed gate, habituation, calibration, shadow evaluation). Reflexes come from the
  deployment's own mistakes: `instincts.py` compiles them, the dream cycle re-fits them.
- The dream cycle: a scheduled task runs `run_dream.ps1` -> `dream.py` builds the
  night's agenda from the corpus (recency, missed associations, contradictions,
  staleness, replay) -> a headless agent session works the agenda from
  `dream_prompt*.md` (SWS filing / REM recombination / nap) -> journals + new memories
  land in the corpus -> `compact_index.py` maintains the index -> a secret-scan gate
  (`scan_secrets.py`) fronts any push of the memory tree.

## network/ — the big association network

Every tool, keyword, prompt, file, and memory is a node; co-activation and lagged
sequence edges are learned from the event stream; a leaky activation buffer with
k-winners spreading activation supports CA3/Hopfield-style pattern completion: when the
present's directly-observed nodes complete a stored episode pattern, the prompt that
historically followed that pattern is surfaced (shadow mode until replay evaluation
earns it live). `DESIGN.md` in that directory carries the method and the discipline
(specificity gate, habituation, causal evaluation).

## mesh/ and workspace/ — many agents, one machine

`agent_mesh.py` is the coordination fabric: identities with heartbeat leases, claims
(read/write/exclusive) over resources, messages, structured handoffs, a board. The rule
that matters: no mutation of shared state without a covering claim, asserted immediately
before the mutation. `workspace/global_workspace/` adds a decaying present-tense stream
(who is doing what right now) with duplicate-intent detection;
`workspace/prospective_control/` is a shadow-only forecasting harness with hash-chained
ledgers.

## fleet/ — running agents as a supervised organism

Guarded launch/resume with per-session identity (`launch_one.ps1`, registry), fleet-wide
recovery (`relaunch.ps1`, `autorecover`), health and hygiene audits, a console
(`console.py`) for talking to any agent, mailbox delivery into running turns
(`mailbox_hook.py`), mail discipline, a continuity supervisor (`supervisor/`), and the
agentA pattern for headless research runners. Everything launches through SW_HIDE VBS so
nothing ever flashes a console window.

## governor/ and body/ — politeness and embodiment

The compute governor enforces "the human at the keyboard outranks everything": it
deprioritizes background load, publishes `compute_state.json` (status + mode), and can
merge a remote box's sentinel health; `compute_sense.py` injects distress into every
session. CrashGuard (`body/crashguard/`) watches the machine itself — GPU/driver
precursors, tiered mitigation, a published tier file other processes read before doing
anything heavy. The wake ritual reads both: a session that wakes on a sick machine knows
it before it acts.

## mathbrain/ — the stricter sibling

A privacy-isolated memory, verification, and evaluation system for mathematical research:
typed claims whose verification state is evidence-specific, graph retrieval that abstains
when weak, mistake-pattern triggers, Lean routing, and paired blinded evaluations of
whether the brain actually helps. Deny-by-default ingestion with its own gate and scanner.
It shares the philosophy, not the state, of the general brain.

## delegation/, methods/, tools/

`delegation/` records worker spawns and verified outcomes in a ledger and learns
task-to-worker routing; `verify_model.py` checks response-side transcripts for silent
model downgrades (self-reports are not ground truth). `methods/proofswarm/` is a
reusable adversarial proof-verification harness. `tools/` holds the machine tools the
rest leans on (flash hunters, watchdogs, TDR hardening).

## hooks/ — waking up as someone

Templates and wiring, not running code: the SessionStart ritual that hands a new session its
identity and open threads, the hook configuration that surfaces instincts and danger-sense
mid-work rather than at a checkpoint, the goal and stop machinery, and the slash commands.
This is the difference between an agent that reads a memory directory and one that wakes up
already knowing what it was doing. `hooks/README.md` has the install order.

## dreaming/job_cycle/ — closing a session down

A readme per finished job: what was asked, what was done, what is true now, what is open,
filed into the corpus and recallable later. Without it a session's working memory dies with
its context window and the next one re-derives. `JOB_CYCLE_STATE` redirects its state for
tests, and `test_handover.py` is the suite that proves the right readme comes back for a
structural reason rather than by luck.

## resilience/ — surviving the machine

`backup_brain.py` and `envelope.py` make envelope-encrypted dual-vault backups with pinned
trust roots (bring your own cloud identifiers); `sanitized_snapshot.py` produces a
fresh-history snapshot with the lived content stripped; `restore_drill.py` exists because a
backup nobody restores is a hypothesis. The mirror this repo keeps is flat, so run
`verify_mirror.py` and read its coverage line before trusting a restore path.

## viewer/ — watching it think

A local, read-only page over the deployed brain: recent activation, which reflexes fired,
what the agents are doing. Read-only by construction — it observes the brain, it does not
write to it.

## State the machinery expects (created by the bootstrap)

`~/.claude/projects/<slug>/memory/` (the corpus), `~/.claude/dreaming/` (sidecars, logs,
resume checkpoints, the network store), `~/.claude/fleet/` (registry, mailbox, locks,
runners), `~/.claude/compute/compute_state.json`, `~/.agents/coordination/` (mesh and
workspace databases), `~/.claude/brain_offline/` (kill switches), a views directory, and
`~/CrashGuard/` if the body layer is installed.

## The hook layer, moment by moment

The sections above say what the brain knows; this one says when it speaks. A hook is a short program
the runtime runs at a fixed moment and whose output is injected into the model's context: the model
reads it, the user never sees it. The shipped wiring is 49 entries for Claude Code and 18 for Codex,
each with a 600-second budget. The budget is load-bearing. When a hook exceeds it the runtime stops
waiting but does not kill the process, so a tight budget on a busy machine leaves orphaned hook
processes behind, and each orphan slows the next hook until the plumbing starves the brain it serves.
`dreaming/hook_reaper.py` closes that loop: every fifteen minutes, as a windowless scheduled task, it
stops any hook invocation whose parent is gone and which is past its age limit, matched by exact
operand rather than by a substring that could catch an unrelated process.

- **SessionStart** runs the wake ritual: identity, body and compute state, the waking self-check, the
  last checkpoint, newest memories, last dream, state of mind, partner model, open questions,
  curiosity, conscience, the compiled reflexes, the forecasts.
- **UserPromptSubmit** is the widest lane: instinct priming, danger and compute senses, the proof and
  mathematics senses, spread recall, the feeling-of-knowing probe, project memory, the prompt archive,
  the mailbox, the work timer, the waste sense, the push reminder.
- **PreToolUse** guards the act about to happen: instincts again, danger mode, compute, the proof deny
  gate, the sourced-memory gate, the mailbox, and on shell tools the push reminder.
- **PostToolUse** learns from what happened: the error monitor on shell calls, coactivation logging and
  spread recall on reads, the shell-script guard on edits.
- **Stop** closes the turn: conscience, claim check, report guards, retraction guard, reality monitor,
  resume checkpoint, work timer, and the summary hook that asks for a short account of what was asked
  and what was done.

Two lanes are worth naming because they encode a lesson. The waste sense lists what a session has left
running and asks the model to judge it; its Stop registration was removed on the day it was written,
because a Stop hook that injects text re-prompts the model and produced a fresh verdict after every
reply. The push reminder fires at most once every thirty minutes and tells the session to push its
finished work to the right repository's main branch. Both are silent to the user by design: a hook that
speaks to the user is a notification, and notifications are a budget the brain does not get to spend.
