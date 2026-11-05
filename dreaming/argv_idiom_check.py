#!/usr/bin/env python3
"""LANDING VERIFIER for mesh task 711 - is the argv idiom still dangerous anywhere?

A task board is transport. This is the check that says whether the defect is gone.

THE DEFECT: `x = sys.argv[1] if len(sys.argv) > 1 else DEFAULT` silently accepts a FLAG as its
most important positional argument. When that argument selects an input corpus, the program does
real work against an empty one and OVERWRITES its outputs with the result. Measured 2026-08-22:
`predict.py --help` cut predictions.tsv from 136 rows to 1 and brain/PREDICTIONS.md from 2,858
bytes to "0 open bets". Sixth recurrence; see script_help_flag_can_execute_work_2026_07_14.

A script is DANGEROUS if it uses the idiom AND writes files AND has no argparse.
A script is SAFE once it has argparse (or stops writing, or stops using the idiom).

Exit 0 when nothing is dangerous. Exit 1 with the list when something is.
Usage: argv_idiom_check.py [dir]   (default: the dreaming dir this file sits beside)
"""
import io, os, re, sys

d = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else \
    os.path.dirname(os.path.abspath(__file__))
# ^ deliberately flag-safe: a leading '-' is NOT taken as a directory. That is the whole lesson.

IDIOM = re.compile(r"sys\.argv\[1\]\s+if\s+len\(sys\.argv\)")
WRITES = re.compile(r"""io\.open\([^)]*["']w|(?<!\.)\bopen\([^)]*["']w|\.write\(|os\.replace\(|shutil\.(copy|move)""")

if not os.path.isdir(d):
    print("not a directory: %s" % d); sys.exit(2)

dangerous, safe, checked = [], [], 0
for fn in sorted(os.listdir(d)):
    if not fn.endswith(".py"):
        continue
    p = os.path.join(d, fn)
    try:
        s = io.open(p, encoding="utf-8", errors="replace").read()
    except Exception:
        continue
    checked += 1
    if not IDIOM.search(s):
        continue
    writes = len(WRITES.findall(s))
    has_ap = "argparse" in s
    if writes and not has_ap:
        dangerous.append((fn, writes))
    else:
        safe.append((fn, writes, has_ap))

print("scanned %d .py files in %s" % (checked, d))
print()
if safe:
    print("uses the idiom but SAFE (argparse present, or writes nothing):")
    for fn, w, ap in safe:
        print("   %-38s writes=%-3d argparse=%s" % (fn, w, ap))
    print()
if dangerous:
    print("DANGEROUS - idiom + writes + NO argparse:")
    for fn, w in dangerous:
        print("   %-38s writes=%d" % (fn, w))
    print()
    print("FAIL: %d script(s) can still destroy their own outputs on a stray flag." % len(dangerous))
    print("Fix each with argparse (nargs='?') plus an os.path.isdir guard that REFUSES,")
    print("then prove it: --help exit 0 no writes; unknown flag exit 2 no writes; bad dir exit 2")
    print("no writes; no-args byte-identical; --dry-run no writes. predict.py is the worked example.")
    sys.exit(1)
print("PASS: no script uses the idiom while writing without argparse.")
sys.exit(0)
