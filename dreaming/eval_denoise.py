#!/usr/bin/env python3
"""eval_denoise.py - does excluding the status-memo NOISE cluster improve recall for real memories?

26% of the corpus is near-identical transient status memos (build_watchdog_WNN_quiet, inspector_WNN:
"W#N Cycle M CLEAN quiet, nothing happened"), and one is the graph's #1 hub (degree 109). type:transient
hides them from the DREAM but recall does NOT respect it - spread_recall surfaces them as associations.
This measures the decisive question before any fix: if the recall graph excludes that cluster, does
spreading activation reach MORE of what real sessions actually read next (less hub-flooding), or are the
status hubs inert? eval_walk_reach protocol (causal splits, co-read pre-split, wiki as-of-now).

A = full graph.  B = graph minus status-noise nodes (they can neither be a target nor a bridge).
Ship a recall-side transient filter only if B reaches real targets at least as well as A (no regression)
AND ideally better. Read-only. Usage: python eval_denoise.py

VERDICT 2026-07-10 (measured, 613-event trace, 3 splits): the status hubs are INERT for recall. Dropping
the whole 183-memo noise cluster from the graph moved real-target recall by EXACTLY 0.000 on hit@10/@25/MRR
across all 3 splits. Why: the walk is seeded by the prompt's lexical hits (real memories), and the status
cluster's high degree is INTERNAL cross-linking (the near-identical memos link each other), not bridges to
real memories - so a real seed never traverses into it. So the 26%-of-corpus status bloat is a disk/index/
sidecar-space cost, NOT a recall-quality problem; the graph #1 hub being a worthless "W#113 quiet" memo is
cosmetically alarming but behaviourally harmless. DO NOT mass-archive or graph-exclude for RECALL's sake -
it buys nothing. The only real actions are optional: (1) extend tag_transient's pattern to build_watchdog/
inspector-quiet so the DREAM stops seeing 130 near-identical memos as consolidation fodder (dream-side,
non-destructive type tag, unmeasured benefit); (2) archival for tidiness (owner call). Filed so nobody
spends effort de-noising recall on the false belief it helps. See brain_graph_linkpred_2026_07_10.
"""
import io, itertools, os, re, sys
from collections import defaultdict

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
sys.path.insert(0, DR)
import spread_recall as sr

LINK_RE = re.compile(r"\[\[([^\]]+?)\]\]")
DATE_TAIL = re.compile(r"_\d{4}_\d{2}_\d{2}$")
SKIP = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS", "AUTOBIOGRAPHY",
        "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS", "MISTAKES", "CONSCIENCE", "CURIOSITY"}
# the status-noise pattern: quiet watchdog/inspector cycle memos (transient operational pings)
NOISE = re.compile(r"(build_watchdog_w\d+.*quiet|inspector_w\d+.*(quiet|clean)|_quiet_20|watchdog.*_cycle\d+)", re.I)
W_COACT, W_WIKI, MAXSESS = 1.6, 1.0, 25


def norm(s):
    s = s.strip()
    if s.endswith(".md"):
        s = s[:-3]
    return DATE_TAIL.sub("", s.replace("-", "_").lower())


def is_noise(canon):
    return bool(NOISE.search(canon))


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


def wiki_edges(real, drop):
    e = defaultdict(float)
    for fn in os.listdir(MEM):
        if not fn.endswith(".md") or fn[:-3] in SKIP:
            continue
        src = norm(fn)
        if src not in real or src in drop:
            continue
        try:
            t = io.open(os.path.join(MEM, fn), encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for m in LINK_RE.findall(t):
            b = norm(m)
            if b in real and b != src and b not in drop:
                e[tuple(sorted((src, b)))] += W_WIKI
    return e


def coread(pre, drop):
    by = defaultdict(set)
    for ep, s, se in pre:
        if s not in drop:
            by[se].add(s)
    c = defaultdict(int)
    for ss in by.values():
        if 2 <= len(ss) <= MAXSESS:
            for a, b in itertools.combinations(sorted(ss), 2):
                c[(a, b)] += 1
    return {k: W_COACT * v for k, v in c.items()}


def to_adj(dicts):
    tot = defaultdict(float)
    for d in dicts:
        for k, w in d.items():
            tot[k] += w
    adj = defaultdict(list)
    deg = defaultdict(float)
    for (a, b), w in tot.items():
        adj[a].append((b, w))
        adj[b].append((a, w))
        deg[a] += w
        deg[b] += w
    return adj, deg


def evaluate(adj, deg, sessions):
    h10 = h25 = mrr = n = 0
    for seed, targets in sessions:
        act, _ = sr._spread({seed: 2.2}, adj, deg)
        ranked = [s for s, _ in sorted(act.items(), key=lambda kv: -kv[1]) if s != seed]
        pos = {s: i for i, s in enumerate(ranked)}
        for t in targets:
            n += 1
            i = pos.get(t)
            if i is None:
                continue
            if i < 10:
                h10 += 1
            if i < 25:
                h25 += 1
            mrr += 1.0 / (i + 1)
    if n == 0:
        return (0.0, 0.0, 0.0, 0)
    return (h10 / n, h25 / n, mrr / n, n)


def main():
    real = {norm(fn) for fn in os.listdir(MEM) if fn.endswith(".md") and fn[:-3] not in SKIP}
    noise = {s for s in real if is_noise(s)}
    print(f"eval_denoise: {len(real)} memories, {len(noise)} match the status-noise pattern "
          f"({100.0 * len(noise) / len(real):.1f}%).")
    events = [(ep, norm(s), se) for ep, s, se in load_events() if norm(s) in real]
    # how many REAL targets are themselves noise (would we lose real reach by dropping them)?
    for split in (0.60, 0.70, 0.80):
        T = events[int(split * len(events))][0]
        pre = [e for e in events if e[0] < T]
        post = [e for e in events if e[0] >= T]
        pby = defaultdict(list)
        for ep, s, se in post:
            if s not in pby[se]:
                pby[se].append(s)
        # real (non-noise) targets only - excluding a noise node cannot hurt a non-noise target unfairly
        sessions = [(v[0], [t for t in v[1:] if not is_noise(t)])
                    for v in pby.values() if 2 <= len(v) <= MAXSESS]
        sessions = [(s, ts) for s, ts in sessions if ts and not is_noise(s)]
        if not sessions:
            continue
        wikiA = wiki_edges(real, set())
        coA = coread(pre, set())
        adjA, degA = to_adj([wikiA, coA])
        wikiB = wiki_edges(real, noise)
        coB = coread(pre, noise)
        adjB, degB = to_adj([wikiB, coB])
        h10a, h25a, mra, nt = evaluate(adjA, degA, sessions)
        h10b, h25b, mrb, _ = evaluate(adjB, degB, sessions)
        print(f"  split {split:.2f} ({len(sessions)} sess, {nt} real targets):  "
              f"A hit@10 {h10a:.3f} @25 {h25a:.3f} MRR {mra:.3f}  |  "
              f"B(denoised) d10 {h10b - h10a:+.3f} d25 {h25b - h25a:+.3f} dMRR {mrb - mra:+.3f}")
    print("  read: B>=A means the status cluster was crowding real recall (de-noising helps); "
          "B~=A means the hubs are inert (leave them). Real targets only, so no unfair credit for dropping noise.")


if __name__ == "__main__":
    main()
