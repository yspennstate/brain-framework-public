from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import math_brain.random_external_execution_manifest_v4 as execution_manifest

COMMIT = "a" * 40
TREE = "b" * 40


def install_frozen_source(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, bytes]:
    source = {
        relative_path: f"# frozen {relative_path}\n".encode()
        for relative_path in execution_manifest.RANDOM_EXTERNAL_EXECUTION_SOURCE_PATHS_V4
    }

    def source_bytes(relative_path: str) -> bytes:
        return source[relative_path]

    def run_git(_repository_root: Path, *arguments: str) -> bytes:
        if arguments[0] == "rev-parse":
            return (TREE + "\n").encode("ascii")
        assert arguments[:3] == ("ls-tree", "-z", COMMIT)
        return b"".join(
            (
                f"100644 blob {execution_manifest._git_blob_sha1(source[path])}\t{path}".encode()
                + b"\x00"
            )
            for path in execution_manifest.RANDOM_EXTERNAL_EXECUTION_SOURCE_PATHS_V4
        )

    monkeypatch.setattr(execution_manifest, "_source_bytes", source_bytes)
    monkeypatch.setattr(execution_manifest, "_run_git", run_git)
    return source


def capture(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[execution_manifest.RandomExternalExecutionManifestV4, dict[str, bytes]]:
    source = install_frozen_source(monkeypatch)
    manifest = execution_manifest.capture_current_random_external_execution_manifest_v4(
        repository_root=Path("C:/synthetic-repository"),
        git_commit_sha1=COMMIT,
        git_tree_sha1=TREE,
    )
    return manifest, source


def test_manifest_binds_exact_ordered_source_runtime_and_git_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, _source = capture(monkeypatch)

    manifest.validate_current()
    manifest.validate_git_tree(Path("C:/synthetic-repository"))
    assert tuple(artifact.relative_path for artifact in manifest.artifacts) == (
        execution_manifest.RANDOM_EXTERNAL_EXECUTION_SOURCE_PATHS_V4
    )
    assert manifest.commitment_record()["content_digest"] == manifest.content_digest


def test_live_source_drift_invalidates_manifest_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, source = capture(monkeypatch)
    first_path = execution_manifest.RANDOM_EXTERNAL_EXECUTION_SOURCE_PATHS_V4[0]
    source[first_path] += b"# drift\n"

    with pytest.raises(ValueError, match="execution artifact changed"):
        manifest.validate_current()


def test_runtime_drift_cannot_be_hidden_by_recomputing_content_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, _source = capture(monkeypatch)
    attacker = replace(manifest, python_version="0.0.0")

    with pytest.raises(ValueError, match="runtime changed"):
        attacker.validate_current()


def test_commit_label_must_resolve_to_exact_tree_and_source_blobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, source = capture(monkeypatch)

    def wrong_tree(_repository_root: Path, *arguments: str) -> bytes:
        if arguments[0] == "rev-parse":
            return ("c" * 40 + "\n").encode("ascii")
        return b"".join(
            f"100644 blob {execution_manifest._git_blob_sha1(source[path])}\t{path}\x00".encode()
            for path in execution_manifest.RANDOM_EXTERNAL_EXECUTION_SOURCE_PATHS_V4
        )

    monkeypatch.setattr(execution_manifest, "_run_git", wrong_tree)
    with pytest.raises(ValueError, match="differs from the frozen tree"):
        manifest.validate_git_tree(Path("C:/synthetic-repository"))


def test_manifest_rejects_reordered_or_substituted_source_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, _source = capture(monkeypatch)

    with pytest.raises(ValueError, match="exact ordered source surface"):
        replace(manifest, artifacts=tuple(reversed(manifest.artifacts)))

    wrong = replace(manifest.artifacts[0], git_blob_sha1="d" * 40)
    substituted = replace(manifest, artifacts=(wrong, *manifest.artifacts[1:]))
    with pytest.raises(ValueError, match="execution artifact changed"):
        substituted.validate_current()
