#!/usr/bin/env python3
"""memory_push.py - put the memories this computer files where the other computers pull them. Never loses a line.

The other half of memory_pull.py. Without it each computer's new memories stay on that computer until someone copies
them by hand: on one deployment 150 memories were missing or stale online in a night, corrections made in place among
them, and the other computers recalled the old versions.

Targets (pick one, as for memory_pull):
    --repo owner/name --folder path/in/repo   a GitHub repository; listed over the API with fleet_board's auth and
                                              written in one commit by push_paths_api.py (defaults: BRAIN_MEMORY_REPO,
                                              BRAIN_MEMORY_FOLDER, branch main or BRAIN_MEMORY_BRANCH)
    --to-dir PATH                             any shared folder (a synced drive, a network share)

What goes up, per memory file (BRAIN_MEM_DIR, else the folder spread_recall.py names):
    new           a dated name (slug ends _YYYY_MM_DD or carries a _YYYYMMDDTHHMMSS stamp) not at the target
    fast-forward  differs at the target, and the target still holds the version this computer last synced
    superset      differs at the target, and every line there is kept here or extended by a line here
Anything else that differs is HELD and listed: both sides changed it, and a merge is a person's or an agent's job,
not this script's. A file must have been still for --still seconds (default 120) so a half-edited memory waits.
Never sent: upper-case index files (MEMORY.md and the like, each computer's own), undated names not already at the
target (blank template seeds), files of 2 MB or more, files the secret scan flags. Nothing at the target is deleted.
A listing that lacks over 10% of the names this computer synced before (or, with no history, an empty listing
against a folder of more than 20 memories) is taken as broken and the run stops; a broken listing would make every
memory look new and send it over the real copy. --trust-listing overrides. The last-synced blob ids live in the state file
(BRAIN_MEMORY_PUSH_STATE). Dry run by default; --apply sends.

    python3 memory_push.py --repo you/brain-memories --folder memory            what would go up
    python3 memory_push.py --repo you/brain-memories --folder memory --apply
    python3 memory_push.py --to-dir /shared/brain/memory --apply --max 50
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import base64, hashlib, json, os, re, shutil, subprocess, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

NAME = re.compile(r"^[a-z0-9][a-z0-9_.\-]{2,200}\.md$")
DATED = re.compile(r"_20\d\d_\d\d_\d\d|_20\d{6}T\d{6}")
MAX_BYTES = 2_000_000
STATE = os.environ.get("BRAIN_MEMORY_PUSH_STATE") or os.path.join(
    os.path.expanduser("~"), ".claude", "fleet_board", "memory_push_state.json")
MESH = os.environ.get("BRAIN_MESH_CLI") or os.path.normpath(os.path.join(HERE, "..", "mesh", "agent_mesh.py"))
NOWIN = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def mem_dir():
    if os.environ.get("BRAIN_MEM_DIR"):
        return os.environ["BRAIN_MEM_DIR"]
    try:
        import spread_recall
        return spread_recall.MEM
    except Exception:
        return None


def blob_sha(data):
    """git's blob id, which is also what GitHub's tree listing reports."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def lines(data):
    return data.decode("utf-8", "replace").splitlines()


def covered(old, new):
    """Every non-blank line of `old` is in `new`, or a line of `new` extends it (a description grown in place)."""
    have = set(new)
    return all(any(len(k) > len(l) and k.startswith(l) for k in new) for l in old if l.strip() and l not in have)


class DirTarget:
    def __init__(self, path):
        self.path = path
        self._by_sha = {}

    def names(self):
        out = {}
        for n in os.listdir(self.path):
            p = os.path.join(self.path, n)
            if os.path.isfile(p):
                out[n] = blob_sha(read_bytes(p))
                self._by_sha[out[n]] = p
        return out

    def read_blob(self, sha):
        return read_bytes(self._by_sha[sha])

    def publish(self, mem, names, message):
        for n in names:
            tmp = os.path.join(self.path, ".%s.tmp" % n)
            shutil.copyfile(os.path.join(mem, n), tmp)
            os.replace(tmp, os.path.join(self.path, n))
        return 0, "copied %d" % len(names)


class RepoTarget:
    def __init__(self, repo, folder, branch):
        import memory_pull
        self.src = memory_pull.RepoSource(repo, folder, branch)
        self.repo, self.folder, self.branch = repo, folder.strip("/"), branch

    def names(self):
        return self.src.names()

    def read_blob(self, sha):
        st, r = self.src.api("GET", "/repos/%s/git/blobs/%s" % (self.repo, sha), timeout=60)
        if st != 200 or "content" not in r:
            raise RuntimeError("blob read failed: %s" % st)
        return base64.b64decode(r["content"])

    def publish(self, mem, names, message):
        stage = tempfile.mkdtemp(prefix="memory_push_")
        try:
            link = os.path.join(stage, *self.folder.split("/"))
            os.makedirs(os.path.dirname(link), exist_ok=True)
            os.symlink(os.path.abspath(mem), link)
            r = subprocess.run([sys.executable, os.path.join(HERE, "push_paths_api.py"), "--repo", self.repo,
                                "--branch", self.branch, "--root", stage, "--message", message]
                               + ["%s/%s" % (self.folder, n) for n in names],
                               capture_output=True, text=True, timeout=1800, stdin=subprocess.DEVNULL, **NOWIN)
            tail = (r.stdout + r.stderr).strip().splitlines()
            return r.returncode, tail[-1] if tail else ""
        finally:
            shutil.rmtree(stage, ignore_errors=True)


def local_names(mem):
    return set(os.listdir(mem))


def eligible(mem, remote):
    out = []
    for n in os.listdir(mem):
        if not NAME.match(n) or not (DATED.search(n) or n in remote):
            continue
        try:
            if os.path.getsize(os.path.join(mem, n)) >= MAX_BYTES:
                continue
        except OSError:
            continue
        out.append(n)
    return sorted(out)


def local_shas(mem, names, cache):
    """Blob id per file; a file is read again only when its mtime or size moved (memory folders run to 10^4 files)."""
    out = {}
    for n in names:
        p = os.path.join(mem, n)
        try:
            st = os.stat(p)
        except OSError:
            continue
        key, old = [st.st_mtime_ns, st.st_size], cache.get(n)
        if old and old[:2] == key:
            out[n] = old[2]
            continue
        try:
            out[n] = blob_sha(read_bytes(p))
        except OSError:
            continue
        cache[n] = key + [out[n]]
    return out


def plan(mem, remote, local, synced, target, now, still=120):
    """-> ({name: why} to send, [held names]). `synced` gains every name already equal at the target."""
    send, held = {}, []
    for n, sha in local.items():
        if sha == remote.get(n):
            synced[n] = sha
            continue
        try:
            if now - os.path.getmtime(os.path.join(mem, n)) < still:
                continue
        except OSError:
            continue
        if n not in remote:
            send[n] = "new"
        elif synced.get(n) == remote[n]:
            send[n] = "fast-forward"
        else:
            try:
                ok = covered(lines(target.read_blob(remote[n])), lines(read_bytes(os.path.join(mem, n))))
            except Exception:
                ok = False   # an unread target copy covers nothing: hold
            if ok:
                send[n] = "superset"
            else:
                held.append(n)
    return send, held


def scan_clean(mem, names):
    scanner = os.path.join(HERE, "scan_secrets.py")
    if not os.path.exists(scanner):
        return [], "scan_secrets.py not found; nothing sent"
    ok = []
    for i in range(0, len(names), 200):
        part = names[i:i + 200]
        run = lambda ns: subprocess.run([sys.executable, scanner] + ns, cwd=mem, capture_output=True,
                                        stdin=subprocess.DEVNULL, **NOWIN).returncode == 0
        ok += part if run(part) else [n for n in part if run([n])]
    return ok, ""


def mesh(*args):
    if not os.path.exists(MESH):
        return 0
    return subprocess.run([sys.executable, MESH] + list(args), capture_output=True, stdin=subprocess.DEVNULL,
                          **NOWIN).returncode


def push(target, mem, apply=False, cap=200, still=120, trust_listing=False, state=None, out=print,
         scan=scan_clean, now=None):
    state = {} if state is None else state
    synced, cache = state.setdefault("synced", {}), state.setdefault("hash", {})
    now = time.time() if now is None else now
    remote = target.names()
    names = eligible(mem, remote)
    # A broken listing (an empty or truncated tree, a wrong branch) makes every memory look new, and "new" is sent
    # over whatever the target really holds. The test is the names this computer has synced before: they must still
    # be listed. With no history, an empty listing against a full folder is refused; new memories never count.
    known = [n for n in state["synced"] if n in local_names(mem)]
    found = sum(n in remote for n in known)
    broken = found < 0.9 * len(known) if known else (not remote and len(names) > 20)
    if broken and not trust_listing:
        out("target listing holds %d of the %d names synced before (%d listed in all): taken as a broken listing, "
            "nothing sent (--trust-listing when it is right)" % (found, len(known), len(remote)))
        return {"sent": [], "held": [], "stopped": "listing"}
    local = local_shas(mem, names, cache)
    send, held = plan(mem, remote, local, synced, target, now, still)
    order = sorted(send, key=lambda n: (DATED.search(n).group(0) if DATED.search(n) else "", n), reverse=True)
    if cap and len(order) > cap:
        out("taking the newest %d of %d this run (--max)" % (cap, len(order)))
        order = order[:cap]
    clean, why_none = scan(mem, order)
    if why_none:
        out(why_none)
    for n in order:
        out("  %s %-12s %s%s" % ("send" if apply else "would send", send[n], n, "" if n in clean else "  (secret scan: dropped)"))
    for n in held:
        out("  HELD (changed here and at the target) %s" % n)
    result = {"sent": [], "held": held, "dropped": [n for n in order if n not in clean]}
    if apply and clean:
        rc, last = target.publish(mem, clean, "memories (auto) [skip ci]")
        out("publish rc=%s %s" % (rc, last))
        if rc == 0:
            for n in clean:
                synced[n] = local[n]
            result["sent"] = clean
        result["rc"] = rc
    out("%d to send (%d new, %d fast-forward, %d superset), %d held, %d already equal" % (
        len(order), sum(send[n] == "new" for n in order), sum(send[n] == "fast-forward" for n in order),
        sum(send[n] == "superset" for n in order), len(held), sum(1 for n in local if local[n] == remote.get(n))))
    return result


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)

    def opt(flag, default=None):
        return a[a.index(flag) + 1] if flag in a and a.index(flag) + 1 < len(a) else default

    mem = mem_dir()
    if not mem or not os.path.isdir(mem):
        sys.exit("memory folder not found (set BRAIN_MEM_DIR)")
    if opt("--to-dir"):
        target, res = DirTarget(opt("--to-dir")), "path:" + os.path.abspath(opt("--to-dir"))
    else:
        repo = opt("--repo", os.environ.get("BRAIN_MEMORY_REPO"))
        folder = opt("--folder", os.environ.get("BRAIN_MEMORY_FOLDER"))
        if not repo or not folder:
            sys.exit("name a target: --to-dir PATH, or --repo owner/name --folder path (or BRAIN_MEMORY_REPO and "
                     "BRAIN_MEMORY_FOLDER)")
        target = RepoTarget(repo, folder, opt("--branch", os.environ.get("BRAIN_MEMORY_BRANCH", "main")))
        res = "git:%s/%s" % (repo, folder.strip("/"))
    apply = "--apply" in a
    try:
        state = json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    agent = os.environ.get("BRAIN_AGENT_ID", "memory-push")
    if apply:
        mesh("hello", "--agent", agent, "--runtime", "script", "--task", "push this computer's memories")
        if mesh("claim", "--agent", agent, "--resource", res, "--mode", "write", "--ttl", "1800",
                "--purpose", "memory_push") != 0:
            print("%s is claimed by another agent; nothing sent this run" % res)
            return 0
    try:
        r = push(target, mem, apply=apply, cap=int(opt("--max", "200")), still=float(opt("--still", "120")),
                 trust_listing="--trust-listing" in a, state=state)
    finally:
        if apply:
            mesh("release", "--agent", agent, "--resource", res)
    if apply:
        try:
            os.makedirs(os.path.dirname(STATE), exist_ok=True)
            state["at"] = time.strftime("%Y-%m-%d %H:%M %z")
            with open(STATE + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(state, fh)
            os.replace(STATE + ".tmp", STATE)
        except OSError:
            pass
    return 1 if r.get("rc") not in (None, 0) else 0


if __name__ == "__main__":
    sys.exit(main())
