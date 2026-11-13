"""A warm recall process that lives through a BM25 rebuild must let go of the old postings file and then answer as a cold
process would. brain_bm25_release.release() is what the recall server runs on a rebuild before it decides to stay up.

Builds a small brain in a temporary HOME, warms brain.py in one process, rebuilds the index with one memory added, and
checks: the warm process held the old (now unlinked) postings file before release and holds nothing after; after
release its recall of the new memory prints what a fresh process prints.
Run: python3 test_recall_server_bm25_stay.py   (exit 0 = pass)"""
import json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MEMS = {
    "station_serial_swap": "Station QX-7731 carried calibration serial 7KDP-2210 until the overnight swap.",
    "store_backup_height": "The store was backed up at block 604417 in three parts and the restore was checked.",
    "garden_watering_note": "Water the tomatoes twice a week and the basil every day in summer.",
}
NEW = ("orchard_pruning_note", "Prune the pear and apple trees in late winter before the buds swell.")
Q_OLD, Q_NEW = "station calibration serial swap", "prune pear apple trees winter"

WARM = r'''
import contextlib, io, json, os, sys
sys.path.insert(0, os.getcwd())
import brain, bm25, brain_bm25_release as rel
mem = sys.argv[1]
def recall(q):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        brain.recall(q)
    return buf.getvalue()
first = recall(%r)
slug, text = %r
with open(os.path.join(mem, slug + ".md"), "w") as f:
    f.write("---\nname: %%s\ndescription: \"%%s\"\nmetadata:\n  type: reference\n---\n\n%%s\n" %% (slug, slug.replace("_", " "), text))
with open(os.path.join(mem, "MEMORY.md"), "a") as f:
    f.write("- [%%s.md](%%s.md) - %%s\n" %% (slug, slug, slug.replace("_", " ")))
bm25.build()
bm25.build_postings()
# on a live machine the old generation is swept off disk soon after; do that here so a hold is visible
import glob
cur = os.path.abspath(bm25.postings_path(bm25.INDEX, bm25._index_signature(bm25.INDEX)))
for p in glob.glob(bm25.INDEX + ".postings.*.sqlite3"):
    if os.path.abspath(p) != cur:
        os.remove(p)
before =[p for _fd, _n, p in rel.held_deleted(min_bytes=0) if ".postings." in os.path.basename(p or "")]
if os.environ.get("TEST_SKIP_RELEASE") != "1":
    rel.release(vars(brain))
after = [p for _fd, _n, p in rel.held_deleted(min_bytes=0) if ".postings." in os.path.basename(p or "")]
print("RESULT " + json.dumps({"first": first, "before": before, "after": after, "new": recall(%r)}))
''' % (Q_OLD, NEW, Q_NEW)

COLD = r'''
import contextlib, io, os, sys
sys.path.insert(0, os.getcwd())
import brain
for i, q in enumerate((%r, %r)):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        brain.recall(q)
    if i:
        print("<<SECOND>>")
        sys.stdout.write(buf.getvalue())
''' % (Q_OLD, Q_NEW)


def main():
    tmp = tempfile.mkdtemp(prefix="bm25stay_")
    fails = []
    try:
        mem = os.path.join(tmp, ".claude", "projects", "t", "memory")
        os.makedirs(mem)
        idx = ["# Memory Index"]
        for slug, text in MEMS.items():
            with open(os.path.join(mem, slug + ".md"), "w") as f:
                f.write("---\nname: %s\ndescription: \"%s\"\nmetadata:\n  type: reference\n---\n\n%s\n"
                        % (slug, slug.replace("_", " "), text))
            idx.append("- [%s.md](%s.md) - %s" % (slug, slug, slug.replace("_", " ")))
        with open(os.path.join(mem, "MEMORY.md"), "w") as f:
            f.write("\n".join(idx) + "\n")
        env = dict(os.environ, HOME=tmp, BRAIN_SLUG="t", BRAIN_BM25_INDEX=os.path.join(tmp, "bm25.json.gz"),
                   BRAIN_RECALL_LOG="0", BRAIN_RECALL_REFRESH="0", BRAIN_RECALL_QUERY_EMBED="0",
                   BRAIN_RECALL_SERVER="0")
        env.pop("BRAIN_MEM_DIR", None)
        subprocess.run([sys.executable, "-c", "import bm25; bm25.build(); bm25.build_postings()"], cwd=HERE, env=env,
                       capture_output=True)
        if not os.path.exists(env["BRAIN_BM25_INDEX"]):
            print("FAIL setup: bm25 index not built")
            return 1

        def warm(skip=False):
            e = dict(env, TEST_SKIP_RELEASE="1" if skip else "0")
            r = subprocess.run([sys.executable, "-c", WARM, mem], cwd=HERE, env=e, capture_output=True, text=True,
                               timeout=300)
            line = [l for l in r.stdout.splitlines() if l.startswith("RESULT ")]
            if not line:
                return None, r.stderr[-400:]
            return json.loads(line[-1][7:]), ""

        res, err = warm()
        if res is None:
            print("FAIL warm process:", err)
            return 1
        if "station_serial_swap" not in res["first"]:
            fails.append("setup: the warm process's first recall did not find the station memory")
        if not res["before"]:
            fails.append("setup: the warm process held no unlinked postings file after the rebuild, so this test "
                         "cannot see a hold (has the rebuild stopped unlinking the old generation?)")
        if res["after"]:
            fails.append("release() left the old postings file held: %s" % res["after"])
        # the cold side asks the same two questions in one fresh process, so per-process hints print alike
        cold = subprocess.run([sys.executable, "-c", COLD], cwd=HERE, env=env, capture_output=True, text=True,
                              timeout=120).stdout.split("<<SECOND>>\n", 1)[-1]
        if "orchard_pruning_note" not in cold:
            fails.append("setup: a cold process did not find the new memory")
        if res["new"] != cold:
            fails.append("after release the warm recall differs from a cold process:\n%s\n---\n%s"
                         % (res["new"][:400], cold[:400]))
        # control: without release the hold must still be there, or the check above proves nothing
        res2, err2 = warm(skip=True)
        if res2 is None or not res2["after"]:
            fails.append("control: without release() the old postings file should still be held, got %s"
                         % (res2 and res2["after"], ) if res2 else err2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for f in fails:
        print("FAIL", f)
    print("PASS" if not fails else "%d failure(s)" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    # Discovery is not execution: a help request exits before any work.
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print(__doc__ or os.path.basename(__file__))
        raise SystemExit(0)
    sys.exit(main())
