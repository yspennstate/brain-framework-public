# Standing up a fresh brain

1. Clone this repository somewhere permanent (the launchers assume
   `%USERPROFILE%\brain-framework`; adjust them if you clone elsewhere).

2. Dry-run the installer, read what it plans, then apply:

   ```
   python bootstrap/install.py --project-dir C:\Users\<you> --timezone <tz>
   python bootstrap/install.py --project-dir C:\Users\<you> --timezone <tz> --apply
   ```

   This copies the machinery into the standard layout (`~/.claude/dreaming`,
   `~/.claude/fleet`, `~/CrashGuard`, ...), expands the install tokens, creates the
   state directories, seeds the memory corpus (index, ledgers, and the seed identity
   templates), and generates `bootstrap/settings.hooks.generated.json`.

3. Wire the hooks: merge the generated `hooks` object into `~/.claude/settings.json`.
   Install the wake ritual: copy the expanded `hooks/wake_brain.template.ps1` to
   `~/.claude/wake_brain.ps1` and `hooks/brain_startup.template.md` to
   `~/.claude/brain_startup.md`, and point a SessionStart hook at the wake script.
   The installer renders `bootstrap/templates/CLAUDE.template.md` into
   `~/.claude/CLAUDE.md`, your global agent instructions (`--partner NAME` names the person
   the brain works with). An existing `CLAUDE.md` is left alone and the rendered copy goes
   beside it as `CLAUDE.brain.md` for you to merge.

4. Fill in the seed identity memories the installer placed in the corpus
   (`identity_who_you_are`, `relationship_our_work_together`, ...): they are templates
   with instructions, and the wake ritual reads them by name.

5. Optional organs, each with its own README: the dream cycle's scheduled task
   (`dreaming/run_dream.ps1` via a SW_HIDE VBS), the compute governor
   (`governor/install_compute_governor.ps1`), CrashGuard
   (`body/crashguard/bin/crashguard-launch-all.ps1` at logon), the fleet supervisor,
   and the resilience backup (requires your own cloud identifiers - see
   `docs/PORTING.md`).

6. First sessions: the corpus grows by use. Record corrections in `MISTAKES.md`; run a
   dream after a few days of work (`dreaming/run_dream.ps1`); let `instincts.py` compile
   the first reflexes; build the big network once an event history exists
   (`network/adapt_live.py` then `network/build_graph.py` - see `network/DESIGN.md`).

Nothing in this repository requires the deployment it was extracted from. If a script
still assumes that machine, that is a bug; `docs/PORTING.md` records what was
deliberately left behind.
