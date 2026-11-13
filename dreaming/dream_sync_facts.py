"""dream_sync_facts.py - the per-file git facts dream_git_sync.ps1's parking loops need, in four git calls instead of
two or three git processes per file.

    python dream_sync_facts.py <repo> <target_oid> <paths_file> <out_tsv>

<paths_file> holds repo-relative paths, one per line, UTF-8, exactly as `git diff --name-only` printed them. For each,
<out_tsv> gets one line: path, exists (1/0, as Test-Path -LiteralPath answers), tracked (1/0, as
`git ls-files --error-unmatch -- path` answers), the local blob id (`git hash-object -- path`, only for a path that
exists as a file and is either untracked or edited against HEAD; empty otherwise) and its blob id in <target_oid>
(`git rev-parse <target>:path`; empty when absent). The caller keeps every decision; this file only answers. Exit 0 on
success; anything else means the caller falls back to its per-file calls.

Why: one parking loop over 1,141 incoming paths ran 34 minutes - about 1,300 git process starts on a loaded
machine - and during those minutes live memory files moved on and git refused the move.
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)
import os
import subprocess
import sys

NOWIN = 0x08000000 if os.name == "nt" else 0


def git(repo, args, inp=None):
    p = subprocess.run(["git", "-C", repo] + args, input=inp, capture_output=True, creationflags=NOWIN)
    if p.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args[:3]), p.stderr.decode("utf-8", "replace")[:200]))
    return p.stdout


def main():
    repo, target, paths_file, out_tsv = sys.argv[1:5]
    paths = [l.rstrip("\r\n") for l in open(paths_file, encoding="utf-8-sig") if l.rstrip("\r\n")]
    tracked = set(git(repo, ["ls-files", "-z"]).decode("utf-8", "surrogateescape").split("\0"))
    edited = set(git(repo, ["-c", "core.quotepath=false", "diff", "--name-only", "-z", "HEAD"])
                 .decode("utf-8", "surrogateescape").split("\0"))
    exists = {p: os.path.lexists(os.path.join(repo, p)) for p in paths}
    is_tracked = {p: p in tracked for p in paths}
    want_local = [p for p in paths if exists[p] and os.path.isfile(os.path.join(repo, p))
                  and (not is_tracked[p] or p in edited)]
    local = {}
    if want_local:
        out = git(repo, ["hash-object", "--stdin-paths"], ("\n".join(want_local) + "\n").encode("utf-8"))
        shas = out.decode("ascii").split()
        if len(shas) != len(want_local):
            raise RuntimeError("hash-object returned %d ids for %d paths" % (len(shas), len(want_local)))
        local = dict(zip(want_local, shas))
    incoming = {}
    if paths:
        out = git(repo, ["cat-file", "--batch-check=%(objectname) %(objecttype)"],
                  ("".join("%s:%s\n" % (target, p) for p in paths)).encode("utf-8"))
        rows = out.decode("utf-8", "replace").splitlines()
        if len(rows) != len(paths):
            raise RuntimeError("cat-file returned %d rows for %d paths" % (len(rows), len(paths)))
        for p, row in zip(paths, rows):
            parts = row.split()
            incoming[p] = parts[0] if len(parts) == 2 and parts[1] in ("blob", "tree", "commit") else ""
    tmp = out_tsv + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        for p in paths:
            fh.write("%s\t%d\t%d\t%s\t%s\n" % (p, exists[p], is_tracked[p], local.get(p, ""), incoming.get(p, "")))
    os.replace(tmp, out_tsv)
    return 0


if __name__ == "__main__":
    if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
        print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
        raise SystemExit(0)
    try:
        sys.exit(main())
    except Exception as exc:
        print("dream_sync_facts: " + str(exc)[:300], file=sys.stderr)
        sys.exit(3)
