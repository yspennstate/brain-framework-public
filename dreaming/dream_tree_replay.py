"""dream_tree_replay.py - replay the local commits of a brain repo onto the advertised main without a worktree.

    python dream_tree_replay.py <repo> <onto_oid> [<memory_dir> [<mirror_prefix>]]

Exit 0 and print the new head sha when every commit in <onto>..HEAD replays with `git merge-tree --write-tree` (a
commit that becomes empty against the new base is dropped, as `git rebase` drops it, and a commit whose patch is
already upstream is skipped). Exit 3 on a conflict it cannot settle, a merge commit, a signed or re-encoded commit, or
a git error, so the caller can fall back to an ordinary rebase. It writes only objects: no ref, no working tree, and
only a private temporary index. Moving the branch to the printed sha is the caller's job.

Why: rebasing local commits in a temporary worktree checks out every file of the repo. A brain repo holds hundreds of
thousands of small files, and on a slow disk that checkout alone can take a quarter of an hour to replay one commit.
merge-tree computes the same merge from the object database in seconds.

A conflicted path is settled only when the answer already exists byte for byte; nothing new is ever written:
- MEMORY MIRROR: a path under <mirror_prefix> (default `memory/`, or DREAM_SYNC_MEMORY_PREFIX) that ends in .md takes
  the live memory file from <memory_dir>, when one is given and the file exists. The live memory folder is the
  source the mirror copies, so its bytes are the right answer.
- OTHER MIRRORS: paths that copy a live file outside the memory folder, given by the caller in DREAM_SYNC_MIRRORS as
  JSON {"prefixes": {repo_prefix: live_dir}, "files": {repo_path: live_file}}. Such a path takes its live file only
  when that file equals one of the two sides, so the result is always a version that exists.
- CHECKOUT: a path whose copy in the repo's own working tree equals the new base's side takes that side; the checkout
  had already moved on from the local commit's version, so nothing it holds is lost.
- GENERATED: a path under the generated-views prefix (default `brain/`, or DREAM_SYNC_GENERATED_PREFIX), which every
  dream rebuilds from the memories, takes the local commit's side.
Any other conflict declines.
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import json
import os
import subprocess
import sys
import tempfile

NOWIN = 0x08000000 if os.name == "nt" else 0
MIRROR_PREFIX = os.environ.get("DREAM_SYNC_MEMORY_PREFIX", "memory/")
GENERATED_PREFIX = os.environ.get("DREAM_SYNC_GENERATED_PREFIX", "brain/")


def _mirror_table():
    raw = os.environ.get("DREAM_SYNC_MIRRORS", "")
    if not raw:
        return {}, {}
    try:
        t = json.loads(raw)
        return dict(t.get("prefixes") or {}), dict(t.get("files") or {})
    except Exception:
        return {}, {}


def _safe(path):
    return not ("~" in path or ".." in path.split("/") or path.startswith("/"))


def other_mirror_live(path):
    """The live file a non-memory mirror path copies, if it exists; None otherwise."""
    if not _safe(path):
        return None
    prefixes, files = _mirror_table()
    live = files.get(path)
    if live is None:
        for pre, d in prefixes.items():
            if path.startswith(pre):
                live = os.path.join(d, *path[len(pre):].split("/"))
                break
    return live if live and os.path.isfile(live) else None


def checkout_copy(repo, path):
    """The working tree's own copy of a conflicted path, if it is a plain file inside the repo; None otherwise."""
    if not _safe(path):
        return None
    f = os.path.join(repo, *path.split("/"))
    return f if os.path.isfile(f) else None


def git(repo, *args, inp=None, env=None):
    p = subprocess.run(["git", "-C", repo] + list(args), input=inp, capture_output=True, env=env,
                       creationflags=NOWIN)
    return p.returncode, p.stdout, p.stderr


def live_path(memory_dir, prefix, path):
    if not memory_dir or not path.startswith(prefix) or not _safe(path) or not path.endswith(".md"):
        return None
    live = os.path.join(memory_dir, *path[len(prefix):].split("/"))
    return live if os.path.isfile(live) else None


def _blob(repo, rev, path):
    rc, out, _ = git(repo, "rev-parse", "-q", "--verify", "%s:%s" % (rev, path))
    return out.decode("ascii", "replace").strip() if rc == 0 else None


def _hash(repo, path, f):
    rc, sha, _ = git(repo, "hash-object", "--path", path, "--", f)
    return sha.decode("ascii", "replace").strip() if rc == 0 else None


def resolve_from_live(repo, tree, paths, memory_dir, prefix, sides=(), other=None, rules=None):
    """Return the tree with each conflicted path settled, or None. sides = (new base, local commit). `other`, if a
    list, collects the non-memory mirror paths settled; `rules`, if a dict, collects {path: "checkout" | "generated"}."""
    lives, blobs = {}, {}
    for p in paths:
        live = live_path(memory_dir, prefix, p)
        if live is None:
            live = other_mirror_live(p)
            if live is None:
                if len(sides) != 2:
                    return None
                if GENERATED_PREFIX and p.startswith(GENERATED_PREFIX):
                    blob = _blob(repo, sides[1], p)
                    if blob is None:
                        return None
                    blobs[p] = blob
                    if rules is not None:
                        rules[p] = "generated"
                    continue
                cc = checkout_copy(repo, p)
                base_blob = _blob(repo, sides[0], p)
                if cc is None or base_blob is None or _hash(repo, p, cc) != base_blob:
                    return None
                blobs[p] = base_blob
                if rules is not None:
                    rules[p] = "checkout"
                continue
            if _hash(repo, p, live) not in [b for b in (_blob(repo, s, p) for s in sides) if b]:
                return None
            if other is not None:
                other.append(p)
        lives[p] = live
    if not paths:
        return None
    fd, idx = tempfile.mkstemp(prefix="tree_replay_idx_")
    os.close(fd)
    os.remove(idx)
    env = dict(os.environ, GIT_INDEX_FILE=idx)
    try:
        if git(repo, "read-tree", tree, env=env)[0] != 0:
            return None
        for p, live in lives.items():
            rc, blob, _ = git(repo, "hash-object", "-w", "--path", p, "--", live)
            if rc != 0:
                return None
            blobs[p] = blob.decode("ascii").strip()
        for p, blob in blobs.items():
            rc, _, _ = git(repo, "update-index", "--add", "--cacheinfo", "100644,%s,%s" % (blob, p), env=env)
            if rc != 0:
                return None
        rc, out, _ = git(repo, "write-tree", "--missing-ok", env=env)   # a blob:none clone: no lazy fetch
        return out.decode("ascii").strip() if rc == 0 else None
    finally:
        if os.path.exists(idx):
            os.remove(idx)


def replay(repo, onto, memory_dir=None, prefix=MIRROR_PREFIX):
    replay.skipped = 0
    rc, out, _ = git(repo, "config", "--get", "commit.gpgsign")
    if rc == 0 and out.strip().lower() in (b"true", b"1", b"yes", b"on"):
        return None, "commit signing is configured", []
    rc, merges, _ = git(repo, "rev-list", "--merges", onto + "..HEAD")
    if rc != 0 or merges.strip():
        return None, "a merge commit is in the range", []
    # A local commit whose patch is already upstream is skipped, as `git rebase` skips it: a change committed locally
    # and also pushed another way would otherwise conflict once the file moved again upstream.
    rc, out, _ = git(repo, "rev-list", "--reverse", "--right-only", "--cherry-pick", "--no-merges", onto + "...HEAD")
    if rc != 0:
        return None, "rev-list failed", []
    rc, every, _ = git(repo, "rev-list", onto + "..HEAD")
    if rc != 0:
        return None, "rev-list failed", []
    replay.skipped = len(every.split()) - len(out.split())
    base = onto
    resolved = []
    for c in out.decode("ascii", "replace").split():
        rc, raw, _ = git(repo, "cat-file", "commit", c)
        if rc != 0:
            return None, "cat-file failed on " + c, []
        header, _, msg = raw.partition(b"\n\n")
        parents, author, extra = [], None, []
        for line in header.split(b"\n"):
            key, _, val = line.partition(b" ")
            if key == b"parent":
                parents.append(val.decode("ascii"))
            elif key == b"author":
                author = val
            elif key in (b"encoding", b"gpgsig", b"gpgsig-sha256", b"mergetag"):
                extra.append(key.decode())
        if len(parents) != 1 or author is None or extra:
            return None, "commit %s is not a plain single-parent commit (%s)" % (c[:10], ",".join(extra) or "parents"), []
        rc, mt, _ = git(repo, "merge-tree", "--write-tree", "--name-only", "--no-messages",
                        "--merge-base=" + parents[0], base, c)
        lines = [l for l in mt.decode("utf-8", "replace").split("\n") if l.strip()]
        if rc == 1 and lines:
            conflicted = lines[1:]
            other, rules = [], {}
            tree = resolve_from_live(repo, lines[0].strip(), conflicted, memory_dir, prefix, sides=(base, c),
                                     other=other, rules=rules)
            if tree is None:
                return None, "merge-tree %s on %s conflicted on %s" % (c[:10], base[:10], ", ".join(conflicted[:3])), []
            resolved.extend((rules[p] + ":" + p) if p in rules else ("other:" + p) if p in other else p
                            for p in conflicted)
        elif rc != 0 or not lines:
            return None, "merge-tree %s on %s (exit %d)" % (c[:10], base[:10], rc), []
        else:
            tree = lines[0].strip()
        rc, btree, _ = git(repo, "rev-parse", base + "^{tree}")
        if rc != 0:
            return None, "rev-parse failed", []
        if tree == btree.decode("ascii").strip():
            continue                                   # became empty: already upstream
        a = author.decode("utf-8", "replace")
        name, _, rest = a.partition(" <")
        email, _, when = rest.partition("> ")
        env = dict(os.environ, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email, GIT_AUTHOR_DATE=when)
        rc, new, err = git(repo, "commit-tree", tree, "-p", base, inp=msg, env=env)
        if rc != 0:
            return None, "commit-tree failed: " + err.decode("utf-8", "replace")[:200], []
        base = new.decode("ascii").strip()
    return base, "ok", resolved


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) not in (2, 3, 4):
        print("usage: dream_tree_replay.py <repo> <onto_oid> [<memory_dir> [<mirror_prefix>]]")
        return 2
    memory_dir = argv[2] if len(argv) > 2 else None
    prefix = argv[3] if len(argv) > 3 else MIRROR_PREFIX
    head, why, resolved = replay(argv[0], argv[1], memory_dir, prefix)
    if head is None:
        print("declined: " + why)
        return 3
    if replay.skipped:
        print("skipped %d local commit(s) already upstream as identical patches" % replay.skipped)
    for tag, what in (("checkout:", "to the new base, whose bytes the working tree already holds"),
                      ("generated:", "with the local commit's regenerated view"),
                      ("other:", "from a live file equal to one side")):
        got = [p[len(tag):] for p in resolved if p.startswith(tag)]
        if got:
            print("resolved %d conflict(s) %s: %s" % (len(got), what, ", ".join(p.rsplit("/", 1)[-1] for p in got[:6])))
    mem = [p for p in resolved if not p.startswith(("other:", "checkout:", "generated:"))]
    if mem:
        print("resolved %d memory-mirror conflict(s) from the live files: %s" % (
            len(mem), ", ".join(p.rsplit("/", 1)[-1] for p in mem[:6])))
    print(head)
    return 0


if __name__ == "__main__":
    sys.exit(main())
