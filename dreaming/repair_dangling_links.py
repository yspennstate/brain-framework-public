#!/usr/bin/env python3
"""Repair [[links]] that omit the date suffix brain.py appends at creation.

MEASURED 2026-08-09: the memory corpus carries 227 distinct dangling link targets
across 391 citations. They break down as

     60  date-suffix mismatch  - the memory EXISTS. A note writes
                                 [[kettle_descaling_routine]] while the
                                 file is kettle_descaling_routine_2026_01_01.md.
                                 Every one of the 60 resolves to exactly ONE file; zero
                                 are ambiguous.
     20  not links at all      - python/json syntax caught by the [[ ]] regex, e.g.
                                 [["ITM5", 70.0]] inside a fenced code block
    147  genuinely missing     - real gaps, plus noise like [[...]], [[288]], [[A]]

Only the first group is repairable, and this repairs only that group. About a quarter
of the apparent graph sparsity is a naming convention mismatch rather than absent
memories, so the connectivity gain is real and free.

DEFAULT IS DRY RUN. Pass --apply to write.

SAFETY, because this mutates many files in a corpus other sessions write to:
  - ambiguous targets (>1 candidate file) are SKIPPED, never guessed
  - links inside fenced code blocks are left alone - that is where the 20 false
    positives live and rewriting code is never wanted
  - each file is re-read immediately before its own write, so a concurrent edit is
    picked up rather than clobbered
  - a file whose content changed between scan and write is SKIPPED and reported
  - MEMORY.md is never touched: it carries pointers, not [[links]], and brain.py owns it
  - take a mesh claim on memory:brain/index before --apply

Written 2026-08-09 and deliberately NOT run: eleven minutes remained in the session and
starting a 60-file mutation against live writers is the exact pattern recorded in
hand_edit_bypassed_the_owning_tool_2026_08_09. Run it with a clean context.
"""

import argparse
import collections
import glob
import io
import os
import re
import sys


def _brain_slug_resolve():
    """The memory corpus this install belongs to.

    Order matters. $BRAIN_SLUG wins so a caller can point one script at another corpus.
    Then BRAIN_SLUG beside this file, which bootstrap/install.py writes with the slug of the
    --project-dir it was given; without it an install whose project directory is not the home
    directory builds a corpus nothing ever reads. The home-derived value stays last so a tree
    that works today keeps working untouched.
    """
    env = os.environ.get("BRAIN_SLUG", "").strip()
    if env:
        return env
    try:
        marker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "BRAIN_SLUG")
        with open(marker, encoding="utf-8") as fh:
            named = fh.read().strip()
        if named:
            return named
    except OSError:
        pass
    home = os.path.expanduser("~")
    return home.replace(":", "-").replace(chr(92), "-").replace("/", "-")


# Claude Code names a project directory after the home path, with ':' and the
# separators replaced by '-'. Deriving it keeps this tree free of one machine's
# username and correct on any other - and unlike a template token it is a real
# value at import time.
_BRAIN_SLUG = _brain_slug_resolve()
MEM = os.path.join(os.path.expanduser("~"), ".claude", "projects",
                   _BRAIN_SLUG, "memory")
LINK = re.compile(r"\[\[([^\]]+)\]\]")
FENCE = re.compile(r"```.*?```", re.S)
DATED = re.compile(r"_20\d\d_\d\d_\d\d$")


def slugs():
    out = [os.path.basename(f)[:-3] for f in glob.glob(os.path.join(MEM, "*.md"))]
    return [s for s in out if s not in ("MEMORY.md",)]


def build_index(all_slugs):
    """slug-without-date -> [candidate files]. Only unique candidates are usable."""
    idx = collections.defaultdict(list)
    for s in all_slugs:
        idx[DATED.sub("", s)].append(s)
    return idx


def masked(text):
    """Blank out fenced code so links inside it are never rewritten."""
    return FENCE.sub(lambda m: " " * len(m.group(0)), text)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="write the repairs (default is a dry run)")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N files; 0 means no limit")
    a = ap.parse_args()

    if not os.path.isdir(MEM):
        print("ABORT: memory directory not found: %s" % MEM)
        return 1

    all_slugs = slugs()
    known = set(all_slugs)
    idx = build_index(all_slugs)
    print("corpus: %d memories" % len(all_slugs))

    planned = []          # (path, original_text, new_text, [(old, new), ...])
    amb, skipped_code = 0, 0
    for path in sorted(glob.glob(os.path.join(MEM, "*.md"))):
        if os.path.basename(path) == "MEMORY.md":
            continue
        try:
            text = io.open(path, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        scan = masked(text)
        subs = []
        for target in set(LINK.findall(scan)):
            if target in known:
                continue
            cands = idx.get(target, [])
            if len(cands) == 1:
                subs.append((target, cands[0]))
            elif len(cands) > 1:
                amb += 1
        if not subs:
            continue
        new = text
        for old, repl in subs:
            # rewrite only outside code fences: split on fences and patch the gaps
            parts, last, chunks = [], 0, []
            for m in FENCE.finditer(new):
                chunks.append((last, m.start(), True))
                chunks.append((m.start(), m.end(), False))
                last = m.end()
            chunks.append((last, len(new), True))
            for s, e, editable in chunks:
                seg = new[s:e]
                if editable:
                    seg = seg.replace("[[%s]]" % old, "[[%s]]" % repl)
                parts.append(seg)
            new = "".join(parts)
        if new != text:
            planned.append((path, text, new, subs))
        if a.limit and len(planned) >= a.limit:
            break

    total = sum(len(p[3]) for p in planned)
    print("files to change : %d" % len(planned))
    print("links to repair : %d" % total)
    print("ambiguous targets skipped (never guessed): %d" % amb)
    if not planned:
        print("nothing to do")
        return 0

    for path, _t, _n, subs in planned[:8]:
        print("  %s" % os.path.basename(path)[:72])
        for old, repl in subs[:3]:
            print("      [[%s]] -> [[%s]]" % (old[:52], repl[:56]))
    if len(planned) > 8:
        print("  ... and %d more files" % (len(planned) - 8))

    if not a.apply:
        print()
        print("DRY RUN - nothing written. Re-run with --apply (hold a mesh claim on")
        print("memory:brain/index first).")
        return 0

    wrote, raced = 0, 0
    for path, original, new, _subs in planned:
        try:
            current = io.open(path, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            raced += 1
            continue
        if current != original:          # another writer got here first
            raced += 1
            print("  SKIP (changed under us): %s" % os.path.basename(path))
            continue
        io.open(path, "w", encoding="utf-8", newline="\n").write(new)
        wrote += 1
    print()
    print("wrote %d files, skipped %d that changed under us" % (wrote, raced))
    print("re-run without --apply to confirm the remaining count fell as expected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
