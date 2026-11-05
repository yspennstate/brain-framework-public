#!/usr/bin/env python3
"""control_random_graph.py - the negative control eval_fusion_weights' verdict leans on.

Question: is graph-dominant recall's edge over lexical-dominant a REAL held-out-co-read prediction, or a
structural artifact of the graph being BUILT from co-reads? Test: degree-preserving random rewire (double-
edge-swap keeps every node's edge count and the weight multiset, randomizes WHO connects to whom). Re-run
current vs graph-dominant vs lexical-dominant on the REAL graph and the REWIRED graph.
  - If graph-dominance still wins big on the rewired graph -> the win is degree/structure, not the specific
    edges -> reject reweight (and be humble about the live hook's graph-dominance).
  - If the win COLLAPSES on the rewired graph -> the real edges genuinely predict future co-reads -> the
    signal is real (for the proactive/co-read mode), the live hook is validated, reweighting the deliberate
    CLI still needs its own ground truth but the finding is not an artifact.
Light: 3 blends x 2 graphs x 3 splits. Read-only. Usage: python control_random_graph.py [--swaps 10]
"""
import os, random, sys
sys.path.insert(0, os.path.expanduser(r"~/.claude/dreaming"))
import spread_recall as sr
import brain as B
import eval_fusion_weights as E

random.seed(1729)   # fixed - reproducible; Math.random-free (plain interpreter, not a workflow script)


def edge_list(adj):
    seen = {}
    for a, nbrs in adj.items():
        for b, w in nbrs:
            key = (a, b) if a < b else (b, a)
            seen[key] = w
    return [(a, b, w) for (a, b), w in seen.items()]


def rewire(edges, passes):
    """Degree-preserving double-edge-swap. Weights travel with their edge."""
    E_ = [list(e) for e in edges]
    present = set((a, b) if a < b else (b, a) for a, b, w in E_)
    n_swaps = passes * len(E_)
    m = len(E_)
    done = 0
    for _ in range(n_swaps):
        i, j = random.randrange(m), random.randrange(m)
        if i == j:
            continue
        a, b, w1 = E_[i]
        c, d, w2 = E_[j]
        if len({a, b, c, d}) < 4:
            continue
        na, nb = (a, d), (c, b)
        ka = (na[0], na[1]) if na[0] < na[1] else (na[1], na[0])
        kb = (nb[0], nb[1]) if nb[0] < nb[1] else (nb[1], nb[0])
        if ka in present or kb in present:
            continue
        present.discard((a, b) if a < b else (b, a))
        present.discard((c, d) if c < d else (d, c))
        present.add(ka)
        present.add(kb)
        E_[i] = [a, d, w1]
        E_[j] = [c, b, w2]
        done += 1
    return E_, done


def to_adj(edges):
    from collections import defaultdict
    adj = defaultdict(list)
    deg = defaultdict(float)
    for a, b, w in edges:
        adj[a].append((b, w))
        adj[b].append((a, w))
        deg[a] += w
        deg[b] += w
    return adj, deg


def main():
    passes = 10
    a = sys.argv[1:]
    for i, x in enumerate(a):
        if x == "--swaps" and i + 1 < len(a):
            try:
                passes = int(a[i + 1])
            except Exception:
                pass

    # cache embeddings once (avoid per-call .npz reloads)
    _emb = sr._load_embeddings()
    sr._load_embeddings = lambda: _emb

    idx = B._archived_index_lines()
    idx.update(sr._index_lines())
    sal = sr._load_salience()
    tier = B._tiers()
    adj, deg = sr._load_graph()

    edges = edge_list(adj)
    rew, done = rewire(edges, passes)
    radj, rdeg = to_adj(rew)
    print(f"control_random_graph: {len(edges)} edges, {done} successful swaps ({passes} passes) - "
          f"degrees preserved, targets randomized.")

    # sessions per split (reuse eval_fusion_weights loader logic)
    import io
    rows = []
    for i, ln in enumerate(io.open(os.path.join(E.DR, "retrievals.tsv"), encoding="utf-8", errors="replace")):
        if i == 0:
            continue
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 4:
            try:
                rows.append((float(p[1]), p[0], p[3]))
            except Exception:
                pass
    rows.sort()
    ts = [r[0] for r in rows]
    splits = (0.60, 0.70, 0.80)
    sess_by_split = []
    for sp in splits:
        T = ts[int(sp * len(ts))]
        sess_by_split.append(E.sessions_from_trace([r for r in rows if r[0] >= T]))

    BLENDS = {"current 0.45/0.25/0.30": (0.45, 0.25, 0.30),
              "graph-dom 0.0/1.0/0.0": (0.0, 1.0, 0.0),
              "lex-dom 1.0/0.0/0.0": (1.0, 0.0, 0.0)}

    def avg_mrr(A, D):
        out = {}
        for name, b in BLENDS.items():
            per = [E.score_blend(s, idx, A, D, sal, tier, *b)[2] for s in sess_by_split]
            out[name] = sum(per) / len(per)
        return out

    real = avg_mrr(adj, deg)
    rand = avg_mrr(radj, rdeg)
    print("\n                         REAL graph   REWIRED graph   (real-rewired)")
    for name in BLENDS:
        print(f"  {name:24s}  {real[name]:.4f}       {rand[name]:.4f}        {real[name]-rand[name]:+.4f}")

    gd_real = real["graph-dom 0.0/1.0/0.0"] - real["lex-dom 1.0/0.0/0.0"]
    gd_rand = rand["graph-dom 0.0/1.0/0.0"] - rand["lex-dom 1.0/0.0/0.0"]
    print(f"\n  graph-dom advantage over lex-dom:  REAL {gd_real:+.4f}   REWIRED {gd_rand:+.4f}")
    if gd_rand < 0.4 * gd_real:
        print("  -> advantage COLLAPSES on the random graph: the REAL edges predict held-out co-reads. "
              "Signal is real (proactive mode); live hook validated.")
    else:
        print("  -> advantage SURVIVES rewiring: graph-dom is a degree/structure artifact, not edge signal. "
              "Reject reweight; be humble about graph-dominance.")


if __name__ == "__main__":
    main()
