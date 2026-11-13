# Standing up a fresh brain

1. Clone this repository somewhere permanent and run the commands below from its
   `framework/` folder. The installer copies the governor, mesh, workspace and tools into
   `%USERPROFILE%\brain-framework`, and the launchers point at that installed copy, so the
   clone can live anywhere. Keep it, though: the network builders in step 6 run from it.

2. Dry-run the installer, read what it plans, then apply:

   ```
   python bootstrap/install.py --project-dir C:\Users\<you> --timezone <tz>
   python bootstrap/install.py --project-dir C:\Users\<you> --timezone <tz> --apply
   ```

   `--timezone` takes `UTC` (the default) or an IANA name such as `Europe/London`. Python on
   Windows has no time-zone database of its own, so any name other than UTC needs
   `python -m pip install tzdata` first; without it the nightly dream stops at its agenda step
   and says so in its log.

   This copies the machinery into the standard layout (`~/.claude/dreaming`,
   `~/.claude/fleet`, `~/CrashGuard`, ...), expands the install tokens, creates the
   state directories, makes `~/brain-repo` with the mesh, delegation, resilience,
   workspace and governor modules where the code looks for them (see the framework
   README), seeds the memory corpus (index, ledgers, and the seed identity
   templates), and generates `bootstrap/settings.hooks.generated.json`.

3. Wire the hooks: merge the generated `hooks` object into `~/.claude/settings.json`,
   or add `--merge-settings` to the `--apply` run and the installer does it. It keeps every
   key and hook already in the file, adds only the groups that are missing (a second run
   adds nothing), writes a dated `settings.json.bak-*` first, and stops without writing if
   the existing file is not valid JSON. The installer has already written the wake ritual, `~/.claude/wake_brain.ps1` and
   `~/.claude/brain_startup.md`, with the tokens filled in, and the generated wiring points
   SessionStart at it. If either file already existed it is left alone and the new one is
   written beside it with `.new` added. To see what a session will receive, run
   `powershell -NoProfile -File ~/.claude/wake_brain.ps1` (on Linux and macOS,
   `python3 hooks/wake_brain.py`). On a fresh install its waking check prints WARN for "no dream
   ran recently" and for the semantic embeddings (never checked, or none current). Both are
   expected until a dream has run and the embedding sidecar has been built; recall's lexical
   and graph lanes work without it. Any other WARN line names a file the install did not place.
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
