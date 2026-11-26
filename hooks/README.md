# hooks/

The session's nervous system, as templates:

- `settings.hooks.template.json` — the full hook wiring (PreToolUse, PostToolUse,
  PostToolUseFailure, UserPromptSubmit, Stop, SessionStart, PreCompact, PostCompact), with install tokens. The
  bootstrap expands it to `bootstrap/settings.hooks.generated.json`; merge that
  object into your runtime's `settings.json`.
- `wake_brain.template.ps1` — the SessionStart wake ritual: identity, body state,
  compute state, the waking self-check (distrust the green light), your own last
  checkpoint, newest memories, last dream, state of mind, partner model, open
  questions, curiosity, conscience, the compiled reflexes, and the brain's forecasts.
  Expanded and installed as `~/.claude/wake_brain.ps1`.
- `brain_startup.template.md` — the orientation text the wake ritual prints first;
  installed as `~/.claude/brain_startup.md`.
- `commands/` — slash commands: `/dream` (run a dream cycle now), `/dont` and
  `/connect` (disconnect/reconnect a session from the brain), `/high` (broadcast the
  delegation method).

The hook scripts themselves live in `dreaming/` (instinct priming, danger mode,
conscience, guards, archiving, checkpoints) and `fleet/` (mailbox delivery); this
directory is the wiring and the wake surface. Keep every .ps1 ASCII and run it through
the PowerShell parser after any edit — a single bad literal here and sessions stop
waking into the brain.

The task checklist (`dreaming/task_checklist_hook.py`, wired on UserPromptSubmit, Stop and SessionStart) keeps
the partner's asks to every agent in one dated list - their words, the agent, what it is doing, a done mark
and a seen mark - and answers "what did I ask the agents?" from it. Agents run `working`/`done`; only the
partner marks `seen`. Store: `~/brain-repo/12_cognitive_architecture/task_checklist/` when that tree exists, else
`~/.claude/task_checklist/`; TASK_CHECKLIST_HOME overrides both. Codex sessions get the same hook from
`hooks.codex.template.json` (installer flag `--merge-codex`). Full description: `docs/TASK_CHECKLIST.md`.

The prompt hook (`dreaming/owner_prompts_hook.py`) hands every session the partner's recent prompts. It is wired as its
own UserPromptSubmit command, not through a dispatcher, because Claude Code hands the model at most 10,000 characters of
any one hook command's output and writes the rest to a file with a 2 KB preview; a block that shares a command with other
hooks loses its budget to them. Every delivery therefore goes out in parts of at most 9,500 characters, one part per
prompt (a queued part is re-split if the budget shrinks; under 2,500 the hook waits a prompt). Two modes. `--mode simple`
sends one block per session per hour, ordered for a reader who may see only its first part: what the partner keeps saying
(negative verdicts given more than once, clustered by the thing they named through the `objects` map per topic in the
lexicon file, marked `!!`, newest cluster first, and asks repeated on several days); RELEVANT TO THIS SESSION, every ask of
the last three thousand prompts on each thread the session's prompts have touched, newest first, with the open checklist
entries for it, the threads in the order the session touched them, newest touch first; then the last 48 hours on every
other thread, newest first. A thread touched between blocks arrives at once; a delivery still in flight at the hour
continues with the partner's newest asks put in front, and a fresh block starts once the queue is drained. On Codex the
output is wrapped as JSON (`hookSpecificOutput.additionalContext`): Codex discards plain stdout and cuts a context at
about 10,000 characters. `--mode layered` (the default) sends instead: at wake, the shape of the last three thousand prompts (a day-by-thread
table, older days folded into weeks), each thread's newest two asks and the last three hours verbatim (about 8 KB); the
first time a prompt touches a thread, one line per earlier day of that thread and then its last 48 hours, one line per ask
with re-asks folded and its open checklist entries (up to 15 KB, once per thread per session); then, at most hourly, only
what arrived since the session last looked. When the partner asks what they asked, the verbatim last hundred from
`owner_prompt_context.py` is served in full, and `--show "<thread>" --hours 48`, `--show all --hours 6`, `--show 1000`
and `--show 100` pull any slice on demand. The digest and files are rebuilt hourly by `dreaming/owner_prompts_hourly.py`
from the prompt ledger; `OWNER_THREADS.md` carries each thread's dated history and what is being worked on, and a thread's
checkbox follows the task checklist. Copy `owner_prompt_topics.example.json` to `owner_prompt_topics.json` and put your own
vocabulary in it, in the partner's own spellings; names that identify a person or a private system stay out of the code.
The builder reads the ledger incrementally through a small cache and falls back to a full read on any doubt;
`owner_prompts_hook.py --status` shows the build's age, the wiring and the last fires, and `--selftest` runs 30 checks on
a synthetic corpus without touching live state. To check delivery, look at the receiver, never the sender's log: a Claude
transcript records a plain-stdout hook's context as an attachment of type `hook_success`, and a content that starts with
`<persisted-output>` means the model saw 2 KB of it.

Two session-hygiene hooks sit in the dispatcher list. `dreaming/compaction_guard.py` (UserPromptSubmit) warns when
automatic compaction is not set up (no `autoCompactWindow` in `~/.claude/settings.json`, one above the limit, or
`autoCompactEnabled` false) or is not happening (the session's last call read far more than the window); it prints nothing
when all is well, and `--fleet` lists oversize sessions. `dreaming/advisor_nudge.py` (UserPromptSubmit and PostToolUse)
reminds a session that has an advisor tool to call it before committing to an approach and before reporting anything
done, and counts advisor calls in the transcript every twelve tool calls. The nudge ships off, because a stock install
has no advisor tool: turn it on by creating `~/.claude/advisor_nudge/ENABLED` or setting `ADVISOR_NUDGE=1`. Both take
`--selftest`.

Motivation hooks. `dreaming/motivation_hooks.py` gives an agent one short encouraging line at the moment it is likely to
need one: at the start of a session, after a second failed tool call within ten minutes, when its last message says it
cannot do something or is stuck, and after it has been corrected. A refusal by a rule or a guard gets a different line,
one that says the rule stands and points at the honest way through it. The thousand lines are
`dreaming/motivation_hooks.jsonl` (25 kinds of 40). `dreaming/motivation_hourly.py`, which the prompt hook imports, adds
one longer piece per session per hour, the first twenty minutes in (`motivation_hourly.jsonl`). The hook is quiet on
purpose: a lone failure is silent, failures a few seconds apart count as one, a prompt in a session's first ten minutes
is silent, and after any line the session waits before it gets another, so a failure right after the session-start line
says nothing. Wiring: the session-start line is a direct SessionStart entry in `settings.hooks.template.json`; prompts
and failed tool calls go through the dispatcher, which is why the template routes PostToolUseFailure to
`hook_dispatch.py`. Fires are logged to `~/.claude/motivation_hooks/fires.jsonl`; the file
`~/.claude/motivation_hooks/OFF` silences both. Both programs take `--selftest`. An install made before this section
existed gets the new SessionStart and PostToolUseFailure groups by running the installer again with `--merge-settings`.
