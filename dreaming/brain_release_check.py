"""brain_release_check.py - keep this machine's full brain graph at the newest published release.

The owner, 2 Nov 2026 16:58 EST: "The Mac mini should have its own brain mechanism where we download the full brain".
The main computer publishes the full graph as GitHub releases named brain-graph-full-<date> on
yspennstate/brain-repo (nodes, uses, first_touch as .gz, edges.npz, manifest.json with a sha256
per file). This script finds the newest one and, if it is newer than the store recall reads, downloads it to a
staging folder beside the store, checks every file against the manifest, and swaps it in with two renames. The old
store is kept as graph_lane_store.prev_<tag> (brain data is never deleted).

    python3 brain_release_check.py            # dry run: say what is installed and what is published
    python3 brain_release_check.py --apply    # download, verify and swap if a newer release exists
    python3 brain_release_check.py --apply --with-net   # also fetch the release's net.sqlite3 (1.9 GB) beside it

Refuses when free disk is under 2x the download plus 2 GB, and when the mesh claim on memory:graph_lane_store is held.
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import gzip, hashlib, json, os, shutil, subprocess, sys, time

REPO = "yspennstate/brain-repo"
PREFIX = "brain-graph-full-"
DREAM = os.path.expanduser("~/.claude/dreaming")
STORE = os.path.join(DREAM, "graph_lane_store")
STATE = os.path.join(STORE, "INSTALLED_RELEASE.json")
NET_DIR = os.path.join(DREAM, "network")
MS = os.path.expanduser("~/brain/brain-framework/mesh/agent_mesh.py")
# The mesh claim goes under the caller's own id (BRAIN_AGENT_ID). With none set the checker joins the mesh as itself:
# a default naming one session failed every claim once that session was gone (5 Nov 2026, rc 3 after the lock).
AGENT = os.environ.get("BRAIN_AGENT_ID") or "graph-release-check"
OWN_ID = not os.environ.get("BRAIN_AGENT_ID")
STORE_FILES = ("nodes.json", "uses.json", "first_touch.json", "edges.npz")


def gh(*args):
    return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=600)


def newest_release():
    p = gh("api", "repos/%s/releases?per_page=50" % REPO)
    if p.returncode != 0:
        raise SystemExit("cannot list releases: %s" % p.stderr.strip()[:200])
    rel = [r for r in json.loads(p.stdout) if r["tag_name"].startswith(PREFIX) and not r.get("draft")]
    return max(rel, key=lambda r: r["published_at"]) if rel else None


def installed():
    if os.path.exists(STATE):
        return json.load(open(STATE))
    # stores installed before this script: identify by the manifest's edges sha
    man = os.path.join(STORE, "manifest.json")
    if os.path.exists(man):
        return {"tag": None, "edges_sha256": json.load(open(man)).get("edges.npz", {}).get("sha256")}
    return {}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def free_bytes(path):
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def mesh(*args):
    return subprocess.run([sys.executable, MS, *args], capture_output=True, text=True).returncode


def main():
    apply_, with_net = "--apply" in sys.argv, "--with-net" in sys.argv
    rel = newest_release()
    have = installed()
    if rel is None:
        print("no %s* release published" % PREFIX)
        return 0
    assets = {a["name"]: a for a in rel["assets"]}
    print("published: %s (%s)" % (rel["tag_name"], rel["published_at"]))
    print("installed: %s" % (have.get("tag") or ("pre-script store, edges sha %s" % (have.get("edges_sha256") or "?")[:12])))
    stage = STORE + ".new"
    # is it newer? same tag, or (pre-script store) same edges sha as the release manifest -> up to date
    if have.get("tag") == rel["tag_name"]:
        print("up to date")
        need_store = False
    else:
        need_store = True
        if have.get("tag") is None and have.get("edges_sha256") and "manifest.json" in assets:
            os.makedirs(stage, exist_ok=True)
            p = gh("release", "download", rel["tag_name"], "-R", REPO, "-p", "manifest.json", "-D", stage, "--clobber")
            if p.returncode == 0:
                rm = json.load(open(os.path.join(stage, "manifest.json")))
                if rm.get("edges.npz", {}).get("sha256") == have["edges_sha256"]:
                    print("up to date (installed store matches %s by edges sha256); recording the tag" % rel["tag_name"])
                    need_store = False
                    if apply_:
                        json.dump({"tag": rel["tag_name"], "published_at": rel["published_at"], "installed_at": time.time(),
                                   "edges_sha256": have["edges_sha256"], "how": "matched existing store"},
                                  open(STATE, "w"), indent=1)
            if not need_store:
                shutil.rmtree(stage, ignore_errors=True)  # held only the compared manifest
    want =[n for n in ("manifest.json", "nodes.json.gz", "uses.json.gz", "first_touch.json.gz", "edges.npz") if need_store]
    if with_net and "net.sqlite3" in assets and not os.path.exists(os.path.join(NET_DIR, "net_full_%s.sqlite3" % rel["tag_name"])):
        want.append("net.sqlite3")
    if not want:
        return 0
    size = sum(assets[n]["size"] for n in want if n in assets)
    free = free_bytes(DREAM)
    print("to fetch: %s (%.2f GB); free %.2f GB" % (", ".join(want), size / 1e9, free / 1e9))
    if not apply_:
        print("dry run: pass --apply to download, verify and swap")
        return 0
    if free < 2 * size + 2e9:
        print("REFUSED: free disk %.2f GB is under 2x the download + 2 GB" % (free / 1e9))
        return 3
    if OWN_ID:
        mesh("hello", "--agent", AGENT, "--runtime", "script", "--task", "brain graph release swap")
    rc = mesh("claim", "--agent", AGENT, "--resource", "memory:graph_lane_store", "--mode", "exclusive",
              "--purpose", "install brain graph release %s" % rel["tag_name"], "--ttl", "3600")
    if rc != 0:
        print("REFUSED: memory:graph_lane_store is claimed by another agent" if rc == 3 else
              "REFUSED: the mesh claim as %s failed (rc %s)" % (AGENT, rc))
        if OWN_ID:
            mesh("bye", "--agent", AGENT, "--status", "blocked", "--summary", "claim refused rc=%s" % rc)
        return 3
    try:
        os.makedirs(stage, exist_ok=True)
        for n in want:
            p = gh("release", "download", rel["tag_name"], "-R", REPO, "-p", n, "-D", stage, "--clobber")
            if p.returncode != 0:
                raise RuntimeError("download %s failed: %s" % (n, p.stderr.strip()[:200]))
        if "net.sqlite3" in want:
            dst = os.path.join(NET_DIR, "net_full_%s.sqlite3" % rel["tag_name"])
            os.replace(os.path.join(stage, "net.sqlite3"), dst)
            print("net.sqlite3 -> %s (not wired in; the live network file is unchanged)" % dst)
        if not need_store:
            return 0
        man = json.load(open(os.path.join(stage, "manifest.json")))
        for n in STORE_FILES:
            gz = os.path.join(stage, n + ".gz")
            if os.path.exists(gz):
                with gzip.open(gz, "rb") as fi, open(os.path.join(stage, n), "wb") as fo:
                    shutil.copyfileobj(fi, fo, 1 << 22)
                os.remove(gz)
            got, exp = sha256(os.path.join(stage, n)), man.get(n, {}).get("sha256")
            if not exp or got != exp:
                raise RuntimeError("%s sha256 %s does not match the manifest %s" % (n, got[:12], (exp or "none")[:12]))
            print("verified %s" % n)
        prev = "%s.prev_%s" % (STORE, (installed().get("tag") or time.strftime("%Y%m%d_%H%M%S")))
        os.replace(STORE, prev)
        os.replace(stage, STORE)
        json.dump({"tag": rel["tag_name"], "published_at": rel["published_at"], "installed_at": time.time(),
                   "edges_sha256": man["edges.npz"]["sha256"], "how": "downloaded and verified", "previous": prev},
                  open(STATE, "w"), indent=1)
        print("installed %s; previous store kept at %s" % (rel["tag_name"], prev))
        # recall never builds the walk itself, so until this runs the graph lane prints "no prebuilt walk" and adds
        # nothing (3 Nov: 10 s for the 11-03 release)
        b = subprocess.run([sys.executable, os.path.join(DREAM, "graph_lane.py"), "--build"], capture_output=True,
                           text=True)
        print((b.stdout.strip().splitlines() or ["walk build printed nothing"])[-1] if b.returncode == 0
              else "walk build FAILED (rc %d): run python3 graph_lane.py --build" % b.returncode)
        return 0
    finally:
        mesh("release", "--agent", AGENT, "--resource", "memory:graph_lane_store")
        if OWN_ID:
            mesh("bye", "--agent", AGENT, "--status", "done", "--summary", "graph release swap")


if __name__ == "__main__":
    sys.exit(main())
