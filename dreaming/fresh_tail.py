#!/usr/bin/env python3
"""fresh_tail.py - retrieve memories written AFTER the last BM25 build.

THE HOLE THIS CLOSES, measured 2026-08-17 04:5x. The lexical index is rebuilt on a ~3h cadence.
A memory written between rebuilds is in NO channel: not lexical (absent from the index), not
semantic (the embedding sidecar is refreshed on the same launcher), and not graph (a brand-new
node has no edges yet). So it is unreachable by every route until the next rebuild.

Measured on the live brain at 04:5x: 9637 eligible on disk, 9625 in the index, 12 invisible. The
oldest invisible memory was written at 04:03:06 - SEVEN SECONDS after the 04:02:59 build - so it
waits the full cadence. A probe carrying a five-minute-old memory's most distinctive keywords
("ascii_lint launcher-088 em-dash launch_one runner fold") did not return it at any rank; the top
hit was its own 2026-07-08 ancestor instead.

That is a real failure of the brain's core loop and it has a name in the corpus already: an agent
writes a lesson, hits the same wall half an hour later, and re-derives it. Several scars on file
are that exact shape.

WHY THIS IS A QUERY-TIME FALLBACK RATHER THAN AN INDEX WRITE. Appending to bm25_index.json.gz
would move the observed generation hash, and a peer campaign's acceptance is currently blocked on
generation stability - three volatile-generation blockers were published on 2026-08-17 because probe
runs kept being invalidated by index changes. This module NEVER writes the index and never rebuilds
it. It reads the invisible tail directly, which is a handful of files, and scores them against the
LIVE index's own corpus statistics so the numbers are on the same scale as the lexical channel.

HONEST LIMITS, because this is a fallback and not a fix for the cadence:
  - It scores with the corpus df/avgdl of the CURRENT index. A fresh document's own terms are not
    in df, so their idf is the maximum the formula allows. That is the correct behaviour for a rare
    term and it does mean a fresh doc can outrank an indexed one on a shared rare term.
  - It reads files on every call. Bounded by the tail size (12 at measurement), not by the corpus.
  - If the index is missing or unreadable it returns UNKNOWN rather than an empty list, because
    "no fresh memories" and "cannot tell" are different answers.
  - IT ALWAYS RETURNS SOMETHING WHEN ANY TOKEN OVERLAPS, so it is NOT safe to merge into recall as
    it stands. Measured 2026-08-17: a deliberately unrelated query ("<ticker> grid flatten at the close
    hedge leg") still returned four rows, top score 3.15, while genuine hits on the same tail scored
    9.9 and 61.1. That ordering is encouraging and it is NOT a calibration - it is two queries.
    Wiring this without a merge rule would raise the irrelevant-context rate on every recall, which
    is one of the outcomes the retrieval contract explicitly measures.
    I PROPOSED A RELATIVE MERGE RULE - surface a fresh document only when it beats the k-th indexed
    result - AND THEN MEASURED IT, AND IT DOES NOT WORK. Calibrated 2026-08-17 against 299 real
    owner prompts sampled from the prompt ledger, with a 16-document tail:
        k=1  false injection 0.00%   analogous-wording true positives 1 of 5
        k=3  0.33%                   1 of 5
        k=5  1.00%                   1 of 5
        k=8  1.00%                   1 of 5
        k=12 -                       2 of 5
        k=20 1.34%                   2 of 5
        k=40 (worse than 1.34%)      3 of 5
    Safe settings retrieve almost nothing; settings that retrieve pay broadly. The cause is
    structural rather than a tuning failure: a fresh memory competes against 9,625 indexed
    documents for the top slots, and on ANALOGOUS wording - the phrasing the contract requires
    retrieval to work on - fresh docs score 7-10 against top indexed scores of 11-15. Only heavy
    keyword overlap (61.1 vs 24.7) clears the bar, and that is the case that needs no help.

    SO THE HONEST CONCLUSION IS THAT QUERY-TIME RESCUE IS THE WRONG SHAPE. The fix for the
    invisibility gap is to get new memories INTO the index - an incremental append of the new
    document's tf and df, so it competes on equal footing in the normal channel - rather than to
    score it separately and try to splice the result in. Generation churn is the objection to
    appending, and that is a scheduling and coordination problem, not evidence that the design is
    wrong. This module remains useful as a DIAGNOSTIC (what is invisible right now, and would a
    given query have found it) and that is the only role it should be given until then.

Usage:
    python fresh_tail.py --selftest              controls, no dependence on live corpus state
    python fresh_tail.py "your query here"       rank the invisible tail against a query
"""
from __future__ import annotations

import gzip
import io
import json
import math
import os
import re
import sys
import time

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm25  # noqa: E402  - reuse the live tokenizer, skip rules and paths

try:
    import node_docs
except Exception:                                    # attachments are optional
    node_docs = None

# TAKE THESE FROM bm25 RATHER THAN RESTATING THEM. I first wrote K1 = 1.5 with an idf denominator
# of df + 1.5, and documented the scores as being on the same scale as the lexical channel. They
# were not: bm25 uses K1 = 1.2 and df + 0.5 (bm25.py line 28 and the idf in score()). A duplicated
# constant is a claim that silently stops being true, so import it and let the shared value move.
K1, B = bm25.K1, bm25.B


class Unknown(Exception):
    """Raised when the tail cannot be determined. Never silently becomes an empty result."""


def _doc_tokens(mem_dir, slug):
    """Tokenize one memory EXACTLY as bm25.read_docs would, so scores stay comparable."""
    path = os.path.join(mem_dir, slug + ".md")
    try:
        with io.open(path, encoding="utf-8", errors="replace") as handle:
            body = handle.read()
    except Exception:
        return []
    body = re.sub(r"^---.*?---",
                  lambda m: re.sub(r"^(name|metadata|node_type|type|originSessionId).*$",
                                   "", m.group(0), flags=re.M),
                  body, count=1, flags=re.S)
    tks = bm25.tokens(slug.replace("_", " ") + " " + body)[:bm25.MAX_DOC_TOKENS]
    if node_docs is not None:
        try:
            extra = node_docs.search_text_for_node(mem_dir, slug)
            if extra:
                tks.extend(bm25.tokens(extra)[:bm25.MAX_ATTACHMENT_TOKENS])
        except Exception:
            pass
    return tks


def index_state(index_path=None):
    """Return (indexed_slugs, payload, index_mtime). Raises Unknown rather than guessing."""
    index_path = index_path or bm25.INDEX
    if not os.path.exists(index_path):
        raise Unknown("index absent: %s" % index_path)
    # Read the bytes and let go of the index before parsing them (on Windows no publish can replace it while this
    # holds it; a streamed parse takes 9-13 s under load, 13 Oct 2026). The mtime is the handle's, so it belongs to
    # the generation actually parsed even if a publish lands in between.
    try:
        with open(index_path, "rb") as fh:
            mtime = os.fstat(fh.fileno()).st_mtime
            blob = fh.read()
        with gzip.GzipFile(fileobj=io.BytesIO(blob), mode="rb") as gz:
            with io.TextIOWrapper(gz, encoding="utf-8") as f:
                payload = json.load(f)
    except Exception as exc:
        raise Unknown("index unreadable (%s: %s)" % (type(exc).__name__, exc))
    return set(payload.get("dl", {})), payload, mtime


def tail_slugs(mem_dir=None, index_path=None):
    """Eligible memories on disk that the live index does not contain."""
    mem_dir = mem_dir or bm25.MEM
    indexed, _, _ = index_state(index_path)
    out = []
    for fn in os.listdir(mem_dir):
        if not fn.endswith(".md"):
            continue
        slug = fn[:-3]
        if slug in bm25.SKIP_FILES or slug.startswith("dream_"):
            continue
        if slug not in indexed:
            out.append(slug)
    return sorted(out)


def rank(query, mem_dir=None, index_path=None, limit=8):
    """Score the invisible tail against `query` using the live index's corpus statistics."""
    mem_dir = mem_dir or bm25.MEM
    indexed, payload, _ = index_state(index_path)
    slugs = tail_slugs(mem_dir, index_path)
    if not slugs:
        return []

    df = payload.get("df", {})
    n = max(1, int(payload.get("n", 1)))
    avgdl = float(payload.get("avgdl", 1.0)) or 1.0
    # sorted(set(...)) for the same two reasons bm25.BM25.score does it: a repeated query term
    # must not be counted twice, and a fixed summation order keeps the last ulp identical across
    # processes (set order is hash-randomized per run). My first draft iterated the raw token list
    # and double-counted repeats.
    #
    # ONE DELIBERATE DIFFERENCE FROM bm25.BM25.score, and it is the whole point of this module:
    # that method keeps only query terms already present in df, so a term appearing for the first
    # time in an unindexed memory is discarded. Dropping exactly those terms would make a fresh
    # memory unfindable by the words that make it new. Here they are kept and scored at df = 0.
    q = sorted(set(bm25.tokens(query)))
    if not q:
        return []

    scored = []
    for slug in slugs:
        tks = _doc_tokens(mem_dir, slug)
        if not tks:
            continue
        tf = {}
        for t in tks:
            tf[t] = tf.get(t, 0) + 1
        dl = len(tks)
        s = 0.0
        for t in q:
            f = tf.get(t, 0)
            if not f:
                continue
            # IDENTICAL to bm25.BM25.score's idf. A term unique to a fresh document has df = 0,
            # which gives (n + 0.5) / 0.5 - large but finite, and correct: a term no indexed
            # memory contains really is maximally discriminative. No extra smoothing is needed,
            # and adding some (my first draft used df + 1.5) breaks comparability with the
            # lexical channel for the sake of a division-by-zero that cannot happen.
            idf = math.log(1.0 + (n - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))
            s += idf * (f * (K1 + 1.0)) / (f + K1 * (1.0 - B + B * dl / avgdl))
        if s > 0:
            scored.append((s, slug))
    scored.sort(reverse=True)
    return scored[:limit]


# --------------------------------------------------------------------------- controls
def _selftest():
    """Controls run against a FIXTURE corpus, so they do not depend on live tail contents."""
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="freshtail_")
    mem = os.path.join(tmp, "memory")
    os.makedirs(mem)
    try:
        def write(slug, text):
            with io.open(os.path.join(mem, slug + ".md"), "w", encoding="utf-8", newline="\n") as f:
                f.write("---\nname: %s\ndescription: %s\n---\n\n%s\n" % (slug, text[:60], text))

        write("indexed_note_about_turbines", "turbine maintenance schedule and blade inspection")
        write("fresh_note_about_zeppelins", "zeppelin mooring mast procedure and helium venting")
        write("fresh_note_about_bicycles", "bicycle derailleur adjustment and chain wear")

        # Build an index containing ONLY the first note, so the other two are the invisible tail.
        idx = os.path.join(tmp, "idx.json.gz")
        docs = {"indexed_note_about_turbines": _doc_tokens(mem, "indexed_note_about_turbines")}
        df, tfs, dl = {}, {}, {}
        for slug, tks in docs.items():
            tf = {}
            for t in tks:
                tf[t] = tf.get(t, 0) + 1
            tfs[slug], dl[slug] = tf, len(tks)
            for t in tf:
                df[t] = df.get(t, 0) + 1
        payload = {"n": len(docs), "avgdl": sum(dl.values()) / len(docs), "df": df, "dl": dl, "tf": tfs}
        with gzip.open(idx, "wt", encoding="utf-8") as f:
            json.dump(payload, f)

        fails = []

        tail = tail_slugs(mem, idx)
        ok = tail == ["fresh_note_about_bicycles", "fresh_note_about_zeppelins"]
        print("  %-5s tail is exactly the two unindexed notes: %s" % ("PASS" if ok else "FAIL", tail))
        if not ok:
            fails.append("tail contents")

        r = rank("zeppelin mooring mast", mem, idx)
        ok = bool(r) and r[0][1] == "fresh_note_about_zeppelins"
        print("  %-5s POSITIVE: an invisible memory is retrieved   -> %s"
              % ("PASS" if ok else "FAIL", r[:1]))
        if not ok:
            fails.append("positive retrieval")

        r = rank("turbine blade inspection", mem, idx)
        ok = (r == [])
        print("  %-5s NEGATIVE: a query about the INDEXED note pulls nothing from the tail -> %s"
              % ("PASS" if ok else "FAIL", r))
        if not ok:
            fails.append("indexed-topic query leaked")

        r = rank("submarine periscope calibration", mem, idx)
        ok = (r == [])
        print("  %-5s NEGATIVE: an unrelated query returns nothing -> %s"
              % ("PASS" if ok else "FAIL", r))
        if not ok:
            fails.append("unrelated query leaked")

        # A tail with nothing in it must be empty, not an error.
        docs2 = {s: _doc_tokens(mem, s) for s in
                 ("indexed_note_about_turbines", "fresh_note_about_zeppelins", "fresh_note_about_bicycles")}
        df, tfs, dl = {}, {}, {}
        for slug, tks in docs2.items():
            tf = {}
            for t in tks:
                tf[t] = tf.get(t, 0) + 1
            tfs[slug], dl[slug] = tf, len(tks)
            for t in tf:
                df[t] = df.get(t, 0) + 1
        full = os.path.join(tmp, "full.json.gz")
        with gzip.open(full, "wt", encoding="utf-8") as f:
            json.dump({"n": 3, "avgdl": sum(dl.values()) / 3.0, "df": df, "dl": dl, "tf": tfs}, f)
        ok = (tail_slugs(mem, full) == [] and rank("zeppelin mooring mast", mem, full) == [])
        print("  %-5s NEGATIVE: nothing invisible -> empty, and no false hit" % ("PASS" if ok else "FAIL"))
        if not ok:
            fails.append("full index still produced a tail")

        # A missing index must be UNKNOWN, never a quiet empty tail.
        try:
            tail_slugs(mem, os.path.join(tmp, "does_not_exist.json.gz"))
            print("  FAIL  UNKNOWN: a missing index returned instead of raising")
            fails.append("missing index did not raise")
        except Unknown as exc:
            print("  PASS  UNKNOWN: a missing index raises rather than reporting an empty tail (%s)"
                  % str(exc)[:48])

        # EQUIVALENCE CONTROL. Holding corpus statistics fixed and using only query terms that are
        # already in df, this module's arithmetic must reproduce bm25.BM25.score EXACTLY. This is
        # what licenses the claim that the scores merge on the lexical channel's scale; without it
        # that claim is a comment. (It caught a real defect: K1 = 1.5 and an idf denominator of
        # df + 1.5, both wrong, both invisible until the numbers were compared.)
        idx_all = bm25.BM25.from_docs(docs2)
        probe = "turbine blade inspection"
        theirs = dict(idx_all.score(probe))
        full_payload = {"n": idx_all.n, "avgdl": idx_all.avgdl,
                        "df": idx_all.df, "dl": idx_all.dl, "tf": idx_all.tf}
        tokens_q = sorted(set(bm25.tokens(probe)))
        mine = 0.0
        slug = "indexed_note_about_turbines"
        tf_s = idx_all.tf[slug]
        for t in tokens_q:
            f = tf_s.get(t, 0)
            if not f:
                continue
            d = full_payload["df"].get(t, 0)
            idf = math.log(1.0 + (full_payload["n"] - d + 0.5) / (d + 0.5))
            mine += idf * (f * (K1 + 1.0)) / (f + K1 * (1.0 - B + B * idx_all.dl[slug] / idx_all.avgdl))
        delta = abs(mine - theirs.get(slug, 0.0))
        ok = delta < 1e-12
        print("  %-5s EQUIVALENCE: my arithmetic == bm25.BM25.score (delta %.3e)"
              % ("PASS" if ok else "FAIL", delta))
        if not ok:
            fails.append("scoring diverges from bm25")

        print()
        if fails:
            print("SELFTEST FAILED: %s" % ", ".join(fails))
            return 1
        print("SELFTEST PASSED - 7 controls, 4 negative, 1 equivalence against bm25 itself.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    args = [a for a in sys.argv[1:]]
    if "--selftest" in args:
        return _selftest()
    if not args:
        print(__doc__)
        return 0
    query = " ".join(args)
    try:
        indexed, _, mtime = index_state()
        slugs = tail_slugs()
    except Unknown as exc:
        print("UNKNOWN: %s" % exc)
        return 2
    print("index built %s | %d indexed | %d invisible to every channel"
          % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime)), len(indexed), len(slugs)))
    r = rank(query)
    if not r:
        print("no invisible memory matches this query.")
        return 0
    print()
    for s, slug in r:
        print("  %6.3f  %s" % (s, slug))
    return 0


if __name__ == "__main__":
    sys.exit(main())
