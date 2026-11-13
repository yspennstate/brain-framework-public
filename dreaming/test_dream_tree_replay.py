"""test_dream_tree_replay.py - the dream's sync replay on throwaway git repos: a clean replay, a patch already
upstream skipped, a memory-mirror conflict settled from the live file, a generated view taking the local side, a
conflict whose working-tree copy equals the new base, and a conflict it must decline (exit 3)."""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import os
import shutil
import subprocess
import tempfile

import dream_tree_replay as R


def sh(repo, *args):
    p = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True)
    assert p.returncode == 0, (args, p.stderr)
    return p.stdout.strip()


def write(repo, path, text):
    f = os.path.join(repo, *path.split("/"))
    os.makedirs(os.path.dirname(f), exist_ok=True)
    with open(f, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def commit(repo, files, msg):
    for p, t in files.items():
        write(repo, p, t)
        sh(repo, "add", p)
    sh(repo, "commit", "-q", "-m", msg)
    return sh(repo, "rev-parse", "HEAD")


def show(repo, rev, path):
    return sh(repo, "show", "%s:%s" % (rev, path))


def make_repo():
    d = tempfile.mkdtemp(prefix="dtr_test_")
    sh(d, "init", "-q", "-b", "main")
    for k, v in (("user.name", "t"), ("user.email", "t@example.invalid"), ("commit.gpgsign", "false"),
                 ("core.autocrlf", "false")):
        sh(d, "config", k, v)
    base = commit(d, {"memory/a.md": "a0\n", "brain/v.md": "v0\n", "f.txt": "f0\n"}, "base")
    return d, base


def upstream(d, base, files):
    """Make the 'advertised main': a commit on top of base, on a side branch, then return to the local branch."""
    sh(d, "checkout", "-q", "-b", "up", base)
    up = commit(d, files, "upstream")
    sh(d, "checkout", "-q", "main")
    return up


def run(d, up, memory_dir=None):
    head, why, resolved = R.replay(d, up, memory_dir, "memory/")
    return head, why, resolved


def test_clean_replay():
    d, base = make_repo()
    try:
        commit(d, {"g.txt": "g\n"}, "local")
        up = upstream(d, base, {"h.txt": "h\n"})
        head, why, _ = run(d, up)
        assert head and why == "ok", why
        assert sh(d, "rev-parse", head + "^") == up
        assert show(d, head, "g.txt") == "g" and show(d, head, "h.txt") == "h"
        assert sh(d, "log", "-1", "--format=%s", head) == "local"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_patch_already_upstream_is_skipped():
    d, base = make_repo()
    try:
        commit(d, {"g.txt": "g\n"}, "local")
        up = upstream(d, base, {"g.txt": "g\n"})
        head, why, _ = run(d, up)
        assert head == up and R.replay.skipped == 1, (head, why)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_memory_mirror_conflict_takes_the_live_file():
    d, base = make_repo()
    live = tempfile.mkdtemp(prefix="dtr_mem_")
    try:
        commit(d, {"memory/a.md": "local\n"}, "local")
        up = upstream(d, base, {"memory/a.md": "upstream\n"})
        assert run(d, up)[0] is None                      # no memory folder given: declines
        with open(os.path.join(live, "a.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("live\n")
        head, why, resolved = run(d, up, live)
        assert head, why
        assert show(d, head, "memory/a.md") == "live" and resolved == ["memory/a.md"]
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(live, ignore_errors=True)


def test_generated_view_takes_the_local_side():
    d, base = make_repo()
    try:
        commit(d, {"brain/v.md": "v-local\n"}, "local")
        up = upstream(d, base, {"brain/v.md": "v-up\n"})
        head, why, resolved = run(d, up)
        assert head, why
        assert show(d, head, "brain/v.md") == "v-local" and resolved == ["generated:brain/v.md"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_checkout_equal_to_base_takes_base_and_otherwise_declines():
    d, base = make_repo()
    try:
        commit(d, {"f.txt": "f-local\n"}, "local")
        up = upstream(d, base, {"f.txt": "f-up\n"})
        head, why, _ = run(d, up)                         # working tree holds f-local: neither rule applies
        assert head is None and "conflicted on f.txt" in why, why
        assert R.main([d, up]) == 3
        write(d, "f.txt", "f-up\n")                       # the checkout already moved on to upstream's bytes
        head, why, resolved = run(d, up)
        assert head, why
        assert show(d, head, "f.txt") == "f-up" and resolved == ["checkout:f.txt"]
        assert sh(d, "rev-parse", head + "^{tree}") == sh(d, "rev-parse", up + "^{tree}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_merge_commit_declines_and_nothing_moves():
    d, base = make_repo()
    try:
        sh(d, "checkout", "-q", "-b", "side", base)
        commit(d, {"s.txt": "s\n"}, "side")
        sh(d, "checkout", "-q", "main")
        commit(d, {"g.txt": "g\n"}, "local")
        sh(d, "merge", "-q", "--no-edit", "side")
        before = sh(d, "rev-parse", "HEAD")
        head, why, _ = R.replay(d, base)
        assert head is None and "merge commit" in why
        assert sh(d, "rev-parse", "HEAD") == before
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
