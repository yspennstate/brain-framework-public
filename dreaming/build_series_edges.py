#!/usr/bin/env python3
"""build_series_edges.py - make the brain's research PROGRAMS navigable, not just reachable.

The orphan/island bridges (build_orphan_edges) made every memory reachable (95.2%->99.6% connected),
but a research SERIES is still not NAVIGABLE: arnold_diffusion has 43 memories all in the graph, yet
from one cycle the 2-hop walk reaches only 2-7 of the other 42 (measured 2026-07-10). A session that
recalls "arnold cycle 22" cannot reach its own proof program. The cosine bridge connected each member
to a single nearest sibling (a sparse chain), and the walk's fan-out normalization means a lone chain
does not carry activation across a 43-memory program.

This detects series by shared STEM (the common prefix before a trailing cycle/round/W-number + date) and,
for each substantive series, links each member to its K nearest siblings by content cosine - a denser
local structure so the 2-hop walk reaches the whole program. Status-ping series (build_watchdog/inspector
_quiet) are EXCLUDED - they were measured inert for recall (eval_denoise), so making them navigable buys
nothing. Writes a SEPARATE series_edges.tsv that build_spread_edges merges (never edits a memory body).
Weight and K are eval-chosen (eval_series_edges.py) for navigability gain at zero recall regression.
Read-only bar the tsv. Usage: python build_series_edges.py [--k 4] [--weight 0.15] [--quiet]

SHIPPED 2026-07-10 (eval-gated): k=4, weight-factor 0.15, ~1004 edges over 12 substantive series
(arnold_diffusion, builder_new, inspector, the rmt/cast programs, W80 directives...). Existing-recall
delta was EXACTLY 0.000 on hit@10/@25/MRR across 3 splits at every weight tried (these programs are old
and not in the recent trace, so linking them cannot crowd current recall). Verified live: arnold
navigability 2 -> 27 of 42 members reachable in 2 hops; a control memory still returns 115 associates.
Wired into run_dream before build_spread_edges. See brain_orphan_reachability_2026_07_10.
"""
import io, os, re, sys
from collections import defaultdict
try:
    import embedding_artifact
except Exception:
    embedding_artifact = None

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
EMB = os.path.join(DR, "memory_embeddings.npz")
EMB_AUTHORITY = os.path.join(DR, ".embedding_current.json")
OUT = os.path.join(DR, "series_edges.tsv")
DATE_TAIL = re.compile(r"_\d{4}_\d{2}_\d{2}$")
# the varying index token of a series: cycle18 / w94 / round7 / part3 / v1 / cycle_31 etc.
SERIES_SPLIT = re.compile(r"_(?:cycle|c|w|round|part|pt|v|wave|day|iter|step|ablation|pod)?_?\d{1,4}(?:_|$)", re.I)
NOISE = re.compile(r"(build_watchdog|inspector_w\d+.*(quiet|clean)|_quiet_20)", re.I)
SKIP = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS", "AUTOBIOGRAPHY",
        "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS", "MISTAKES", "CONSCIENCE", "CURIOSITY"}
MIN_SERIES = 4


def norm(s):
    s = s.strip()
    if s.endswith(".md"):
        s = s[:-3]
    return DATE_TAIL.sub("", s.replace("-", "_").lower())


def stem(canon):
    """The series stem: the prefix before the first cycle/round/W-number token."""
    m = SERIES_SPLIT.search(canon)
    return canon[:m.start()] if m and m.start() >= 4 else None


def _argf(flag, d, cast=float):
    a = sys.argv[1:]
    for i, x in enumerate(a):
        if x == flag and i + 1 < len(a):
            try:
                return cast(a[i + 1])
            except Exception:
                return d
    return d


def series_edges(k=4, weight=0.15):
    """Return [(memberA, memberB, weight)] linking each series member to its K nearest siblings by cosine.
    Pure function so the eval reuses it. Uses the real filename slugs in the output."""
    real = {norm(fn): fn[:-3] for fn in os.listdir(MEM) if fn.endswith(".md") and fn[:-3] not in SKIP}
    body_len = {}
    for canon, slug in real.items():
        try:
            body_len[canon] = os.path.getsize(os.path.join(MEM, slug + ".md"))
        except Exception:
            body_len[canon] = 0
    groups = defaultdict(list)
    for canon in real:
        if NOISE.search(canon):
            continue
        st = stem(canon)
        if st:
            groups[st].append(canon)
    # SUBSTANCE filter: link only series of real research memos, not short status pings. A status cycle
    # ("W#100 quiet. Bots untouched") is a few hundred bytes; a research memo is thousands. Excluding
    # low-median-length series drops inspector/builder/audit-cycle noise (measured inert, eval_denoise)
    # while keeping arnold_diffusion, the rmt/cast programs, the W80 directives, etc. Content, not name.
    MIN_MEDIAN_BYTES = 900
    series = {}
    for st, ms in groups.items():
        if len(ms) < MIN_SERIES:
            continue
        lens = sorted(body_len.get(m, 0) for m in ms)
        median = lens[len(lens) // 2]
        if median >= MIN_MEDIAN_BYTES:
            series[st] = ms
    if embedding_artifact is None:
        return [], series
    import numpy as np
    loaded = embedding_artifact.load_verified_embeddings(
        EMB, EMB_AUTHORITY, MEM, "BAAI/bge-small-en-v1.5", verify_all_rows=True
    )
    if loaded is None:
        return [], series
    raw_idx, mat, _ = loaded
    idx = {norm(s): i for s, i in raw_idx.items()}
    out = []
    seen = set()
    for st, members in series.items():
        mem_in = [m for m in members if m in idx]
        if len(mem_in) < MIN_SERIES:
            continue
        V = mat[[idx[m] for m in mem_in]]
        sims = V @ V.T
        for r, a in enumerate(mem_in):
            order = sims[r].argsort()[::-1]
            added = 0
            for c in order:
                b = mem_in[int(c)]
                if b == a:
                    continue
                key = tuple(sorted((a, b)))
                if key in seen:
                    continue
                seen.add(key)
                out.append((real[a], real[b], round(weight * float(sims[r, int(c)]), 3)))
                added += 1
                if added >= k:
                    break
    return out, series


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes build artifacts, so a help
        # request must exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    k = _argf("--k", 4, int)
    weight = _argf("--weight", 0.15)
    quiet = "--quiet" in sys.argv[1:]
    edges, series = series_edges(k, weight)
    try:
        with io.open(OUT, "w", encoding="utf-8") as f:
            f.write("a\tb\tweight\n")
            for a, b, w in edges:
                f.write(f"{a}\t{b}\t{w}\n")
    except Exception as e:
        if not quiet:
            print("build_series_edges: write failed:", e)
        return
    if not quiet:
        big = sorted(series.items(), key=lambda kv: -len(kv[1]))[:6]
        print(f"build_series_edges: {len(series)} substantive series (>= {MIN_SERIES}), "
              f"{len(edges)} intra-series edges (k={k}, weight-factor {weight}) -> {OUT}")
        for st, ms in big:
            print(f"  {st}  ({len(ms)} members)")


if __name__ == "__main__":
    main()
