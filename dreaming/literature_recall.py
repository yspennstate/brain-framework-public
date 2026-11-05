#!/usr/bin/env python3
"""lit_recall.py - ask the harvested literature a question (BM25 over title + abstract + body).

  python lit_recall.py "hippocampal replay consolidation prioritized"            # top 8
  python lit_recall.py "spreading activation fan effect ACT-R" --n 12 --domain mathematics
  python lit_recall.py --stats

Returns ranked papers with id, year, domain, title and a snippet of the abstract, plus the path of
the extracted text so the caller can read the paper. Scores are BM25 (k1 = 1.2, b = 0.75) over the
index built by extract.py (title + abstract weighted 3x, body 1x). This is the literature lane the
brain consults; it is deliberately separate from the memory corpus so a paper never masquerades as
a lived memory.
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import math
import os
import re
import sys

ROOT = os.environ.get("BRAIN_LITERATURE_DIR") or os.path.join(os.path.expanduser("~"), ".brain", "literature")
BM25_PATH = os.path.join(ROOT, "lit_bm25.json.gz")
LIT_INDEX = os.path.join(ROOT, "lit_index.jsonl")
TOKEN = re.compile(r"[a-z][a-z0-9\-]{2,}")
STOP = set("the and for that with this from are was were which their have has been not but also our these than "
           "can into such where when then more between both each other into over under about after before while "
           "using used use one two three its they them there here how what who all any may might would could should "
           "however therefore thus hence within without upon per via et al fig figure table section results".split())


def tokens(s):
    return [t for t in TOKEN.findall((s or "").lower()) if t not in STOP]


def load():
    with gzip.open(BM25_PATH, "rt", encoding="utf-8") as f:
        idx = json.load(f)
    meta = {}
    with io.open(LIT_INDEX, encoding="utf-8") as f:
        for ln in f:
            try:
                r = json.loads(ln)
                meta[r["id"]] = r
            except Exception:
                pass
    return idx, meta


def search(query, n=8, domain=None, idx=None, meta=None):
    if idx is None:
        idx, meta = load()
    q = tokens(query)
    N, avgdl, df = idx["n"], idx["avgdl"], idx["df"]
    k1, b = 1.2, 0.75
    out = []
    for d in idx["docs"]:
        m = meta.get(d["id"], {})
        if domain and m.get("domain") != domain:
            continue
        tf = d["tf"]; dl = d["dl"]
        s = 0.0
        for t in q:
            f = tf.get(t)
            if not f:
                continue
            idf = math.log(1.0 + (N - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))
            s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / avgdl))
        if s > 0:
            out.append((s, d["id"]))
    out.sort(reverse=True)
    res = []
    for s, pid in out[:n]:
        m = meta.get(pid, {})
        res.append({"score": round(s, 3), "id": pid, "year": m.get("year"), "domain": m.get("domain"),
                    "title": m.get("title"), "abstract": (m.get("abstract") or "")[:300], "text_path": m.get("text_path"),
                    "arxiv_id": m.get("arxiv_id")})
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", default=None)
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--domain", default=None)
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    idx, meta = load()
    if a.stats or not a.query:
        by = {}
        for m in meta.values():
            by[m.get("domain")] = by.get(m.get("domain"), 0) + 1
        print("papers indexed: %d  vocabulary: %d  by domain: %s  built: %s" % (idx["n"], len(idx["df"]), by, idx.get("built")))
        return 0
    res = search(a.query, a.n, a.domain, idx, meta)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    print("lit_recall: %r  (%d papers)" % (a.query, idx["n"]))
    for r in res:
        print("  %6.2f  %s  [%s %s]  %s" % (r["score"], r["id"], r["year"], r["domain"], (r["title"] or "")[:90]))
        print("          %s" % r["abstract"][:200].replace("\n", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
