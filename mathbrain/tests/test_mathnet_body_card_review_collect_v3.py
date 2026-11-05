from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import math_brain.mathnet_body_card_review_collect_v3 as collect
from math_brain.models import canonical_json


def test_private_writer_is_idempotent_and_rejects_different_bytes(tmp_path: Path) -> None:
    target = tmp_path / "private.json"
    assert collect._write_private_once(target, b'{"ok":true}\n', maximum=1_000) == target
    assert collect._write_private_once(target, b'{"ok":true}\n', maximum=1_000) == target
    with pytest.raises(FileExistsError, match="already differs"):
        collect._write_private_once(target, b'{"ok":false}\n', maximum=1_000)
    assert not list(tmp_path.glob(".*.tmp"))


def test_private_file_recovers_its_known_crash_staging_hardlink(tmp_path: Path) -> None:
    target = tmp_path / "private.json"
    collect._write_private_once(target, b'{"ok":true}\n', maximum=1_000)
    staging = tmp_path / ".private.json.0123456789abcdef0123456789abcdef.tmp"
    os.link(target, staging)
    assert target.stat().st_nlink == 2

    assert collect._private_file(tmp_path, target.name, must_exist=True) == target
    assert not staging.exists()
    assert target.stat().st_nlink == 1


def test_cold_verify_rejects_loaded_source_outside_anchored_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_digest = "sha256:" + "1" * 64
    anchored_revision = "2" * 40
    collect._write_private_once(
        tmp_path / collect.PUBLIC_PRECOMMITMENT_FILE,
        b"{}\n",
        maximum=1_000,
    )
    projection = object()
    bindings = SimpleNamespace(raw_file_digest="sha256:" + "3" * 64)
    public = {
        "core": {"implementation_revision": {"oid": anchored_revision}},
        "core_digest": "sha256:" + "4" * 64,
    }
    monkeypatch.setattr(collect, "_load_projection", lambda _root: (b"", projection))
    monkeypatch.setattr(
        collect,
        "_load_bindings",
        lambda _root, _projection: (b"", bindings),
    )
    monkeypatch.setattr(
        collect,
        "parse_body_card_review_public_precommitment_v3",
        lambda _raw, *, expected_digest: public,
    )
    monkeypatch.setattr(
        collect,
        "body_card_review_public_precommitment_digest_v3",
        lambda _public: expected_digest,
    )
    monkeypatch.setattr(
        collect.execution,
        "capture_body_card_review_implementation_revision_v3",
        lambda _repository: (str(tmp_path), "5" * 40),
    )
    args = SimpleNamespace(
        private_root=str(tmp_path),
        repository_root=str(tmp_path),
        expected_public_digest=expected_digest,
        remote="origin",
    )
    with pytest.raises(ValueError, match="implementation differs"):
        collect._verify(args)


def test_source_capture_attests_package_initializer_hidden_from_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    module_root = repository / "src" / "math_brain"
    module_root.mkdir(parents=True)
    execution = collect.execution
    module_paths = {
        execution: module_root / "mathnet_body_card_review_execution_v3.py",
        execution._attempt_ledger_module: module_root / "attempt_ledger.py",
        execution._canonical_ed25519_module: module_root / "canonical_ed25519_v3.py",
        execution._codex_artifact_module: module_root / "codex_artifact_io.py",
        execution._codex_runner_module: module_root / "codex_runner.py",
        execution._review_module: module_root / "mathnet_body_card_review_v3.py",
        execution._models_module: module_root / "models.py",
    }
    for index, (module, path) in enumerate(module_paths.items()):
        path.write_text(f"# synthetic module {index}\n", encoding="utf-8")
        monkeypatch.setattr(module, "__file__", str(path))
    for name in (
        "__init__.py",
        "mathnet_body_card_review_signed_pair_v3.py",
        "mathnet_body_card_review_collect_v3.py",
    ):
        (module_root / name).write_text(f"# synthetic {name}\n", encoding="utf-8")

    collect._git(tmp_path, "init", str(repository))
    collect._git(repository, "config", "user.name", "Source Capture Test")
    collect._git(repository, "config", "user.email", "source-capture@example.invalid")
    collect._git(repository, "add", "src")
    collect._git(repository, "commit", "--message", "freeze synthetic modules")
    collect._git(
        repository,
        "update-index",
        "--assume-unchanged",
        "src/math_brain/__init__.py",
    )
    (module_root / "__init__.py").write_text("# hidden initializer mutation\n", encoding="utf-8")
    assert collect._git(repository, "status", "--porcelain", "--untracked-files=all") == ""

    with pytest.raises(ValueError, match="bytes differ"):
        execution.capture_body_card_review_implementation_revision_v3(repository)


def test_annotated_remote_anchor_ack_is_live_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    remote_repository = tmp_path / "remote.git"
    collect._git(tmp_path, "init", "--bare", str(remote_repository))
    collect._git(tmp_path, "init", str(repository))
    collect._git(repository, "config", "user.name", "Body Card Test")
    collect._git(repository, "config", "user.email", "body-card-test@example.invalid")
    (repository / "tracked.txt").write_text("implementation\n", encoding="utf-8")
    collect._git(repository, "add", "tracked.txt")
    collect._git(repository, "commit", "--message", "freeze implementation")
    collect._git(repository, "remote", "add", "origin", str(remote_repository))
    collect._git(repository, "push", "origin", "HEAD:refs/heads/main")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Private Parent Identity")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "private-parent@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2000-01-01T00:00:00+0900")
    revision = collect._git(repository, "rev-parse", "--verify", "HEAD")
    assert revision is not None

    public_digest = "sha256:" + "1" * 64
    public_raw_digest = "sha256:" + "2" * 64
    bindings_digest = "sha256:" + "3" * 64
    core_digest = "sha256:" + "4" * 64
    tag_name = collect._tag_name(public_digest)
    remote_locator = collect._remote_locator(repository, "origin")
    message = collect._anchor_message(
        implementation_revision=revision,
        public_digest=public_digest,
        public_raw_digest=public_raw_digest,
        event_family_bindings_digest=bindings_digest,
        core_digest=core_digest,
        remote="origin",
        remote_locator=remote_locator,
    )
    collect._git(
        repository,
        "tag",
        "--annotate",
        "--no-sign",
        "--message",
        message,
        tag_name,
        revision,
        anchor_identity=True,
    )
    tag_oid, target = collect._local_tag_details(
        repository,
        tag_name=tag_name,
        expected_revision=revision,
        expected_message=message,
    )
    raw_tag = collect._git(repository, "cat-file", "-p", tag_oid)
    assert raw_tag is not None
    tagger_header = raw_tag.splitlines()[3]
    assert collect._ANCHOR_TAGGER_RE.fullmatch(tagger_header) is not None
    assert "Body Card Test" not in raw_tag
    assert "Private Parent Identity" not in raw_tag
    assert tagger_header.endswith(" +0000")
    assert (
        collect._remote_tag_details(
            repository,
            remote="origin",
            tag_name=tag_name,
        )
        is None
    )
    collect._git(
        repository,
        "push",
        "origin",
        f"refs/tags/{tag_name}:refs/tags/{tag_name}",
    )
    assert collect._remote_tag_details(
        repository,
        remote="origin",
        tag_name=tag_name,
    ) == (tag_oid, target)

    private_root = tmp_path / "private"
    private_root.mkdir()
    ack = {
        "anchor_message_digest": collect._sha256(message.encode("ascii")),
        "implementation_revision": revision,
        "public_precommitment_digest": public_digest,
        "remote": "origin",
        "remote_locator": remote_locator,
        "remote_ref": f"refs/tags/{tag_name}",
        "remote_tag_object_oid": tag_oid,
        "schema": collect.BODY_CARD_COLLECTION_ANCHOR_ACK_SCHEMA_V3,
        "schema_version": collect.BODY_CARD_REVIEW_VERSION_V3,
        "tag_name": tag_name,
    }
    collect._write_private_once(
        private_root / collect.ANCHOR_ACK_FILE,
        canonical_json(ack).encode("utf-8") + b"\n",
        maximum=100_000,
    )
    assert (
        collect._verify_anchor_ack(
            private_root,
            repository,
            expected_public_digest=public_digest,
            expected_revision=revision,
            expected_message=message,
            expected_remote="origin",
            expected_remote_locator=remote_locator,
        )
        == ack
    )
    collect._git(repository, "push", "origin", f":refs/tags/{tag_name}")
    with pytest.raises(ValueError, match="live remote"):
        collect._verify_anchor_ack(
            private_root,
            repository,
            expected_public_digest=public_digest,
            expected_revision=revision,
            expected_message=message,
            expected_remote="origin",
            expected_remote_locator=remote_locator,
        )

    collect._git(
        repository,
        "push",
        "origin",
        f"refs/tags/{tag_name}:refs/tags/{tag_name}",
    )
    replacement_remote = tmp_path / "replacement.git"
    collect._git(tmp_path, "init", "--bare", str(replacement_remote))
    collect._git(repository, "remote", "set-url", "origin", str(replacement_remote))
    with pytest.raises(ValueError, match="anchor acknowledgment differs"):
        collect._verify_anchor_ack(
            private_root,
            repository,
            expected_public_digest=public_digest,
            expected_revision=revision,
            expected_message=message,
            expected_remote="origin",
            expected_remote_locator=remote_locator,
        )
