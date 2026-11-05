"""eval_rewrite_fused.py - the rewrite tool against plain recall at the same number of lines shown (27 Sep, claude-825b1305).

The rewrite measurement (memory a_cheap_rewrite_before_recall_finds_the_note_far_more_often_2026_09_25) counted the
source memory in ANY of four top-5 lists, up to 20 notes, against the question's own top 5: candidate coverage, not what
an agent reads (audit account01/B19, R5). recall_rewrite.py shows one interleaved list. This runs, on today's index, the
question (top 20) and the three rewrites the replication saved (top 10 each, as the tool asks), interleaves them with the
tool's own function, and compares plain@k with fused@k at k = 1, 5, 10, 20 lines. No model call: the rewrites are the
saved ones. Every recall runs with BRAIN_RECALL_LOG=0, at below-normal priority, without a console.

    python eval_rewrite_fused.py OUT_JSON [--bank rep300|100] [--workers 3] [--limit N]
Rows (bank ids and ranks) go to the recall-keys quarantine under a run-specific name; the result holds counts only.
"""
import concurrent.futures
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
import time

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
sys.path.insert(0, DR)
import recall_rewrite as RW  # noqa: E402  (search() and interleave(), exactly as the tool runs them)

Q = os.path.expanduser("~/bank_recall_keys_quarantine/recall4_r1")
BANKS = {"rep300": ("bank_rep300.jsonl", "r1w_rep300_rows.jsonl"), "100": ("bank100.jsonl", "r1w_rows.jsonl")}
KS = (1, 5, 10, 20)


import os as _ko_os, sys as _ko_sys  # noqa: E402
_ko_sys.path.insert(0, _ko_os.path.join(_ko_os.path.expanduser("~"), ".claude", "brain_graph", "bin"))
from quarantine_rows import keep_old  # noqa: E402  a rerun keeps the previous output (R13 trap 1, 27 Sep): brain data is never deleted


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sign_p(a, b):
    n = a + b
    return 1.0 if not n else min(1.0, 2 * sum(math.comb(n, j) for j in range(min(a, b) + 1)) / 2 ** n)


def rank_in(slug, rows):
    for i, r in enumerate(rows, 1):
        if r[0].lower() == slug.lower():
            return i
    return None


def one(item, rewrites):
    plain = RW.search(item["q_paraphrase"], 20)
    per = max(5, -(-20 // 4) + 5)                         # the tool's per-list depth for 20 lines of four queries
    lists = [plain[:per]] + [RW.search(q, per) for q in rewrites]
    fused = [x[0] for x in RW.interleave(lists, 20)]
    return {"id": item["id"], "stratum": item.get("stratum"), "plain": rank_in(item["source_slug"], plain),
            "fused": rank_in(item["source_slug"], fused), "n_plain": len(plain), "n_lists": [len(x) for x in lists]}


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    out = a[0]
    bank = a[a.index("--bank") + 1] if "--bank" in a else "rep300"
    workers = int(a[a.index("--workers") + 1]) if "--workers" in a else 3
    limit = int(a[a.index("--limit") + 1]) if "--limit" in a else None
    os.environ["BRAIN_RECALL_LOG"] = "0"                  # the children inherit it: evaluations stay out of the log
    # ONE retrieval state for the whole run (scheduled audit account02/B08 auto11, 27 Sep): the 27 Sep equal-lines run
    # took 40 minutes on whatever index and sidecar were live, so its rewrite lists could come from different generations.
    # Every recall now reads a pinned copy of the word index and of the link sidecar; the graph store (rebuilt from the
    # capture) is hashed at the start and the end, so a rebuild during the run is on the record.
    import bm25
    import graph_lane
    tmp = tempfile.mkdtemp(prefix="rewrite_fused_")
    idx = os.path.join(tmp, "bm25_index_pinned.json.gz")
    shutil.copy2(bm25.INDEX, idx)
    if os.path.exists(bm25.INDEX + ".pkl"):
        shutil.copy2(bm25.INDEX + ".pkl", idx + ".pkl")
    side_live = os.path.join(graph_lane.LIVE, "sidecar.sqlite")
    side = os.path.join(tmp, "sidecar_pinned.sqlite")
    if os.path.exists(side_live):
        shutil.copy2(side_live, side)
        os.environ["BRAIN_GRAPH_SIDECAR"] = side
    os.environ.update({"BRAIN_BM25_INDEX": idx, "BRAIN_RECALL_REFRESH": "0", "BRAIN_BM25_POSTINGS": "0"})
    store = graph_lane.dial().get("store") or ""
    edges = os.path.join(store, "edges.npz")
    state = {"index_sha256": sha(idx), "sidecar_sha256": sha(side) if os.path.exists(side) else None,
             "graph_lambda": graph_lane.dial().get("lambda"), "graph_store": store,
             "store_edges_sha256_start": sha(edges) if os.path.exists(edges) else None}
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass
    bf, rf = BANKS[bank]
    items = {x["id"]: x for x in (json.loads(l) for l in open(os.path.join(Q, bf), encoding="utf-8") if l.strip())}
    saved = [json.loads(l) for l in open(os.path.join(Q, rf), encoding="utf-8") if l.strip()]
    todo = [(items[r["id"]], r["queries"][:3]) for r in saved if not r.get("error") and r["id"] in items]
    if limit:
        todo = todo[:limit]
    t0 = time.time()
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for n, row in enumerate(ex.map(lambda x: one(*x), todo), 1):
            rows.append(row)
            if n % 25 == 0:
                print("%d/%d  %.0f s" % (n, len(todo), time.time() - t0), flush=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    with open(keep_old(os.path.join(Q, "rewrite_fused_rows_%s_%s.jsonl" % (bank, stamp))), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    hit = lambda r, k: r is not None and r <= k
    res = {"what": __doc__.split("\n\n")[0], "built": time.strftime("%Y-%m-%d %H:%M:%S"), "bank": bank, "n": len(rows),
           "empty_plain_lists": sum(1 for r in rows if not r["n_plain"]), "by_k": {}, "by_stratum": {},
           "rows_file": "bank_recall_keys_quarantine/recall4_r1/rewrite_fused_rows_%s_%s.jsonl" % (bank, stamp),
           "seconds": round(time.time() - t0)}
    for k in KS:
        both = sum(1 for r in rows if hit(r["plain"], k) and hit(r["fused"], k))
        po = sum(1 for r in rows if hit(r["plain"], k) and not hit(r["fused"], k))
        fo = sum(1 for r in rows if hit(r["fused"], k) and not hit(r["plain"], k))
        res["by_k"][str(k)] = {"plain": both + po, "fused": both + fo, "plain_only": po, "fused_only": fo,
                               "sign_p": round(sign_p(po, fo), 4)}
    for s in sorted({r["stratum"] for r in rows if r["stratum"]}):
        sub = [r for r in rows if r["stratum"] == s]
        res["by_stratum"][s] = {str(k): {"n": len(sub), "plain": sum(hit(r["plain"], k) for r in sub),
                                         "fused": sum(hit(r["fused"], k) for r in sub)} for k in (5, 20)}
    state["store_edges_sha256_end"] = sha(edges) if os.path.exists(edges) else None
    state["store_rebuilt_during_run"] = state["store_edges_sha256_end"] != state["store_edges_sha256_start"]
    res["retrieval_state"] = state
    json.dump(res, open(keep_old(out), "w", encoding="utf-8"), indent=1)
    shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps(res["by_k"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
