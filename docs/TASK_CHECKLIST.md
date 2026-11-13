# The task checklist

When one person runs many agents at once, two things get lost. The person forgets what they asked which agent, and
the agents do not know that another agent is already on the same request. The checklist is one shared, dated record
of every request the owner makes of any agent, who is working on it, and where it stands. It lives in the brain tree,
next to the memories, so it outlasts every session.

## What an entry holds

- the request in the owner's own words, with the time it was made
- the agent it went to, and the runtime (Claude Code, Codex, a scheduled job)
- one line, written by the agent, of what it is doing about it, and dated progress notes
- a done mark with a one-line result, or blocked with the reason, or dropped
- a seen mark, which only the owner sets

Done and seen are separate on purpose. Done means the agent finished; seen means the owner has read the result. The
done-but-unseen entries are the owner's reading queue, and no agent marks one seen on the owner's behalf.

## Install

`bootstrap/install.py` copies the hook, `checklist.py` and the `/checklist` and `/seen` commands, and wires the hook
for both runtimes:

```
python bootstrap/install.py --project-dir <dir> --apply --merge-settings --merge-codex
```

`--merge-settings` adds the Claude Code wiring to `~/.claude/settings.json`; `--merge-codex` adds the Codex wiring to
`~/.codex/hooks.json`. Both write a dated backup first, keep every hook already there, and add nothing on a second
run. Without the two flags the installer writes the wiring to `bootstrap/settings.hooks.generated.json` and
`bootstrap/hooks.codex.generated.json` for merging by hand.

## What happens on its own

Once wired, an agent needs no instructions for the first half:

- On every prompt the hook registers the owner's request under the session that received it, in the owner's words,
  with the time. Short follow-ups ("ok do it", "yes") attach to that session's open entry instead of making new ones.
- The same hook prints what other agents have open, so a session sees at once that its request is already being
  worked on elsewhere.
- When the owner asks what the agents are doing, the hook prints the whole list, so the session answers from it.
- At each stop (and at session start in Claude Code) the hook reminds the agent of its own open entries.

The rest is the agent's part, by hand:

```
python ~/.claude/dreaming/checklist.py working <id> "<one line: what you are doing>"
python ~/.claude/dreaming/checklist.py note    <id> "<dated progress>"
python ~/.claude/dreaming/checklist.py done    <id> "<the result in one line>"
python ~/.claude/dreaming/checklist.py blocked <id> "<why>"
python ~/.claude/dreaming/checklist.py list
```

`seen <id|all|today>` is the owner's command (`/seen` in Claude Code). Add a line to the agent instructions asking
each agent to close its entry and name it in its final report; the template in `bootstrap/templates/` already does.

## Storage

The store is `~/brain-repo/12_cognitive_architecture/task_checklist/` when that brain tree exists, otherwise
`~/.claude/task_checklist/`; `TASK_CHECKLIST_HOME` overrides both. It holds three files:

- `ledger.jsonl`, an append-only record of every change, one JSON line each;
- `state.json`, rebuilt from the ledger if it is ever damaged;
- `CHECKLIST.md`, the view the owner reads: open, done and not yet seen, and seen, by day.

Keep the store under git with the rest of the brain tree, and several machines can share one list;
`task_checklist_merge.py` merges two ledgers.

## Testing

```
python -I dreaming/test_task_checklist_fresh_install.py
python -I dreaming/test_task_checklist_save_bytes.py
python -I dreaming/task_checklist_hook.py --selftest
```

The first sends one request through the hook as each runtime would, in an empty home, then runs working, done and
list and checks the result reaches `CHECKLIST.md`.
