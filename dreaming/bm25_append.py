#!/usr/bin/env python3
"""bm25_append.py - add newly written memories to the BM25 index WITHOUT a full rebuild.

WHY THIS AND NOT A QUERY-TIME FALLBACK. A memory written between rebuilds is in no recall channel
at all (measured 2026-08-17: 9637 eligible, 9625 indexed, up to 16 invisible; the oldest invisible
one was written seven seconds after the build that missed it). I first tried to rescue those at
query time with fresh_tail.py and a relative merge rule, then calibrated it against 299 real owner
prompts and REFUTED IT: safe settings retrieved almost nothing (k=1 gave 0.00% false injection and
1 of 5 analogous-wording true positives), and settings that retrieved paid broadly. The cause is
structural - a fresh document competes against 9,625 indexed ones for the top slots and loses on
analogous wording, which is exactly the phrasing retrieval must handle.

So the fix is to put the document IN the index, where it competes on equal footing. That is cheap:
BM25 needs only per-document term frequencies, a document length, and a corpus-wide document
frequency count. Adding a document is a local update - it does not require re-reading the corpus.

CORRECTNESS IS PROVEN, NOT ASSERTED. --selftest builds a full index over N documents, then builds
one over a subset and appends the rest, and requires the two payloads to be EQUAL: same n, same
avgdl, same df for every term, same tf and dl for every document. If appending ever diverges from
rebuilding, that control fails.

GENERATION SAFETY. Appending changes the index bytes, which moves the observed generation hash that
a peer campaign's receipts bind. This module therefore NEVER writes the live index unless explicitly
pointed at it with --out, and --dry-run reports exactly what would change. Scheduling the live
append is a coordination decision, not this file's to make.

Usage:
    python bm25_append.py --selftest                  correctness controls on fixtures
    python bm25_append.py --dry-run                   what is invisible and what would be added
    python bm25_append.py --out PATH                  write an appended COPY to PATH, live untouched
"""
from __future__ import annotations

import gzip
import io
import json
import os
import sys

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm25  # noqa: E402


def load_payload(path):
    # Read the bytes and close before parsing: on Windows no publish can replace the index while a reader holds it.
    with open(path, "rb") as fh:
        blob = fh.read()
    with gzip.GzipFile(fileobj=io.BytesIO(blob), mode="rb") as gz:
        with io.TextIOWrapper(gz, encoding="utf-8") as f:
            return json.load(f)


def save_payload(payload, path):
    """Atomic, mirroring bm25.build: a reader that opens a half-written gz raises EOFError and the
    lexical channel silently drops out of recall."""
    tmp = "%s.tmp.%d" % (path, os.getpid())
    try:
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def append_docs(payload, docs):
    """Add {slug: token_list} to a payload in place and return how many were added.

    A slug already present is SKIPPED rather than double-counted: adding it again would inflate df
    for every one of its terms and corrupt idf across the whole corpus.
    """
    added = 0
    for slug, tks in docs.items():
        if not tks or slug in payload["dl"]:
            continue
        tf = {}
        for t in tks:
            tf[t] = tf.get(t, 0) + 1
        payload["tf"][slug] = tf
        payload["dl"][slug] = len(tks)
        for t in tf:
            payload["df"][t] = payload["df"].get(t, 0) + 1
        added += 1
    n = len(payload["dl"])
    payload["n"] = n
    payload["avgdl"] = (sum(payload["dl"].values()) / n) if n else 0.0
    return added


def invisible_docs(mem_dir=None, index_path=None):
    """Tokenize exactly the memories the index is missing, using bm25's own reader rules."""
    mem_dir = mem_dir or bm25.MEM
    index_path = index_path or bm25.INDEX
    payload = load_payload(index_path)
    have = set(payload["dl"])
    out = {}
    for fn in os.listdir(mem_dir):
        if not fn.endswith(".md"):
            continue
        slug = fn[:-3]
        if slug in bm25.SKIP_FILES or slug.startswith("dream_") or slug in have:
            continue
        import fresh_tail
        tks = fresh_tail._doc_tokens(mem_dir, slug)
        if tks:
            out[slug] = tks
    return payload, out


def _selftest():
    """Append must be indistinguishable from rebuild. That is the whole claim."""
    import io
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="bm25append_")
    mem = os.path.join(tmp, "memory")
    os.makedirs(mem)
    try:
        texts = {
            "note_turbines": "turbine maintenance schedule blade inspection rotor",
            "note_zeppelins": "zeppelin mooring mast procedure helium venting airship",
            "note_bicycles": "bicycle derailleur adjustment chain wear cassette",
            "note_baking": "sourdough hydration bulk fermentation crumb structure",
            "note_turbines_two": "turbine blade coating erosion inspection interval",
        }
        for slug, t in texts.items():
            with io.open(os.path.join(mem, slug + ".md"), "w", encoding="utf-8", newline="\n") as f:
                f.write("---\nname: %s\n---\n\n%s\n" % (slug, t))

        import fresh_tail
        docs = {s: fresh_tail._doc_tokens(mem, s) for s in texts}

        def build_payload(subset):
            df, tfs, dl = {}, {}, {}
            for slug in subset:
                tf = {}
                for t in docs[slug]:
                    tf[t] = tf.get(t, 0) + 1
                tfs[slug], dl[slug] = tf, len(docs[slug])
                for t in tf:
                    df[t] = df.get(t, 0) + 1
            n = len(subset)
            return {"n": n, "avgdl": (sum(dl.values()) / n) if n else 0.0,
                    "df": df, "dl": dl, "tf": tfs}

        allslugs = sorted(texts)
        first3, rest = allslugs[:3], allslugs[3:]

        full = build_payload(allslugs)
        partial = build_payload(first3)
        added = append_docs(partial, {s: docs[s] for s in rest})

        fails = []
        print("  %-5s appended %d documents (expected %d)"
              % ("PASS" if added == len(rest) else "FAIL", added, len(rest)))
        if added != len(rest):
            fails.append("count")

        for key in ("n", "avgdl"):
            ok = full[key] == partial[key]
            print("  %-5s %s equal after append (%s vs %s)"
                  % ("PASS" if ok else "FAIL", key, full[key], partial[key]))
            if not ok:
                fails.append(key)

        ok = full["df"] == partial["df"]
        print("  %-5s df identical for all %d terms" % ("PASS" if ok else "FAIL", len(full["df"])))
        if not ok:
            fails.append("df")
            diff = {k: (full["df"].get(k), partial["df"].get(k))
                    for k in set(full["df"]) | set(partial["df"])
                    if full["df"].get(k) != partial["df"].get(k)}
            print("        differing terms: %s" % list(diff.items())[:6])

        ok = full["tf"] == partial["tf"] and full["dl"] == partial["dl"]
        print("  %-5s tf and dl identical for every document" % ("PASS" if ok else "FAIL"))
        if not ok:
            fails.append("tf/dl")

        # NEGATIVE CONTROL: appending a document that is already present must change NOTHING.
        before = json.dumps(partial, sort_keys=True)
        again = append_docs(partial, {s: docs[s] for s in rest})
        unchanged = (json.dumps(partial, sort_keys=True) == before)
        print("  %-5s NEGATIVE: re-appending existing docs added %d and left the payload identical"
              % ("PASS" if (again == 0 and unchanged) else "FAIL", again))
        if not (again == 0 and unchanged):
            fails.append("idempotence")

        # NEGATIVE CONTROL: scoring must actually improve for the appended documents.
        idx_partial = bm25.BM25.from_docs({s: docs[s] for s in first3})
        idx_after = bm25.BM25.__new__(bm25.BM25)
        idx_after.n, idx_after.avgdl = partial["n"], partial["avgdl"]
        idx_after.df, idx_after.dl, idx_after.tf = partial["df"], partial["dl"], partial["tf"]
        # The query must target a document that is actually IN `rest`. My first version queried
        # note_baking, which sorted() had placed in first3 - so it tested a document that was
        # already indexed and the control failed for its own reason rather than the code's. Derive
        # the target from `rest` instead of hardcoding a slug next to a hardcoded query.
        target = rest[-1]                                   # note_zeppelins under this fixture
        distinctive = [t for t in docs[target]
                       if sum(1 for s in first3 if t in docs[s]) == 0 and not t.startswith("note")]
        q = " ".join(distinctive[:3])
        pre = dict(idx_partial.score(q))
        post = dict(idx_after.score(q))
        ok = bool(distinctive) and (target not in pre) and (target in post)
        print("  %-5s an APPENDED doc (%s) is unfindable before and findable after (%r)"
              % ("PASS" if ok else "FAIL", target, q))
        if not ok:
            fails.append("retrievability")

        print()
        if fails:
            print("SELFTEST FAILED: %s" % ", ".join(fails))
            return 1
        print("SELFTEST PASSED - append is byte-equivalent to rebuild, idempotent, and retrievable.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        return _selftest()

    payload, docs = invisible_docs()
    print("live index      : %d documents" % payload["n"])
    print("invisible on disk: %d" % len(docs))
    for s in sorted(docs)[:20]:
        print("    %s" % s)

    if "--out" not in args:
        print()
        print("DRY RUN - nothing written. Appending changes the index bytes and therefore the")
        print("observed generation hash that peer receipts bind, so the live write is a scheduling")
        print("decision. Pass --out PATH to write an appended COPY.")
        return 0

    out = args[args.index("--out") + 1]
    if os.path.abspath(out) == os.path.abspath(bm25.INDEX):
        print("REFUSING: --out points at the LIVE index. This tool writes copies; a live append")
        print("must be an explicit, coordinated action, not a flag typo.")
        return 3
    added = append_docs(payload, docs)
    save_payload(payload, out)
    print()
    print("appended %d documents -> %s (%d bytes), live index untouched"
          % (added, out, os.path.getsize(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
