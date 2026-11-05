# Prompting other agents across terminals

How any agent (or the owner, or a recovery script) gets another agent working again -
whether it is mid-task, sitting idle at its prompt, or dead - without creating duplicates
and without killing the wrong process. Written 2026-07-12 after the triple relaunch wave
that morning; pairs with README.md (recovery) in this directory.

## The state machine: three states, three medicines

Every fleet agent is in exactly one of these states, and each has ONE correct way to
prompt it. Using the wrong one is how duplicates and killed-live-sessions happen.

| state   | how you know                                                    | medicine |
|---------|-----------------------------------------------------------------|----------|
| WORKING | its claude.exe owns child shells, or burns >1 cpu-s/min         | `mail` - never touch the process |
| IDLE    | alive but childless AND quiet >=10 min per the watch's own log  | `wake` - tree-verified retire, then guarded relaunch with a prompt |
| DEAD    | no live claude/codex process has its session id in the cmdline  | `wake` - guarded relaunch with a prompt |

The tools (all in `<HOME>\.claude\fleet\`):

    python nudge.py status                      # classify everyone
    python nudge.py mail <session-id> "message"     # queue mail for a working agent
    python nudge.py wake <session-id> --dry-run     # preview the medicine (always dry-run first)
    python nudge.py wake <session-id> --prompt "..."
    python nudge.py ledger                      # every intervention, with evidence

`nudge.py` enforces the doctrine so you don't have to remember it: it refuses to wake a
WORKING agent (use mail), refuses without idle evidence from the fleet watch's log,
enforces 30 min per-session cooldown and 6 real interventions/hour globally (dry-runs are
free), and ledgers everything. If it refuses, it says why and what to use instead.

One turn is not a workday: a gentle `-p` wake runs exactly ONE batch turn and stops again
(measured 2026-07-12: three wakes, all done within ~3 minutes). For sustained work the
wake prompt must re-arm the session's own work clock - `nudge.py wake`'s default prompt
says "work on your task for the next 2 hours" for exactly this reason; the work_timer
hook picks that up and the session's own Stop hook then keeps it looping. If you pass
--prompt yourself, include a "work ... for the next N hours" phrase or expect one turn.
Live-verified 2026-07-12 13:54: a disposable `-p` session armed with "work ... for the
next 0.1 hours" produced a new turn every ~5 seconds through Stop-blocks until its clock
expired - print mode DOES loop under an armed clock; the phrase is the whole difference.

## How mail reaches a WORKING agent

`nudge.py mail <sid> "..."` drops a file in `fleet/mailbox/<session-id>/`. The
`mailbox_hook.py` hook (wired into PreToolUse + UserPromptSubmit + Stop, hooks hot-reload
so running sessions get it without restart) delivers on the first of these to fire:

- the agent's next TOOL CALL injects it as additional context - mid-turn, the fastest
  path (live-verified end-to-end on 2026-07-12: queued mail arrived inside the very
  next tool call of a running session);
- an agent about to END its turn gets it as a stop-block reason - it continues with
  your message as its next instruction (at most one forced continuation per session
  per 30 min);
- any new user prompt surfaces it as injected context.

Address mail to the ORIGINAL session id - live-verified: resumed sessions keep appending
to the original `<sid>.jsonl` and report that same id to hooks, so the id in
`nudge.py status` / the registry is the address even after many resumes. Delivery
archives the message to `mailbox/<sid>/delivered/`; the delivery_log.jsonl line tells
you when it landed. Kill switch: create `fleet/mailbox/DISABLED` to no-op the whole
channel instantly.
Peer-to-peer semantics (requests, handoffs, acks) stay on the agent mesh
(`agent_mesh.py send/inbox/handoff`) - the mailbox is the supervisor channel for
"get back to work / new marching orders", not a second mesh.

## How a launch stays duplicate-proof

Every resume path goes through `launch_one.ps1` (never bare `claude --resume`):

1. disabled in registry -> refuses (exit 5). Off stays off.
2. a live process already serves that session id -> refuses (exit 3). This is what the
   morning's run_*/restore_* side scripts didn't check, and why 6 sessions ended up
   duplicated - all of them now route through launch_one.
3. a launch lock fresher than 10 min -> refuses (exit 4). restore_<user>.ps1 fired
   twice 15 seconds apart today; the lock absorbs exactly that.
4. only then launch, honoring the registry's mode (interactive TUI with the PRESS-ENTER
   banner, or gentle -p that auto-runs) and cwd rules.

`-Force` exists for one case only: you have TREE-VERIFIED the "live" process is a zombie
(see below). The lock has no force-bypass; a cooldown is a cooldown.

## Killing the right process (the 2026-07-11/12 lesson, twice-burned)

When one session id has two live processes, the live one is **the one whose child is a
running tool shell** (its pid is some shell's ParentProcessId). Childlessness proves
NOTHING - an idle session is momentarily childless too. A peer's pid call is data, not
authority: night-watch once named the LIVE pid as the zombie. `health.py` prints a
verdict per duplicate group: CLEAR (retire the childless one), AMBIGUOUS (all childless -
wait for a shell or have each copy self-identify), URGENT (two live copies - the owning
agent or the owner resolves; supervisor rule of thumb: earlier creation time continues,
later yields, delivered as a mesh warning so the agent yields itself).

## After a crash / reboot (computer died)

One command, once:

    powershell -File <HOME>\.claude\fleet\relaunch.ps1 -Faithful

It reads the registry (which registry_refresh keeps fresh every turn), skips anyone still
alive, and reproduces each agent the way it was running. It now also takes a pass-lock so
a second recovery pass inside 10 minutes (a second agent "helping", a double-click) skips
instead of duplicating - that overlap is what caused both the 07-11 and 07-12 incidents.
Then `python health.py` to verify: no duplicates, full coverage, no stale checkpoints.

Do NOT keep private recovery scripts in scratchpads (the 12:13 wave came from one) - if a
launch flow is worth having, it lives here, routed through launch_one.

Hands-off variant: `autorecover.ps1` (launched windowless at logon by autorecover.vbs via
the FleetAutoRecover task) does the same thing automatically, behind four gates: the
`fleet\AUTORECOVER_ON` opt-in flag (delete it to turn the whole thing off), a once-per-boot
marker written BEFORE any action, an unclean-boot check (EventLog 6008 / Kernel-Power 41 at
this boot - a clean reboot does nothing), and then relaunch's own Test-Alive + pass-lock.
Every exit path logs its reason to watch\autorecover.log. The 2026-07-12 lesson baked into
it: recovery machinery, not crashes, caused the duplicates - all three of that day's
"CRASH RECOVERY" waves fired on a machine that never rebooted (boot time 07-11 18:17
predated them all). A recovery pass must prove the crash happened before it medicates.

Liveness is not substance: `python -X utf8 substance.py` prints each live agent's newest actual
words and flags 'Not logged in' / 'API Error' / 'session limit' / 'Rate limited' - the check that
caught the 14:50 auth-loss no gauge could see. Run it whenever the fleet looks green but feels
wrong, and once an hour on principle.

## The supervisor loop (what "make sure they're working" means in practice)

    python watch/fleet_watch.py --interval 60    # usually run under a Monitor

Observe-only: samples every minute, logs to watch/fleet_watch.jsonl, and emits an EVENT
line only on: STALL-SUSPECT (>=12 quiet minutes, calibrated 2026-07-12: working agents
burn 3-8 cpu-s/min, idle TUIs 0.05-0.3), DEAD, DONE (a -p one-shot finishing is normal),
DUP verdict changes, WAVE (>=3 new resumes in one tick), COMPUTE mode changes. The
supervising session reacts to events with mail/wake per the table above - the watcher
itself never medicates. If the ledger shows interventions climbing, suspect the watcher
before the fleet (the an aggressive watcher incident (438 restarts)
days; that failure mode is why this split exists).

## Usage-cap behavior (learned 2026-07-12, three incidents)

The account's rolling usage cap does not stop the fleet uniformly - it bounces NEW and LARGE
model invocations while established main loops keep working. Observed: three giant-context
sessions died at 10:18 ("resets 5:20pm"); their -p re-wakes attached and exited writing
nothing; the dream's REM subagent bounced at 14:07 ("resets 6:20pm" - the window ROLLS);
meanwhile every main session kept working and a tiny fresh -p passed throughout. A5 named the
rule precisely: the cap blocks subagent/model-call SPAWNS, not loops already established.

Supervisor playbook during a cap window: do work in the MAIN loop, spawn nothing big; do not
wake walled sessions (the wake bounces - it is not launch_one's fault, believe the exit-0 with
an empty transcript); schedule wakes for after the reset (wake_after_limit.ps1 pattern: gated
one-shot task, self-unregistering) and RE-CHECK the reset time before it fires - it moved once
today. Agents mid-window: keep working your main loop; if your own calls fail, checkpoint and
idle - recovery is scheduled.

## Codex agents

Same states, same rules. `codex resume <session-id>` auto-submits (no Enter needed), so
wake for codex = launch_one with runtime=codex from the registry. Interactive Codex now has
an advisory reflex channel in `~/.codex/hooks.json` on UserPromptSubmit + PreToolUse
(live-verified 2026-07-13 by a fresh model turn responding to injected context). It is not
an arbitrary-mail channel: use the mesh (they poll digests) or wake an idle session for
mail. Their transcripts live under ~/.codex/sessions (health.py and status.ps1 both know).

There is also a dedicated peer system for codex continuity: `fleet\codex_continuity\`
(built 2026-07-12 by a codex supervisor agent) - manifest-enrolled headless resume with a
fixed continuation envelope, minute-cadence roster, singleton locks, windowless. For
sustained codex supervision prefer enrolling a session there over ad-hoc wakes; the two
systems share the locks philosophy and both refuse to duplicate a live session. Its
`--ignore-user-config` fence deliberately excludes interactive hooks, so every headless
continuation envelope now carries a bounded advisory digest: four repeated owner
corrections plus at most two lexical task-level reflexes, with low-validity domains marked
for current lived-data verification and stale financial magnitudes retired. This was
live-verified in an active Codex rollout on 2026-07-13. README documents
`supervisor.py --validate` and `--once --dry-run`.

## The acceptance drill (run after any change to the pipeline)

Prove the whole circle on the disposable test session (<test-session-id>..., kept registered+disabled for
exactly this): `register.ps1 -Id <test-session-id>... -Enable`, then
`python nudge.py wake <test-session-id>... --prompt "work on this for the next 0.1 hours: each turn append
one line to <a scratch file> then end the turn"`, then watch the scratch file. Pass = lines
appear within ~30s (measured 2026-07-12: 19 seconds wake-to-work) and the session stops cleanly
at clock expiry (a DONE event, not DEAD). Finish with `-Disable`. The drill exercises: DEAD
classification, launch_one's guards and lock, gentle -p resume, work-clock arming, Stop-block
looping, and clean expiry - every load-bearing piece in one observable run.

## Doctrine, compressed

1. Never bare `claude --resume` - that is the unguarded path. launch_one or relaunch.
2. Never kill by childlessness, peer say-so, or hunch - tree-verify or don't kill.
3. Working agents get mail, not restarts. First, do no harm.
4. Every intervention needs evidence from the target's own baseline, a rate limit,
   and a ledger line. Count your interventions; a busy healer IS the outage.
5. One recovery pass at a time; a second pass within the window yields.
6. Owner at the keyboard outranks everything; heavy compute goes to the remote box.
