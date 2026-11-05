# -*- coding: utf-8 -*-
"""Refuse a full checkout of the brain repo (10 Oct 2026).

brain-repo is about 252,000 files and 13 GB at origin/main. A `git worktree add` or a `git clone` of it
checks the whole tree out onto C:. It happened five times (23 Jul, 25 Jul, 28 Sep, 2 Oct, 10 Oct): on 10 Oct 03:19 a
worktree add for a five-file push wrote 215,106 files into a scratch folder before it was stopped, with the reflex
brain_repo_worktree_is_13gb_use_temp_index_2026_09_28 firing on the command. A reflex asks; this refuses.

Refused (PreToolUse, Bash and PowerShell, run by hook_dispatch): `git worktree add` in the brain repo (the command cds
into it, names it with -C, or the session's cwd is inside it) and `git clone` of it, unless the command says
--no-checkout (a clone may also say --sparse or --filter=). Allowed: any other repo, worktree list/remove/prune, and
text that only mentions the words inside a heredoc or here-string.

    python -s brain_checkout_guard.py            (reads the hook JSON on stdin)
    python -s brain_checkout_guard.py --selftest  (controls both ways)
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)
import json
import os
import re
import sys
import time

REPO = "brain-repo"
LOG = os.path.expanduser("~").replace("\\", "/") + "/.claude/brain_graph/logs/guard.log"
GIT = r"\bgit(?:\.exe)?\b(?:\s+-[Cc]\s+(?:\"[^\"]*\"|'[^']*'|\S+))*"
WORKTREE_ADD = re.compile(GIT + r"\s+worktree\s+add\b(?P<rest>[^\n;&|]*)")
CLONE = re.compile(GIT + r"\s+clone\b(?P<rest>[^\n;&|]*)")


def command_part(cmd):
    """The command without heredoc and here-string bodies: text there is data, not a command."""
    for mark in ("<<", "@'", '@"'):
        i = cmd.find(mark)
        if i >= 0:
            cmd = cmd[:i]
    return cmd


def verdict(cmd, cwd=""):
    c = command_part(cmd)
    norm = c.replace("\\", "/")
    in_repo = REPO in norm or REPO in cwd.replace("\\", "/")
    for m in WORKTREE_ADD.finditer(norm):
        if in_repo and "--no-checkout" not in m.group(0):
            return True, "git worktree add"
    for m in CLONE.finditer(norm):
        rest = m.group("rest")
        if REPO in rest and not re.search(r"--no-checkout|--sparse|--filter=", rest):
            return True, "git clone"
    return False, ""


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    if data.get("tool_name") not in ("Bash", "PowerShell"):
        return
    cmd = str((data.get("tool_input") or {}).get("command") or "")
    deny, what = verdict(cmd, str(data.get("cwd") or ""))
    if not deny:
        return
    reason = ("REFUSED: %s of the brain repo checks out about 252,000 files (13 GB) onto C:. To push a few files, build "
              "the commit in a temporary index with no checkout: set GIT_INDEX_FILE to a scratch file; git read-tree "
              "origin/main; h=$(git hash-object -w --path P FILE); git update-index --add --cacheinfo 100644,$h,P; "
              "t=$(git write-tree); c=$(git commit-tree $t -p origin/main -m MSG); scan the range; git push origin "
              "$c:refs/heads/main (memory brain_repo_worktree_is_13gb_use_temp_index_2026_09_28). For a narrow checkout "
              "use --no-checkout, then git sparse-checkout set DIRS." % what)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write("%s checkout refused (%s): %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                                         str(data.get("session_id"))[:8], cmd[:300].replace("\n", " ")))
    except Exception:
        pass
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                        "permissionDecisionReason": reason}}))


def selftest():
    W = "C:/Users/me/AppData/Local/Temp/claude/x/scratchpad/brain_push"
    refuse = [
        ('cd /c/Users/me/brain-repo && W="%s" && timeout 180 git fetch -q origin main && '
         'git worktree add -q --detach "$W" origin/main && cp a b' % W, ""),          # the 10 Oct 03:19 command
        ("git -C C:/Users/me/brain-repo worktree add C:/tmp/wt origin/main", ""),
        ("git worktree add ../wt origin/main", "C:\\Users\\me\\brain-repo"),
        ('git -C "C:\\Users\\me\\brain-repo" worktree add -f D:/wt HEAD', ""),
        ("git clone https://github.com/example/brain-repo.git brain", ""),
    ]
    allow = [
        ("git -C C:/Users/me/brain-repo worktree add --no-checkout C:/tmp/wt origin/main", ""),
        ("git -C C:/Users/me/math-store-main worktree add C:/tmp/mb origin/main", ""),
        ("git worktree list", "C:\\Users\\me\\brain-repo"),
        ("git -C C:/Users/me/brain-repo worktree prune", ""),
        ("git clone --filter=blob:none --sparse https://github.com/example/brain-repo.git b", ""),
        ("cat >> MISTAKES.md <<'EOF'\nran git worktree add of brain-repo\nEOF", ""),
        ("export GIT_INDEX_FILE=/c/tmp/idx && git -C C:/Users/me/brain-repo read-tree origin/main", ""),
    ]
    ok = all(verdict(c, d)[0] for c, d in refuse) and not any(verdict(c, d)[0] for c, d in allow)
    for c, d in refuse:
        if not verdict(c, d)[0]:
            print("MISSED:", c[:100])
    for c, d in allow:
        if verdict(c, d)[0]:
            print("FALSE REFUSAL:", c[:100])
    print("selftest %s (%d refused, %d allowed)" % ("PASS" if ok else "FAIL", len(refuse), len(allow)))
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        sys.exit(selftest())
    try:
        main()
    except Exception:
        pass
