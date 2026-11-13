"""test_memory_twoway_sync.py - the two-way memory sync on a throwaway origin, clone and memory folder: a file the
mirror holds more of is pulled, a file both sides changed is merged without losing a line here, a file only here is
listed for the push, generated nodes are left alone, a superseded_by line travels into the frontmatter, and --apply
--no-push writes backups under a temporary home and never pushes."""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import os
import shutil
import subprocess
import tempfile

import memory_twoway_sync as T

FM = "---\nname: %s\ndescription: d\nmetadata:\n  type: project\n---\n"


def run(cwd, *a):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, capture_output=True)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    return r.stdout.decode("utf-8", "replace")


def write(p, t):
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(t)


def read(p):
    with open(p, encoding="utf-8", newline="") as fh:
        return fh.read()


def test_classify_and_merge_units():
    assert T.classify(["a", "b"], ["a", "b", ""]) == "SAME"
    assert T.classify(["a"], ["a", "b"]) == "LIVE_AHEAD"
    assert T.classify(["a", "b"], ["a"]) == "ORIGIN_AHEAD"
    assert T.classify(["a", "x"], ["a", "y"]) == "BOTH"
    o = ["---", "name: n", "superseded_by: m", "---", "body", "zebras graze 9 km north"]
    lv = ["---", "name: n", "---", "body", "Otters: QUICK swimmers?"]
    out, k = T.merge(o, lv)
    assert out[:4] == ["---", "name: n", "superseded_by: m", "---"], out
    assert "Otters: QUICK swimmers?" in out and "zebras graze 9 km north" in out
    assert k == 2
    # the old form of a line edited here is not appended again
    out2, k2 = T.merge(["body", "the result is 41.5 percent"], ["body", "the result is 41.7 percent"])
    assert k2 == 0 and out2 == ["body", "the result is 41.7 percent"]


def test_authored_skips_generated_and_hubs():
    assert T.authored("a_fact_2026_01_01.md")
    assert not T.authored("checklist_task_t1.md") and not T.authored("term_x.md")
    assert not T.authored("MISTAKES.md") and not T.authored("topic_hub.md")


def test_end_to_end_apply_no_push():
    d = tempfile.mkdtemp(prefix="twoway_test_")
    saved = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE")}
    os.environ["HOME"] = os.environ["USERPROFILE"] = d
    try:
        origin, seed, clone, mem = (os.path.join(d, x) for x in ("origin.git", "seed", "clone", "mem"))
        run(d, "init", "-q", "--bare", "-b", "main", origin)
        run(d, "clone", "-q", origin, seed)
        os.makedirs(os.path.join(seed, "memory"))
        files = {
            "ahead_2026_01_01.md": FM % "ahead" + "one\ntwo from the other computer\n",
            "live_2026_01_01.md": FM % "live" + "one\n",
            "both_2026_01_01.md": FM % "both" + "shared\nan addendum written on the other computer about lighthouses\n",
            "checklist_task_t9.md": "generated there\n",
        }
        for n, t in files.items():
            write(os.path.join(seed, "memory", n), t)
        run(seed, "checkout", "-q", "-b", "main")
        run(seed, "add", "-A")
        run(seed, "commit", "-q", "-m", "seed")
        run(seed, "push", "-q", "origin", "main")
        run(d, "clone", "-q", origin, clone)
        os.makedirs(mem)
        write(os.path.join(mem, "ahead_2026_01_01.md"), FM % "ahead" + "one\n")
        write(os.path.join(mem, "live_2026_01_01.md"), FM % "live" + "one\nmore written here\n")
        write(os.path.join(mem, "both_2026_01_01.md"), FM % "both" + "shared\na local note about harbours and tides\n")
        write(os.path.join(mem, "only_here_2026_01_01.md"), FM % "only" + "new\n")
        write(os.path.join(mem, "checklist_task_t9.md"), "generated here\n")
        args = ["--repo", clone, "--memory-dir", mem, "--mirror", "memory", "--days", "1"]
        assert T.main(args) == 0                                         # dry run writes nothing
        assert read(os.path.join(mem, "ahead_2026_01_01.md")).endswith("one\n")
        assert T.main(args + ["--apply", "--no-push"]) == 0
        assert read(os.path.join(mem, "ahead_2026_01_01.md")).endswith("two from the other computer\n")
        both = read(os.path.join(mem, "both_2026_01_01.md"))
        assert "a local note about harbours and tides" in both and "lighthouses" in both, both
        assert read(os.path.join(mem, "live_2026_01_01.md")).endswith("more written here\n")
        assert read(os.path.join(mem, "checklist_task_t9.md")) == "generated here\n"
        bk = T.backup_dir()
        assert bk.startswith(d) and os.path.isfile(os.path.join(bk, "ahead_2026_01_01.md"))
        assert os.path.isfile(os.path.join(bk, "both_2026_01_01.md"))
        assert run(origin, "log", "--oneline", "main").count("\n") == 1  # nothing pushed
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
