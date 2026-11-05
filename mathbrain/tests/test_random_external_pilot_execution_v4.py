from __future__ import annotations

import hashlib
import inspect
from dataclasses import replace
from pathlib import Path

import pytest

import math_brain.random_external_execution_manifest_v4 as manifest_module
import math_brain.random_external_pilot_execution_v4 as execution
from math_brain.attempt_ledger import DispatchMaterial, StartedAttempt
from math_brain.codex_runner import capture_codex_executable_provenance
from math_brain.random_external_execution_manifest_v4 import (
    RANDOM_EXTERNAL_EXECUTION_SOURCE_PATHS_V4,
    RandomExternalExecutionArtifactV4,
    RandomExternalExecutionManifestV4,
)


def digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def execution_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> RandomExternalExecutionManifestV4:
    source = {
        path: f"# frozen {path}\n".encode() for path in RANDOM_EXTERNAL_EXECUTION_SOURCE_PATHS_V4
    }
    monkeypatch.setattr(manifest_module, "_source_bytes", source.__getitem__)
    return RandomExternalExecutionManifestV4(
        git_commit_sha1="a" * 40,
        git_tree_sha1="b" * 40,
        artifacts=tuple(
            RandomExternalExecutionArtifactV4(
                relative_path=path,
                git_blob_sha1=manifest_module._git_blob_sha1(raw),
                content_sha256=manifest_module._sha256(raw),
            )
            for path, raw in source.items()
        ),
        **manifest_module._runtime_identity(),
    )


def policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> execution.RandomExternalExecutionPolicyV4:
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"MZ\x00\x00synthetic-native-binary")
    provenance = capture_codex_executable_provenance(str(executable))
    return execution.freeze_random_external_execution_policy_v4(
        executable_provenance=provenance,
        implementation_manifest=execution_manifest(monkeypatch),
        model="gpt-5.6-sol",
        effort="ultra",
        model_provider="openai",
        timeout_seconds=60,
    )


def test_policy_freeze_builds_digest_before_exact_dataclass_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    frozen = policy(monkeypatch, tmp_path)

    frozen.__post_init__()
    assert frozen.content_digest.startswith("sha256:")
    assert frozen.implementation_manifest_digest == frozen.implementation_manifest.content_digest
    with pytest.raises(ValueError, match="digest is stale"):
        replace(frozen, content_digest=digest("attacker-policy"))


def test_instruction_drift_is_rejected_before_a_slot_can_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    frozen = policy(monkeypatch, tmp_path)
    monkeypatch.setattr(execution, "DEFAULT_BASE_INSTRUCTIONS", "changed after freeze")

    with pytest.raises(ValueError, match="instructions changed"):
        frozen.__post_init__()


def test_ineligible_runner_result_becomes_an_exception_before_serialization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    frozen = policy(monkeypatch, tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    dispatch = DispatchMaterial(
        run_spec_digest=digest("run-spec"),
        batch_digest=digest("batch"),
        prompt="Solve the opaque task.",
        output_schema_json=b'{"type":"object"}',
        request_policy_digest=frozen.content_digest,
        dispatch_nonce=digest("dispatch"),
    )
    calls: list[str] = []

    class IneligibleResult:
        def require_eligible(self) -> None:
            calls.append("require_eligible")
            raise RuntimeError("ineligible synthetic result")

    class FakeRunner:
        def __init__(self, _config: object) -> None:
            pass

        def run(self, *, prompt: str, output_schema: object) -> IneligibleResult:
            assert prompt == dispatch.prompt
            assert output_schema == {"type": "object"}
            return IneligibleResult()

    monkeypatch.setattr(execution, "CodexAppServerRunner", FakeRunner)
    monkeypatch.setattr(
        execution.RandomExternalExecutionPolicyV4,
        "validate_current",
        lambda self, *, repository_root=None: None,
    )

    with pytest.raises(RuntimeError, match="ineligible synthetic result"):
        execution._run_one_desktop_attempt_v4(
            runtime,
            dispatch,
            policy=frozen,
            auth_file=auth,
            repository_root=tmp_path,
        )
    assert calls == ["require_eligible"]


def test_private_ledger_root_rejects_git_storage_and_path_aliases(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()

    with pytest.raises(ValueError, match="outside every Git repository"):
        execution._canonical_private_ledger_root_v4(
            repository / "private-ledger",
            must_exist=False,
        )

    outside = tmp_path / "outside"
    outside.mkdir()
    alias = outside / ".." / "outside" / "private-ledger"
    with pytest.raises(ValueError, match="parent alias"):
        execution._canonical_private_ledger_root_v4(alias, must_exist=False)


def test_campaign_lock_rejects_a_second_executor(tmp_path: Path) -> None:
    ledger_root = tmp_path / "ledger"
    ledger_root.mkdir()

    with (
        execution._exclusive_execution_lock_v4(ledger_root),
        pytest.raises(RuntimeError, match="another v4 executor"),
        execution._exclusive_execution_lock_v4(ledger_root),
    ):
        raise AssertionError("second executor unexpectedly acquired the lock")


def test_burned_runner_failure_requires_a_distinct_authenticated_record() -> None:
    key = b"closure-key-role-separated-00000000000000000000000000000001"
    slot_id = "slot_" + "1" * 32
    started = StartedAttempt(
        slot_id=slot_id,
        ordinal=0,
        ledger_digest=digest("ledger"),
        record_digest=digest("started"),
        runtime_id="run_" + "2" * 32,
        runtime_device=1,
        runtime_inode=2,
        prerequisite_terminal_digest=None,
    )
    authenticator = execution._burned_runner_failure_authenticator_v4(
        ledger_key=key,
        execution_public_record_digest=digest("public-record"),
        ledger_digest=started.ledger_digest,
        ordinal=0,
        slot_id=slot_id,
        started_record_digest=started.record_digest,
    )
    attempt = execution.RandomExternalClosedAttemptV4(
        ordinal=0,
        slot_id=slot_id,
        slot_commitment_digest=digest("slot-commitment"),
        started=started,
        selected_outcome=None,
        terminal=None,
        disposition=execution.RandomExternalClosedDispositionV4.BURNED_RUNNER_FAILURE,
        burned_runner_failure_authenticator=authenticator,
    )

    assert attempt.commitment_record()["burned_runner_failure_authenticator"] == authenticator
    with pytest.raises(ValueError, match="lacks its authenticator"):
        replace(attempt, burned_runner_failure_authenticator=None)


def test_public_execution_surface_has_no_runner_retry_or_unacknowledged_bound_path() -> None:
    parameters = inspect.signature(execution.execute_random_external_pilot_v4).parameters

    assert tuple(parameters) == (
        "acknowledged",
        "runtime_parent",
        "repository_root",
        "auth_file",
    )
    assert "runner" not in parameters
    assert "retry" not in parameters
    assert parameters["acknowledged"].annotation != execution.BoundRandomExternalExecutionV4
