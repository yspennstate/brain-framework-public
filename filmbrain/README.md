# filmbrain

The movie-making part of the brain: a node/edge graph with measurements
attached, so a pipeline can ask what is known before it renders and write back
what it found out. The same shape as the math brain beside it - one specialist
layer that answers in its own domain and learns from its own runs.

## Build the store first

**The store database is not shipped, deliberately.** Most of a grown film
brain's nodes are the *tool surface* of one machine - every filter, option, node
and render setting read out of the binaries actually installed there - and a
database copied from elsewhere describes software this machine may not have.
A fresh install has three steps:

```bash
python ingest/load_craft.py            # the authored craft layer: ~590 nodes, ~1000 edges
python tools/verify_tools.py           # find and RUN each tool; record what actually works
python ingest/harvest_ffmpeg.py        # read the installed toolchain's own surface into the graph
python ingest/ingest_blender.py        # (each harvester is optional; run the ones you have)
```

`FILMBRAIN_ROOT` moves the whole thing; `FILMBRAIN_DB` moves only the database.
Without either, both sit beside this file.

Then write `craft/this_machine.py`. It ships as a template with no figures on
purpose: it is a record of one machine's measured capability, and a number
copied from another machine will be quoted in a plan and the plan will be wrong.

## Ask it something

    python filmbrain.py recall "how do I stop a render looking computer generated"
    python filmbrain.py recall "why does synthetic narration sound robotic"
    python filmbrain.py node craft.grain
    python filmbrain.py neighbors craft.narration --depth 2
    python filmbrain.py checklist narration
    python filmbrain.py tools
    python filmbrain.py stats

`recall` ranks craft, principles and failures above tool parameters, so a
question about looking cinematic does not return four hundred ffmpeg options.
Ask for the detail explicitly with `--kind param`.

## Ask it to plan something

    python fbplan.py "a ten minute explainer about the trading book with charts" --minutes 10
    python fbplan.py "a documentary from old photographs"
    python fbplan.py --list

This is the difference between a reference and a brain. It reads what you want,
gives you the shape that form usually wants, the craft rules that apply, the
checklist of what goes wrong and what prevents each one, the recipes with their
actual commands, and what it will cost on this machine. The cost figures marked
UPPER BOUND are exactly that - a single measured unit scaled up with nothing
reused - and the realistic case is far below them.

## Teach it something

    python filmbrain.py learn "grain at strength 4 is what stops a 640x352 upscale reading as plastic" \
        --node craft.grain --evidence "88 clips, Bastogne night 09-03" --confidence verified

Confidence is `provisional`, `verified` or `owner-stated`, the same three the
main brain uses. Evidence is not optional in practice - a lesson without it is
a rumour.

## Let it record itself

Wrap any render or synthesis command and it records without anyone remembering
to:

    python fbrun.py --kind render --tool blender --node craft.rendering -- blender -b scene.blend ...

It captures the command, parameters, wall time, exit status and the artefact.
The useful part is that a render script can teach the brain by printing

    FBMETRIC gpu_minutes_per_clip=13.1
    FBLEARN grain at strength 4 stops a 640x352 upscale reading as plastic

and nothing else has to change.

## What is in it

    nodes   31,099        edges   35,124        together   66,223

| source | what it is |
|---|---|
| `authored` | the craft: camera, light, colour, editing, sound, narration, animation, story, VFX, pipeline, delivery, AI video, the toolchain, and runnable recipes |
| `harvest:ffmpeg` | every filter, option, enum value, encoder, muxer and pixel format in the ffmpeg on this machine, read out of the binary |
| `harvest:blender` | every operator, shader/compositor/geometry node, modifier, constraint and render setting in the Blender on this machine, introspected from bpy |
| `curated` | the links between the two, so a craft concept names the exact filter that implements it |
| `stub` | pointed at but not yet written - a to-do list, not an error |

The tool surface is the bulk of the count and the craft layer is the part that
makes it a film brain. Both matter: without the craft it is an API dump,
without the API it cannot tell you the command.

Node kinds: craft, principle, failure, tool, param, preset, engine, voice,
metric, asset, work, person, project, run, format, reference, term.

## Layout

    fb_store.py            schema and the store
    filmbrain.py           the CLI - recall, node, neighbors, learn, record, checklist, tools, stats
    fbplan.py              "I want to make X" -> shape, craft, failures, recipes, cost
    fbrun.py               wrap a command, record the run
    craft/                 the authored knowledge, one file per domain
    ingest/                harvesters: ffmpeg, blender, the craft loader, the cross-linker
    pipeline/              beatfilm.py (record-first film engine), visuals.py (cards, charts,
                           image moves, treated clips), make_timeline.py (MLT for Kdenlive)
    experiments/           things actually run: the Blender look test, the recipe proofs, the films
    tools/                 installers, verify_tools.py, INVENTORY.json
    store/filmbrain.db     sqlite, WAL, FTS5 over nodes and lessons
    logs/                  install and harvest logs

To rebuild after editing the craft files:

    python ingest/load_craft.py          # idempotent, reports dangling stubs
    python ingest/link_craft_to_tools.py # curated craft -> tool edges

To re-harvest after a tool upgrade:

    python ingest/harvest_ffmpeg.py
    "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe" --background \
        --factory-startup --python ingest/blender_probe.py -- store/blender_api.json
    python ingest/ingest_blender.py

## Conventions worth keeping

- A node's `source` says what kind of truth it is. Harvested, authored and
  measured are different things and must stay separable.
- Nothing is deleted. A superseded node keeps its edges and gains a
  `superseded_by` edge. Default recall excludes non-active nodes, nodes with
  a superseded_by edge, and lessons linked to either. Explicit `node <id>`
  keeps historical content accessible and shows its status and replacement edges.
- A relation that points at a node that does not exist creates a stub. Stubs
  are the to-do list; `load_craft.py` prints them.
- Measurements point at the run that produced them, so a number can always be
  traced back to the render that made it.

## The companion

`<HOME>\narration_lab` is where the voice work lives - the measurement
rig, the engines, the human reference corpus and the experiments. Its findings
come back here as lessons.
