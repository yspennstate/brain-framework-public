#!/usr/bin/env python3
"""build_communities.py - group the memories into communities of the association graph, for TOPICS.md.

TOPICS.md sorted memories into five keyword buckets (build_brain_architecture.topic_of): one held 52% of the graph,
"Other" most of the rest. Louvain communities on spread_edges.tsv (resolution 1, seed 1) do far better on the same
graph (30 Oct, d2b_54_communities.py, coactivation pairs held out): modularity .143 -> .752, mean same-cluster
cosine above random +.011 -> +.056, and a session's next read lands in the cluster of the one before 53.7% of the
time against 9.9% by chance, where the buckets gave 41.8% against 26.2%.

Each community is named by the slug words most over-represented in it (count in community / expected from the whole
graph, words in at least 5 of its members) and by its best-connected member. Communities under MIN_SIZE members are
pooled as "small communities". Writes communities.tsv (slug, community number, label); the views builder groups
TOPICS.md by it while it is under 3 days old. Read-only over the memory folder.
Usage:  python build_communities.py [--quiet]
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import collections, io, math, os, re, sys, time

DR = os.path.dirname(os.path.abspath(__file__))
EDGES = os.path.join(DR, "spread_edges.tsv")
OUT = os.path.join(DR, "communities.tsv")
HEALTH = os.path.join(DR, "graph_health.tsv")
MIN_SIZE = 20
MIN_DF = 30          # a label word must be common across the graph, not one series' private token
HEX = re.compile(r"^[0-9a-f]{6,}$")
DATE_TAIL = re.compile(r"_\d{4}_\d{2}_\d{2}$")
STOP = set("a an the of to in on for and or is are was be by at as it its not no vs with from into than that this "
           "what when why how do does did can one two new old".split())


def words(slug):
    # plain words only: session ids (bb437fd8), tickers and row codes (at15, row3) made unreadable labels
    return {w for w in DATE_TAIL.sub("", slug).lower().split("_")
            if len(w) > 3 and w.isalpha() and w not in STOP and not HEX.match(w)}


def health(G, n_comms, q):
    """One row per run into graph_health.tsv (1000-ways 2B #74): the graph's size, clustering and mean path length, so
    a drift in its shape shows as a trend. On 30 Oct: clustering .255 against .0002 for a random graph of the same
    size, mean path 5.29 against 5.58, one component (d2b_74_small_world.py). Path length (BFS out of 300 seeded
    sources, about 2 min in the background dream: 60 sources gave 5.02 against 5.29, too noisy for a trend; two 300-source sets gave 5.21 and 5.29, so read only moves over .1)."""
    import random
    import networkx as nx
    try:
        C = nx.average_clustering(G)
        cc = G.subgraph(max(nx.connected_components(G), key=len))
        tot = cnt = 0
        for s in random.Random(5).sample(sorted(cc), min(300, cc.number_of_nodes())):
            for _n, d in nx.single_source_shortest_path_length(cc, s).items():
                if d:
                    tot += d
                    cnt += 1
        new = not os.path.exists(HEALTH)
        with io.open(HEALTH, "a", encoding="utf-8") as f:
            if new:
                f.write("date\tnodes\tedges\tlargest_component\tclustering\tmean_path\tcommunities\tmodularity\n")
            f.write("%s\t%d\t%d\t%.4f\t%.4f\t%.3f\t%d\t%.3f\n" % (
                time.strftime("%Y-%m-%d %H:%M"), G.number_of_nodes(), G.number_of_edges(),
                cc.number_of_nodes() / max(G.number_of_nodes(), 1), C, tot / max(cnt, 1), n_comms, q))
    except Exception as e:      # the health row is a side record: never let it cost the communities file
        print("build_communities: graph health row skipped (%s)" % e)


def main():
    quiet = "--quiet" in sys.argv[1:]
    try:
        import networkx as nx
    except ImportError:
        print("build_communities: networkx missing; TOPICS.md keeps the keyword buckets")
        return
    G = nx.Graph()
    with io.open(EDGES, encoding="utf-8", errors="replace") as f:
        next(f, None)
        for ln in f:
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 3 and p[0] != p[1]:
                try:
                    G.add_edge(p[0], p[1], weight=float(p[2]))
                except ValueError:
                    pass
    comms = sorted(nx.community.louvain_communities(G, weight="weight", resolution=1.0, seed=1), key=len, reverse=True)
    df = collections.Counter(w for n in G for w in words(n))
    total = G.number_of_nodes()
    rows, named = [], 0
    for i, c in enumerate(comms):
        if len(c) < MIN_SIZE:
            label = "small communities (under %d memories each)" % MIN_SIZE
        else:
            cf = collections.Counter(w for n in c for w in words(n))
            lift = {w: k / (len(c) * df[w] / total) for w, k in cf.items() if k >= 5 and df[w] >= MIN_DF}
            top = sorted(lift, key=lambda w: (-lift[w] * math.log(cf[w]), w))[:4]
            hub = max(c, key=lambda n: (G.degree(n, weight="weight"), n))
            label = "%s (around %s)" % (", ".join(top) or "mixed", hub)
            named += 1
        rows.extend((n, i, label) for n in sorted(c))
    tmp = OUT + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write("slug\tcommunity\tlabel\n")
        for n, i, label in rows:
            f.write("%s\t%d\t%s\n" % (n, i, label))
    os.replace(tmp, OUT)
    q = nx.community.modularity(G, comms, weight="weight")
    health(G, len(comms), q)
    if not quiet:
        print("build_communities: %d nodes in %d communities (%d named, modularity %.3f) -> %s" % (
            total, len(comms), named, q, OUT))
        for c in comms[:8]:
            n0 = sorted(c)[0]
            print("  %5d  %s" % (len(c), next(l for s, _, l in rows if s == n0)[:120]))


if __name__ == "__main__":
    main()
