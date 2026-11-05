"""Inert two-process collector for the twelve-case external gold audit.

The module defines preparation, append-only first-attempt collection, signed
private receipts, and cold replay.  Importing it never launches Codex.  Production
execution is an explicit call, runs the two desktop app-server processes
sequentially, and deliberately gives neither STARTED record a predecessor.

The public precommitment contains only commitments and execution policy.  Problem
bodies, case aliases, decisions, source gold, and transcripts remain in private
state or the private signed-pair artifact.  Two processes and two signing keys do
not establish provider, served-model, reviewer-origin, statistical, or epistemic
independence; all corresponding authority flags remain false.
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
from collections.abc import Mapping
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
from .canonical_ed25519_v3 import CanonicalEd25519SignerV3
from .codex_artifact_io import (
    deserialize_codex_run_result,
    serialize_codex_run_result,
)
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
from .external_blind_gold_audit_v3 import (
    EXTERNAL_GOLD_AUDIT_VERSION_V3,
    EXTERNAL_GOLD_PRECOMMITMENT_SCHEMA_V3,
    ColdReplayVerifiedBodyCardSelectionV3,
    ColdReplayVerifiedExternalGoldPairV3,
    CollectedExternalGoldAuditV3,
    ExternalGoldAttemptEvidenceV3,
    ExternalGoldAuditDecisionV3,
    ExternalGoldAuditPlanV3,
    ExternalGoldAuditReceiptV3,
    ExternalGoldReceiptStatusV3,
    ExternalGoldReviewerSlotV3,
    ExternalGoldRunEvidenceV3,
    FrozenExternalGoldVaultV3,
    authenticate_signed_external_gold_audit_pair_bytes_v3,
    build_external_gold_audit_plan_v3,
    external_gold_reviewer_signing_domain_v3,
    parse_external_gold_audit_output_v3,
    parse_external_gold_audit_precommitment_v3,
    serialize_signed_external_gold_audit_pair_v3,
    sign_external_gold_audit_receipt_v3,
    validate_external_gold_receipt_pair_v3,
)
from .mathnet_body_card_review_v3 import FrozenBodyCardProjectionV3
from .models import canonical_json

EXTERNAL_GOLD_REQUESTED_MODEL_V3 = "gpt-5.6-sol"
EXTERNAL_GOLD_REQUESTED_PROVIDER_V3 = "openai"
EXTERNAL_GOLD_REQUESTED_EFFORT_V3 = "high"
EXTERNAL_GOLD_TIMEOUT_SECONDS_V3 = 900
EXTERNAL_GOLD_MAX_WORKERS_V3 = 1
EXTERNAL_GOLD_NO_RETRY_V3 = True
EXTERNAL_GOLD_PRIVATE_ARTIFACT_MEDIA_TYPE_V3 = "application/json"
EXTERNAL_GOLD_RUN_MAX_EVENT_LINE_BYTES_V3 = 2_000_000
EXTERNAL_GOLD_RUN_MAX_TRANSCRIPT_BYTES_V3 = 32_000_000
EXTERNAL_GOLD_RUN_MAX_STDERR_BYTES_V3 = 1_000_000
EXTERNAL_GOLD_CORE_SCHEMA_V3 = (
    "org.math-research-brain/engineering-pilot-external-gold-audit-core"
)
EXTERNAL_GOLD_PRIVATE_STATE_SCHEMA_V3 = (
    "org.math-research-brain/engineering-pilot-external-gold-audit-private-state"
)
EXTERNAL_GOLD_PRIVATE_STATE_MAX_BYTES_V3 = 1_000_000

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_HMAC_RE = re.compile(r"hmac-sha256:[0-9a-f]{64}\Z", re.ASCII)
_GIT_OID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z", re.ASCII)
_TAG_RE = re.compile(r"external-blind-gold-audit-v3-[0-9a-z._-]{1,80}\Z", re.ASCII)


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _domain_digest(value: object, *, domain: str) -> str:
    return _sha256(domain.encode("ascii") + b"\x00" + canonical_json(value).encode("utf-8"))


def _digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical SHA-256 digest")
    return value


def _hmac_value(value: object, label: str) -> str:
    if type(value) is not str or _HMAC_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical HMAC commitment")
    return value


def _key(value: object, label: str) -> bytes:
    if type(value) is not bytes or len(value) != 32:
        raise ValueError(f"{label} must contain exactly 32 immutable bytes")
    return value


def _canonical_bytes(value: object, *, lf: bool = False) -> bytes:
    raw = canonical_json(value).encode("utf-8")
    return raw + (b"\n" if lf else b"")


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


def _decode_canonical_private_json(raw: bytes) -> dict[str, object]:
    if type(raw) is not bytes or not raw.endswith(b"\n") or not (
        1 < len(raw) <= EXTERNAL_GOLD_PRIVATE_STATE_MAX_BYTES_V3
    ):
        raise ValueError("external gold private-state framing is invalid")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, value in pairs:
            if name in result:
                raise ValueError("external gold private state contains a duplicate key")
            result[name] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"external gold private state contains non-finite {value}")

    try:
        value = json.loads(
            raw[:-1].decode("utf-8", errors="strict"),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise ValueError("external gold private state is not strict JSON") from error
    if type(value) is not dict or _canonical_bytes(value, lf=True) != raw:
        raise ValueError("external gold private state is not canonical JSON")
    return value


def _expected_request_policy_material(
    policy: ExternalGoldAuditRunPolicyV3,
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
class ExternalGoldAuditRunPolicyV3:
    """Exact foreground desktop-Codex process policy frozen before collection."""

    executable_provenance: ExecutableProvenance
    requested_model: str = EXTERNAL_GOLD_REQUESTED_MODEL_V3
    requested_provider: str = EXTERNAL_GOLD_REQUESTED_PROVIDER_V3
    requested_effort: str = EXTERNAL_GOLD_REQUESTED_EFFORT_V3
    timeout_seconds: int = EXTERNAL_GOLD_TIMEOUT_SECONDS_V3
    max_workers: int = EXTERNAL_GOLD_MAX_WORKERS_V3
    no_retry: bool = EXTERNAL_GOLD_NO_RETRY_V3
    base_instructions: str = DEFAULT_BASE_INSTRUCTIONS
    developer_instructions: str = DEFAULT_DEVELOPER_INSTRUCTIONS

    def __post_init__(self) -> None:
        if type(self) is not ExternalGoldAuditRunPolicyV3:
            raise ValueError("external gold run-policy subclasses are forbidden")
        if type(self.executable_provenance) is not ExecutableProvenance:
            raise ValueError("external gold policy needs exact executable provenance")
        self.executable_provenance.__post_init__()
        fixed = (
            (self.requested_model, EXTERNAL_GOLD_REQUESTED_MODEL_V3, "model"),
            (self.requested_provider, EXTERNAL_GOLD_REQUESTED_PROVIDER_V3, "provider"),
            (self.requested_effort, EXTERNAL_GOLD_REQUESTED_EFFORT_V3, "effort"),
            (self.base_instructions, DEFAULT_BASE_INSTRUCTIONS, "base instructions"),
            (
                self.developer_instructions,
                DEFAULT_DEVELOPER_INSTRUCTIONS,
                "developer instructions",
            ),
        )
        for actual, expected, label in fixed:
            if type(actual) is not str or actual != expected:
                raise ValueError(f"external gold {label} differs from fixed policy")
        if type(self.timeout_seconds) is not int or self.timeout_seconds != (
            EXTERNAL_GOLD_TIMEOUT_SECONDS_V3
        ):
            raise ValueError("external gold timeout differs from fixed policy")
        if type(self.max_workers) is not int or self.max_workers != 1:
            raise ValueError("external gold processes must be scheduled sequentially")
        if self.no_retry is not True:
            raise ValueError("external gold attempts must remain first-attempt-only")

    @property
    def command_policy_digest(self) -> str:
        return app_server_command_tail_policy_digest(
            self.requested_model,
            self.requested_effort,
        )

    @property
    def process_policy_digest(self) -> str:
        return solver_process_isolation_policy_digest()

    @property
    def request_policy_digest(self) -> str:
        return _domain_digest(
            _expected_request_policy_material(self),
            domain="org.math-research-brain/external-gold-request-policy/v1",
        )

    def public_payload(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "base_instruction_digest": _sha256(self.base_instructions.encode("utf-8")),
            "command_policy_digest": self.command_policy_digest,
            "developer_instruction_digest": _sha256(
                self.developer_instructions.encode("utf-8")
            ),
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
        raise ValueError("external gold implementation Git state cannot be verified")
    return result.stdout.strip()


def _verify_critical_implementation_files(repository: Path) -> None:
    package = Path(__file__).resolve(strict=True).parent
    critical_names = (
        "__init__.py",
        "attempt_ledger.py",
        "canonical_ed25519_v3.py",
        "codex_artifact_io.py",
        "codex_runner.py",
        "exact_answer_v3.py",
        "external_blind_gold_audit_collect_v3.py",
        "external_blind_gold_audit_v3.py",
        "mathnet_bank_v3.py",
        "mathnet_body_card_review_signed_pair_v3.py",
        "mathnet_body_card_review_v3.py",
        "models.py",
    )
    for name in critical_names:
        path = (package / name).resolve(strict=True)
        try:
            relative = path.relative_to(repository).as_posix()
        except ValueError as error:
            raise ValueError(
                "loaded external gold implementation is outside the attested worktree"
            ) from error
        tracked = _git(repository, "ls-files", "--error-unmatch", "--", relative)
        if tracked.replace("\\", "/") != relative:
            raise ValueError("external gold implementation dependency is not tracked")
        working_blob = _git(
            repository,
            "hash-object",
            f"--path={relative}",
            "--",
            relative,
        )
        committed_blob = _git(repository, "rev-parse", f"HEAD:{relative}")
        if not hmac.compare_digest(working_blob, committed_blob):
            raise ValueError("external gold loaded dependency differs from tagged commit")


@dataclass(frozen=True, slots=True)
class ExternalGoldCodeIdentityV3:
    repository_root: str
    commit_oid: str
    annotated_tag: str
    tag_object_oid: str

    def __post_init__(self) -> None:
        if type(self) is not ExternalGoldCodeIdentityV3:
            raise ValueError("external gold code-identity subclasses are forbidden")
        root = Path(self.repository_root)
        if type(self.repository_root) is not str or not root.is_absolute():
            raise ValueError("external gold repository root must be absolute")
        if type(self.commit_oid) is not str or _GIT_OID_RE.fullmatch(self.commit_oid) is None:
            raise ValueError("external gold commit oid is invalid")
        if type(self.tag_object_oid) is not str or _GIT_OID_RE.fullmatch(
            self.tag_object_oid
        ) is None:
            raise ValueError("external gold tag-object oid is invalid")
        if type(self.annotated_tag) is not str or _TAG_RE.fullmatch(self.annotated_tag) is None:
            raise ValueError("external gold annotated tag name is invalid")

    def public_payload(self) -> dict[str, str]:
        self.__post_init__()
        return {
            "annotated_tag": self.annotated_tag,
            "commit_oid": self.commit_oid,
            "tag_object_oid": self.tag_object_oid,
        }


def capture_external_gold_code_identity_v3(
    repository_root: str | Path,
    *,
    annotated_tag: str,
) -> ExternalGoldCodeIdentityV3:
    """Capture one clean HEAD, exact annotated tag object, and peeled commit."""

    raw = Path(repository_root)
    if not raw.is_absolute() or type(annotated_tag) is not str or _TAG_RE.fullmatch(
        annotated_tag
    ) is None:
        raise ValueError("external gold code preflight inputs are invalid")
    root = raw.resolve(strict=True)
    if Path(_git(root, "rev-parse", "--show-toplevel")).resolve(strict=True) != root:
        raise ValueError("external gold repository path is not the worktree root")
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("external gold implementation worktree is not clean")
    head = _git(root, "rev-parse", "--verify", "HEAD")
    tag_ref = f"refs/tags/{annotated_tag}"
    tag_oid = _git(root, "rev-parse", "--verify", f"{tag_ref}^{{tag}}")
    if _git(root, "cat-file", "-t", tag_oid) != "tag":
        raise ValueError("external gold implementation tag is not annotated")
    peeled = _git(root, "rev-parse", "--verify", f"{tag_ref}^{{commit}}")
    identity = ExternalGoldCodeIdentityV3(
        repository_root=str(root),
        commit_oid=head,
        annotated_tag=annotated_tag,
        tag_object_oid=tag_oid,
    )
    if peeled != identity.commit_oid:
        raise ValueError("external gold annotated tag does not name exact HEAD")
    _verify_critical_implementation_files(root)
    return identity


@dataclass(frozen=True, slots=True)
class ExternalGoldAuthSnapshotV3:
    canonical_path: str
    device: int
    inode: int
    size_bytes: int
    content_commitment: str

    def __post_init__(self) -> None:
        if type(self) is not ExternalGoldAuthSnapshotV3:
            raise ValueError("external gold auth-snapshot subclasses are forbidden")
        if type(self.canonical_path) is not str or not Path(self.canonical_path).is_absolute():
            raise ValueError("external gold auth path must be absolute")
        for value in (self.device, self.inode, self.size_bytes):
            if type(value) is not int or value < 0:
                raise ValueError("external gold auth file identity is invalid")
        if not 0 < self.size_bytes <= MAX_AUTH_FILE_BYTES:
            raise ValueError("external gold auth file size is outside runner limits")
        _hmac_value(self.content_commitment, "external gold auth commitment")


def _capture_auth(
    auth_file: str | Path,
    *,
    ledger_key: bytes,
) -> tuple[ExternalGoldAuthSnapshotV3, bytes]:
    raw = Path(auth_file)
    if not raw.is_absolute() or raw.name != "auth.json":
        raise ValueError("external gold auth file must be an absolute auth.json")
    try:
        if raw.is_symlink():
            raise ValueError("external gold auth file may not be a symlink")
        path = raw.resolve(strict=True)
        before = path.lstat()
        content = path.read_bytes()
        after = path.lstat()
    except ValueError:
        raise
    except OSError as error:
        raise ValueError("external gold auth file cannot be inspected") from error
    if (
        path.is_symlink()
        or not stat.S_ISREG(before.st_mode)
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or len(content) != before.st_size
        or not 0 < len(content) <= MAX_AUTH_FILE_BYTES
        or any((ancestor / ".git").exists() for ancestor in (path.parent, *path.parents))
    ):
        raise ValueError("external gold auth file fails the regular-file boundary")
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, value in pairs:
            if name in result:
                raise ValueError("external gold auth JSON contains a duplicate key")
            result[name] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"external gold auth JSON contains non-finite {value}")

    try:
        decoded = json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("external gold auth file is not JSON") from error
    if not isinstance(decoded, Mapping):
        raise ValueError("external gold auth file root must be an object")
    material = (
        str(path).encode("utf-8")
        + b"\x00"
        + str(int(before.st_dev)).encode("ascii")
        + b"\x00"
        + str(int(before.st_ino)).encode("ascii")
        + b"\x00"
        + str(int(before.st_size)).encode("ascii")
        + b"\x00"
        + content
    )
    return (
        ExternalGoldAuthSnapshotV3(
            canonical_path=str(path),
            device=int(before.st_dev),
            inode=int(before.st_ino),
            size_bytes=int(before.st_size),
            content_commitment=secret_commitment(
                _key(ledger_key, "external gold ledger key"),
                domain="external-gold-audit-v3/auth-snapshot",
                material=material,
            ),
        ),
        content,
    )


def capture_external_gold_auth_snapshot_v3(
    auth_file: str | Path,
    *,
    ledger_key: bytes,
) -> ExternalGoldAuthSnapshotV3:
    """Capture the exact regular auth file without exposing its bytes publicly."""

    snapshot, _content = _capture_auth(auth_file, ledger_key=ledger_key)
    return snapshot


@dataclass(frozen=True, slots=True)
class ExternalGoldPrivateMaterialV3:
    ledger_key: bytes = field(repr=False)
    alias_key: bytes = field(repr=False)
    reviewer_a_seed: bytes = field(repr=False)
    reviewer_b_seed: bytes = field(repr=False)

    def __post_init__(self) -> None:
        values = (
            _key(self.ledger_key, "external gold ledger key"),
            _key(self.alias_key, "external gold alias key"),
            _key(self.reviewer_a_seed, "external gold reviewer A seed"),
            _key(self.reviewer_b_seed, "external gold reviewer B seed"),
        )
        if len(set(values)) != len(values):
            raise ValueError("external gold private keys must be independently generated")

    @classmethod
    def generate(cls) -> ExternalGoldPrivateMaterialV3:
        return cls(*(os.urandom(32) for _ in range(4)))

    def signer(self, slot: ExternalGoldReviewerSlotV3) -> CanonicalEd25519SignerV3:
        if slot is ExternalGoldReviewerSlotV3.REVIEWER_A:
            seed = self.reviewer_a_seed
        elif slot is ExternalGoldReviewerSlotV3.REVIEWER_B:
            seed = self.reviewer_b_seed
        else:
            raise ValueError("external gold signer slot is invalid")
        return CanonicalEd25519SignerV3(
            seed,
            domain=external_gold_reviewer_signing_domain_v3(slot),
        )


@dataclass(frozen=True, slots=True)
class PreparedExternalGoldSlotV3:
    reviewer_slot: ExternalGoldReviewerSlotV3
    ordinal: int
    slot_id: str
    dispatch: DispatchMaterial = field(repr=False)
    commitment: SlotCommitment

    def __post_init__(self) -> None:
        expected = 0 if self.reviewer_slot is ExternalGoldReviewerSlotV3.REVIEWER_A else 1
        if type(self.reviewer_slot) is not ExternalGoldReviewerSlotV3 or self.ordinal != expected:
            raise ValueError("prepared external gold slot has the wrong reviewer ordinal")
        if type(self.dispatch) is not DispatchMaterial or type(
            self.commitment
        ) is not SlotCommitment:
            raise ValueError("prepared external gold slot lacks exact ledger material")
        if self.slot_id != self.commitment.slot_id:
            raise ValueError("prepared external gold slot id differs from commitment")


def _plan_commitment(
    key: bytes,
    plan: ExternalGoldAuditPlanV3,
    *,
    field_name: str,
    value: object,
) -> str:
    return secret_commitment(
        key,
        domain=f"external-gold-audit-v3/{plan.reviewer_slot.value}/{field_name}",
        material=_canonical_bytes(value),
    )


def _reviewer_public_payload(
    plan: ExternalGoldAuditPlanV3,
    signer: CanonicalEd25519SignerV3,
    *,
    commitment_key: bytes,
) -> dict[str, object]:
    plan.__post_init__()
    verifier = signer.verifier()
    return {
        "authority_commitment": verifier.authority_commitment,
        "key_fingerprint": verifier.key_fingerprint,
        "order_commitment": _plan_commitment(
            commitment_key,
            plan,
            field_name="order",
            value=plan.order_digest,
        ),
        "output_schema_commitment": _plan_commitment(
            commitment_key,
            plan,
            field_name="output-schema",
            value=plan.output_schema_digest,
        ),
        "plan_commitment": _plan_commitment(
            commitment_key,
            plan,
            field_name="plan",
            value={
                "order_digest": plan.order_digest,
                "output_schema_digest": plan.output_schema_digest,
                "presentation_digest": plan.presentation_digest,
                "prompt_digest": plan.prompt_digest,
                "projection_content_digest": plan.projection_content_digest,
                "projection_raw_file_digest": plan.projection_raw_file_digest,
                "selection_report_digest": plan.selection_report_digest,
            },
        ),
        "presentation_commitment": _plan_commitment(
            commitment_key,
            plan,
            field_name="presentation",
            value=plan.presentation_digest,
        ),
        "prompt_commitment": _plan_commitment(
            commitment_key,
            plan,
            field_name="prompt",
            value=plan.prompt_digest,
        ),
        "public_key_hex": verifier.public_key_bytes().hex(),
        "signing_domain": external_gold_reviewer_signing_domain_v3(
            plan.reviewer_slot
        ),
    }


def _build_core_precommitment(
    selection: ColdReplayVerifiedBodyCardSelectionV3,
    projection: FrozenBodyCardProjectionV3,
    vault: FrozenExternalGoldVaultV3,
    plans: tuple[ExternalGoldAuditPlanV3, ExternalGoldAuditPlanV3],
    policy: ExternalGoldAuditRunPolicyV3,
    private_material: ExternalGoldPrivateMaterialV3,
    auth_snapshot: ExternalGoldAuthSnapshotV3,
    code_identity: ExternalGoldCodeIdentityV3,
) -> dict[str, object]:
    signer_a = private_material.signer(ExternalGoldReviewerSlotV3.REVIEWER_A)
    signer_b = private_material.signer(ExternalGoldReviewerSlotV3.REVIEWER_B)
    if signer_a.verifier().key_fingerprint == signer_b.verifier().key_fingerprint:
        raise ValueError("external gold reviewer signing keys are not distinct")
    projection_commitment = secret_commitment(
        private_material.ledger_key,
        domain="external-gold-audit-v3/projection",
        material=_canonical_bytes(
            {
                "content_digest": projection.content_digest,
                "raw_file_digest": projection.raw_file_digest,
            }
        ),
    )
    selection_commitment = secret_commitment(
        private_material.ledger_key,
        domain="external-gold-audit-v3/selection",
        material=_canonical_bytes(
            {
                "report_digest": selection.report_digest,
                "selected_count": len(selection.selected_review_item_ids),
            }
        ),
    )
    return {
        "auth_continuity_commitment": auth_snapshot.content_commitment,
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
        "gold_present_in_reviewer_payload": False,
        "gold_reveal_stage": "assembler_after_both_terminal_receipts",
        "process_schedule": {
            "max_workers": 1,
            "order": ["reviewer_a", "reviewer_b"],
            "predecessor_dependent_started_chain": False,
            "sequential": True,
        },
        "projection_commitment": projection_commitment,
        "provider_independence_verified": False,
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
        "reviewer_statistical_independence_verified": False,
        "run_policy": policy.public_payload(),
        "schema": EXTERNAL_GOLD_CORE_SCHEMA_V3,
        "schema_version": EXTERNAL_GOLD_AUDIT_VERSION_V3,
        "selection_commitment": selection_commitment,
        "served_model_identity_verified": False,
        "source_metadata_present_in_reviewer_payload": False,
        "vault_commitment": vault.public_commitment(private_material.ledger_key),
    }


def _batch_digest(
    plan: ExternalGoldAuditPlanV3,
    *,
    core_digest: str,
    vault_digest: str,
) -> str:
    return _domain_digest(
        {
            "core_digest": core_digest,
            "order_digest": plan.order_digest,
            "output_schema_digest": plan.output_schema_digest,
            "presentation_digest": plan.presentation_digest,
            "reviewer_slot": plan.reviewer_slot.value,
            "selection_report_digest": plan.selection_report_digest,
            "vault_digest": vault_digest,
        },
        domain="org.math-research-brain/external-gold-batch/v1",
    )


@dataclass(frozen=True, slots=True)
class PreparedExternalGoldAuditsV3:
    selection: ColdReplayVerifiedBodyCardSelectionV3 = field(repr=False)
    projection: FrozenBodyCardProjectionV3 = field(repr=False)
    vault: FrozenExternalGoldVaultV3 = field(repr=False)
    reviewer_a_plan: ExternalGoldAuditPlanV3 = field(repr=False)
    reviewer_b_plan: ExternalGoldAuditPlanV3 = field(repr=False)
    policy: ExternalGoldAuditRunPolicyV3
    private_material: ExternalGoldPrivateMaterialV3 = field(repr=False)
    auth_snapshot: ExternalGoldAuthSnapshotV3
    code_identity: ExternalGoldCodeIdentityV3
    core_precommitment: dict[str, object]
    core_precommitment_digest: str
    slots: tuple[PreparedExternalGoldSlotV3, PreparedExternalGoldSlotV3]

    def __post_init__(self) -> None:
        if type(self) is not PreparedExternalGoldAuditsV3:
            raise ValueError("prepared external gold subclasses are forbidden")
        exact_types = (
            (self.selection, ColdReplayVerifiedBodyCardSelectionV3),
            (self.projection, FrozenBodyCardProjectionV3),
            (self.vault, FrozenExternalGoldVaultV3),
            (self.reviewer_a_plan, ExternalGoldAuditPlanV3),
            (self.reviewer_b_plan, ExternalGoldAuditPlanV3),
            (self.policy, ExternalGoldAuditRunPolicyV3),
            (self.private_material, ExternalGoldPrivateMaterialV3),
            (self.auth_snapshot, ExternalGoldAuthSnapshotV3),
            (self.code_identity, ExternalGoldCodeIdentityV3),
        )
        if any(type(value) is not expected for value, expected in exact_types):
            raise ValueError("prepared external gold state contains an inexact record")
        self.selection.validate_current()
        self.projection.__post_init__()
        self.vault.validate_current()
        self.policy.__post_init__()
        self.private_material.__post_init__()
        self.auth_snapshot.__post_init__()
        self.code_identity.__post_init__()
        if type(self.core_precommitment) is not dict:
            raise ValueError("prepared external gold core must be an exact object")
        _digest(self.core_precommitment_digest, "prepared external gold core")
        if type(self.slots) is not tuple or len(self.slots) != 2:
            raise ValueError("prepared external gold state must contain exactly two slots")
        if tuple(slot.ordinal for slot in self.slots) != (0, 1) or tuple(
            slot.reviewer_slot for slot in self.slots
        ) != (
            ExternalGoldReviewerSlotV3.REVIEWER_A,
            ExternalGoldReviewerSlotV3.REVIEWER_B,
        ):
            raise ValueError("prepared external gold slots differ from A-then-B scheduling")
        if self.slots[0].slot_id == self.slots[1].slot_id:
            raise ValueError("prepared external gold slots reuse an identifier")
        if self.reviewer_a_plan.review_item_order_ids == (
            self.reviewer_b_plan.review_item_order_ids
        ):
            raise ValueError("external gold reviewers must receive independently ordered bodies")

    @property
    def plans(self) -> tuple[ExternalGoldAuditPlanV3, ExternalGoldAuditPlanV3]:
        return (self.reviewer_a_plan, self.reviewer_b_plan)

    @property
    def commitments(self) -> tuple[SlotCommitment, SlotCommitment]:
        return (self.slots[0].commitment, self.slots[1].commitment)

    def validate_current(self) -> None:
        self.__post_init__()
        rebuilt = _prepare_external_gold_audits(
            self.selection,
            self.projection,
            self.vault,
            self.policy,
            private_material=self.private_material,
            auth_snapshot=self.auth_snapshot,
            code_identity=self.code_identity,
            fixed_tokens=tuple(
                (slot.slot_id, slot.dispatch.dispatch_nonce) for slot in self.slots
            ),
        )
        if (
            rebuilt.reviewer_a_plan != self.reviewer_a_plan
            or rebuilt.reviewer_b_plan != self.reviewer_b_plan
            or rebuilt.core_precommitment != self.core_precommitment
            or rebuilt.core_precommitment_digest != self.core_precommitment_digest
            or rebuilt.slots != self.slots
        ):
            raise ValueError("prepared external gold state differs from reconstruction")


def _prepare_external_gold_audits(
    selection: ColdReplayVerifiedBodyCardSelectionV3,
    projection: FrozenBodyCardProjectionV3,
    vault: FrozenExternalGoldVaultV3,
    policy: ExternalGoldAuditRunPolicyV3,
    *,
    private_material: ExternalGoldPrivateMaterialV3,
    auth_snapshot: ExternalGoldAuthSnapshotV3,
    code_identity: ExternalGoldCodeIdentityV3,
    fixed_tokens: tuple[tuple[str, str], tuple[str, str]] | None,
) -> PreparedExternalGoldAuditsV3:
    selection.validate_current()
    projection.__post_init__()
    vault.validate_current()
    if (
        vault.projection_raw_file_digest != projection.raw_file_digest
        or vault.projection_content_digest != projection.content_digest
    ):
        raise ValueError("external gold vault differs from the frozen projection")
    plans = (
        build_external_gold_audit_plan_v3(
            selection,
            projection,
            ExternalGoldReviewerSlotV3.REVIEWER_A,
            alias_key=private_material.alias_key,
        ),
        build_external_gold_audit_plan_v3(
            selection,
            projection,
            ExternalGoldReviewerSlotV3.REVIEWER_B,
            alias_key=private_material.alias_key,
        ),
    )
    if plans[0].review_item_order_ids == plans[1].review_item_order_ids:
        raise ValueError("external gold role-separated orders unexpectedly coincide")
    core = _build_core_precommitment(
        selection,
        projection,
        vault,
        plans,
        policy,
        private_material,
        auth_snapshot,
        code_identity,
    )
    core_digest = _domain_digest(
        core,
        domain="org.math-research-brain/external-gold-audit-core/v1",
    )
    if fixed_tokens is None:
        tokens = tuple((new_slot_id(), _sha256(os.urandom(32))) for _ in range(2))
    else:
        tokens = fixed_tokens
    slots: list[PreparedExternalGoldSlotV3] = []
    for ordinal, (plan, token) in enumerate(zip(plans, tokens, strict=True)):
        slot_id, dispatch_nonce = token
        dispatch = DispatchMaterial(
            run_spec_digest=core_digest,
            batch_digest=_batch_digest(
                plan,
                core_digest=core_digest,
                vault_digest=vault.content_digest,
            ),
            prompt=plan.prompt_text,
            output_schema_json=plan.output_schema_json,
            request_policy_digest=policy.request_policy_digest,
            dispatch_nonce=dispatch_nonce,
        )
        slots.append(
            PreparedExternalGoldSlotV3(
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
    return PreparedExternalGoldAuditsV3(
        selection=selection,
        projection=projection,
        vault=vault,
        reviewer_a_plan=plans[0],
        reviewer_b_plan=plans[1],
        policy=policy,
        private_material=private_material,
        auth_snapshot=auth_snapshot,
        code_identity=code_identity,
        core_precommitment=core,
        core_precommitment_digest=core_digest,
        slots=(slots[0], slots[1]),
    )


def prepare_external_gold_audits_v3(
    selection: ColdReplayVerifiedBodyCardSelectionV3,
    projection: FrozenBodyCardProjectionV3,
    vault: FrozenExternalGoldVaultV3,
    policy: ExternalGoldAuditRunPolicyV3,
    *,
    private_material: ExternalGoldPrivateMaterialV3,
    auth_snapshot: ExternalGoldAuthSnapshotV3,
    code_identity: ExternalGoldCodeIdentityV3,
) -> PreparedExternalGoldAuditsV3:
    prepared = _prepare_external_gold_audits(
        selection,
        projection,
        vault,
        policy,
        private_material=private_material,
        auth_snapshot=auth_snapshot,
        code_identity=code_identity,
        fixed_tokens=None,
    )
    prepared.validate_current()
    return prepared


def _private_state_body(prepared: PreparedExternalGoldAuditsV3) -> dict[str, object]:
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
        "inputs": {
            "projection_content_digest": prepared.projection.content_digest,
            "projection_raw_file_digest": prepared.projection.raw_file_digest,
            "selection_bindings_digest": prepared.selection.bindings_digest,
            "selection_report_digest": prepared.selection.report_digest,
            "selection_review_precommitment_digest": (
                prepared.selection.review_precommitment_digest
            ),
            "selection_signed_pair_digest": prepared.selection.signed_pair_digest,
            "vault_content_digest": prepared.vault.content_digest,
            "vault_raw_file_digest": prepared.vault.raw_file_digest,
            "vault_source_manifest_digest": prepared.vault.source_manifest_digest,
            "vault_universe_content_digest": prepared.vault.universe_content_digest,
        },
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
        },
        "schema": EXTERNAL_GOLD_PRIVATE_STATE_SCHEMA_V3,
        "schema_version": EXTERNAL_GOLD_AUDIT_VERSION_V3,
        "slots": [
            {
                "dispatch_nonce": slot.dispatch.dispatch_nonce,
                "ordinal": slot.ordinal,
                "reviewer_slot": slot.reviewer_slot.value,
                "slot_id": slot.slot_id,
            }
            for slot in prepared.slots
        ],
    }


def serialize_external_gold_private_state_v3(
    prepared: PreparedExternalGoldAuditsV3,
) -> bytes:
    if type(prepared) is not PreparedExternalGoldAuditsV3:
        raise ValueError("external gold private serialization requires exact prepared state")
    prepared.validate_current()
    raw = _canonical_bytes(_private_state_body(prepared), lf=True)
    if len(raw) > EXTERNAL_GOLD_PRIVATE_STATE_MAX_BYTES_V3:
        raise ValueError("external gold private state exceeds its byte budget")
    return raw


def _exact_mapping(
    value: object,
    keys: set[str],
    label: str,
) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError(f"{label} fields differ from the private-state schema")
    return value


def _hex_key(value: object, label: str) -> bytes:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{label} is not a canonical private key")
    return bytes.fromhex(value)


def deserialize_external_gold_private_state_v3(
    raw: bytes,
    selection: ColdReplayVerifiedBodyCardSelectionV3,
    projection: FrozenBodyCardProjectionV3,
    vault: FrozenExternalGoldVaultV3,
) -> PreparedExternalGoldAuditsV3:
    root = _exact_mapping(
        _decode_canonical_private_json(raw),
        {
            "auth_snapshot",
            "code_identity",
            "core_precommitment_digest",
            "inputs",
            "policy",
            "private_material",
            "schema",
            "schema_version",
            "slots",
        },
        "external gold private state",
    )
    if (
        root["schema"] != EXTERNAL_GOLD_PRIVATE_STATE_SCHEMA_V3
        or root["schema_version"] != EXTERNAL_GOLD_AUDIT_VERSION_V3
        or type(root["schema_version"]) is not int
    ):
        raise ValueError("external gold private-state schema differs")
    inputs = _exact_mapping(
        root["inputs"],
        {
            "projection_content_digest",
            "projection_raw_file_digest",
            "selection_bindings_digest",
            "selection_report_digest",
            "selection_review_precommitment_digest",
            "selection_signed_pair_digest",
            "vault_content_digest",
            "vault_raw_file_digest",
            "vault_source_manifest_digest",
            "vault_universe_content_digest",
        },
        "external gold private-state inputs",
    )
    expected_inputs = {
        "projection_content_digest": projection.content_digest,
        "projection_raw_file_digest": projection.raw_file_digest,
        "selection_bindings_digest": selection.bindings_digest,
        "selection_report_digest": selection.report_digest,
        "selection_review_precommitment_digest": selection.review_precommitment_digest,
        "selection_signed_pair_digest": selection.signed_pair_digest,
        "vault_content_digest": vault.content_digest,
        "vault_raw_file_digest": vault.raw_file_digest,
        "vault_source_manifest_digest": vault.source_manifest_digest,
        "vault_universe_content_digest": vault.universe_content_digest,
    }
    if inputs != expected_inputs:
        raise ValueError("external gold private state is bound to different frozen inputs")
    private = _exact_mapping(
        root["private_material"],
        {
            "alias_key_hex",
            "ledger_key_hex",
            "reviewer_a_seed_hex",
            "reviewer_b_seed_hex",
        },
        "external gold private material",
    )
    material = ExternalGoldPrivateMaterialV3(
        ledger_key=_hex_key(private["ledger_key_hex"], "external gold ledger key"),
        alias_key=_hex_key(private["alias_key_hex"], "external gold alias key"),
        reviewer_a_seed=_hex_key(
            private["reviewer_a_seed_hex"],
            "external gold reviewer A seed",
        ),
        reviewer_b_seed=_hex_key(
            private["reviewer_b_seed_hex"],
            "external gold reviewer B seed",
        ),
    )
    auth = _exact_mapping(
        root["auth_snapshot"],
        {"canonical_path", "content_commitment", "device", "inode", "size_bytes"},
        "external gold private auth snapshot",
    )
    code = _exact_mapping(
        root["code_identity"],
        {"annotated_tag", "commit_oid", "repository_root", "tag_object_oid"},
        "external gold private code identity",
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
        "external gold private run policy",
    )
    executable = _exact_mapping(
        policy_value["executable_provenance"],
        {"canonical_path", "sha256_digest", "size_bytes"},
        "external gold private executable provenance",
    )
    try:
        policy = ExternalGoldAuditRunPolicyV3(
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
        auth_snapshot = ExternalGoldAuthSnapshotV3(**auth)
        code_identity = ExternalGoldCodeIdentityV3(**code)
    except (TypeError, ValueError) as error:
        raise ValueError("external gold private state contains invalid typed records") from error
    raw_slots = root["slots"]
    if type(raw_slots) is not list or len(raw_slots) != 2:
        raise ValueError("external gold private state must retain exactly two slots")
    tokens: list[tuple[str, str]] = []
    for ordinal, raw_slot in enumerate(raw_slots):
        slot = _exact_mapping(
            raw_slot,
            {"dispatch_nonce", "ordinal", "reviewer_slot", "slot_id"},
            "external gold private slot",
        )
        expected_role = (
            ExternalGoldReviewerSlotV3.REVIEWER_A
            if ordinal == 0
            else ExternalGoldReviewerSlotV3.REVIEWER_B
        )
        if (
            slot["ordinal"] != ordinal
            or type(slot["ordinal"]) is not int
            or slot["reviewer_slot"] != expected_role.value
            or type(slot["slot_id"]) is not str
            or type(slot["dispatch_nonce"]) is not str
        ):
            raise ValueError("external gold private slot order or role differs")
        tokens.append((slot["slot_id"], slot["dispatch_nonce"]))
    rebuilt = _prepare_external_gold_audits(
        selection,
        projection,
        vault,
        policy,
        private_material=material,
        auth_snapshot=auth_snapshot,
        code_identity=code_identity,
        fixed_tokens=(tokens[0], tokens[1]),
    )
    if root["core_precommitment_digest"] != rebuilt.core_precommitment_digest or (
        _private_state_body(rebuilt) != root
    ):
        raise ValueError("external gold private state changed during reconstruction")
    rebuilt.validate_current()
    return rebuilt


def _private_state_path(path: str | Path, *, must_exist: bool) -> Path:
    raw = Path(path)
    if not raw.is_absolute():
        raise ValueError("external gold private-state path must be absolute")
    parent = raw.parent.resolve(strict=True)
    if any((ancestor / ".git").exists() for ancestor in (parent, *parent.parents)):
        raise ValueError("external gold private state must remain outside Git")
    normalized = parent / raw.name
    if must_exist:
        try:
            status = normalized.lstat()
            resolved = normalized.resolve(strict=True)
        except OSError as error:
            raise ValueError("external gold private-state file is missing") from error
        if (
            not normalized.is_symlink()
            and stat.S_ISREG(status.st_mode)
            and resolved == normalized
            and status.st_nlink > 1
        ):
            staging_pattern = re.compile(
                rf"\.{re.escape(normalized.name)}\.[0-9a-f]{{32}}\.tmp\Z"
            )
            linked_staging: list[Path] = []
            for candidate in parent.iterdir():
                if staging_pattern.fullmatch(candidate.name) is None:
                    continue
                candidate_status = candidate.lstat()
                if (
                    not candidate.is_symlink()
                    and stat.S_ISREG(candidate_status.st_mode)
                    and (candidate_status.st_dev, candidate_status.st_ino)
                    == (status.st_dev, status.st_ino)
                ):
                    linked_staging.append(candidate)
            if len(linked_staging) != status.st_nlink - 1:
                raise ValueError("external gold private state has unknown hard links")
            for candidate in linked_staging:
                candidate.unlink()
            status = normalized.lstat()
        if normalized.is_symlink() or not stat.S_ISREG(status.st_mode) or resolved != normalized:
            raise ValueError("external gold private state must be a regular non-symlink file")
        if status.st_nlink != 1:
            raise ValueError("external gold private state has an unsafe link count")
        return resolved
    if normalized.exists() or normalized.is_symlink():
        raise FileExistsError("external gold private-state file already exists")
    return normalized


def save_external_gold_private_state_v3(
    path: str | Path,
    prepared: PreparedExternalGoldAuditsV3,
) -> Path:
    target = _private_state_path(path, must_exist=False)
    raw = serialize_external_gold_private_state_v3(prepared)
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(16)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    for optional in ("O_BINARY", "O_NOINHERIT", "O_NOFOLLOW"):
        flags |= int(getattr(os, optional, 0))
    descriptor = os.open(temporary, flags, 0o600)
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:  # pragma: no cover - OS contract defense
                raise OSError("external gold private-state write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.link(temporary, target)
        _fsync_directory(target.parent)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()
        _fsync_directory(target.parent)
    return target


def load_external_gold_private_state_v3(
    path: str | Path,
    selection: ColdReplayVerifiedBodyCardSelectionV3,
    projection: FrozenBodyCardProjectionV3,
    vault: FrozenExternalGoldVaultV3,
) -> PreparedExternalGoldAuditsV3:
    target = _private_state_path(path, must_exist=True)
    try:
        raw = target.read_bytes()
    except OSError as error:
        raise ValueError("external gold private state cannot be read") from error
    return deserialize_external_gold_private_state_v3(raw, selection, projection, vault)


def create_external_gold_audit_ledger_v3(
    prepared: PreparedExternalGoldAuditsV3,
    ledger_root: str | Path,
) -> AttemptLedger:
    if type(prepared) is not PreparedExternalGoldAuditsV3:
        raise ValueError("external gold ledger creation requires exact prepared state")
    prepared.validate_current()
    return AttemptLedger.create(
        ledger_root,
        key=prepared.private_material.ledger_key,
        slots=prepared.commitments,
    )


def open_external_gold_audit_ledger_v3(
    prepared: PreparedExternalGoldAuditsV3,
    ledger_root: str | Path,
) -> AttemptLedger:
    if type(prepared) is not PreparedExternalGoldAuditsV3:
        raise ValueError("external gold ledger opening requires exact prepared state")
    prepared.validate_current()
    ledger = AttemptLedger.open(
        ledger_root,
        key=prepared.private_material.ledger_key,
    )
    if ledger.slots != prepared.commitments:
        raise LedgerIntegrityError("external gold reopened ledger differs from private state")
    return ledger


def _final_precommitment(
    prepared: PreparedExternalGoldAuditsV3,
    *,
    ledger_digest: str,
    private_state_digest: str,
) -> dict[str, object]:
    return {
        "attempt_ledger": {
            "digest": _digest(ledger_digest, "external gold attempt ledger"),
            "ordered_slots": [
                slot.commitment.to_dict(ordinal=slot.ordinal) for slot in prepared.slots
            ],
            "private_state_digest": _digest(
                private_state_digest,
                "external gold durable private state",
            ),
            "slot_count": 2,
        },
        "core": prepared.core_precommitment,
        "core_digest": prepared.core_precommitment_digest,
        "schema": EXTERNAL_GOLD_PRECOMMITMENT_SCHEMA_V3,
        "schema_version": EXTERNAL_GOLD_AUDIT_VERSION_V3,
    }


@dataclass(frozen=True, slots=True)
class BoundExternalGoldAuditsV3:
    prepared: PreparedExternalGoldAuditsV3 = field(repr=False)
    attempt_ledger_digest: str
    private_state_path: str = field(repr=False)
    private_state_digest: str
    public_precommitment: dict[str, object]
    public_precommitment_digest: str

    def __post_init__(self) -> None:
        if type(self) is not BoundExternalGoldAuditsV3 or type(
            self.prepared
        ) is not PreparedExternalGoldAuditsV3:
            raise ValueError("bound external gold state is inexact")
        _digest(self.attempt_ledger_digest, "bound external gold ledger")
        _digest(self.private_state_digest, "bound external gold private state")
        _digest(self.public_precommitment_digest, "bound external gold precommitment")
        if type(self.private_state_path) is not str or not Path(
            self.private_state_path
        ).is_absolute():
            raise ValueError("bound external gold private-state path must be absolute")
        if type(self.public_precommitment) is not dict:
            raise ValueError("bound external gold precommitment must be an exact object")

    @property
    def public_precommitment_bytes(self) -> bytes:
        return _canonical_bytes(self.public_precommitment, lf=True)

    def validate_current(self) -> None:
        self.__post_init__()
        self.prepared.validate_current()
        private_path = _private_state_path(self.private_state_path, must_exist=True)
        try:
            private_raw = private_path.read_bytes()
        except OSError as error:
            raise ValueError("bound external gold private state cannot be read") from error
        if _sha256(private_raw) != self.private_state_digest or (
            deserialize_external_gold_private_state_v3(
                private_raw,
                self.prepared.selection,
                self.prepared.projection,
                self.prepared.vault,
            )
            != self.prepared
        ):
            raise ValueError("bound external gold private state differs from preparation")
        expected = _final_precommitment(
            self.prepared,
            ledger_digest=self.attempt_ledger_digest,
            private_state_digest=self.private_state_digest,
        )
        raw = _canonical_bytes(expected, lf=True)
        if self.public_precommitment != expected or _sha256(raw) != (
            self.public_precommitment_digest
        ):
            raise ValueError("bound external gold precommitment is stale")
        if parse_external_gold_audit_precommitment_v3(
            raw,
            expected_digest=self.public_precommitment_digest,
        ) != expected:
            raise ValueError("bound external gold precommitment failed strict replay")

    def validate_ledger(self, ledger: AttemptLedger) -> None:
        if type(ledger) is not AttemptLedger:
            raise ValueError("bound external gold state requires an exact attempt ledger")
        if ledger.slots != self.prepared.commitments or ledger.ledger_digest != (
            self.attempt_ledger_digest
        ):
            raise LedgerIntegrityError("external gold ledger differs from precommitment")
        for slot in self.prepared.slots:
            ledger.verify_dispatch(slot.slot_id, slot.dispatch)


def bind_external_gold_audits_v3(
    prepared: PreparedExternalGoldAuditsV3,
    ledger: AttemptLedger,
    *,
    private_state_path: str | Path,
) -> BoundExternalGoldAuditsV3:
    if type(prepared) is not PreparedExternalGoldAuditsV3 or type(ledger) is not (
        AttemptLedger
    ):
        raise ValueError("external gold binding requires exact prepared state and ledger")
    prepared.validate_current()
    if ledger.slots != prepared.commitments:
        raise ValueError("external gold ledger slot universe differs from preparation")
    if any(ledger.state(slot.slot_id) is not SlotState.PENDING for slot in prepared.slots):
        raise ValueError("external gold precommitment must precede every STARTED record")
    private_path = _private_state_path(private_state_path, must_exist=True)
    try:
        private_raw = private_path.read_bytes()
    except OSError as error:
        raise ValueError("external gold durable private state cannot be read") from error
    if deserialize_external_gold_private_state_v3(
        private_raw,
        prepared.selection,
        prepared.projection,
        prepared.vault,
    ) != prepared:
        raise ValueError("external gold durable private state differs from preparation")
    private_state_digest = _sha256(private_raw)
    record = _final_precommitment(
        prepared,
        ledger_digest=ledger.ledger_digest,
        private_state_digest=private_state_digest,
    )
    raw = _canonical_bytes(record, lf=True)
    bound = BoundExternalGoldAuditsV3(
        prepared=prepared,
        attempt_ledger_digest=ledger.ledger_digest,
        private_state_path=str(private_path),
        private_state_digest=private_state_digest,
        public_precommitment=record,
        public_precommitment_digest=_sha256(raw),
    )
    bound.validate_current()
    bound.validate_ledger(ledger)
    return bound


def restore_bound_external_gold_audits_v3(
    prepared: PreparedExternalGoldAuditsV3,
    ledger: AttemptLedger,
    *,
    private_state_path: str | Path,
    public_precommitment_bytes: bytes,
    expected_public_precommitment_digest: str,
) -> BoundExternalGoldAuditsV3:
    """Restore a crash-resumable bound state without minting slots or keys."""

    if type(prepared) is not PreparedExternalGoldAuditsV3 or type(ledger) is not (
        AttemptLedger
    ):
        raise ValueError("external gold restore requires exact private state and ledger")
    prepared.validate_current()
    parsed = parse_external_gold_audit_precommitment_v3(
        public_precommitment_bytes,
        expected_digest=expected_public_precommitment_digest,
    )
    ledger_record = parsed["attempt_ledger"]
    if type(ledger_record) is not dict:
        raise ValueError("external gold restored precommitment ledger is invalid")
    private_path = _private_state_path(private_state_path, must_exist=True)
    try:
        private_raw = private_path.read_bytes()
    except OSError as error:
        raise ValueError("external gold restored private state cannot be read") from error
    if deserialize_external_gold_private_state_v3(
        private_raw,
        prepared.selection,
        prepared.projection,
        prepared.vault,
    ) != prepared:
        raise ValueError("external gold restored private state differs from preparation")
    private_state_digest = _sha256(private_raw)
    expected_slots = [
        slot.commitment.to_dict(ordinal=slot.ordinal) for slot in prepared.slots
    ]
    if (
        parsed["core"] != prepared.core_precommitment
        or parsed["core_digest"] != prepared.core_precommitment_digest
        or ledger_record.get("digest") != ledger.ledger_digest
        or ledger_record.get("ordered_slots") != expected_slots
        or ledger_record.get("private_state_digest") != private_state_digest
        or ledger_record.get("slot_count") != 2
    ):
        raise ValueError("external gold restored state differs from anchored precommitment")
    bound = BoundExternalGoldAuditsV3(
        prepared=prepared,
        attempt_ledger_digest=ledger.ledger_digest,
        private_state_path=str(private_path),
        private_state_digest=private_state_digest,
        public_precommitment=parsed,
        public_precommitment_digest=expected_public_precommitment_digest,
    )
    bound.validate_current()
    bound.validate_ledger(ledger)
    return bound


def _validate_live_inputs(
    prepared: PreparedExternalGoldAuditsV3,
    *,
    repository_root: str | Path,
    auth_file: str | Path,
) -> bytes:
    observed_code = capture_external_gold_code_identity_v3(
        repository_root,
        annotated_tag=prepared.code_identity.annotated_tag,
    )
    if observed_code != prepared.code_identity:
        raise ValueError("external gold code identity changed after precommitment")
    observed_executable = capture_codex_executable_provenance(
        prepared.policy.executable_provenance.canonical_path
    )
    if observed_executable != prepared.policy.executable_provenance:
        raise ValueError("external gold executable changed after precommitment")
    auth_snapshot, auth_bytes = _capture_auth(
        auth_file,
        ledger_key=prepared.private_material.ledger_key,
    )
    if auth_snapshot != prepared.auth_snapshot:
        raise ValueError("external gold auth identity or bytes changed after precommitment")
    return auth_bytes


def _run_native_slot(
    prepared: PreparedExternalGoldAuditsV3,
    ledger: AttemptLedger,
    slot: PreparedExternalGoldSlotV3,
    *,
    runtime_parent: Path,
    repository_root: Path,
    auth_bytes: bytes,
) -> TerminalTransition:
    """Run one fresh synchronous desktop process under an independent STARTED record."""

    def runner(runtime: Path, dispatch: DispatchMaterial) -> ArtifactPayload:
        config = CodexRunConfig(
            cwd=runtime,
            model=prepared.policy.requested_model,
            effort=prepared.policy.requested_effort,
            model_provider=prepared.policy.requested_provider,
            timeout_seconds=prepared.policy.timeout_seconds,
            executable=prepared.policy.executable_provenance.canonical_path,
            expected_executable_provenance=prepared.policy.executable_provenance,
            auth_bytes=auth_bytes,
            base_instructions=prepared.policy.base_instructions,
            developer_instructions=prepared.policy.developer_instructions,
            max_event_line_bytes=EXTERNAL_GOLD_RUN_MAX_EVENT_LINE_BYTES_V3,
            max_transcript_bytes=EXTERNAL_GOLD_RUN_MAX_TRANSCRIPT_BYTES_V3,
            max_stderr_bytes=EXTERNAL_GOLD_RUN_MAX_STDERR_BYTES_V3,
        )
        # A new runner object owns one fresh foreground process and one thread.
        result = CodexAppServerRunner(config).run(
            prompt=dispatch.prompt,
            output_schema=json.loads(dispatch.output_schema_json),
        )
        return ArtifactPayload(
            serialize_codex_run_result(result),
            EXTERNAL_GOLD_PRIVATE_ARTIFACT_MEDIA_TYPE_V3,
        )

    # Deliberately not execute_first_attempt_after: neither role depends on the
    # predecessor's terminal state, even though scheduling remains sequential.
    return ledger.execute_first_attempt(
        slot.slot_id,
        runtime_parent=runtime_parent,
        repository_root=repository_root,
        dispatch=slot.dispatch,
        runner=runner,
    )


def _policy_matches(
    artifact: CodexRunResult,
    plan: ExternalGoldAuditPlanV3,
    policy: ExternalGoldAuditRunPolicyV3,
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
        and hmac.compare_digest(
            artifact.executable_sha256_digest,
            expected.sha256_digest,
        )
        and hmac.compare_digest(
            artifact.command_tail_policy_digest,
            policy.command_policy_digest,
        )
        and hmac.compare_digest(
            artifact.solver_process_isolation_policy_digest,
            policy.process_policy_digest,
        )
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
    policy: ExternalGoldAuditRunPolicyV3,
) -> ExternalGoldRunEvidenceV3:
    if not artifact.standard_popen_used or artifact.codex_home_path_digest is None:
        raise ValueError("external gold run lacks standard fresh-process evidence")
    accepted = (
        artifact.audit.accepted_model,
        artifact.audit.accepted_provider,
        artifact.audit.accepted_effort,
    )
    if any(type(value) is not str or not value for value in accepted):
        raise ValueError("external gold run lacks accepted request selectors")
    return ExternalGoldRunEvidenceV3(
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
class _ReturnedEvaluation:
    status: ExternalGoldReceiptStatusV3
    decisions: tuple[ExternalGoldAuditDecisionV3, ...]
    failure_code: str | None
    run: ExternalGoldRunEvidenceV3 | None


def _evaluate_returned_artifact(
    artifact_bytes: bytes,
    plan: ExternalGoldAuditPlanV3,
    policy: ExternalGoldAuditRunPolicyV3,
) -> _ReturnedEvaluation:
    try:
        artifact = deserialize_codex_run_result(artifact_bytes)
        if not hmac.compare_digest(serialize_codex_run_result(artifact), artifact_bytes):
            raise ValueError("external gold Codex artifact is noncanonical")
    except Exception:
        return _ReturnedEvaluation(
            ExternalGoldReceiptStatusV3.FAILED,
            (),
            "artifact_invalid",
            None,
        )
    if not _policy_matches(artifact, plan, policy):
        return _ReturnedEvaluation(
            ExternalGoldReceiptStatusV3.FAILED,
            (),
            "policy_mismatch",
            None,
        )
    run = _run_evidence(artifact, policy)
    try:
        if type(artifact.output_text) is not str:
            raise ValueError("external gold artifact lacks one output text")
        decisions = parse_external_gold_audit_output_v3(artifact.output_text, plan)
    except Exception:
        return _ReturnedEvaluation(
            ExternalGoldReceiptStatusV3.FAILED,
            (),
            "decision_invalid",
            run,
        )
    return _ReturnedEvaluation(
        ExternalGoldReceiptStatusV3.RETURNED,
        decisions,
        None,
        run,
    )


def _attempt_evidence(
    ledger: AttemptLedger,
    slot: PreparedExternalGoldSlotV3,
    terminal: TerminalTransition,
    artifact_bytes: bytes,
) -> ExternalGoldAttemptEvidenceV3:
    started = ledger.started_attempt(slot.slot_id)
    selected = ledger.selected_outcome(slot.slot_id)
    if (
        started is None
        or selected is None
        or ledger.terminal_transition(slot.slot_id) != terminal
        or started.ordinal != slot.ordinal
        or selected.ordinal != slot.ordinal
        or terminal.ordinal != slot.ordinal
        or selected.started_record_digest != started.record_digest
        or selected.object_digest != terminal.object_digest
        or selected.kind is not terminal.kind
        or started.prerequisite_terminal_digest is not None
    ):
        raise LedgerIntegrityError("external gold attempt history is not exact")
    payload = ledger.load_selected_artifact(slot.slot_id)
    if payload.media_type != EXTERNAL_GOLD_PRIVATE_ARTIFACT_MEDIA_TYPE_V3 or not (
        hmac.compare_digest(payload.content, artifact_bytes)
    ):
        raise LedgerIntegrityError("external gold selected artifact changed")
    return ExternalGoldAttemptEvidenceV3(
        ledger_digest=ledger.ledger_digest,
        slot_id=slot.slot_id,
        ordinal=slot.ordinal,
        started_record_digest=started.record_digest,
        selection_record_digest=selected.selection_record_digest,
        terminal_record_digest=terminal.record_digest,
        object_digest=terminal.object_digest,
        artifact_payload_digest=_sha256(artifact_bytes),
        terminal_kind=terminal.kind.value,
        prerequisite_terminal_digest=None,
    )


def _parse_failure_artifact(raw: bytes) -> str:
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise LedgerIntegrityError("external gold failure artifact is invalid") from error
    if type(value) is not dict or set(value) != {"detail_commitment", "failure_code"}:
        raise LedgerIntegrityError("external gold failure artifact schema differs")
    if _canonical_bytes(value) != raw:
        raise LedgerIntegrityError("external gold failure artifact is noncanonical")
    failure_code = value["failure_code"]
    if failure_code not in {"runner_exception", "orphaned_started"}:
        raise LedgerIntegrityError("external gold failure code was not collector-issued")
    detail = value["detail_commitment"]
    if detail is not None:
        _hmac_value(detail, "external gold failure detail")
    return failure_code


def _receipt_for_terminal(
    bound: BoundExternalGoldAuditsV3,
    ledger: AttemptLedger,
    slot: PreparedExternalGoldSlotV3,
) -> CollectedExternalGoldAuditV3:
    terminal = ledger.terminal_transition(slot.slot_id)
    if terminal is None:
        raise LedgerIntegrityError("external gold slot is not terminal")
    payload = ledger.load_selected_artifact(slot.slot_id)
    attempt = _attempt_evidence(ledger, slot, terminal, payload.content)
    plan = bound.prepared.plans[slot.ordinal]
    if terminal.kind is OutcomeKind.FAILED:
        status = ExternalGoldReceiptStatusV3.FAILED
        decisions: tuple[ExternalGoldAuditDecisionV3, ...] = ()
        failure_code = _parse_failure_artifact(payload.content)
        run = None
    elif terminal.kind is OutcomeKind.RETURNED:
        evaluated = _evaluate_returned_artifact(
            payload.content,
            plan,
            bound.prepared.policy,
        )
        status = evaluated.status
        decisions = evaluated.decisions
        failure_code = evaluated.failure_code
        run = evaluated.run
    else:  # pragma: no cover - closed enum defense
        raise LedgerIntegrityError("external gold terminal kind is unsupported")
    receipt = ExternalGoldAuditReceiptV3(
        reviewer_slot=slot.reviewer_slot,
        status=status,
        decisions=decisions,
        failure_code=failure_code,
        selection_report_digest=plan.selection_report_digest,
        projection_raw_file_digest=plan.projection_raw_file_digest,
        projection_content_digest=plan.projection_content_digest,
        order_digest=plan.order_digest,
        presentation_digest=plan.presentation_digest,
        prompt_digest=plan.prompt_digest,
        output_schema_digest=plan.output_schema_digest,
        audit_precommitment_digest=bound.public_precommitment_digest,
        attempt=attempt,
        run=run,
        confirmatory_claim_authorized=False,
        served_model_identity_verified=False,
        provider_independence_verified=False,
        epistemic_independence_verified=False,
        reviewer_origin_verified=False,
    )
    return CollectedExternalGoldAuditV3(
        manifest=sign_external_gold_audit_receipt_v3(
            receipt,
            bound.prepared.private_material.signer(slot.reviewer_slot),
        ),
        artifact_bytes=payload.content,
    )


def _terminalize_orphaned_starts(ledger: AttemptLedger) -> None:
    recovery = ledger.recover_orphans()
    for slot_id in recovery.burned_without_outcome:
        started = ledger.started_attempt(slot_id)
        if started is None or ledger.terminal_transition(slot_id) is not None:
            raise LedgerIntegrityError("external gold orphan recovery changed concurrently")
        ledger.record_failure(
            started,
            failure_code="orphaned_started",
        )


def collect_external_gold_audits_v3(
    bound: BoundExternalGoldAuditsV3,
    *,
    ledger: AttemptLedger,
    public_precommitment_bytes: bytes,
    expected_public_precommitment_digest: str,
    runtime_parent: str | Path,
    repository_root: str | Path,
    auth_file: str | Path,
) -> tuple[CollectedExternalGoldAuditV3, CollectedExternalGoldAuditV3]:
    """Run or resume exactly A then B, once each, and return signed private receipts."""

    if type(bound) is not BoundExternalGoldAuditsV3 or type(ledger) is not AttemptLedger:
        raise ValueError("external gold collection requires exact bound state and ledger")
    if type(public_precommitment_bytes) is not bytes or type(
        expected_public_precommitment_digest
    ) is not str:
        raise ValueError("external gold collection anchor inputs are invalid")
    bound.validate_current()
    bound.validate_ledger(ledger)
    if (
        public_precommitment_bytes != bound.public_precommitment_bytes
        or expected_public_precommitment_digest != bound.public_precommitment_digest
        or parse_external_gold_audit_precommitment_v3(
            public_precommitment_bytes,
            expected_digest=expected_public_precommitment_digest,
        )
        != bound.public_precommitment
    ):
        raise ValueError("external gold execution precommitment differs from anchor")
    runtime_root = Path(runtime_parent)
    repository = Path(repository_root)
    if not runtime_root.is_absolute() or not repository.is_absolute():
        raise ValueError("external gold runtime and repository roots must be absolute")
    runtime_root = runtime_root.resolve(strict=True)
    repository = repository.resolve(strict=True)

    # A crash-burned STARTED slot is made terminal as a sanitized failure.  It is
    # never reset, retried, or replaced.  This happens before considering PENDING.
    _terminalize_orphaned_starts(ledger)
    bound.validate_ledger(ledger)
    _validate_live_inputs(
        bound.prepared,
        repository_root=repository,
        auth_file=auth_file,
    )
    for slot in bound.prepared.slots:
        state = ledger.state(slot.slot_id)
        if state is SlotState.PENDING:
            auth_bytes = _validate_live_inputs(
                bound.prepared,
                repository_root=repository,
                auth_file=auth_file,
            )
            with suppress(AttemptRunnerFailed):
                _run_native_slot(
                    bound.prepared,
                    ledger,
                    slot,
                    runtime_parent=runtime_root,
                    repository_root=repository,
                    auth_bytes=auth_bytes,
                )
            # A runner exception already owns a sanitized FAILED terminal. Reviewer
            # B remains independently schedulable and still consumes its own slot.
        elif state is SlotState.STARTED:
            raise LedgerIntegrityError("external gold orphan was not terminalized")
        elif state not in {SlotState.RETURNED, SlotState.FAILED}:
            raise LedgerIntegrityError("external gold slot has an unsupported state")
        bound.validate_ledger(ledger)
        started = ledger.started_attempt(slot.slot_id)
        if started is None or started.prerequisite_terminal_digest is not None:
            raise LedgerIntegrityError("external gold STARTED record acquired a dependency")

    _validate_live_inputs(
        bound.prepared,
        repository_root=repository,
        auth_file=auth_file,
    )
    collected = tuple(
        _receipt_for_terminal(bound, ledger, slot) for slot in bound.prepared.slots
    )
    validate_external_gold_receipt_pair_v3(
        collected[0].manifest.receipt,
        collected[1].manifest.receipt,
        reviewer_a_key_fingerprint=collected[0].manifest.key_fingerprint,
        reviewer_b_key_fingerprint=collected[1].manifest.key_fingerprint,
    )
    return (collected[0], collected[1])


def collect_external_gold_signed_pair_bytes_v3(
    bound: BoundExternalGoldAuditsV3,
    *,
    ledger: AttemptLedger,
    public_precommitment_bytes: bytes,
    expected_public_precommitment_digest: str,
    runtime_parent: str | Path,
    repository_root: str | Path,
    auth_file: str | Path,
) -> bytes:
    reviewer_a, reviewer_b = collect_external_gold_audits_v3(
        bound,
        ledger=ledger,
        public_precommitment_bytes=public_precommitment_bytes,
        expected_public_precommitment_digest=expected_public_precommitment_digest,
        runtime_parent=runtime_parent,
        repository_root=repository_root,
        auth_file=auth_file,
    )
    return serialize_signed_external_gold_audit_pair_v3(reviewer_a, reviewer_b)


def _replay_receipt(
    collected: CollectedExternalGoldAuditV3,
    plan: ExternalGoldAuditPlanV3,
    policy: ExternalGoldAuditRunPolicyV3,
    slot: PreparedExternalGoldSlotV3,
    *,
    ledger_digest: str,
    precommitment_digest: str,
) -> None:
    receipt = collected.manifest.receipt
    expected_fields = {
        "selection_report_digest": plan.selection_report_digest,
        "projection_raw_file_digest": plan.projection_raw_file_digest,
        "projection_content_digest": plan.projection_content_digest,
        "order_digest": plan.order_digest,
        "presentation_digest": plan.presentation_digest,
        "prompt_digest": plan.prompt_digest,
        "output_schema_digest": plan.output_schema_digest,
        "audit_precommitment_digest": precommitment_digest,
    }
    if any(getattr(receipt, name) != value for name, value in expected_fields.items()):
        raise ValueError("external gold receipt differs from reconstructed plan")
    attempt = receipt.attempt
    if (
        receipt.reviewer_slot is not slot.reviewer_slot
        or attempt.ordinal != slot.ordinal
        or attempt.slot_id != slot.slot_id
        or attempt.ledger_digest != ledger_digest
        or attempt.prerequisite_terminal_digest is not None
        or attempt.artifact_payload_digest != _sha256(collected.artifact_bytes)
    ):
        raise ValueError("external gold receipt attempt differs from precommitment")
    if attempt.terminal_kind == OutcomeKind.FAILED.value:
        failure_code = _parse_failure_artifact(collected.artifact_bytes)
        if (
            receipt.status is not ExternalGoldReceiptStatusV3.FAILED
            or receipt.failure_code != failure_code
            or receipt.decisions
            or receipt.run is not None
        ):
            raise ValueError("external gold failed receipt differs from failure artifact")
        return
    if attempt.terminal_kind != OutcomeKind.RETURNED.value:
        raise ValueError("external gold receipt terminal kind is unsupported")
    evaluated = _evaluate_returned_artifact(collected.artifact_bytes, plan, policy)
    if (
        receipt.status is not evaluated.status
        or receipt.decisions != evaluated.decisions
        or receipt.failure_code != evaluated.failure_code
        or receipt.run != evaluated.run
    ):
        raise ValueError("external gold receipt differs from cold artifact replay")


def cold_replay_external_gold_audits_v3(
    signed_pair_bytes: bytes,
    *,
    bound: BoundExternalGoldAuditsV3,
    public_precommitment_bytes: bytes,
    expected_public_precommitment_digest: str,
) -> ColdReplayVerifiedExternalGoldPairV3:
    """Authenticate both opaque receipts, replay both artifacts, then mint the pair."""

    if type(signed_pair_bytes) is not bytes or type(bound) is not BoundExternalGoldAuditsV3:
        raise ValueError("external gold cold replay requires immutable exact inputs")
    bound.validate_current()
    if (
        public_precommitment_bytes != bound.public_precommitment_bytes
        or expected_public_precommitment_digest != bound.public_precommitment_digest
    ):
        raise ValueError("external gold cold replay precommitment differs from bound state")
    reviewer_a, reviewer_b = authenticate_signed_external_gold_audit_pair_bytes_v3(
        signed_pair_bytes,
        precommitment_bytes=public_precommitment_bytes,
        expected_precommitment_digest=expected_public_precommitment_digest,
    )
    ledger = bound.public_precommitment["attempt_ledger"]
    if type(ledger) is not dict or type(ledger.get("digest")) is not str:
        raise ValueError("external gold precommitment ledger is invalid")
    for collected, plan, slot in zip(
        (reviewer_a, reviewer_b),
        bound.prepared.plans,
        bound.prepared.slots,
        strict=True,
    ):
        _replay_receipt(
            collected,
            plan,
            bound.prepared.policy,
            slot,
            ledger_digest=ledger["digest"],
            precommitment_digest=expected_public_precommitment_digest,
        )
    return ColdReplayVerifiedExternalGoldPairV3._derive_after_artifact_replay(
        reviewer_a=reviewer_a,
        reviewer_b=reviewer_b,
        signed_pair_bytes=signed_pair_bytes,
        precommitment_digest=expected_public_precommitment_digest,
    )


__all__ = [
    "EXTERNAL_GOLD_CORE_SCHEMA_V3",
    "EXTERNAL_GOLD_MAX_WORKERS_V3",
    "EXTERNAL_GOLD_NO_RETRY_V3",
    "EXTERNAL_GOLD_PRIVATE_STATE_MAX_BYTES_V3",
    "EXTERNAL_GOLD_PRIVATE_STATE_SCHEMA_V3",
    "EXTERNAL_GOLD_REQUESTED_EFFORT_V3",
    "EXTERNAL_GOLD_REQUESTED_MODEL_V3",
    "EXTERNAL_GOLD_REQUESTED_PROVIDER_V3",
    "EXTERNAL_GOLD_TIMEOUT_SECONDS_V3",
    "BoundExternalGoldAuditsV3",
    "ExternalGoldAuditRunPolicyV3",
    "ExternalGoldAuthSnapshotV3",
    "ExternalGoldCodeIdentityV3",
    "ExternalGoldPrivateMaterialV3",
    "PreparedExternalGoldAuditsV3",
    "PreparedExternalGoldSlotV3",
    "bind_external_gold_audits_v3",
    "capture_external_gold_auth_snapshot_v3",
    "capture_external_gold_code_identity_v3",
    "cold_replay_external_gold_audits_v3",
    "collect_external_gold_audits_v3",
    "collect_external_gold_signed_pair_bytes_v3",
    "create_external_gold_audit_ledger_v3",
    "deserialize_external_gold_private_state_v3",
    "load_external_gold_private_state_v3",
    "open_external_gold_audit_ledger_v3",
    "prepare_external_gold_audits_v3",
    "restore_bound_external_gold_audits_v3",
    "save_external_gold_private_state_v3",
    "serialize_external_gold_private_state_v3",
]
