"""Opaque task/scoring identity map for the random-external v4 pilot.

The v4 plan contains arm and provenance metadata that must never enter a solver prompt
or blind scoring capsule.  This module derives role-separated opaque identifiers for all
72 assignments and binds their private joins before any attempt reaches ``STARTED``.

The solver/output protocol remains the exact-rational v3 protocol, hence projected task
and score identifiers retain its ``solvertaskv3_`` and ``scorev3_`` grammars.  The map,
derivation domains, and public precommitment are separately versioned v4.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field

from .exact_answer_v3 import ExactRationalV3
from .models import canonical_json
from .three_arm_pilot_protocol_v3 import (
    ArmBlindScoreCapsuleV3,
    SolverTaskProjectionV3,
)
from .three_arm_random_external_pilot_v4 import (
    RANDOM_EXTERNAL_PILOT_CASE_COUNT_V4,
    RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4,
    RandomExternalPilotPlanV4,
)

RANDOM_EXTERNAL_TASK_MAP_SCHEMA_V4 = (
    "org.math-research-brain/random-external-pilot-task-map/v4"
)

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_HMAC_RE = re.compile(r"hmac-sha256:[0-9a-f]{64}\Z", re.ASCII)
_RESPONSE_ID_RE = re.compile(r"solvertaskv3_[0-9a-f]{64}\Z", re.ASCII)
_SCORE_ID_RE = re.compile(r"scorev3_[0-9a-f]{64}\Z", re.ASCII)
_SEED_DOMAIN = b"math-brain/random-external-task-map-v4/seed\x00"
_ID_DOMAIN = b"math-brain/random-external-task-map-v4/id\x00"
_COMMITMENT_DOMAIN = b"math-brain/random-external-task-map-v4/commitment\x00"


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _digest_json(value: object) -> str:
    return _sha256(canonical_json(value).encode("utf-8"))


def _digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical SHA-256 digest")
    return value


def _hmac_digest(value: object, label: str) -> str:
    if type(value) is not str or _HMAC_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical HMAC-SHA-256 commitment")
    return value


def _secret(value: object, label: str) -> bytes:
    if type(value) is not bytes or len(value) < 32:
        raise ValueError(f"{label} must contain at least 256 bits")
    return value


def _seed_commitment(seed: bytes, *, role: str, plan_digest: str) -> str:
    return _sha256(
        _SEED_DOMAIN
        + role.encode("ascii")
        + b"\x00"
        + plan_digest.encode("ascii")
        + b"\x00"
        + seed
    )


def _derived_id(
    seed: bytes,
    *,
    role: str,
    prefix: str,
    plan_digest: str,
    ordinal: int,
    private_task_id: str,
) -> str:
    material = (
        _ID_DOMAIN
        + role.encode("ascii")
        + b"\x00"
        + plan_digest.encode("ascii")
        + b"\x00"
        + ordinal.to_bytes(4, "big")
        + b"\x00"
        + private_task_id.encode("ascii")
    )
    return prefix + hmac.new(seed, material, hashlib.sha256).hexdigest()


def _hmac_json(key: bytes, *, role: str, value: object) -> str:
    material = (
        _COMMITMENT_DOMAIN
        + role.encode("ascii")
        + b"\x00"
        + canonical_json(value).encode("utf-8")
    )
    return "hmac-sha256:" + hmac.new(key, material, hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class RandomExternalTaskIdentityBindingV4:
    ordinal: int
    schedule_index: int
    private_task_id: str
    response_task_id: str
    score_id: str
    problem_digest: str
    context_digest: str

    def __post_init__(self) -> None:
        if type(self) is not RandomExternalTaskIdentityBindingV4:
            raise ValueError("v4 task-binding subclasses are forbidden")
        if type(self.ordinal) is not int or not (
            0 <= self.ordinal < RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4
        ):
            raise ValueError("v4 task-binding ordinal is invalid")
        if type(self.schedule_index) is not int or self.schedule_index != self.ordinal:
            raise ValueError("v4 task binding must preserve exact schedule order")
        _digest(self.private_task_id, "v4 private task id")
        if type(self.response_task_id) is not str or _RESPONSE_ID_RE.fullmatch(
            self.response_task_id
        ) is None:
            raise ValueError("v4 response task id violates the solver protocol")
        if type(self.score_id) is not str or _SCORE_ID_RE.fullmatch(self.score_id) is None:
            raise ValueError("v4 score id violates the scorer protocol")
        _digest(self.problem_digest, "v4 task-binding problem")
        _digest(self.context_digest, "v4 task-binding context")
        if self.private_task_id in {self.response_task_id, self.score_id}:
            raise ValueError("v4 private and projected task identifiers collided")

    def to_private_dict(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "context_digest": self.context_digest,
            "ordinal": self.ordinal,
            "private_task_id": self.private_task_id,
            "problem_digest": self.problem_digest,
            "response_task_id": self.response_task_id,
            "schedule_index": self.schedule_index,
            "score_id": self.score_id,
        }


@dataclass(frozen=True, slots=True)
class SealedRandomExternalTaskMapV4:
    plan_digest: str
    plan_public_precommitment_digest: str
    entries: tuple[RandomExternalTaskIdentityBindingV4, ...] = field(repr=False)
    response_seed_commitment: str
    score_seed_commitment: str
    mapping_commitment: str
    response_universe_commitment: str
    score_universe_commitment: str
    prestarted_publication_required: bool = True
    external_semantic_balance_claimed: bool = False
    curated_lane_pooled: bool = False
    chronology_authenticated_in_artifact: bool = False
    served_model_identity_verified: bool = False

    def __post_init__(self) -> None:
        if type(self) is not SealedRandomExternalTaskMapV4:
            raise ValueError("v4 task-map subclasses are forbidden")
        _digest(self.plan_digest, "v4 task-map plan")
        _digest(
            self.plan_public_precommitment_digest,
            "v4 task-map public plan precommitment",
        )
        _digest(self.response_seed_commitment, "v4 response seed commitment")
        _digest(self.score_seed_commitment, "v4 score seed commitment")
        for value, label in (
            (self.mapping_commitment, "mapping"),
            (self.response_universe_commitment, "response universe"),
            (self.score_universe_commitment, "score universe"),
        ):
            _hmac_digest(value, f"v4 {label} commitment")
        if type(self.entries) is not tuple or len(self.entries) != (
            RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4
        ):
            raise ValueError("v4 task map requires exactly 72 entries")
        if any(type(item) is not RandomExternalTaskIdentityBindingV4 for item in self.entries):
            raise ValueError("v4 task map contains an inexact binding")
        for item in self.entries:
            item.__post_init__()
        if tuple(item.ordinal for item in self.entries) != tuple(
            range(RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4)
        ):
            raise ValueError("v4 task map differs from exact schedule order")
        for label, values in (
            ("private task", tuple(item.private_task_id for item in self.entries)),
            ("response task", tuple(item.response_task_id for item in self.entries)),
            ("score", tuple(item.score_id for item in self.entries)),
        ):
            if len(set(values)) != RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4:
                raise ValueError(f"v4 task map reuses a {label} id")
        if hmac.compare_digest(
            self.response_seed_commitment, self.score_seed_commitment
        ):
            raise ValueError("v4 response and score seeds are not role-separated")
        if (
            self.prestarted_publication_required is not True
            or self.external_semantic_balance_claimed is not False
            or self.curated_lane_pooled is not False
            or self.chronology_authenticated_in_artifact is not False
            or self.served_model_identity_verified is not False
        ):
            raise ValueError("v4 task map overclaims its authority")

    def private_record(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "entries": [item.to_private_dict() for item in self.entries],
            "plan_digest": self.plan_digest,
            "plan_public_precommitment_digest": self.plan_public_precommitment_digest,
            "response_seed_commitment": self.response_seed_commitment,
            "schema": RANDOM_EXTERNAL_TASK_MAP_SCHEMA_V4,
            "score_seed_commitment": self.score_seed_commitment,
        }

    def public_precommitment(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "case_count": RANDOM_EXTERNAL_PILOT_CASE_COUNT_V4,
            "chronology_authenticated_in_artifact": (
                self.chronology_authenticated_in_artifact
            ),
            "curated_lane_pooled": self.curated_lane_pooled,
            "external_semantic_balance_claimed": self.external_semantic_balance_claimed,
            "mapping_commitment": self.mapping_commitment,
            "plan_digest": self.plan_digest,
            "plan_public_precommitment_digest": self.plan_public_precommitment_digest,
            "prestarted_publication_required": self.prestarted_publication_required,
            "response_seed_commitment": self.response_seed_commitment,
            "response_universe_commitment": self.response_universe_commitment,
            "schema": RANDOM_EXTERNAL_TASK_MAP_SCHEMA_V4,
            "score_seed_commitment": self.score_seed_commitment,
            "score_universe_commitment": self.score_universe_commitment,
            "served_model_identity_verified": self.served_model_identity_verified,
            "task_count": len(self.entries),
        }

    @property
    def public_precommitment_digest(self) -> str:
        return _digest_json(self.public_precommitment())

    def binding_at(self, ordinal: int) -> RandomExternalTaskIdentityBindingV4:
        if type(ordinal) is not int or not 0 <= ordinal < len(self.entries):
            raise ValueError("v4 task-map ordinal is invalid")
        return self.entries[ordinal]


def _validate_plan(plan: RandomExternalPilotPlanV4) -> None:
    if type(plan) is not RandomExternalPilotPlanV4:
        raise ValueError("v4 task map requires the exact random-external plan")
    plan.__post_init__()
    if len(plan.cases) != RANDOM_EXTERNAL_PILOT_CASE_COUNT_V4 or len(
        plan.assignments
    ) != RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4:
        raise ValueError("v4 task map requires exactly 24 cases and 72 assignments")


def build_sealed_random_external_task_map_v4(
    plan: RandomExternalPilotPlanV4,
    *,
    response_seed: bytes,
    score_seed: bytes,
    commitment_key: bytes,
) -> SealedRandomExternalTaskMapV4:
    """Seal all private joins before the one-shot attempt ledger is created."""

    _validate_plan(plan)
    response_secret = _secret(response_seed, "v4 response-id seed")
    score_secret = _secret(score_seed, "v4 score-id seed")
    commitment_secret = _secret(commitment_key, "v4 task-map commitment key")
    secrets = (response_secret, score_secret, commitment_secret)
    if any(
        hmac.compare_digest(left, right)
        for index, left in enumerate(secrets)
        for right in secrets[index + 1 :]
    ):
        raise ValueError("v4 task-map secrets must be role-separated")
    entries = tuple(
        RandomExternalTaskIdentityBindingV4(
            ordinal=ordinal,
            schedule_index=assignment.schedule_index,
            private_task_id=assignment.task_id,
            response_task_id=_derived_id(
                response_secret,
                role="response",
                prefix="solvertaskv3_",
                plan_digest=plan.digest,
                ordinal=ordinal,
                private_task_id=assignment.task_id,
            ),
            score_id=_derived_id(
                score_secret,
                role="score",
                prefix="scorev3_",
                plan_digest=plan.digest,
                ordinal=ordinal,
                private_task_id=assignment.task_id,
            ),
            problem_digest=assignment.problem_digest,
            context_digest=assignment.context_digest,
        )
        for ordinal, assignment in enumerate(plan.assignments)
    )
    private_entries = [item.to_private_dict() for item in entries]
    return SealedRandomExternalTaskMapV4(
        plan_digest=plan.digest,
        plan_public_precommitment_digest=plan.public_precommitment_digest,
        entries=entries,
        response_seed_commitment=_seed_commitment(
            response_secret, role="response", plan_digest=plan.digest
        ),
        score_seed_commitment=_seed_commitment(
            score_secret, role="score", plan_digest=plan.digest
        ),
        mapping_commitment=_hmac_json(
            commitment_secret,
            role="mapping",
            value={
                "entries": private_entries,
                "plan_digest": plan.digest,
                "plan_public_precommitment_digest": plan.public_precommitment_digest,
            },
        ),
        response_universe_commitment=_hmac_json(
            commitment_secret,
            role="response-universe",
            value=[item.response_task_id for item in entries],
        ),
        score_universe_commitment=_hmac_json(
            commitment_secret,
            role="score-universe",
            value=[item.score_id for item in entries],
        ),
    )


def verify_sealed_random_external_task_map_v4(
    task_map: SealedRandomExternalTaskMapV4,
    plan: RandomExternalPilotPlanV4,
    *,
    response_seed: bytes,
    score_seed: bytes,
    commitment_key: bytes,
    expected_public_precommitment_digest: str,
) -> None:
    """Cold-replay every private join and the external public-map anchor."""

    if type(task_map) is not SealedRandomExternalTaskMapV4:
        raise ValueError("v4 task-map replay requires the exact map")
    task_map.__post_init__()
    expected = _digest(
        expected_public_precommitment_digest,
        "expected v4 task-map public precommitment",
    )
    if not hmac.compare_digest(task_map.public_precommitment_digest, expected):
        raise ValueError("v4 task map differs from its external precommitment")
    rebuilt = build_sealed_random_external_task_map_v4(
        plan,
        response_seed=response_seed,
        score_seed=score_seed,
        commitment_key=commitment_key,
    )
    if not hmac.compare_digest(
        canonical_json(task_map.private_record()),
        canonical_json(rebuilt.private_record()),
    ) or not hmac.compare_digest(
        canonical_json(task_map.public_precommitment()),
        canonical_json(rebuilt.public_precommitment()),
    ):
        raise ValueError("v4 task map differs from deterministic cold replay")


def solver_projection_from_random_external_task_map_v4(
    task_map: SealedRandomExternalTaskMapV4,
    plan: RandomExternalPilotPlanV4,
    *,
    ordinal: int,
) -> SolverTaskProjectionV3:
    """Materialize the arm/provenance-free solver projection at one sealed ordinal."""

    if type(task_map) is not SealedRandomExternalTaskMapV4:
        raise ValueError("v4 solver projection requires the exact task map")
    task_map.__post_init__()
    _validate_plan(plan)
    if (
        not hmac.compare_digest(task_map.plan_digest, plan.digest)
        or not hmac.compare_digest(
            task_map.plan_public_precommitment_digest,
            plan.public_precommitment_digest,
        )
    ):
        raise ValueError("v4 task map belongs to another plan")
    binding = task_map.binding_at(ordinal)
    assignment = plan.assignments[ordinal]
    if not all(
        (
            binding.schedule_index == assignment.schedule_index,
            hmac.compare_digest(binding.private_task_id, assignment.task_id),
            hmac.compare_digest(binding.problem_digest, assignment.problem_digest),
            hmac.compare_digest(binding.context_digest, assignment.context_digest),
        )
    ):
        raise ValueError("v4 task binding differs from the sealed assignment")
    private_task = plan.solver_task(binding.private_task_id)
    return SolverTaskProjectionV3(
        task_id=binding.response_task_id,
        problem_text=private_task.problem_text,
        supporting_material=private_task.brain_context,
        problem_digest=private_task.problem_digest,
        supporting_material_digest=private_task.context_digest,
    )


def score_capsule_from_random_external_task_map_v4(
    task_map: SealedRandomExternalTaskMapV4,
    plan: RandomExternalPilotPlanV4,
    *,
    ordinal: int,
    response_text: str,
    expected_answer: ExactRationalV3,
) -> ArmBlindScoreCapsuleV3:
    """Create a blind scorer capsule with the precommitted opaque score identity."""

    projection = solver_projection_from_random_external_task_map_v4(
        task_map, plan, ordinal=ordinal
    )
    binding = task_map.binding_at(ordinal)
    return ArmBlindScoreCapsuleV3(
        score_id=binding.score_id,
        task_id=projection.task_id,
        problem_digest=projection.problem_digest,
        response_text=response_text,
        expected_answer=expected_answer,
    )


__all__ = [
    "RANDOM_EXTERNAL_TASK_MAP_SCHEMA_V4",
    "RandomExternalTaskIdentityBindingV4",
    "SealedRandomExternalTaskMapV4",
    "build_sealed_random_external_task_map_v4",
    "score_capsule_from_random_external_task_map_v4",
    "solver_projection_from_random_external_task_map_v4",
    "verify_sealed_random_external_task_map_v4",
]
