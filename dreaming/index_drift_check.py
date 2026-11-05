#!/usr/bin/env python3
"""Find MEMORY.md rows that advertise a claim their own memory has since retracted.

WHY THIS EXISTS. An index row is written when a memory is FILED and is never revisited when its
description changes. Correcting a memory IN PLACE is the documented right way to do it, so that the
correction travels with any excerpt - but the row does not follow. The result is perverse: the more
carefully a memory is corrected, the wider the gap between what MEMORY.md advertises and what the
file says. And MEMORY.md auto-loads into every session, so a stale row teaches the wrong thing before
anyone opens the file.

Measured 2026-08-13: 10 of 93 indexed memories had a row omitting a correction the file carried, and
all ten still read [VERIFIED]. Among them a row selling a measurement its file records as CANCELLED,
and a row selling "14/14 arms p=0.0001" over a file saying the mechanism is UNCONFIRMED.

    python index_drift_check.py            report drift
    python index_drift_check.py --list     also list rows that merely differ in wording

Read-only. It never writes; fixing a row means claiming memory:brain/index first.
"""
import io
import os
import re
import sys

MEM = os.path.join(os.path.expanduser("~"), ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
IDX = os.path.join(MEM, "MEMORY.md")

# A file carrying one of these while its index row does not is the dangerous case: the row is
# selling a claim the file has withdrawn.
CORRECTION_WORDS = ["RETRACT", "SUPERSED", "CANCELLED", "CORRECTED", "UNCONFIRMED",
                    "WEAKENED", "WITHDRAWN", "HALF RET", "WAS WRONG", "DOES NOT HOLD"]


def rows():
    out = {}
    if not os.path.exists(IDX):
        return out
    for line in io.open(IDX, encoding="utf-8", errors="replace").read().splitlines():
        m = re.match(r"\s*-\s*\[([^\]]+)\]\(([^)]+)\)\s*[-—]\s*(.*)", line)
        if m:
            out[m.group(2)] = m.group(3).strip()
    return out


def desc(fn):
    p = os.path.join(MEM, fn)
    if not os.path.exists(p):
        return None
    t = io.open(p, encoding="utf-8", errors="replace").read()
    m = re.search(r'^description:\s*"?(.*?)"?\s*$', t, re.M)
    return m.group(1).strip() if m else None


def main():
    show_all = "--list" in sys.argv
    r = rows()
    if not r:
        print("  no index rows readable at %s" % IDX)
        return 3
    danger, cosmetic = [], []
    for fn, row in r.items():
        d = desc(fn)
        if not d:
            continue
        du, ru = d.upper(), row.upper()
        hits = [w for w in CORRECTION_WORDS if w in du and w not in ru]
        if hits:
            danger.append((fn, hits, row, d))
        elif d[:40].lower() not in row.lower():
            cosmetic.append((fn, row, d))
    print("  %d indexed memories checked" % len(r))
    print("  %d rows HIDE A CORRECTION the file carries" % len(danger))
    print("  %d rows differ only in wording" % len(cosmetic))
    if danger:
        print()
        print("  THESE TEACH THE WRONG THING ON SESSION START:")
        for fn, hits, row, d in danger:
            print("    %s" % fn)
            print("        missing from row: %s" % ",".join(hits))
            print("        row  : %s" % row[:110].encode("ascii", "replace").decode())
            print("        file : %s" % d[:110].encode("ascii", "replace").decode())
    if show_all and cosmetic:
        print()
        print("  wording-only differences (usually harmless):")
        for fn, row, d in cosmetic:
            print("    %-58s" % fn[:58])
    print()
    print("  To fix: claim memory:brain/index, copy the file's description into its row, and verify")
    print("  the line count is unchanged and MEMORY.md still decodes as strict utf-8.")
    return 1 if danger else 0


if __name__ == "__main__":
    sys.exit(main())
