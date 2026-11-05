"""The use-learned edges that lead to memories, as a small table a cloud session can read (26 Sep).

A cloud session sees only GitHub, and the touch events the graph is learned from stay on the laptop (they hold the words
agents and the owner wrote). What travels is derived: for every memory, folder, project or repository j and every memory i
that followed it within ten minutes in 40 days of sessions, the count, P(i | j) over j's whole row, PMI and normalised PMI
(Bouma 2009), plus each memory's node size (uses, last use). No words, no commands, no session ids.
Usage: export_mem_edges.py STORE OUT_DIR [--min-count 1]"""
import argparse
import gzip
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from graph_store import GraphStore  # noqa: E402

SRC_KINDS = ("mem:", "dir:", "proj:", "repo:")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("store")
    ap.add_argument("out")
    ap.add_argument("--min-count", type=float, default=1)
    a = ap.parse_args()
    st = GraphStore(a.store)
    os.makedirs(a.out, exist_ok=True)
    names = st.names
    is_mem = np.array([n.startswith("mem:") for n in names])
    tot = float(st.total_w)
    n_rows = 0
    with gzip.open(os.path.join(a.out, "mem_edges.tsv.gz"), "wt", encoding="utf-8") as f:
        f.write("src\tdst\tcount\tp_dst_given_src\tpmi\tnpmi\n")
        for j, nm in enumerate(names):
            if not nm.startswith(SRC_KINDS):
                continue
            lo, hi = st._slice(st.keys, j)
            if hi <= lo:
                continue
            xs = (st.keys[lo:hi] & 0xFFFFFFFF).astype(np.int64)
            c = st.cnt[lo:hi]
            m = is_mem[xs] & (c >= a.min_count) & (xs != j)
            if not m.any():
                continue
            for x, cc in zip(xs[m].tolist(), c[m].tolist()):
                p_ij = cc / st.out_sum[j]
                p_i = st.in_w[x] / tot
                pmi = math.log(p_ij / p_i)
                p_joint = cc / tot
                npmi = pmi / -math.log(p_joint) if p_joint < 1 else 0.0
                f.write("%s\t%s\t%g\t%.5g\t%.4f\t%.4f\n" % (nm, names[x], cc, p_ij, pmi, npmi))
                n_rows += 1
    sizes = {}
    for x in np.flatnonzero(is_mem).tolist():
        u = st.uses.get(int(x))
        if u:
            last, old_n, old_first = u
            sizes[names[x][4:]] = {"uses": len(last) + int(old_n or 0), "last": max(last) if last else None,
                                   "first": old_first or (min(last) if last else None)}
    json.dump(sizes, open(os.path.join(a.out, "mem_sizes.json"), "w", encoding="utf-8"), separators=(",", ":"))
    meta = {"store_built": getattr(st, "meta", {}).get("built") if hasattr(st, "meta") else None, "rows": n_rows,
            "memories_with_uses": len(sizes), "min_count": a.min_count, "total_weight": tot,
            "sources": list(SRC_KINDS),
            "note": "context -> target within 10 minutes, the 30 pct hubs dropped; sessions that built or graded the "
                    "recall banks were left out of the events (capture/exclude_bank_sessions.py)"}
    json.dump(meta, open(os.path.join(a.out, "META.json"), "w", encoding="utf-8"), indent=1)
    print(json.dumps(meta))


if __name__ == "__main__":
    main()
