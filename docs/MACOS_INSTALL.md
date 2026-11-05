# Installing the brain on macOS

This guide describes the Mac port verified on 29 October 2026. Start with a checkout containing the POSIX changes; the original Windows bootstrap instructions alone do not produce working Mac hooks. The framework supplies the machinery and seed memories. A person's lived memories, private project configuration, mathematical artifacts and installed film tools are separate inputs.

For the existing Mac mini, read `~/brain/BRAIN_INSTALL_STATUS.md` before changing anything. It is the deployment record and names the current integration owners. This document is a repeatable installation guide, not a claim that every optional subsystem is complete. The source snapshot used here ends at tick 24, 01:35 on 29 October; concurrent workers may have resolved issues listed below.

## Prepare the checkout and Python

Use a permanent checkout, such as `~/brain/brain-framework`, and Python 3.13. The verified machine uses `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3`. Use the interpreter that will run hooks when installing dependencies. Hooks run with `-s`, so dependencies installed only in the Python user site will not be visible. A virtual environment is suitable if every hook and scheduled command uses its absolute interpreter path.

The commands below assume an already obtained checkout and local porting files. They describe installation work for the integrator; no network access or live installation is needed to read this guide. Review the repository's dependency files for the components you enable. Semantic recall additionally uses `sentence-transformers` and the `BAAI/bge-small-en-v1.5` model; arrange the model cache before an offline build.

In a shell, select the actual interpreter, project directory and time zone:

```bash
BRAIN_PY=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3
BRAIN_PROJECT="$HOME"                  # directory where agent sessions start
BRAIN_TZ=Asia/Tbilisi                   # replace with your own IANA zone
BRAIN_REPO="$HOME/brain/brain-framework"
cd "$BRAIN_REPO"
"$BRAIN_PY" -s --version
"$BRAIN_PY" bootstrap/install.py --project-dir "$BRAIN_PROJECT" --timezone "$BRAIN_TZ" --python "$BRAIN_PY"
```

The last command is a dry run. Read its destinations. The installer places dreaming code in `~/.claude/dreaming`, commands in `~/.claude/commands`, and several other components under `~/brain-framework`, even when the source checkout is elsewhere. It creates state directories and generates hook wiring. It does not merge settings or install the math and film stores.

Back up the existing settings, agent instructions, installed code and memories before applying. Applying copies framework machinery over existing installed files; it is not an upgrade merge for a customized lived brain. On this Mac the installed tree includes lived-module changes that must be preserved. Use a separate staging home for a rehearsal, with `--home <empty-staging-home>` and a matching `--project-dir`; do not point its builds at production indexes.

When the plan is correct, apply the same options:

```bash
"$BRAIN_PY" bootstrap/install.py --project-dir "$BRAIN_PROJECT" --timezone "$BRAIN_TZ" --python "$BRAIN_PY" --apply
```

## Choose and populate the memory corpus

The installer writes the chosen project slug into `~/.claude/dreaming/BRAIN_SLUG`. The slug is the full project path with colons and separators replaced by dashes. For a project directory `/Users/<you>`, it is `-Users-<you>`; memories live in `~/.claude/projects/-Users-alex/memory`. Check the marker and directory before building indexes. A different session working directory must not silently select an empty corpus.

Fill in the seed identity and relationship memories for a fresh brain. To restore an existing brain, copy the authorized lived corpus into this directory while preserving the seeds separately for comparison. The October Mac installation restored 28,219 files and 82 lived state files; those are historical counts for that corpus, not required targets. State artifacts can carry Windows paths, so inspect them before copying. Preserve ledgers and provenance rather than replacing them with fresh empty files.

`BRAIN_MEM_DIR` can redirect supported recall code, but not every helper honors the same variable. Check each builder's input and output paths. In particular, the live embedding builder inspected for this guide still has a deployment-specific default memory path; pass the chosen memory directory explicitly.

## Check the POSIX port before enabling hooks

The October port is recorded in framework commit `29ea7df`, with later governor, film and dream work in `8eedfd0`, `2c924e5` and `e458b73`. Use the current reviewed branch containing those changes and any subsequent fixes. The deployment also has local and lived-module merges; a commit list is not a full backup of that install.

`~/brain/porting/port_posix.py` is a local migration aid, not a dependency shipped by every framework clone. It can preview rewrites on a staged tree:

```bash
"$BRAIN_PY" "$HOME/brain/porting/port_posix.py" "/path/to/staged-tree"
```

Replace `/path/to/staged-tree` with a real path. Apply only to a reviewed staging copy with `--apply`, then inspect the diff and compile changed Python files. The tool fixes tilde/backslash literals, forced backslash separators and Windows subprocess creation flags, and adjusts an old film root. It does not port PowerShell algorithms, Windows process queries, private configuration, or arbitrary absolute Windows paths.

The reviewed process port uses `posix_proc.py` and POSIX branches for process census, parent identities, liveness and idle sensing. Keep Windows behavior behind `os.name == "nt"` and use portable paths. CrashGuard and the Windows fleet launchers remain separate porting work; copying them is not evidence that they run on macOS.

## Wire the session and tool hooks

Inspect `bootstrap/settings.hooks.generated.json`, then merge its required behavior into `~/.claude/settings.json` while preserving unrelated settings. The current template still includes bare `python`, PowerShell SessionStart entries and some backslash paths. `--python` does not fix every literal command in that template. Convert the generated configuration before enabling it.

Use the absolute interpreter with `-s` in each command, forward slash paths, and shell quoting for paths containing spaces. Normalize both `hook_dispatch_list.json` and the settings that call it. The verified deployment routes seven events through the dispatcher: SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop, PreCompact and PostCompact. Keep the event matchers and avoid wiring a hook twice through both settings and the dispatch list.

Install the Python wake implementation from `hooks/wake_brain.py` as `~/.claude/wake_brain.py`. Render `hooks/brain_startup.template.md` with the same installation values into `~/.claude/brain_startup.md`, or review the restored lived startup text and fix its paths. In the dispatch list, replace the two PowerShell wake entries with calls to the installed Python script using `--part 1` and `--part 2`. Run both parts manually before opening a new session:

```bash
"$BRAIN_PY" -s "$HOME/.claude/wake_brain.py" --part 1
"$BRAIN_PY" -s "$HOME/.claude/wake_brain.py" --part 2
```

The Python wake reads the latest dated state-of-mind triplet and uses Mac load and memory pressure in place of Windows CrashGuard. A fresh install can correctly warn that no dream or embedding audit has run yet.

Bootstrap creates `~/.claude/dreaming/HOOK_DISPATCH_ENABLED`. If dispatch is broken, move this flag to a saved disabled filename: calls through the dispatcher become silent no-ops. Hooks wired directly in settings are unaffected. Restore the flag only after repairing and checking the configuration. Preserve the settings backup as the wider rollback path.

Review the dispatch allowlist after changing covered code. The October install required a reviewed allowlist refresh. Follow the installed signing/review procedure; copying an old allowlist or bypassing validation is not a repair.

Merge the generated `~/.claude/CLAUDE.brain.md` into existing instructions if bootstrap left that file beside `CLAUDE.md`. Review commands in `~/.claude/commands` for Windows executables and paths. On the verified Mac, `/dream`, `/checklist` and `/seen` were rewritten for Python; app-specific commands were left out when their applications were absent.

## Build recall, then verify what it can retrieve

After the memory directory and code agree, build lexical and association artifacts:

```bash
nice -n 15 "$BRAIN_PY" -s "$HOME/.claude/dreaming/bm25.py" build
nice -n 15 "$BRAIN_PY" -s "$HOME/.claude/dreaming/build_spread_edges.py"
"$BRAIN_PY" -s "$HOME/.claude/dreaming/brain.py" recall "a distinctive fact from an actual memory"
```

Replace the example query with a fact whose source file you can inspect. Check the returned memory, not just a successful exit. The October build reported 27,997 lexical documents and 93,702 association edges. Corpus changes alter both counts.

For semantic recall, use the production sentence-transformer builder, then publish its staged artifact through the publication tool. The local LSA builder is diagnostic-only and must not replace the live sidecar. Set the correct memory directory explicitly:

```bash
BRAIN_MEMORY="$HOME/.claude/projects/$(cat "$HOME/.claude/dreaming/BRAIN_SLUG")/memory"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
nice -n 15 "$BRAIN_PY" -s "$HOME/.claude/dreaming/build_memory_embeddings.py" "$BRAIN_MEMORY" --out "$HOME/.claude/dreaming/memory_embeddings.next.npz"
"$BRAIN_PY" -s "$HOME/.claude/dreaming/publish_memory_embeddings.py" --staged "$HOME/.claude/dreaming/memory_embeddings.next.npz" --mem-dir "$BRAIN_MEMORY"
"$BRAIN_PY" -s "$HOME/.claude/dreaming/embedding_coverage.py" --mem-dir "$BRAIN_MEMORY" --require-complete --write-status
"$BRAIN_PY" -s "$HOME/.claude/dreaming/brain.py" recall "a distinctive fact from an actual memory"
```

Run the builder and a dream sequentially. On a shared machine, keep builds at low priority and monitor resource use. The verified corpus produced 28,020 vectors of 384 dimensions and complete coverage. Confirm the coverage audit and that recall reports semantic recall on; creating an NPZ alone does not establish that it is published or used.

## Run a mechanical dream manually

When recall is ready, inspect `~/.claude/dreaming/run_dream.py` and its tail, then run a manual nap:

```bash
nice -n 15 "$BRAIN_PY" -s "$HOME/.claude/dreaming/run_dream.py" --type nap
```

This changes derived brain state. Read the resulting `~/.claude/dreaming/logs/dream_*.log`. Review every `STEP FAILED`, `STEP CAPPED` and `STEP SKIPPED` entry as well as the final completion marker: this runner continues after individual step failures. A zero runner exit and “dream cycle complete” are insufficient evidence of a fully successful tail.

A `STOP` file beside the runner prevents a new cycle and is checked between tail steps. It does not interrupt the current child immediately. The runner uses an exclusive flock on `dream.lock`; the persistent file's existence does not mean a dream is running. Leave the lock file in place so its inode remains stable.

The Mac runner is mechanical-only, even when the selected type is `rem`. It does not run the unattended language-model REM session from the Windows script. That session and any permission-bypass behavior remain an explicit owner decision. The mechanical runner also does not supply cross-machine Git synchronization.

## Optional organs and unresolved configuration

The project-memory warning `[prompt-contract] Signed hot cache validation failed` means its signed cache could not be validated. In the observed install, the private project configuration, signing key and cache were absent. The local proposal in `~/brain/porting/project_memory_draft/` is a draft, not installed authority. Have the owner restore or approve the project list, then follow `dreaming/project_memory/README.md` to build the store and signed cache. Verify an on-task action and a deliberately drifted control. Do not silence the warning by weakening validation.

The POSIX governor can be observed once without installing a service:

```bash
"$BRAIN_PY" -s "$BRAIN_REPO/governor/compute_governor_posix.py" --once
```

It publishes observed load and politeness state; the reviewed implementation does not kill or renice processes. Continuous governor execution and a mechanical dream schedule through launchd were not authorized in the observed installation. Proposed plists stay outside `~/Library/LaunchAgents` until the owner decides. A plist on disk is not a loaded service; record an actual loaded state and fresh output when scheduling is approved. Windows Task Scheduler, VBS and PowerShell launchers are not Mac installation steps.

Math research support needs its own package and store. The October deployment restored a 51,052-row database from an export using the corrected importer (`math-research-brain` commit `2c6cde49`) and configured `MATHBRAIN_STORE` plus `MATHBRAIN_BUILD_SRC`. Database rows alone were insufficient: 7,310 of 11,451 cards referenced an absent `objects/sha256` tree, and some cards failed digest checks. Restore matching artifacts and verify reader integrity before treating this lane as fully working. Follow the math package's own instructions rather than treating the framework's isolated math smoke as proof of that lived store.

Film support stays in the checkout unless installed separately. From `filmbrain/`, run `python3 ingest/load_craft.py`, then `python3 tools/verify_tools.py`; run harvesters only for binaries actually present. `FILMBRAIN_ROOT` and `FILMBRAIN_DB` select alternate locations. The October authored store had 592 nodes, 996 edges and 11 stubs; all 20 inventoried film tools were absent. Craft recall can work while rendering tools remain unavailable. Keep a Mac inventory separate from a Windows inventory and record measured machine capability in `craft/this_machine.py`.

The mesh was exercised successfully on the Mac. Use `mesh/README.md` for hello, digest, claims and inbox operations, and honor active claims before editing a shared runtime. Do not install the deployment-specific keep-working Stop gate as a generic requirement: it points at the owner's install checklist and has its own session scoping and off switch.

## Verify the installation and preserve shared data

Run checkout tests separately from installed-runtime checks. From the checkout:

```bash
nice -n 15 "$BRAIN_PY" scripts/smoke_framework.py
nice -n 15 "$BRAIN_PY" scripts/smoke_framework.py --installed "$HOME" --receipt installed.json
nice -n 15 "$BRAIN_PY" -m pytest dreaming mesh governor methods -q -p no:cacheprovider --timeout=120 --continue-on-collection-errors
```

Install the test dependencies, including the timeout plugin, in the selected interpreter. Compile any changed Python files with `python3 -m py_compile <changed-file>` before integration. See `docs/EXERCISING.md` for redirected state and scratch verification. Avoid a whole-tree pytest invocation: the repository documents colliding test basenames.

The historical Mac baseline was 24/24 checkout smoke and 16/18 installed smoke. The two installed failures represented math and film components absent from bootstrap's layout. The recorded framework suite had 227 passes and 8 pre-existing failures, with collection issues tracked separately. These figures are comparison points, not permission to ignore a new failure. Save the full output and compare individual failing tests with the current baseline.

The local porting harness can exercise installed hooks:

```bash
nice -n 15 "$BRAIN_PY" "$HOME/brain/porting/hook_harness.py" --out "$HOME/brain/porting/hook_results.tsv"
```

Read its TSV and summary; the harness itself does not return a failing process status for every bad hook result. The historical result was 76/76 hook invocations with hook return code zero. The harness normalizes some interpreter/path differences, so also verify real dispatch from a new session. It executes live hooks, which can append state, including synthetic prompt rows. Use an agreed test window and recognizable harness session IDs. Quiet hooks need positive controls: silence can mean a threshold, a conditional trigger or an intentional shadow mode. On this install proof-deny and gap-watch were shadowed by design; their silence was not a portability failure.

Before syncing a restored brain with another machine, merge the ledgers. The Mac dream deliberately leaves prompt-shard publication disabled because rebuilding a month from a short local ledger could overwrite thousands of Windows prompts. Union the remote shard with local rows before publication. The shared task checklist likewise needs a union of ledger events, collision handling for reused task IDs, and regenerated state and Markdown. Never overwrite either machine's ledger or rebuild shared history solely from the smaller local copy.

For deployment-specific new-memory publication, the install record describes an explicit new-files-only temporary Git index with `GIT_NO_LAZY_FETCH=1` and `git write-tree --missing-ok` for a partial clone. That technique does not merge modified ledgers by itself. Keep publication a separate reviewed operation, verify the exact paths at the remote afterward, and preserve the original copies. No automatic push, trading process or approval process is required to stand up recall and mechanical dreams.

If a restored Windows path produced a directory whose name literally contains backslashes, retain it until its state has been compared and merged into the correct Mac location. Similarly, a strong-node scan raising `ENAMETOOLONG` for a long wiki-link phrase needs a reviewed scanner fix and a new reviewed runtime generation; do not modify or repin a hash-pinned runtime in place. Its deliberately degraded observation state is a separate issue from the path defect.

## Sources for this guide

The deployment observations come from `~/brain/BRAIN_INSTALL_STATUS.md` through tick 24 and the local porting folder, particularly `port_posix.py`, `hook_harness.py` and `project_memory_draft/README.md`. Installer behavior was checked against `bootstrap/install.py`, `bootstrap/README.md` and `hooks/settings.hooks.template.json`. Runtime command forms were checked against the installed Python wake, mechanical dream runner, BM25 builder, embedding builder, publisher and coverage checker. Consult `README.md`, `docs/EXERCISING.md`, `filmbrain/README.md`, `mesh/README.md` and the project-memory README for subsystem details.

The counts above are attributed historical observations. This guide was reviewed against source; it was not validated by reinstalling the owner's live brain.
