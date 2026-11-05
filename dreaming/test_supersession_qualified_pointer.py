"""A superseded_by pointer its author narrowed is a PART supersession for every reader (4 Oct 2026).

dot 125873D4 found that spread_recall's hint cut the first token of "slug # only its third section" and told the reader
to read the successor instead; claude:4a88bc43 found the same in the route map's lenient lead (which caught only '#')
and in live_sidecar, on a live memory whose qualifier is a parenthesis ("slug (the half-real part only, ...; the
75-percent picture count stands)"). supersession.pointer_scope is the one classification; this checks it, the route map,
and the recall hint, in a throwaway memory folder with the module caches pointed there too.

    python test_supersession_qualified_pointer.py [--module supersession.py] [--recall spread_recall.py]
"""
import sys
if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = sys.argv[1:]
MOD = os.path.abspath(ARGS[ARGS.index("--module") + 1]) if "--module" in ARGS else os.path.join(HERE, "supersession.py")
REC = os.path.abspath(ARGS[ARGS.index("--recall") + 1]) if "--recall" in ARGS else os.path.join(HERE, "spread_recall.py")
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
    sys.modules[name] = m
    loader.exec_module(m)
    return m


def note(mem, slug, pointer=None, extra="", age=60):
    fm = "name: %s\ndescription: a fixture note\n" % slug
    if pointer is not None:
        fm += "superseded_by: %s\n" % pointer
    fm += extra
    p = os.path.join(mem, slug + ".md")
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("---\n%s---\n\nbody of %s\n" % (fm, slug))
    t = time.time() - age
    os.utime(p, (t, t))


def main():
    td = tempfile.mkdtemp(prefix="qualptr_")
    try:
        ss = load(MOD, "supersession")          # spread_recall imports it by this name
        cache_home = os.path.join(td, "cache")
        os.makedirs(cache_home)
        ss.HERE = cache_home
        mem = os.path.join(td, "memory")
        os.makedirs(mem)

        # 1. the classification itself
        cases = [
            ("new_note", ("whole", ["new_note"], "")),
            ("'new_note'", ("whole", ["new_note"], "")),
            ("new_note.md", ("whole", ["new_note"], "")),
            ("new_note, other_note", ("whole", ["new_note", "other_note"], "")),
            ("new_note   # only its third section", ("part", ["new_note"], "only its third section")),
            ("new_note (the half-real part only, his 30 Sep; the 75-percent count stands)",
             ("part", ["new_note"], "the half-real part only, his 30 Sep; the 75-percent count stands")),
            ("new_note - the same-evening correction", ("part", ["new_note"], "- the same-evening correction")),
            ("new_note, which replaced the second half", ("part", ["new_note"], "which replaced the second half")),
            ('"new_note # only its third section"', ("part", ["new_note"], "only its third section")),
            ("", ("none", [], "")),
            ("- not a slug", ("none", [], "")),
        ]
        if not hasattr(ss, "pointer_scope"):        # the pre-fix module (a control run): only sections 2 and 3 apply
            print("SKIP  pointer_scope is absent from %s" % os.path.basename(MOD))
            cases = []
        for value, want in cases:
            got = ss.pointer_scope(value)
            check(tuple(got) == want, "pointer_scope(%r) -> %r (got %r)" % (value, want, got))

        # 2. the route map: a whole pointer routes, a narrowed one does not
        for s in ("succ_2026_10_04", "succ2_2026_10_04"):
            note(mem, s)
        note(mem, "whole_old_2026_10_04", "succ_2026_10_04")
        note(mem, "list_old_2026_10_04", "succ_2026_10_04, succ2_2026_10_04")
        note(mem, "hash_old_2026_10_04", "succ_2026_10_04   # only its third section")
        note(mem, "paren_old_2026_10_04", "succ_2026_10_04 (the half-real part only; the count stands)")
        note(mem, "prose_old_2026_10_04", "succ_2026_10_04 - the same-evening correction")
        routes = ss.load_map(mem, include_frontmatter=True)
        check(routes.get("whole_old_2026_10_04") == "succ_2026_10_04", "a plain pointer routes")
        check(routes.get("list_old_2026_10_04") == "succ_2026_10_04", "a comma list of slugs routes to its first")
        for s in ("hash_old_2026_10_04", "paren_old_2026_10_04", "prose_old_2026_10_04"):
            check(s not in routes, "a narrowed pointer does not route: %s (got %r)" % (s, routes.get(s)))

        # 3. the recall hint: SUPERSEDED for the whole, PARTLY SUPERSEDED (with its qualifier) for the narrowed
        sr = load(REC, "spread_recall_under_test")
        sr.MEM = mem
        h = sr._status_hint("whole_old_2026_10_04", "")
        check(h.startswith("hint: SUPERSEDED by `succ_2026_10_04`"), "whole pointer -> SUPERSEDED hint (%s)" % h[:70])
        for slug, words in (("hash_old_2026_10_04", "only its third section"),
                            ("paren_old_2026_10_04", "the half-real part only")):
            h = sr._status_hint(slug, "")
            check(h.startswith("hint: PARTLY SUPERSEDED") and words in h and "succ_2026_10_04" in h
                  and "read that one instead" not in h, "narrowed pointer -> PARTLY SUPERSEDED naming the part (%s)" % h[:90])
        # an existing structured part declaration keeps its own rendering
        note(mem, "inpart_old_2026_10_04", None,
             extra='superseded_in_part: ["the old clause -> succ2_2026_10_04"]\n')
        h = sr._status_hint("inpart_old_2026_10_04", "")
        check(h.startswith("hint: PARTLY SUPERSEDED: the old clause moved to `succ2_2026_10_04`"),
              "superseded_in_part still renders as before (%s)" % h[:90])
        h = sr._status_hint("succ_2026_10_04", "")
        check("SUPERSEDED" not in h, "a memory with no pointer gets no supersession hint (%s)" % h[:60])
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("%d checks, %d failed" % (len(ran), len(fails)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
