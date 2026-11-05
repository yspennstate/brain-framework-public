#!/usr/bin/env python3
"""Additive dream persistence without changing the shared checkout.

Default: dry run (writes unreachable blobs/trees, never commits or updates refs).
Explicit --path files are new-files-only. Existing files are kept from --base;
only explicit --ledger JSONL files may change, by lossless record union. A
checklist ledger's derived state must be rebuilt separately, never overwritten.
No discovery, staging-all, reset, stash, worktree, or implicit fetch is used.
Python 3.9+, Git 2.x; pathlib and forward-slash Git paths work on Windows/POSIX.
"""

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile


class SyncError(RuntimeError):
    pass


def git(repo, *args, data=None, env=None, check=True):
    child_env = os.environ.copy()
    # Do not inherit another session's index, worktree, or object-store routing.
    for key in list(child_env):
        if key.startswith("GIT_") and key not in {"GIT_EXEC_PATH"}:
            child_env.pop(key)
    child_env.update(GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0",
                     GIT_CONFIG_NOSYSTEM="1", GIT_NO_REPLACE_OBJECTS="1")
    if env:
        child_env.update(env)
    try:
        result = subprocess.run(
            [shutil.which("git") or "git", "--no-optional-locks", "-C", str(repo),
             "-c", "maintenance.auto=false", "-c", "gc.auto=0",
             "-c", "core.fsmonitor=false", *args],
            input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            # macOS sandbox can stall fork/exec before Popen's timeout starts.
            # An absolute executable and close_fds=False enable posix_spawn there.
            # Python-created descriptors are non-inheritable by default (PEP 446).
            close_fds=sys.platform != "darwin",
            env=child_env, timeout=120, check=False)
    except subprocess.TimeoutExpired as exc:
        raise SyncError(f"git {args[0]} timed out after 120 seconds; sync blocked") from exc
    if check and result.returncode:
        raise SyncError(f"git {args[0]} exited {result.returncode}: "
                        + result.stderr.decode("utf-8", "replace").strip())
    return result


def output(repo, *args, **kwargs):
    return git(repo, *args, **kwargs).stdout.decode("ascii").strip()


def safe_path(repo, name):
    path = PurePosixPath(name)
    if (not name or "\\" in name or ":" in name or "\x00" in name
            or path.is_absolute() or any(p in {".", "..", ".git"}
                                        for p in name.split("/"))
            or any(p.lower() == ".git" for p in path.parts)
            or name != path.as_posix()):
        raise SyncError(f"unsafe repository path: {name!r}")
    local = repo.joinpath(*path.parts)
    # Do not follow symlinks or Windows junctions out of the checkout.
    for parent in [local, *local.parents]:
        if parent == repo:
            break
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise SyncError(f"symlink/junction candidate: {name}")
    if not local.resolve().is_relative_to(repo):
        raise SyncError(f"candidate outside repository: {name}")
    return local


def records(raw, label):
    """Validate JSONL and deduplicate semantic records, preserving original bytes."""
    result = []
    seen = set()
    for number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line.decode("utf-8"),
                                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            if not isinstance(record, dict):
                raise ValueError("record must be an object")
            key = json.dumps(record, sort_keys=True, ensure_ascii=True,
                             separators=(",", ":"), allow_nan=False)
        except (UnicodeError, ValueError) as exc:
            raise SyncError(f"invalid JSONL {label}:{number}: {exc}") from exc
        if key not in seen:
            result.append((key, line))
            seen.add(key)
    return result


def union_ledger(remote, local, name):
    """Keep base bytes verbatim; append distinct local events in local order.

    Same id/timestamp with different content is deliberately retained: generic
    sync cannot re-id domain events or invent an ordering for derived state.
    """
    existing = records(remote, name + " (base)")
    incoming = records(local, name + " (local)")
    seen = {key for key, _ in existing}
    additions = [line for key, line in incoming if key not in seen]
    if not additions:
        return remote
    return remote + (b"\n" if remote and not remote.endswith(b"\n") else b"") + b"\n".join(additions) + b"\n"


def base_commit(repo, base):
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", base):
        raise SyncError("base must be an explicit full commit OID")
    if output(repo, "cat-file", "-t", base) != "commit":
        raise SyncError("base is not a locally available commit; fetch separately")
    return base


def build(repo, base, paths, ledgers=(), create_commit=False,
          message="Persist additive dream data"):
    repo = Path(repo).resolve()
    base_commit(repo, base)
    paths = sorted(set(paths))
    ledgers = set(ledgers)
    if not ledgers.issubset(paths):
        raise SyncError("every ledger must also be an explicit --path")
    entries = {}
    # ls-tree reads tree metadata, not the absent blobs of a partial clone.
    for entry in git(repo, "ls-tree", "-rz", "--full-tree", base).stdout.split(b"\0"):
        if entry:
            meta, name = entry.split(b"\t", 1)
            mode, kind, oid = meta.decode("ascii").split()
            entries[os.fsdecode(name)] = (mode, kind, oid)
    changes = []
    skipped = []
    with tempfile.TemporaryDirectory(prefix="dream-sync-") as scratch:
        env = {"GIT_INDEX_FILE": str(Path(scratch) / "index")}
        git(repo, "read-tree", base, env=env)
        for name in paths:
            local = safe_path(repo, name)
            old = entries.get(name)
            if old and name not in ledgers:
                skipped.append({"path": name, "reason": "already exists in base"})
                continue
            if any(p.startswith(name + "/") for p in entries):
                raise SyncError(f"candidate collides with base directory: {name}")
            if any(parent.as_posix() in entries for parent in PurePosixPath(name).parents
                   if parent.as_posix() != "."):
                raise SyncError(f"candidate descends from base file: {name}")
            if not local.is_file():
                raise SyncError(f"candidate is not a regular file: {name}")
            before = local.stat()
            data = local.read_bytes()
            after = local.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (
                    after.st_size, after.st_mtime_ns, after.st_ino):
                raise SyncError(f"candidate changed while read: {name}; retry")
            mode = "100755" if os.name != "nt" and before.st_mode & 0o111 else "100644"
            if name in ledgers:
                if not name.endswith(".jsonl"):
                    raise SyncError(f"ledger must be JSONL: {name}")
                if old and (old[1] != "blob" or old[0] not in {"100644", "100755"}):
                    raise SyncError(f"ledger base entry is not a regular blob: {name}")
                if old:
                    # GIT_NO_LAZY_FETCH needs git 2.44; Apple git 2.39 ignores it, and an absent blob makes cat-file
                    # walk every object of the 5 GB pack first (3 Nov: 20+ min under swap). Fetch the one blob.
                    git(repo, "-c", "fetch.negotiationAlgorithm=noop", "fetch", "-q", "origin", "--no-tags",
                        "--no-write-fetch-head", "--recurse-submodules=no", "--filter=blob:none", old[2])
                remote = git(repo, "cat-file", "blob", old[2]).stdout if old else b""
                data = union_ledger(remote, data, name)
                if old:
                    mode = old[0]
            oid = output(repo, "hash-object", "-w", "--stdin", data=data)
            if old and oid == old[2]:
                skipped.append({"path": name, "reason": "union unchanged"})
                continue
            git(repo, "update-index", "--add", "--cacheinfo", mode, oid, name, env=env)
            changes.append({"path": name, "action": "union" if old else "add",
                            "blob": oid, "bytes": len(data)})
        tree = output(repo, "write-tree", "--missing-ok", env=env)
    commit = None
    if changes and create_commit:
        commit = output(repo, "commit-tree", tree, "-p", base,
                        data=(message + "\n").encode("utf-8"))
    return {"base": base, "tree": tree, "commit": commit,
            "changes": changes, "skipped": skipped, "dry_run": not create_commit}


def advertised(repo, remote, branch):
    lines = git(repo, "ls-remote", "--refs", "--", remote,
                f"refs/heads/{branch}").stdout.splitlines()
    if len(lines) != 1:
        raise SyncError("remote branch must exist and advertise exactly one OID")
    oid, ref = lines[0].decode("ascii").split()
    if ref != f"refs/heads/{branch}":
        raise SyncError("unexpected advertised ref")
    return base_commit(repo, oid)


def push_endpoint(repo, remote):
    # A named remote may have a different pushurl (or several of them).
    # Certify and lease against the exact same endpoint that will receive data.
    names = git(repo, "remote").stdout.decode("utf-8").splitlines()
    if remote not in names:
        return remote
    urls = git(repo, "remote", "get-url", "--push", "--all", remote).stdout.decode("utf-8").splitlines()
    if len(urls) != 1 or not urls[0]:
        raise SyncError("publication requires exactly one push endpoint")
    return urls[0]


def push_with_resync(repo, remote, branch, paths, ledgers, certifier,
                     message="Persist additive dream data", attempts=3):
    """Explicit publish API; callers must provide a fail-closed range scanner.

    A moved remote is re-read and rebuilt from scratch, then re-certified.
    Missing commits/blobs fail closed; never fetch implicitly. Every leased push
    is also ancestry-checked so a matching lease cannot authorize a rewind.
    """
    if not certifier:
        raise SyncError("publishing requires an explicit certifier")
    git(repo, "check-ref-format", f"refs/heads/{branch}")
    remote = push_endpoint(repo, remote)
    for attempt in range(attempts):
        base = advertised(repo, remote, branch)
        plan = build(repo, base, paths, ledgers, True, message)
        if not plan["commit"]:
            return dict(plan, pushed=False, reason="no additive changes")
        commit = plan["commit"]
        git(repo, "merge-base", "--is-ancestor", base, commit)
        # Callable receives the exact immutable range, never a moving ref.
        if certifier(f"{base}..{commit}") is not True:
            raise SyncError("exact range did not certify; no push attempted")
        result = git(repo, "-c", "push.followTags=false", "-c", "push.recurseSubmodules=no",
                     "push", "--no-follow-tags", "--no-tags", "--recurse-submodules=no",
                     f"--force-with-lease=refs/heads/{branch}:{base}", "--",
                     remote, f"{commit}:refs/heads/{branch}", check=False)
        if result.returncode == 0:
            return dict(plan, pushed=True)
        if advertised(repo, remote, branch) == base:
            raise SyncError("push failed without remote movement: "
                            + result.stderr.decode("utf-8", "replace"))
    raise SyncError("remote kept moving; retry limit reached; checkout untouched")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--base", required=True, help="locally available advertised commit OID")
    parser.add_argument("--path", action="append", default=[], dest="paths")
    parser.add_argument("--ledger", action="append", default=[])
    parser.add_argument("--create-commit", action="store_true",
                        help="create an unattached commit; does not publish or update a branch")
    parser.add_argument("--message", default="Persist additive dream data")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(build(args.repo, args.base, args.paths, args.ledger,
                               args.create_commit, args.message), indent=2))
        return 0
    except (SyncError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"dream sync blocked: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
