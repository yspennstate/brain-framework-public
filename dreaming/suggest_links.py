#!/usr/bin/env python3
"""suggest_links.py - propose missing [[wiki-links]] the hand-authored backbone is missing, using
the graph-structure signal that beat raw co-read count at link prediction (eval_linkpred, 07-10).

The wiki-link backbone "carries the walk while the Hebbian graph fills in" (build_spread_edges), but
it is hand-authored and therefore incomplete - two memories about the same method or program often
share many neighbours yet were never directly linked. Adamic-Adar (sum over shared neighbours z of
1/log(1+deg z)) is the classical link predictor for exactly this, and it is CAUSALLY VALIDATED here:
on pairs with NO wiki-link pre-split, AA predicts which get CO-READ after the split at AUROC
0.76/0.71/0.81 (3 splits, 2026-07-10) - so a high-AA unlinked pair is a real latent association, not
noise. (It does NOT help the online walk's reach - that was measured and rejected, eval_walk_norm; its
value is offline curation, not runtime ranking. See memory brain_graph_linkpred_2026_07_10.)

This is a READ-ONLY curation aid: it RANKS and REPORTS candidate links for the dream / owner to author
by hand. It never edits a memory or the graph - auto-adding edges would mutate the non-stationary graph
the walk runs on, the churn the brain-method memory warns against. A human/dream decides which to link.

Output: link_suggestions.md (top-N pairs, each with descriptions, the shared-neighbour "bridges" that
explain the association, and the current direct co-read count). Read-only, fail-safe.
Usage:  python suggest_links.py [--top N] [--min-common K] [--quiet]
"""
import heapq, io, math, os, re, sys
from collections import defaultdict
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
COACT = os.path.join(DR, "coactivation.tsv")
OUT = os.path.join(DR, "link_suggestions.md")
LINK_RE = re.compile(r"\[\[([^\]]+?)\]\]")
DATE_TAIL = re.compile(r"_\d{4}_\d{2}_\d{2}$")
SKIP = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS", "AUTOBIOGRAPHY",
        "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS", "MISTAKES", "CONSCIENCE", "CURIOSITY"}
W_COACT, W_WIKI = 1.6, 1.0


def norm(s):
    s = s.strip()
    if s.endswith(".md"):
        s = s[:-3]
    return DATE_TAIL.sub("", s.replace("-", "_").lower())


def _arg(flag, default, cast):
    a = sys.argv[1:]
    for i, x in enumerate(a):
        if x == flag and i + 1 < len(a):
            try:
                return cast(a[i + 1])
            except Exception:
                return default
    return default


def load():
    real, desc = {}, {}
    for fn in os.listdir(MEM):
        if fn.endswith(".md") and fn[:-3] not in SKIP:
            real[norm(fn)] = fn[:-3]
    nbr = defaultdict(set)
    wiki_pairs = set()
    coread = defaultdict(int)
    superseded = set()
    for fn in os.listdir(MEM):
        if not fn.endswith(".md") or fn[:-3] in SKIP:
            continue
        src = norm(fn)
        if src not in real:
            continue
        try:
            body = io.open(os.path.join(MEM, fn), encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        dm = re.search(r'description:\s*"?(.+?)"?\s*\n', body)
        desc[src] = (dm.group(1) if dm else "").strip()[:120]
        if re.search(r'^\s*superseded_by:', body, re.M):
            superseded.add(src)
        for m in LINK_RE.findall(body):
            b = norm(m)
            if b in real and b != src:
                nbr[src].add(b)
                nbr[b].add(src)
                wiki_pairs.add(tuple(sorted((src, b))))
    if os.path.exists(COACT):
        for ln in io.open(COACT, encoding="utf-8", errors="replace"):
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 3 and p[0] != "a":
                a, b = norm(p[0]), norm(p[1])
                if a in real and b in real and a != b:
                    nbr[a].add(b)
                    nbr[b].add(a)
                    try:
                        coread[tuple(sorted((a, b)))] += int(float(p[2]))
                    except Exception:
                        pass
    return real, desc, nbr, wiki_pairs, coread, superseded


def rank(nbr, wiki_pairs, superseded, min_common=3, top=25, hub_deg=1000):
    """Top-N unlinked pairs by Adamic-Adar, exact. Returns (ranked [((a, b), score)], deg, total, exact_total).

    Sums are accumulated row by row (one small dict per node), so nothing holds every two-hop pair at once and
    no pair needs a set intersection. Neighbours with degree above hub_deg are not walked: their share
    (+1 to the count, +1/log(1+deg) to the score) is added by a membership test on each pair the walk found.
    A pair whose ONLY shared neighbours are hubs is then never seen; its score is at most the sum of the hub
    weights, so the top N is exact when the N-th score is above that bound. Otherwise the walk reruns with
    every neighbour walked. (Before 1 Nov the walk kept a set of all ~150M two-hop pairs on a 41k-node graph
    with a 16,770-degree hub and was killed at the dream's 2700 s cap every night.)"""
    names = sorted(nbr)
    idx = {nm: i for i, nm in enumerate(names)}
    n = len(names)
    N = [[idx[x] for x in nbr[nm]] for nm in names]
    deg = [len(x) for x in N]
    w = [1.0 / math.log(1.0 + d) if d > 1 else 0.0 for d in deg]
    sup = [nm in superseded for nm in names]
    wiki = set()
    for p, q in wiki_pairs:
        if p in idx and q in idx:
            i, j = idx[p], idx[q]
            wiki.add((i, j) if i < j else (j, i))
    hubs = [z for z in range(n) if deg[z] > hub_deg]
    hub_set = {h: set(N[h]) for h in hubs}
    hubs_of = defaultdict(list)
    for h in hubs:
        for x in N[h]:
            hubs_of[x].append(h)
    heap, total = [], 0
    for a in range(n):
        if sup[a]:
            continue
        cnt, sc = {}, {}
        for z in N[a]:
            if deg[z] > hub_deg:
                continue
            wz = w[z]
            for b in N[z]:
                if b > a:
                    cnt[b] = cnt.get(b, 0) + 1
                    sc[b] = sc.get(b, 0.0) + wz
        ha = hubs_of.get(a, ())
        for b, c in cnt.items():
            if sup[b] or (a, b) in wiki:
                continue
            s = sc[b]
            for h in ha:
                if b in hub_set[h]:
                    c += 1
                    s += w[h]
            if c < min_common:
                continue
            total += 1
            item = (s, -a, -b)
            if len(heap) < top:
                heapq.heappush(heap, item)
            elif item > heap[0]:
                heapq.heapreplace(heap, item)
    bound = sum(w[h] for h in hubs)
    if hubs and (len(heap) < top or heap[0][0] <= bound + 1e-9):
        return rank(nbr, wiki_pairs, superseded, min_common, top, hub_deg=float("inf"))
    ranked = [((names[-na], names[-nb]), s) for s, na, nb in sorted(heap, reverse=True)]
    degn = {nm: deg[i] for i, nm in enumerate(names)}
    return ranked, degn, total, not hubs


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    top = _arg("--top", 25, int)
    min_common = _arg("--min-common", 3, int)
    quiet = "--quiet" in sys.argv[1:]
    real, desc, nbr, wiki_pairs, coread, superseded = load()
    # main's rank() (b9cc5c0673, 1 Nov): Adamic-Adar summed row by row into a top-N heap, never a set of every
    # two-hop pair. That set grew with the graph: on the Mac this step took 308 s on 30 Oct, 701 s on 1 Nov and was
    # cut at the dream's 1,800 s cap three times on 2 Nov. The cosine rerank below now reads the top `pool` pairs by
    # AA instead of every pair with >= min_common shared neighbours.
    pool = _arg("--pool", 5000, int)
    ranked_aa, deg, total, exact_total = rank(nbr, wiki_pairs, superseded, min_common, top=max(pool, top))
    aa = dict(ranked_aa)

    # Inside this pool (>= min_common shared neighbours) meaning picks the real links better than Adamic-Adar: holding
    # out 10% of spread_edges on 30 Oct, the top 100 by memory-vector cosine held 12 held-out links against 2 by AA
    # (only links not written in either embedded text; d2b_52_embed_linkpred.py). AA stays the fallback and is shown.
    cos = {}
    try:
        sys.path.insert(0, DR)
        import spread_recall as _sr
        emb = _sr._load_embeddings()
        if emb is not None:
            idx_e = emb.index
            vec = {}
            for (a, b) in aa:
                sa, sb = real.get(a, a), real.get(b, b)
                if sa in idx_e and sb in idx_e:
                    if sa not in vec:
                        vec[sa] = emb.normalized_centroid([idx_e[sa]])
                    cos[(a, b)] = float(emb.row_dot(idx_e[sb], vec[sa]))
    except Exception:
        cos = {}
    if cos:
        # session records (compaction anchors), data_ archive-file nodes (near-identical series: img_acc2..acc5) and
        # near-copies (cosine >= 0.98) top a cosine ranking without being links anyone would write. Without them the
        # held-out test above reads 12 of the top 25 for cosine against 0 for AA.
        _rec = getattr(_sr, "SESSION_RECORD", None)
        def _ok(k):
            sa, sb = real.get(k[0], k[0]), real.get(k[1], k[1])
            return (cos.get(k, 1.0) < 0.98 and not sa.startswith("data_") and not sb.startswith("data_")
                    and not (_rec and (_rec.match(sa) or _rec.match(sb))))
        ranked = sorted((kv for kv in aa.items() if _ok(kv[0])), key=lambda kv: -cos[kv[0]])[:top]
    else:
        ranked = sorted(aa.items(), key=lambda kv: -kv[1])[:top]
    bridges = {(a, b): sorted(nbr[a] & nbr[b], key=lambda z: (deg.get(z, 1), z))[:3] for (a, b), _ in ranked}
    lines = ["# Suggested wiki-links (%s over the association graph)" % (
                 "memory-vector cosine among pairs sharing >= %d neighbours" % min_common if cos else "Adamic-Adar"), "",
             "Read-only curation aid (suggest_links.py). Each pair shares many neighbours but has no",
             "direct [[link]]. AA predicts future co-read of unlinked pairs at AUROC ~0.71-0.81 (causal,",
             "2026-07-10), so these are real latent associations. Author the ones that are genuinely",
             "related by hand - do NOT bulk-add (mutating the non-stationary graph is the churn to avoid).",
             "",
             "CAVEAT: prefer SPECIFIC-to-specific pairs (both degrees low). A link to a high-degree hub",
             "(deg shown) adds little - the walk already reaches it in two hops - and densifies the",
             "hub-attractor the fire-precision judges flagged. High-deg endpoints recur here by nature.",
             ""]
    for (a, b), s in ranked:
        cr = coread.get(tuple(sorted((a, b))), 0)
        via = ", ".join(f"{real.get(z, z)} (deg {deg.get(z, 0)})" for z in bridges[(a, b)])
        cs = f"cos {cos[(a, b)]:.3f}, " if (a, b) in cos else ""
        lines.append(f"- **{cs}AA {s:.2f}** (co-read {cr}x)  `{real.get(a, a)}` [deg {deg.get(a, 0)}] "
                     f"<> `{real.get(b, b)}` [deg {deg.get(b, 0)}]")
        lines.append(f"    - {desc.get(a, '')}")
        lines.append(f"    - {desc.get(b, '')}")
        lines.append(f"    - shared context: {via}")
    try:
        with io.open(OUT, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        if not quiet:
            print("suggest_links: write failed:", e)
        return
    if not quiet:
        more = "" if exact_total else " (pairs whose only shared neighbours are hubs not counted)"
        print(f"suggest_links: {total} unlinked pairs with >={min_common} shared neighbours{more}, "
              f"cosine read the top {len(aa)} by AA; "
              f"wrote top {len(ranked)} to {OUT}")
        for (a, b), s in ranked[:8]:
            cs = f"cos {cos[(a, b)]:.3f}  " if (a, b) in cos else ""
            print(f"  {cs}AA {s:.2f}  {real.get(a, a)}  <>  {real.get(b, b)}")


if __name__ == "__main__":
    main()
