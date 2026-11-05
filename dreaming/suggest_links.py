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
import io, math, os, re, sys
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
    deg = {z: len(nbr[z]) for z in nbr}

    aa = {}
    bridges = {}
    seen = set()
    for a in nbr:
        Na = nbr[a]
        for z in Na:
            for b in nbr[z]:
                if b <= a:
                    continue
                key = (a, b)
                if key in seen:
                    continue
                seen.add(key)
                if tuple(sorted((a, b))) in wiki_pairs:
                    continue
                if a in superseded or b in superseded:
                    continue
                common = Na & nbr[b]
                if len(common) < min_common:
                    continue
                score = sum(1.0 / math.log(1.0 + deg.get(z, 1)) for z in common if deg.get(z, 1) > 1)
                aa[key] = score
                bridges[key] = sorted(common, key=lambda z: deg.get(z, 1))[:3]

    ranked = sorted(aa.items(), key=lambda kv: -kv[1])[:top]
    lines = ["# Suggested wiki-links (Adamic-Adar over the association graph)", "",
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
        lines.append(f"- **AA {s:.2f}** (co-read {cr}x)  `{real.get(a, a)}` [deg {deg.get(a, 0)}] "
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
        print(f"suggest_links: {len(aa)} unlinked pairs with >={min_common} shared neighbours; "
              f"wrote top {len(ranked)} to {OUT}")
        for (a, b), s in ranked[:8]:
            print(f"  AA {s:.2f}  {real.get(a, a)}  <>  {real.get(b, b)}")


if __name__ == "__main__":
    main()
