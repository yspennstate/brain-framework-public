# Fleet recovery apparatus

Bring a crashed fleet of Claude + Codex agents back into individual visible Windows Terminal windows,
resumed and already working, with one command. Also: stop the whole fleet at once, and a convention for
how agents record their state so recovery never has to guess.

Location: `<HOME>\.claude\fleet\`

## The commands

    # after a crash - bring everyone back, one visible window each, resumed
    powershell -File <HOME>\.claude\fleet\relaunch.ps1              # SAFE default: gentle -p, auto-runs, no focus-stealing
    powershell -File <HOME>\.claude\fleet\relaunch.ps1 -Faithful    # reproduce EXACTLY how each agent was running (see modes below)
    powershell -File <HOME>\.claude\fleet\relaunch.ps1 -Interactive # force all interactive AND press Enter for you (steals focus briefly)
    powershell -File <HOME>\.claude\fleet\relaunch.ps1 -Discover    # also find recent unregistered sessions
    powershell -File <HOME>\.claude\fleet\relaunch.ps1 -DryRun -Faithful   # show what it would do (and each agent's launch mode), do nothing
    powershell -File <HOME>\.claude\fleet\relaunch.ps1 -Model claude-opus-4-8   # resume on a set model (e.g. when Fable is session-capped)

    # launch/resume ONE session, guarded - refuses if it's already live, cooling down, or disabled
    powershell -File <HOME>\.claude\fleet\launch_one.ps1 -Id <session-id>       # prompt/mode/title from the registry
    powershell -File <HOME>\.claude\fleet\launch_one.ps1 -Id <id> -DryRun       # preview; honors the guards, writes nothing

    # stop the whole fleet at once (never touches your own session, dreams, or Windows Terminal)
    powershell -File <HOME>\.claude\fleet\killall.ps1 -DryRun       # preview
    powershell -File <HOME>\.claude\fleet\killall.ps1               # do it

    # an agent records how to bring it back (run at startup and when its plan changes)
    powershell -File <HOME>\.claude\fleet\register.ps1 -Id <session-id> -Runtime claude `
        -Title "short name" -Task "what to keep doing" -Checkpoint "last-good state; next step" -Mode interactive

    # turn one off (stays off across relaunches until you turn it back on)
    powershell -File <HOME>\.claude\fleet\register.ps1 -Id <session-id> -Disable
    powershell -File <HOME>\.claude\fleet\register.ps1 -Id <session-id> -Enable

    # see the fleet's health at a glance (working / idle / dead) + machine state
    powershell -File <HOME>\.claude\fleet\status.ps1

    # prove the recovery invariants still hold (temp registry, opens no windows)
    powershell -File <HOME>\.claude\fleet\selftest.ps1

## The three relaunch modes

The default was flipped to be quiet after the injection version stole focus and spat keystrokes when the
mouse moved. Pick by what you want:

- **plain `relaunch.ps1`** - every agent comes back as `claude --resume <id> -p '<prompt>'`. The `-p` form
  auto-submits and streams into the window, so nothing presses Enter and nothing grabs your mouse. The
  catch: `-p` runs one batch turn and stops (it does not keep going), and the window can look quiet while
  the transcript grows. This is the safe "just get them back and see their state" button.
- **`-Faithful`** - each agent comes back the way it was actually running, read from its `mode` in the
  registry. `mode:"interactive"` agents reopen as a persistent TUI with the prompt pre-filled and a yellow
  "PRESS ENTER to resume" banner - you press Enter (still no injection). `mode:"gentle"` agents come back
  `-p`. This restores the current setup after a crash.
- **`-Interactive`** - forces the full interactive TUI for everyone and presses Enter for you. This is the
  only path that injects: it brings each window to the foreground and, if Windows' foreground lock refuses,
  presses Alt (`SendKeys '%'`) to release it, then sends `{ENTER}` - only into a window it created. Hands-off,
  but it steals focus for a minute or two at launch. Use it when you want zero clicks and don't mind that.

`mode` is captured from how the agent is actually running (a `-p` in its command line = gentle, otherwise
interactive). Run `snapshot_modes.ps1` to re-capture the current setup into the registry after you change
which agents are interactive vs headless.

## One session at a time: launch_one.ps1

Every launch path goes through `launch_one.ps1`; a bare `claude --resume` is the unguarded path that
caused the 2026-07-12 duplicates (a second recovery pass re-ran the side scripts against a live fleet
and duplicated 6 sessions - two processes appending to one transcript). launch_one refuses instead of
duplicating, in this order (exit codes are a frozen contract; nudge.py consumes them):

- registry `disabled: true` -> exit 5 (`-Force` overrides)
- a live claude/codex process already carries the id -> exit 3 (`-Force` only after you have
  tree-verified those pids are zombies - a peer's say-so nearly killed a live session on 07-11)
- `locks\<id>.launch.lock` fresher than 10 minutes -> exit 4 (no `-Force` bypass; the lock is left
  behind after every launch on purpose - it IS the cooldown against rapid double-fire). A lock older
  than the last boot never blocks, so a pre-crash lock cannot refuse the post-crash recovery.

`-Prompt`/`-Mode`/`-Title` override the registry; absent, the resume prompt is built from task +
checkpoint exactly like relaunch.ps1. The run_*.ps1 / restore_*.ps1 side scripts are now thin wrappers
that pass their old title/prompt/mode through launch_one: same window as before when the session is
dead, a refusal instead of a duplicate when it is not. relaunch.ps1 itself takes a 10-minute
`locks\relaunch.pass.lock` per non-DryRun pass, so a second full recovery pass inside the window
refuses too (`-DryRun` is exempt; delete the lock to override deliberately).

## How agents store their state (the registry)

`registry\<session-id>.json` per agent: `id, runtime, title, task, cwd, model, mode, checkpoint, disabled,
updated`. `register.ps1` writes it; `registry_refresh.py` (a Stop hook) keeps the `checkpoint` fresh every
turn so it never goes stale. `relaunch.ps1` reads it to resume each agent with the right prompt instead of
reverse-engineering the task from transcripts.

- **`checkpoint`** is the important one - the last on-disk state plus the very next step - so a resumed
  agent picks up where it was, not from scratch.
- **`mode`** decides how `-Faithful` brings it back (interactive vs gentle).
- **`disabled`** - set with `register.ps1 -Disable`. Every relaunch mode skips it, so anything you turn off
  stays off until `-Enable`. A re-register never silently clears it.
- The files are written UTF-8 **with a BOM on purpose**: PowerShell's bare `Get-Content` reads a no-BOM
  UTF-8 file as ANSI and mangles non-ASCII (em-dashes become `Ã¢`) into the resume prompt. Python readers
  use `utf-8-sig`, which handles both. Do not switch the writers to BOM-less.

Agents that never register can still be recovered with `relaunch.ps1 -Discover`, which finds recent
top-level Claude transcripts and root Codex sessions (skipping subagents, delegation workers, and dreams)
and adds them with a generic "resume your task" prompt and `mode:"gentle"`.

## The parts that bite

- **`claude --resume <id> 'prompt'` only pre-fills the box; it does not submit.** That is why interactive
  recovery either asks you to press Enter (`-Faithful`) or injects it (`-Interactive`). `-p` and
  `codex resume` both auto-submit, so they need no Enter.
- **A resumed session may write to a forked transcript**, not the original `<id>.jsonl`. "Did it submit?"
  is answered by watching for growth in the original or any new file.
- **Never kill the WindowsTerminal process.** All WT windows share one process, so killing it kills your
  own controlling session and every other tab. `killall.ps1` only stops the claude/codex processes.
- **Resume runs from the session's real cwd.** A cwd under `.claude` (a tool dir) is a corruption and
  `claude --resume` fails with "No conversation found"; relaunch falls back to `<HOME>`.
- **Keep heavy compute off this laptop.** The crashes are GPU/PCIe load-correlated; the resume prompt
  tells every agent to push heavy work to the Azure box.

## Recovery playbook

1. `relaunch.ps1 -DryRun -Faithful` - confirm the list and each agent's launch mode look right.
2. Pick a mode: plain to just get them back quietly, `-Faithful` to restore the exact setup (you press
   Enter in the interactive windows), `-Interactive` for hands-off (expect brief focus-stealing).
3. Watch each window; each ends its first turn with a status line. Interactive windows that are still
   pre-filled show the yellow "PRESS ENTER" banner.

`selftest.ps1` proves the load-bearing guarantees (off stays off, mode round-trips, files keep their BOM,
relaunch skips disabled and reproduces each mode, launch_one refuses live/locked/disabled launches, the
side scripts stay routed through launch_one, and the relaunch pass-lock refuses a rapid second pass)
against throwaway registry/locks dirs, opening no windows. Run it after changing any of these scripts.
