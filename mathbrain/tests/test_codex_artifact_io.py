from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import fields, replace
from pathlib import Path

import pytest

import math_brain.codex_artifact_io as artifact_io
from math_brain import codex_runner
from math_brain.codex_artifact_io import (
    MAX_PRIVATE_ARTIFACT_BYTES,
    PRIVATE_ARTIFACT_CLASSIFICATION,
    CodexArtifactError,
    deserialize_codex_run_result,
    serialize_codex_run_result,
)
from math_brain.codex_runner import (
    DEFAULT_BASE_INSTRUCTIONS,
    DEFAULT_DEVELOPER_INSTRUCTIONS,
    CodexRunResult,
    app_server_command_tail_policy_digest,
    audit_app_server_messages,
    locked_initialize_request_json,
)

CONTENT_DIGEST_DOMAIN = b"math-brain/private-codex-run-result/v3\x00"


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def request_bytes(value: object) -> bytes:
    return canonical_json(value)


def reseal(envelope: dict[str, object]) -> bytes:
    result = envelope["result"]
    envelope["content_digest"] = (
        "sha256:" + hashlib.sha256(CONTENT_DIGEST_DOMAIN + canonical_json(result)).hexdigest()
    )
    return canonical_json(envelope)


@pytest.fixture
def artifact() -> CodexRunResult:
    requested_model = "gpt-5.6-sol"
    requested_effort = "ultra"
    requested_provider = "openai"
    prompt = "Solve the private sealed batch."
    output_schema = {"type": "object"}
    output_text = '{"ok":true}'
    thread_id = "019b2234-5678-7abc-8def-0123456789ab"
    turn_id = "019b2234-5678-7abc-9def-0123456789ac"
    message_id = "message-private-1"
    runtime_cwd = (
        str(Path("C:/private-codex-runtime")) if os.name == "nt" else "/private-codex-runtime"
    )
    executable_path = (
        str(Path("C:/private-bin/codex.exe")) if os.name == "nt" else "/private-bin/codex"
    )
    thread_start_result = {
        "model": requested_model,
        "modelProvider": requested_provider,
        "reasoningEffort": requested_effort,
        "thread": {"id": thread_id},
    }
    thread_request = {
        "id": 2,
        "method": "thread/start",
        "params": {
            "allowProviderModelFallback": False,
            "approvalPolicy": "never",
            "baseInstructions": DEFAULT_BASE_INSTRUCTIONS,
            "cwd": runtime_cwd,
            "developerInstructions": DEFAULT_DEVELOPER_INSTRUCTIONS,
            "dynamicTools": [],
            "environments": [],
            "ephemeral": True,
            "experimentalRawEvents": True,
            "model": requested_model,
            "modelProvider": requested_provider,
            "personality": "none",
            "runtimeWorkspaceRoots": [],
            "sandbox": "read-only",
            "selectedCapabilityRoots": [],
        },
    }
    turn_request = {
        "id": 3,
        "method": "turn/start",
        "params": {
            "approvalPolicy": "never",
            "effort": requested_effort,
            "environments": [],
            "input": [{"text": prompt, "type": "text"}],
            "model": requested_model,
            "outputSchema": output_schema,
            "personality": "none",
            "threadId": thread_id,
        },
    }
    messages = [
        {
            "id": 1,
            "result": {"userAgent": "private-codec-test", "zero": 0},
        },
        {"method": "thread/started", "params": {"thread": {"id": thread_id}}},
        {"id": 2, "result": thread_start_result},
        {"id": 3, "result": {"turn": {"id": turn_id}}},
        {
            "method": "turn/started",
            "params": {
                "threadId": thread_id,
                "turn": {"id": turn_id, "items": [], "status": "inProgress"},
            },
        },
        {
            "method": "rawResponseItem/completed",
            "params": {
                "threadId": thread_id,
                "turnId": turn_id,
                "item": {
                    "type": "message",
                    "role": "developer",
                    "content": [
                        {"type": "input_text", "text": DEFAULT_BASE_INSTRUCTIONS},
                        {
                            "type": "input_text",
                            "text": DEFAULT_DEVELOPER_INSTRUCTIONS,
                        },
                    ],
                },
            },
        },
        {
            "method": "rawResponseItem/completed",
            "params": {
                "threadId": thread_id,
                "turnId": turn_id,
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                },
            },
        },
        {
            "method": "item/started",
            "params": {
                "item": {"id": message_id, "type": "agentMessage"},
                "threadId": thread_id,
                "turnId": turn_id,
            },
        },
        {
            "method": "item/completed",
            "params": {
                "item": {
                    "id": message_id,
                    "phase": "final_answer",
                    "text": output_text,
                    "type": "agentMessage",
                },
                "threadId": thread_id,
                "turnId": turn_id,
            },
        },
        {
            "method": "turn/completed",
            "params": {
                "threadId": thread_id,
                "turn": {"id": turn_id, "items": [], "status": "completed"},
            },
        },
    ]
    transcript = b"".join(
        json.dumps(message, ensure_ascii=True, separators=(",", ":")).encode("utf-8") + b"\n"
        for message in messages
    )
    stderr = b"\x00\xffprivate diagnostic\r\n"
    audit = audit_app_server_messages(
        messages,
        requested_model=requested_model,
        requested_effort=requested_effort,
        requested_provider=requested_provider,
        expected_thread_id=thread_id,
        expected_turn_id=turn_id,
        thread_start_result=thread_start_result,
        process_exit_code=0,
        forced_termination=False,
        expected_model_inputs=(
            ("developer", DEFAULT_BASE_INSTRUCTIONS),
            ("developer", DEFAULT_DEVELOPER_INSTRUCTIONS),
            ("user", prompt),
        ),
    )
    executable_bytes = b"MZ\x00\x00private-codec-native"
    return CodexRunResult(
        requested_model=requested_model,
        requested_effort=requested_effort,
        requested_provider=requested_provider,
        prompt_text=prompt,
        output_schema_json=canonical_json(output_schema),
        launch_command_json=canonical_json(
            [
                executable_path,
                *codex_runner._app_server_command_tail(
                    requested_model,
                    requested_effort,
                ),
            ]
        ),
        executable_path=executable_path,
        executable_size_bytes=len(executable_bytes),
        executable_sha256_digest=digest(executable_bytes),
        executable_lock_enforced=True,
        standard_popen_used=False,
        solver_process_isolation_policy_digest=(
            codex_runner.solver_process_isolation_policy_digest()
        ),
        codex_home_directory_device=13,
        codex_home_directory_inode=17,
        codex_home_path_digest=None,
        codex_home_initial_auth_only=False,
        codex_home_cleanup_verified=True,
        credential_leak_scan_passed=True,
        command_tail_policy_digest=app_server_command_tail_policy_digest(
            requested_model,
            requested_effort,
        ),
        initialize_request_json=locked_initialize_request_json(),
        initialized_notification_json=request_bytes({"method": "initialized", "params": {}}),
        thread_start_request_json=request_bytes(thread_request),
        turn_start_request_json=request_bytes(turn_request),
        execution_nonce="1" * 64,
        timeout_seconds=180,
        runtime_directory_device=7,
        runtime_directory_inode=11,
        thread_id=thread_id,
        turn_id=turn_id,
        output_text=output_text,
        messages=tuple(messages),
        transcript_jsonl=transcript,
        transcript_hash=digest(transcript),
        stderr_bytes=stderr,
        stderr_hash=digest(stderr),
        stderr_excerpt=stderr.decode("utf-8", errors="replace")[-4_000:],
        process_exit_code=0,
        forced_termination=False,
        elapsed_seconds=1.25,
        audit=audit,
    )


def test_private_artifact_round_trips_every_constructor_field_exactly(
    artifact: CodexRunResult,
) -> None:
    encoded = serialize_codex_run_result(artifact)
    restored = deserialize_codex_run_result(encoded)
    assert serialize_codex_run_result(restored) == encoded
    for field in fields(CodexRunResult):
        assert getattr(restored, field.name) == getattr(artifact, field.name)

    envelope = json.loads(encoded)
    assert envelope["privacy_classification"] == PRIVATE_ARTIFACT_CLASSIFICATION
    assert set(envelope["result"]) == {field.name for field in fields(CodexRunResult)}
    assert envelope["result"]["stderr_bytes"]["encoding"] == "base64"
    assert base64.b64decode(envelope["result"]["stderr_bytes"]["data"]) == artifact.stderr_bytes


def test_private_artifact_rejects_noncanonical_and_duplicate_json(
    artifact: CodexRunResult,
) -> None:
    encoded = serialize_codex_run_result(artifact)
    with pytest.raises(CodexArtifactError, match="not canonical"):
        deserialize_codex_run_result(encoded[:-1] + b" }")
    duplicate = b'{"schema":"duplicate",' + encoded[1:]
    with pytest.raises(CodexArtifactError, match="duplicate object key"):
        deserialize_codex_run_result(duplicate)


def test_private_artifact_detects_content_tamper_before_reconstruction(
    artifact: CodexRunResult,
) -> None:
    envelope = json.loads(serialize_codex_run_result(artifact))
    envelope["result"]["prompt_text"] = "tampered private prompt"
    with pytest.raises(CodexArtifactError, match="content digest does not match"):
        deserialize_codex_run_result(canonical_json(envelope))


def test_private_artifact_rejects_resealed_invalid_base64(
    artifact: CodexRunResult,
) -> None:
    envelope = json.loads(serialize_codex_run_result(artifact))
    envelope["result"]["stderr_bytes"]["data"] = "***not-base64***"
    with pytest.raises(CodexArtifactError, match="stderr_bytes is not canonical base64"):
        deserialize_codex_run_result(reseal(envelope))


def test_private_artifact_replays_transcript_hash_and_audit_invariants(
    artifact: CodexRunResult,
) -> None:
    transcript_envelope = json.loads(serialize_codex_run_result(artifact))
    transcript_envelope["result"]["transcript_jsonl"]["data"] = base64.b64encode(
        artifact.transcript_jsonl + b'{"method":"forged"}\n'
    ).decode("ascii")
    with pytest.raises(CodexArtifactError, match="complete self-validation"):
        deserialize_codex_run_result(reseal(transcript_envelope))

    audit_envelope = json.loads(serialize_codex_run_result(artifact))
    audit_envelope["result"]["audit"]["final_message_count"] = 2
    with pytest.raises(CodexArtifactError, match="complete self-validation"):
        deserialize_codex_run_result(reseal(audit_envelope))


def test_private_artifact_rejects_schema_drift_and_field_changes(
    artifact: CodexRunResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = json.loads(serialize_codex_run_result(artifact))
    del missing["result"]["executable_sha256_digest"]
    with pytest.raises(CodexArtifactError, match="run-result fields differ"):
        deserialize_codex_run_result(reseal(missing))

    unknown = json.loads(serialize_codex_run_result(artifact))
    unknown["result"]["future_field"] = "unsafe"
    with pytest.raises(CodexArtifactError, match="run-result fields differ"):
        deserialize_codex_run_result(reseal(unknown))

    with monkeypatch.context() as patch:
        patch.setattr(artifact_io, "_RESULT_FIELDS", artifact_io._RESULT_FIELDS[:-1])
        with pytest.raises(CodexArtifactError, match="schema seal"):
            serialize_codex_run_result(artifact)

    changed_type = list(artifact_io._RESULT_SCHEMA)
    changed_type[0] = (changed_type[0][0], "bytes", changed_type[0][2])
    with monkeypatch.context() as patch:
        patch.setattr(artifact_io, "_RESULT_SCHEMA", tuple(changed_type))
        with pytest.raises(CodexArtifactError, match="codec schema version"):
            serialize_codex_run_result(artifact)


def test_private_artifact_requires_private_classification(
    artifact: CodexRunResult,
) -> None:
    envelope = json.loads(serialize_codex_run_result(artifact))
    envelope["privacy_classification"] = "public"
    with pytest.raises(CodexArtifactError, match="classification"):
        deserialize_codex_run_result(canonical_json(envelope))


def test_private_artifact_rejects_type_confusion_and_nonfinite_json(
    artifact: CodexRunResult,
) -> None:
    encoded = serialize_codex_run_result(artifact)
    with pytest.raises(CodexArtifactError, match="immutable bytes"):
        deserialize_codex_run_result(bytearray(encoded))  # type: ignore[arg-type]

    envelope = json.loads(encoded)
    envelope["schema_version"] = True
    with pytest.raises(CodexArtifactError, match="schema version"):
        deserialize_codex_run_result(canonical_json(envelope))

    version_two = json.loads(encoded)
    version_two["schema_version"] = 2
    with pytest.raises(CodexArtifactError, match="schema version"):
        deserialize_codex_run_result(canonical_json(version_two))

    nonfinite = encoded.replace(b'"elapsed_seconds":1.25', b'"elapsed_seconds":NaN')
    with pytest.raises(CodexArtifactError, match="invalid JSON constant"):
        deserialize_codex_run_result(nonfinite)


def test_private_artifact_enforces_encoded_size_limit(
    artifact: CodexRunResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoded = serialize_codex_run_result(artifact)
    monkeypatch.setattr(artifact_io, "MAX_PRIVATE_ARTIFACT_BYTES", len(encoded) - 1)
    with pytest.raises(CodexArtifactError, match="exceeds its encoded byte limit"):
        serialize_codex_run_result(artifact)
    with pytest.raises(CodexArtifactError, match="byte length is outside its limit"):
        deserialize_codex_run_result(encoded)


def test_private_artifact_limit_matches_attempt_ledger_contract() -> None:
    assert MAX_PRIVATE_ARTIFACT_BYTES == 64 * 1024 * 1024


@pytest.mark.parametrize(
    ("field", "replacement"),
    [("id", True), ("id", 1.0), ("zero", -0.0)],
)
def test_private_artifact_rejects_python_equality_normalization(
    artifact: CodexRunResult,
    field: str,
    replacement: object,
) -> None:
    envelope = json.loads(serialize_codex_run_result(artifact))
    if field == "id":
        envelope["result"]["messages"][0]["id"] = replacement
    else:
        envelope["result"]["messages"][0]["result"][field] = replacement
    with pytest.raises(CodexArtifactError, match="changed during invariant reconstruction"):
        deserialize_codex_run_result(reseal(envelope))


@pytest.mark.parametrize(
    ("field", "replacement"),
    [("id", True), ("id", 1.0), ("experimentalApi", 1)],
)
def test_private_artifact_rejects_type_confusion_in_initialize_request(
    artifact: CodexRunResult,
    field: str,
    replacement: object,
) -> None:
    request = json.loads(artifact.initialize_request_json)
    if field == "id":
        request["id"] = replacement
    else:
        request["params"]["capabilities"][field] = replacement
    envelope = json.loads(serialize_codex_run_result(artifact))
    envelope["result"]["initialize_request_json"]["data"] = base64.b64encode(
        canonical_json(request)
    ).decode("ascii")
    with pytest.raises(CodexArtifactError, match="complete self-validation"):
        deserialize_codex_run_result(reseal(envelope))


@pytest.mark.parametrize(("request_field", "replacement"), [("thread", 2.0), ("turn", 3.0)])
def test_private_artifact_rejects_float_request_ids(
    artifact: CodexRunResult,
    request_field: str,
    replacement: float,
) -> None:
    request_name = f"{request_field}_start_request_json"
    request = json.loads(getattr(artifact, request_name))
    request["id"] = replacement
    confused = replace(artifact, **{request_name: canonical_json(request)})
    with pytest.raises(CodexArtifactError, match=f"{request_field}/start request id"):
        serialize_codex_run_result(confused)


def test_private_artifact_rejects_type_confused_output_schema(
    artifact: CodexRunResult,
) -> None:
    turn_request = json.loads(artifact.turn_start_request_json)
    turn_request["params"]["outputSchema"] = {"minimum": True}
    confused = replace(
        artifact,
        output_schema_json=canonical_json({"minimum": 1}),
        turn_start_request_json=canonical_json(turn_request),
    )
    with pytest.raises(CodexArtifactError, match="type-exact retained evidence"):
        serialize_codex_run_result(confused)


def test_private_artifact_wraps_overflow_and_excessive_nesting(
    artifact: CodexRunResult,
) -> None:
    envelope = json.loads(serialize_codex_run_result(artifact))
    envelope["result"]["elapsed_seconds"] = 10**400
    with pytest.raises(CodexArtifactError, match="complete self-validation"):
        deserialize_codex_run_result(reseal(envelope))

    nested = b'{"nested":' + (b"[" * 1_500) + b"0" + (b"]" * 1_500) + b"}"
    with pytest.raises(CodexArtifactError, match="nesting exceeds"):
        deserialize_codex_run_result(nested)
