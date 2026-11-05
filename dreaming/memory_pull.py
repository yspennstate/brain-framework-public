#!/usr/bin/env python3
"""memory_pull.py - bring memories other computers filed into this computer's memory folder. Insert-only.

When one brain runs on several computers, each computer files its memories into its own folder and one of them (or
each in turn) pushes them to a shared repository. A memory filed on computer A is dark to recall on computer B until
something copies it across. This is that copy, in its smallest safe form.

Sources (pick one):
    --repo owner/name --folder path/in/repo   a GitHub repository, read over the API with fleet_board's auth
                                              (defaults: BRAIN_MEMORY_REPO, BRAIN_MEMORY_FOLDER, branch main or
                                              BRAIN_MEMORY_BRANCH); one tree call lists the folder
    --from-dir PATH                           any folder of memory files: a clone of the shared repository, a
                                              network share, a synced drive

What it does: compares the source with this computer's memory folder (BRAIN_MEM_DIR, else the folder
spread_recall.py names) and writes each file that is MISSING here, newest slug date first, at most --max per run
(default 200). A file that already exists here is never touched (it is opened with O_EXCL), so a local edit is never
overwritten and nothing is ever deleted. Files without frontmatter, names that are not memory slugs, and the shared
indexes (MEMORY.md, INDEX_ARCHIVE.md, MISTAKES.md: each computer keeps its own) are skipped. When anything arrived,
it runs `bm25.py refresh` so recall sees the new files within seconds. Dry run by default; --apply writes.

    python3 memory_pull.py --from-dir ../shared-memories/memory            what would arrive
    python3 memory_pull.py --repo you/brain-memories --folder memory --apply
    python3 memory_pull.py --max 50 --apply
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import base64, json, os, re, subprocess, sys, time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

NAME = re.compile(r"^[a-z0-9][a-z0-9_.\-]{2,200}\.md$")
SKIP = {"MEMORY.md", "INDEX_ARCHIVE.md", "MISTAKES.md"}   # indexes and shared logs are each computer's own
STATE = os.environ.get("BRAIN_MEMORY_PULL_STATE") or os.path.join(
    os.path.expanduser("~"), ".claude", "fleet_board", "memory_pull_state.json")


def mem_dir():
    if os.environ.get("BRAIN_MEM_DIR"):
        return os.environ["BRAIN_MEM_DIR"]
    try:
        import spread_recall
        return spread_recall.MEM
    except Exception:
        return None


class DirSource:
    def __init__(self, path):
        self.path = path

    def names(self):
        return {n: None for n in os.listdir(self.path) if os.path.isfile(os.path.join(self.path, n))}

    def read(self, name):
        with open(os.path.join(self.path, name), "rb") as fh:
            return fh.read()


class RepoSource:
    def __init__(self, repo, folder, branch):
        import fleet_board  # its api() carries this computer's GitHub auth
        self.api = fleet_board.api
        self.repo, self.folder, self.branch = repo, folder.strip("/"), branch

    def names(self):
        """Every file in the folder, from ONE tree listing (the folder's tree sha comes from its parent)."""
        parent, leaf = self.folder.rsplit("/", 1) if "/" in self.folder else ("", self.folder)
        st, items = self.api("GET", "/repos/%s/contents/%s?ref=%s" % (self.repo, parent, self.branch), timeout=30)
        if st != 200 or not isinstance(items, list):
            raise RuntimeError("parent listing failed: %s" % st)
        sha = next((i["sha"] for i in items if i.get("name") == leaf and i.get("type") == "dir"), None)
        if not sha:
            raise RuntimeError("memory folder not on %s" % self.branch)
        st, tree = self.api("GET", "/repos/%s/git/trees/%s" % (self.repo, sha), timeout=60)
        if st != 200 or "tree" not in tree:
            raise RuntimeError("tree listing failed: %s" % st)
        if tree.get("truncated"):
            print("warning: the tree listing was truncated; some files will wait for a later run")
        return {e["path"]: e["sha"] for e in tree["tree"] if e.get("type") == "blob"}

    def read(self, name):
        st, r = self.api("GET", "/repos/%s/contents/%s/%s?ref=%s" % (self.repo, self.folder, name, self.branch),
                         timeout=30)
        if st != 200 or "content" not in r:
            raise RuntimeError("fetch failed: %s" % st)
        return base64.b64decode(r["content"])


def slug_date(name):
    m = re.search(r"(\d{4})_(\d\d)_(\d\d)\.md$", name)
    return m.groups() if m else ("", "", "")


def pull(source, mem, apply=False, cap=200, refresh=True, out=print):
    files = source.names()
    local = set(os.listdir(mem))
    missing = [n for n in files if NAME.match(n) and n not in SKIP and n not in local]
    missing.sort()
    missing.sort(key=slug_date, reverse=True)   # newest first, so a capped run takes the fresh ones
    out("memory folder: %s" % mem)
    out("%d files at the source, %d here, %d at the source and missing here" % (len(files), len(local), len(missing)))
    if cap and len(missing) > cap:
        out("taking the newest %d this run (--max); the rest come on later runs" % cap)
        missing = missing[:cap]
    wrote = []
    for n in missing:
        out(("  pull " if apply else "  would pull ") + n)
        if not apply:
            continue
        try:
            body = source.read(n)
        except Exception as e:
            out("    %s" % e)
            continue
        if not body.lstrip(b"\xef\xbb\xbf").startswith(b"---"):
            out("    skipped: no frontmatter")
            continue
        try:
            fd = os.open(os.path.join(mem, n), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)   # never overwrite
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
        wrote.append(n)
    if apply and wrote and refresh:
        bm = os.path.join(HERE, "bm25.py")
        if os.path.exists(bm):
            try:
                subprocess.run([sys.executable, bm, "refresh"], capture_output=True, timeout=600,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception as e:
                out("bm25 refresh failed: %r" % e)
    if apply:
        out("wrote %d" % len(wrote))
    return wrote


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)

    def opt(flag, default=None):
        return a[a.index(flag) + 1] if flag in a and a.index(flag) + 1 < len(a) else default

    apply = "--apply" in a
    cap = int(opt("--max", "200"))
    mem = mem_dir()
    if not mem or not os.path.isdir(mem):
        sys.exit("memory folder not found (set BRAIN_MEM_DIR)")
    if opt("--from-dir"):
        source = DirSource(opt("--from-dir"))
    else:
        repo = opt("--repo", os.environ.get("BRAIN_MEMORY_REPO"))
        folder = opt("--folder", os.environ.get("BRAIN_MEMORY_FOLDER"))
        if not repo or not folder:
            sys.exit("name a source: --from-dir PATH, or --repo owner/name --folder path (or BRAIN_MEMORY_REPO "
                     "and BRAIN_MEMORY_FOLDER)")
        source = RepoSource(repo, folder, opt("--branch", os.environ.get("BRAIN_MEMORY_BRANCH", "main")))
    wrote = pull(source, mem, apply=apply, cap=cap, refresh="--no-refresh" not in a)
    if apply:
        try:
            os.makedirs(os.path.dirname(STATE), exist_ok=True)
            with open(STATE, "w", encoding="utf-8") as fh:
                json.dump({"last": datetime.now(timezone.utc).isoformat(), "wrote": len(wrote),
                           "at": time.strftime("%Y-%m-%d %H:%M %z")}, fh)
        except OSError:
            pass


if __name__ == "__main__":
    main()
