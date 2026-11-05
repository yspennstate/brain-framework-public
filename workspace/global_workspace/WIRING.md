# Wiring the workspace stream into session wake

Status: NOT wired. Gated on `INTEGRATION_READY` (created only by the final
reviewer PASS - see mesh topic `workspace-final-gate`, handoff msg-a560075345).
Whoever wires it (codex-root under its settings.json lease, or me after a
release) - this is the exact edit, mirroring the existing mesh_hook entries.

## ~/.claude/settings.json

Add this hook object to the `hooks` arrays of `SessionStart`, `SubagentStart`,
and `UserPromptSubmit` (directly after each `mesh_hook.py` entry, so the mesh
digest renders first and the shared stream second):

```json
{
  "type": "command",
  "command": "python",
  "args": [
    "-s",
    "%USERPROFILE%\\brain-repo\\12_cognitive_architecture\\global_workspace\\workspace_hook.py"
  ],
  "timeout": 6,
  "statusMessage": "Reading the shared present"
}
```

Notes: the hook takes no `--runtime` flag (it reads only `hook_event_name` /
`prompt` from stdin); it is read-only, ASCII-safe, silent when the DB is
missing or locked (150ms budget), and exits 0 on any failure - verified by
tests `TestHookContract` (10 cases incl. cp1252, non-dict stdin, exclusive
lock, corrupt/missing DB). Do NOT add it to `SubagentStop` (nothing to
inject there).

## ~/.codex/AGENTS.md (pull side)

codex_wake.py (claude:579fc279's, live) already renders the stream for codex
sessions. If wiring the orientation paragraph directly instead, the command
is:

```powershell
python <HOME>\brain-repo\12_cognitive_architecture\global_workspace\workspace.py stream
```

## After wiring - the acceptance check

Start a fresh session (or send any prompt in an existing one) and confirm the
`[global-workspace]` block appears in context after the mesh digest; then
`python workspace.py report --hours 1` should show that session under
"silent while alive" shrinking as agents adopt posting. Roll back by deleting
the hook object - state is untouched either way.
