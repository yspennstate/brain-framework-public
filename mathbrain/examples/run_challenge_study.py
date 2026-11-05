"""Run the private sealed holdout study from a clean, exact repository revision."""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from math_brain.attempt_ledger import AttemptLedger
from math_brain.challenge_study import (
    StudySecrets,
    execute_prepared_study,
    open_preregistered_holdout_study,
    preregister_holdout_study,
)
from math_brain.codex_runner import MAX_AUTH_FILE_BYTES


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0))
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _write_exclusive(path: Path, raw: bytes, *, private: bool = True) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | int(getattr(os, "O_BINARY", 0))
    descriptor = os.open(path, flags, 0o600 if private else 0o644)
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write while persisting private study state")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.chmod(path, 0o600 if private else 0o644)
    except OSError:
        pass
    _fsync_directory(path.parent)


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _git_output(repository: Path, *arguments: str) -> str:
    executable = shutil.which("git")
    if not executable:
        raise RuntimeError("git executable was not found")
    creationflags = 0
    if os.name == "nt":
        creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) | int(
            getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
        )
    completed = subprocess.run(
        [executable, "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        creationflags=creationflags,
        shell=False,
        text=True,
        timeout=30,
    )
    return completed.stdout.strip()


def _require_exact_clean_revision(repository: Path, expected: str) -> None:
    actual = _git_output(repository, "rev-parse", "--verify", "HEAD")
    if actual != expected:
        raise RuntimeError("brain revision differs from the checked-out HEAD")
    if _git_output(repository, "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("worktree changes must be committed before preregistration")


def _require_tracked_file(repository: Path, path: Path) -> Path:
    resolved = path.resolve(strict=True)
    if not _inside(resolved, repository):
        raise RuntimeError("study configuration and launcher must belong to the brain revision")
    relative = resolved.relative_to(repository).as_posix()
    try:
        tracked = _git_output(
            repository,
            "ls-files",
            "--error-unmatch",
            "--",
            relative,
        )
    except subprocess.CalledProcessError as error:
        raise RuntimeError("study input is not tracked by the brain revision") from error
    if tracked != relative:
        raise RuntimeError("study input did not resolve to one exact tracked file")
    return resolved


def _require_auth_file(path: Path) -> Path:
    if path.is_symlink():
        raise RuntimeError("Codex auth source must not be a symlink")
    resolved = path.resolve(strict=True)
    status = resolved.lstat()
    if (
        resolved.name != "auth.json"
        or not stat.S_ISREG(status.st_mode)
        or not 0 < status.st_size <= MAX_AUTH_FILE_BYTES
    ):
        raise RuntimeError("Codex auth source is not a bounded regular auth.json")
    if any(
        (ancestor / ".git").exists()
        for ancestor in (resolved.parent, *resolved.parents)
    ):
        raise RuntimeError("Codex auth source must be outside every Git repository")
    return resolved


def _workspace(parent: Path, repository: Path) -> tuple[Path, Path]:
    parent.mkdir(parents=True, exist_ok=True)
    parent = parent.resolve(strict=True)
    if _inside(parent, repository) or any(
        (ancestor / ".git").exists() for ancestor in (parent, *parent.parents)
    ):
        raise RuntimeError("private workspace parent must be outside every Git repository")
    root = Path(tempfile.mkdtemp(prefix="study_", dir=parent)).resolve(strict=True)
    runtime = root / "runtime"
    runtime.mkdir()
    _fsync_directory(root)
    return root, runtime


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--challenge-config", type=Path, required=True)
    parser.add_argument("--evaluation-config", type=Path, required=True)
    parser.add_argument("--holdout-payload", type=Path, required=True)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--brain-revision", required=True)
    parser.add_argument("--codex-auth-file", type=Path, required=True)
    parser.add_argument("--workspace-parent", type=Path, required=True)
    parser.add_argument("--max-workers", type=int, choices=(1, 2), default=1)
    return parser


def main() -> int:
    args = _parser().parse_args()
    repository = args.repository_root.resolve(strict=True)
    _require_exact_clean_revision(repository, args.brain_revision)
    challenge_config = _require_tracked_file(repository, args.challenge_config)
    evaluation_config = _require_tracked_file(repository, args.evaluation_config)
    _require_tracked_file(repository, Path(__file__))
    expected_module = (repository / "src" / "math_brain" / "challenge_study.py").resolve(
        strict=True
    )
    loaded_module = Path(
        __import__(StudySecrets.__module__, fromlist=["__file__"]).__file__
    ).resolve(strict=True)
    if loaded_module != expected_module:
        raise RuntimeError("challenge-study code was not loaded from the exact brain revision")
    codex_auth_file = _require_auth_file(args.codex_auth_file)

    secrets_material = StudySecrets.generate()
    workspace, runtime_parent = _workspace(args.workspace_parent, repository)
    _write_exclusive(workspace / "ledger.key", secrets_material.ledger_key)
    _write_exclusive(
        workspace / "study-secrets.json",
        _canonical_json(
            {
                "brain_revision": args.brain_revision,
                "classification": "private_evaluator_control_material",
                "runtime_salt_base64": base64.b64encode(
                    secrets_material.runtime_salt
                ).decode("ascii"),
                "schema": "org.math-research-brain/private-study-secrets",
                "schema_version": 1,
                "seeds": {
                    "bootstrap": secrets_material.seeds.bootstrap,
                    "condition": secrets_material.seeds.condition,
                    "order": secrets_material.seeds.order,
                    "pair": secrets_material.seeds.pair,
                    "variant": secrets_material.seeds.variant,
                },
            }
        ),
    )
    preregistration = preregister_holdout_study(
        challenge_config=challenge_config,
        evaluation_config=evaluation_config,
        executable_path=args.executable,
        brain_revision=args.brain_revision,
        secrets_material=secrets_material,
    )
    _write_exclusive(
        workspace / "preregistration.json",
        _canonical_json(
            preregistration.private_preregistration_record(secrets_material)
        ),
    )
    # This second clean-tree check is after the durable preregistration and
    # immediately before the first private payload read.
    _require_exact_clean_revision(repository, args.brain_revision)
    holdout_payload = args.holdout_payload.resolve(strict=True).read_text(
        encoding="utf-8"
    )
    prepared = open_preregistered_holdout_study(
        preregistration,
        holdout_payload_json=holdout_payload,
        secrets_material=secrets_material,
    )
    _write_exclusive(
        workspace / "precommitment.json",
        _canonical_json(prepared.private_precommitment_record()),
    )
    ledger = AttemptLedger.create(
        workspace / "ledger",
        key=secrets_material.ledger_key,
        slots=prepared.commitments,
    )
    _write_exclusive(
        workspace / "ledger-anchor.json",
        _canonical_json(
            {
                "evaluator_manifest_digest": prepared.evaluator_manifest_digest,
                "ledger_digest": ledger.ledger_digest,
                "schema": "org.math-research-brain/private-ledger-anchor",
                "schema_version": 1,
            }
        ),
    )

    _require_exact_clean_revision(repository, args.brain_revision)
    execution = execute_prepared_study(
        prepared,
        ledger=ledger,
        runtime_parent=runtime_parent,
        repository_root=repository,
        auth_file=codex_auth_file,
        max_workers=args.max_workers,
    )
    summary = execution.privacy_safe_summary()
    summary.update(
        {
            "brain_revision": prepared.brain_revision,
            "evaluator_manifest_digest": prepared.evaluator_manifest_digest,
            "executable_provenance": prepared.executable_provenance.to_dict(),
            "run_spec_digest": prepared.plan.spec.digest,
            "schema": "org.math-research-brain/private-study-summary",
            "schema_version": 1,
        }
    )
    _write_exclusive(workspace / "summary.json", _canonical_json(summary))
    print(
        json.dumps(
            {"summary": summary, "workspace": str(workspace)},
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
