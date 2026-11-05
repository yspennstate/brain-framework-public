"""Crash-safe, first-attempt-only execution ledger.

The ledger deliberately contains commitments rather than prompts, conditions, or
holdout cases.  Every durable state change is O_EXCL-staged, fsynced, then atomically
published at an exclusive final name.  A slot is irreversibly burned by a fsynced
STARTED record before its runner is invoked, and a terminal transition may only
reference a previously fsynced, content-addressed outcome object.

This is an integrity and crash-recovery primitive, not a security boundary against
an attacker who can delete the whole directory or obtain the caller-managed HMAC
key.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import stat
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


LEDGER_SCHEMA = "org.math-research-brain/first-attempt-ledger"
LEDGER_SCHEMA_VERSION = 1
MAX_PAYLOAD_BYTES = 64 * 1024 * 1024
MAX_JSON_STRING_BYTES = ((MAX_PAYLOAD_BYTES + 2) // 3) * 4 + 1024
MAX_RECORD_BYTES = MAX_JSON_STRING_BYTES + 1024 * 1024
MAX_SLOTS = 10_000
MAX_HISTORY_SNAPSHOT_ATTEMPTS = 16

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_HMAC_RE = re.compile(r"hmac-sha256:[0-9a-f]{64}\Z")
_SLOT_ID_RE = re.compile(r"slot_[0-9a-f]{32}\Z")
_RUNTIME_ID_RE = re.compile(r"run_[0-9a-f]{32}\Z")
_FAILURE_CODE_RE = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_MEDIA_TYPE_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,63}/"
    r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,63}\Z"
)
_HEX_OBJECT_RE = re.compile(r"[0-9a-f]{64}\.json\Z")


class LedgerError(RuntimeError):
    """Base class for ledger failures."""


class LedgerFormatError(LedgerError):
    """A persisted record is malformed or non-canonical."""


class LedgerIntegrityError(LedgerError):
    """A digest or HMAC does not validate."""


class LedgerStateError(LedgerError):
    """A requested transition is illegal in the current immutable history."""


class SlotBurnedError(LedgerStateError):
    """A STARTED record already exists, so the runner must never be retried."""


class AttemptRunnerFailed(LedgerError):
    """A regular runner exception was durably recorded as FAILED."""

    def __init__(self, terminal: TerminalTransition) -> None:
        super().__init__(f"runner failed for {terminal.slot_id}; failure was durably recorded")
        self.terminal = terminal


class OutcomeKind(StrEnum):
    RETURNED = "RETURNED"
    FAILED = "FAILED"


class SlotState(StrEnum):
    PENDING = "PENDING"
    STARTED = "STARTED"
    RETURNED = "RETURNED"
    FAILED = "FAILED"


def _key_bytes(key: bytes | bytearray | memoryview) -> bytes:
    if not isinstance(key, (bytes, bytearray, memoryview)):
        raise ValueError("the caller-supplied HMAC key must be bytes-like")
    material = bytes(key)
    if len(material) < 32:
        raise ValueError("the caller-supplied HMAC key must contain at least 32 bytes")
    return material


def _nfc_text(
    value: Any,
    label: str,
    *,
    maximum_bytes: int = 4096,
    allow_layout_controls: bool = False,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if value != unicodedata.normalize("NFC", value):
        raise ValueError(f"{label} must be NFC-normalized")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8") from exc
    if (not allow_empty and not value) or len(encoded) > maximum_bytes:
        minimum = 0 if allow_empty else 1
        raise ValueError(f"{label} must contain {minimum}..{maximum_bytes} UTF-8 bytes")
    for character in value:
        if allow_layout_controls and character in "\n\r\t":
            continue
        if unicodedata.category(character) in {"Cc", "Cf", "Cs"}:
            raise ValueError(f"{label} contains a control, format, or surrogate character")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be sha256: followed by 64 lowercase hex digits")
    return value


def _hmac_value(value: Any, label: str) -> str:
    if not isinstance(value, str) or _HMAC_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be hmac-sha256: followed by 64 lowercase hex digits")
    return value


def _strict_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if not all(isinstance(key, str) for key in value):
        raise LedgerFormatError(f"{label} keys must all be strings")
    actual = set(value)
    if actual != expected:
        raise LedgerFormatError(
            f"{label} keys differ; missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _validate_json_value(value: Any, label: str = "JSON value") -> None:
    if value is None or isinstance(value, (bool, str)):
        if isinstance(value, str):
            try:
                _nfc_text(
                    value,
                    label,
                    maximum_bytes=MAX_JSON_STRING_BYTES,
                    allow_layout_controls=True,
                    allow_empty=True,
                )
            except ValueError as exc:
                raise LedgerFormatError(str(exc)) from exc
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise LedgerFormatError(f"{label} may not contain non-finite numbers")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{label}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            try:
                _nfc_text(key, f"{label} key")
            except ValueError as exc:
                raise LedgerFormatError(str(exc)) from exc
            _validate_json_value(item, f"{label}.{key}")
        return
    raise LedgerFormatError(f"{label} contains unsupported type {type(value).__name__}")


def _canonical_json_bytes(value: Any) -> bytes:
    _validate_json_value(value)
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _reject_constant(value: str) -> None:
    raise LedgerFormatError(f"non-finite JSON constant is forbidden: {value}")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LedgerFormatError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _strict_json_bytes(raw: bytes) -> Any:
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise LedgerFormatError("ledger JSON must use canonical ASCII escaping") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise LedgerFormatError("invalid ledger JSON") from exc
    canonical = _canonical_json_bytes(value)
    if not hmac.compare_digest(canonical, raw):
        raise LedgerFormatError("ledger JSON is not in its exact canonical encoding")
    return value


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _mac(key: bytes, domain: str, body: Mapping[str, Any]) -> str:
    material = b"math-brain/attempt-ledger/v1\0" + domain.encode("ascii") + b"\0"
    material += _canonical_json_bytes(dict(body))
    return "hmac-sha256:" + hmac.new(key, material, hashlib.sha256).hexdigest()


def secret_commitment(
    key: bytes | bytearray | memoryview,
    *,
    domain: str,
    material: bytes,
) -> str:
    """Return a domain-separated opaque commitment without retaining ``material``."""

    secret = _key_bytes(key)
    safe_domain = _nfc_text(domain, "commitment domain", maximum_bytes=256)
    if not safe_domain.isascii():
        raise ValueError("commitment domain must be ASCII")
    if not isinstance(material, bytes):
        raise ValueError("commitment material must be bytes")
    prefix = b"math-brain/attempt-ledger/commitment/v1\0" + safe_domain.encode("ascii")
    return "hmac-sha256:" + hmac.new(secret, prefix + b"\0" + material, hashlib.sha256).hexdigest()


def new_slot_id() -> str:
    """Generate an opaque slot identifier that carries no condition or pair label."""

    return "slot_" + secrets.token_hex(16)


@dataclass(frozen=True, slots=True)
class SlotCommitment:
    slot_id: str
    run_spec_commitment: str
    batch_commitment: str
    prompt_commitment: str
    output_schema_commitment: str
    request_policy_commitment: str
    dispatch_commitment: str

    def __post_init__(self) -> None:
        if not isinstance(self.slot_id, str) or _SLOT_ID_RE.fullmatch(self.slot_id) is None:
            raise ValueError("slot_id must be slot_ followed by 32 lowercase hex digits")
        for name, value in (
            ("run_spec_commitment", self.run_spec_commitment),
            ("batch_commitment", self.batch_commitment),
            ("prompt_commitment", self.prompt_commitment),
            ("output_schema_commitment", self.output_schema_commitment),
            ("request_policy_commitment", self.request_policy_commitment),
            ("dispatch_commitment", self.dispatch_commitment),
        ):
            _hmac_value(value, name)

    @classmethod
    def seal(
        cls,
        *,
        key: bytes | bytearray | memoryview,
        slot_id: str,
        run_spec_digest: str,
        batch_digest: str,
        prompt: str,
        output_schema_json: bytes,
        request_policy_digest: str,
        dispatch_nonce: str,
    ) -> SlotCommitment:
        """Seal exact slot material while retaining only opaque HMAC commitments."""

        if not isinstance(slot_id, str) or _SLOT_ID_RE.fullmatch(slot_id) is None:
            raise ValueError("slot_id must be slot_ followed by 32 lowercase hex digits")
        _digest(run_spec_digest, "run_spec_digest")
        _digest(batch_digest, "batch_digest")
        _digest(request_policy_digest, "request_policy_digest")
        _digest(dispatch_nonce, "dispatch_nonce")
        prompt_text = _nfc_text(
            prompt,
            "prompt",
            maximum_bytes=MAX_PAYLOAD_BYTES,
            allow_layout_controls=True,
        )
        if not isinstance(output_schema_json, bytes):
            raise ValueError("output_schema_json must be bytes")
        _strict_json_bytes(output_schema_json)

        def commit(field: str, material: bytes) -> str:
            return secret_commitment(
                key,
                domain=f"slot/{slot_id}/{field}",
                material=material,
            )

        return cls(
            slot_id=slot_id,
            run_spec_commitment=commit("run-spec", run_spec_digest.encode("ascii")),
            batch_commitment=commit("batch", batch_digest.encode("ascii")),
            prompt_commitment=commit("prompt", prompt_text.encode("utf-8")),
            output_schema_commitment=commit("output-schema", output_schema_json),
            request_policy_commitment=commit(
                "request-policy", request_policy_digest.encode("ascii")
            ),
            dispatch_commitment=commit("dispatch", dispatch_nonce.encode("ascii")),
        )

    def to_dict(self, *, ordinal: int) -> dict[str, Any]:
        return {
            "batch_commitment": self.batch_commitment,
            "dispatch_commitment": self.dispatch_commitment,
            "ordinal": ordinal,
            "output_schema_commitment": self.output_schema_commitment,
            "prompt_commitment": self.prompt_commitment,
            "request_policy_commitment": self.request_policy_commitment,
            "run_spec_commitment": self.run_spec_commitment,
            "slot_id": self.slot_id,
        }

    @classmethod
    def from_dict(cls, value: Any, *, ordinal: int) -> SlotCommitment:
        if not isinstance(value, dict):
            raise LedgerFormatError("precommitted slot must be an object")
        _strict_keys(
            value,
            {
                "batch_commitment",
                "dispatch_commitment",
                "ordinal",
                "output_schema_commitment",
                "prompt_commitment",
                "request_policy_commitment",
                "run_spec_commitment",
                "slot_id",
            },
            "precommitted slot",
        )
        if type(value["ordinal"]) is not int or value["ordinal"] != ordinal:
            raise LedgerFormatError("slot ordinals must be exact, contiguous, and ordered")
        try:
            return cls(
                slot_id=value["slot_id"],
                run_spec_commitment=value["run_spec_commitment"],
                batch_commitment=value["batch_commitment"],
                prompt_commitment=value["prompt_commitment"],
                output_schema_commitment=value["output_schema_commitment"],
                request_policy_commitment=value["request_policy_commitment"],
                dispatch_commitment=value["dispatch_commitment"],
            )
        except ValueError as exc:
            raise LedgerFormatError(f"invalid precommitted slot: {exc}") from exc


@dataclass(frozen=True, slots=True)
class DispatchMaterial:
    """Exact in-memory request material re-presented immediately before STARTED."""

    run_spec_digest: str
    batch_digest: str
    prompt: str
    output_schema_json: bytes
    request_policy_digest: str
    dispatch_nonce: str

    def __post_init__(self) -> None:
        _digest(self.run_spec_digest, "run_spec_digest")
        _digest(self.batch_digest, "batch_digest")
        _digest(self.request_policy_digest, "request_policy_digest")
        _digest(self.dispatch_nonce, "dispatch_nonce")
        _nfc_text(
            self.prompt,
            "prompt",
            maximum_bytes=MAX_PAYLOAD_BYTES,
            allow_layout_controls=True,
        )
        if not isinstance(self.output_schema_json, bytes):
            raise ValueError("output_schema_json must be bytes")
        _strict_json_bytes(self.output_schema_json)

    def seal(
        self,
        *,
        key: bytes | bytearray | memoryview,
        slot_id: str,
    ) -> SlotCommitment:
        return SlotCommitment.seal(
            key=key,
            slot_id=slot_id,
            run_spec_digest=self.run_spec_digest,
            batch_digest=self.batch_digest,
            prompt=self.prompt,
            output_schema_json=self.output_schema_json,
            request_policy_digest=self.request_policy_digest,
            dispatch_nonce=self.dispatch_nonce,
        )


@dataclass(frozen=True, slots=True)
class ArtifactPayload:
    content: bytes
    media_type: str = "application/json"

    def __post_init__(self) -> None:
        if not isinstance(self.content, bytes):
            raise ValueError("artifact content must be bytes")
        if len(self.content) > MAX_PAYLOAD_BYTES:
            raise ValueError(f"artifact exceeds the {MAX_PAYLOAD_BYTES}-byte limit")
        if (
            not isinstance(self.media_type, str)
            or _MEDIA_TYPE_RE.fullmatch(self.media_type) is None
        ):
            raise ValueError("artifact media_type is invalid")
        if self.media_type == "application/json" or self.media_type.endswith("+json"):
            _strict_json_bytes(self.content)


@dataclass(frozen=True, slots=True)
class RuntimeDirectoryLease:
    slot_id: str
    opaque_id: str
    path: Path
    repository_root: Path
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class StartedAttempt:
    slot_id: str
    ordinal: int
    ledger_digest: str
    record_digest: str
    runtime_id: str
    runtime_device: int
    runtime_inode: int
    prerequisite_terminal_digest: str | None = None


@dataclass(frozen=True, slots=True)
class OutcomeObject:
    slot_id: str
    ordinal: int
    ledger_digest: str
    started_record_digest: str
    kind: OutcomeKind
    object_digest: str
    media_type: str
    payload: ArtifactPayload


@dataclass(frozen=True, slots=True)
class OutcomeSelection:
    slot_id: str
    ordinal: int
    ledger_digest: str
    started_record_digest: str
    kind: OutcomeKind
    object_digest: str
    media_type: str
    record_digest: str


@dataclass(frozen=True, slots=True)
class StoredOutcome:
    slot_id: str
    ordinal: int
    ledger_digest: str
    started_record_digest: str
    kind: OutcomeKind
    object_digest: str
    media_type: str
    selection_record_digest: str


@dataclass(frozen=True, slots=True)
class TerminalTransition:
    slot_id: str
    ordinal: int
    kind: OutcomeKind
    object_digest: str
    record_digest: str


@dataclass(frozen=True, slots=True)
class AttemptHistoryRecord:
    """Payload-free projection of one slot from one stable validated snapshot."""

    ordinal: int
    slot: SlotCommitment
    started: StartedAttempt | None
    selected_outcome: StoredOutcome | None
    terminal: TerminalTransition | None


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    finalized: tuple[TerminalTransition, ...]
    burned_without_outcome: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _HistorySnapshot:
    starts: Mapping[str, StartedAttempt]
    selections: Mapping[str, OutcomeSelection]
    terminals: Mapping[str, TerminalTransition]
    objects: Mapping[str, OutcomeObject]


def _signed_record(key: bytes, record_type: str, body: Mapping[str, Any]) -> bytes:
    record = {
        "body": dict(body),
        "hmac_sha256": _mac(key, record_type, body),
        "schema": LEDGER_SCHEMA,
        "schema_version": LEDGER_SCHEMA_VERSION,
    }
    return _canonical_json_bytes(record)


def _read_signed_record(path: Path, key: bytes, expected_type: str) -> tuple[dict[str, Any], bytes]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise LedgerFormatError(f"cannot stat ledger record {path.name}: {exc}") from exc
    if size <= 0 or size > MAX_RECORD_BYTES:
        raise LedgerFormatError(f"ledger record {path.name} has an invalid byte size")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise LedgerFormatError(f"cannot read ledger record {path.name}: {exc}") from exc
    value = _strict_json_bytes(raw)
    if not isinstance(value, dict):
        raise LedgerFormatError("signed ledger record must be an object")
    _strict_keys(value, {"body", "hmac_sha256", "schema", "schema_version"}, "record")
    if (
        value["schema"] != LEDGER_SCHEMA
        or type(value["schema_version"]) is not int
        or value["schema_version"] != LEDGER_SCHEMA_VERSION
    ):
        raise LedgerFormatError("unsupported ledger record schema")
    body = value["body"]
    if not isinstance(body, dict):
        raise LedgerFormatError("signed ledger record body must be an object")
    if body.get("record_type") != expected_type:
        raise LedgerFormatError(f"expected {expected_type} record")
    supplied = _hmac_value(value["hmac_sha256"], "record HMAC")
    expected = _mac(key, expected_type, body)
    if not hmac.compare_digest(supplied, expected):
        raise LedgerIntegrityError(f"HMAC verification failed for {path.name}")
    return body, raw


def _fsync_directory(path: Path) -> None:
    """Flush a directory entry where the host supports directory fsync."""

    flags = os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0))
    try:
        descriptor = os.open(path, flags)
    except OSError:
        if os.name == "nt":
            return
        raise
    try:
        os.fsync(descriptor)
    except OSError:
        if os.name != "nt":
            raise
    finally:
        os.close(descriptor)


def _write_and_fsync(descriptor: int, raw: bytes) -> None:
    """Write a complete staged record and flush its bytes before publication."""

    view = memoryview(raw)
    written = 0
    while written < len(view):
        count = os.write(descriptor, view[written:])
        if count <= 0:  # pragma: no cover - defensive OS contract check
            raise OSError("short write while persisting ledger record")
        written += count
    os.fsync(descriptor)


def _write_exclusive(path: Path, raw: bytes, *, staging_directory: Path) -> None:
    """Publish complete bytes atomically at an exclusive final pathname.

    O_EXCL owns a unique staging inode.  Only after that inode is fully written and
    fsynced is it hard-linked to the final name.  ``os.link`` is an atomic,
    no-replace final-name primitive, so a reader can observe either no record or the
    complete record, never an O_EXCL-created zero-length/partial final file.
    """

    if not staging_directory.is_dir() or _is_reparse_point(staging_directory):
        raise LedgerFormatError("staging directory must be a real directory")
    staged = staging_directory / f"{secrets.token_hex(32)}.pending"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | int(getattr(os, "O_BINARY", 0))
    descriptor = os.open(staged, flags, 0o600)
    try:
        _write_and_fsync(descriptor, raw)
    finally:
        os.close(descriptor)
    try:
        os.link(staged, path)
    except BaseException:
        try:
            staged.unlink()
            _fsync_directory(staging_directory)
        except OSError:
            pass
        raise
    _fsync_directory(path.parent)
    try:
        staged.unlink()
        _fsync_directory(staging_directory)
    except OSError:
        pass


def _is_reparse_point(path: Path) -> bool:
    try:
        status = path.lstat()
    except OSError:
        return False
    attributes = int(getattr(status, "st_file_attributes", 0))
    reparse = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    return path.is_symlink() or bool(reparse and attributes & reparse)


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _inside_git_repository(path: Path) -> bool:
    return any((candidate / ".git").exists() for candidate in (path, *path.parents))


class AttemptLedger:
    """Immutable, HMAC-authenticated state machine for first-attempt execution."""

    def __init__(
        self,
        *,
        root: Path,
        key: bytes,
        precommit_raw: bytes,
        slots: tuple[SlotCommitment, ...],
    ) -> None:
        self._root = root
        self._key = key
        self._precommit_raw = precommit_raw
        self._ledger_digest = _sha256(precommit_raw)
        self._slots = slots
        self._slot_index = {slot.slot_id: index for index, slot in enumerate(slots)}

    @property
    def root(self) -> Path:
        return self._root

    @property
    def ledger_digest(self) -> str:
        return self._ledger_digest

    @property
    def slots(self) -> tuple[SlotCommitment, ...]:
        return self._slots

    @classmethod
    def create(
        cls,
        root: str | Path,
        *,
        key: bytes | bytearray | memoryview,
        slots: Sequence[SlotCommitment],
    ) -> AttemptLedger:
        secret = _key_bytes(key)
        path = Path(root)
        if not path.is_absolute():
            raise ValueError("ledger root must be an absolute path")
        parent = path.parent.resolve(strict=True)
        path = parent / path.name
        if path.exists():
            raise FileExistsError(f"ledger root already exists: {path}")
        if _is_reparse_point(parent):
            raise ValueError("ledger parent may not be a symlink or reparse point")
        frozen_slots = tuple(slots)
        if not frozen_slots:
            raise ValueError("at least one exact ordered slot must be precommitted")
        if len(frozen_slots) > MAX_SLOTS:
            raise ValueError(f"at most {MAX_SLOTS} slots may be precommitted")
        if not all(isinstance(slot, SlotCommitment) for slot in frozen_slots):
            raise ValueError("slots must contain only SlotCommitment values")
        ids = [slot.slot_id for slot in frozen_slots]
        if len(ids) != len(set(ids)):
            raise ValueError("precommitted slot identifiers must be unique")

        os.mkdir(path, 0o700)
        _fsync_directory(parent)
        transitions = path / "transitions"
        objects = path / "objects"
        object_store = objects / "sha256"
        staging = path / "staging"
        for directory in (transitions, objects, object_store, staging):
            os.mkdir(directory, 0o700)
            _fsync_directory(directory.parent)
        body = {
            "created_at_ns": time.time_ns(),
            "ledger_nonce": secrets.token_hex(32),
            "record_type": "PRECOMMITTED",
            "slots": [slot.to_dict(ordinal=index) for index, slot in enumerate(frozen_slots)],
        }
        precommit_raw = _signed_record(secret, "PRECOMMITTED", body)
        _write_exclusive(
            path / "precommit.json",
            precommit_raw,
            staging_directory=staging,
        )
        ledger = cls(
            root=path,
            key=secret,
            precommit_raw=precommit_raw,
            slots=frozen_slots,
        )
        ledger._ensure_layout()
        ledger._validate_history()
        return ledger

    @classmethod
    def open(
        cls,
        root: str | Path,
        *,
        key: bytes | bytearray | memoryview,
    ) -> AttemptLedger:
        secret = _key_bytes(key)
        path = Path(root)
        if not path.is_absolute():
            raise ValueError("ledger root must be an absolute path")
        path = path.resolve(strict=True)
        if not path.is_dir() or _is_reparse_point(path):
            raise LedgerFormatError("ledger root must be a real directory")
        body, raw = _read_signed_record(path / "precommit.json", secret, "PRECOMMITTED")
        _strict_keys(body, {"created_at_ns", "ledger_nonce", "record_type", "slots"}, "precommit")
        if type(body["created_at_ns"]) is not int or body["created_at_ns"] < 0:
            raise LedgerFormatError("precommit created_at_ns must be a nonnegative integer")
        nonce = body["ledger_nonce"]
        if not isinstance(nonce, str) or re.fullmatch(r"[0-9a-f]{64}", nonce) is None:
            raise LedgerFormatError("precommit ledger_nonce is invalid")
        slot_values = body["slots"]
        if not isinstance(slot_values, list) or not slot_values or len(slot_values) > MAX_SLOTS:
            raise LedgerFormatError("precommit must contain a nonempty ordered slot list")
        slots = tuple(
            SlotCommitment.from_dict(value, ordinal=index)
            for index, value in enumerate(slot_values)
        )
        if len({slot.slot_id for slot in slots}) != len(slots):
            raise LedgerFormatError("precommitted slot identifiers must be unique")
        ledger = cls(root=path, key=secret, precommit_raw=raw, slots=slots)
        ledger._ensure_layout()
        ledger._validate_history()
        return ledger

    def _ensure_layout(self) -> None:
        expected = (
            self._root / "transitions",
            self._root / "objects",
            self._root / "staging",
        )
        for directory in expected:
            if not directory.is_dir() or _is_reparse_point(directory):
                raise LedgerFormatError(f"ledger path is not a real directory: {directory.name}")
        object_dir = self._root / "objects" / "sha256"
        if not object_dir.is_dir() or _is_reparse_point(object_dir):
            raise LedgerFormatError("objects/sha256 must be a real directory")
        expected_names = {"objects", "precommit.json", "staging", "transitions"}
        actual_names = {child.name for child in self._root.iterdir()}
        if actual_names != expected_names:
            raise LedgerFormatError(
                f"unexpected ledger-root entries: {sorted(actual_names - expected_names)}"
            )
        if {child.name for child in (self._root / "objects").iterdir()} != {"sha256"}:
            raise LedgerFormatError("objects directory contains unexpected entries")

    def _slot(self, slot_id: str) -> tuple[int, SlotCommitment]:
        try:
            ordinal = self._slot_index[slot_id]
        except (KeyError, TypeError) as exc:
            raise LedgerStateError("slot is not in the sealed precommitment") from exc
        return ordinal, self._slots[ordinal]

    def verify_dispatch(self, slot_id: str, dispatch: DispatchMaterial) -> None:
        """Recompute and compare every exact request commitment without persisting it."""

        ordinal, sealed = self._slot(slot_id)
        if not isinstance(dispatch, DispatchMaterial):
            raise LedgerStateError("dispatch must be exact DispatchMaterial")
        observed = dispatch.seal(key=self._key, slot_id=slot_id)
        expected_raw = _canonical_json_bytes(sealed.to_dict(ordinal=ordinal))
        observed_raw = _canonical_json_bytes(observed.to_dict(ordinal=ordinal))
        if not hmac.compare_digest(expected_raw, observed_raw):
            raise LedgerIntegrityError("dispatch material differs from the precommitment")

    def _transition_path(self, ordinal: int, slot_id: str, suffix: str) -> Path:
        return self._root / "transitions" / f"{ordinal:06d}-{slot_id}.{suffix}.json"

    def _started_path(self, ordinal: int, slot_id: str) -> Path:
        return self._transition_path(ordinal, slot_id, "started")

    def _selection_path(self, ordinal: int, slot_id: str) -> Path:
        return self._transition_path(ordinal, slot_id, "outcome-selected")

    def _terminal_paths(self, ordinal: int, slot_id: str) -> tuple[Path, Path]:
        return (
            self._transition_path(ordinal, slot_id, "returned"),
            self._transition_path(ordinal, slot_id, "failed"),
        )

    def _expected_transition_names(self) -> set[str]:
        names: set[str] = set()
        for ordinal, slot in enumerate(self._slots):
            names.add(self._started_path(ordinal, slot.slot_id).name)
            names.add(self._selection_path(ordinal, slot.slot_id).name)
            names.update(path.name for path in self._terminal_paths(ordinal, slot.slot_id))
        return names

    def _load_started(self, ordinal: int, slot_id: str) -> StartedAttempt | None:
        path = self._started_path(ordinal, slot_id)
        if not path.exists():
            return None
        body, raw = _read_signed_record(path, self._key, "STARTED")
        legacy_keys = {
            "ledger_digest",
            "ordinal",
            "record_type",
            "runtime_device",
            "runtime_id",
            "runtime_inode",
            "slot_id",
            "started_at_ns",
        }
        current_keys = legacy_keys | {"prerequisite_terminal_digest"}
        if set(body) == legacy_keys:
            prerequisite_terminal_digest = None
        else:
            _strict_keys(body, current_keys, "STARTED")
            raw_prerequisite = body["prerequisite_terminal_digest"]
            prerequisite_terminal_digest = (
                None
                if raw_prerequisite is None
                else _digest(raw_prerequisite, "STARTED prerequisite terminal digest")
            )
        if body["ledger_digest"] != self._ledger_digest:
            raise LedgerIntegrityError("STARTED record belongs to another ledger")
        if body["ordinal"] != ordinal or body["slot_id"] != slot_id:
            raise LedgerIntegrityError("STARTED record slot identity mismatch")
        if type(body["started_at_ns"]) is not int or body["started_at_ns"] < 0:
            raise LedgerFormatError("STARTED time is invalid")
        runtime_id = body["runtime_id"]
        if not isinstance(runtime_id, str) or _RUNTIME_ID_RE.fullmatch(runtime_id) is None:
            raise LedgerFormatError("STARTED runtime_id is invalid")
        for field in ("runtime_device", "runtime_inode"):
            if type(body[field]) is not int or body[field] < 0:
                raise LedgerFormatError(f"STARTED {field} is invalid")
        return StartedAttempt(
            slot_id=slot_id,
            ordinal=ordinal,
            ledger_digest=self._ledger_digest,
            record_digest=_sha256(raw),
            runtime_id=runtime_id,
            runtime_device=body["runtime_device"],
            runtime_inode=body["runtime_inode"],
            prerequisite_terminal_digest=prerequisite_terminal_digest,
        )

    def _load_selection(
        self,
        ordinal: int,
        slot_id: str,
        started: StartedAttempt | None,
    ) -> OutcomeSelection | None:
        path = self._selection_path(ordinal, slot_id)
        if not path.exists():
            return None
        if started is None:
            raise LedgerIntegrityError("outcome selection exists without STARTED")
        body, raw = _read_signed_record(path, self._key, "OUTCOME_SELECTED")
        _strict_keys(
            body,
            {
                "ledger_digest",
                "media_type",
                "object_digest",
                "ordinal",
                "outcome_kind",
                "record_type",
                "selected_at_ns",
                "slot_id",
                "started_record_digest",
            },
            "OUTCOME_SELECTED",
        )
        if (
            body["ledger_digest"] != self._ledger_digest
            or body["ordinal"] != ordinal
            or body["slot_id"] != slot_id
            or body["started_record_digest"] != started.record_digest
        ):
            raise LedgerIntegrityError("outcome selection identity mismatch")
        if type(body["selected_at_ns"]) is not int or body["selected_at_ns"] < 0:
            raise LedgerFormatError("outcome selection time is invalid")
        try:
            kind = OutcomeKind(body["outcome_kind"])
        except (TypeError, ValueError) as exc:
            raise LedgerFormatError("outcome selection kind is invalid") from exc
        media_type = body["media_type"]
        if not isinstance(media_type, str) or _MEDIA_TYPE_RE.fullmatch(media_type) is None:
            raise LedgerFormatError("outcome selection media_type is invalid")
        return OutcomeSelection(
            slot_id=slot_id,
            ordinal=ordinal,
            ledger_digest=self._ledger_digest,
            started_record_digest=started.record_digest,
            kind=kind,
            object_digest=_digest(body["object_digest"], "selected object digest"),
            media_type=media_type,
            record_digest=_sha256(raw),
        )

    def _load_terminal(
        self,
        ordinal: int,
        slot_id: str,
        started: StartedAttempt | None,
        selection: OutcomeSelection | None,
    ) -> TerminalTransition | None:
        returned, failed = self._terminal_paths(ordinal, slot_id)
        existing = [path for path in (returned, failed) if path.exists()]
        if len(existing) > 1:
            raise LedgerIntegrityError("slot has both RETURNED and FAILED transitions")
        if not existing:
            return None
        if started is None:
            raise LedgerIntegrityError("terminal transition exists without STARTED")
        if selection is None:
            raise LedgerIntegrityError("terminal transition exists without outcome selection")
        path = existing[0]
        kind = OutcomeKind.RETURNED if path == returned else OutcomeKind.FAILED
        body, raw = _read_signed_record(path, self._key, kind.value)
        _strict_keys(
            body,
            {
                "ledger_digest",
                "object_digest",
                "ordinal",
                "record_type",
                "recorded_at_ns",
                "selection_record_digest",
                "slot_id",
                "started_record_digest",
            },
            kind.value,
        )
        if (
            body["ledger_digest"] != self._ledger_digest
            or body["ordinal"] != ordinal
            or body["slot_id"] != slot_id
            or body["started_record_digest"] != started.record_digest
            or body["selection_record_digest"] != selection.record_digest
        ):
            raise LedgerIntegrityError("terminal transition identity mismatch")
        object_digest = _digest(body["object_digest"], "terminal object_digest")
        if type(body["recorded_at_ns"]) is not int or body["recorded_at_ns"] < 0:
            raise LedgerFormatError("terminal recorded_at_ns is invalid")
        stored = self._load_object(object_digest)
        if (
            stored.slot_id != slot_id
            or stored.ordinal != ordinal
            or stored.started_record_digest != started.record_digest
            or stored.kind is not kind
            or stored.object_digest != selection.object_digest
            or stored.media_type != selection.media_type
            or selection.kind is not kind
        ):
            raise LedgerIntegrityError("terminal transition references the wrong outcome object")
        return TerminalTransition(
            slot_id=slot_id,
            ordinal=ordinal,
            kind=kind,
            object_digest=object_digest,
            record_digest=_sha256(raw),
        )

    def _load_object(self, object_digest: str) -> OutcomeObject:
        digest = _digest(object_digest, "object digest")
        path = self._root / "objects" / "sha256" / f"{digest.removeprefix('sha256:')}.json"
        body, raw = _read_signed_record(path, self._key, "OUTCOME_OBJECT")
        if not hmac.compare_digest(_sha256(raw), digest):
            raise LedgerIntegrityError("outcome object filename does not match its content")
        _strict_keys(
            body,
            {
                "ledger_digest",
                "media_type",
                "ordinal",
                "outcome_kind",
                "payload_base64",
                "payload_sha256",
                "record_type",
                "slot_id",
                "started_record_digest",
            },
            "outcome object",
        )
        if body["ledger_digest"] != self._ledger_digest:
            raise LedgerIntegrityError("outcome object belongs to another ledger")
        try:
            kind = OutcomeKind(body["outcome_kind"])
        except (TypeError, ValueError) as exc:
            raise LedgerFormatError("outcome object kind is invalid") from exc
        if type(body["ordinal"]) is not int or body["ordinal"] < 0:
            raise LedgerFormatError("outcome object ordinal is invalid")
        slot_id = body["slot_id"]
        if not isinstance(slot_id, str) or _SLOT_ID_RE.fullmatch(slot_id) is None:
            raise LedgerFormatError("outcome object slot_id is invalid")
        started_digest = _digest(body["started_record_digest"], "started record digest")
        media_type = body["media_type"]
        if not isinstance(media_type, str) or _MEDIA_TYPE_RE.fullmatch(media_type) is None:
            raise LedgerFormatError("outcome object media_type is invalid")
        payload_digest = _digest(body["payload_sha256"], "payload digest")
        payload_text = body["payload_base64"]
        if not isinstance(payload_text, str):
            raise LedgerFormatError("outcome object payload_base64 must be a string")
        try:
            payload = base64.b64decode(payload_text.encode("ascii"), validate=True)
        except (UnicodeEncodeError, ValueError) as exc:
            raise LedgerFormatError("outcome object payload_base64 is invalid") from exc
        if len(payload) > MAX_PAYLOAD_BYTES or not hmac.compare_digest(
            _sha256(payload), payload_digest
        ):
            raise LedgerIntegrityError("outcome payload digest or size is invalid")
        if media_type == "application/json" or media_type.endswith("+json"):
            _strict_json_bytes(payload)
        return OutcomeObject(
            slot_id=slot_id,
            ordinal=body["ordinal"],
            ledger_digest=self._ledger_digest,
            started_record_digest=started_digest,
            kind=kind,
            object_digest=digest,
            media_type=media_type,
            payload=ArtifactPayload(payload, media_type),
        )

    def _all_objects(self) -> tuple[OutcomeObject, ...]:
        directory = self._root / "objects" / "sha256"
        results: list[OutcomeObject] = []
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            if (
                not path.is_file()
                or _is_reparse_point(path)
                or _HEX_OBJECT_RE.fullmatch(path.name) is None
            ):
                raise LedgerFormatError(f"unexpected outcome-object entry: {path.name}")
            results.append(self._load_object("sha256:" + path.stem))
        return tuple(results)

    def _history_inventory(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """Collect the final names whose publication can change history.

        Staging names are deliberately excluded: they are never records.  Immutable
        final files are only added, so equal inventories before and after a parse
        give that parse a stable linearization interval.
        """

        inventories: list[tuple[str, tuple[str, ...]]] = []
        for label, directory in (
            ("transitions", self._root / "transitions"),
            ("objects", self._root / "objects" / "sha256"),
        ):
            try:
                names = tuple(sorted(path.name for path in directory.iterdir()))
            except OSError as exc:
                raise LedgerFormatError(f"cannot inventory {label} directory: {exc}") from exc
            inventories.append((label, names))
        return tuple(inventories)

    def _validate_history_once(self) -> _HistorySnapshot:
        transition_dir = self._root / "transitions"
        expected_names = self._expected_transition_names()
        for path in transition_dir.iterdir():
            if not path.is_file() or _is_reparse_point(path) or path.name not in expected_names:
                raise LedgerFormatError(f"unexpected transition entry: {path.name}")
        starts: dict[str, StartedAttempt] = {}
        selections: dict[str, OutcomeSelection] = {}
        terminals: dict[str, TerminalTransition] = {}
        for ordinal, slot in enumerate(self._slots):
            started = self._load_started(ordinal, slot.slot_id)
            if started is not None:
                starts[slot.slot_id] = started
            selection = self._load_selection(ordinal, slot.slot_id, started)
            if selection is not None:
                selections[slot.slot_id] = selection
            terminal = self._load_terminal(ordinal, slot.slot_id, started, selection)
            if terminal is not None:
                terminals[slot.slot_id] = terminal
        objects = self._all_objects()
        objects_by_digest = {stored.object_digest: stored for stored in objects}
        for stored in objects:
            started = starts.get(stored.slot_id)
            selection = selections.get(stored.slot_id)
            if (
                started is None
                or stored.ordinal != started.ordinal
                or stored.started_record_digest != started.record_digest
            ):
                raise LedgerIntegrityError("outcome object has no exact STARTED parent")
            if selection is None or selection.object_digest != stored.object_digest:
                raise LedgerIntegrityError("outcome object has no exact O_EXCL selection")
        for slot_id, selection in selections.items():
            stored = objects_by_digest.get(selection.object_digest)
            if stored is not None and (
                stored.slot_id != slot_id
                or stored.ordinal != selection.ordinal
                or stored.started_record_digest != selection.started_record_digest
                or stored.kind is not selection.kind
                or stored.media_type != selection.media_type
            ):
                raise LedgerIntegrityError("outcome selection and object differ")
        for slot_id, terminal in terminals.items():
            selection = selections[slot_id]
            if terminal.object_digest != selection.object_digest:
                raise LedgerIntegrityError(
                    "terminal transition does not own the selected outcome object"
                )
        return _HistorySnapshot(starts, selections, terminals, objects_by_digest)

    def _history_snapshot(self) -> _HistorySnapshot:
        """Return one validated, stable immutable-history snapshot.

        A writer can atomically add a dependency between two reader lookups.  Such
        a mixed read is not corruption.  Retry only when the final-name inventory
        proves that publication overlapped the parse; a stable invalid inventory
        still fails closed with its original integrity/format error.
        """

        for _attempt in range(MAX_HISTORY_SNAPSHOT_ATTEMPTS):
            before = self._history_inventory()
            try:
                snapshot = self._validate_history_once()
            except LedgerError:
                after = self._history_inventory()
                if before != after:
                    continue
                raise
            if before == self._history_inventory():
                return snapshot
        raise LedgerStateError("ledger history did not stabilize during concurrent publication")

    def _validate_history(self) -> None:
        self._history_snapshot()

    def state(self, slot_id: str) -> SlotState:
        self._slot(slot_id)
        snapshot = self._history_snapshot()
        terminal = snapshot.terminals.get(slot_id)
        if terminal is not None:
            return SlotState(terminal.kind.value)
        return SlotState.STARTED if slot_id in snapshot.starts else SlotState.PENDING

    def started_attempt(self, slot_id: str) -> StartedAttempt | None:
        """Load the exact durable STARTED record through the public API."""

        self._slot(slot_id)
        return self._history_snapshot().starts.get(slot_id)

    def terminal_transition(self, slot_id: str) -> TerminalTransition | None:
        """Load the exact durable terminal record through the public API."""

        self._slot(slot_id)
        return self._history_snapshot().terminals.get(slot_id)

    def selected_outcome(self, slot_id: str) -> StoredOutcome | None:
        """Load the selected durable outcome metadata through the public API."""

        self._slot(slot_id)
        snapshot = self._history_snapshot()
        selection = snapshot.selections.get(slot_id)
        if selection is None:
            return None
        candidate = snapshot.objects.get(selection.object_digest)
        if candidate is None:
            raise LedgerStateError("selected outcome object is not yet durable")
        return self._stored_from_object(selection, candidate)

    def history_records(self) -> tuple[AttemptHistoryRecord, ...]:
        """Return every slot's payload-free state from one stable history snapshot."""

        snapshot = self._history_snapshot()
        records: list[AttemptHistoryRecord] = []
        for ordinal, slot in enumerate(self._slots):
            selection = snapshot.selections.get(slot.slot_id)
            selected_outcome: StoredOutcome | None = None
            if selection is not None:
                candidate = snapshot.objects.get(selection.object_digest)
                if candidate is None:  # pragma: no cover - snapshot validator owns this
                    raise LedgerIntegrityError("selected outcome object is not durable")
                selected_outcome = self._stored_from_object(selection, candidate)
            records.append(
                AttemptHistoryRecord(
                    ordinal=ordinal,
                    slot=slot,
                    started=snapshot.starts.get(slot.slot_id),
                    selected_outcome=selected_outcome,
                    terminal=snapshot.terminals.get(slot.slot_id),
                )
            )
        return tuple(records)

    def load_selected_artifact(self, slot_id: str) -> ArtifactPayload:
        """Recover the exact selected artifact/failure bytes after a restart."""

        stored = self.selected_outcome(slot_id)
        if stored is None:
            raise LedgerStateError("slot has no selected durable outcome")
        return self._load_object(stored.object_digest).payload

    def allocate_runtime_directory(
        self,
        slot_id: str,
        *,
        runtime_parent: str | Path,
        repository_root: str | Path,
    ) -> RuntimeDirectoryLease:
        """Create an empty, opaque runtime directory outside every Git repository."""

        self._slot(slot_id)
        if self.state(slot_id) is not SlotState.PENDING:
            raise SlotBurnedError(f"slot {slot_id} is already burned")
        parent = Path(runtime_parent)
        repository = Path(repository_root)
        if not parent.is_absolute() or not repository.is_absolute():
            raise ValueError("runtime_parent and repository_root must be absolute")
        parent = parent.resolve(strict=True)
        repository = repository.resolve(strict=True)
        if not parent.is_dir() or _is_reparse_point(parent):
            raise ValueError("runtime_parent must be a real directory")
        if not repository.is_dir():
            raise ValueError("repository_root must be an existing directory")
        if _inside(parent, self._root):
            raise ValueError("runtime_parent must be outside the ledger root")
        if _inside(parent, repository) or _inside_git_repository(parent):
            raise ValueError("runtime_parent must be outside every Git repository")
        for _attempt in range(32):
            opaque_id = "run_" + secrets.token_hex(16)
            path = parent / opaque_id
            try:
                os.mkdir(path, 0o700)
            except FileExistsError:  # pragma: no cover - 128-bit collision defense
                continue
            _fsync_directory(parent)
            resolved = path.resolve(strict=True)
            if _inside(resolved, repository) or _inside_git_repository(resolved):
                raise LedgerStateError(
                    "allocated runtime directory unexpectedly entered a Git repo"
                )
            status = resolved.stat()
            return RuntimeDirectoryLease(
                slot_id=slot_id,
                opaque_id=opaque_id,
                path=resolved,
                repository_root=repository,
                device=int(status.st_dev),
                inode=int(status.st_ino),
            )
        raise LedgerStateError("could not allocate an opaque runtime directory")

    def _validate_runtime_lease(self, slot_id: str, lease: RuntimeDirectoryLease) -> None:
        if not isinstance(lease, RuntimeDirectoryLease) or lease.slot_id != slot_id:
            raise LedgerStateError("runtime lease belongs to another slot")
        if _RUNTIME_ID_RE.fullmatch(lease.opaque_id) is None or lease.path.name != lease.opaque_id:
            raise LedgerStateError("runtime lease is not opaque")
        try:
            path = lease.path.resolve(strict=True)
        except OSError as exc:
            raise LedgerStateError("runtime directory no longer exists") from exc
        if path != lease.path or not path.is_dir() or _is_reparse_point(path):
            raise LedgerStateError("runtime directory identity changed")
        if _inside(path, lease.repository_root) or _inside_git_repository(path):
            raise LedgerStateError("runtime directory is inside a Git repository")
        status = path.stat()
        if int(status.st_dev) != lease.device or int(status.st_ino) != lease.inode:
            raise LedgerStateError("runtime directory device/inode identity changed")
        if next(path.iterdir(), None) is not None:
            raise LedgerStateError("runtime directory must be empty before STARTED")

    def start(
        self,
        slot_id: str,
        *,
        runtime: RuntimeDirectoryLease,
        dispatch: DispatchMaterial,
    ) -> StartedAttempt:
        """Burn ``slot_id`` durably.  The caller may invoke its runner only after return."""

        return self._start(
            slot_id,
            runtime=runtime,
            dispatch=dispatch,
            prerequisite_terminal=None,
        )

    def start_after(
        self,
        slot_id: str,
        *,
        runtime: RuntimeDirectoryLease,
        dispatch: DispatchMaterial,
        prerequisite_terminal: TerminalTransition | None,
    ) -> StartedAttempt:
        """Burn a slot while binding it to the exact preceding RETURNED transition.

        Ordinal zero requires ``None``.  Every later ordinal requires the exact,
        durably persisted RETURNED transition of the immediately preceding slot.
        This is a causal dependency, not a comparison of untrusted timestamps.
        """

        return self._start(
            slot_id,
            runtime=runtime,
            dispatch=dispatch,
            prerequisite_terminal=prerequisite_terminal,
            require_returned_prerequisite=True,
        )

    def validate_start_prerequisite(
        self,
        slot_id: str,
        prerequisite_terminal: TerminalTransition | None,
    ) -> None:
        """Validate a causal STARTED dependency without allocating a runtime."""

        snapshot = self._history_snapshot()
        ordinal, _slot = self._slot(slot_id)
        self._validated_prerequisite_digest(
            snapshot,
            ordinal=ordinal,
            prerequisite_terminal=prerequisite_terminal,
            require_returned_prerequisite=True,
        )

    def _validated_prerequisite_digest(
        self,
        snapshot: _HistorySnapshot,
        *,
        ordinal: int,
        prerequisite_terminal: TerminalTransition | None,
        require_returned_prerequisite: bool,
    ) -> str | None:
        if not require_returned_prerequisite:
            if prerequisite_terminal is not None:
                raise LedgerStateError("legacy STARTED publication cannot carry a prerequisite")
            return None
        if ordinal == 0:
            if prerequisite_terminal is not None:
                raise LedgerStateError("first slot cannot have a prerequisite terminal")
            return None
        preceding_slot_id = self._slots[ordinal - 1].slot_id
        persisted_terminal = snapshot.terminals.get(preceding_slot_id)
        if (
            type(prerequisite_terminal) is not TerminalTransition
            or persisted_terminal is None
            or prerequisite_terminal != persisted_terminal
            or prerequisite_terminal.slot_id != preceding_slot_id
            or prerequisite_terminal.ordinal != ordinal - 1
            or prerequisite_terminal.kind is not OutcomeKind.RETURNED
        ):
            raise LedgerStateError(
                "slot prerequisite must be the exact preceding RETURNED transition"
            )
        return prerequisite_terminal.record_digest

    def _start(
        self,
        slot_id: str,
        *,
        runtime: RuntimeDirectoryLease,
        dispatch: DispatchMaterial,
        prerequisite_terminal: TerminalTransition | None,
        require_returned_prerequisite: bool = False,
    ) -> StartedAttempt:
        """Internal STARTED publication shared by legacy and causal callers."""

        snapshot = self._history_snapshot()
        ordinal, _slot = self._slot(slot_id)
        self.verify_dispatch(slot_id, dispatch)
        existing = snapshot.starts.get(slot_id)
        if existing is not None:
            raise SlotBurnedError(f"slot {slot_id} already has a STARTED record")
        for earlier in self._slots[:ordinal]:
            if earlier.slot_id not in snapshot.starts:
                raise LedgerStateError("slots must be started in their exact precommitted order")
        prerequisite_digest = self._validated_prerequisite_digest(
            snapshot,
            ordinal=ordinal,
            prerequisite_terminal=prerequisite_terminal,
            require_returned_prerequisite=require_returned_prerequisite,
        )
        self._validate_runtime_lease(slot_id, runtime)
        body = {
            "ledger_digest": self._ledger_digest,
            "ordinal": ordinal,
            "prerequisite_terminal_digest": prerequisite_digest,
            "record_type": "STARTED",
            "runtime_device": runtime.device,
            "runtime_id": runtime.opaque_id,
            "runtime_inode": runtime.inode,
            "slot_id": slot_id,
            "started_at_ns": time.time_ns(),
        }
        raw = _signed_record(self._key, "STARTED", body)
        path = self._started_path(ordinal, slot_id)
        try:
            _write_exclusive(
                path,
                raw,
                staging_directory=self._root / "staging",
            )
        except FileExistsError as exc:
            raise SlotBurnedError(f"slot {slot_id} was concurrently burned") from exc
        return self._load_started(ordinal, slot_id)  # type: ignore[return-value]

    def _require_started(
        self,
        started: StartedAttempt,
        snapshot: _HistorySnapshot,
    ) -> StartedAttempt:
        if not isinstance(started, StartedAttempt) or started.ledger_digest != self._ledger_digest:
            raise LedgerStateError("STARTED token belongs to another ledger")
        self._slot(started.slot_id)
        persisted = snapshot.starts.get(started.slot_id)
        if persisted is None or persisted != started:
            raise LedgerStateError("STARTED token does not match the immutable record")
        if started.slot_id in snapshot.terminals:
            raise LedgerStateError("slot already has a terminal transition")
        return persisted

    def _select_outcome(
        self,
        started: StartedAttempt,
        candidate: OutcomeObject,
    ) -> OutcomeSelection:
        existing = self._load_selection(started.ordinal, started.slot_id, started)
        if existing is not None:
            if (
                existing.object_digest != candidate.object_digest
                or existing.kind is not candidate.kind
                or existing.media_type != candidate.media_type
            ):
                raise LedgerStateError(
                    "another outcome already won this STARTED attempt's O_EXCL selection"
                )
            return existing
        body = {
            "ledger_digest": self._ledger_digest,
            "media_type": candidate.media_type,
            "object_digest": candidate.object_digest,
            "ordinal": started.ordinal,
            "outcome_kind": candidate.kind.value,
            "record_type": "OUTCOME_SELECTED",
            "selected_at_ns": time.time_ns(),
            "slot_id": started.slot_id,
            "started_record_digest": started.record_digest,
        }
        raw = _signed_record(self._key, "OUTCOME_SELECTED", body)
        path = self._selection_path(started.ordinal, started.slot_id)
        try:
            _write_exclusive(
                path,
                raw,
                staging_directory=self._root / "staging",
            )
        except FileExistsError as exc:
            raise LedgerStateError(
                "another outcome already won this STARTED attempt's O_EXCL selection"
            ) from exc
        selection = self._load_selection(started.ordinal, started.slot_id, started)
        if selection is None:  # pragma: no cover - filesystem race defense
            raise LedgerIntegrityError("outcome selection disappeared after O_EXCL")
        if (
            selection.object_digest != candidate.object_digest
            or selection.kind is not candidate.kind
            or selection.media_type != candidate.media_type
        ):
            raise LedgerStateError(
                "another outcome already won this STARTED attempt's O_EXCL selection"
            )
        return selection

    def _stored_from_object(
        self,
        selection: OutcomeSelection,
        candidate: OutcomeObject,
    ) -> StoredOutcome:
        if (
            candidate.slot_id != selection.slot_id
            or candidate.ordinal != selection.ordinal
            or candidate.started_record_digest != selection.started_record_digest
            or candidate.kind is not selection.kind
            or candidate.media_type != selection.media_type
        ):
            raise LedgerIntegrityError("selected outcome object differs from its selection")
        return StoredOutcome(
            slot_id=selection.slot_id,
            ordinal=selection.ordinal,
            ledger_digest=self._ledger_digest,
            started_record_digest=selection.started_record_digest,
            kind=selection.kind,
            object_digest=selection.object_digest,
            media_type=selection.media_type,
            selection_record_digest=selection.record_digest,
        )

    def _stored_from_selection(self, selection: OutcomeSelection) -> StoredOutcome:
        return self._stored_from_object(selection, self._load_object(selection.object_digest))

    def _persist_outcome(
        self,
        started: StartedAttempt,
        *,
        kind: OutcomeKind,
        payload: ArtifactPayload,
    ) -> StoredOutcome:
        snapshot = self._history_snapshot()
        persisted = self._require_started(started, snapshot)
        body = {
            "ledger_digest": self._ledger_digest,
            "media_type": payload.media_type,
            "ordinal": persisted.ordinal,
            "outcome_kind": kind.value,
            "payload_base64": base64.b64encode(payload.content).decode("ascii"),
            "payload_sha256": _sha256(payload.content),
            "record_type": "OUTCOME_OBJECT",
            "slot_id": persisted.slot_id,
            "started_record_digest": persisted.record_digest,
        }
        raw = _signed_record(self._key, "OUTCOME_OBJECT", body)
        object_digest = _sha256(raw)
        candidate = OutcomeObject(
            slot_id=persisted.slot_id,
            ordinal=persisted.ordinal,
            ledger_digest=self._ledger_digest,
            started_record_digest=persisted.record_digest,
            kind=kind,
            object_digest=object_digest,
            media_type=payload.media_type,
            payload=payload,
        )
        selection = self._select_outcome(persisted, candidate)
        path = self._root / "objects" / "sha256" / f"{object_digest.removeprefix('sha256:')}.json"
        try:
            _write_exclusive(
                path,
                raw,
                staging_directory=self._root / "staging",
            )
        except FileExistsError:
            try:
                existing_raw = path.read_bytes()
            except OSError as exc:
                raise LedgerIntegrityError(
                    "cannot verify existing content-addressed object"
                ) from exc
            if not hmac.compare_digest(existing_raw, raw):
                raise LedgerIntegrityError("content-addressed object collision")
        return self._stored_from_selection(selection)

    def persist_returned(
        self,
        started: StartedAttempt,
        artifact: ArtifactPayload,
    ) -> StoredOutcome:
        if not isinstance(artifact, ArtifactPayload):
            raise ValueError("artifact must be an ArtifactPayload")
        return self._persist_outcome(started, kind=OutcomeKind.RETURNED, payload=artifact)

    def persist_failure(
        self,
        started: StartedAttempt,
        *,
        failure_code: str,
        detail_commitment: str | None = None,
    ) -> StoredOutcome:
        if not isinstance(failure_code, str) or _FAILURE_CODE_RE.fullmatch(failure_code) is None:
            raise ValueError("failure_code must be a short lowercase machine code")
        if detail_commitment is not None:
            _hmac_value(detail_commitment, "failure detail commitment")
        raw = _canonical_json_bytes(
            {"detail_commitment": detail_commitment, "failure_code": failure_code}
        )
        return self._persist_outcome(
            started,
            kind=OutcomeKind.FAILED,
            payload=ArtifactPayload(raw, "application/json"),
        )

    def finalize(self, stored: StoredOutcome) -> TerminalTransition:
        snapshot = self._history_snapshot()
        if not isinstance(stored, StoredOutcome) or stored.ledger_digest != self._ledger_digest:
            raise LedgerStateError("outcome object belongs to another ledger")
        started = snapshot.starts.get(stored.slot_id)
        if started is None or started.record_digest != stored.started_record_digest:
            raise LedgerStateError("outcome object has no exact STARTED parent")
        selection = snapshot.selections.get(stored.slot_id)
        if selection is None or selection.record_digest != stored.selection_record_digest:
            raise LedgerStateError("outcome token does not match the immutable selection")
        candidate = snapshot.objects.get(selection.object_digest)
        if candidate is None or self._stored_from_object(selection, candidate) != stored:
            raise LedgerStateError("outcome token does not match the content-addressed object")
        existing = snapshot.terminals.get(stored.slot_id)
        if existing is not None:
            if existing.object_digest != stored.object_digest or existing.kind is not stored.kind:
                raise LedgerIntegrityError(
                    "existing terminal transition references another outcome"
                )
            return existing
        body = {
            "ledger_digest": self._ledger_digest,
            "object_digest": stored.object_digest,
            "ordinal": stored.ordinal,
            "record_type": stored.kind.value,
            "recorded_at_ns": time.time_ns(),
            "selection_record_digest": stored.selection_record_digest,
            "slot_id": stored.slot_id,
            "started_record_digest": stored.started_record_digest,
        }
        raw = _signed_record(self._key, stored.kind.value, body)
        suffix = "returned" if stored.kind is OutcomeKind.RETURNED else "failed"
        path = self._transition_path(stored.ordinal, stored.slot_id, suffix)
        other_paths = self._terminal_paths(stored.ordinal, stored.slot_id)
        if any(candidate.exists() for candidate in other_paths):
            terminal = self._load_terminal(stored.ordinal, stored.slot_id, started, selection)
            if terminal is None:  # pragma: no cover - filesystem race defense
                raise LedgerIntegrityError("terminal transition disappeared during finalization")
            return terminal
        try:
            _write_exclusive(
                path,
                raw,
                staging_directory=self._root / "staging",
            )
        except FileExistsError:
            pass
        terminal = self._load_terminal(stored.ordinal, stored.slot_id, started, selection)
        if terminal is None or terminal.object_digest != stored.object_digest:
            raise LedgerIntegrityError("terminal transition race produced inconsistent history")
        return terminal

    def record_returned(
        self,
        started: StartedAttempt,
        artifact: ArtifactPayload,
    ) -> TerminalTransition:
        """Persist the artifact first, then append its RETURNED transition."""

        return self.finalize(self.persist_returned(started, artifact))

    def record_failure(
        self,
        started: StartedAttempt,
        *,
        failure_code: str,
        detail_commitment: str | None = None,
    ) -> TerminalTransition:
        """Persist the sanitized failure first, then append its FAILED transition."""

        return self.finalize(
            self.persist_failure(
                started,
                failure_code=failure_code,
                detail_commitment=detail_commitment,
            )
        )

    def recover_orphans(self) -> RecoveryReport:
        """Finalize only signed outcome objects with an exact immutable STARTED parent.

        STARTED slots lacking an outcome remain burned and are reported; they are never
        reset to PENDING and never dispatched again.
        """

        snapshot = self._history_snapshot()
        finalized: list[TerminalTransition] = []
        burned: list[str] = []
        for slot in self._slots:
            started = snapshot.starts.get(slot.slot_id)
            if started is None:
                continue
            selection = snapshot.selections.get(slot.slot_id)
            terminal = snapshot.terminals.get(slot.slot_id)
            if terminal is not None:
                continue
            if selection is None:
                burned.append(slot.slot_id)
                continue
            candidate = snapshot.objects.get(selection.object_digest)
            if candidate is None:
                burned.append(slot.slot_id)
                continue
            finalized.append(self.finalize(self._stored_from_object(selection, candidate)))
        return RecoveryReport(tuple(finalized), tuple(burned))

    def execute_first_attempt(
        self,
        slot_id: str,
        *,
        runtime_parent: str | Path,
        repository_root: str | Path,
        dispatch: DispatchMaterial,
        runner: Callable[[Path, DispatchMaterial], ArtifactPayload],
    ) -> TerminalTransition:
        """Burn a slot, invoke ``runner`` once, and durably record its outcome.

        Regular ``Exception`` failures receive a sanitized FAILED object.  A process
        crash or other ``BaseException`` leaves STARTED as a conservative burned slot.
        Re-entering this method for that slot raises before the runner can be called.
        """

        return self._execute_first_attempt(
            slot_id,
            runtime_parent=runtime_parent,
            repository_root=repository_root,
            dispatch=dispatch,
            runner=runner,
            prerequisite_terminal=None,
            require_returned_prerequisite=False,
        )

    def execute_first_attempt_after(
        self,
        slot_id: str,
        *,
        runtime_parent: str | Path,
        repository_root: str | Path,
        dispatch: DispatchMaterial,
        prerequisite_terminal: TerminalTransition | None,
        runner: Callable[[Path, DispatchMaterial], ArtifactPayload],
    ) -> TerminalTransition:
        """Execute once with a durable dependency on the preceding RETURNED slot."""

        return self._execute_first_attempt(
            slot_id,
            runtime_parent=runtime_parent,
            repository_root=repository_root,
            dispatch=dispatch,
            runner=runner,
            prerequisite_terminal=prerequisite_terminal,
            require_returned_prerequisite=True,
        )

    def _execute_first_attempt(
        self,
        slot_id: str,
        *,
        runtime_parent: str | Path,
        repository_root: str | Path,
        dispatch: DispatchMaterial,
        runner: Callable[[Path, DispatchMaterial], ArtifactPayload],
        prerequisite_terminal: TerminalTransition | None,
        require_returned_prerequisite: bool,
    ) -> TerminalTransition:
        if not callable(runner):
            raise ValueError("runner must be callable")
        self.verify_dispatch(slot_id, dispatch)
        if require_returned_prerequisite:
            self.validate_start_prerequisite(slot_id, prerequisite_terminal)
        runtime = self.allocate_runtime_directory(
            slot_id,
            runtime_parent=runtime_parent,
            repository_root=repository_root,
        )
        if require_returned_prerequisite:
            started = self.start_after(
                slot_id,
                runtime=runtime,
                dispatch=dispatch,
                prerequisite_terminal=prerequisite_terminal,
            )
        else:
            started = self.start(slot_id, runtime=runtime, dispatch=dispatch)
        try:
            artifact = runner(runtime.path, dispatch)
            if not isinstance(artifact, ArtifactPayload):
                raise TypeError("runner must return ArtifactPayload")
        except Exception as exc:
            exception_name = f"{type(exc).__module__}.{type(exc).__qualname__}".encode("utf-8")
            detail = secret_commitment(
                self._key,
                domain=f"slot/{slot_id}/runner-exception-type",
                material=exception_name,
            )
            terminal = self.record_failure(
                started,
                failure_code="runner_exception",
                detail_commitment=detail,
            )
            raise AttemptRunnerFailed(terminal) from exc
        return self.record_returned(started, artifact)


__all__ = [
    "ArtifactPayload",
    "AttemptHistoryRecord",
    "AttemptLedger",
    "AttemptRunnerFailed",
    "DispatchMaterial",
    "LedgerError",
    "LedgerFormatError",
    "LedgerIntegrityError",
    "LedgerStateError",
    "OutcomeKind",
    "RecoveryReport",
    "RuntimeDirectoryLease",
    "SlotBurnedError",
    "SlotCommitment",
    "SlotState",
    "StartedAttempt",
    "StoredOutcome",
    "TerminalTransition",
    "new_slot_id",
    "secret_commitment",
]
