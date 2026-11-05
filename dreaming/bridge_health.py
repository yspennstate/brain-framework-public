#!/usr/bin/env python3
"""bridge_health.py - find the load-bearing BRIDGE memories and check they're trustworthy.

A memory's betweenness centrality = the share of shortest paths that run through it. High-betweenness
memories are the bridges the association walk crosses to get between knowledge clusters; recall paths
through them are only as good as they are. So a high-betweenness memory that is SUPERSEDED, flagged
stale, or a low-value status ping is a structural risk: many "this reminds me..." paths route through a
weak node. This computes approximate betweenness (Brandes from sampled sources - exact is O(VE), the
sample is fast and the ranking is stable at the top) over the live association graph and flags any top
bridge that is superseded / carries a stale marker / matches the known status-noise pattern.

Read-only. Usage: python bridge_health.py [--samples 250] [--top 25]
"""
import io, os, re, sys, random
from collections import defaultdict, deque
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
EDGES = os.path.join(DR, "spread_edges.tsv")
SUPERSEDED = os.path.join(DR, "superseded.tsv")
NOISE = re.compile(r"(build_watchdog|inspector_w\d+.*(quiet|clean)|_quiet_20)", re.I)


def _arg(flag, d, cast=int):
    a = sys.argv[1:]
    for i, x in enumerate(a):
        if x == flag and i + 1 < len(a):
            try:
                return cast(a[i + 1])
            except Exception:
                return d
    return d


def load_graph():
    adj = defaultdict(list)
    try:
        with io.open(EDGES, encoding="utf-8", errors="replace") as f:
            next(f, None)
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) >= 3 and p[0] != "a":
                    adj[p[0]].append(p[1])
                    adj[p[1]].append(p[0])
    except Exception:
        pass
    return adj


def approx_betweenness(adj, samples, seed=13):
    """Brandes' accumulation from a random sample of sources (unweighted, BFS)."""
    nodes = list(adj)
    rng = random.Random(seed)
    srcs = nodes if len(nodes) <= samples else rng.sample(nodes, samples)
    bc = defaultdict(float)
    for s in srcs:
        S = []
        P = defaultdict(list)
        sigma = defaultdict(float)
        sigma[s] = 1.0
        dist = {s: 0}
        Q = deque([s])
        while Q:
            v = Q.popleft()
            S.append(v)
            for w in adj[v]:
                if w not in dist:
                    dist[w] = dist[v] + 1
                    Q.append(w)
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    P[w].append(v)
        delta = defaultdict(float)
        while S:
            w = S.pop()
            for v in P[w]:
                if sigma[w] > 0:
                    delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w])
            if w != s:
                bc[w] += delta[w]
    return bc


def load_superseded():
    m = set()
    try:
        with io.open(SUPERSEDED, encoding="utf-8", errors="replace") as f:
            next(f, None)
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if p and p[0] != "old":
                    m.add(p[0])
    except Exception:
        pass
    return m


def desc_and_flags(slug):
    d = ""
    stale = False
    try:
        t = io.open(os.path.join(MEM, slug + ".md"), encoding="utf-8", errors="replace").read()
        mm = re.search(r'description:\s*"?(.+?)"?\s*\n', t)
        d = (mm.group(1) if mm else "")[:80]
        # ONLY a genuine supersession marker means stale. NOT the warning emoji - the corpus uses it to
        # mark IMPORTANT memories (owner master rulebooks), not stale ones (false-positive caught 07-10).
        if re.search(r"^\s*superseded_by:\s*\S", t, re.M):
            stale = True
    except Exception:
        pass
    return d, stale


def main():
    samples = _arg("--samples", 250)
    top = _arg("--top", 25)
    adj = load_graph()
    if not adj:
        print("bridge_health: no graph")
        return
    bc = approx_betweenness(adj, samples)
    sup = load_superseded()
    ranked = sorted(bc.items(), key=lambda kv: -kv[1])[:top]
    print(f"bridge_health: betweenness over {len(adj)} nodes (approx, {min(samples, len(adj))} sources). "
          f"Top bridges the recall walk routes through:")
    risk = []       # genuine recall-integrity risk: a superseded/stale memory on real paths
    artifact = []   # status-noise: high betweenness is a within-cluster artifact, inert for recall
    for slug, b in ranked:
        d, stale = desc_and_flags(slug)
        marks = []
        if slug in sup or stale:
            marks.append("SUPERSEDED - stale bridge")
            risk.append(slug)
        elif NOISE.search(slug.lower()):
            marks.append("status-noise (inert for recall per eval_denoise; within-cluster artifact)")
            artifact.append(slug)
        tag = ("  <- " + ", ".join(marks)) if marks else ""
        print(f"  {b:8.0f}  {slug[:52]}{tag}")
        if not tag:
            print(f"            {d}")
    print()
    if risk:
        print(f"RISK: {len(risk)} load-bearing bridge(s) are SUPERSEDED/stale - recall paths route through "
              f"outdated content. The dream/owner should refresh or re-link: {', '.join(risk[:5])}")
    else:
        print("Bridge integrity GOOD: no superseded/stale memory sits on the top recall paths. "
              + (f"({len(artifact)} status-noise nodes rank high by raw betweenness but are inert for "
                 "recall - a within-cluster artifact, not a risk.)" if artifact else ""))


if __name__ == "__main__":
    main()
