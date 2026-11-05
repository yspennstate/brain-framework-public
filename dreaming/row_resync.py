#!/usr/bin/env python3
"""row_resync - EXPERIMENTAL, COMPARATOR UNRELIABLE (2026-08-13 15:1x).
DO NOT TRUST ITS COUNTS. Both comparators tried (equality, prefix) flag
brand-new rows written minutes earlier by brain.py itself - the row-writing
convention is neither the full description nor its plain prefix. Until this
tool learns brain.py's actual convention, its reports are noise. FIX is
disabled. The 96-row figure previously reported from this tool is RETRACTED.
Original intent preserved below for a future fix.

CORRECTION an hour after building: MEMORY.md rows are SHORT SUMMARIES by
design (~60-200 chars vs full descriptions of 700+) - the auto-load
context cost. The original equality comparator flagged ~96 rows, almost
all false positives (short-row-vs-full-description), and --fix would have
pasted full descriptions into the index, bloating every session's
context. HARMFUL - so --fix now refuses.

What remains valid: REPORT mode with a PREFIX comparator - a row whose
normalized text is not a prefix of its file's normalized description
start is a REAL drift candidate (the description was rewritten after the
row). Fixing requires brain.py's own row-writing convention (unknown to
this tool); fix rows via brain.py or manual claim-gated edits.

Usage: python row_resync.py     # prefix-based drift report, read-only
"""
import io
import re
import sys
from pathlib import Path

MEM = Path(__file__).resolve().parent.parent / "projects" / "{{BRAIN_SLUG}}" / "memory"
INDEX = MEM / "MEMORY.md"
DESC_RE = re.compile(r"^description:\s*(.+?)\s*$", re.M)
ROW_RE = re.compile(r"^- \[(?P<name>[^\]]+\.md)\]\((?P=name)\) - (?P<desc>.*)$")


def file_description(path: Path) -> str | None:
    try:
        head = io.open(path, encoding="utf-8").read(4000)
    except OSError:
        return None
    m = DESC_RE.search(head)
    if not m:
        return None
    d = m.group(1).strip()
    if d.startswith('"') and d.endswith('"'):
        d = d[1:-1]
    return " ".join(d.split())


def main() -> int:
    if "--fix" in sys.argv:
        print("REFUSED: --fix disabled - rows are short summaries by design;")
        print("pasting full descriptions would bloat the auto-load index.")
        print("Fix real drifts via brain.py or manual claim-gated edits.")
        return 2
    fix = False
    only = {a for a in sys.argv[1:] if not a.startswith("--")}
    lines = INDEX.read_text(encoding="utf-8").splitlines(keepends=True)
    drifted, fixed = [], 0
    for i, line in enumerate(lines):
        m = ROW_RE.match(line.rstrip("\n"))
        if not m:
            continue
        name = m.group("name")
        slug = name[:-3]
        if only and slug not in only:
            continue
        desc = file_description(MEM / name)
        if desc is None:
            continue
        row_desc = " ".join(m.group("desc").split())
        probe = row_desc[:80].rstrip(". ")
        if probe and desc.startswith(probe):
            continue  # short row that matches the description's start = synced
        drifted.append(slug)
        if fix:
            lines[i] = f"- [{name}]({name}) - {desc}\n"
            fixed += 1
    if fix and fixed:
        INDEX.write_text("".join(lines), encoding="utf-8")
    print(f"rows checked against files; drifted: {len(drifted)}"
          + (f"; fixed: {fixed}" if fix else " (report only - rerun with --fix under claim)"))
    for s in drifted:
        print(f"  {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
