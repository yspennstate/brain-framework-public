"""test_push_paths_gitdata.py - the Git Data API route lands what was scanned, as one commit, and refuses the rest.

    python test_push_paths_gitdata.py [PATH_TO_push_paths_gitdata.py]

Builds a small git repository in a temporary folder and replaces the GitHub API with a fake, so nothing goes over the
network. Needs git on PATH.
"""
import hashlib
import importlib.util
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(HERE, "push_paths_gitdata.py")
os.environ["PP_POST_GAP"] = "0"
os.environ.setdefault("PP_TOK", "test-token-not-used")
spec = importlib.util.spec_from_file_location("gitdata_under_test", PATH)
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)
G.time.sleep = lambda s: None
FAILS = []


def run(name, ok):
    print("%s  %s" % ("PASS" if ok else "FAIL", name))
    if not ok:
        FAILS.append(name)


def git(*a):
    return subprocess.run(["git"] + list(a), capture_output=True, text=True, check=True).stdout.strip()


root = tempfile.mkdtemp(prefix="gitdata_test_")
os.chdir(root)
git("init", "-q")
git("config", "user.email", "t@example.invalid")
git("config", "user.name", "t")
for n, body in (("a.txt", "one\n"), ("b.txt", "two\n"), ("c.txt", "three\n")):
    with open(n, "w", newline="\n") as fh:
        fh.write(body)
git("add", "-A")
git("commit", "-q", "-m", "base")
BASE = git("rev-parse", "HEAD")
with open("a.txt", "w", newline="\n") as fh:
    fh.write("one changed\n")
git("commit", "-q", "-am", "scanned")
C0 = git("rev-parse", "HEAD")
A_SHA = git("rev-parse", C0 + ":a.txt")


class Fake:
    """GitHub as far as the tool sees it. tips: the tip main shows on each ref read; compares: (x, y) -> files."""

    def __init__(self, tips, compares=None, blob_status=201, blob_sha=None, patch=None, big=None, tipblobs=None):
        self.tips, self.compares = list(tips), compares or {}
        self.blob_status, self.blob_sha, self.patch = blob_status, blob_sha, list(patch or [])
        self.big, self.tipblobs = big or set(), tipblobs
        self.blobs, self.trees, self.commits, self.patches = [], [], [], []
        self.tip = self.tips[0]

    def call(self, slug, method, path, body=None, tries=3, rate_waits=4):
        if method == "GET" and path == "/git/ref/heads/main":
            self.tip = self.tips.pop(0) if len(self.tips) > 1 else self.tips[0]
            return 200, {"object": {"sha": self.tip}}
        if method == "GET" and path.startswith("/compare/"):
            x, y = path[len("/compare/"):].split("...")
            if y == "newcommit":
                return 200, {"status": "ahead", "files": [{"filename": i["path"]} for i in self.trees[-1]]}
            files = self.compares.get((x, y), [])
            if (x, y) in self.big:
                files = [{"filename": "other%d" % i} for i in range(300)]
            return 200, {"status": "ahead", "files": [{"filename": f} for f in files] if files and isinstance(files[0], str) else files}
        if method == "GET" and path.startswith("/git/commits/"):
            return 200, {"tree": {"sha": "tree-of-" + path.rsplit("/", 1)[1]}}
        if method == "POST" and path == "/git/blobs":
            import base64
            raw = base64.b64decode(body["content"])
            self.blobs.append(raw)
            sha = self.blob_sha or hashlib.sha1(b"blob %d\0" % len(raw) + raw).hexdigest()
            return self.blob_status, ({"sha": sha} if self.blob_status == 201 else {"message": "no"})
        if method == "POST" and path == "/git/trees":
            self.trees.append(body["tree"])
            return 201, {"sha": "newtree"}
        if method == "POST" and path == "/git/commits":
            self.commits.append(body)
            return 201, {"sha": "newcommit"}
        if method == "PATCH" and path == "/git/refs/heads/main":
            self.patches.append(body)
            return (self.patch.pop(0) if self.patch else 200), {}
        raise AssertionError("unexpected call %s %s" % (method, path))


def go(fake, paths):
    G.call = fake.call
    G._tip_blobs_graphql = lambda slug, tip, ps: fake.tipblobs
    sys.argv = ["push_paths_gitdata.py", "o/r", BASE, C0, "msg"] + list(paths)
    return G.main()


# 1. the changed path lands as one commit on the tip, force false, with the scanned blob
f = Fake(["tip1"], {(BASE, "tip1"): ["c.txt"]})
rc = go(f, ["a.txt"])
run("1 a changed path lands as one commit, force false", rc == 0 and len(f.commits) == 1
    and f.commits[0]["parents"] == ["tip1"] and f.patches == [{"sha": "newcommit", "force": False}]
    and f.trees[0] == [{"path": "a.txt", "mode": "100644", "type": "blob", "sha": A_SHA}]
    and f.blobs == [b"one changed\n"])
# 2. a named path that matches the base costs no upload
f = Fake(["tip1"])
rc = go(f, ["a.txt", "b.txt"])
run("2 an unchanged path is not uploaded", rc == 0 and len(f.blobs) == 1 and [i["path"] for i in f.trees[0]] == ["a.txt"])
# 3. nothing changed at all: no commit
f = Fake(["tip1"])
rc = go(f, ["b.txt"])
run("3 nothing to land makes no commit", rc == 0 and not f.commits and not f.blobs)
# 4. a peer changed our path since the scan: refused, no ref move
f = Fake(["tip1"], {(BASE, "tip1"): ["a.txt"]})
rc = go(f, ["a.txt"])
run("4 a peer's change to our path is refused", rc == 3 and not f.patches)
# 5. the API refuses the blob outright: exit 4 so the caller can take another route
f = Fake(["tip1"], blob_status=403)
rc = go(f, ["a.txt"])
run("5 an unusable API exits 4", rc == 4 and not f.patches)
# 6. the uploaded blob hashes differently from the scanned one: refused
f = Fake(["tip1"], blob_sha="0" * 40)
rc = go(f, ["a.txt"])
run("6 a blob that is not the scanned one is refused", rc == 3 and not f.patches)
# 7. a path the scanned commit lacks: refused before any call
f = Fake(["tip1"])
rc = go(f, ["a.txt", "missing.txt"])
run("7 a path missing from the scanned commit is refused", rc == 3 and not f.blobs)
# 8. a lost ref race is retried on the new tip, checking only the commits since the last good tip
f = Fake(["tip1", "tip2"], {(BASE, "tip1"): ["c.txt"], ("tip1", "tip2"): ["b.txt"]}, patch=[422, 200])
rc = go(f, ["a.txt"])
run("8 a lost race is retried on the new tip", rc == 0 and [c["parents"] for c in f.commits] == [["tip1"], ["tip2"]])
# 9. compare caps at 300 files: the tip's own blobs decide, and a peer change there is refused
f = Fake(["tip1"], big={(BASE, "tip1")}, tipblobs={"a.txt": "f" * 40})
rc = go(f, ["a.txt"])
run("9 past 300 changed files the tip's blobs are checked, a peer change refused", rc == 3 and not f.patches)
# 10. same, and our path is untouched at the tip: it lands
f = Fake(["tip1"], big={(BASE, "tip1")}, tipblobs={"a.txt": git("rev-parse", BASE + ":a.txt")})
rc = go(f, ["a.txt"])
run("10 past 300 changed files with our path untouched, it lands", rc == 0 and len(f.patches) == 1)
os.chdir(HERE)
import shutil
shutil.rmtree(root, ignore_errors=True)
print("%d of 10 controls pass (%s)" % (10 - len(FAILS), PATH))
sys.exit(1 if FAILS else 0)
