"""A recall whose query is only an identifier (a serial, a short hash, a block height) must reach the memory that
holds it, and a query that already seeded must print the same thing with the identifier seeds on or off.

Builds a four-memory brain in a temporary HOME with its own shadow bm25 index, then runs the real brain.py recall.
Run: python3 test_recall_id_seeds.py   (exit 0 = pass)"""
import os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MEMS = {
    "station_serial_swap": "Station QX-7731 carried calibration serial 7KDP-2210 until the overnight swap.",
    "store_backup_height": "The store was backed up at block 604417 in three parts and the restore was checked.",
    "commit_for_parity_port": "The parity port landed as commit e1f2a3b9 on the worktree branch.",
    "garden_watering_note": "Water the tomatoes twice a week and the basil every day in summer.",
}


def main():
    tmp = tempfile.mkdtemp(prefix="idseeds_")
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
        b = subprocess.run([sys.executable, "-c", "import bm25; bm25.build()"], cwd=HERE, env=env,
                           capture_output=True, text=True)
        if not os.path.exists(env["BRAIN_BM25_INDEX"]):
            print("FAIL setup: bm25 index not built:", (b.stdout + b.stderr)[-300:])
            return 1

        def recall(q, on=True):
            e = dict(env, BRAIN_RECALL_ID_SEEDS="0")
            if on:
                e.pop("BRAIN_RECALL_ID_SEEDS")   # "on" is the default the brain runs with, not an explicit 1
            return subprocess.run([sys.executable, "brain.py", "recall", q], cwd=HERE, env=e, capture_output=True,
                                  text=True, timeout=120).stdout

        for q, want in (("7KDP-2210", "station_serial_swap"), ("604417", "store_backup_height"),
                        ("e1f2a3b9", "commit_for_parity_port")):
            out = recall(q)
            if want not in out:
                fails.append("identifier %r did not reach %s" % (q, want))
            off = recall(q, on=False)
            if "no lexical foothold" not in off:
                fails.append("with BRAIN_RECALL_ID_SEEDS=0, %r should stop at no lexical foothold" % q)
        # the last one seeds through its words and carries a digit: without the "no seed yet" guard, BM25 would add
        # the station memory to it
        for q in ("station calibration serial swap", "store backup block restore checked",
                  "parity port commit station 2026"):
            if recall(q) != recall(q, on=False):
                fails.append("query %r seeded already, but its output changed with the identifier seeds on" % q)
        if recall("zzqx 404040") != recall("zzqx 404040", on=False):
            fails.append("a digit query with no BM25 hit must print the same as with the seeds off")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for f in fails:
        print("FAIL", f)
    print("PASS" if not fails else "%d failure(s)" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
