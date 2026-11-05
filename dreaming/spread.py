#!/usr/bin/env python3
"""spread.py - recall by spreading activation (Collins & Loftus 1975), not keyword search.

Keyword/grep recall misses the memory two links away that shares no surface terms. This seeds
activation on the memories that match a cue, then propagates it along the brain's association graph -
the hand-written [[wiki-links]] PLUS the learned Hebbian co-activation weights (coactivation.tsv) -
for a few hops with distance decay, and returns the most-activated memories. That surfaces the
relevant scar or method that lives one hop past the cue's vocabulary, which grep and 1-hop similarity
both miss. It's the retrieval algorithm the feeling-of-knowing probe and the instinct layer gesture at.

Usage:  python spread.py "cue words or a memory slug"   ->  ranked associated memories
Pure read-only. Fail-safe: prints nothing useful rather than erroring.

NOTE (2026-07-08): this is the ON-DEMAND hand tool. The LIVE hook path is spread_recall.py
(UserPromptSubmit/PostToolUse), which walks build_spread_edges.py's merged graph and does not
import this file - an edit here changes nothing a session experiences automatically.
"""
import sys, os, io, re
from collections import defaultdict
from supersession import active_supersessions
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # descs carry em-dashes/greek; don't crash on cp1252
except Exception:
    pass

HOME = os.path.expanduser("~")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
COACT = os.path.join(HOME, ".claude", "dreaming", "coactivation.tsv")
WIKI = re.compile(r"\[\[([^\]\|#]+)\]\]")
FM_DESC = re.compile(r"^description:\s*(.+)$", re.M)
HOPS = 3
DECAY = 0.5
STOP = set("the a an and or of to in for on with is are was were be this that it as at by from into over per not no new one two use used using via etc when then than but our we you your".split())


def _norm(s):
    return re.sub(r"[_-]20\d\d[_-]\d\d(?:[_-]\d\d)?$", "", s.strip().lower().rstrip(".md")).replace("-", "_")


def build_graph():
    """adjacency {slug: {slug: weight}} from wiki-links (undirected) + Hebbian co-activation."""
    adj = defaultdict(lambda: defaultdict(float))
    descs, canon = {}, {}
    files = [f for f in os.listdir(MEM) if f.endswith(".md")]
    for fn in files:
        canon[_norm(fn[:-3])] = fn[:-3]
    for fn in files:
        slug = fn[:-3]
        try:
            body = io.open(os.path.join(MEM, fn), encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        dm = FM_DESC.search(body)
        descs[slug] = (dm.group(1) if dm else "")[:160]
        for l in WIKI.findall(body):
            tgt = canon.get(_norm(l))
            if tgt and tgt != slug:
                adj[slug][tgt] += 1.0
                adj[tgt][slug] += 1.0
    # substrate pages leak into coactivation.tsv as false hubs (MISTAKES at w=1.685, caught by the
    # 07-09 graph audit) - the logger now skips them, but old edges linger until decay clears them
    substrate = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS",
                 "AUTOBIOGRAPHY", "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS",
                 "CONSCIENCE", "MISTAKES", "CURIOSITY"}
    try:
        with io.open(COACT, encoding="utf-8") as f:
            next(f, None)
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) >= 3 and p[0] != "a":
                    try:
                        a, b, w = p[0], p[1], float(p[2])
                    except Exception:
                        continue
                    if a in substrate or b in substrate:
                        continue
                    adj[a][b] += min(w, 6.0) * 0.5   # learned edges weigh alongside explicit links
                    adj[b][a] += min(w, 6.0) * 0.5
    except Exception:
        pass
    return adj, descs


def _collapse_graph(adj, superseded):
    """Route historical graph nodes to current endpoints without duplicate edge inflation."""
    collapsed = defaultdict(lambda: defaultdict(float))
    for source, neighbors in adj.items():
        current_source = superseded.get(source, source)
        for target, weight in neighbors.items():
            current_target = superseded.get(target, target)
            if current_target == current_source:
                continue
            collapsed[current_source][current_target] = max(
                collapsed[current_source][current_target], weight)
    return collapsed


def spread(cue, adj, descs, k=10, superseded=None):
    superseded = (active_supersessions(MEM) if superseded is None else superseded)
    adj = _collapse_graph(adj, superseded)
    words = set(w for w in re.findall(r"[a-z][a-z_]{2,}", cue.lower()) if w not in STOP)
    act = defaultdict(float)
    # seed: exact slug, or memories whose slug/desc match the cue words
    for slug in descs:
        current = superseded.get(slug, slug)
        if _norm(slug) == _norm(cue):
            act[current] = max(act[current], 2.0)
        else:
            hay = (slug.lower() + " " + descs.get(slug, "").lower())
            ov = sum(1 for w in words if w in hay)
            if ov >= 2:
                act[current] = max(act[current], 1.0)
    if not act:
        return []
    seeds = set(act)
    frontier = dict(act)
    for _ in range(HOPS):
        nxt = defaultdict(float)
        for node, a in frontier.items():
            nbrs = adj.get(node, {})
            if not nbrs:
                continue
            tot = sum(nbrs.values()) or 1.0
            for nb, w in nbrs.items():
                nxt[nb] += a * (w / tot) * DECAY
        for nb, a in nxt.items():
            act[nb] += a
        frontier = nxt
    ranked = sorted(((a, s) for s, a in act.items() if s not in seeds), reverse=True)
    return ranked[:k]


def main():
    cue = " ".join(sys.argv[1:]).strip()
    if not cue:
        print("usage: python spread.py <cue words or memory slug>")
        return
    adj, descs = build_graph()
    hits = spread(cue, adj, descs)
    if not hits:
        print(f"(nothing activated from '{cue}')")
        return
    print(f"spreading activation from '{cue}' (association, not keyword):")
    for a, slug in hits:
        print(f"  {a:5.2f}  {slug} - {descs.get(slug,'')[:90]}")


if __name__ == "__main__":
    main()
