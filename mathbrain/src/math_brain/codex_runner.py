"""Fail-closed Codex app-server transport for blinded mathematical evaluations.

The transport owns one fresh app-server process for one solver turn.  It does
not persist transcripts or decide whether the mathematical brain helped; those
responsibilities belong to the sealed evaluation layer.  The app-server event
stream is retained so callers can prove that a run did not use tools, spawn
subagents, or silently reroute the requested model.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import queue
import re
import secrets
import shutil
import stat
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path, PureWindowsPath
from types import MappingProxyType
from typing import Any, BinaryIO

SHA256_PREFIX = "sha256:"
SAFE_SELECTOR_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")
CODEX_UUID7_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
CODEX_UUID7_WIRE_EXAMPLE = "00000000-0000-7000-8000-000000000000"
FORBIDDEN_ITEM_TYPES = frozenset(
    {
        "commandExecution",
        "fileChange",
        "mcpToolCall",
        "dynamicToolCall",
        "collabAgentToolCall",
        "subAgentActivity",
        "webSearch",
        "imageView",
        "sleep",
        "imageGeneration",
        "enteredReviewMode",
        "exitedReviewMode",
        "contextCompaction",
    }
)
ALLOWED_ITEM_TYPES = frozenset({"userMessage", "agentMessage", "reasoning"})
FORBIDDEN_RAW_ITEM_TYPES = frozenset(
    {
        "local_shell_call",
        "function_call",
        "function_call_output",
        "tool_search_call",
        "tool_search_output",
        "custom_tool_call",
        "custom_tool_call_output",
        "web_search_call",
        "image_generation_call",
        "compaction",
        "compaction_trigger",
        "context_compaction",
    }
)
ALLOWED_RAW_ITEM_TYPES = frozenset({"message", "reasoning"})
SUPPRESSED_AMBIENT_EVENT_METHODS = (
    "account/rateLimits/updated",
    "mcpServer/startupStatus/updated",
    "remoteControl/status/changed",
)
ALLOWED_EVENT_METHODS = frozenset(
    {
        *SUPPRESSED_AMBIENT_EVENT_METHODS,
        "thread/started",
        "thread/status/changed",
        "thread/tokenUsage/updated",
        "turn/started",
        "turn/completed",
        "item/started",
        "item/completed",
        "item/agentMessage/delta",
        "item/reasoning/summaryTextDelta",
        "item/reasoning/summaryPartAdded",
        "item/reasoning/textDelta",
        "model/verification",
        "rawResponseItem/completed",
    }
)
THREAD_AND_TURN_EVENT_METHODS = frozenset(
    {
        "thread/tokenUsage/updated",
        "item/started",
        "item/completed",
        "item/agentMessage/delta",
        "item/reasoning/summaryTextDelta",
        "item/reasoning/summaryPartAdded",
        "item/reasoning/textDelta",
        "model/verification",
        "rawResponseItem/completed",
    }
)
ITEM_DELTA_EVENT_METHODS = frozenset(
    {
        "item/agentMessage/delta",
        "item/reasoning/summaryTextDelta",
        "item/reasoning/summaryPartAdded",
        "item/reasoning/textDelta",
    }
)
DISABLED_FEATURES = (
    "apps",
    "auth_elicitation",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "computer_use",
    "enable_mcp_apps",
    "hooks",
    "image_generation",
    "in_app_browser",
    "memories",
    "multi_agent",
    "multi_agent_v2",
    "plugin_sharing",
    "plugins",
    "remote_plugin",
    "shell_tool",
    "standalone_web_search",
    "tool_suggest",
    "workspace_dependencies",
)
DEFAULT_BASE_INSTRUCTIONS = (
    "You are an independent mathematical proof auditor. Work only from the "
    "user-supplied problem views and reference packets. Return only the JSON "
    "required by the supplied output schema."
)
DEFAULT_DEVELOPER_INSTRUCTIONS = (
    "Do not use tools, files, network access, plugins, skills, memory, or "
    "subagents. Do not ask questions or reveal chain-of-thought."
)

# The desktop app emits additional model-visible context.  Its preimages may
# contain product-owned or machine-local material and therefore must never be
# tracked.  These UTF-8 SHA-256 commitments pin every byte after replacing only
# independently validated dynamic values.  Any other change fails closed.
_DESKTOP_PERMISSIONS_INPUT_DIGEST_V1 = (
    "sha256:4103287cd2f8e02c0c606bbbae018671bd04d7e7eeca9cbf862f8b53f9275313"
)
_DESKTOP_SKILLS_NORMALIZED_DIGEST_V1 = (
    "sha256:11c0b3927b296d8a012f12efc6e14f167dabba278c493f6999fbddbdcb31924c"
)
_DESKTOP_PRIMARY_AGENT_INPUT_DIGEST_V1 = (
    "sha256:fccb993bd0ff45747511e4c90dd1524d804d353084a0767e09684d10512c20fe"
)
_DESKTOP_MULTI_AGENT_INPUT_DIGEST_V1 = (
    "sha256:65523c71c4fa1ed65e831bd7a589a5c2c0d4a5f7dfac562e431e1a10937b374e"
)
_DESKTOP_ENVIRONMENT_NORMALIZED_DIGEST_V1 = (
    "sha256:61ace9c59d1534ac1eb0cfa7f514ec8cb773ad23aaced38d6764da48c882a177"
)
_DESKTOP_USER_AGENT_NORMALIZED_DIGEST_V1 = (
    "sha256:a3fc83eefbeb19eadf44e20647ed0dc5bf6f9c9af785e6e6d35cd590c87e63a5"
)
_DESKTOP_MODEL_VERIFICATIONS_DIGEST_V1 = (
    "sha256:f8ac73a7191b534a562dbd42c065e926fbc5eceb92a45cc27b68c99283669fc9"
)
_DESKTOP_SKILLS_HOME_SUBSTITUTION_COUNT_V1 = 5
_DESKTOP_ENVIRONMENT_DATE_SUBSTITUTION_COUNT_V1 = 1
MODEL_INPUT_PROFILE_EXACT_V1 = "exact-v1"
MODEL_INPUT_PROFILE_DESKTOP_V1 = "desktop-v1"
MAX_AUTH_FILE_BYTES = 1_048_576
SOLVER_CHILD_ENVIRONMENT_NAMES = (
    "CODEX_HOME",
    "COMSPEC",
    "PATH",
    "PATHEXT",
    "SystemRoot",
    "TEMP",
    "TMP",
    "WINDIR",
)
_SOLVER_PROCESS_ISOLATION_POLICY = {
    "auth_copy": "bounded_regular_file_exclusive",
    "child_environment_names": list(SOLVER_CHILD_ENVIRONMENT_NAMES),
    "codex_home": "unique_auth_only_then_private_temp",
    "credential_scan": "all_long_auth_json_values_against_transcript_and_stderr",
    "global_environment_mutation": False,
    "home_cleanup": "recursive_and_absence_verified_before_return",
    "schema": "org.math-research-brain/solver-process-isolation-policy",
    "schema_version": 1,
}


def solver_process_isolation_policy_digest() -> str:
    """Return the public digest of the credential and child-env boundary."""

    return _sha256(_canonical_json_bytes(_SOLVER_PROCESS_ISOLATION_POLICY))


class AppServerError(RuntimeError):
    """Base class for transport, protocol, and isolation failures."""


class AppServerProtocolError(AppServerError):
    """The server stopped, timed out, or violated the JSONL protocol."""


@dataclass(frozen=True, slots=True)
class ExecutableProvenance:
    """Immutable identity of the native executable passed directly to Popen."""

    canonical_path: str
    size_bytes: int
    sha256_digest: str

    def __post_init__(self) -> None:
        if type(self.canonical_path) is not str or not self.canonical_path:
            raise ValueError("executable canonical path must be nonempty text")
        path = Path(self.canonical_path)
        if "\x00" in self.canonical_path or not path.is_absolute():
            raise ValueError("executable canonical path must be absolute")
        try:
            normalized = str(path.resolve(strict=False))
        except OSError as error:
            raise ValueError("executable canonical path cannot be normalized") from error
        if self.canonical_path != normalized:
            raise ValueError("executable canonical path is not normalized")
        if os.name == "nt" and path.suffix.lower() != ".exe":
            raise ValueError("executable canonical path must name a native .exe")
        if type(self.size_bytes) is not int or self.size_bytes <= 0:
            raise ValueError("executable size must be a positive exact integer")
        if type(self.sha256_digest) is not str or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", self.sha256_digest
        ):
            raise ValueError("executable digest must be canonical SHA-256")

    def to_dict(self) -> dict[str, object]:
        return {
            "canonical_path": self.canonical_path,
            "sha256_digest": self.sha256_digest,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class _ExecutableSnapshot:
    provenance: ExecutableProvenance
    device: int
    inode: int
    modified_ns: int


@dataclass(slots=True)
class _ExecutableLease:
    snapshot: _ExecutableSnapshot | None
    lock_enforced: bool
    handle: int | None = None
    file_descriptor: int | None = None

    def close(self) -> None:
        if self.handle is not None:
            import ctypes

            handle = self.handle
            self.handle = None
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int
            kernel32.CloseHandle(ctypes.c_void_p(handle))
        if self.file_descriptor is not None:
            descriptor = self.file_descriptor
            self.file_descriptor = None
            os.close(descriptor)


def _require_exact_int(value: object, label: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{label} must be an exact integer")
    return value


@dataclass(frozen=True, slots=True)
class CodexRunConfig:
    """Immutable request and resource policy for one solver process."""

    cwd: Path
    model: str = "gpt-5.6-sol"
    effort: str = "ultra"
    model_provider: str = "openai"
    timeout_seconds: int = 900
    max_event_line_bytes: int = 2_000_000
    max_transcript_bytes: int = 32_000_000
    max_stderr_bytes: int = 1_000_000
    executable: str | None = None
    expected_executable_provenance: ExecutableProvenance | None = None
    auth_file: Path | None = None
    auth_bytes: bytes | None = field(default=None, repr=False, compare=False)
    base_instructions: str = DEFAULT_BASE_INSTRUCTIONS
    developer_instructions: str = DEFAULT_DEVELOPER_INSTRUCTIONS

    def __post_init__(self) -> None:
        raw_cwd = Path(self.cwd)
        if raw_cwd.is_absolute():
            try:
                raw_cwd = raw_cwd.resolve(strict=True)
            except OSError:
                raw_cwd = raw_cwd.resolve(strict=False)
        object.__setattr__(self, "cwd", raw_cwd)
        for label, value in (
            ("model", self.model),
            ("effort", self.effort),
            ("model_provider", self.model_provider),
        ):
            if type(value) is not str or not SAFE_SELECTOR_RE.fullmatch(value):
                raise ValueError(f"unsafe {label} selector")
        timeout_seconds = _require_exact_int(self.timeout_seconds, "timeout_seconds")
        max_event_line_bytes = _require_exact_int(self.max_event_line_bytes, "max_event_line_bytes")
        max_transcript_bytes = _require_exact_int(self.max_transcript_bytes, "max_transcript_bytes")
        max_stderr_bytes = _require_exact_int(self.max_stderr_bytes, "max_stderr_bytes")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_event_line_bytes < 1_024:
            raise ValueError("max_event_line_bytes is too small")
        if max_transcript_bytes < max_event_line_bytes:
            raise ValueError("transcript budget must cover at least one event line")
        if max_stderr_bytes < 1_024:
            raise ValueError("max_stderr_bytes is too small")
        if (
            type(self.base_instructions) is not str
            or type(self.developer_instructions) is not str
            or not self.base_instructions.strip()
            or not self.developer_instructions.strip()
        ):
            raise ValueError("both instruction layers are required")
        if self.executable is not None:
            if type(self.executable) is not str or not self.executable:
                raise ValueError("explicit executable must be nonempty text")
            if "\x00" in self.executable or not Path(self.executable).is_absolute():
                raise ValueError("explicit executable must be an absolute path")
        if self.expected_executable_provenance is not None:
            if type(self.expected_executable_provenance) is not ExecutableProvenance:
                raise ValueError("expected executable provenance must be exact")
            self.expected_executable_provenance.__post_init__()
            if self.executable != self.expected_executable_provenance.canonical_path:
                raise ValueError("expected executable provenance requires its exact explicit path")
        if self.auth_file is not None:
            auth_file = Path(self.auth_file)
            if not auth_file.is_absolute():
                raise ValueError("auth_file must be an absolute path")
            try:
                if auth_file.is_symlink():
                    raise ValueError("auth_file must be a regular non-symlink file")
                auth_file = auth_file.resolve(strict=True)
                auth_status = auth_file.lstat()
            except ValueError:
                raise
            except OSError as error:
                raise ValueError("auth_file cannot be inspected") from error
            if auth_file.name != "auth.json":
                raise ValueError("auth_file must be named auth.json")
            if auth_file.is_symlink() or not stat.S_ISREG(auth_status.st_mode):
                raise ValueError("auth_file must be a regular non-symlink file")
            if not 0 < auth_status.st_size <= MAX_AUTH_FILE_BYTES:
                raise ValueError("auth_file byte length is outside its limit")
            if any(
                (ancestor / ".git").exists() for ancestor in (auth_file.parent, *auth_file.parents)
            ):
                raise ValueError("auth_file must be outside every Git repository")
            object.__setattr__(self, "auth_file", auth_file)
        if self.auth_bytes is not None:
            if self.auth_file is not None:
                raise ValueError("auth_file and auth_bytes are mutually exclusive")
            if (
                type(self.auth_bytes) is not bytes
                or not 0 < len(self.auth_bytes) <= MAX_AUTH_FILE_BYTES
            ):
                raise ValueError("auth_bytes must be bounded immutable bytes")
            try:
                decoded_auth = _decode_json_value(self.auth_bytes)
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
                raise ValueError("auth_bytes are not strict JSON") from error
            if not isinstance(decoded_auth, Mapping):
                raise ValueError("auth_bytes root must be an object")
            object.__setattr__(self, "auth_bytes", bytes(self.auth_bytes))

    def validate_runtime(self) -> None:
        """Require an existing, empty, absolute directory outside the repository."""

        if not self.cwd.is_absolute() or not self.cwd.is_dir():
            raise ValueError("cwd must be an existing absolute directory")
        try:
            first_entry = next(self.cwd.iterdir(), None)
        except OSError as error:
            raise ValueError("cwd cannot be inspected") from error
        if first_entry is not None:
            raise ValueError("cwd must be empty before a blinded run")
        if any((ancestor / ".git").exists() for ancestor in (self.cwd, *self.cwd.parents)):
            raise ValueError("cwd must be outside every Git repository")

    def runtime_directory_identity(self) -> tuple[int, int]:
        self.validate_runtime()
        try:
            status = self.cwd.stat()
        except OSError as error:
            raise ValueError("cwd identity cannot be inspected") from error
        return int(status.st_dev), int(status.st_ino)


@dataclass(frozen=True, slots=True)
class IsolationAudit:
    """Fail-closed audit of accepted configuration and emitted events."""

    eligible: bool
    reasons: tuple[str, ...]
    accepted_model: str | None
    accepted_effort: str | None
    accepted_provider: str | None
    model_identity_verified: bool
    reroutes: tuple[tuple[str, str, str], ...]
    final_message_count: int


@dataclass(frozen=True, slots=True)
class CodexRunResult:
    """In-memory evidence from one fresh process and one fresh thread."""

    requested_model: str
    requested_effort: str
    requested_provider: str
    prompt_text: str
    output_schema_json: bytes
    launch_command_json: bytes
    executable_path: str
    executable_size_bytes: int
    executable_sha256_digest: str
    executable_lock_enforced: bool
    standard_popen_used: bool
    solver_process_isolation_policy_digest: str
    codex_home_directory_device: int
    codex_home_directory_inode: int
    codex_home_path_digest: str | None
    codex_home_initial_auth_only: bool
    codex_home_cleanup_verified: bool
    credential_leak_scan_passed: bool
    command_tail_policy_digest: str
    initialize_request_json: bytes
    initialized_notification_json: bytes
    thread_start_request_json: bytes
    turn_start_request_json: bytes
    execution_nonce: str
    timeout_seconds: int
    runtime_directory_device: int
    runtime_directory_inode: int
    thread_id: str
    turn_id: str
    output_text: str | None
    messages: tuple[Mapping[str, Any], ...]
    transcript_jsonl: bytes
    transcript_hash: str
    stderr_bytes: bytes
    stderr_hash: str
    stderr_excerpt: str
    process_exit_code: int
    forced_termination: bool
    elapsed_seconds: float
    audit: IsolationAudit

    def __post_init__(self) -> None:
        for label, value in (
            ("requested_model", self.requested_model),
            ("requested_effort", self.requested_effort),
            ("requested_provider", self.requested_provider),
        ):
            if type(value) is not str or not SAFE_SELECTOR_RE.fullmatch(value):
                raise AppServerProtocolError(f"invalid {label} in run result")
        if type(self.prompt_text) is not str or not self.prompt_text.strip():
            raise AppServerProtocolError("run prompt artifact must be non-empty text")
        if type(self.output_schema_json) is not bytes:
            raise AppServerProtocolError("run output-schema artifact must be immutable bytes")
        try:
            decoded_schema = _decode_json_object(self.output_schema_json)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise AppServerProtocolError("run output-schema artifact is invalid") from error
        canonical_schema = json.dumps(
            decoded_schema,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if self.output_schema_json != canonical_schema:
            raise AppServerProtocolError("run output-schema artifact is not canonical")
        launch_command = _decode_canonical_json_value(
            self.launch_command_json,
            "launch command",
        )
        try:
            executable_provenance = ExecutableProvenance(
                canonical_path=self.executable_path,
                size_bytes=self.executable_size_bytes,
                sha256_digest=self.executable_sha256_digest,
            )
        except ValueError as error:
            raise AppServerProtocolError("run executable provenance is invalid") from error
        if type(self.executable_lock_enforced) is not bool:
            raise AppServerProtocolError("executable lock evidence must be a boolean")
        if type(self.standard_popen_used) is not bool:
            raise AppServerProtocolError("Popen-factory evidence must be a boolean")
        if self.solver_process_isolation_policy_digest != solver_process_isolation_policy_digest():
            raise AppServerProtocolError("solver-process isolation policy digest is invalid")
        if (
            type(self.codex_home_directory_device) is not int
            or type(self.codex_home_directory_inode) is not int
            or self.codex_home_directory_device < 0
            or self.codex_home_directory_inode < 0
        ):
            raise AppServerProtocolError("Codex-home directory identity is invalid")
        if self.standard_popen_used:
            if (
                type(self.codex_home_path_digest) is not str
                or re.fullmatch(r"sha256:[0-9a-f]{64}", self.codex_home_path_digest) is None
            ):
                raise AppServerProtocolError(
                    "standard solver process lacks a Codex-home path digest"
                )
        elif self.codex_home_path_digest is not None:
            raise AppServerProtocolError(
                "nonstandard solver process cannot assert a Codex-home path digest"
            )
        for label, value in (
            ("codex_home_initial_auth_only", self.codex_home_initial_auth_only),
            ("codex_home_cleanup_verified", self.codex_home_cleanup_verified),
            ("credential_leak_scan_passed", self.credential_leak_scan_passed),
        ):
            if type(value) is not bool:
                raise AppServerProtocolError(f"{label} evidence must be a boolean")
        if self.standard_popen_used and (
            self.codex_home_directory_device <= 0
            or self.codex_home_directory_inode <= 0
            or not self.codex_home_initial_auth_only
        ):
            raise AppServerProtocolError("standard solver process lacks sterile-home evidence")
        expected_tail_digest = app_server_command_tail_policy_digest(
            self.requested_model,
            self.requested_effort,
        )
        if self.command_tail_policy_digest != expected_tail_digest:
            raise AppServerProtocolError("command-tail policy digest is invalid")
        if (
            not isinstance(launch_command, list)
            or not launch_command
            or any(type(part) is not str or not part for part in launch_command)
            or launch_command[0] != executable_provenance.canonical_path
            or tuple(launch_command[1:])
            != _app_server_command_tail(self.requested_model, self.requested_effort)
        ):
            raise AppServerProtocolError("launch command differs from the locked no-tool policy")
        _decode_canonical_request(
            self.initialize_request_json,
            "initialize",
        )
        initialized_notification = _decode_canonical_request(
            self.initialized_notification_json,
            "initialized",
        )
        if self.initialize_request_json != locked_initialize_request_json():
            raise AppServerProtocolError("initialize request differs from the locked protocol")
        if initialized_notification != {"method": "initialized", "params": {}}:
            raise AppServerProtocolError(
                "initialized notification differs from the locked protocol"
            )
        thread_request = _decode_canonical_request(
            self.thread_start_request_json,
            "thread/start",
        )
        turn_request = _decode_canonical_request(
            self.turn_start_request_json,
            "turn/start",
        )
        if type(self.execution_nonce) is not str or not re.fullmatch(
            r"[0-9a-f]{64}", self.execution_nonce
        ):
            raise AppServerProtocolError("execution nonce must be 256 bits of lowercase hex")
        if type(self.timeout_seconds) is not int or self.timeout_seconds <= 0:
            raise AppServerProtocolError("run timeout must be a positive exact integer")
        if (
            type(self.runtime_directory_device) is not int
            or self.runtime_directory_device < 0
            or type(self.runtime_directory_inode) is not int
            or self.runtime_directory_inode < 0
        ):
            raise AppServerProtocolError(
                "runtime directory identity must be exact nonnegative integers"
            )
        require_codex_uuid7(self.thread_id, "run-result thread identifier")
        require_codex_uuid7(self.turn_id, "run-result turn identifier")
        _validate_outbound_requests(
            thread_request,
            turn_request,
            requested_model=self.requested_model,
            requested_effort=self.requested_effort,
            requested_provider=self.requested_provider,
            prompt_text=self.prompt_text,
            output_schema=decoded_schema,
            thread_id=self.thread_id,
        )
        if type(self.transcript_jsonl) is not bytes or type(self.stderr_bytes) is not bytes:
            raise AppServerProtocolError("run artifacts must be immutable bytes")
        if self.transcript_hash != _sha256(self.transcript_jsonl):
            raise AppServerProtocolError("transcript hash does not match transcript bytes")
        if self.stderr_hash != _sha256(self.stderr_bytes):
            raise AppServerProtocolError("stderr hash does not match stderr bytes")
        if type(self.process_exit_code) is not int:
            raise AppServerProtocolError("process exit code must be an exact integer")
        if type(self.forced_termination) is not bool:
            raise AppServerProtocolError("forced_termination must be a boolean")
        if (
            isinstance(self.elapsed_seconds, bool)
            or not isinstance(self.elapsed_seconds, (int, float))
            or not math.isfinite(float(self.elapsed_seconds))
            or self.elapsed_seconds < 0
        ):
            raise AppServerProtocolError("elapsed_seconds must be finite and nonnegative")

        parsed_messages = _parse_transcript_jsonl(self.transcript_jsonl)
        if type(self.messages) is not tuple:
            raise AppServerProtocolError("messages must be a tuple")
        try:
            supplied_messages = tuple(_thaw_json(message) for message in self.messages)
        except (TypeError, ValueError) as error:
            raise AppServerProtocolError("messages are not JSON evidence") from error
        if supplied_messages != parsed_messages:
            raise AppServerProtocolError("messages do not match the transcript")

        responses = _validated_protocol_responses(parsed_messages)
        response_positions = {
            message["id"]: index
            for index, message in enumerate(parsed_messages)
            if "method" not in message and message.get("id") in {1, 2, 3}
        }
        if not (response_positions[1] < response_positions[2] < response_positions[3]):
            raise AppServerProtocolError("protocol responses violate request causality")
        thread_start_result = responses[2]
        thread = thread_start_result.get("thread")
        if not isinstance(thread, Mapping) or thread.get("id") != self.thread_id:
            raise AppServerProtocolError("thread_id does not match thread/start response")
        turn_start_result = responses[3]
        turn = turn_start_result.get("turn")
        if not isinstance(turn, Mapping) or turn.get("id") != self.turn_id:
            raise AppServerProtocolError("turn_id does not match turn/start response")

        agent_messages = _agent_messages(parsed_messages)
        derived_output = (
            agent_messages[0].get("text")
            if len(agent_messages) == 1 and isinstance(agent_messages[0].get("text"), str)
            else None
        )
        if self.output_text != derived_output:
            raise AppServerProtocolError("output_text does not match the transcript")
        derived_stderr_excerpt = self.stderr_bytes.decode("utf-8", errors="replace")[-4_000:]
        if self.stderr_excerpt != derived_stderr_excerpt:
            raise AppServerProtocolError("stderr excerpt does not match stderr bytes")

        derived_audit = audit_app_server_messages(
            parsed_messages,
            requested_model=self.requested_model,
            requested_effort=self.requested_effort,
            requested_provider=self.requested_provider,
            expected_thread_id=self.thread_id,
            expected_turn_id=self.turn_id,
            thread_start_result=thread_start_result,
            process_exit_code=self.process_exit_code,
            forced_termination=self.forced_termination,
            expected_model_inputs=(
                ("developer", thread_request["params"]["baseInstructions"]),
                ("developer", thread_request["params"]["developerInstructions"]),
                ("user", self.prompt_text),
            ),
            expected_model_input_profile=(
                MODEL_INPUT_PROFILE_DESKTOP_V1
                if self.standard_popen_used and self.codex_home_initial_auth_only
                else MODEL_INPUT_PROFILE_EXACT_V1
            ),
            expected_runtime_cwd=thread_request["params"]["cwd"],
            expected_codex_home_path_digest=self.codex_home_path_digest,
        )
        if self.audit != derived_audit:
            raise AppServerProtocolError("audit does not match transcript-derived audit")
        object.__setattr__(
            self,
            "messages",
            tuple(_deep_freeze_json(message) for message in parsed_messages),
        )
        object.__setattr__(self, "audit", derived_audit)

    @property
    def executable_provenance(self) -> ExecutableProvenance:
        return ExecutableProvenance(
            canonical_path=self.executable_path,
            size_bytes=self.executable_size_bytes,
            sha256_digest=self.executable_sha256_digest,
        )

    @property
    def protocol_execution_id(self) -> str:
        """Identify the server-bound turn without claiming local process freshness.

        Local metadata such as the launcher path, timeout, runtime-directory identity,
        stderr, timing, and execution nonce deliberately cannot mint another protocol
        identity. A crash-safe first-attempt ledger must prove local process freshness
        separately.
        """

        request_policy = dict(self.request_policy_material)
        request_policy.pop("timeout_seconds")
        material = {
            "output_schema_digest": _sha256(self.output_schema_json),
            "prompt_digest": _sha256(self.prompt_text.encode("utf-8")),
            "request_policy": request_policy,
            "thread_id": self.thread_id,
            "transcript_digest": self.transcript_hash,
            "turn_id": self.turn_id,
        }
        return _sha256(
            b"math-brain/codex-protocol-execution/v2\x00" + _canonical_json_bytes(material)
        )

    @property
    def execution_id(self) -> str:
        """Backward-compatible alias for the non-freshness protocol identity."""

        return self.protocol_execution_id

    @property
    def request_policy_material(self) -> dict[str, Any]:
        thread = _decode_canonical_request(
            self.thread_start_request_json,
            "thread/start",
        )["params"]
        turn = _decode_canonical_request(
            self.turn_start_request_json,
            "turn/start",
        )["params"]
        return {
            "base_instructions": thread["baseInstructions"],
            "developer_instructions": thread["developerInstructions"],
            "fixed_thread_policy": {
                "allow_provider_model_fallback": thread["allowProviderModelFallback"],
                "approval_policy": thread["approvalPolicy"],
                "dynamic_tools": thread["dynamicTools"],
                "environments": thread["environments"],
                "ephemeral": thread["ephemeral"],
                "experimental_raw_events": thread["experimentalRawEvents"],
                "sandbox": thread["sandbox"],
            },
            "fixed_turn_policy": {
                "approval_policy": turn["approvalPolicy"],
                "environments": turn["environments"],
            },
            "requested_model": self.requested_model,
            "requested_provider": self.requested_provider,
            "requested_reasoning_effort": self.requested_effort,
            "timeout_seconds": self.timeout_seconds,
        }

    @property
    def runtime_cwd(self) -> str:
        request = _decode_canonical_request(
            self.thread_start_request_json,
            "thread/start",
        )
        value = request["params"]["cwd"]
        assert isinstance(value, str)
        return value

    def require_eligible(self) -> CodexRunResult:
        if not self.audit.eligible:
            raise AppServerError("ineligible Codex run: " + ", ".join(self.audit.reasons))
        return self


def _sha256(data: bytes) -> str:
    return SHA256_PREFIX + hashlib.sha256(data).hexdigest()


def _matches_desktop_user_agent_commitment_v1(value: object) -> bool:
    if type(value) is not str or not value or "\x00" in value or len(value) > 512:
        return False
    build_matches = tuple(
        re.finditer(r"(?<=\.)[0-9]{1,10}(?=; [A-Za-z0-9_]{1,32}\))", value)
    )
    if len(build_matches) != 1:
        return False
    match = build_matches[0]
    normalized = value[: match.start()] + "<WINDOWS_BUILD>" + value[match.end() :]
    return (
        _sha256(normalized.encode("utf-8"))
        == _DESKTOP_USER_AGENT_NORMALIZED_DIGEST_V1
    )


def _matches_desktop_verification_commitment_v1(value: object) -> bool:
    if type(value) is not list or any(type(item) is not str for item in value):
        return False
    canonical = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return _sha256(canonical) == _DESKTOP_MODEL_VERIFICATIONS_DIGEST_V1


def _validate_json_value(value: object) -> None:
    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("non-finite JSON number")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("JSON object key is not a string")
            _validate_json_value(item)
        return
    raise ValueError("value is not JSON-compatible")


def _decode_json_value(raw: bytes) -> Any:
    decoded = raw.decode("utf-8", errors="strict")

    def reject_constant(value: str) -> None:
        raise ValueError(f"invalid JSON constant: {value}")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON object key: {key}")
            result[key] = item
        return result

    value = json.loads(
        decoded,
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_constant,
    )
    _validate_json_value(value)
    return value


def _decode_json_object(raw: bytes) -> dict[str, Any]:
    value = _decode_json_value(raw)
    if not isinstance(value, Mapping):
        raise ValueError("app-server event must be a JSON object")
    return dict(value)


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def encode_codex_app_server_message(value: Mapping[str, Any]) -> bytes:
    """Encode one exact outbound app-server JSONL record, including its LF."""

    if not isinstance(value, Mapping):
        raise ValueError("app-server outbound message must be an object")
    return _canonical_json_bytes(value) + b"\n"


def require_codex_uuid7(value: object, label: str) -> str:
    """Require the canonical UUIDv7 identifier promised by desktop app-server."""

    if type(label) is not str or not label:
        raise ValueError("Codex UUID label must be nonempty text")
    if type(value) is not str or CODEX_UUID7_RE.fullmatch(value) is None:
        raise AppServerProtocolError(f"{label} must be a canonical lowercase UUIDv7")
    return value


def build_locked_thread_start_request(
    *,
    model: str,
    model_provider: str,
    cwd: str,
    base_instructions: str,
    developer_instructions: str,
) -> dict[str, Any]:
    """Build the sole thread/start request used by the locked desktop transport."""

    return {
        "id": 2,
        "method": "thread/start",
        "params": {
            "allowProviderModelFallback": False,
            "approvalPolicy": "never",
            "baseInstructions": base_instructions,
            "cwd": cwd,
            "developerInstructions": developer_instructions,
            "dynamicTools": [],
            "environments": [],
            "ephemeral": True,
            "experimentalRawEvents": True,
            "model": model,
            "modelProvider": model_provider,
            "personality": "none",
            "runtimeWorkspaceRoots": [],
            "sandbox": "read-only",
            "selectedCapabilityRoots": [],
        },
    }


def build_locked_turn_start_request(
    *,
    model: str,
    effort: str,
    prompt: str,
    output_schema: Mapping[str, Any],
    thread_id: str,
) -> dict[str, Any]:
    """Build the sole turn/start request after validating the returned UUIDv7."""

    return {
        "id": 3,
        "method": "turn/start",
        "params": {
            "approvalPolicy": "never",
            "effort": effort,
            "environments": [],
            "input": [{"text": prompt, "type": "text"}],
            "model": model,
            "outputSchema": dict(output_schema),
            "personality": "none",
            "threadId": require_codex_uuid7(thread_id, "thread/start identifier"),
        },
    }


def _auth_string_values(value: object) -> tuple[bytes, ...]:
    values: list[bytes] = []

    def visit(item: object) -> None:
        if isinstance(item, Mapping):
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, str):
            encoded = item.encode("utf-8")
            if len(encoded) >= 16:
                values.append(encoded)

    visit(value)
    return tuple(sorted(set(values)))


@dataclass(slots=True)
class _SolverHomeLease:
    path: Path
    device: int
    inode: int
    auth_values: tuple[bytes, ...]

    def child_environment(self) -> dict[str, str]:
        temporary = self.path / "tmp"
        temporary.mkdir(mode=0o700)
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        system32 = str(Path(system_root) / "System32")
        environment = {
            "CODEX_HOME": str(self.path),
            "COMSPEC": str(Path(system32) / "cmd.exe"),
            "PATH": system32,
            "PATHEXT": ".COM;.EXE;.BAT;.CMD",
            "SystemRoot": system_root,
            "TEMP": str(temporary),
            "TMP": str(temporary),
            "WINDIR": system_root,
        }
        if tuple(sorted(environment)) != tuple(sorted(SOLVER_CHILD_ENVIRONMENT_NAMES)):
            raise AppServerProtocolError("solver child environment differs from policy")
        return environment

    def verify_no_credential_leak(self, *artifacts: bytes) -> None:
        for artifact in artifacts:
            if any(secret in artifact for secret in self.auth_values):
                raise AppServerProtocolError("solver output contained authentication material")

    def cleanup(self) -> None:
        try:
            status = self.path.lstat()
        except OSError as error:
            raise AppServerProtocolError(
                "sterile Codex home identity could not be revalidated"
            ) from error
        if (
            self.path.is_symlink()
            or not stat.S_ISDIR(status.st_mode)
            or int(status.st_dev) != self.device
            or int(status.st_ino) != self.inode
        ):
            raise AppServerProtocolError("sterile Codex home identity changed before cleanup")
        shutil.rmtree(self.path)
        if self.path.exists():
            raise AppServerProtocolError("sterile Codex home cleanup was not verified")


def _prepare_solver_home(config: CodexRunConfig) -> _SolverHomeLease:
    auth_file = config.auth_file
    if config.auth_bytes is not None:
        auth_bytes = config.auth_bytes
    elif auth_file is None:
        raise ValueError("standard Codex execution requires an explicit auth_file")
    else:
        try:
            before = auth_file.stat()
            with auth_file.open("rb") as source:
                opened = os.fstat(source.fileno())
                auth_bytes = source.read(MAX_AUTH_FILE_BYTES + 1)
            after = auth_file.stat()
        except OSError as error:
            raise ValueError("auth_file could not be read") from error
        stable_identity = (
            (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            )
            == (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
                opened.st_mtime_ns,
            )
            == (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            )
        )
        if not stable_identity or not 0 < len(auth_bytes) <= MAX_AUTH_FILE_BYTES:
            raise ValueError("auth_file changed or exceeded its byte limit")
    try:
        decoded_auth = _decode_json_value(auth_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise ValueError("auth_file is not strict JSON") from error
    if not isinstance(decoded_auth, Mapping):
        raise ValueError("auth_file root must be an object")

    home = config.cwd.parent / f".codex-home-{secrets.token_hex(16)}"
    try:
        home.mkdir(mode=0o700)
        destination = home / "auth.json"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        descriptor = os.open(destination, flags, 0o600)
        try:
            view = memoryview(auth_bytes)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short auth-file write")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        entries = tuple(home.iterdir())
        if len(entries) != 1 or entries[0].name != "auth.json":
            raise AppServerProtocolError("sterile Codex home was not auth-only")
        destination_status = destination.lstat()
        if destination.is_symlink() or not stat.S_ISREG(destination_status.st_mode):
            raise AppServerProtocolError("sterile auth copy is not a regular file")
        home_status = home.stat()
        return _SolverHomeLease(
            path=home,
            device=int(home_status.st_dev),
            inode=int(home_status.st_ino),
            auth_values=_auth_string_values(decoded_auth),
        )
    except BaseException:
        if home.exists():
            shutil.rmtree(home, ignore_errors=True)
        raise


def locked_initialize_request() -> dict[str, Any]:
    """Return a fresh copy of the evaluator's exact app-server initializer."""

    return {
        "id": 1,
        "method": "initialize",
        "params": {
            "capabilities": {
                "experimentalApi": True,
                "optOutNotificationMethods": list(SUPPRESSED_AMBIENT_EVENT_METHODS),
            },
            "clientInfo": {
                "name": "math-research-brain-evaluator",
                "title": "Math Research Brain Evaluator",
                "version": "0.1.0",
            },
        },
    }


def locked_initialize_request_json() -> bytes:
    """Return the type-sensitive canonical initializer evidence bytes."""

    return _canonical_json_bytes(locked_initialize_request())


def _decode_canonical_request(raw: bytes, label: str) -> dict[str, Any]:
    if type(raw) is not bytes:
        raise AppServerProtocolError(f"{label} request artifact must be immutable bytes")
    try:
        value = _decode_json_object(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise AppServerProtocolError(f"{label} request artifact is invalid") from error
    if raw != _canonical_json_bytes(value):
        raise AppServerProtocolError(f"{label} request artifact is not canonical")
    return value


def _decode_canonical_json_value(raw: bytes, label: str) -> Any:
    if type(raw) is not bytes:
        raise AppServerProtocolError(f"{label} artifact must be immutable bytes")
    try:
        value = _decode_json_value(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise AppServerProtocolError(f"{label} artifact is invalid") from error
    canonical = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if raw != canonical:
        raise AppServerProtocolError(f"{label} artifact is not canonical")
    return value


def _validate_outbound_requests(
    thread_request: Mapping[str, Any],
    turn_request: Mapping[str, Any],
    *,
    requested_model: str,
    requested_effort: str,
    requested_provider: str,
    prompt_text: str,
    output_schema: Mapping[str, Any],
    thread_id: str,
) -> None:
    if set(thread_request) != {"id", "method", "params"}:
        raise AppServerProtocolError("thread/start request fields differ from policy")
    thread = thread_request.get("params")
    if (
        thread_request.get("id") != 2
        or thread_request.get("method") != "thread/start"
        or not isinstance(thread, Mapping)
    ):
        raise AppServerProtocolError("thread/start request artifact is malformed")
    thread_keys = {
        "allowProviderModelFallback",
        "approvalPolicy",
        "baseInstructions",
        "cwd",
        "developerInstructions",
        "dynamicTools",
        "environments",
        "ephemeral",
        "experimentalRawEvents",
        "model",
        "modelProvider",
        "personality",
        "runtimeWorkspaceRoots",
        "sandbox",
        "selectedCapabilityRoots",
    }
    if set(thread) != thread_keys:
        raise AppServerProtocolError("thread/start policy fields differ from the allowlist")
    cwd = thread.get("cwd")
    if type(cwd) is not str or not Path(cwd).is_absolute():
        raise AppServerProtocolError("thread/start cwd must be an absolute path")
    if (
        thread.get("allowProviderModelFallback") is not False
        or thread.get("approvalPolicy") != "never"
        or type(thread.get("baseInstructions")) is not str
        or not thread["baseInstructions"].strip()
        or type(thread.get("developerInstructions")) is not str
        or not thread["developerInstructions"].strip()
        or thread.get("dynamicTools") != []
        or thread.get("environments") != []
        or thread.get("ephemeral") is not True
        or thread.get("experimentalRawEvents") is not True
        or thread.get("model") != requested_model
        or thread.get("modelProvider") != requested_provider
        or thread.get("personality") != "none"
        or thread.get("runtimeWorkspaceRoots") != []
        or thread.get("sandbox") != "read-only"
        or thread.get("selectedCapabilityRoots") != []
    ):
        raise AppServerProtocolError("thread/start request differs from the locked policy")

    if set(turn_request) != {"id", "method", "params"}:
        raise AppServerProtocolError("turn/start request fields differ from policy")
    turn = turn_request.get("params")
    if (
        turn_request.get("id") != 3
        or turn_request.get("method") != "turn/start"
        or not isinstance(turn, Mapping)
    ):
        raise AppServerProtocolError("turn/start request artifact is malformed")
    turn_keys = {
        "approvalPolicy",
        "effort",
        "environments",
        "input",
        "model",
        "outputSchema",
        "personality",
        "threadId",
    }
    if set(turn) != turn_keys:
        raise AppServerProtocolError("turn/start policy fields differ from the allowlist")
    if (
        turn.get("approvalPolicy") != "never"
        or turn.get("effort") != requested_effort
        or turn.get("environments") != []
        or turn.get("input") != [{"text": prompt_text, "type": "text"}]
        or turn.get("model") != requested_model
        or turn.get("outputSchema") != output_schema
        or turn.get("personality") != "none"
        or turn.get("threadId") != thread_id
    ):
        raise AppServerProtocolError("turn/start request differs from retained artifacts")


def _parse_transcript_jsonl(transcript: bytes) -> tuple[dict[str, Any], ...]:
    if not transcript:
        raise AppServerProtocolError("app-server transcript is empty")
    parsed: list[dict[str, Any]] = []
    for line in transcript.splitlines():
        if not line.strip():
            raise AppServerProtocolError("app-server transcript contains a blank event")
        try:
            parsed.append(_decode_json_object(line))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise AppServerProtocolError("invalid app-server JSONL transcript") from error
    return tuple(parsed)


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_json(item) for item in value]
    _validate_json_value(value)
    return value


def _deep_freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _deep_freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze_json(item) for item in value)
    _validate_json_value(value)
    return value


def _validated_protocol_responses(
    messages: Sequence[Mapping[str, Any]],
) -> dict[int, Mapping[str, Any]]:
    responses: dict[int, Mapping[str, Any]] = {}
    for message in messages:
        if "method" in message or "id" not in message:
            continue
        request_id = message.get("id")
        if type(request_id) is not int or request_id not in {1, 2, 3}:
            raise AppServerProtocolError("transcript contains a foreign response id")
        if request_id in responses:
            raise AppServerProtocolError("transcript contains a duplicate response id")
        if "error" in message:
            raise AppServerProtocolError("transcript contains a failed protocol response")
        result = message.get("result")
        if not isinstance(result, Mapping):
            raise AppServerProtocolError("protocol response has no object result")
        responses[request_id] = result
    if set(responses) != {1, 2, 3}:
        raise AppServerProtocolError("transcript is missing a protocol response")
    return responses


def _toml_string(value: str) -> str:
    """Encode an already validated selector as a TOML basic string."""

    if not SAFE_SELECTOR_RE.fullmatch(value):
        raise ValueError("unsafe TOML selector")
    return f'"{value}"'


def _is_native_binary_header(header: bytes) -> bool:
    if header.startswith(b"MZ") or header.startswith(b"\x7fELF"):
        return True
    return header[:4] in {
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
        b"\xfe\xed\xfa\xce",
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",
    }


def _snapshot_resolved_executable(path: Path) -> _ExecutableSnapshot:
    """Hash one regular native binary while checking its path and open handle agree."""

    try:
        with path.open("rb") as stream:
            opened_before = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened_before.st_mode):
                raise AppServerProtocolError("Codex executable is not a regular file")
            digest = hashlib.sha256()
            header = b""
            while True:
                block = stream.read(1_048_576)
                if not block:
                    break
                if not header:
                    header = block[:4]
                digest.update(block)
            opened_after = os.fstat(stream.fileno())
        path_after = path.stat()
    except (OSError, ValueError) as error:
        raise AppServerProtocolError("Codex executable cannot be inspected") from error
    opened_identity = (
        int(opened_before.st_dev),
        int(opened_before.st_ino),
        int(opened_before.st_size),
        int(opened_before.st_mtime_ns),
    )
    if opened_identity != (
        int(opened_after.st_dev),
        int(opened_after.st_ino),
        int(opened_after.st_size),
        int(opened_after.st_mtime_ns),
    ) or opened_identity != (
        int(path_after.st_dev),
        int(path_after.st_ino),
        int(path_after.st_size),
        int(path_after.st_mtime_ns),
    ):
        raise AppServerProtocolError("Codex executable changed while it was hashed")
    if opened_before.st_size <= 0 or not _is_native_binary_header(header):
        raise AppServerProtocolError("Codex executable is not a recognized native binary")
    return _ExecutableSnapshot(
        provenance=ExecutableProvenance(
            canonical_path=str(path),
            size_bytes=int(opened_before.st_size),
            sha256_digest=SHA256_PREFIX + digest.hexdigest(),
        ),
        device=int(opened_before.st_dev),
        inode=int(opened_before.st_ino),
        modified_ns=int(opened_before.st_mtime_ns),
    )


def _resolve_codex_executable_path(explicit: str | None) -> Path:
    if explicit is None:
        candidate = shutil.which("codex.exe" if os.name == "nt" else "codex")
        if not candidate:
            raise AppServerProtocolError("native Codex executable was not found")
    else:
        if type(explicit) is not str or not explicit or "\x00" in explicit:
            raise AppServerProtocolError("explicit Codex executable path is invalid")
        if not Path(explicit).is_absolute():
            raise AppServerProtocolError("explicit Codex executable path must be absolute")
        candidate = explicit
    try:
        path = Path(candidate).resolve(strict=True)
    except OSError as error:
        raise AppServerProtocolError("native Codex executable was not found") from error
    if os.name == "nt" and path.suffix.lower() != ".exe":
        raise AppServerProtocolError("Codex executable must be a native .exe, not a shim")
    if os.name != "nt" and not os.access(path, os.X_OK):
        raise AppServerProtocolError("Codex executable is not executable")
    return path


def resolve_codex_executable(explicit: str | None = None) -> str:
    """Resolve only a native binary; command, PowerShell, and JavaScript shims fail."""

    path = _resolve_codex_executable_path(explicit)
    _snapshot_resolved_executable(path)
    return str(path)


def snapshot_codex_executable(explicit: str | None = None) -> _ExecutableSnapshot:
    """Return a stable preflight snapshot for the exact Popen command target."""

    return _snapshot_resolved_executable(_resolve_codex_executable_path(explicit))


def capture_codex_executable_provenance(
    explicit: str | None = None,
) -> ExecutableProvenance:
    """Capture the public path/size/SHA tuple used in a runtime precommit."""

    return snapshot_codex_executable(explicit).provenance


def _lease_codex_executable(explicit: str | None) -> _ExecutableLease:
    """Hold the command target stable while it is hashed, launched, and rehashed."""

    path = _resolve_codex_executable_path(explicit)
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        kernel32.CreateFileW.restype = ctypes.c_void_p
        handle = kernel32.CreateFileW(
            str(path),
            0x80000000,  # GENERIC_READ
            0x00000001,  # FILE_SHARE_READ: deny later write and delete opens
            None,
            3,  # OPEN_EXISTING
            0x00000080,  # FILE_ATTRIBUTE_NORMAL
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if handle in {None, invalid_handle}:
            error = ctypes.WinError(ctypes.get_last_error())
            raise AppServerProtocolError(
                "Codex executable could not be locked against replacement"
            ) from error
        lease = _ExecutableLease(
            snapshot=None,
            lock_enforced=True,
            handle=int(handle),
        )
    else:
        try:
            descriptor = os.open(path, os.O_RDONLY)
        except OSError as error:
            raise AppServerProtocolError("Codex executable cannot be held open") from error
        lease = _ExecutableLease(
            snapshot=None,
            # An open descriptor or advisory lock does not prevent POSIX rename.
            lock_enforced=False,
            file_descriptor=descriptor,
        )
    try:
        snapshot = _snapshot_resolved_executable(path)
        if lease.file_descriptor is not None:
            held = os.fstat(lease.file_descriptor)
            if (
                int(held.st_dev),
                int(held.st_ino),
                int(held.st_size),
                int(held.st_mtime_ns),
            ) != (
                snapshot.device,
                snapshot.inode,
                snapshot.provenance.size_bytes,
                snapshot.modified_ns,
            ):
                raise AppServerProtocolError(
                    "Codex executable changed before its lease was established"
                )
        lease.snapshot = snapshot
        return lease
    except BaseException:
        lease.close()
        raise


def _app_server_command_tail(model: str, effort: str) -> tuple[str, ...]:
    command = [
        "app-server",
        "--stdio",
        "--strict-config",
        "-c",
        f"model={_toml_string(model)}",
        "-c",
        f"model_reasoning_effort={_toml_string(effort)}",
        "-c",
        'approval_policy="never"',
        "-c",
        'sandbox_mode="read-only"',
        "-c",
        'web_search="disabled"',
        "-c",
        "mcp_servers={}",
        "-c",
        "project_doc_max_bytes=0",
        "-c",
        "project_doc_fallback_filenames=[]",
        "-c",
        'history.persistence="none"',
        "-c",
        "analytics.enabled=false",
    ]
    for feature in DISABLED_FEATURES:
        command.extend(("--disable", feature))
    return tuple(command)


def app_server_command_tail_policy_digest(model: str, effort: str) -> str:
    """Commit to every app-server argument except the separately bound executable."""

    material = json.dumps(
        list(_app_server_command_tail(model, effort)),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256(b"math-brain/codex-command-tail-policy/v1\x00" + material)


def build_app_server_command(config: CodexRunConfig) -> tuple[str, ...]:
    """Return the exact no-tool app-server command recorded by the run spec."""

    return (
        resolve_codex_executable(config.executable),
        *_app_server_command_tail(config.model, config.effort),
    )


def _event_params(message: Mapping[str, Any]) -> Mapping[str, Any]:
    params = message.get("params", {})
    return params if isinstance(params, Mapping) else {}


def _item_type(message: Mapping[str, Any]) -> str | None:
    params = _event_params(message)
    item = params.get("item")
    if isinstance(item, Mapping):
        value = item.get("type")
        return value if isinstance(value, str) else None
    return None


def _agent_messages(messages: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    results: list[Mapping[str, Any]] = []
    for message in messages:
        if message.get("method") != "item/completed":
            continue
        item = _event_params(message).get("item")
        if isinstance(item, Mapping) and item.get("type") == "agentMessage":
            results.append(item)
    return tuple(results)


def _required_id_reason(
    actual: object,
    expected: str,
    *,
    label: str,
    method: str,
) -> str | None:
    if type(actual) is not str or not actual:
        return f"missing_{label}_id:{method}"
    if actual != expected:
        return f"foreign_{label}_id:{method}"
    return None


def _event_binding_reasons(
    message: Mapping[str, Any],
    *,
    method: str,
    expected_thread_id: str,
    expected_turn_id: str,
) -> tuple[str, ...]:
    params = _event_params(message)
    reasons: list[str] = []
    if method == "thread/started":
        thread = params.get("thread")
        thread_id = thread.get("id") if isinstance(thread, Mapping) else None
        reason = _required_id_reason(thread_id, expected_thread_id, label="thread", method=method)
        if reason:
            reasons.append(reason)
    elif method in {"turn/started", "turn/completed"}:
        reason = _required_id_reason(
            params.get("threadId"),
            expected_thread_id,
            label="thread",
            method=method,
        )
        if reason:
            reasons.append(reason)
        turn = params.get("turn")
        turn_id = turn.get("id") if isinstance(turn, Mapping) else None
        reason = _required_id_reason(turn_id, expected_turn_id, label="turn", method=method)
        if reason:
            reasons.append(reason)
    elif method == "thread/status/changed" or method == "mcpServer/startupStatus/updated":
        reason = _required_id_reason(
            params.get("threadId"),
            expected_thread_id,
            label="thread",
            method=method,
        )
        if reason:
            reasons.append(reason)
    elif method in {
        "account/rateLimits/updated",
        "remoteControl/status/changed",
    }:
        # Process-level status notifications carry no solver thread or turn id.
        # Their exact inert payloads are checked by the main audit below.
        pass
    elif method in THREAD_AND_TURN_EVENT_METHODS or method == "model/rerouted":
        for wire_name, expected, label in (
            ("threadId", expected_thread_id, "thread"),
            ("turnId", expected_turn_id, "turn"),
        ):
            reason = _required_id_reason(
                params.get(wire_name), expected, label=label, method=method
            )
            if reason:
                reasons.append(reason)
    else:  # pragma: no cover - every allowlisted method has an explicit binding
        reasons.append(f"unbound_allowed_event:{method}")

    if method in {"item/started", "item/completed"}:
        item = params.get("item")
        item_id = item.get("id") if isinstance(item, Mapping) else None
        if type(item_id) is not str or not item_id:
            reasons.append(f"missing_item_id:{method}")
    elif method in ITEM_DELTA_EVENT_METHODS:
        if type(params.get("itemId")) is not str or not params.get("itemId"):
            reasons.append(f"missing_item_id:{method}")
    return tuple(reasons)


def _is_expected_turn_completion(
    message: Mapping[str, Any], *, expected_thread_id: str, expected_turn_id: str
) -> bool:
    if message.get("method") != "turn/completed":
        return False
    params = _event_params(message)
    turn = params.get("turn")
    return (
        params.get("threadId") == expected_thread_id
        and isinstance(turn, Mapping)
        and turn.get("id") == expected_turn_id
    )


def _audit_desktop_thread_response_v1(
    messages: Sequence[Mapping[str, Any]],
    *,
    thread_start_result: Mapping[str, Any],
    expected_runtime_cwd: str,
    expected_thread_id: str,
    requested_provider: str,
) -> tuple[str | None, tuple[str, ...]]:
    reasons: list[str] = []
    expected_result_keys = {
        "activePermissionProfile",
        "approvalPolicy",
        "approvalsReviewer",
        "cwd",
        "instructionSources",
        "model",
        "modelProvider",
        "multiAgentMode",
        "reasoningEffort",
        "runtimeWorkspaceRoots",
        "sandbox",
        "serviceTier",
        "thread",
    }
    if set(thread_start_result) != expected_result_keys:
        reasons.append("desktop_thread_response_fields")
    if (
        thread_start_result.get("cwd") != expected_runtime_cwd
        or thread_start_result.get("approvalPolicy") != "never"
        or thread_start_result.get("approvalsReviewer") != "user"
        or thread_start_result.get("serviceTier") is not None
        or thread_start_result.get("activePermissionProfile") is not None
        or thread_start_result.get("runtimeWorkspaceRoots") != []
        or thread_start_result.get("instructionSources") != []
        or thread_start_result.get("multiAgentMode") != "explicitRequestOnly"
        or thread_start_result.get("sandbox") != {"networkAccess": False, "type": "readOnly"}
    ):
        reasons.append("desktop_thread_response_policy")

    thread = thread_start_result.get("thread")
    expected_thread_keys = {
        "agentNickname",
        "agentRole",
        "cliVersion",
        "createdAt",
        "cwd",
        "ephemeral",
        "extra",
        "forkedFromId",
        "gitInfo",
        "historyMode",
        "id",
        "modelProvider",
        "name",
        "parentThreadId",
        "path",
        "preview",
        "recencyAt",
        "sessionId",
        "source",
        "status",
        "threadSource",
        "turns",
        "updatedAt",
    }
    environment_date: str | None = None
    if not isinstance(thread, Mapping) or set(thread) != expected_thread_keys:
        reasons.append("desktop_thread_object_shape")
    else:
        timestamps = (thread.get("createdAt"), thread.get("updatedAt"), thread.get("recencyAt"))
        timestamps_valid = all(type(value) is int and value > 0 for value in timestamps)
        if (
            thread.get("id") != expected_thread_id
            or thread.get("sessionId") != expected_thread_id
            or thread.get("cwd") != expected_runtime_cwd
            or thread.get("ephemeral") is not True
            or thread.get("modelProvider") != requested_provider
            or thread.get("historyMode") != "legacy"
            or thread.get("cliVersion") != "0.144.0-alpha.4"
            or thread.get("source") != "vscode"
            or thread.get("preview") != ""
            or thread.get("status") != {"type": "idle"}
            or thread.get("turns") != []
            or any(
                thread.get(field) is not None
                for field in (
                    "agentNickname",
                    "agentRole",
                    "extra",
                    "forkedFromId",
                    "gitInfo",
                    "name",
                    "parentThreadId",
                    "path",
                    "threadSource",
                )
            )
            or not timestamps_valid
            or not timestamps[0] <= timestamps[1] <= timestamps[2]
        ):
            reasons.append("desktop_thread_object_policy")
        if timestamps_valid:
            try:
                environment_date = datetime.fromtimestamp(timestamps[0]).date().isoformat()
            except (OSError, OverflowError, ValueError):
                reasons.append("desktop_thread_timestamp_invalid")

    thread_events = [message for message in messages if message.get("method") == "thread/started"]
    if len(thread_events) != 1:
        reasons.append("desktop_thread_started_cardinality")
    else:
        event = thread_events[0]
        params = event.get("params")
        if (
            set(event) != {"method", "params"}
            or not isinstance(params, Mapping)
            or set(params) != {"thread"}
            or params.get("thread") != thread
        ):
            reasons.append("desktop_thread_started_mismatch")
    return environment_date, tuple(dict.fromkeys(reasons))


def _audit_desktop_turn_and_user_lifecycle_v1(
    messages: Sequence[Mapping[str, Any]],
    input_event_indices: Sequence[int],
    *,
    expected_prompt: str,
    expected_thread_id: str,
    expected_turn_id: str,
) -> tuple[str, ...]:
    reasons: list[str] = []
    turn_completions = [
        message for message in messages if message.get("method") == "turn/completed"
    ]
    if len(turn_completions) != 1:
        reasons.append("desktop_turn_completed_cardinality")
    else:
        completion = turn_completions[0]
        completion_params = completion.get("params")
        completed_turn = (
            completion_params.get("turn") if isinstance(completion_params, Mapping) else None
        )
        if (
            set(completion) != {"method", "params"}
            or not isinstance(completion_params, Mapping)
            or set(completion_params) != {"threadId", "turn"}
            or completion_params.get("threadId") != expected_thread_id
            or not isinstance(completed_turn, Mapping)
            or set(completed_turn)
            != {
                "completedAt",
                "durationMs",
                "error",
                "id",
                "items",
                "itemsView",
                "startedAt",
                "status",
            }
            or completed_turn.get("id") != expected_turn_id
            or completed_turn.get("items") != []
            or completed_turn.get("itemsView") != "notLoaded"
            or completed_turn.get("status") != "completed"
            or completed_turn.get("error") is not None
            or type(completed_turn.get("startedAt")) is not int
            or completed_turn.get("startedAt") <= 0
            or type(completed_turn.get("completedAt")) is not int
            or completed_turn.get("completedAt") < completed_turn.get("startedAt")
            or type(completed_turn.get("durationMs")) is not int
            or completed_turn.get("durationMs") < 0
        ):
            reasons.append("desktop_turn_completed_shape")

    turn_responses = [
        message for message in messages if "method" not in message and message.get("id") == 3
    ]
    if len(turn_responses) != 1:
        reasons.append("desktop_turn_response_cardinality")
    else:
        result = turn_responses[0].get("result")
        turn = result.get("turn") if isinstance(result, Mapping) else None
        if (
            not isinstance(result, Mapping)
            or set(result) != {"turn"}
            or not isinstance(turn, Mapping)
            or set(turn)
            != {
                "completedAt",
                "durationMs",
                "error",
                "id",
                "items",
                "itemsView",
                "startedAt",
                "status",
            }
            or turn.get("id") != expected_turn_id
            or turn.get("items") != []
            or turn.get("itemsView") != "notLoaded"
            or turn.get("status") != "inProgress"
            or any(
                turn.get(field) is not None
                for field in ("completedAt", "durationMs", "error", "startedAt")
            )
        ):
            reasons.append("desktop_turn_response_shape")

    turn_started = [
        (index, message)
        for index, message in enumerate(messages)
        if message.get("method") == "turn/started"
    ]
    if len(turn_started) != 1:
        reasons.append("desktop_turn_started_cardinality")
        turn_started_index = None
    else:
        turn_started_index, event = turn_started[0]
        params = event.get("params")
        turn = params.get("turn") if isinstance(params, Mapping) else None
        if (
            set(event) != {"method", "params"}
            or not isinstance(params, Mapping)
            or set(params) != {"threadId", "turn"}
            or params.get("threadId") != expected_thread_id
            or not isinstance(turn, Mapping)
            or set(turn)
            != {
                "completedAt",
                "durationMs",
                "error",
                "id",
                "items",
                "itemsView",
                "startedAt",
                "status",
            }
            or turn.get("id") != expected_turn_id
            or turn.get("items") != []
            or turn.get("itemsView") != "notLoaded"
            or turn.get("status") != "inProgress"
            or type(turn.get("startedAt")) is not int
            or turn.get("startedAt") <= 0
            or any(turn.get(field) is not None for field in ("completedAt", "durationMs", "error"))
        ):
            reasons.append("desktop_turn_started_shape")

    user_started = [
        (index, message)
        for index, message in enumerate(messages)
        if message.get("method") == "item/started" and _item_type(message) == "userMessage"
    ]
    user_completed = [
        (index, message)
        for index, message in enumerate(messages)
        if message.get("method") == "item/completed" and _item_type(message) == "userMessage"
    ]
    if len(user_started) != 1 or len(user_completed) != 1:
        reasons.append("desktop_user_item_cardinality")
    else:
        started_index, started_event = user_started[0]
        completed_index, completed_event = user_completed[0]
        started_params = started_event.get("params")
        completed_params = completed_event.get("params")
        started_item = started_params.get("item") if isinstance(started_params, Mapping) else None
        completed_item = (
            completed_params.get("item") if isinstance(completed_params, Mapping) else None
        )
        expected_content = [{"text": expected_prompt, "text_elements": [], "type": "text"}]
        if (
            set(started_event) != {"method", "params"}
            or set(completed_event) != {"method", "params"}
            or not isinstance(started_params, Mapping)
            or not isinstance(completed_params, Mapping)
            or set(started_params) != {"item", "startedAtMs", "threadId", "turnId"}
            or set(completed_params) != {"completedAtMs", "item", "threadId", "turnId"}
            or started_params.get("threadId") != expected_thread_id
            or completed_params.get("threadId") != expected_thread_id
            or started_params.get("turnId") != expected_turn_id
            or completed_params.get("turnId") != expected_turn_id
            or type(started_params.get("startedAtMs")) is not int
            or type(completed_params.get("completedAtMs")) is not int
            or completed_params.get("completedAtMs") < started_params.get("startedAtMs")
            or not isinstance(started_item, Mapping)
            or set(started_item) != {"clientId", "content", "id", "type"}
            or started_item.get("type") != "userMessage"
            or type(started_item.get("id")) is not str
            or not started_item.get("id")
            or started_item.get("clientId") is not None
            or started_item.get("content") != expected_content
            or completed_item != started_item
        ):
            reasons.append("desktop_user_item_shape")
        if turn_started_index is not None:
            expected_inputs = list(range(turn_started_index + 1, turn_started_index + 6))
            if list(input_event_indices) != expected_inputs:
                reasons.append("desktop_model_input_lifecycle_position")
            if started_index != turn_started_index + 6 or completed_index != started_index + 1:
                reasons.append("desktop_user_item_lifecycle_position")
    return tuple(dict.fromkeys(reasons))


def _audit_desktop_assistant_output_v1(
    messages: Sequence[Mapping[str, Any]],
    assistant_output_events: Sequence[tuple[int, Mapping[str, Any]]],
    *,
    expected_thread_id: str,
    expected_turn_id: str,
) -> tuple[str, ...]:
    reasons: list[str] = []
    agent_started = [
        (index, message)
        for index, message in enumerate(messages)
        if message.get("method") == "item/started" and _item_type(message) == "agentMessage"
    ]
    agent_completed = [
        (index, message)
        for index, message in enumerate(messages)
        if message.get("method") == "item/completed" and _item_type(message) == "agentMessage"
    ]
    if len(agent_started) != 1 or len(agent_completed) != 1:
        reasons.append("desktop_agent_item_cardinality")
        return tuple(reasons)
    started_index, started_event = agent_started[0]
    completed_index, completed_event = agent_completed[0]
    started_params = started_event.get("params")
    completed_params = completed_event.get("params")
    started_item = started_params.get("item") if isinstance(started_params, Mapping) else None
    completed_item = completed_params.get("item") if isinstance(completed_params, Mapping) else None
    if (
        set(started_event) != {"method", "params"}
        or set(completed_event) != {"method", "params"}
        or not isinstance(started_params, Mapping)
        or not isinstance(completed_params, Mapping)
        or set(started_params) != {"item", "startedAtMs", "threadId", "turnId"}
        or set(completed_params) != {"completedAtMs", "item", "threadId", "turnId"}
        or started_params.get("threadId") != expected_thread_id
        or completed_params.get("threadId") != expected_thread_id
        or started_params.get("turnId") != expected_turn_id
        or completed_params.get("turnId") != expected_turn_id
        or type(started_params.get("startedAtMs")) is not int
        or type(completed_params.get("completedAtMs")) is not int
        or completed_params.get("completedAtMs") < started_params.get("startedAtMs")
        or not isinstance(started_item, Mapping)
        or set(started_item) != {"id", "memoryCitation", "phase", "text", "type"}
        or started_item.get("type") != "agentMessage"
        or type(started_item.get("id")) is not str
        or not started_item.get("id")
        or started_item.get("text") != ""
        or started_item.get("phase") != "final_answer"
        or started_item.get("memoryCitation") is not None
        or not isinstance(completed_item, Mapping)
        or set(completed_item) != {"id", "memoryCitation", "phase", "text", "type"}
        or completed_item.get("id") != started_item.get("id")
        or completed_item.get("type") != "agentMessage"
        or completed_item.get("phase") != "final_answer"
        or completed_item.get("memoryCitation") is not None
        or type(completed_item.get("text")) is not str
        or not started_index < completed_index
    ):
        reasons.append("desktop_agent_item_shape")
        return tuple(reasons)

    message_id = started_item["id"]
    final_text = completed_item["text"]
    deltas = [
        (index, message)
        for index, message in enumerate(messages)
        if message.get("method") == "item/agentMessage/delta"
    ]
    if not deltas:
        reasons.append("desktop_agent_delta_missing")
    else:
        delta_texts: list[str] = []
        for index, event in deltas:
            params = event.get("params")
            if (
                set(event) != {"method", "params"}
                or not isinstance(params, Mapping)
                or set(params) != {"delta", "itemId", "threadId", "turnId"}
                or params.get("threadId") != expected_thread_id
                or params.get("turnId") != expected_turn_id
                or params.get("itemId") != message_id
                or type(params.get("delta")) is not str
                or not started_index < index < completed_index
            ):
                reasons.append("desktop_agent_delta_shape")
                continue
            delta_texts.append(params["delta"])
        if "".join(delta_texts) != final_text:
            reasons.append("desktop_agent_delta_text_mismatch")

    if len(assistant_output_events) != 1:
        reasons.append("desktop_raw_assistant_cardinality")
    else:
        raw_index, raw_event = assistant_output_events[0]
        params = raw_event.get("params")
        item = params.get("item") if isinstance(params, Mapping) else None
        expected_content = [{"text": final_text, "type": "output_text"}]
        if (
            set(raw_event) != {"method", "params"}
            or not isinstance(params, Mapping)
            or set(params) != {"item", "threadId", "turnId"}
            or params.get("threadId") != expected_thread_id
            or params.get("turnId") != expected_turn_id
            or not isinstance(item, Mapping)
            or set(item)
            != {
                "content",
                "id",
                "internal_chat_message_metadata_passthrough",
                "phase",
                "role",
                "type",
            }
            or item.get("type") != "message"
            or item.get("role") != "assistant"
            or item.get("id") != message_id
            or item.get("phase") != "final_answer"
            or item.get("content") != expected_content
            or item.get("internal_chat_message_metadata_passthrough")
            != {"turn_id": expected_turn_id}
            or raw_index != completed_index + 1
        ):
            reasons.append("desktop_raw_assistant_shape")

    content_methods = {
        "item/started",
        "item/completed",
        "item/agentMessage/delta",
        "item/reasoning/summaryTextDelta",
        "item/reasoning/summaryPartAdded",
        "item/reasoning/textDelta",
        "model/verification",
        "rawResponseItem/completed",
    }
    for index, event in enumerate(messages):
        if index <= completed_index or event.get("method") not in content_methods:
            continue
        if (
            len(assistant_output_events) == 1
            and index == assistant_output_events[0][0]
            and index == completed_index + 1
        ):
            continue
        reasons.append("desktop_content_event_after_final_message")
    return tuple(dict.fromkeys(reasons))


def _audit_desktop_ambient_model_inputs_v1(
    messages: Sequence[Mapping[str, Any]],
    input_events: Sequence[Mapping[str, Any]],
    input_event_indices: Sequence[int],
    assistant_output_events: Sequence[tuple[int, Mapping[str, Any]]],
    *,
    expected_model_inputs: Sequence[tuple[str, str]],
    expected_runtime_cwd: str,
    expected_codex_home_path_digest: str,
    expected_thread_id: str,
    expected_turn_id: str,
    thread_start_result: Mapping[str, Any],
    requested_provider: str,
) -> tuple[str, ...]:
    """Validate one exact desktop-owned model-input profile.

    This is intentionally not a loose allowlist.  Order, event grouping, roles,
    part counts, metadata, and normalized text digests are pinned.  The tracked
    source contains no ambient-text preimages.  The only substitutions are the
    host-local ISO calendar date and the sterile Codex-home path returned by
    initialize, both of which are independently constrained.
    """

    reasons: list[str] = []
    desktop_response_ids: list[int] = []
    for message in messages:
        if not isinstance(message, Mapping):
            reasons.append("desktop_protocol_wrapper_fields")
        elif "method" in message:
            if set(message) != {"method", "params"}:
                reasons.append("desktop_protocol_wrapper_fields")
        else:
            response_id = message.get("id")
            if type(response_id) is int:
                desktop_response_ids.append(response_id)
            if set(message) != {"id", "result"}:
                reasons.append("desktop_protocol_wrapper_fields")
    if Counter(desktop_response_ids) != Counter({1: 1, 2: 1, 3: 1}):
        reasons.append("desktop_protocol_response_cardinality")
    if len(expected_model_inputs) != 3 or tuple(role for role, _ in expected_model_inputs) != (
        "developer",
        "developer",
        "user",
    ):
        return ("desktop_model_input_expectation_shape",)
    configured_developer = expected_model_inputs[1][1]
    expected_prompt = expected_model_inputs[2][1]
    environment_date, thread_reasons = _audit_desktop_thread_response_v1(
        messages,
        thread_start_result=thread_start_result,
        expected_runtime_cwd=expected_runtime_cwd,
        expected_thread_id=expected_thread_id,
        requested_provider=requested_provider,
    )
    reasons.extend(thread_reasons)

    initialize_responses = [
        message for message in messages if "method" not in message and message.get("id") == 1
    ]
    codex_home: str | None = None
    if len(initialize_responses) != 1:
        reasons.append("desktop_initialize_response_cardinality")
    else:
        initialize_result = initialize_responses[0].get("result")
        if (
            not isinstance(initialize_result, Mapping)
            or set(initialize_result) != {"codexHome", "platformFamily", "platformOs", "userAgent"}
            or initialize_result.get("platformFamily") != "windows"
            or initialize_result.get("platformOs") != "windows"
            or not _matches_desktop_user_agent_commitment_v1(
                initialize_result.get("userAgent")
            )
            or type(initialize_result.get("codexHome")) is not str
        ):
            reasons.append("desktop_initialize_response_shape")
        else:
            codex_home = initialize_result["codexHome"]

    if codex_home is not None:
        try:
            home_path = PureWindowsPath(codex_home)
            runtime_path = PureWindowsPath(expected_runtime_cwd)
        except (TypeError, ValueError):
            reasons.append("desktop_codex_home_invalid")
        else:
            if (
                "\x00" in codex_home
                or not home_path.is_absolute()
                or not runtime_path.is_absolute()
                or re.fullmatch(r"\.codex-home-[0-9a-f]{32}", home_path.name) is None
                or str(home_path.parent).casefold() != str(runtime_path.parent).casefold()
            ):
                reasons.append("desktop_codex_home_invalid")
        if _sha256(codex_home.encode("utf-8")) != expected_codex_home_path_digest:
            reasons.append("desktop_codex_home_path_digest_mismatch")

    expected_shapes = (
        ("developer", 3),
        ("developer", 1),
        ("developer", 1),
        ("user", 1),
        ("user", 1),
    )
    observed_texts: list[tuple[str, ...] | None] = []
    if len(input_events) != len(expected_shapes):
        reasons.append("desktop_model_input_event_cardinality")
    for index, (expected_role, expected_part_count) in enumerate(expected_shapes):
        if index >= len(input_events):
            observed_texts.append(None)
            continue
        message = input_events[index]
        if set(message) != {"method", "params"}:
            reasons.append("desktop_model_input_event_fields")
        params = message.get("params")
        if not isinstance(params, Mapping) or set(params) != {
            "item",
            "threadId",
            "turnId",
        }:
            reasons.append("desktop_model_input_params_shape")
            observed_texts.append(None)
            continue
        if params.get("threadId") != expected_thread_id or params.get("turnId") != expected_turn_id:
            reasons.append("desktop_model_input_foreign_binding")
        item = params.get("item")
        if not isinstance(item, Mapping) or set(item) != {
            "content",
            "internal_chat_message_metadata_passthrough",
            "role",
            "type",
        }:
            reasons.append("desktop_model_input_item_shape")
            observed_texts.append(None)
            continue
        if item.get("type") != "message" or item.get("role") != expected_role:
            reasons.append("desktop_model_input_role_or_type")
        metadata = item.get("internal_chat_message_metadata_passthrough")
        if not isinstance(metadata, Mapping) or dict(metadata) != {"turn_id": expected_turn_id}:
            reasons.append("desktop_model_input_metadata")
        content = item.get("content")
        if type(content) is not list or len(content) != expected_part_count:
            reasons.append("desktop_model_input_content_shape")
            observed_texts.append(None)
            continue
        texts: list[str] = []
        for part in content:
            if (
                not isinstance(part, Mapping)
                or set(part) != {"text", "type"}
                or part.get("type") != "input_text"
                or type(part.get("text")) is not str
            ):
                reasons.append("desktop_model_input_part_shape")
                texts = []
                break
            texts.append(part["text"])
        observed_texts.append(tuple(texts) if texts else None)
    if len(input_events) > len(expected_shapes):
        # Inspecting only the first five must never make an extra event inert.
        reasons.append("desktop_model_input_extra_event")

    if len(observed_texts) >= 1 and observed_texts[0] is not None:
        first = observed_texts[0]
        assert first is not None
        if _sha256(first[0].encode("utf-8")) != _DESKTOP_PERMISSIONS_INPUT_DIGEST_V1:
            reasons.append("desktop_permissions_input_mismatch")
        if first[1] != configured_developer:
            reasons.append("desktop_configured_developer_input_mismatch")
        if codex_home is None:
            reasons.append("desktop_skills_home_unbound")
        else:
            home_for_skill_locator = codex_home.replace("\\", "/")
            if (
                first[2].count(home_for_skill_locator)
                != _DESKTOP_SKILLS_HOME_SUBSTITUTION_COUNT_V1
            ):
                reasons.append("desktop_skills_input_mismatch")
            else:
                normalized_skills = first[2].replace(
                    home_for_skill_locator,
                    "<CODEX_HOME>",
                )
                if (
                    _sha256(normalized_skills.encode("utf-8"))
                    != _DESKTOP_SKILLS_NORMALIZED_DIGEST_V1
                ):
                    reasons.append("desktop_skills_input_mismatch")
    if (
        len(observed_texts) >= 2
        and observed_texts[1] is not None
        and _sha256(observed_texts[1][0].encode("utf-8"))
        != _DESKTOP_PRIMARY_AGENT_INPUT_DIGEST_V1
    ):
        reasons.append("desktop_primary_agent_input_mismatch")
    if (
        len(observed_texts) >= 3
        and observed_texts[2] is not None
        and _sha256(observed_texts[2][0].encode("utf-8"))
        != _DESKTOP_MULTI_AGENT_INPUT_DIGEST_V1
    ):
        reasons.append("desktop_multi_agent_input_mismatch")
    if len(observed_texts) >= 4 and observed_texts[3] is not None:
        environment_text = observed_texts[3][0]
        if environment_date is None:
            reasons.append("desktop_environment_date_unbound")
        elif (
            environment_text.count(environment_date)
            != _DESKTOP_ENVIRONMENT_DATE_SUBSTITUTION_COUNT_V1
        ):
            reasons.append("desktop_environment_input_mismatch")
        else:
            normalized_environment = environment_text.replace(
                environment_date,
                "<CURRENT_DATE>",
            )
            if (
                _sha256(normalized_environment.encode("utf-8"))
                != _DESKTOP_ENVIRONMENT_NORMALIZED_DIGEST_V1
            ):
                reasons.append("desktop_environment_input_mismatch")
    if (
        len(observed_texts) >= 5
        and observed_texts[4] is not None
        and observed_texts[4][0] != expected_prompt
    ):
        reasons.append("desktop_prompt_input_mismatch")
    reasons.extend(
        _audit_desktop_turn_and_user_lifecycle_v1(
            messages,
            input_event_indices,
            expected_prompt=expected_prompt,
            expected_thread_id=expected_thread_id,
            expected_turn_id=expected_turn_id,
        )
    )
    reasons.extend(
        _audit_desktop_assistant_output_v1(
            messages,
            assistant_output_events,
            expected_thread_id=expected_thread_id,
            expected_turn_id=expected_turn_id,
        )
    )
    return tuple(dict.fromkeys(reasons))


def audit_app_server_messages(
    messages: Sequence[Mapping[str, Any]],
    *,
    requested_model: str,
    requested_effort: str,
    requested_provider: str,
    expected_thread_id: str,
    expected_turn_id: str,
    thread_start_result: Mapping[str, Any],
    process_exit_code: int,
    forced_termination: bool,
    expected_model_inputs: Sequence[tuple[str, str]] | None = None,
    expected_model_input_profile: str = MODEL_INPUT_PROFILE_EXACT_V1,
    expected_runtime_cwd: str | None = None,
    expected_codex_home_path_digest: str | None = None,
) -> IsolationAudit:
    """Audit the full stream against one expected thread and turn."""

    for label, value in (
        ("expected_thread_id", expected_thread_id),
        ("expected_turn_id", expected_turn_id),
    ):
        if type(value) is not str or not value:
            raise ValueError(f"{label} is required")
    if expected_model_inputs is not None:
        if not isinstance(expected_model_inputs, (list, tuple)):
            raise ValueError("expected model inputs must be a role/text sequence")
        for item in expected_model_inputs:
            if (
                not isinstance(item, (list, tuple))
                or len(item) != 2
                or item[0] not in {"developer", "system", "user"}
                or type(item[1]) is not str
                or not item[1]
            ):
                raise ValueError("expected model inputs must contain exact role/text pairs")
    if expected_model_input_profile not in {
        MODEL_INPUT_PROFILE_EXACT_V1,
        MODEL_INPUT_PROFILE_DESKTOP_V1,
    }:
        raise ValueError("unknown expected model-input profile")
    if expected_model_input_profile == MODEL_INPUT_PROFILE_DESKTOP_V1:
        if expected_model_inputs is None:
            raise ValueError("desktop model-input profile requires expected inputs")
        if (
            type(expected_runtime_cwd) is not str
            or not expected_runtime_cwd
            or "\x00" in expected_runtime_cwd
        ):
            raise ValueError("desktop model-input profile requires a runtime cwd")
        if (
            type(expected_codex_home_path_digest) is not str
            or re.fullmatch(r"sha256:[0-9a-f]{64}", expected_codex_home_path_digest) is None
        ):
            raise ValueError("desktop model-input profile requires a Codex-home path digest")
    expected_input_counts = (
        Counter(tuple(item) for item in expected_model_inputs)
        if expected_model_inputs is not None
        else None
    )
    observed_input_counts: Counter[tuple[str, str]] = Counter()
    observed_input_events: list[Mapping[str, Any]] = []
    observed_input_event_indices: list[int] = []
    observed_assistant_output_events: list[tuple[int, Mapping[str, Any]]] = []

    reasons: list[str] = []
    reroutes: list[tuple[str, str, str]] = []
    accepted_model = thread_start_result.get("model")
    accepted_effort = thread_start_result.get("reasoningEffort")
    accepted_provider = thread_start_result.get("modelProvider")

    if accepted_model != requested_model:
        reasons.append("accepted_model_mismatch")
    if accepted_effort != requested_effort:
        reasons.append("accepted_effort_mismatch")
    if accepted_provider != requested_provider:
        reasons.append("accepted_provider_mismatch")
    accepted_thread = thread_start_result.get("thread")
    if not isinstance(accepted_thread, Mapping) or accepted_thread.get("id") != expected_thread_id:
        reasons.append("accepted_thread_id_mismatch")
    if process_exit_code != 0:
        reasons.append("nonzero_process_exit")
    if forced_termination:
        reasons.append("forced_process_termination")

    matching_turn_completion_indices: list[int] = []
    final_item_indices: list[int] = []
    thread_started_indices: list[int] = []
    turn_started_indices: list[int] = []
    item_started_indices: dict[str, list[int]] = {}
    item_lifecycle: dict[str, dict[str, Any]] = {}
    user_completed_indices: list[int] = []
    agent_started_indices: list[int] = []
    model_verification_indices: list[int] = []
    for message_index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            reasons.append("malformed_protocol_message")
            continue
        if "method" not in message:
            if "id" not in message:
                reasons.append("malformed_protocol_message")
            continue
        method = message.get("method")
        if type(method) is not str or not method:
            reasons.append("unknown_event_method:missing")
            continue
        params = _event_params(message)
        if "id" in message:
            reasons.append(f"server_request:{method}")
        if method == "model/rerouted":
            reasons.extend(
                _event_binding_reasons(
                    message,
                    method=method,
                    expected_thread_id=expected_thread_id,
                    expected_turn_id=expected_turn_id,
                )
            )
            from_model = params.get("fromModel")
            to_model = params.get("toModel")
            if not isinstance(from_model, str) or not isinstance(to_model, str):
                reasons.append("malformed_model_reroute")
            reason = json.dumps(params.get("reason"), sort_keys=True, ensure_ascii=True)
            reroutes.append(
                (
                    from_model if isinstance(from_model, str) else "",
                    to_model if isinstance(to_model, str) else "",
                    reason,
                )
            )
            reasons.append("model_rerouted")
            continue
        if method not in ALLOWED_EVENT_METHODS:
            if method.startswith(("hook/", "mcp/", "mcpServer/", "command/", "process/")):
                reasons.append(f"forbidden_event:{method}")
            else:
                reasons.append(f"unknown_event:{method}")
            continue

        reasons.extend(
            _event_binding_reasons(
                message,
                method=method,
                expected_thread_id=expected_thread_id,
                expected_turn_id=expected_turn_id,
            )
        )
        if method == "thread/started":
            thread_started_indices.append(message_index)
        elif method == "turn/started":
            turn_started_indices.append(message_index)
        if method in {"item/started", "item/completed"}:
            item_type = _item_type(message)
            if item_type in FORBIDDEN_ITEM_TYPES:
                reasons.append(f"forbidden_item:{item_type}")
            elif item_type not in ALLOWED_ITEM_TYPES:
                reasons.append(f"unknown_item:{item_type or 'missing'}")
            item = params.get("item")
            item_id = item.get("id") if isinstance(item, Mapping) else None
            if method == "item/started" and isinstance(item_id, str):
                item_started_indices.setdefault(item_id, []).append(message_index)
                if item_type == "agentMessage":
                    agent_started_indices.append(message_index)
                if item_id in item_lifecycle:
                    reasons.append("duplicate_item_started")
                else:
                    item_lifecycle[item_id] = {
                        "completed": None,
                        "started": message_index,
                        "type": item_type,
                    }
            elif method == "item/completed" and isinstance(item_id, str):
                if item_type == "userMessage":
                    user_completed_indices.append(message_index)
                lifecycle = item_lifecycle.get(item_id)
                if lifecycle is None:
                    reasons.append("item_completed_without_start")
                elif lifecycle["completed"] is not None:
                    reasons.append("duplicate_item_completed")
                else:
                    lifecycle["completed"] = message_index
                    if lifecycle["type"] != item_type:
                        reasons.append("item_type_changed")
            if method == "item/completed" and item_type == "agentMessage":
                final_item_indices.append(message_index)
        elif method in ITEM_DELTA_EVENT_METHODS:
            item_id = params.get("itemId")
            lifecycle = item_lifecycle.get(item_id) if isinstance(item_id, str) else None
            if lifecycle is None or lifecycle["completed"] is not None:
                reasons.append("delta_without_active_item")
            elif (method == "item/agentMessage/delta" and lifecycle["type"] != "agentMessage") or (
                method != "item/agentMessage/delta" and lifecycle["type"] != "reasoning"
            ):
                reasons.append("delta_item_type_mismatch")
        elif method == "rawResponseItem/completed":
            item_type = _item_type(message)
            if item_type in FORBIDDEN_RAW_ITEM_TYPES:
                reasons.append(f"forbidden_raw_item:{item_type}")
            elif item_type not in ALLOWED_RAW_ITEM_TYPES:
                reasons.append(f"unknown_raw_item:{item_type or 'missing'}")
            elif item_type == "message":
                item = params.get("item")
                role = item.get("role") if isinstance(item, Mapping) else None
                content = item.get("content") if isinstance(item, Mapping) else None
                if role == "assistant":
                    observed_assistant_output_events.append((message_index, message))
                else:
                    observed_input_events.append(message)
                    observed_input_event_indices.append(message_index)
                if role not in {"assistant", "developer", "system", "user"}:
                    reasons.append("unknown_raw_message_role")
                elif not isinstance(content, (list, tuple)):
                    reasons.append(f"malformed_raw_message:{role}")
                elif role == "assistant":
                    if any(
                        not isinstance(part, Mapping)
                        or part.get("type") != "output_text"
                        or type(part.get("text")) is not str
                        for part in content
                    ):
                        reasons.append("unexpected_model_output_shape")
                else:
                    for part in content:
                        if (
                            not isinstance(part, Mapping)
                            or part.get("type") != "input_text"
                            or type(part.get("text")) is not str
                        ):
                            reasons.append(f"malformed_model_input:{role}")
                        else:
                            observed_input_counts[(role, part["text"])] += 1
        elif method == "model/verification":
            model_verification_indices.append(message_index)
            if (
                set(message) != {"method", "params"}
                or set(params) != {"threadId", "turnId", "verifications"}
                or not _matches_desktop_verification_commitment_v1(
                    params.get("verifications")
                )
            ):
                reasons.append("unsafe_model_verification")
        elif method == "remoteControl/status/changed":
            if (
                set(params) != {"environmentId", "installationId", "serverName", "status"}
                or params.get("status") != "disabled"
                or type(params.get("serverName")) is not str
                or not params.get("serverName")
                or type(params.get("installationId")) is not str
                or not params.get("installationId")
                or params.get("environmentId") is not None
            ):
                reasons.append("unsafe_remote_control_status")
        elif method == "mcpServer/startupStatus/updated":
            if (
                set(params) != {"error", "failureReason", "name", "status", "threadId"}
                or type(params.get("name")) is not str
                or not params.get("name")
                or params.get("status") not in {"starting", "ready"}
                or params.get("error") is not None
                or params.get("failureReason") is not None
            ):
                reasons.append("unsafe_mcp_startup_status")
        elif method == "account/rateLimits/updated":
            if set(params) != {"rateLimits"} or not isinstance(params.get("rateLimits"), Mapping):
                reasons.append("malformed_rate_limit_status")
        elif method == "thread/status/changed":
            status = params.get("status")
            status_type = status.get("type") if isinstance(status, Mapping) else None
            if status_type == "active":
                active_flags = status.get("activeFlags")
                if not isinstance(active_flags, (list, tuple)) or active_flags:
                    reasons.append("unsafe_thread_status")
            elif status_type != "idle":
                reasons.append("unsafe_thread_status")
        elif method == "turn/started":
            turn = params.get("turn")
            if not isinstance(turn, Mapping) or turn.get("status") != "inProgress":
                reasons.append("turn_not_started")
        elif method == "turn/completed":
            if _is_expected_turn_completion(
                message,
                expected_thread_id=expected_thread_id,
                expected_turn_id=expected_turn_id,
            ):
                matching_turn_completion_indices.append(message_index)
            turn = params.get("turn")
            if not isinstance(turn, Mapping) or turn.get("status") != "completed":
                reasons.append("turn_not_completed")

    if expected_model_input_profile == MODEL_INPUT_PROFILE_DESKTOP_V1:
        assert expected_model_inputs is not None
        assert expected_runtime_cwd is not None
        assert expected_codex_home_path_digest is not None
        reasons.extend(
            _audit_desktop_ambient_model_inputs_v1(
                messages,
                observed_input_events,
                observed_input_event_indices,
                observed_assistant_output_events,
                expected_model_inputs=expected_model_inputs,
                expected_runtime_cwd=expected_runtime_cwd,
                expected_codex_home_path_digest=expected_codex_home_path_digest,
                expected_thread_id=expected_thread_id,
                expected_turn_id=expected_turn_id,
                thread_start_result=thread_start_result,
                requested_provider=requested_provider,
            )
        )
    elif expected_input_counts is not None:
        for role, _text_value in observed_input_counts:
            if (role, _text_value) not in expected_input_counts:
                reasons.append(f"unexpected_model_input:{role}")
        for (role, _text_value), expected_count in expected_input_counts.items():
            observed_count = observed_input_counts[(role, _text_value)]
            if observed_count < expected_count:
                reasons.append(f"missing_model_input:{role}")
            elif observed_count > expected_count:
                reasons.append(f"duplicate_model_input:{role}")

    if len(model_verification_indices) > 1:
        reasons.append("model_verification_cardinality")
    if len(model_verification_indices) == 1:
        verification_index = model_verification_indices[0]
        if (
            len(user_completed_indices) != 1
            or len(agent_started_indices) != 1
            or not (user_completed_indices[0] < verification_index < agent_started_indices[0])
        ):
            reasons.append("model_verification_lifecycle_order")

    if not matching_turn_completion_indices:
        reasons.append("missing_turn_completed")
    elif len(matching_turn_completion_indices) != 1:
        reasons.append("turn_completed_cardinality")
    if len(matching_turn_completion_indices) == 1:
        completion_index = matching_turn_completion_indices[0]
        if len(final_item_indices) == 1 and final_item_indices[0] >= completion_index:
            reasons.append("final_message_not_before_turn_completed")
        for trailing in messages[completion_index + 1 :]:
            trailing_method = trailing.get("method") if isinstance(trailing, Mapping) else None
            if trailing_method not in {
                "thread/status/changed",
                "thread/tokenUsage/updated",
            }:
                reasons.append(f"event_after_turn_completed:{trailing_method or 'missing'}")
            elif trailing_method == "thread/status/changed":
                status = _event_params(trailing).get("status")
                status_type = status.get("type") if isinstance(status, Mapping) else None
                if status_type != "idle":
                    reasons.append("non_idle_status_after_turn_completed")
    if len(thread_started_indices) != 1:
        reasons.append("thread_started_cardinality")
    if len(turn_started_indices) != 1:
        reasons.append("turn_started_cardinality")
    if (
        len(thread_started_indices) == len(turn_started_indices) == 1
        and thread_started_indices[0] >= turn_started_indices[0]
    ):
        reasons.append("lifecycle_start_order")
    if len(turn_started_indices) == len(matching_turn_completion_indices) == 1:
        turn_start_index = turn_started_indices[0]
        turn_completion_index = matching_turn_completion_indices[0]
        turn_interval_methods = {
            "item/started",
            "item/completed",
            "item/agentMessage/delta",
            "item/reasoning/summaryTextDelta",
            "item/reasoning/summaryPartAdded",
            "item/reasoning/textDelta",
            "model/verification",
            "rawResponseItem/completed",
        }
        if any(
            message.get("method") in turn_interval_methods
            and not turn_start_index < index < turn_completion_index
            for index, message in enumerate(messages)
            if isinstance(message, Mapping)
        ):
            reasons.append("turn_bound_event_outside_lifecycle")
    if len(final_item_indices) == 1:
        final_message = _event_params(messages[final_item_indices[0]]).get("item")
        final_id = final_message.get("id") if isinstance(final_message, Mapping) else None
        starts = item_started_indices.get(final_id, []) if isinstance(final_id, str) else []
        if len(starts) != 1 or starts[0] >= final_item_indices[0]:
            reasons.append("final_message_item_lifecycle")
        if len(turn_started_indices) == 1 and turn_started_indices[0] >= final_item_indices[0]:
            reasons.append("final_message_before_turn_started")
    if any(lifecycle["completed"] is None for lifecycle in item_lifecycle.values()):
        reasons.append("item_lifecycle_incomplete")
    final_items = _agent_messages(messages)
    if len(final_items) != 1:
        reasons.append("final_message_cardinality")
    else:
        final_item = final_items[0]
        if not isinstance(final_item.get("text"), str):
            reasons.append("final_message_text_not_string")
        if final_item.get("phase") not in {None, "final_answer"}:
            reasons.append("final_message_phase")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return IsolationAudit(
        eligible=not unique_reasons,
        reasons=unique_reasons,
        accepted_model=accepted_model if isinstance(accepted_model, str) else None,
        accepted_effort=accepted_effort if isinstance(accepted_effort, str) else None,
        accepted_provider=accepted_provider if isinstance(accepted_provider, str) else None,
        # Current app-server events prove accepted configuration and reroutes,
        # but expose no positive response-side served-model attestation.
        model_identity_verified=False,
        reroutes=tuple(reroutes),
        final_message_count=len(final_items),
    )


class _StreamPump(threading.Thread):
    def __init__(self, stream: BinaryIO, output: queue.Queue[bytes | BaseException | None]) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._output = output

    def run(self) -> None:
        try:
            while True:
                line = self._stream.readline()
                if not line:
                    break
                self._output.put(line)
        except BaseException as error:  # pragma: no cover - platform pipe failure
            self._output.put(error)
        finally:
            self._output.put(None)


class _StderrPump(threading.Thread):
    def __init__(self, stream: BinaryIO, maximum: int) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._maximum = maximum
        self.data = bytearray()
        self.overflowed = False

    def run(self) -> None:
        try:
            while True:
                block = self._stream.read(65_536)
                if not block:
                    break
                remaining = self._maximum - len(self.data)
                if remaining > 0:
                    self.data.extend(block[:remaining])
                if len(block) > remaining:
                    self.overflowed = True
        except OSError:  # pragma: no cover - process teardown race
            pass


ProcessFactory = Callable[..., subprocess.Popen[bytes]]
_STANDARD_POPEN = subprocess.Popen


class CodexAppServerRunner:
    """Own a synchronous, hidden, BelowNormal app-server child process."""

    def __init__(
        self,
        config: CodexRunConfig,
        *,
        process_factory: ProcessFactory = _STANDARD_POPEN,
    ) -> None:
        self.config = config
        self._process_factory = process_factory

    def _creation_options(self) -> tuple[int, subprocess.STARTUPINFO | None]:
        if os.name != "nt":
            return 0, None
        flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        flags |= int(getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        return flags, startup

    def run(self, *, prompt: str, output_schema: Mapping[str, Any]) -> CodexRunResult:
        if self._process_factory is not _STANDARD_POPEN:
            return self._run_once(
                prompt=prompt,
                output_schema=output_schema,
                child_environment=None,
                codex_home_device=0,
                codex_home_inode=0,
                codex_home_path_digest=None,
                sterile_home_enforced=False,
            )
        lease = _prepare_solver_home(self.config)
        try:
            result = self._run_once(
                prompt=prompt,
                output_schema=output_schema,
                child_environment=lease.child_environment(),
                codex_home_device=lease.device,
                codex_home_inode=lease.inode,
                codex_home_path_digest=_sha256(str(lease.path).encode("utf-8")),
                sterile_home_enforced=True,
            )
            lease.verify_no_credential_leak(
                result.transcript_jsonl,
                result.stderr_bytes,
            )
        except BaseException:
            try:
                lease.cleanup()
            except BaseException as cleanup_error:
                raise AppServerProtocolError(
                    "sterile Codex home cleanup failed after solver error"
                ) from cleanup_error
            raise
        lease.cleanup()
        return replace(
            result,
            codex_home_cleanup_verified=True,
            credential_leak_scan_passed=True,
        )

    def _run_once(
        self,
        *,
        prompt: str,
        output_schema: Mapping[str, Any],
        child_environment: Mapping[str, str] | None,
        codex_home_device: int,
        codex_home_inode: int,
        codex_home_path_digest: str | None,
        sterile_home_enforced: bool,
    ) -> CodexRunResult:
        if not prompt.strip():
            raise ValueError("prompt is required")
        schema_bytes = json.dumps(
            output_schema,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(schema_bytes) > self.config.max_event_line_bytes:
            raise ValueError("output schema exceeds the event-line budget")
        schema_snapshot = _decode_json_object(schema_bytes)
        runtime_directory_device, runtime_directory_inode = self.config.runtime_directory_identity()
        executable_lease = _lease_codex_executable(self.config.executable)
        executable_before = executable_lease.snapshot
        assert executable_before is not None
        if (
            self.config.expected_executable_provenance is not None
            and executable_before.provenance != self.config.expected_executable_provenance
        ):
            executable_lease.close()
            raise AppServerProtocolError("leased Codex executable differs from expected provenance")
        command = (
            executable_before.provenance.canonical_path,
            *_app_server_command_tail(self.config.model, self.config.effort),
        )
        launch_command_json = json.dumps(
            list(command),
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        creationflags, startupinfo = self._creation_options()
        started = time.monotonic()
        process_factory = self._process_factory
        standard_popen_used = process_factory is _STANDARD_POPEN
        try:
            process = process_factory(
                command,
                cwd=self.config.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                shell=False,
                creationflags=creationflags,
                startupinfo=startupinfo,
                env=child_environment,
            )
        except BaseException:
            executable_lease.close()
            raise
        if process.stdin is None or process.stdout is None or process.stderr is None:
            try:
                process.kill()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired as error:
                    raise AppServerProtocolError(
                        "app-server with missing pipes could not be reaped"
                    ) from error
            finally:
                executable_lease.close()
            raise AppServerProtocolError("app-server pipes were not created")

        stdout_queue: queue.Queue[bytes | BaseException | None] = queue.Queue()
        stdout_pump = _StreamPump(process.stdout, stdout_queue)
        stderr_pump = _StderrPump(process.stderr, self.config.max_stderr_bytes)
        stdout_pump.start()
        stderr_pump.start()
        messages: list[Mapping[str, Any]] = []
        transcript = bytearray()
        deadline = started + self.config.timeout_seconds
        forced_termination = False

        def send(message: Mapping[str, Any]) -> bytes:
            raw = encode_codex_app_server_message(message)
            if len(raw) > self.config.max_event_line_bytes:
                raise AppServerProtocolError("outbound JSONL message is too large")
            process.stdin.write(raw)
            process.stdin.flush()
            return raw[:-1]

        def record(item: bytes | BaseException | None) -> Mapping[str, Any] | None:
            if item is None:
                return None
            if isinstance(item, BaseException):
                raise AppServerProtocolError("failed reading app-server stdout") from item
            if len(item) > self.config.max_event_line_bytes:
                raise AppServerProtocolError("inbound event line exceeds its byte budget")
            transcript.extend(item)
            if len(transcript) > self.config.max_transcript_bytes:
                raise AppServerProtocolError("event transcript exceeds its byte budget")
            try:
                materialized = _decode_json_object(item)
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
                raise AppServerProtocolError("invalid app-server JSONL event") from error
            messages.append(materialized)
            return materialized

        def receive() -> Mapping[str, Any]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AppServerProtocolError("app-server run timed out")
            try:
                item = stdout_queue.get(timeout=remaining)
            except queue.Empty as error:
                raise AppServerProtocolError("app-server run timed out") from error
            if item is None:
                raise AppServerProtocolError("app-server stdout closed unexpectedly")
            materialized = record(item)
            assert materialized is not None
            return materialized

        def await_response(request_id: int) -> Mapping[str, Any]:
            while True:
                message = receive()
                if (
                    type(message.get("id")) is not int
                    or message.get("id") != request_id
                    or "method" in message
                ):
                    continue
                if "error" in message:
                    detail = json.dumps(message["error"], sort_keys=True, ensure_ascii=True)
                    raise AppServerProtocolError(
                        f"app-server request {request_id} failed: {detail}"
                    )
                result = message.get("result")
                if not isinstance(result, Mapping):
                    raise AppServerProtocolError(
                        f"app-server response {request_id} has no object result"
                    )
                return result

        thread_start_result: Mapping[str, Any]
        initialize_request_json = b""
        initialized_notification_json = b""
        thread_start_request_json = b""
        turn_start_request_json = b""
        thread_id = ""
        turn_id = ""
        execution_nonce = secrets.token_hex(32)
        try:
            initialize_request_json = send(locked_initialize_request())
            await_response(1)
            initialized_notification_json = send({"method": "initialized", "params": {}})
            thread_start_request_json = send(
                build_locked_thread_start_request(
                    model=self.config.model,
                    model_provider=self.config.model_provider,
                    cwd=str(self.config.cwd),
                    base_instructions=self.config.base_instructions,
                    developer_instructions=self.config.developer_instructions,
                )
            )
            thread_start_result = await_response(2)
            thread = thread_start_result.get("thread")
            if (
                not isinstance(thread, Mapping)
                or type(thread.get("id")) is not str
                or not thread.get("id")
            ):
                raise AppServerProtocolError("thread/start returned no thread id")
            thread_id = require_codex_uuid7(thread["id"], "thread/start identifier")
            turn_start_request_json = send(
                build_locked_turn_start_request(
                    model=self.config.model,
                    effort=self.config.effort,
                    prompt=prompt,
                    output_schema=schema_snapshot,
                    thread_id=thread_id,
                )
            )
            turn_start_result = await_response(3)
            turn = turn_start_result.get("turn")
            if (
                not isinstance(turn, Mapping)
                or type(turn.get("id")) is not str
                or not turn.get("id")
            ):
                raise AppServerProtocolError("turn/start returned no turn id")
            turn_id = require_codex_uuid7(turn["id"], "turn/start identifier")
            while not any(
                _is_expected_turn_completion(
                    message,
                    expected_thread_id=thread_id,
                    expected_turn_id=turn_id,
                )
                for message in messages
            ):
                receive()
        finally:
            protocol_failed = sys.exc_info()[0] is not None
            try:
                with contextlib.suppress(OSError):
                    process.stdin.close()
                remaining = max(0.1, min(5.0, deadline - time.monotonic()))
                try:
                    process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    forced_termination = True
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
                stdout_pump.join(timeout=2)
                stderr_pump.join(timeout=2)
            finally:
                if protocol_failed or sys.exc_info()[0] is not None:
                    executable_lease.close()

        try:
            if stdout_pump.is_alive() or stderr_pump.is_alive():
                raise AppServerProtocolError("app-server pipe reader did not stop")
            while True:
                try:
                    queued = stdout_queue.get_nowait()
                except queue.Empty:
                    break
                record(queued)
            if stderr_pump.overflowed:
                raise AppServerProtocolError("app-server stderr exceeded its byte budget")
            executable_after = _snapshot_resolved_executable(
                Path(executable_before.provenance.canonical_path)
            )
            if executable_after != executable_before:
                raise AppServerProtocolError(
                    "Codex executable changed between preflight and postflight"
                )
        finally:
            executable_lease.close()
        exit_code = int(process.returncode if process.returncode is not None else -1)
        audit = audit_app_server_messages(
            messages,
            requested_model=self.config.model,
            requested_effort=self.config.effort,
            requested_provider=self.config.model_provider,
            expected_thread_id=thread_id,
            expected_turn_id=turn_id,
            thread_start_result=thread_start_result,
            process_exit_code=exit_code,
            forced_termination=forced_termination,
            expected_model_inputs=(
                ("developer", self.config.base_instructions),
                ("developer", self.config.developer_instructions),
                ("user", prompt),
            ),
            expected_model_input_profile=(
                MODEL_INPUT_PROFILE_DESKTOP_V1
                if standard_popen_used and sterile_home_enforced
                else MODEL_INPUT_PROFILE_EXACT_V1
            ),
            expected_runtime_cwd=str(self.config.cwd),
            expected_codex_home_path_digest=codex_home_path_digest,
        )
        agents = _agent_messages(messages)
        output_text = agents[0].get("text") if len(agents) == 1 else None
        if output_text is not None and not isinstance(output_text, str):
            output_text = None
        stderr_bytes = bytes(stderr_pump.data)
        return CodexRunResult(
            requested_model=self.config.model,
            requested_effort=self.config.effort,
            requested_provider=self.config.model_provider,
            prompt_text=prompt,
            output_schema_json=schema_bytes,
            launch_command_json=launch_command_json,
            executable_path=executable_before.provenance.canonical_path,
            executable_size_bytes=executable_before.provenance.size_bytes,
            executable_sha256_digest=executable_before.provenance.sha256_digest,
            executable_lock_enforced=executable_lease.lock_enforced,
            standard_popen_used=standard_popen_used,
            solver_process_isolation_policy_digest=(solver_process_isolation_policy_digest()),
            codex_home_directory_device=codex_home_device,
            codex_home_directory_inode=codex_home_inode,
            codex_home_path_digest=codex_home_path_digest,
            codex_home_initial_auth_only=sterile_home_enforced,
            codex_home_cleanup_verified=False,
            credential_leak_scan_passed=False,
            command_tail_policy_digest=app_server_command_tail_policy_digest(
                self.config.model,
                self.config.effort,
            ),
            initialize_request_json=initialize_request_json,
            initialized_notification_json=initialized_notification_json,
            thread_start_request_json=thread_start_request_json,
            turn_start_request_json=turn_start_request_json,
            execution_nonce=execution_nonce,
            timeout_seconds=self.config.timeout_seconds,
            runtime_directory_device=runtime_directory_device,
            runtime_directory_inode=runtime_directory_inode,
            thread_id=thread_id,
            turn_id=turn_id,
            output_text=output_text,
            messages=tuple(messages),
            transcript_jsonl=bytes(transcript),
            transcript_hash=_sha256(bytes(transcript)),
            stderr_bytes=stderr_bytes,
            stderr_hash=_sha256(stderr_bytes),
            stderr_excerpt=stderr_bytes.decode("utf-8", errors="replace")[-4_000:],
            process_exit_code=exit_code,
            forced_termination=forced_termination,
            elapsed_seconds=max(0.0, time.monotonic() - started),
            audit=audit,
        )


def validate_probability(value: object) -> float:
    """Shared primitive for callers building strict response schemas."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("probability must be a JSON number, not a boolean")
    probability = float(value)
    if not math.isfinite(probability) or not 0 <= probability <= 1:
        raise ValueError("probability must be finite and in [0, 1]")
    return probability
