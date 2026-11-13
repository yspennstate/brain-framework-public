"""test_prepush_new_ref.py - the pre-push secret gate certifies every commit a push makes reachable, new refs included
(28 Sep 2026; scheduled audits account02/B06 r65, R24-B06-PREPUSH-NEW-REF-FALLBACK-CAN-NARROW-20260928;
account03/B19 r69, B19-R51-FRESH-BASE-REPLACE-REF-GRAPH-MISMATCH-20260928; account02/B19 auto43,
B19-R51-NEWREF-TEST-UNPINNED-SCANNER-20260928; account02/B06 r68, R51-B06-NEWREF-WITNESS-DELETE-CUSTOMREF-20260928).

    python test_prepush_new_ref.py [GATE.py] [--scanner SCAN_SECRETS.py] [--old GATE_BEFORE.py] [--older GATE_OLDER.py]

Everything runs in a temporary folder with an empty HOME, so no global git configuration, template or hook of the
machine takes part: a bare repository stands in for the remote and a clone of it pushes through the gate under test,
installed as the clone's pre-push hook. The gate runs the scanner beside this file (or --scanner) through a recorder, so
the test knows exactly which ranges were certified; the first lines printed are the sha256 of every file under test.
For every push: newly reachable = the commits the pushed ref reaches that the remote's other refs, as they stood when
the push landed, did not; certified = the commits in the ranges the scanner was given, read without replace objects or
grafts, as the scanner reads them. A push that succeeded with a newly reachable commit outside the certified set fails.
 G1 new branch after the remote's main was rewound behind this clone's tracking ref (R <- A <- B, the remote rewound
    from A to R): both A and B are certified, the push succeeds
 G2 new branch with a fresh tracking ref: only B is newly reachable and certified
 G3 two remote refs (main at R, another at A): the base is the one leaving the least to scan, whatever the ref names
 G4 a new branch whose history the remote shares nothing with: refused, nothing on the remote
 G5 an existing branch updated A -> B: the range git hands the hook, A..B, unchanged
 G6 a new tag in G1's state: A and B certified, as for a branch
 G7 the remote's only ref is a commit this clone lacks (rewritten from R by another clone): refused
 G8 the remote's only ref is X, unrelated to R <- A <- B, and a local replace ref puts Y (parent A) in place of X: refused
    as unrelated, or R, A and B all certified (B19 r69's fixture)
 G9 the same with a graft line "X A" in place of the replace ref: nothing newly reachable goes uncertified
 G10 the remote has main at R and w at A; w is deleted while the first range is scanned: the gate scans again from R,
    so A and B are both certified (B06 r68's witness deletion)
With --old (the gate before 28 Sep 20:5x) G8 and G10 must fail; with --older (before 28 Sep 16:4x) G1, G6 and G7 must
fail (the checks can fail).
"""
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))


def _opt(name):
    return os.path.abspath(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else None


GATE = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else \
    os.path.join(HERE, "prepush_secret_gate.py")
SCANNER = _opt("--scanner") or os.path.join(HERE, "scan_secrets.py")
OLD = _opt("--old")
OLDER = _opt("--older")
NOWIN = 0x08000000 if os.name == "nt" else 0
ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
           GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
for _k in [k for k in ENV if k.upper().startswith("GIT_") and k.upper() not in (
        "GIT_TERMINAL_PROMPT", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")]:
    ENV.pop(_k)
fails, ran = [], []

RECORDER = '''import json, os, subprocess, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "scanned.log"), "a", encoding="utf-8") as fh:
    fh.write(" ".join(sys.argv[1:]) + "\\n")
act = os.path.join(here, "on_first_scan.json")
if os.path.exists(act):
    cwd, args = json.load(open(act, encoding="utf-8"))
    os.replace(act, act + ".done")
    subprocess.run(["git", "-C", cwd] + args, capture_output=True)
sys.exit(subprocess.run([sys.executable, %r] + sys.argv[1:]).returncode)
'''


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]


def rmtree(path):
    def clear(func, name, _exc):
        os.chmod(name, stat.S_IWRITE)
        func(name)
    shutil.rmtree(path, onexc=clear)


def git(cwd, *args, ok=True, env=None):
    p = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True, env=env or ENV, creationflags=NOWIN)
    if ok and p.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), p.stderr.strip()))
    return p.stdout.strip() if ok else (p.returncode, p.stdout + p.stderr)


def measure_env():
    """How the scanner reads the graph: no replace objects, no grafts."""
    return dict(ENV, GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull)


def commit(repo, name):
    with open(os.path.join(repo, name), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(name + "\n")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", "add " + name)
    return git(repo, "rev-parse", "HEAD")


class Fixture:
    def __init__(self, td, tag, gate):
        self.remote = os.path.join(td, tag + "_remote.git")
        self.clone = os.path.join(td, tag + "_clone")
        self.gdir = os.path.join(td, tag + "_gate")
        os.makedirs(self.gdir)
        shutil.copyfile(gate, os.path.join(self.gdir, "prepush_secret_gate.py"))
        with open(os.path.join(self.gdir, "scan_secrets.py"), "w", encoding="utf-8") as fh:
            fh.write(RECORDER % SCANNER)
        git(td, "init", "-q", "--bare", "-b", "main", self.remote)
        git(td, "clone", "-q", self.remote, self.clone)
        git(self.clone, "checkout", "-q", "-b", "main")
        hook = os.path.join(self.clone, ".git", "hooks", "pre-push")
        with open(hook, "w", encoding="utf-8", newline="\n") as fh:
            fh.write('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable.replace("\\", "/"),
                                                         os.path.join(self.gdir, "prepush_secret_gate.py").replace("\\", "/")))
        os.chmod(hook, 0o755)

    def on_first_scan(self, cwd, *args):
        with open(os.path.join(self.gdir, "on_first_scan.json"), "w", encoding="utf-8") as fh:
            json.dump([cwd, list(args)], fh)

    def refs(self):
        out = git(self.remote, "for-each-ref", "--format=%(objectname) %(refname)")
        return {l.split()[1]: l.split()[0] for l in out.splitlines() if l.strip()}

    def push(self, *spec):
        log = os.path.join(self.gdir, "scanned.log")
        if os.path.exists(log):
            os.remove(log)
        before = self.refs()
        rc, out = git(self.clone, "push", "origin", *spec, ok=False)
        after = self.refs()
        certified = set()
        if os.path.exists(log):
            for line in open(log, encoding="utf-8"):
                parts = line.split()
                if len(parts) == 2 and parts[0] == "--range":
                    certified |= set(git(self.clone, "rev-list", parts[1], env=measure_env()).split())
        # the remote as it stood when the push landed: every ref the push did not write, as it is now (a ref deleted
        # while the hook ran is gone), and each pushed ref at its value before
        pushed = {ref for ref, oid in after.items() if before.get(ref) != oid}
        landed = [oid for ref, oid in after.items() if ref not in pushed] + [before[ref] for ref in pushed if ref in before]
        newly = set()
        for ref in pushed:
            args = ["rev-list", after[ref]] + (["--not"] + sorted(set(landed)) if landed else [])
            newly |= set(git(self.remote, *args).split())
        return {"rc": rc, "certified": certified, "newly": newly, "missed": newly - certified, "moved": bool(pushed),
                "out": out.strip()[-300:]}


def base_state(f):
    """R <- A on the remote's main, fetched; B on A locally."""
    r = commit(f.clone, "r.txt")
    a = commit(f.clone, "a.txt")
    git(f.clone, "push", "-q", "--no-verify", "origin", "main")
    b = commit(f.clone, "b.txt")
    return r, a, b


def unrelated_remote_main(f, td, tag):
    """Another clone replaces the remote's main with an orphan commit X; this clone fetches X. Returns X."""
    other = os.path.join(td, tag + "_other")
    git(td, "clone", "-q", f.remote, other)
    git(other, "checkout", "-q", "--orphan", "lone")
    git(other, "rm", "-rq", "--cached", ".")
    x = commit(other, "x.txt")
    git(other, "push", "-q", "--no-verify", "--force", "origin", "HEAD:refs/heads/main")
    git(f.clone, "fetch", "-q", "origin")
    return x


def short(s):
    return sorted(x[:7] for x in s)


def run_all(gate, tag):
    td = tempfile.mkdtemp(prefix="prepush_gate_")
    res = {}
    try:
        # G1 stale-ahead: the remote rewound to R
        f = Fixture(td, "g1", gate)
        r, a, b = base_state(f)
        git(f.remote, "update-ref", "refs/heads/main", r)
        p = f.push("%s:refs/heads/new" % b)
        res["G1"] = (p["rc"] == 0 and not p["missed"] and p["newly"] == {a, b},
                     "%s G1 new branch after a rewind (R <- A <- B, remote back at R): rc %d, newly reachable %s, certified "
                     "%s, missed %s" % (tag, p["rc"], short(p["newly"]), short(p["certified"]), short(p["missed"])))
        # G6 a new tag, in G1's state before anything else was pushed (its own fixture)
        f = Fixture(td, "g6", gate)
        r, a, b = base_state(f)
        git(f.remote, "update-ref", "refs/heads/main", r)
        git(f.clone, "tag", "t1", b)
        p = f.push("refs/tags/t1")
        res["G6"] = (p["rc"] == 0 and not p["missed"] and p["newly"] == {a, b},
                     "%s G6 new tag at B, remote main rewound to R: rc %d, newly reachable %s, missed %s" % (
                         tag, p["rc"], short(p["newly"]), short(p["missed"])))
        # G2 fresh tracking
        f = Fixture(td, "g2", gate)
        r, a, b = base_state(f)
        p = f.push("%s:refs/heads/new" % b)
        res["G2"] = (p["rc"] == 0 and p["certified"] == {b} == p["newly"],
                     "%s G2 new branch, tracking fresh: rc %d, certified %s, newly reachable %s" % (
                         tag, p["rc"], short(p["certified"]), short(p["newly"])))
        # G3 two remote refs, named both ways round
        picks = []
        for names in (("aaa", "zzz"), ("zzz", "aaa")):
            f = Fixture(td, "g3" + names[0], gate)
            r, a, b = base_state(f)
            git(f.remote, "update-ref", "refs/heads/" + names[0], a)
            git(f.remote, "update-ref", "refs/heads/main", r)
            git(f.remote, "update-ref", "refs/heads/" + names[1], r)
            p = f.push("%s:refs/heads/new" % b)
            picks.append((p["rc"], sorted(p["certified"]) == sorted([b]), not p["missed"]))
        res["G3"] = (picks[0] == picks[1] == (0, True, True),
                     "%s G3 remote refs at R and A under either name order: (rc, certified == {B}, nothing missed) %s" % (
                         tag, picks))
        # G4 unrelated history
        f = Fixture(td, "g4", gate)
        base_state(f)
        git(f.clone, "checkout", "-q", "--orphan", "lone")
        git(f.clone, "rm", "-rq", "--cached", ".")
        o = commit(f.clone, "orphan.txt")
        p = f.push("%s:refs/heads/lone" % o)
        res["G4"] = (p["rc"] != 0 and not p["moved"],
                     "%s G4 a new branch sharing no history with the remote: rc %d, remote moved %s" % (tag, p["rc"], p["moved"]))
        # G5 existing ref update
        f = Fixture(td, "g5", gate)
        r, a, b = base_state(f)
        p = f.push("%s:refs/heads/main" % b)
        res["G5"] = (p["rc"] == 0 and p["certified"] == {b} == p["newly"],
                     "%s G5 existing branch A -> B: rc %d, certified %s" % (tag, p["rc"], short(p["certified"])))
        # G7 the remote's only ref is a commit this clone lacks
        f = Fixture(td, "g7", gate)
        r, a, b = base_state(f)
        other = os.path.join(td, "g7_other")
        git(td, "clone", "-q", f.remote, other)
        git(other, "reset", "-q", "--hard", r)
        x = commit(other, "x.txt")
        git(other, "push", "-q", "--no-verify", "--force", "origin", "HEAD:refs/heads/main")
        p = f.push("%s:refs/heads/new" % b)
        res["G7"] = (p["rc"] != 0 and not p["moved"],
                     "%s G7 remote main rewritten to a commit this clone lacks (R <- X): rc %d, remote moved %s, missed %s"
                     % (tag, p["rc"], p["moved"], short(p["missed"])))
        # G8 a local replace ref grafts the remote's unrelated X onto A (B19 r69)
        f = Fixture(td, "g8", gate)
        r, a, b = base_state(f)
        x = unrelated_remote_main(f, td, "g8")
        y = git(f.clone, "commit-tree", git(f.clone, "rev-parse", x + "^{tree}"), "-p", a, "-m", "y")
        git(f.clone, "replace", x, y)
        p = f.push("%s:refs/heads/new" % b)
        res["G8"] = (not p["missed"] and (p["rc"] != 0 or p["newly"] <= p["certified"]),
                     "%s G8 replace ref X -> Y (parent A), remote main X unrelated: rc %d, newly reachable %s, certified %s, "
                     "missed %s" % (tag, p["rc"], short(p["newly"]), short(p["certified"]), short(p["missed"])))
        # G9 the same through a graft line
        f = Fixture(td, "g9", gate)
        r, a, b = base_state(f)
        x = unrelated_remote_main(f, td, "g9")
        os.makedirs(os.path.join(f.clone, ".git", "info"), exist_ok=True)
        with open(os.path.join(f.clone, ".git", "info", "grafts"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("%s %s\n" % (x, a))
        p = f.push("%s:refs/heads/new" % b)
        res["G9"] = (not p["missed"],
                     "%s G9 graft X -> A, remote main X unrelated: rc %d, newly reachable %s, certified %s, missed %s" % (
                         tag, p["rc"], short(p["newly"]), short(p["certified"]), short(p["missed"])))
        # G10 the witness is deleted while the first range is scanned (B06 r68)
        f = Fixture(td, "g10", gate)
        r, a, b = base_state(f)
        git(f.remote, "update-ref", "refs/heads/w", a)
        git(f.remote, "update-ref", "refs/heads/main", r)
        f.on_first_scan(f.remote, "update-ref", "-d", "refs/heads/w")
        p = f.push("%s:refs/heads/new" % b)
        res["G10"] = (not p["missed"] and (p["rc"] != 0 or p["newly"] == {a, b}),
                      "%s G10 witness w (at A) deleted during the scan, main at R: rc %d, newly reachable %s, certified %s, "
                      "missed %s" % (tag, p["rc"], short(p["newly"]), short(p["certified"]), short(p["missed"])))
    finally:
        rmtree(td)
    return res


def main():
    home = tempfile.mkdtemp(prefix="prepush_home_")
    ENV.update(HOME=home, USERPROFILE=home, XDG_CONFIG_HOME=os.path.join(home, ".config"))
    try:
        print("gate %s %s" % (sha(GATE), GATE))
        print("scanner %s %s" % (sha(SCANNER), SCANNER))
        for label, path in (("old", OLD), ("older", OLDER)):
            if path:
                print("%s %s %s" % (label, sha(path), path))
        print("HOME %s (empty)" % home)
        for key, (ok, what) in sorted(run_all(GATE, "new").items(), key=lambda kv: int(kv[0][1:])):
            check(ok, what)
        if OLD:
            old = run_all(OLD, "before 28 Sep 20:5x")
            for key, n in (("G8", "N4"), ("G10", "N5")):
                check(not old[key][0], "%s the gate before this change fails %s (the check can fail): %s" % (
                    n, key, old[key][1].split(": ", 1)[1]))
        if OLDER:
            older = run_all(OLDER, "before 28 Sep 16:4x")
            for key, n in (("G1", "N1"), ("G6", "N2"), ("G7", "N3")):
                check(not older[key][0], "%s the gate before 28 Sep 16:4x fails %s (the check can fail): %s" % (
                    n, key, older[key][1].split(": ", 1)[1]))
    finally:
        rmtree(home)
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
