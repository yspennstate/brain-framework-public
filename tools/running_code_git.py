"""Which running programs execute code that git does not hold?

A long-lived process (a governor, a mesh server, a recall server) started from a clone keeps running whatever is in
that working tree. If the file is modified, untracked, or committed but never pushed, the code that is actually in
charge exists nowhere else, and the first reset or fresh clone of that tree loses it. This lists every running
Python program whose script sits inside a git working tree and says which of the three it is.

    python tools/running_code_git.py [--json] [--all]

Scripts outside any git working tree are not listed. --all also prints the scripts that are clean and pushed. Exits 1 when any running script is not safely in git, 0
otherwise. Read-only: it runs ps, lsof (macOS) or /proc (Linux), and git status / git branch -r --contains.
"""
import argparse, json, os, re, subprocess, sys

PY = re.compile(r"^python[\d.]*(\.exe)?$", re.I)


def _run(cmd, cwd=None, timeout=20):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def _cwd_of(pid):
    link = "/proc/%d/cwd" % pid
    if os.path.exists(link):
        try:
            return os.readlink(link)
        except OSError:
            return None
    rc, out = _run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], timeout=10)
    for line in out.splitlines():
        if line.startswith("n"):
            return line[1:]
    return None


def processes():
    """[(pid, argv list)] for every process ps can see; argv split on whitespace (paths with spaces are rare here)."""
    try:
        import psutil
        out = []
        for p in psutil.process_iter(["pid", "cmdline"]):
            if p.info.get("cmdline"):
                out.append((p.info["pid"], list(p.info["cmdline"])))
        return out
    except Exception:
        pass
    rc, out = _run(["ps", "-axo", "pid=,command="])
    procs = []
    for line in out.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[0].isdigit():
            procs.append((int(parts[0]), parts[1:]))
    return procs


def script_of(argv):
    """The .py file a Python command line runs, or None (python -c / -m and non-Python programs give None)."""
    if not argv or not PY.match(os.path.basename(argv[0])):
        return None
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in ("-c", "-m"):
            return None
        if a.startswith("-"):
            i += 2 if a in ("-W", "-X") else 1
            continue
        return a if a.endswith(".py") else None
    return None


def _blob_on_remote(top, path):
    """True when the working file's exact bytes are that path's blob on some remote branch: the clone is behind or
    off its branch, but nothing would be lost by resetting it."""
    rc, blob = _run(["git", "-C", top, "hash-object", "--", path])
    rc2, refs = _run(["git", "-C", top, "for-each-ref", "--format=%(refname)", "refs/remotes"])
    if rc or rc2 or not blob.strip():
        return False
    rel = os.path.relpath(os.path.realpath(path), os.path.realpath(top)).replace(os.sep, "/")
    for ref in refs.split()[:50]:
        r, b = _run(["git", "-C", top, "rev-parse", "--verify", "-q", "%s:%s" % (ref, rel)])
        if r == 0 and b.strip() == blob.strip():
            return True
    return False


def git_state(path):
    """(repo root, state) for a file inside a git working tree, or None. state is one of
    'modified', 'modified, on remote' (the same bytes are on a remote branch), 'untracked', 'unpushed' (HEAD is on no
    remote branch), 'clean'."""
    d = os.path.dirname(path)
    rc, top = _run(["git", "-C", d, "rev-parse", "--show-toplevel"])
    if rc != 0 or not top.strip():
        return None
    top = top.strip()
    rc, st = _run(["git", "-C", top, "status", "--porcelain", "--", path])
    if rc != 0:
        return None
    code = st[:2]
    if code == "??":
        return top, "untracked"
    if code.strip():
        return top, ("modified, on remote" if _blob_on_remote(top, path) else "modified")
    rc, rem = _run(["git", "-C", top, "branch", "-r", "--contains", "HEAD"])
    if rc == 0 and not rem.strip():
        rc2, remotes = _run(["git", "-C", top, "remote"])
        if remotes.strip():
            return top, "unpushed"
    return top, "clean"


def survey(procs=None, cwd_of=_cwd_of, state_of=git_state):
    rows = []
    for pid, argv in (processes() if procs is None else procs):
        s = script_of(argv)
        if not s:
            continue
        if not os.path.isabs(s):
            c = cwd_of(pid)
            if not c:
                continue
            s = os.path.join(c, s)
        s = os.path.realpath(s)
        if not os.path.isfile(s):
            continue
        g = state_of(s)
        if g:
            rows.append({"pid": pid, "script": s, "repo": g[0], "state": g[1]})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--all", action="store_true", help="also list clean, pushed scripts")
    a = ap.parse_args(argv)
    rows = survey()
    bad = [r for r in rows if r["state"] not in ("clean", "modified, on remote")]
    if a.json:
        print(json.dumps(rows if a.all else bad, indent=1))
    else:
        for r in (rows if a.all else bad):
            print("%-9s pid %-7d %s" % (r["state"], r["pid"], r["script"]))
        print("%d running script(s) in git working trees, %d not safely in git" % (len(rows), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
