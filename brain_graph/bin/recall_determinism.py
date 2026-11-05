"""Run brain.py recall on the same questions under different PYTHONHASHSEED values and count where the printed lists
differ (27 Sep, claude-825b1305). Pinned index, store and sidecar; the live recall log is left alone. Prints ids and
counts only (the bank's gold stays in the quarantine).
    python recall_determinism.py OUT_JSON --store STORE_DIR --sidecar SIDECAR [--n-his 56] [--n-rew 64] [--seeds 1,2,3]
                                 [--workers 4]
STORE_DIR and SIDECAR are pinned copies (a live store is rebuilt under a running test); a copied store needs its walk
cache built first (BRAIN_GRAPH_STORE=STORE_DIR python graph_lane.py --build). 27 Sep result: 0 of 120 questions differed
across three hash seeds at the live dial (results/RECALL_DETERMINISM_2026-09-27.json).
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import concurrent.futures, json, os, re, shutil, subprocess, sys, tempfile, time
HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
BIN = os.path.join(HOME, ".claude", "brain_graph", "bin")
sys.path.insert(0, DR); sys.path.insert(0, BIN)
NOWIN = 0x08000000 if os.name == "nt" else 0
ROW = re.compile(r"^\s+([0-9]*\.?[0-9]+)\s+\[([^\]]*)\]\s+(\S+)\s*$")
a = sys.argv[1:]
out = a[0]
def arg(k, d): return a[a.index(k) + 1] if k in a else d
n_his, n_rew = int(arg("--n-his", 56)), int(arg("--n-rew", 64))
seeds = [int(x) for x in arg("--seeds", "1,2,3").split(",")]
workers = int(arg("--workers", 4))
import eval_graph_mixture as egm
its = egm.items()
his = [x for x in its if x[0] == "his56"][:n_his]
rew = [x for x in its if x[0] == "reworded_heldout"][:n_rew]
items = his + rew
import bm25
tmp = tempfile.mkdtemp(prefix="recall_det_")
idx = os.path.join(tmp, "bm25_index_pinned.json.gz")
shutil.copy2(bm25.INDEX, idx)
if os.path.exists(bm25.INDEX + ".pkl"):
    shutil.copy2(bm25.INDEX + ".pkl", idx + ".pkl")
store = os.path.abspath(arg("--store", ""))
side = os.path.abspath(arg("--sidecar", ""))
if not os.path.isdir(store) or not os.path.isfile(side):
    raise SystemExit("--store (a directory) and --sidecar (a file) are required: pinned copies, not the live ones")
base_env = dict(os.environ, BRAIN_BM25_INDEX=idx, BRAIN_RECALL_REFRESH="0", BRAIN_BM25_POSTINGS="0",
                BRAIN_GRAPH_STORE=store, BRAIN_GRAPH_SIDECAR=side, RECALL_REWRITE_NO_LOG="1",
                BRAIN_RECALL_LOG="1", BRAIN_RECALL_LOG_FULL="1")
def run(item, seed):
    kind, key, q, gold = item
    log = os.path.join(tmp, "%s_%d.jsonl" % (key, seed))
    env = dict(base_env, PYTHONHASHSEED=str(seed), BRAIN_RECALL_LOG_PATH=log)
    p = subprocess.run([sys.executable, "-X", "utf8", os.path.join(DR, "brain.py"), "recall", q, "-n", "20"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900, env=env,
                       creationflags=NOWIN)
    rows = []
    for ln in p.stdout.splitlines():
        m = ROW.match(ln)
        if m:
            rows.append((m.group(3), m.group(1)))
    return rows
t0 = time.time()
res = {}
jobs = [(it, s) for it in items for s in seeds]
with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
    for (it, s), rows in zip(jobs, ex.map(lambda j: run(*j), jobs)):
        res.setdefault(it[1], {"kind": it[0], "gold": it[3], "runs": {}})["runs"][s] = rows
summary = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "seeds": seeds, "n": len(items), "seconds": 0,
           "differ_top5": 0, "differ_top10": 0, "differ_top20": 0, "gold_rank_differs": 0, "hit5_flips": 0,
           "empty_runs": 0, "differ_only_at_equal_printed_scores": 0, "ids_differ_top10": []}
for key, r in res.items():
    lists = [r["runs"][s] for s in seeds]
    if any(not l for l in lists):
        summary["empty_runs"] += 1
        continue
    names = [[x for x, _ in l] for l in lists]
    for k in (5, 10, 20):
        if len({tuple(n[:k]) for n in names}) > 1:
            summary["differ_top%d" % k] += 1
    if len({tuple(n[:10]) for n in names}) > 1:
        summary["ids_differ_top10"].append(key)
    gr = [next((i + 1 for i, x in enumerate(n) if x.lower() in r["gold"]), 0) for n in names]
    if len(set(gr)) > 1:
        summary["gold_rank_differs"] += 1
    h5 = [bool(g and g <= 5) for g in gr]
    if len(set(h5)) > 1:
        summary["hit5_flips"] += 1
    if len({tuple(n) for n in names}) > 1:
        # every position where the names differ carries the same printed score in every run
        ok = True
        L = min(len(n) for n in names)
        for i in range(L):
            if len({n[i] for n in names}) > 1:
                sc = {l[i][1] for l in lists}
                if len(sc) > 1:
                    ok = False
        if ok:
            summary["differ_only_at_equal_printed_scores"] += 1
summary["seconds"] = round(time.time() - t0)
json.dump(summary, open(out, "w", encoding="utf-8"), indent=1)
print(json.dumps({k: v for k, v in summary.items() if k != "ids_differ_top10"}))
print("ids with a different top 10:", summary["ids_differ_top10"][:40])
shutil.rmtree(tmp, ignore_errors=True)
