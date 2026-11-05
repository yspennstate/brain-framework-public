#!/usr/bin/env python3
"""eval_bm25_postings.py - is the term-major sidecar bit-for-bit the same ranker as score()?

The sidecar (bm25.build_postings / bm25.score_postings) exists only to make scoring cheap: a hook is a
fresh process, and the document-major payload costs a 35 MB pickle load and a sweep over every document
to answer one query. It is worth nothing if it answers differently, so this is the gate it has to pass
before the dial is flipped: over a large, varied query battery, `score_postings` must return the SAME
slugs in the SAME order with the SAME floats as `BM25().score`.

The battery is built from the corpus itself rather than hand-written, so it cannot be tuned to pass:
every memory contributes its own name as a query (the shape recall uses), plus his real prompts from the
archive, plus adversarial shapes - single terms, very common terms, terms that appear in one memory,
numbers, punctuation, queries with no indexable token at all.

    python eval_bm25_postings.py [--queries N] [--topk K]

Exit 0 only when every query matches exactly; any mismatch prints the query and the first differing row.
"""
from __future__ import annotations

import io
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm25  # noqa: E402

MEM = bm25.MEM


def battery(n_slug=1500, n_prompt=600, seed=20260914):
    rnd = random.Random(seed)
    qs = []
    try:
        names = [f[:-3] for f in os.listdir(MEM) if f.endswith(".md")]
        for s in rnd.sample(names, min(n_slug, len(names))):
            qs.append(" ".join(w for w in s.split("_") if not w.isdigit()))
    except Exception:
        pass
    p = os.path.join(MEM, "prompts", "PROMPTS.jsonl")
    try:
        with io.open(p, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        for ln in rnd.sample(lines, min(n_prompt, len(lines))):
            try:
                d = json.loads(ln)
                t = str(d.get("prompt") or d.get("text") or "")[:600]
            except Exception:
                t = ln[:600]
            if t.strip():
                qs.append(t)
    except Exception:
        pass
    # adversarial shapes: the cases where a reimplementation usually diverges
    z = bm25._load_payload(bm25.INDEX)
    df = z["df"]
    common = sorted(df, key=lambda t: -df[t])[:25]
    rare = [t for t in df if df[t] == 1][:25]
    qs += common + rare
    qs += [t + " " + u for t, u in zip(common[:12], rare[:12])]
    qs += ["", "   ", "!!!", "a", "zz", "0.45", "v2-B", "<remote-box> lane",
           "qqqqqqq nonexistent token here", "the and for with", "1s 2x iv mv dd",
           "ratio 0.25 step 0.0035 width 0.12", "WHAT DID THE NIGHTLY BACKUP DO WRONG"]
    return qs


def main():
    topk = 40
    if "--topk" in sys.argv:
        topk = int(sys.argv[sys.argv.index("--topk") + 1])
    qs = battery()
    if "--queries" in sys.argv:
        qs = qs[: int(sys.argv[sys.argv.index("--queries") + 1])]
    got = bm25._postings_conn(bm25.INDEX)
    if got is None:
        print("FAIL: no usable postings sidecar for this index generation - run bm25.build_postings() first")
        return 2
    # The reference arm must be the DOCUMENT-MAJOR PAYLOAD path, whatever the dial says. Once
    # BRAIN_BM25_POSTINGS defaulted on, a plain BM25() routed its score() at the sidecar too and this
    # gate compared the sidecar with itself: it printed PASS at 1.0x where it had been 2.9x, and it
    # could no longer fail on the only axis it exists to test. Pin the flag off for the reference.
    bm25._POSTINGS_ON = False
    bm = bm25.BM25(bm25.INDEX)
    if bm._sc is not None:
        print("FAIL: the reference arm is not the payload path - this gate cannot fail; fix it before believing it")
        return 2
    bad = 0
    t_payload = t_post = 0.0
    for q in qs:
        t0 = time.time()
        a = bm.score(q, topk)
        t_payload += time.time() - t0
        t0 = time.time()
        b = bm25.score_postings(q, topk, bm25.INDEX, conn=got)
        t_post += time.time() - t0
        if b is None:
            print("FAIL: sidecar declined to answer %r" % q[:70])
            bad += 1
            continue
        if len(a) != len(b) or any(x[0] != y[0] or x[1] != y[1] for x, y in zip(a, b)):
            bad += 1
            if bad <= 5:
                print("MISMATCH on %r" % q[:70])
                for i, (x, y) in enumerate(zip(a, b)):
                    if x[0] != y[0] or x[1] != y[1]:
                        print("   row %d: payload %s %.17g | sidecar %s %.17g" % (i, x[0][:44], x[1], y[0][:44], y[1]))
                        break
                if len(a) != len(b):
                    print("   lengths differ: payload %d, sidecar %d" % (len(a), len(b)))
    try:
        got[0].close()
    except Exception:
        pass
    print("queries %d | exact matches %d | MISMATCHES %d" % (len(qs), len(qs) - bad, bad))
    print("scoring time over the battery: payload path %.1f s, sidecar %.1f s (%.1fx)"
          % (t_payload, t_post, (t_payload / t_post) if t_post else 0.0))
    print("GATE: %s" % ("PASS - the sidecar is the same ranker" if bad == 0 else "FAIL - do not enable"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
