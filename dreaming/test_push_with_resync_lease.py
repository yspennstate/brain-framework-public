"""test_push_with_resync_lease.py - the nightly dream's Push-WithResync publishes nothing its certification did not cover,
even when the remote is rewound between the last check and the push (28 Sep 2026, claude-825b1305; scheduled audit
account03/B06 r55, R51-B06-PUSH-WITH-RESYNC-UNLEASED-REWIND-38).

    python test_push_with_resync_lease.py [--lib DREAM_GIT_SYNC.ps1] [--old PRE_28_SEP.ps1] [--old-stale PRE_14_OCT.ps1]

Each arm builds R <- A <- B in a temporary clone with a bare remote whose main is A, records A..B as certified, then
calls Push-WithResync(B, base A) through a hidden PowerShell. Every range the function certifies is recorded too.
Newly reachable = what the remote's main reaches afterwards and did not before; it must lie inside the certified set.
 NORMAL   nothing moves: B is published
 REWIND   the remote is rewound to R before the push, one attempt: nothing is published (B06's reproduction)
 RESYNC   the same rewind, two attempts, the advertised main is R: the retry certifies R..B and publishes B
 FORWARD  the remote moved to C (a child of A), two attempts, advertised C: B is replayed onto C, certified, published
With --old, the function before this change must publish A uncertified in REWIND (the check can fail).

STALE arms (14 Oct 2026, claude-c320faaf-r): the caller certified C..B against a main C that a peer pushed after B was
built on A, so B does not descend from its certified base. Before the change the function blocked the push and the
night's commit stayed local (eight October dreams).
 STALE     base C, advertised C, two attempts: B is replayed onto C, C..B' certified, B' published, C kept
 STALE1    the same with one attempt: nothing is published and the remote stays at C
 STALEFWD  base C, but main moved on to D (a child of C) before the re-sync: B is replayed onto D and published
In every arm of the new function nothing the remote had before is lost from its main.
With --old-stale (the function before 14 Oct, e.g. dream_git_sync.ps1.bak_stalebase_20261014), STALE must leave the
commit local (N2: the case this change exists for). --old takes the function before 28 Sep
(dream_git_sync.ps1.bak_r55lease_20260928) for N1.
"""
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
LIB = os.path.abspath(sys.argv[sys.argv.index("--lib") + 1]) if "--lib" in sys.argv else os.path.join(HERE, "dream_git_sync.ps1")
OLD = os.path.abspath(sys.argv[sys.argv.index("--old") + 1]) if "--old" in sys.argv else None
OLD_STALE = os.path.abspath(sys.argv[sys.argv.index("--old-stale") + 1]) if "--old-stale" in sys.argv else None
PS = shutil.which("powershell") or r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
NOWIN = 0x08000000 if os.name == "nt" else 0
ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
           GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
fails, ran = [], []

DRIVER = r'''param([string]$Lib, [string]$Repo, [string]$PushUrl, [string]$Sha, [string]$Base, [string]$AdvOid,
      [int]$Attempts, [string]$Log)
$ErrorActionPreference = 'Continue'
. $Lib
$adv = [pscustomobject]@{ Oid = $AdvOid; PushUrl = $PushUrl }
$getAdv = { $adv }.GetNewClosure()
$logPath = $Log
$certify = { param($range) Add-Content -LiteralPath $logPath -Value $range; $true }.GetNewClosure()
$r = Push-WithResync -Repo $Repo -PushUrl $PushUrl -CommitSha $Sha -BaseOid $Base -GetAdvertised $getAdv -Certify $certify `
    -Attempts $Attempts -SleepSeconds 0 -Logger { param($m) Write-Host "LOG $m" }
Write-Host ("RESULT " + ($r | ConvertTo-Json -Compress))
'''


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def rmtree(path):
    def clear(func, name, _exc):
        os.chmod(name, stat.S_IWRITE)
        func(name)
    shutil.rmtree(path, onexc=clear)


def git(cwd, *args):
    r = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True, env=ENV, creationflags=NOWIN)
    if r.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args[:3]), r.stderr.strip()[-300:]))
    return r.stdout.strip()


def commit(repo, name):
    with open(os.path.join(repo, name + ".txt"), "w", encoding="utf-8") as fh:
        fh.write(name + "\n")
    git(repo, "add", name + ".txt")
    git(repo, "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD")


def setup(td):
    """R <- A on the remote's main, B on A in the clone. Returns (clone, remote, R, A, B)."""
    tmpl = os.path.join(td, "empty_template")
    os.makedirs(tmpl)
    remote = os.path.join(td, "remote.git")
    git(td, "init", "-q", "--bare", "--template=" + tmpl, "-b", "main", remote)
    clone = os.path.join(td, "clone")
    git(td, "init", "-q", "--template=" + tmpl, "-b", "main", clone)
    r = commit(clone, "R")
    a = commit(clone, "A")
    git(clone, "push", "-q", remote, "main:refs/heads/main")
    b = commit(clone, "B")
    return clone, remote, r, a, b


def reach(repo, tip):
    return set(git(repo, "rev-list", tip).split()) if tip else set()


def certified(clone, ranges):
    out = set()
    for rg in ranges:
        rg = rg.strip()
        if rg:
            out |= set(git(clone, "rev-list", rg).split())
    return out


def run_arm(lib, mode):
    td = tempfile.mkdtemp(prefix="push_resync_%s_" % mode.lower())
    try:
        clone, remote, r, a, b = setup(td)
        log = os.path.join(td, "certified.log")
        adv, attempts, base, c, d = a, 1, a, None, None
        if mode in ("REWIND", "RESYNC"):
            git(remote, "update-ref", "refs/heads/main", r)
            adv, attempts = r, (2 if mode == "RESYNC" else 1)
        if mode in ("FORWARD", "STALE", "STALE1", "STALEFWD"):
            other = os.path.join(td, "other")
            git(td, "clone", "-q", "--template=" + os.path.join(td, "empty_template"), remote, other)
            c = commit(other, "C")
            git(other, "push", "-q", "origin", "main:refs/heads/main")
            git(clone, "fetch", "-q", remote, "main:refs/remotes/o/main")
            adv, attempts = c, 2
            if mode.startswith("STALE"):
                base = c                             # the caller certified C..B: B was built on A before C landed
                attempts = 1 if mode == "STALE1" else 2
            if mode == "STALEFWD":
                d = commit(other, "D")               # main moves on again after the certification
                git(other, "push", "-q", "origin", "main:refs/heads/main")
                adv = d
        with open(log, "w", encoding="utf-8") as fh:
            fh.write("%s..%s\n" % (base, b))         # the caller's own certification of base..B
        before = git(remote, "rev-parse", "refs/heads/main")
        drv = os.path.join(td, "driver.ps1")
        with open(drv, "w", encoding="utf-8-sig") as fh:
            fh.write(DRIVER)
        # PowerShell dot-sources only a .ps1 path, and a backup copy of the library carries another suffix
        lib_ps1 = os.path.join(td, "lib_under_test.ps1")
        shutil.copyfile(lib, lib_ps1)
        p = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", drv, "-Lib", lib_ps1,
                            "-Repo", clone, "-PushUrl", remote, "-Sha", b, "-Base", base, "-AdvOid", adv,
                            "-Attempts", str(attempts), "-Log", log], capture_output=True, text=True, env=ENV,
                           creationflags=NOWIN, timeout=600)
        out = p.stdout + p.stderr
        res = None
        for line in out.splitlines():
            if line.startswith("RESULT ") and line[7:].strip():
                res = json.loads(line[7:])
        if res is None:                     # the function did not return: counted as not pushed, with its output shown
            res = {"Pushed": False, "_error": out.strip()[-300:]}
        after = git(remote, "rev-parse", "refs/heads/main")
        newly = reach(remote, after) - reach(remote, before)
        lost = reach(remote, before) - reach(remote, after)
        cert = certified(clone, open(log, encoding="utf-8").read().splitlines())
        return {"pushed": bool(res.get("Pushed")), "after": after, "a": a, "b": b, "r": r, "c": c, "d": d,
                "newly": newly, "lost": lost, "uncertified": newly - cert,
                "log": [l for l in out.splitlines() if l.startswith("LOG ")][-3:]}
    finally:
        rmtree(td)


def main():
    for lib, label in ((LIB, "new"), (OLD, "old"), (OLD_STALE, "old-stale")):
        if not lib:
            continue
        for mode in ("NORMAL", "REWIND", "RESYNC", "FORWARD", "STALE", "STALE1", "STALEFWD"):
            if (label == "old" and mode != "REWIND") or (label == "old-stale" and mode != "STALE"):
                continue
            x = run_arm(lib, mode)
            short = {k: (v[:7] if isinstance(v, str) else v) for k, v in x.items() if k in ("pushed", "after")}
            if label == "new":
                if mode == "NORMAL":
                    ok = x["pushed"] and x["after"] == x["b"] and not x["uncertified"]
                elif mode == "REWIND":
                    ok = not x["pushed"] and x["after"] == x["r"] and not x["newly"]
                elif mode == "STALE1":
                    ok = not x["pushed"] and x["after"] == x["c"] and not x["newly"]
                elif mode in ("STALE", "STALEFWD"):
                    # B' is a new commit on the advertised main, so neither B nor the old main is the tip
                    ok = (x["pushed"] and not x["uncertified"] and x["newly"] and x["after"] not in (x["b"], x["c"], x["d"]))
                else:
                    ok = x["pushed"] and not x["uncertified"] and x["after"] != x["r"]
                ok = bool(ok) and not x["lost"]
                check(ok, "%s %s: %s, newly reachable %d, uncertified %d, lost %d %s" % (
                    label, mode, short, len(x["newly"]), len(x["uncertified"]), len(x["lost"]), x["log"] if not ok else ""))
            elif mode == "REWIND":
                check(x["pushed"] and len(x["uncertified"]) >= 1,
                      "N1 old REWIND: the function before 28 Sep publishes %d commit(s) no certification covered "
                      "(pushed %s): the defect B06 found" % (len(x["uncertified"]), x["pushed"]))
            else:
                check(not x["pushed"] and not x["newly"],
                      "N2 old STALE: the function before 14 Oct leaves the commit local (pushed %s, newly reachable "
                      "%d): the block eight October dreams hit" % (x["pushed"], len(x["newly"])))
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
