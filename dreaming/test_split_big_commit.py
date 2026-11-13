"""test_split_big_commit.py - an oversized local commit is split into parts that each fit the scan budget, the
final tree and every later commit are unchanged, and the tool refuses pushed history, small commits and a blob
too big for any part.

    python test_split_big_commit.py

Works in a scratch git repository; touches nothing else.
 T1  --dry-run lists the parts and moves no ref
 T2  a real run: main keeps its tree, the later commit keeps its tree and message, a backup ref holds the old tip,
     and each part adds at most the budget in new blobs
 T3  a commit already in the pushed base is refused
 T4  a commit within the budget is left alone
 T5  a single blob over the budget is refused
"""
if __name__ == "__main__" and __import__("sys").argv[1:2] in (["-h"], ["--help"]):
    print((__doc__ or "").strip() or "run with no arguments")
    raise SystemExit(0)

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "split_big_commit.py")
fails, ran = [], []
KB = 1000


def check(ok, what):
    ran.append(what)
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        fails.append(what)


def git(repo, *a, inp=None):
    p = subprocess.run(["git", "-C", repo, *a], capture_output=True, input=inp)
    if p.returncode:
        raise RuntimeError("git %s: %s" % (" ".join(a), p.stderr.decode(errors="replace")))
    return p.stdout.decode().strip()


def tool(repo, *a):
    p = subprocess.run([sys.executable, TOOL, *a, "--repo", repo], capture_output=True)
    return p.returncode, p.stdout.decode(errors="replace") + p.stderr.decode(errors="replace")


def write(repo, name, size, seed):
    path = os.path.join(repo, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(os.urandom(size) if seed is None else bytes((seed + i) % 251 for i in range(size)))


def commit(repo, msg):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


def new_repo(tmp, name):
    repo = os.path.join(tmp, name)
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    for k, v in (("user.name", "Test"), ("user.email", "test@example.invalid"), ("commit.gpgsign", "false"),
                 ("core.autocrlf", "false")):
        git(repo, "config", k, v)
    write(repo, "readme.txt", 100, 1)
    base = commit(repo, "base")
    return repo, base


def added_bytes(repo, c):
    out = git(repo, "diff-tree", "-r", "--no-renames", c + "^", c).splitlines()
    oids = [line.split()[3] for line in out if line.startswith(":") and line.split()[4] != "D"]
    return sum(int(git(repo, "cat-file", "-s", o)) for o in set(oids))


def main():
    tmp = tempfile.mkdtemp(prefix="split_test_")
    try:
        repo, base = new_repo(tmp, "r1")
        for k in range(5):
            write(repo, "data/f%d.bin" % k, 300 * KB, None)
        os.remove(os.path.join(repo, "readme.txt"))
        big = commit(repo, "big change\n\nbody line")
        write(repo, "later.txt", 50, 7)
        later = commit(repo, "later change")
        tip_tree = git(repo, "rev-parse", "main^{tree}")

        rc, out = tool(repo, big, "--budget", "0.7", "--base", base, "--dry-run")
        check(rc == 0 and "part 3:" in out and git(repo, "rev-parse", "main") == later,
              "T1 dry run: rc %d, three parts listed, main unmoved" % rc)

        rc, out = tool(repo, big, "--budget", "0.7", "--base", base)
        new_tip = git(repo, "rev-parse", "main")
        chain = git(repo, "rev-list", "--reverse", base + "..main").split()
        backups = git(repo, "for-each-ref", "--format=%(objectname)", "refs/backup/")
        sizes = [added_bytes(repo, c) for c in chain[:-1]]
        check(rc == 0 and new_tip != later and git(repo, "rev-parse", "main^{tree}") == tip_tree
              and git(repo, "log", "-1", "--format=%B", "main").strip() == "later change"
              and git(repo, "rev-parse", "main~1^{tree}") == git(repo, "rev-parse", big + "^{tree}")
              and backups == later and len(chain) == 4 and all(s <= 700 * KB for s in sizes),
              "T2 split: rc %d, %d commits, part sizes %r, trees kept, backup %s" % (rc, len(chain), sizes,
                                                                                     backups == later))

        rc, out = tool(repo, chain[0], "--budget", "0.7", "--base", new_tip)
        check(rc != 0 and "never rewrite pushed history" in out, "T3 pushed commit refused: rc %d" % rc)

        repo2, base2 = new_repo(tmp, "r2")
        write(repo2, "small.bin", 20 * KB, 3)
        small = commit(repo2, "small")
        rc, out = tool(repo2, small, "--budget", "0.7", "--base", base2)
        check(rc != 0 and "nothing to do" in out and git(repo2, "rev-parse", "main") == small,
              "T4 small commit left alone: rc %d" % rc)

        repo3, base3 = new_repo(tmp, "r3")
        write(repo3, "huge.bin", 900 * KB, None)
        huge = commit(repo3, "huge")
        rc, out = tool(repo3, huge, "--budget", "0.7", "--base", base3)
        check(rc != 0 and "over the per-part budget" in out and git(repo3, "rev-parse", "main") == huge,
              "T5 oversized blob refused: rc %d" % rc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)), flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
