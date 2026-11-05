"""test_supersession_identity.py - the supersession cache knows a replaced file, a directory is never a memory, and a
target renamed only in case keeps its route where the file system ignores case (29 Sep 2026, claude-825b1305; scheduled
audits account03/B19 r75 and account02/B11 auto46).

    python test_supersession_identity.py [--module SUPERSESSION.py] [--old OLD_SUPERSESSION.py]

A throwaway memory folder; the module's cache is written into a throwaway folder too (HERE is pointed there). Notes are
aged a minute so the cache keeps them.
 E6 a predecessor replaced by rename with the same size and its old mtime, its pointer moved from one target to another
    of the same length: the warm cache follows it, as a cold one does
 E7 a directory named ghost_2026_09_29.md: not a known memory, a prose pointer to it builds no route, and the audit
    counts a strict pointer to it as dangling
 E8 a strict pointer naming Target_Correction_2026_09_29 while the file is target_correction_2026_09_29.md: on a file
    system that ignores case (Windows) the route reaches the file as spelled and resolves; where case matters, the
    spellings stay distinct and nothing routes
 E9 the same for a quoted prose pointer
 E10 after all of it, warm routes equal a cold rebuild's
 L1 (reported, not asserted) an in-place write of the same size whose mtime is set back: on Windows the cache still
    serves the old target; the module says so in its comment
With --old, the module before this change must fail E6 on the warm cache and count E7's directory as a memory (the check
can fail).
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
FOLDS = os.path.normcase("A") == os.path.normcase("a")
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


def text(slug, pointer=None):
    fm = "name: %s\ndescription: a fixture note\n" % slug
    if pointer is not None:
        fm += "superseded_by: %s\n" % pointer
    return "---\n%s---\n\nbody of %s\n" % (fm, slug)


def note(mem, slug, pointer=None, age=60, file_slug=None):
    p = os.path.join(mem, (file_slug or slug) + ".md")
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text(slug, pointer))
    t = time.time() - age
    os.utime(p, (t, t))
    return p


def cases(path, tag):
    td = tempfile.mkdtemp(prefix="supersession_id_%s_" % tag)
    try:
        m = load(path, "ssid_" + tag)
        cache_home = os.path.join(td, "cache")
        os.makedirs(cache_home)
        m.HERE = cache_home
        m._FM_OTHER_MIN = 0   # lever 17: a small folder reads uncached unless told; these controls test the cache
        mem = os.path.join(td, "memory")
        os.makedirs(mem)
        idx = os.path.join(td, "no_index.tsv")
        pred = note(mem, "moved_note_2026_09_29", "target_aa_2026_09_29")
        note(mem, "target_aa_2026_09_29")
        note(mem, "target_bb_2026_09_29")
        inplace = note(mem, "inplace_note_2026_09_29", "target_aa_2026_09_29")
        os.makedirs(os.path.join(mem, "ghost_2026_09_29.md"))
        note(mem, "ghost_pointer_2026_09_29", '"ghost_2026_09_29"')
        note(mem, "ghost_strict_2026_09_29", "ghost_2026_09_29")
        note(mem, "case_strict_2026_09_29", "Target_Correction_2026_09_29")
        note(mem, "case_quoted_2026_09_29", '"Target_Quoted_2026_09_29"')
        note(mem, "target_correction_2026_09_29")
        note(mem, "target_quoted_2026_09_29")
        r = {}
        first = m._frontmatter_targets(mem)
        r["E6_before"] = first.get("moved_note_2026_09_29")
        st = os.stat(pred)
        tmp = pred + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text("moved_note_2026_09_29", "target_bb_2026_09_29"))
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, pred)
        st2 = os.stat(inplace)
        with open(inplace, "r+", encoding="utf-8", newline="\n") as fh:
            body = fh.read()
            fh.seek(0)
            fh.write(body.replace("target_aa_2026_09_29", "target_bb_2026_09_29"))
        os.utime(inplace, ns=(st2.st_atime_ns, st2.st_mtime_ns))
        warm = m._frontmatter_targets(mem)
        r["E6_warm"] = warm.get("moved_note_2026_09_29")
        r["L1_warm"] = warm.get("inplace_note_2026_09_29")
        r["E7_known"] = "ghost_2026_09_29" in m._known_memory_slugs(mem)
        r["E7_prose"] = warm.get("ghost_pointer_2026_09_29")
        routes, dangling, _prose, _part = m.audit_pointers(mem)[:4]
        r["E7_audit"] = ("routes" if any(x[0] == "ghost_strict_2026_09_29" for x in routes) else
                         "dangling" if any(x[0] == "ghost_strict_2026_09_29" for x in dangling) else "neither")
        active = m.active_supersessions(mem, index_path=idx, include_frontmatter=True)
        r["E8_route"] = warm.get("case_strict_2026_09_29")
        r["E8_active"] = active.get("case_strict_2026_09_29")
        r["E9_route"] = warm.get("case_quoted_2026_09_29")
        r["E9_active"] = active.get("case_quoted_2026_09_29")
        shutil.rmtree(cache_home)
        os.makedirs(cache_home)
        cold = m._frontmatter_targets(mem)
        r["E6_cold"] = cold.get("moved_note_2026_09_29")
        r["L1_cold"] = cold.get("inplace_note_2026_09_29")
        r["warm_equals_cold"] = {k: v for k, v in warm.items() if k != "inplace_note_2026_09_29"} == \
            {k: v for k, v in cold.items() if k != "inplace_note_2026_09_29"}
        return r
    finally:
        shutil.rmtree(td, ignore_errors=True)


def main():
    r = cases(MOD, "new")
    check(r["E6_before"] == "target_aa_2026_09_29" and r["E6_warm"] == "target_bb_2026_09_29" == r["E6_cold"],
          "E6 replaced by rename, same size and mtime: warm %s, cold %s (was %s)" % (r["E6_warm"], r["E6_cold"],
                                                                                    r["E6_before"]))
    check(not r["E7_known"] and r["E7_prose"] is None and r["E7_audit"] == "dangling",
          "E7 a directory named *.md: known %s, prose route %s, audit %s" % (r["E7_known"], r["E7_prose"], r["E7_audit"]))
    if FOLDS:
        check(r["E8_route"] == "target_correction_2026_09_29" and r["E8_active"] == "target_correction_2026_09_29",
              "E8 strict pointer renamed only in case (case-folding file system): route %s, resolves to %s" % (
                  r["E8_route"], r["E8_active"]))
        check(r["E9_route"] == "target_quoted_2026_09_29" and r["E9_active"] == "target_quoted_2026_09_29",
              "E9 quoted pointer renamed only in case: route %s, resolves to %s" % (r["E9_route"], r["E9_active"]))
    else:
        check(r["E8_active"] is None and r["E9_active"] is None,
              "E8/E9 case-sensitive file system: the spellings stay distinct, nothing resolves (%s, %s)" % (
                  r["E8_active"], r["E9_active"]))
    check(r["warm_equals_cold"], "E10 warm routes equal a cold rebuild's (L1's note aside)")
    print("INFO  L1 in-place write, same size, mtime set back: warm %s, cold %s%s" % (
        r["L1_warm"], r["L1_cold"], " (the documented limit)" if r["L1_warm"] != r["L1_cold"] else ""))
    if OLD:
        o = cases(OLD, "old")
        check(o["E6_warm"] == "target_aa_2026_09_29" and o["E7_known"],
              "N1 the module given keeps E6's old target on the warm cache (%s) and counts E7's directory as a memory "
              "(%s) (the check can fail)" % (o["E6_warm"], o["E7_known"]))
        if FOLDS:
            check(o["E8_active"] is None, "N2 the module given loses E8's route (%s) (the check can fail)" % o["E8_active"])
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
