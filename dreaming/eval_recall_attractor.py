#!/usr/bin/env python3
"""eval_recall_attractor.py - does one node sit on top of every deliberate recall?

A recall lane that scores a document by how many query words it contains, with no IDF and no
length normalisation, lets one long attached document mention every common word once and sit at
rank 1 on every query that uses them. This instrument measures that, read-only, over the memory
folder brain.py is pointed at (BRAIN_MEM_DIR):

  1. ATTRACTOR  over a set of queries, the share of queries whose rank-1 slug is the single most
                common rank-1 slug, and the number of distinct rank-1 slugs. A healthy recall
                spreads its top hits.
  2. POSITIVE   (query -> expected slug) pairs: hit@1 and hit@8. A repair of the lane must not
                lower them.
  3. NEGATIVE   a nonsense query must not put any memory on top with a strong score.

    python eval_recall_attractor.py [--queries FILE] [--pairs FILE.tsv] [--n 40] [--max-share 0.5] [--out receipt.json]

--queries holds one query per line; without it the queries are the memories' own descriptions
(each memory asked about itself, a sample of --n). --pairs holds "query<TAB>slug" lines. The exit
code is 0 when the attractor share is at most --max-share and no negative query scores at or above
--neg-floor, 1 otherwise.
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import io
import json
import os
import random
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RANK_RE = re.compile(r"^\s+(\d+\.\d\d)\s+\[([^\]]*)\]\s+(\S+)")
NEGATIVE = ["purple giraffe quantum toaster zebra", "xylophone marmalade tectonic sonnet"]


def brain_recall(brain=None):
    """A recall function over brain.py: query -> [(score, flags, slug)] in rank order."""
    if brain is None:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import brain  # noqa: F811

    def run(query, n=8):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                brain.recall(query, n=n)
            except SystemExit:
                pass
        out = []
        for ln in buf.getvalue().splitlines():
            if ln.startswith("--- AUTO-READ") or ln.startswith("read one WITH"):
                break
            m = RANK_RE.match(ln)
            if m:
                out.append((float(m.group(1)), m.group(2), m.group(3)))
        return out
    return run


def description_queries(mem, n, seed=7):
    """Each memory's description as a query, deduplicated, a seeded sample of n."""
    rows, seen = [], set()
    for name in sorted(os.listdir(mem)):
        if not name.endswith(".md") or name.upper() == name:
            continue
        try:
            with io.open(os.path.join(mem, name), encoding="utf-8", errors="replace") as fh:
                head = fh.read(4000)
        except OSError:
            continue
        m = re.search(r"^description:\s*(.+)$", head, re.M)
        if not m:
            continue
        q = re.sub(r"^\[[A-Z-]+\]\s*", "", m.group(1).strip().strip('"'))
        if len(q) < 12 or q.lower() in seen:
            continue
        seen.add(q.lower())
        rows.append(q)
    random.Random(seed).shuffle(rows)
    return rows[:n]


def read_lines(path):
    with io.open(path, encoding="utf-8") as fh:
        return [ln.rstrip("\n") for ln in fh if ln.strip() and not ln.startswith("#")]


def measure(recall, queries, pairs=(), negatives=NEGATIVE):
    top1, per_query = [], []
    for q in queries:
        r = recall(q)
        top1.append(r[0][2] if r else None)
        per_query.append({"q": q[:100], "top": [s for _, _, s in r[:3]]})
    c = collections.Counter(t for t in top1 if t)
    most, count = c.most_common(1)[0] if c else (None, 0)
    h1 = h8 = 0
    pos_rows = []
    for q, want in pairs:
        slugs = [s for _, _, s in recall(q)]
        h1 += int(bool(slugs) and slugs[0] == want)
        h8 += int(want in slugs[:8])
        pos_rows.append({"q": q, "want": want, "rank": slugs.index(want) + 1 if want in slugs else None,
                         "top": slugs[:3]})
    neg_rows = [{"q": q, "top": recall(q)[:3]} for q in negatives]
    return {"n_queries": len(queries), "attractor_top1_slug": most,
            "attractor_top1_share": round(count / max(1, len(queries)), 3), "distinct_top1": len(c),
            "positive_hit1": h1, "positive_hit8": h8, "positive_n": len(pairs),
            "neg_top_score": max([r["top"][0][0] for r in neg_rows if r["top"]] or [0.0]),
            "positive_rows": pos_rows, "negative_rows": neg_rows, "per_query": per_query}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--queries")
    ap.add_argument("--pairs")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--max-share", type=float, default=0.5)
    ap.add_argument("--neg-floor", type=float, default=1.0)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    t0 = time.time()
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import brain
    qs = read_lines(a.queries)[:a.n] if a.queries else description_queries(brain.MEM, a.n)
    pairs = [tuple(ln.split("\t", 1)) for ln in read_lines(a.pairs)] if a.pairs else []
    rec = measure(brain_recall(brain), qs, pairs)
    rec["seconds"] = round(time.time() - t0, 1)
    rec["ok"] = rec["attractor_top1_share"] <= a.max_share and rec["neg_top_score"] < a.neg_floor
    print(json.dumps({k: v for k, v in rec.items() if k not in ("per_query", "positive_rows", "negative_rows")},
                     indent=1))
    misses = [(r["q"], r["rank"]) for r in rec["positive_rows"] if r["rank"] != 1]
    if misses:
        print("positive misses:", misses)
    if a.out:
        with io.open(a.out, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, indent=1, ensure_ascii=False)
    return 0 if rec["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
