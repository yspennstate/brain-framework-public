#!/usr/bin/env python3
"""build_spread_edges.py - compile the association graph the spreading-activation recall walks.

Recall today is lexical: fok_probe/instinct_prime match the prompt's words against filenames and
index lines. That structurally can't find the memory that matters but shares no vocabulary - the
distant, "this reminds me..." connection a person makes. Spreading activation (Collins & Loftus
1975; Anderson's ACT-R) does: light up what the prompt touches, let activation flow along the
association graph, and see what downstream node glows that you wouldn't have grepped for.

This merges the two association signals the brain already keeps into one weighted edge list the
online hook can load fast (the walk must run inside an 8s hook, so the heavy parse lives here):
  - Hebbian co-activation (coactivation.tsv): memories the brain actually READ TOGETHER while
    reasoning. The lived signal - strong, but young and sparse, so on its own it just echoes the
    last few sessions' hubs. Weighted higher (W_COACT) because "reasoned with together" is a
    truer link than "an author once cross-referenced."
  - Wiki-links ([[slug]] in memory bodies): the hand-authored backbone - rich (3000+ links) and
    stable, so it carries the walk while the Hebbian graph fills in.

Undirected, deduped, symmetric. Node ids are memory slugs (filename without .md), links resolved
the way dream.py does it (strip a trailing _YYYY_MM_DD, fold - / _) so [[a-b]] finds a_b_2026.md.
Read-only over the memory dir; writes spread_edges.tsv. Rebuild in the dream / nav build.
"""
import os, re, io, sys
from collections import defaultdict

HOME = os.path.expanduser("~")
# BRAIN_MEM_DIR names the memory folder outright (a machine whose folder name differs from the installed slug,
# or a test copy); otherwise the installed one.
MEM = os.environ.get("BRAIN_MEM_DIR") or os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
DR = os.path.dirname(os.path.abspath(__file__))
COACT = os.path.join(DR, "coactivation.tsv")
OUT = os.path.join(DR, "spread_edges.tsv")

W_COACT = 1.6    # the lived "reasoned together" signal outweighs a hand-authored cross-reference
W_WIKI = 1.0
W_TWIN = 0.5     # name twins that differ only in their date tail (see main)
LINK_RE = re.compile(r"\[\[([^\]]+?)\]\]")
DATE_TAIL = re.compile(r"_\d{4}_\d{2}_\d{2}$")
# non-memory pages that would act as false hubs (everything links the index / the mistakes log).
# RECONSOLIDATION_QUEUE is transient substrate (a nightly-cycling work-queue of slugs due for review);
# an edge to/from it just connects whatever memories happen to share the queue - pure noise. This is the
# authoritative node filter: build_spread_edges.add() rejects any edge whose endpoint isn't in `real`
# (built from this SKIP), so a name here is excluded from the live walk graph no matter what a generator emits.
SKIP = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS", "AUTOBIOGRAPHY",
        "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS", "MISTAKES", "CONSCIENCE", "CURIOSITY",
        "RECONSOLIDATION_QUEUE"}


def norm(slug):
    """Resolve a slug/link to a canonical key: drop .md, fold separators, strip a trailing date."""
    s = slug.strip()
    if s.endswith(".md"):
        s = s[:-3]
    s = s.replace("-", "_").lower()
    s = DATE_TAIL.sub("", s)
    return s


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes build artifacts, so a help
        # request must exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    # canonical key -> the real filename slug (so output ids are the actual files)
    # A name that matches a file exactly (case and -/_ folded, date kept) goes to that file; only a name with no
    # exact file falls back to the date-stripped key. Keying nodes by the stripped key alone merged files that
    # differ only in their date (identity_who_you_are vs identity_who_you_are_2026_07_07, two observer_stack_audit
    # runs months apart): on 31 Oct 2026 that hid 26 memories from the walk graph and sent 1,850 of 97,828 exact
    # links to the wrong twin.
    real, exact = {}, {}
    for fn in sorted(os.listdir(MEM)):
        if fn.endswith(".md") and fn[:-3] not in SKIP:
            real[norm(fn)] = fn[:-3]
            exact[fn[:-3].replace("-", "_").lower()] = fn[:-3]

    def resolve(slug):
        s = slug.strip()
        if s.endswith(".md"):
            s = s[:-3]
        s = s.replace("-", "_").lower()
        return exact.get(s) or real.get(DATE_TAIL.sub("", s))

    edges = defaultdict(float)   # (slugA, slugB) sorted -> weight

    def add(a, b, w):
        a, b = resolve(a), resolve(b)
        if a is None or b is None or a == b:
            return
        key = (a, b) if a < b else (b, a)
        edges[key] += w

    # Date twins (files whose names differ only in the date tail) are the same subject: the undated wake stubs
    # (identity_who_you_are, shared_language, ...) link only to each other, and without this they form an island
    # apart from their dated full versions.
    twins = defaultdict(list)
    for s in exact.values():
        twins[norm(s)].append(s)
    twin_n = 0
    for group in twins.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                add(a, b, W_TWIN)
                twin_n += 1

    # wiki-links from every memory body
    wiki_n = 0
    for fn in os.listdir(MEM):
        if not fn.endswith(".md") or fn[:-3] in SKIP:
            continue
        src = fn[:-3]
        try:
            text = io.open(os.path.join(MEM, fn), encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for m in LINK_RE.findall(text):
            add(src, m, W_WIKI)
            wiki_n += 1

    # Hebbian co-activation
    coact_n = 0
    if os.path.exists(COACT):
        for ln in io.open(COACT, encoding="utf-8", errors="replace"):
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 3 and p[0] != "a":
                try:
                    add(p[0], p[1], W_COACT * float(p[2]))
                    coact_n += 1
                except Exception:
                    pass

    # Orphan attachment edges (build_orphan_edges.py): low-weight content links that lift the ~23% of
    # memories with zero wiki/co-read edges from INVISIBLE to reachable. Eval-gated: at these weights the
    # existing recall is non-regressed to 0.000 on hit@10/@25/MRR (eval_orphan_edges.py, 2026-07-10). The
    # weight already lives in the file; add() merges it like any edge. Absent file -> behaviour unchanged.
    orphan_n = 0
    ORPHAN = os.path.join(DR, "orphan_edges.tsv")
    if os.path.exists(ORPHAN):
        for ln in io.open(ORPHAN, encoding="utf-8", errors="replace"):
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 3 and p[0] != "a":
                try:
                    add(p[0], p[1], float(p[2]))
                    orphan_n += 1
                except Exception:
                    pass

    # Series edges (build_series_edges.py): link each research-PROGRAM member to its nearest siblings so
    # the walk can traverse the whole program, not just reach it (arnold_diffusion navigability 2->27 of
    # 42). Eval-gated to 0.000 recall regression (eval_series_edges.py). Absent file -> unchanged.
    series_n = 0
    SERIES = os.path.join(DR, "series_edges.tsv")
    if os.path.exists(SERIES):
        for ln in io.open(SERIES, encoding="utf-8", errors="replace"):
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 3 and p[0] != "a":
                try:
                    add(p[0], p[1], float(p[2]))
                    series_n += 1
                except Exception:
                    pass

    # Keyword edges (build_keyword_edges.py): the BM25 fallback for the SHORT high-salience memos cosine
    # can't embed - mostly the owner's isolated FEEDBACK (feedback_gpu_svd_always, feedback_full_margin_carry
    # ...). Attaches each to the connected memo sharing its rarest terms, so his corrections surface by
    # association, not only by a direct lexical query. Eval-gated to 0.000 recall regression
    # (eval_keyword_edges.py). Absent file -> unchanged.
    keyword_n = 0
    KEYWORD = os.path.join(DR, "keyword_edges.tsv")
    if os.path.exists(KEYWORD):
        for ln in io.open(KEYWORD, encoding="utf-8", errors="replace"):
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 3 and p[0] != "a":
                try:
                    add(p[0], p[1], float(p[2]))
                    keyword_n += 1
                except Exception:
                    pass

    # atomic write: a concurrent session's spread_recall reads this file live; a half-written file
    # would break its walk. Write to a temp then os.replace (atomic on Windows + POSIX).
    tmp = OUT + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write("a\tb\tweight\n")
        for (a, b), w in sorted(edges.items(), key=lambda kv: -kv[1]):
            f.write(f"{a}\t{b}\t{w:.3f}\n")
    os.replace(tmp, OUT)

    print(f"spread_edges: {len(edges)} edges from {wiki_n} wiki-links + {twin_n} date-twin + {coact_n} coactivation pairs "
          f"+ {orphan_n} orphan + {series_n} series + {keyword_n} keyword edges over {len(exact)} nodes -> {OUT}")


FRESH = os.path.join(DR, "spread_edges_fresh.tsv")
LOOKBACK = 7200  # seconds; see fresh()


def fresh():
    """The wiki-links of memories written after spread_edges.tsv was built, into spread_edges_fresh.tsv.

    Only the nightly dream rebuilds spread_edges.tsv, so a memory written in the morning had no graph edge in
    recall until the next night (in the reference install, 13 of 95 memories written over two days were isolated,
    every one of them written after the last nightly build). This reads only the newer files and skips any pair
    the nightly file already holds, so an edited old memory does not count its links twice. spread_recall merges
    the file while it is newer than spread_edges.tsv; the next nightly build makes it moot. Links INTO a new
    memory from a file older than the build are not seen here (that file was read before the target existed).
    bm25.py refresh calls this whenever it indexes a new memory, so the links are live within one refresh."""
    try:
        # Look back LOOKBACK seconds before the nightly file's mtime: the build reads the memory dir minutes before it
        # writes OUT, so a memory written inside that window was in neither file (3 Nov 02:33: 11 term_ nodes written,
        # build file stamped 02:35:15, all 11 isolated). Pairs OUT already holds are skipped below, so re-reading the
        # window never counts a link twice. 3 Nov 2026.
        cut = os.path.getmtime(OUT) - LOOKBACK
    except OSError:
        return
    real, newer = {}, []
    with os.scandir(MEM) as it:
        for de in it:
            if de.name.endswith(".md") and de.name[:-3] not in SKIP:
                real[norm(de.name)] = de.name[:-3]
                try:
                    if de.stat().st_mtime > cut:
                        newer.append(de.name)
                except OSError:
                    pass
    have = set()
    with io.open(OUT, encoding="utf-8", errors="replace") as f:
        next(f, None)
        for ln in f:
            p = ln.split("\t", 2)
            if len(p) >= 2:
                a, b = norm(p[0]), norm(p[1])
                have.add((a, b) if a < b else (b, a))
    edges = defaultdict(float)
    for fn in newer:
        src = norm(fn)
        try:
            text = io.open(os.path.join(MEM, fn), encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for m in set(LINK_RE.findall(text)):
            b = norm(m)
            if b == src or b not in real:
                continue
            key = (src, b) if src < b else (b, src)
            if key not in have:
                edges[key] = W_WIKI
    tmp = FRESH + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write("a\tb\tweight\n")
        for (a, b), w in sorted(edges.items()):
            f.write(f"{real[a]}\t{real[b]}\t{w:.3f}\n")
    os.replace(tmp, FRESH)
    print(f"spread_edges_fresh: {len(edges)} edges from {len(newer)} memories newer than the nightly build -> {FRESH}")


if __name__ == "__main__":
    if "--fresh" in sys.argv[1:]:
        fresh()
    else:
        main()
