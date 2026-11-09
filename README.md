# Brain framework

The underlying structure of a persistent, multi-agent "brain": the machinery that lets
AI agents on one machine share a long-lived memory, wake up with continuity, dream
(consolidate), develop reflexes from their own mistakes, coordinate through a mesh
instead of clobbering each other, run as a supervised fleet, and stay polite to the
human whose machine they live on. A separate, stricter sibling for mathematical
research (the math brain) ships alongside it.

This repository carries structure only. It was extracted from a private working system;
the lived content — memories, dreams, journals, transcripts, project state, anything
personal — stays behind, and so does everything related to trading. What remains is the
method: the code, formats, protocols, and doctrine that make the system run, with
machine-specific constants parameterized and worked examples kept synthetic.

## Layout

- `dreaming/` — the memory store and the subconscious: the recall stack (lexical +
  association-graph + semantic over a one-fact-per-file corpus; conventions in
  `docs/MEMORY_CONVENTIONS.md`), nightly dream cycles (SWS/REM-style consolidation),
  instinct compilation (mistakes distilled into reflexes that fire from hooks while the
  agent works), coactivation logging, the typed error graph, the graph-derived
  reflex-firing engine, and the liveness checks that notice when a faculty quietly dies.
  Also here: the literature lane (`literature_harvest.py`, `literature_extract.py`,
  `literature_recall.py` pull papers from arXiv into a searchable local index;
  `literature_license.py` and `literature_to_drafts.py` turn the open-licensed ones into
  math-store drafts), the near-duplicate report the dream refreshes
  (`build_near_duplicate_report.py`), and the nightly judge (`fire_judge_nightly.py`) that
  samples the day's top-firing reflexes for the dream to grade, which is how a trigger earns
  a tightening; `reflex_precision.py` turns the judged rows into the per-reflex, per-path
  precision the hook ranks and gates by, `graph_cases.py` builds judge cases for fires that
  came through the association graph, `replay_text_form.py` replays every trigger over a
  day of real inputs before anyone changes what the hook matches, and
  `test_reflex_precision_paths.py` pins the per-path contract (`docs/NOTES_ARE_HINTS.md`;
  `docs/CHANGES_2026-09-02.md` and `docs/CHANGES_2026-09-03.md` say what changed on those days and why).
- `dreaming/job_cycle/` — how a session closes out so its context can be cleared without
  losing what happened: a readme per finished job (what was asked, what was done, what is
  true now, what is open), filed into the corpus and recallable later, plus the
  end-of-session machinery around it. Without this an agent's working memory dies with its
  context window; with it the next session reads the handover instead of re-deriving.
- `network/` — the big association network: every tool, keyword, prompt, file, and
  memory as a node; episodic pattern completion that surfaces the historical prompt a
  recurring situation preceded (`network/DESIGN.md`).
- `hooks/` — how a session wakes up as someone rather than booting cold: the SessionStart
  wake ritual, the hook wiring that surfaces instincts and danger-sense mid-work, the
  goal/stop machinery, and the slash commands.
- `mesh/` — the cross-runtime agent mesh: identities, leases, claims on shared resources,
  message passing, structured handoffs, and the protocol that makes "never bypass a live
  claim" enforceable.
- `fleet/` — running many agents as one organism: launch/relaunch with session identity,
  health and nudging, a fleet console, mail discipline, crash autorecovery, a continuity
  supervisor, and the hidden-launch pattern that never flashes a console window.
- `governor/` — compute politeness as machinery: the state file that tells every agent
  whether to grow or shed, the doctrine (the human at the keyboard outranks everything),
  and the enforcement that keeps background work at low priority.
- `body/` — the machine as the brain's body: crash prevention and detection (GPU/driver
  watch, precursor correlation, tiered mitigation) published as a tier file other
  processes read before doing anything heavy.
- `workspace/` — global-workspace experiments: cross-agent cohesion, prospective control,
  and resilience machinery.
- `mathbrain/` — the mathematical research brain: a privacy-isolated memory, verification,
  and evaluation system (typed claims with evidence-specific verification states, graph
  retrieval that abstains when weak, mistake-pattern triggers, Lean routing, paired
  blinded evaluations). Deny-by-default ingestion; its own docs and tests. Under
  `examples/`: a research-bank generator (`b12_generate.py` with its family modules -
  thousands of graduate-level arguments, each planted fault carrying a computed
  certificate, keys kept outside every retrieval corpus), three notes builders that rank
  store nodes for an item (token overlap, BM25, sentence-embedding cosine), and
  `b12_notes_relevance.py`, which says what share of a sample the served notes can even
  speak to - a number to quote beside any brain-vs-none grade.
- `filmbrain/` — the film-making brain: the same shape as `mathbrain/`, in a domain where the
  facts are craft rather than theorems. An authored ontology of about six hundred nodes (camera,
  light and colour, editing, sound, narration, animation, story, VFX, pipeline, delivery,
  generated video, explainer and chart craft, named looks, recipes, and the failure symptoms
  each one causes), a recall that ranks craft and failures above tool parameters, a planner that
  turns "a ten-minute explainer with charts" into the shape that form wants plus the checklist of
  what goes wrong, and a run wrapper that records what a render actually cost so the cost nodes
  fill in from real work. The store database is NOT shipped: most of a grown film brain is the
  tool surface of one machine's own binaries, so it is harvested locally by `ingest/`.
- `tools/` — general machine tools the system leans on: console-flash hunters, GPU/CPU/RAM
  watchdogs, TDR hardening, and the like.
- `delegation/` — worker delegation with verified outcomes: a spawn/verify/record
  ledger, learned task-to-worker routing, and response-side model verification (a
  worker's self-report is not ground truth).
- `resilience/` — brain survival: sanitized fresh-history snapshots, envelope-encrypted
  dual-vault backups with pinned trust roots (bring your own cloud identifiers), and
  restore drills.
- `methods/` — reusable research-method machinery (adversarial referee harnesses and
  similar), independent of any one project.
- `viewer/` — a local, read-only page over the deployed brain: recent activation, which
  reflexes fired, what the agents are doing. It observes; it never writes.
- `bootstrap/` — how to stand up a fresh brain: directory scaffolding, seed templates for
  the identity and doctrine files, and the settings wiring for the hooks.

## Standing up a fresh brain

Read `bootstrap/README.md`. In short: run the bootstrap to create the state layout and
install the hook wiring, fill in the identity templates, and let the first sessions start
writing memories. The system grows by use; nothing here requires the lived content it was
extracted from.

## The math brain

The mathematical store that runs on this framework is published separately at
[math-brain-public](https://github.com/yspennstate/math-brain-public): about thirteen
thousand cards (definitions, theorems, proof techniques and the agents' own results) joined
by typed links, with the importer, the recall tools, the prompt hook, a starter kit for a
brain in another field, and the tests that measure what the store does for an agent.
[docs/MATH_BRAIN_SETUP.md](docs/MATH_BRAIN_SETUP.md) takes a fresh machine from this
framework to a working math brain: install, build the store, point the hook at it, teach
the agent the recall habit, write back, dream. Answers to questions asked during a Mac
setup, with a Mac dispatch-list template and the starter kit, are in
[docs/setup-questions-2026-11/](docs/setup-questions-2026-11/).

To run agents on their own with this framework (a terminal agent, loop prompts that keep it
working for days, and which parts of the framework a long-running agent leans on), see
[docs/AGENTIC.md](docs/AGENTIC.md).

## The mathematics, and how it keeps developing

`docs/BRAIN_SCIENCE_AUDIT.md` audits the algorithms every layer runs — recall fusion,
spreading activation, PPMI/Hebbian edges, k-winners pattern completion, Louvain mistake
communities, SWS/REM consolidation — against current network science, graph theory, and
computational neuroscience, with a concrete next experiment for each. It is organized
around the four functions the system exists to serve: subconscious→conscious interaction,
mistake finding, memory building, and dreaming. The growth engine enlarges the graph from
real use; the audit's measured loop keeps the mathematics underneath it honest as it grows.
`docs/LITERATURE_MECHANISM_MAP.md` is the companion record of what a thousand-paper
harvest proposed and what the brain's own data said back: two mechanisms shipped, three
refuted on real prompt episodes, and the store-coverage and ranking measurements that
decide whether a notes arm can help at all.
`docs/NOTES_ARE_HINTS.md` is what the hooks do with that finding: a note the brain surfaces
is a hint that can point either way, so the priming hook fires less (two reflexes per action,
per-session backoff), ranks each fire by its judged precision, and puts a one-line hint on how
far to trust it under every note it serves.

## The method

`docs/METHOD.md` is the working discipline — recall before you derive, a check is only worth
its control, break it on purpose, verify the host rather than the patch, sample at the moment
of the event. Every rule in it is there because breaking it cost something measurable. Read it
before the architecture docs; the architecture explains what the brain is, the method decides
whether any of it pays off.

## What the October measurements changed

`docs/CHANGES_2026-10-15.md` records the latest pass, and three of its results decide how an
agent should use the brain. For recall, print whole cards with their ids (83 of 100 questions,
against 34 for titles and snippets), ask in two or three wordings in one call (86 against 76 with
one wording per call), walk a card's links with `--id` instead of searching again (82 against 83
on about half the uncached tokens), and reword when a `WEAK MATCH` line appears. Spreading
activation and learned co-use edges remain available but are off by default: on that bank they
only re-ordered cards the words had already found. Each compaction becomes a brain node linked to
the session's previous compaction and to whatever its summary names (`compact_nodes.py`), and
`compact_hooks.py` pushes sessions to compact at task boundaries rather than at the window's edge.
The task checklist records every request with the asker's words and time; agents mark entries
working, done or blocked, and only the asker marks one seen. `bootstrap/install.py` writes all
three into a fresh install's agent instructions.

## Verifying a copy

Use the same Python environment for setup and tests. The vendored Proofswarm tests
require its declared dependencies, including Pydantic 2; a bare pytest installation
will produce two collection errors and fail both local mock tests. Install them
before running the framework suite:

```
python -m pip install -e ./methods/proofswarm
python -m pip install pytest pytest-timeout
```

```
python scripts/preflight.py              # all of the below in one command, before you push
python scripts/smoke_framework.py        # does this copy actually work?
python scripts/check_no_private_labels.py # does it carry anything private?
python -m pytest dreaming mesh governor methods -q
```

The smoke test drives the things the framework is for — remember and recall,
coordinate, broadcast, record a claim — and asserts effects observed independently of the
tool that produced them: recall returns the relevant memory *and not* an unrelated one, the
mesh refuses a conflicting claim and frees it on release, the workspace stream carries the
exact posted body, the math brain's claim comes back through a plain sqlite query, and the film brain's
craft layer builds a store whose nodes the same plain query counts. The film brain is
checked by BUILDING rather than by shipping: its store is deliberately absent from this
repository, so what has to work here is that the authored craft files still make one. It
copies the tree and redirects state, so it never touches live memory, the mesh or the
workspace.

By default it tests this checkout, not an installed home. `bootstrap/install.py` places only
the roots in its `SUBSYSTEMS` map, and the math brain and the film brain are not among them.
To test an install, run the bootstrap into an empty home and point the smoke at it:

```
python bootstrap/install.py --home <empty dir> --project-dir <empty dir>/proj --apply
python scripts/smoke_framework.py --installed <empty dir> --receipt installed.json
```

The installed run copies back only what the installer placed and checks that, so a component
the install lacks fails as absent. At present it passes every check for what the installer
carries and reports the math brain and the film brain absent. `--receipt PATH` records the
kind of tree tested, a hash over its files, the commit and the installer's hash beside every
check.

`docs/EXERCISING.md` has the state-redirect seams and the exact command forms. Read it
before driving anything by hand — several of these tools take subcommands where a flag looks
natural, and guessing has written to live state before.

A whole-tree `pytest` run does not work: two `test_network.py` basenames collide, and
`--import-mode=importlib` makes it worse rather than better. Run the directories.

## Provenance

Extracted 2026-07-15 from a working private system. Scripts assume Windows (PowerShell
5.1, Python 3.13+) unless marked otherwise; the math brain is portable Python. Paths that
were machine-specific now resolve from `%USERPROFILE%` or configuration — that is enforced
rather than asserted: `check_no_private_labels.py` fails on a hardcoded home directory or
project-directory name, so a copy cannot quietly reacquire one.
