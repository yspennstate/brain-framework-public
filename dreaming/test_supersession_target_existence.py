"""test_supersession_target_existence.py - a prose supersession pointer routes exactly while its target exists, whatever the
frontmatter cache holds (28 Sep 2026; interactive audit IA-20260928-04).

    python test_supersession_target_existence.py [--module SUPERSESSION.py] [--old OLD_SUPERSESSION.py]

A throwaway memory folder; the module's cache is written into a throwaway folder too (HERE is pointed there). The
predecessors are aged a minute so the cache keeps them.
 E1 a quoted pointer whose target does not exist yet: no route; the target is created, the predecessor untouched: the
    warm cache routes it, as a cold one does
 E2 a pointer whose target exists: routed; the target is removed: the warm cache no longer routes it
 E3 a pointer narrowed by a '#' comment never routes, target or no target
 E4 a plain unquoted pointer routes as before
With --old, the module before this change must keep E1's route missing on the warm cache (the check can fail).
"""
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile
import time

if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = sys.argv[1:]
MOD = os.path.abspath(ARGS[ARGS.index("--module") + 1]) if "--module" in ARGS else os.path.join(HERE, "supersession.py")
OLD = os.path.abspath(ARGS[ARGS.index("--old") + 1]) if "--old" in ARGS else None
fails, ran = [], []


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def note(mem, slug, pointer=None, age=60):
    fm = "name: %s\ndescription: a fixture note\n" % slug
    if pointer is not None:
        fm += "superseded_by: %s\n" % pointer
    p = os.path.join(mem, slug + ".md")
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("---\n%s---\n\nbody of %s\n" % (fm, slug))
    t = time.time() - age
    os.utime(p, (t, t))


def cases(path, tag):
    td = tempfile.mkdtemp(prefix="supersession_%s_" % tag)
    try:
        m = load(path, "ss_" + tag)
        cache_home = os.path.join(td, "cache")
        os.makedirs(cache_home)
        m.HERE = cache_home
        m._FM_OTHER_MIN = 0   # lever 17: a small folder reads uncached unless told; these controls test the cache
        mem = os.path.join(td, "memory")
        os.makedirs(mem)
        note(mem, "old_note_2026_09_28", '"new_note_2026_09_28"')
        note(mem, "kept_note_2026_09_28", '"kept_target_2026_09_28"')
        note(mem, "kept_target_2026_09_28")
        note(mem, "partial_note_2026_09_28", "part_target_2026_09_28   # only its third section")
        note(mem, "part_target_2026_09_28")
        note(mem, "plain_note_2026_09_28", "plain_target_2026_09_28")
        note(mem, "plain_target_2026_09_28")
        r = {}
        first = m._frontmatter_targets(mem)
        r["E1_before"] = first.get("old_note_2026_09_28")
        r["E2_before"] = first.get("kept_note_2026_09_28")
        note(mem, "new_note_2026_09_28")
        os.remove(os.path.join(mem, "kept_target_2026_09_28.md"))
        warm = m._frontmatter_targets(mem)
        r["E1_warm"] = warm.get("old_note_2026_09_28")
        r["E2_warm"] = warm.get("kept_note_2026_09_28")
        r["E3"] = warm.get("partial_note_2026_09_28")
        r["E4"] = warm.get("plain_note_2026_09_28")
        shutil.rmtree(cache_home)
        os.makedirs(cache_home)
        cold = m._frontmatter_targets(mem)
        r["E1_cold"] = cold.get("old_note_2026_09_28")
        r["warm_equals_cold"] = warm == cold
        return r
    finally:
        shutil.rmtree(td, ignore_errors=True)


def main():
    r = cases(MOD, "new")
    check(r["E1_before"] is None and r["E1_warm"] == "new_note_2026_09_28" and r["E1_cold"] == r["E1_warm"],
          "E1 target missing: no route (%s); target created: warm routes to %s, cold to %s" % (
              r["E1_before"], r["E1_warm"], r["E1_cold"]))
    check(r["E2_before"] == "kept_target_2026_09_28" and r["E2_warm"] is None,
          "E2 target present: routed (%s); target removed: warm route %s" % (r["E2_before"], r["E2_warm"]))
    check(r["E3"] is None, "E3 a pointer narrowed by a comment: no route (%s)" % r["E3"])
    check(r["E4"] == "plain_target_2026_09_28", "E4 a plain pointer routes (%s)" % r["E4"])
    check(r["warm_equals_cold"], "E5 after both changes the warm cache's routes equal a cold rebuild's")
    if OLD:
        o = cases(OLD, "old")
        check(o["E1_warm"] is None and o["E1_cold"] == "new_note_2026_09_28",
              "N1 the module before this change keeps E1 unrouted on the warm cache (%s) while a cold rebuild routes it "
              "(%s) (the check can fail)" % (o["E1_warm"], o["E1_cold"]))
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
