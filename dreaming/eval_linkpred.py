#!/usr/bin/env python3
"""eval_linkpred.py - do graph-STRUCTURE link predictors beat raw co-read count at predicting
what the brain reaches for next?

eval_recall_signals showed the value axis is RANKING future co-reads (not topology - eval_pmi_edges).
Its baseline signal for a pair is the DIRECT edge weight (past co-read count). But predicting which
pairs get co-read next is exactly the link-prediction problem, and classical graph theory says the
direct edge is often beaten by STRUCTURAL scores that look at shared context:
  - Common Neighbors  CN(a,b) = |G(a) & G(b)|                      (Newman 2001)
  - Adamic-Adar       AA(a,b) = sum_{z in G(a)&G(b)} 1/log(1+deg z) (rare shared context counts more)
  - Jaccard           |G(a)&G(b)| / |G(a)|G(b)|                     (overlap, size-normalized)
  - Katz2 (2-paths)   sum_z w(a,z)*w(z,b)                           (weighted 2-hop, bounded Katz proxy)
  - blends raw + lambda*AA

Causal, recommender-style (identical protocol to eval_recall_signals so verdicts compare): split the
trace at 60/70/80th pct. Build the association graph (wiki as-of-now + co-read PRE-split) and score
each candidate pair. Positives = pairs co-read AFTER T with both members active before T; negatives =
random active non-future pairs. AUROC per predictor, per split. A predictor that beats raw-count
CLEANLY across all splits is a candidate to blend into the pair ranking / walk seeding.

Neighbors for the structural scores come from the SAME pre-split graph on both arms, so no post-split
leak. Read-only, deterministic (fixed RNG). Usage: python eval_linkpred.py

VERDICT 2026-07-10 (measured, 613-event trace, 3 splits). Two baselines, because the honest test is not
"beat raw_count" but "beat the LIVE WALK" (spread_recall._spread is itself a 2-hop degree-normalized
structural score):
  - vs raw_count (direct edge): EVERY structural predictor wins ~+0.10 AUROC cleanly (0.79 vs 0.69). This
    just confirms 2-hop structure >> the direct edge - which the walk already exploits.
  - vs walk2hop (the decisive one): the walk captures the BULK (walk2hop AUROC ~0.79 ~= the structural
    scores). BUT Adamic-Adar still beats the walk on all 3 splits with no flip: dWalk +0.004/+0.010/+0.031;
    common_nbr +0.001/+0.008/+0.023; raw+0.5AA +0.005/+0.011/+0.014. Jaccard and Katz2 SIGN-FLIP (reject).
The mechanism: the walk normalizes an intermediate's outflow by its total edge WEIGHT (linear: /deg); AA
weights each shared neighbour by 1/log(#neighbours) - a gentler, log down-weight of promiscuous bridges,
which edges ahead exactly on the hub-heavy pairs. So the clean-but-SMALL (~+0.01) win is real. NOT WIRED
yet: the margin is small and the recent split thin (n=28), and paying online-hook latency for +0.01 fails
"enforce only where the consequence earns it." Next step filed: test /log(deg) intermediate normalization
in the WALK's REACH (eval_walk_reach), and re-run as the nightly trace grows - ship when the margin holds
on a bigger trace. This eval is the maturing instrument. See memory brain_graph_linkpred_2026_07_10.
"""
import io, itertools, math, os, random, re, sys
from collections import defaultdict

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")

LINK_RE = re.compile(r"\[\[([^\]]+?)\]\]")
DATE_TAIL = re.compile(r"_\d{4}_\d{2}_\d{2}$")
SKIP = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS", "AUTOBIOGRAPHY",
        "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS", "MISTAKES", "CONSCIENCE", "CURIOSITY"}
W_COACT, W_WIKI = 1.6, 1.0
MAXSESS = 25


def norm(slug):
    s = slug.strip()
    if s.endswith(".md"):
        s = s[:-3]
    s = s.replace("-", "_").lower()
    return DATE_TAIL.sub("", s)


def load_events():
    ev = []
    with io.open(os.path.join(DR, "retrievals.tsv"), encoding="utf-8", errors="replace") as f:
        for i, ln in enumerate(f):
            if i == 0:
                continue
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 4:
                try:
                    ev.append((float(p[1]), p[0], p[3]))
                except Exception:
                    pass
    ev.sort()
    return ev


def wiki_edges(real):
    edges = defaultdict(float)
    for fn in os.listdir(MEM):
        if not fn.endswith(".md") or fn[:-3] in SKIP:
            continue
        src = norm(fn)
        if src not in real:
            continue
        try:
            text = io.open(os.path.join(MEM, fn), encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for m in LINK_RE.findall(text):
            b = norm(m)
            if b in real and b != src:
                edges[tuple(sorted((src, b)))] += W_WIKI
    return edges


def coread_counts(pre):
    by = defaultdict(set)
    for ep, slug, sess in pre:
        by[sess].add(slug)
    cnt = defaultdict(int)
    for slugs in by.values():
        if 2 <= len(slugs) <= MAXSESS:
            for a, b in itertools.combinations(sorted(slugs), 2):
                cnt[(a, b)] += 1
    return cnt


def build_adj(wiki, cnt):
    w = defaultdict(float)
    for k, v in wiki.items():
        w[k] += v
    for k, v in cnt.items():
        w[k] += W_COACT * v
    nbr = defaultdict(dict)
    for (a, b), wt in w.items():
        nbr[a][b] = wt
        nbr[b][a] = wt
    return nbr


def auroc(pos, neg, rng):
    if not pos or not neg:
        return None
    pp = pos if len(pos) <= 400 else rng.sample(pos, 400)
    nn = neg if len(neg) <= 400 else rng.sample(neg, 400)
    return sum((1.0 if a > b else 0.5 if a == b else 0.0) for a in pp for b in nn) / (len(pp) * len(nn))


def scorers(nbr):
    deg = {z: len(nbr[z]) for z in nbr}
    # the LIVE walk as a pair scorer - the decisive baseline. spread_recall._spread is already a
    # 2-hop, doubly-degree-normalized structural score, so "beats raw_count" is not enough: a
    # shippable structural predictor must beat THE WALK. Build adj/deg once and reuse.
    import spread_recall as sr
    wadj = defaultdict(list)
    wdeg = defaultdict(float)
    for a in nbr:
        for b, w in nbr[a].items():
            wadj[a].append((b, w))
        wdeg[a] = sum(nbr[a].values())

    _wcache = {}

    def walk(a, b):
        act = _wcache.get(a)
        if act is None:
            act, _ = sr._spread({a: 2.2}, wadj, wdeg)
            _wcache[a] = act
        return act.get(b, 0.0)

    def raw(a, b):
        return nbr.get(a, {}).get(b, 0.0)

    def common(a, b):
        return float(len(set(nbr.get(a, {})) & set(nbr.get(b, {}))))

    def adamic(a, b):
        s = set(nbr.get(a, {})) & set(nbr.get(b, {}))
        return sum(1.0 / math.log(1.0 + deg.get(z, 1)) for z in s if deg.get(z, 1) > 1)

    def jaccard(a, b):
        A, B = set(nbr.get(a, {})), set(nbr.get(b, {}))
        u = len(A | B)
        return len(A & B) / u if u else 0.0

    def katz2(a, b):
        s = set(nbr.get(a, {})) & set(nbr.get(b, {}))
        return sum(nbr[a][z] * nbr[z][b] for z in s)

    def raw_aa(a, b):
        return raw(a, b) + 0.5 * adamic(a, b)

    return {"raw_count": raw, "walk2hop": walk, "common_nbr": common, "adamic_adar": adamic,
            "jaccard": jaccard, "katz2paths": katz2, "raw+0.5AA": raw_aa}


def main():
    real = {norm(fn) for fn in os.listdir(MEM) if fn.endswith(".md") and fn[:-3] not in SKIP}
    events = [(ep, norm(s), sess) for ep, s, sess in load_events() if norm(s) in real]
    if len(events) < 40:
        print(f"eval_linkpred: trace too thin ({len(events)} events).")
        return
    wiki = wiki_edges(real)
    names = ["raw_count", "walk2hop", "common_nbr", "adamic_adar", "jaccard", "katz2paths", "raw+0.5AA"]
    print(f"eval_linkpred: {len(events)} in-corpus events, {len(wiki)} wiki edges. "
          f"AUROC of each predictor -> future co-read (baselines = raw_count AND walk2hop=the live walk).")
    deltas = defaultdict(list)       # vs raw_count
    deltas_w = defaultdict(list)     # vs walk2hop (the decisive comparison)
    for split in (0.60, 0.70, 0.80):
        T = events[int(split * len(events))][0]
        pre = [e for e in events if e[0] < T]
        post = [e for e in events if e[0] >= T]
        cnt = coread_counts(pre)
        nbr = build_adj(wiki, cnt)
        active = sorted({s for _, s, _ in pre})
        # future co-read pairs (both members active before T) = positives
        fut = coread_counts(post)
        aset = set(active)
        pos_pairs = [p for p in fut if p[0] in aset and p[1] in aset]
        if len(pos_pairs) < 10:
            print(f"split {split:.2f}: only {len(pos_pairs)} future pairs active pre-split - skip")
            continue
        rng = random.Random(11)
        fkey = set(fut)
        neg_pairs, tries = [], 0
        while len(neg_pairs) < len(pos_pairs) and tries < 60000:
            a, b = sorted(rng.sample(active, 2))
            tries += 1
            if (a, b) not in fkey:
                neg_pairs.append((a, b))
        sc = scorers(nbr)
        aucs = {}
        for nm in names:
            f = sc[nm]
            aucs[nm] = auroc([f(*p) for p in pos_pairs], [f(*p) for p in neg_pairs], rng)
        base, basew = aucs["raw_count"], aucs["walk2hop"]
        for nm in names:
            if nm not in ("raw_count",) and aucs[nm] is not None and base is not None:
                deltas[nm].append(aucs[nm] - base)
            if nm not in ("raw_count", "walk2hop") and aucs[nm] is not None and basew is not None:
                deltas_w[nm].append(aucs[nm] - basew)
        print(f"split {split:.2f}  ({len(pos_pairs)} pos / {len(neg_pairs)} neg):")
        for nm in names:
            ap = aucs[nm]
            if ap is None:
                print(f"    {nm:12s} n/a"); continue
            dv = "" if nm == "raw_count" else f"  dRaw {ap - base:+.3f}"
            dw = "" if nm in ("raw_count", "walk2hop") else f"  dWalk {ap - basew:+.3f}"
            print(f"    {nm:12s} AUROC {ap:.3f}{dv}{dw}")
    print("\nverdict vs the LIVE WALK (the decisive test - clean win = beats walk2hop all splits, no flip):")
    for nm in names[2:]:
        ds = deltas_w[nm]
        clean = len(ds) == 3 and all(d > 0 for d in ds)
        flip = any(d > 0 for d in ds) and any(d < 0 for d in ds)
        print(f"  {nm:12s} dWalk per split = {['%+.3f' % d for d in ds]}  -> "
              f"{'CLEAN WIN over the walk - worth wiring' if clean else ('SIGN FLIP - reject' if flip else 'no clean win - walk already captures it')}")
    print("  (vs raw_count all structural predictors already win by ~+0.10 - but the walk is itself structural)")
    print("  NOTE: young trace; treat any win as PROVISIONAL and re-run as the nightly trace grows.")


if __name__ == "__main__":
    main()
