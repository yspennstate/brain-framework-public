"""One-shot desktop execution for the frozen v4 brain-benefit pilot.

The module owns the causal boundary between a sealed 72-slot plan and fresh Codex
desktop processes.  It cold-replays the plan and opaque task map, live-refetches the
public pre-beacon GitHub anchor, seals every exact prompt before any ``STARTED``
record, and then permits at most one fresh process/thread for each slot.  A failed
process remains an intention-to-treat failure; this API has no retry or replacement
surface.

Raw prompts, transcripts, and responses are stored only as private attempt-ledger
artifacts.  Public records contain commitments and counts, never arm labels or task
payloads.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .attempt_ledger import (
    ArtifactPayload,
    AttemptHistoryRecord,
    AttemptLedger,
    AttemptRunnerFailed,
    DispatchMaterial,
    OutcomeKind,
    SlotCommitment,
    SlotState,
    StartedAttempt,
    StoredOutcome,
    TerminalTransition,
)
from .codex_artifact_io import serialize_codex_run_result
from .codex_runner import (
    DEFAULT_BASE_INSTRUCTIONS,
    DEFAULT_DEVELOPER_INSTRUCTIONS,
    CodexAppServerRunner,
    CodexRunConfig,
    ExecutableProvenance,
    capture_codex_executable_provenance,
    solver_process_isolation_policy_digest,
)
from .exact_answer_v3 import MAX_EXACT_ANSWER_BYTES_V3
from .github_execution_ack_v4 import (
    GitHubExecutionAcknowledgementV4,
    verify_live_github_execution_ack_v4,
)
from .github_precommit_anchor_v4 import (
    GitHubPrecommitAnchorEvidenceV4,
    verify_live_github_precommit_anchor_v4,
)
from .models import canonical_json
from .random_external_execution_authorization_v4 import (
    RandomExternalExecutionAuthorizationV4,
)
from .random_external_execution_manifest_v4 import RandomExternalExecutionManifestV4
from .random_external_pilot_task_map_v4 import (
    SealedRandomExternalTaskMapV4,
    solver_projection_from_random_external_task_map_v4,
    verify_sealed_random_external_task_map_v4,
)
from .three_arm_pilot_protocol_v3 import (
    SOLVER_PROMPT_TEMPLATE_DIGEST_V3,
    SOLVER_RESPONSE_SCHEMA_DIGEST_V3,
    SolverTaskProjectionV3,
    assert_three_arm_protocol_config_v3,
    render_solver_prompt_v3,
    solver_prompt_digest_v3,
)
from .three_arm_random_external_pilot_v4 import (
    RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4,
    RandomExternalPilotPlanV4,
    verify_random_external_pilot_plan_v4,
)

RANDOM_EXTERNAL_EXECUTION_SCHEMA_V4 = "org.math-research-brain/random-external-pilot-execution/v4"
RANDOM_EXTERNAL_EXECUTION_POLICY_SCHEMA_V4 = (
    "org.math-research-brain/random-external-pilot-execution-policy/v4"
)
RANDOM_EXTERNAL_SOLVER_OUTPUT_SCHEMA_V4 = (
    "org.math-research-brain/random-external-solver-output-schema/v4"
)

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_HMAC_RE = re.compile(r"hmac-sha256:[0-9a-f]{64}\Z", re.ASCII)
_SLOT_ID_RE = re.compile(r"slot_[0-9a-f]{32}\Z", re.ASCII)
_TASK_ID_RE = re.compile(r"solvertaskv3_[0-9a-f]{64}\Z", re.ASCII)
_SAFE_SELECTOR_RE = re.compile(r"[A-Za-z0-9._-]{1,128}\Z", re.ASCII)
_ID_DOMAIN = b"math-brain/random-external-execution-v4/id\x00"
_DISPATCH_DOMAIN = b"math-brain/random-external-execution-v4/dispatch\x00"
_BURNED_CLOSURE_DOMAIN = b"math-brain/random-external-execution-v4/burned-closure\x00"


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _digest_json(value: object) -> str:
    return _sha256(canonical_json(value).encode("utf-8"))


def _digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical SHA-256 digest")
    return value


def _secret(value: object, label: str) -> bytes:
    if type(value) is not bytes or len(value) < 32:
        raise ValueError(f"{label} must contain at least 256 bits")
    return bytes(value)


def _selector(value: object, label: str) -> str:
    if type(value) is not str or _SAFE_SELECTOR_RE.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def _role_separated(secrets: tuple[bytes, ...]) -> None:
    if any(
        hmac.compare_digest(left, right)
        for index, left in enumerate(secrets)
        for right in secrets[index + 1 :]
    ):
        raise ValueError("v4 execution secrets must be role-separated")


def _derived_slot_id(seed: bytes, plan_digest: str, ordinal: int, task_id: str) -> str:
    material = (
        _ID_DOMAIN
        + plan_digest.encode("ascii")
        + b"\x00"
        + ordinal.to_bytes(4, "big")
        + b"\x00"
        + task_id.encode("ascii")
    )
    return "slot_" + hmac.new(seed, material, hashlib.sha256).hexdigest()[:32]


def _derived_dispatch_nonce(
    seed: bytes,
    plan_digest: str,
    ordinal: int,
    task_id: str,
) -> str:
    material = (
        _DISPATCH_DOMAIN
        + plan_digest.encode("ascii")
        + b"\x00"
        + ordinal.to_bytes(4, "big")
        + b"\x00"
        + task_id.encode("ascii")
    )
    return "sha256:" + hmac.new(seed, material, hashlib.sha256).hexdigest()


def random_external_solver_output_schema_v4(
    projection: SolverTaskProjectionV3,
) -> dict[str, object]:
    """Return a fresh per-task JSON schema with the opaque response id fixed."""

    if type(projection) is not SolverTaskProjectionV3:
        raise ValueError("v4 output schema requires an exact solver projection")
    projection.__post_init__()
    if _TASK_ID_RE.fullmatch(projection.task_id) is None:
        raise ValueError("v4 output schema task id violates the solver protocol")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
        "properties": {
            "abstain": {"type": "boolean"},
            "concise_reasoning": {
                "maxLength": 4_096,
                "minLength": 1,
                "type": "string",
            },
            "final_answer": {
                "anyOf": [
                    {
                        "maxLength": MAX_EXACT_ANSWER_BYTES_V3,
                        "minLength": 1,
                        "type": "string",
                    },
                    {"type": "null"},
                ]
            },
            "task_id": {"const": projection.task_id, "type": "string"},
        },
        "required": ["abstain", "concise_reasoning", "final_answer", "task_id"],
        "type": "object",
    }


def _execution_policy_payload_v4(
    *,
    model: str,
    effort: str,
    model_provider: str,
    timeout_seconds: int,
    executable_provenance: ExecutableProvenance,
    solver_isolation_policy_digest: str,
    base_instructions_digest: str,
    developer_instructions_digest: str,
    implementation_manifest_digest: str,
    served_model_identity_verified: bool = False,
    retry_authorized: bool = False,
) -> dict[str, object]:
    return {
        "base_instructions_digest": base_instructions_digest,
        "developer_instructions_digest": developer_instructions_digest,
        "effort": effort,
        "executable_provenance": executable_provenance.to_dict(),
        "fresh_process_per_slot": True,
        "implementation_manifest_digest": implementation_manifest_digest,
        "model": model,
        "model_provider": model_provider,
        "retry_authorized": retry_authorized,
        "schema": RANDOM_EXTERNAL_EXECUTION_POLICY_SCHEMA_V4,
        "schema_version": 4,
        "served_model_identity_verified": served_model_identity_verified,
        "solver_isolation_policy_digest": solver_isolation_policy_digest,
        "timeout_seconds": timeout_seconds,
    }


@dataclass(frozen=True, slots=True)
class RandomExternalExecutionPolicyV4:
    """Exact model, executable, and sterile-process policy sealed before planning."""

    model: str
    effort: str
    model_provider: str
    timeout_seconds: int
    executable_provenance: ExecutableProvenance
    solver_isolation_policy_digest: str
    base_instructions_digest: str
    developer_instructions_digest: str
    implementation_manifest: RandomExternalExecutionManifestV4 = field(repr=False)
    content_digest: str
    served_model_identity_verified: bool = False
    retry_authorized: bool = False

    def _payload(self) -> dict[str, object]:
        return _execution_policy_payload_v4(
            model=self.model,
            effort=self.effort,
            model_provider=self.model_provider,
            timeout_seconds=self.timeout_seconds,
            executable_provenance=self.executable_provenance,
            solver_isolation_policy_digest=self.solver_isolation_policy_digest,
            base_instructions_digest=self.base_instructions_digest,
            developer_instructions_digest=self.developer_instructions_digest,
            implementation_manifest_digest=self.implementation_manifest.content_digest,
            served_model_identity_verified=self.served_model_identity_verified,
            retry_authorized=self.retry_authorized,
        )

    def __post_init__(self) -> None:
        if type(self) is not RandomExternalExecutionPolicyV4:
            raise ValueError("v4 execution-policy subclasses are forbidden")
        for value, label in (
            (self.model, "v4 model"),
            (self.effort, "v4 effort"),
            (self.model_provider, "v4 model provider"),
        ):
            _selector(value, label)
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 3_600:
            raise ValueError("v4 timeout must be an exact bounded integer")
        if type(self.executable_provenance) is not ExecutableProvenance:
            raise ValueError("v4 execution policy requires exact executable provenance")
        self.executable_provenance.__post_init__()
        if type(self.implementation_manifest) is not RandomExternalExecutionManifestV4:
            raise ValueError("v4 execution policy requires an exact implementation manifest")
        self.implementation_manifest.validate_current()
        for value, label in (
            (self.solver_isolation_policy_digest, "v4 solver isolation policy"),
            (self.base_instructions_digest, "v4 base instructions"),
            (self.developer_instructions_digest, "v4 developer instructions"),
            (self.content_digest, "v4 execution policy"),
        ):
            _digest(value, label)
        if not hmac.compare_digest(
            self.solver_isolation_policy_digest,
            solver_process_isolation_policy_digest(),
        ):
            raise ValueError("v4 solver isolation policy changed")
        if not hmac.compare_digest(
            self.base_instructions_digest,
            _sha256(DEFAULT_BASE_INSTRUCTIONS.encode("utf-8")),
        ) or not hmac.compare_digest(
            self.developer_instructions_digest,
            _sha256(DEFAULT_DEVELOPER_INSTRUCTIONS.encode("utf-8")),
        ):
            raise ValueError("v4 solver instructions changed")
        if self.served_model_identity_verified is not False or self.retry_authorized is not False:
            raise ValueError("v4 execution policy overclaims identity or retry authority")
        if not hmac.compare_digest(self.content_digest, _digest_json(self._payload())):
            raise ValueError("v4 execution policy digest is stale")

    def to_dict(self) -> dict[str, object]:
        self.__post_init__()
        return self._payload() | {"content_digest": self.content_digest}

    @property
    def implementation_manifest_digest(self) -> str:
        return self.implementation_manifest.content_digest

    def validate_current(self, *, repository_root: str | Path | None = None) -> None:
        self.__post_init__()
        if (
            capture_codex_executable_provenance(self.executable_provenance.canonical_path)
            != self.executable_provenance
        ):
            raise ValueError("live Codex executable differs from the sealed v4 policy")
        if repository_root is not None:
            self.implementation_manifest.validate_git_tree(repository_root)


def freeze_random_external_execution_policy_v4(
    *,
    executable_provenance: ExecutableProvenance,
    implementation_manifest: RandomExternalExecutionManifestV4,
    model: str = "gpt-5.6-sol",
    effort: str = "ultra",
    model_provider: str = "openai",
    timeout_seconds: int = 900,
) -> RandomExternalExecutionPolicyV4:
    """Freeze policy bytes; this function does not infer served-model identity."""

    if type(implementation_manifest) is not RandomExternalExecutionManifestV4:
        raise ValueError("v4 execution policy requires an exact implementation manifest")
    implementation_manifest.validate_current()
    isolation_digest = solver_process_isolation_policy_digest()
    base_digest = _sha256(DEFAULT_BASE_INSTRUCTIONS.encode("utf-8"))
    developer_digest = _sha256(DEFAULT_DEVELOPER_INSTRUCTIONS.encode("utf-8"))
    payload = _execution_policy_payload_v4(
        model=model,
        effort=effort,
        model_provider=model_provider,
        timeout_seconds=timeout_seconds,
        executable_provenance=executable_provenance,
        solver_isolation_policy_digest=isolation_digest,
        base_instructions_digest=base_digest,
        developer_instructions_digest=developer_digest,
        implementation_manifest_digest=implementation_manifest.content_digest,
    )
    return RandomExternalExecutionPolicyV4(
        model=model,
        effort=effort,
        model_provider=model_provider,
        timeout_seconds=timeout_seconds,
        executable_provenance=executable_provenance,
        solver_isolation_policy_digest=isolation_digest,
        base_instructions_digest=base_digest,
        developer_instructions_digest=developer_digest,
        implementation_manifest=implementation_manifest,
        content_digest=_digest_json(payload),
    )


@dataclass(frozen=True, slots=True)
class PreparedRandomExternalExecutionSlotV4:
    ordinal: int
    private_task_id: str = field(repr=False)
    response_task_id: str
    slot_id: str
    prompt_digest: str
    output_schema_digest: str
    dispatch: DispatchMaterial = field(repr=False)
    commitment: SlotCommitment

    def __post_init__(self) -> None:
        if type(self) is not PreparedRandomExternalExecutionSlotV4:
            raise ValueError("v4 prepared-slot subclasses are forbidden")
        if type(self.ordinal) is not int or not (
            0 <= self.ordinal < RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4
        ):
            raise ValueError("v4 prepared-slot ordinal is invalid")
        _digest(self.private_task_id, "v4 prepared private task")
        if (
            type(self.response_task_id) is not str
            or _TASK_ID_RE.fullmatch(self.response_task_id) is None
        ):
            raise ValueError("v4 prepared response task id is invalid")
        if type(self.slot_id) is not str or _SLOT_ID_RE.fullmatch(self.slot_id) is None:
            raise ValueError("v4 prepared slot id is invalid")
        _digest(self.prompt_digest, "v4 prepared prompt")
        _digest(self.output_schema_digest, "v4 prepared output schema")
        if type(self.dispatch) is not DispatchMaterial or type(self.commitment) is not (
            SlotCommitment
        ):
            raise ValueError("v4 prepared slot requires exact ledger records")
        self.dispatch.__post_init__()
        self.commitment.__post_init__()
        if (
            self.commitment.slot_id != self.slot_id
            or not hmac.compare_digest(
                self.prompt_digest,
                _sha256(self.dispatch.prompt.encode("utf-8")),
            )
            or not hmac.compare_digest(
                self.output_schema_digest,
                _sha256(self.dispatch.output_schema_json),
            )
        ):
            raise ValueError("v4 prepared slot differs from its exact dispatch")


@dataclass(frozen=True, slots=True)
class PreparedRandomExternalExecutionV4:
    """Private replay material plus the public commitments for all 72 slots."""

    plan: RandomExternalPilotPlanV4 = field(repr=False)
    task_map: SealedRandomExternalTaskMapV4 = field(repr=False)
    policy: RandomExternalExecutionPolicyV4
    slots: tuple[PreparedRandomExternalExecutionSlotV4, ...] = field(repr=False)
    allocation_seed: bytes = field(repr=False)
    task_map_response_seed: bytes = field(repr=False)
    task_map_score_seed: bytes = field(repr=False)
    task_map_commitment_key: bytes = field(repr=False)
    ledger_key: bytes = field(repr=False)
    slot_id_seed: bytes = field(repr=False)
    dispatch_seed: bytes = field(repr=False)
    expected_plan_public_precommitment_digest: str
    expected_task_map_public_precommitment_digest: str
    random_selection_precommitment_bytes: bytes = field(repr=False)
    expected_random_selection_precommitment_record_sha256: str
    anchor_evidence: GitHubPrecommitAnchorEvidenceV4

    def __post_init__(self) -> None:
        if type(self) is not PreparedRandomExternalExecutionV4:
            raise ValueError("v4 prepared-execution subclasses are forbidden")
        if (
            type(self.plan) is not RandomExternalPilotPlanV4
            or type(self.task_map) is not SealedRandomExternalTaskMapV4
            or type(self.policy) is not RandomExternalExecutionPolicyV4
        ):
            raise ValueError("v4 prepared execution requires exact plan, map, and policy")
        self.policy.__post_init__()
        assert_three_arm_protocol_config_v3(self.plan.config.base)
        if not hmac.compare_digest(
            self.plan.config.base.runtime_policy_digest,
            self.policy.content_digest,
        ):
            raise ValueError("v4 plan runtime policy differs from the execution policy")
        expected_plan = _digest(
            self.expected_plan_public_precommitment_digest,
            "expected v4 plan public precommitment",
        )
        expected_map = _digest(
            self.expected_task_map_public_precommitment_digest,
            "expected v4 task-map public precommitment",
        )
        expected_random = _digest(
            self.expected_random_selection_precommitment_record_sha256,
            "expected random selector precommitment record",
        )
        if type(self.random_selection_precommitment_bytes) is not bytes or not (
            self.random_selection_precommitment_bytes
        ):
            raise ValueError("v4 execution requires immutable random precommitment bytes")
        if not hmac.compare_digest(
            _sha256(self.random_selection_precommitment_bytes),
            expected_random,
        ):
            raise ValueError("random selector precommitment bytes differ from their record SHA")
        if not hmac.compare_digest(
            self.plan.config.external_selection_precommitment_digest,
            expected_random,
        ):
            raise ValueError("v4 plan must bind the exact random precommitment record SHA")
        if type(self.anchor_evidence) is not GitHubPrecommitAnchorEvidenceV4:
            raise ValueError("v4 execution requires exact GitHub anchor evidence")
        self.anchor_evidence.validate_current()
        if not hmac.compare_digest(
            self.anchor_evidence.content_digest,
            self.plan.config.external_selection_precommit_anchor_evidence_digest,
        ):
            raise ValueError("v4 plan differs from the exact GitHub anchor evidence")
        secrets = (
            _secret(self.allocation_seed, "v4 allocation seed"),
            _secret(self.task_map_response_seed, "v4 task-map response seed"),
            _secret(self.task_map_score_seed, "v4 task-map score seed"),
            _secret(self.task_map_commitment_key, "v4 task-map commitment key"),
            _secret(self.ledger_key, "v4 ledger key"),
            _secret(self.slot_id_seed, "v4 slot-id seed"),
            _secret(self.dispatch_seed, "v4 dispatch seed"),
        )
        _role_separated(secrets)
        verify_random_external_pilot_plan_v4(
            self.plan,
            allocation_seed=self.allocation_seed,
            expected_public_precommitment_digest=expected_plan,
        )
        verify_sealed_random_external_task_map_v4(
            self.task_map,
            self.plan,
            response_seed=self.task_map_response_seed,
            score_seed=self.task_map_score_seed,
            commitment_key=self.task_map_commitment_key,
            expected_public_precommitment_digest=expected_map,
        )
        if type(self.slots) is not tuple or len(self.slots) != (
            RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4
        ):
            raise ValueError("v4 execution requires exactly 72 prepared slots")
        if any(type(slot) is not PreparedRandomExternalExecutionSlotV4 for slot in self.slots):
            raise ValueError("v4 execution slots must be exact")
        if tuple(slot.ordinal for slot in self.slots) != tuple(
            range(RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4)
        ):
            raise ValueError("v4 execution slots must retain exact schedule order")
        if len({slot.slot_id for slot in self.slots}) != len(self.slots):
            raise ValueError("v4 execution reuses a ledger slot id")
        if len({slot.response_task_id for slot in self.slots}) != len(self.slots):
            raise ValueError("v4 execution reuses an opaque response task id")
        for slot in self.slots:
            slot.__post_init__()
            assignment = self.plan.assignments[slot.ordinal]
            projection = solver_projection_from_random_external_task_map_v4(
                self.task_map,
                self.plan,
                ordinal=slot.ordinal,
            )
            expected_schema_raw = canonical_json(
                random_external_solver_output_schema_v4(projection)
            ).encode("utf-8")
            if (
                slot.private_task_id != assignment.task_id
                or slot.response_task_id != projection.task_id
                or slot.slot_id
                != _derived_slot_id(
                    self.slot_id_seed,
                    self.plan.digest,
                    slot.ordinal,
                    assignment.task_id,
                )
                or slot.prompt_digest != solver_prompt_digest_v3(projection)
                or slot.dispatch.prompt != render_solver_prompt_v3(projection)
                or slot.dispatch.output_schema_json != expected_schema_raw
                or slot.dispatch.dispatch_nonce
                != _derived_dispatch_nonce(
                    self.dispatch_seed,
                    self.plan.digest,
                    slot.ordinal,
                    assignment.task_id,
                )
                or slot.commitment != slot.dispatch.seal(key=self.ledger_key, slot_id=slot.slot_id)
            ):
                raise ValueError("v4 execution slot differs from deterministic cold replay")

    def public_precommitment(self) -> dict[str, object]:
        self.__post_init__()
        slot_records = [slot.commitment.to_dict(ordinal=slot.ordinal) for slot in self.slots]
        return {
            "anchor_evidence_digest": self.anchor_evidence.content_digest,
            "attempt_count": len(self.slots),
            "exact_schedule_order": True,
            "execution_policy_digest": self.policy.content_digest,
            "fresh_process_per_slot": True,
            "model_identity_verified": False,
            "plan_digest": self.plan.digest,
            "plan_public_precommitment_digest": (self.expected_plan_public_precommitment_digest),
            "random_selection_precommitment_record_sha256": (
                self.expected_random_selection_precommitment_record_sha256
            ),
            "retry_authorized": False,
            "schema": RANDOM_EXTERNAL_EXECUTION_SCHEMA_V4,
            "schema_version": 4,
            "slot_commitment_manifest_digest": _digest_json(slot_records),
            "task_map_public_precommitment_digest": (
                self.expected_task_map_public_precommitment_digest
            ),
        }

    @property
    def public_precommitment_digest(self) -> str:
        return _digest_json(self.public_precommitment())


def _verify_live_anchor(prepared: PreparedRandomExternalExecutionV4) -> None:
    evidence = prepared.anchor_evidence
    fresh = verify_live_github_precommit_anchor_v4(
        prepared.random_selection_precommitment_bytes,
        expected_precommitment_record_sha256=(
            prepared.expected_random_selection_precommitment_record_sha256
        ),
        expected_anchor_evidence_digest=evidence.content_digest,
        repository_owner=evidence.repository_owner,
        repository_name=evidence.repository_name,
        issue_number=evidence.issue_number,
        comment_id=evidence.comment_id,
        expected_actor_login=evidence.actor_login,
        expected_actor_id=evidence.actor_id,
    )
    if fresh != evidence:
        raise ValueError("live GitHub anchor differs from the sealed evidence record")


def _run_spec_digest(
    plan: RandomExternalPilotPlanV4,
    task_map: SealedRandomExternalTaskMapV4,
    policy: RandomExternalExecutionPolicyV4,
) -> str:
    return _digest_json(
        {
            "execution_policy_digest": policy.content_digest,
            "plan_digest": plan.digest,
            "prompt_template_digest": SOLVER_PROMPT_TEMPLATE_DIGEST_V3,
            "response_schema_digest": SOLVER_RESPONSE_SCHEMA_DIGEST_V3,
            "task_map_public_precommitment_digest": task_map.public_precommitment_digest,
        }
    )


def _batch_digest(plan: RandomExternalPilotPlanV4) -> str:
    return _digest_json(
        {
            "case_universe_digest": plan.case_universe_digest,
            "plan_digest": plan.digest,
            "schedule_digest": plan.schedule_digest,
            "task_count": len(plan.assignments),
        }
    )


def prepare_random_external_execution_v4(
    plan: RandomExternalPilotPlanV4,
    task_map: SealedRandomExternalTaskMapV4,
    policy: RandomExternalExecutionPolicyV4,
    *,
    allocation_seed: bytes,
    task_map_response_seed: bytes,
    task_map_score_seed: bytes,
    task_map_commitment_key: bytes,
    ledger_key: bytes,
    slot_id_seed: bytes,
    dispatch_seed: bytes,
    expected_plan_public_precommitment_digest: str,
    expected_task_map_public_precommitment_digest: str,
    random_selection_precommitment_bytes: bytes,
    expected_random_selection_precommitment_record_sha256: str,
    anchor_evidence: GitHubPrecommitAnchorEvidenceV4,
) -> PreparedRandomExternalExecutionV4:
    """Cold-replay and seal all slots after a direct live-anchor verification."""

    if (
        type(plan) is not RandomExternalPilotPlanV4
        or type(task_map) is not SealedRandomExternalTaskMapV4
        or type(policy) is not RandomExternalExecutionPolicyV4
    ):
        raise ValueError("v4 execution preparation requires exact inputs")
    run_spec_digest = _run_spec_digest(plan, task_map, policy)
    batch_digest = _batch_digest(plan)
    slots: list[PreparedRandomExternalExecutionSlotV4] = []
    for ordinal, assignment in enumerate(plan.assignments):
        projection = solver_projection_from_random_external_task_map_v4(
            task_map,
            plan,
            ordinal=ordinal,
        )
        prompt = render_solver_prompt_v3(projection)
        schema_raw = canonical_json(random_external_solver_output_schema_v4(projection)).encode(
            "utf-8"
        )
        slot_id = _derived_slot_id(slot_id_seed, plan.digest, ordinal, assignment.task_id)
        dispatch = DispatchMaterial(
            run_spec_digest=run_spec_digest,
            batch_digest=batch_digest,
            prompt=prompt,
            output_schema_json=schema_raw,
            request_policy_digest=policy.content_digest,
            dispatch_nonce=_derived_dispatch_nonce(
                dispatch_seed,
                plan.digest,
                ordinal,
                assignment.task_id,
            ),
        )
        slots.append(
            PreparedRandomExternalExecutionSlotV4(
                ordinal=ordinal,
                private_task_id=assignment.task_id,
                response_task_id=projection.task_id,
                slot_id=slot_id,
                prompt_digest=solver_prompt_digest_v3(projection),
                output_schema_digest=_sha256(schema_raw),
                dispatch=dispatch,
                commitment=dispatch.seal(key=ledger_key, slot_id=slot_id),
            )
        )
    prepared = PreparedRandomExternalExecutionV4(
        plan=plan,
        task_map=task_map,
        policy=policy,
        slots=tuple(slots),
        allocation_seed=allocation_seed,
        task_map_response_seed=task_map_response_seed,
        task_map_score_seed=task_map_score_seed,
        task_map_commitment_key=task_map_commitment_key,
        ledger_key=ledger_key,
        slot_id_seed=slot_id_seed,
        dispatch_seed=dispatch_seed,
        expected_plan_public_precommitment_digest=(expected_plan_public_precommitment_digest),
        expected_task_map_public_precommitment_digest=(
            expected_task_map_public_precommitment_digest
        ),
        random_selection_precommitment_bytes=random_selection_precommitment_bytes,
        expected_random_selection_precommitment_record_sha256=(
            expected_random_selection_precommitment_record_sha256
        ),
        anchor_evidence=anchor_evidence,
    )
    prepared.__post_init__()
    _verify_live_anchor(prepared)
    return prepared


def _is_reparse_point_v4(path: Path) -> bool:
    try:
        status = path.lstat()
    except OSError as error:
        raise ValueError(f"cannot inspect v4 ledger path: {path}") from error
    return path.is_symlink() or bool(
        getattr(status, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _inside_git_repository_v4(path: Path) -> bool:
    return any((ancestor / ".git").exists() for ancestor in (path, *path.parents))


def _canonical_private_ledger_root_v4(
    value: str | Path,
    *,
    must_exist: bool,
) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("v4 execution ledger root must be absolute")
    if ".." in path.parts:
        raise ValueError("v4 execution ledger root must not use a parent alias")
    if must_exist:
        root = path.resolve(strict=True)
        if not root.is_dir() or _is_reparse_point_v4(root):
            raise ValueError("v4 execution ledger root must be a real directory")
    else:
        parent = path.parent.resolve(strict=True)
        if not parent.is_dir() or _is_reparse_point_v4(parent):
            raise ValueError("v4 execution ledger parent must be a real directory")
        root = parent / path.name
        if root.exists() or root.is_symlink():
            raise ValueError("new v4 execution ledger root must not already exist")
    supplied = os.path.normcase(os.path.abspath(str(path)))
    canonical = os.path.normcase(str(root))
    if supplied != canonical:
        raise ValueError("v4 execution ledger root must use its canonical path")
    if _inside_git_repository_v4(root):
        raise ValueError("v4 private execution ledger must be outside every Git repository")
    return root


@dataclass(frozen=True, slots=True)
class BoundRandomExternalExecutionV4:
    prepared: PreparedRandomExternalExecutionV4 = field(repr=False)
    ledger_root: str
    ledger_digest: str

    def __post_init__(self) -> None:
        if type(self) is not BoundRandomExternalExecutionV4 or type(self.prepared) is not (
            PreparedRandomExternalExecutionV4
        ):
            raise ValueError("v4 bound execution requires exact prepared state")
        self.prepared.__post_init__()
        _digest(self.ledger_digest, "v4 execution ledger")
        if type(self.ledger_root) is not str:
            raise ValueError("v4 execution ledger root must be exact text")
        root = _canonical_private_ledger_root_v4(self.ledger_root, must_exist=True)
        ledger = AttemptLedger.open(root, key=self.prepared.ledger_key)
        if (
            str(ledger.root) != self.ledger_root
            or not hmac.compare_digest(ledger.ledger_digest, self.ledger_digest)
            or ledger.slots != tuple(slot.commitment for slot in self.prepared.slots)
        ):
            raise ValueError("v4 bound ledger differs from the sealed slot universe")

    @property
    def root(self) -> Path:
        return Path(self.ledger_root)

    def open_ledger(self) -> AttemptLedger:
        self.__post_init__()
        return AttemptLedger.open(self.root, key=self.prepared.ledger_key)

    def public_record(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "execution_precommitment_digest": self.prepared.public_precommitment_digest,
            "execution_policy_digest": self.prepared.policy.content_digest,
            "implementation_manifest_digest": (self.prepared.policy.implementation_manifest_digest),
            "ledger_digest": self.ledger_digest,
            "plan_digest": self.prepared.plan.digest,
            "schema": RANDOM_EXTERNAL_EXECUTION_SCHEMA_V4,
            "schema_version": 4,
            "task_map_public_precommitment_digest": (
                self.prepared.expected_task_map_public_precommitment_digest
            ),
            "task_count": RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4,
        }

    @property
    def public_record_digest(self) -> str:
        return _digest_json(self.public_record())


def create_random_external_execution_ledger_v4(
    prepared: PreparedRandomExternalExecutionV4,
    ledger_root: str | Path,
) -> BoundRandomExternalExecutionV4:
    """Create the immutable 72-slot ledger only after another live-anchor read."""

    if type(prepared) is not PreparedRandomExternalExecutionV4:
        raise ValueError("v4 ledger creation requires exact prepared state")
    prepared.__post_init__()
    _verify_live_anchor(prepared)
    canonical_root = _canonical_private_ledger_root_v4(ledger_root, must_exist=False)
    ledger = AttemptLedger.create(
        canonical_root,
        key=prepared.ledger_key,
        slots=tuple(slot.commitment for slot in prepared.slots),
    )
    if any(ledger.state(slot.slot_id) is not SlotState.PENDING for slot in prepared.slots):
        raise ValueError("new v4 execution ledger was not created fully pending")
    return BoundRandomExternalExecutionV4(
        prepared=prepared,
        ledger_root=str(ledger.root),
        ledger_digest=ledger.ledger_digest,
    )


def restore_random_external_execution_ledger_v4(
    prepared: PreparedRandomExternalExecutionV4,
    ledger_root: str | Path,
    *,
    expected_ledger_digest: str,
) -> BoundRandomExternalExecutionV4:
    """Restore without resetting, replacing, or retrying any burned slot."""

    if type(prepared) is not PreparedRandomExternalExecutionV4:
        raise ValueError("v4 ledger restoration requires exact prepared state")
    prepared.__post_init__()
    _verify_live_anchor(prepared)
    expected = _digest(expected_ledger_digest, "expected v4 execution ledger")
    canonical_root = _canonical_private_ledger_root_v4(ledger_root, must_exist=True)
    ledger = AttemptLedger.open(canonical_root, key=prepared.ledger_key)
    if not hmac.compare_digest(ledger.ledger_digest, expected):
        raise ValueError("restored v4 execution ledger differs from its public digest")
    return BoundRandomExternalExecutionV4(
        prepared=prepared,
        ledger_root=str(ledger.root),
        ledger_digest=ledger.ledger_digest,
    )


@dataclass(frozen=True, slots=True)
class AcknowledgedRandomExternalExecutionV4:
    """One bound ledger named by the issue's unique immutable execution comment."""

    bound: BoundRandomExternalExecutionV4 = field(repr=False)
    authorization: RandomExternalExecutionAuthorizationV4
    acknowledgement: GitHubExecutionAcknowledgementV4
    expected_ack_evidence_digest: str

    def __post_init__(self) -> None:
        if type(self) is not AcknowledgedRandomExternalExecutionV4 or type(self.bound) is not (
            BoundRandomExternalExecutionV4
        ):
            raise ValueError("v4 acknowledged execution requires an exact bound ledger")
        self.bound.__post_init__()
        if type(self.authorization) is not RandomExternalExecutionAuthorizationV4:
            raise ValueError("v4 execution requires exact cold-replayed authorization")
        self.authorization.validate_current()
        if type(self.acknowledgement) is not GitHubExecutionAcknowledgementV4:
            raise ValueError("v4 execution requires exact GitHub acknowledgement evidence")
        self.acknowledgement.validate_current()
        expected = _digest(
            self.expected_ack_evidence_digest,
            "expected v4 execution acknowledgement",
        )
        ack = self.acknowledgement
        prepared = self.bound.prepared
        if not all(
            (
                hmac.compare_digest(ack.content_digest, expected),
                hmac.compare_digest(
                    ack.execution_authorization_digest,
                    self.authorization.content_digest,
                ),
                hmac.compare_digest(
                    ack.execution_precommitment_digest,
                    prepared.public_precommitment_digest,
                ),
                hmac.compare_digest(ack.ledger_digest, self.bound.ledger_digest),
                hmac.compare_digest(
                    ack.execution_public_record_digest,
                    self.bound.public_record_digest,
                ),
                hmac.compare_digest(ack.plan_digest, prepared.plan.digest),
                hmac.compare_digest(
                    ack.task_map_public_precommitment_digest,
                    prepared.expected_task_map_public_precommitment_digest,
                ),
                hmac.compare_digest(ack.execution_policy_digest, prepared.policy.content_digest),
                hmac.compare_digest(
                    ack.implementation_manifest_digest,
                    prepared.policy.implementation_manifest_digest,
                ),
            )
        ):
            raise ValueError("v4 execution acknowledgement differs from the bound campaign")

    def verify_live(self) -> None:
        self.__post_init__()
        ack = self.acknowledgement
        prepared = self.bound.prepared
        fresh = verify_live_github_execution_ack_v4(
            execution_authorization_digest=self.authorization.content_digest,
            execution_precommitment_digest=prepared.public_precommitment_digest,
            ledger_digest=self.bound.ledger_digest,
            execution_public_record_digest=self.bound.public_record_digest,
            plan_digest=prepared.plan.digest,
            task_map_public_precommitment_digest=(
                prepared.expected_task_map_public_precommitment_digest
            ),
            execution_policy_digest=prepared.policy.content_digest,
            implementation_manifest_digest=prepared.policy.implementation_manifest_digest,
            expected_ack_evidence_digest=self.expected_ack_evidence_digest,
            repository_owner=ack.repository_owner,
            repository_name=ack.repository_name,
            issue_number=ack.issue_number,
            comment_id=ack.comment_id,
            actor_login=ack.actor_login,
            actor_id=ack.actor_id,
        )
        if fresh != ack:
            raise ValueError("live GitHub execution acknowledgement changed")


def acknowledge_random_external_execution_v4(
    bound: BoundRandomExternalExecutionV4,
    authorization: RandomExternalExecutionAuthorizationV4,
    acknowledgement: GitHubExecutionAcknowledgementV4,
    *,
    expected_ack_evidence_digest: str,
) -> AcknowledgedRandomExternalExecutionV4:
    """Authorize only a pristine ledger already named by one live public comment."""

    if type(bound) is not BoundRandomExternalExecutionV4:
        raise ValueError("v4 acknowledgement requires an exact bound execution")
    bound.__post_init__()
    _verify_live_anchor(bound.prepared)
    ledger = bound.open_ledger()
    if any(ledger.state(slot.slot_id) is not SlotState.PENDING for slot in bound.prepared.slots):
        raise ValueError("v4 execution acknowledgement must precede every STARTED record")
    authorized = AcknowledgedRandomExternalExecutionV4(
        bound=bound,
        authorization=authorization,
        acknowledgement=acknowledgement,
        expected_ack_evidence_digest=expected_ack_evidence_digest,
    )
    authorized.verify_live()
    return authorized


@dataclass(frozen=True, slots=True)
class RandomExternalExecutionProgressV4:
    execution_public_record_digest: str
    ledger_digest: str
    pending_count: int
    started_without_terminal_count: int
    returned_count: int
    failed_count: int
    started_without_terminal_slot_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self) is not RandomExternalExecutionProgressV4:
            raise ValueError("v4 progress subclasses are forbidden")
        _digest(self.execution_public_record_digest, "v4 progress execution record")
        _digest(self.ledger_digest, "v4 progress ledger")
        counts = (
            self.pending_count,
            self.started_without_terminal_count,
            self.returned_count,
            self.failed_count,
        )
        if any(type(value) is not int or value < 0 for value in counts):
            raise ValueError("v4 progress counts must be nonnegative exact integers")
        if sum(counts) != RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4:
            raise ValueError("v4 progress must retain all 72 intention-to-treat slots")
        if (
            type(self.started_without_terminal_slot_ids) is not tuple
            or len(self.started_without_terminal_slot_ids) != self.started_without_terminal_count
            or tuple(sorted(set(self.started_without_terminal_slot_ids)))
            != self.started_without_terminal_slot_ids
            or any(
                _SLOT_ID_RE.fullmatch(value) is None
                for value in self.started_without_terminal_slot_ids
            )
        ):
            raise ValueError("v4 progress burned-slot universe is invalid")

    @property
    def attempt_universe_closed(self) -> bool:
        return self.pending_count == 0

    @property
    def fully_terminal(self) -> bool:
        return self.pending_count == 0 and self.started_without_terminal_count == 0

    def to_dict(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "attempt_universe_closed": self.attempt_universe_closed,
            "execution_public_record_digest": self.execution_public_record_digest,
            "failed_count": self.failed_count,
            "fully_terminal": self.fully_terminal,
            "ledger_digest": self.ledger_digest,
            "pending_count": self.pending_count,
            "returned_count": self.returned_count,
            "schema": "org.math-research-brain/random-external-execution-progress/v4",
            "schema_version": 4,
            "started_without_terminal_count": self.started_without_terminal_count,
            "started_without_terminal_slot_ids": list(self.started_without_terminal_slot_ids),
            "task_count": RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4,
        }


def random_external_execution_progress_v4(
    bound: BoundRandomExternalExecutionV4,
) -> RandomExternalExecutionProgressV4:
    if type(bound) is not BoundRandomExternalExecutionV4:
        raise ValueError("v4 progress requires exact bound execution state")
    ledger = bound.open_ledger()
    states = {slot.slot_id: ledger.state(slot.slot_id) for slot in bound.prepared.slots}
    burned = tuple(
        sorted(slot_id for slot_id, state in states.items() if state is SlotState.STARTED)
    )
    return RandomExternalExecutionProgressV4(
        execution_public_record_digest=bound.public_record_digest,
        ledger_digest=ledger.ledger_digest,
        pending_count=sum(state is SlotState.PENDING for state in states.values()),
        started_without_terminal_count=len(burned),
        returned_count=sum(state is SlotState.RETURNED for state in states.values()),
        failed_count=sum(state is SlotState.FAILED for state in states.values()),
        started_without_terminal_slot_ids=burned,
    )


class RandomExternalClosedDispositionV4(StrEnum):
    RETURNED = "returned"
    FAILED = "failed"
    BURNED_RUNNER_FAILURE = "burned_runner_failure"


def _burned_runner_failure_authenticator_v4(
    *,
    ledger_key: bytes,
    execution_public_record_digest: str,
    ledger_digest: str,
    ordinal: int,
    slot_id: str,
    started_record_digest: str,
) -> str:
    key = _secret(ledger_key, "v4 burned-runner closure key")
    body = canonical_json(
        {
            "disposition": RandomExternalClosedDispositionV4.BURNED_RUNNER_FAILURE.value,
            "execution_public_record_digest": execution_public_record_digest,
            "ledger_digest": ledger_digest,
            "ordinal": ordinal,
            "slot_id": slot_id,
            "started_record_digest": started_record_digest,
        }
    ).encode("utf-8")
    return (
        "hmac-sha256:"
        + hmac.new(
            key,
            _BURNED_CLOSURE_DOMAIN + body,
            hashlib.sha256,
        ).hexdigest()
    )


@dataclass(frozen=True, slots=True)
class RandomExternalClosedAttemptV4:
    ordinal: int
    slot_id: str
    slot_commitment_digest: str
    started: StartedAttempt
    selected_outcome: StoredOutcome | None
    terminal: TerminalTransition | None
    disposition: RandomExternalClosedDispositionV4
    burned_runner_failure_authenticator: str | None

    def __post_init__(self) -> None:
        if type(self) is not RandomExternalClosedAttemptV4:
            raise ValueError("v4 closed-attempt subclasses are forbidden")
        if type(self.ordinal) is not int or not (
            0 <= self.ordinal < RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4
        ):
            raise ValueError("v4 closed-attempt ordinal is invalid")
        if type(self.slot_id) is not str or _SLOT_ID_RE.fullmatch(self.slot_id) is None:
            raise ValueError("v4 closed-attempt slot id is invalid")
        _digest(self.slot_commitment_digest, "v4 closed-attempt slot commitment")
        if type(self.started) is not StartedAttempt:
            raise ValueError("v4 closed attempt requires an exact STARTED record")
        if (
            self.started.ordinal != self.ordinal
            or self.started.slot_id != self.slot_id
            or self.started.prerequisite_terminal_digest is not None
        ):
            raise ValueError("v4 closed attempt differs from its STARTED record")
        if type(self.disposition) is not RandomExternalClosedDispositionV4:
            raise ValueError("v4 closed-attempt disposition must be exact")
        if self.disposition is RandomExternalClosedDispositionV4.BURNED_RUNNER_FAILURE:
            if self.selected_outcome is not None or self.terminal is not None:
                raise ValueError("burned v4 runner failure cannot carry a selected outcome")
            if (
                type(self.burned_runner_failure_authenticator) is not str
                or _HMAC_RE.fullmatch(self.burned_runner_failure_authenticator) is None
            ):
                raise ValueError("burned v4 runner failure lacks its authenticator")
            return
        if (
            type(self.selected_outcome) is not StoredOutcome
            or type(self.terminal) is not TerminalTransition
            or self.burned_runner_failure_authenticator is not None
        ):
            raise ValueError("terminal v4 attempt requires exact outcome history")
        selected = self.selected_outcome
        terminal = self.terminal
        expected_kind = (
            OutcomeKind.RETURNED
            if self.disposition is RandomExternalClosedDispositionV4.RETURNED
            else OutcomeKind.FAILED
        )
        if (
            selected.ordinal != self.ordinal
            or terminal.ordinal != self.ordinal
            or selected.slot_id != self.slot_id
            or terminal.slot_id != self.slot_id
            or selected.started_record_digest != self.started.record_digest
            or selected.kind is not expected_kind
            or terminal.kind is not expected_kind
            or selected.object_digest != terminal.object_digest
        ):
            raise ValueError("terminal v4 attempt history is internally inconsistent")

    def commitment_record(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "burned_runner_failure_authenticator": (self.burned_runner_failure_authenticator),
            "disposition": self.disposition.value,
            "ledger_digest": self.started.ledger_digest,
            "ordinal": self.ordinal,
            "outcome_kind": (
                None if self.selected_outcome is None else self.selected_outcome.kind.value
            ),
            "outcome_media_type": (
                None if self.selected_outcome is None else self.selected_outcome.media_type
            ),
            "outcome_object_digest": (
                None if self.selected_outcome is None else self.selected_outcome.object_digest
            ),
            "selection_record_digest": (
                None
                if self.selected_outcome is None
                else self.selected_outcome.selection_record_digest
            ),
            "slot_commitment_digest": self.slot_commitment_digest,
            "slot_id": self.slot_id,
            "started_record_digest": self.started.record_digest,
            "terminal_record_digest": (
                None if self.terminal is None else self.terminal.record_digest
            ),
        }


@dataclass(frozen=True, slots=True)
class RandomExternalExecutionClosureV4:
    execution_public_record_digest: str
    acknowledgement_evidence_digest: str
    ledger_digest: str
    attempts: tuple[RandomExternalClosedAttemptV4, ...] = field(repr=False)
    attempt_universe_digest: str
    returned_count: int
    failed_count: int
    burned_runner_failure_count: int
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not RandomExternalExecutionClosureV4:
            raise ValueError("v4 execution-closure subclasses are forbidden")
        for value, label in (
            (self.execution_public_record_digest, "v4 closure execution record"),
            (self.acknowledgement_evidence_digest, "v4 closure acknowledgement"),
            (self.ledger_digest, "v4 closure ledger"),
            (self.attempt_universe_digest, "v4 closure attempt universe"),
        ):
            _digest(value, label)
        if (
            type(self.attempts) is not tuple
            or len(self.attempts) != RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4
            or any(type(attempt) is not RandomExternalClosedAttemptV4 for attempt in self.attempts)
        ):
            raise ValueError("v4 closure requires the exact ordered 72-attempt universe")
        for attempt in self.attempts:
            attempt.__post_init__()
        if tuple(attempt.ordinal for attempt in self.attempts) != tuple(
            range(RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4)
        ) or len({attempt.slot_id for attempt in self.attempts}) != len(self.attempts):
            raise ValueError("v4 closure attempt order or identity is invalid")
        expected_universe = _digest_json([attempt.commitment_record() for attempt in self.attempts])
        if not hmac.compare_digest(self.attempt_universe_digest, expected_universe):
            raise ValueError("v4 closure attempt-universe digest is stale")
        counts = (
            self.returned_count,
            self.failed_count,
            self.burned_runner_failure_count,
        )
        if any(type(value) is not int or value < 0 for value in counts) or sum(counts) != (
            RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4
        ):
            raise ValueError("v4 closure outcome counts must retain all 72 attempts")
        observed = (
            sum(
                attempt.disposition is RandomExternalClosedDispositionV4.RETURNED
                for attempt in self.attempts
            ),
            sum(
                attempt.disposition is RandomExternalClosedDispositionV4.FAILED
                for attempt in self.attempts
            ),
            sum(
                attempt.disposition is RandomExternalClosedDispositionV4.BURNED_RUNNER_FAILURE
                for attempt in self.attempts
            ),
        )
        if counts != observed:
            raise ValueError("v4 closure counts differ from the attempt universe")
        object.__setattr__(self, "content_digest", _digest_json(self._payload()))

    def _payload(self) -> dict[str, object]:
        return {
            "acknowledgement_evidence_digest": self.acknowledgement_evidence_digest,
            "attempt_count": len(self.attempts),
            "attempt_universe_digest": self.attempt_universe_digest,
            "burned_runner_failure_count": self.burned_runner_failure_count,
            "execution_public_record_digest": self.execution_public_record_digest,
            "failed_count": self.failed_count,
            "ledger_digest": self.ledger_digest,
            "returned_count": self.returned_count,
            "schema": "org.math-research-brain/random-external-execution-closure/v4",
            "schema_version": 4,
        }

    def commitment_record(self) -> dict[str, object]:
        self.__post_init__()
        return self._payload() | {"content_digest": self.content_digest}


def _closed_attempt_from_history_v4(
    record: AttemptHistoryRecord,
    *,
    bound: BoundRandomExternalExecutionV4,
) -> RandomExternalClosedAttemptV4:
    if type(record) is not AttemptHistoryRecord or type(record.started) is not StartedAttempt:
        raise ValueError("v4 closure cannot contain a pending attempt")
    slot = bound.prepared.slots[record.ordinal]
    if record.slot != slot.commitment:
        raise ValueError("v4 closure history differs from the sealed slot commitment")
    if record.terminal is None:
        if record.selected_outcome is not None:
            raise ValueError("v4 orphan recovery left a selected outcome unterminated")
        disposition = RandomExternalClosedDispositionV4.BURNED_RUNNER_FAILURE
        authenticator = _burned_runner_failure_authenticator_v4(
            ledger_key=bound.prepared.ledger_key,
            execution_public_record_digest=bound.public_record_digest,
            ledger_digest=bound.ledger_digest,
            ordinal=record.ordinal,
            slot_id=record.slot.slot_id,
            started_record_digest=record.started.record_digest,
        )
    else:
        if type(record.selected_outcome) is not StoredOutcome:
            raise ValueError("v4 terminal history lacks its selected durable outcome")
        disposition = (
            RandomExternalClosedDispositionV4.RETURNED
            if record.terminal.kind is OutcomeKind.RETURNED
            else RandomExternalClosedDispositionV4.FAILED
        )
        authenticator = None
    return RandomExternalClosedAttemptV4(
        ordinal=record.ordinal,
        slot_id=record.slot.slot_id,
        slot_commitment_digest=_digest_json(record.slot.to_dict(ordinal=record.ordinal)),
        started=record.started,
        selected_outcome=record.selected_outcome,
        terminal=record.terminal,
        disposition=disposition,
        burned_runner_failure_authenticator=authenticator,
    )


def _verify_closure_against_bound_v4(
    acknowledged: AcknowledgedRandomExternalExecutionV4,
    closure: RandomExternalExecutionClosureV4,
) -> None:
    bound = acknowledged.bound
    if not all(
        (
            hmac.compare_digest(
                closure.execution_public_record_digest,
                bound.public_record_digest,
            ),
            hmac.compare_digest(
                closure.acknowledgement_evidence_digest,
                acknowledged.expected_ack_evidence_digest,
            ),
            hmac.compare_digest(closure.ledger_digest, bound.ledger_digest),
        )
    ):
        raise ValueError("v4 closure differs from its acknowledged execution")
    for slot, attempt in zip(bound.prepared.slots, closure.attempts, strict=True):
        if attempt.slot_id != slot.slot_id or attempt.started.ledger_digest != bound.ledger_digest:
            raise ValueError("v4 closure attempt belongs to another ledger")
        if attempt.disposition is RandomExternalClosedDispositionV4.BURNED_RUNNER_FAILURE:
            expected = _burned_runner_failure_authenticator_v4(
                ledger_key=bound.prepared.ledger_key,
                execution_public_record_digest=bound.public_record_digest,
                ledger_digest=bound.ledger_digest,
                ordinal=attempt.ordinal,
                slot_id=attempt.slot_id,
                started_record_digest=attempt.started.record_digest,
            )
            if not hmac.compare_digest(
                attempt.burned_runner_failure_authenticator or "",
                expected,
            ):
                raise ValueError("v4 burned-runner closure authenticator is invalid")


def close_random_external_execution_v4(
    acknowledged: AcknowledgedRandomExternalExecutionV4,
    *,
    repository_root: str | Path,
) -> RandomExternalExecutionClosureV4:
    """Recover durable orphans and close one stable, locked 72-attempt snapshot."""

    if type(acknowledged) is not AcknowledgedRandomExternalExecutionV4:
        raise ValueError("v4 closure requires exact acknowledged execution")
    repository = Path(repository_root)
    if not repository.is_absolute():
        raise ValueError("v4 closure repository root must be absolute")
    repository = repository.resolve(strict=True)
    bound = acknowledged.bound
    ledger = bound.open_ledger()
    with _exclusive_execution_lock_v4(ledger.root):
        acknowledged.verify_live()
        _verify_live_anchor(bound.prepared)
        bound.prepared.policy.validate_current(repository_root=repository)
        ledger.recover_orphans()
        first = ledger.history_records()
        second = ledger.history_records()
        if first != second:
            raise ValueError("v4 ledger history changed during locked closure")
        attempts = tuple(_closed_attempt_from_history_v4(record, bound=bound) for record in first)
        universe_digest = _digest_json([attempt.commitment_record() for attempt in attempts])
        closure = RandomExternalExecutionClosureV4(
            execution_public_record_digest=bound.public_record_digest,
            acknowledgement_evidence_digest=acknowledged.expected_ack_evidence_digest,
            ledger_digest=bound.ledger_digest,
            attempts=attempts,
            attempt_universe_digest=universe_digest,
            returned_count=sum(
                attempt.disposition is RandomExternalClosedDispositionV4.RETURNED
                for attempt in attempts
            ),
            failed_count=sum(
                attempt.disposition is RandomExternalClosedDispositionV4.FAILED
                for attempt in attempts
            ),
            burned_runner_failure_count=sum(
                attempt.disposition is RandomExternalClosedDispositionV4.BURNED_RUNNER_FAILURE
                for attempt in attempts
            ),
        )
        _verify_closure_against_bound_v4(acknowledged, closure)
        return closure


def _run_one_desktop_attempt_v4(
    runtime_directory: Path,
    dispatch: DispatchMaterial,
    *,
    policy: RandomExternalExecutionPolicyV4,
    auth_file: Path,
    repository_root: Path,
) -> ArtifactPayload:
    policy.validate_current(repository_root=repository_root)
    try:
        decoded_schema = json.loads(dispatch.output_schema_json.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:  # pragma: no cover - prevalidated
        raise ValueError("v4 dispatch output schema is invalid") from error
    if type(decoded_schema) is not dict:
        raise ValueError("v4 dispatch output schema must be an exact object")
    config = CodexRunConfig(
        cwd=runtime_directory,
        model=policy.model,
        effort=policy.effort,
        model_provider=policy.model_provider,
        timeout_seconds=policy.timeout_seconds,
        executable=policy.executable_provenance.canonical_path,
        expected_executable_provenance=policy.executable_provenance,
        auth_file=auth_file,
        base_instructions=DEFAULT_BASE_INSTRUCTIONS,
        developer_instructions=DEFAULT_DEVELOPER_INSTRUCTIONS,
    )
    # Deliberately instantiate the exact runner here: the public execution API has no
    # caller-supplied process factory or retry callback.
    result = CodexAppServerRunner(config).run(
        prompt=dispatch.prompt,
        output_schema=decoded_schema,
    )
    result.require_eligible()
    policy.validate_current(repository_root=repository_root)
    if (
        result.prompt_text != dispatch.prompt
        or result.output_schema_json != dispatch.output_schema_json
        or result.requested_model != policy.model
        or result.requested_effort != policy.effort
        or result.requested_provider != policy.model_provider
        or result.executable_path != policy.executable_provenance.canonical_path
        or result.executable_size_bytes != policy.executable_provenance.size_bytes
        or result.executable_sha256_digest != policy.executable_provenance.sha256_digest
        or result.solver_process_isolation_policy_digest != policy.solver_isolation_policy_digest
        or result.standard_popen_used is not True
        or result.executable_lock_enforced is not True
        or result.codex_home_initial_auth_only is not True
        or result.codex_home_cleanup_verified is not True
        or result.credential_leak_scan_passed is not True
        or result.process_exit_code != 0
        or result.forced_termination is not False
        or result.audit.eligible is not True
        or result.audit.reroutes != ()
        or result.audit.model_identity_verified is not False
    ):
        raise ValueError("v4 desktop result differs from its sealed execution policy")
    return ArtifactPayload(
        serialize_codex_run_result(result),
        "application/vnd.math-research-brain.codex-run-result+json",
    )


@contextmanager
def _exclusive_execution_lock_v4(ledger_root: Path) -> Iterator[None]:
    """Hold one crash-releasing OS lock outside the strict ledger directory."""

    lock_path = ledger_root.parent / f".{ledger_root.name}.v4-execution.lock"
    if lock_path.is_symlink():
        raise ValueError("v4 execution lock cannot be a symlink")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\x00")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:  # pragma: no cover - production execution is Windows desktop
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        os.close(descriptor)
        raise RuntimeError("another v4 executor already holds the campaign lock") from error
    try:
        yield
    finally:
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:  # pragma: no cover - production execution is Windows desktop
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def execute_random_external_pilot_v4(
    acknowledged: AcknowledgedRandomExternalExecutionV4,
    *,
    runtime_parent: str | Path,
    repository_root: str | Path,
    auth_file: str | Path,
) -> RandomExternalExecutionProgressV4:
    """Run each still-PENDING slot once, sequentially, and never retry STARTED."""

    if type(acknowledged) is not AcknowledgedRandomExternalExecutionV4:
        raise ValueError("v4 execution requires exact externally acknowledged state")
    acknowledged.__post_init__()
    acknowledged.verify_live()
    bound = acknowledged.bound
    _verify_live_anchor(bound.prepared)
    policy = bound.prepared.policy
    runtime = Path(runtime_parent)
    repository = Path(repository_root)
    auth = Path(auth_file)
    if not runtime.is_absolute() or not repository.is_absolute() or not auth.is_absolute():
        raise ValueError("v4 runtime, repository, and auth paths must be absolute")
    runtime = runtime.resolve(strict=True)
    repository = repository.resolve(strict=True)
    auth = auth.resolve(strict=True)
    policy.validate_current(repository_root=repository)
    ledger = bound.open_ledger()

    def sealed_runner(runtime_directory: Path, dispatch: DispatchMaterial) -> ArtifactPayload:
        return _run_one_desktop_attempt_v4(
            runtime_directory,
            dispatch,
            policy=policy,
            auth_file=auth,
            repository_root=repository,
        )

    with _exclusive_execution_lock_v4(ledger.root):
        for slot in bound.prepared.slots:
            state = ledger.state(slot.slot_id)
            if state is not SlotState.PENDING:
                continue
            # Refetch both immutable public authorities and revalidate code immediately
            # before each irreversible STARTED transition in this many-hour campaign.
            acknowledged.verify_live()
            _verify_live_anchor(bound.prepared)
            policy.validate_current(repository_root=repository)
            try:
                ledger.execute_first_attempt(
                    slot.slot_id,
                    runtime_parent=runtime,
                    repository_root=repository,
                    dispatch=slot.dispatch,
                    runner=sealed_runner,
                )
            except AttemptRunnerFailed:
                # The ledger already persisted one FAILED transition.  Continue to the
                # next precommitted slot; there is never a retry of this slot.
                continue
    return random_external_execution_progress_v4(bound)


def terminal_records_for_random_external_scoring_v4(
    acknowledged: AcknowledgedRandomExternalExecutionV4,
    *,
    repository_root: str | Path,
) -> tuple[TerminalTransition | None, ...]:
    """Project terminals only from one live-verified locked closure."""

    closure = close_random_external_execution_v4(
        acknowledged,
        repository_root=repository_root,
    )
    return tuple(attempt.terminal for attempt in closure.attempts)


def closed_artifacts_for_random_external_scoring_v4(
    acknowledged: AcknowledgedRandomExternalExecutionV4,
    closure: RandomExternalExecutionClosureV4,
    *,
    repository_root: str | Path,
) -> tuple[ArtifactPayload | None, ...]:
    """Load payloads only after cold-rebuilding the identical closed manifest."""

    if type(closure) is not RandomExternalExecutionClosureV4:
        raise ValueError("v4 scoring artifacts require an exact closure")
    closure.__post_init__()
    rebuilt = close_random_external_execution_v4(
        acknowledged,
        repository_root=repository_root,
    )
    if rebuilt != closure:
        raise ValueError("v4 scoring closure differs from locked ledger replay")
    ledger = acknowledged.bound.open_ledger()
    return tuple(
        None
        if attempt.disposition is RandomExternalClosedDispositionV4.BURNED_RUNNER_FAILURE
        else ledger.load_selected_artifact(attempt.slot_id)
        for attempt in closure.attempts
    )


__all__ = [
    "RANDOM_EXTERNAL_EXECUTION_POLICY_SCHEMA_V4",
    "RANDOM_EXTERNAL_EXECUTION_SCHEMA_V4",
    "RANDOM_EXTERNAL_SOLVER_OUTPUT_SCHEMA_V4",
    "AcknowledgedRandomExternalExecutionV4",
    "BoundRandomExternalExecutionV4",
    "PreparedRandomExternalExecutionSlotV4",
    "PreparedRandomExternalExecutionV4",
    "RandomExternalClosedAttemptV4",
    "RandomExternalClosedDispositionV4",
    "RandomExternalExecutionClosureV4",
    "RandomExternalExecutionPolicyV4",
    "RandomExternalExecutionProgressV4",
    "acknowledge_random_external_execution_v4",
    "close_random_external_execution_v4",
    "closed_artifacts_for_random_external_scoring_v4",
    "create_random_external_execution_ledger_v4",
    "execute_random_external_pilot_v4",
    "freeze_random_external_execution_policy_v4",
    "prepare_random_external_execution_v4",
    "random_external_execution_progress_v4",
    "random_external_solver_output_schema_v4",
    "restore_random_external_execution_ledger_v4",
    "terminal_records_for_random_external_scoring_v4",
]
