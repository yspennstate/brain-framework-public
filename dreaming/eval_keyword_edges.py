#!/usr/bin/env python3
"""eval_keyword_edges.py - non-regression gate for build_keyword_edges (BM25 attachment of short/feedback
orphans cosine can't anchor). A = current graph (+orphan+series), B = + keyword edges. Existing recall
must be non-regressed (these target old feedback memos not in the recent trace, so expect ~0). Same causal
walk-reach protocol as eval_orphan_edges. Read-only. Usage: python eval_keyword_edges.py [--weight 0.1]"""
import io, itertools, os, re, sys
from collections import defaultdict
HOME = os.path.expanduser("~"); DR = os.path.join(HOME, ".claude", "dreaming")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
sys.path.insert(0, DR)
import spread_recall as sr
import build_keyword_edges as bke
import build_orphan_edges as boe
import build_series_edges as bse
LINK_RE = re.compile(r"\[\[([^\]]+?)\]\]"); DATE = re.compile(r"_\d{4}_\d{2}_\d{2}$")
SKIP = bke.SKIP; W_COACT, W_WIKI, MAXSESS = 1.6, 1.0, 25
def norm(s):
    s = s.strip(); s = s[:-3] if s.endswith(".md") else s
    return DATE.sub("", s.replace("-", "_").lower())
def load_events():
    ev = []
    for i, ln in enumerate(io.open(os.path.join(DR, "retrievals.tsv"), encoding="utf-8", errors="replace")):
        if i == 0: continue
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 4:
            try: ev.append((float(p[1]), norm(p[0]), p[3]))
            except: pass
    ev.sort(); return ev
def wiki(real):
    e = defaultdict(float)
    for fn in os.listdir(MEM):
        if not fn.endswith(".md") or fn[:-3] in SKIP: continue
        src = norm(fn)
        if src not in real: continue
        try: t = io.open(os.path.join(MEM, fn), encoding="utf-8", errors="replace").read()
        except: continue
        for m in LINK_RE.findall(t):
            b = norm(m)
            if b in real and b != src: e[tuple(sorted((src, b)))] += W_WIKI
    return e
def coread(pre):
    by = defaultdict(set)
    for ep, s, se in pre: by[se].add(s)
    c = defaultdict(int)
    for ss in by.values():
        if 2 <= len(ss) <= MAXSESS:
            for a, b in itertools.combinations(sorted(ss), 2): c[(a, b)] += 1
    return {k: W_COACT * v for k, v in c.items()}
def to_adj(ds):
    tot = defaultdict(float)
    for d in ds:
        for k, w in d.items(): tot[k] += w
    adj = defaultdict(list); deg = defaultdict(float)
    for (a, b), w in tot.items():
        adj[a].append((b, w)); adj[b].append((a, w)); deg[a] += w; deg[b] += w
    return adj, deg
def ev_reach(adj, deg, sess):
    h10 = h25 = mrr = n = 0
    for seed, tg in sess:
        act, _ = sr._spread({seed: 2.2}, adj, deg)
        ranked = [s for s, _ in sorted(act.items(), key=lambda kv: -kv[1]) if s != seed]
        pos = {s: i for i, s in enumerate(ranked)}
        for t in tg:
            n += 1; i = pos.get(t)
            if i is None: continue
            if i < 10: h10 += 1
            if i < 25: h25 += 1
            mrr += 1.0 / (i + 1)
    return (h10 / n, h25 / n, mrr / n, n) if n else (0, 0, 0, 0)
def main():
    weight = 0.1
    a = sys.argv[1:]
    for i, x in enumerate(a):
        if x == "--weight" and i + 1 < len(a):
            try: weight = float(a[i + 1])
            except: pass
    real = {norm(fn) for fn in os.listdir(MEM) if fn.endswith(".md") and fn[:-3] not in SKIP}
    events = [e for e in load_events() if e[1] in real]
    W = wiki(real)
    oe = {tuple(sorted((norm(x), norm(y)))): w for x, y, w in boe.orphan_edges(0.40, 0.1)[0]}
    se = {tuple(sorted((norm(x), norm(y)))): w for x, y, w in bse.series_edges(4, 0.15)[0]}
    ke_raw, _, _ = bke.keyword_edges(30.0, weight)
    ke = {tuple(sorted((norm(x), norm(y)))): w for x, y, w in ke_raw}
    print(f"eval_keyword_edges: {len(ke)} keyword edges (weight {weight}).")
    for split in (0.60, 0.70, 0.80):
        T = events[int(split * len(events))][0]
        pre = [e for e in events if e[0] < T]; post = [e for e in events if e[0] >= T]
        co = coread(pre)
        pby = defaultdict(list)
        for ep, s, se_id in post:
            if s not in pby[se_id]: pby[se_id].append(s)
        sess = [(v[0], v[1:]) for v in pby.values() if 2 <= len(v) <= MAXSESS]
        if not sess: continue
        adjA, degA = to_adj([W, co, oe, se])
        adjB, degB = to_adj([W, co, oe, se, ke])
        h10a, h25a, mra, nt = ev_reach(adjA, degA, sess)
        h10b, h25b, mrb, _ = ev_reach(adjB, degB, sess)
        print(f"  split {split:.2f} ({len(sess)} sess, {nt} tgt):  A @10 {h10a:.3f} @25 {h25a:.3f} MRR {mra:.3f} "
              f"| B(+kw) d10 {h10b-h10a:+.3f} d25 {h25b-h25a:+.3f} dMRR {mrb-mra:+.3f}")
    print("  GATE: deltas ~0 to wire. The value is associative reach for the owner's isolated feedback.")
if __name__ == "__main__":
    main()
