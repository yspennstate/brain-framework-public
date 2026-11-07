# Brain install status

The deployment record for one machine. Keep it at ~/brain/BRAIN_INSTALL_STATUS.md.
While a "- [ ]" line is open, dreaming/keep_working_brain.py refuses to let a session
stop and hands it the next open step. Tick a step only after checking it worked, and
append one dated line per finished job:

    printf -- '- %s <step> (<agent id>): <result>\n' "$(date '+%Y-%m-%d %H:%M')" >> ~/brain/BRAIN_INSTALL_STATUS.md

## Machine

- host:
- operating system:
- interpreter (BRAIN_PY, Python 3.11 or newer):
- framework checkout (BRAIN_REPO):
- math store (MATHBRAIN_STORE):
- integration owner:

## Steps

- [ ] clone brain-framework-public and read docs/MACOS_INSTALL.md (on a Mac)
- [ ] bootstrap/install.py dry run read, then --apply
- [ ] hooks merged into ~/.claude/settings.json; dispatch list normalized (hook_dispatch_list.macos.template.json)
- [ ] wake: ~/.claude/wake_brain.py --part 1 and --part 2 run by hand without error
- [ ] ~/.claude/dreaming/HOOK_DISPATCH_ENABLED present; a new session shows the wake text
- [ ] seed identity memories filled in
- [ ] recall built (bm25 and association artifacts) and one recall question answered
- [ ] math store imported with no failed rows; MATHBRAIN_STORE and MATHBRAIN_BUILD_SRC set
- [ ] a [math-brain] note seen in a new session
- [ ] one manual nap: run_dream.py --type nap, log read for STEP FAILED / CAPPED / SKIPPED
- [ ] decision recorded on scheduling (launchd) and on the language-model REM session

## Log
