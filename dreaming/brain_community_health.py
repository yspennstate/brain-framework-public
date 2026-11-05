#!/usr/bin/env python3
"""brain_community_health.py - community/modularity structure of the association graph (spread_edges.tsv).

The connectivity view (brain_graph_structure) says the graph is ~99% reachable; this asks a different
question: is it well-ORGANIZED (knowledge clusters into coherent topics) or a hairball (everything mixed)?
Modularity Q answers it - Q>0.3 is clear community structure, Q~0 is a hairball. And per major research
PROGRAM: do its members sit in ONE community (coherent, navigable) or scatter across many (fragmented =
missing internal cross-links, an actionable gap like the one build_series_edges closed).

Baseline (2026-07-11, 1444 nodes / 4579 edges): Q = 0.76 (stable across seeds), 28 communities mapping to
real themes (build-infra, audit, brain-architecture, research domains, observers). Research programs are
COHERENT (arnold_diffusion 97% in one community, rmt_ensemble 100%) - the reachability+series edge work
produced good structure, not a tangle. The only diffuse clusters are STATUS-PING memos (build_watchdog
across 5 communities), which is correct - operational logs are not a topic. No actionable fragmentation.
Re-run periodically: a DROP in Q as the corpus grows would signal the graph is de-organizing. Read-only.
Usage: python brain_community_health.py
"""
import os, re, io
from collections import Counter, defaultdict
import networkx as nx

DR = os.path.dirname(os.path.abspath(__file__))
EDGES = os.path.join(DR, "spread_edges.tsv")


def load_graph():
    G = nx.Graph()
    for i, ln in enumerate(io.open(EDGES, encoding="utf-8", errors="replace")):
        if i == 0:
            continue
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 3 and p[0] != "a":
            try:
                G.add_edge(p[0], p[1], weight=float(p[2]))
            except Exception:
                pass
    return G


DATE = re.compile(r"_\d{4}_\d{2}_\d{2}$")
def stem(slug):
    s = DATE.sub("", slug)
    return re.sub(r"_(cycle|round|w|day|part|v)\d+.*$", "", s, flags=re.I)


def theme(members):
    toks = Counter()
    for m in members:
        parts = DATE.sub("", m).split("_")
        if parts:
            toks[parts[0]] += 1
    return ", ".join(f"{t}({c})" for t, c in toks.most_common(4))


def main():
    G = load_graph()
    print(f"graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    # multi-seed robustness: Louvain is stochastic; a stable Q means the structure is real, not a seed fluke
    qs = []
    best = None
    for seed in (1, 42, 100, 7, 2024):
        c = nx.community.louvain_communities(G, weight="weight", seed=seed, resolution=1.0)
        q = nx.community.modularity(G, c, weight="weight")
        qs.append(q)
        if best is None or q > best[0]:
            best = (q, c)
    qmean = sum(qs) / len(qs)
    qspread = max(qs) - min(qs)
    print(f"modularity Q = {qmean:.3f} mean, spread {qspread:.3f} across 5 seeds "
          f"({'STABLE' if qspread < 0.03 else 'seed-sensitive'}); Q>0.3 = clear communities, Q~0 = hairball")
    comms = best[1]
    node_comm = {n: cid for cid, c in enumerate(comms) for n in c}
    sizes = sorted((len(c) for c in comms), reverse=True)
    print(f"communities: {len(comms)}  top sizes: {sizes[:12]}")

    print("\n--- largest communities, by dominant token ---")
    for c in sorted(comms, key=len, reverse=True)[:8]:
        print(f"  size {len(c):4d}: {theme(c)}")

    print("\n--- major program fragmentation (members -> #communities) ---")
    by_stem = defaultdict(list)
    for n in G.nodes():
        by_stem[stem(n)].append(n)
    progs = sorted(((s, ns) for s, ns in by_stem.items() if len(ns) >= 5), key=lambda x: -len(x[1]))
    for s, ns in progs[:14]:
        cids = Counter(node_comm.get(n) for n in ns)
        dom = cids.most_common(1)[0][1] / len(ns)
        flag = "  <- FRAGMENTED (missing cross-links?)" if len(cids) >= 3 and dom < 0.6 else ""
        print(f"  {s[:44]:44s} {len(ns):3d} memos -> {len(cids)} communities (dominant {dom:.0%}){flag}")


if __name__ == "__main__":
    main()
