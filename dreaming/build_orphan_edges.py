#!/usr/bin/env python3
"""build_orphan_edges.py - make everything NOT in the main graph component REACHABLE, safely.

23% of memories are graph orphans (degree 0), and beyond them sit small ISLANDS - coherent clusters
with internal co-read edges but no bridge to the main graph (e.g. an example project-audit cluster,
a vit-pruning research cluster). Both are invisible to a session working in the main graph
(brain_orphan_reachability_2026_07_10). This connects them by machine WITHOUT editing any memory body
(that would race the concurrent dreams and churn prose): it writes a SEPARATE edge file, orphan_edges.tsv,
that build_spread_edges merges as a third, low-weight source. Reversible (delete + rebuild), contained,
and eval-gated (eval_orphan_edges.py proves non-regression before it is wired).

COMPONENT-AWARE: it finds connected components, takes the largest as "main", and for every OTHER
component adds ONE bridge - the (component-node, main-node) pair with the highest calibrated cosine from
the bge sidecar (AUC ~0.90, STRONG), above a threshold, at a weight BELOW a wiki-link. One bridge makes
a whole island reachable. The anchor is guaranteed to be in main (attaching an orphan to its nearest
neighbour is not enough if that neighbour is also disconnected - clustered orphans would just form a new
island). Took the graph 95.2% -> 99.6% single-component, 0.000 recall regression. Read-only bar the tsv.
Threshold is on the CALIBRATED cosine (recentred by the sidecar's ~0.68 bias): default 0.40 attaches
~283 of 334 orphans at genuinely-related anchors and excludes the noisy tail (e.g. an imagenet memory
mis-matching an options one at calibrated 0.16). Leaving a weak-anchor orphan unconnected beats a wrong
edge. Usage:  python build_orphan_edges.py [--min-cos 0.40] [--weight 0.8] [--quiet]
"""
import io, os, re, sys
from collections import defaultdict
try:
    import embedding_artifact
except Exception:
    embedding_artifact = None

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
COACT = os.path.join(DR, "coactivation.tsv")
EMB = os.path.join(DR, "memory_embeddings.npz")
EMB_AUTHORITY = os.path.join(DR, ".embedding_current.json")
OUT = os.path.join(DR, "orphan_edges.tsv")
LINK_RE = re.compile(r"\[\[([^\]]+?)\]\]")
DATE_TAIL = re.compile(r"_\d{4}_\d{2}_\d{2}$")
SKIP = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS", "AUTOBIOGRAPHY",
        "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS", "MISTAKES", "CONSCIENCE", "CURIOSITY"}


def norm(s):
    s = s.strip()
    if s.endswith(".md"):
        s = s[:-3]
    return DATE_TAIL.sub("", s.replace("-", "_").lower())


def _argf(flag, d):
    a = sys.argv[1:]
    for i, x in enumerate(a):
        if x == flag and i + 1 < len(a):
            try:
                return float(a[i + 1])
            except Exception:
                return d
    return d


def orphan_edges(min_cos=0.55, weight=0.8):
    """Return list of (orphan_slug, anchor_slug, weight). Pure function for the eval to reuse."""
    real = {norm(fn): fn[:-3] for fn in os.listdir(MEM) if fn.endswith(".md") and fn[:-3] not in SKIP}
    deg = defaultdict(int)
    for fn in os.listdir(MEM):
        if not fn.endswith(".md") or fn[:-3] in SKIP:
            continue
        src = norm(fn)
        if src not in real:
            continue
        try:
            body = io.open(os.path.join(MEM, fn), encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for m in LINK_RE.findall(body):
            b = norm(m)
            if b in real and b != src:
                deg[src] += 1
                deg[b] += 1
    if os.path.exists(COACT):
        for ln in io.open(COACT, encoding="utf-8", errors="replace"):
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 3 and p[0] != "a":
                a, b = norm(p[0]), norm(p[1])
                if a in real and b in real and a != b:
                    deg[a] += 1
                    deg[b] += 1
    # adjacency (wiki + co-read) for connected-component analysis: attaching a node to its nearest
    # neighbour is not enough if that neighbour is ALSO disconnected (clustered orphans would just form
    # a new island - and pre-existing islands, e.g. an example project-audit cluster, never touch
    # main at all). So the anchor must live in the MAIN component; one bridge per non-main component
    # then makes the WHOLE component reachable from the main graph.
    adj = defaultdict(set)
    for fn in os.listdir(MEM):
        if not fn.endswith(".md") or fn[:-3] in SKIP:
            continue
        src = norm(fn)
        if src not in real:
            continue
        try:
            body = io.open(os.path.join(MEM, fn), encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for m in LINK_RE.findall(body):
            b = norm(m)
            if b in real and b != src:
                adj[src].add(b)
                adj[b].add(src)
    if os.path.exists(COACT):
        for ln in io.open(COACT, encoding="utf-8", errors="replace"):
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 3 and p[0] != "a":
                a, b = norm(p[0]), norm(p[1])
                if a in real and b in real and a != b:
                    adj[a].add(b)
                    adj[b].add(a)
    # connected components (nodes with no edges are their own size-1 component = orphans)
    seen = set()
    comps = []
    for n in real:
        if n in seen:
            continue
        stack, comp = [n], []
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            comp.append(x)
            for nb in adj.get(x, ()):
                if nb not in seen:
                    stack.append(nb)
        comps.append(comp)
    comps.sort(key=len, reverse=True)
    main = set(comps[0]) if comps else set()

    if embedding_artifact is None:
        return [], real, deg
    import numpy as np
    loaded = embedding_artifact.load_verified_embeddings(
        EMB, EMB_AUTHORITY, MEM, "BAAI/bge-small-en-v1.5", verify_all_rows=True
    )
    if loaded is None:
        return [], real, deg
    raw_idx, mat, bias = loaded
    idx = {norm(s): i for s, i in raw_idx.items()}
    # bge-small has a ~0.68 random-pair cosine baseline; recenter by the sidecar's own bias so the
    # threshold means "genuinely related", not "0.04 above chance" (self-calibration, as spread_recall).
    main_ids = np.array([idx[s] for s in main if s in idx])
    main_slugs = [s for s in main if s in idx]
    if main_ids.size == 0:
        return [], real, deg
    main_mat = mat[main_ids]
    out = []
    for comp in comps:
        if main and set(comp) & main:      # this IS (part of) the main component; skip
            continue
        # best (comp_node, main_node) bridge by calibrated cosine - one edge connects the whole component
        best = None
        for s in comp:
            if s not in idx:
                continue
            sims = main_mat @ mat[idx[s]]
            j = int(np.argmax(sims))
            c = (float(sims[j]) - bias) / (1.0 - bias)
            if best is None or c > best[2]:
                best = (s, main_slugs[j], c)
        if best and best[2] >= min_cos:
            out.append((real[best[0]], real[best[1]], round(weight * best[2], 3)))
    return out, real, deg


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes build artifacts, so a help
        # request must exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    min_cos = _argf("--min-cos", 0.40)
    # weight-factor 0.1 was the eval-chosen point (eval_orphan_edges.py, 2026-07-10): orphan edges strong
    # enough to lift a degree-0 node to reachable, weak enough that EXISTING recall is non-regressed to
    # exactly 0.000 on hit@10/@25/MRR across 3 splits. 0.2+ cost a small consistent hit@25 loss.
    weight = _argf("--weight", 0.1)
    quiet = "--quiet" in sys.argv[1:]
    edges, real, deg = orphan_edges(min_cos, weight)
    try:
        with io.open(OUT, "w", encoding="utf-8") as f:
            f.write("a\tb\tweight\n")
            for a, b, w in edges:
                f.write(f"{a}\t{b}\t{w}\n")
    except Exception as e:
        if not quiet:
            print("build_orphan_edges: write failed:", e)
        return
    n_orphan = sum(1 for s in real if deg.get(s, 0) == 0)
    if not quiet:
        print(f"build_orphan_edges: {n_orphan} orphans; wrote {len(edges)} attachment edges "
              f"(cos>={min_cos}, weight-factor {weight}) -> {OUT}")
        for a, b, w in edges[:6]:
            print(f"  {a}  ->  {b}   (w {w})")


if __name__ == "__main__":
    main()
