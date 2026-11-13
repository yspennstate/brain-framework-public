# -*- coding: utf-8 -*-
"""graph_lane.py - the use-grown graph as a lane of brain.py recall, behind one dial.

His order, 26 Sep 2026 04:28: build the edge and diffusion part now, with a parameter that tunes it against the simple
recall that works today. The edges are an agent's store (graph_store.py over the capture of every touch agents
make: words, tools, files, folders, memories read and written, notes served, reflexes, errors); the diffusion is
diffusion.py's personalised PageRank over them, seeded by the query's words and, with prf > 0, by lexical search's
top memories (what agents read together with what bm25 already found).

    BRAIN_GRAPH_LAMBDA   the dial: 0 = today's recall order exactly, 1 = the graph's order alone
    BRAIN_GRAPH_STORE    the store to walk (a graph_store.py output folder)
Defaults for both, and for the walk's own parameters, are in brain_graph_dial.json beside this file.

    python graph_lane.py "a question"      # the graph's own top memories for a question, and the dial in force
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DIAL = os.path.join(HERE, "brain_graph_dial.json")
_G = {}


def dial():
    d = {"lambda": 0.0, "store": "", "alpha": 0.3, "gamma": 0.5, "co_w": 0.5, "cap": 300, "prf": 5, "fuse": "convex"}
    try:
        with open(DIAL, encoding="utf-8") as fh:
            d.update(json.load(fh))
    except Exception:
        pass
    try:
        if os.environ.get("BRAIN_GRAPH_LAMBDA", "").strip():
            d["lambda"] = min(1.0, max(0.0, float(os.environ["BRAIN_GRAPH_LAMBDA"])))
    except ValueError:
        pass
    if os.environ.get("BRAIN_GRAPH_STORE", "").strip():
        d["store"] = os.environ["BRAIN_GRAPH_STORE"].strip()
    # a measurement sets the use/link mixture per run (27 Sep, the fleet's R7: which walk carries the signal)
    try:
        if os.environ.get("BRAIN_GRAPH_MIX", "").strip():
            d["mix_links"] = min(1.0, max(0.0, float(os.environ["BRAIN_GRAPH_MIX"])))
    except ValueError:
        pass
    return d


def _generation(path):
    """Which version of a file a cache entry was read from: its absolute path (resolved when it is a link), file id, size and last write, and for
    an SQLite file the change counter in its header; the path alone when the file is missing. A rebuild swapped in by
    rename gets a new file id, and a rewrite in place (the capture refreshes the sidecar's memory rows every 15 minutes)
    a new write time and counter, so either one makes a new key. The counter tells apart two commits inside one tick of
    the file clock (about 15 ms here), which size and write time cannot; it moves in rollback-journal mode, the
    sidecar's, and not in WAL mode. Any other file adds a hash of its last 64 KB: an npz ends in its zip directory, which
    carries every member's CRC-32, so a same-size rewrite in place that restores the write time still makes a new key
    (1000-page report D05-T04)."""
    import hashlib
    # abspath, and realpath only for a link: realpath opens the file to ask for its final name, and on the live
    # sidecar (held open by the capture and many readers) that took 20-330 ms in half the calls and 12.5 s of one
    # 38 s recall (30 Oct, lead 6). The key's file id, size and write time still tell two files apart; a path
    # through a junction now keys on the junction's spelling (one cache miss, never a wrong hit).
    real = os.path.abspath(path)
    if os.path.islink(real):
        real = os.path.realpath(path)
    try:
        st = os.stat(real)
    except OSError:
        return (real, None, None, None, None, None)
    counter = digest = None
    try:
        with open(real, "rb") as fh:
            head = fh.read(28)
            if head[:16] == b"SQLite format 3\x00":
                counter = int.from_bytes(head[24:28], "big")
            else:
                fh.seek(max(0, st.st_size - 65536))
                digest = hashlib.sha1(fh.read(65536)).hexdigest()[:20]
    except OSError:
        pass
    return (real, st.st_ino, st.st_size, st.st_mtime_ns, counter, digest)


def _keep(cache, key, value, most):
    """Hold value under key, dropping the oldest entries past most: a long-lived process that sees rebuild after
    rebuild keeps the last few generations (two arms and a switch back), not all of them."""
    cache.pop(key, None)
    while len(cache) >= most:
        cache.pop(next(iter(cache)))
    cache[key] = value
    return value


def _graph(d, build=False):
    """The prebuilt walk for the dial's store, or None. Recall never builds it (minutes under load): the store's
    rebuild job, or `python graph_lane.py --build`, does. Keyed by the generation of the store's edges as well as its
    path, as the walk's disk cache already is: until 30 Sep a process kept the first walk it loaded for a path after
    the store was rebuilt there (interactive audit B100-03)."""
    key = (d["store"], d["co_w"], d["cap"], d["alpha"]) + _generation(os.path.join(d["store"], "edges.npz"))
    if _G.get(key) is None:
        sys.path.insert(0, HERE)
        import diffusion
        _keep(_G, key, diffusion.Diffusion.cached(d["store"], co_w=d["co_w"], cap=int(d["cap"]), alpha=d["alpha"],
                                                  cache_dir=os.path.join(HERE, "graph_lane_cache"), build=build), 2)
    return _G[key]


def ranked(query, allowed=None, lex_top=(), depth=200, d=None):
    """[(slug, score)] best first from the walk; [] when the store is missing or no seed is a node."""
    import numpy as np
    d = d or dial()
    if not d.get("store") or not os.path.exists(os.path.join(d["store"], "edges.npz")):
        return []
    g = _graph(d)
    if g is None:
        print("  [graph lane] no prebuilt walk for %s - run: python graph_lane.py --build" % d["store"])
        return []
    w = g.seed_words(query)
    tot = sum(w.values())
    S = np.zeros((g.N, 1), np.float32)
    for i, v in w.items():
        S[i, 0] = v
    top = [(sl, sc) for sl, sc in list(lex_top)[:int(d.get("prf", 0))] if sl in g.slug_ix and sc > 0]
    if top:
        share = (tot or 1.0) / sum(sc for _sl, sc in top)
        for sl, sc in top:
            S[g.id.get("mem:" + sl, g.mem_nodes[sl][0]), 0] += sc * share
    if S.sum() <= 0:
        return []
    # 16 walk steps, not the cache's 20 (residual 0.7^16 = 3e-3): the served recall answers were the same, word for
    # word, on all 22 seed questions at 16 and at 14 (4 Nov 2026), the 200-deep graph list the same on
    # 10 of 13 walks with the same memories on all 13; ppr median 175 -> 154 ms. Set here, never in the dial: the
    # iteration count is part of the walk cache's name. BRAIN_PPR_ITERS=20 restores the old walk.
    try:
        iters = int(os.environ.get("BRAIN_PPR_ITERS", "16"))
    except ValueError:
        iters = 16
    R = g.ppr(S, iters=iters)
    return g.ranked_memories(R, gamma=d["gamma"], allowed=allowed, depth=depth, with_scores=True)[0]


_LINKS = {}
LIVE = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "live")
RECALL_LOG = os.path.join(LIVE, "recall_log.jsonl")


def _link_graph():
    """Memory <-> memory links from the live sidecar ([[links]], superseded_by, the E1 typed links), loaded once per
    sidecar generation: {slug: [(slug, weight)]} both directions, and each node's total out-weight.

    Until 30 Sep the cache had one key for the whole process, so a process that switched BRAIN_GRAPH_SIDECAR, or that
    outlived a rebuild of the live sidecar, kept serving the first graph it had read (interactive audit B100-03). recall
    runs one process per question and the evaluations pin the sidecar in fresh processes, so no measurement so far read
    a stale graph; a long-lived reader or two arms in one interpreter would have."""
    import sqlite3
    # BRAIN_GRAPH_SIDECAR pins a copy for a measurement: the live sidecar is rebuilt by the capture every 15 minutes
    path = os.environ.get("BRAIN_GRAPH_SIDECAR", "").strip() or os.path.join(LIVE, "sidecar.sqlite")
    gen = _generation(path)
    if gen in _LINKS:
        return _LINKS[gen]
    nbr, out = {}, {}
    if gen[1] is not None:
        db = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True, timeout=5)
        best = {}
        for s, d, w in db.execute("SELECT src, dst, w FROM link"):
            for a, b in ((s, d), (d, s)):
                if w > best.get((a, b), 0.0):
                    best[(a, b)] = w
        db.close()
        for (a, b), w in best.items():
            nbr.setdefault(a, []).append((b, w))
            out[a] = out.get(a, 0.0) + w
    if _generation(path) != gen:
        # replaced or rewritten while it was read: which generation was read is unknown, so it answers this call only
        return nbr, out
    return _keep(_LINKS, gen, (nbr, out), 4)


class _LazyLinks:
    """The link graph read one memory at a time, only for the memories the push reaches (about 800 of 44,000 on
    2 Nov). Same lists as _link_graph: both directions, the largest positive weight per pair, neighbours in order of
    first appearance by rowid (the full read's scan order). One read transaction, so every list is from one snapshot."""

    def __init__(self, path):
        import sqlite3
        self.db = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True, timeout=5,
                                  isolation_level=None)
        self.db.execute("BEGIN")
        self.nbr, self.out = {}, {}

    def get(self, a):
        lst = self.nbr.get(a)
        if lst is None:
            best = {}
            for s, d, w in self.db.execute("SELECT src, dst, w FROM link WHERE src = ? OR dst = ? ORDER BY rowid",
                                           (a, a)):
                for x, b in ((s, d), (d, s)):
                    if x == a and w > best.get(b, 0.0):
                        best[b] = w
            lst = self.nbr[a] = list(best.items())
            o = 0.0
            for _, w in lst:
                o = o + w
            self.out[a] = o
        return lst

    def prefetch(self, nodes):
        """Read every listed memory's links in one query per 400 (rowid order, so each list's order is unchanged)."""
        miss = [n for n in dict.fromkeys(nodes) if n not in self.nbr]
        for i in range(0, len(miss), 400):
            part = miss[i:i + 400]
            want = set(part)
            best = {n: {} for n in part}
            q = ",".join("?" * len(part))
            for s, d, w in self.db.execute("SELECT src, dst, w FROM link WHERE src IN (%s) OR dst IN (%s) ORDER BY rowid"
                                           % (q, q), part + part):
                for x, b in ((s, d), (d, s)):
                    if x in want:
                        bx = best[x]
                        if w > bx.get(b, 0.0):
                            bx[b] = w
            for n in part:
                lst = self.nbr[n] = list(best[n].items())
                o = 0.0
                for _, w in lst:
                    o = o + w
                self.out[n] = o

    def outw(self, a):
        self.get(a)
        return self.out[a]

    def close(self):
        try:
            self.db.execute("COMMIT")
        except Exception:
            pass
        self.db.close()


# Lever 9 (3 Nov 2026): a long-lived process (brain_recall_server sets this on) keeps the neighbour
# lists the lazy walk has read, keyed by the sidecar's generation (file id, size, write time, SQLite change counter).
# The key is taken AFTER this call's read transaction holds its shared lock, so no writer can commit between the key
# and the reads (the sidecar runs in rollback-journal mode); lists read under one key come from one version of the
# file. A rewrite (the capture's 15-minute refresh) makes a new key and the walk starts empty again. Off by default:
# a one-shot process runs exactly the old code.
LAZY_RESIDENT = False
_LAZY_RES = {}


def _link_ranked_lazy(lex_top, n_seeds, alpha, eps, depth):
    path = os.environ.get("BRAIN_GRAPH_SIDECAR", "").strip() or os.path.join(LIVE, "sidecar.sqlite")
    if not os.path.exists(path):
        return []
    g = _LazyLinks(path)
    gen = None
    if LAZY_RESIDENT:
        try:
            g.db.execute("SELECT rowid FROM link LIMIT 1").fetchall()  # take the shared lock first
            gen = _generation(path)
            if gen[4] is None:   # not a readable SQLite header: no safe key
                gen = None
            elif str(g.db.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal":
                gen = None       # in WAL mode commits move neither the counter nor the file: no safe key
            elif gen in _LAZY_RES:
                g.nbr, g.out = _LAZY_RES[gen]
        except Exception:
            gen = None
    try:
        out = _walk_lazy(g, lex_top, n_seeds, alpha, eps, depth)
        if gen is not None and gen not in _LAZY_RES:
            _keep(_LAZY_RES, gen, (g.nbr, g.out), 1)
        return out
    finally:
        g.close()


def _walk_lazy(g, lex_top, n_seeds, alpha, eps, depth):
    if True:
        g.prefetch([sl for sl, _ in list(lex_top)[:n_seeds]])
        seeds = {sl: max(sc, 0.0) for sl, sc in list(lex_top)[:n_seeds] if g.get(sl)}
        tot = sum(seeds.values())
        if tot <= 0:
            return []
        r = {k: v / tot for k, v in seeds.items()}
        p = {}
        todo = list(r)
        while todo:
            u = todo.pop()
            ru = r.get(u, 0.0)
            o = g.outw(u)
            if ru <= eps * max(o, 1.0):
                continue
            r[u] = 0.0
            if not o:
                p[u] = p.get(u, 0.0) + ru
                continue
            p[u] = p.get(u, 0.0) + alpha * ru
            push = (1 - alpha) * ru / o
            lst = g.get(u)
            g.prefetch([v for v, _ in lst])
            for v, x in lst:
                before = r.get(v, 0.0)
                r[v] = before + push * x
                if before <= eps * max(g.outw(v), 1.0) < r[v]:
                    todo.append(v)
        return sorted(p.items(), key=lambda kv: -kv[1])[:depth]


def link_ranked(lex_top, n_seeds=10, alpha=0.5, eps=1e-4, depth=200):
    if os.environ.get("BRAIN_LINK_LAZY", "1") != "0":
        return _link_ranked_lazy(lex_top, n_seeds, alpha, eps, depth)
    return _link_ranked_full(lex_top, n_seeds, alpha, eps, depth)


def _link_ranked_full(lex_top, n_seeds=10, alpha=0.5, eps=1e-4, depth=200):
    """Personalised PageRank over the memories' own links, restarting on lexical search's top memories weighted by their
    scores (push method). The setting measured best on the held-out bank, 26 Sep: ten seeds, restart 0.5 (hit@5 0.348
    against bm25 0.342 at weight 0.1). [(slug, score)] best first."""
    nbr, out = _link_graph()
    seeds = {sl: max(sc, 0.0) for sl, sc in list(lex_top)[:n_seeds] if sl in nbr}
    tot = sum(seeds.values())
    if tot <= 0:
        return []
    r = {k: v / tot for k, v in seeds.items()}
    p = {}
    todo = list(r)
    while todo:
        u = todo.pop()
        ru = r.get(u, 0.0)
        o = out.get(u, 0.0)
        if ru <= eps * max(o, 1.0):
            continue
        r[u] = 0.0
        if not o:
            p[u] = p.get(u, 0.0) + ru
            continue
        p[u] = p.get(u, 0.0) + alpha * ru
        push = (1 - alpha) * ru / o
        for v, x in nbr[u]:
            before = r.get(v, 0.0)
            r[v] = before + push * x
            if before <= eps * max(out.get(v, 0.0), 1.0) < r[v]:
                todo.append(v)
    return sorted(p.items(), key=lambda kv: -kv[1])[:depth]


def combine(use, links, mix=0.5):
    """One graph list from the use-grown walk and the link walk: min-max each, then (1 - mix) use + mix links.

    A source at weight zero takes no part: at mix 0 the link walk adds no candidates and at mix 1 the use walk adds
    none. A list whose scores are all equal (one item, or one score throughout) normalizes to 1, as fuse_scores does.
    Until 29 Sep both rules differed: an inactive source's candidates joined at score 0, a constant list normalized to
    0, and fuse_scores then gave an all-zero graph list full credit, so adding a zero-weight source changed the winner
    (interactive audit BM-20260929-01). Live recall runs at mix 0.5; the endpoints are the mixture ablations."""
    def norm(xs):
        if not xs:
            return {}
        hi, lo = max(v for _, v in xs), min(v for _, v in xs)
        return {s: ((v - lo) / (hi - lo) if hi > lo else 1.0) for s, v in xs}
    if mix <= 0.0:
        links = []
    elif mix >= 1.0:
        use = []
    u, l = norm(use), norm(links)
    if not u:
        mix = 1.0
    if not l:
        mix = 0.0
    f = {s: (1 - mix) * u.get(s, 0.0) + mix * l.get(s, 0.0) for s in set(u) | set(l)}
    # equal scores in name order: the dict is built over a set, whose order follows string hashes, which Python
    # randomises per process - sorted by score alone, two recalls of one question showed tied memories in either
    # order (27 Sep, the R7 replay control's 2 of 1,632)
    return sorted(f.items(), key=lambda kv: (-kv[1], kv[0]))


def log_recall(query, lam, body, graph, shown, extra=None):
    """One line per recall for graph_learn.py: what bm25 ranked, what the graph ranked, what was shown.
    BRAIN_RECALL_LOG=0 keeps a recall out of the log: evaluations and test banks set it, so the log holds agents' own
    searches only (26 Sep; the learner could not match an evaluation's recall to a session anyway)."""
    if os.environ.get("BRAIN_RECALL_LOG", "1") == "0":
        return
    try:
        import time
        os.makedirs(LIVE, exist_ok=True)
        # BRAIN_RECALL_LOG_FULL=1 (evaluations only) logs the whole body and graph lists: fuse() scales each list by
        # its own minimum, so a replay from the first 30 cannot reproduce a longer list's order (27 Sep, R7 replay)
        # 100 since 29 Sep (30 before): the learner may use a replay only when no item cut from a list could have
        # changed the opened memory's rank (interactive audit G02), and with 30 kept that held for 32-69 percent of the
        # last 500 recalls' top three; "n" below says whether a list was cut at all
        cap = None if os.environ.get("BRAIN_RECALL_LOG_FULL") == "1" else 100
        # ...and at full precision: four places made two near-equal scores (2.3e-5 apart) replay in the other order
        # (27 Sep, R7); the live log keeps four places, which graph_learn replays with the rounded bounds
        nd = None if cap is None else 4
        rnd = (lambda v: float(v)) if nd is None else (lambda v: round(float(v), nd))
        rec = {"t": round(time.time(), 1), "q": str(query)[:200], "lam": lam,
               "body": [[s, rnd(v)] for s, v in body[:cap]],
               "graph": [[s, rnd(v)] for s, v in graph[:cap]], "shown": list(shown)[:10],
               "n": [len(body), len(graph)]}
        # the whole lists' bounds: fuse() scales each list by its own minimum and maximum, so with these four numbers
        # the first 30 of each replay the order recall showed (27 Sep: 0 of 80 top-10s differ, 32 of 80 without)
        if body and graph:
            # rounded as the items are (4 places), so a replay fuses exactly the numbers it reads
            rec["bounds"] = [rnd(min(v for _, v in body)), rnd(max(v for _, v in body)),
                             rnd(min(v for _, v in graph)), rnd(max(v for _, v in graph))]
        # Which session searched, and whether it was a person's session or a headless worker (27 Sep, scheduled audit
        # account01/B05 R6): without it the learner and the evaluations joined a search to a session by a +-90 s
        # overlap of shown slugs, and a headless script's searches could not be told from his sessions'. A Claude Code
        # tool call carries CLAUDE_CODE_SESSION_ID and CLAUDE_CODE_ENTRYPOINT (cli = interactive, sdk-cli = headless).
        _sid = os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CODEX_SESSION_ID")
        if _sid:
            rec["session"] = _sid
        if os.environ.get("CLAUDE_CODE_ENTRYPOINT"):
            rec["entry"] = os.environ["CLAUDE_CODE_ENTRYPOINT"]
        if extra:
            rec.update(extra)
        # BRAIN_RECALL_LOG_PATH sends a measurement's records to its own file (27 Sep, the held-out graph mixture):
        # it needs each question's body and graph lists, and the live log must hold only agents' own searches
        with open(os.environ.get("BRAIN_RECALL_LOG_PATH") or RECALL_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except Exception:
        pass


def fuse_scores(body, graph, lam, how="convex", bounds=None):
    """The fusion itself: [(slug, fused score)], best first, equal scores in name order. Recall (fuse, below) and the
    learner's replay (graph_learn.fuse) both run this one function, so a replay ranks what recall ranked; until 29 Sep
    the learner kept its own copy, which ordered ties by hash and so differently in 8 of 16 processes (interactive audit
    G03). bounds (lo, hi, glo, ghi): each whole list's lowest and highest score, for a replay of lists the recall log
    cut to 30. A list whose scores are all equal (one item, or one score throughout) normalizes to 1, its own top: until
    29 Sep it normalized to 0, and a one-item lexical body gave the order to the graph at lambda 0.1 (account01/B03,
    B03-R7-BODY-SINGLETON-LAMBDA-COLLAPSE-026).
    Two properties of min-max scaling, measured 30 Sep (the 1000-page report's D05-T01 and D05-T05): a list's lowest
    candidate scores 0, the same as a slug the list does not hold, so the graph's weakest pick gets no lift; and one
    extreme score squeezes the rest, one graph score ten times the others leaving them the bottom 6 pct of the range
    (1 of 105 pairs swapped at lambda 0.1, 19 at 0.5; "rrf" swaps none). Live recalls show no such collapse: over 1,412
    logged recalls the second graph candidate keeps a median 0.68 of the range and never falls under 0.2."""
    if bounds:
        lo, hi, glo, ghi = (float(x) for x in bounds)
    else:
        hi, lo = max(b for _, b in body), min(b for _, b in body)
        ghi, glo = max(v for _, v in graph), min(v for _, v in graph)
    bn = {s: ((b - lo) / (hi - lo) if hi > lo else 1.0) for s, b in body}
    gn = {s: ((v - glo) / (ghi - glo) if ghi > glo else 1.0) for s, v in graph}
    if how == "rrf":
        br = {s: r for r, (s, _) in enumerate(body)}
        gr = {s: r for r, (s, _) in enumerate(graph)}
        f = {s: (1 - lam) / (61 + br[s]) if s in br else 0.0 for s in set(br) | set(gr)}
        for s, r in gr.items():
            f[s] = f.get(s, 0.0) + lam / (61 + r)
    else:
        f = {s: (1 - lam) * bn.get(s, 0.0) + lam * gn.get(s, 0.0) for s in set(bn) | set(gn)}
    return sorted(f.items(), key=lambda it: (-it[1], it[0]))      # equal scores in name order, not hash order (27 Sep)


def fuse(body, graph, lam, how="convex"):
    """Blend a scored lexical body [(slug, score)] with the graph's [(slug, score)]; the result keeps the body's
    score range, so displays and floors downstream read as before. lam 0 returns the body unchanged. A body whose
    scores are all equal has no range to keep: its fused order is spread from 0 to that score, the top at it."""
    if lam <= 0 or not body or not graph:
        return body
    hi = max(b for _, b in body)
    lo = min(b for _, b in body)
    fs = fuse_scores(body, graph, lam, how)
    fhi = fs[0][1] or 1.0
    if hi > lo:
        return [(s, lo + (hi - lo) * (v / fhi)) for s, v in fs]
    return [(s, hi * (v / fhi)) for s, v in fs]


if __name__ == "__main__":
    d = dial()
    print("dial: lambda %.2f  store %s  alpha %s gamma %s prf %s fuse %s" % (
        d["lambda"], d["store"] or "(none)", d["alpha"], d["gamma"], d["prf"], d["fuse"]))
    if sys.argv[1:2] == ["--build"]:
        import time
        try:  # a build run from a windowless job: BelowNormal, but out of power throttling (it starves otherwise)
            import ctypes
            import ctypes.wintypes as _w
            import psutil
            psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)

            class _PPTS(ctypes.Structure):
                _fields_ = [("Version", _w.ULONG), ("ControlMask", _w.ULONG), ("StateMask", _w.ULONG)]
            _k = ctypes.WinDLL("kernel32", use_last_error=True)
            _k.OpenProcess.restype = _w.HANDLE
            _k.OpenProcess.argtypes = [_w.DWORD, _w.BOOL, _w.DWORD]
            _k.SetProcessInformation.argtypes = [_w.HANDLE, ctypes.c_int, ctypes.c_void_p, _w.DWORD]
            _st = _PPTS(1, 1, 0)
            _k.SetProcessInformation(_k.OpenProcess(0x0200, False, os.getpid()), 4, ctypes.byref(_st), ctypes.sizeof(_st))
        except Exception:
            pass
        t = time.time()
        g = _graph(d, build=True)
        print("walk ready: %d nodes, %d edges (%.0f s)" % (g.N, g.nnz, time.time() - t) if g is not None
              else "no store at %s" % d["store"])
    else:
        q = " ".join(sys.argv[1:]) or "how do we rotate thumbnail tests on youtube"
        for s, v in ranked(q, d=d)[:10]:
            print("  %8.4g  %s" % (v, s))
