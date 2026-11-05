"""build_repeats_ab.py - what counting an exact repeat once does to the store (28 Sep, claude-825b1305; account03/B12 R12).

    python build_repeats_ab.py OUT_JSON [--keep-stores DIR]

Two builds on the same events with run_graph's own inputs (event files, bank exclusion, half-life, verdict file, test
workers left out) and one fixed `until`, differing only in graph_store.build(collapse_repeats=...). Reports the
events, pairs, co-activation pairs and total weight of each, how edge weights moved on the edges both hold, the edges
only one holds, the hubs, and the overlap of the 1,000 heaviest edges. The live store is not touched; the two stores
are written to a temp folder and removed unless --keep-stores names one.
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import json
import os
import shutil
import sys
import tempfile
import time

import numpy as np

BIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BIN)
import graph_store      # noqa: E402
import run_graph        # noqa: E402


def load(d):
    z = np.load(os.path.join(d, "edges.npz"))
    meta = json.load(open(os.path.join(d, "nodes.json"), encoding="utf-8"))
    return z["keys"], z["cnt"], meta["names"], meta["meta"]


def main():
    out = sys.argv[1]
    keep = sys.argv[sys.argv.index("--keep-stores") + 1] if "--keep-stores" in sys.argv else None
    run_graph.polite() if hasattr(run_graph, "polite") else None
    files = run_graph.event_files()
    try:
        hl = run_graph._config().get("half_life_days")
    except Exception:
        hl = None
    until = time.time() - 120.0
    tmp = keep or tempfile.mkdtemp(prefix="repeats_ab_")
    # graph_store counts the lines before it reads them and the live build holds the graph lock so the capture cannot
    # append in between; this script takes no lock, so both arms read a copy made once, here
    snap = os.path.join(tmp, "snap")
    os.makedirs(snap, exist_ok=True)
    copied = []
    for f in files:
        shutil.copyfile(f, os.path.join(snap, os.path.basename(f)))
        copied.append(os.path.join(snap, os.path.basename(f)))
    vf = run_graph._verdict_file()
    if vf and os.path.exists(vf):
        shutil.copyfile(vf, os.path.join(snap, os.path.basename(vf)))
        vf = os.path.join(snap, os.path.basename(vf))
    files = copied
    res = {"until": until, "files": [os.path.basename(f) for f in files], "half_life_days": hl, "arms": {}}
    for arm, flag in (("kept", False), ("collapsed", True)):
        t0 = time.time()
        d = os.path.join(tmp, arm)
        shutil.rmtree(d, ignore_errors=True)
        meta = graph_store.build(files if len(files) > 1 else files[0], d, until=until,
                                 exclude_rx=graph_store.BANK_RX, half_life_days=hl,
                                 verdict_file=vf, skip_session=run_graph._not_a_test_worker(),
                                 collapse_repeats=flag)
        res["arms"][arm] = {k: meta.get(k) for k in ("events", "sessions", "pairs", "co_pairs", "total_w",
                                                     "repeats_collapsed", "hubs")}
        res["arms"][arm]["seconds"] = round(time.time() - t0, 1)
        print(arm, json.dumps({k: v for k, v in res["arms"][arm].items() if k != "hubs"}), flush=True)
    ka, ca, na, _ = load(os.path.join(tmp, "kept"))
    kb, cb, nb, _ = load(os.path.join(tmp, "collapsed"))
    if na != nb:                      # node ids are assigned in reading order; map b's names onto a's ids
        ida = {n: i for i, n in enumerate(na)}
        remap = np.array([ida.get(n, -1) for n in nb], np.int64)
        src, dst = kb >> 32, kb & 0xFFFFFFFF
        kb = (remap[src] << 32) | remap[dst]
        res["node_names_differ"] = True
    ia = {int(k): float(c) for k, c in zip(ka.tolist(), ca.tolist())}
    ib = {int(k): float(c) for k, c in zip(kb.tolist(), cb.tolist())}
    common = ia.keys() & ib.keys()
    ratio = np.array([ib[k] / ia[k] for k in common if ia[k] > 0])
    res["edges"] = {"kept": len(ia), "collapsed": len(ib), "both": len(common), "only_kept": len(ia.keys() - ib.keys()),
                    "only_collapsed": len(ib.keys() - ia.keys()),
                    "weight_ratio_on_shared": {q: float(np.percentile(ratio, p)) for q, p in
                                               (("p1", 1), ("p10", 10), ("median", 50), ("p90", 90), ("p99", 99))},
                    "shared_edges_lighter": int((ratio < 0.999).sum()), "shared_edges_heavier": int((ratio > 1.001).sum())}
    top_a = set(sorted(ia, key=ia.get, reverse=True)[:1000])
    top_b = set(sorted(ib, key=ib.get, reverse=True)[:1000])
    res["top1000_overlap"] = len(top_a & top_b)
    ha, hb = set(res["arms"]["kept"]["hubs"] or []), set(res["arms"]["collapsed"]["hubs"] or [])
    res["hubs"] = {"kept_only": sorted(ha - hb), "collapsed_only": sorted(hb - ha), "both": len(ha & hb)}
    json.dump(res, open(out, "w", encoding="utf-8"), indent=1)
    print(json.dumps({k: v for k, v in res.items() if k not in ("arms", "files")}, indent=1))
    if not keep:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
