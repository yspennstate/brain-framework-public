"""Controls for foresight reading the dated prose corrections (27 Sep, claude-825b1305).

The old version (brain repository a5cb082f7a, or a path given as the first argument) and the live one parse the same
ledgers. Every old row must survive unchanged; the only new rows are dated entries that state 'reflex:'. A synthetic
ledger covers the edge cases; the live ledger is read, never written.

    python test_foresight_dated.py [OLD_FORESIGHT_PY]
"""
import sys
if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)
import collections
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

DR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DR)
FAILS = []

TEXT = """# Mistakes

- **2026-08-10 (bold)** "x" -> wrong: a; lesson: keep the bold rows; reflex: none yet
- 2026-09-20 05:1x his clock (agentx, topic): a dated entry that names its guard. Rule: do the thing first. reflex:
  a_fix_at_one_site_is_a_note
- 2026-09-21 (agentx): a dated entry with no reflex field at all. Rule: something.
  - **2026-09-22 (an indented bold line under a dated entry)** -> wrong: b; lesson: stays its own row; reflex: none
- 2026-09-23 (agentx): a dated entry that says it has no guard yet. reflex: none yet
"""


def load(path, name):
    if not path.endswith(".py"):
        dst = os.path.join(tempfile.mkdtemp(prefix="fs_old_"), name + ".py")
        shutil.copy(path, dst)
        path = dst
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def old_path():
    if len(sys.argv) > 1:
        return sys.argv[1]
    repo = os.path.join(os.path.expanduser("~"), "brain-repo")
    blob = subprocess.run(["git", "-C", repo, "show", "a5cb082f7a:12_cognitive_architecture/dreaming/foresight.py"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    if "def _mistakes" not in blob:
        raise SystemExit("could not read the old foresight from git history; pass its path")
    p = os.path.join(tempfile.mkdtemp(prefix="fs_old_"), "foresight_old.py")
    open(p, "w", encoding="utf-8").write(blob)
    return p


def rows(mod, text):
    d = tempfile.mkdtemp(prefix="fs_mem_")
    open(os.path.join(d, "MISTAKES.md"), "w", encoding="utf-8").write(text)
    keep = mod.MEM
    mod.MEM = d                                   # read at call time
    try:
        return mod._mistakes()
    finally:
        mod.MEM = keep


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (("  " + detail) if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def main():
    new = load(os.path.join(DR, "foresight.py"), "fs_live")
    old = load(old_path(), "fs_old")
    rn, ro = rows(new, TEXT), rows(old, TEXT)
    check("1 the old version reads the two bold rows only", len(ro) == 2, str(ro))
    check("2 every old row reads the same in the new version", collections.Counter(ro) <= collections.Counter(rn),
          "%s vs %s" % (ro, rn))
    extra = [r for r in rn if r not in ro]
    ids = sorted(r[1] for r in extra)
    check("3 exactly the two dated entries that state 'reflex:' are added", ids == ["a_fix_at_one_site_is_a_note", "none"],
          str(extra))
    named = [r for r in extra if r[1] == "a_fix_at_one_site_is_a_note"]
    check("4 its rule becomes the lesson", bool(named) and named[0][2].startswith("do the thing first"), str(named))
    check("5 an indented bold line under a dated entry stays its own row",
          any(r[0] == "2026-09-22" for r in rn) and any(r[0] == "2026-09-22" for r in ro), str(rn))
    live = open(os.path.join(new.MEM, "MISTAKES.md"), encoding="utf-8", errors="replace").read()
    ln, lo = rows(new, live), rows(old, live)
    lost = collections.Counter(lo) - collections.Counter(ln)
    # 12 Oct 2026 (front F10): two changes are intended, and only these may move an old row. (a) A 'none' row whose
    # entry is quoted, with its date, in a live reflex's sting now reads as that reflex. (b) A row whose 'reflex:' word
    # is prose ('the', 'before', 'claim' ...: no underscore, not a live id) is no longer reported as a missing reflex.
    gut = new._gut()
    have = collections.Counter(ln)
    explained = collections.Counter()
    for (d, rid, les), k in lost.items():
        if rid != "none" and "_" not in rid and rid not in gut:
            explained[(d, rid, les)] += k                          # (b)
        elif rid == "none" and any(r[0] == d and r[1] in gut and r[2] == les and have[r] for r in have):
            explained[(d, rid, les)] += k                          # (a): same date and lesson, now a live id
    lost = lost - explained
    check("6 on the live ledger no old row is lost or changed beyond the two intended changes", not lost,
          str(list(lost)[:3]))
    # 7: the whole ledger is read - an entry past the old 200,000-char cap is found (it was not before 12 Oct)
    pad = "".join("- **2026-07-01 (pad %d)** filler line without a guard field\n" % i for i in range(5000))
    far = TEXT.split("## The corrections", 1)[0] + "## The corrections\n" + pad + \
        "- **2026-10-01 (far)** \"y\" -> wrong: c; lesson: the far entry; reflex: none yet\n"
    rf = rows(new, far)
    check("7 an entry past 200,000 characters is read", len(far) > 200000 and any(r[0] == "2026-10-01" for r in rf),
          "len %d rows %s" % (len(far), rf[-2:]))
    # 8: a prose word after 'reflex:' is not reported as a missing reflex
    prose = TEXT.split("## The corrections", 1)[0] + "## The corrections\n" + \
        "- **2026-10-02 (prose)** \"z\" -> wrong: d; lesson: e; reflex: the reflex fired four times and was not heeded\n"
    rp = rows(new, prose)
    check("8 a prose word after 'reflex:' is not read as a reflex id", not any(r[1] == "the" for r in rp), str(rp))
    print("live ledger: %d rows before, %d now (%d dated entries state reflex:; %d old rows moved as intended)"
          % (len(lo), len(ln), len(ln) - len(lo), sum(explained.values())))
    print("%d of 8 controls pass" % (8 - len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
