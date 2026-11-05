# Common-sense hooks

A small set of plain questions a sensible collaborator asks before certain actions: before a recursive delete,
when a number looks too good, when the user asks "you sure?", when a setting changed and the result did not. Each
question lives in its own file, `csNNN_<name>.py`, as a single `HOOK` dict. The engine, `cs_core.py`, scores every
hook against the hook payload and prints the line of the one or two that fit best.

A printed line looks like this:

    [common sense CS006 | You sure? means check it a second way] The user asks whether you are sure: have you
    checked it a second, independent way, or are you about to repeat the first answer? Re-derive it by a different
    path ... Why: Repeating an answer with more confidence is not a check. (unjudged; a question, not a verdict.
    Judge: cs_core.py --judge CS006 on|off)

The line is a hint. It says the situation resembles one where the rule applies; the agent still decides whether it
does.

## How a hook is chosen

1. `gate`: plain substrings, one of which must occur. Most payloads stop here, so a quiet call costs a few
   milliseconds.
2. On a tool call the hook must also match the command (`cmd`) or the file path (`path`): 3 points.
3. `kw2` patterns add 2 points each (at most 4), `kw` patterns 1 point each (at most 3). The total must reach
   `need` (prompts) or `need_tool` (tool calls).
4. Any `not` pattern vetoes the hook.
5. Among the survivors, a `fallback` hook gives way to a specific hook of the same `area`. The rest are ranked by
   score, priority and judged precision.
6. Back-off, per session: at most two lines on a prompt and one on a tool call; after a tool-call line, tool calls
   stay quiet for five minutes; one hook speaks again only after 45 minutes, then 2 hours, then 8 hours.
7. Judging: `cs_core.py --judge CS006 off` records a fire as off target. A hook judged off target on six of its
   first ten judgments, or on every one of its first four, goes quiet. The printed line carries the count, so the
   reader knows how far to trust it.

Text the harness injects into a prompt (`<system-reminder>` and similar blocks) is removed before scoring.

## Wiring

Claude Code, in `settings.json`:

```json
"hooks": {
  "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python /path/to/common_sense/cs_core.py --all"}]}],
  "PreToolUse": [{"matcher": "Bash|PowerShell|Edit|Write|MultiEdit",
                  "hooks": [{"type": "command", "command": "python /path/to/common_sense/cs_core.py --all"}]}]
}
```

Codex, in `hooks.json`: the same command with `--codex`, which wraps the lines as
`{"hookSpecificOutput": {"hookEventName": ..., "additionalContext": ...}}` (Codex discards plain stdout).

The file `ENABLED` beside the engine is the switch: delete it and every hook is silent. State (back-off, the fire
log, judgments, the registry cache) goes to `CS_STATE_DIR`, by default `state/` here.

## Writing a hook

Copy an example, give it the next id, and write its `fire` and `quiet` examples first: `p:<prompt>`,
`c:<command>`, `w:<path>|<content>`. Then tune `gate`, `kw2`, `kw` and `need` until

    python cs_core.py --selftest

passes. The self-test wakes every hook on each of its fire examples, keeps it silent on each quiet example, checks
that no line is cut, and runs the engine's own controls: a harmless payload wakes nothing, a hook does not fire
twice in one session inside its back-off, six off-target judgments silence it, `ENABLED` absent means silence,
and the Codex output is the JSON Codex reads. `cs_core.py --query "<text>"` shows which hooks a text would wake
and with what score.

The quiet examples matter as much as the fire examples. A hook that fires on everything that shares its words is
noise, and noise teaches agents to skip every line.
