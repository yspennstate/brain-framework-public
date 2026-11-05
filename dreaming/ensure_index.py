#!/usr/bin/env python3
"""ensure_index.py - close the standing index-completeness gap (OQ #1173 calibration note).

A fifth of the memory corpus (377/1908 on 2026-07-15; 26% when first measured 07-11) sits
in NEITHER MEMORY.md NOR INDEX_ARCHIVE.md - mostly old bulk runs written without a
brain.py index line. They stay recallable (bm25/semantic/graph index by CONTENT), so the
cost is wake-coldness plus one lost seed signal - but the gap is standing and grows a
little whenever any writer skips the index discipline. This pass appends one archive line
per un-indexed memory, in INDEX_ARCHIVE's own format, description pulled from frontmatter.

Append-only by design (no read-modify-write surgery on the archive body), under
brain.py's cross-process _memory_write_lock so it can never race a concurrent `remember`
or the dream's compact_index. Nightly wiring keeps the gap closed at ~zero marginal cost
(the scan is a directory listing + two containment checks).

Usage:
  python ensure_index.py           # dry run: report the gap + a sample
  python ensure_index.py --write   # append the missing lines
"""
import io
import os
import re
import sys
import time

DR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DR)
import brain as B
from index_text import shorten_index_text

MEM = B.MEM
ARCHIVE = os.path.join(MEM, "INDEX_ARCHIVE.md")
INDEX = os.path.join(MEM, "MEMORY.md")

# view/system pages that never get index lines (superset of the embeddings SKIP set)
VIEWS = {
    "MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "OPEN_QUESTIONS", "MISTAKES", "AUTOBIOGRAPHY",
    "MODEL_OF_PARTNER", "CONSCIENCE", "STATE_OF_MIND", "FASCINATIONS", "THE_OTHER_MINDS",
    "SALIENCE", "CURIOSITY", "GROWTH", "RECENT_BRAIN", "STATS", "RECONSOLIDATION_QUEUE",
    "INTENTIONS", "OPEN_LOOPS",
}

_DESC = re.compile(r"^\s*description:\s*[\"']?(.*?)[\"']?\s*$", re.M)


def desc_of(path):
    try:
        head = io.open(path, encoding="utf-8", errors="replace").read(4000)
    except Exception:
        return ""
    m = _DESC.search(head)
    if m and m.group(1).strip():
        return m.group(1).strip()
    body = re.sub(r"^---.*?---\s*", "", head, count=1, flags=re.S)
    line = next((l.strip("# ").strip() for l in body.splitlines() if l.strip()), "")
    return line


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    write = "--write" in sys.argv[1:]
    idx = io.open(INDEX, encoding="utf-8", errors="replace").read()
    arc = io.open(ARCHIVE, encoding="utf-8", errors="replace").read()
    both = idx + arc
    missing = []
    for fn in sorted(os.listdir(MEM)):
        if not fn.endswith(".md") or fn[:-3] in VIEWS:
            continue
        if fn in both:
            continue
        missing.append(fn)
    print("ensure_index: %d un-indexed memory file(s)" % len(missing))
    if not missing:
        return 0
    if not write:
        for fn in missing[:5]:
            print("  e.g. %s - %s" % (fn, desc_of(os.path.join(MEM, fn))[:80]))
        print("(dry run - pass --write to append archive lines)")
        return 0
    lines = ["", "<!-- ensure_index backfill %s: %d un-indexed memories given archive lines -->"
             % (time.strftime("%Y-%m-%d"), len(missing))]
    for fn in missing:
        d = shorten_index_text(desc_of(os.path.join(MEM, fn)), 220)
        lines.append("- [%s](%s) - %s" % (fn, fn, d or "(no description)"))
    with B._memory_write_lock():
        with io.open(ARCHIVE, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    print("appended %d lines to INDEX_ARCHIVE.md (under the shared write lock)" % len(missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
