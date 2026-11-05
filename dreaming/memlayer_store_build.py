#!/usr/bin/env python3
"""Build a COPY of the full brain store with the staged memory layer joined in (TOP10 rank 8, claude-1ca628ae, 3 Nov).

  memlayer_store_build.py [--out DIR]     default ~/.claude/dreaming/graph_lane_store_memlayer

Reads graph_lane_store (never writes it) and graph_lane_stage_memlayer/{nodes_add,edges_add}.jsonl. Appends the
missing memories as mem:<slug> nodes and adds every staged edge (links_to, cites, supersedes) as a co-activation edge
in both directions with count 1, so the use walk can reach a memory that no hooked session ever touched. Keys are
src<<32|dst, so appending nodes leaves every existing key valid. cites edges whose file node is not in the store are
dropped (a new file node with one edge would only be a dead end). The live dial is untouched: an arm points at the copy
with BRAIN_GRAPH_STORE.
"""
import json, os, shutil, sys, time
import numpy as np

HOME = os.path.expanduser("~")
SRC = os.path.join(HOME, ".claude/dreaming/graph_lane_store")
STAGE = os.path.join(HOME, ".claude/dreaming/graph_lane_stage_memlayer")


def main():
    out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else SRC + "_memlayer"
    tmp = out + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    d = json.load(open(os.path.join(SRC, "nodes.json")))
    names = d["names"]
    n0 = len(names)
    ix = {n: i for i, n in enumerate(names)}
    added = 0
    for line in open(os.path.join(STAGE, "nodes_add.jsonl")):
        nid = json.loads(line)["id"]
        if nid not in ix:
            ix[nid] = len(names); names.append(nid); added += 1
    pairs, kinds, dropped = set(), {}, 0
    for line in open(os.path.join(STAGE, "edges_add.jsonl")):
        e = json.loads(line)
        s, t = e["src"], e["dst"]
        # a staged mem: id may live in the store only as memw:<slug>; use whichever node the store has
        s = s if s in ix else ("memw:" + s[4:] if "memw:" + s[4:] in ix else s)
        t = t if t in ix else ("memw:" + t[4:] if "memw:" + t[4:] in ix else t)
        if s not in ix or t not in ix or s == t:
            dropped += 1; continue
        a, b = ix[s], ix[t]
        for p in ((a, b), (b, a)):
            if p not in pairs:
                pairs.add(p); kinds[e["type"]] = kinds.get(e["type"], 0) + 1
    N = len(names)
    z = np.load(os.path.join(SRC, "edges.npz"))
    new_keys = np.array(sorted((a << 32) | b for a, b in pairs), np.int64)
    co_keys, co_cnt = z["co_keys"], z["co_cnt"]
    have = np.isin(new_keys, co_keys)          # an existing co edge keeps its learned count
    new_keys = new_keys[~have]
    arrays = {k: z[k] for k in z.files}
    arrays["co_keys"] = np.concatenate((co_keys, new_keys))
    arrays["co_cnt"] = np.concatenate((co_cnt, np.ones(len(new_keys), co_cnt.dtype)))
    pad = N - n0
    arrays["out_sum"] = np.concatenate((z["out_sum"], np.zeros(pad, z["out_sum"].dtype)))
    arrays["in_w"] = np.concatenate((z["in_w"], np.zeros(pad, z["in_w"].dtype)))
    np.savez(os.path.join(tmp, "edges.npz"), **arrays)
    d["names"] = names
    d["meta"] = dict(d.get("meta", {}), memlayer={"added_nodes": added, "added_co_edges": int(len(new_keys)),
                                                   "already_co": int(have.sum()), "dropped": dropped,
                                                   "by_type_directed": kinds, "built": time.strftime("%F %T %z")})
    json.dump(d, open(os.path.join(tmp, "nodes.json"), "w"))
    for f in ("uses.json", "first_touch.json", "INSTALLED_RELEASE.json"):
        if os.path.exists(os.path.join(SRC, f)):
            shutil.copy2(os.path.join(SRC, f), tmp)
    shutil.rmtree(out, ignore_errors=True)
    os.replace(tmp, out)
    print(json.dumps({"out": out, "nodes": N, "nodes_before": n0, **d["meta"]["memlayer"]}, indent=1))


if __name__ == "__main__":
    main()
