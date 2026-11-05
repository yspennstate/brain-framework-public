"""Sealed arm-private identity map for the 72-task engineering pilot.

The evaluation plan necessarily contains assignment identifiers derived from private arm
metadata.  Those identifiers must never be copied into a solver prompt or an arm-blind score
capsule.  This module derives two pseudorandom, role-separated identifier universes from
private 256-bit seeds and seals their one-to-one mapping to the plan before any attempt may
reach ``STARTED``.

The public projection contains only keyed commitments and counts.  It authenticates bytes
under caller-held secrets, but it does not itself prove publication chronology, provider
identity, or reviewer independence; the execution assembler must bind the published digest
to the attempt ledger before dispatch.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field

from .exact_answer_v3 import ExactRationalV3
from .models import canonical_json
from .three_arm_evaluation import V3_PILOT_CASE_COUNT, StudyStage, ThreeArmPlan
from .three_arm_pilot_protocol_v3 import (
    ArmBlindScoreCapsuleV3,
    SolverTaskProjectionV3,
)

PILOT_TASK_MAP_SCHEMA_V3 = "org.math-research-brain/pilot-task-map/v3"
PILOT_TASK_MAP_SCHEMA_VERSION_V3 = 1
PILOT_TASK_MAP_COUNT_V3 = V3_PILOT_CASE_COUNT * 3

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_HMAC_RE = re.compile(r"hmac-sha256:[0-9a-f]{64}\Z", re.ASCII)
_RESPONSE_ID_RE = re.compile(r"solvertaskv3_[0-9a-f]{64}\Z", re.ASCII)
_SCORE_ID_RE = re.compile(r"scorev3_[0-9a-f]{64}\Z", re.ASCII)


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


def _response_id(value: object) -> str:
    if type(value) is not str or _RESPONSE_ID_RE.fullmatch(value) is None:
        raise ValueError("pilot response task id is invalid")
    return value


def _score_id(value: object) -> str:
    if type(value) is not str or _SCORE_ID_RE.fullmatch(value) is None:
        raise ValueError("pilot score id is invalid")
    return value


def _seed_commitment(seed: bytes, *, role: str, plan_digest: str) -> str:
    material = (
        b"math-brain/pilot-task-map-v3/seed-commitment/"
        + role.encode("ascii")
        + b"\x00"
        + plan_digest.encode("ascii")
        + b"\x00"
        + seed
    )
    return _sha256(material)


def _derived_id(
    seed: bytes,
    *,
    role: str,
    prefix: str,
    plan_digest: str,
    ordinal: int,
    plan_task_id: str,
) -> str:
    material = (
        b"math-brain/pilot-task-map-v3/id/"
        + role.encode("ascii")
        + b"\x00"
        + plan_digest.encode("ascii")
        + b"\x00"
        + ordinal.to_bytes(4, "big")
        + b"\x00"
        + plan_task_id.encode("ascii")
    )
    return prefix + hmac.new(seed, material, hashlib.sha256).hexdigest()


def _hmac_json(key: bytes, *, role: str, value: object) -> str:
    material = (
        b"math-brain/pilot-task-map-v3/commitment/"
        + role.encode("ascii")
        + b"\x00"
        + canonical_json(value).encode("utf-8")
    )
    return "hmac-sha256:" + hmac.new(key, material, hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class PilotTaskIdentityBindingV3:
    """Private one-to-one join keys for one sealed plan assignment."""

    ordinal: int
    schedule_index: int
    plan_task_id: str
    response_task_id: str
    score_id: str
    problem_digest: str
    context_digest: str

    def __post_init__(self) -> None:
        if type(self) is not PilotTaskIdentityBindingV3:
            raise ValueError("pilot task-binding subclasses are forbidden")
        if type(self.ordinal) is not int or not 0 <= self.ordinal < PILOT_TASK_MAP_COUNT_V3:
            raise ValueError("pilot task-binding ordinal is invalid")
        if type(self.schedule_index) is not int or self.schedule_index != self.ordinal:
            raise ValueError("pilot task-binding must preserve the exact schedule order")
        _digest(self.plan_task_id, "pilot private plan task id")
        _response_id(self.response_task_id)
        _score_id(self.score_id)
        _digest(self.problem_digest, "pilot task-binding problem digest")
        _digest(self.context_digest, "pilot task-binding context digest")
        if self.plan_task_id in {self.response_task_id, self.score_id}:
            raise ValueError("pilot private and projected identifiers collided")

    def to_private_dict(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "context_digest": self.context_digest,
            "ordinal": self.ordinal,
            "plan_task_id": self.plan_task_id,
            "problem_digest": self.problem_digest,
            "response_task_id": self.response_task_id,
            "schedule_index": self.schedule_index,
            "score_id": self.score_id,
        }


@dataclass(frozen=True, slots=True)
class SealedPilotTaskMapV3:
    """Complete private map plus an arm-free public precommitment."""

    plan_digest: str
    entries: tuple[PilotTaskIdentityBindingV3, ...] = field(repr=False)
    response_seed_commitment: str
    score_seed_commitment: str
    mapping_commitment: str
    response_universe_commitment: str
    score_universe_commitment: str
    engineering_pilot_only: bool = True
    prestarted_publication_required: bool = True
    chronology_authenticated_in_artifact: bool = False
    served_model_identity_verified: bool = False

    def __post_init__(self) -> None:
        if type(self) is not SealedPilotTaskMapV3:
            raise ValueError("pilot task-map subclasses are forbidden")
        _digest(self.plan_digest, "pilot task-map plan digest")
        _digest(self.response_seed_commitment, "pilot response-seed commitment")
        _digest(self.score_seed_commitment, "pilot score-seed commitment")
        for label, value in (
            ("mapping", self.mapping_commitment),
            ("response universe", self.response_universe_commitment),
            ("score universe", self.score_universe_commitment),
        ):
            _hmac_digest(value, f"pilot {label} commitment")
        if type(self.entries) is not tuple or len(self.entries) != PILOT_TASK_MAP_COUNT_V3:
            raise ValueError("pilot task map requires exactly 72 entries")
        if any(type(entry) is not PilotTaskIdentityBindingV3 for entry in self.entries):
            raise ValueError("pilot task map requires exact binding records")
        for entry in self.entries:
            entry.__post_init__()
        if tuple(entry.ordinal for entry in self.entries) != tuple(range(PILOT_TASK_MAP_COUNT_V3)):
            raise ValueError("pilot task map differs from exact schedule order")
        for label, values in (
            ("plan task", tuple(entry.plan_task_id for entry in self.entries)),
            ("response task", tuple(entry.response_task_id for entry in self.entries)),
            ("score", tuple(entry.score_id for entry in self.entries)),
        ):
            if len(set(values)) != PILOT_TASK_MAP_COUNT_V3:
                raise ValueError(f"pilot task map reuses a {label} id")
        if hmac.compare_digest(self.response_seed_commitment, self.score_seed_commitment):
            raise ValueError("pilot response and score seeds are not role-separated")
        if (
            self.engineering_pilot_only is not True
            or self.prestarted_publication_required is not True
            or self.chronology_authenticated_in_artifact is not False
            or self.served_model_identity_verified is not False
        ):
            raise ValueError("pilot task map overclaims its evidentiary authority")

    def private_record(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "entries": [entry.to_private_dict() for entry in self.entries],
            "plan_digest": self.plan_digest,
            "response_seed_commitment": self.response_seed_commitment,
            "schema": PILOT_TASK_MAP_SCHEMA_V3,
            "schema_version": PILOT_TASK_MAP_SCHEMA_VERSION_V3,
            "score_seed_commitment": self.score_seed_commitment,
        }

    def public_precommitment(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "chronology_authenticated_in_artifact": self.chronology_authenticated_in_artifact,
            "engineering_pilot_only": self.engineering_pilot_only,
            "mapping_commitment": self.mapping_commitment,
            "plan_digest": self.plan_digest,
            "prestarted_publication_required": self.prestarted_publication_required,
            "response_seed_commitment": self.response_seed_commitment,
            "response_universe_commitment": self.response_universe_commitment,
            "schema": PILOT_TASK_MAP_SCHEMA_V3,
            "schema_version": PILOT_TASK_MAP_SCHEMA_VERSION_V3,
            "score_seed_commitment": self.score_seed_commitment,
            "score_universe_commitment": self.score_universe_commitment,
            "served_model_identity_verified": self.served_model_identity_verified,
            "task_count": len(self.entries),
        }

    @property
    def public_precommitment_digest(self) -> str:
        return _digest_json(self.public_precommitment())

    def binding_at(self, ordinal: int) -> PilotTaskIdentityBindingV3:
        if type(ordinal) is not int or not 0 <= ordinal < len(self.entries):
            raise ValueError("pilot task-map ordinal is invalid")
        return self.entries[ordinal]


def _validate_plan_v3(plan: ThreeArmPlan) -> None:
    if type(plan) is not ThreeArmPlan:
        raise ValueError("pilot task map requires an exact ThreeArmPlan")
    plan.__post_init__()
    if plan.config.stage is not StudyStage.PILOT:
        raise ValueError("pilot task map cannot bind a confirmatory plan")
    if len(plan.cases) != V3_PILOT_CASE_COUNT or len(plan.assignments) != (PILOT_TASK_MAP_COUNT_V3):
        raise ValueError("pilot task map requires exactly 24 cases and 72 assignments")


def build_sealed_pilot_task_map_v3(
    plan: ThreeArmPlan,
    *,
    response_seed: bytes,
    score_seed: bytes,
    commitment_key: bytes,
) -> SealedPilotTaskMapV3:
    """Build all 72 private joins before any ledger slot may be started."""

    _validate_plan_v3(plan)
    response_secret = _secret(response_seed, "pilot response-id seed")
    score_secret = _secret(score_seed, "pilot score-id seed")
    commitment_secret = _secret(commitment_key, "pilot task-map commitment key")
    secrets = (response_secret, score_secret, commitment_secret)
    if any(
        hmac.compare_digest(left, right)
        for index, left in enumerate(secrets)
        for right in secrets[index + 1 :]
    ):
        raise ValueError("pilot task-map secrets must be role-separated")

    entries = tuple(
        PilotTaskIdentityBindingV3(
            ordinal=ordinal,
            schedule_index=assignment.schedule_index,
            plan_task_id=assignment.task_id,
            response_task_id=_derived_id(
                response_secret,
                role="response",
                prefix="solvertaskv3_",
                plan_digest=plan.digest,
                ordinal=ordinal,
                plan_task_id=assignment.task_id,
            ),
            score_id=_derived_id(
                score_secret,
                role="score",
                prefix="scorev3_",
                plan_digest=plan.digest,
                ordinal=ordinal,
                plan_task_id=assignment.task_id,
            ),
            problem_digest=assignment.problem_digest,
            context_digest=assignment.context_digest,
        )
        for ordinal, assignment in enumerate(plan.assignments)
    )
    private_entries = [entry.to_private_dict() for entry in entries]
    response_ids = [entry.response_task_id for entry in entries]
    score_ids = [entry.score_id for entry in entries]
    return SealedPilotTaskMapV3(
        plan_digest=plan.digest,
        entries=entries,
        response_seed_commitment=_seed_commitment(
            response_secret,
            role="response",
            plan_digest=plan.digest,
        ),
        score_seed_commitment=_seed_commitment(
            score_secret,
            role="score",
            plan_digest=plan.digest,
        ),
        mapping_commitment=_hmac_json(
            commitment_secret,
            role="mapping",
            value={"entries": private_entries, "plan_digest": plan.digest},
        ),
        response_universe_commitment=_hmac_json(
            commitment_secret,
            role="response-universe",
            value=response_ids,
        ),
        score_universe_commitment=_hmac_json(
            commitment_secret,
            role="score-universe",
            value=score_ids,
        ),
    )


def verify_sealed_pilot_task_map_v3(
    task_map: SealedPilotTaskMapV3,
    plan: ThreeArmPlan,
    *,
    response_seed: bytes,
    score_seed: bytes,
    commitment_key: bytes,
    expected_public_precommitment_digest: str,
) -> None:
    """Cold-replay every private join and the externally expected public digest."""

    if type(task_map) is not SealedPilotTaskMapV3:
        raise ValueError("pilot task-map replay requires an exact sealed map")
    task_map.__post_init__()
    expected_public = _digest(
        expected_public_precommitment_digest,
        "expected pilot task-map precommitment digest",
    )
    if not hmac.compare_digest(task_map.public_precommitment_digest, expected_public):
        raise ValueError("pilot task map differs from the expected public precommitment")
    rebuilt = build_sealed_pilot_task_map_v3(
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
        raise ValueError("pilot task map differs from deterministic cold replay")


def solver_projection_from_sealed_map_v3(
    task_map: SealedPilotTaskMapV3,
    plan: ThreeArmPlan,
    *,
    ordinal: int,
) -> SolverTaskProjectionV3:
    """Materialize a solver-safe projection without minting or exposing an arm identifier."""

    if type(task_map) is not SealedPilotTaskMapV3:
        raise ValueError("solver projection requires an exact sealed task map")
    task_map.__post_init__()
    _validate_plan_v3(plan)
    if not hmac.compare_digest(task_map.plan_digest, plan.digest):
        raise ValueError("pilot task map belongs to another plan")
    binding = task_map.binding_at(ordinal)
    assignment = plan.assignments[ordinal]
    expected = (
        binding.schedule_index == assignment.schedule_index,
        hmac.compare_digest(binding.plan_task_id, assignment.task_id),
        hmac.compare_digest(binding.problem_digest, assignment.problem_digest),
        hmac.compare_digest(binding.context_digest, assignment.context_digest),
    )
    if not all(expected):
        raise ValueError("pilot task binding differs from the sealed assignment")
    private_task = plan.solver_task(binding.plan_task_id)
    return SolverTaskProjectionV3(
        task_id=binding.response_task_id,
        problem_text=private_task.problem_text,
        supporting_material=private_task.brain_context,
        problem_digest=private_task.problem_digest,
        supporting_material_digest=private_task.context_digest,
    )


def score_capsule_from_sealed_map_v3(
    task_map: SealedPilotTaskMapV3,
    plan: ThreeArmPlan,
    *,
    ordinal: int,
    response_text: str,
    expected_answer: ExactRationalV3,
) -> ArmBlindScoreCapsuleV3:
    """Create the scorer view with the score ID fixed by the prestarted private map."""

    projection = solver_projection_from_sealed_map_v3(task_map, plan, ordinal=ordinal)
    binding = task_map.binding_at(ordinal)
    return ArmBlindScoreCapsuleV3(
        score_id=binding.score_id,
        task_id=projection.task_id,
        problem_digest=projection.problem_digest,
        response_text=response_text,
        expected_answer=expected_answer,
    )


__all__ = [
    "PILOT_TASK_MAP_COUNT_V3",
    "PILOT_TASK_MAP_SCHEMA_V3",
    "PilotTaskIdentityBindingV3",
    "SealedPilotTaskMapV3",
    "build_sealed_pilot_task_map_v3",
    "score_capsule_from_sealed_map_v3",
    "solver_projection_from_sealed_map_v3",
    "verify_sealed_pilot_task_map_v3",
]
