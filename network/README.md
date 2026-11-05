# network/

The big association network: every tool, keyword, prompt, file, and memory as a node;
firing by spreading activation and episodic pattern completion. Method and discipline in
`DESIGN.md`.

Build from your own event history:

```
python adapt_live.py --prompts-dir <archived-prompts> --outcomes <outcomes.jsonl> --out events.jsonl
python build_graph.py --db net.sqlite3 --events events.jsonl
python episodes.py    --db net.sqlite3 --events events.jsonl
```

Evaluate causally before trusting it (train-era episodes only, replayed against
held-out prompts, scored against recency/frequency baselines):

```
python eval_replay.py --db net.sqlite3 --events events.jsonl --split-ts <epoch>
```

Keep it growing — this is the standing task, not a one-time build. Every dream cycle:

```
python dream_grow.py            # folds new tools/prompts/keywords/memories past the water mark
```

`grow.py` is the incremental core (canonical nodes, intensity accumulates, idempotent);
`dream_grow.py` rebuilds the event stream from the live records and calls it. Install into
a live brain with `install_live.py`.

Retrieval returns the information, not just the node id:

```python
for node_id, score, k, content in engine.complete_with_content():
    ...   # content is the surfaced prompt's text or the memory's first paragraph
```

Wire `hook_network_fire.py` as a PostToolUse/UserPromptSubmit hook with `NETWORK_DB`
set; it runs in shadow mode (logging would-have-fired retrievals to
`network_shadow.tsv`) until you create a `LIVE` flag file beside the database.

Audit how the subconscious is actually firing (one of the development loop's signals):

```
python firing_audit.py [shadow-fires.tsv]
```

It reports the fire rate, the reflexes that fire most (habituation candidates — a reflex
firing on more than half of all actions carries almost no information), and the high-lift
co-firing pairs that are the real cascade edges. Run it once per dream to track the
cascade structure over the development program.

`surprise_rank.py` is the fix that finding points to: order what gets SURFACED by
information content (−log2 p_fire), so a rare high-stakes reflex outranks a ubiquitous
one. It never deletes a reflex (a lone low-information match still surfaces — do no harm);
it reorders, and drops the lowest-information matches only when a limited banner budget
forces a choice. `python surprise_rank.py` runs its self-test.

`python -m unittest test_network` exercises the whole path on a synthetic corpus,
including the guards that matter: recurrent spread cannot count as evidence, a retrieved
prompt cools down before it may fire again, completion returns node content, and growth is
idempotent-and-additive.
