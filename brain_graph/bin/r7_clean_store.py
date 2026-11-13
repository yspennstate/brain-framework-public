"""Build a copy of the use-grown graph store without the test workers' events, for the held-out measurement of the
deployed graph mixture.

Mirrors run_graph.build and run_graph._recall_walk exactly - same event files (build_config.json), the bank exclusion,
the half-life and the verdict file - except that the live touch file is first copied without the events of sessions
session_kinds.py calls 'test'. Nothing live is written: the store goes to OUT, and its walk cache to the graph lane's
cache folder under a key made from OUT's path, which no live store has. The events on disk are untouched.

Its name says clean; what it builds is a KNOWN-TEST-EXCLUDED store: only
sessions classified 'test' are dropped, and his, 'script' and 'unknown' sessions stay, so it is not a store of the
target population. A target-population store needs an allowlist of admissible sessions, which this does not have.

    python r7_clean_store.py OUT_DIR
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import json
import os
import sys
import time

HOME = os.path.expanduser("~")
BIN = os.path.join(HOME, ".claude", "brain_graph", "bin")
DR = os.path.join(HOME, ".claude", "dreaming")
sys.path.insert(0, BIN)
sys.path.insert(0, DR)
import graph_store  # noqa: E402
import run_graph  # noqa: E402
import session_kinds  # noqa: E402


def main():
    out = os.path.abspath(sys.argv[1])
    os.makedirs(out, exist_ok=True)
    t0 = time.time()
    files = run_graph.event_files()
    touch = files[0]
    filtered = os.path.join(out, "events_touch_without_tests.jsonl")
    kinds, kept, dropped = {}, 0, 0
    with open(touch, encoding="utf-8", errors="replace") as src, open(filtered, "w", encoding="utf-8") as dst:
        for ln in src:
            try:
                s = json.loads(ln).get("session") or ""
            except ValueError:
                continue
            k = kinds.get(s)
            if k is None:
                k = kinds[s] = session_kinds.kind(s)
            if k == "test":
                dropped += 1
                continue
            dst.write(ln)
            kept += 1
    session_kinds.save()
    print("filtered %s: kept %d events, dropped %d (test workers, %d sessions) in %.0f s"
          % (os.path.basename(touch), kept, dropped, sum(1 for v in kinds.values() if v == "test"), time.time() - t0),
          flush=True)
    try:
        hl = json.load(open(os.path.join(run_graph.HOME, "build_config.json"), encoding="utf-8")).get("half_life_days")
    except Exception:
        hl = None
    store = os.path.join(out, "store")
    t1 = time.time()
    ev = [filtered] + files[1:]
    meta = graph_store.build(ev if len(ev) > 1 else filtered, store, exclude_rx=graph_store.BANK_RX,
                             half_life_days=hl, verdict_file=run_graph._verdict_file())
    print("store: %s in %.0f s" % ({k: meta.get(k) for k in ("events", "sessions", "pairs", "co_pairs", "excluded_events")},
                                    time.time() - t1), flush=True)
    import diffusion
    import graph_lane
    d = graph_lane.dial()
    t2 = time.time()
    g = diffusion.Diffusion.cached(store, co_w=d["co_w"], cap=int(d["cap"]), alpha=d["alpha"],
                                   cache_dir=os.path.join(DR, "graph_lane_cache"), build=True)
    print("walk: %d nodes, %d edges in %.0f s; total %.0f s" % (g.N, g.nnz, time.time() - t2, time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
