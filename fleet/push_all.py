#!/usr/bin/env python3
"""push_all.py - the safe per-repo GitHub pusher, codified (owner push-cadence rule).

Secret-scan the push contents FIRST (dreaming/scan_secrets.py); git pull --rebase; git push;
never force. By DEFAULT it only pushes already-COMMITTED unpushed commits - it does NOT commit
your dirty working tree (committing another agent's mid-edit is that agent's own call).
--commit-and-push stages TRACKED changes (never untracked) and commits with your message,
still secret-scanned, still never force.

  python push_all.py <repo-path>                          # push committed-unpushed only
  python push_all.py <repo-path> --dry-run                # scan + show, push nothing
  python push_all.py <repo-path> --commit-and-push -m MSG # also commit tracked dirty files

Exit: 0 pushed / nothing-to-do, 2 secret-hold, 3 no-upstream/other, 4 rebase-conflict.
"""
import os, sys, subprocess, argparse

HOME = os.path.expanduser("~")
SCAN = os.path.join(HOME, ".claude", "dreaming", "scan_secrets.py")
NO_WIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def git(repo, *a, timeout=180):
    r = subprocess.run(["git", "-C", repo] + list(a), capture_output=True, text=True,
                       timeout=timeout, creationflags=NO_WIN)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def scan_files(abs_files):
    """Whole-file scan (conservative) of each existing file; returns [(file, detail)] flagged."""
    flagged = []
    for f in abs_files:
        if not os.path.isfile(f):
            continue
        r = subprocess.run(["python", SCAN, f], capture_output=True, text=True, creationflags=NO_WIN)
        if r.returncode != 0:
            flagged.append((f, (r.stdout or "").strip()))
    return flagged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--commit-and-push", action="store_true")
    ap.add_argument("-m", "--message", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    repo = os.path.abspath(a.repo)
    rc_chk, _, _ = git(repo, "rev-parse", "--is-inside-work-tree")   # handles worktrees (.git is a file)
    if rc_chk != 0:
        print("not a git repo: %s" % repo)
        return 3
    _, branch, _ = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    rc_u, upstream, _ = git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if rc_u != 0 or not upstream:
        print("HOLD: no upstream tracking branch for %s - set one (git push -u) or push "
              "manually; this tool will not guess a remote branch." % branch)
        return 3

    # SECRET GATE. Two things get published, scanned two different ways (independent audit 2026-07-12):
    #  - committed unpushed history -> scan the ACTUAL pushed blobs via `scan_secrets.py --range`. A
    #    working-tree scan misses a secret that was committed then edited off disk, a deleted file whose
    #    blob still pushes, or one added+removed across intermediate commits - all push, all scan "clean".
    #  - (opt-in --commit-and-push) tracked dirty files -> disk IS what gets committed, so whole-file scan.
    rng = subprocess.run(["python", SCAN, "--range", "%s..HEAD" % upstream],
                         cwd=repo, capture_output=True, text=True, creationflags=NO_WIN)
    if rng.returncode != 0:      # 1 = secret in the range, 2 = scanner/git error -> fail closed either way
        print("SECRET-HOLD: push range %s..HEAD did not scan clean - NOT pushing %s:" % (upstream, branch))
        print((rng.stdout or rng.stderr or "").strip()[:600])
        return 2
    dirty = []
    if a.commit_and_push:
        _, uo, _ = git(repo, "diff", "--name-only")            # unstaged tracked
        _, so, _ = git(repo, "diff", "--cached", "--name-only")  # staged
        dirty = sorted(set(x for x in (uo.splitlines() + so.splitlines()) if x))
        flagged = scan_files([os.path.join(repo, f) for f in dirty])
        if flagged:
            print("SECRET-HOLD: scan flagged %d dirty file(s) - NOT pushing %s:" % (len(flagged), branch))
            for f, det in flagged:
                print("  %s" % f)
            return 2

    # refresh the tracking ref so ahead/behind are real (the local @{u} can be stale), then commit the
    # opt-in dirty set, then decide by ahead/behind (independent audit 2026-07-12):
    git(repo, "fetch")

    if a.commit_and_push and dirty:
        if not a.message:
            print("--commit-and-push needs -m MESSAGE")
            return 3
        git(repo, "add", "-u")                 # tracked modifications only, never untracked
        rc_c, o_c, e_c = git(repo, "commit", "-m", a.message)
        if rc_c != 0:                          # a failing hook / unset identity must NOT silently push older commits
            print("commit FAILED (not pushing): " + (o_c + e_c)[:300])
            return 3
        print(o_c or e_c)

    _, ahead, _ = git(repo, "rev-list", "--count", "%s..HEAD" % upstream)
    _, behind, _ = git(repo, "rev-list", "--count", "HEAD..%s" % upstream)
    print("repo=%s\n  branch=%s upstream=%s ahead=%s behind=%s tracked_dirty_to_commit=%d secrets=CLEAN"
          % (repo, branch, upstream, ahead, behind, len(dirty)))

    if ahead in ("0", ""):
        # nothing to push - do NOT pull (fast-forwarding a live checkout under another agent is an
        # unadvertised mutation from a tool that promises 'nothing to do').
        print("nothing to push (0 commits ahead)%s - not touching the working tree."
              % (" [dry-run]" if a.dry_run else ""))
        return 0

    if a.dry_run:
        print("[dry-run] ahead=%s behind=%s: would %spush (never --force)."
              % (ahead, behind, "rebase (--autostash) then " if behind not in ("0", "") else ""))
        return 0

    if behind not in ("0", ""):
        # --autostash lets a dirty default-mode tree rebase instead of the tool refusing to push at all.
        try:
            rc_r, o_r, e_r = git(repo, "pull", "--rebase", "--autostash")
        except Exception as ex:
            git(repo, "rebase", "--abort")
            print("rebase errored (%s) - aborted, not pushing." % ex)
            return 4
        if rc_r != 0:
            git(repo, "rebase", "--abort")
            print("REBASE could not complete cleanly - aborted, resolve manually:\n" + (o_r + e_r)[:400])
            return 4
    rc_p, o_p, e_p = git(repo, "push")
    print(o_p or e_p)
    return 0 if rc_p == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
