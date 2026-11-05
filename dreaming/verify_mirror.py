#!/usr/bin/env python3
"""verify_mirror.py - keep the repo's machinery mirror honest (the restore path rots quietly).

The brain's MACHINERY lives at ~/.claude (dreaming/*, wake_brain.ps1, brain_startup.md); the repo
carries a mirror under 12_cognitive_architecture/ that exists so RESTORE.md can rebuild a dead
laptop. Tonight's finding (2026-07-09): the mirror is synced BY HAND, so it drifts - sessions
copy what they remember to copy, forget the rest, and sometimes edit the repo side directly.
The memory corpus already syncs mechanically every dream; this does the same for the machinery.

  python verify_mirror.py                  # report drift, change nothing (exit 0 always)
  python verify_mirror.py --sync-to-repo   # copy live -> repo for drifted/missing machinery

Scope: code + prompts only (*.py, *.ps1, *.vbs, dream_prompt*.md + the two wake singletons).
Runtime state (tsv/npz/jsonl/logs/agenda/views) is deliberately out of scope - the corpus
robocopy and the scripts that own those files handle them. Sync direction is live -> repo,
never the reverse; a repo-side file NEWER than live is loudly flagged instead of overwritten
blindly (someone worked on the mirror - reconcile by hand, that's a human call).
"""
import hashlib
import io
import os
import shutil
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
LIVE_DR = os.path.join(HOME, ".claude", "dreaming")
REPO_ROOT = os.path.join(HOME, "brain-repo")
REPO = os.path.join(REPO_ROOT, "12_cognitive_architecture")
REPO_DR = os.path.join(REPO, "dreaming")
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

SINGLETONS = [
    (os.path.join(HOME, ".claude", "wake_brain.ps1"), os.path.join(REPO, "wake_brain.ps1")),
    (os.path.join(HOME, ".claude", "brain_startup.md"), os.path.join(REPO, "brain_startup.md")),
]


def _machinery(d):
    out = []
    try:
        for fn in sorted(os.listdir(d)):
            p = os.path.join(d, fn)
            if not os.path.isfile(p):
                continue
            if fn.endswith((".py", ".ps1", ".vbs")) or fn.startswith("dream_prompt"):
                out.append(fn)
    except Exception:
        pass
    return out


def _uncovered_subdirs():
    """Report machinery in SUBDIRECTORIES, which this mirror does not carry.

    _machinery() uses os.listdir, a flat read, so every subdirectory of dreaming/ is
    outside the mirror by construction - not by anyone's decision. That was recorded on
    2026-07-15 for error_graph/ and graph_fire/ and left open; by 2026-08-10 it had grown
    to include job_cycle/ (the session-handover system) and network/, with 132
    subdirectory .py against 215 mirrored at top level and three directories carrying no
    backup at all.

    This only COUNTS them. Syncing would mean deciding which build outputs belong in the
    repository - strong_nodes/runtime is content-addressed generations - and that is the
    owner's call, not a mirror maintainer's. But an unreported gap grows silently, which
    is exactly what happened for three weeks, so the number is printed every run.
    """
    skip = ("__pycache__", ".pytest_cache", "state", "logs", "strong_nodes")
    found = {}
    for dirpath, dirnames, filenames in os.walk(LIVE_DR):
        if dirpath == LIVE_DR:
            dirnames[:] = [d for d in dirnames if d not in skip]
            continue
        rel = os.path.relpath(dirpath, LIVE_DR).replace(os.sep, "/")
        if any(part in skip for part in rel.split("/")):
            continue
        n = sum(1 for f in filenames if f.endswith((".py", ".ps1", ".vbs")))
        if n:
            mirrored = os.path.isdir(os.path.join(REPO_DR, rel.replace("/", os.sep)))
            found[rel] = (n, mirrored)
    return found


def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(*args):
    try:
        r = subprocess.run(["git", "-C", REPO_ROOT] + list(args), capture_output=True, text=True,
                           timeout=60, creationflags=_NOWIN)
    except Exception:
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _repo_side_newer(repo, live):
    """True when the repo copy holds content newer than the live file.

    Judged by git, not by the file's mtime (2026-09-24). A checkout rewrites a file with the
    current time, so an old committed copy looks new: on 09-24 two files (one a 09-09 commit with
    its mtime bumped 09-19 by a checkout, the other a 07-31 commit bumped 09-08) were flagged
    "REPO-SIDE NEWER" and left alone for weeks, while the live files carried two newer rules that
    never reached git.

    When the repo copy is byte-for-byte a committed version (at HEAD or at origin/main - agents
    push by plumbing, so the checkout's HEAD often lags), the time that version was committed is
    compared with the live file's mtime. Only a copy that matches no commit - a hand edit in the
    checkout - falls back to the mtime rule, and so does any git failure (never clobber on doubt).
    """
    live_mtime = os.path.getmtime(live)
    by_mtime = os.path.getmtime(repo) > live_mtime + 2
    rel = os.path.relpath(repo, REPO_ROOT).replace(os.sep, "/")
    work = _git("hash-object", "--", rel)
    if not work:
        return by_mtime
    for ref in ("HEAD", "origin/main"):
        if _git("rev-parse", "-q", "--verify", "%s:%s" % (ref, rel)) == work:
            ct = _git("log", "-1", "--format=%ct", ref, "--", rel)
            try:
                return int(ct) > live_mtime + 2
            except (TypeError, ValueError):
                return by_mtime
    return by_mtime


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    sync = "--sync-to-repo" in sys.argv[1:]
    pairs = [(os.path.join(LIVE_DR, fn), os.path.join(REPO_DR, fn)) for fn in _machinery(LIVE_DR)]
    pairs += SINGLETONS
    same = synced = 0
    drift, repo_newer, missing_repo = [], [], []
    for live, repo in pairs:
        fn = os.path.basename(repo)
        if not os.path.exists(live):
            continue
        if not os.path.exists(repo):
            missing_repo.append(fn)
            if sync:
                try:
                    shutil.copyfile(live, repo)
                    synced += 1
                except Exception as e:
                    print("verify_mirror: copy FAILED for %s: %s" % (fn, e))
            continue
        if _sha(live) == _sha(repo):
            same += 1
            continue
        if _repo_side_newer(repo, live):
            repo_newer.append(fn)   # repo side edited directly - do NOT clobber, a human reconciles
            continue
        drift.append(fn)
        if sync:
            try:
                shutil.copyfile(live, repo)
                synced += 1
            except Exception as e:
                print("verify_mirror: copy FAILED for %s: %s" % (fn, e))

    bits = ["verify_mirror: %d in sync" % same]
    if drift:
        bits.append(("SYNCED live->repo: " if sync else "DRIFT (live newer): ") + ", ".join(drift))
    if missing_repo:
        bits.append(("added to repo: " if sync else "missing in repo: ") + ", ".join(missing_repo))
    if repo_newer:
        bits.append("REPO-SIDE NEWER (left alone - reconcile by hand): " + ", ".join(repo_newer))
    print("; ".join(bits))
    # an orphan on the repo side (machinery retired live) is information, not an action
    orphans = [fn for fn in _machinery(REPO_DR) if not os.path.exists(os.path.join(LIVE_DR, fn))]
    if orphans:
        print("verify_mirror: repo-only (retired live? delete by hand if truly dead): " + ", ".join(orphans))

    # Coverage, not an action: this mirror is flat, so subdirectory machinery is outside
    # the restore path it exists to protect. Printed every run because the gap grew
    # unnoticed from two directories to five between 2026-07-15 and 2026-08-10.
    subs = _uncovered_subdirs()
    if subs:
        total = sum(n for n, _ in subs.values())
        worst = sorted(subs.items(), key=lambda kv: -kv[1][0])[:4]
        print("verify_mirror: NOT COVERED (this mirror is flat) - %d machinery file(s) in "
              "%d subdirectory(ies): %s"
              % (total, len(subs),
                 ", ".join("%s(%d%s)" % (rel, n, "" if mirrored else ", no repo dir")
                           for rel, (n, mirrored) in worst)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
