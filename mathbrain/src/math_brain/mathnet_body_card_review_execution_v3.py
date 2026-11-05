"""Private two-slot execution kernel for the blinded MathNet body/card review.

The public preregistration is deliberately two-stage.  A ledger-independent core
is frozen first and becomes each slot's ``run_spec_digest``.  The exact HMAC-sealed
A/B slots are then placed in one append-only ledger, and the final public record
binds that ledger.  Reviewer submissions sign both digests, avoiding a circular
``final prereg -> ledger -> final prereg`` dependency.

This is a cooperative local collection boundary.  Kernel locks, O_EXCL records,
and HMACs prevent accidental overlap, retries, or post-hoc request drift.  They do
not defend against an operator who copies/deletes the whole private state or uses
its keys to forge a parallel history; the externally anchored final digest is the
chronology witness for that stronger threat model.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import stat
import subprocess
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import attempt_ledger as _attempt_ledger_module
from . import canonical_ed25519_v3 as _canonical_ed25519_module
from . import codex_artifact_io as _codex_artifact_module
from . import codex_runner as _codex_runner_module
from . import mathnet_body_card_review_v3 as _review_module
from . import models as _models_module
from .attempt_ledger import (
    ArtifactPayload,
    AttemptLedger,
    AttemptRunnerFailed,
    DispatchMaterial,
    LedgerIntegrityError,
    LedgerStateError,
    OutcomeKind,
    SlotCommitment,
    SlotState,
    TerminalTransition,
    new_slot_id,
    secret_commitment,
)
from .canonical_ed25519_v3 import CanonicalEd25519SignerV3, CanonicalEd25519VerifierV3
from .codex_artifact_io import (
    MAX_PRIVATE_ARTIFACT_BYTES,
    deserialize_codex_run_result,
    serialize_codex_run_result,
)
from .codex_runner import (
    CODEX_UUID7_WIRE_EXAMPLE,
    MAX_AUTH_FILE_BYTES,
    CodexAppServerRunner,
    CodexRunConfig,
    ExecutableProvenance,
    build_locked_thread_start_request,
    build_locked_turn_start_request,
    capture_codex_executable_provenance,
    encode_codex_app_server_message,
)
from .mathnet_body_card_review_v3 import (
    BODY_CARD_OUTPUT_SCHEMA_V3,
    BODY_CARD_PRECOMMITMENT_SCHEMA_V3,
    BODY_CARD_PROMPT_TEMPLATE_DIGEST_V3,
    BODY_CARD_REVIEW_VERSION_V3,
    BodyCardReviewerSlotV3,
    BodyCardReviewPlanV3,
    BodyCardReviewRunPolicyV3,
    FrozenBodyCardProjectionV3,
    SignedBodyCardReviewManifestV3,
    _ascii_json,
    _domain_digest,
    _sha256,
    _validate_digest,
    bind_body_card_review_attempt_v3,
    body_card_review_public_precommitment_digest_v3,
    body_card_reviewer_signing_domain_v3,
    build_body_card_review_plan_v3,
    parse_body_card_review_public_precommitment_v3,
    parse_body_card_review_run_v3,
    sign_body_card_review_submission_v3,
    validate_body_card_reviewer_pair_v3,
)
from .models import canonical_json

BODY_CARD_CORE_PRECOMMITMENT_SCHEMA_V3 = (
    "org.math-research-brain/engineering-pilot-body-card-review-core"
)
BODY_CARD_PRIVATE_STATE_SCHEMA_V3 = (
    "org.math-research-brain/engineering-pilot-body-card-review-private-state"
)
BODY_CARD_PUBLICATION_RECEIPT_SCHEMA_V3 = (
    "org.math-research-brain/engineering-pilot-body-card-publication-receipt"
)
BODY_CARD_PRIVATE_STATE_MAX_BYTES_V3 = 1_000_000
BODY_CARD_PUBLICATION_RECEIPT_MAX_BYTES_V3 = 100_000
BODY_CARD_PRIVATE_ARTIFACT_MEDIA_TYPE_V3 = "application/json"
BODY_CARD_EXECUTION_SLOT_COUNT_V3 = 2
BODY_CARD_RUN_MAX_EVENT_LINE_BYTES_V3 = 2_000_000
BODY_CARD_RUN_MAX_TRANSCRIPT_BYTES_V3 = 4_000_000
BODY_CARD_RUN_MAX_STDERR_BYTES_V3 = 1_000_000

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_HMAC_RE = re.compile(r"hmac-sha256:[0-9a-f]{64}\Z")
_GIT_OID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_SLOT_ID_RE = re.compile(r"slot_[0-9a-f]{32}\Z")


def _canonical_bytes(value: object) -> bytes:
    return canonical_json(value).encode("utf-8")


def _require_git_oid(value: object, label: str) -> str:
    if type(value) is not str or _GIT_OID_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a full lowercase Git object id")
    return value


def _require_hmac(value: object, label: str) -> str:
    if type(value) is not str or _HMAC_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical HMAC-SHA256 commitment")
    return value


def _private_key_bytes(value: object, label: str) -> bytes:
    if type(value) is not bytes or len(value) != 32:
        raise ValueError(f"{label} must contain exactly 32 immutable bytes")
    return value


def _is_reparse_point(path: Path) -> bool:
    try:
        status = path.lstat()
    except OSError:
        return False
    attributes = int(getattr(status, "st_file_attributes", 0))
    reparse = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    return path.is_symlink() or bool(reparse and attributes & reparse)


def _inside_git_repository(path: Path) -> bool:
    return any((candidate / ".git").exists() for candidate in (path, *path.parents))


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


def _private_root_path(path: str | Path, *, must_exist: bool) -> Path:
    raw = Path(path)
    if not raw.is_absolute():
        raise ValueError("body/card private ledger root must be absolute")
    for candidate in (raw.parent, *raw.parent.parents):
        if _is_reparse_point(candidate):
            raise ValueError("body/card private ledger ancestors may not be reparse points")
    parent = raw.parent.resolve(strict=True)
    if _inside_git_repository(parent):
        raise ValueError("body/card private ledger must stay outside every Git repository")
    normalized = parent / raw.name
    if must_exist:
        if _is_reparse_point(normalized):
            raise ValueError("body/card private ledger root may not be a reparse point")
        resolved = normalized.resolve(strict=True)
        if resolved != normalized or not resolved.is_dir():
            raise ValueError("body/card private ledger root must be a real directory")
        return resolved
    if normalized.exists() or _is_reparse_point(normalized):
        raise FileExistsError(f"body/card private ledger root already exists: {normalized}")
    return normalized


def _git_command(repository_root: Path, *arguments: str) -> str:
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=30,
        creationflags=creationflags,
    )
    if result.returncode != 0 or result.stderr.strip():
        raise ValueError("body/card implementation Git state cannot be verified")
    return result.stdout.strip()


def capture_body_card_review_implementation_revision_v3(
    repository_root: str | Path,
) -> tuple[str, str]:
    """Return the exact clean repository root and full implementation OID."""

    raw = Path(repository_root)
    if not raw.is_absolute():
        raise ValueError("body/card implementation repository root must be absolute")
    root = raw.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("body/card implementation repository root must be a directory")
    observed_root = Path(_git_command(root, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if observed_root != root:
        raise ValueError("body/card implementation path is not the Git worktree root")
    revision = _require_git_oid(
        _git_command(root, "rev-parse", "--verify", "HEAD"),
        "body/card implementation revision",
    )
    if _git_command(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("body/card implementation worktree is not completely clean")
    critical_paths = (
        Path(__file__).with_name("__init__.py").resolve(strict=True),
        Path(__file__).resolve(strict=True),
        Path(_attempt_ledger_module.__file__).resolve(strict=True),
        Path(_canonical_ed25519_module.__file__).resolve(strict=True),
        Path(_codex_artifact_module.__file__).resolve(strict=True),
        Path(_codex_runner_module.__file__).resolve(strict=True),
        Path(_review_module.__file__).resolve(strict=True),
        Path(_models_module.__file__).resolve(strict=True),
        Path(__file__).with_name("mathnet_body_card_review_signed_pair_v3.py").resolve(strict=True),
        Path(__file__).with_name("mathnet_body_card_review_collect_v3.py").resolve(strict=True),
    )
    for path in critical_paths:
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError(
                "body/card loaded implementation module is outside the attested worktree"
            ) from exc
        tracked = _git_command(root, "ls-files", "--error-unmatch", "--", relative)
        if tracked.replace("\\", "/") != relative:
            raise ValueError("body/card implementation module is not tracked at the attested OID")
        working_blob = _git_command(
            root,
            "hash-object",
            f"--path={relative}",
            "--",
            relative,
        )
        committed_blob = _git_command(root, "rev-parse", f"HEAD:{relative}")
        if not hmac.compare_digest(working_blob, committed_blob):
            raise ValueError("body/card implementation module bytes differ from the attested OID")
    return (str(root), revision)


@dataclass(frozen=True, slots=True)
class BodyCardReviewAuthSnapshotV3:
    canonical_path: str
    device: int
    inode: int
    size_bytes: int
    content_commitment: str

    def __post_init__(self) -> None:
        raw = Path(self.canonical_path)
        if type(self.canonical_path) is not str or not raw.is_absolute():
            raise ValueError("body/card auth snapshot path must be absolute")
        for value, label in (
            (self.device, "device"),
            (self.inode, "inode"),
            (self.size_bytes, "size"),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"body/card auth snapshot {label} is invalid")
        if not 0 < self.size_bytes <= MAX_AUTH_FILE_BYTES:
            raise ValueError("body/card auth snapshot size is outside runner limits")
        _require_hmac(self.content_commitment, "body/card auth content commitment")


def _capture_auth_snapshot(
    auth_file: str | Path,
    *,
    ledger_key: bytes,
) -> BodyCardReviewAuthSnapshotV3:
    snapshot, _content = _capture_auth_snapshot_and_bytes(
        auth_file,
        ledger_key=ledger_key,
    )
    return snapshot


def _capture_auth_snapshot_and_bytes(
    auth_file: str | Path,
    *,
    ledger_key: bytes,
) -> tuple[BodyCardReviewAuthSnapshotV3, bytes]:
    raw = Path(auth_file)
    if not raw.is_absolute():
        raise ValueError("body/card auth file must be absolute")
    try:
        if raw.is_symlink():
            raise ValueError("body/card auth file may not be a symlink")
        path = raw.resolve(strict=True)
        status = path.lstat()
        content = path.read_bytes()
        after = path.lstat()
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError("body/card auth file cannot be inspected") from exc
    if (
        path.name != "auth.json"
        or path.is_symlink()
        or not stat.S_ISREG(status.st_mode)
        or (status.st_dev, status.st_ino, status.st_size)
        != (after.st_dev, after.st_ino, after.st_size)
        or not 0 < len(content) <= MAX_AUTH_FILE_BYTES
        or len(content) != status.st_size
        or _inside_git_repository(path.parent)
    ):
        raise ValueError("body/card auth file fails the fixed regular-file boundary")
    try:
        decoded_auth = _codex_runner_module._decode_json_value(content)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError("body/card auth file is not strict runner-compatible JSON") from exc
    if not isinstance(decoded_auth, Mapping):
        raise ValueError("body/card auth file root must be an object")
    material = (
        str(path).encode("utf-8")
        + b"\0"
        + str(int(status.st_dev)).encode("ascii")
        + b"\0"
        + str(int(status.st_ino)).encode("ascii")
        + b"\0"
        + str(int(status.st_size)).encode("ascii")
        + b"\0"
        + content
    )
    snapshot = BodyCardReviewAuthSnapshotV3(
        canonical_path=str(path),
        device=int(status.st_dev),
        inode=int(status.st_ino),
        size_bytes=int(status.st_size),
        content_commitment=secret_commitment(
            ledger_key,
            domain="body-card-review-v3/auth-snapshot",
            material=material,
        ),
    )
    return (snapshot, content)


@dataclass(frozen=True, slots=True)
class BodyCardReviewPrivateMaterialV3:
    ledger_key: bytes
    reviewer_a_seed: bytes
    reviewer_b_seed: bytes

    def __post_init__(self) -> None:
        values = (
            _private_key_bytes(self.ledger_key, "body/card ledger key"),
            _private_key_bytes(self.reviewer_a_seed, "body/card reviewer A seed"),
            _private_key_bytes(self.reviewer_b_seed, "body/card reviewer B seed"),
        )
        if len(set(values)) != len(values):
            raise ValueError("body/card private keys must be independently generated")

    @classmethod
    def generate(cls) -> BodyCardReviewPrivateMaterialV3:
        return cls(os.urandom(32), os.urandom(32), os.urandom(32))

    def signer(self, slot: BodyCardReviewerSlotV3) -> CanonicalEd25519SignerV3:
        if slot is BodyCardReviewerSlotV3.REVIEWER_A:
            seed = self.reviewer_a_seed
        elif slot is BodyCardReviewerSlotV3.REVIEWER_B:
            seed = self.reviewer_b_seed
        else:
            raise ValueError("body/card signer slot is invalid")
        return CanonicalEd25519SignerV3(
            seed,
            domain=body_card_reviewer_signing_domain_v3(slot),
        )


def _reviewer_public_payload(
    plan: BodyCardReviewPlanV3,
    verifier: CanonicalEd25519VerifierV3,
) -> dict[str, object]:
    expected = CanonicalEd25519VerifierV3(
        verifier.public_key_bytes(),
        domain=body_card_reviewer_signing_domain_v3(plan.reviewer_slot),
    )
    if verifier.authority_commitment != expected.authority_commitment:
        raise ValueError("body/card reviewer verifier uses the wrong signing domain")
    return {
        "authority_commitment": verifier.authority_commitment,
        "card_order_digest": plan.card_order_digest,
        "item_order_digest": plan.item_order_digest,
        "key_fingerprint": verifier.key_fingerprint,
        "output_schema_digest": plan.output_schema_digest,
        "presentation_digest": plan.presentation_digest,
        "prompt_digest": plan.prompt_digest,
        "public_key_hex": verifier.public_key_bytes().hex(),
        "signing_domain": body_card_reviewer_signing_domain_v3(plan.reviewer_slot),
    }


def _selection_payload() -> dict[str, object]:
    from .mathnet_body_card_review_v3 import BODY_CARD_CELL_ORDER_V3

    return {
        "agreement_rule": "exact-english-domain-difficulty-relevant-card",
        "card_reuse_allowed": True,
        "cell_order": [
            {"difficulty": difficulty.value, "domain": domain.value}
            for domain, difficulty in BODY_CARD_CELL_ORDER_V3
        ],
        "event_family_reuse_allowed": False,
        "infeasible_means_empty": True,
        "partial_fill_allowed": False,
        "rule": "lexicographically-smallest-complete-vector",
    }


def _permutation_payload() -> dict[str, object]:
    from .mathnet_body_card_review_v3 import (
        _CARD_ORDER_COMMITMENT_DOMAIN,
        _CARD_ORDER_DOMAINS,
        _ITEM_ORDER_COMMITMENT_DOMAIN,
        _ITEM_ORDER_DOMAINS,
    )

    return {
        "algorithm": "sha256-domain-nul-content-digest-nul-id-sort-raw-key-then-id",
        "card_order_commitment_domain": _CARD_ORDER_COMMITMENT_DOMAIN,
        "card_order_domains": dict(_CARD_ORDER_DOMAINS),
        "item_order_commitment_domain": _ITEM_ORDER_COMMITMENT_DOMAIN,
        "item_order_domains": dict(_ITEM_ORDER_DOMAINS),
    }


def build_body_card_review_core_precommitment_v3(
    projection: FrozenBodyCardProjectionV3,
    reviewer_a_plan: BodyCardReviewPlanV3,
    reviewer_b_plan: BodyCardReviewPlanV3,
    policy: BodyCardReviewRunPolicyV3,
    *,
    reviewer_a_verifier: CanonicalEd25519VerifierV3,
    reviewer_b_verifier: CanonicalEd25519VerifierV3,
    event_family_bindings_digest: str,
    auth_snapshot_commitment: str,
    implementation_revision: str,
) -> dict[str, object]:
    """Build the ledger-independent, text-free core preregistration."""

    if type(projection) is not FrozenBodyCardProjectionV3:
        raise ValueError("body/card core requires an exact frozen projection")
    projection.__post_init__()
    if (
        type(reviewer_a_plan) is not BodyCardReviewPlanV3
        or type(reviewer_b_plan) is not BodyCardReviewPlanV3
        or type(policy) is not BodyCardReviewRunPolicyV3
    ):
        raise ValueError("body/card core requires exact plan and policy records")
    expected_a = build_body_card_review_plan_v3(projection, BodyCardReviewerSlotV3.REVIEWER_A)
    expected_b = build_body_card_review_plan_v3(projection, BodyCardReviewerSlotV3.REVIEWER_B)
    if reviewer_a_plan != expected_a or reviewer_b_plan != expected_b:
        raise ValueError("body/card core reviewer plans differ from deterministic reconstruction")
    if (
        type(reviewer_a_verifier) is not CanonicalEd25519VerifierV3
        or type(reviewer_b_verifier) is not CanonicalEd25519VerifierV3
        or hmac.compare_digest(
            reviewer_a_verifier.key_fingerprint,
            reviewer_b_verifier.key_fingerprint,
        )
    ):
        raise ValueError("body/card core requires two distinct exact reviewer verifiers")
    revision = _require_git_oid(implementation_revision, "body/card implementation revision")
    bindings_digest = _validate_digest(
        event_family_bindings_digest,
        "body/card event-family bindings digest",
    )
    auth_commitment = _require_hmac(
        auth_snapshot_commitment,
        "body/card auth continuity commitment",
    )
    return {
        "automatic_retrieval_evaluation": False,
        "auth_continuity_commitment": auth_commitment,
        "confirmatory_claim_authorized": False,
        "engineering_upper_bound_only": True,
        "event_family_bindings_digest": bindings_digest,
        "gold_present_in_reviewer_payload": False,
        "implementation_revision": {
            "git_object_format": "sha1" if len(revision) == 40 else "sha256",
            "oid": revision,
        },
        "model_input_base_instructions_attested": False,
        "permutation": _permutation_payload(),
        "projection": {
            "card_count": len(projection.cards),
            "content_digest": projection.content_digest,
            "item_count": len(projection.items),
            "raw_file_digest": projection.raw_file_digest,
        },
        "prompt_template_digest": BODY_CARD_PROMPT_TEMPLATE_DIGEST_V3,
        "review_output_schema": BODY_CARD_OUTPUT_SCHEMA_V3,
        "review_output_schema_version": BODY_CARD_REVIEW_VERSION_V3,
        "reviewer_a": _reviewer_public_payload(reviewer_a_plan, reviewer_a_verifier),
        "reviewer_b": _reviewer_public_payload(reviewer_b_plan, reviewer_b_verifier),
        "reviewer_processes": 2,
        "reviewer_statistical_independence_verified": False,
        "run_order": ["reviewer_a", "reviewer_b"],
        "run_policy": policy.public_payload(),
        "schema": BODY_CARD_CORE_PRECOMMITMENT_SCHEMA_V3,
        "schema_version": BODY_CARD_REVIEW_VERSION_V3,
        "selection": _selection_payload(),
        "served_model_identity_verified": False,
        "source_metadata_present_in_reviewer_payload": False,
    }


def body_card_review_core_precommitment_digest_v3(value: dict[str, object]) -> str:
    if type(value) is not dict:
        raise ValueError("body/card core precommitment must be an exact object")
    return _domain_digest(
        value,
        domain="org.math-research-brain/engineering-pilot-body-card-core/v1",
    )


@dataclass(frozen=True, slots=True)
class PreparedBodyCardReviewSlotV3:
    reviewer_slot: BodyCardReviewerSlotV3
    ordinal: int
    slot_id: str
    dispatch: DispatchMaterial
    commitment: SlotCommitment

    def __post_init__(self) -> None:
        expected = 0 if self.reviewer_slot is BodyCardReviewerSlotV3.REVIEWER_A else 1
        if type(self.reviewer_slot) is not BodyCardReviewerSlotV3 or self.ordinal != expected:
            raise ValueError("prepared body/card slot has the wrong reviewer ordinal")
        if type(self.ordinal) is not int:
            raise ValueError("prepared body/card slot ordinal must be exact")
        if type(self.slot_id) is not str or _SLOT_ID_RE.fullmatch(self.slot_id) is None:
            raise ValueError("prepared body/card slot id is invalid")
        if (
            type(self.dispatch) is not DispatchMaterial
            or type(self.commitment) is not SlotCommitment
        ):
            raise ValueError("prepared body/card slot requires exact dispatch and commitment")
        if self.commitment.slot_id != self.slot_id:
            raise ValueError("prepared body/card slot id differs from its commitment")


def _batch_digest(
    *,
    projection: FrozenBodyCardProjectionV3,
    plan: BodyCardReviewPlanV3,
    event_family_bindings_digest: str,
    core_precommitment_digest: str,
) -> str:
    return _domain_digest(
        {
            "core_precommitment_digest": core_precommitment_digest,
            "event_family_bindings_digest": event_family_bindings_digest,
            "output_schema_digest": plan.output_schema_digest,
            "presentation_digest": plan.presentation_digest,
            "projection_content_digest": projection.content_digest,
            "projection_raw_file_digest": projection.raw_file_digest,
            "reviewer_slot": plan.reviewer_slot.value,
        },
        domain="org.math-research-brain/engineering-pilot-body-card-batch/v1",
    )


def _validate_transport_budget(
    plan: BodyCardReviewPlanV3,
    policy: BodyCardReviewRunPolicyV3,
) -> None:
    """Prove the exact large review request fits before any slot can be burned."""

    maximum_cwd = "C:/" + "x" * 32_000
    thread_request = build_locked_thread_start_request(
        model=policy.requested_model,
        model_provider=policy.requested_provider,
        cwd=maximum_cwd,
        base_instructions=policy.base_instructions,
        developer_instructions=policy.developer_instructions,
    )
    turn_request = build_locked_turn_start_request(
        model=policy.requested_model,
        effort=policy.requested_effort,
        prompt=plan.prompt_text,
        output_schema=json.loads(plan.output_schema_json),
        thread_id=CODEX_UUID7_WIRE_EXAMPLE,
    )
    if (
        max(
            len(encode_codex_app_server_message(thread_request)),
            len(encode_codex_app_server_message(turn_request)),
        )
        > BODY_CARD_RUN_MAX_EVENT_LINE_BYTES_V3
    ):
        raise ValueError("body/card exact outbound request exceeds the event-line budget")
    if BODY_CARD_RUN_MAX_TRANSCRIPT_BYTES_V3 < 2 * BODY_CARD_RUN_MAX_EVENT_LINE_BYTES_V3:
        raise ValueError("body/card transcript budget cannot hold two maximum event lines")
    # Conservative closed-form bound for the private codec.  Six transcript bytes
    # cover canonical JSON re-encoding expansion of the parsed message tree; all
    # byte artifacts use base64 (4/3), and the remaining duplicate prompt/output
    # strings and envelope overhead are bounded by four event lines plus 1 MiB.
    encoded_upper_bound = (
        6 * BODY_CARD_RUN_MAX_TRANSCRIPT_BYTES_V3
        + 4 * ((BODY_CARD_RUN_MAX_TRANSCRIPT_BYTES_V3 + 2) // 3)
        + 5 * 4 * ((BODY_CARD_RUN_MAX_EVENT_LINE_BYTES_V3 + 2) // 3)
        + 4 * BODY_CARD_RUN_MAX_EVENT_LINE_BYTES_V3
        + 2 * BODY_CARD_RUN_MAX_STDERR_BYTES_V3
        + 1_000_000
    )
    if encoded_upper_bound > MAX_PRIVATE_ARTIFACT_BYTES:
        raise ValueError("body/card private artifact cannot fit its exact codec budget")


@dataclass(frozen=True, slots=True)
class PreparedBodyCardReviewsV3:
    projection: FrozenBodyCardProjectionV3
    reviewer_a_plan: BodyCardReviewPlanV3
    reviewer_b_plan: BodyCardReviewPlanV3
    policy: BodyCardReviewRunPolicyV3
    private_material: BodyCardReviewPrivateMaterialV3
    auth_snapshot: BodyCardReviewAuthSnapshotV3
    repository_root: str
    implementation_revision: str
    event_family_bindings_digest: str
    core_precommitment: dict[str, object]
    core_precommitment_digest: str
    slots: tuple[PreparedBodyCardReviewSlotV3, PreparedBodyCardReviewSlotV3]

    def __post_init__(self) -> None:
        if type(self.projection) is not FrozenBodyCardProjectionV3:
            raise ValueError("prepared body/card state requires an exact projection")
        if (
            type(self.reviewer_a_plan) is not BodyCardReviewPlanV3
            or type(self.reviewer_b_plan) is not BodyCardReviewPlanV3
            or type(self.policy) is not BodyCardReviewRunPolicyV3
            or type(self.private_material) is not BodyCardReviewPrivateMaterialV3
            or type(self.auth_snapshot) is not BodyCardReviewAuthSnapshotV3
        ):
            raise ValueError("prepared body/card state contains an inexact record")
        if type(self.repository_root) is not str or not Path(self.repository_root).is_absolute():
            raise ValueError("prepared body/card repository root must be absolute")
        _require_git_oid(self.implementation_revision, "prepared implementation revision")
        _validate_digest(self.event_family_bindings_digest, "prepared bindings digest")
        _validate_digest(self.core_precommitment_digest, "prepared core digest")
        if type(self.core_precommitment) is not dict:
            raise ValueError("prepared body/card core must be an exact object")
        if type(self.slots) is not tuple or len(self.slots) != 2:
            raise ValueError("prepared body/card state must contain exactly two slots")
        if tuple(slot.ordinal for slot in self.slots) != (0, 1) or tuple(
            slot.reviewer_slot for slot in self.slots
        ) != (BodyCardReviewerSlotV3.REVIEWER_A, BodyCardReviewerSlotV3.REVIEWER_B):
            raise ValueError("prepared body/card slots differ from fixed A-then-B order")
        if self.slots[0].slot_id == self.slots[1].slot_id:
            raise ValueError("prepared body/card slots reuse an identifier")

    @property
    def commitments(self) -> tuple[SlotCommitment, SlotCommitment]:
        return (self.slots[0].commitment, self.slots[1].commitment)

    @property
    def attempt_universe_commitment(self) -> str:
        return _domain_digest(
            [slot.commitment.to_dict(ordinal=slot.ordinal) for slot in self.slots],
            domain="org.math-research-brain/engineering-pilot-body-card-attempt-universe/v1",
        )

    def validate_current(self) -> None:
        self.__post_init__()
        rebuilt = _prepare_body_card_reviews(
            self.projection,
            self.policy,
            private_material=self.private_material,
            auth_snapshot=self.auth_snapshot,
            repository_root=self.repository_root,
            implementation_revision=self.implementation_revision,
            event_family_bindings_digest=self.event_family_bindings_digest,
            fixed_tokens=tuple((slot.slot_id, slot.dispatch.dispatch_nonce) for slot in self.slots),
        )
        if (
            rebuilt.reviewer_a_plan != self.reviewer_a_plan
            or rebuilt.reviewer_b_plan != self.reviewer_b_plan
            or rebuilt.core_precommitment != self.core_precommitment
            or rebuilt.core_precommitment_digest != self.core_precommitment_digest
            or rebuilt.slots != self.slots
        ):
            raise ValueError("prepared body/card state differs from exact reconstruction")


def _prepare_body_card_reviews(
    projection: FrozenBodyCardProjectionV3,
    policy: BodyCardReviewRunPolicyV3,
    *,
    private_material: BodyCardReviewPrivateMaterialV3,
    auth_snapshot: BodyCardReviewAuthSnapshotV3,
    repository_root: str,
    implementation_revision: str,
    event_family_bindings_digest: str,
    fixed_tokens: tuple[tuple[str, str], tuple[str, str]] | None,
) -> PreparedBodyCardReviewsV3:
    reviewer_a_plan = build_body_card_review_plan_v3(projection, BodyCardReviewerSlotV3.REVIEWER_A)
    reviewer_b_plan = build_body_card_review_plan_v3(projection, BodyCardReviewerSlotV3.REVIEWER_B)
    _validate_transport_budget(reviewer_a_plan, policy)
    _validate_transport_budget(reviewer_b_plan, policy)
    signer_a = private_material.signer(BodyCardReviewerSlotV3.REVIEWER_A)
    signer_b = private_material.signer(BodyCardReviewerSlotV3.REVIEWER_B)
    core = build_body_card_review_core_precommitment_v3(
        projection,
        reviewer_a_plan,
        reviewer_b_plan,
        policy,
        reviewer_a_verifier=signer_a.verifier(),
        reviewer_b_verifier=signer_b.verifier(),
        event_family_bindings_digest=event_family_bindings_digest,
        auth_snapshot_commitment=auth_snapshot.content_commitment,
        implementation_revision=implementation_revision,
    )
    core_digest = body_card_review_core_precommitment_digest_v3(core)
    if fixed_tokens is None:
        tokens = (
            (new_slot_id(), _sha256(os.urandom(32))),
            (new_slot_id(), _sha256(os.urandom(32))),
        )
    else:
        tokens = fixed_tokens
    if (
        type(tokens) is not tuple
        or len(tokens) != 2
        or any(type(token) is not tuple or len(token) != 2 for token in tokens)
        or len({token[0] for token in tokens}) != 2
        or len({token[1] for token in tokens}) != 2
    ):
        raise ValueError("body/card private state must retain two unique slot token pairs")
    slots: list[PreparedBodyCardReviewSlotV3] = []
    for ordinal, (plan, token) in enumerate(
        zip((reviewer_a_plan, reviewer_b_plan), tokens, strict=True)
    ):
        slot_id, nonce = token
        if type(slot_id) is not str or _SLOT_ID_RE.fullmatch(slot_id) is None:
            raise ValueError("body/card private slot id is invalid")
        _validate_digest(nonce, "body/card dispatch nonce")
        dispatch = DispatchMaterial(
            run_spec_digest=core_digest,
            batch_digest=_batch_digest(
                projection=projection,
                plan=plan,
                event_family_bindings_digest=event_family_bindings_digest,
                core_precommitment_digest=core_digest,
            ),
            prompt=plan.prompt_text,
            output_schema_json=plan.output_schema_json,
            request_policy_digest=policy.request_policy_digest,
            dispatch_nonce=nonce,
        )
        slots.append(
            PreparedBodyCardReviewSlotV3(
                reviewer_slot=plan.reviewer_slot,
                ordinal=ordinal,
                slot_id=slot_id,
                dispatch=dispatch,
                commitment=dispatch.seal(key=private_material.ledger_key, slot_id=slot_id),
            )
        )
    return PreparedBodyCardReviewsV3(
        projection=projection,
        reviewer_a_plan=reviewer_a_plan,
        reviewer_b_plan=reviewer_b_plan,
        policy=policy,
        private_material=private_material,
        auth_snapshot=auth_snapshot,
        repository_root=repository_root,
        implementation_revision=implementation_revision,
        event_family_bindings_digest=event_family_bindings_digest,
        core_precommitment=core,
        core_precommitment_digest=core_digest,
        slots=(slots[0], slots[1]),
    )


def prepare_body_card_reviews_v3(
    projection: FrozenBodyCardProjectionV3,
    policy: BodyCardReviewRunPolicyV3,
    *,
    repository_root: str | Path,
    auth_file: str | Path,
    event_family_bindings_digest: str,
    private_material: BodyCardReviewPrivateMaterialV3 | None = None,
) -> PreparedBodyCardReviewsV3:
    """Freeze clean source, auth continuity, keys, plans, and exact A/B dispatches."""

    if type(projection) is not FrozenBodyCardProjectionV3 or type(policy) is not (
        BodyCardReviewRunPolicyV3
    ):
        raise ValueError("body/card preparation requires exact projection and policy")
    material = (
        BodyCardReviewPrivateMaterialV3.generate() if private_material is None else private_material
    )
    if type(material) is not BodyCardReviewPrivateMaterialV3:
        raise ValueError("body/card preparation requires exact private material")
    root, revision = capture_body_card_review_implementation_revision_v3(repository_root)
    auth_snapshot = _capture_auth_snapshot(auth_file, ledger_key=material.ledger_key)
    prepared = _prepare_body_card_reviews(
        projection,
        policy,
        private_material=material,
        auth_snapshot=auth_snapshot,
        repository_root=root,
        implementation_revision=revision,
        event_family_bindings_digest=event_family_bindings_digest,
        fixed_tokens=None,
    )
    prepared.validate_current()
    return prepared


def create_body_card_review_ledger_v3(
    prepared: PreparedBodyCardReviewsV3,
    ledger_root: str | Path,
) -> AttemptLedger:
    """Create one private two-slot ledger outside every Git repository."""

    if type(prepared) is not PreparedBodyCardReviewsV3:
        raise ValueError("body/card ledger creation requires exact prepared state")
    prepared.validate_current()
    target = _private_root_path(ledger_root, must_exist=False)
    return AttemptLedger.create(
        target,
        key=prepared.private_material.ledger_key,
        slots=prepared.commitments,
    )


def open_body_card_review_ledger_v3(
    prepared: PreparedBodyCardReviewsV3,
    ledger_root: str | Path,
) -> AttemptLedger:
    if type(prepared) is not PreparedBodyCardReviewsV3:
        raise ValueError("body/card ledger opening requires exact prepared state")
    prepared.validate_current()
    target = _private_root_path(ledger_root, must_exist=True)
    ledger = AttemptLedger.open(target, key=prepared.private_material.ledger_key)
    if ledger.slots != prepared.commitments:
        raise LedgerIntegrityError("body/card ledger differs from private precommitment")
    return ledger


def _final_precommitment(
    prepared: PreparedBodyCardReviewsV3,
    *,
    ledger_digest: str,
    private_state_digest: str,
) -> dict[str, object]:
    return {
        "attempt_ledger": {
            "attempt_universe_commitment": prepared.attempt_universe_commitment,
            "digest": _validate_digest(ledger_digest, "body/card attempt-ledger digest"),
            "ordered_slots": [
                slot.commitment.to_dict(ordinal=slot.ordinal) for slot in prepared.slots
            ],
            "private_state_commitment": _validate_digest(
                private_state_digest,
                "body/card durable private-state digest",
            ),
            "slot_count": BODY_CARD_EXECUTION_SLOT_COUNT_V3,
        },
        "core": prepared.core_precommitment,
        "core_digest": prepared.core_precommitment_digest,
        "schema": BODY_CARD_PRECOMMITMENT_SCHEMA_V3,
        "schema_version": BODY_CARD_REVIEW_VERSION_V3,
    }


@dataclass(frozen=True, slots=True)
class BoundBodyCardReviewsV3:
    """Prepared state fenced to one pristine ledger and one final preregistration."""

    prepared: PreparedBodyCardReviewsV3
    attempt_ledger_digest: str
    private_state_path: str = field(repr=False)
    private_state_digest: str
    public_precommitment: dict[str, object]
    public_precommitment_digest: str

    def __post_init__(self) -> None:
        if type(self.prepared) is not PreparedBodyCardReviewsV3:
            raise ValueError("bound body/card state requires exact prepared state")
        _validate_digest(self.attempt_ledger_digest, "bound body/card ledger digest")
        _validate_digest(self.private_state_digest, "bound body/card private-state digest")
        _validate_digest(self.public_precommitment_digest, "bound public precommitment digest")
        if (
            type(self.private_state_path) is not str
            or not Path(self.private_state_path).is_absolute()
        ):
            raise ValueError("bound body/card private-state path must be absolute")
        if type(self.public_precommitment) is not dict:
            raise ValueError("bound public precommitment must be an exact object")

    def validate_current(self) -> None:
        self.__post_init__()
        self.prepared.validate_current()
        private_path = _private_state_path(self.private_state_path, must_exist=True)
        try:
            private_raw = private_path.read_bytes()
        except OSError as exc:
            raise ValueError("bound body/card private state cannot be read") from exc
        if not hmac.compare_digest(_sha256(private_raw), self.private_state_digest) or (
            deserialize_body_card_review_private_state_v3(
                private_raw,
                self.prepared.projection,
            )
            != self.prepared
        ):
            raise ValueError("bound body/card private state differs from durable preparation")
        expected = _final_precommitment(
            self.prepared,
            ledger_digest=self.attempt_ledger_digest,
            private_state_digest=self.private_state_digest,
        )
        if self.public_precommitment != expected or not hmac.compare_digest(
            self.public_precommitment_digest,
            body_card_review_public_precommitment_digest_v3(expected),
        ):
            raise ValueError("bound body/card preregistration is stale")

    def validate_ledger(self, ledger: AttemptLedger) -> None:
        if type(ledger) is not AttemptLedger:
            raise ValueError("bound body/card state requires an exact attempt ledger")
        _private_root_path(ledger.root, must_exist=True)
        private_path = _private_state_path(self.private_state_path, must_exist=True)
        lock_path = _body_card_lifecycle_lock_path(ledger)
        if ledger.root in private_path.parents or private_path == lock_path:
            raise ValueError("body/card private state collides with ledger-owned paths")
        if ledger.slots != self.prepared.commitments or not hmac.compare_digest(
            ledger.ledger_digest,
            self.attempt_ledger_digest,
        ):
            raise ValueError("body/card ledger differs from the published binding")
        for slot in self.prepared.slots:
            ledger.verify_dispatch(slot.slot_id, slot.dispatch)


def _bound_public_precommitment_bytes(bound: BoundBodyCardReviewsV3) -> bytes:
    """Internal comparison bytes; publication is gated by ``finalize_*``."""

    if type(bound) is not BoundBodyCardReviewsV3:
        raise ValueError("body/card public bytes require exact bound state")
    bound.validate_current()
    return _ascii_json(bound.public_precommitment).encode("ascii") + b"\n"


def _publication_receipt_body(bound: BoundBodyCardReviewsV3) -> dict[str, object]:
    public_bytes = _bound_public_precommitment_bytes(bound)
    return {
        "attempt_ledger_digest": bound.attempt_ledger_digest,
        "attempt_universe_commitment": bound.prepared.attempt_universe_commitment,
        "implementation_revision": bound.prepared.implementation_revision,
        "private_state_digest": bound.private_state_digest,
        "public_precommitment_digest": bound.public_precommitment_digest,
        "public_precommitment_raw_digest": _sha256(public_bytes),
    }


def _publication_receipt_bytes(bound: BoundBodyCardReviewsV3) -> bytes:
    body = _publication_receipt_body(bound)
    envelope = {
        "body": body,
        "receipt_mac": secret_commitment(
            bound.prepared.private_material.ledger_key,
            domain="body-card-publication-receipt-v3",
            material=_canonical_bytes(body),
        ),
        "schema": BODY_CARD_PUBLICATION_RECEIPT_SCHEMA_V3,
        "schema_version": BODY_CARD_REVIEW_VERSION_V3,
    }
    raw = _canonical_bytes(envelope)
    if len(raw) > BODY_CARD_PUBLICATION_RECEIPT_MAX_BYTES_V3:
        raise ValueError("body/card publication receipt exceeds its byte budget")
    return raw


def _save_publication_receipt(
    bound: BoundBodyCardReviewsV3,
    ledger: AttemptLedger,
    path: str | Path,
) -> Path:
    try:
        target = _private_state_path(path, must_exist=False)
    except FileExistsError:
        target = _private_state_path(path, must_exist=True)
    private_path = _private_state_path(bound.private_state_path, must_exist=True)
    lock_path = _body_card_lifecycle_lock_path(ledger)
    if target in (private_path, lock_path) or ledger.root in target.parents:
        raise ValueError("body/card publication receipt collides with lifecycle state")
    raw = _publication_receipt_bytes(bound)
    if target.exists():
        try:
            existing = target.read_bytes()
        except OSError as exc:
            raise ValueError("body/card publication receipt cannot be reread") from exc
        if not hmac.compare_digest(existing, raw):
            raise FileExistsError(f"body/card publication receipt already differs: {target}")
        return target
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(16)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    for optional in ("O_BINARY", "O_NOINHERIT", "O_NOFOLLOW"):
        flags |= int(getattr(os, optional, 0))
    try:
        descriptor = os.open(temporary, flags, 0o600)
        try:
            written = 0
            while written < len(raw):
                count = os.write(descriptor, raw[written:])
                if count <= 0:  # pragma: no cover
                    raise OSError("body/card publication-receipt write made no progress")
                written += count
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.link(temporary, target)
        except FileExistsError:
            try:
                existing = _private_state_path(target, must_exist=True).read_bytes()
            except OSError as exc:
                raise ValueError("body/card publication receipt cannot be reread") from exc
            if not hmac.compare_digest(existing, raw):
                raise FileExistsError(
                    f"body/card publication receipt already differs: {target}"
                ) from None
        _fsync_directory(target.parent)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()
        _fsync_directory(target.parent)
    return target


def _validate_publication_receipt(
    bound: BoundBodyCardReviewsV3,
    ledger: AttemptLedger,
    path: str | Path,
) -> Path:
    target = _private_state_path(path, must_exist=True)
    private_path = _private_state_path(bound.private_state_path, must_exist=True)
    lock_path = _body_card_lifecycle_lock_path(ledger)
    if target in (private_path, lock_path) or ledger.root in target.parents:
        raise ValueError("body/card publication receipt collides with lifecycle state")
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise ValueError("body/card publication receipt cannot be read") from exc
    if not 1 <= len(raw) <= BODY_CARD_PUBLICATION_RECEIPT_MAX_BYTES_V3:
        raise ValueError("body/card publication receipt has an invalid byte length")
    try:
        envelope = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_float=lambda _value: (_ for _ in ()).throw(ValueError("floats forbidden")),
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("constants forbidden")),
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ValueError("body/card publication receipt is invalid JSON") from exc
    if _canonical_bytes(envelope) != raw:
        raise ValueError("body/card publication receipt is not canonical")
    root = _exact_mapping(
        envelope,
        {"body", "receipt_mac", "schema", "schema_version"},
        "body/card publication receipt",
    )
    if (
        root["schema"] != BODY_CARD_PUBLICATION_RECEIPT_SCHEMA_V3
        or type(root["schema"]) is not str
        or root["schema_version"] != BODY_CARD_REVIEW_VERSION_V3
        or type(root["schema_version"]) is not int
        or root["body"] != _publication_receipt_body(bound)
        or root["receipt_mac"]
        != secret_commitment(
            bound.prepared.private_material.ledger_key,
            domain="body-card-publication-receipt-v3",
            material=_canonical_bytes(_publication_receipt_body(bound)),
        )
    ):
        raise ValueError("body/card publication receipt differs from the bound state")
    return target


def finalize_body_card_review_publication_v3(
    bound: BoundBodyCardReviewsV3,
    *,
    ledger: AttemptLedger,
    repository_root: str | Path,
    auth_file: str | Path,
    publication_receipt_path: str | Path,
) -> bytes:
    """Emit public bytes only after durable state and all live inputs revalidate."""

    if type(bound) is not BoundBodyCardReviewsV3 or type(ledger) is not AttemptLedger:
        raise ValueError("body/card publication requires exact bound state and ledger")
    with _body_card_lifecycle_lock(ledger):
        bound.validate_current()
        bound.validate_ledger(ledger)
        _validate_live_inputs(
            bound.prepared,
            repository_root=repository_root,
            auth_file=auth_file,
        )
        if any(
            ledger.state(slot.slot_id) is not SlotState.PENDING for slot in bound.prepared.slots
        ):
            raise ValueError("body/card public bytes must be emitted before either STARTED")
        _save_publication_receipt(bound, ledger, publication_receipt_path)
        _validate_publication_receipt(bound, ledger, publication_receipt_path)
        return _bound_public_precommitment_bytes(bound)


def bind_body_card_review_ledger_v3(
    prepared: PreparedBodyCardReviewsV3,
    ledger: AttemptLedger,
    *,
    private_state_path: str | Path,
) -> BoundBodyCardReviewsV3:
    """Fence one pristine ledger before its public digest is anchored."""

    if type(prepared) is not PreparedBodyCardReviewsV3 or type(ledger) is not AttemptLedger:
        raise ValueError("body/card binding requires exact prepared and ledger records")
    prepared.validate_current()
    state_path = _private_state_path(private_state_path, must_exist=True)
    try:
        state_raw = state_path.read_bytes()
    except OSError as exc:
        raise ValueError("body/card durable private state cannot be read") from exc
    if deserialize_body_card_review_private_state_v3(state_raw, prepared.projection) != prepared:
        raise ValueError("body/card durable private state differs from preparation")
    state_digest = _sha256(state_raw)
    if ledger.root in state_path.parents or state_path == _body_card_lifecycle_lock_path(ledger):
        raise ValueError("body/card private state collides with ledger-owned paths")
    with _body_card_lifecycle_lock(ledger):
        if ledger.slots != prepared.commitments:
            raise ValueError("body/card ledger slot universe differs from preparation")
        if any(ledger.state(slot.slot_id) is not SlotState.PENDING for slot in prepared.slots):
            raise ValueError("body/card ledger must be bound while both slots are pending")
        public = _final_precommitment(
            prepared,
            ledger_digest=ledger.ledger_digest,
            private_state_digest=state_digest,
        )
        bound = BoundBodyCardReviewsV3(
            prepared=prepared,
            attempt_ledger_digest=ledger.ledger_digest,
            private_state_path=str(state_path),
            private_state_digest=state_digest,
            public_precommitment=public,
            public_precommitment_digest=body_card_review_public_precommitment_digest_v3(public),
        )
        bound.validate_current()
        bound.validate_ledger(ledger)
        return bound


def restore_bound_body_card_review_ledger_v3(
    prepared: PreparedBodyCardReviewsV3,
    ledger: AttemptLedger,
    *,
    private_state_path: str | Path,
    public_precommitment_bytes: bytes,
    expected_public_precommitment_digest: str,
) -> BoundBodyCardReviewsV3:
    """Restore an already anchored binding; never mint a replacement identity."""

    if type(prepared) is not PreparedBodyCardReviewsV3 or type(ledger) is not AttemptLedger:
        raise ValueError("body/card restoration requires exact prepared and ledger records")
    public = parse_body_card_review_public_precommitment_v3(
        public_precommitment_bytes,
        expected_digest=expected_public_precommitment_digest,
    )
    state_path = _private_state_path(private_state_path, must_exist=True)
    try:
        state_raw = state_path.read_bytes()
    except OSError as exc:
        raise ValueError("body/card durable private state cannot be read") from exc
    if deserialize_body_card_review_private_state_v3(state_raw, prepared.projection) != prepared:
        raise ValueError("body/card durable private state differs from restoration")
    bound = BoundBodyCardReviewsV3(
        prepared=prepared,
        attempt_ledger_digest=ledger.ledger_digest,
        private_state_path=str(state_path),
        private_state_digest=_sha256(state_raw),
        public_precommitment=public,
        public_precommitment_digest=expected_public_precommitment_digest,
    )
    with _body_card_lifecycle_lock(ledger):
        bound.validate_current()
        bound.validate_ledger(ledger)
    return bound


def _body_card_lifecycle_lock_path(ledger: AttemptLedger) -> Path:
    root = _private_root_path(ledger.root, must_exist=True)
    return root.parent / f".{root.name}.body-card-review-v3.lock"


@contextmanager
def _body_card_lifecycle_lock(ledger: AttemptLedger) -> Iterator[None]:
    """Hold one nonblocking kernel byte lock across the whole A/B lifecycle."""

    if type(ledger) is not AttemptLedger:
        raise ValueError("body/card lifecycle lock requires an exact ledger")
    path = _body_card_lifecycle_lock_path(ledger)
    if path.is_symlink():
        raise ValueError("body/card lifecycle lock may not be a symlink")
    flags = os.O_RDWR | os.O_CREAT
    for optional_flag in ("O_BINARY", "O_NOINHERIT", "O_NOFOLLOW"):
        flags |= int(getattr(os, optional_flag, 0))
    descriptor = os.open(path, flags, 0o600)
    locked = False
    try:
        status = os.fstat(descriptor)
        path_status = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(status.st_mode)
            or not stat.S_ISREG(path_status.st_mode)
            or (status.st_dev, status.st_ino) != (path_status.st_dev, path_status.st_ino)
            or status.st_nlink != 1
        ):
            raise ValueError("body/card lifecycle lock must be one regular file")
        if status.st_size == 0:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.write(descriptor, b"\0") != 1:  # pragma: no cover
                raise OSError("body/card lifecycle lock initialization made no progress")
            os.fsync(descriptor)
        elif status.st_size != 1:
            raise ValueError("body/card lifecycle lock has an invalid size")
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("body/card lifecycle is active in another collector") from exc
        locked = True
        yield
    finally:
        if locked:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _private_state_body(prepared: PreparedBodyCardReviewsV3) -> dict[str, object]:
    return {
        "auth_snapshot": {
            "canonical_path": prepared.auth_snapshot.canonical_path,
            "content_commitment": prepared.auth_snapshot.content_commitment,
            "device": prepared.auth_snapshot.device,
            "inode": prepared.auth_snapshot.inode,
            "size_bytes": prepared.auth_snapshot.size_bytes,
        },
        "event_family_bindings_digest": prepared.event_family_bindings_digest,
        "implementation_revision": prepared.implementation_revision,
        "policy": {
            "base_instructions": prepared.policy.base_instructions,
            "developer_instructions": prepared.policy.developer_instructions,
            "executable_path": prepared.policy.executable_provenance.canonical_path,
            "executable_sha256_digest": (prepared.policy.executable_provenance.sha256_digest),
            "executable_size_bytes": prepared.policy.executable_provenance.size_bytes,
            "max_workers": prepared.policy.max_workers,
            "no_retry": prepared.policy.no_retry,
            "requested_effort": prepared.policy.requested_effort,
            "requested_model": prepared.policy.requested_model,
            "requested_provider": prepared.policy.requested_provider,
            "timeout_seconds": prepared.policy.timeout_seconds,
        },
        "private_material": {
            "ledger_key_hex": prepared.private_material.ledger_key.hex(),
            "reviewer_a_seed_hex": prepared.private_material.reviewer_a_seed.hex(),
            "reviewer_b_seed_hex": prepared.private_material.reviewer_b_seed.hex(),
        },
        "projection_content_digest": prepared.projection.content_digest,
        "projection_raw_file_digest": prepared.projection.raw_file_digest,
        "repository_root": prepared.repository_root,
        "slot_tokens": [
            {
                "dispatch_nonce": slot.dispatch.dispatch_nonce,
                "slot_id": slot.slot_id,
            }
            for slot in prepared.slots
        ],
    }


def serialize_body_card_review_private_state_v3(
    prepared: PreparedBodyCardReviewsV3,
) -> bytes:
    if type(prepared) is not PreparedBodyCardReviewsV3:
        raise ValueError("body/card private serialization requires exact prepared state")
    prepared.validate_current()
    body = _private_state_body(prepared)
    envelope = {
        "body": body,
        "content_digest": _domain_digest(
            body,
            domain="org.math-research-brain/engineering-pilot-body-card-private-state/v1",
        ),
        "schema": BODY_CARD_PRIVATE_STATE_SCHEMA_V3,
        "schema_version": BODY_CARD_REVIEW_VERSION_V3,
    }
    raw = _canonical_bytes(envelope)
    if len(raw) > BODY_CARD_PRIVATE_STATE_MAX_BYTES_V3:
        raise ValueError("body/card private state exceeds its byte budget")
    return raw


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("body/card private state contains a duplicate JSON key")
        result[key] = value
    return result


def _exact_mapping(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError(f"{label} keys differ from the private-state schema")
    return value


def deserialize_body_card_review_private_state_v3(
    raw: bytes,
    projection: FrozenBodyCardProjectionV3,
) -> PreparedBodyCardReviewsV3:
    if (
        type(raw) is not bytes
        or not 1 <= len(raw) <= BODY_CARD_PRIVATE_STATE_MAX_BYTES_V3
        or type(projection) is not FrozenBodyCardProjectionV3
    ):
        raise ValueError("body/card private state requires bounded bytes and exact projection")
    try:
        envelope = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_float=lambda _value: (_ for _ in ()).throw(ValueError("floats forbidden")),
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("constants forbidden")),
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ValueError("body/card private state is invalid JSON") from exc
    if _canonical_bytes(envelope) != raw:
        raise ValueError("body/card private state must use canonical JSON bytes")
    root = _exact_mapping(
        envelope,
        {"body", "content_digest", "schema", "schema_version"},
        "body/card private envelope",
    )
    if (
        root["schema"] != BODY_CARD_PRIVATE_STATE_SCHEMA_V3
        or type(root["schema_version"]) is not int
        or root["schema_version"] != BODY_CARD_REVIEW_VERSION_V3
    ):
        raise ValueError("body/card private state schema is unsupported")
    body = _exact_mapping(
        root["body"],
        {
            "auth_snapshot",
            "event_family_bindings_digest",
            "implementation_revision",
            "policy",
            "private_material",
            "projection_content_digest",
            "projection_raw_file_digest",
            "repository_root",
            "slot_tokens",
        },
        "body/card private body",
    )
    expected_content_digest = _domain_digest(
        body,
        domain="org.math-research-brain/engineering-pilot-body-card-private-state/v1",
    )
    if not hmac.compare_digest(
        _validate_digest(root["content_digest"], "body/card private content digest"),
        expected_content_digest,
    ):
        raise ValueError("body/card private state content digest differs")
    if (
        body["projection_raw_file_digest"] != projection.raw_file_digest
        or body["projection_content_digest"] != projection.content_digest
    ):
        raise ValueError("body/card private state differs from the frozen projection")
    policy_value = _exact_mapping(
        body["policy"],
        {
            "base_instructions",
            "developer_instructions",
            "executable_path",
            "executable_sha256_digest",
            "executable_size_bytes",
            "max_workers",
            "no_retry",
            "requested_effort",
            "requested_model",
            "requested_provider",
            "timeout_seconds",
        },
        "body/card private policy",
    )
    policy = BodyCardReviewRunPolicyV3(
        executable_provenance=ExecutableProvenance(
            canonical_path=policy_value["executable_path"],
            size_bytes=policy_value["executable_size_bytes"],
            sha256_digest=policy_value["executable_sha256_digest"],
        ),
        requested_model=policy_value["requested_model"],
        requested_provider=policy_value["requested_provider"],
        requested_effort=policy_value["requested_effort"],
        timeout_seconds=policy_value["timeout_seconds"],
        max_workers=policy_value["max_workers"],
        no_retry=policy_value["no_retry"],
        base_instructions=policy_value["base_instructions"],
        developer_instructions=policy_value["developer_instructions"],
    )
    private_value = _exact_mapping(
        body["private_material"],
        {"ledger_key_hex", "reviewer_a_seed_hex", "reviewer_b_seed_hex"},
        "body/card private keys",
    )
    try:
        material = BodyCardReviewPrivateMaterialV3(
            bytes.fromhex(private_value["ledger_key_hex"]),
            bytes.fromhex(private_value["reviewer_a_seed_hex"]),
            bytes.fromhex(private_value["reviewer_b_seed_hex"]),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("body/card private state key material is invalid") from exc
    if any(
        type(private_value[name]) is not str or private_value[name] != value.hex()
        for name, value in (
            ("ledger_key_hex", material.ledger_key),
            ("reviewer_a_seed_hex", material.reviewer_a_seed),
            ("reviewer_b_seed_hex", material.reviewer_b_seed),
        )
    ):
        raise ValueError("body/card private state keys are not canonical lowercase hex")
    auth_value = _exact_mapping(
        body["auth_snapshot"],
        {"canonical_path", "content_commitment", "device", "inode", "size_bytes"},
        "body/card auth snapshot",
    )
    snapshot = BodyCardReviewAuthSnapshotV3(
        canonical_path=auth_value["canonical_path"],
        content_commitment=auth_value["content_commitment"],
        device=auth_value["device"],
        inode=auth_value["inode"],
        size_bytes=auth_value["size_bytes"],
    )
    token_values = body["slot_tokens"]
    if type(token_values) is not list or len(token_values) != 2:
        raise ValueError("body/card private state slot universe is incomplete")
    tokens_list: list[tuple[str, str]] = []
    for value in token_values:
        token = _exact_mapping(
            value,
            {"dispatch_nonce", "slot_id"},
            "body/card private slot token",
        )
        tokens_list.append((token["slot_id"], token["dispatch_nonce"]))
    prepared = _prepare_body_card_reviews(
        projection,
        policy,
        private_material=material,
        auth_snapshot=snapshot,
        repository_root=body["repository_root"],
        implementation_revision=body["implementation_revision"],
        event_family_bindings_digest=body["event_family_bindings_digest"],
        fixed_tokens=(tokens_list[0], tokens_list[1]),
    )
    if _private_state_body(prepared) != body:
        raise ValueError("body/card private state changed during invariant reconstruction")
    return prepared


def _private_state_path(path: str | Path, *, must_exist: bool) -> Path:
    raw = Path(path)
    if not raw.is_absolute():
        raise ValueError("body/card private-state path must be absolute")
    for candidate in (raw.parent, *raw.parent.parents):
        if _is_reparse_point(candidate):
            raise ValueError("body/card private-state ancestors may not be reparse points")
    parent = raw.parent.resolve(strict=True)
    if _inside_git_repository(parent):
        raise ValueError("body/card private state must stay outside every Git repository")
    normalized = parent / raw.name
    if must_exist:
        try:
            status = normalized.lstat()
            resolved = normalized.resolve(strict=True)
        except OSError as exc:
            raise ValueError("body/card private state must be an existing file") from exc
        if (
            normalized.is_symlink()
            or not stat.S_ISREG(status.st_mode)
            or resolved != normalized
            or _inside_git_repository(resolved.parent)
        ):
            raise ValueError("body/card private state must be a regular non-symlink file")
        return resolved
    if normalized.exists() or normalized.is_symlink():
        raise FileExistsError(f"body/card private-state path exists: {normalized}")
    return normalized


def save_body_card_review_private_state_v3(
    path: str | Path,
    prepared: PreparedBodyCardReviewsV3,
) -> Path:
    target = _private_state_path(path, must_exist=False)
    raw = serialize_body_card_review_private_state_v3(prepared)
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(16)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    for optional in ("O_BINARY", "O_NOINHERIT", "O_NOFOLLOW"):
        flags |= int(getattr(os, optional, 0))
    try:
        descriptor = os.open(temporary, flags, 0o600)
        try:
            written = 0
            while written < len(raw):
                count = os.write(descriptor, raw[written:])
                if count <= 0:  # pragma: no cover
                    raise OSError("body/card private-state write made no progress")
                written += count
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.link(temporary, target)
        _fsync_directory(target.parent)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()
        _fsync_directory(target.parent)
    return target


def load_body_card_review_private_state_v3(
    path: str | Path,
    projection: FrozenBodyCardProjectionV3,
) -> PreparedBodyCardReviewsV3:
    target = _private_state_path(path, must_exist=True)
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise ValueError("body/card private state could not be read") from exc
    return deserialize_body_card_review_private_state_v3(raw, projection)


def _validate_current_implementation(
    prepared: PreparedBodyCardReviewsV3,
    *,
    repository_root: str | Path,
) -> None:
    observed_root, observed_revision = capture_body_card_review_implementation_revision_v3(
        repository_root
    )
    if observed_root != prepared.repository_root or not hmac.compare_digest(
        observed_revision,
        prepared.implementation_revision,
    ):
        raise ValueError("body/card implementation differs from preregistered clean source")


def _validate_live_inputs(
    prepared: PreparedBodyCardReviewsV3,
    *,
    repository_root: str | Path,
    auth_file: str | Path,
) -> bytes:
    _validate_current_implementation(prepared, repository_root=repository_root)
    executable = capture_codex_executable_provenance(
        prepared.policy.executable_provenance.canonical_path
    )
    if executable != prepared.policy.executable_provenance:
        raise ValueError("body/card executable differs from preregistered SHA and size")
    observed_auth, auth_bytes = _capture_auth_snapshot_and_bytes(
        auth_file,
        ledger_key=prepared.private_material.ledger_key,
    )
    if observed_auth != prepared.auth_snapshot:
        raise ValueError("body/card auth identity or content changed after preregistration")
    return auth_bytes


def _run_native_slot(
    *,
    prepared: PreparedBodyCardReviewsV3,
    ledger: AttemptLedger,
    slot: PreparedBodyCardReviewSlotV3,
    prerequisite_terminal: TerminalTransition | None,
    runtime_parent: Path,
    repository_root: Path,
    auth_bytes: bytes,
) -> TerminalTransition:
    """Own the only production runner callback and persist its exact codec bytes."""

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
            max_event_line_bytes=BODY_CARD_RUN_MAX_EVENT_LINE_BYTES_V3,
            max_transcript_bytes=BODY_CARD_RUN_MAX_TRANSCRIPT_BYTES_V3,
            max_stderr_bytes=BODY_CARD_RUN_MAX_STDERR_BYTES_V3,
        )
        artifact = CodexAppServerRunner(config).run(
            prompt=dispatch.prompt,
            output_schema=json.loads(dispatch.output_schema_json),
        )
        return ArtifactPayload(
            serialize_codex_run_result(artifact),
            BODY_CARD_PRIVATE_ARTIFACT_MEDIA_TYPE_V3,
        )

    return ledger.execute_first_attempt_after(
        slot.slot_id,
        runtime_parent=runtime_parent,
        repository_root=repository_root,
        dispatch=slot.dispatch,
        prerequisite_terminal=prerequisite_terminal,
        runner=runner,
    )


def _validate_slot_history(
    prepared: PreparedBodyCardReviewsV3,
    ledger: AttemptLedger,
    *,
    ordinal: int,
) -> TerminalTransition:
    slot = prepared.slots[ordinal]
    if ledger.state(slot.slot_id) is not SlotState.RETURNED:
        raise LedgerStateError("body/card reviewer slot did not return and cannot be retried")
    terminal = ledger.terminal_transition(slot.slot_id)
    started = ledger.started_attempt(slot.slot_id)
    if (
        terminal is None
        or terminal.kind is not OutcomeKind.RETURNED
        or terminal.ordinal != ordinal
        or started is None
        or started.ordinal != ordinal
    ):
        raise LedgerIntegrityError("body/card reviewer history lacks exact returned records")
    if ordinal == 0:
        if started.prerequisite_terminal_digest is not None:
            raise LedgerIntegrityError("reviewer A STARTED record has a prerequisite")
    else:
        predecessor = ledger.terminal_transition(prepared.slots[ordinal - 1].slot_id)
        if (
            predecessor is None
            or predecessor.kind is not OutcomeKind.RETURNED
            or started.prerequisite_terminal_digest != predecessor.record_digest
        ):
            raise LedgerIntegrityError("reviewer B lacks the exact reviewer A dependency")
    return terminal


def execute_body_card_reviews_v3(
    bound: BoundBodyCardReviewsV3,
    *,
    ledger: AttemptLedger,
    public_precommitment_bytes: bytes,
    expected_public_precommitment_digest: str,
    publication_receipt_path: str | Path,
    runtime_parent: str | Path,
    repository_root: str | Path,
    auth_file: str | Path,
) -> tuple[SignedBodyCardReviewManifestV3, SignedBodyCardReviewManifestV3]:
    """Run or resume A then B exactly once and sign only durable ledger artifacts."""

    if type(bound) is not BoundBodyCardReviewsV3 or type(ledger) is not AttemptLedger:
        raise ValueError("body/card execution requires exact bound state and ledger")
    anchored = parse_body_card_review_public_precommitment_v3(
        public_precommitment_bytes,
        expected_digest=expected_public_precommitment_digest,
    )
    if (
        anchored != bound.public_precommitment
        or not hmac.compare_digest(
            expected_public_precommitment_digest,
            bound.public_precommitment_digest,
        )
        or public_precommitment_bytes != _bound_public_precommitment_bytes(bound)
    ):
        raise ValueError("body/card execution preregistration differs from external anchor")
    runtime_root = Path(runtime_parent)
    repository = Path(repository_root)
    if not runtime_root.is_absolute() or not repository.is_absolute():
        raise ValueError("body/card runtime and repository roots must be absolute")
    runtime_root = runtime_root.resolve(strict=True)
    repository = repository.resolve(strict=True)
    persisted_prepared = load_body_card_review_private_state_v3(
        bound.private_state_path,
        bound.prepared.projection,
    )
    if persisted_prepared != bound.prepared:
        raise ValueError("body/card execution differs from durable private preparation")
    _validate_current_implementation(bound.prepared, repository_root=repository)
    with _body_card_lifecycle_lock(ledger):
        bound.validate_current()
        bound.validate_ledger(ledger)
        _validate_publication_receipt(bound, ledger, publication_receipt_path)
        ledger.recover_orphans()
        prerequisite: TerminalTransition | None = None
        for ordinal, slot in enumerate(bound.prepared.slots):
            bound.validate_current()
            bound.validate_ledger(ledger)
            _validate_publication_receipt(bound, ledger, publication_receipt_path)
            state = ledger.state(slot.slot_id)
            if state is SlotState.PENDING:
                if ordinal == 1:
                    prerequisite = _validate_slot_history(
                        bound.prepared,
                        ledger,
                        ordinal=0,
                    )
                auth_bytes = _validate_live_inputs(
                    bound.prepared,
                    repository_root=repository,
                    auth_file=auth_file,
                )
                try:
                    _run_native_slot(
                        prepared=bound.prepared,
                        ledger=ledger,
                        slot=slot,
                        prerequisite_terminal=prerequisite,
                        runtime_parent=runtime_root,
                        repository_root=repository,
                        auth_bytes=auth_bytes,
                    )
                except AttemptRunnerFailed as exc:
                    raise RuntimeError(
                        f"body/card reviewer {slot.reviewer_slot.value} failed and is burned"
                    ) from exc
            elif state is not SlotState.RETURNED:
                raise LedgerStateError(
                    f"body/card reviewer {slot.reviewer_slot.value} is burned without return"
                )
            prerequisite = _validate_slot_history(
                bound.prepared,
                ledger,
                ordinal=ordinal,
            )
            bound.validate_current()
            bound.validate_ledger(ledger)
            _validate_publication_receipt(bound, ledger, publication_receipt_path)
            _validate_current_implementation(bound.prepared, repository_root=repository)

        manifests: list[SignedBodyCardReviewManifestV3] = []
        bound.validate_current()
        bound.validate_ledger(ledger)
        _validate_publication_receipt(bound, ledger, publication_receipt_path)
        _validate_current_implementation(bound.prepared, repository_root=repository)
        for ordinal, slot in enumerate(bound.prepared.slots):
            terminal = _validate_slot_history(bound.prepared, ledger, ordinal=ordinal)
            payload = ledger.load_selected_artifact(slot.slot_id)
            if payload.media_type != BODY_CARD_PRIVATE_ARTIFACT_MEDIA_TYPE_V3:
                raise LedgerIntegrityError("body/card private artifact media type changed")
            artifact = deserialize_codex_run_result(payload.content)
            attempt_evidence = bind_body_card_review_attempt_v3(
                ledger,
                terminal,
                payload.content,
                reviewer_slot=slot.reviewer_slot,
            )
            plan = (
                bound.prepared.reviewer_a_plan if ordinal == 0 else bound.prepared.reviewer_b_plan
            )
            submission = parse_body_card_review_run_v3(
                artifact,
                bound.prepared.projection,
                plan,
                bound.prepared.policy,
                attempt_evidence,
                core_precommitment_digest=bound.prepared.core_precommitment_digest,
                review_precommitment_digest=bound.public_precommitment_digest,
            )
            manifests.append(
                sign_body_card_review_submission_v3(
                    submission,
                    bound.prepared.private_material.signer(slot.reviewer_slot),
                )
            )
        validate_body_card_reviewer_pair_v3(
            manifests[0].submission,
            manifests[1].submission,
            reviewer_a_key_fingerprint=(
                bound.prepared.private_material.signer(
                    BodyCardReviewerSlotV3.REVIEWER_A
                ).key_fingerprint
            ),
            reviewer_b_key_fingerprint=(
                bound.prepared.private_material.signer(
                    BodyCardReviewerSlotV3.REVIEWER_B
                ).key_fingerprint
            ),
        )
        bound.validate_current()
        bound.validate_ledger(ledger)
        _validate_publication_receipt(bound, ledger, publication_receipt_path)
        _validate_current_implementation(bound.prepared, repository_root=repository)
        return (manifests[0], manifests[1])


__all__ = [
    "BODY_CARD_CORE_PRECOMMITMENT_SCHEMA_V3",
    "BODY_CARD_EXECUTION_SLOT_COUNT_V3",
    "BODY_CARD_PRIVATE_STATE_SCHEMA_V3",
    "BodyCardReviewAuthSnapshotV3",
    "BodyCardReviewPrivateMaterialV3",
    "BoundBodyCardReviewsV3",
    "PreparedBodyCardReviewSlotV3",
    "PreparedBodyCardReviewsV3",
    "bind_body_card_review_ledger_v3",
    "body_card_review_core_precommitment_digest_v3",
    "build_body_card_review_core_precommitment_v3",
    "capture_body_card_review_implementation_revision_v3",
    "create_body_card_review_ledger_v3",
    "deserialize_body_card_review_private_state_v3",
    "execute_body_card_reviews_v3",
    "finalize_body_card_review_publication_v3",
    "load_body_card_review_private_state_v3",
    "open_body_card_review_ledger_v3",
    "prepare_body_card_reviews_v3",
    "restore_bound_body_card_review_ledger_v3",
    "save_body_card_review_private_state_v3",
    "serialize_body_card_review_private_state_v3",
]
