"""test_search_session_identity.py - a logged search that names its session is judged in that session or not at all
.

    python test_search_session_identity.py [--module GRAPH_LEARN.py] [--old OLD_GRAPH_LEARN.py]

Drives graph_learn.search_session and judge_searches with synthetic session maps (no transcript, no learned state):
 S1 the record names session A, A is absent from the map, and B recalled the same memory 2 s later and opened it:
    not assigned to B, and judge_searches returns no session and no reads for it
 S2 the record names A, A is present and opened a memory: A, with A's read
 S3 no session named, one session's recalls overlap: that session (the legacy join still works)
 S4 no session named, two sessions overlap equally: no session (a tie is no answer)
With --old, graph_learn before this change must assign S1 to B (the check can fail).
"""
import importlib.machinery
import importlib.util
import os
import sys

if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.abspath(sys.argv[sys.argv.index("--module") + 1]) if "--module" in sys.argv else os.path.join(HERE, "graph_learn.py")
OLD = os.path.abspath(sys.argv[sys.argv.index("--old") + 1]) if "--old" in sys.argv else None
fails, ran = [], []
T = 1_800_000_000.0


def load(path, name):
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def cases(m):
    r = {}
    rec = {"t": T, "session": "aaaaaaaa-1111-4000-8000-000000000000", "shown": ["memory_x_2026_09_28"]}
    by_s = {"bbbbbbbb": [(T + 2, "recalled", "memory_x_2026_09_28"), (T + 5, "mem", "memory_x_2026_09_28")]}
    s1 = m.search_session(rec, by_s)
    j1 = m.judge_searches([rec], by_s)[0]
    r["S1"] = (s1 is None and j1[1] is None and j1[2] == [],
               "S1 named A absent, B overlapping: session %r, judged %r" % (s1, (j1[1], j1[2])))
    by_s2 = dict(by_s, aaaaaaaa=[(T + 3, "mem", "memory_y_2026_09_28")])
    j2 = m.judge_searches([rec], by_s2)[0]
    r["S2"] = (j2[1] == "aaaaaaaa" and j2[2] == ["memory_y_2026_09_28"], "S2 named A present: %r" % ((j2[1], j2[2]),))
    rec3 = {"t": T, "shown": ["memory_x_2026_09_28"]}
    s3 = m.search_session(rec3, by_s)
    r["S3"] = (s3 == "bbbbbbbb", "S3 no session named, one overlap: %r" % (s3,))
    by_s4 = dict(by_s, cccccccc=[(T + 1, "recalled", "memory_x_2026_09_28")])
    s4 = m.search_session(rec3, by_s4)
    r["S4"] = (s4 is None, "S4 no session named, a tie: %r" % (s4,))
    return r


def main():
    for k, (ok, what) in sorted(cases(load(MOD, "gl_under_test")).items()):
        check(ok, what)
    if OLD:
        old = cases(load(OLD, "gl_old"))
        check(not old["S1"][0], "N1 graph_learn before this change fails S1 (the check can fail): %s" % old["S1"][1].split(": ", 1)[1])
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
