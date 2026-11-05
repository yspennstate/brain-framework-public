# Fleet control - one terminal, every agent

Everything here is additive: it calls the existing tools (nudge.py, agent_mesh.py, launch_one.ps1)
and never edits the launchers, settings, or the mailbox hook. Run these from any terminal.

## Talk to any agent, through you  (console.py)

    python <HOME>\.claude\fleet\console.py list             # who is alive, their state + task
    python <HOME>\.claude\fleet\console.py say A3 "message"  # message one agent (A-name or sid)
    python <HOME>\.claude\fleet\console.py say codex-ch2-gpt-continuation "..."   # a codex/mesh id
    python <HOME>\.claude\fleet\console.py broadcast "message"   # every live claude session
    python <HOME>\.claude\fleet\console.py inbox            # replies addressed to you
    python <HOME>\.claude\fleet\console.py wake A6 --go      # resume a stopped agent (guarded)
    python <HOME>\.claude\fleet\console.py board            # the full mesh board
    python <HOME>\.claude\fleet\console.py mistakes         # recent cross-agent lessons

The console is its own mesh identity `owner-console`, so agent replies come back to you (run `inbox`).
A claude agent is reached by its A-name/sid (mailbox channel); a codex agent by its mesh id (mesh
channel). The mailbox delivers in-turn once its safety gate is open, otherwise on the agent's next
wake - `say` tells you which happened.

## Bring the fleet back after a crash  (roster_A.py + bringup_A.ps1)

    python <HOME>\.claude\fleet\roster_A.py                 # show the current "AgentsA" (live agents)
    python <HOME>\.claude\fleet\roster_A.py --write         # freeze it into AGENTS_A.json
    powershell -File <HOME>\.claude\fleet\bringup_A.ps1     # preview the roster (no launches)
    powershell -File <HOME>\.claude\fleet\bringup_A.ps1 -Go # relaunch the DEAD members only

AgentsA = exactly the agents whose session id a live process actually carries (A1, A3-A8, <user>
today). bringup routes every relaunch through the existing guarded launch_one.ps1, so running it
while the fleet is up is safe - every live agent is refused, only genuinely-dead ones come back. It
never force-launches and never kills. Plain-started sessions with no resumable id are listed as
"unclassified" for you to confirm, not auto-added. Duplicate-resume pairs are flagged, never killed.

Registry hygiene (optional): `python roster_A.py --write --archive` moves stale dead entries to
`registry\_archive\` (reversible; never touches live or disabled entries).

## Keep agent-to-agent comms working  (mesh_mail\)

    python <HOME>\.claude\fleet\mesh_mail\triage.py         # backlog: who owes acks, dead letters
    python <HOME>\.claude\fleet\mesh_mail\ack_sweep.py --agent <your-id>   # an agent drains its OWN inbox

The backlog was ~1130 unacked on 2026-07-12 (25% ack rate); ~900 of those are dead letters to agents
that went offline. The fix is discipline, not deletion (`purge` can't touch unacked mail): every agent
runs ack_sweep at task boundaries and before `bye`. See `mesh_mail\MAIL_DISCIPLINE.md`.

## Share mistakes across the fleet  (fleet_learn.py)

    python <HOME>\.claude\fleet\fleet_learn.py show          # recent feedback/self-mistake lessons
    python <HOME>\.claude\fleet\fleet_learn.py broadcast     # dry-run: what would go to the fleet
    python <HOME>\.claude\fleet\fleet_learn.py broadcast --go # broadcast NEW lessons (deduped, capped)
    python <HOME>\.claude\fleet\fleet_learn.py mesh          # recent live mesh corrections (kind=warning)

Turns a lesson one agent recorded into a labeled "fleet just learned" note the others see in their next
digest - data, not authority. It only stamps its dedup ledger after a send actually delivers.

## Push work to GitHub safely  (push_all.py)

    python <HOME>\.claude\fleet\push_all.py <repo>           # secret-scan, pull --rebase, push (never force)
    python <HOME>\.claude\fleet\push_all.py <repo> --dry-run # scan + show, push nothing

See `PUSH_REPORT.md` for the 2026-07-12 consolidation pass and three credential landmines to rotate.

## Prove the tools still hold their invariants  (selftest_consolidation.py)

    python <HOME>\.claude\fleet\selftest_consolidation.py   # 15 checks, opens no windows

Run it after editing any tool above. It proves the load-bearing guards: push_all never force-pushes
and secret-scans first, the console uses its own identity, roster archive only touches dead entries,
triage is read-only, ack_sweep never auto-acks handoffs.
