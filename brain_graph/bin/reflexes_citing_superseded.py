"""reflexes_citing_superseded.py - which compiled reflexes cite a memory that has since been superseded? (28 Sep 10:0x,
claude-825b1305)

A reflex is a copy of an order's wording, compiled once; the order can move after it (the GPU policy moved four times in
two weeks and the reflex spoke a step behind - scar-fire study, 28 Sep). This lists every compiled reflex whose source,
gut or sting names a memory slug whose file carries superseded_by, with the chain's end, so each one can be read against
the order in force. It changes nothing.

    python reflexes_citing_superseded.py OUT_JSON
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import io
import json
import os
import re
import sys

DR = os.path.join(os.path.expanduser("~"), ".claude", "dreaming")
MEM = os.path.join(os.path.expanduser("~"), ".claude", "projects", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")), "memory")
SLUG = re.compile(r"\b([a-z][a-z0-9_]{12,}_20\d\d_\d\d_\d\d)\b")
SUP = re.compile(r"^\s*superseded_by:\s*['\"]?([^'\"\n]+)", re.M)


def superseded_by(slug):
    p = os.path.join(MEM, slug + ".md")
    try:
        head = io.open(p, encoding="utf-8", errors="replace").read(4000)
    except OSError:
        return None
    m = SUP.search(head)
    return m.group(1).strip() if m else None


def chain_end(slug, seen=None):
    seen = seen or set()
    nxt = superseded_by(slug)
    if not nxt or slug in seen:
        return slug
    seen.add(slug)
    first = re.split(r"[,\s]+", nxt)[0]
    return chain_end(first, seen) if first else slug


def main():
    rows = []
    with io.open(os.path.join(DR, "instincts.tsv"), encoding="utf-8") as fh:
        next(fh, None)
        for ln in fh:
            p = ln.rstrip("\n").split("\t")
            if len(p) < 7:
                continue
            rid, gut, sting, source = p[0], p[4], p[5], p[6]
            cited = {}
            for field, text in (("source", source), ("gut", gut), ("sting", sting)):
                for s in SLUG.findall(text):
                    cited.setdefault(s, field)
            stale = []
            for s, field in cited.items():
                if superseded_by(s):
                    stale.append({"slug": s, "field": field, "superseded_by": superseded_by(s), "chain_end": chain_end(s),
                                  "end_also_cited": chain_end(s) in cited})
            if stale:
                rows.append({"reflex": rid, "gut": gut[:200], "stale_citations": stale})
    in_source = [r for r in rows if any(c["field"] == "source" for c in r["stale_citations"])]
    unresolved = [r for r in rows if any(not c["end_also_cited"] for c in r["stale_citations"])]
    res = {"reflexes_with_a_superseded_citation": len(rows), "of_them_in_source": len(in_source),
           "citing_a_superseded_memory_without_its_successor": len(unresolved), "rows": rows}
    json.dump(res, io.open(sys.argv[1], "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}))
    for r in unresolved[:25]:
        c = [x for x in r["stale_citations"] if not x["end_also_cited"]][0]
        print("  %-48s %-7s %s -> %s" % (r["reflex"][:48], c["field"], c["slug"][:60], c["chain_end"][:60]))


if __name__ == "__main__":
    main()
