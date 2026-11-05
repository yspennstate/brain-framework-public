#!/usr/bin/env python3
"""Independent GitHub + S3 backup for the shared brain repository.

The source worktree and index are read-only. GitHub receives a fresh-history,
full-tree-scanned operational snapshot. S3 receives the exact Git object
database plus immutable recovery snapshots for uncommitted/untracked files.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from typing import Iterable, Mapping, Sequence

try:
    import sanitized_snapshot
    import envelope
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import sanitized_snapshot
    import envelope


DEFAULT_REPO = Path(r"{{USERPROFILE}}\brain-repo")
DEFAULT_BUCKET = "<github-user>-ai-brain-backup-<aws-account-primary>"
DEFAULT_REGION = "us-east-1"
DEFAULT_SECONDARY_BUCKET = "<github-user>-ai-brain-backup-secondary-<aws-account-secondary>"
DEFAULT_SECONDARY_REGION = "us-west-2"
DEFAULT_SECONDARY_PROFILE = "secondary"
DEFAULT_REMOTE = "origin"
DEFAULT_STATE = Path(r"{{USERPROFILE}}\.brain-resilience")
DEFAULT_KMS_KEY = "alias/brain-resilience-backup"
DEFAULT_SIGNING_KEY = "alias/brain-resilience-receipt-signing-rsa"
DEFAULT_SECONDARY_KMS_KEY = "alias/brain-resilience-backup"
DEFAULT_SECONDARY_SIGNING_KEY = "alias/brain-resilience-receipt-signing-rsa"
PINNED_SIGNERS = {
    "arn:aws:kms:us-east-1:<aws-account-primary>:key/<kms-key-id-1>": Path(__file__).resolve().parent / "trust" / "primary-receipt-signing.pub",
    "arn:aws:kms:us-west-2:<aws-account-secondary>:key/<kms-key-id-2>": Path(__file__).resolve().parent / "trust" / "secondary-receipt-signing.pub",
}
PINNED_SIGNER_SHA256 = {
    "arn:aws:kms:us-east-1:<aws-account-primary>:key/<kms-key-id-1>": "<sha256-of-pinned-pubkey-1>",
    "arn:aws:kms:us-west-2:<aws-account-secondary>:key/<kms-key-id-2>": "<sha256-of-pinned-pubkey-2>",
}
PINNED_ENCRYPTION_KEYS = (
    "arn:aws:kms:us-east-1:<aws-account-primary>:key/<kms-key-id-3>",
    "arn:aws:kms:us-west-2:<aws-account-secondary>:key/<kms-key-id-4>",
)
RUNNER_ID = "brain-backup-runner"
MIN_TRACKED_FILES = 100_000
UPLOAD_CHUNK_BYTES = 512 * 1024 * 1024
COMPUTE_STATE_PATH = Path(r"{{USERPROFILE}}\.claude\compute\compute_state.json")


class BackupError(RuntimeError):
    pass


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def stamp(value: dt.datetime | None = None) -> str:
    return (value or utc_now()).strftime("%Y%m%dT%H%M%SZ")


def log(message: str) -> None:
    print(f"{utc_now().isoformat(timespec='seconds')} {message}", flush=True)


def require_compute_window(path: Path = COMPUTE_STATE_PATH) -> None:
    """Fail closed before heavy work when this machine is not owner-away/open."""

    if not path.is_file():
        return
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError("compute state is unreadable; deferring heavy backup work") from exc
    if not isinstance(value, dict):
        raise BackupError("compute state has an unexpected shape; deferring heavy backup work")
    mode = str(value.get("mode", ""))
    if value.get("status") == "distress" or value.get("owner_active") is True:
        raise BackupError("compute governor reports distress/owner activity; backup deferred")
    if mode not in {"open", "owner_away_open"}:
        raise BackupError(f"compute mode is {mode or 'unknown'}; backup waits for owner-away/open")


def run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    ok: Iterable[int] = (0,),
    text: bool = True,
    stdout_file: Path | None = None,
) -> subprocess.CompletedProcess:
    kwargs: dict[str, object] = {
        "cwd": str(cwd) if cwd else None,
        "stderr": subprocess.PIPE,
        "text": text,
        "env": {**os.environ, "AWS_PAGER": "", "AWS_CLI_AUTO_PROMPT": "off"},
    }
    handle = None
    if stdout_file is None:
        kwargs["stdout"] = subprocess.PIPE
    else:
        stdout_file.parent.mkdir(parents=True, exist_ok=True)
        handle = stdout_file.open("wb")
        kwargs["stdout"] = handle
        kwargs["text"] = False
    try:
        result = subprocess.run(list(argv), **kwargs)
    finally:
        if handle is not None:
            handle.close()
    if check and result.returncode not in set(ok):
        err = result.stderr if isinstance(result.stderr, str) else (result.stderr or b"").decode("utf-8", "replace")
        raise BackupError(f"command failed ({result.returncode}): {' '.join(argv)}\n{err[-4000:]}")
    return result


def output(argv: Sequence[str], *, cwd: Path) -> str:
    result = run(argv, cwd=cwd)
    assert isinstance(result.stdout, str)
    return result.stdout.strip()


def output_bytes(argv: Sequence[str], *, cwd: Path) -> bytes:
    result = run(argv, cwd=cwd, text=False)
    assert isinstance(result.stdout, bytes)
    return result.stdout


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


class ProcessLock:
    """One-byte OS lock; released by the kernel even after a crash."""

    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def __enter__(self) -> "ProcessLock":
        assert_external(str(self.path.parent.resolve()))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise BackupError("another brain backup is already running") from exc
        return self

    def __exit__(self, *_: object) -> None:
        if self.handle is None:
            return
        self.handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


class MeshLease:
    def __init__(self, mesh: Path, repo: Path, bucket: str, remote_repo: str,
                 enabled: bool = True, state: Path = DEFAULT_STATE,
                 secondary_bucket: str = DEFAULT_SECONDARY_BUCKET):
        self.mesh = mesh
        self.repo = repo
        self.bucket = bucket
        self.remote_repo = remote_repo
        self.state = state.resolve()
        self.secondary_bucket = secondary_bucket
        self.enabled = enabled
        self.stop = threading.Event()
        self.pulser: threading.Thread | None = None
        self.success = False

    def _mesh(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return run([sys.executable, str(self.mesh), *args], cwd=self.repo, check=check)

    def __enter__(self) -> "MeshLease":
        global _ACTIVE_MESH_LEASE
        if not self.enabled:
            return self
        self._mesh(
            "hello", "--agent", RUNNER_ID, "--runtime", "headless",
            "--task", "Push the independent GitHub and S3 brain backup",
            "--cwd", str(self.repo), "--status", "working", "--ttl", "20m",
        )
        claim = self._mesh(
            "claim", "--agent", RUNNER_ID,
            "--resource", f"external:aws/s3/{self.bucket}",
            "--resource", f"external:github/{self.remote_repo}",
            "--resource", "external:aws/kms/brain-resilience-backup",
            "--resource", "external:aws/kms/brain-resilience-signing",
            "--resource", f"external:aws/s3/{self.secondary_bucket}",
            "--resource", "external:aws/kms/secondary-brain-resilience-backup",
            "--resource", "external:aws/kms/secondary-brain-resilience-signing",
            "--resource", str(self.state),
            "--mode", "write", "--purpose", "Atomic scheduled brain backup", "--ttl", "20m",
            check=False,
        )
        if claim.returncode != 0:
            self._mesh("bye", "--agent", RUNNER_ID, "--status", "offline", "--summary", "Backup deferred: external claim busy", check=False)
            raise BackupError("backup external resources are claimed by another live agent")
        def pulse() -> None:
            while not self.stop.wait(300):
                self._mesh("pulse", "--agent", RUNNER_ID, "--status", "working", "--task", "Independent brain backup in progress", check=False)

        self.pulser = threading.Thread(target=pulse, name="mesh-pulse", daemon=True)
        self.pulser.start()
        _ACTIVE_MESH_LEASE = self
        return self

    def __exit__(self, exc_type: object, *_: object) -> None:
        global _ACTIVE_MESH_LEASE
        if not self.enabled:
            return
        self.stop.set()
        if self.pulser:
            self.pulser.join(timeout=2)
        summary = "Independent GitHub and S3 backup verified" if exc_type is None else "Backup exited with an error; see local log"
        status = "done" if exc_type is None else "offline"
        self._mesh("bye", "--agent", RUNNER_ID, "--status", status, "--summary", summary, check=False)
        _ACTIVE_MESH_LEASE = None


_ACTIVE_MESH_LEASE: MeshLease | None = None


def assert_external(resource: str) -> None:
    lease = _ACTIVE_MESH_LEASE
    if lease is None or not lease.enabled:
        return
    result = lease._mesh(
        "assert", "--agent", RUNNER_ID, "--resource", resource, check=False,
    )
    if result.returncode != 0:
        raise BackupError(f"agent-mesh claim expired before external mutation: {resource}")


def git(repo: Path, *args: str) -> str:
    return output(["git", *args], cwd=repo)


def selected_refs(repo: Path) -> dict[str, str]:
    raw = git(repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs")
    refs: dict[str, str] = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) != 2 or len(parts[1]) != 40:
            raise BackupError(f"unexpected Git ref row: {line[:200]}")
        refs[parts[0]] = parts[1]
    return dict(sorted(refs.items()))


def source_snapshot(repo: Path, *, min_tracked: int = MIN_TRACKED_FILES) -> dict[str, object]:
    if not (repo / ".git").is_dir() or not (repo / "RESTORE.md").is_file():
        raise BackupError(f"refusing unexpected source repo: {repo}")
    top = Path(git(repo, "rev-parse", "--show-toplevel")).resolve()
    if top != repo.resolve():
        raise BackupError(f"source path is not the repository root: {top}")
    tracked_blob = output_bytes(["git", "ls-files", "-z"], cwd=repo)
    tracked = tracked_blob.count(b"\0")
    if tracked < min_tracked:
        raise BackupError(f"tracked-file safety floor failed: {tracked} < {min_tracked}")
    status = output_bytes(["git", "status", "--porcelain=v2", "-z", "--untracked-files=all"], cwd=repo)
    untracked = output_bytes(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=repo)
    refs = selected_refs(repo)
    return {
        "head_sha": git(repo, "rev-parse", "HEAD"),
        "head_tree": git(repo, "rev-parse", "HEAD^{tree}"),
        "tracked_files": tracked,
        "untracked_files": untracked.count(b"\0"),
        "status_sha256": sha256_bytes(status),
        "status_entries": status.count(b"\0"),
        "status_bytes": status,
        "source_refs": refs,
        "source_refs_sha256": hashlib.sha256(envelope.canonical_json_bytes(refs)).hexdigest(),
    }


def remote_tip(repo: Path, remote: str) -> str | None:
    result = run(["git", "ls-remote", "--heads", remote, "refs/heads/main"], cwd=repo)
    assert isinstance(result.stdout, str)
    line = result.stdout.strip()
    if not line:
        return None
    candidate = line.split()[0]
    if len(candidate) != 40 or any(ch not in "0123456789abcdef" for ch in candidate.lower()):
        raise BackupError(f"unexpected remote ref output: {line[:200]}")
    return candidate


def push_ref(repo: Path, remote: str, sha: str) -> None:
    last_error = ""
    for attempt, delay in enumerate((0, 5, 15), start=1):
        if delay:
            time.sleep(delay)
        assert_external("external:github/<github-user>/ai-brain-resilience-backup")
        result = run(["git", "push", remote, f"{sha}:refs/heads/main"], cwd=repo, check=False)
        if result.returncode == 0:
            return
        last_error = result.stderr if isinstance(result.stderr, str) else "push failed"
        log(f"GitHub push attempt {attempt} failed; retrying")
    raise BackupError(f"GitHub push failed after retries: {last_error[-3000:]}")


def push_github(repo: Path, remote: str, head: str) -> str:
    tip = remote_tip(repo, remote)
    if tip is None:
        raise BackupError("GitHub backup is empty; run seed-github once")
    if tip == head:
        return tip
    local = run(["git", "cat-file", "-e", f"{tip}^{{commit}}"], cwd=repo, check=False)
    if local.returncode != 0:
        raise BackupError("backup remote tip is not present locally; refusing an ambiguous update")
    ancestor = run(["git", "merge-base", "--is-ancestor", tip, head], cwd=repo, check=False)
    if ancestor.returncode != 0:
        raise BackupError("backup remote diverged; refusing to force-push")
    push_ref(repo, remote, head)
    verified = remote_tip(repo, remote)
    if verified != head:
        raise BackupError(f"GitHub verification failed: expected {head}, found {verified}")
    return verified


class _HashingReader:
    def __init__(self, handle: object):
        self.handle = handle
        self.digest = hashlib.sha256()
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        data = self.handle.read(size)
        self.digest.update(data)
        self.bytes_read += len(data)
        return data


def write_recovery_artifacts(repo: Path, stage: Path, captured: dict[str, object]) -> dict[str, object]:
    """Capture recovery bytes once and return a content-level fingerprint."""

    stage.mkdir(parents=True, exist_ok=True)
    head = str(captured["head_sha"])
    run(["git", "diff", "--binary", head, "--", "."], cwd=repo, stdout_file=stage / "working-tree.patch")
    run(["git", "diff", "--cached", "--binary", head, "--", "."], cwd=repo, stdout_file=stage / "index.patch")
    (stage / "status.porcelain-v2.z").write_bytes(captured["status_bytes"] if isinstance(captured["status_bytes"], bytes) else b"")
    refs = captured.get("source_refs")
    if not isinstance(refs, dict):
        raise BackupError("capture is missing the selected Git ref map")
    (stage / "refs.txt").write_text(
        "".join(f"{sha} {ref}\n" for ref, sha in sorted(refs.items())),
        encoding="utf-8", newline="\n",
    )
    raw_untracked = output_bytes(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=repo)
    untracked = [item.decode("utf-8", "surrogateescape") for item in raw_untracked.split(b"\0") if item]
    entries: list[dict[str, object]] = []
    archive = stage / "untracked.tar.gz"
    with tarfile.open(archive, "w:gz", compresslevel=6) as tar:
        for relative in sorted(untracked):
            candidate = repo / Path(relative)
            if candidate.is_symlink():
                raise BackupError(f"untracked symlinks require an explicit recovery policy: {relative}")
            source = candidate.resolve()
            if repo.resolve() not in source.parents or not source.is_file():
                raise BackupError(f"unsupported or escaping untracked path: {relative}")
            archive_name = relative.replace("\\", "/")
            info = tar.gettarinfo(str(source), arcname=archive_name)
            if not info.isfile():
                continue
            with source.open("rb") as handle:
                reader = _HashingReader(handle)
                tar.addfile(info, reader)
            if reader.bytes_read != info.size:
                raise BackupError(f"untracked file changed while being captured: {relative}")
            entries.append({"path": archive_name, "bytes": info.size, "sha256": reader.digest.hexdigest()})
    atomic_json(stage / "untracked-manifest.json", {"files": entries})
    components = {
        "source_head": head,
        "source_refs_sha256": captured["source_refs_sha256"],
        "status_sha256": file_sha256(stage / "status.porcelain-v2.z"),
        "working_tree_patch_sha256": file_sha256(stage / "working-tree.patch"),
        "index_patch_sha256": file_sha256(stage / "index.patch"),
        "untracked_files": entries,
    }
    components["content_sha256"] = hashlib.sha256(envelope.canonical_json_bytes(components)).hexdigest()
    return components


def validate_recovery_capture(repo: Path, state: Path, captured: dict[str, object],
                              captured_fingerprint: dict[str, object]) -> bool:
    """Optimistically prove that paths *and bytes* were stable during capture."""

    before = source_snapshot(repo, min_tracked=1)
    with tempfile.TemporaryDirectory(prefix="capture-check-", dir=state) as temp:
        validation = write_recovery_artifacts(repo, Path(temp), before)
    after = source_snapshot(repo, min_tracked=1)
    return bool(
        captured["head_sha"] == before["head_sha"] == after["head_sha"]
        and captured["source_refs_sha256"] == before["source_refs_sha256"] == after["source_refs_sha256"]
        and captured["status_sha256"] == before["status_sha256"] == after["status_sha256"]
        and captured_fingerprint["content_sha256"] == validation["content_sha256"]
    )


def aws(*args: str, cwd: Path | None = None, profile: str | None = None) -> str:
    command = ["aws", *args]
    if profile:
        command.extend(["--profile", profile])
    return output(command, cwd=cwd or DEFAULT_REPO)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_sha256_digest(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.digest()


def s3_head(repo: Path, bucket: str, region: str, key: str, version_id: str | None = None,
            profile: str | None = None) -> dict[str, object]:
    args = [
        "s3api", "head-object", "--bucket", bucket, "--key", key,
        "--checksum-mode", "ENABLED", "--region", region, "--output", "json",
    ]
    if version_id:
        args.extend(["--version-id", version_id])
    return json.loads(aws(*args, cwd=repo, profile=profile))


def s3_checksum(repo: Path, bucket: str, region: str, key: str, version_id: str | None = None,
                profile: str | None = None) -> str | None:
    return s3_head(repo, bucket, region, key, version_id, profile).get("ChecksumSHA256")


def load_last_success(state: Path) -> dict[str, object] | None:
    path = state / "last_success.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def create_bundle(repo: Path, state: Path, stage: Path, captured_head: str, captured_refs: dict[str, str],
                  previous: dict[str, object] | None = None,
                  bootstrap_bundle: Path | None = None) -> dict[str, object]:
    previous_head = str(previous.get("source_head_sha", "")) if previous else ""
    previous_run = str(previous.get("run_id", "")) if previous else ""
    previous_refs_value = previous.get("source_refs") if previous else None
    previous_refs = previous_refs_value if isinstance(previous_refs_value, dict) else {}
    captured_tips = {str(value) for value in captured_refs.values()} | {captured_head}
    previous_tips = {str(value) for value in previous_refs.values()}
    if previous_head:
        previous_tips.add(previous_head)
    if previous_refs == captured_refs and previous_head == captured_head:
        return {
            "type": "reference", "base_source_head": previous_head, "base_run_id": previous_run,
            "source_refs_sha256": hashlib.sha256(envelope.canonical_json_bytes(captured_refs)).hexdigest(),
        }

    incremental = bool(previous_refs)
    if incremental:
        for sha in previous_tips:
            if run(["git", "cat-file", "-e", f"{sha}^{{object}}"], cwd=repo, check=False).returncode != 0:
                incremental = False
                break
    if incremental:
        new_objects = output([
            "git", "rev-list", "--objects", *sorted(captured_tips),
            "--not", *sorted(previous_tips),
        ], cwd=repo)
        if not new_objects:
            return {
                "type": "ref-update", "base_source_head": previous_head, "base_run_id": previous_run,
                "source_refs_sha256": hashlib.sha256(envelope.canonical_json_bytes(captured_refs)).hexdigest(),
            }
    bundle_type = "incremental" if incremental else "full"
    bundle = stage / f"repository-{bundle_type}.bundle"
    head_anchor: str | None = None
    if bundle_type == "full" and bootstrap_bundle is not None:
        bootstrap_bundle = bootstrap_bundle.resolve()
        if not bootstrap_bundle.is_file():
            raise BackupError(f"bootstrap bundle does not exist: {bootstrap_bundle}")
        log("Git bundle: validating and reusing the interrupted run's local full archive")
        try:
            os.link(bootstrap_bundle, bundle)
        except OSError:
            shutil.copyfile(bootstrap_bundle, bundle)
    else:
        log(f"Git bundle: creating {bundle_type} recovery archive")
        with tempfile.TemporaryDirectory(prefix="bundle-mirror-", dir=state) as tmp:
            mirror = Path(tmp) / "source.git"
            run(["git", "clone", "--mirror", "--shared", str(repo), str(mirror)], cwd=repo)
            mirror_refs = git(mirror, "for-each-ref", "--format=%(refname)").splitlines()
            for ref in mirror_refs:
                if ref not in captured_refs:
                    run(["git", "update-ref", "-d", ref], cwd=mirror)
            for ref, sha in captured_refs.items():
                run(["git", "update-ref", ref, sha], cwd=mirror)
            head_anchor = f"refs/brain-resilience-internal/source-head-{captured_head[:12]}"
            while head_anchor in captured_refs:
                head_anchor += "-capture"
            run(["git", "update-ref", head_anchor, captured_head], cwd=mirror)
            bundle_git = [
                "git", "-c", "pack.threads=1", "-c", "pack.window=0", "-c", "pack.depth=0",
                "bundle", "create", str(bundle),
            ]
            if incremental:
                exclusions = [f"^{sha}" for sha in sorted(previous_tips)]
                run([*bundle_git, "--all", *exclusions], cwd=mirror)
            else:
                run([*bundle_git, "--all"], cwd=mirror)
    verified = run(["git", "bundle", "verify", str(bundle)], cwd=repo)
    heads = output(["git", "bundle", "list-heads", str(bundle)], cwd=repo)
    listed: dict[str, str] = {}
    for line in heads.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            listed[parts[1]] = parts[0]
    mismatched: dict[str, tuple[str, str | None]] = {}
    previous_tips_list = sorted(previous_tips)
    for ref, sha in captured_refs.items():
        listed_sha = listed.get(ref)
        if listed_sha == sha:
            continue
        if listed_sha is not None or bundle_type == "full":
            mismatched[ref] = (sha, listed_sha)
            continue
        required = output(["git", "rev-list", "--objects", sha, "--not", *previous_tips_list], cwd=repo)
        if required:
            mismatched[ref] = (sha, None)
    if head_anchor is not None and bundle_type == "full" and listed.get(head_anchor) != captured_head:
        mismatched[head_anchor] = (captured_head, listed.get(head_anchor))
    elif head_anchor is not None and bundle_type == "incremental":
        required_head = output([
            "git", "rev-list", "--objects", captured_head, "--not", *previous_tips_list,
        ], cwd=repo)
        if required_head and listed.get(head_anchor) != captured_head:
            mismatched[head_anchor] = (captured_head, listed.get(head_anchor))
    elif bundle_type == "full" and captured_head not in listed.values():
        mismatched["HEAD"] = (captured_head, None)
    if mismatched:
        raise BackupError(f"bundle ref map does not match the captured source: {list(mismatched)[:5]}")
    return {
        "type": bundle_type,
        "file": bundle.name,
        "source_head": captured_head,
        "source_refs_sha256": hashlib.sha256(envelope.canonical_json_bytes(captured_refs)).hexdigest(),
        "source_ref_count": len(captured_refs),
        "base_source_head": previous_head or None,
        "base_run_id": previous_run or None,
        "bytes": bundle.stat().st_size,
        "verify": ((verified.stdout or "") + (verified.stderr or "")).strip()[-2000:],
    }


def put_verified_object(repo: Path, bucket: str, region: str, path: Path, key: str,
                        profile: str | None = None, expected_lock_mode: str = "GOVERNANCE",
                        minimum_retain_days: int = 29) -> dict[str, object]:
    if path.stat().st_size >= 5_000_000_000:
        raise BackupError(f"single-object S3 upload limit exceeded: {path.name}")
    digest = file_sha256_digest(path)
    expected_b64 = base64.b64encode(digest).decode("ascii")
    command = [
        "aws", "s3api", "put-object", "--bucket", bucket, "--key", key,
        "--body", str(path), "--checksum-sha256", expected_b64, "--region", region,
        "--output", "json",
    ]
    if profile:
        command.extend(["--profile", profile])
    assert_external(f"external:aws/s3/{bucket}")
    result = run(command, cwd=repo)
    assert isinstance(result.stdout, str)
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BackupError("S3 put-object returned invalid JSON") from exc
    version_id = response.get("VersionId")
    if not isinstance(version_id, str) or not version_id:
        raise BackupError(f"S3 did not return an immutable version id for {key}")
    head = s3_head(repo, bucket, region, key, version_id, profile)
    retain_value = head.get("ObjectLockRetainUntilDate")
    try:
        retain_until = dt.datetime.fromisoformat(str(retain_value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise BackupError(f"S3 object has no valid retention date: {key}") from exc
    if (head.get("ChecksumSHA256") != expected_b64 or head.get("ContentLength") != path.stat().st_size
            or head.get("ObjectLockMode") != expected_lock_mode
            or retain_until < utc_now() + dt.timedelta(days=minimum_retain_days)):
        raise BackupError(f"S3 object verification failed: {key}")
    return {
        "key": key,
        "bucket": bucket,
        "region": region,
        "profile_hint": profile or "default",
        "version_id": version_id,
        "bytes": path.stat().st_size,
        "sha256": digest.hex(),
        "checksum_sha256_b64": expected_b64,
        "object_lock_mode": head.get("ObjectLockMode"),
        "retain_until_utc": retain_until.isoformat(),
    }


def put_replicated_object(repo: Path, path: Path, key: str, *,
                          bucket: str, region: str, secondary_bucket: str,
                          secondary_region: str, secondary_profile: str) -> list[dict[str, object]]:
    return [
        put_verified_object(repo, bucket, region, path, key),
        put_verified_object(
            repo, secondary_bucket, secondary_region, path, key, secondary_profile,
            expected_lock_mode="COMPLIANCE", minimum_retain_days=89,
        ),
    ]


def verify_object_descriptor(repo: Path, descriptor: dict[str, object]) -> None:
    hint = str(descriptor.get("profile_hint", "default"))
    profile = None if hint == "default" else hint
    head = s3_head(
        repo, str(descriptor["bucket"]), str(descriptor["region"]), str(descriptor["key"]),
        str(descriptor["version_id"]), profile,
    )
    actual = head.get("ChecksumSHA256")
    expected = descriptor.get("checksum_sha256_b64")
    if (actual != expected or head.get("ContentLength") != descriptor.get("bytes")
            or head.get("ObjectLockMode") != descriptor.get("object_lock_mode")
            or str(head.get("ObjectLockRetainUntilDate")) != str(descriptor.get("retain_until_utc"))):
        raise BackupError(f"versioned S3 artifact no longer matches its signed descriptor: {descriptor.get('key')}")


def verify_manifest_artifacts(repo: Path, manifest: dict[str, object],
                              names: Iterable[str] | None = None) -> None:
    uploaded = manifest.get("uploaded_artifacts")
    if not isinstance(uploaded, dict) or not uploaded:
        raise BackupError("signed manifest has no recovery artifacts")
    if names is not None:
        requested = list(names)
        missing = [name for name in requested if name not in uploaded]
        if missing:
            raise BackupError("signed manifest is missing required artifacts: " + ", ".join(missing))
        uploaded = {name: uploaded[name] for name in requested}
    expected_keys = set(PINNED_ENCRYPTION_KEYS)
    for name, artifact in uploaded.items():
        if not isinstance(artifact, dict):
            raise BackupError(f"artifact descriptor is malformed: {name}")
        encryption = artifact.get("encryption")
        storage = artifact.get("storage")
        if not isinstance(encryption, dict) or not isinstance(storage, dict):
            raise BackupError(f"artifact encryption/storage metadata is missing: {name}")
        wrappings = encryption.get("key_wrappings")
        wrapped_by = {
            str(item.get("kms_key_id")) for item in wrappings or [] if isinstance(item, dict)
        }
        if wrapped_by != expected_keys:
            raise BackupError(f"artifact is not recoverable through both AWS accounts: {name}")
        chunks = storage.get("chunks")
        if not isinstance(chunks, list) or not chunks:
            raise BackupError(f"artifact has no ciphertext chunks: {name}")
        total = 0
        expected_offset = 0
        for index, chunk in enumerate(chunks):
            if not isinstance(chunk, dict) or chunk.get("index") != index or chunk.get("offset") != expected_offset:
                raise BackupError(f"artifact chunk sequence is invalid: {name}")
            replicas = chunk.get("replicas")
            if not isinstance(replicas, list) or {item.get("bucket") for item in replicas if isinstance(item, dict)} != {
                DEFAULT_BUCKET, DEFAULT_SECONDARY_BUCKET,
            }:
                raise BackupError(f"artifact chunk lacks two independent replicas: {name}")
            for replica in replicas:
                if not isinstance(replica, dict):
                    raise BackupError(f"artifact replica is malformed: {name}")
                verify_object_descriptor(repo, replica)
            chunk_bytes = int(chunk.get("bytes", -1))
            total += chunk_bytes
            expected_offset += chunk_bytes
        if total != encryption.get("ciphertext_bytes") or total != storage.get("ciphertext_bytes"):
            raise BackupError(f"artifact ciphertext length is inconsistent: {name}")


def iter_ciphertext_chunks(path: Path, chunk_directory: Path) -> Iterable[tuple[int, int, Path]]:
    """Yield bounded files so repository growth never crosses PutObject's 5 GB limit."""

    if path.stat().st_size <= UPLOAD_CHUNK_BYTES:
        yield 0, 0, path
        return
    chunk_directory.mkdir(parents=True, exist_ok=True)
    offset = 0
    with path.open("rb") as incoming:
        index = 0
        while offset < path.stat().st_size:
            part = chunk_directory / f"part{index:04d}"
            remaining = min(UPLOAD_CHUNK_BYTES, path.stat().st_size - offset)
            try:
                with part.open("wb") as outgoing:
                    left = remaining
                    while left:
                        block = incoming.read(min(4 * 1024 * 1024, left))
                        if not block:
                            raise BackupError("ciphertext ended while creating upload chunks")
                        outgoing.write(block)
                        left -= len(block)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                yield index, offset, part
            finally:
                part.unlink(missing_ok=True)
            offset += remaining
            index += 1


def upload_snapshot(repo: Path, bucket: str, region: str, stage: Path, run_id: str,
                    source_head: str, kms_key_id: str, secondary_bucket: str,
                    secondary_region: str, secondary_profile: str,
                    secondary_kms_key_id: str) -> dict[str, dict[str, object]]:
    uploaded: dict[str, dict[str, object]] = {}
    paths = sorted(item for item in stage.rglob("*") if item.is_file() and ".ciphertext" not in item.parts)
    for path in paths:
        relative = path.relative_to(stage).as_posix()
        if relative == "manifest.json":
            continue
        encrypted = stage / ".ciphertext" / f"{relative}.enc"
        log(f"encrypting {relative} ({path.stat().st_size} bytes) before upload")
        try:
            encryption = envelope.encrypt_artifact(
                path, encrypted, cwd=repo, key_id=kms_key_id, region=region,
                run_id=run_id, source_head=source_head, artifact_name=relative,
                secondary_key_id=secondary_kms_key_id,
                secondary_region=secondary_region, secondary_profile=secondary_profile,
            )
            chunks: list[dict[str, object]] = []
            chunked = encrypted.stat().st_size > UPLOAD_CHUNK_BYTES
            for index, offset, chunk in iter_ciphertext_chunks(
                encrypted, stage / ".ciphertext" / f"{relative}.chunks",
            ):
                suffix = f".part{index:04d}" if chunked else ""
                key = f"snapshots/{run_id}/{relative}.enc{suffix}"
                primary_object = put_verified_object(repo, bucket, region, chunk, key)
                secondary_object = put_verified_object(
                    repo, secondary_bucket, secondary_region, chunk, key, secondary_profile,
                    expected_lock_mode="COMPLIANCE", minimum_retain_days=89,
                )
                chunks.append({
                    "index": index,
                    "offset": offset,
                    "bytes": chunk.stat().st_size,
                    "replicas": [primary_object, secondary_object],
                })
        except envelope.EnvelopeError as exc:
            raise BackupError(str(exc)) from exc
        finally:
            encrypted.unlink(missing_ok=True)
        uploaded[relative] = {
            "storage": {
                "format": "chunked" if chunked else "single",
                "ciphertext_bytes": encryption["ciphertext_bytes"],
                "ciphertext_sha256": encryption["ciphertext_sha256"],
                "chunks": chunks,
            },
            "encryption": encryption,
        }
    return uploaded


def load_s3_json(repo: Path, bucket: str, region: str, key: str,
                 version_id: str | None = None, profile: str | None = None) -> tuple[dict[str, object], bytes]:
    with tempfile.TemporaryDirectory(prefix="brain-s3-json-") as temp:
        target = Path(temp) / "object.json"
        argv = [
            "aws", "s3api", "get-object", "--bucket", bucket, "--key", key,
            "--checksum-mode", "ENABLED", "--region", region,
        ]
        if version_id:
            argv.extend(["--version-id", version_id])
        if profile:
            argv.extend(["--profile", profile])
        argv.append(str(target))
        run(argv, cwd=repo)
        raw = target.read_bytes()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BackupError(f"S3 JSON object is invalid: {key}") from exc
    if not isinstance(value, dict):
        raise BackupError(f"S3 JSON object has an unexpected shape: {key}")
    return value, raw


def descriptor_profile(descriptor: Mapping[str, object],
                       profile_overrides_by_bucket: Mapping[str, str] | None = None) -> str | None:
    """Resolve an S3 profile, allowing clean-machine aliases to override recorded hints."""

    bucket = str(descriptor.get("bucket", ""))
    if profile_overrides_by_bucket is not None and bucket in profile_overrides_by_bucket:
        return profile_overrides_by_bucket[bucket] or None
    hint = str(descriptor.get("profile_hint", "default"))
    return None if hint == "default" else hint


def verify_completion_receipt(
    repo: Path, bucket: str, region: str, receipt_object: dict[str, object],
    profile_overrides_by_bucket: Mapping[str, str] | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    if receipt_object.get("bucket") not in {None, bucket} or receipt_object.get("region") not in {None, region}:
        raise BackupError("completion receipt descriptor is from a different vault")
    profile = descriptor_profile(receipt_object, profile_overrides_by_bucket)
    receipt, receipt_raw = load_s3_json(
        repo, bucket, region, str(receipt_object["key"]), str(receipt_object["version_id"]), profile,
    )
    receipt_sha256 = hashlib.sha256(receipt_raw).hexdigest()
    if receipt_object.get("sha256") and receipt_sha256 != receipt_object.get("sha256"):
        raise BackupError("completion receipt hash does not match its anchor")
    payload = receipt.get("payload")
    authentication = receipt.get("authentication")
    if not isinstance(payload, dict) or not isinstance(authentication, dict):
        raise BackupError("completion receipt is malformed")
    signatures = authentication.get("signatures")
    if not isinstance(signatures, list):
        raise BackupError("completion receipt has no signatures")
    seen: set[str] = set()
    message = envelope.canonical_json_bytes(payload)
    for signature in signatures:
        if not isinstance(signature, dict):
            raise BackupError("completion receipt signature is malformed")
        key_id = str(signature.get("kms_key_id", ""))
        public_key = PINNED_SIGNERS.get(key_id)
        if public_key is None or signature.get("algorithm") != "RSASSA_PSS_SHA_256":
            raise BackupError(f"completion receipt used an unpinned signer: {key_id}")
        if envelope.public_key_fingerprint(public_key) != PINNED_SIGNER_SHA256[key_id]:
            raise BackupError(f"pinned receipt public key file was replaced: {key_id}")
        try:
            valid = envelope.verify_signature(public_key, message, str(signature["signature_b64"]))
        except (KeyError, ValueError, envelope.EnvelopeError) as exc:
            raise BackupError(f"completion receipt signature failed: {exc}") from exc
        if not valid:
            raise BackupError(f"completion receipt signature is invalid: {key_id}")
        seen.add(key_id)
    if seen != set(PINNED_SIGNERS):
        raise BackupError("completion receipt does not contain every pinned account signature")
    manifest_replicas = payload.get("manifest_replicas")
    if not isinstance(manifest_replicas, list) or len(manifest_replicas) < 2:
        raise BackupError("completion receipt has no independent manifest replicas")
    manifest_object = next(
        (item for item in manifest_replicas if isinstance(item, dict) and item.get("bucket") == bucket),
        manifest_replicas[0],
    )
    if not isinstance(manifest_object, dict):
        raise BackupError("completion receipt manifest replica is malformed")
    manifest, manifest_raw = load_s3_json(
        repo, str(manifest_object["bucket"]), str(manifest_object["region"]),
        str(manifest_object["key"]), str(manifest_object["version_id"]),
        descriptor_profile(manifest_object, profile_overrides_by_bucket),
    )
    if (hashlib.sha256(manifest_raw).hexdigest() != manifest_object.get("sha256")
            or manifest_object.get("sha256") != payload.get("manifest_sha256")):
        raise BackupError("snapshot manifest hash does not match the authenticated receipt")
    if manifest.get("run_id") != payload.get("run_id") or manifest.get("source_head_sha") != payload.get("source_head_sha"):
        raise BackupError("completion receipt and snapshot manifest disagree")
    receipt["_verified_sha256"] = receipt_sha256
    return receipt, manifest


def list_receipt_versions(repo: Path, bucket: str, region: str,
                          profile: str | None = None) -> list[dict[str, object]]:
    """List every immutable receipt version, including versions hidden by delete markers."""

    versions: list[dict[str, object]] = []
    key_marker: str | None = None
    version_marker: str | None = None
    seen_markers: set[tuple[str | None, str | None]] = set()
    while True:
        args = [
            "s3api", "list-object-versions", "--bucket", bucket, "--prefix", "receipts/",
            "--region", region, "--output", "json", "--no-paginate",
        ]
        if key_marker:
            args.extend(["--key-marker", key_marker])
        if version_marker:
            args.extend(["--version-id-marker", version_marker])
        payload = json.loads(aws(*args, cwd=repo, profile=profile) or "{}")
        page = payload.get("Versions", [])
        if not isinstance(page, list):
            raise BackupError("S3 receipt version listing has an unexpected shape")
        versions.extend(item for item in page if isinstance(item, dict))
        if payload.get("IsTruncated") is not True:
            break
        next_marker = payload.get("NextKeyMarker")
        next_version = payload.get("NextVersionIdMarker")
        marker = (
            str(next_marker) if next_marker is not None else None,
            str(next_version) if next_version is not None else None,
        )
        if marker == (None, None) or marker in seen_markers:
            raise BackupError("S3 receipt version pagination did not advance")
        seen_markers.add(marker)
        key_marker, version_marker = marker
    return versions


def discover_receipt_history(
    repo: Path, bucket: str, region: str, profile: str | None = None,
    profile_overrides_by_bucket: Mapping[str, str] | None = None,
) -> list[tuple[dict[str, object], dict[str, object]]]:
    candidates = list_receipt_versions(repo, bucket, region, profile)
    valid: dict[str, tuple[dict[str, object], dict[str, object]]] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict) or not str(candidate.get("Key", "")).endswith(".json"):
            continue
        descriptor = {
            "key": candidate.get("Key"),
            "version_id": candidate.get("VersionId"),
            "bucket": bucket,
            "region": region,
            "profile_hint": profile or "default",
        }
        try:
            receipt, manifest = verify_completion_receipt(
                repo, bucket, region, descriptor, profile_overrides_by_bucket,
            )
        except BackupError:
            continue
        receipt["_verified_descriptor"] = descriptor
        valid[str(receipt["_verified_sha256"])] = (receipt, manifest)
    if not valid:
        raise BackupError("vault contains no valid dual-signed completion receipt")

    referenced: set[str] = set()
    roots = 0
    for receipt, _ in valid.values():
        receipt_payload = receipt.get("payload")
        if not isinstance(receipt_payload, dict):
            raise BackupError("valid receipt lost its payload")
        previous = receipt_payload.get("previous_completion_anchor")
        if previous is None:
            roots += 1
            continue
        if not isinstance(previous, dict):
            raise BackupError("receipt chain contains a malformed previous anchor")
        previous_sha = str(previous.get("sha256", ""))
        previous_entry = valid.get(previous_sha)
        if previous_entry is None:
            raise BackupError("receipt chain is missing a signed predecessor")
        previous_payload = previous_entry[0].get("payload")
        if not isinstance(previous_payload, dict) or previous_payload.get("run_id") != previous.get("run_id"):
            raise BackupError("receipt chain predecessor identity does not match")
        referenced.add(previous_sha)
    tip_items = [(sha, item) for sha, item in valid.items() if sha not in referenced]
    if roots != 1 or len(tip_items) != 1:
        raise BackupError("receipt history is forked or has multiple roots")
    tip_sha, tip = tip_items[0]
    visited: set[str] = set()
    cursor: str | None = tip_sha
    while cursor is not None:
        if cursor in visited:
            raise BackupError("receipt history contains a cycle")
        visited.add(cursor)
        receipt_payload = valid[cursor][0].get("payload")
        if not isinstance(receipt_payload, dict):
            raise BackupError("receipt history payload is malformed")
        previous = receipt_payload.get("previous_completion_anchor")
        cursor = str(previous.get("sha256")) if isinstance(previous, dict) else None
    if visited != set(valid):
        raise BackupError("vault contains signed receipts outside the accepted chain")
    ordered: list[tuple[dict[str, object], dict[str, object]]] = []
    cursor = tip_sha
    while cursor is not None:
        ordered.append(valid[cursor])
        receipt_payload = valid[cursor][0].get("payload")
        previous = receipt_payload.get("previous_completion_anchor") if isinstance(receipt_payload, dict) else None
        cursor = str(previous.get("sha256")) if isinstance(previous, dict) else None
    ordered.reverse()
    return ordered


def discover_receipt_tip(
    repo: Path, bucket: str, region: str, profile: str | None = None,
    profile_overrides_by_bucket: Mapping[str, str] | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    return discover_receipt_history(
        repo, bucket, region, profile, profile_overrides_by_bucket,
    )[-1]


def _optional_receipt_tip(repo: Path, bucket: str, region: str,
                          profile: str | None = None) -> tuple[dict[str, object], dict[str, object]] | None:
    candidates = [
        item for item in list_receipt_versions(repo, bucket, region, profile)
        if str(item.get("Key", "")).startswith("receipts/") and str(item.get("Key", "")).endswith(".json")
    ]
    if not candidates:
        return None
    return discover_receipt_tip(repo, bucket, region, profile)


def verify_restore_dependencies(
    repo: Path, history: Sequence[tuple[dict[str, object], dict[str, object]]],
) -> None:
    """Verify exact immutable versions needed to reconstruct the latest signed state."""

    full_indexes = [
        index for index, (_, manifest) in enumerate(history)
        if isinstance(manifest.get("bundle"), dict) and manifest["bundle"].get("type") == "full"
    ]
    if not full_indexes:
        raise BackupError("signed receipt history contains no full Git bundle")
    for _, manifest in history[full_indexes[-1]:]:
        bundle = manifest.get("bundle")
        if not isinstance(bundle, dict):
            raise BackupError("signed manifest has no bundle descriptor")
        if bundle.get("type") in {"full", "incremental"}:
            filename = str(bundle.get("file", ""))
            if not filename:
                raise BackupError("signed bundle descriptor has no filename")
            verify_manifest_artifacts(repo, manifest, [filename])
        elif bundle.get("type") not in {"reference", "ref-update"}:
            raise BackupError(f"signed manifest has unknown bundle type: {bundle.get('type')}")
    verify_manifest_artifacts(repo, history[-1][1])


def _replicate_receipt(repo: Path, receipt: dict[str, object], *, target_bucket: str,
                       target_region: str, target_profile: str | None) -> dict[str, object]:
    descriptor = receipt.get("_verified_descriptor")
    payload = receipt.get("payload")
    if not isinstance(descriptor, dict) or not isinstance(payload, dict):
        raise BackupError("cannot reconcile a receipt without verified provenance")
    hint = str(descriptor.get("profile_hint", "default"))
    _, raw = load_s3_json(
        repo, str(descriptor["bucket"]), str(descriptor["region"]), str(descriptor["key"]),
        str(descriptor["version_id"]), None if hint == "default" else hint,
    )
    with tempfile.TemporaryDirectory(prefix="brain-receipt-reconcile-") as temp:
        path = Path(temp) / "receipt.json"
        path.write_bytes(raw)
        return put_verified_object(
            repo, target_bucket, target_region, path, str(descriptor["key"]), target_profile,
            expected_lock_mode="COMPLIANCE" if target_profile else "GOVERNANCE",
            minimum_retain_days=89 if target_profile else 29,
        )


def load_verified_previous(repo: Path, *, bucket: str, region: str,
                           secondary_bucket: str, secondary_region: str,
                           secondary_profile: str) -> dict[str, object] | None:
    """Use the dual-vault signed chain as authority; local state is only a cache."""

    primary = _optional_receipt_tip(repo, bucket, region)
    secondary = _optional_receipt_tip(repo, secondary_bucket, secondary_region, secondary_profile)
    if primary is None and secondary is None:
        return None
    if primary is None:
        assert secondary is not None
        prior = secondary[0].get("payload", {}).get("previous_completion_anchor")
        if prior is not None:
            raise BackupError("primary vault is missing more than one signed receipt; manual reconciliation required")
        _replicate_receipt(repo, secondary[0], target_bucket=bucket, target_region=region, target_profile=None)
    elif secondary is None:
        prior = primary[0].get("payload", {}).get("previous_completion_anchor")
        if prior is not None:
            raise BackupError("secondary vault is missing more than one signed receipt; manual reconciliation required")
        _replicate_receipt(
            repo, primary[0], target_bucket=secondary_bucket, target_region=secondary_region,
            target_profile=secondary_profile,
        )
    else:
        primary_sha = str(primary[0].get("_verified_sha256"))
        secondary_sha = str(secondary[0].get("_verified_sha256"))
        if primary_sha != secondary_sha:
            primary_previous = primary[0].get("payload", {}).get("previous_completion_anchor")
            secondary_previous = secondary[0].get("payload", {}).get("previous_completion_anchor")
            if isinstance(primary_previous, dict) and primary_previous.get("sha256") == secondary_sha:
                _replicate_receipt(
                    repo, primary[0], target_bucket=secondary_bucket, target_region=secondary_region,
                    target_profile=secondary_profile,
                )
            elif isinstance(secondary_previous, dict) and secondary_previous.get("sha256") == primary_sha:
                _replicate_receipt(repo, secondary[0], target_bucket=bucket, target_region=region, target_profile=None)
            else:
                raise BackupError("dual vault receipt tips diverged and neither extends the other")

    primary = discover_receipt_tip(repo, bucket, region)
    secondary = discover_receipt_tip(repo, secondary_bucket, secondary_region, secondary_profile)
    primary_sha = str(primary[0].get("_verified_sha256"))
    secondary_sha = str(secondary[0].get("_verified_sha256"))
    if primary_sha != secondary_sha or primary[1].get("run_id") != secondary[1].get("run_id"):
        raise BackupError("dual vaults do not agree on the signed receipt tip")
    if envelope.canonical_json_bytes(primary[1]) != envelope.canonical_json_bytes(secondary[1]):
        raise BackupError("dual vaults returned different signed manifests")
    primary_history = discover_receipt_history(repo, bucket, region)
    if primary_history[-1][0].get("_verified_sha256") != primary_sha:
        raise BackupError("receipt history changed while verifying restore dependencies")
    verify_restore_dependencies(repo, primary_history)
    primary_descriptor = primary[0].get("_verified_descriptor")
    secondary_descriptor = secondary[0].get("_verified_descriptor")
    if not isinstance(primary_descriptor, dict) or not isinstance(secondary_descriptor, dict):
        raise BackupError("verified receipt tip lacks versioned replicas")
    previous = dict(primary[1])
    previous["completion_receipt"] = {
        "run_id": primary[1].get("run_id"),
        "sha256": primary_sha,
        "replicas": [primary_descriptor, secondary_descriptor],
    }
    return previous


def verify_latest_receipt(repo: Path, bucket: str, region: str,
                          profile: str | None = None) -> tuple[dict[str, object], dict[str, object]]:
    pointer, _ = load_s3_json(repo, bucket, region, "manifests/latest.json", profile=profile)
    receipt_replicas = pointer.get("completion_receipt_replicas")
    if not isinstance(receipt_replicas, list):
        raise BackupError("latest pointer has no completion receipt")
    receipt_object = next(
        (item for item in receipt_replicas if isinstance(item, dict) and item.get("bucket") == bucket), None,
    )
    if not isinstance(receipt_object, dict):
        raise BackupError("latest pointer has no receipt in this vault")
    pointer_receipt, pointer_manifest = verify_completion_receipt(repo, bucket, region, receipt_object)
    tip_receipt, tip_manifest = discover_receipt_tip(repo, bucket, region, profile)
    if (pointer_receipt.get("_verified_sha256") != tip_receipt.get("_verified_sha256")
            or pointer_manifest.get("run_id") != tip_manifest.get("run_id")):
        raise BackupError("latest pointer is a rollback and does not name the signed chain tip")
    return pointer_receipt, pointer_manifest


def verify_controls(source_repo: Path, clean_repo: Path, bucket: str, region: str, remote: str,
                    expected_clean_sha: str | None = None, kms_key_id: str = DEFAULT_KMS_KEY,
                    signing_key_id: str = DEFAULT_SIGNING_KEY,
                    secondary_bucket: str = DEFAULT_SECONDARY_BUCKET,
                    secondary_region: str = DEFAULT_SECONDARY_REGION,
                    secondary_profile: str = DEFAULT_SECONDARY_PROFILE,
                    secondary_kms_key_id: str = DEFAULT_SECONDARY_KMS_KEY,
                    secondary_signing_key_id: str = DEFAULT_SECONDARY_SIGNING_KEY) -> dict[str, object]:
    versioning = json.loads(aws("s3api", "get-bucket-versioning", "--bucket", bucket, "--region", region, "--output", "json", cwd=source_repo) or "{}")
    public = json.loads(aws("s3api", "get-public-access-block", "--bucket", bucket, "--region", region, "--output", "json", cwd=source_repo))
    lock = json.loads(aws("s3api", "get-object-lock-configuration", "--bucket", bucket, "--region", region, "--output", "json", cwd=source_repo))
    encryption = json.loads(aws("s3api", "get-bucket-encryption", "--bucket", bucket, "--region", region, "--output", "json", cwd=source_repo))
    encryption_key = json.loads(aws("kms", "describe-key", "--key-id", kms_key_id, "--region", region, "--output", "json", cwd=source_repo))
    signing_key = json.loads(aws("kms", "describe-key", "--key-id", signing_key_id, "--region", region, "--output", "json", cwd=source_repo))
    secondary_versioning = json.loads(aws("s3api", "get-bucket-versioning", "--bucket", secondary_bucket, "--region", secondary_region, "--output", "json", cwd=source_repo, profile=secondary_profile) or "{}")
    secondary_public = json.loads(aws("s3api", "get-public-access-block", "--bucket", secondary_bucket, "--region", secondary_region, "--output", "json", cwd=source_repo, profile=secondary_profile))
    secondary_lock = json.loads(aws("s3api", "get-object-lock-configuration", "--bucket", secondary_bucket, "--region", secondary_region, "--output", "json", cwd=source_repo, profile=secondary_profile))
    secondary_encryption = json.loads(aws("s3api", "get-bucket-encryption", "--bucket", secondary_bucket, "--region", secondary_region, "--output", "json", cwd=source_repo, profile=secondary_profile))
    secondary_encryption_key = json.loads(aws("kms", "describe-key", "--key-id", secondary_kms_key_id, "--region", secondary_region, "--output", "json", cwd=source_repo, profile=secondary_profile))
    secondary_signing_key = json.loads(aws("kms", "describe-key", "--key-id", secondary_signing_key_id, "--region", secondary_region, "--output", "json", cwd=source_repo, profile=secondary_profile))
    remote_sha = remote_tip(clean_repo, remote)
    block = public.get("PublicAccessBlockConfiguration", {})
    secondary_block = secondary_public.get("PublicAccessBlockConfiguration", {})
    secondary_retention = secondary_lock.get("ObjectLockConfiguration", {}).get("Rule", {}).get("DefaultRetention", {})
    checks = {
        "github_remote_sha": remote_sha,
        "s3_versioning_enabled": versioning.get("Status") == "Enabled",
        "s3_object_lock_enabled": lock.get("ObjectLockConfiguration", {}).get("ObjectLockEnabled") == "Enabled",
        "s3_public_access_blocked": all(block.get(k) is True for k in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")),
        "s3_default_encryption": bool(encryption.get("ServerSideEncryptionConfiguration", {}).get("Rules")),
        "client_encryption_key_enabled": encryption_key.get("KeyMetadata", {}).get("KeyState") == "Enabled",
        "client_encryption_key_usage": encryption_key.get("KeyMetadata", {}).get("KeyUsage") == "ENCRYPT_DECRYPT",
        "receipt_signing_key_enabled": signing_key.get("KeyMetadata", {}).get("KeyState") == "Enabled",
        "receipt_signing_key_usage": signing_key.get("KeyMetadata", {}).get("KeyUsage") == "SIGN_VERIFY",
        "secondary_s3_versioning_enabled": secondary_versioning.get("Status") == "Enabled",
        "secondary_s3_object_lock_compliance": (
            secondary_lock.get("ObjectLockConfiguration", {}).get("ObjectLockEnabled") == "Enabled"
            and secondary_retention.get("Mode") == "COMPLIANCE"
            and int(secondary_retention.get("Days", 0)) >= 90
        ),
        "secondary_s3_public_access_blocked": all(secondary_block.get(k) is True for k in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")),
        "secondary_s3_default_encryption": bool(secondary_encryption.get("ServerSideEncryptionConfiguration", {}).get("Rules")),
        "secondary_client_encryption_key_enabled": secondary_encryption_key.get("KeyMetadata", {}).get("KeyState") == "Enabled",
        "secondary_client_encryption_key_usage": secondary_encryption_key.get("KeyMetadata", {}).get("KeyUsage") == "ENCRYPT_DECRYPT",
        "secondary_receipt_signing_key_enabled": secondary_signing_key.get("KeyMetadata", {}).get("KeyState") == "Enabled",
        "secondary_receipt_signing_key_usage": secondary_signing_key.get("KeyMetadata", {}).get("KeyUsage") == "SIGN_VERIFY",
    }
    if expected_clean_sha:
        checks["github_matches_expected"] = remote_sha == expected_clean_sha
    if not all(value for key, value in checks.items() if key != "github_remote_sha"):
        raise BackupError(f"backup control verification failed: {checks}")
    if expected_clean_sha and remote_sha != expected_clean_sha:
        raise BackupError(f"GitHub clean snapshot verification failed: {checks}")
    return checks


def perform_backup(args: argparse.Namespace) -> dict[str, object]:
    require_compute_window()
    repo = args.repo.resolve()
    state = args.state.resolve()
    mesh = repo / "12_cognitive_architecture" / "agent_mesh" / "agent_mesh.py"
    remote_repo = "<github-user>/ai-brain-resilience-backup"
    with MeshLease(
        mesh, repo, args.bucket, remote_repo, enabled=not args.no_mesh, state=state,
        secondary_bucket=args.secondary_bucket,
    ), ProcessLock(state / "run.lock"):
        clean_repo = state / "github-snapshot"
        if args.reuse_clean:
            report_path = clean_repo / "SANITIZATION_REPORT.json"
            if not report_path.is_file() or not (clean_repo / ".git").is_dir():
                raise BackupError("no verified sanitized snapshot is available to reuse")
            clean_report = json.loads(report_path.read_text(encoding="utf-8"))
            current = source_snapshot(repo, min_tracked=args.min_tracked)
            current_content = sanitized_snapshot.source_content_fingerprint(repo)
            clean_dirty = git(clean_repo, "status", "--porcelain")
            github_sha = git(clean_repo, "rev-parse", "HEAD")
            if (clean_dirty or clean_report.get("source_changed_during_export") or
                    clean_report.get("source_head") != current["head_sha"] or
                    clean_report.get("source_status_sha256_after") != current["status_sha256"] or
                    clean_report.get("source_content_sha256_after") != current_content or
                    remote_tip(clean_repo, args.remote) != github_sha):
                raise BackupError("sanitized snapshot reuse gate failed; run a fresh full-tree export")
            log(f"reusing verified sanitized snapshot {github_sha[:12]}")
        else:
            log("building the fresh-history sanitized GitHub snapshot")
            try:
                github_sha, clean_report = sanitized_snapshot.update_repository(
                    repo, clean_repo, state / "github-snapshot-files.json",
                    repo / "12_cognitive_architecture" / "dreaming" / "scan_secrets.py",
                    before_push=lambda: assert_external(f"external:github/{remote_repo}"),
                )
            except sanitized_snapshot.SnapshotError as exc:
                raise BackupError(str(exc)) from exc

        previous = load_verified_previous(
            repo, bucket=args.bucket, region=args.region,
            secondary_bucket=args.secondary_bucket, secondary_region=args.secondary_region,
            secondary_profile=args.secondary_profile,
        )
        captured: dict[str, object] | None = None
        recovery_fingerprint: dict[str, object] | None = None
        stage: Path | None = None
        run_stamp = stamp()
        for attempt in range(1, 4):
            before = source_snapshot(repo, min_tracked=args.min_tracked)
            candidate = state / "staging" / f"{run_stamp}-try{attempt}"
            candidate_fingerprint = write_recovery_artifacts(repo, candidate, before)
            if validate_recovery_capture(repo, state, before, candidate_fingerprint):
                captured, recovery_fingerprint, stage = before, candidate_fingerprint, candidate
                break
            log(f"source paths or content changed during recovery capture attempt {attempt}; retrying")
        if captured is None or recovery_fingerprint is None or stage is None:
            raise BackupError("could not capture a stable source state after three attempts")

        log(f"captured source HEAD {str(captured['head_sha'])[:12]} with {captured['status_entries']} dirty entries")
        require_compute_window()
        captured_refs = captured.get("source_refs")
        if not isinstance(captured_refs, dict):
            raise BackupError("stable capture has no Git ref map")
        bundle = create_bundle(
            repo, state, stage, str(captured["head_sha"]), captured_refs, previous, args.bootstrap_bundle,
        )
        source_changed_after_capture = not validate_recovery_capture(repo, state, captured, recovery_fingerprint)
        previous_receipt = previous.get("completion_receipt") if previous else None
        manifest = {
            "schema_version": 2,
            "completed_at_utc": utc_now().isoformat(),
            "run_id": run_stamp,
            "source_repo": str(repo),
            "source_head_sha": captured["head_sha"],
            "source_head_tree": captured["head_tree"],
            "source_refs": captured_refs,
            "source_refs_sha256": captured["source_refs_sha256"],
            "github_sanitized_sha": github_sha,
            "github_sanitization": clean_report,
            "tracked_files": captured["tracked_files"],
            "untracked_files": captured["untracked_files"],
            "dirty_entries": captured["status_entries"],
            "status_sha256": captured["status_sha256"],
            "recovery_fingerprint": recovery_fingerprint,
            "source_changed_after_capture": source_changed_after_capture,
            "bundle": bundle,
            "previous_completion_receipt": previous_receipt,
            "layers": {
                "github": "fresh-history text-only snapshot with full-tree secret redaction",
                "s3_git": "client-encrypted, verified full or incremental Git bundle with exact private recovery history",
                "s3_recovery": "client-encrypted immutable artifacts with content fingerprints, version IDs, hashes, and authenticated completion receipt",
            },
        }
        manifest_path = stage / "manifest.json"
        atomic_json(manifest_path, manifest)
        require_compute_window()
        uploaded = upload_snapshot(
            repo, args.bucket, args.region, stage, run_stamp, str(captured["head_sha"]), args.kms_key,
            args.secondary_bucket, args.secondary_region, args.secondary_profile, args.secondary_kms_key,
        )
        manifest["uploaded_artifacts"] = uploaded
        immutable_encryption_keys = set(PINNED_ENCRYPTION_KEYS)
        for name, value in uploaded.items():
            encryption_value = value.get("encryption")
            artifact_keys = {
                str(wrapping.get("kms_key_id"))
                for wrapping in encryption_value.get("key_wrappings", [])
                if isinstance(wrapping, dict)
            } if isinstance(encryption_value, dict) else set()
            if artifact_keys != immutable_encryption_keys:
                raise BackupError(f"recovery artifact lacks both pinned KMS recipients: {name}")
        manifest["client_side_encryption"] = {
            "required_for_all_artifacts": True,
            "kms_key_recipients": sorted(immutable_encryption_keys),
            "algorithm": "AES-256-GCM",
        }
        checks = verify_controls(
            repo, clean_repo, args.bucket, args.region, args.remote,
            expected_clean_sha=github_sha, kms_key_id=args.kms_key, signing_key_id=args.signing_key,
            secondary_bucket=args.secondary_bucket, secondary_region=args.secondary_region,
            secondary_profile=args.secondary_profile, secondary_kms_key_id=args.secondary_kms_key,
            secondary_signing_key_id=args.secondary_signing_key,
        )
        manifest["verification"] = checks
        verify_manifest_artifacts(repo, manifest)
        atomic_json(manifest_path, manifest)

        manifest_replicas = put_replicated_object(
            repo, manifest_path, f"snapshots/{run_stamp}/manifest.json",
            bucket=args.bucket, region=args.region, secondary_bucket=args.secondary_bucket,
            secondary_region=args.secondary_region, secondary_profile=args.secondary_profile,
        )
        manifest_sha256 = file_sha256(manifest_path)
        receipt_payload = {
            "schema_version": 1,
            "run_id": run_stamp,
            "source_head_sha": captured["head_sha"],
            "github_sanitized_sha": github_sha,
            "manifest_replicas": manifest_replicas,
            "manifest_sha256": manifest_sha256,
            "previous_completion_anchor": previous_receipt,
            "authenticated_at_utc": utc_now().isoformat(),
        }
        try:
            receipt_message = envelope.canonical_json_bytes(receipt_payload)
            signatures = [
                envelope.sign_message(repo, args.signing_key, args.region, receipt_message),
                envelope.sign_message(
                    repo, args.secondary_signing_key, args.secondary_region, receipt_message,
                    args.secondary_profile,
                ),
            ]
        except envelope.EnvelopeError as exc:
            raise BackupError(str(exc)) from exc
        receipt = {
            "payload": receipt_payload,
            "authentication": {
                "required_signers": sorted(PINNED_SIGNERS),
                "signatures": signatures,
            },
        }
        receipt_path = stage / "completion-receipt.json"
        atomic_json(receipt_path, receipt)
        receipt_replicas = put_replicated_object(
            repo, receipt_path, f"receipts/{run_stamp}.json",
            bucket=args.bucket, region=args.region, secondary_bucket=args.secondary_bucket,
            secondary_region=args.secondary_region, secondary_profile=args.secondary_profile,
        )
        for receipt_object in receipt_replicas:
            _, verified_manifest = verify_completion_receipt(
                repo, str(receipt_object["bucket"]), str(receipt_object["region"]), receipt_object,
            )
            if verified_manifest.get("source_head_sha") != captured["head_sha"]:
                raise BackupError("authenticated completion receipt restored the wrong source SHA")

        pointer = {
            "schema_version": 2,
            "published_at_utc": utc_now().isoformat(),
            "run_id": run_stamp,
            "source_head_sha": captured["head_sha"],
            "completion_receipt_replicas": receipt_replicas,
            "completion_receipt_sha256": file_sha256(receipt_path),
        }
        pointer_path = stage / "latest.json"
        atomic_json(pointer_path, pointer)
        historical_pointers = put_replicated_object(
            repo, pointer_path, f"manifests/{run_stamp}.json",
            bucket=args.bucket, region=args.region, secondary_bucket=args.secondary_bucket,
            secondary_region=args.secondary_region, secondary_profile=args.secondary_profile,
        )
        latest_pointers = put_replicated_object(
            repo, pointer_path, "manifests/latest.json",
            bucket=args.bucket, region=args.region, secondary_bucket=args.secondary_bucket,
            secondary_region=args.secondary_region, secondary_profile=args.secondary_profile,
        )
        for verify_bucket, verify_region, verify_profile in (
            (args.bucket, args.region, None),
            (args.secondary_bucket, args.secondary_region, args.secondary_profile),
        ):
            _, latest_manifest = verify_latest_receipt(repo, verify_bucket, verify_region, verify_profile)
            if latest_manifest.get("run_id") != run_stamp:
                raise BackupError("latest pointer did not resolve to the completed run in both vaults")

        manifest["completion_receipt"] = {
            "run_id": run_stamp,
            "sha256": file_sha256(receipt_path),
            "replicas": receipt_replicas,
        }
        manifest["historical_pointers"] = historical_pointers
        manifest["latest_pointers"] = latest_pointers
        atomic_json(state / "last_success.json", manifest)
        log(f"backup verified: source={str(captured['head_sha'])[:12]} clean={github_sha[:12]}")
        return manifest


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    p.add_argument("--state", type=Path, default=DEFAULT_STATE)
    p.add_argument("--bucket", default=DEFAULT_BUCKET)
    p.add_argument("--region", default=DEFAULT_REGION)
    p.add_argument("--remote", default=DEFAULT_REMOTE)
    p.add_argument("--kms-key", default=DEFAULT_KMS_KEY)
    p.add_argument("--signing-key", default=DEFAULT_SIGNING_KEY)
    p.add_argument("--secondary-bucket", default=DEFAULT_SECONDARY_BUCKET)
    p.add_argument("--secondary-region", default=DEFAULT_SECONDARY_REGION)
    p.add_argument("--secondary-profile", default=DEFAULT_SECONDARY_PROFILE)
    p.add_argument("--secondary-kms-key", default=DEFAULT_SECONDARY_KMS_KEY)
    p.add_argument("--secondary-signing-key", default=DEFAULT_SECONDARY_SIGNING_KEY)
    p.add_argument("--reuse-clean", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--bootstrap-bundle", type=Path, help=argparse.SUPPRESS)
    p.add_argument("--min-tracked", type=int, default=MIN_TRACKED_FILES, help=argparse.SUPPRESS)
    p.add_argument("--no-mesh", action="store_true", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("run")
    sub.add_parser("verify")
    sub.add_parser("seed-github")
    sub.add_parser("status")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "run":
            perform_backup(args)
        elif args.command == "verify":
            controls = verify_controls(
                args.repo.resolve(), args.state.resolve() / "github-snapshot",
                args.bucket, args.region, args.remote,
                kms_key_id=args.kms_key, signing_key_id=args.signing_key,
                secondary_bucket=args.secondary_bucket, secondary_region=args.secondary_region,
                secondary_profile=args.secondary_profile, secondary_kms_key_id=args.secondary_kms_key,
                secondary_signing_key_id=args.secondary_signing_key,
            )
            receipt, manifest = verify_latest_receipt(args.repo.resolve(), args.bucket, args.region)
            _, secondary_manifest = verify_latest_receipt(
                args.repo.resolve(), args.secondary_bucket, args.secondary_region, args.secondary_profile,
            )
            if secondary_manifest.get("run_id") != manifest.get("run_id"):
                raise BackupError("primary and secondary vaults resolve to different completed runs")
            history = discover_receipt_history(args.repo.resolve(), args.bucket, args.region)
            if history[-1][0].get("_verified_sha256") != receipt.get("_verified_sha256"):
                raise BackupError("latest pointer and verified dependency history disagree")
            verify_restore_dependencies(args.repo.resolve(), history)
            print(json.dumps({
                "controls": controls,
                "authenticated_run_id": manifest.get("run_id"),
                "source_head_sha": manifest.get("source_head_sha"),
                "receipt_authentication": receipt.get("authentication"),
            }, indent=2, sort_keys=True))
        elif args.command == "seed-github":
            require_compute_window()
            repo = args.repo.resolve()
            mesh = repo / "12_cognitive_architecture" / "agent_mesh" / "agent_mesh.py"
            with MeshLease(
                mesh, repo, args.bucket, "<github-user>/ai-brain-resilience-backup",
                enabled=not args.no_mesh, state=args.state.resolve(),
                secondary_bucket=args.secondary_bucket,
            ), ProcessLock(args.state.resolve() / "run.lock"):
                try:
                    result, report = sanitized_snapshot.update_repository(
                        repo, args.state.resolve() / "github-snapshot",
                        args.state.resolve() / "github-snapshot-files.json",
                        repo / "12_cognitive_architecture" / "dreaming" / "scan_secrets.py",
                        before_push=lambda: assert_external(
                            "external:github/<github-user>/ai-brain-resilience-backup",
                        ),
                    )
                except sanitized_snapshot.SnapshotError as exc:
                    raise BackupError(str(exc)) from exc
            print(json.dumps({"github_sanitized_sha": result, "sanitization": report}, indent=2, sort_keys=True))
        elif args.command == "status":
            path = args.state.resolve() / "last_success.json"
            if not path.is_file():
                raise BackupError("no successful backup manifest exists yet")
            print(path.read_text(encoding="utf-8"), end="")
        return 0
    except BackupError as exc:
        log(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
