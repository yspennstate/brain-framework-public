# Exercising this framework without touching live state

Every component here has a documented seam that redirects its state to a throwaway
location. None of them is obvious from the outside, and guessing costs real damage: an
agent-mesh test written against four invented environment variables wrote two fake agents
and a live claim into the shared mesh before anyone noticed.

The rule that follows from that: **read the entry point, then redirect, then verify the
redirect worked by checking the live target afterwards.** A test that prints its own
containment claim is not evidence. The empty temp directory is.

## The seams

| component | redirect with | verified by |
|---|---|---|
| memory / recall | `BRAIN_MEM_DIR` | index built and queried in the temp corpus |
| agent mesh | `AGENT_MESH_HOME` (`agent_mesh.py`) | live `agent_mesh.sqlite3` mtime unchanged |
| global workspace | `--db` flag, or `GLOBAL_WORKSPACE_DB` | live `global_workspace.sqlite3` mtime unchanged |
| math brain | the database is an explicit CLI argument | database created under the temp path |
| session handover | `JOB_CYCLE_STATE` (`dreaming/job_cycle`) | the readme is found by walking the temp state tree, not by the writer's exit code |
| dream cycle | positional `[memory_dir] [out_path]` | the agenda names a memory that exists only in the fixture |

Several scripts also write artifacts *next to themselves* — `bm25` its index, `instincts`
its compiled tsv, `dream.py` its agenda. Copy the tree to a temp directory before running
them, or they will modify the checkout you are testing.

## Commands that actually work

Discovered by reading the parsers, after four wrong guesses.

```
# memory: build an index, then query it
python dreaming/bm25.py build                  # NOT --build
python dreaming/bm25.py query "some words"
python dreaming/brain.py recall "some words"

# agent mesh: claims are the point - a second agent must be refused
python mesh/agent_mesh.py hello  --agent A --runtime claude --task "..."
python mesh/agent_mesh.py claim  --agent A --resource topic:x --purpose "..." --mode exclusive
python mesh/agent_mesh.py claim  --agent B --resource topic:x --purpose "..." --mode exclusive   # exit 3
python mesh/agent_mesh.py release --agent A --resource topic:x
python mesh/agent_mesh.py bye    --agent A --summary "..."      # --summary is required

# global workspace: broadcast and the shared intent ledger
python workspace/global_workspace/workspace.py --db DB post --agent A \
    --kind found --body "..." --salience 4                      # --kind and --body, not --text
python workspace/global_workspace/workspace.py --db DB stream
python workspace/global_workspace/workspace.py --db DB intent-add --agent A --goal "..."
python workspace/global_workspace/workspace.py --db DB intents

# math brain
python -m math_brain.cli init DB
python -m math_brain.cli add-claim DB "statement" --kind theorem --domain optimization
python -m math_brain.cli recall DB "query"
```

`post --kind` accepts doing, found, decided, need, warning, done. `add-claim --kind` accepts
definition, lemma, theorem, conjecture, computation, heuristic, example, counterexample.

## What a real check looks like

Exit 0 means the command ran, not that it worked. Each of these was confirmed by an effect
observed independently of the tool that produced it:

- recall returns the relevant memory **and not** an unrelated one in the same corpus;
- the mesh refuses the second claim with `claim_conflict`, then accepts it after release;
- the workspace stream contains the exact body that was posted;
- the math brain's claim is readable from the database by a plain sqlite client.

The index line in `MEMORY.md` carries each memory's real description, and the lexical arm of
recall matches against those lines. A fixture that writes a placeholder description there
will make recall correctly find nothing, which looks like a broken engine and is not.
