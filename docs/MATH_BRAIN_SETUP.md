# Setting up the math brain on the brain framework

The two public repositories are two halves of one system.

- [brain-framework-public](https://github.com/yspennstate/brain-framework-public) is the machinery: shared memory, the wake ritual, the prompt hooks, the nightly dream, the reflexes compiled from mistakes, the agent mesh and the fleet supervisor.
- [math-brain-public](https://github.com/yspennstate/math-brain-public) is the mathematical store that runs on it: about thirteen thousand cards and their typed links, the importer, the recall tools and the prompt hook that serves cards to an agent. Its `framework/` folder is a copy of the framework as it stood at the export date; the framework repository is the one to install from.

You can run the math store on its own (the math repository's README shows how, in five commands). Install the framework as well when you want the agent to keep memories between sessions, dream over them, and get math notes and its own past work in the same prompt.

## 1. Install the framework

```bash
git clone https://github.com/yspennstate/brain-framework-public ~/brain-framework
cd ~/brain-framework
python bootstrap/install.py --project-dir "$HOME" --timezone <tz>            # dry run: read the plan
python bootstrap/install.py --project-dir "$HOME" --timezone <tz> --apply
```

Then follow `bootstrap/README.md` steps 3 and 4: merge the generated hooks into `~/.claude/settings.json` and fill in the seed identity memories. On a Mac read `docs/MACOS_INSTALL.md` first. It covers the interpreter (Python 3.11 or newer, with `-s`), the Python wake script that replaces the PowerShell one, the dispatcher flag, and the parts that stay Windows-only.

## 2. Build the math store

```bash
git clone --filter=blob:none --sparse https://github.com/yspennstate/math-brain-public ~/math-brain-public
cd ~/math-brain-public
git sparse-checkout set src tools agent_tools export config navigation
python tools/import_store.py --out local/population/brain.sqlite3
python agent_tools/math_recall.py "spectral gap relaxation time" --or "absolute spectral gap reversible Markov chain"
```

The import checks every row against the digests in `export/population/MANIFEST.json`. A row that fails is reported, not loaded. Do not use a store that failed rows.

## 3. Point the framework at the store

The framework's math hook (`~/.claude/dreaming/math_sense.py`) and the recall tool read two settings:

```bash
export MATHBRAIN_STORE="$HOME/math-brain-public/local/population/brain.sqlite3"
export MATHBRAIN_BUILD_SRC="$HOME/math-brain-public/src"
```

Put them where your agent's hooks will see them: the `env` block of `~/.claude/settings.json` for Claude Code, or the shell profile that starts Codex. The hook is already in the framework's `dreaming/hook_dispatch_list.json` under UserPromptSubmit. With the store unset it stays silent, so a silent hook usually means one of these two paths is wrong.

Check it end to end: open a new session, ask a question that names a mathematical object in the store, and look for a `[math-brain]` line in the context the hook adds. The hook is quiet on most prompts by design, so try two or three questions. If none brings a note, run `math_recall.py` from step 2 in a shell with the same two settings exported. If that works, the hook is not seeing the settings.

## 4. Teach the agent the recall habit

Copy the "default recall workflow" section of the math repository's `AGENTS_GUIDE.md` into the agent's instructions (`~/.claude/CLAUDE.md`, or `AGENTS.md` for Codex). In short: search with two or three wordings in one call, open the ids it prints with `--id`, reword when it says WEAK MATCH, and stop after three empty rounds. A hook note is a lead to open, not an answer.

## 5. Writing back

The two halves take different records.

- Our own working notes, mistakes and decisions go into the framework's memory corpus: `python ~/.claude/dreaming/brain.py remember <slug> --type project --desc "..."`.
- Mathematics (definitions, theorems, proofs, counterexamples, attempts) goes into the math store as packets of cards and links. See `AGENTS_GUIDE.md` section 6 and `docs/adding-cards.md` in the math repository.

The framework's `dreaming/math_transport.py` carries a result from a memory into the math store automatically. It is written against the private store's schema and its own vocabulary filters, so on a new install treat it as a model to adapt, not a step to run. Writing cards through the import tools is the supported path.

## 6. The dream

Run a dream once a few days of work exist. On Windows the scheduled task runs `dreaming/run_dream.ps1` through a hidden VBS launcher. On a Mac run `run_dream.py --type nap` by hand (see `docs/MACOS_INSTALL.md`). The macOS runner is mechanical only: it files, links and consolidates, but it does not start the unattended language-model REM session that the Windows script runs. A `STOP` file beside the runner blocks the next cycle.

A smaller dream, built for a brain in a new field, is in the math repository under `examples/field_brain_starter/dream/`.

## Where to read more

| Question | Where |
|---|---|
| How recall, the graph and the dream work | framework `docs/BRAIN_SCIENCE_AUDIT.md`, `dreaming/README.md` |
| Mac install, step by step | framework `docs/MACOS_INSTALL.md` |
| How agents should use the store | math `AGENTS_GUIDE.md`, `docs/agency.md` |
| Adding cards | math `docs/adding-cards.md` |
| A brain for another field | math `examples/field_brain_starter/`, `docs/brain-in-an-afternoon.md` |
| What the tests measured | math `TESTS.md`, `docs/benchmark.md` |
