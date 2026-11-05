#!/usr/bin/env python3
"""Extract text from the harvested PDFs and build the literature index the brain can consult.

For every manifest row with a PDF on disk:
  text/<domain>/<id>.txt      full text (PyMuPDF), first 400 KB, UTF-8
  lit_index.jsonl             one row per paper: id, domain, title, year, authors, abstract,
                              n_pages, n_chars, sha256, first_section_heads
Then a BM25 index over title + abstract + body (own tokenizer, no external package) is written
to lit_bm25.json.gz for `lit_recall.py`. Resumable: papers already extracted (same sha256) skip.

Usage:
  python extract.py            # extract everything new, rebuild the index
  python extract.py --index    # rebuild the index only
"""
from __future__ import annotations

import argparse
import collections
import gzip
import io
import json
import math
import os
import re
import sys
import time

ROOT = os.environ.get("BRAIN_LITERATURE_DIR") or os.path.join(os.path.expanduser("~"), ".brain", "literature")
MANIFEST = os.path.join(ROOT, "manifest.jsonl")
TEXT_DIR = os.path.join(ROOT, "text")
LIT_INDEX = os.path.join(ROOT, "lit_index.jsonl")
BM25_PATH = os.path.join(ROOT, "lit_bm25.json.gz")
MAX_CHARS = 400_000
TOKEN = re.compile(r"[a-z][a-z0-9\-]{2,}")
STOP = set("the and for that with this from are was were which their have has been not but also our these than "
           "can into such where when then more between both each other into over under about after before while "
           "using used use one two three its they them there here how what who all any may might would could should "
           "however therefore thus hence within without upon per via et al fig figure table section results".split())


def tokens(s: str) -> list[str]:
    return [t for t in TOKEN.findall((s or "").lower()) if t not in STOP]


def load_manifest():
    rows = {}
    if os.path.exists(MANIFEST):
        with io.open(MANIFEST, encoding="utf-8") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                    rows[r["id"]] = r
                except Exception:
                    pass
    return rows


def extract_pdf(path: str) -> tuple[str, int, list[str]]:
    import fitz  # PyMuPDF
    doc = fitz.open(path)
    parts = []
    n = 0
    heads = []
    for page in doc:
        n += 1
        t = page.get_text("text")
        parts.append(t)
        if sum(len(x) for x in parts) > MAX_CHARS:
            break
    text = "\n".join(parts)[:MAX_CHARS]
    for m in re.finditer(r"^\s*(\d{1,2}(?:\.\d)?\s+[A-Z][A-Za-z ,\-]{3,60})\s*$", text, re.M):
        heads.append(m.group(1).strip())
        if len(heads) >= 25:
            break
    return text, n, heads


def build_index(rows_out):
    """BM25 over title + abstract (weight 3) + body (weight 1), stored compactly."""
    df = collections.Counter()
    docs = []
    for r in rows_out:
        body_path = r.get("text_path")
        body = ""
        if body_path and os.path.exists(body_path):
            body = io.open(body_path, encoding="utf-8", errors="replace").read()
        tf = collections.Counter()
        for t in tokens(r.get("title", "") + " " + r.get("abstract", "")):
            tf[t] += 3
        for t in tokens(body[:150_000]):
            tf[t] += 1
        for t in tf:
            df[t] += 1
        docs.append({"id": r["id"], "dl": sum(tf.values()), "tf": dict(tf)})
    n = len(docs)
    avgdl = (sum(d["dl"] for d in docs) / n) if n else 1.0
    with gzip.open(BM25_PATH, "wt", encoding="utf-8") as f:
        json.dump({"n": n, "avgdl": avgdl, "df": dict(df), "docs": docs, "built": time.strftime("%Y-%m-%dT%H:%M:%S")}, f)
    return n, len(df)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", action="store_true")
    args = ap.parse_args()
    rows = load_manifest()
    done = {}
    if os.path.exists(LIT_INDEX):
        with io.open(LIT_INDEX, encoding="utf-8") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                    done[r["id"]] = r
                except Exception:
                    pass
    out = dict(done)
    if not args.index:
        n_new = 0
        for pid, r in rows.items():
            if r.get("status") not in ("downloaded", "have") or not r.get("pdf_path"):
                continue
            if pid in done and done[pid].get("sha256") == r.get("sha256"):
                continue
            d = os.path.join(TEXT_DIR, r["domain"])
            os.makedirs(d, exist_ok=True)
            tp = os.path.join(d, re.sub(r"[^A-Za-z0-9_.-]", "_", pid) + ".txt")
            try:
                text, npages, heads = extract_pdf(r["pdf_path"])
            except Exception as exc:
                out[pid] = {"id": pid, "domain": r["domain"], "title": r.get("title"), "status": "extract_failed",
                            "error": "%s: %s" % (type(exc).__name__, str(exc)[:120]), "sha256": r.get("sha256")}
                continue
            with io.open(tp, "w", encoding="utf-8") as f:
                f.write(text)
            out[pid] = {"id": pid, "domain": r["domain"], "title": r.get("title"), "year": r.get("year"),
                        "authors": r.get("authors"), "abstract": r.get("abstract"), "arxiv_id": r.get("arxiv_id"),
                        "n_pages": npages, "n_chars": len(text), "sha256": r.get("sha256"), "text_path": tp,
                        "section_heads": heads, "query": r.get("query"), "status": "extracted"}
            n_new += 1
            if n_new % 50 == 0:
                print("extracted", n_new, flush=True)
        with io.open(LIT_INDEX, "w", encoding="utf-8") as f:
            for r in out.values():
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print("extracted %d new; index rows %d" % (n_new, len(out)))
    ok = [r for r in out.values() if r.get("status") == "extracted"]
    n, v = build_index(ok)
    print("bm25: %d docs, %d vocabulary -> %s" % (n, v, BM25_PATH))
    return 0


if __name__ == "__main__":
    sys.exit(main())
