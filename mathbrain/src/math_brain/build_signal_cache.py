"""Build the read-only index-signal cache for the store.

retrieve() indexes every node on every query. Profiled 2026-08-30 over a 3191-node store,
that was 4.07s of a 6.7s call, and it took the injection hook to 2.6-3.7s per prompt against
its own 2.5s budget. The signals are a pure function of the node text, so the work is
identical every time and can be done once.

This script does it once. The hook loads the result read-only and never writes it, so two
sessions running concurrently cannot race on the file. Re-run it after any bulk ingest.

    python -m math_brain.build_signal_cache
"""

from __future__ import annotations

import os
import pickle
import sys
import time
from pathlib import Path

from math_brain.retrieval import _index_signals_uncached, _signal_key
from math_brain.store import ResearchStore

# MATHBRAIN_STORE (as the hook reads it), else the population store of this checkout. Until 30 Sep one machine's
# absolute user path was committed here, as it was in expansion.py (account04/B03).
STORE = Path(os.environ.get("MATHBRAIN_STORE", "").strip()
             or Path(__file__).resolve().parents[2] / "local" / "population" / "brain.sqlite3")
CACHE = STORE.with_name("brain_signal_cache.pkl")


def publish(cache: dict, dest: Path, tries: int = 20) -> None:
    """Write the cache beside dest under this writer's own temporary name, read it back whole, then replace dest.

    Until 29 Sep 2026 every build wrote through one fixed name, dest + ".tmp": a second builder opening it truncated the
    first builder's finished file, the first then published a zero-byte cache, and the second failed on a name already
    moved (interactive audit 20260929T154310Z, INT-R79-SIGNAL-CACHE-SHARED-TEMP-20260929). A reader copes with a bad cache
    (load_signal_cache returns 0 and retrieve() recomputes), so this cost speed, not answers. Each writer now has a unique
    temporary file and removes only that one; what is published always reads back as a whole cache; the replace is retried
    while a reader holds the destination open (Windows refuses to replace an open file)."""
    import os
    import tempfile

    fd, tmp = tempfile.mkstemp(prefix=dest.name + ".", suffix=".tmp", dir=str(dest.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            pickle.dump(cache, fh, protocol=pickle.HIGHEST_PROTOCOL)
            fh.flush()
            os.fsync(fh.fileno())
        with open(tmp, "rb") as fh:
            back = pickle.load(fh)
        if not isinstance(back, dict) or len(back) != len(cache):
            raise RuntimeError("the cache written to %s did not read back whole" % tmp)
        for attempt in range(tries):
            try:
                os.replace(tmp, dest)
                break
            except PermissionError:
                if attempt == tries - 1:
                    raise
                time.sleep(0.25)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def main() -> int:
    t0 = time.perf_counter()
    with ResearchStore(str(STORE)) as store:
        nodes = list(store.iter_nodes())

    # Index exactly the text retrieve() indexes: `node.title + " " + node.body` per node
    # (retrieval.py, the document_signals comprehension). The first version of this builder
    # hashed the title and the body SEPARATELY, so retrieve() never hit the cache - every
    # lookup missed and the uncached path ran - which is why the 2026-08-30 A/B measured the
    # cache as slower (the load cost with none of the saving). Found 2026-09-03 by asking the
    # cache whether it held the combined text: it did not. A drift here still costs speed and
    # never correctness: a miss simply recomputes. Only the combined text is cached: the
    # separate title and body forms are never looked up, and leaving them out keeps the file
    # a third of the size (measured 2026-09-03: 17.4 MB with all three forms, load 0.18 s;
    # combined only, load about 0.1 s; retrieve 0.30 s against 1.05 s uncached in a fresh process).
    cache = {}
    for n in nodes:
        text = (n.title or "") + " " + (n.body or "")
        if not text.strip():
            continue
        key = _signal_key(text)
        if key not in cache:
            cache[key] = _index_signals_uncached(text)

    publish(cache, CACHE)

    print(f"  {len(nodes)} nodes -> {len(cache)} cached signal entries "
          f"in {time.perf_counter()-t0:.1f}s")
    print(f"  wrote {CACHE.name} ({CACHE.stat().st_size/1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
