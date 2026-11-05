#!/usr/bin/env python3
"""build_anchors.py - OFFLINE builder: turn the association graph into a reflex firing model.

The runtime firing hook must be fast (it's on the every-tool-call path), so we do the expensive
graph work HERE, once, and bake the result into graph_fire/reflex_anchors.json. For each reflex we:

  1. find its ANCHOR nodes: its `source` slug if that slug is a real node in spread_edges.tsv, PLUS
     the memory nodes whose slug-vocabulary overlaps the reflex's own trigger/gut/sting text. The second
     path is what rescues the 18/51 reflexes whose source is the un-embedded ledger MISTAKES/PREDICTIONS
     (measured: those would NEVER fire by a source-node-only design).
  2. spread activation from the anchors over the graph (2 hops, DECAY per hop) - the same diffusion
     spread_recall.py does at recall time, but precomputed.
  3. project the diffused node-activation onto VOCABULARY: each activated node donates its activation to
     its slug tokens. The result is a reflex -> {token: weight} vector: a graph-DERIVED bag-of-words model.

At runtime (gfcore.py) firing a reflex is then a cheap dot product of the live action's tokens against
this vector - and it generalises past the reflex's literal regex (a token that only appears in a
graph-near memory still contributes), which is the point: 'heredoc' can wake the 'CRLF' scar.

Reuses spread_recall's graph knobs (DECAY=0.5, HOPS=2) so the diffusion matches the conscious side.
Stdlib only, forward-slash paths. Run: python graph_fire/build_anchors.py
"""
import os, io, json, re, math, collections

HERE = os.path.dirname(os.path.abspath(__file__))
DREAM = os.path.dirname(HERE)
EDGES = os.path.join(DREAM, "spread_edges.tsv")
TSV = os.path.join(DREAM, "instincts.tsv")
OUT = os.path.join(HERE, "reflex_anchors.json")

DECAY = 0.5      # per-hop activation decay (matches spread_recall.py:47)
HOPS = 2         # spread radius (matches spread_recall.py HOPS)
TEXT_ANCHOR_K = 6   # up to K text-matched anchor nodes per reflex
MIN_TOK_OVERLAP = 2 # a node needs >= this many shared salient tokens to be a text anchor

# date fragments, months, and generic glue that carry no domain signal
_STOP = set("""the a an and or of to in on for by is are was were be been being it its this that these those
with without into onto from as at we you he she they them his her their our your my me not no yes do did done
if then else than so but scar gist method feedback project reference dream daydream 2026 2025 jan feb mar apr may
jun jul aug sep oct nov dec 01 02 03 04 05 06 07 08 09 10 11 12 20 21 22 23 24 25 26 27 28 29 30 31
your you'll thing things one two three new old now day days
before after check make sure look take run use get keep need want first still just also see saw open
said say tell point place change move find what which when where why how who into out off down over under
about around here there this that will would could should can may might must more most less very really""".split())

_WORD = re.compile(r"[a-zA-Z][a-zA-Z0-9]{2,}")


def toks(s):
    out = []
    for w in _WORD.findall(s or ""):
        w = w.lower()
        if w in _STOP or w.isdigit():
            continue
        out.append(w)
    return out


def reflex_text_tokens(rec):
    """Salient tokens from a reflex's own words: its regex trigger vocabulary + gut + sting."""
    trig = rec.get("trigger", "")
    # strip regex metacharacters to bare alternation words
    trig_words = re.sub(r"[\\^$.|?*+()\[\]{}]", " ", trig)
    trig_words = re.sub(r"\bb\b|\bi\b|\bs\b|\bw\b|\bd\b", " ", trig_words)  # drop \b \s \w \d leftovers
    bag = toks(trig_words) + toks(rec.get("gut", "")) + toks(rec.get("sting", ""))
    return bag


def load_reflexes():
    out = []
    if not os.path.exists(TSV):
        return out
    rows = [l.rstrip("\n").split("\t") for l in io.open(TSV, encoding="utf-8")]
    for p in rows[1:]:
        if len(p) >= 7 and p[1] in ("fire", "guide", "carry"):
            try:
                charge = float(p[7]) if len(p) >= 8 else 0.6
            except Exception:
                charge = 0.6
            out.append({"id": p[0], "mode": p[1], "validity": p[2], "trigger": p[3],
                        "gut": p[4], "sting": p[5], "source": p[6], "charge": charge})
    return out


def load_graph():
    adj = collections.defaultdict(dict)
    nodes = set()
    if not os.path.exists(EDGES):
        return adj, nodes
    for l in io.open(EDGES, encoding="utf-8"):
        q = l.rstrip("\n").split("\t")
        if len(q) < 3:
            continue
        a, b = q[0], q[1]
        try:
            w = float(q[2])
        except Exception:
            w = 1.0
        nodes.add(a); nodes.add(b)
        # undirected reachability, keep max weight
        adj[a][b] = max(adj[a].get(b, 0.0), w)
        adj[b][a] = max(adj[b].get(a, 0.0), w)
    return adj, nodes


def spread(anchors, adj):
    """2-hop weighted BFS from anchor nodes -> {node: activation}. Anchors start at their given weight."""
    act = collections.defaultdict(float)
    frontier = dict(anchors)  # node -> activation
    for n, a in frontier.items():
        act[n] += a
    cur = frontier
    for hop in range(HOPS):
        nxt = collections.defaultdict(float)
        for n, a in cur.items():
            nbrs = adj.get(n, {})
            if not nbrs:
                continue
            norm = sum(nbrs.values()) or 1.0
            for m, w in nbrs.items():
                flow = a * DECAY * (w / norm)
                if flow > 1e-4:
                    nxt[m] += flow
        for m, a in nxt.items():
            act[m] += a
        cur = nxt
        if not cur:
            break
    return act


def ppr_spread(anchors, adj, alpha=0.15, iters=40):
    """Personalized PageRank: the stationary distribution of a random walk that restarts to the reflex's
    anchors with probability alpha each step. The principled version of the truncated BFS - it accounts
    for the whole graph, not just 2 hops, and column-normalises by out-weight so a high-degree hub doesn't
    dominate. Power iteration to convergence; sparse, ~40 iters over ~1400 nodes is milliseconds."""
    S = sum(anchors.values()) or 1.0
    restart = {n: a / S for n, a in anchors.items()}
    r = dict(restart)
    for _ in range(iters):
        nr = collections.defaultdict(float)
        for n, rn in r.items():
            nbrs = adj.get(n, {})
            tot = sum(nbrs.values()) or 1.0
            if not nbrs:
                # dangling node: return mass to the restart set (keeps it a proper distribution)
                for m, a in restart.items():
                    nr[m] += (1 - alpha) * rn * a
                continue
            for m, w in nbrs.items():
                nr[m] += (1 - alpha) * rn * (w / tot)
        for n, a in restart.items():
            nr[n] += alpha * a
        r = nr
    return r


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    import sys
    method, out_path = "bfs", OUT
    for a in sys.argv[1:]:
        if a.startswith("--method="):
            method = a.split("=", 1)[1]
        if a.startswith("--out="):
            out_path = a.split("=", 1)[1]
    diffuse = ppr_spread if method == "ppr" else spread
    reflexes = load_reflexes()
    adj, nodes = load_graph()
    # precompute node -> salient tokens (for text-anchoring and projection)
    node_toks = {n: set(toks(n)) for n in nodes}
    # invert: token -> nodes that contain it (for fast text-anchor lookup)
    tok_nodes = collections.defaultdict(set)
    for n, ts in node_toks.items():
        for t in ts:
            tok_nodes[t].add(n)

    rawvecs, rawmeta = {}, {}
    blind_rescued = 0
    for rec in reflexes:
        rid = rec["id"]
        src = rec["source"]
        rtoks = reflex_text_tokens(rec)
        rtok_set = set(rtoks)
        anchors = {}
        in_graph = src in nodes
        if in_graph:
            anchors[src] = 1.0
        # text anchors: nodes sharing >= MIN_TOK_OVERLAP salient tokens with the reflex's own words
        cand = collections.Counter()
        for t in rtok_set:
            for n in tok_nodes.get(t, ()):
                cand[n] += 1
        text_anchors = [(n, c) for n, c in cand.most_common() if c >= MIN_TOK_OVERLAP and n != src]
        for n, c in text_anchors[:TEXT_ANCHOR_K]:
            anchors[n] = max(anchors.get(n, 0.0), min(1.0, 0.4 + 0.2 * c))  # more overlap -> stronger anchor
        if not in_graph and anchors:
            blind_rescued += 1
        # diffuse (bfs 2-hop, or personalized PageRank)
        act = diffuse(anchors, adj) if anchors else {}
        # project node activation onto vocabulary
        tokw = collections.defaultdict(float)
        for n, a in act.items():
            nts = node_toks.get(n) or set()
            if not nts:
                continue
            share = a / len(nts)
            for t in nts:
                tokw[t] += share
        # also fold the reflex's OWN trigger tokens in at a floor weight, so a direct regex-vocab hit
        # always scores even if the graph is sparse for this reflex (union safety).
        for t in rtok_set:
            tokw[t] = max(tokw[t], 0.15)
        rawvecs[rid] = dict(tokw)
        rawmeta[rid] = {
            "src": src, "in_graph": in_graph, "mode": rec["mode"], "validity": rec["validity"],
            "charge": rec["charge"], "n_anchor_nodes": len(anchors), "n_act_nodes": len(act),
            "anchors": {n: round(w, 3) for n, w in sorted(anchors.items(), key=lambda kv: -kv[1])[:12]},
        }
    # Precision guard is STOPWORDS (kills 'before'/'check' glue), NOT cross-reflex IDF: IDF would damp the
    # shared domain tokens (grid/sim/book) that many domain reflexes correctly share and that ARE the
    # mistake-context signal. Keep raw graph-diffused weights; normalise per reflex; trim to top-40.
    model = {}
    for rid, tokw in rawvecs.items():
        mx = max(tokw.values()) if tokw else 1.0
        norm = {t: round(w / mx, 4) for t, w in tokw.items() if w / mx >= 0.06}
        top = dict(sorted(norm.items(), key=lambda kv: -kv[1])[:40])
        m = dict(rawmeta[rid])
        m["tokens"] = top
        model[rid] = m
    meta = {
        "n_reflexes": len(reflexes),
        "n_graph_nodes": len(nodes),
        "reflexes_with_graph_anchor": sum(1 for m in model.values() if m["n_act_nodes"] > 0),
        "blind_reflexes_rescued": blind_rescued,
        "decay": DECAY, "hops": HOPS,
    }
    meta["method"] = method
    with io.open(out_path, "w", encoding="utf-8") as f:
        json.dump({"_meta": meta, "reflexes": model}, f, ensure_ascii=False, indent=0)
    print("wrote", out_path, "method=" + method)
    print(json.dumps(meta, indent=2))
    # show the payoff on today's-mistake reflexes that were graph-blind (source=MISTAKES)
    for rid in ("sim_sign_vs_lived_nav", "leveraged_etf_is_not_linear", "vxn_long_tenor_state",
                "he_will_look", "confirm_the_target"):
        m = model.get(rid)
        if m:
            print("  %-28s in_graph=%-5s act_nodes=%-3d top_tokens=%s"
                  % (rid, m["in_graph"], m["n_act_nodes"], list(m["tokens"])[:8]))


if __name__ == "__main__":
    main()
