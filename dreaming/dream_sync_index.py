"""dream_sync_index.py - let the dream's checkout move keep local files in place instead of deleting or parking them.

    python dream_sync_index.py stage <repo> <old> <target> <paths_file> <staged_out> <undo_out>
    python dream_sync_index.py undo  <repo> <undo_file>

`git reset --keep <target>` refuses a path whose local file would be overwritten: an untracked copy of an incoming file,
or a tracked file edited here. dream_git_sync.ps1 used to delete the untracked copies (identical ones outright,
differing ones after copying them aside) and to revert edited files to HEAD, let the move write the incoming version,
and put the local bytes back. For those minutes the files were missing, and a job that read one of them in that
window failed.

`stage` sets the INDEX entry of each listed path to the target's own entry (mode and blob, from one
`git diff --raw -z old target`), with one `git update-index -z --index-info`, and never touches the working tree. git's
two-way merge (reset --keep) keeps an index entry that already equals the target's and leaves its file alone; after the
move the file is tracked at the incoming version, clean when its bytes match and shown as an edit when they differ -
the same end state as delete-and-restore, without the gap. Only paths the target adds or modifies as a regular file or
symlink are staged; everything else (a deletion, a submodule, a path not in the diff) is left to the caller's old
handling and simply not listed in <staged_out>. <undo_out> gets the index entries to put back if the move is refused:
the HEAD entry for a tracked path, mode 0 (remove) for an untracked one. `undo` feeds that file to --index-info.

Exit 0 on success (possibly staging nothing); anything else means nothing was changed and the caller keeps its old path.
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)
import os
import subprocess
import sys

NOWIN = 0x08000000 if os.name == "nt" else 0
ZERO = "0" * 40
MODES = ("100644", "100755", "120000")


def git(repo, args, inp=None):
    p = subprocess.run(["git", "-C", repo] + args, input=inp, capture_output=True, creationflags=NOWIN)
    if p.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args[:3]), p.stderr.decode("utf-8", "replace")[:200]))
    return p.stdout


def raw_diff(repo, old, target):
    out = git(repo, ["-c", "core.quotepath=false", "diff", "--raw", "-z", "--no-renames", "--no-abbrev", old, target])
    parts = out.split(b"\0")
    entries = {}
    i = 0
    while i + 1 < len(parts) and parts[i].startswith(b":"):
        meta = parts[i][1:].decode("ascii").split()
        path = parts[i + 1].decode("utf-8", "surrogateescape")
        if len(meta) == 5:
            entries[path] = {"old_mode": meta[0], "new_mode": meta[1], "old_oid": meta[2], "new_oid": meta[3],
                             "status": meta[4]}
        i += 2
    return entries


def current_index(repo, paths):
    """{path: (mode, oid)} for stage-0 index entries of the given paths (absent = untracked)."""
    if not paths:
        return {}
    # a pathspec per path would overflow the command line on a long list; read the whole index once
    out = git(repo, ["-c", "core.quotepath=false", "ls-files", "-s", "-z"])
    want = set(paths)
    have = {}
    for rec in out.split(b"\0"):
        if not rec:
            continue
        head, _, path = rec.partition(b"\t")
        p = path.decode("utf-8", "surrogateescape")
        if p in want:
            mode, oid, stage = head.decode("ascii").split()
            if stage == "0":
                have[p] = (mode, oid)
            else:
                have[p] = ("conflict", oid)
    return have


def stage(repo, old, target, paths_file, staged_out, undo_out):
    paths = [l.rstrip("\r\n") for l in open(paths_file, encoding="utf-8-sig") if l.rstrip("\r\n")]
    raw = raw_diff(repo, old, target)
    idx = current_index(repo, paths)
    todo, undo = [], []
    for p in dict.fromkeys(paths):
        e = raw.get(p)
        if not e or e["status"] not in ("A", "M", "T") or e["new_mode"] not in MODES or e["new_oid"] == ZERO:
            continue
        cur = idx.get(p)
        if cur is not None and cur[0] == "conflict":
            continue
        todo.append((e["new_mode"], e["new_oid"], p))
        undo.append((cur[0], cur[1], p) if cur is not None else ("0", ZERO, p))
    # the undo file is written BEFORE the index changes, so a crash in between can still be undone
    tmp = undo_out + ".tmp"
    with open(tmp, "wb") as fh:
        for mode, oid, p in undo:
            fh.write(("%s %s\t" % (mode, oid)).encode("ascii") + p.encode("utf-8", "surrogateescape") + b"\0")
    os.replace(tmp, undo_out)
    if todo:
        data = b"".join(("%s %s\t" % (mode, oid)).encode("ascii") + p.encode("utf-8", "surrogateescape") + b"\0"
                        for mode, oid, p in todo)
        git(repo, ["update-index", "-z", "--index-info"], data)
    tmp = staged_out + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        for _mode, _oid, p in todo:
            fh.write(p + "\n")
    os.replace(tmp, staged_out)
    return 0


def undo(repo, undo_file):
    data = open(undo_file, "rb").read()
    if data:
        git(repo, ["update-index", "-z", "--index-info"], data)
    return 0


def main():
    if len(sys.argv) >= 8 and sys.argv[1] == "stage":
        return stage(*sys.argv[2:8])
    if len(sys.argv) >= 4 and sys.argv[1] == "undo":
        return undo(sys.argv[2], sys.argv[3])
    print(__doc__.split("\n\n")[0], file=sys.stderr)
    return 2


if __name__ == "__main__":
    if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
        print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
        raise SystemExit(0)
    try:
        sys.exit(main())
    except Exception as exc:
        print("dream_sync_index: " + str(exc)[:300], file=sys.stderr)
        sys.exit(3)
