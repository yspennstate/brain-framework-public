"""Canonical private persistence for complete ``CodexRunResult`` artifacts.

The encoded bytes contain raw prompts, transcripts, stderr, and model output.
They are private evaluation evidence and must stay in ignored local storage.
The envelope checksum detects accidental or unauthenticated byte tampering; it
is an integrity checksum, not a signature or an authorization boundary.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
from dataclasses import fields
from typing import Any, Mapping

from .codex_runner import (
    AppServerProtocolError,
    CodexRunResult,
    IsolationAudit,
    locked_initialize_request_json,
)


PRIVATE_ARTIFACT_SCHEMA = "org.math-research-brain/private-codex-run-result"
PRIVATE_ARTIFACT_SCHEMA_VERSION = 2
PRIVATE_ARTIFACT_CLASSIFICATION = "private_raw_evaluation_evidence"
# Keep this independent lower-layer cap aligned with attempt_ledger.MAX_PAYLOAD_BYTES.
MAX_PRIVATE_ARTIFACT_BYTES = 64 * 1024 * 1024

_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CONTENT_DIGEST_DOMAIN = b"math-brain/private-codex-run-result/v2\x00"
_MAX_JSON_NESTING = 128
_ENVELOPE_KEYS = {
    "content_digest",
    "privacy_classification",
    "result",
    "schema",
    "schema_version",
}
_RESULT_SCHEMA = (
    ("requested_model", "str", True),
    ("requested_effort", "str", True),
    ("requested_provider", "str", True),
    ("prompt_text", "str", True),
    ("output_schema_json", "bytes", True),
    ("launch_command_json", "bytes", True),
    ("executable_path", "str", True),
    ("executable_size_bytes", "int", True),
    ("executable_sha256_digest", "str", True),
    ("executable_lock_enforced", "bool", True),
    ("standard_popen_used", "bool", True),
    ("solver_process_isolation_policy_digest", "str", True),
    ("codex_home_directory_device", "int", True),
    ("codex_home_directory_inode", "int", True),
    ("codex_home_initial_auth_only", "bool", True),
    ("codex_home_cleanup_verified", "bool", True),
    ("credential_leak_scan_passed", "bool", True),
    ("command_tail_policy_digest", "str", True),
    ("initialize_request_json", "bytes", True),
    ("initialized_notification_json", "bytes", True),
    ("thread_start_request_json", "bytes", True),
    ("turn_start_request_json", "bytes", True),
    ("execution_nonce", "str", True),
    ("timeout_seconds", "int", True),
    ("runtime_directory_device", "int", True),
    ("runtime_directory_inode", "int", True),
    ("thread_id", "str", True),
    ("turn_id", "str", True),
    ("output_text", "str | None", True),
    ("messages", "tuple[Mapping[str, Any], ...]", True),
    ("transcript_jsonl", "bytes", True),
    ("transcript_hash", "str", True),
    ("stderr_bytes", "bytes", True),
    ("stderr_hash", "str", True),
    ("stderr_excerpt", "str", True),
    ("process_exit_code", "int", True),
    ("forced_termination", "bool", True),
    ("elapsed_seconds", "float", True),
    ("audit", "IsolationAudit", True),
)
_RESULT_FIELDS = tuple(name for name, _, _ in _RESULT_SCHEMA)
_BYTE_FIELDS = frozenset(
    {
        "output_schema_json",
        "launch_command_json",
        "initialize_request_json",
        "initialized_notification_json",
        "thread_start_request_json",
        "turn_start_request_json",
        "transcript_jsonl",
        "stderr_bytes",
    }
)
_AUDIT_SCHEMA = (
    ("eligible", "bool", True),
    ("reasons", "tuple[str, ...]", True),
    ("accepted_model", "str | None", True),
    ("accepted_effort", "str | None", True),
    ("accepted_provider", "str | None", True),
    ("model_identity_verified", "bool", True),
    ("reroutes", "tuple[tuple[str, str, str], ...]", True),
    ("final_message_count", "int", True),
)
_AUDIT_FIELDS = tuple(name for name, _, _ in _AUDIT_SCHEMA)


class CodexArtifactError(ValueError):
    """A private artifact is malformed, noncanonical, stale, or self-inconsistent."""


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _validate_json_value(value: object, *, depth: int = 0) -> None:
    if depth > _MAX_JSON_NESTING:
        raise CodexArtifactError("artifact JSON nesting exceeds its limit")
    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise CodexArtifactError("artifact contains a non-finite JSON number")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if type(key) is not str:
                raise CodexArtifactError("artifact contains a non-string object key")
            _validate_json_value(item, depth=depth + 1)
        return
    raise CodexArtifactError("artifact contains a non-JSON value")


def _decode_canonical_json(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes:
        raise CodexArtifactError("private artifact input must be immutable bytes")
    if not raw or len(raw) > MAX_PRIVATE_ARTIFACT_BYTES:
        raise CodexArtifactError("private artifact byte length is outside its limit")

    def reject_constant(value: str) -> None:
        raise CodexArtifactError(f"invalid JSON constant in artifact: {value}")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        decoded: dict[str, Any] = {}
        for key, value in pairs:
            if key in decoded:
                raise CodexArtifactError("private artifact contains a duplicate object key")
            decoded[key] = value
        return decoded

    try:
        decoded = raw.decode("utf-8", errors="strict")
        value = json.loads(
            decoded,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_constant,
        )
        _validate_json_value(value)
        if not isinstance(value, dict):
            raise CodexArtifactError("private artifact root must be an object")
        if _canonical_json_bytes(value) != raw:
            raise CodexArtifactError("private artifact JSON is not canonical")
    except CodexArtifactError:
        raise
    except RecursionError as error:
        raise CodexArtifactError("artifact JSON nesting exceeds its limit") from error
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        OverflowError,
        TypeError,
        ValueError,
    ) as error:
        raise CodexArtifactError("private artifact is not strict UTF-8 JSON") from error
    return value


def _assert_codec_schema_current() -> None:
    observed_result_schema = tuple(
        (field.name, str(field.type), field.init) for field in fields(CodexRunResult)
    )
    observed_audit_schema = tuple(
        (field.name, str(field.type), field.init) for field in fields(IsolationAudit)
    )
    if observed_result_schema != _RESULT_SCHEMA:
        raise CodexArtifactError("CodexRunResult changed without a codec schema version")
    if observed_audit_schema != _AUDIT_SCHEMA:
        raise CodexArtifactError("IsolationAudit changed without a codec schema version")
    if tuple(name for name, _, _ in _RESULT_SCHEMA) != _RESULT_FIELDS:
        raise CodexArtifactError("CodexRunResult field codec differs from its schema seal")
    if tuple(name for name, _, _ in _AUDIT_SCHEMA) != _AUDIT_FIELDS:
        raise CodexArtifactError("IsolationAudit field codec differs from its schema seal")


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_json(item) for item in value]
    _validate_json_value(value)
    return value


def _encode_bytes(value: object, label: str) -> dict[str, str]:
    if type(value) is not bytes:
        raise CodexArtifactError(f"{label} is not immutable bytes")
    return {
        "data": base64.b64encode(value).decode("ascii"),
        "encoding": "base64",
    }


def _decode_bytes(value: object, label: str) -> bytes:
    if not isinstance(value, dict) or set(value) != {"data", "encoding"}:
        raise CodexArtifactError(f"{label} byte wrapper fields differ from the schema")
    if value.get("encoding") != "base64" or type(value.get("data")) is not str:
        raise CodexArtifactError(f"{label} is not canonical base64")
    encoded = value["data"]
    try:
        raw = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as error:
        raise CodexArtifactError(f"{label} is not canonical base64") from error
    if base64.b64encode(raw).decode("ascii") != encoded:
        raise CodexArtifactError(f"{label} is not canonical base64")
    return raw


def _audit_to_json(audit: object) -> dict[str, object]:
    if type(audit) is not IsolationAudit:
        raise CodexArtifactError("run audit must be an exact IsolationAudit")
    return {
        "accepted_effort": audit.accepted_effort,
        "accepted_model": audit.accepted_model,
        "accepted_provider": audit.accepted_provider,
        "eligible": audit.eligible,
        "final_message_count": audit.final_message_count,
        "model_identity_verified": audit.model_identity_verified,
        "reasons": list(audit.reasons),
        "reroutes": [list(reroute) for reroute in audit.reroutes],
    }


def _audit_from_json(value: object) -> IsolationAudit:
    if not isinstance(value, dict) or set(value) != set(_AUDIT_FIELDS):
        raise CodexArtifactError("run audit fields differ from the codec schema")
    if type(value["eligible"]) is not bool:
        raise CodexArtifactError("run audit eligible flag must be a boolean")
    if type(value["model_identity_verified"]) is not bool:
        raise CodexArtifactError("run audit identity flag must be a boolean")
    if type(value["final_message_count"]) is not int or value["final_message_count"] < 0:
        raise CodexArtifactError("run audit final-message count is invalid")
    for label in ("accepted_model", "accepted_effort", "accepted_provider"):
        if value[label] is not None and type(value[label]) is not str:
            raise CodexArtifactError(f"run audit {label} must be text or null")
    reasons = value["reasons"]
    if not isinstance(reasons, list) or any(type(reason) is not str for reason in reasons):
        raise CodexArtifactError("run audit reasons must be a string array")
    reroutes = value["reroutes"]
    if not isinstance(reroutes, list) or any(
        not isinstance(reroute, list)
        or len(reroute) != 3
        or any(type(part) is not str for part in reroute)
        for reroute in reroutes
    ):
        raise CodexArtifactError("run audit reroutes must be string triples")
    return IsolationAudit(
        eligible=value["eligible"],
        reasons=tuple(reasons),
        accepted_model=value["accepted_model"],
        accepted_effort=value["accepted_effort"],
        accepted_provider=value["accepted_provider"],
        model_identity_verified=value["model_identity_verified"],
        reroutes=tuple(tuple(reroute) for reroute in reroutes),
        final_message_count=value["final_message_count"],
    )


def _assert_type_sensitive_request_evidence(result: CodexRunResult) -> None:
    expected_initialized = _canonical_json_bytes(
        {"method": "initialized", "params": {}}
    )
    if result.initialize_request_json != locked_initialize_request_json():
        raise CodexArtifactError("initialize request is not type-exact locked evidence")
    if result.initialized_notification_json != expected_initialized:
        raise CodexArtifactError("initialized notification differs from locked evidence")

    thread_request = _decode_canonical_json(result.thread_start_request_json)
    turn_request = _decode_canonical_json(result.turn_start_request_json)
    if type(thread_request.get("id")) is not int or thread_request["id"] != 2:
        raise CodexArtifactError("thread/start request id is not an exact integer")
    if type(turn_request.get("id")) is not int or turn_request["id"] != 3:
        raise CodexArtifactError("turn/start request id is not an exact integer")
    turn_params = turn_request.get("params")
    if not isinstance(turn_params, dict) or "outputSchema" not in turn_params:
        raise CodexArtifactError("turn/start output schema evidence is missing")
    if _canonical_json_bytes(turn_params["outputSchema"]) != result.output_schema_json:
        raise CodexArtifactError(
            "turn/start output schema is not type-exact retained evidence"
        )


def _result_to_json(result: CodexRunResult) -> dict[str, object]:
    _assert_type_sensitive_request_evidence(result)
    encoded: dict[str, object] = {}
    for field_name in _RESULT_FIELDS:
        value = getattr(result, field_name)
        if field_name in _BYTE_FIELDS:
            encoded[field_name] = _encode_bytes(value, field_name)
        elif field_name == "messages":
            encoded[field_name] = _thaw_json(value)
        elif field_name == "audit":
            encoded[field_name] = _audit_to_json(value)
        else:
            _validate_json_value(value)
            encoded[field_name] = value
    return encoded


def _result_from_json(value: object) -> CodexRunResult:
    if not isinstance(value, dict) or set(value) != set(_RESULT_FIELDS):
        raise CodexArtifactError("run-result fields differ from the codec schema")
    decoded: dict[str, object] = {}
    for field_name in _RESULT_FIELDS:
        field_value = value[field_name]
        if field_name in _BYTE_FIELDS:
            decoded[field_name] = _decode_bytes(field_value, field_name)
        elif field_name == "messages":
            if not isinstance(field_value, list) or any(
                not isinstance(message, dict) for message in field_value
            ):
                raise CodexArtifactError("run messages must be an object array")
            decoded[field_name] = tuple(field_value)
        elif field_name == "audit":
            decoded[field_name] = _audit_from_json(field_value)
        else:
            decoded[field_name] = field_value
    try:
        restored = CodexRunResult(**decoded)  # type: ignore[arg-type]
    except (
        AppServerProtocolError,
        OverflowError,
        RecursionError,
        TypeError,
        ValueError,
    ) as error:
        raise CodexArtifactError(
            "persisted Codex run result failed complete self-validation"
        ) from error
    restored_payload = _result_to_json(restored)
    if _canonical_json_bytes(restored_payload) != _canonical_json_bytes(value):
        raise CodexArtifactError(
            "persisted Codex run result changed during invariant reconstruction"
        )
    return restored


def _content_digest(result_payload: Mapping[str, object]) -> str:
    digest = hashlib.sha256(
        _CONTENT_DIGEST_DOMAIN + _canonical_json_bytes(result_payload)
    ).hexdigest()
    return "sha256:" + digest


def serialize_codex_run_result(result: CodexRunResult) -> bytes:
    """Encode one complete self-checking run as canonical private JSON bytes."""

    _assert_codec_schema_current()
    if type(result) is not CodexRunResult:
        raise CodexArtifactError("only an exact CodexRunResult may be serialized")
    result_payload = _result_to_json(result)
    envelope = {
        "content_digest": _content_digest(result_payload),
        "privacy_classification": PRIVATE_ARTIFACT_CLASSIFICATION,
        "result": result_payload,
        "schema": PRIVATE_ARTIFACT_SCHEMA,
        "schema_version": PRIVATE_ARTIFACT_SCHEMA_VERSION,
    }
    _validate_json_value(envelope)
    encoded = _canonical_json_bytes(envelope)
    if len(encoded) > MAX_PRIVATE_ARTIFACT_BYTES:
        raise CodexArtifactError("private artifact exceeds its encoded byte limit")
    return encoded


def deserialize_codex_run_result(raw: bytes) -> CodexRunResult:
    """Decode canonical private JSON and rerun every ``CodexRunResult`` invariant."""

    _assert_codec_schema_current()
    envelope = _decode_canonical_json(raw)
    if set(envelope) != _ENVELOPE_KEYS:
        raise CodexArtifactError("private artifact envelope fields differ from the schema")
    if envelope.get("schema") != PRIVATE_ARTIFACT_SCHEMA:
        raise CodexArtifactError("private artifact schema identifier is unsupported")
    if type(envelope.get("schema_version")) is not int or envelope[
        "schema_version"
    ] != PRIVATE_ARTIFACT_SCHEMA_VERSION:
        raise CodexArtifactError("private artifact schema version is unsupported")
    if envelope.get("privacy_classification") != PRIVATE_ARTIFACT_CLASSIFICATION:
        raise CodexArtifactError("private artifact classification is missing or unsafe")
    result_payload = envelope.get("result")
    if not isinstance(result_payload, dict):
        raise CodexArtifactError("private artifact result payload must be an object")
    supplied_digest = envelope.get("content_digest")
    if type(supplied_digest) is not str or _DIGEST_PATTERN.fullmatch(supplied_digest) is None:
        raise CodexArtifactError("private artifact content digest is malformed")
    expected_digest = _content_digest(result_payload)
    if not hmac.compare_digest(supplied_digest, expected_digest):
        raise CodexArtifactError("private artifact content digest does not match")
    return _result_from_json(result_payload)


__all__ = [
    "CodexArtifactError",
    "MAX_PRIVATE_ARTIFACT_BYTES",
    "PRIVATE_ARTIFACT_CLASSIFICATION",
    "PRIVATE_ARTIFACT_SCHEMA",
    "PRIVATE_ARTIFACT_SCHEMA_VERSION",
    "deserialize_codex_run_result",
    "serialize_codex_run_result",
]
