"""Exact code/runtime identity for random-external v4 solver execution.

The experiment may not treat a caller-supplied digest as evidence that the live
execution stack is the committed stack.  This module binds every direct source file
on the dispatch boundary, the Python/Unicode runtime, and the exact Git commit/tree.
The manifest is validated before every slot starts and again after every solver exits.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import platform
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .models import canonical_json

RANDOM_EXTERNAL_EXECUTION_MANIFEST_SCHEMA_V4 = (
    "org.math-research-brain/random-external-execution-manifest/v4"
)

RANDOM_EXTERNAL_EXECUTION_SOURCE_PATHS_V4 = (
    "src/math_brain/attempt_ledger.py",
    "src/math_brain/codex_artifact_io.py",
    "src/math_brain/codex_runner.py",
    "src/math_brain/exact_answer_v3.py",
    "src/math_brain/github_execution_ack_v4.py",
    "src/math_brain/github_precommit_anchor_v4.py",
    "src/math_brain/models.py",
    "src/math_brain/random_external_execution_authorization_v4.py",
    "src/math_brain/random_external_execution_manifest_v4.py",
    "src/math_brain/random_external_pilot_execution_v4.py",
    "src/math_brain/random_external_pilot_task_map_v4.py",
    "src/math_brain/three_arm_evaluation.py",
    "src/math_brain/three_arm_pilot_protocol_v3.py",
    "src/math_brain/three_arm_random_external_pilot_v4.py",
)

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_GIT_SHA1_RE = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_MANIFEST_DOMAIN = b"math-brain/random-external-execution-manifest-v4\x00"
_MODULE_DIRECTORY = Path(__file__).resolve().parent
_SOURCE_ROOT = _MODULE_DIRECTORY.parent.parent


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _git_blob_sha1(raw: bytes) -> str:
    header = f"blob {len(raw)}\0".encode("ascii")
    return hashlib.sha1(header + raw, usedforsecurity=False).hexdigest()


def _digest_json(value: object) -> str:
    return _sha256(_MANIFEST_DOMAIN + canonical_json(value).encode("utf-8"))


def _digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical SHA-256 digest")
    return value


def _git_sha1(value: object, label: str) -> str:
    if type(value) is not str or _GIT_SHA1_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be an exact lowercase Git SHA-1")
    return value


def _text(value: object, label: str) -> str:
    if (
        type(value) is not str
        or not value
        or "\x00" in value
        or "\n" in value
        or len(value.encode("utf-8")) > 128
    ):
        raise ValueError(f"{label} must be bounded one-line text")
    return value


def _is_reparse_point(path: Path) -> bool:
    try:
        status = path.lstat()
    except OSError as error:
        raise ValueError(f"cannot inspect execution source path: {path}") from error
    attributes = getattr(status, "st_file_attributes", 0)
    reparse_flag = getattr(os.stat_result, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    # CPython exposes the Windows constant on stat, not os.stat_result, on some builds.
    if not reparse_flag:
        import stat

        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return path.is_symlink() or bool(attributes & reparse_flag)


def _source_path(relative_path: str) -> Path:
    if relative_path not in RANDOM_EXTERNAL_EXECUTION_SOURCE_PATHS_V4:
        raise ValueError("execution artifact path is outside the frozen source surface")
    path = (_SOURCE_ROOT / relative_path).resolve(strict=True)
    expected = _SOURCE_ROOT / relative_path
    if path != expected or _is_reparse_point(path) or not path.is_file():
        raise ValueError("execution artifact must be an exact regular source file")
    return path


def _source_bytes(relative_path: str) -> bytes:
    try:
        return _source_path(relative_path).read_bytes()
    except OSError as error:
        raise ValueError(f"cannot read execution artifact: {relative_path}") from error


def _runtime_identity() -> dict[str, str]:
    cache_tag = sys.implementation.cache_tag
    if type(cache_tag) is not str or not cache_tag:
        raise ValueError("Python cache tag is unavailable")
    return {
        "python_cache_tag": cache_tag,
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "unicode_version": unicodedata.unidata_version,
    }


def _run_git(repository_root: Path, *arguments: str) -> bytes:
    if not repository_root.is_absolute():
        raise ValueError("execution source repository must be absolute")
    root = repository_root.resolve(strict=True)
    if not root.is_dir() or not (root / ".git").exists():
        raise ValueError("execution source repository is not an exact Git worktree")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            ("git", "-c", "core.quotepath=false", "-C", str(root), *arguments),
            check=False,
            capture_output=True,
            creationflags=creation_flags,
            shell=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("cannot inspect the execution Git tree") from error
    if completed.returncode != 0 or completed.stderr:
        raise ValueError("execution Git-tree inspection failed")
    return completed.stdout


@dataclass(frozen=True, slots=True)
class RandomExternalExecutionArtifactV4:
    relative_path: str
    git_blob_sha1: str
    content_sha256: str

    def __post_init__(self) -> None:
        if type(self) is not RandomExternalExecutionArtifactV4:
            raise ValueError("execution-artifact subclasses are forbidden")
        if self.relative_path not in RANDOM_EXTERNAL_EXECUTION_SOURCE_PATHS_V4:
            raise ValueError("execution artifact path is outside the frozen source surface")
        _git_sha1(self.git_blob_sha1, "execution artifact Git blob")
        _digest(self.content_sha256, "execution artifact content")

    def validate_current(self) -> None:
        self.__post_init__()
        raw = _source_bytes(self.relative_path)
        if not hmac.compare_digest(self.git_blob_sha1, _git_blob_sha1(raw)) or not (
            hmac.compare_digest(self.content_sha256, _sha256(raw))
        ):
            raise ValueError(f"execution artifact changed: {self.relative_path}")

    def commitment_record(self) -> dict[str, str]:
        self.__post_init__()
        return {
            "content_sha256": self.content_sha256,
            "git_blob_sha1": self.git_blob_sha1,
            "relative_path": self.relative_path,
        }


@dataclass(frozen=True, slots=True)
class RandomExternalExecutionManifestV4:
    git_commit_sha1: str
    git_tree_sha1: str
    artifacts: tuple[RandomExternalExecutionArtifactV4, ...]
    python_implementation: str
    python_version: str
    python_cache_tag: str
    unicode_version: str
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not RandomExternalExecutionManifestV4:
            raise ValueError("execution-manifest subclasses are forbidden")
        self._validate_fields(check_current=False)
        object.__setattr__(self, "content_digest", _digest_json(self._payload()))

    def _validate_fields(self, *, check_current: bool) -> None:
        _git_sha1(self.git_commit_sha1, "execution Git commit")
        _git_sha1(self.git_tree_sha1, "execution Git tree")
        if (
            type(self.artifacts) is not tuple
            or tuple(artifact.relative_path for artifact in self.artifacts)
            != RANDOM_EXTERNAL_EXECUTION_SOURCE_PATHS_V4
            or any(
                type(artifact) is not RandomExternalExecutionArtifactV4
                for artifact in self.artifacts
            )
        ):
            raise ValueError("execution manifest must contain the exact ordered source surface")
        for artifact in self.artifacts:
            artifact.__post_init__()
        for value, label in (
            (self.python_implementation, "Python implementation"),
            (self.python_version, "Python version"),
            (self.python_cache_tag, "Python cache tag"),
            (self.unicode_version, "Unicode version"),
        ):
            _text(value, label)
        if check_current:
            observed = {
                "python_cache_tag": self.python_cache_tag,
                "python_implementation": self.python_implementation,
                "python_version": self.python_version,
                "unicode_version": self.unicode_version,
            }
            if observed != _runtime_identity():
                raise ValueError("execution Python or Unicode runtime changed")
            for artifact in self.artifacts:
                artifact.validate_current()

    def _payload(self) -> dict[str, object]:
        return {
            "artifacts": [artifact.commitment_record() for artifact in self.artifacts],
            "git_commit_sha1": self.git_commit_sha1,
            "git_tree_sha1": self.git_tree_sha1,
            "python_cache_tag": self.python_cache_tag,
            "python_implementation": self.python_implementation,
            "python_version": self.python_version,
            "schema": RANDOM_EXTERNAL_EXECUTION_MANIFEST_SCHEMA_V4,
            "schema_version": 4,
            "unicode_version": self.unicode_version,
        }

    def validate_current(self) -> None:
        self._validate_fields(check_current=True)
        if not hmac.compare_digest(self.content_digest, _digest_json(self._payload())):
            raise ValueError("execution manifest digest is stale")

    def validate_git_tree(self, repository_root: str | Path) -> None:
        """Prove that commit/tree labels own every frozen source blob."""

        self.validate_current()
        root = Path(repository_root)
        observed_tree = _run_git(root, "rev-parse", f"{self.git_commit_sha1}^{{tree}}")
        try:
            tree_sha1 = observed_tree.decode("ascii", errors="strict").strip()
        except UnicodeDecodeError as error:
            raise ValueError("execution Git tree identity is not ASCII") from error
        if not hmac.compare_digest(tree_sha1, self.git_tree_sha1):
            raise ValueError("execution Git commit differs from the frozen tree")
        raw = _run_git(
            root,
            "ls-tree",
            "-z",
            self.git_commit_sha1,
            "--",
            *RANDOM_EXTERNAL_EXECUTION_SOURCE_PATHS_V4,
        )
        records: dict[str, str] = {}
        for item in raw.split(b"\x00"):
            if not item:
                continue
            try:
                metadata, path_raw = item.split(b"\t", 1)
                mode, kind, object_id = metadata.decode("ascii").split(" ")
                relative_path = path_raw.decode("utf-8", errors="strict")
            except (UnicodeDecodeError, ValueError) as error:
                raise ValueError("execution Git tree returned a malformed record") from error
            if mode != "100644" or kind != "blob" or relative_path in records:
                raise ValueError("execution Git tree contains a noncanonical source record")
            records[relative_path] = _git_sha1(object_id, "execution Git-tree blob")
        expected = {artifact.relative_path: artifact.git_blob_sha1 for artifact in self.artifacts}
        if records != expected:
            raise ValueError("execution Git tree differs from the frozen source blobs")

    def commitment_record(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}


def capture_current_random_external_execution_manifest_v4(
    *,
    repository_root: str | Path,
    git_commit_sha1: str,
    git_tree_sha1: str,
) -> RandomExternalExecutionManifestV4:
    """Capture and immediately prove the exact committed execution surface."""

    artifacts = tuple(
        RandomExternalExecutionArtifactV4(
            relative_path=relative_path,
            git_blob_sha1=_git_blob_sha1(raw),
            content_sha256=_sha256(raw),
        )
        for relative_path in RANDOM_EXTERNAL_EXECUTION_SOURCE_PATHS_V4
        for raw in (_source_bytes(relative_path),)
    )
    manifest = RandomExternalExecutionManifestV4(
        git_commit_sha1=git_commit_sha1,
        git_tree_sha1=git_tree_sha1,
        artifacts=artifacts,
        **_runtime_identity(),
    )
    manifest.validate_git_tree(repository_root)
    return manifest


__all__ = [
    "RANDOM_EXTERNAL_EXECUTION_MANIFEST_SCHEMA_V4",
    "RANDOM_EXTERNAL_EXECUTION_SOURCE_PATHS_V4",
    "RandomExternalExecutionArtifactV4",
    "RandomExternalExecutionManifestV4",
    "capture_current_random_external_execution_manifest_v4",
]
