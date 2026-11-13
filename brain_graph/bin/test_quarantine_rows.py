"""Controls for quarantine_rows.keep_old and its six writers.

1-5 exercise keep_old in a temporary folder; 6 reads each writer's source and requires every fixed-name rows file it
opens for writing to go through keep_old. Nothing live is read or written except the six sources, read-only.

    python test_quarantine_rows.py [BIN_DIR]
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import os
import re
import sys
import tempfile
import time

BIN = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BIN)
from quarantine_rows import keep_old  # noqa: E402

FAILS = []


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


d = tempfile.mkdtemp(prefix="qrows_")
p = os.path.join(d, "rows.jsonl")
kept = lambda: sorted(f for f in os.listdir(d) if f != "rows.jsonl")

check("1 a missing file is left alone and nothing is created", keep_old(p) == p and kept() == [])

open(p, "w").write("run A\n")
os.utime(p, (1790400000, 1790400000))
keep_old(p)
open(p, "w").write("run B\n")
ks = kept()
check("2 the first run's rows survive the second run's write",
      len(ks) == 1 and open(os.path.join(d, ks[0])).read() == "run A\n" and open(p).read() == "run B\n")

keep_old(p)
keep_old(p)
check("3 calling it twice on the same bytes keeps one copy", len([k for k in kept() if open(os.path.join(d, k)).read() == "run B\n"]) == 1)

os.utime(p, (1790400000, 1790400000))           # same second as run A's copy, different bytes
keep_old(p)
ks = kept()
check("4 a name clash with different bytes gets its own copy, the old copy untouched",
      len(ks) == 3 and sorted(open(os.path.join(d, k)).read() for k in ks) == ["run A\n", "run B\n", "run B\n"]
      and any(k.endswith("_2.jsonl") for k in ks))

ro = os.path.join(d, "ro")
os.makedirs(ro)
q = os.path.join(ro, "rows.json")
open(q, "w").write("x")
try:
    import shutil
    orig = shutil.copy2
    shutil.copy2 = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
    raised = False
    try:
        keep_old(q)
    except OSError:
        raised = True
finally:
    shutil.copy2 = orig
check("5 a failed copy raises, so the caller never writes over the file", raised and open(q).read() == "x")

WRITERS = {"eval_doc_expansion.py": "doc_expansion_rows.jsonl", "study_date_roles.py": "date_roles_rows.json",
           "study_expansion_filter.py": "expansion_filter_rows.json",
           "study_tokenizer_variants.py": "tokenizer_variant_rows.json",
           "eval_rewrite_his_words.py": "ROWS", "eval_usage_order.py": "ROWS_Q"}
bad = []
for fn, name in WRITERS.items():
    src = open(os.path.join(BIN, fn), encoding="utf-8").read()
    opens = [ln for ln in src.split("\n") if name in ln and re.search(r"open\(", ln) and '"w"' in ln]
    if not opens or any("keep_old(" not in ln for ln in opens):
        bad.append(fn)
check("6 every writer passes its fixed-name rows file through keep_old (%s)" % (", ".join(bad) or "all six"), not bad)

print("%d of 6 controls pass" % (6 - len(FAILS)))
sys.exit(1 if FAILS else 0)
