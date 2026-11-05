# Video brain: craft cards and a delivery checker for film work

This folder is a small extension of `filmbrain`. It holds the preferences one
person gave for how their films should be made, written as craft cards, plus a
checker that refuses a delivery manifest whose evidence is missing or stale. It
does not render anything and does not touch the filmbrain graph on its own.

It is also a worked example of a pattern that carries over to other domains:
write down what the person you work for keeps asking for, attach each item to a
concrete production step and a check, and make the final hand-off fail closed
when the check has no evidence behind it.

## Running it

From this folder, with any Python 3.9 or later and no extra packages:

```bash
python -B video_brain.py brief "make a documentary with maps and narration"
python -B video_brain.py check templates/documentary.json
python -B video_brain.py check path/to/manifest.json --artifact-root path/to/project
```

`brief` routes a task description to the relevant domains and prints the cards
for them as JSON: the practice, where it stops applying, the check that shows it
was followed, and the source it came from. `check` reads a delivery manifest
(see `DELIVERY_SCHEMA.md`) and prints `PASS` or `BLOCKED` with one finding per
problem. The two templates are deliberately empty and return `BLOCKED`.
`--artifact-root` also hashes the files the manifest names, which can be slow on
large media.

## What is here

- `catalog.py`: the source of truth. Nine domains (intent, geometry, design,
  story, voice, timing, review, compute, delivery) and the cards under them.
  `knowledge()` builds the card list from it at run time.
- `video_brain.py`: the `brief` router and the `check` command.
- `HANDBOOK.md`, `modules/*.md`, `knowledge.json`: the same cards rendered for
  reading. They are a snapshot of `catalog.py`; if you edit the catalog, the
  command-line tool picks the change up at once, but these files do not.
- `DELIVERY_SCHEMA.md`, `templates/`: the manifest format and two blank forms,
  one for a documentary and one for a lecture.
- `STATUS.json`, `DASHBOARD.md`: the evidence stages recorded when the pack was
  first built.

The cards cite their sources by short ids (`CH...`, `CX...`, dated direction
notes). Those point into the original author's conversation records, which are
not published; read them as "this came from a stated preference or an observed
failure", not as links you can follow. Several cards also describe one
machine's hardware and voice choices (a particular narration voice and speed, a
GPU target). Treat those as an example of the level of detail that helps, and
replace them with your own.

## What is not here

The tests, the scripts that regenerate the handbook and templates, the importer
that adds these cards to a live filmbrain store, the deploy script and the
prompt-review ledger stayed in the private source tree. Nothing in this folder
calls them. If you want the cards in the filmbrain graph, write a short loader
that adds each card in `knowledge.json` as a node through `fb_store.Store`, the
way `filmbrain/ingest/load_craft.py` adds the craft modules, and keep the cards'
`owner.video.` id prefix so a reload touches only them.

## Making one for your own work

1. Replace `DOMAINS` and `CARDS` in `catalog.py`. Each card is
   `(slug, title, practice, boundary, check, sources)`; the slug's first part
   must be one of your domain names. Keep the boundary honest: a rule with no
   stated limit gets applied where it does not fit.
2. Edit the keyword patterns in `video_brain.py` (the `brief` command) so the
   words your tasks use reach the right domains. Try a few realistic task
   descriptions and a few that should not fire.
3. Change the manifest checks to the evidence your hand-off really needs: for a
   report it might be sources and a second reviewer, for a release the build
   hash and the test run. Start every new field as required and loosen it only
   with a reason.
4. Re-render `HANDBOOK.md` so people can read what the tool enforces.

The checker can only confirm that the recorded evidence is consistent. It
cannot tell whether anyone watched the film, whether a picture explains the
idea, or whether the content is right. That still takes a person, or an agent,
looking at the finished thing.
