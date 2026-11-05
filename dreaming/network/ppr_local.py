"""Local personalized PageRank (Andersen-Chung-Lang 2006 forward-push).

Spreading activation over the association graph is a bounded one-hop frontier; it works
(topic-level memory recall is validated) but it is ad-hoc and noisy on sparse topics.
Personalized PageRank is the principled version: the stationary distribution of a random
walk that restarts at the seed with probability alpha gives a convergent, multi-hop
importance that concentrates near the seed and decays smoothly with graph distance.

The ACL forward-push computes an eps-approximate PPR vector touching only O(1/(alpha*eps))
nodes near the seed - it never materializes the full 20k-node / 1.84M-edge graph, so it
scales. It reads edges through the store's cached neighbors(), so repeated nodes are free.

  ppr = local_ppr(store, seed_nodes, alpha=0.15, eps=1e-4)
  top_memories = rank_memories(ppr, top=8)
"""
from collections import defaultdict


def _out_neighbors(store, nid, cache):
    nb = cache.get(nid)
    if nb is None:
        nb = store.neighbors(nid, kind="co", limit=32)
        cache[nid] = nb
    return nb


def local_ppr(store, seed_nodes, alpha=0.15, eps=1e-4, max_push=200000):
    """Approximate personalized PageRank from a seed set via forward-push.

    Returns {node_id: ppr_mass}. alpha is the restart probability (higher = stays closer
    to the seed); eps is the residual tolerance (smaller = more nodes, more accurate).
    Invariant maintained: for every node, residual/degree < eps at termination (the ACL
    guarantee), so the returned p is an eps-approximation of the true PPR vector.
    """
    seed = [s for s in set(seed_nodes)]
    if not seed:
        return {}
    p = defaultdict(float)          # the PPR estimate
    r = defaultdict(float)          # the residual
    w = 1.0 / len(seed)
    for s in seed:
        r[s] = w
    cache = {}
    # degree = sum of out-edge weights (co-graph is weighted); precompute lazily
    deg = {}

    def degree(nid):
        d = deg.get(nid)
        if d is None:
            nb = _out_neighbors(store, nid, cache)
            d = sum(wt for _, wt in nb) or 1.0
            deg[nid] = d
        return d

    # worklist of nodes whose residual may exceed the push threshold
    queue = [s for s in seed if r[s] > eps * degree(s)]
    in_q = set(queue)
    pushes = 0
    while queue and pushes < max_push:
        u = queue.pop()
        in_q.discard(u)
        ru = r[u]
        du = degree(u)
        if ru <= eps * du:
            continue
        # push: settle alpha of the residual into p, spread the rest to neighbors
        p[u] += alpha * ru
        remain = (1.0 - alpha) * ru
        r[u] = 0.0
        for v, wt in _out_neighbors(store, u, cache):
            r[v] += remain * (wt / du)
            if v not in in_q and r[v] > eps * degree(v):
                queue.append(v)
                in_q.add(v)
        pushes += 1
    return dict(p)


def rank_memories(ppr, top=8):
    mems = [(nid, m) for nid, m in ppr.items() if nid.startswith("mem:")]
    mems.sort(key=lambda x: -x[1])
    return mems[:top]


def rank_kind(ppr, kind, top=8):
    pref = kind + ":"
    out = [(nid, m) for nid, m in ppr.items() if nid.startswith(pref)]
    out.sort(key=lambda x: -x[1])
    return out[:top]
