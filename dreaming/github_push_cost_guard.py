#!/usr/bin/env python3
"""github_push_cost_guard.py - a git pre-push hook that keeps GitHub free.

Design note: agents push often (a reminder every half hour, a push per finished piece), and on a private repository
whose workflows run on push, every push starts GitHub Actions. A push is free; the runs are not. Past the account's
free minutes each run is billed, a Windows minute counts twice and a macOS minute ten times. In the deployment this
came from, one private repository with a five-job cross-platform matrix logged about 160 runs in thirty days, almost
all of them started by agent pushes. The rule that followed: everything still gets saved on GitHub, but no push may
cost money without someone deciding it should.

What this hook does, on every `git push` from a clone it is installed in:
  1. reads the workflows AT THE COMMIT BEING PUSHED and decides whether this push starts a run (a push trigger
     that matches the branch and the changed paths; or a pull_request trigger on a non-default branch, which
     re-runs an open pull request's checks);
  2. if it would, the push goes through only when the head commit's message carries [skip ci] (or [ci skip],
     [no ci], [skip actions], [actions skip] - GitHub then starts no push or pull_request run), or when the pusher
     set ALLOW_CI_PUSH=1 because this push needs the test run (then read its result);
  3. otherwise it refuses, says why and how to fix it (git commit --amend to add [skip ci], or ALLOW_CI_PUSH=1).
Pushes to public repositories and pushes that start no workflow pass untouched. Every decision is logged to
github_push_cost_guard.log beside this file (repo, ref, decision; never a credential).

An internal error lets the push through with a loud warning: losing work to a guard bug would break
the other half of the rule (everything still gets saved on GitHub).

Install:   python github_push_cost_guard.py --install <clone> [<clone> ...]   (writes <git-common-dir>/hooks/pre-push)
Selftest:  python github_push_cost_guard.py --selftest
"""
import fnmatch
import io
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "github_push_cost_guard.log")
ZERO = "0" * 40
SKIP_RX = re.compile(r"\[(skip ci|ci skip|no ci|skip actions|actions skip)\]", re.I)
# Repositories known to be PUBLIC (standard runners cost nothing there), as owner/name in BRAIN_PUBLIC_REPOS,
# comma-separated. Everything else is treated as private.
PUBLIC = {s.strip() for s in os.environ.get("BRAIN_PUBLIC_REPOS", "").split(",") if s.strip()}
HOOK_TEXT = """#!/bin/sh
# Cost guard: a push that would start billed GitHub Actions runs on a private repo is refused
# unless the head commit says [skip ci] or ALLOW_CI_PUSH=1 is set. Source: %s
PY="%s"
[ -x "$PY" ] || PY=python
exec "$PY" -X utf8 "%s" "$@"
""" % (os.path.abspath(__file__).replace("\\", "/"), sys.executable.replace("\\", "/"),
       os.path.abspath(__file__).replace("\\", "/"))


def log(msg):
    try:
        with io.open(LOG, "a", encoding="utf-8") as fh:
            fh.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def git(*args, cwd=None):
    p = subprocess.run(["git"] + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=cwd, creationflags=0x08000000 if os.name == "nt" else 0)
    return p.returncode, p.stdout


def repo_slug(url):
    m = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", url or "")
    return ("%s/%s" % (m.group(1), m.group(2))) if m else ""


def glob_rx(pattern):
    """GitHub filter pattern -> regex: * is any run without '/', ** crosses '/', ? one char."""
    out, i = "", 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**", i):
            out += ".*"
            i += 2
            if i < len(pattern) and pattern[i] == "/":
                out += "/?"
                i += 1
            continue
        out += {"*": "[^/]*", "?": "[^/]"}.get(c, re.escape(c))
        i += 1
    return re.compile("^" + out + "$")


def matches(name, patterns):
    """Ordered GitHub semantics: a later !pattern excludes what an earlier pattern included."""
    hit = False
    for p in patterns or []:
        p = str(p)
        if p.startswith("!"):
            if glob_rx(p[1:]).match(name):
                hit = False
        elif glob_rx(p).match(name):
            hit = True
    return hit


def triggers_of(doc):
    on = doc.get("on", doc.get(True)) if isinstance(doc, dict) else None
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return {str(x): None for x in on}
    if isinstance(on, dict):
        return {str(k): v for k, v in on.items()}
    return {}


def push_would_run(cfg, kind, name, changed):
    """cfg is the workflow's push (or pull_request) config; kind 'branch' or 'tag'; changed = file list or None."""
    if not isinstance(cfg, dict):
        return True                                   # bare `push:` runs on every branch and tag
    has_b = "branches" in cfg or "branches-ignore" in cfg
    has_t = "tags" in cfg or "tags-ignore" in cfg
    if kind == "branch":
        if has_t and not has_b:
            return False
        if "branches" in cfg and not matches(name, cfg.get("branches")):
            return False
        if "branches-ignore" in cfg and matches(name, cfg.get("branches-ignore")):
            return False
    else:
        if has_b and not has_t:
            return False
        if "tags" in cfg and not matches(name, cfg.get("tags")):
            return False
        if "tags-ignore" in cfg and matches(name, cfg.get("tags-ignore")):
            return False
        return True                                   # path filters are not evaluated for tags
    if changed is None:
        return True                                   # cannot tell which files changed: assume it runs
    if "paths" in cfg:
        return any(matches(f, cfg.get("paths")) for f in changed)
    if "paths-ignore" in cfg:
        return not all(matches(f, cfg.get("paths-ignore")) for f in changed)
    return True


def workflows_at(sha, cwd=None):
    """[(path, parsed-or-None)] for .github/workflows/*.y*ml at the commit being pushed."""
    rc, out = git("ls-tree", "--name-only", sha, ".github/workflows/", cwd=cwd)
    if rc != 0:
        return []
    found = []
    for path in out.split("\n"):
        path = path.strip()
        if not path.endswith((".yml", ".yaml")):
            continue
        rc, text = git("show", "%s:%s" % (sha, path), cwd=cwd)
        doc = None
        try:
            import yaml
            doc = yaml.safe_load(text)
        except Exception:
            doc = None
        found.append((path, doc))
    return found


def would_start_runs(workflows, ref, changed, default_branch):
    kind, name = ("branch", ref[len("refs/heads/"):]) if ref.startswith("refs/heads/") else (
        ("tag", ref[len("refs/tags/"):]) if ref.startswith("refs/tags/") else ("other", ref))
    hits = []
    for path, doc in workflows:
        if doc is None:
            hits.append((path, "unparsed - assumed to run"))
            continue
        trig = triggers_of(doc)
        if kind in ("branch", "tag") and "push" in trig and push_would_run(trig["push"], kind, name, changed):
            hits.append((path, "push"))
            continue
        if kind == "branch" and name != default_branch:
            for pr in ("pull_request", "pull_request_target"):
                if pr in trig:
                    hits.append((path, "%s (runs if a pull request from this branch is open)" % pr))
                    break
    return hits


MASS_DELETE_FLOOR = 500        # a push deleting fewer files than this is never judged
MASS_DELETE_RATIO = 0.30       # refuse when a push deletes at least this share of the files it starts from
MASS_DELETE_CAP = 50000        # ...or at least this many files, whatever the share


def push_base(local_sha, remote_ref, remote_sha, cwd=None):
    """The commit a push is measured against: the remote's tip when this clone has it, else the merge-base with the
    remote-tracking branch, else the pushed commit's first parent; None when there is nothing to compare with."""
    if remote_sha != ZERO and git("cat-file", "-e", remote_sha + "^{commit}", cwd=cwd)[0] == 0:
        return remote_sha
    if remote_ref.startswith("refs/heads/"):
        rc, out = git("merge-base", local_sha, "refs/remotes/origin/" + remote_ref[len("refs/heads/"):], cwd=cwd)
        if rc == 0 and out.strip():
            return out.strip()
    rc, out = git("rev-parse", "--verify", "-q", local_sha + "^1", cwd=cwd)
    return out.strip() if rc == 0 and out.strip() else None


def mass_delete_check(remote_url, lines, env=None, cwd=None):
    """Refuse a push that deletes most of what it starts from, on every repository, public or private.

    25 Sep 2026: a commit made in a `git clone --no-checkout` clone, with no checkout before `git add`, had an index
    holding only the added file, so the commit kept one file and deleted the other 247,053 of the brain repository.
    Only a stalled secret scan stopped it before the push. Tree-only git calls (--name-only, --no-renames), so a
    partial clone fetches no blobs. ALLOW_MASS_DELETE=1 lets an intended mass deletion through."""
    env = os.environ if env is None else env
    if str(env.get("ALLOW_MASS_DELETE", "")).strip() == "1":
        return True, []
    slug = repo_slug(remote_url) or remote_url
    for line in lines:
        parts = line.split()
        if len(parts) != 4:
            continue
        local_ref, local_sha, remote_ref, remote_sha = parts
        if local_sha == ZERO:
            continue
        base = push_base(local_sha, remote_ref, remote_sha, cwd=cwd)
        if not base:
            continue
        rc, out = git("diff", "--name-only", "--no-renames", "--diff-filter=D", base, local_sha, cwd=cwd)
        if rc != 0:
            continue
        deleted = sum(1 for f in out.split("\n") if f.strip())
        if deleted < MASS_DELETE_FLOOR:
            continue
        rc, out = git("ls-tree", "-r", "--name-only", base, cwd=cwd)
        before = sum(1 for f in out.split("\n") if f.strip()) if rc == 0 else 0
        share = deleted / float(before) if before else 1.0
        if deleted >= MASS_DELETE_CAP or share >= MASS_DELETE_RATIO:
            log("REFUSE mass-delete %s %s: %d of %d files (%.0f%%)" % (slug, remote_ref, deleted, before, 100 * share))
            return False, [
                "[push-guard] REFUSED: this push to %s %s deletes %d of the %d files it starts from (%.0f%%). That is "
                "what a commit made in a clone with an empty index looks like (git clone --no-checkout, then git add "
                "and commit without a checkout): it keeps only the files it added. Check: git diff --name-status "
                "--no-renames %s %s | cut -c1 | sort | uniq -c . If the deletion is really intended: "
                "ALLOW_MASS_DELETE=1 git push ..." % (slug, remote_ref, deleted, before, 100 * share, base[:12],
                                                     local_sha[:12])]
    return True, []


def decide(remote_url, lines, env=None, cwd=None):
    """Return (allow, messages). lines are the pre-push stdin lines."""
    env = os.environ if env is None else env
    slug = repo_slug(remote_url)
    msgs = []
    ok, deletion_msgs = mass_delete_check(remote_url, lines, env=env, cwd=cwd)
    if not ok:
        return False, deletion_msgs
    if slug in PUBLIC:
        log("allow public %s" % slug)
        return True, msgs
    rc, head = git("symbolic-ref", "--short", "refs/remotes/origin/HEAD", cwd=cwd)
    default_branch = head.strip().split("/", 1)[-1] if rc == 0 and head.strip() else "main"
    allow = True
    for line in lines:
        parts = line.split()
        if len(parts) != 4:
            continue
        local_ref, local_sha, remote_ref, remote_sha = parts
        if local_sha == ZERO:
            continue                                  # a branch deletion starts nothing
        changed = None
        if remote_sha != ZERO and git("cat-file", "-e", remote_sha + "^{commit}", cwd=cwd)[0] == 0:
            rc, out = git("diff", "--name-only", remote_sha, local_sha, cwd=cwd)
            changed = [f for f in out.split("\n") if f.strip()] if rc == 0 else None
        hits = would_start_runs(workflows_at(local_sha, cwd=cwd), remote_ref, changed, default_branch)
        if not hits:
            log("allow no-run %s %s" % (slug, remote_ref))
            continue
        rc, message = git("log", "-1", "--format=%B", local_sha, cwd=cwd)
        if SKIP_RX.search(message or ""):
            log("allow skip-ci %s %s (%d workflow(s) skipped)" % (slug, remote_ref, len(hits)))
            continue
        # A pull_request trigger costs only when a pull request from this branch is open, which a pre-push hook
        # cannot see; refusing would block free pushes of work branches (the math-store export pushes to
        # agent/* branches every hour). Warn, and refuse only what is certain to start a billed run.
        certain = [h for h in hits if not h[1].startswith("pull_request")]
        if not certain:
            log("allow pr-warn %s %s -> %s" % (slug, remote_ref, ", ".join(p for p, _ in hits)))
            msgs.append("[push-cost] note: %s has pull_request workflows (%s); if a pull request from %s is open, "
                        "this push re-runs its billed checks. Put [skip ci] in the head commit when the run is not "
                        "needed." % (slug, ", ".join(p for p, _ in hits), remote_ref))
            continue
        if str(env.get("ALLOW_CI_PUSH", "")).strip() == "1":
            log("allow ALLOW_CI_PUSH %s %s -> %s" % (slug, remote_ref, ", ".join(p for p, _ in hits)))
            msgs.append("[push-cost] ALLOW_CI_PUSH=1: this push starts billed runs on %s (%s). Read the run's "
                        "result before calling the work pushed." % (slug, ", ".join(p for p, _ in hits)))
            continue
        allow = False
        log("REFUSE %s %s -> %s" % (slug, remote_ref, "; ".join("%s: %s" % h for h in hits)))
        msgs.append(
            "[push-cost] REFUSED: pushing %s to %s would start GitHub Actions on a private repository, which costs "
            "money once the free minutes are used (Windows x2, macOS x10):\n%s\n"
            "Pushes must cost nothing, and everything still gets saved on GitHub. Fix: put [skip ci] "
            "in the head commit message (git commit --amend, or an empty commit: git commit --allow-empty -m \"sync "
            "[skip ci]\") and push again, in ONE batched push. Only if this push truly needs the test run: "
            "ALLOW_CI_PUSH=1 git push ..., at most once a day, then read the result."
            % (remote_ref, slug, "\n".join("    %s  (%s)" % h for h in hits)))
    return allow, msgs


def install(clones):
    for clone in clones:
        rc, common = git("rev-parse", "--git-common-dir", cwd=clone)
        if rc != 0:
            print("skip (not a git clone): %s" % clone)
            continue
        common = common.strip()
        if not os.path.isabs(common):
            common = os.path.join(clone, common)
        hooks = os.path.join(common, "hooks")
        os.makedirs(hooks, exist_ok=True)
        target = os.path.join(hooks, "pre-push")
        if os.path.exists(target):
            old = io.open(target, encoding="utf-8", errors="replace").read()
            if "github_push_cost_guard.py" not in old:
                print("SKIP (a different pre-push hook exists, not overwritten): %s" % target)
                continue
        with io.open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(HOOK_TEXT)
        print("installed: %s" % target)


def selftest():
    ok = []

    def check(name, cond):
        ok.append(bool(cond))
        print("  %s %s" % ("PASS" if cond else "FAIL", name))

    ci = {"on": {"push": {"branches": ["main"]}, "pull_request": None}}
    enclave = {True: {"pull_request": {"paths": ["confidential/**"]}, "workflow_dispatch": None}}
    wfs = [("ci.yml", ci), ("enclave.yml", enclave)]
    check("a push to main starts ci", would_start_runs(wfs, "refs/heads/main", ["a.py"], "main"))
    check("a push to a feature branch is a possible pull_request run",
          any("pull_request" in why for _, why in would_start_runs(wfs, "refs/heads/feat", ["a.py"], "main")))
    check("a tag push starts nothing here", not would_start_runs(wfs, "refs/tags/v1", None, "main"))
    docs = [("docs.yml", {"on": {"push": {"branches": ["main"], "paths-ignore": ["docs/**", "*.md"]}}})]
    check("paths-ignore: a docs-only push starts nothing",
          not would_start_runs(docs, "refs/heads/main", ["docs/a.md", "README.md"], "main"))
    check("paths-ignore: a code push starts it", would_start_runs(docs, "refs/heads/main", ["docs/a.md", "x.py"], "main"))
    check("glob ** crosses directories", matches("confidential/a/b.py", ["confidential/**"]))
    check("glob * stays in one directory", not matches("a/b.md", ["*.md"]))
    check("negated pattern excludes", not matches("release/x", ["release/**", "!release/x"]))
    check("bare on: push runs on any branch", would_start_runs([("w", {"on": "push"})], "refs/heads/x", None, "main"))
    check("unparsed workflow is assumed to run", would_start_runs([("w", None)], "refs/heads/main", None, "main"))
    check("skip token found", SKIP_RX.search("sync memories [skip ci]") and SKIP_RX.search("x [CI SKIP]"))
    check("no skip token", not SKIP_RX.search("skip ci later"))
    check("slug from https", repo_slug("https://github.com/example-org/private-repo.git") == "example-org/private-repo")
    check("slug from ssh", repo_slug("git@github.com:example-org/brain-framework.git") == "example-org/brain-framework")
    kem = [("kemeny.yml", {"on": {"push": {"paths": ["papers/k/paper/main.tex"]}}})]
    check("paths: a push that does not touch the manuscript starts nothing",
          not would_start_runs(kem, "refs/heads/master", ["papers/k/notes.md"], "master"))
    check("paths: a push touching the manuscript starts the build",
          would_start_runs(kem, "refs/heads/master", ["papers/k/paper/main.tex"], "master"))
    check("a branch filter excludes a work branch",
          not would_start_runs([("ci.yml", {"on": {"push": {"branches": ["main"]}}})], "refs/heads/agent/x", None, "main"))

    # Mass deletion (25 Sep 2026), measured on a real repository built in a temporary folder.
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="pushguard_")
    repo = os.path.join(tmp, "r")
    nowin = 0x08000000 if os.name == "nt" else 0

    def g(*args, cwd=repo, inp=None):
        p = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false"]
                           + list(args), cwd=cwd, input=inp, capture_output=True, text=True, creationflags=nowin)
        return p.stdout.strip()

    try:
        os.makedirs(repo)
        g("init", "-q")
        blob = g("hash-object", "-w", "--stdin", inp="x\n")

        def tree(n, extra=()):
            rows = "".join("100644 blob %s\tf%05d.txt\n" % (blob, i) for i in range(n))
            return g("mktree", inp=rows + "".join("100644 blob %s\t%s\n" % (blob, e) for e in extra))

        base = g("commit-tree", tree(1000), "-m", "base")
        add_one = g("commit-tree", tree(1000, ["new.md"]), "-p", base, "-m", "add one")
        keep_one = g("commit-tree", tree(0, ["new.md"]), "-p", base, "-m", "empty index")
        cut_300 = g("commit-tree", tree(700), "-p", base, "-m", "delete 300")
        url = "https://github.com/example-org/some-private-repo.git"

        def line(sha, remote=base, ref="refs/heads/main"):
            return ["%s %s %s %s" % (ref, sha, ref, remote)]

        check("mass delete: adding one file passes", mass_delete_check(url, line(add_one), {}, cwd=repo)[0])
        check("mass delete: deleting 300 of 1000 (under the floor) passes",
              mass_delete_check(url, line(cut_300), {}, cwd=repo)[0])
        check("mass delete: a commit that keeps 1 file of 1000 is refused",
              not mass_delete_check(url, line(keep_one), {}, cwd=repo)[0])
        check("mass delete: refused on a new branch too (measured against the first parent)",
              not mass_delete_check(url, line(keep_one, remote=ZERO, ref="refs/heads/x"), {}, cwd=repo)[0])
        check("mass delete: ALLOW_MASS_DELETE=1 lets it through",
              mass_delete_check(url, line(keep_one), {"ALLOW_MASS_DELETE": "1"}, cwd=repo)[0])
        PUBLIC.add("example-org/public-repo")
        check("mass delete: decide refuses it on a public repository as well",
              not decide("https://github.com/example-org/public-repo.git", line(keep_one), env={}, cwd=repo)[0])
        # the lived failure: a --no-checkout clone, one file added and committed with no checkout first
        g("update-ref", "refs/heads/main", base)
        clone = os.path.join(tmp, "c")
        g("clone", "-q", "--no-checkout", repo, clone, cwd=tmp)
        with io.open(os.path.join(clone, "memo.md"), "w", encoding="utf-8") as fh:
            fh.write("one memory\n")
        g("add", "memo.md", cwd=clone)
        g("commit", "-q", "-m", "add memo", cwd=clone)
        head = g("rev-parse", "HEAD", cwd=clone)
        check("mass delete: the commit from a --no-checkout clone is refused",
              bool(head) and not mass_delete_check(url, line(head), {}, cwd=clone)[0])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("selftest %s: %d/%d" % ("ok" if all(ok) else "FAILED", sum(ok), len(ok)))
    return 0 if all(ok) else 1


def main():
    if "--selftest" in sys.argv:
        return selftest()
    if "--install" in sys.argv:
        install(sys.argv[sys.argv.index("--install") + 1:])
        return 0
    try:
        remote_url = sys.argv[2] if len(sys.argv) > 2 else (sys.argv[1] if len(sys.argv) > 1 else "")
        lines = [l for l in sys.stdin.read().splitlines() if l.strip()]
        allow, msgs = decide(remote_url, lines)
        for m in msgs:
            sys.stderr.write(m + "\n")
        return 0 if allow else 1
    except Exception as exc:
        log("ERROR (push allowed): %r" % (exc,))
        sys.stderr.write("[push-cost] guard error, push allowed: %r - check that this push does not start billed "
                         "CI (head commit [skip ci]).\n" % (exc,))
        return 0


if __name__ == "__main__":
    sys.exit(main())
