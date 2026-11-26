# Installing the brain on macOS

Every step below uses only files in this repository. The commands were run on 26 November 2026 on a
Mac mini (Apple silicon), from a fresh shallow clone, into a throwaway home folder, so nothing in an
existing install was touched. The test machine had only the system Python 3.9, so the results below
are for 3.9; the places where 3.9 falls short, and why, are named.

## What you need

- git and a terminal (Terminal.app is enough).
- Python 3.11 or newer for the whole framework. The math brain declares `requires-python >= 3.11`.
  The `/usr/bin/python3` that ships with macOS is 3.9: the core brain installs and runs with it, but
  the four math-brain checks fail (see "Check the install"). Install a newer Python from python.org or
  with Homebrew (`brew install python@3.13`) and use its absolute path everywhere below.
- Claude Code, if you want the hooks to run inside agent sessions.

## Get the checkout

```bash
git clone --depth 1 https://github.com/yspennstate/brain-framework-public.git ~/brain-framework-public
cd ~/brain-framework-public
BRAIN_PY=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3   # for example; use your own path
"$BRAIN_PY" --version
```

## Dry run, then apply

```bash
"$BRAIN_PY" bootstrap/install.py --project-dir "$HOME" --timezone "$(readlink /etc/localtime | sed 's#.*zoneinfo/##')" --python "$BRAIN_PY"
```

Without `--apply` this only prints the plan. Read the destinations. On the test run they were:

- `~/.claude/dreaming` (705 files), `~/.claude/brain_graph`, `~/.claude/fleet`, `~/.claude/mathbrain`
  and the slash commands in `~/.claude/commands`;
- `~/brain-framework/` for the governor, mesh, workspace and tools;
- `~/brain-repo/` for the folders the brain fills as it runs (keep that folder under git);
- `~/CrashGuard`;
- the memory corpus at `~/.claude/projects/<slug>/memory`, where the slug is your project path with
  every `/` turned into `-` (written to `~/.claude/dreaming/BRAIN_SLUG`);
- `~/.claude/CLAUDE.md` for the agent instructions.

The installer does not register any launchd job or cron entry; nothing runs until an agent session
starts or you run a script yourself.

Back up `~/.claude` before applying if you already have one: applying copies files over what is
there, it does not merge a customised install. Then:

```bash
"$BRAIN_PY" bootstrap/install.py --project-dir "$HOME" --timezone "$(readlink /etc/localtime | sed 's#.*zoneinfo/##')" --python "$BRAIN_PY" --apply
```

The test run installed 1,365 files. At the end it warns that the query-meaning lane is off: recall
then finds only memories that share a word with the question. To turn it on, either re-run the
install with `--fetch-models`, or:

```bash
"$BRAIN_PY" -m pip install --user onnxruntime tokenizers
"$BRAIN_PY" ~/.claude/dreaming/fetch_bge_onnx.py        # the model, about 133 MB
```

## Build the reflex table

The reflex hook only reads `~/.claude/dreaming/instincts.tsv`. The installer compiles that table at
the end of `--apply` and prints a `reflex table:` line; on the test install it reported 27 starter
reflexes (19 fire, 8 carry). If it prints `REFLEX TABLE NOT BUILT` instead, or you installed with an
older copy of the installer, build it by hand:

```bash
"$BRAIN_PY" ~/.claude/dreaming/instincts.py
```

Run by hand with the system Python 3.9, the compiler also printed "TRIGGER COST: the check exited 1": the trigger-cost check
(`instinct_cost.py`) imports `re._constants`, which exists only from Python 3.11. The table is
still written; the check runs with 3.11 or newer. Your own reflexes go in
`~/.claude/dreaming/instincts_learned.json`, a JSON list that does not exist on a fresh install. Run
the compiler again after editing it; the table is a build output, so edit the JSON, not the table.

## Wire the hooks into Claude Code

The installer writes `bootstrap/settings.hooks.generated.json`. On a Mac every command in it already
starts with the interpreter you passed (`--python`), with forward slashes and no PowerShell, so the
file can be merged as it is. Merge its `hooks` section into `~/.claude/settings.json`, keeping any
settings you already have.

The dispatcher list the installer writes, `~/.claude/dreaming/hook_dispatch_list.json` (88 entries on
the test install), is ready for a Mac as it stands: its commands use your interpreter, and its two
session-start wake entries call `hooks/wake_brain.py` in your checkout by absolute path, with
`--part 1` and `--part 2`. So leave the checkout where you cloned it, or the wake breaks. The list
has no PowerShell commands; the word appears only as a tool name in some matchers.

If you want to build your own list instead,
`docs/setup-questions-2026-11/hook_dispatch_list.macos.template.json` is a shorter template
(82 entries): replace `{{PYTHON}}`, `{{CLAUDE_DIR}}`, `{{DREAMING}}` and `{{FLEET}}` with your paths.
`docs/setup-questions-2026-11/ANSWERS.txt` explains how it differs from the Windows list.

## Check the install

From the checkout:

```bash
nice -n 15 "$BRAIN_PY" scripts/smoke_framework.py
nice -n 15 "$BRAIN_PY" scripts/smoke_framework.py --installed "$HOME"
```

What the test run printed with the system Python 3.9:

| run | passed | failed |
|---|---|---|
| checkout | 20 of 24 | the four math-brain checks (database initialises, claim accepted, claim recalled, claim readable) |
| installed | 16 of 21 | the same four, and "film brain: present", because the installer does not copy `filmbrain/` |

The math-brain failures come from the Python version: run by hand, the math brain stops at
`from enum import StrEnum`, which Python added in 3.11. Use 3.11 or newer for those checks. The film brain can be used from the checkout (`filmbrain/`, run
`python3 ingest/load_craft.py` there), which is why the checkout run passes its film checks.

To see what you have installed, layer by layer:

```bash
"$BRAIN_PY" tools/hook_census.py --root ~/.claude --families tools/hook_families.example.json
```

On the test install, after the reflex table was built and before the hooks were merged into
`settings.json`, it printed:

| layer | count |
|---|---|
| commands in `settings.json` | 0 |
| entries in `hook_dispatch_list.json` | 88 |
| hook program files | 85 |
| reflex rows (each with its own trigger) | 27 |
| motivation lines / compaction cards / paper-writing rules | 999 / 53 / 0 |
| rules in all | 1,079 |
| fire rows | 0 (nothing has run yet) |

The paper-writing family counts 0 here because its rule file ships with the math-brain repository,
not this one.

## Further steps (not re-run in this test)

The files these steps use are all in the checkout or the install; the steps themselves were not
run on the test machine.

**Wake hook.** The session-start wake runs `hooks/wake_brain.py` from the checkout (see "Wire the
hooks" above). Run both parts once by hand, from the checkout, before opening a session:

```bash
"$BRAIN_PY" -s hooks/wake_brain.py --part 1
"$BRAIN_PY" -s hooks/wake_brain.py --part 2
```

A fresh install can warn that no dream or embedding audit has run yet; that is expected.

**Switching the dispatcher off.** The installer creates `~/.claude/dreaming/HOOK_DISPATCH_ENABLED`.
Renaming that file makes every call through the dispatcher a silent no-op (hooks wired directly in
`settings.json` still run). Put it back once the wiring is fixed.

**Recall.** Once memories are in the memory folder, build the word and association indexes, then ask
a question whose answer you can check in a memory file:

```bash
nice -n 15 "$BRAIN_PY" -s ~/.claude/dreaming/bm25.py build
nice -n 15 "$BRAIN_PY" -s ~/.claude/dreaming/build_spread_edges.py
"$BRAIN_PY" -s ~/.claude/dreaming/brain.py recall "a distinctive fact from one of your memories"
```

For meaning-based recall over the memories themselves, build the embeddings to a staging file,
publish it, and check coverage. The builder needs `sentence-transformers` in your interpreter:

```bash
"$BRAIN_PY" -m pip install --user sentence-transformers
BRAIN_MEMORY=~/.claude/projects/"$(cat ~/.claude/dreaming/BRAIN_SLUG)"/memory
nice -n 15 "$BRAIN_PY" -s ~/.claude/dreaming/build_memory_embeddings.py "$BRAIN_MEMORY" --out ~/.claude/dreaming/memory_embeddings.next.npz
"$BRAIN_PY" -s ~/.claude/dreaming/publish_memory_embeddings.py --staged ~/.claude/dreaming/memory_embeddings.next.npz --mem-dir "$BRAIN_MEMORY"
"$BRAIN_PY" -s ~/.claude/dreaming/embedding_coverage.py --mem-dir "$BRAIN_MEMORY" --require-complete --write-status
```

Pass the memory folder explicitly; not every builder reads the same environment variable.

**A manual dream.** `~/.claude/dreaming/run_dream.py --type nap` runs one mechanical consolidation
pass and changes derived brain state. It carries on past a failed step, so read its log under
`~/.claude/dreaming/logs/` for `STEP FAILED`, `STEP CAPPED` and `STEP SKIPPED` rather than trusting
its exit code. A `STOP` file beside the runner stops the next cycle, not the current one. The
`dream.lock` file is always there; its presence does not mean a dream is running.

**Film craft.** From `filmbrain/` in the checkout, `python3 ingest/load_craft.py` loads the craft
store and `python3 tools/verify_tools.py` lists which rendering tools this machine has.

**Tests.** `docs/EXERCISING.md` explains how to run the test suites against scratch state rather
than your live install.

**Two machines.** Before syncing a brain between machines, merge their ledgers (prompt shards, the
task checklist) as a union of rows. Copying one machine's files over the other's loses history.

## Optional pieces

- The compute governor can be observed once without installing anything:
  `"$BRAIN_PY" -s ~/brain-framework/governor/compute_governor_posix.py --once`. It reports load and
  does not kill or renice processes.
- Several agents on one Mac coordinate through the mesh: see `mesh/README.md` for hello, digest,
  claims and inbox.
- Before syncing a brain with a second machine, merge the ledgers rather than copying one machine's
  files over the other's.

## Running agents on it

Open Terminal, `cd` to the project folder you passed as `--project-dir`, and start Claude Code there.
The session-start hook hands the agent the brain; `python3 ~/.claude/dreaming/brain.py recall "..."`
asks it a question by hand. A recurring task in one session looks like:

```
/loop 10m check the open problems list, pick one, and record what you tried with brain.py remember
```
