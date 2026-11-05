"""eval_graph_mixture.py - the graph lane as deployed, measured held out (the audit fleet's job R7; 27 Sep, claude-825b1305).

brain.py recall blends its word-search body with the graph lane (the use-grown walk and the memories' own links, half
and half) at the dial's weight, 0.2 today. That weight was set at 0.1 from the reworded bank and then moved to 0.15 and
0.2 by graph_learn from searches that turned out to be the benches' test workers. This measures the deployed mixture on
questions no learner saw: his 56 and the held-out half of the 22 Sep reworded bank.

One real recall per question and store, with the lane on and its record sent to a private file (BRAIN_RECALL_LOG_PATH):
the record holds the body before blending, the graph list and what was shown, so the order at any weight is
head + graph_lane.fuse(body, graph, weight) - exactly what recall would have shown. Everything else is pinned for the run:
one copy of the word index with refresh and publish off, and the store named on the command line.

    python eval_graph_mixture.py OUT_JSON --store NAME=PATH [--store NAME=PATH ...] [--workers 4] [--limit N]
                                 [--mixes 0,0.5,1] [--sidecar PATH]

--mixes runs every store once per use/link mixture (BRAIN_GRAPH_MIX; 0 = the use-grown walk alone, 1 = the link walk
alone), each arm named STORE@MIX; --sidecar pins a copy of the link graph (BRAIN_GRAPH_SIDECAR), which the live capture
rebuilds every 15 minutes. The output records the sha256 of the pinned index, each store's edges and the sidecar.

Per-question rows name gold memories, so they go to the key quarantine; the output holds counts only.

WHAT WEIGHT 0 IS (27 Sep, scheduled audit account04/B12 r6): recall's head (policy routes and attachment promotions,
scored above the bm25 peak) and its body (bm25's hits with the name and question-coverage boosts, a superseded memory
collapsed onto its correction) are kept as recall built them, and the association and semantic tails sit below both,
so weight 0 is the deployed stack without the use-grown lane - the fusion effect conditional on recall's own pools,
which is what the dial decides - and not a lexical-only system. The output says so under 'estimand'. Every
hash is written in full; the replay control names the questions it could not reproduce.
"""
import concurrent.futures
import glob
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time

HOME = os.path.expanduser("~")
BIN = os.path.dirname(os.path.abspath(__file__))
DR = os.path.join(HOME, ".claude", "dreaming")
sys.path.insert(0, DR)
sys.path.insert(0, BIN)
Q = os.path.join(HOME, "bank_recall_keys_quarantine")
NOWIN = 0x08000000 if os.name == "nt" else 0
LAMBDAS = (0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4)


import os as _ko_os, sys as _ko_sys  # noqa: E402
_ko_sys.path.insert(0, _ko_os.path.join(_ko_os.path.expanduser("~"), ".claude", "brain_graph", "bin"))
from quarantine_rows import keep_old  # noqa: E402  a rerun keeps the previous output (R13 trap 1, 27 Sep): brain data is never deleted


def half(qid):
    return "tune" if int(hashlib.sha1(qid.encode()).hexdigest(), 16) % 2 == 0 else "test"


def items(limit=0):
    out = []
    import eval_brain_recall_fresh as fresh
    for i, (q, golds) in enumerate(fresh.BANK + fresh.BANK_V2_EXTRA):
        golds = {g.lower() for g in golds if os.path.exists(os.path.join(fresh.MEM, g + ".md"))}
        if golds:
            out.append(("his56", "b%03d" % i, q, golds))
    solved = set(json.load(open(os.path.join(Q, "headless_solver_sessions_20260926.json"), encoding="utf-8")).values())
    for ln in open(os.path.join(Q, "recall_keys.jsonl"), encoding="utf-8"):
        if not ln.strip():
            continue
        k = json.loads(ln)
        if k["id"] in solved or half(k["id"]) != "test":
            continue
        out.append(("reworded_heldout", k["id"], k["q_paraphrase"], {k["source_slug"].lower()}))
    return out[:limit] if limit else out


# what every recall call runs under besides the pinned index, its log path, the store and the mixture (recorded in the
# output with the command line, B12 r7's third recommendation)
RECALL_ENV = {"BRAIN_RECALL_LOG": "1", "RECALL_REWRITE_NO_LOG": "1", "BRAIN_RECALL_LOG_FULL": "1", "BRAIN_GRAPH_LAMBDA": "0.2"}


def one(store, pinned_env, tmp, item, mix=None):
    kind, key, q, gold = item
    log = os.path.join(tmp, "%s_%s.jsonl" % (hashlib.sha1(("%s|%s" % (store, mix)).encode()).hexdigest()[:8], key))
    env = dict(os.environ, BRAIN_RECALL_LOG_PATH=log, BRAIN_GRAPH_STORE=store, **RECALL_ENV, **pinned_env)
    if mix is not None:
        env["BRAIN_GRAPH_MIX"] = str(mix)
    try:
        subprocess.run([sys.executable, "-X", "utf8", os.path.join(DR, "brain.py"), "recall", q, "-n", "20"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900, env=env,
                       creationflags=NOWIN)
        rec = [json.loads(l) for l in open(log, encoding="utf-8") if l.strip()][-1]
    except Exception:
        return None
    return {"kind": kind, "key": key, "gold": sorted(gold), "body": rec["body"], "graph": rec["graph"],
            "shown": rec["shown"], "n_use": rec.get("n_use"), "n_link": rec.get("n_link"), "mix_logged": rec.get("mix")}


def ranks(row):
    import graph_lane
    body = [(s, float(v)) for s, v in row["body"]]
    graph = [(s, float(v)) for s, v in row["graph"]]
    in_body, in_graph = {s for s, _ in body}, {s for s, _ in graph}
    # brain.py: order = head + fuse(body, graph). The head (items above the lexical peak) is in neither list and is the
    # front of what recall showed; a graph item that is not in the body is NOT head (27 Sep: the first rule took every
    # shown item missing from the body as head, and 201 of 544 replays failed). The lists are logged whole
    # (BRAIN_RECALL_LOG_FULL), since fuse() scales each by its own minimum.
    head = []
    for s in row["shown"]:
        if s in in_body or s in in_graph:
            break
        head.append(s)
    row["replayable"] = all(s in in_body or s in in_graph for s in row["shown"][len(head):])
    out = {}
    for lam in LAMBDAS:
        order = head + [s for s, _ in graph_lane.fuse(body, graph, lam, "convex")]
        r = next((i + 1 for i, s in enumerate(order) if s.lower() in row["gold"]), 0)
        out[lam] = r
        if lam == 0.2:
            # the positive control: every recall ran at 0.2, so the replay at 0.2 must be what recall showed
            row["replay_matches_shown"] = order[:len(row["shown"])] == list(row["shown"])
    return out


SHOWN = 20                    # brain.py recall -n 20


def shown(rank):
    return rank if rank and rank <= SHOWN else 0


def sign_p(a, b):
    n, k = a + b, min(a, b)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n) if n else 1.0


def main():
    a = sys.argv[1:]
    out_path = a[0]
    stores = [s.split("=", 1) for i, s in enumerate(a) if i and a[i - 1] == "--store"]
    workers = int(a[a.index("--workers") + 1]) if "--workers" in a else 4
    limit = int(a[a.index("--limit") + 1]) if "--limit" in a else 0
    mixes = [float(x) for x in a[a.index("--mixes") + 1].split(",")] if "--mixes" in a else [None]
    sidecar = a[a.index("--sidecar") + 1] if "--sidecar" in a else ""
    import bm25
    tmp = tempfile.mkdtemp(prefix="graph_mixture_")
    idx = os.path.join(tmp, "bm25_index_pinned.json.gz")
    shutil.copy2(bm25.INDEX, idx)
    if os.path.exists(bm25.INDEX + ".pkl"):
        shutil.copy2(bm25.INDEX + ".pkl", idx + ".pkl")
    pinned_env = {"BRAIN_BM25_INDEX": idx, "BRAIN_RECALL_REFRESH": "0", "BRAIN_BM25_POSTINGS": "0"}
    if sidecar:
        pinned_env["BRAIN_GRAPH_SIDECAR"] = sidecar
    its = items(limit)
    t0 = time.time()

    def sha(path):
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    def sha16(path):
        return sha(path)[:16]
    res = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "lambdas": LAMBDAS, "n_items": len(its),
           "estimand": "fusion_off_conditional: weight 0 keeps recall's head and body as built (policy/attachment "
                       "head; bm25 hits with their boosts and superseded-collapse); a graph weight is compared against "
                       "the deployed stack without the use-grown lane, not against a lexical-only system",
           "index_sha256_16": sha16(idx), "index_sha256": sha(idx), "mixes": mixes,
           "sidecar": {"path": sidecar, "sha256_16": sha16(sidecar), "sha256": sha(sidecar)} if sidecar else "live (not pinned)",
           "store_edges_sha256_16": {name: sha16(os.path.join(store, "edges.npz")) for name, store in stores
                                     if os.path.exists(os.path.join(store, "edges.npz"))},
           "store_sha256": {name: {f: sha(os.path.join(store, f)) for f in ("edges.npz", "nodes.json", "uses.json")
                                   if os.path.exists(os.path.join(store, f))} for name, store in stores},
           # the use walk is read from a cache keyed by the store's absolute path (diffusion.Diffusion.cached): identical
           # store bytes at another path have no walk, so the cache is part of the state (account03/B01 auto31, 27 Sep)
           "walk_cache_sha256": {name: {os.path.basename(c): sha(c) for c in sorted(glob.glob(os.path.join(
               DR, "graph_lane_cache", hashlib.sha1(os.path.abspath(store).lower().encode("utf-8")).hexdigest()[:12] + "_*.npz")))}
               for name, store in stores},
           "stores": {}}
    rows_all = []
    for (name0, store), mix in [(s, m) for s in stores for m in mixes]:
        name = name0 if mix is None else "%s@%g" % (name0, mix)
        rows = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            for n, r in enumerate(ex.map(lambda it: one(store, pinned_env, tmp, it, mix), its), 1):
                if r is not None:
                    r["store"] = name
                    r["mix"] = mix
                    r["ranks"] = ranks(r)
                    rows.append(r)
                # a store copied to a new path has no walk cache (diffusion.cached keys it by path, and recall never
                # builds one), so the use walk comes back empty and combine() quietly falls back to the links: an arm
                # that asks for the use walk and gets none on its first rows is not measuring what it says (27 Sep)
                if len(rows) == 10 and (mix is None or mix < 1) and all(not x.get("n_use") for x in rows):
                    raise SystemExit("the use walk is empty on the first 10 recalls of %s: build the store's walk "
                                     "(BRAIN_GRAPH_STORE=%s python graph_lane.py --build) and rerun" % (name, store))
                if n % 20 == 0:
                    print("%s %d/%d (%.0f s)" % (name, n, len(its), time.time() - t0), flush=True)
        rows_all += rows
        rep = [r for r in rows if r.get("replayable")]
        st = {"replay_matches_shown": "%d of %d" % (sum(1 for r in rows if r.get("replay_matches_shown")), len(rows)),
              "replayable": len(rep),
              "replay_mismatch_ids": sorted(r["key"] for r in rows if not r.get("replay_matches_shown")),
              "rows_missing": len(its) - len(rows),
              "replay_matches_shown_of_replayable": "%d of %d" % (sum(1 for r in rep if r.get("replay_matches_shown")),
                                                                  len(rep))}
        for kind in sorted({r["kind"] for r in rows}):
            rs = [r for r in rows if r["kind"] == kind]
            d = {"n": len(rs)}
            for lam in LAMBDAS:
                # a rank past the lines recall prints counts as not shown: the graph lane extends the list to ~350
                # items, and a gold memory absent at weight 0 that turns up at 169 is neither a gain nor a loss (27 Sep)
                rk = [shown(r["ranks"][lam]) for r in rs]
                base = [shown(r["ranks"][0.0]) for r in rs]
                h5 = [bool(x and x <= 5) for x in rk]
                b5 = [bool(x and x <= 5) for x in base]
                gi = sum(1 for x, y in zip(b5, h5) if y and not x)
                go = sum(1 for x, y in zip(b5, h5) if x and not y)
                d[str(lam)] = {"hit1": sum(1 for x in rk if x == 1), "hit5": sum(h5), "hit10": sum(1 for x in rk if x and x <= 10),
                               "mrr": round(sum(1.0 / x for x in rk if x) / max(1, len(rs)), 4),
                               "top5_in_vs_0": gi, "top5_out_vs_0": go, "sign_p": round(sign_p(gi, go), 4),
                               "rank_up_vs_0": sum(1 for x, y in zip(base, rk) if (y or 99) < (x or 99)),
                               "rank_down_vs_0": sum(1 for x, y in zip(base, rk) if (y or 99) > (x or 99))}
            st[kind] = d
            print(name, kind, json.dumps({k: v for k, v in d.items() if k in ("n", "0.0", "0.1", "0.2")}), flush=True)
        res["stores"][name] = {"path": store, "sets": st}
    with open(keep_old(os.path.join(Q, "graph_mixture_rows_%s.jsonl" % time.strftime("%Y%m%d_%H%M"))), "w", encoding="utf-8") as fh:
        for r in rows_all:
            fh.write(json.dumps(r) + "\n")         # the graph list too: a replay can be rechecked offline
    res["seconds"] = round(time.time() - t0)
    # FAIL CLOSED (account04/B14 r2 and B12 r6, 27 Sep): the replay at 0.2 is the positive control, so a run in which any
    # arm lost a question or replayed one differently from what recall showed is written with control_passed false and
    # exits 2; its counts are not a result
    res["control_passed"] = all(st["sets"]["rows_missing"] == 0 and not st["sets"]["replay_mismatch_ids"]
                                for st in res["stores"].values())
    # A RESOLVABLE ARTIFACT (scheduled audit account04/B12 r7, 27 Sep): a hash without the bytes cannot be rerun, so the
    # pinned word index is kept under a name made from its own hash (one copy per index generation, never overwritten),
    # and the code that produced the numbers is named by hash too. Only the .json.gz is kept (25 MB): the .pkl beside it
    # is bm25.py's parsed copy of the same json, rebuilt on load (39 MB), so its hash is recorded and its bytes are not.
    keep_dir = os.path.join(HOME, ".claude", "brain_graph", "study", "pinned_indexes")
    os.makedirs(keep_dir, exist_ok=True)
    dst = os.path.join(keep_dir, "bm25_%s.json.gz" % res["index_sha256_16"])
    if not os.path.exists(dst):
        shutil.copy2(idx, dst)
    kept = {os.path.basename(dst): sha(dst)}
    res["index_retained"] = {"dir": keep_dir, "files": kept,
                             "pkl_cache_sha256_not_kept": sha(idx + ".pkl") if os.path.exists(idx + ".pkl") else None}
    res["code_sha256"] = {os.path.basename(p): sha(p) for p in (os.path.abspath(__file__),
                          os.path.join(DR, "graph_lane.py"), os.path.join(DR, "brain.py"), os.path.join(DR, "bm25.py"),
                          os.path.join(DR, "diffusion.py")) if os.path.exists(p)}
    res["command"] = [os.path.basename(sys.argv[0])] + sys.argv[1:]
    res["environment"] = {"set_for_every_recall": dict(RECALL_ENV, **pinned_env),
                          "set_per_arm": ["BRAIN_GRAPH_STORE", "BRAIN_GRAPH_MIX", "BRAIN_RECALL_LOG_PATH"],
                          "inherited": {k: v for k, v in sorted(os.environ.items()) if k.startswith(("BRAIN_", "RECALL_"))}}
    json.dump(res, open(keep_old(out_path), "w", encoding="utf-8"), indent=1)
    shutil.rmtree(tmp, ignore_errors=True)
    if not res["control_passed"]:
        print("CONTROL FAILED: " + json.dumps({k: {"missing": v["sets"]["rows_missing"], "mismatch": v["sets"]["replay_mismatch_ids"]}
                                              for k, v in res["stores"].items()}), flush=True)
        sys.exit(2)


if __name__ == "__main__":
    main()
