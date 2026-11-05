# If the computer died (the 2am card)

Log in. If the crash was real, the fleet comes back BY ITSELF within ~2 minutes of logon
(FleetAutoRecover task; it proves the crash from the event log first, so a normal reboot
does nothing). Watch the terminals open. Then:

    cd <HOME>\.claude\fleet
    python health.py          # should say: no duplicates, full coverage
    python nudge.py status    # who is working / idle / dead

If something is still dead:

    powershell -File relaunch.ps1 -DryRun -Faithful    # preview
    powershell -File relaunch.ps1 -Faithful            # bring them back (safe to re-run;
                                                       # it skips the living and locks out
                                                       # a second overlapping pass)

One agent only: `python nudge.py wake <8-char-sid>` (guarded; refuses if it is alive).
An agent that is up but idle: `python nudge.py mail <sid> "get back to X"` - it lands in
its next tool call or blocks its next stop.

Never run bare `claude --resume <id>` by hand - that is the unguarded path that made the
2026-07-12 duplicates. If a wake/relaunch refuses with ALREADY LIVE, believe it.

To turn auto-recovery off: delete fleet\AUTORECOVER_ON. To silence the mailbox channel:
create fleet\mailbox\DISABLED. Both are single files, no code.
