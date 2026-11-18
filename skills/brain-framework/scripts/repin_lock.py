"""Re-pin references/upstream-lock.json (and the revision named in references/source-map.md) to a commit of the
repository this skill ships in, or check that the checkout still matches the lock.

    python skills/brain-framework/scripts/repin_lock.py <full commit sha>     # re-pin
    python skills/brain-framework/scripts/repin_lock.py --check               # exit 1 if the checkout drifted

The source root is the folder that holds skills/ (the repository root in brain-framework-public, framework/ in
math-brain-public). The lock lists every file under it at that commit with its Git blob sha, relative to the source
root, except the skill's own folder: the lock lives there and cannot hash itself. So pin to the commit BEFORE the
one that carries the new lock, and make the re-pin the last commit of any push that touches files outside the skill.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).absolute().parent.parent
ROOT = SKILL.parent.parent
LOCK = SKILL / "references" / "upstream-lock.json"
SMAP = SKILL / "references" / "source-map.md"


def git(*args, binary=False):
    out = subprocess.run(["git", "-C", str(ROOT)] + list(args), capture_output=True, check=True).stdout
    return out if binary else out.decode("utf-8").strip()


def blob_sha(path):
    raw = path.read_bytes()
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def check():
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    bad = [r["path"] for r in lock["files"] if not (ROOT / r["path"]).is_file() or blob_sha(ROOT / r["path"]) != r["sha"]]
    print(json.dumps({"revision": lock["revision"], "files": len(lock["files"]), "drifted": len(bad), "first": bad[:10]}))
    if not lock["files"]:
        print("the lock lists no files", file=sys.stderr)
        return 1
    if bad:
        print("the checkout differs from the lock; re-pin with: repin_lock.py <commit>  (on Windows also check "
              "git ls-files --eol for w/crlf: the lock hashes the committed LF bytes)", file=sys.stderr)
    return 1 if bad else 0


def repin(revision, repository):
    top = Path(git("rev-parse", "--show-toplevel")).absolute()
    subdir = ROOT.absolute().relative_to(top).as_posix()
    subdir = "" if subdir == "." else subdir + "/"
    skill_prefix = SKILL.absolute().relative_to(ROOT.absolute()).as_posix() + "/"
    if not repository:
        url = git("config", "--get", "remote.origin.url")
        m = re.search(r"github\.com[:/]([\w.\-]+/[\w.\-]+?)(?:\.git)?$", url)
        if not m:
            raise SystemExit("cannot read the GitHub repository from origin; pass --repository-url")
        repository = "https://github.com/" + m.group(1)
    files = []
    for row in git("ls-tree", "-r", "-z", "--full-tree", revision, binary=True).split(b"\0"):
        if not row:
            continue
        meta, path = row.split(b"\t", 1)
        mode, kind, sha = meta.decode().split()
        path = path.decode("utf-8")
        if not path.startswith(subdir):
            continue
        path = path[len(subdir):]
        if path.startswith(skill_prefix):
            continue
        if kind != "blob" or mode not in ("100644", "100755"):
            raise SystemExit("link or submodule needs review: " + subdir + path)
        files.append({"path": path, "sha": sha})
    if not files:
        raise SystemExit("no files under %r at %s; nothing to pin" % (subdir or "the root", revision))
    old =json.loads(LOCK.read_text(encoding="utf-8"))
    lock = {"repository": repository, "revision": revision, "subdir": subdir, "excluded_prefixes": [skill_prefix],
            "files": sorted(files, key=lambda r: r["path"])}
    with open(LOCK, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(lock, indent=2) + "\n")
    text = SMAP.read_text(encoding="utf-8").replace(old["revision"], revision)
    text = text.replace(old.get("repository", repository), repository)
    with open(SMAP, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(json.dumps({"repository": repository, "revision": revision, "subdir": subdir, "files": len(files),
                      "excluded": skill_prefix, "previous": old["revision"]}))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("revision", nargs="?")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--repository-url", default=None, help="default: read from the checkout's origin")
    args = ap.parse_args()
    if args.check:
        return check()
    if not args.revision or not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        raise SystemExit("give the full 40-character commit sha (or --check)")
    return repin(args.revision, args.repository_url)


if __name__ == "__main__":
    sys.exit(main())
