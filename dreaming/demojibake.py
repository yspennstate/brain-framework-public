#!/usr/bin/env python3
"""Repair baked-in mojibake (UTF-8 read as cp1252, possibly repeatedly) across the
live memory corpus. Per-line, only when the line round-trips cleanly and strictly
reduces mojibake markers. Dry-run by default; 'apply' to write."""
import os, re, sys, io

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file works at module scope, so the guard
    # must precede the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)

MEM = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory")
APPLY = len(sys.argv) > 1 and sys.argv[1] == "apply"
MARK = re.compile(r"â€|Ã¢|Ã©|Ã¨|Ã¼|Ã¶|Ã¤|ðŸ|âš |Â[°·§ ]|Ã—|â‰|â†|â‡")

def sloppy_encode(t):
    out = bytearray()
    for ch in t:
        try:
            out += ch.encode("cp1252")
        except UnicodeEncodeError:
            cp = ord(ch)
            if cp <= 0xFF:
                out.append(cp)
            else:
                raise
    return bytes(out)

def fix_line(ln):
    cur = ln
    for _ in range(4):
        if not MARK.search(cur):
            break
        try:
            cand = sloppy_encode(cur).decode("utf-8")
        except Exception:
            break
        if cand == cur or MARK.findall(cand) >= MARK.findall(cur):
            if len(MARK.findall(cand)) >= len(MARK.findall(cur)):
                break
        cur = cand
    return cur

files_hit, lines_fixed = 0, 0
for root, dirs, fns in os.walk(MEM):
    for fn in fns:
        if not fn.endswith(".md"):
            continue
        p = os.path.join(root, fn)
        try:
            s = io.open(p, encoding="utf-8").read()
        except Exception:
            continue
        if not MARK.search(s):
            continue
        lines = s.split("\n")
        new_lines = [fix_line(l) if MARK.search(l) else l for l in lines]
        changed = sum(1 for a, b in zip(lines, new_lines) if a != b)
        if changed:
            files_hit += 1
            lines_fixed += changed
            if not APPLY and files_hit <= 8:
                ex = next(b for a, b in zip(lines, new_lines) if a != b)
                print(f"  {fn}: {changed} lines  e.g. {ex.strip()[:90]!r}")
            if APPLY:
                new = "\n".join(new_lines)
                if new.count("�") <= s.count("�"):  # never introduce loss
                    io.open(p, "w", encoding="utf-8", newline="\n").write(new)
print(("APPLIED" if APPLY else "DRY-RUN"), f"files={files_hit} lines={lines_fixed}")
