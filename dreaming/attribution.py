#!/usr/bin/env python3
"""attribution.py - did a SURFACED memory actually get used, or ignored, or mislead? (the utilization gap)

Every recall eval in this brain proxies "did the memory help" with CO-READ (was it read later). The
2026 literature says that proxy misses most of the story: retrieval accuracy explains only ~60% of RAG
quality, the other ~40% is UTILIZATION (CUE metric), and as memory gets stronger, failures shift from
retrieval-miss to post-retrieval - "Retrieved-but-Unused" and a surfaced memory that MISLEADS a wrong
answer that then looks like model failure (MemSyco-Bench 2607.01071; The Misattribution Gap 2605.22842).

This joins three durable ledgers, per session, to attribute an OUTCOME to each surfacing:
  surfaced  - surfaced.tsv (spread_recall / fok_probe append when they fire: session, ts, slug)
  read      - retrievals.tsv (the memory was actually opened after being surfaced)
  error     - outcomes.jsonl (a felt-error fired in the same session near the surfacing)

Per memory, over time:
  used-rate     = fraction of its surfacings followed by a read of it that session (utilization, the 40%)
  near-error    = surfacings where an ERN felt-error fired within the window (a mislead SUSPECT - not
                  proof; the Misattribution Gap is that this looks like model failure, so flag, don't blame)
A memory surfaced often but never used is noise (fade candidate); one whose surfacings cluster with
errors is a mislead suspect for the dream to read. Laptop-safe, read-only over the ledgers.
Usage: python attribution.py
"""
import os, io, json, time
from collections import defaultdict

DR = os.path.dirname(os.path.abspath(__file__))
SURF = os.path.join(DR, "surfaced.tsv")
TRACE = os.path.join(DR, "retrievals.tsv")
OUTCOMES = os.path.join(DR, "outcomes.jsonl")
USE_WINDOW = 3600      # a surfaced memory read within this many seconds counts as "used"
ERR_WINDOW = 900       # a felt-error within this many seconds of a surfacing = near-error


def load_surfaced():
    rows = []
    try:
        for ln in io.open(SURF, encoding="utf-8", errors="replace"):
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 3 and p[0] != "ts":
                try:
                    rows.append((float(p[0]), p[1][:12], p[2]))   # (ts, session, slug)
                except Exception:
                    pass
    except Exception:
        pass
    return rows


def load_reads():
    by = defaultdict(list)   # (session, slug) -> [epoch,...]
    try:
        for i, ln in enumerate(io.open(TRACE, encoding="utf-8", errors="replace")):
            if i == 0:
                continue
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 4:
                try:
                    by[(p[3][:12], p[0])].append(float(p[1]))
                except Exception:
                    pass
    except Exception:
        pass
    return by


def load_errors():
    by = defaultdict(list)   # session -> [ts,...]
    try:
        for ln in io.open(OUTCOMES, encoding="utf-8", errors="replace"):
            if not ln.strip().startswith("{"):
                continue
            try:
                r = json.loads(ln)
                by[(r.get("session") or "")[:12]].append(float(r.get("ts", 0)))
            except Exception:
                pass
    except Exception:
        pass
    return by


def load_neighbors():
    """slug -> set(neighbour slugs) from the association graph, for TOPICAL use: the surfaced memory's
    description steered the session to its cluster, so a read of a NEIGHBOUR counts as the association
    working even if that exact file was never opened (description-level use the exact-read proxy misses)."""
    nbr = defaultdict(set)
    try:
        with io.open(os.path.join(DR, "spread_edges.tsv"), encoding="utf-8", errors="replace") as f:
            next(f, None)
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) >= 3 and p[0] != "a":
                    nbr[p[0]].add(p[1])
                    nbr[p[1]].add(p[0])
    except Exception:
        pass
    return nbr


def reads_indexes():
    """(session -> [(epoch, slug)]) and (slug -> [epoch]) for topical and delayed-use checks."""
    by_sess = defaultdict(list)
    by_slug = defaultdict(list)
    try:
        for i, ln in enumerate(io.open(TRACE, encoding="utf-8", errors="replace")):
            if i == 0:
                continue
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 4:
                try:
                    ep = float(p[1])
                    by_sess[p[3][:12]].append((ep, p[0]))
                    by_slug[p[0]].append(ep)
                except Exception:
                    pass
    except Exception:
        pass
    return by_sess, by_slug


def main():
    surfaced = load_surfaced()
    if len(surfaced) < 10:
        print("attribution: only %d surfacings logged yet - the surface-log matures as spread_recall/"
              "fok fire. Re-run once it has accumulated (needs live sessions)." % len(surfaced))
        return
    reads = load_reads()
    errors = load_errors()
    nbr = load_neighbors()
    by_sess, by_slug = reads_indexes()

    per = defaultdict(lambda: {"surf": 0, "used": 0, "topical": 0, "delayed": 0, "near_err": 0})
    for ts, sess, slug in surfaced:
        st = per[slug]
        st["surf"] += 1
        exact = any(0 <= (e - ts) <= USE_WINDOW for e in reads.get((sess, slug), []))
        if exact:
            st["used"] += 1
        # TOPICAL: a NEIGHBOUR of the surfaced memory read this session within the window (the association
        # steered the session to the right cluster even if that exact file was never opened)
        elif nbr.get(slug) and any(s in nbr[slug] and 0 <= (e - ts) <= USE_WINDOW
                                   for e, s in by_sess.get(sess, [])):
            st["topical"] += 1
        # DELAYED: the exact memory read in ANY session AFTER this surfacing (a seed that paid off later)
        elif any(e > ts + USE_WINDOW for e in by_slug.get(slug, [])):
            st["delayed"] += 1
        if any(abs(e - ts) <= ERR_WINDOW for e in errors.get(sess, [])):
            st["near_err"] += 1

    rows = sorted(per.items(), key=lambda kv: -kv[1]["surf"])
    tot_s = sum(v["surf"] for _, v in rows)
    tot_u = sum(v["used"] for _, v in rows)
    tot_b = sum(v["used"] + v["topical"] + v["delayed"] for _, v in rows)
    print("attribution: %d surfacings across %d memories (utilization = did a surface get used?)" % (tot_s, len(rows)))
    print("  NARROW used-rate: %.2f  (%d/%d led to a read of the exact memory, same session)"
          % (tot_u / max(1, tot_s), tot_u, tot_s))
    print("  BROAD  used-rate: %.2f  (%d/%d - exact + TOPICAL (a neighbour read) + DELAYED (read later); "
          "the description-level use the exact-read proxy misses)" % (tot_b / max(1, tot_s), tot_b, tot_s))
    print("  %-44s %5s %5s %5s %5s %8s" % ("memory", "surf", "exact", "topcl", "delay", "near-err"))
    for slug, v in rows[:15]:
        broad = v["used"] + v["topical"] + v["delayed"]
        flag = "  <- noise? (never used any way)" if v["surf"] >= 4 and broad == 0 else (
               "  <- mislead suspect" if v["near_err"] >= 2 and v["near_err"] >= broad else "")
        print("  %-44s %5d %5d %5d %5d %9d%s" % (slug[:44], v["surf"], v["used"], v["topical"],
                                                 v["delayed"], v["near_err"], flag))
    print("  read: a memory surfaced >=4x with 0 BROAD uses (never read, no neighbour read, never read "
          "later) is a real fade/de-prioritise candidate; surfacings clustered with felt-errors are "
          "mislead SUSPECTS for the dream (the Misattribution Gap - looks like model failure, root may "
          "be the memory). Habituation (spread_recall) already damps the worst over-surfacer live.")


if __name__ == "__main__":
    main()
