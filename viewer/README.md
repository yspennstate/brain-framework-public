# Brain Live

Brain Live is a local, read-only view of the deployed brain. It combines:

- the canonical association database (`~/.claude/dreaming/network/net.sqlite3`);
- per-session activation buffers from `graph_fire`;
- the live agent mesh and its current write claims;
- recent file use and reflex firings from the graph-fire ledger; and
- the durable memory-to-memory graph in `spread_edges.tsv`.

The default view is the current working set. Bright red files are files observed
in tool activity during the last two minutes or protected by an active mesh
claim. Memory glow comes from the real decaying per-session activation buffers.
Moving edge light is a bounded display of that measured activation over stored
connections; it is not a claim that every visible neighbor was directly read.

## Run

On Windows, double-click `launch.vbs`. It starts Python without a console window,
binds only to `127.0.0.1`, and opens the default browser. The process exits after
20 minutes without a browser refresh.

For development:

```powershell
python .\server.py --open
```

The app has no third-party runtime dependencies and does not write to the brain.
Environment variables such as `BRAIN_NETWORK_DB`, `BRAIN_MEMORY_DIR`, and
`BRAIN_MESH_DB` can point it at a different deployment.

## Test

```powershell
python -m unittest discover -s tests -v
```
