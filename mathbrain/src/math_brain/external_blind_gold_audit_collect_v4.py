"""Physical lifecycle and exact-ledger collector for external-gold audit v4.

Importing this module never launches a model.  Production collection is an
explicit foreground call that requires an externally acknowledged precommitment
and an O_EXCL campaign lease held for the complete collection.  Crash recovery is
separate and requires a signed exclusive-ownership acknowledgement; ordinary
collectors never orphan-recover a live STARTED slot.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from .attempt_ledger import (
    ArtifactPayload,
    AttemptLedger,
    AttemptRunnerFailed,
    DispatchMaterial,
    LedgerIntegrityError,
    OutcomeKind,
    SlotCommitment,
    SlotState,
    TerminalTransition,
    new_slot_id,
    secret_commitment,
)
from .canonical_ed25519_v3 import (
    CanonicalEd25519SignerV3,
    CanonicalEd25519VerifierV3,
)
from .codex_artifact_io import deserialize_codex_run_result, serialize_codex_run_result
from .codex_runner import (
    DEFAULT_BASE_INSTRUCTIONS,
    DEFAULT_DEVELOPER_INSTRUCTIONS,
    MAX_AUTH_FILE_BYTES,
    CodexAppServerRunner,
    CodexRunConfig,
    CodexRunResult,
    ExecutableProvenance,
    app_server_command_tail_policy_digest,
    capture_codex_executable_provenance,
    solver_process_isolation_policy_digest,
)
from .external_blind_gold_audit_v4 import (
    EXTERNAL_GOLD_AUDIT_VERSION_V4,
    EXTERNAL_GOLD_REPORT_SIGNING_DOMAIN_V4,
    CollectedExternalGoldAuditV4,
    ExternalGoldAttemptEvidenceV4,
    ExternalGoldAuditDecisionV4,
    ExternalGoldAuditPlanV4,
    ExternalGoldAuditReceiptV4,
    ExternalGoldReceiptStatusV4,
    ExternalGoldReviewerSlotV4,
    ExternalGoldRunEvidenceV4,
    RandomExternalColdReplayEvidenceV4,
    RandomExternalGoldSelectionBindingV4,
    SealedExternalGoldVaultCommitmentV4,
    _fresh_random_external_replay_v4,
    _mint_cold_replayed_campaign_v4,
    authenticate_signed_external_gold_audit_pair_v4,
    build_external_gold_audit_plan_v4,
    external_gold_reviewer_signing_domain_v4,
    parse_external_gold_audit_output_v4,
    serialize_signed_external_gold_audit_pair_v4,
    sign_external_gold_audit_receipt_v4,
)
from .models import canonical_json

EXTERNAL_GOLD_REQUESTED_MODEL_V4 = "gpt-5.6-sol"
EXTERNAL_GOLD_REQUESTED_PROVIDER_V4 = "openai"
EXTERNAL_GOLD_REQUESTED_EFFORT_V4 = "high"
EXTERNAL_GOLD_TIMEOUT_SECONDS_V4 = 900
EXTERNAL_GOLD_PRIVATE_ARTIFACT_MEDIA_TYPE_V4 = "application/json"
EXTERNAL_GOLD_RUN_MAX_EVENT_LINE_BYTES_V4 = 2_000_000
EXTERNAL_GOLD_RUN_MAX_TRANSCRIPT_BYTES_V4 = 32_000_000
EXTERNAL_GOLD_RUN_MAX_STDERR_BYTES_V4 = 1_000_000
EXTERNAL_GOLD_PRIVATE_STATE_MAX_BYTES_V4 = 2_000_000
EXTERNAL_GOLD_PUBLIC_PRECOMMIT_MAX_BYTES_V4 = 1_000_000

EXTERNAL_GOLD_CORE_SCHEMA_V4 = "org.math-research-brain/engineering-pilot-external-gold-core-v4"
EXTERNAL_GOLD_PRIVATE_STATE_SCHEMA_V4 = (
    "org.math-research-brain/engineering-pilot-external-gold-private-state-v4"
)
EXTERNAL_GOLD_PUBLIC_PRECOMMIT_SCHEMA_V4 = (
    "org.math-research-brain/engineering-pilot-external-gold-precommit-v4"
)
EXTERNAL_GOLD_HOME_RECEIPT_SCHEMA_V4 = (
    "org.math-research-brain/engineering-pilot-external-gold-home-receipt-v4"
)
EXTERNAL_GOLD_ANCHOR_ACK_SCHEMA_V4 = (
    "org.math-research-brain/engineering-pilot-external-gold-anchor-ack-v4"
)
EXTERNAL_GOLD_RECOVERY_ACK_SCHEMA_V4 = (
    "org.math-research-brain/engineering-pilot-external-gold-recovery-ack-v4"
)
EXTERNAL_GOLD_ANCHOR_SIGNING_DOMAIN_V4 = "org.math-research-brain/external-gold-anchor-witness/v4"
EXTERNAL_GOLD_RECOVERY_SIGNING_DOMAIN_V4 = (
    "org.math-research-brain/external-gold-recovery-witness/v4"
)

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_HMAC_RE = re.compile(r"hmac-sha256:[0-9a-f]{64}\Z", re.ASCII)
_GIT_OID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z", re.ASCII)
_TAG_RE = re.compile(r"external-blind-gold-audit-v4-[0-9a-z._-]{1,80}\Z", re.ASCII)
_SIGNATURE_RE = re.compile(r"ed25519:[0-9a-f]{128}\Z", re.ASCII)
_LOCK_NONCE_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)

_PRIVATE_STATE_NAME = "private-state.json"
_LEDGER_NAME = "attempt-ledger"
_PRECOMMIT_NAME = "public-precommitment.json"
_HOME_RECEIPT_NAME = "ledger-home-receipt.json"
_ANCHOR_ACK_NAME = "external-anchor-acknowledgement.json"
_SIGNED_PAIR_NAME = "signed-review-pair.json"
_LIFECYCLE_LOCK_NAME = "lifecycle.lock"
_RECOVERY_CLAIM_NAME = "recovery.claim"
_RECOVERED_LOCK_NAME = "recovered-lifecycle-lock.json"
_RECOVERY_ACK_NAME = "post-crash-ownership-acknowledgement.json"
_STATE_NAMES = (
    "state-00-prepared.json",
    "state-01-anchored.json",
    "state-02-collecting.json",
    "state-03-terminal.json",
)


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical SHA-256 digest")
    return value


def _hmac_value(value: object, label: str) -> str:
    if type(value) is not str or _HMAC_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical HMAC-SHA-256 commitment")
    return value


def _key(value: object, label: str) -> bytes:
    if type(value) is not bytes or len(value) != 32:
        raise ValueError(f"{label} must contain exactly 32 immutable bytes")
    return value


def _canonical_bytes(value: object, *, lf: bool = False) -> bytes:
    raw = canonical_json(value).encode("utf-8")
    return raw + (b"\n" if lf else b"")


def _domain_digest(value: object, *, domain: str) -> str:
    return _sha256(domain.encode("ascii") + b"\x00" + _canonical_bytes(value))


def _exact_mapping(value: object, keys: set[str], label: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError(f"{label} has unexpected fields")
    return value


def _decode_canonical_json(raw: bytes, *, maximum: int, label: str) -> dict[str, object]:
    if type(raw) is not bytes or not raw.endswith(b"\n") or not 1 < len(raw) <= maximum:
        raise ValueError(f"{label} framing is invalid")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, value in pairs:
            if name in result:
                raise ValueError(f"{label} contains a duplicate key")
            result[name] = value
        return result

    try:
        value = json.loads(
            raw[:-1].decode("utf-8", errors="strict"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ValueError(f"{label} contains non-finite {constant}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise ValueError(f"{label} is not strict JSON") from error
    if type(value) is not dict or _canonical_bytes(value, lf=True) != raw:
        raise ValueError(f"{label} is not canonical JSON")
    return value


def _fsync_directory(path: Path) -> None:
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


def _open_exclusive(path: Path, raw: bytes) -> tuple[int, os.stat_result]:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
    for optional in ("O_BINARY", "O_NOINHERIT", "O_NOFOLLOW"):
        flags |= int(getattr(os, optional, 0))
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:  # pragma: no cover - OS contract defense
                raise OSError("exclusive write made no progress")
            view = view[written:]
        os.fsync(descriptor)
        status = os.fstat(descriptor)
        _fsync_directory(path.parent)
        return descriptor, status
    except BaseException:
        os.close(descriptor)
        with suppress(FileNotFoundError):
            path.unlink()
        raise


def _write_exclusive(path: Path, raw: bytes) -> None:
    descriptor, _status = _open_exclusive(path, raw)
    os.close(descriptor)


def _read_single_regular(path: Path, *, maximum: int) -> tuple[bytes, os.stat_result]:
    if type(maximum) is not int or maximum <= 0:
        raise ValueError("campaign artifact byte budget is invalid")
    descriptor: int | None = None
    try:
        path_before = path.lstat()
        if (
            stat.S_ISLNK(path_before.st_mode)
            or not stat.S_ISREG(path_before.st_mode)
            or path_before.st_nlink != 1
            or not 0 < path_before.st_size <= maximum
        ):
            raise ValueError("campaign artifact is not one physical regular file")
        flags = os.O_RDONLY
        for optional in ("O_BINARY", "O_NOINHERIT", "O_NOFOLLOW"):
            flags |= int(getattr(os, optional, 0))
        descriptor = os.open(path, flags)
        descriptor_before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(descriptor_before.st_mode)
            or descriptor_before.st_nlink != 1
            or (path_before.st_dev, path_before.st_ino)
            != (descriptor_before.st_dev, descriptor_before.st_ino)
        ):
            raise ValueError("campaign artifact changed while it was opened")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1_048_576, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        descriptor_after = os.fstat(descriptor)
        path_after = path.lstat()
    except OSError as error:
        raise ValueError("campaign artifact cannot be read") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    identity_before = (
        path_before.st_dev,
        path_before.st_ino,
        path_before.st_size,
        path_before.st_mtime_ns,
    )
    if (
        identity_before
        != (
            descriptor_before.st_dev,
            descriptor_before.st_ino,
            descriptor_before.st_size,
            descriptor_before.st_mtime_ns,
        )
        or identity_before
        != (
            descriptor_after.st_dev,
            descriptor_after.st_ino,
            descriptor_after.st_size,
            descriptor_after.st_mtime_ns,
        )
        or identity_before
        != (
            path_after.st_dev,
            path_after.st_ino,
            path_after.st_size,
            path_after.st_mtime_ns,
        )
        or descriptor_after.st_nlink != 1
        or path_after.st_nlink != 1
        or not 0 < len(raw) <= maximum
        or len(raw) != path_before.st_size
    ):
        raise ValueError("campaign artifact changed during inspection")
    return raw, descriptor_after


def _path_identity(path: Path, *, directory: bool) -> tuple[Path, int, int]:
    raw = path
    if not raw.is_absolute() or raw.is_symlink():
        raise ValueError("campaign path must be absolute and non-symlink")
    resolved = raw.resolve(strict=True)
    status = resolved.lstat()
    if (
        resolved != raw
        or (directory and not stat.S_ISDIR(status.st_mode))
        or (not directory and not stat.S_ISREG(status.st_mode))
    ):
        raise ValueError("campaign path identity is not canonical")
    return resolved, int(status.st_dev), int(status.st_ino)


def _attempt_ledger_physical_snapshot(root: Path) -> tuple[tuple[object, ...], ...]:
    """Certify one stable physical name for every exact ledger file and directory."""

    directories = (
        root,
        root / "objects",
        root / "objects" / "sha256",
        root / "staging",
        root / "transitions",
    )
    snapshot: list[tuple[object, ...]] = []
    for directory in directories:
        resolved, device, inode = _path_identity(directory, directory=True)
        snapshot.append(("directory", str(resolved), device, inode))
    if {entry.name for entry in root.iterdir()} != {
        "objects",
        "precommit.json",
        "staging",
        "transitions",
    } or {entry.name for entry in (root / "objects").iterdir()} != {"sha256"}:
        raise ValueError("external-gold attempt ledger inventory differs")
    if next((root / "staging").iterdir(), None) is not None:
        raise ValueError("external-gold attempt ledger staging is not empty")
    transition_paths = sorted(
        (root / "transitions").iterdir(),
        key=lambda path: path.name,
    )
    object_paths = sorted(
        (root / "objects" / "sha256").iterdir(),
        key=lambda path: path.name,
    )
    if len(transition_paths) > 6 or len(object_paths) > 2:
        raise ValueError("external-gold attempt ledger exceeds its exact two-slot inventory")
    file_paths = [root / "precommit.json"]
    file_paths.extend(transition_paths)
    file_paths.extend(object_paths)
    physical_names: set[tuple[int, int]] = set()
    for path in file_paths:
        _raw, status = _read_single_regular(path, maximum=128 * 1024 * 1024)
        identity = (int(status.st_dev), int(status.st_ino))
        if identity in physical_names:
            raise ValueError("external-gold attempt ledger reuses one physical file")
        physical_names.add(identity)
        snapshot.append(
            (
                "file",
                path.relative_to(root).as_posix(),
                *identity,
                int(status.st_size),
                int(status.st_mtime_ns),
            )
        )
    return tuple(snapshot)


def _git(repository: Path, *arguments: str) -> str:
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=30,
        creationflags=creationflags,
    )
    if result.returncode != 0 or result.stderr.strip():
        raise ValueError("external-gold implementation Git state cannot be verified")
    return result.stdout.strip()


def _expected_request_policy_material(
    policy: ExternalGoldAuditRunPolicyV4,
) -> dict[str, object]:
    return {
        "base_instructions": policy.base_instructions,
        "developer_instructions": policy.developer_instructions,
        "fixed_thread_policy": {
            "allow_provider_model_fallback": False,
            "approval_policy": "never",
            "dynamic_tools": [],
            "environments": [],
            "ephemeral": True,
            "experimental_raw_events": True,
            "sandbox": "read-only",
        },
        "fixed_turn_policy": {"approval_policy": "never", "environments": []},
        "requested_model": policy.requested_model,
        "requested_provider": policy.requested_provider,
        "requested_reasoning_effort": policy.requested_effort,
        "timeout_seconds": policy.timeout_seconds,
    }


@dataclass(frozen=True, slots=True)
class ExternalGoldAuditRunPolicyV4:
    executable_provenance: ExecutableProvenance
    requested_model: str = EXTERNAL_GOLD_REQUESTED_MODEL_V4
    requested_provider: str = EXTERNAL_GOLD_REQUESTED_PROVIDER_V4
    requested_effort: str = EXTERNAL_GOLD_REQUESTED_EFFORT_V4
    timeout_seconds: int = EXTERNAL_GOLD_TIMEOUT_SECONDS_V4
    max_workers: int = 1
    no_retry: bool = True
    base_instructions: str = DEFAULT_BASE_INSTRUCTIONS
    developer_instructions: str = DEFAULT_DEVELOPER_INSTRUCTIONS

    def __post_init__(self) -> None:
        if (
            type(self) is not ExternalGoldAuditRunPolicyV4
            or type(self.executable_provenance) is not ExecutableProvenance
        ):
            raise ValueError("external-gold run policy is inexact")
        self.executable_provenance.__post_init__()
        fixed = (
            (self.requested_model, EXTERNAL_GOLD_REQUESTED_MODEL_V4, "model"),
            (self.requested_provider, EXTERNAL_GOLD_REQUESTED_PROVIDER_V4, "provider"),
            (self.requested_effort, EXTERNAL_GOLD_REQUESTED_EFFORT_V4, "effort"),
            (self.base_instructions, DEFAULT_BASE_INSTRUCTIONS, "base instructions"),
            (
                self.developer_instructions,
                DEFAULT_DEVELOPER_INSTRUCTIONS,
                "developer instructions",
            ),
        )
        for actual, expected, label in fixed:
            if type(actual) is not str or actual != expected:
                raise ValueError(f"external-gold {label} differs from fixed policy")
        if type(self.timeout_seconds) is not int or self.timeout_seconds != 900:
            raise ValueError("external-gold timeout differs from fixed policy")
        if type(self.max_workers) is not int or self.max_workers != 1 or self.no_retry is not True:
            raise ValueError("external-gold collection must be sequential and no-retry")

    @property
    def command_policy_digest(self) -> str:
        return app_server_command_tail_policy_digest(self.requested_model, self.requested_effort)

    @property
    def process_policy_digest(self) -> str:
        return solver_process_isolation_policy_digest()

    @property
    def request_policy_digest(self) -> str:
        return _domain_digest(
            _expected_request_policy_material(self),
            domain="org.math-research-brain/external-gold-request-policy/v4",
        )

    def public_payload(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "base_instruction_digest": _sha256(self.base_instructions.encode("utf-8")),
            "command_policy_digest": self.command_policy_digest,
            "developer_instruction_digest": _sha256(self.developer_instructions.encode("utf-8")),
            "executable": {
                "sha256_digest": self.executable_provenance.sha256_digest,
                "size_bytes": self.executable_provenance.size_bytes,
            },
            "foreground_synchronous": True,
            "max_workers": 1,
            "no_retry": True,
            "process_isolation_policy_digest": self.process_policy_digest,
            "requested_effort": self.requested_effort,
            "requested_model": self.requested_model,
            "requested_provider": self.requested_provider,
            "request_policy_digest": self.request_policy_digest,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True, slots=True)
class ExternalGoldCodeIdentityV4:
    repository_root: str
    commit_oid: str
    annotated_tag: str
    tag_object_oid: str

    def __post_init__(self) -> None:
        if type(self) is not ExternalGoldCodeIdentityV4:
            raise ValueError("external-gold code identity subclasses are forbidden")
        root = Path(self.repository_root)
        if type(self.repository_root) is not str or not root.is_absolute():
            raise ValueError("external-gold repository root must be absolute")
        if type(self.commit_oid) is not str or _GIT_OID_RE.fullmatch(self.commit_oid) is None:
            raise ValueError("external-gold commit oid is invalid")
        if (
            type(self.tag_object_oid) is not str
            or _GIT_OID_RE.fullmatch(self.tag_object_oid) is None
        ):
            raise ValueError("external-gold tag-object oid is invalid")
        if type(self.annotated_tag) is not str or _TAG_RE.fullmatch(self.annotated_tag) is None:
            raise ValueError("external-gold annotated tag is invalid")

    def public_payload(self) -> dict[str, str]:
        self.__post_init__()
        return {
            "annotated_tag": self.annotated_tag,
            "commit_oid": self.commit_oid,
            "tag_object_oid": self.tag_object_oid,
        }


def capture_external_gold_code_identity_v4(
    repository_root: str | Path,
    *,
    annotated_tag: str,
) -> ExternalGoldCodeIdentityV4:
    raw = Path(repository_root)
    if (
        not raw.is_absolute()
        or type(annotated_tag) is not str
        or _TAG_RE.fullmatch(annotated_tag) is None
    ):
        raise ValueError("external-gold code preflight inputs are invalid")
    root = raw.resolve(strict=True)
    if Path(_git(root, "rev-parse", "--show-toplevel")).resolve(strict=True) != root:
        raise ValueError("external-gold repository path is not the worktree root")
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("external-gold implementation worktree is not clean")
    head = _git(root, "rev-parse", "--verify", "HEAD")
    tag_ref = f"refs/tags/{annotated_tag}"
    tag_oid = _git(root, "rev-parse", "--verify", f"{tag_ref}^{{tag}}")
    peeled = _git(root, "rev-parse", "--verify", f"{tag_ref}^{{commit}}")
    if _git(root, "cat-file", "-t", tag_oid) != "tag" or peeled != head:
        raise ValueError("external-gold annotated tag does not name exact HEAD")
    critical = (
        "src/math_brain/attempt_ledger.py",
        "src/math_brain/canonical_ed25519_v3.py",
        "src/math_brain/codex_artifact_io.py",
        "src/math_brain/codex_runner.py",
        "src/math_brain/exact_answer_v3.py",
        "src/math_brain/external_blind_gold_audit_collect_v4.py",
        "src/math_brain/external_blind_gold_audit_v4.py",
        "src/math_brain/mathnet_bank_v3.py",
        "src/math_brain/mathnet_random_external_lane_v4.py",
        "src/math_brain/models.py",
    )
    for relative in critical:
        if _git(root, "ls-files", "--error-unmatch", "--", relative) != relative:
            raise ValueError("external-gold critical dependency is not tracked")
        working = _git(root, "hash-object", f"--path={relative}", "--", relative)
        committed = _git(root, "rev-parse", f"HEAD:{relative}")
        if working != committed:
            raise ValueError("external-gold critical dependency differs from tagged commit")
    return ExternalGoldCodeIdentityV4(
        repository_root=str(root),
        commit_oid=head,
        annotated_tag=annotated_tag,
        tag_object_oid=tag_oid,
    )


@dataclass(frozen=True, slots=True)
class ExternalGoldAuthSnapshotV4:
    canonical_path: str
    device: int
    inode: int
    size_bytes: int
    content_commitment: str

    def __post_init__(self) -> None:
        if (
            type(self) is not ExternalGoldAuthSnapshotV4
            or not Path(self.canonical_path).is_absolute()
        ):
            raise ValueError("external-gold auth snapshot is inexact")
        for value in (self.device, self.inode, self.size_bytes):
            if type(value) is not int or value < 0:
                raise ValueError("external-gold auth identity is invalid")
        if not 0 < self.size_bytes <= MAX_AUTH_FILE_BYTES:
            raise ValueError("external-gold auth size is outside runner limits")
        _hmac_value(self.content_commitment, "external-gold auth commitment")


def _capture_auth(
    auth_file: str | Path,
    *,
    ledger_key: bytes,
) -> tuple[ExternalGoldAuthSnapshotV4, bytes]:
    raw = Path(auth_file)
    if not raw.is_absolute() or raw.name != "auth.json" or raw.is_symlink():
        raise ValueError("external-gold auth file must be an absolute regular auth.json")
    path = raw.resolve(strict=True)
    content, status = _read_single_regular(path, maximum=MAX_AUTH_FILE_BYTES)
    try:
        decoded = json.loads(content.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("external-gold auth file is not JSON") from error
    if type(decoded) is not dict:
        raise ValueError("external-gold auth root must be an object")
    material = (
        str(path).encode("utf-8")
        + b"\x00"
        + str(int(status.st_dev)).encode("ascii")
        + b"\x00"
        + str(int(status.st_ino)).encode("ascii")
        + b"\x00"
        + content
    )
    return (
        ExternalGoldAuthSnapshotV4(
            canonical_path=str(path),
            device=int(status.st_dev),
            inode=int(status.st_ino),
            size_bytes=len(content),
            content_commitment=secret_commitment(
                _key(ledger_key, "external-gold ledger key"),
                domain="external-gold-audit-v4/auth-snapshot",
                material=material,
            ),
        ),
        content,
    )


def capture_external_gold_auth_snapshot_v4(
    auth_file: str | Path,
    *,
    ledger_key: bytes,
) -> ExternalGoldAuthSnapshotV4:
    snapshot, _content = _capture_auth(auth_file, ledger_key=ledger_key)
    return snapshot


@dataclass(frozen=True, slots=True)
class ExternalGoldPrivateMaterialV4:
    ledger_key: bytes = field(repr=False)
    alias_key: bytes = field(repr=False)
    vault_commitment_key: bytes = field(repr=False)
    reviewer_a_seed: bytes = field(repr=False)
    reviewer_b_seed: bytes = field(repr=False)
    report_signing_seed: bytes = field(repr=False)

    def __post_init__(self) -> None:
        values = tuple(
            _key(value, label)
            for value, label in (
                (self.ledger_key, "external-gold ledger key"),
                (self.alias_key, "external-gold alias key"),
                (self.vault_commitment_key, "external-gold vault commitment key"),
                (self.reviewer_a_seed, "external-gold reviewer A seed"),
                (self.reviewer_b_seed, "external-gold reviewer B seed"),
                (self.report_signing_seed, "external-gold report signing seed"),
            )
        )
        if len(set(values)) != len(values):
            raise ValueError("external-gold private keys must be independently generated")

    @classmethod
    def generate(cls) -> ExternalGoldPrivateMaterialV4:
        return cls(*(os.urandom(32) for _ in range(6)))

    def signer(self, slot: ExternalGoldReviewerSlotV4) -> CanonicalEd25519SignerV3:
        if slot is ExternalGoldReviewerSlotV4.REVIEWER_A:
            seed = self.reviewer_a_seed
        elif slot is ExternalGoldReviewerSlotV4.REVIEWER_B:
            seed = self.reviewer_b_seed
        else:
            raise ValueError("external-gold signer slot is invalid")
        return CanonicalEd25519SignerV3(
            seed,
            domain=external_gold_reviewer_signing_domain_v4(slot),
        )

    def report_signer(self) -> CanonicalEd25519SignerV3:
        self.__post_init__()
        return CanonicalEd25519SignerV3(
            self.report_signing_seed,
            domain=EXTERNAL_GOLD_REPORT_SIGNING_DOMAIN_V4,
        )


@dataclass(frozen=True, slots=True)
class PreparedExternalGoldSlotV4:
    reviewer_slot: ExternalGoldReviewerSlotV4
    ordinal: int
    slot_id: str
    dispatch: DispatchMaterial = field(repr=False)
    commitment: SlotCommitment

    def __post_init__(self) -> None:
        expected = 0 if self.reviewer_slot is ExternalGoldReviewerSlotV4.REVIEWER_A else 1
        if type(self.reviewer_slot) is not ExternalGoldReviewerSlotV4 or self.ordinal != expected:
            raise ValueError("prepared external-gold slot has the wrong role or ordinal")
        if (
            type(self.dispatch) is not DispatchMaterial
            or type(self.commitment) is not SlotCommitment
        ):
            raise ValueError("prepared external-gold slot lacks exact ledger material")
        if self.slot_id != self.commitment.slot_id:
            raise ValueError("prepared external-gold slot id differs from commitment")


@dataclass(frozen=True, slots=True)
class PreparedExternalGoldCampaignV4:
    """Gold-free state after public preparation has performed a fresh random replay.

    ``validate_current`` checks durable integrity only.  Public preparation and
    restore entry points rerun the random lane because nominal Python objects or
    marker fields are not mint authority.
    """

    selection: RandomExternalGoldSelectionBindingV4 = field(repr=False)
    random_replay_evidence: RandomExternalColdReplayEvidenceV4
    sealed_vault: SealedExternalGoldVaultCommitmentV4
    reviewer_a_plan: ExternalGoldAuditPlanV4 = field(repr=False)
    reviewer_b_plan: ExternalGoldAuditPlanV4 = field(repr=False)
    policy: ExternalGoldAuditRunPolicyV4
    private_material: ExternalGoldPrivateMaterialV4 = field(repr=False)
    auth_snapshot: ExternalGoldAuthSnapshotV4
    code_identity: ExternalGoldCodeIdentityV4
    anchor_verifier_public_key: bytes = field(repr=False)
    recovery_verifier_public_key: bytes = field(repr=False)
    core_precommitment: dict[str, object]
    core_precommitment_digest: str
    slots: tuple[PreparedExternalGoldSlotV4, PreparedExternalGoldSlotV4]

    def __post_init__(self) -> None:
        exact = (
            (self.selection, RandomExternalGoldSelectionBindingV4),
            (self.random_replay_evidence, RandomExternalColdReplayEvidenceV4),
            (self.sealed_vault, SealedExternalGoldVaultCommitmentV4),
            (self.reviewer_a_plan, ExternalGoldAuditPlanV4),
            (self.reviewer_b_plan, ExternalGoldAuditPlanV4),
            (self.policy, ExternalGoldAuditRunPolicyV4),
            (self.private_material, ExternalGoldPrivateMaterialV4),
            (self.auth_snapshot, ExternalGoldAuthSnapshotV4),
            (self.code_identity, ExternalGoldCodeIdentityV4),
        )
        if type(self) is not PreparedExternalGoldCampaignV4 or any(
            type(value) is not expected for value, expected in exact
        ):
            raise ValueError("prepared external-gold campaign contains inexact state")
        self.selection.validate_current()
        self.random_replay_evidence.validate_current()
        if self.random_replay_evidence.selection != self.selection:
            raise ValueError("prepared external-gold campaign lost its fresh random replay")
        self.sealed_vault.validate_current()
        self.reviewer_a_plan.__post_init__()
        self.reviewer_b_plan.__post_init__()
        self.policy.__post_init__()
        self.private_material.__post_init__()
        self.auth_snapshot.__post_init__()
        self.code_identity.__post_init__()
        for key, label in (
            (self.anchor_verifier_public_key, "external-gold anchor verifier"),
            (self.recovery_verifier_public_key, "external-gold recovery verifier"),
        ):
            if type(key) is not bytes or len(key) != 32:
                raise ValueError(f"{label} must contain exactly 32 bytes")
        if self.anchor_verifier_public_key == self.recovery_verifier_public_key:
            raise ValueError("external-gold anchor and recovery witnesses must be distinct")
        if type(self.core_precommitment) is not dict:
            raise ValueError("prepared external-gold core must be exact")
        _digest(self.core_precommitment_digest, "prepared external-gold core")
        if (
            type(self.slots) is not tuple
            or len(self.slots) != 2
            or tuple(slot.ordinal for slot in self.slots) != (0, 1)
        ):
            raise ValueError("prepared external-gold campaign must retain exact two slots")
        if self.slots[0].slot_id == self.slots[1].slot_id:
            raise ValueError("prepared external-gold campaign reuses a slot id")
        if self.reviewer_a_plan.item_order_ids == self.reviewer_b_plan.item_order_ids:
            raise ValueError("external-gold reviewer orders unexpectedly coincide")
        forbidden_names = {"raw", "path", "entries", "gold", "vault_raw", "vault_path"}
        if forbidden_names & set(self.__dataclass_fields__):
            raise ValueError("prepared external-gold campaign acquired private-gold state")

    @property
    def plans(self) -> tuple[ExternalGoldAuditPlanV4, ExternalGoldAuditPlanV4]:
        return self.reviewer_a_plan, self.reviewer_b_plan

    @property
    def commitments(self) -> tuple[SlotCommitment, SlotCommitment]:
        return self.slots[0].commitment, self.slots[1].commitment

    @property
    def report_verifier(self) -> CanonicalEd25519VerifierV3:
        return self.private_material.report_signer().verifier()

    def validate_current(self) -> None:
        self.__post_init__()
        rebuilt = _prepare_external_gold_campaign_v4(
            self.selection,
            self.random_replay_evidence,
            self.sealed_vault,
            self.policy,
            private_material=self.private_material,
            auth_snapshot=self.auth_snapshot,
            code_identity=self.code_identity,
            anchor_verifier_public_key=self.anchor_verifier_public_key,
            recovery_verifier_public_key=self.recovery_verifier_public_key,
            fixed_tokens=tuple((slot.slot_id, slot.dispatch.dispatch_nonce) for slot in self.slots),
        )
        if rebuilt != self:
            raise ValueError("prepared external-gold campaign differs from reconstruction")


def _reviewer_public_payload(
    plan: ExternalGoldAuditPlanV4,
    signer: CanonicalEd25519SignerV3,
    *,
    commitment_key: bytes,
) -> dict[str, object]:
    verifier = signer.verifier()

    def commit(label: str, value: object) -> str:
        return secret_commitment(
            commitment_key,
            domain=f"external-gold-audit-v4/{plan.reviewer_slot.value}/{label}",
            material=_canonical_bytes(value),
        )

    return {
        "authority_commitment": verifier.authority_commitment,
        "key_fingerprint": verifier.key_fingerprint,
        "order_commitment": commit("order", plan.order_digest),
        "output_schema_commitment": commit("output-schema", plan.output_schema_digest),
        "plan_commitment": commit(
            "plan",
            {
                "order_digest": plan.order_digest,
                "output_schema_digest": plan.output_schema_digest,
                "presentation_digest": plan.presentation_digest,
                "projection_digest": plan.projection_digest,
                "prompt_digest": plan.prompt_digest,
                "selection_digest": plan.selection_digest,
            },
        ),
        "presentation_commitment": commit("presentation", plan.presentation_digest),
        "prompt_commitment": commit("prompt", plan.prompt_digest),
        "public_key_hex": verifier.public_key_bytes().hex(),
        "signing_domain": external_gold_reviewer_signing_domain_v4(plan.reviewer_slot),
    }


def _witness_public_payload(public_key: bytes, *, domain: str) -> dict[str, str]:
    verifier = CanonicalEd25519VerifierV3(public_key, domain=domain)
    return {
        "authority_commitment": verifier.authority_commitment,
        "key_fingerprint": verifier.key_fingerprint,
        "public_key_hex": public_key.hex(),
        "signing_domain": domain,
    }


def _prepare_external_gold_campaign_v4(
    selection: RandomExternalGoldSelectionBindingV4,
    random_replay_evidence: RandomExternalColdReplayEvidenceV4,
    sealed_vault: SealedExternalGoldVaultCommitmentV4,
    policy: ExternalGoldAuditRunPolicyV4,
    *,
    private_material: ExternalGoldPrivateMaterialV4,
    auth_snapshot: ExternalGoldAuthSnapshotV4,
    code_identity: ExternalGoldCodeIdentityV4,
    anchor_verifier_public_key: bytes,
    recovery_verifier_public_key: bytes,
    fixed_tokens: tuple[tuple[str, str], tuple[str, str]] | None,
) -> PreparedExternalGoldCampaignV4:
    selection.validate_current()
    random_replay_evidence.validate_current()
    if random_replay_evidence.selection != selection:
        raise ValueError("external-gold preparation requires its exact fresh random replay")
    sealed_vault.validate_current()
    if sealed_vault.projection_digest != selection.projection_digest:
        raise ValueError("sealed external-gold vault differs from random projection")
    if sealed_vault.raw_digest != selection.private_gold_vault_record_sha256:
        raise ValueError("sealed external-gold vault differs from random private-vault replay")
    plans = (
        build_external_gold_audit_plan_v4(
            selection,
            ExternalGoldReviewerSlotV4.REVIEWER_A,
            alias_key=private_material.alias_key,
        ),
        build_external_gold_audit_plan_v4(
            selection,
            ExternalGoldReviewerSlotV4.REVIEWER_B,
            alias_key=private_material.alias_key,
        ),
    )
    signer_a = private_material.signer(ExternalGoldReviewerSlotV4.REVIEWER_A)
    signer_b = private_material.signer(ExternalGoldReviewerSlotV4.REVIEWER_B)
    report_signer = private_material.report_signer()
    reviewer_a_public_key = signer_a.verifier().public_key_bytes()
    reviewer_b_public_key = signer_b.verifier().public_key_bytes()
    report_signing_public_key = report_signer.verifier().public_key_bytes()
    if (
        len(
            {
                reviewer_a_public_key,
                reviewer_b_public_key,
                anchor_verifier_public_key,
                recovery_verifier_public_key,
                report_signing_public_key,
            }
        )
        != 5
    ):
        raise ValueError(
            "external-gold reviewer and witness keys, including report authority, "
            "must all be distinct"
        )
    core = {
        "anchor_witness": _witness_public_payload(
            anchor_verifier_public_key,
            domain=EXTERNAL_GOLD_ANCHOR_SIGNING_DOMAIN_V4,
        ),
        "code_identity": code_identity.public_payload(),
        "confirmatory_claim_authorized": False,
        "eligibility": {
            "both_reviewers_must_solve": True,
            "exact_agreement_required": True,
            "failed_audit_topup_allowed": False,
            "private_source_gold_match_required": True,
            "reselection_allowed": False,
            "selected_case_count": 12,
            "unsolved_is_lane_failure": True,
        },
        "epistemic_independence_verified": False,
        "gold_present_in_pre_run_state": False,
        "gold_present_in_reviewer_payload": False,
        "gold_reveal_stage": "after_two_terminal_slots_and_exact_cold_replay",
        "process_schedule": {
            "max_workers": 1,
            "order": ["reviewer_a", "reviewer_b"],
            "predecessor_dependent_started_chain": False,
            "sequential": True,
        },
        "privileged_anchor_witness_required": True,
        "privileged_recovery_witness_required": True,
        "provider_independence_verified": False,
        "private_gold_vault_record_sha256": (selection.private_gold_vault_record_sha256),
        "random_gold_audit_input_digest": selection.random_gold_audit_input_digest,
        "random_lane_capability_digest": selection.random_lane_capability_digest,
        "random_lane_cold_replay": random_replay_evidence.public_payload(),
        "report_signing_authority": _witness_public_payload(
            report_signing_public_key,
            domain=EXTERNAL_GOLD_REPORT_SIGNING_DOMAIN_V4,
        ),
        "recovery_witness": _witness_public_payload(
            recovery_verifier_public_key,
            domain=EXTERNAL_GOLD_RECOVERY_SIGNING_DOMAIN_V4,
        ),
        "reviewer_a": _reviewer_public_payload(
            plans[0],
            signer_a,
            commitment_key=private_material.ledger_key,
        ),
        "reviewer_b": _reviewer_public_payload(
            plans[1],
            signer_b,
            commitment_key=private_material.ledger_key,
        ),
        "reviewer_origin_verified": False,
        "reviewer_processes": 2,
        "run_policy": policy.public_payload(),
        "schema": EXTERNAL_GOLD_CORE_SCHEMA_V4,
        "schema_version": EXTERNAL_GOLD_AUDIT_VERSION_V4,
        "sealed_vault": sealed_vault.public_payload(),
        "selection": {
            "private_gold_vault_record_sha256": (selection.private_gold_vault_record_sha256),
            "projection_digest": selection.projection_digest,
            "random_gold_audit_input_digest": (selection.random_gold_audit_input_digest),
            "random_lane_capability_digest": selection.random_lane_capability_digest,
            "selected_case_count": 12,
            "selection_digest": selection.selection_digest,
        },
        "served_model_identity_verified": False,
        "source_metadata_present_in_reviewer_payload": False,
    }
    core_digest = _domain_digest(
        core,
        domain="org.math-research-brain/external-gold-core/v4",
    )
    tokens = fixed_tokens or tuple((new_slot_id(), _sha256(os.urandom(32))) for _ in range(2))
    slots: list[PreparedExternalGoldSlotV4] = []
    for ordinal, (plan, token) in enumerate(zip(plans, tokens, strict=True)):
        slot_id, dispatch_nonce = token
        dispatch = DispatchMaterial(
            run_spec_digest=core_digest,
            batch_digest=_domain_digest(
                {
                    "core_digest": core_digest,
                    "order_digest": plan.order_digest,
                    "private_gold_vault_record_sha256": (
                        selection.private_gold_vault_record_sha256
                    ),
                    "projection_digest": selection.projection_digest,
                    "random_gold_audit_input_digest": (selection.random_gold_audit_input_digest),
                    "random_lane_capability_digest": (selection.random_lane_capability_digest),
                    "reviewer_slot": plan.reviewer_slot.value,
                    "sealed_vault_commitment": sealed_vault.commitment,
                    "selection_digest": selection.selection_digest,
                },
                domain="org.math-research-brain/external-gold-batch/v4",
            ),
            prompt=plan.prompt_text,
            output_schema_json=plan.output_schema_json,
            request_policy_digest=policy.request_policy_digest,
            dispatch_nonce=dispatch_nonce,
        )
        slots.append(
            PreparedExternalGoldSlotV4(
                reviewer_slot=plan.reviewer_slot,
                ordinal=ordinal,
                slot_id=slot_id,
                dispatch=dispatch,
                commitment=dispatch.seal(
                    key=private_material.ledger_key,
                    slot_id=slot_id,
                ),
            )
        )
    return PreparedExternalGoldCampaignV4(
        selection=selection,
        random_replay_evidence=random_replay_evidence,
        sealed_vault=sealed_vault,
        reviewer_a_plan=plans[0],
        reviewer_b_plan=plans[1],
        policy=policy,
        private_material=private_material,
        auth_snapshot=auth_snapshot,
        code_identity=code_identity,
        anchor_verifier_public_key=anchor_verifier_public_key,
        recovery_verifier_public_key=recovery_verifier_public_key,
        core_precommitment=core,
        core_precommitment_digest=core_digest,
        slots=(slots[0], slots[1]),
    )


def prepare_external_gold_campaign_v4(
    selection: RandomExternalGoldSelectionBindingV4,
    sealed_vault: SealedExternalGoldVaultCommitmentV4,
    policy: ExternalGoldAuditRunPolicyV4,
    *,
    random_precommitment_bytes: bytes,
    random_beacon_receipt_bytes: bytes,
    expected_random_precommitment_record_sha256: str,
    expected_random_beacon_receipt_record_sha256: str,
    external_selection_precommit_anchor_evidence_digest: str,
    random_source: object,
    random_selection_capability: object,
    random_audit_input: object,
    private_material: ExternalGoldPrivateMaterialV4,
    auth_snapshot: ExternalGoldAuthSnapshotV4,
    code_identity: ExternalGoldCodeIdentityV4,
    anchor_verifier: CanonicalEd25519VerifierV3,
    recovery_verifier: CanonicalEd25519VerifierV3,
) -> PreparedExternalGoldCampaignV4:
    if (
        type(anchor_verifier) is not CanonicalEd25519VerifierV3
        or type(recovery_verifier) is not CanonicalEd25519VerifierV3
    ):
        raise ValueError("external-gold witnesses must be exact Ed25519 verifiers")
    expected_anchor = CanonicalEd25519VerifierV3(
        anchor_verifier.public_key_bytes(),
        domain=EXTERNAL_GOLD_ANCHOR_SIGNING_DOMAIN_V4,
    )
    expected_recovery = CanonicalEd25519VerifierV3(
        recovery_verifier.public_key_bytes(),
        domain=EXTERNAL_GOLD_RECOVERY_SIGNING_DOMAIN_V4,
    )
    if (
        anchor_verifier.authority_commitment != expected_anchor.authority_commitment
        or recovery_verifier.authority_commitment != expected_recovery.authority_commitment
    ):
        raise ValueError("external-gold witness domains differ from policy")
    fresh = _fresh_random_external_replay_v4(
        random_precommitment_bytes,
        random_beacon_receipt_bytes,
        expected_precommitment_record_sha256=(expected_random_precommitment_record_sha256),
        expected_beacon_receipt_record_sha256=(expected_random_beacon_receipt_record_sha256),
        external_selection_precommit_anchor_evidence_digest=(
            external_selection_precommit_anchor_evidence_digest
        ),
        source=random_source,
        expected_capability=random_selection_capability,
        expected_audit_input=random_audit_input,
    )
    if fresh.evidence.selection != selection:
        raise ValueError("external-gold selection differs from fresh random cold replay")
    prepared = _prepare_external_gold_campaign_v4(
        selection,
        fresh.evidence,
        sealed_vault,
        policy,
        private_material=private_material,
        auth_snapshot=auth_snapshot,
        code_identity=code_identity,
        anchor_verifier_public_key=anchor_verifier.public_key_bytes(),
        recovery_verifier_public_key=recovery_verifier.public_key_bytes(),
        fixed_tokens=None,
    )
    prepared.validate_current()
    return prepared


def _private_state_body(prepared: PreparedExternalGoldCampaignV4) -> dict[str, object]:
    prepared.__post_init__()
    policy = prepared.policy
    return {
        "auth_snapshot": {
            "canonical_path": prepared.auth_snapshot.canonical_path,
            "content_commitment": prepared.auth_snapshot.content_commitment,
            "device": prepared.auth_snapshot.device,
            "inode": prepared.auth_snapshot.inode,
            "size_bytes": prepared.auth_snapshot.size_bytes,
        },
        "code_identity": {
            "annotated_tag": prepared.code_identity.annotated_tag,
            "commit_oid": prepared.code_identity.commit_oid,
            "repository_root": prepared.code_identity.repository_root,
            "tag_object_oid": prepared.code_identity.tag_object_oid,
        },
        "core_precommitment_digest": prepared.core_precommitment_digest,
        "policy": {
            "base_instructions": policy.base_instructions,
            "developer_instructions": policy.developer_instructions,
            "executable_provenance": policy.executable_provenance.to_dict(),
            "max_workers": policy.max_workers,
            "no_retry": policy.no_retry,
            "requested_effort": policy.requested_effort,
            "requested_model": policy.requested_model,
            "requested_provider": policy.requested_provider,
            "timeout_seconds": policy.timeout_seconds,
        },
        "private_material": {
            "alias_key_hex": prepared.private_material.alias_key.hex(),
            "ledger_key_hex": prepared.private_material.ledger_key.hex(),
            "reviewer_a_seed_hex": prepared.private_material.reviewer_a_seed.hex(),
            "reviewer_b_seed_hex": prepared.private_material.reviewer_b_seed.hex(),
            "report_signing_seed_hex": (prepared.private_material.report_signing_seed.hex()),
            "vault_commitment_key_hex": prepared.private_material.vault_commitment_key.hex(),
        },
        "random_replay_evidence": prepared.random_replay_evidence.public_payload(),
        "schema": EXTERNAL_GOLD_PRIVATE_STATE_SCHEMA_V4,
        "schema_version": EXTERNAL_GOLD_AUDIT_VERSION_V4,
        "sealed_vault": prepared.sealed_vault.public_payload(),
        "selection": {
            "cases": [
                {
                    "item_id": case.item_id,
                    "problem_text": case.problem_text,
                    "problem_text_digest": case.problem_text_digest,
                }
                for case in prepared.selection.cases
            ],
            "projection_digest": prepared.selection.projection_digest,
            "private_gold_vault_record_sha256": (
                prepared.selection.private_gold_vault_record_sha256
            ),
            "random_gold_audit_input_digest": (prepared.selection.random_gold_audit_input_digest),
            "random_lane_capability_digest": (prepared.selection.random_lane_capability_digest),
            "selection_digest": prepared.selection.selection_digest,
        },
        "slots": [
            {
                "dispatch_nonce": slot.dispatch.dispatch_nonce,
                "ordinal": slot.ordinal,
                "reviewer_slot": slot.reviewer_slot.value,
                "slot_id": slot.slot_id,
            }
            for slot in prepared.slots
        ],
        "witnesses": {
            "anchor_public_key_hex": prepared.anchor_verifier_public_key.hex(),
            "recovery_public_key_hex": prepared.recovery_verifier_public_key.hex(),
        },
    }


def serialize_external_gold_private_state_v4(
    prepared: PreparedExternalGoldCampaignV4,
) -> bytes:
    if type(prepared) is not PreparedExternalGoldCampaignV4:
        raise ValueError("external-gold private serialization requires exact prepared state")
    prepared.validate_current()
    raw = _canonical_bytes(_private_state_body(prepared), lf=True)
    if len(raw) > EXTERNAL_GOLD_PRIVATE_STATE_MAX_BYTES_V4:
        raise ValueError("external-gold private state exceeds its byte budget")
    forbidden = (b"vault_path", b"vault_raw", b'gold"', b'entries"')
    if any(token in raw for token in forbidden):
        raise ValueError("external-gold private state acquired forbidden vault material")
    return raw


def _hex_key(value: object, label: str) -> bytes:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{label} is not a canonical private key")
    return bytes.fromhex(value)


def deserialize_external_gold_private_state_v4(
    raw: bytes,
    selection: RandomExternalGoldSelectionBindingV4,
    random_replay_evidence: RandomExternalColdReplayEvidenceV4,
    sealed_vault: SealedExternalGoldVaultCommitmentV4,
) -> PreparedExternalGoldCampaignV4:
    root = _exact_mapping(
        _decode_canonical_json(
            raw,
            maximum=EXTERNAL_GOLD_PRIVATE_STATE_MAX_BYTES_V4,
            label="external-gold private state",
        ),
        {
            "auth_snapshot",
            "code_identity",
            "core_precommitment_digest",
            "policy",
            "private_material",
            "random_replay_evidence",
            "schema",
            "schema_version",
            "sealed_vault",
            "selection",
            "slots",
            "witnesses",
        },
        "external-gold private state",
    )
    if (
        root["schema"] != EXTERNAL_GOLD_PRIVATE_STATE_SCHEMA_V4
        or root["schema_version"] != EXTERNAL_GOLD_AUDIT_VERSION_V4
    ):
        raise ValueError("external-gold private-state schema differs")
    selection.validate_current()
    random_replay_evidence.validate_current()
    sealed_vault.validate_current()
    if (
        random_replay_evidence.selection != selection
        or root["random_replay_evidence"] != random_replay_evidence.public_payload()
    ):
        raise ValueError("external-gold private state differs from fresh random replay")
    expected_selection = {
        "cases": [
            {
                "item_id": case.item_id,
                "problem_text": case.problem_text,
                "problem_text_digest": case.problem_text_digest,
            }
            for case in selection.cases
        ],
        "private_gold_vault_record_sha256": selection.private_gold_vault_record_sha256,
        "projection_digest": selection.projection_digest,
        "random_gold_audit_input_digest": selection.random_gold_audit_input_digest,
        "random_lane_capability_digest": selection.random_lane_capability_digest,
        "selection_digest": selection.selection_digest,
    }
    if root["selection"] != expected_selection or root["sealed_vault"] != (
        sealed_vault.public_payload()
    ):
        raise ValueError("external-gold private state is bound to other frozen inputs")
    private = _exact_mapping(
        root["private_material"],
        {
            "alias_key_hex",
            "ledger_key_hex",
            "reviewer_a_seed_hex",
            "reviewer_b_seed_hex",
            "report_signing_seed_hex",
            "vault_commitment_key_hex",
        },
        "external-gold private material",
    )
    material = ExternalGoldPrivateMaterialV4(
        ledger_key=_hex_key(private["ledger_key_hex"], "external-gold ledger key"),
        alias_key=_hex_key(private["alias_key_hex"], "external-gold alias key"),
        vault_commitment_key=_hex_key(
            private["vault_commitment_key_hex"],
            "external-gold vault commitment key",
        ),
        reviewer_a_seed=_hex_key(
            private["reviewer_a_seed_hex"],
            "external-gold reviewer A seed",
        ),
        reviewer_b_seed=_hex_key(
            private["reviewer_b_seed_hex"],
            "external-gold reviewer B seed",
        ),
        report_signing_seed=_hex_key(
            private["report_signing_seed_hex"],
            "external-gold report signing seed",
        ),
    )
    policy_value = _exact_mapping(
        root["policy"],
        {
            "base_instructions",
            "developer_instructions",
            "executable_provenance",
            "max_workers",
            "no_retry",
            "requested_effort",
            "requested_model",
            "requested_provider",
            "timeout_seconds",
        },
        "external-gold private policy",
    )
    executable = _exact_mapping(
        policy_value["executable_provenance"],
        {"canonical_path", "sha256_digest", "size_bytes"},
        "external-gold private executable",
    )
    auth = _exact_mapping(
        root["auth_snapshot"],
        {"canonical_path", "content_commitment", "device", "inode", "size_bytes"},
        "external-gold private auth snapshot",
    )
    code = _exact_mapping(
        root["code_identity"],
        {"annotated_tag", "commit_oid", "repository_root", "tag_object_oid"},
        "external-gold private code identity",
    )
    witnesses = _exact_mapping(
        root["witnesses"],
        {"anchor_public_key_hex", "recovery_public_key_hex"},
        "external-gold private witnesses",
    )
    raw_slots = root["slots"]
    if type(raw_slots) is not list or len(raw_slots) != 2:
        raise ValueError("external-gold private state lacks exact two slots")
    tokens: list[tuple[str, str]] = []
    for ordinal, raw_slot in enumerate(raw_slots):
        slot = _exact_mapping(
            raw_slot,
            {"dispatch_nonce", "ordinal", "reviewer_slot", "slot_id"},
            "external-gold private slot",
        )
        expected_role = (
            ExternalGoldReviewerSlotV4.REVIEWER_A
            if ordinal == 0
            else ExternalGoldReviewerSlotV4.REVIEWER_B
        )
        if (
            slot["ordinal"] != ordinal
            or slot["reviewer_slot"] != expected_role.value
            or type(slot["slot_id"]) is not str
            or type(slot["dispatch_nonce"]) is not str
        ):
            raise ValueError("external-gold private slot order or role differs")
        tokens.append((slot["slot_id"], slot["dispatch_nonce"]))
    try:
        policy = ExternalGoldAuditRunPolicyV4(
            executable_provenance=ExecutableProvenance(**executable),
            requested_model=policy_value["requested_model"],
            requested_provider=policy_value["requested_provider"],
            requested_effort=policy_value["requested_effort"],
            timeout_seconds=policy_value["timeout_seconds"],
            max_workers=policy_value["max_workers"],
            no_retry=policy_value["no_retry"],
            base_instructions=policy_value["base_instructions"],
            developer_instructions=policy_value["developer_instructions"],
        )
        auth_snapshot = ExternalGoldAuthSnapshotV4(**auth)
        code_identity = ExternalGoldCodeIdentityV4(**code)
        anchor_key = bytes.fromhex(witnesses["anchor_public_key_hex"])
        recovery_key = bytes.fromhex(witnesses["recovery_public_key_hex"])
    except (TypeError, ValueError) as error:
        raise ValueError("external-gold private typed state is invalid") from error
    rebuilt = _prepare_external_gold_campaign_v4(
        selection,
        random_replay_evidence,
        sealed_vault,
        policy,
        private_material=material,
        auth_snapshot=auth_snapshot,
        code_identity=code_identity,
        anchor_verifier_public_key=anchor_key,
        recovery_verifier_public_key=recovery_key,
        fixed_tokens=(tokens[0], tokens[1]),
    )
    if (
        root["core_precommitment_digest"] != rebuilt.core_precommitment_digest
        or _private_state_body(rebuilt) != root
    ):
        raise ValueError("external-gold private state changed during reconstruction")
    rebuilt.validate_current()
    return rebuilt


def _public_precommitment(
    prepared: PreparedExternalGoldCampaignV4,
    *,
    ledger_digest: str,
    private_state_digest: str,
) -> dict[str, object]:
    return {
        "attempt_ledger": {
            "digest": _digest(ledger_digest, "external-gold attempt ledger"),
            "ordered_slots": [
                slot.commitment.to_dict(ordinal=slot.ordinal) for slot in prepared.slots
            ],
            "private_state_digest": _digest(
                private_state_digest,
                "external-gold durable private state",
            ),
            "slot_count": 2,
        },
        "core": prepared.core_precommitment,
        "core_digest": prepared.core_precommitment_digest,
        "schema": EXTERNAL_GOLD_PUBLIC_PRECOMMIT_SCHEMA_V4,
        "schema_version": EXTERNAL_GOLD_AUDIT_VERSION_V4,
    }


def parse_external_gold_public_precommitment_v4(
    raw: bytes,
    *,
    expected_digest: str,
) -> dict[str, object]:
    expected = _digest(expected_digest, "external-gold public precommitment")
    if not hmac.compare_digest(_sha256(raw), expected):
        raise ValueError("external-gold precommitment differs from its anchor")
    root = _exact_mapping(
        _decode_canonical_json(
            raw,
            maximum=EXTERNAL_GOLD_PUBLIC_PRECOMMIT_MAX_BYTES_V4,
            label="external-gold public precommitment",
        ),
        {"attempt_ledger", "core", "core_digest", "schema", "schema_version"},
        "external-gold public precommitment",
    )
    if (
        root["schema"] != EXTERNAL_GOLD_PUBLIC_PRECOMMIT_SCHEMA_V4
        or root["schema_version"] != EXTERNAL_GOLD_AUDIT_VERSION_V4
        or type(root["core"]) is not dict
        or _domain_digest(
            root["core"],
            domain="org.math-research-brain/external-gold-core/v4",
        )
        != root["core_digest"]
    ):
        raise ValueError("external-gold public precommitment core differs")
    ledger = _exact_mapping(
        root["attempt_ledger"],
        {"digest", "ordered_slots", "private_state_digest", "slot_count"},
        "external-gold precommitted ledger",
    )
    _digest(ledger["digest"], "external-gold precommitted ledger")
    _digest(ledger["private_state_digest"], "external-gold private state")
    if (
        ledger["slot_count"] != 2
        or type(ledger["ordered_slots"]) is not list
        or len(ledger["ordered_slots"]) != 2
    ):
        raise ValueError("external-gold precommitted slot universe differs")
    return root


def _home_receipt_body(
    prepared: PreparedExternalGoldCampaignV4,
    *,
    campaign_root: Path,
    ledger: AttemptLedger,
    private_state_digest: str,
    public_precommitment_digest: str,
) -> dict[str, object]:
    root, root_device, root_inode = _path_identity(campaign_root, directory=True)
    ledger_root, ledger_device, ledger_inode = _path_identity(ledger.root, directory=True)
    return {
        "campaign_root": str(root),
        "campaign_root_device": root_device,
        "campaign_root_inode": root_inode,
        "code_commit_oid": prepared.code_identity.commit_oid,
        "code_tag_object_oid": prepared.code_identity.tag_object_oid,
        "ledger_digest": ledger.ledger_digest,
        "ledger_root": str(ledger_root),
        "ledger_root_device": ledger_device,
        "ledger_root_inode": ledger_inode,
        "ordered_slots": [slot.commitment.to_dict(ordinal=slot.ordinal) for slot in prepared.slots],
        "private_state_digest": private_state_digest,
        "public_precommitment_digest": public_precommitment_digest,
        "schema": EXTERNAL_GOLD_HOME_RECEIPT_SCHEMA_V4,
        "schema_version": EXTERNAL_GOLD_AUDIT_VERSION_V4,
    }


def _signed_home_receipt(body: dict[str, object], *, ledger_key: bytes) -> bytes:
    authentication = secret_commitment(
        ledger_key,
        domain="external-gold-audit-v4/physical-ledger-home",
        material=_canonical_bytes(body),
    )
    return _canonical_bytes({"authentication": authentication, "body": body}, lf=True)


def _validate_home_receipt(
    raw: bytes,
    *,
    prepared: PreparedExternalGoldCampaignV4,
    campaign_root: Path,
    ledger: AttemptLedger,
    private_state_digest: str,
    public_precommitment_digest: str,
) -> dict[str, object]:
    record = _exact_mapping(
        _decode_canonical_json(raw, maximum=1_000_000, label="external-gold home receipt"),
        {"authentication", "body"},
        "external-gold home receipt",
    )
    body = _home_receipt_body(
        prepared,
        campaign_root=campaign_root,
        ledger=ledger,
        private_state_digest=private_state_digest,
        public_precommitment_digest=public_precommitment_digest,
    )
    if record["body"] != body:
        raise ValueError("external-gold physical home receipt belongs to another root")
    expected = _signed_home_receipt(body, ledger_key=prepared.private_material.ledger_key)
    if not hmac.compare_digest(expected, raw):
        raise ValueError("external-gold physical home receipt authentication differs")
    return body


def _state_record(
    *,
    phase: str,
    prior_state_digest: str | None,
    bindings: dict[str, object],
) -> bytes:
    return _canonical_bytes(
        {
            "bindings": bindings,
            "phase": phase,
            "prior_state_digest": prior_state_digest,
            "schema": "org.math-research-brain/external-gold-campaign-state-v4",
            "schema_version": EXTERNAL_GOLD_AUDIT_VERSION_V4,
        },
        lf=True,
    )


def _read_state(root: Path, ordinal: int) -> tuple[dict[str, object], bytes]:
    raw, _status = _read_single_regular(root / _STATE_NAMES[ordinal], maximum=1_000_000)
    value = _exact_mapping(
        _decode_canonical_json(raw, maximum=1_000_000, label="external-gold campaign state"),
        {"bindings", "phase", "prior_state_digest", "schema", "schema_version"},
        "external-gold campaign state",
    )
    if (
        value["schema"] != "org.math-research-brain/external-gold-campaign-state-v4"
        or value["schema_version"] != EXTERNAL_GOLD_AUDIT_VERSION_V4
        or type(value["bindings"]) is not dict
    ):
        raise ValueError("external-gold campaign-state schema differs")
    expected_prior = None if ordinal == 0 else _sha256(_read_state(root, ordinal - 1)[1])
    if value["prior_state_digest"] != expected_prior:
        raise ValueError("external-gold campaign-state chain differs")
    return value, raw


@dataclass(frozen=True, slots=True)
class BoundExternalGoldCampaignV4:
    prepared: PreparedExternalGoldCampaignV4 = field(repr=False)
    campaign_root: str
    private_state_digest: str
    attempt_ledger_digest: str
    public_precommitment: dict[str, object]
    public_precommitment_digest: str
    ledger_home_receipt_digest: str

    def __post_init__(self) -> None:
        if (
            type(self) is not BoundExternalGoldCampaignV4
            or type(self.prepared) is not PreparedExternalGoldCampaignV4
        ):
            raise ValueError("bound external-gold campaign is inexact")
        if type(self.campaign_root) is not str or not Path(self.campaign_root).is_absolute():
            raise ValueError("bound external-gold campaign root must be absolute")
        for value, label in (
            (self.private_state_digest, "bound external-gold private state"),
            (self.attempt_ledger_digest, "bound external-gold attempt ledger"),
            (self.public_precommitment_digest, "bound external-gold precommitment"),
            (self.ledger_home_receipt_digest, "bound external-gold home receipt"),
        ):
            _digest(value, label)
        if type(self.public_precommitment) is not dict:
            raise ValueError("bound external-gold public precommitment must be exact")

    @property
    def root(self) -> Path:
        return Path(self.campaign_root)

    @property
    def public_precommitment_bytes(self) -> bytes:
        return _canonical_bytes(self.public_precommitment, lf=True)

    def open_ledger(self) -> AttemptLedger:
        before = _attempt_ledger_physical_snapshot(self.root / _LEDGER_NAME)
        ledger = AttemptLedger.open(
            self.root / _LEDGER_NAME,
            key=self.prepared.private_material.ledger_key,
        )
        after = _attempt_ledger_physical_snapshot(self.root / _LEDGER_NAME)
        if before != after:
            raise LedgerIntegrityError("external-gold ledger changed during physical replay")
        if ledger.ledger_digest != self.attempt_ledger_digest or ledger.slots != (
            self.prepared.commitments
        ):
            raise LedgerIntegrityError("external-gold ledger differs from physical home")
        for slot in self.prepared.slots:
            ledger.verify_dispatch(slot.slot_id, slot.dispatch)
        return ledger

    def validate_current(self) -> None:
        self.__post_init__()
        self.prepared.validate_current()
        root, _device, _inode = _path_identity(self.root, directory=True)
        if str(root) != self.campaign_root:
            raise ValueError("external-gold campaign root identity changed")
        private_raw, _private_status = _read_single_regular(
            root / _PRIVATE_STATE_NAME,
            maximum=EXTERNAL_GOLD_PRIVATE_STATE_MAX_BYTES_V4,
        )
        if _sha256(private_raw) != self.private_state_digest or (
            deserialize_external_gold_private_state_v4(
                private_raw,
                self.prepared.selection,
                self.prepared.random_replay_evidence,
                self.prepared.sealed_vault,
            )
            != self.prepared
        ):
            raise ValueError("external-gold durable private state differs")
        precommit_raw, _precommit_status = _read_single_regular(
            root / _PRECOMMIT_NAME,
            maximum=EXTERNAL_GOLD_PUBLIC_PRECOMMIT_MAX_BYTES_V4,
        )
        if (
            precommit_raw != self.public_precommitment_bytes
            or _sha256(precommit_raw) != self.public_precommitment_digest
            or parse_external_gold_public_precommitment_v4(
                precommit_raw,
                expected_digest=self.public_precommitment_digest,
            )
            != self.public_precommitment
        ):
            raise ValueError("external-gold public precommitment differs")
        ledger = self.open_ledger()
        receipt_raw, _receipt_status = _read_single_regular(
            root / _HOME_RECEIPT_NAME,
            maximum=1_000_000,
        )
        if _sha256(receipt_raw) != self.ledger_home_receipt_digest:
            raise ValueError("external-gold home receipt digest differs")
        _validate_home_receipt(
            receipt_raw,
            prepared=self.prepared,
            campaign_root=root,
            ledger=ledger,
            private_state_digest=self.private_state_digest,
            public_precommitment_digest=self.public_precommitment_digest,
        )
        state0, _state0_raw = _read_state(root, 0)
        if state0["phase"] != "prepared" or state0["bindings"] != {
            "ledger_home_receipt_digest": self.ledger_home_receipt_digest,
            "public_precommitment_digest": self.public_precommitment_digest,
        }:
            raise ValueError("external-gold prepared state differs")
        expected_names = {
            _PRIVATE_STATE_NAME,
            _LEDGER_NAME,
            _PRECOMMIT_NAME,
            _HOME_RECEIPT_NAME,
            _STATE_NAMES[0],
        }
        optional = {
            _ANCHOR_ACK_NAME,
            _SIGNED_PAIR_NAME,
            _LIFECYCLE_LOCK_NAME,
            _RECOVERY_CLAIM_NAME,
            _RECOVERED_LOCK_NAME,
            _RECOVERY_ACK_NAME,
            *_STATE_NAMES[1:],
        }
        actual = {entry.name for entry in root.iterdir()}
        if not expected_names <= actual or actual - expected_names - optional:
            raise ValueError("external-gold campaign home contains unexpected entries")


def create_external_gold_campaign_home_v4(
    prepared: PreparedExternalGoldCampaignV4,
    campaign_root: str | Path,
    *,
    random_precommitment_bytes: bytes,
    random_beacon_receipt_bytes: bytes,
    expected_random_precommitment_record_sha256: str,
    expected_random_beacon_receipt_record_sha256: str,
    external_selection_precommit_anchor_evidence_digest: str,
    random_source: object,
    random_selection_capability: object,
    random_audit_input: object,
) -> BoundExternalGoldCampaignV4:
    if type(prepared) is not PreparedExternalGoldCampaignV4:
        raise ValueError("external-gold home creation requires exact prepared state")
    prepared.validate_current()
    fresh = _fresh_random_external_replay_v4(
        random_precommitment_bytes,
        random_beacon_receipt_bytes,
        expected_precommitment_record_sha256=(expected_random_precommitment_record_sha256),
        expected_beacon_receipt_record_sha256=(expected_random_beacon_receipt_record_sha256),
        external_selection_precommit_anchor_evidence_digest=(
            external_selection_precommit_anchor_evidence_digest
        ),
        source=random_source,
        expected_capability=random_selection_capability,
        expected_audit_input=random_audit_input,
    )
    if fresh.evidence != prepared.random_replay_evidence:
        raise ValueError("external-gold home differs from fresh random cold replay")
    raw_root = Path(campaign_root)
    if not raw_root.is_absolute():
        raise ValueError("external-gold campaign root must be absolute")
    parent = raw_root.parent.resolve(strict=True)
    if raw_root.exists() or raw_root.is_symlink():
        raise FileExistsError("external-gold campaign root already exists")
    if any((ancestor / ".git").exists() for ancestor in (parent, *parent.parents)):
        raise ValueError("external-gold campaign home must remain outside Git")
    os.mkdir(raw_root, 0o700)
    _fsync_directory(parent)
    root, _root_device, _root_inode = _path_identity(raw_root, directory=True)
    private_raw = serialize_external_gold_private_state_v4(prepared)
    _write_exclusive(root / _PRIVATE_STATE_NAME, private_raw)
    ledger = AttemptLedger.create(
        root / _LEDGER_NAME,
        key=prepared.private_material.ledger_key,
        slots=prepared.commitments,
    )
    private_digest = _sha256(private_raw)
    precommit = _public_precommitment(
        prepared,
        ledger_digest=ledger.ledger_digest,
        private_state_digest=private_digest,
    )
    precommit_raw = _canonical_bytes(precommit, lf=True)
    precommit_digest = _sha256(precommit_raw)
    _write_exclusive(root / _PRECOMMIT_NAME, precommit_raw)
    home_body = _home_receipt_body(
        prepared,
        campaign_root=root,
        ledger=ledger,
        private_state_digest=private_digest,
        public_precommitment_digest=precommit_digest,
    )
    home_raw = _signed_home_receipt(
        home_body,
        ledger_key=prepared.private_material.ledger_key,
    )
    _write_exclusive(root / _HOME_RECEIPT_NAME, home_raw)
    home_digest = _sha256(home_raw)
    state0 = _state_record(
        phase="prepared",
        prior_state_digest=None,
        bindings={
            "ledger_home_receipt_digest": home_digest,
            "public_precommitment_digest": precommit_digest,
        },
    )
    _write_exclusive(root / _STATE_NAMES[0], state0)
    bound = BoundExternalGoldCampaignV4(
        prepared=prepared,
        campaign_root=str(root),
        private_state_digest=private_digest,
        attempt_ledger_digest=ledger.ledger_digest,
        public_precommitment=precommit,
        public_precommitment_digest=precommit_digest,
        ledger_home_receipt_digest=home_digest,
    )
    bound.validate_current()
    if any(ledger.state(slot.slot_id) is not SlotState.PENDING for slot in prepared.slots):
        raise LedgerIntegrityError("external-gold home was not created fully pending")
    return bound


def restore_external_gold_campaign_home_v4(
    prepared: PreparedExternalGoldCampaignV4,
    campaign_root: str | Path,
    *,
    expected_public_precommitment_digest: str,
    random_precommitment_bytes: bytes,
    random_beacon_receipt_bytes: bytes,
    expected_random_precommitment_record_sha256: str,
    expected_random_beacon_receipt_record_sha256: str,
    external_selection_precommit_anchor_evidence_digest: str,
    random_source: object,
    random_selection_capability: object,
    random_audit_input: object,
) -> BoundExternalGoldCampaignV4:
    fresh = _fresh_random_external_replay_v4(
        random_precommitment_bytes,
        random_beacon_receipt_bytes,
        expected_precommitment_record_sha256=(expected_random_precommitment_record_sha256),
        expected_beacon_receipt_record_sha256=(expected_random_beacon_receipt_record_sha256),
        external_selection_precommit_anchor_evidence_digest=(
            external_selection_precommit_anchor_evidence_digest
        ),
        source=random_source,
        expected_capability=random_selection_capability,
        expected_audit_input=random_audit_input,
    )
    if fresh.evidence != prepared.random_replay_evidence:
        raise ValueError("external-gold restore differs from fresh random cold replay")
    root, _device, _inode = _path_identity(Path(campaign_root), directory=True)
    private_raw, _private_status = _read_single_regular(
        root / _PRIVATE_STATE_NAME,
        maximum=EXTERNAL_GOLD_PRIVATE_STATE_MAX_BYTES_V4,
    )
    if (
        deserialize_external_gold_private_state_v4(
            private_raw,
            prepared.selection,
            fresh.evidence,
            prepared.sealed_vault,
        )
        != prepared
    ):
        raise ValueError("external-gold restored private state differs")
    precommit_raw, _precommit_status = _read_single_regular(
        root / _PRECOMMIT_NAME,
        maximum=EXTERNAL_GOLD_PUBLIC_PRECOMMIT_MAX_BYTES_V4,
    )
    parsed = parse_external_gold_public_precommitment_v4(
        precommit_raw,
        expected_digest=expected_public_precommitment_digest,
    )
    ledger = AttemptLedger.open(root / _LEDGER_NAME, key=prepared.private_material.ledger_key)
    receipt_raw, _receipt_status = _read_single_regular(
        root / _HOME_RECEIPT_NAME,
        maximum=1_000_000,
    )
    bound = BoundExternalGoldCampaignV4(
        prepared=prepared,
        campaign_root=str(root),
        private_state_digest=_sha256(private_raw),
        attempt_ledger_digest=ledger.ledger_digest,
        public_precommitment=parsed,
        public_precommitment_digest=expected_public_precommitment_digest,
        ledger_home_receipt_digest=_sha256(receipt_raw),
    )
    bound.validate_current()
    return bound


def _witness_envelope(
    body: dict[str, object],
    *,
    signer: CanonicalEd25519SignerV3,
    domain: str,
) -> bytes:
    if type(signer) is not CanonicalEd25519SignerV3:
        raise ValueError("external-gold witness signer must be exact")
    expected = CanonicalEd25519VerifierV3(signer.verifier().public_key_bytes(), domain=domain)
    if signer.authority_commitment != expected.authority_commitment:
        raise ValueError("external-gold witness signer uses the wrong domain")
    record = _canonical_bytes(body)
    return _canonical_bytes(
        {
            "authority_commitment": expected.authority_commitment,
            "body": body,
            "key_fingerprint": expected.key_fingerprint,
            "signature": signer.sign(record),
        },
        lf=True,
    )


def _verify_witness_envelope(
    raw: bytes,
    *,
    verifier: CanonicalEd25519VerifierV3,
    domain: str,
    label: str,
) -> dict[str, object]:
    if type(verifier) is not CanonicalEd25519VerifierV3:
        raise ValueError(f"{label} verifier must be exact")
    expected = CanonicalEd25519VerifierV3(verifier.public_key_bytes(), domain=domain)
    if verifier.authority_commitment != expected.authority_commitment:
        raise ValueError(f"{label} verifier uses the wrong domain")
    envelope = _exact_mapping(
        _decode_canonical_json(raw, maximum=1_000_000, label=label),
        {"authority_commitment", "body", "key_fingerprint", "signature"},
        label,
    )
    if (
        envelope["authority_commitment"] != expected.authority_commitment
        or envelope["key_fingerprint"] != expected.key_fingerprint
        or type(envelope["body"]) is not dict
        or type(envelope["signature"]) is not str
        or _SIGNATURE_RE.fullmatch(envelope["signature"]) is None
    ):
        raise ValueError(f"{label} authority fields differ")
    expected.verify(_canonical_bytes(envelope["body"]), envelope["signature"])
    return envelope["body"]


def create_external_gold_anchor_acknowledgement_v4(
    *,
    public_precommitment_digest: str,
    ledger_home_receipt_digest: str,
    code_commit_oid: str,
    code_tag_object_oid: str,
    anchor_namespace: str,
    anchor_object_oid: str,
    acknowledged_at_ns: int,
    signer: CanonicalEd25519SignerV3,
) -> bytes:
    """Create the privileged external-witness receipt consumed by the collector.

    Custody/separation of this witness key remains an explicit operator trust
    boundary; the collector proves the signed acknowledgement existed before any
    STARTED record, not that the same OS user could never access the key.
    """

    for value, label in (
        (public_precommitment_digest, "external-gold anchored precommitment"),
        (ledger_home_receipt_digest, "external-gold anchored home receipt"),
    ):
        _digest(value, label)
    for value, label in (
        (code_commit_oid, "external-gold anchored commit"),
        (code_tag_object_oid, "external-gold anchored tag object"),
        (anchor_object_oid, "external-gold external anchor object"),
    ):
        if type(value) is not str or _GIT_OID_RE.fullmatch(value) is None:
            raise ValueError(f"{label} is invalid")
    if type(anchor_namespace) is not str or not re.fullmatch(
        r"[a-z][a-z0-9._:/-]{2,255}", anchor_namespace
    ):
        raise ValueError("external-gold anchor namespace is invalid")
    if type(acknowledged_at_ns) is not int or acknowledged_at_ns <= 0:
        raise ValueError("external-gold anchor acknowledgement time is invalid")
    body = {
        "acknowledged_at_ns": acknowledged_at_ns,
        "anchor_namespace": anchor_namespace,
        "anchor_object_oid": anchor_object_oid,
        "code_commit_oid": code_commit_oid,
        "code_tag_object_oid": code_tag_object_oid,
        "ledger_home_receipt_digest": ledger_home_receipt_digest,
        "public_precommitment_digest": public_precommitment_digest,
        "schema": EXTERNAL_GOLD_ANCHOR_ACK_SCHEMA_V4,
        "schema_version": EXTERNAL_GOLD_AUDIT_VERSION_V4,
    }
    return _witness_envelope(
        body,
        signer=signer,
        domain=EXTERNAL_GOLD_ANCHOR_SIGNING_DOMAIN_V4,
    )


def _anchor_verifier(prepared: PreparedExternalGoldCampaignV4) -> CanonicalEd25519VerifierV3:
    return CanonicalEd25519VerifierV3(
        prepared.anchor_verifier_public_key,
        domain=EXTERNAL_GOLD_ANCHOR_SIGNING_DOMAIN_V4,
    )


def _recovery_verifier(prepared: PreparedExternalGoldCampaignV4) -> CanonicalEd25519VerifierV3:
    return CanonicalEd25519VerifierV3(
        prepared.recovery_verifier_public_key,
        domain=EXTERNAL_GOLD_RECOVERY_SIGNING_DOMAIN_V4,
    )


def _validate_anchor_acknowledgement(
    bound: BoundExternalGoldCampaignV4,
    raw: bytes,
) -> dict[str, object]:
    body = _verify_witness_envelope(
        raw,
        verifier=_anchor_verifier(bound.prepared),
        domain=EXTERNAL_GOLD_ANCHOR_SIGNING_DOMAIN_V4,
        label="external-gold anchor acknowledgement",
    )
    value = _exact_mapping(
        body,
        {
            "acknowledged_at_ns",
            "anchor_namespace",
            "anchor_object_oid",
            "code_commit_oid",
            "code_tag_object_oid",
            "ledger_home_receipt_digest",
            "public_precommitment_digest",
            "schema",
            "schema_version",
        },
        "external-gold anchor acknowledgement",
    )
    if (
        value["schema"] != EXTERNAL_GOLD_ANCHOR_ACK_SCHEMA_V4
        or value["schema_version"] != EXTERNAL_GOLD_AUDIT_VERSION_V4
        or value["public_precommitment_digest"] != bound.public_precommitment_digest
        or value["ledger_home_receipt_digest"] != bound.ledger_home_receipt_digest
        or value["code_commit_oid"] != bound.prepared.code_identity.commit_oid
        or value["code_tag_object_oid"] != bound.prepared.code_identity.tag_object_oid
        or type(value["acknowledged_at_ns"]) is not int
        or value["acknowledged_at_ns"] <= 0
        or type(value["anchor_namespace"]) is not str
        or type(value["anchor_object_oid"]) is not str
        or _GIT_OID_RE.fullmatch(value["anchor_object_oid"]) is None
    ):
        raise ValueError("external-gold anchor acknowledgement bindings differ")
    return value


def anchor_external_gold_campaign_v4(
    bound: BoundExternalGoldCampaignV4,
    acknowledgement_bytes: bytes,
    *,
    expected_acknowledgement_digest: str,
) -> str:
    if type(bound) is not BoundExternalGoldCampaignV4 or type(acknowledgement_bytes) is not bytes:
        raise ValueError("external-gold anchoring requires exact bound campaign and bytes")
    bound.validate_current()
    expected = _digest(expected_acknowledgement_digest, "external-gold anchor acknowledgement")
    if not hmac.compare_digest(_sha256(acknowledgement_bytes), expected):
        raise ValueError("external-gold acknowledgement differs from external witness digest")
    _validate_anchor_acknowledgement(bound, acknowledgement_bytes)
    ledger = bound.open_ledger()
    if any(ledger.state(slot.slot_id) is not SlotState.PENDING for slot in bound.prepared.slots):
        raise LedgerIntegrityError("external-gold external anchor must precede every STARTED")
    _write_exclusive(bound.root / _ANCHOR_ACK_NAME, acknowledgement_bytes)
    _state0, state0_raw = _read_state(bound.root, 0)
    state1 = _state_record(
        phase="anchored",
        prior_state_digest=_sha256(state0_raw),
        bindings={
            "anchor_acknowledgement_digest": expected,
            "public_precommitment_digest": bound.public_precommitment_digest,
        },
    )
    _write_exclusive(bound.root / _STATE_NAMES[1], state1)
    return expected


def _load_anchored_state(bound: BoundExternalGoldCampaignV4) -> tuple[str, bytes]:
    raw, _status = _read_single_regular(bound.root / _ANCHOR_ACK_NAME, maximum=1_000_000)
    digest = _sha256(raw)
    _validate_anchor_acknowledgement(bound, raw)
    state1, state1_raw = _read_state(bound.root, 1)
    if state1["phase"] != "anchored" or state1["bindings"] != {
        "anchor_acknowledgement_digest": digest,
        "public_precommitment_digest": bound.public_precommitment_digest,
    }:
        raise ValueError("external-gold anchored campaign state differs")
    return digest, state1_raw


_CAMPAIGN_LEASE_TOKEN_V4 = object()


@dataclass(slots=True, init=False)
class ExternalGoldCampaignLeaseV4:
    campaign_root: str
    descriptor: int = field(repr=False)
    lock_device: int
    lock_inode: int
    lock_bytes: bytes = field(repr=False)
    lock_digest: str
    recovery_authorized: bool
    recovery_acknowledgement_digest: str | None
    closed: bool = False

    def __new__(cls, token: object = None) -> ExternalGoldCampaignLeaseV4:
        if token is not _CAMPAIGN_LEASE_TOKEN_V4:
            raise TypeError("external-gold campaign leases require exclusive acquisition")
        return object.__new__(cls)

    def validate_active(self, bound: BoundExternalGoldCampaignV4) -> None:
        if type(self) is not ExternalGoldCampaignLeaseV4 or self.closed:
            raise ValueError("external-gold campaign lease is closed or inexact")
        if self.campaign_root != bound.campaign_root:
            raise ValueError("external-gold campaign lease belongs to another home")
        _digest(self.lock_digest, "external-gold lifecycle lock")
        if _sha256(self.lock_bytes) != self.lock_digest:
            raise ValueError("external-gold lifecycle lock digest is stale")
        try:
            descriptor_status = os.fstat(self.descriptor)
            path_status = (bound.root / _LIFECYCLE_LOCK_NAME).lstat()
            current = (bound.root / _LIFECYCLE_LOCK_NAME).read_bytes()
        except OSError as error:
            raise ValueError("external-gold lifecycle lock is no longer held") from error
        if (
            not stat.S_ISREG(path_status.st_mode)
            or path_status.st_nlink != 1
            or (descriptor_status.st_dev, descriptor_status.st_ino)
            != (self.lock_device, self.lock_inode)
            or (path_status.st_dev, path_status.st_ino) != (self.lock_device, self.lock_inode)
            or current != self.lock_bytes
        ):
            raise ValueError("external-gold lifecycle lock identity changed")
        record = _decode_canonical_json(
            self.lock_bytes,
            maximum=1_000_000,
            label="external-gold lifecycle lock",
        )
        if (
            record.get("campaign_root") != self.campaign_root
            or record.get("ledger_home_receipt_digest") != bound.ledger_home_receipt_digest
            or record.get("public_precommitment_digest") != bound.public_precommitment_digest
            or record.get("recovery_authorized") is not self.recovery_authorized
            or record.get("recovery_acknowledgement_digest") != self.recovery_acknowledgement_digest
        ):
            raise ValueError("external-gold lifecycle lock bindings differ")

    def release_after_terminal(self, bound: BoundExternalGoldCampaignV4) -> None:
        self.validate_active(bound)
        state3, _state3_raw = _read_state(bound.root, 3)
        if state3["phase"] != "terminal":
            raise ValueError("external-gold lifecycle lease cannot release before terminal state")
        path = bound.root / _LIFECYCLE_LOCK_NAME
        status = path.lstat()
        if (status.st_dev, status.st_ino) != (self.lock_device, self.lock_inode):
            raise ValueError("external-gold lifecycle lock was replaced before release")
        os.close(self.descriptor)
        self.closed = True
        current = path.lstat()
        if (current.st_dev, current.st_ino) != (self.lock_device, self.lock_inode):
            raise ValueError("external-gold lifecycle lock changed during release")
        path.unlink()
        _fsync_directory(bound.root)


def _mint_campaign_lease_v4(
    *,
    bound: BoundExternalGoldCampaignV4,
    descriptor: int,
    status: os.stat_result,
    raw: bytes,
    recovery_authorized: bool,
    recovery_acknowledgement_digest: str | None,
) -> ExternalGoldCampaignLeaseV4:
    instance = ExternalGoldCampaignLeaseV4(_CAMPAIGN_LEASE_TOKEN_V4)
    object.__setattr__(instance, "campaign_root", bound.campaign_root)
    object.__setattr__(instance, "descriptor", descriptor)
    object.__setattr__(instance, "lock_device", int(status.st_dev))
    object.__setattr__(instance, "lock_inode", int(status.st_ino))
    object.__setattr__(instance, "lock_bytes", raw)
    object.__setattr__(instance, "lock_digest", _sha256(raw))
    object.__setattr__(instance, "recovery_authorized", recovery_authorized)
    object.__setattr__(
        instance,
        "recovery_acknowledgement_digest",
        recovery_acknowledgement_digest,
    )
    object.__setattr__(instance, "closed", False)
    try:
        instance.validate_active(bound)
    except BaseException:
        os.close(descriptor)
        object.__setattr__(instance, "closed", True)
        raise
    return instance


def _lock_record(
    bound: BoundExternalGoldCampaignV4,
    *,
    anchor_acknowledgement_digest: str,
    recovery_authorized: bool,
    recovery_acknowledgement_digest: str | None,
) -> bytes:
    return _canonical_bytes(
        {
            "acquired_at_ns": time.time_ns(),
            "anchor_acknowledgement_digest": anchor_acknowledgement_digest,
            "campaign_root": bound.campaign_root,
            "ledger_home_receipt_digest": bound.ledger_home_receipt_digest,
            "lock_nonce": secrets.token_hex(32),
            "owner_pid": os.getpid(),
            "public_precommitment_digest": bound.public_precommitment_digest,
            "recovery_acknowledgement_digest": recovery_acknowledgement_digest,
            "recovery_authorized": recovery_authorized,
            "schema": "org.math-research-brain/external-gold-lifecycle-lock-v4",
            "schema_version": EXTERNAL_GOLD_AUDIT_VERSION_V4,
        },
        lf=True,
    )


def acquire_external_gold_campaign_lease_v4(
    bound: BoundExternalGoldCampaignV4,
) -> ExternalGoldCampaignLeaseV4:
    if type(bound) is not BoundExternalGoldCampaignV4:
        raise ValueError("external-gold lease acquisition requires exact bound state")
    bound.validate_current()
    anchor_digest, state1_raw = _load_anchored_state(bound)
    if (bound.root / _STATE_NAMES[2]).exists():
        raise ValueError("external-gold collecting state already exists; recovery is required")
    ledger = bound.open_ledger()
    if any(ledger.state(slot.slot_id) is not SlotState.PENDING for slot in bound.prepared.slots):
        raise LedgerIntegrityError("external-gold fresh collector requires two pending slots")
    raw = _lock_record(
        bound,
        anchor_acknowledgement_digest=anchor_digest,
        recovery_authorized=False,
        recovery_acknowledgement_digest=None,
    )
    descriptor, status = _open_exclusive(bound.root / _LIFECYCLE_LOCK_NAME, raw)
    lease = _mint_campaign_lease_v4(
        bound=bound,
        descriptor=descriptor,
        status=status,
        raw=raw,
        recovery_authorized=False,
        recovery_acknowledgement_digest=None,
    )
    state2 = _state_record(
        phase="collecting",
        prior_state_digest=_sha256(state1_raw),
        bindings={
            "anchor_acknowledgement_digest": anchor_digest,
            "lifecycle_lock_digest": lease.lock_digest,
        },
    )
    try:
        _write_exclusive(bound.root / _STATE_NAMES[2], state2)
        lease.validate_active(bound)
    except BaseException:
        # Keep the lock and fail closed.  A partially-started lifecycle can only
        # be taken over through the separately signed post-crash recovery path.
        raise
    return lease


def create_external_gold_post_crash_ownership_v4(
    *,
    public_precommitment_digest: str,
    ledger_home_receipt_digest: str,
    stale_lifecycle_lock_digest: str,
    recovery_owner_nonce: str,
    acknowledged_at_ns: int,
    signer: CanonicalEd25519SignerV3,
) -> bytes:
    for value, label in (
        (public_precommitment_digest, "external-gold recovery precommitment"),
        (ledger_home_receipt_digest, "external-gold recovery home receipt"),
        (stale_lifecycle_lock_digest, "external-gold stale lifecycle lock"),
    ):
        _digest(value, label)
    if (
        type(recovery_owner_nonce) is not str
        or _LOCK_NONCE_RE.fullmatch(recovery_owner_nonce) is None
    ):
        raise ValueError("external-gold recovery owner nonce is invalid")
    if type(acknowledged_at_ns) is not int or acknowledged_at_ns <= 0:
        raise ValueError("external-gold recovery acknowledgement time is invalid")
    return _witness_envelope(
        {
            "acknowledged_at_ns": acknowledged_at_ns,
            "ledger_home_receipt_digest": ledger_home_receipt_digest,
            "public_precommitment_digest": public_precommitment_digest,
            "recovery_owner_nonce": recovery_owner_nonce,
            "schema": EXTERNAL_GOLD_RECOVERY_ACK_SCHEMA_V4,
            "schema_version": EXTERNAL_GOLD_AUDIT_VERSION_V4,
            "stale_lifecycle_lock_digest": stale_lifecycle_lock_digest,
        },
        signer=signer,
        domain=EXTERNAL_GOLD_RECOVERY_SIGNING_DOMAIN_V4,
    )


def acquire_external_gold_recovery_lease_v4(
    bound: BoundExternalGoldCampaignV4,
    ownership_acknowledgement_bytes: bytes,
    *,
    expected_acknowledgement_digest: str,
) -> ExternalGoldCampaignLeaseV4:
    if (
        type(bound) is not BoundExternalGoldCampaignV4
        or type(ownership_acknowledgement_bytes) is not bytes
    ):
        raise ValueError("external-gold recovery requires exact campaign and witness bytes")
    bound.validate_current()
    anchor_digest, _state1_raw = _load_anchored_state(bound)
    state2, _state2_raw = _read_state(bound.root, 2)
    if state2["phase"] != "collecting" or (bound.root / _STATE_NAMES[3]).exists():
        raise ValueError("external-gold recovery requires nonterminal collecting state")
    stale_raw, _stale_status = _read_single_regular(
        bound.root / _LIFECYCLE_LOCK_NAME,
        maximum=1_000_000,
    )
    stale_digest = _sha256(stale_raw)
    if state2["bindings"] != {
        "anchor_acknowledgement_digest": anchor_digest,
        "lifecycle_lock_digest": stale_digest,
    }:
        raise ValueError("external-gold recovery state binds another lifecycle lock")
    expected_ack = _digest(
        expected_acknowledgement_digest,
        "external-gold recovery acknowledgement",
    )
    if _sha256(ownership_acknowledgement_bytes) != expected_ack:
        raise ValueError("external-gold recovery acknowledgement differs from witness digest")
    body = _verify_witness_envelope(
        ownership_acknowledgement_bytes,
        verifier=_recovery_verifier(bound.prepared),
        domain=EXTERNAL_GOLD_RECOVERY_SIGNING_DOMAIN_V4,
        label="external-gold recovery acknowledgement",
    )
    value = _exact_mapping(
        body,
        {
            "acknowledged_at_ns",
            "ledger_home_receipt_digest",
            "public_precommitment_digest",
            "recovery_owner_nonce",
            "schema",
            "schema_version",
            "stale_lifecycle_lock_digest",
        },
        "external-gold recovery acknowledgement",
    )
    if (
        value["schema"] != EXTERNAL_GOLD_RECOVERY_ACK_SCHEMA_V4
        or value["schema_version"] != EXTERNAL_GOLD_AUDIT_VERSION_V4
        or value["public_precommitment_digest"] != bound.public_precommitment_digest
        or value["ledger_home_receipt_digest"] != bound.ledger_home_receipt_digest
        or value["stale_lifecycle_lock_digest"] != stale_digest
        or type(value["recovery_owner_nonce"]) is not str
        or _LOCK_NONCE_RE.fullmatch(value["recovery_owner_nonce"]) is None
        or type(value["acknowledged_at_ns"]) is not int
        or value["acknowledged_at_ns"] <= 0
    ):
        raise ValueError("external-gold recovery witness bindings differ")
    claim_raw = _canonical_bytes(
        {
            "acknowledgement_digest": expected_ack,
            "claim_nonce": secrets.token_hex(32),
            "home_receipt_digest": bound.ledger_home_receipt_digest,
            "schema": "org.math-research-brain/external-gold-recovery-claim-v4",
        },
        lf=True,
    )
    claim_descriptor, _claim_status = _open_exclusive(
        bound.root / _RECOVERY_CLAIM_NAME,
        claim_raw,
    )
    try:
        recovery_ack_path = bound.root / _RECOVERY_ACK_NAME
        if recovery_ack_path.exists():
            observed_ack, _observed_status = _read_single_regular(
                recovery_ack_path,
                maximum=1_000_000,
            )
            if observed_ack != ownership_acknowledgement_bytes:
                raise ValueError("external-gold durable recovery acknowledgement differs")
        else:
            _write_exclusive(recovery_ack_path, ownership_acknowledgement_bytes)
        recovered_path = bound.root / _RECOVERED_LOCK_NAME
        if recovered_path.exists() or recovered_path.is_symlink():
            raise ValueError("external-gold campaign already consumed one recovery")
        os.rename(bound.root / _LIFECYCLE_LOCK_NAME, recovered_path)
        _fsync_directory(bound.root)
        new_raw = _lock_record(
            bound,
            anchor_acknowledgement_digest=anchor_digest,
            recovery_authorized=True,
            recovery_acknowledgement_digest=expected_ack,
        )
        descriptor, status = _open_exclusive(
            bound.root / _LIFECYCLE_LOCK_NAME,
            new_raw,
        )
    finally:
        os.close(claim_descriptor)
        claim_path = bound.root / _RECOVERY_CLAIM_NAME
        if claim_path.exists():
            claim_path.unlink()
            _fsync_directory(bound.root)
    lease = _mint_campaign_lease_v4(
        bound=bound,
        descriptor=descriptor,
        status=status,
        raw=new_raw,
        recovery_authorized=True,
        recovery_acknowledgement_digest=expected_ack,
    )
    return lease


def recover_orphaned_external_gold_starts_v4(
    bound: BoundExternalGoldCampaignV4,
    lease: ExternalGoldCampaignLeaseV4,
) -> tuple[str, ...]:
    """Burn orphaned STARTED slots only under signed exclusive post-crash ownership."""

    if (
        type(lease) is not ExternalGoldCampaignLeaseV4
        or not lease.recovery_authorized
        or (lease.recovery_acknowledgement_digest is None)
    ):
        raise ValueError("external-gold orphan recovery lacks exclusive post-crash ownership")
    lease.validate_active(bound)
    ledger = bound.open_ledger()
    recovery = ledger.recover_orphans()
    for slot_id in recovery.burned_without_outcome:
        started = ledger.started_attempt(slot_id)
        if started is None or ledger.terminal_transition(slot_id) is not None:
            raise LedgerIntegrityError("external-gold orphan history changed concurrently")
        ledger.record_failure(started, failure_code="orphaned_started")
    return recovery.burned_without_outcome


def _validate_live_inputs(
    prepared: PreparedExternalGoldCampaignV4,
    *,
    repository_root: Path,
    auth_file: str | Path,
) -> bytes:
    observed_code = capture_external_gold_code_identity_v4(
        repository_root,
        annotated_tag=prepared.code_identity.annotated_tag,
    )
    if observed_code != prepared.code_identity:
        raise ValueError("external-gold code identity changed after precommitment")
    observed_executable = capture_codex_executable_provenance(
        prepared.policy.executable_provenance.canonical_path
    )
    if observed_executable != prepared.policy.executable_provenance:
        raise ValueError("external-gold executable changed after precommitment")
    auth_snapshot, auth_bytes = _capture_auth(
        auth_file,
        ledger_key=prepared.private_material.ledger_key,
    )
    if auth_snapshot != prepared.auth_snapshot:
        raise ValueError("external-gold auth identity or bytes changed after precommitment")
    return auth_bytes


def _run_native_slot_v4(
    plan: ExternalGoldAuditPlanV4,
    policy: ExternalGoldAuditRunPolicyV4,
    auth_bytes: bytes,
    *,
    ledger: AttemptLedger,
    slot: PreparedExternalGoldSlotV4,
    runtime_parent: Path,
    repository_root: Path,
) -> TerminalTransition:
    """Run one slot with only its plan, public run policy, and auth bytes.

    In particular this function cannot receive a prepared campaign, sealed-vault
    object, raw/path/entry vault material, reviewer signing seed, or report key.
    """

    if (
        type(plan) is not ExternalGoldAuditPlanV4
        or type(policy) is not (ExternalGoldAuditRunPolicyV4)
        or type(auth_bytes) is not bytes
    ):
        raise ValueError("external-gold native slot inputs are inexact")
    plan.__post_init__()
    policy.__post_init__()

    def runner(runtime: Path, dispatch: DispatchMaterial) -> ArtifactPayload:
        config = CodexRunConfig(
            cwd=runtime,
            model=policy.requested_model,
            effort=policy.requested_effort,
            model_provider=policy.requested_provider,
            timeout_seconds=policy.timeout_seconds,
            executable=policy.executable_provenance.canonical_path,
            expected_executable_provenance=policy.executable_provenance,
            auth_bytes=auth_bytes,
            base_instructions=policy.base_instructions,
            developer_instructions=policy.developer_instructions,
            max_event_line_bytes=EXTERNAL_GOLD_RUN_MAX_EVENT_LINE_BYTES_V4,
            max_transcript_bytes=EXTERNAL_GOLD_RUN_MAX_TRANSCRIPT_BYTES_V4,
            max_stderr_bytes=EXTERNAL_GOLD_RUN_MAX_STDERR_BYTES_V4,
        )
        result = CodexAppServerRunner(config).run(
            prompt=dispatch.prompt,
            output_schema=json.loads(dispatch.output_schema_json),
        )
        return ArtifactPayload(
            serialize_codex_run_result(result),
            EXTERNAL_GOLD_PRIVATE_ARTIFACT_MEDIA_TYPE_V4,
        )

    return ledger.execute_first_attempt(
        slot.slot_id,
        runtime_parent=runtime_parent,
        repository_root=repository_root,
        dispatch=slot.dispatch,
        runner=runner,
    )


def _policy_matches(
    artifact: CodexRunResult,
    plan: ExternalGoldAuditPlanV4,
    policy: ExternalGoldAuditRunPolicyV4,
) -> bool:
    expected = policy.executable_provenance
    return bool(
        artifact.prompt_text == plan.prompt_text
        and hmac.compare_digest(artifact.output_schema_json, plan.output_schema_json)
        and artifact.requested_model == policy.requested_model
        and artifact.requested_provider == policy.requested_provider
        and artifact.requested_effort == policy.requested_effort
        and artifact.timeout_seconds == policy.timeout_seconds
        and artifact.executable_path == expected.canonical_path
        and artifact.executable_size_bytes == expected.size_bytes
        and artifact.executable_sha256_digest == expected.sha256_digest
        and artifact.command_tail_policy_digest == policy.command_policy_digest
        and artifact.solver_process_isolation_policy_digest == policy.process_policy_digest
        and artifact.request_policy_material == _expected_request_policy_material(policy)
        and artifact.standard_popen_used
        and (os.name != "nt" or artifact.executable_lock_enforced)
        and artifact.codex_home_initial_auth_only
        and artifact.codex_home_cleanup_verified
        and artifact.credential_leak_scan_passed
        and artifact.process_exit_code == 0
        and not artifact.forced_termination
        and artifact.audit.eligible
        and artifact.audit.reasons == ()
        and artifact.audit.accepted_model == policy.requested_model
        and artifact.audit.accepted_provider == policy.requested_provider
        and artifact.audit.accepted_effort == policy.requested_effort
        and artifact.audit.reroutes == ()
        and artifact.audit.final_message_count == 1
        and artifact.audit.model_identity_verified is False
    )


def _run_evidence(
    artifact: CodexRunResult,
    policy: ExternalGoldAuditRunPolicyV4,
) -> ExternalGoldRunEvidenceV4:
    accepted = (
        artifact.audit.accepted_model,
        artifact.audit.accepted_provider,
        artifact.audit.accepted_effort,
    )
    if (
        not artifact.standard_popen_used
        or artifact.codex_home_path_digest is None
        or any(type(value) is not str or not value for value in accepted)
    ):
        raise ValueError("external-gold run lacks exact fresh-process evidence")
    return ExternalGoldRunEvidenceV4(
        protocol_execution_id=artifact.protocol_execution_id,
        execution_nonce=artifact.execution_nonce,
        thread_id=artifact.thread_id,
        turn_id=artifact.turn_id,
        transcript_digest=artifact.transcript_hash,
        stderr_digest=artifact.stderr_hash,
        runtime_directory_device=artifact.runtime_directory_device,
        runtime_directory_inode=artifact.runtime_directory_inode,
        codex_home_directory_device=artifact.codex_home_directory_device,
        codex_home_directory_inode=artifact.codex_home_directory_inode,
        codex_home_path_digest=artifact.codex_home_path_digest,
        executable_sha256_digest=artifact.executable_sha256_digest,
        executable_size_bytes=artifact.executable_size_bytes,
        command_policy_digest=artifact.command_tail_policy_digest,
        process_policy_digest=artifact.solver_process_isolation_policy_digest,
        request_policy_digest=policy.request_policy_digest,
        requested_model=artifact.requested_model,
        requested_provider=artifact.requested_provider,
        requested_effort=artifact.requested_effort,
        accepted_model=accepted[0],
        accepted_provider=accepted[1],
        accepted_effort=accepted[2],
        timeout_seconds=artifact.timeout_seconds,
        model_identity_verified=False,
    )


@dataclass(frozen=True, slots=True)
class _ReturnedEvaluationV4:
    status: ExternalGoldReceiptStatusV4
    decisions: tuple[ExternalGoldAuditDecisionV4, ...]
    failure_code: str | None
    run: ExternalGoldRunEvidenceV4 | None


def _evaluate_returned_artifact(
    artifact_bytes: bytes,
    plan: ExternalGoldAuditPlanV4,
    policy: ExternalGoldAuditRunPolicyV4,
) -> _ReturnedEvaluationV4:
    try:
        artifact = deserialize_codex_run_result(artifact_bytes)
        if serialize_codex_run_result(artifact) != artifact_bytes:
            raise ValueError("external-gold Codex artifact is noncanonical")
    except Exception:
        return _ReturnedEvaluationV4(
            ExternalGoldReceiptStatusV4.FAILED,
            (),
            "artifact_invalid",
            None,
        )
    if not _policy_matches(artifact, plan, policy):
        return _ReturnedEvaluationV4(
            ExternalGoldReceiptStatusV4.FAILED,
            (),
            "policy_mismatch",
            None,
        )
    run = _run_evidence(artifact, policy)
    try:
        if type(artifact.output_text) is not str:
            raise ValueError("external-gold artifact lacks output text")
        decisions = parse_external_gold_audit_output_v4(artifact.output_text, plan)
    except Exception:
        return _ReturnedEvaluationV4(
            ExternalGoldReceiptStatusV4.FAILED,
            (),
            "decision_invalid",
            run,
        )
    return _ReturnedEvaluationV4(
        ExternalGoldReceiptStatusV4.RETURNED,
        decisions,
        None,
        run,
    )


def _parse_failure_artifact(raw: bytes) -> str:
    value = _decode_canonical_json(raw + b"\n", maximum=1_000_000, label="failure artifact")
    record = _exact_mapping(
        value,
        {"detail_commitment", "failure_code"},
        "external-gold failure artifact",
    )
    if record["failure_code"] not in {"runner_exception", "orphaned_started"}:
        raise LedgerIntegrityError("external-gold failure code was not collector-issued")
    if record["detail_commitment"] is not None:
        _hmac_value(record["detail_commitment"], "external-gold failure detail")
    return record["failure_code"]


def _attempt_evidence(
    ledger: AttemptLedger,
    slot: PreparedExternalGoldSlotV4,
    terminal: TerminalTransition,
    artifact_bytes: bytes,
) -> ExternalGoldAttemptEvidenceV4:
    history = ledger.history_records()
    record = history[slot.ordinal]
    if record.slot != slot.commitment or record.started is None or record.selected_outcome is None:
        raise LedgerIntegrityError("external-gold attempt history is incomplete")
    started = record.started
    selected = record.selected_outcome
    if (
        record.terminal != terminal
        or started.slot_id != slot.slot_id
        or selected.slot_id != slot.slot_id
        or terminal.slot_id != slot.slot_id
        or started.ordinal != slot.ordinal
        or selected.ordinal != slot.ordinal
        or terminal.ordinal != slot.ordinal
        or selected.started_record_digest != started.record_digest
        or selected.object_digest != terminal.object_digest
        or selected.kind is not terminal.kind
        or started.prerequisite_terminal_digest is not None
    ):
        raise LedgerIntegrityError("external-gold attempt history does not form one exact chain")
    payload = ledger.load_selected_artifact(slot.slot_id)
    if payload.media_type != "application/json" or payload.content != artifact_bytes:
        raise LedgerIntegrityError("external-gold selected artifact differs")
    return ExternalGoldAttemptEvidenceV4(
        ledger_digest=ledger.ledger_digest,
        slot_id=slot.slot_id,
        ordinal=slot.ordinal,
        started_record_digest=started.record_digest,
        selection_record_digest=selected.selection_record_digest,
        terminal_record_digest=terminal.record_digest,
        object_digest=terminal.object_digest,
        artifact_payload_digest=_sha256(artifact_bytes),
        artifact_media_type=payload.media_type,
        terminal_kind=terminal.kind.value,
        runtime_device=started.runtime_device,
        runtime_inode=started.runtime_inode,
        prerequisite_terminal_digest=None,
    )


def _receipt_for_terminal(
    bound: BoundExternalGoldCampaignV4,
    ledger: AttemptLedger,
    slot: PreparedExternalGoldSlotV4,
    *,
    anchor_acknowledgement_digest: str,
) -> CollectedExternalGoldAuditV4:
    terminal = ledger.terminal_transition(slot.slot_id)
    if terminal is None:
        raise LedgerIntegrityError("external-gold slot is not terminal")
    payload = ledger.load_selected_artifact(slot.slot_id)
    attempt = _attempt_evidence(ledger, slot, terminal, payload.content)
    plan = bound.prepared.plans[slot.ordinal]
    if terminal.kind is OutcomeKind.FAILED:
        status = ExternalGoldReceiptStatusV4.FAILED
        decisions: tuple[ExternalGoldAuditDecisionV4, ...] = ()
        failure_code = _parse_failure_artifact(payload.content)
        run = None
    elif terminal.kind is OutcomeKind.RETURNED:
        evaluated = _evaluate_returned_artifact(payload.content, plan, bound.prepared.policy)
        status = evaluated.status
        decisions = evaluated.decisions
        failure_code = evaluated.failure_code
        run = evaluated.run
    else:  # pragma: no cover - closed enum defense
        raise LedgerIntegrityError("external-gold terminal kind is unsupported")
    receipt = ExternalGoldAuditReceiptV4(
        reviewer_slot=slot.reviewer_slot,
        status=status,
        decisions=decisions,
        failure_code=failure_code,
        selection_digest=plan.selection_digest,
        projection_digest=plan.projection_digest,
        order_digest=plan.order_digest,
        presentation_digest=plan.presentation_digest,
        prompt_digest=plan.prompt_digest,
        output_schema_digest=plan.output_schema_digest,
        request_policy_digest=bound.prepared.policy.request_policy_digest,
        public_precommitment_digest=bound.public_precommitment_digest,
        anchor_acknowledgement_digest=anchor_acknowledgement_digest,
        ledger_home_receipt_digest=bound.ledger_home_receipt_digest,
        attempt=attempt,
        run=run,
    )
    return CollectedExternalGoldAuditV4(
        manifest=sign_external_gold_audit_receipt_v4(
            receipt,
            bound.prepared.private_material.signer(slot.reviewer_slot),
        ),
        artifact_bytes=payload.content,
    )


def _history_payload(ledger: AttemptLedger) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for record in ledger.history_records():
        if record.started is None or record.selected_outcome is None or record.terminal is None:
            raise LedgerIntegrityError("external-gold history is not fully terminal")
        artifact = ledger.load_selected_artifact(record.slot.slot_id)
        started_path = (
            ledger.root / "transitions" / f"{record.ordinal:06d}-{record.slot.slot_id}.started.json"
        )
        started_raw, _started_status = _read_single_regular(started_path, maximum=1_000_000)
        if _sha256(started_raw) != record.started.record_digest:
            raise LedgerIntegrityError("external-gold STARTED bytes differ from history")
        try:
            started_record = json.loads(started_raw.decode("ascii"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise LedgerIntegrityError("external-gold STARTED bytes are invalid") from error
        if (
            type(started_record) is not dict
            or _canonical_bytes(started_record) != started_raw
            or type(started_record.get("body")) is not dict
            or type(started_record["body"].get("started_at_ns")) is not int
            or started_record["body"]["started_at_ns"] <= 0
        ):
            raise LedgerIntegrityError("external-gold STARTED timestamp is invalid")
        payload.append(
            {
                "artifact_media_type": artifact.media_type,
                "artifact_payload_digest": _sha256(artifact.content),
                "ledger_digest": record.started.ledger_digest,
                "object_digest": record.selected_outcome.object_digest,
                "ordinal": record.ordinal,
                "prerequisite_terminal_digest": record.started.prerequisite_terminal_digest,
                "runtime_device": record.started.runtime_device,
                "runtime_id": record.started.runtime_id,
                "runtime_inode": record.started.runtime_inode,
                "selection_record_digest": record.selected_outcome.selection_record_digest,
                "slot": record.slot.to_dict(ordinal=record.ordinal),
                "started_record_digest": record.started.record_digest,
                "started_at_ns": started_record["body"]["started_at_ns"],
                "terminal_kind": record.terminal.kind.value,
                "terminal_record_digest": record.terminal.record_digest,
            }
        )
    return payload


def _history_digest(ledger: AttemptLedger) -> str:
    return _domain_digest(
        _history_payload(ledger),
        domain="org.math-research-brain/external-gold-ledger-history/v4",
    )


def collect_external_gold_signed_pair_bytes_v4(
    bound: BoundExternalGoldCampaignV4,
    lease: ExternalGoldCampaignLeaseV4,
    *,
    runtime_parent: str | Path,
    repository_root: str | Path,
    auth_file: str | Path,
) -> bytes:
    """Run pending slots once; never recover STARTED state on the ordinary path."""

    if type(bound) is not BoundExternalGoldCampaignV4 or type(lease) is not (
        ExternalGoldCampaignLeaseV4
    ):
        raise ValueError("external-gold collection requires exact bound state and lease")
    bound.validate_current()
    lease.validate_active(bound)
    anchor_digest, _state1_raw = _load_anchored_state(bound)
    state2, _state2_raw = _read_state(bound.root, 2)
    if lease.recovery_authorized:
        recovered_lock, _recovered_status = _read_single_regular(
            bound.root / _RECOVERED_LOCK_NAME,
            maximum=1_000_000,
        )
        collecting_lock_digest = _sha256(recovered_lock)
    else:
        collecting_lock_digest = lease.lock_digest
    if state2["phase"] != "collecting" or state2["bindings"] != {
        "anchor_acknowledgement_digest": anchor_digest,
        "lifecycle_lock_digest": collecting_lock_digest,
    }:
        raise ValueError("external-gold collecting state binds another lifecycle lock")
    runtime = Path(runtime_parent)
    repository = Path(repository_root)
    if not runtime.is_absolute() or not repository.is_absolute():
        raise ValueError("external-gold runtime and repository roots must be absolute")
    runtime = runtime.resolve(strict=True)
    repository = repository.resolve(strict=True)
    campaign_root = bound.root.resolve(strict=True)
    if (
        runtime == campaign_root
        or runtime in campaign_root.parents
        or campaign_root in runtime.parents
    ):
        raise ValueError(
            "external-gold runtime parent must not contain or enter the private campaign home"
        )
    ledger = bound.open_ledger()
    _validate_live_inputs(bound.prepared, repository_root=repository, auth_file=auth_file)
    for plan, slot in zip(bound.prepared.plans, bound.prepared.slots, strict=True):
        lease.validate_active(bound)
        state = ledger.state(slot.slot_id)
        if state is SlotState.STARTED:
            raise LedgerIntegrityError(
                "external-gold STARTED slot requires signed post-crash recovery"
            )
        if state is SlotState.PENDING:
            auth_bytes = _validate_live_inputs(
                bound.prepared,
                repository_root=repository,
                auth_file=auth_file,
            )
            with suppress(AttemptRunnerFailed):
                _run_native_slot_v4(
                    plan,
                    bound.prepared.policy,
                    auth_bytes,
                    ledger=ledger,
                    slot=slot,
                    runtime_parent=runtime,
                    repository_root=repository,
                )
        elif state not in {SlotState.RETURNED, SlotState.FAILED}:
            raise LedgerIntegrityError("external-gold slot has an unsupported state")
        lease.validate_active(bound)
        started = ledger.started_attempt(slot.slot_id)
        if started is None or started.prerequisite_terminal_digest is not None:
            raise LedgerIntegrityError("external-gold STARTED record acquired a predecessor")
    _validate_live_inputs(bound.prepared, repository_root=repository, auth_file=auth_file)
    collected = tuple(
        _receipt_for_terminal(
            bound,
            ledger,
            slot,
            anchor_acknowledgement_digest=anchor_digest,
        )
        for slot in bound.prepared.slots
    )
    pair = serialize_signed_external_gold_audit_pair_v4(collected[0], collected[1])
    pair_path = bound.root / _SIGNED_PAIR_NAME
    if pair_path.exists():
        existing, _pair_status = _read_single_regular(pair_path, maximum=len(pair))
        if existing != pair:
            raise LedgerIntegrityError("external-gold durable pair differs after recovery")
    else:
        _write_exclusive(pair_path, pair)
    history_digest = _history_digest(ledger)
    pair_digest = _sha256(pair)
    state3_path = bound.root / _STATE_NAMES[3]
    if state3_path.exists():
        state3, _state3_raw = _read_state(bound.root, 3)
        if state3["phase"] != "terminal" or state3["bindings"] != {
            "ledger_history_digest": history_digest,
            "signed_pair_digest": pair_digest,
        }:
            raise LedgerIntegrityError("external-gold terminal state differs")
    else:
        _state2, state2_raw = _read_state(bound.root, 2)
        _write_exclusive(
            state3_path,
            _state_record(
                phase="terminal",
                prior_state_digest=_sha256(state2_raw),
                bindings={
                    "ledger_history_digest": history_digest,
                    "signed_pair_digest": pair_digest,
                },
            ),
        )
    lease.validate_active(bound)
    return pair


def _reviewer_verifier(
    prepared: PreparedExternalGoldCampaignV4,
    slot: ExternalGoldReviewerSlotV4,
) -> CanonicalEd25519VerifierV3:
    return prepared.private_material.signer(slot).verifier()


def _replay_one_receipt(
    collected: CollectedExternalGoldAuditV4,
    *,
    plan: ExternalGoldAuditPlanV4,
    policy: ExternalGoldAuditRunPolicyV4,
    slot: PreparedExternalGoldSlotV4,
    history: dict[str, object],
    precommitment_digest: str,
    anchor_acknowledgement_digest: str,
    home_receipt_digest: str,
) -> None:
    receipt = collected.manifest.receipt
    attempt = receipt.attempt
    expected = {
        "artifact_media_type": attempt.artifact_media_type,
        "artifact_payload_digest": attempt.artifact_payload_digest,
        "ledger_digest": attempt.ledger_digest,
        "object_digest": attempt.object_digest,
        "ordinal": attempt.ordinal,
        "prerequisite_terminal_digest": attempt.prerequisite_terminal_digest,
        "runtime_device": attempt.runtime_device,
        "runtime_inode": attempt.runtime_inode,
        "selection_record_digest": attempt.selection_record_digest,
        "started_record_digest": attempt.started_record_digest,
        "terminal_kind": attempt.terminal_kind,
        "terminal_record_digest": attempt.terminal_record_digest,
    }
    if any(history[name] != value for name, value in expected.items()) or history["slot"] != (
        slot.commitment.to_dict(ordinal=slot.ordinal)
    ):
        raise ValueError("external-gold signed receipt differs from exact ledger history")
    if (
        receipt.reviewer_slot is not slot.reviewer_slot
        or receipt.selection_digest != plan.selection_digest
        or receipt.projection_digest != plan.projection_digest
        or receipt.order_digest != plan.order_digest
        or receipt.presentation_digest != plan.presentation_digest
        or receipt.prompt_digest != plan.prompt_digest
        or receipt.output_schema_digest != plan.output_schema_digest
        or receipt.request_policy_digest != policy.request_policy_digest
        or receipt.public_precommitment_digest != precommitment_digest
        or receipt.anchor_acknowledgement_digest != anchor_acknowledgement_digest
        or receipt.ledger_home_receipt_digest != home_receipt_digest
        or attempt.artifact_payload_digest != _sha256(collected.artifact_bytes)
    ):
        raise ValueError("external-gold signed receipt differs from anchored campaign")
    if attempt.terminal_kind == OutcomeKind.FAILED.value:
        failure_code = _parse_failure_artifact(collected.artifact_bytes)
        if (
            receipt.status is not ExternalGoldReceiptStatusV4.FAILED
            or receipt.failure_code != failure_code
            or receipt.decisions
            or receipt.run is not None
        ):
            raise ValueError("external-gold failed receipt differs from exact artifact")
        return
    if attempt.terminal_kind != OutcomeKind.RETURNED.value:
        raise ValueError("external-gold terminal kind is unsupported")
    evaluated = _evaluate_returned_artifact(collected.artifact_bytes, plan, policy)
    if (
        receipt.status is not evaluated.status
        or receipt.decisions != evaluated.decisions
        or receipt.failure_code != evaluated.failure_code
        or receipt.run != evaluated.run
    ):
        raise ValueError("external-gold receipt differs from cold artifact replay")
    if receipt.run is not None and (
        receipt.run.runtime_directory_device != history["runtime_device"]
        or receipt.run.runtime_directory_inode != history["runtime_inode"]
    ):
        raise ValueError("external-gold artifact runtime differs from STARTED record")


def _validate_fresh_pair(
    left: ExternalGoldAuditReceiptV4,
    right: ExternalGoldAuditReceiptV4,
) -> None:
    if left.run is None or right.run is None:
        return
    unique_fields = (
        "protocol_execution_id",
        "execution_nonce",
        "thread_id",
        "turn_id",
        "codex_home_path_digest",
    )
    for name in unique_fields:
        if getattr(left.run, name) == getattr(right.run, name):
            raise ValueError(f"external-gold fresh processes reuse {name}")
    if (
        left.run.runtime_directory_device,
        left.run.runtime_directory_inode,
    ) == (
        right.run.runtime_directory_device,
        right.run.runtime_directory_inode,
    ) or (
        left.run.codex_home_directory_device,
        left.run.codex_home_directory_inode,
    ) == (
        right.run.codex_home_directory_device,
        right.run.codex_home_directory_inode,
    ):
        raise ValueError("external-gold fresh processes reuse physical directories")
    common = (
        "executable_sha256_digest",
        "executable_size_bytes",
        "command_policy_digest",
        "process_policy_digest",
        "request_policy_digest",
        "requested_model",
        "requested_provider",
        "requested_effort",
        "accepted_model",
        "accepted_provider",
        "accepted_effort",
        "timeout_seconds",
        "model_identity_verified",
    )
    for name in common:
        if getattr(left.run, name) != getattr(right.run, name):
            raise ValueError(f"external-gold reviewer runs differ on fixed field {name}")


def cold_replay_external_gold_campaign_v4(
    signed_pair_bytes: bytes,
    *,
    bound: BoundExternalGoldCampaignV4,
    lease: ExternalGoldCampaignLeaseV4,
    expected_public_precommitment_digest: str,
    expected_anchor_acknowledgement_digest: str,
):
    """Open the exact physical ledger and replay both complete histories/artifacts."""

    if type(signed_pair_bytes) is not bytes or type(bound) is not BoundExternalGoldCampaignV4:
        raise ValueError("external-gold cold replay requires exact immutable inputs")
    bound.validate_current()
    lease.validate_active(bound)
    if expected_public_precommitment_digest != bound.public_precommitment_digest:
        raise ValueError("external-gold cold replay precommitment differs from home")
    anchor_digest, _state1_raw = _load_anchored_state(bound)
    if expected_anchor_acknowledgement_digest != anchor_digest:
        raise ValueError("external-gold cold replay anchor acknowledgement differs")
    state2, _state2_raw = _read_state(bound.root, 2)
    if lease.recovery_authorized:
        collecting_lock, _collecting_lock_status = _read_single_regular(
            bound.root / _RECOVERED_LOCK_NAME,
            maximum=1_000_000,
        )
        collecting_lock_digest = _sha256(collecting_lock)
    else:
        collecting_lock_digest = lease.lock_digest
    if state2["phase"] != "collecting" or state2["bindings"] != {
        "anchor_acknowledgement_digest": anchor_digest,
        "lifecycle_lock_digest": collecting_lock_digest,
    }:
        raise ValueError("external-gold cold replay collecting state differs")
    durable_pair, _pair_status = _read_single_regular(
        bound.root / _SIGNED_PAIR_NAME,
        maximum=max(len(signed_pair_bytes), 1),
    )
    if durable_pair != signed_pair_bytes:
        raise ValueError("external-gold cold replay pair differs from physical home")
    ledger = bound.open_ledger()
    history = _history_payload(ledger)
    if len(history) != 2 or any(
        ledger.state(slot.slot_id) not in {SlotState.RETURNED, SlotState.FAILED}
        for slot in bound.prepared.slots
    ):
        raise ValueError("external-gold cold replay requires exactly two terminal slots")
    history_digest = _history_digest(ledger)
    anchor_raw, _anchor_status = _read_single_regular(
        bound.root / _ANCHOR_ACK_NAME,
        maximum=1_000_000,
    )
    anchor_body = _validate_anchor_acknowledgement(bound, anchor_raw)
    if any(row["started_at_ns"] < anchor_body["acknowledged_at_ns"] for row in history):
        raise ValueError("external-gold STARTED record predates external acknowledgement")
    state3, _state3_raw = _read_state(bound.root, 3)
    if state3["phase"] != "terminal" or state3["bindings"] != {
        "ledger_history_digest": history_digest,
        "signed_pair_digest": _sha256(signed_pair_bytes),
    }:
        raise ValueError("external-gold cold replay terminal state differs")
    reviewer_a, reviewer_b = authenticate_signed_external_gold_audit_pair_v4(
        signed_pair_bytes,
        reviewer_a_verifier=_reviewer_verifier(
            bound.prepared,
            ExternalGoldReviewerSlotV4.REVIEWER_A,
        ),
        reviewer_b_verifier=_reviewer_verifier(
            bound.prepared,
            ExternalGoldReviewerSlotV4.REVIEWER_B,
        ),
    )
    for collected, plan, slot, row in zip(
        (reviewer_a, reviewer_b),
        bound.prepared.plans,
        bound.prepared.slots,
        history,
        strict=True,
    ):
        _replay_one_receipt(
            collected,
            plan=plan,
            policy=bound.prepared.policy,
            slot=slot,
            history=row,
            precommitment_digest=bound.public_precommitment_digest,
            anchor_acknowledgement_digest=anchor_digest,
            home_receipt_digest=bound.ledger_home_receipt_digest,
        )
    _validate_fresh_pair(reviewer_a.manifest.receipt, reviewer_b.manifest.receipt)
    expected_home_entries = {
        _PRIVATE_STATE_NAME,
        _LEDGER_NAME,
        _PRECOMMIT_NAME,
        _HOME_RECEIPT_NAME,
        _ANCHOR_ACK_NAME,
        _SIGNED_PAIR_NAME,
        _LIFECYCLE_LOCK_NAME,
        *_STATE_NAMES,
    }
    if lease.recovery_authorized:
        expected_home_entries.update({_RECOVERED_LOCK_NAME, _RECOVERY_ACK_NAME})
        recovery_raw, _recovery_status = _read_single_regular(
            bound.root / _RECOVERY_ACK_NAME,
            maximum=1_000_000,
        )
        if _sha256(recovery_raw) != lease.recovery_acknowledgement_digest:
            raise ValueError("external-gold durable recovery acknowledgement differs")
        recovered_raw, _recovered_status = _read_single_regular(
            bound.root / _RECOVERED_LOCK_NAME,
            maximum=1_000_000,
        )
        recovery_body = _verify_witness_envelope(
            recovery_raw,
            verifier=_recovery_verifier(bound.prepared),
            domain=EXTERNAL_GOLD_RECOVERY_SIGNING_DOMAIN_V4,
            label="external-gold durable recovery acknowledgement",
        )
        if (
            recovery_body.get("public_precommitment_digest") != bound.public_precommitment_digest
            or recovery_body.get("ledger_home_receipt_digest") != bound.ledger_home_receipt_digest
            or recovery_body.get("stale_lifecycle_lock_digest") != _sha256(recovered_raw)
        ):
            raise ValueError("external-gold durable recovery bindings differ")
    actual_home_entries = {entry.name for entry in bound.root.iterdir()}
    if actual_home_entries != expected_home_entries:
        raise ValueError("external-gold terminal campaign home contains extra or missing entries")
    lease.validate_active(bound)
    return _mint_cold_replayed_campaign_v4(
        reviewer_a=reviewer_a.manifest.receipt,
        reviewer_b=reviewer_b.manifest.receipt,
        signed_pair_bytes=signed_pair_bytes,
        public_precommitment_digest=bound.public_precommitment_digest,
        anchor_acknowledgement_digest=anchor_digest,
        ledger_home_receipt_digest=bound.ledger_home_receipt_digest,
        ledger_history_digest=history_digest,
    )


__all__ = [
    "EXTERNAL_GOLD_PRIVATE_STATE_SCHEMA_V4",
    "EXTERNAL_GOLD_PUBLIC_PRECOMMIT_SCHEMA_V4",
    "BoundExternalGoldCampaignV4",
    "ExternalGoldAuditRunPolicyV4",
    "ExternalGoldAuthSnapshotV4",
    "ExternalGoldCampaignLeaseV4",
    "ExternalGoldCodeIdentityV4",
    "ExternalGoldPrivateMaterialV4",
    "PreparedExternalGoldCampaignV4",
    "PreparedExternalGoldSlotV4",
    "acquire_external_gold_campaign_lease_v4",
    "acquire_external_gold_recovery_lease_v4",
    "anchor_external_gold_campaign_v4",
    "capture_external_gold_auth_snapshot_v4",
    "capture_external_gold_code_identity_v4",
    "cold_replay_external_gold_campaign_v4",
    "collect_external_gold_signed_pair_bytes_v4",
    "create_external_gold_anchor_acknowledgement_v4",
    "create_external_gold_campaign_home_v4",
    "create_external_gold_post_crash_ownership_v4",
    "deserialize_external_gold_private_state_v4",
    "parse_external_gold_public_precommitment_v4",
    "prepare_external_gold_campaign_v4",
    "recover_orphaned_external_gold_starts_v4",
    "restore_external_gold_campaign_home_v4",
    "serialize_external_gold_private_state_v4",
]
