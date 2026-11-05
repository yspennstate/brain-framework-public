"""test_supersession_route_cache.py - active_supersessions answers a fresh process from the saved route-name slugs while
the memory folder's key is unchanged, and lists the folder again whenever that could be wrong (5 Nov 2026).

    python test_supersession_route_cache.py [--module SUPERSESSION.py] [--old OLD_SUPERSESSION.py]

The module is copied into a throwaway folder and loaded from there, so its .sup_route_known_*.json lands beside the
copy. Routes come from a superseded.tsv index without frontmatter, as fok_probe and spread_recall call it. A fresh
process is simulated by emptying the module's per-process slug set; folder listings are counted. A module that skips
saving for folders under the temp directory (test folders) is shown another temp directory for the run.
 R1 a cold call equals the answer with the cache off (BRAIN_SUP_ROUTE_CACHE=0) and saves the route slugs
 R2 a fresh process with the folder unchanged answers without listing the folder, and the answer is the same
 R3 a new memory file moves the folder key: the next fresh process lists again and still equals the cache-off answer
 R4 a saved file whose key is stale is ignored even when its slug list is wrong
 R5 a route that names a slug outside the saved universe (a new index line, folder key unchanged) lists again
 R6 with the cache off nothing is saved
 R7 (a module with ROUTE_KNOWN_MAX) after more folders than the cap, the file holds at most the cap and the newest
    folder is still answered without a listing
With --old, the module before this change must list the folder on R2 (the check can fail).
"""
import importlib.machinery
import importlib.util
import glob
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
    td = tempfile.mkdtemp(prefix="sup_route_%s_" % tag)
    os.environ.pop("BRAIN_SUP_ROUTE_CACHE", None)
    real_gettempdir = tempfile.gettempdir
    tempfile.gettempdir = lambda: os.path.join(td, "not_the_fixture")
    try:
        code = os.path.join(td, "code")
        os.makedirs(code)
        copy = os.path.join(code, "supersession.py")
        shutil.copyfile(path, copy)
        loader = importlib.machinery.SourceFileLoader("sup_route_" + tag, copy)
        spec = importlib.util.spec_from_file_location("sup_route_" + tag, copy, loader=loader)
        m = importlib.util.module_from_spec(spec)
        loader.exec_module(m)
        cache_home = os.path.join(td, "cache")
        os.makedirs(cache_home)
        m.HERE = cache_home
        if hasattr(m, "_FM_OTHER_MIN"):
            m._FM_OTHER_MIN = 0
        mem = os.path.join(td, "memory")
        os.makedirs(mem)
        index = os.path.join(td, "superseded.tsv")
        with open(index, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("old\tcorrection\n"
                     "old_a_2026_11_05\tnew_a_2026_11_05\n"
                     "old_b_2026_11_05\tmid_b_2026_11_05\n"
                     "mid_b_2026_11_05\tend_b_2026_11_05\n"
                     "dangling_2026_11_05\tmissing_target_2026_11_05\n"
                     "gone_source_2026_11_05\tnew_a_2026_11_05\n")
        for s in ("old_a", "new_a", "old_b", "mid_b", "end_b", "dangling"):
            note(mem, s + "_2026_11_05")
        for i in range(40):
            note(mem, "filler_%02d_2026_11_05" % i)
        real_md = m._md_files
        listed = [0]

        def counted(d):
            listed[0] += 1
            return real_md(d)

        m._md_files = counted

        def fresh():
            if hasattr(m, "_KNOWN_SLUGS"):
                m._KNOWN_SLUGS.clear()

        def call():
            return m.active_supersessions(mem, index_path=index)

        def off():
            fresh()
            os.environ["BRAIN_SUP_ROUTE_CACHE"] = "0"
            try:
                return call()
            finally:
                os.environ.pop("BRAIN_SUP_ROUTE_CACHE", None)
                fresh()

        saved = lambda: glob.glob(os.path.join(code, ".sup_route_known*.json"))
        r = {}
        truth = off()
        r["R6_nothing_saved_when_off"] = not saved()
        fresh()
        a = call()
        r["R1"] = (a == truth, bool(saved()), a)
        fresh()
        n0 = listed[0]
        b = call()
        r["R2"] = (b == truth, listed[0] - n0)
        note(mem, "gone_source_2026_11_05")
        truth3 = off()
        fresh()
        n0 = listed[0]
        c = call()
        r["R3"] = (c == truth3, listed[0] - n0)
        # R4: plant a wrong slug list under a key that no longer matches the folder
        import json
        def stale(x):                           # every saved entry, whatever the file's layout
            if isinstance(x, dict):
                if "key" in x:
                    return dict(x, key=[0, 0, 0], known=[])
                return {k: stale(v) for k, v in x.items()}
            return x
        for f in saved():
            json.dump(stale(json.load(open(f))), open(f, "w"))
        fresh()
        n0 = listed[0]
        d = call()
        r["R4"] = (d == truth3, listed[0] - n0)
        # R5: a new index line (the folder key stays) whose slugs are outside the saved universe
        fresh()
        call()                                  # re-save under the current key
        key_before = m._dir_key(mem) if hasattr(m, "_dir_key") else None
        with open(index, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("filler_00_2026_11_05\tfiller_01_2026_11_05\n")
        truth5 = off()
        fresh()
        n0 = listed[0]
        e = call()
        r["R5"] = (e == truth5, listed[0] - n0, key_before == (m._dir_key(mem) if hasattr(m, "_dir_key") else None),
                   "filler_00_2026_11_05" in truth5)
        cap = getattr(m, "ROUTE_KNOWN_MAX", None)
        if cap:
            last = None
            for k in range(cap + 2):
                other = os.path.join(td, "memory_%d" % k)
                os.makedirs(other)
                for s in ("old_a", "new_a"):
                    note(other, s + "_2026_11_05")
                fresh()
                m.active_supersessions(other, index_path=index)
                last = other
            entries = sum(len(json.load(open(f))) for f in saved())
            fresh()
            n0 = listed[0]
            g = m.active_supersessions(last, index_path=index)
            r["R7"] = (entries <= cap, entries, cap, listed[0] - n0, g.get("old_a_2026_11_05"))
        return r
    finally:
        tempfile.gettempdir = real_gettempdir
        os.environ.pop("BRAIN_SUP_ROUTE_CACHE", None)
        shutil.rmtree(td, ignore_errors=True)


def main():
    r = cases(MOD, "new")
    ok1, wrote, ans = r["R1"]
    check(ok1 and wrote and ans.get("old_b_2026_11_05") == "end_b_2026_11_05",
          "R1 cold answer equals cache-off (%s) and the route slugs were saved (%s); chain resolves to %s" % (
              ok1, wrote, ans.get("old_b_2026_11_05")))
    check(r["R2"][0] and r["R2"][1] == 0,
          "R2 fresh process, folder unchanged: same answer (%s), folder listed %d time(s)" % r["R2"])
    check(r["R3"][0] and r["R3"][1] >= 1,
          "R3 a new file (a route's missing source appears): same as cache-off (%s), folder listed %d time(s)" % r["R3"])
    check(r["R4"][0] and r["R4"][1] >= 1,
          "R4 a stale-key file with a wrong list: same as cache-off (%s), listed %d time(s)" % r["R4"])
    check(r["R5"][0] and r["R5"][1] >= 1 and r["R5"][3],
          "R5 a route outside the saved universe: same as cache-off (%s), listed %d time(s), key unchanged %s, "
          "new route present %s" % r["R5"])
    check(r["R6_nothing_saved_when_off"], "R6 cache off: nothing saved")
    if "R7" in r:
        check(r["R7"][0] and r["R7"][3] == 0 and r["R7"][4] == "new_a_2026_11_05",
              "R7 past the cap: %d saved folder(s) for a cap of %d; newest folder listed %d time(s), routes to %s"
              % r["R7"][1:])
    if OLD:
        o = cases(OLD, "old")
        check(o["R2"][1] >= 1,
              "N1 the module before this change lists the folder on a fresh process (%d time(s)) (the check can fail)"
              % o["R2"][1])
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
