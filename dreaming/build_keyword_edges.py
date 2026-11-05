#!/usr/bin/env python3
"""build_keyword_edges.py - connect the SHORT high-value memos that cosine can't anchor (mostly the
owner's feedback/rules), via BM25 keyword overlap.

The reachability finding (2026-07-10): after orphan+island+series edges, ~55 truly-isolated (degree-0)
HIGH-SALIENCE memories remain - and they are dominated by the owner's FEEDBACK (feedback_gpu_svd_always,
feedback_full_margin_carry_required, feedback_ndx_conid...). His corrections are high-value wisdom, but
they can only be found by a direct lexical query, never surfaced by association. build_orphan_edges could
not attach them because they are SHORT: too little content to embed, so their best calibrated cosine sits
below the 0.40 anchor threshold. But BM25 keyword overlap nails them - a short GPU feedback shares rare
terms with the GPU memories. So this is the keyword fallback: for each degree-0 memo, BM25-search its body
and attach it to the best CONNECTED memo above a score threshold. Same safe pattern as build_orphan_edges:
a separate keyword_edges.tsv merged by build_spread_edges at low weight, never a memory-body edit,
eval-gated non-regression. Read-only bar the tsv. Usage: python build_keyword_edges.py [--min-bm25 30] [--weight 0.1] [--quiet]
"""
import io, os, re, sys
from collections import defaultdict

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
COACT = os.path.join(DR, "coactivation.tsv")
OUT = os.path.join(DR, "keyword_edges.tsv")
LINK_RE = re.compile(r"\[\[([^\]]+?)\]\]")
DATE_TAIL = re.compile(r"_\d{4}_\d{2}_\d{2}$")
SKIP = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS", "AUTOBIOGRAPHY",
        "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS", "MISTAKES", "CONSCIENCE", "CURIOSITY",
        "RECONSOLIDATION_QUEUE"}  # transient nightly work-queue, not a memory - never an attachment target


def norm(s):
    s = s.strip()
    if s.endswith(".md"):
        s = s[:-3]
    return DATE_TAIL.sub("", s.replace("-", "_").lower())


def _argf(flag, d, cast=float):
    a = sys.argv[1:]
    for i, x in enumerate(a):
        if x == flag and i + 1 < len(a):
            try:
                return cast(a[i + 1])
            except Exception:
                return d
    return d


def keyword_edges(min_bm25=30.0, weight=0.1):
    """Return [(orphan_slug, anchor_slug, weight)]. Pure function for the eval to reuse.
    Since 2026-07-16 the list also carries COMPONENT BRIDGES (OQ-1188): one BM25 edge per
    off-main island whose members neither cosine nor the degree-0 pass could attach -
    build_orphan_edges bridges components by cosine (can't see weak embeddings), the pass
    above only visits degree-0 nodes (skips degree-1+ islands); an island with internal
    edges and weak embeddings fell through both. Probe-measured 07-11: same-project pairs
    score BM25 35-44, genuinely-weak ones ~10-20, so the >=30 threshold separates."""
    real = {norm(fn): fn[:-3] for fn in os.listdir(MEM) if fn.endswith(".md") and fn[:-3] not in SKIP}
    deg = defaultdict(int)
    adj_pairs = []
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
        for m in LINK_RE.findall(body):
            b = norm(m)
            if b in real and b != src:
                deg[src] += 1
                deg[b] += 1
                adj_pairs.append((src, b))
    if os.path.exists(COACT):
        for ln in io.open(COACT, encoding="utf-8", errors="replace"):
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 3 and p[0] != "a":
                a, b = norm(p[0]), norm(p[1])
                if a in real and b in real and a != b:
                    deg[a] += 1
                    deg[b] += 1
                    adj_pairs.append((a, b))
    # target ONLY the degree-0 memos that the cosine tool (build_orphan_edges) leaves unconnected -
    # the short ones with no strong content anchor. Redundant keyword edges to already-cosine-connected
    # memos are harmless but wasteful; this is the clean fallback for exactly the cosine-can't residual.
    cosine_connected = set()
    oe_pairs = []
    try:
        import build_orphan_edges as boe
        oe, _, _ = boe.orphan_edges(0.40, 0.1)
        cosine_connected = {norm(a) for a, b, w in oe} | {norm(b) for a, b, w in oe}
        oe_pairs = [(norm(a), norm(b)) for a, b, w in oe]
    except Exception:
        cosine_connected = set()
    orphans = [s for s in real if deg.get(s, 0) == 0 and s not in cosine_connected]
    try:
        from bm25 import BM25
        bm = BM25()
    except Exception:
        return [], real, deg
    out = []
    for s in orphans:
        try:
            body = io.open(os.path.join(MEM, real[s] + ".md"), encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        try:
            hits = bm.score(body[:400], 10)
        except Exception:
            continue
        for h, sc in hits:
            hn = norm(h)
            # anchor must be a real, CONNECTED memo (deg>0), not the orphan itself
            if hn != s and hn in real and deg.get(hn, 0) > 0 and sc >= min_bm25:
                out.append((real[s], real.get(hn, h), round(weight, 3)))
                break

    # ---- component bridges (OQ-1188): one BM25 edge per off-main ISLAND ----
    # Union adjacency = wiki + co-read + cosine orphan edges + the keyword edges above;
    # BFS components; for every non-main component, the best (member -> main-member) BM25
    # pair >= min_bm25 becomes ONE bridge (one edge reaches the whole island, same design
    # as build_orphan_edges' component pass). Guarded: any failure -> no bridges, never
    # fewer plain keyword edges.
    try:
        adj = defaultdict(set)
        for a, b in adj_pairs + oe_pairs + [(norm(a), norm(b)) for a, b, w in out]:
            if a in real and b in real and a != b:
                adj[a].add(b)
                adj[b].add(a)
        comp = {}
        cid = 0
        for start in adj:
            if start in comp:
                continue
            cid += 1
            stack = [start]
            comp[start] = cid
            while stack:
                x = stack.pop()
                for y in adj[x]:
                    if y not in comp:
                        comp[y] = cid
                        stack.append(y)
        if comp:
            from collections import Counter as _C
            sizes = _C(comp.values())
            main_id = sizes.most_common(1)[0][0]
            main_nodes = {n for n, c in comp.items() if c == main_id}
            islands = defaultdict(list)
            for n, c in comp.items():
                if c != main_id:
                    islands[c].append(n)
            for c, members in sorted(islands.items()):
                best = None
                for s in sorted(members)[:12]:
                    try:
                        body = io.open(os.path.join(MEM, real[s] + ".md"),
                                       encoding="utf-8", errors="replace").read()
                        hits = bm.score(body[:400], 10)
                    except Exception:
                        continue
                    for h, sc in hits:
                        hn = norm(h)
                        if hn in main_nodes and hn != s and sc >= min_bm25:
                            if best is None or sc > best[2]:
                                best = (real[s], real.get(hn, h), sc)
                            break
                if best:
                    out.append((best[0], best[1], round(weight, 3)))
    except Exception:
        pass
    return out, real, deg


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes build artifacts, so a help
        # request must exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    min_bm25 = _argf("--min-bm25", 30.0)
    weight = _argf("--weight", 0.1)
    quiet = "--quiet" in sys.argv[1:]
    edges, real, deg = keyword_edges(min_bm25, weight)
    try:
        with io.open(OUT, "w", encoding="utf-8") as f:
            f.write("a\tb\tweight\n")
            for a, b, w in edges:
                f.write(f"{a}\t{b}\t{w}\n")
    except Exception as e:
        if not quiet:
            print("build_keyword_edges: write failed:", e)
        return
    if not quiet:
        n_orphan = sum(1 for s in real if deg.get(s, 0) == 0)
        print(f"build_keyword_edges: {n_orphan} degree-0 orphans; {len(edges)} BM25-anchored "
              f"(min_bm25={min_bm25}, weight {weight}) -> {OUT}")
        for a, b, w in edges[:6]:
            print(f"  {a}  ->  {b}")


if __name__ == "__main__":
    main()
