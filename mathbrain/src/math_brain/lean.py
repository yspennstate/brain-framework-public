"""Fail-closed local Lean verification with an immutable execution envelope.

Kernel acceptance and statement fidelity remain separate authorities.  This
module only produces a kernel-check result; it never signs or persists evidence.
"""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import hmac
import json
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from .models import (
    Claim,
    ClaimKind,
    Evidence,
    EvidenceAttestation,
    EvidenceAuthorityMode,
    EvidenceAuthorityRole,
    EvidenceKind,
    EvidenceReceipt,
)
from .privacy import read_stable_file
from .verification import evidence_authorization_mac

SHA256_PREFIX = "sha256:"
EMPTY_HASH = SHA256_PREFIX + hashlib.sha256(b"").hexdigest()
MAX_SOURCE_FILE_BYTES = 1_048_576
MAX_SOURCE_TREE_BYTES = 8_388_608
MAX_SOURCE_FILES = 256
MAX_OUTPUT_BYTES = 8_388_608
MAX_VERSION_OUTPUT_BYTES = 65_536
MAX_DIAGNOSTIC_EXCERPT_RAW_BYTES = 1_000_000
MAX_COMPUTE_STATE_BYTES = 131_072
MAX_COMPUTE_STATE_AGE_SECONDS = 60
MAX_FIDELITY_NOTES_BYTES = 65_536
PROCESS_POLL_SECONDS = 0.05
COMPUTE_POLL_SECONDS = 0.25
ALLOWED_COMMAND = ("lean", "--snapshot-project")

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_HMAC_RE = re.compile(r"hmac-sha256:[0-9a-f]{64}\Z")
_TOOLCHAIN_RE = re.compile(r"leanprover/lean4:v([0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9._-]+)?)\Z")
_PROJECT_NAME_RE = re.compile(r"[A-Z][A-Za-z0-9_]{0,63}\Z")
_MODULE_RE = re.compile(r"[A-Z][A-Za-z0-9_]*(?:\.[A-Z][A-Za-z0-9_]*)*\Z")
_DECLARATION_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)+\Z")
_AUTHORITY_ID_RE = re.compile(r"[a-z0-9][a-z0-9._/-]{0,127}\Z")
_FORBIDDEN_TRUST_RE = re.compile(
    r"(?<![A-Za-z0-9_'])(?:"
    r"sorry|admit|sorryAx|axiom|constant|opaque|unsafe|partial|"
    r"extern|foreign|initialize|builtin_initialize|run_tac|run_cmd|"
    r"elab|elab_rules|syntax|macro|macro_rules|native_decide|"
    r"bv_decide|ofReduceBool|implemented_by|include_str|include_bytes|"
    r"trace|dbg_trace|logInfo|logWarning|logError"
    r")(?![A-Za-z0-9_'])"
)
_FORBIDDEN_COMMAND_RE = re.compile(r"#(?:eval|reduce|run|guard|guard_msgs)\b")
_IMPORT_LINE_RE = re.compile(r"\s*import\s+([A-Za-z0-9_.' ]+)\s*\Z")
_AXIOM_PRINT_LINE_RE = re.compile(
    r"\s*#print\s+axioms\s+([A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)+)\s*\Z"
)
_RESULT_SEAL_KEY = os.urandom(32)


def _sha256(raw: bytes) -> str:
    return SHA256_PREFIX + hashlib.sha256(raw).hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_digest(value: object) -> str:
    return _sha256(_canonical_json_bytes(value))


def sha256_file(path: Path) -> str:
    """Hash a file through the same stable-reader boundary used by the verifier."""

    raw = read_stable_file(path, root=path.parent, max_file_bytes=MAX_SOURCE_TREE_BYTES)
    return _sha256(raw)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


@dataclass(frozen=True, slots=True)
class LeanResult:
    """Bounded, path-scrubbed record of one local Lean project build."""

    available: bool
    passed: bool
    returncode: int | None
    stdout: str
    stderr: str
    reason: str
    source_hash: str
    source_tree_hash: str
    toolchain_version: str
    toolchain_version_verified: bool
    checked_declarations: tuple[str, ...]
    axiom_report_verified: bool
    command: tuple[str, ...]
    request_digest: str
    output_hash: str
    executable_hash: str
    executable_size_bytes: int
    executable_lock_enforced: bool
    expected_executable_hash_matched: bool
    direct_toolchain_verified: bool
    project_manifest_hash: str
    toolchain_digest: str
    environment_digest: str
    compute_observations_hash: str
    verifier_digest: str
    execution_nonce: str
    source_snapshot_enforced: bool
    trust_policy_enforced: bool
    tree_guard_enforced: bool
    hidden_window_enforced: bool
    below_normal_priority_enforced: bool
    compute_policy_enforced: bool
    standard_process_used: bool
    forced_termination: bool
    output_overflowed: bool
    cleanup_verified: bool
    runtime_seal: str
    network_used: bool = False
    marginal_cost_microusd: int = 0

    def __post_init__(self) -> None:
        for name, value in (
            ("available", self.available),
            ("passed", self.passed),
            ("toolchain_version_verified", self.toolchain_version_verified),
            ("axiom_report_verified", self.axiom_report_verified),
            ("executable_lock_enforced", self.executable_lock_enforced),
            ("expected_executable_hash_matched", self.expected_executable_hash_matched),
            ("direct_toolchain_verified", self.direct_toolchain_verified),
            ("source_snapshot_enforced", self.source_snapshot_enforced),
            ("trust_policy_enforced", self.trust_policy_enforced),
            ("tree_guard_enforced", self.tree_guard_enforced),
            ("hidden_window_enforced", self.hidden_window_enforced),
            ("below_normal_priority_enforced", self.below_normal_priority_enforced),
            ("compute_policy_enforced", self.compute_policy_enforced),
            ("standard_process_used", self.standard_process_used),
            ("forced_termination", self.forced_termination),
            ("output_overflowed", self.output_overflowed),
            ("cleanup_verified", self.cleanup_verified),
            ("network_used", self.network_used),
        ):
            if type(value) is not bool:
                raise TypeError(f"Lean result {name} must be a boolean")
        if self.returncode is not None and type(self.returncode) is not int:
            raise TypeError("Lean return code must be an exact integer or None")
        if self.command != ALLOWED_COMMAND:
            raise ValueError("Lean result command is not the fixed local build command")
        for name, value in (
            ("source_hash", self.source_hash),
            ("source_tree_hash", self.source_tree_hash),
            ("request_digest", self.request_digest),
            ("output_hash", self.output_hash),
            ("executable_hash", self.executable_hash),
            ("project_manifest_hash", self.project_manifest_hash),
            ("toolchain_digest", self.toolchain_digest),
            ("environment_digest", self.environment_digest),
            ("compute_observations_hash", self.compute_observations_hash),
            ("verifier_digest", self.verifier_digest),
        ):
            if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
                raise ValueError(f"Lean result {name} must be a canonical SHA-256 digest")
        if type(self.executable_size_bytes) is not int or self.executable_size_bytes < 0:
            raise ValueError("Lean executable size must be a nonnegative exact integer")
        if not re.fullmatch(r"[0-9a-f]{64}", self.execution_nonce):
            raise ValueError("Lean execution nonce must be 256-bit lowercase hex")
        if not _HMAC_RE.fullmatch(self.runtime_seal):
            raise ValueError("Lean result runtime seal must be canonical HMAC-SHA256")
        if not isinstance(self.checked_declarations, tuple) or any(
            not isinstance(item, str) or _DECLARATION_RE.fullmatch(item) is None
            for item in self.checked_declarations
        ):
            raise ValueError("Lean checked declarations must be canonical names")
        if len(set(self.checked_declarations)) != len(self.checked_declarations):
            raise ValueError("Lean checked declarations must be unique")
        if len(self.stdout.encode("utf-8")) + len(self.stderr.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise ValueError("Lean diagnostics exceed the result byte budget")
        if not self.reason or len(self.reason) > 500:
            raise ValueError("Lean result reason must be a nonempty bounded string")
        if self.passed and (not self.available or self.returncode != 0):
            raise ValueError("a passing Lean result must be available with return code zero")
        if type(self.marginal_cost_microusd) is not int or self.marginal_cost_microusd < 0:
            raise ValueError("Lean marginal cost must be a nonnegative exact integer")

    def _authority_gates(self) -> tuple[tuple[str, bool], ...]:
        return (
            ("available", self.available),
            ("passed", self.passed),
            ("returncode", self.returncode == 0),
            ("toolchain_version_verified", self.toolchain_version_verified),
            ("checked_declarations", bool(self.checked_declarations)),
            ("axiom_report_verified", self.axiom_report_verified),
            ("executable_lock_enforced", self.executable_lock_enforced),
            ("expected_executable_hash_matched", self.expected_executable_hash_matched),
            ("direct_toolchain_verified", self.direct_toolchain_verified),
            ("source_snapshot_enforced", self.source_snapshot_enforced),
            ("trust_policy_enforced", self.trust_policy_enforced),
            ("tree_guard_enforced", self.tree_guard_enforced),
            ("hidden_window_enforced", self.hidden_window_enforced),
            ("below_normal_priority_enforced", self.below_normal_priority_enforced),
            ("compute_policy_enforced", self.compute_policy_enforced),
            ("standard_process_used", self.standard_process_used),
            ("no_forced_termination", not self.forced_termination),
            ("no_output_overflow", not self.output_overflowed),
            ("cleanup_verified", self.cleanup_verified),
            ("no_network", not self.network_used),
            ("zero_marginal_cost", self.marginal_cost_microusd == 0),
        )

    @property
    def authority_eligible(self) -> bool:
        """Whether this envelope may back a later local automated attestation."""

        return all(ok for _, ok in self._authority_gates())

    @property
    def authority_blockers(self) -> tuple[str, ...]:
        """The gates that keep this envelope from backing an attestation, by name; empty when it is eligible.

        A build that passes as a diagnostic says here why it cannot issue evidence. On a platform where the Lean
        executable cannot be held against replacement (every platform but Windows) that includes
        executable_lock_enforced (account04/B18, B18-R79-LEAN-POSIX-AUTHORITY-UNREACHABLE-20260929)."""

        return tuple(name for name, ok in self._authority_gates() if not ok)

    def digest(self) -> str:
        payload = asdict(self)
        payload["authority_eligible"] = self.authority_eligible
        return _canonical_digest(payload)

    def _runtime_seal_is_valid(self) -> bool:
        expected = _runtime_seal(self)
        return hmac.compare_digest(expected, self.runtime_seal)


def _runtime_seal(result: LeanResult) -> str:
    payload = asdict(result)
    payload.pop("runtime_seal")
    raw = _canonical_json_bytes(payload)
    return "hmac-sha256:" + hmac.new(_RESULT_SEAL_KEY, raw, hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class _SourceFile:
    relative_path: str
    module_name: str
    imports: tuple[str, ...]
    raw: bytes
    digest: str


@dataclass(frozen=True, slots=True)
class _ProjectInputs:
    source_hash: str
    source_tree_hash: str
    project_manifest_hash: str
    toolchain_file_hash: str
    toolchain_id: str
    pinned_version: str
    files: tuple[_SourceFile, ...]
    build_order: tuple[str, ...]
    axiom_prints: tuple[str, ...]
    lakefile_raw: bytes
    toolchain_raw: bytes
    trust_violation: str | None


def _strict_project_inputs(source: Path, root: Path) -> _ProjectInputs:
    toolchain_raw = read_stable_file(root / "lean-toolchain", root=root, max_file_bytes=1_024)
    lakefile_raw = read_stable_file(root / "lakefile.toml", root=root, max_file_bytes=65_536)
    try:
        toolchain_text = toolchain_raw.decode("utf-8").strip()
        lakefile = tomllib.loads(lakefile_raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError("Lean project metadata must be strict UTF-8 TOML") from error
    toolchain_match = _TOOLCHAIN_RE.fullmatch(toolchain_text)
    if toolchain_match is None:
        raise ValueError("Lean toolchain must pin one exact leanprover/lean4 release")
    if set(lakefile) != {"name", "version", "defaultTargets", "lean_lib"}:
        raise ValueError("Lake manifest contains unsupported executable or dependency fields")
    name = lakefile.get("name")
    version = lakefile.get("version")
    targets = lakefile.get("defaultTargets")
    libraries = lakefile.get("lean_lib")
    if not isinstance(name, str) or _PROJECT_NAME_RE.fullmatch(name) is None:
        raise ValueError("Lake project name is not a safe module name")
    if not isinstance(version, str) or not version or len(version) > 64:
        raise ValueError("Lake project version must be a bounded string")
    if targets != [name]:
        raise ValueError("Lake defaultTargets must contain exactly the local library")
    if not isinstance(libraries, list) or len(libraries) != 1:
        raise ValueError("Lake manifest must define exactly one local Lean library")
    library = libraries[0]
    if not isinstance(library, dict) or set(library) != {"name"} or library["name"] != name:
        raise ValueError("Lake library must exactly match the local project name")

    candidates = []
    for candidate in root.rglob("*.lean"):
        relative = candidate.relative_to(root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        candidates.append((relative.as_posix(), candidate))
    candidates.sort(key=lambda item: item[0].encode("utf-8"))
    if not candidates or len(candidates) > MAX_SOURCE_FILES:
        raise ValueError("Lean source file count is outside the fixed project budget")
    if len({relative.casefold() for relative, _ in candidates}) != len(candidates):
        raise ValueError("Lean source paths collide under case folding")

    files: list[_SourceFile] = []
    total = 0
    trust_violation: str | None = None
    source_relative = source.relative_to(root).as_posix()
    source_hash = EMPTY_HASH
    known_paths = {relative for relative, _ in candidates}
    axiom_prints: list[str] = []
    for relative, candidate in candidates:
        module_name = relative.removesuffix(".lean").replace("/", ".")
        if _MODULE_RE.fullmatch(module_name) is None:
            raise ValueError("Lean source path is not a canonical module name")
        raw = read_stable_file(candidate, root=root, max_file_bytes=MAX_SOURCE_FILE_BYTES)
        total += len(raw)
        if total > MAX_SOURCE_TREE_BYTES:
            raise ValueError("Lean source tree exceeds the fixed byte budget")
        try:
            text = raw.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("Lean source must be strict UTF-8") from error
        if "\x00" in text and trust_violation is None:
            trust_violation = f"{relative} contains a NUL byte"
        match = _FORBIDDEN_TRUST_RE.search(text) or _FORBIDDEN_COMMAND_RE.search(text)
        if match is not None and trust_violation is None:
            trust_violation = f"{relative} contains forbidden trust token {match.group(0)!r}"
        imports: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#print"):
                axiom_print = _AXIOM_PRINT_LINE_RE.fullmatch(line)
                if axiom_print is not None:
                    axiom_prints.append(axiom_print.group(1))
                elif stripped.startswith("#print axioms") and trust_violation is None:
                    trust_violation = f"{relative} contains a noncanonical axiom report"
            if not stripped.startswith("import"):
                continue
            import_match = _IMPORT_LINE_RE.fullmatch(line)
            if import_match is None:
                if trust_violation is None:
                    trust_violation = f"{relative} contains a noncanonical import command"
                continue
            for module in import_match.group(1).split():
                if _MODULE_RE.fullmatch(module) is None:
                    if trust_violation is None:
                        trust_violation = f"{relative} contains an unsafe import name"
                    continue
                imported_path = module.replace(".", "/") + ".lean"
                if imported_path not in known_paths and trust_violation is None:
                    trust_violation = (
                        f"{relative} imports nonlocal module {module!r}; "
                        "external libraries are disabled"
                    )
                elif imported_path in known_paths:
                    imports.append(module)
        digest = _sha256(raw)
        if relative == source_relative:
            source_hash = digest
        files.append(_SourceFile(relative, module_name, tuple(imports), raw, digest))
    if source_hash == EMPTY_HASH:
        raise ValueError("selected Lean source was not captured in the project snapshot")

    source_tree_hash = _canonical_digest(
        [
            {
                "path": item.relative_path,
                "sha256": item.digest,
                "size_bytes": len(item.raw),
            }
            for item in files
        ]
    )
    toolchain_file_hash = _sha256(toolchain_raw)
    project_manifest_hash = _canonical_digest(
        {
            "lakefile_sha256": _sha256(lakefile_raw),
            "lean_toolchain_sha256": toolchain_file_hash,
            "source_tree_sha256": source_tree_hash,
        }
    )
    by_module = {item.module_name: item for item in files}
    build_order: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str) -> None:
        nonlocal trust_violation
        if module in visited:
            return
        if module in visiting:
            if trust_violation is None:
                trust_violation = "local Lean import graph contains a cycle"
            return
        visiting.add(module)
        for imported in by_module[module].imports:
            visit(imported)
        visiting.remove(module)
        visited.add(module)
        build_order.append(by_module[module].relative_path)

    for module in sorted(by_module):
        visit(module)
    return _ProjectInputs(
        source_hash=source_hash,
        source_tree_hash=source_tree_hash,
        project_manifest_hash=project_manifest_hash,
        toolchain_file_hash=toolchain_file_hash,
        toolchain_id=toolchain_text,
        pinned_version=toolchain_match.group(1),
        files=tuple(files),
        build_order=tuple(build_order),
        axiom_prints=tuple(axiom_prints),
        lakefile_raw=lakefile_raw,
        toolchain_raw=toolchain_raw,
        trust_violation=trust_violation,
    )


@dataclass(frozen=True, slots=True)
class _ExecutableSnapshot:
    path: Path
    digest: str
    size_bytes: int
    device: int
    inode: int
    modified_ns: int


@dataclass(slots=True)
class _ExecutableLease:
    snapshot: _ExecutableSnapshot | None
    lock_enforced: bool
    handle: int | None = None
    descriptor: int | None = None

    def close(self) -> None:
        if self.handle is not None:
            handle = self.handle
            self.handle = None
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int
            kernel32.CloseHandle(ctypes.c_void_p(handle))
        if self.descriptor is not None:
            descriptor = self.descriptor
            self.descriptor = None
            os.close(descriptor)


def _native_header(header: bytes) -> bool:
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


def _resolve_executable(explicit: str | Path | None, toolchain_id: str) -> Path:
    expected_directory = toolchain_id.replace("/", "--").replace(":", "---")
    expected_name = "lean.exe" if os.name == "nt" else "lean"
    if explicit is None:
        elan_home = Path(os.environ.get("ELAN_HOME", Path.home() / ".elan"))
        candidate = elan_home / "toolchains" / expected_directory / "bin" / expected_name
    else:
        candidate = Path(explicit)
        if not candidate.is_absolute():
            raise ValueError("explicit Lean executable path must be absolute")
    resolved = candidate.resolve(strict=True)
    if (
        resolved.name.casefold() != expected_name
        or resolved.parent.name.casefold() != "bin"
        or resolved.parent.parent.name != expected_directory
    ):
        raise ValueError("Lean executable is not inside the exact pinned installed toolchain")
    if os.name == "nt" and resolved.suffix.casefold() != ".exe":
        raise ValueError("Lean command must resolve to a native .exe, not a script shim")
    if os.name != "nt" and not os.access(resolved, os.X_OK):
        raise ValueError("Lean command is not executable")
    return resolved


def _snapshot_executable(path: Path) -> _ExecutableSnapshot:
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("Lean executable is not a regular file")
            digest = hashlib.sha256()
            header = b""
            while True:
                block = stream.read(1_048_576)
                if not block:
                    break
                if not header:
                    header = block[:4]
                digest.update(block)
            after = os.fstat(stream.fileno())
        path_after = path.stat()
    except OSError as error:
        raise ValueError("Lean executable could not be inspected") from error
    identity = (
        int(before.st_dev),
        int(before.st_ino),
        int(before.st_size),
        int(before.st_mtime_ns),
    )
    if identity != (
        int(after.st_dev),
        int(after.st_ino),
        int(after.st_size),
        int(after.st_mtime_ns),
    ) or identity != (
        int(path_after.st_dev),
        int(path_after.st_ino),
        int(path_after.st_size),
        int(path_after.st_mtime_ns),
    ):
        raise ValueError("Lean executable changed while it was hashed")
    if before.st_size <= 0 or not _native_header(header):
        raise ValueError("Lean executable is not a recognized native binary")
    return _ExecutableSnapshot(
        path=path,
        digest=SHA256_PREFIX + digest.hexdigest(),
        size_bytes=int(before.st_size),
        device=int(before.st_dev),
        inode=int(before.st_ino),
        modified_ns=int(before.st_mtime_ns),
    )


def _lease_executable(path: Path) -> _ExecutableLease:
    if os.name == "nt":
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
            0x80000000,
            0x00000001,
            None,
            3,
            0x00000080,
            None,
        )
        invalid = ctypes.c_void_p(-1).value
        if handle in {None, invalid}:
            raise ValueError("Lean executable could not be locked against replacement")
        lease = _ExecutableLease(None, True, handle=int(handle))
    else:
        descriptor = os.open(path, os.O_RDONLY)
        lease = _ExecutableLease(None, False, descriptor=descriptor)
    try:
        snapshot = _snapshot_executable(path)
        if lease.descriptor is not None:
            held = os.fstat(lease.descriptor)
            if (
                int(held.st_dev),
                int(held.st_ino),
                int(held.st_size),
                int(held.st_mtime_ns),
            ) != (
                snapshot.device,
                snapshot.inode,
                snapshot.size_bytes,
                snapshot.modified_ns,
            ):
                raise ValueError("Lean executable changed before its lease was established")
        lease.snapshot = snapshot
        return lease
    except BaseException:
        lease.close()
        raise


@dataclass(frozen=True, slots=True)
class _ComputeObservation:
    digest: str
    accepted: bool
    reason: str


class _ComputeGateClosed(RuntimeError):
    pass


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"nonfinite JSON constant {value!r} is disabled")


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read_compute_observation(path: Path) -> _ComputeObservation:
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_COMPUTE_STATE_BYTES:
                raise ValueError("compute state is not a bounded regular file")
            raw = stream.read(MAX_COMPUTE_STATE_BYTES + 1)
            after = os.fstat(stream.fileno())
        if len(raw) > MAX_COMPUTE_STATE_BYTES or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ValueError("compute state changed during read")
        state = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(state, dict):
            raise ValueError("compute state is not an object")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return _ComputeObservation(EMPTY_HASH, False, "compute state is unavailable or malformed")

    digest = _sha256(raw)
    if state.get("status") != "ok":
        return _ComputeObservation(digest, False, "compute governor status is not ok")
    if state.get("mode") not in {"open", "owner_away_open"}:
        return _ComputeObservation(digest, False, "compute governor mode is not open")
    if state.get("owner_active") is not False or state.get("owner_away") is not True:
        return _ComputeObservation(digest, False, "owner is not confirmed away")
    if state.get("hung_windows") != 0:
        return _ComputeObservation(digest, False, "hung windows are present")
    if state.get("governor_suspended") is not False:
        return _ComputeObservation(digest, False, "compute governor is suspended")
    timestamp = state.get("ts")
    if not isinstance(timestamp, str):
        return _ComputeObservation(digest, False, "compute state timestamp is missing")
    try:
        observed = datetime.fromisoformat(timestamp)
        now = datetime.now(observed.tzinfo) if observed.tzinfo is not None else datetime.now()
        age = (now - observed).total_seconds()
    except ValueError:
        return _ComputeObservation(digest, False, "compute state timestamp is invalid")
    if age < -5 or age > MAX_COMPUTE_STATE_AGE_SECONDS:
        return _ComputeObservation(digest, False, "compute state is stale")
    return _ComputeObservation(digest, True, "open")


@dataclass(slots=True)
class _ComputeTracker:
    path: Path
    digests: list[str]

    def require_open(self) -> None:
        observation = _read_compute_observation(self.path)
        if not self.digests or self.digests[-1] != observation.digest:
            self.digests.append(observation.digest)
        if not observation.accepted:
            raise _ComputeGateClosed(observation.reason)

    def digest(self) -> str:
        return _canonical_digest(self.digests)


class _OutputBudget:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.used = 0
        self.overflowed = threading.Event()
        self.lock = threading.Lock()

    def retain(self, block: bytes) -> bytes:
        with self.lock:
            remaining = self.maximum - self.used
            kept = block[: max(0, remaining)]
            self.used += len(kept)
            if len(kept) != len(block):
                self.overflowed.set()
            return kept


class _PipePump(threading.Thread):
    def __init__(self, stream: BinaryIO, budget: _OutputBudget) -> None:
        super().__init__(daemon=True)
        self.stream = stream
        self.budget = budget
        self.data = bytearray()
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            while True:
                block = self.stream.read(65_536)
                if not block:
                    break
                self.data.extend(self.budget.retain(block))
        except BaseException as error:  # pragma: no cover - platform pipe failure
            self.error = error


class _TreeGuard:
    enforced = False

    def close(self) -> None:
        return


class _WindowsJobGuard(_TreeGuard):
    enforced = True

    def __init__(self, handle: int) -> None:
        self.handle: int | None = handle

    def close(self) -> None:
        if self.handle is None:
            return
        handle = self.handle
        self.handle = None
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle(ctypes.c_void_p(handle))


class _PosixGroupGuard(_TreeGuard):
    enforced = True

    def __init__(self, process_id: int) -> None:
        self.process_id: int | None = process_id

    def close(self) -> None:
        if self.process_id is None:
            return
        process_id = self.process_id
        self.process_id = None
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process_id, signal.SIGKILL)


def _windows_job_guard(process: subprocess.Popen[bytes]) -> _TreeGuard:
    class _BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimit),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.SetInformationJobObject.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    kernel32.SetInformationJobObject.restype = ctypes.c_int
    kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.AssignProcessToJobObject.restype = ctypes.c_int
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError("could not create a Lean process job")
    guard = _WindowsJobGuard(int(job))
    try:
        limits = _ExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel32.SetInformationJobObject(
            ctypes.c_void_p(job), 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            raise OSError("could not configure the Lean process job")
        process_handle = getattr(process, "_handle", None)
        if process_handle is None or not kernel32.AssignProcessToJobObject(
            ctypes.c_void_p(job), ctypes.c_void_p(int(process_handle))
        ):
            raise OSError("could not contain the Lean process tree")
        return guard
    except BaseException:
        guard.close()
        raise


def _attach_tree_guard(process: subprocess.Popen[bytes], *, standard_process: bool) -> _TreeGuard:
    if not standard_process:
        return _TreeGuard()
    if os.name == "nt":
        return _windows_job_guard(process)
    return _PosixGroupGuard(process.pid)


def _creation_options(*, standard_process: bool) -> tuple[dict[str, object], bool, bool]:
    if not standard_process:
        return {}, False, False
    if os.name == "nt":
        flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        flags |= int(getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
        flags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        return {"creationflags": flags, "startupinfo": startup}, True, True
    return {"start_new_session": True}, True, False


def _terminate_process(process: subprocess.Popen[bytes], guard: _TreeGuard) -> None:
    guard.close()
    try:
        process.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        process.terminate()
        process.wait(timeout=2)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    process.kill()
    process.wait(timeout=2)


@dataclass(frozen=True, slots=True)
class _CommandOutcome:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    reason: str
    forced_termination: bool
    output_overflowed: bool
    tree_guard_enforced: bool
    hidden_window_enforced: bool
    below_normal_priority_enforced: bool
    standard_process_used: bool


ProcessFactory = Callable[..., subprocess.Popen[bytes]]
_STANDARD_POPEN = subprocess.Popen


def _run_bounded_command(
    command: tuple[str, ...],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: int,
    output_budget: int,
    compute: _ComputeTracker,
    process_factory: ProcessFactory,
) -> _CommandOutcome:
    compute.require_open()
    standard_process = process_factory is _STANDARD_POPEN
    launch_configuration, hidden, below_normal = _creation_options(
        standard_process=standard_process
    )
    try:
        process = process_factory(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            shell=False,
            env=dict(environment),
            **launch_configuration,
        )
    except (FileNotFoundError, OSError):
        return _CommandOutcome(
            None,
            b"",
            b"",
            "Lean command could not start under the fixed local policy",
            False,
            False,
            False,
            hidden,
            below_normal,
            standard_process,
        )
    if process.stdout is None or process.stderr is None:
        try:
            process.kill()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass
        return _CommandOutcome(
            None,
            b"",
            b"",
            "Lean command pipes were not created",
            True,
            False,
            False,
            hidden,
            below_normal,
            standard_process,
        )
    try:
        guard = _attach_tree_guard(process, standard_process=standard_process)
    except OSError:
        process.kill()
        process.wait(timeout=2)
        return _CommandOutcome(
            None,
            b"",
            b"",
            "Lean process tree could not be contained",
            True,
            False,
            False,
            hidden,
            below_normal,
            standard_process,
        )
    if standard_process and os.name != "nt":
        try:
            os.setpriority(os.PRIO_PROCESS, process.pid, 10)
            below_normal = os.getpriority(os.PRIO_PROCESS, process.pid) >= 10
        except (AttributeError, OSError):
            below_normal = False

    budget = _OutputBudget(output_budget)
    stdout_pump = _PipePump(process.stdout, budget)
    stderr_pump = _PipePump(process.stderr, budget)
    stdout_pump.start()
    stderr_pump.start()
    deadline = time.monotonic() + timeout_seconds
    next_compute_check = time.monotonic() + COMPUTE_POLL_SECONDS
    forced = False
    reason = "completed"
    while process.poll() is None:
        now = time.monotonic()
        if budget.overflowed.is_set():
            reason = "Lean output exceeded the fixed byte budget"
            forced = True
            break
        if now >= deadline:
            reason = "Lean command timed out"
            forced = True
            break
        if now >= next_compute_check:
            try:
                compute.require_open()
            except _ComputeGateClosed as error:
                reason = f"Lean yielded to compute policy: {error}"
                forced = True
                break
            next_compute_check = now + COMPUTE_POLL_SECONDS
        time.sleep(PROCESS_POLL_SECONDS)
    if forced:
        _terminate_process(process, guard)
    else:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            reason = "Lean root exited but descendants retained its pipes"
            forced = True
            _terminate_process(process, guard)
        else:
            guard.close()
    stdout_pump.join(timeout=2)
    stderr_pump.join(timeout=2)
    if budget.overflowed.is_set() and reason == "completed":
        reason = "Lean output exceeded the fixed byte budget"
    if stdout_pump.is_alive() or stderr_pump.is_alive():
        reason = "Lean pipe readers did not terminate"
        forced = True
    if stdout_pump.error is not None or stderr_pump.error is not None:
        reason = "Lean diagnostics could not be read"
        forced = True
    try:
        compute.require_open()
    except _ComputeGateClosed as error:
        reason = f"Lean yielded to compute policy: {error}"
        forced = True
    return _CommandOutcome(
        int(process.returncode) if process.returncode is not None else None,
        bytes(stdout_pump.data),
        bytes(stderr_pump.data),
        reason,
        forced,
        budget.overflowed.is_set(),
        guard.enforced,
        hidden,
        below_normal,
        standard_process,
    )


def _child_environment(executable: Path) -> tuple[dict[str, str], str]:
    allowed = (
        "SystemRoot",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "PATH",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "HOME",
        "LOCALAPPDATA",
        "APPDATA",
        "ELAN_HOME",
    )
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    path_entries = [str(executable.parent)]
    system_root = environment.get("SystemRoot") or environment.get("WINDIR")
    if system_root:
        path_entries.append(str(Path(system_root) / "System32"))
    environment["PATH"] = os.pathsep.join(path_entries)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["MATH_BRAIN_LOCAL_ONLY"] = "1"
    return environment, _environment_digest(environment)


def _environment_digest(environment: Mapping[str, str]) -> str:
    fingerprint = {
        "variables": {
            key: _sha256(value.encode("utf-8")) for key, value in sorted(environment.items())
        },
        "platform": platform.system(),
        "platform_release": platform.release(),
        "machine": platform.machine(),
    }
    return _canonical_digest(fingerprint)


def _materialize_snapshot(inputs: _ProjectInputs) -> Path:
    root = Path(tempfile.mkdtemp(prefix="math-brain-lean-")).resolve()
    try:
        for relative, raw in (
            ("lakefile.toml", inputs.lakefile_raw),
            ("lean-toolchain", inputs.toolchain_raw),
            *((item.relative_path, item.raw) for item in inputs.files),
        ):
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(raw)
        return root
    except BaseException:
        _cleanup_snapshot(root)
        raise


def _cleanup_snapshot(root: Path | None) -> bool:
    if root is None:
        return True
    try:
        temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
        resolved = root.resolve(strict=True)
        if resolved.parent != temporary_root or not resolved.name.startswith("math-brain-lean-"):
            return False
        shutil.rmtree(resolved)
        return not resolved.exists()
    except OSError:
        return False


def _scrub_diagnostics(raw: bytes, replacements: Sequence[Path]) -> str:
    text = raw[:MAX_DIAGNOSTIC_EXCERPT_RAW_BYTES].decode("utf-8", errors="replace")
    for path in replacements:
        rendered = str(path)
        text = re.sub(re.escape(rendered), "<local-path>", text, flags=re.IGNORECASE)
        text = re.sub(
            re.escape(rendered.replace("\\", "/")),
            "<local-path>",
            text,
            flags=re.IGNORECASE,
        )
    return text


def _version_matches(output: bytes, pinned_version: str) -> bool:
    text = output.decode("utf-8", errors="replace")
    return re.search(rf"\b(?:version\s+)?{re.escape(pinned_version)}\b", text) is not None


def _axiom_reports_verified(output: bytes, declarations: tuple[str, ...]) -> bool:
    if not declarations:
        return False
    lines = [line.strip() for line in output.decode("utf-8", errors="replace").splitlines()]
    for declaration in declarations:
        no_axioms = f"'{declaration}' does not depend on any axioms"
        has_axioms_prefix = f"'{declaration}' depends on axioms:"
        if lines.count(no_axioms) != 1 or any(line.startswith(has_axioms_prefix) for line in lines):
            return False
    return True


def _verifier_digest() -> str:
    path = Path(__file__).resolve(strict=True)
    raw = read_stable_file(path, root=path.parent, max_file_bytes=MAX_SOURCE_TREE_BYTES)
    return _sha256(raw)


def _make_result(
    *,
    inputs: _ProjectInputs,
    nonce: str,
    verifier_digest: str,
    compute: _ComputeTracker,
    reason: str,
    available: bool = False,
    passed: bool = False,
    returncode: int | None = None,
    stdout: str = "",
    stderr: str = "",
    toolchain_version: str = "not queried",
    toolchain_version_verified: bool = False,
    checked_declarations: tuple[str, ...] = (),
    axiom_report_verified: bool = False,
    request_digest: str = EMPTY_HASH,
    output_hash: str = EMPTY_HASH,
    executable_hash: str = EMPTY_HASH,
    executable_size_bytes: int = 0,
    executable_lock_enforced: bool = False,
    expected_executable_hash_matched: bool = False,
    direct_toolchain_verified: bool = False,
    toolchain_digest: str = EMPTY_HASH,
    environment_digest: str = EMPTY_HASH,
    source_snapshot_enforced: bool = False,
    trust_policy_enforced: bool = False,
    tree_guard_enforced: bool = False,
    hidden_window_enforced: bool = False,
    below_normal_priority_enforced: bool = False,
    compute_policy_enforced: bool = False,
    standard_process_used: bool = False,
    forced_termination: bool = False,
    output_overflowed: bool = False,
    cleanup_verified: bool = True,
    network_used: bool = True,
) -> LeanResult:
    result = LeanResult(
        available=available,
        passed=passed,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        reason=reason,
        source_hash=inputs.source_hash,
        source_tree_hash=inputs.source_tree_hash,
        toolchain_version=toolchain_version,
        toolchain_version_verified=toolchain_version_verified,
        checked_declarations=checked_declarations,
        axiom_report_verified=axiom_report_verified,
        command=ALLOWED_COMMAND,
        request_digest=request_digest,
        output_hash=output_hash,
        executable_hash=executable_hash,
        executable_size_bytes=executable_size_bytes,
        executable_lock_enforced=executable_lock_enforced,
        expected_executable_hash_matched=expected_executable_hash_matched,
        direct_toolchain_verified=direct_toolchain_verified,
        project_manifest_hash=inputs.project_manifest_hash,
        toolchain_digest=toolchain_digest,
        environment_digest=environment_digest,
        compute_observations_hash=compute.digest(),
        verifier_digest=verifier_digest,
        execution_nonce=nonce,
        source_snapshot_enforced=source_snapshot_enforced,
        trust_policy_enforced=trust_policy_enforced,
        tree_guard_enforced=tree_guard_enforced,
        hidden_window_enforced=hidden_window_enforced,
        below_normal_priority_enforced=below_normal_priority_enforced,
        compute_policy_enforced=compute_policy_enforced,
        standard_process_used=standard_process_used,
        forced_termination=forced_termination,
        output_overflowed=output_overflowed,
        cleanup_verified=cleanup_verified,
        runtime_seal="hmac-sha256:" + "0" * 64,
        network_used=network_used,
    )
    return replace(result, runtime_seal=_runtime_seal(result))


def check_lean_file(
    path: str | Path,
    *,
    command: Sequence[str] = ALLOWED_COMMAND,
    project_root: str | Path | None = None,
    timeout_seconds: int = 60,
    executable: str | Path | None = None,
    expected_executable_hash: str | None = None,
    required_declarations: Sequence[str] = (),
    compute_state_path: str | Path | None = None,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
    _process_factory: ProcessFactory = _STANDARD_POPEN,
) -> LeanResult:
    """Compile a pinned, dependency-free local Lean project from an exact snapshot.

    Runtime execution is a fixed direct-Lean plan.  Lake metadata is parsed but
    Lake itself is never launched.  A caller-supplied executable hash is required
    for authority eligibility; an unpinned success remains diagnostic only.
    """

    if tuple(command) != ALLOWED_COMMAND:
        raise ValueError(
            "Lean verifier permits only the fixed command ('lean', '--snapshot-project')"
        )
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3_600:
        raise ValueError("Lean timeout must be an exact integer in [1, 3600]")
    if type(max_output_bytes) is not int or not 1_024 <= max_output_bytes <= MAX_OUTPUT_BYTES:
        raise ValueError("Lean output budget is outside the fixed safe range")
    if expected_executable_hash is not None and (
        not isinstance(expected_executable_hash, str)
        or _DIGEST_RE.fullmatch(expected_executable_hash) is None
    ):
        raise ValueError("expected Lean executable hash must be canonical SHA-256")
    checked_declarations = tuple(required_declarations)
    if (
        len(checked_declarations) > 64
        or len(set(checked_declarations)) != len(checked_declarations)
        or any(
            not isinstance(item, str) or _DECLARATION_RE.fullmatch(item) is None
            for item in checked_declarations
        )
    ):
        raise ValueError("required Lean declarations must be unique canonical names")

    source = Path(path).resolve(strict=True)
    if source.suffix.casefold() != ".lean":
        raise ValueError("Lean checker accepts only .lean files")
    root = Path(project_root).resolve(strict=True) if project_root is not None else source.parent
    if not _within(source, root):
        raise ValueError("Lean source escapes the project root")

    inputs = _strict_project_inputs(source, root)
    nonce = os.urandom(32).hex()
    verifier_digest = _verifier_digest()
    state_path = (
        Path(compute_state_path)
        if compute_state_path is not None
        else Path.home() / ".claude" / "compute" / "compute_state.json"
    )
    compute = _ComputeTracker(state_path, [])
    if inputs.trust_violation is not None:
        return _make_result(
            inputs=inputs,
            nonce=nonce,
            verifier_digest=verifier_digest,
            compute=compute,
            reason=inputs.trust_violation,
            checked_declarations=checked_declarations,
        )
    if any(inputs.axiom_prints.count(item) != 1 for item in checked_declarations):
        return _make_result(
            inputs=inputs,
            nonce=nonce,
            verifier_digest=verifier_digest,
            compute=compute,
            reason="required declaration lacks one canonical #print axioms command",
            checked_declarations=checked_declarations,
        )
    try:
        compute.require_open()
    except _ComputeGateClosed as error:
        return _make_result(
            inputs=inputs,
            nonce=nonce,
            verifier_digest=verifier_digest,
            compute=compute,
            reason=f"Lean did not start because compute policy is closed: {error}",
        )

    lease: _ExecutableLease | None = None
    try:
        executable_path = _resolve_executable(executable, inputs.toolchain_id)
        lease = _lease_executable(executable_path)
    except (FileNotFoundError, OSError, ValueError) as error:
        if lease is not None:
            lease.close()
        return _make_result(
            inputs=inputs,
            nonce=nonce,
            verifier_digest=verifier_digest,
            compute=compute,
            reason=str(error),
            compute_policy_enforced=True,
            trust_policy_enforced=True,
        )

    before = lease.snapshot
    assert before is not None
    expected_matched = expected_executable_hash == before.digest
    if expected_executable_hash is not None and not expected_matched:
        lease.close()
        return _make_result(
            inputs=inputs,
            nonce=nonce,
            verifier_digest=verifier_digest,
            compute=compute,
            reason="Lean executable does not match the caller's pinned digest",
            executable_hash=before.digest,
            executable_size_bytes=before.size_bytes,
            executable_lock_enforced=lease.lock_enforced,
            direct_toolchain_verified=True,
            compute_policy_enforced=True,
            trust_policy_enforced=True,
            network_used=False,
        )

    environment, environment_digest = _child_environment(executable_path)
    compile_policy = {
        "logical_command": ALLOWED_COMMAND,
        "version_arguments": ["--version"],
        "compile_arguments": [
            "--trust=0",
            "-j1",
            "-M1024",
            "-R",
            "<snapshot>",
            "-o",
            "<module>.olean",
            "<module>.lean",
        ],
        "build_order": list(inputs.build_order),
    }
    request_digest = _canonical_digest(
        {
            "compile_policy": compile_policy,
            "executable_sha256": before.digest,
            "source_tree_sha256": inputs.source_tree_hash,
            "project_manifest_sha256": inputs.project_manifest_hash,
            "timeout_seconds": timeout_seconds,
            "max_output_bytes": max_output_bytes,
            "network_policy": "direct-installed-toolchain-no-lake-no-api",
            "required_declarations": list(checked_declarations),
        }
    )

    snapshot_root: Path | None = None
    cleanup_verified = True
    version_outcome: _CommandOutcome | None = None
    build_outcomes: list[tuple[str, _CommandOutcome]] = []
    executable_stable = True
    failure_reason: str | None = None
    started = time.monotonic()
    deadline = started + timeout_seconds
    remaining_output = max_output_bytes
    try:
        snapshot_root = _materialize_snapshot(inputs)
        environment = dict(environment)
        environment["LEAN_PATH"] = str(snapshot_root)
        environment_digest = _environment_digest(environment)
        version_timeout = max(1, min(20, int(deadline - time.monotonic())))
        version_outcome = _run_bounded_command(
            (str(executable_path), "--version"),
            cwd=snapshot_root,
            environment=environment,
            timeout_seconds=version_timeout,
            output_budget=min(remaining_output, MAX_VERSION_OUTPUT_BYTES),
            compute=compute,
            process_factory=_process_factory,
        )
        version_raw = version_outcome.stdout + b"\n" + version_outcome.stderr
        remaining_output -= len(version_outcome.stdout) + len(version_outcome.stderr)
        version_verified = (
            version_outcome.returncode == 0
            and not version_outcome.forced_termination
            and not version_outcome.output_overflowed
            and _version_matches(version_raw, inputs.pinned_version)
        )
        if not version_verified:
            failure_reason = (
                version_outcome.reason
                if version_outcome.reason != "completed"
                else "observed Lean version does not match the pinned toolchain"
            )
        else:
            for relative in inputs.build_order:
                remaining_time = int(deadline - time.monotonic())
                if remaining_time < 1:
                    failure_reason = "Lean project build exhausted the global time budget"
                    break
                if remaining_output < 1:
                    failure_reason = "Lean project build exhausted the global output budget"
                    break
                input_path = snapshot_root / relative
                output_path = input_path.with_suffix(".olean")
                outcome = _run_bounded_command(
                    (
                        str(executable_path),
                        "--trust=0",
                        "-j1",
                        "-M1024",
                        "-R",
                        str(snapshot_root),
                        "-o",
                        str(output_path),
                        str(input_path),
                    ),
                    cwd=snapshot_root,
                    environment=environment,
                    timeout_seconds=remaining_time,
                    output_budget=remaining_output,
                    compute=compute,
                    process_factory=_process_factory,
                )
                build_outcomes.append((relative, outcome))
                remaining_output -= len(outcome.stdout) + len(outcome.stderr)
                if outcome.forced_termination or outcome.output_overflowed:
                    failure_reason = outcome.reason
                    break
                if outcome.returncode != 0:
                    failure_reason = f"Lean rejected local module {relative}"
                    break
    except (_ComputeGateClosed, OSError, ValueError) as error:
        failure_reason = f"Lean execution failed closed: {error}"
    finally:
        try:
            executable_stable = _snapshot_executable(executable_path) == before
        except ValueError:
            executable_stable = False
        lease.close()
        cleanup_verified = _cleanup_snapshot(snapshot_root)

    outcomes = tuple(
        item
        for item in (
            version_outcome,
            *(outcome for _, outcome in build_outcomes),
        )
        if item is not None
    )
    version_raw = (
        version_outcome.stdout + b"\n" + version_outcome.stderr
        if version_outcome is not None
        else b""
    )
    version_verified = (
        version_outcome is not None
        and version_outcome.returncode == 0
        and not version_outcome.forced_termination
        and not version_outcome.output_overflowed
        and _version_matches(version_raw, inputs.pinned_version)
    )
    output_records = [
        {
            "module": relative,
            "returncode": outcome.returncode,
            "stdout_sha256": _sha256(outcome.stdout),
            "stderr_sha256": _sha256(outcome.stderr),
        }
        for relative, outcome in build_outcomes
    ]
    output_hash = _canonical_digest(
        {
            "version_stdout_sha256": _sha256(
                version_outcome.stdout if version_outcome is not None else b""
            ),
            "version_stderr_sha256": _sha256(
                version_outcome.stderr if version_outcome is not None else b""
            ),
            "module_outputs": output_records,
        }
    )
    toolchain_digest = _canonical_digest(
        {
            "lean_toolchain_sha256": inputs.toolchain_file_hash,
            "lean_executable_sha256": before.digest,
            "observed_version_sha256": _sha256(version_raw),
            "version_matches_pin": version_verified,
            "direct_installed_toolchain": True,
        }
    )
    if not executable_stable:
        failure_reason = "Lean executable changed between preflight and postflight"
    if not cleanup_verified:
        failure_reason = "Lean snapshot cleanup could not be verified"

    forced = any(item.forced_termination for item in outcomes)
    overflowed = any(item.output_overflowed for item in outcomes)
    guards = bool(outcomes) and all(item.tree_guard_enforced for item in outcomes)
    hidden = bool(outcomes) and all(item.hidden_window_enforced for item in outcomes)
    below_normal = bool(outcomes) and all(item.below_normal_priority_enforced for item in outcomes)
    standard = bool(outcomes) and all(item.standard_process_used for item in outcomes)
    all_modules_built = len(build_outcomes) == len(inputs.build_order) and all(
        outcome.returncode == 0 for _, outcome in build_outcomes
    )
    passed = (
        failure_reason is None
        and version_verified
        and all_modules_built
        and not forced
        and not overflowed
        and executable_stable
        and cleanup_verified
    )
    if passed:
        reason = "Lean kernel build passed from the immutable local project snapshot"
        if not lease.lock_enforced:
            # the authority is Windows-only: elsewhere the executable is not held against replacement, so a passing
            # build is a diagnostic and can issue no LEAN_KERNEL evidence (account04/B18-R79)
            reason += ("; diagnostic only: authority unsupported on this platform (the Lean executable can be held "
                       "against replacement only on Windows), so no LEAN_KERNEL evidence can be issued")
    elif failure_reason is not None:
        reason = failure_reason
    else:
        reason = "Lean project build did not complete"

    build_stdout = b"".join(outcome.stdout for _, outcome in build_outcomes)
    build_stderr = b"".join(outcome.stderr for _, outcome in build_outcomes)
    axiom_report_verified = _axiom_reports_verified(
        build_stdout + b"\n" + build_stderr, checked_declarations
    )
    returncode = build_outcomes[-1][1].returncode if build_outcomes else None
    replacements = (
        root,
        executable_path.parent,
        *(filter(None, (snapshot_root,))),
    )
    version_text = _scrub_diagnostics(version_raw[:4_000], replacements).strip()
    reason = _scrub_diagnostics(reason.encode("utf-8"), replacements)[:500]
    return _make_result(
        inputs=inputs,
        nonce=nonce,
        verifier_digest=verifier_digest,
        compute=compute,
        reason=reason,
        available=version_outcome is not None and version_outcome.returncode is not None,
        passed=passed,
        returncode=returncode,
        stdout=_scrub_diagnostics(build_stdout, replacements),
        stderr=_scrub_diagnostics(build_stderr, replacements),
        toolchain_version=version_text[:500] or "unavailable",
        toolchain_version_verified=version_verified,
        checked_declarations=checked_declarations,
        axiom_report_verified=axiom_report_verified,
        request_digest=request_digest,
        output_hash=output_hash,
        executable_hash=before.digest,
        executable_size_bytes=before.size_bytes,
        executable_lock_enforced=lease.lock_enforced,
        expected_executable_hash_matched=expected_matched,
        direct_toolchain_verified=True,
        toolchain_digest=toolchain_digest,
        environment_digest=environment_digest,
        source_snapshot_enforced=snapshot_root is not None,
        trust_policy_enforced=True,
        tree_guard_enforced=guards,
        hidden_window_enforced=hidden,
        below_normal_priority_enforced=below_normal,
        compute_policy_enforced=True,
        standard_process_used=standard,
        forced_termination=forced,
        output_overflowed=overflowed,
        cleanup_verified=cleanup_verified,
        network_used=False,
    )


@dataclass(frozen=True, slots=True)
class LeanEvidenceAttempt:
    """One local verification result and, only on authority success, signed evidence."""

    result: LeanResult
    evidence: Evidence | None = None
    attestation: EvidenceAttestation | None = None
    receipt: EvidenceReceipt | None = None

    def __post_init__(self) -> None:
        issued = (self.evidence, self.attestation, self.receipt)
        if any(item is None for item in issued) and any(item is not None for item in issued):
            raise ValueError("Lean evidence attempt must contain all authority records or none")
        if self.evidence is not None and not self.result.authority_eligible:
            raise ValueError("ineligible Lean result cannot carry authority records")

    @property
    def issued(self) -> bool:
        return self.evidence is not None


class LocalLeanEvidenceAuthority:
    """Own one local automated key and sign only fresh sealed Lean executions."""

    __slots__ = ("_key", "authority_key_id")

    def __init__(self, authority_key_id: str, key: bytes) -> None:
        if (
            not isinstance(authority_key_id, str)
            or _AUTHORITY_ID_RE.fullmatch(authority_key_id) is None
        ):
            raise ValueError("Lean authority key id must be normalized")
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("Lean authority key must contain at least 32 bytes")
        self.authority_key_id = authority_key_id
        self._key = bytes(key)

    def verify_and_issue(
        self,
        claim: Claim,
        path: str | Path,
        *,
        required_declarations: Sequence[str],
        project_root: str | Path | None = None,
        timeout_seconds: int = 60,
        executable: str | Path | None = None,
        expected_executable_hash: str | None = None,
        compute_state_path: str | Path | None = None,
        max_output_bytes: int = MAX_OUTPUT_BYTES,
    ) -> LeanEvidenceAttempt:
        if not isinstance(claim, Claim):
            raise TypeError("Lean authority requires a Claim")
        if claim.kind not in {ClaimKind.THEOREM, ClaimKind.LEMMA}:
            raise ValueError("Lean kernel authority applies only to theorem or lemma claims")
        declarations = tuple(required_declarations)
        if not declarations:
            raise ValueError("Lean authority requires at least one named declaration")
        result = check_lean_file(
            path,
            project_root=project_root,
            timeout_seconds=timeout_seconds,
            executable=executable,
            expected_executable_hash=expected_executable_hash,
            required_declarations=declarations,
            compute_state_path=compute_state_path,
            max_output_bytes=max_output_bytes,
        )
        if (
            not result.authority_eligible
            or not result._runtime_seal_is_valid()
            or result.checked_declarations != declarations
        ):
            return LeanEvidenceAttempt(result)

        artifact_hash = result.digest()
        declaration_scope = ", ".join(result.checked_declarations)
        evidence = Evidence(
            claim_id=claim.id,
            kind=EvidenceKind.LEAN_KERNEL,
            summary="Pinned local Lean kernel accepted the exact immutable source snapshot",
            scope=f"formal declarations: {declaration_scope}",
            verifier=self.authority_key_id,
            passed=True,
            artifact_hash=artifact_hash,
            limitations=(
                "kernel acceptance does not establish prose-to-formalization fidelity",
                "authority is limited to the exact recorded source and toolchain digests",
            ),
        )
        bound_request_digest = _canonical_digest(
            {
                "claim_digest": claim.digest(),
                "declarations": list(result.checked_declarations),
                "lean_request_digest": result.request_digest,
            }
        )
        attestation = EvidenceAttestation(
            evidence_id=evidence.id,
            claim_id=claim.id,
            claim_digest=claim.digest(),
            evidence_digest=evidence.digest(),
            evidence_kind=EvidenceKind.LEAN_KERNEL,
            authority_key_id=self.authority_key_id,
            authority_role=EvidenceAuthorityRole.AUTOMATED_VERIFIER,
            authority_mode=EvidenceAuthorityMode.LOCAL_AUTOMATED,
            nonce=result.execution_nonce,
            artifact_hash=artifact_hash,
            request_digest=bound_request_digest,
            result_digest=result.digest(),
            verifier_digest=result.verifier_digest,
            toolchain_digest=result.toolchain_digest,
            source_digest=result.source_tree_hash,
            environment_digest=result.environment_digest,
            passed=True,
            marginal_cost_microusd=0,
            network_used=False,
        )
        unsigned = EvidenceReceipt(
            evidence_id=evidence.id,
            claim_id=claim.id,
            claim_digest=claim.digest(),
            evidence_digest=evidence.digest(),
            attestation_digest=attestation.digest(),
            evidence_kind=EvidenceKind.LEAN_KERNEL,
            authority_key_id=self.authority_key_id,
            authority_role=EvidenceAuthorityRole.AUTOMATED_VERIFIER,
            nonce=result.execution_nonce,
            artifact_hash=artifact_hash,
            verifier_digest=result.verifier_digest,
            toolchain_digest=result.toolchain_digest,
            source_digest=result.source_tree_hash,
            mac="hmac-sha256:" + "0" * 64,
        )
        receipt = replace(
            unsigned,
            mac=evidence_authorization_mac(unsigned, self._key),
        )
        return LeanEvidenceAttempt(result, evidence, attestation, receipt)


FIDELITY_QUESTIONS = (
    "Do formal and prose quantifiers match?",
    "Do domains and typeclass assumptions match?",
    "Are all definitions extensionally equivalent to the prose definitions?",
    "Did the formal statement add any assumption?",
    "Did the formal statement weaken or alter the conclusion?",
    "Are boundary and degenerate cases represented?",
    "Does any trusted axiom or placeholder remain?",
)


@dataclass(frozen=True, slots=True)
class FormalStatementBinding:
    """Exact source bytes and line span reviewed for one named Lean declaration."""

    declaration: str
    source_path: str
    source_file_hash: str
    statement_hash: str
    start_line: int
    end_line: int

    def __post_init__(self) -> None:
        if not isinstance(self.declaration, str) or _DECLARATION_RE.fullmatch(
            self.declaration
        ) is None:
            raise ValueError("formal statement binding requires a canonical declaration")
        if not isinstance(self.source_path, str) or len(self.source_path) > 512:
            raise ValueError("formal statement binding source path is invalid")
        pure = PurePosixPath(self.source_path)
        if (
            not self.source_path
            or "\\" in self.source_path
            or pure.is_absolute()
            or pure.as_posix() != self.source_path
            or any(part in {"", ".", ".."} for part in pure.parts)
            or pure.suffix.casefold() != ".lean"
        ):
            raise ValueError("formal statement binding source path must be relative .lean")
        for name, value in (
            ("source file hash", self.source_file_hash),
            ("statement hash", self.statement_hash),
        ):
            if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
                raise ValueError(f"formal statement binding {name} must be SHA-256")
        for name, value in (("start line", self.start_line), ("end line", self.end_line)):
            if type(value) is not int or value < 1:
                raise ValueError(f"formal statement binding {name} must be positive")
        if self.end_line < self.start_line:
            raise ValueError("formal statement binding line range is reversed")

    def digest(self) -> str:
        return _canonical_digest(asdict(self))


def _line_span(raw: bytes, start_line: int, end_line: int) -> bytes:
    lines = raw.splitlines(keepends=True)
    if start_line > len(lines) or end_line > len(lines):
        raise ValueError("formal statement binding line range exceeds the source file")
    return b"".join(lines[start_line - 1 : end_line])


def _fragment_names_declaration(fragment: bytes, declaration: str) -> bool:
    try:
        text = fragment.decode("utf-8")
    except UnicodeDecodeError:
        return False
    short_name = declaration.rsplit(".", 1)[-1]
    header = re.compile(
        rf"(?m)^[ \t]*(?:(?:private|protected|noncomputable)\s+)*"
        rf"(?:theorem|lemma|def|abbrev|structure|class|inductive)\s+"
        rf"{re.escape(short_name)}(?=[^A-Za-z0-9_']|\Z)"
    )
    return len(header.findall(text)) == 1


def claim_statement_hash(claim: Claim) -> str:
    """Hash exactly the UTF-8 prose statement carried by a Claim."""

    if not isinstance(claim, Claim):
        raise TypeError("claim statement hashing requires a Claim")
    return _sha256(claim.statement.encode("utf-8"))


def bind_formal_statement(
    project_root: str | Path,
    source_path: str,
    declaration: str,
    *,
    start_line: int,
    end_line: int,
) -> FormalStatementBinding:
    """Stable-read and hash the exact local source span a human will review."""

    placeholder_hash = SHA256_PREFIX + "0" * 64
    candidate = FormalStatementBinding(
        declaration=declaration,
        source_path=source_path,
        source_file_hash=placeholder_hash,
        statement_hash=placeholder_hash,
        start_line=start_line,
        end_line=end_line,
    )
    root = Path(project_root).resolve(strict=True)
    source = root.joinpath(*PurePosixPath(candidate.source_path).parts)
    raw = read_stable_file(source, root=root, max_file_bytes=MAX_SOURCE_FILE_BYTES)
    fragment = _line_span(raw, candidate.start_line, candidate.end_line)
    if not _fragment_names_declaration(fragment, candidate.declaration):
        raise ValueError("formal statement span must contain exactly one named declaration")
    return replace(
        candidate,
        source_file_hash=_sha256(raw),
        statement_hash=_sha256(fragment),
    )


@dataclass(frozen=True, slots=True)
class StatementFidelityReview:
    claim_digest: str
    prose_claim_hash: str
    lean_result_digest: str
    source_tree_hash: str
    statement_bindings: tuple[FormalStatementBinding, ...]
    reviewer_key_id: str
    quantifiers_match: bool
    domains_match: bool
    definitions_match: bool
    assumptions_match: bool
    conclusion_match: bool
    boundary_cases_match: bool
    no_extra_axioms: bool
    notes: str = ""

    def __post_init__(self) -> None:
        for name, value in (
            ("claim digest", self.claim_digest),
            ("prose claim hash", self.prose_claim_hash),
            ("Lean result digest", self.lean_result_digest),
            ("source tree hash", self.source_tree_hash),
        ):
            if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
                raise ValueError(f"fidelity review {name} must be SHA-256")
        if (
            not isinstance(self.statement_bindings, tuple)
            or not self.statement_bindings
            or len(self.statement_bindings) > 64
            or any(
                not isinstance(binding, FormalStatementBinding)
                for binding in self.statement_bindings
            )
        ):
            raise ValueError("fidelity review requires bounded formal statement bindings")
        declarations = tuple(binding.declaration for binding in self.statement_bindings)
        if len(set(declarations)) != len(declarations):
            raise ValueError("fidelity review declaration bindings must be unique")
        if not isinstance(self.reviewer_key_id, str) or _AUTHORITY_ID_RE.fullmatch(
            self.reviewer_key_id
        ) is None:
            raise ValueError("fidelity review requires a normalized reviewer key id")
        for name, value in (
            ("quantifiers_match", self.quantifiers_match),
            ("domains_match", self.domains_match),
            ("definitions_match", self.definitions_match),
            ("assumptions_match", self.assumptions_match),
            ("conclusion_match", self.conclusion_match),
            ("boundary_cases_match", self.boundary_cases_match),
            ("no_extra_axioms", self.no_extra_axioms),
        ):
            if type(value) is not bool:
                raise TypeError(f"fidelity review {name} must be an exact boolean")
        if not isinstance(self.notes, str) or len(self.notes.encode("utf-8")) > (
            MAX_FIDELITY_NOTES_BYTES
        ):
            raise ValueError("fidelity review notes exceed the bounded UTF-8 budget")
        if "\x00" in self.notes:
            raise ValueError("fidelity review notes cannot contain NUL")

    @property
    def formal_statement_hash(self) -> str:
        return _canonical_digest([asdict(binding) for binding in self.statement_bindings])

    def digest(self) -> str:
        payload = asdict(self)
        payload["formal_statement_hash"] = self.formal_statement_hash
        return _canonical_digest(payload)

    @property
    def passed(self) -> bool:
        return all(
            (
                self.quantifiers_match,
                self.domains_match,
                self.definitions_match,
                self.assumptions_match,
                self.conclusion_match,
                self.boundary_cases_match,
                self.no_extra_axioms,
            )
        )


@dataclass(frozen=True, slots=True)
class FidelityEvidenceAttempt:
    """One human review and, only on full binding success, signed evidence."""

    review: StatementFidelityReview
    evidence: Evidence | None = None
    attestation: EvidenceAttestation | None = None
    receipt: EvidenceReceipt | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.review, StatementFidelityReview):
            raise TypeError("fidelity evidence attempt requires a statement review")
        issued = (self.evidence, self.attestation, self.receipt)
        if any(item is None for item in issued) and any(item is not None for item in issued):
            raise ValueError("fidelity attempt must contain all authority records or none")
        if self.evidence is not None and not self.review.passed:
            raise ValueError("failed fidelity review cannot carry authority records")

    @property
    def issued(self) -> bool:
        return self.evidence is not None


class LocalFormalizationFidelityAuthority:
    """Own a human-review key and sign only exact, independently bound reviews."""

    __slots__ = ("_key", "authority_key_id")

    def __init__(self, authority_key_id: str, key: bytes) -> None:
        if (
            not isinstance(authority_key_id, str)
            or _AUTHORITY_ID_RE.fullmatch(authority_key_id) is None
        ):
            raise ValueError("fidelity authority key id must be normalized")
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("fidelity authority key must contain at least 32 bytes")
        self.authority_key_id = authority_key_id
        self._key = bytes(key)

    @staticmethod
    def _matches_exact_sources(
        claim: Claim,
        result: LeanResult,
        review: StatementFidelityReview,
        path: str | Path,
        project_root: str | Path | None,
    ) -> bool:
        if (
            review.claim_digest != claim.digest()
            or review.prose_claim_hash != claim_statement_hash(claim)
            or review.lean_result_digest != result.digest()
            or review.source_tree_hash != result.source_tree_hash
            or tuple(binding.declaration for binding in review.statement_bindings)
            != result.checked_declarations
        ):
            return False
        source = Path(path).resolve(strict=True)
        root = (
            Path(project_root).resolve(strict=True)
            if project_root is not None
            else source.parent
        )
        if source.suffix.casefold() != ".lean" or not _within(source, root):
            return False
        inputs = _strict_project_inputs(source, root)
        if (
            inputs.trust_violation is not None
            or inputs.source_tree_hash != result.source_tree_hash
            or inputs.project_manifest_hash != result.project_manifest_hash
        ):
            return False
        files = {item.relative_path: item for item in inputs.files}
        for binding in review.statement_bindings:
            source_file = files.get(binding.source_path)
            if source_file is None or source_file.digest != binding.source_file_hash:
                return False
            try:
                fragment = _line_span(
                    source_file.raw,
                    binding.start_line,
                    binding.end_line,
                )
            except ValueError:
                return False
            if (
                _sha256(fragment) != binding.statement_hash
                or not _fragment_names_declaration(fragment, binding.declaration)
            ):
                return False
        return True

    def review_and_issue(
        self,
        claim: Claim,
        result: LeanResult,
        review: StatementFidelityReview,
        path: str | Path,
        *,
        project_root: str | Path | None = None,
    ) -> FidelityEvidenceAttempt:
        if not isinstance(claim, Claim):
            raise TypeError("fidelity authority requires a Claim")
        if claim.kind not in {ClaimKind.DEFINITION, ClaimKind.LEMMA, ClaimKind.THEOREM}:
            raise ValueError("fidelity authority applies only to formalizable claims")
        if not isinstance(result, LeanResult):
            raise TypeError("fidelity authority requires a LeanResult")
        if not isinstance(review, StatementFidelityReview):
            raise TypeError("fidelity authority requires a StatementFidelityReview")
        if (
            review.reviewer_key_id != self.authority_key_id
            or not review.passed
            or not review.notes.strip()
            or not result.authority_eligible
            or not result._runtime_seal_is_valid()
            or not self._matches_exact_sources(claim, result, review, path, project_root)
        ):
            return FidelityEvidenceAttempt(review)

        artifact_hash = review.digest()
        declaration_scope = ", ".join(result.checked_declarations)
        evidence = Evidence(
            claim_id=claim.id,
            kind=EvidenceKind.FORMALIZATION_FIDELITY,
            summary="Independent human review accepted the exact prose-to-Lean bindings",
            scope=f"formal declarations: {declaration_scope}",
            verifier=self.authority_key_id,
            passed=True,
            artifact_hash=artifact_hash,
            limitations=(
                "semantic judgment is limited to the exact recorded claim and source digests",
                "fidelity review does not replace Lean kernel acceptance",
            ),
        )
        request_digest = _canonical_digest(
            {
                "claim_digest": claim.digest(),
                "lean_result_digest": result.digest(),
                "review_digest": review.digest(),
                "source_tree_hash": result.source_tree_hash,
                "formal_statement_hash": review.formal_statement_hash,
                "declarations": list(result.checked_declarations),
            }
        )
        verifier_digest = _canonical_digest(
            {
                "implementation": "local-formalization-fidelity-authority-v1",
                "authority_key_id": self.authority_key_id,
                "authority_role": EvidenceAuthorityRole.HUMAN_REVIEWER.value,
                "questions": list(FIDELITY_QUESTIONS),
            }
        )
        environment_digest = _canonical_digest(
            {
                "authority_mode": EvidenceAuthorityMode.LOCAL_HUMAN_REVIEW.value,
                "marginal_cost_microusd": 0,
                "network_used": False,
            }
        )
        nonce = os.urandom(32).hex()
        attestation = EvidenceAttestation(
            evidence_id=evidence.id,
            claim_id=claim.id,
            claim_digest=claim.digest(),
            evidence_digest=evidence.digest(),
            evidence_kind=EvidenceKind.FORMALIZATION_FIDELITY,
            authority_key_id=self.authority_key_id,
            authority_role=EvidenceAuthorityRole.HUMAN_REVIEWER,
            authority_mode=EvidenceAuthorityMode.LOCAL_HUMAN_REVIEW,
            nonce=nonce,
            artifact_hash=artifact_hash,
            request_digest=request_digest,
            result_digest=review.digest(),
            verifier_digest=verifier_digest,
            toolchain_digest=result.toolchain_digest,
            source_digest=result.source_tree_hash,
            environment_digest=environment_digest,
            passed=True,
            marginal_cost_microusd=0,
            network_used=False,
        )
        unsigned = EvidenceReceipt(
            evidence_id=evidence.id,
            claim_id=claim.id,
            claim_digest=claim.digest(),
            evidence_digest=evidence.digest(),
            attestation_digest=attestation.digest(),
            evidence_kind=EvidenceKind.FORMALIZATION_FIDELITY,
            authority_key_id=self.authority_key_id,
            authority_role=EvidenceAuthorityRole.HUMAN_REVIEWER,
            nonce=nonce,
            artifact_hash=artifact_hash,
            verifier_digest=verifier_digest,
            toolchain_digest=result.toolchain_digest,
            source_digest=result.source_tree_hash,
            mac="hmac-sha256:" + "0" * 64,
        )
        receipt = replace(unsigned, mac=evidence_authorization_mac(unsigned, self._key))
        return FidelityEvidenceAttempt(review, evidence, attestation, receipt)
