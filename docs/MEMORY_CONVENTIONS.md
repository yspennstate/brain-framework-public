# Memory conventions

The corpus is one directory of markdown files, one fact per file, plus a handful of
standing ledgers. The machinery (recall, dreaming, views) assumes these conventions.

## A memory file

```markdown
---
name: short-kebab-or-snake-slug_2026_01_01
description: one line used for recall ranking and the index - make it carry the finding
metadata:
  type: user | feedback | project | reference
  status: provisional | verified   # optional; claims start provisional
---

The fact itself. For feedback/project memories, follow with **Why:** and
**How to apply:** lines. Link related memories with [[their_slug]] - the association
graph builds from these links, so link liberally; a link to a not-yet-written memory
marks something worth writing, not an error.
```

Types: `user` (who your partner is), `feedback` (guidance on how to work, with the why),
`project` (ongoing work and constraints not derivable from the code), `reference`
(pointers to external resources). Dates in slugs are the event's date, absolute, never
relative.

## The index

`MEMORY.md` holds one line per memory - `- [title](file.md) - hook` - newest first. It
is auto-loaded into every session, so the hook line must carry the finding, not describe
it. Never put memory content in the index. `brain.py remember` maintains both the file
and the index line; when writing by hand under concurrency, claim `memory:brain/index`
on the mesh first.

## Standing ledgers

These files are not in this repository. They live in the memory directory, except the
two generated views, which go to the brain views directory (`~/brain-views` by default). A
fresh install has none of them until `bootstrap/install.py` runs; it writes a one-line seed
for the first six and leaves the rest to the code or to you.

| File | Seeded by install | Kept up by | What it holds |
|---|---|---|---|
| `MISTAKES.md` | yes | you | dated corrections; the dream reads it and compiles recurring ones into reflexes |
| `CONSCIENCE.md` | yes | `conscience.py` and the dream | debts and patterns where recent work fell short of the values |
| `OPEN_QUESTIONS.md` | yes | the dream, or you | checkbox list of what the brain is carrying |
| `STATE_OF_MIND.md` | yes | the dream | Focus / Worry / Satisfaction lines |
| `MODEL_OF_PARTNER.md` | yes | the dream; seed it by hand | what your partner is in the middle of; read at wake |
| `AUTOBIOGRAPHY.md` | yes | you, at first, then the dream | the story so far, read first at wake |
| `SALIENCE.md` | no | `salience.py` (generated) | what is vivid right now |
| `GROWTH.md` | no | `growth.py` (generated) | where the brain changed its mind |
| `identity_who_you_are.md`, `values_what_we_stand_for.md` | no | you | who the agent is and what it holds to; ordinary memories; the wake checks that they exist |
| `THE_OTHER_MINDS.md`, `FASCINATIONS.md` | no | you | sibling agents sharing the brain; standing curiosities |
| `dreams/` | no | the dream | one journal per dream cycle |

None of these is required for recall to work. The wake hook (`hooks/wake_brain.py`) reads
whichever exist and prints a WARN line naming any it expects that is missing, or, for
`MODEL_OF_PARTNER.md`, `STATE_OF_MIND.md` and `SALIENCE.md`, older than 30 hours. A
one-paragraph file you write yourself is enough to clear the warning.

## Hygiene the machinery enforces

- A memory is superseded, not edited into a lie: `schema_supersede.py` tracks
  supersession; the recall stack demotes superseded facts.
- Descriptions carry status brackets (`[PROVISIONAL]`, `[VERIFIED]`) - a claim's
  evidence class travels with it.
- Transient agent scratch (`transient_*`) stays out of dream agendas.
- Every push of the memory tree passes the secret scanner first.
