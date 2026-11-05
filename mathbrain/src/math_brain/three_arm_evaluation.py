"""Pure, sealed three-arm evaluation primitives for frozen brain retrieval.

The intervention is intentionally narrower than unrestricted live-brain access.  A fresh
solver receives either a relevant frozen math packet, a matched relevance-disjoint packet,
or an empty context.  This module has no filesystem, runner, ledger, retrieval, or gold-data
access; it only commits the solver-visible universe, allocates opaque one-shot tasks, and
summarizes already-scored first attempts.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import random
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from itertools import permutations
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .models import canonical_json

V3_CONFIRMATORY_CASE_COUNT = 96
V3_CONFIRMATORY_SOURCE_CASE_COUNT = 48
V3_CONFIRMATORY_DOMAIN_COUNT = 4
V3_PILOT_CASE_COUNT = 24
V3_BOOTSTRAP_REPETITIONS = 4096
V3_MINIMUM_EFFECT = 0.05
V3_ALPHA = 0.05
V3_EMPTY_RATE_FLOOR = 0.30
V3_EMPTY_RATE_CEILING = 0.85
V3_MAX_SUPPORTED_STRATUM_REGRESSION = 0.05

_DIGEST_PREFIX = "sha256:"
_DIGEST_LENGTH = len(_DIGEST_PREFIX) + 64
_SIGNATURE_PREFIX = "ed25519:"
_SIGNATURE_LENGTH = len(_SIGNATURE_PREFIX) + 128
_MAX_TEXT_BYTES = 256_000
_ARMS_PER_CASE = 3


def _sha256_bytes(value: bytes) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(value).hexdigest()


def _digest_json(value: Any) -> str:
    return _sha256_bytes(canonical_json(value).encode("utf-8"))


def _digest(value: Any, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != _DIGEST_LENGTH
        or not value.startswith(_DIGEST_PREFIX)
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{label} must be canonical sha256:<lowercase hex>")
    return value


def _signature(value: Any, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != _SIGNATURE_LENGTH
        or not value.startswith(_SIGNATURE_PREFIX)
        or any(character not in "0123456789abcdef" for character in value[8:])
    ):
        raise ValueError(f"{label} must be canonical ed25519:<lowercase hex>")
    return value


def _text(
    value: Any,
    label: str,
    *,
    maximum_bytes: int = 4096,
    allow_empty: bool = False,
) -> str:
    if type(value) is not str:
        raise ValueError(f"{label} must be a string")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} must be valid UTF-8") from error
    if len(encoded) > maximum_bytes:
        raise ValueError(f"{label} exceeds its byte budget")
    if not allow_empty and not value.strip():
        raise ValueError(f"{label} must not be empty")
    return value


def _sorted_unique_texts(
    values: Any,
    label: str,
    *,
    maximum_items: int = 128,
) -> tuple[str, ...]:
    if type(values) is not tuple or not 1 <= len(values) <= maximum_items:
        raise ValueError(f"{label} must be a nonempty bounded tuple")
    for value in values:
        _text(value, label, maximum_bytes=256)
    if tuple(sorted(set(values))) != values:
        raise ValueError(f"{label} must be unique and sorted")
    return values


def _seed(value: Any, label: str) -> bytes:
    if type(value) is not bytes or len(value) < 32:
        raise ValueError(f"{label} must contain at least 256 bits")
    return value


def _rank(seed: bytes, domain: str, *parts: object) -> bytes:
    material = bytearray(b"math-brain/three-arm-v3\x00")
    for item in (domain, *parts):
        encoded = str(item).encode("utf-8")
        material.extend(len(encoded).to_bytes(4, "big"))
        material.extend(encoded)
    return hmac.new(seed, bytes(material), hashlib.sha256).digest()


def _seed_commitment(seed: bytes, universe_digest: str) -> str:
    return _sha256_bytes(
        b"math-brain/three-arm-v3/seed\x00"
        + universe_digest.encode("ascii")
        + b"\x00"
        + seed
    )


class StudyArm(StrEnum):
    BRAIN = "brain"
    SHAM = "sham"
    EMPTY = "empty"


class ProblemSource(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class MathDomain(StrEnum):
    ALGEBRA = "algebra"
    NUMBER_THEORY = "number_theory"
    DISCRETE_MATHEMATICS = "discrete_mathematics"
    GEOMETRY = "geometry"


class DifficultyBand(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class StudyStage(StrEnum):
    PILOT = "pilot"
    CONFIRMATORY = "confirmatory"


@dataclass(frozen=True, slots=True)
class TokenizerReceipt:
    tokenizer_id: str
    tokenizer_artifact_digest: str
    token_count: int
    token_ids_digest: str

    def __post_init__(self) -> None:
        _text(self.tokenizer_id, "tokenizer id", maximum_bytes=128)
        _digest(self.tokenizer_artifact_digest, "tokenizer artifact digest")
        if type(self.token_count) is not int or self.token_count <= 0:
            raise ValueError("token count must be a positive exact integer")
        _digest(self.token_ids_digest, "token ids digest")

    def matching_key(self) -> tuple[str, str, int]:
        return self.tokenizer_id, self.tokenizer_artifact_digest, self.token_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_count": self.token_count,
            "token_ids_digest": self.token_ids_digest,
            "tokenizer_artifact_digest": self.tokenizer_artifact_digest,
            "tokenizer_id": self.tokenizer_id,
        }


@dataclass(frozen=True, slots=True)
class FrozenPacket:
    """Evaluator-side receipt for one rendered nonempty packet."""

    packet_id: str
    origin_case_id: str
    text: str = field(repr=False)
    snapshot_manifest_digest: str
    retrieval_policy_digest: str
    query_digest: str
    format_template_digest: str
    section_shape_digest: str
    age_status_vector_digest: str
    source_digests: tuple[str, ...]
    relevance_keys: tuple[str, ...]
    tokenizer_receipts: tuple[TokenizerReceipt, ...]

    def __post_init__(self) -> None:
        _text(self.packet_id, "packet id", maximum_bytes=128)
        _text(self.origin_case_id, "packet origin case id", maximum_bytes=128)
        _text(self.text, "packet text", maximum_bytes=_MAX_TEXT_BYTES)
        for label, value in (
            ("snapshot manifest digest", self.snapshot_manifest_digest),
            ("retrieval policy digest", self.retrieval_policy_digest),
            ("query digest", self.query_digest),
            ("format template digest", self.format_template_digest),
            ("section shape digest", self.section_shape_digest),
            ("age/status vector digest", self.age_status_vector_digest),
        ):
            _digest(value, label)
        _sorted_unique_texts(self.source_digests, "packet source digests")
        for source_digest in self.source_digests:
            _digest(source_digest, "packet source digest")
        _sorted_unique_texts(self.relevance_keys, "packet relevance keys")
        if type(self.tokenizer_receipts) is not tuple or not self.tokenizer_receipts:
            raise ValueError("packet requires at least one tokenizer receipt")
        if any(type(receipt) is not TokenizerReceipt for receipt in self.tokenizer_receipts):
            raise ValueError("packet tokenizer receipts must be exact records")
        identities = tuple(
            (receipt.tokenizer_id, receipt.tokenizer_artifact_digest)
            for receipt in self.tokenizer_receipts
        )
        if tuple(sorted(set(identities))) != identities:
            raise ValueError("packet tokenizer receipts must be unique and sorted")

    @property
    def text_digest(self) -> str:
        return _sha256_bytes(self.text.encode("utf-8"))

    @property
    def byte_length(self) -> int:
        return len(self.text.encode("utf-8"))

    @property
    def matching_key(self) -> tuple[Any, ...]:
        return (
            self.byte_length,
            tuple(receipt.matching_key() for receipt in self.tokenizer_receipts),
            self.format_template_digest,
            self.section_shape_digest,
            self.age_status_vector_digest,
        )

    def commitment_record(self) -> dict[str, Any]:
        return {
            "age_status_vector_digest": self.age_status_vector_digest,
            "byte_length": self.byte_length,
            "format_template_digest": self.format_template_digest,
            "origin_case_id": self.origin_case_id,
            "packet_id": self.packet_id,
            "query_digest": self.query_digest,
            "relevance_keys": list(self.relevance_keys),
            "retrieval_policy_digest": self.retrieval_policy_digest,
            "section_shape_digest": self.section_shape_digest,
            "snapshot_manifest_digest": self.snapshot_manifest_digest,
            "source_digests": list(self.source_digests),
            "text_digest": self.text_digest,
            "tokenizer_receipts": [receipt.to_dict() for receipt in self.tokenizer_receipts],
        }


@dataclass(frozen=True, slots=True)
class ProblemCase:
    """Gold-free public case record; private answers never enter this module."""

    case_id: str
    source: ProblemSource
    domain: MathDomain
    difficulty: DifficultyBand
    independence_cluster_id: str
    problem_text: str = field(repr=False)
    scorer_spec_digest: str
    brain_packet_id: str
    sham_packet_id: str
    required_relevance_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.case_id, "case id", maximum_bytes=128)
        if type(self.source) is not ProblemSource:
            raise ValueError("case source must be an exact ProblemSource")
        if type(self.domain) is not MathDomain:
            raise ValueError("case domain must be an exact MathDomain")
        if type(self.difficulty) is not DifficultyBand:
            raise ValueError("case difficulty must be an exact DifficultyBand")
        _text(self.independence_cluster_id, "independence cluster id", maximum_bytes=128)
        _text(self.problem_text, "problem text", maximum_bytes=128_000)
        _digest(self.scorer_spec_digest, "case scorer specification digest")
        _text(self.brain_packet_id, "case brain packet id", maximum_bytes=128)
        _text(self.sham_packet_id, "case sham packet id", maximum_bytes=128)
        if self.brain_packet_id == self.sham_packet_id:
            raise ValueError("brain and sham packet ids must differ")
        _sorted_unique_texts(self.required_relevance_keys, "required relevance keys")

    @property
    def problem_digest(self) -> str:
        return _sha256_bytes(self.problem_text.encode("utf-8"))

    def commitment_record(self) -> dict[str, Any]:
        return {
            "brain_packet_id": self.brain_packet_id,
            "case_id": self.case_id,
            "difficulty": self.difficulty.value,
            "domain": self.domain.value,
            "independence_cluster_id": self.independence_cluster_id,
            "problem_digest": self.problem_digest,
            "required_relevance_keys": list(self.required_relevance_keys),
            "scorer_spec_digest": self.scorer_spec_digest,
            "sham_packet_id": self.sham_packet_id,
            "source": self.source.value,
        }


@dataclass(frozen=True, slots=True)
class ThreeArmConfig:
    stage: StudyStage
    runtime_policy_digest: str
    prompt_template_digest: str
    response_schema_digest: str
    scorer_manifest_digest: str
    chronology_policy_digest: str
    scoring_authority_commitment: str
    audit_authority_commitment: str
    analysis_authority_commitment: str
    bootstrap_repetitions: int = V3_BOOTSTRAP_REPETITIONS
    minimum_effect: float = V3_MINIMUM_EFFECT
    alpha: float = V3_ALPHA

    def __post_init__(self) -> None:
        if type(self.stage) is not StudyStage:
            raise ValueError("study stage must be exact")
        for label, value in (
            ("runtime policy digest", self.runtime_policy_digest),
            ("prompt template digest", self.prompt_template_digest),
            ("response schema digest", self.response_schema_digest),
            ("scorer manifest digest", self.scorer_manifest_digest),
            ("chronology policy digest", self.chronology_policy_digest),
            ("scoring authority commitment", self.scoring_authority_commitment),
            ("audit authority commitment", self.audit_authority_commitment),
            ("analysis authority commitment", self.analysis_authority_commitment),
        ):
            _digest(value, label)
        if self.bootstrap_repetitions != V3_BOOTSTRAP_REPETITIONS:
            raise ValueError("v3 requires exactly 4,096 bootstrap repetitions")
        if type(self.minimum_effect) is not float or self.minimum_effect != V3_MINIMUM_EFFECT:
            raise ValueError("v3 minimum effect is sealed at 0.05")
        if type(self.alpha) is not float or self.alpha != V3_ALPHA:
            raise ValueError("v3 alpha is sealed at 0.05")

    @property
    def digest(self) -> str:
        return _digest_json(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "alpha": self.alpha,
            "analysis_authority_commitment": self.analysis_authority_commitment,
            "audit_authority_commitment": self.audit_authority_commitment,
            "bootstrap_repetitions": self.bootstrap_repetitions,
            "chronology_policy_digest": self.chronology_policy_digest,
            "minimum_effect": self.minimum_effect,
            "prompt_template_digest": self.prompt_template_digest,
            "response_schema_digest": self.response_schema_digest,
            "runtime_policy_digest": self.runtime_policy_digest,
            "scoring_authority_commitment": self.scoring_authority_commitment,
            "scorer_manifest_digest": self.scorer_manifest_digest,
            "stage": self.stage.value,
        }


@dataclass(frozen=True, slots=True)
class ArmAssignment:
    case_id: str
    arm: StudyArm
    task_id: str
    packet_id: str | None
    within_case_order: int
    schedule_index: int
    problem_digest: str
    context_digest: str

    def __post_init__(self) -> None:
        _text(self.case_id, "assignment case id", maximum_bytes=128)
        if type(self.arm) is not StudyArm:
            raise ValueError("assignment arm must be exact")
        _digest(self.task_id, "assignment task id")
        if self.packet_id is not None:
            _text(self.packet_id, "assignment packet id", maximum_bytes=128)
        if type(self.within_case_order) is not int or self.within_case_order not in {0, 1, 2}:
            raise ValueError("within-case order must be 0, 1, or 2")
        if type(self.schedule_index) is not int or self.schedule_index < 0:
            raise ValueError("schedule index must be nonnegative")
        _digest(self.problem_digest, "assignment problem digest")
        _digest(self.context_digest, "assignment context digest")

    @property
    def digest(self) -> str:
        return _digest_json(self.commitment_record())

    def commitment_record(self) -> dict[str, Any]:
        return {
            "arm": self.arm.value,
            "case_id": self.case_id,
            "context_digest": self.context_digest,
            "packet_id": self.packet_id,
            "problem_digest": self.problem_digest,
            "schedule_index": self.schedule_index,
            "task_id": self.task_id,
            "within_case_order": self.within_case_order,
        }


@dataclass(frozen=True, slots=True)
class SolverTask:
    task_id: str
    problem_text: str = field(repr=False)
    brain_context: str = field(repr=False)
    problem_digest: str
    context_digest: str

    def __post_init__(self) -> None:
        _digest(self.task_id, "solver task id")
        _text(self.problem_text, "solver problem", maximum_bytes=128_000)
        _text(
            self.brain_context,
            "solver brain context",
            maximum_bytes=_MAX_TEXT_BYTES,
            allow_empty=True,
        )
        _digest(self.problem_digest, "solver problem digest")
        _digest(self.context_digest, "solver context digest")
        if _sha256_bytes(self.problem_text.encode("utf-8")) != self.problem_digest:
            raise ValueError("solver problem differs from its digest")
        if _sha256_bytes(self.brain_context.encode("utf-8")) != self.context_digest:
            raise ValueError("solver context differs from its digest")

    def to_dict(self) -> dict[str, Any]:
        return {
            "brain_context": self.brain_context,
            "problem_text": self.problem_text,
            "task_id": self.task_id,
        }


@dataclass(frozen=True, slots=True)
class ThreeArmPlan:
    config: ThreeArmConfig
    cases: tuple[ProblemCase, ...] = field(repr=False)
    packets: tuple[FrozenPacket, ...] = field(repr=False)
    assignments: tuple[ArmAssignment, ...] = field(repr=False)
    case_universe_digest: str
    packet_universe_digest: str
    allocation_seed_commitment: str
    schedule_digest: str

    def __post_init__(self) -> None:
        if type(self.config) is not ThreeArmConfig:
            raise ValueError("plan config must be exact")
        if type(self.cases) is not tuple or type(self.packets) is not tuple:
            raise ValueError("plan universes must be canonical tuples")
        if type(self.assignments) is not tuple:
            raise ValueError("plan assignments must be a canonical tuple")
        for label, value in (
            ("case universe digest", self.case_universe_digest),
            ("packet universe digest", self.packet_universe_digest),
            ("allocation seed commitment", self.allocation_seed_commitment),
            ("schedule digest", self.schedule_digest),
        ):
            _digest(value, label)
        if _digest_json([case.commitment_record() for case in self.cases]) != (
            self.case_universe_digest
        ):
            raise ValueError("case universe differs from its commitment")
        if _digest_json([packet.commitment_record() for packet in self.packets]) != (
            self.packet_universe_digest
        ):
            raise ValueError("packet universe differs from its commitment")
        if _digest_json([item.commitment_record() for item in self.assignments]) != (
            self.schedule_digest
        ):
            raise ValueError("assignment schedule differs from its commitment")
        expected = len(self.cases) * _ARMS_PER_CASE
        if len(self.assignments) != expected:
            raise ValueError("plan must contain exactly three assignments per case")
        if tuple(item.schedule_index for item in self.assignments) != tuple(range(expected)):
            raise ValueError("assignments must retain exact schedule order")
        by_case: dict[str, list[ArmAssignment]] = defaultdict(list)
        for assignment in self.assignments:
            by_case[assignment.case_id].append(assignment)
        if set(by_case) != {case.case_id for case in self.cases}:
            raise ValueError("assignment and case universes differ")
        for case_assignments in by_case.values():
            if {item.arm for item in case_assignments} != set(StudyArm):
                raise ValueError("every case requires exactly one assignment per arm")
            if {item.within_case_order for item in case_assignments} != {0, 1, 2}:
                raise ValueError("every case requires a complete within-case order")
            if len({item.task_id for item in case_assignments}) != 3:
                raise ValueError("task ids must be unique")
        if len({item.task_id for item in self.assignments}) != expected:
            raise ValueError("task ids must be globally unique")

    @property
    def digest(self) -> str:
        return _digest_json(
            {
                "allocation_seed_commitment": self.allocation_seed_commitment,
                "case_universe_digest": self.case_universe_digest,
                "config_digest": self.config.digest,
                "packet_universe_digest": self.packet_universe_digest,
                "schedule_digest": self.schedule_digest,
            }
        )

    def solver_task(self, task_id: str) -> SolverTask:
        assignment = next((item for item in self.assignments if item.task_id == task_id), None)
        if assignment is None:
            raise KeyError(task_id)
        case = next(item for item in self.cases if item.case_id == assignment.case_id)
        if assignment.arm is StudyArm.EMPTY:
            context = ""
        else:
            packet = next(item for item in self.packets if item.packet_id == assignment.packet_id)
            context = packet.text
        task = SolverTask(
            task_id=task_id,
            problem_text=case.problem_text,
            brain_context=context,
            problem_digest=case.problem_digest,
            context_digest=_sha256_bytes(context.encode("utf-8")),
        )
        if task.problem_digest != assignment.problem_digest:
            raise ValueError("solver problem differs from sealed assignment")
        if task.context_digest != assignment.context_digest:
            raise ValueError("solver context differs from sealed assignment")
        return task

    def public_precommitment(self) -> dict[str, Any]:
        counts = Counter(case.source.value for case in self.cases)
        return {
            "allocation_seed_commitment": self.allocation_seed_commitment,
            "case_count": len(self.cases),
            "case_universe_digest": self.case_universe_digest,
            "config_digest": self.config.digest,
            "packet_universe_digest": self.packet_universe_digest,
            "plan_digest": self.digest,
            "schedule_digest": self.schedule_digest,
            "source_counts": dict(sorted(counts.items())),
            "stage": self.config.stage.value,
            "task_count": len(self.assignments),
        }

    @property
    def public_precommitment_digest(self) -> str:
        return _digest_json(self.public_precommitment())


def _normalized_cases(cases: Sequence[ProblemCase]) -> tuple[ProblemCase, ...]:
    if type(cases) not in {list, tuple}:
        raise ValueError("cases must be a bounded sequence")
    result = tuple(cases)
    if not 12 <= len(result) <= 512:
        raise ValueError("three-arm study requires between 12 and 512 cases")
    if any(type(case) is not ProblemCase for case in result):
        raise ValueError("cases must be exact ProblemCase records")
    if len({case.case_id for case in result}) != len(result):
        raise ValueError("case ids must be unique")
    if len({case.independence_cluster_id for case in result}) != len(result):
        raise ValueError("independence clusters must be unique")
    if len({case.problem_digest for case in result}) != len(result):
        raise ValueError("exact duplicate problem bodies are forbidden")
    return tuple(sorted(result, key=lambda case: case.case_id))


def _normalized_packets(packets: Sequence[FrozenPacket]) -> tuple[FrozenPacket, ...]:
    if type(packets) not in {list, tuple}:
        raise ValueError("packets must be a bounded sequence")
    result = tuple(packets)
    if not result or len(result) > 512:
        raise ValueError("packet universe is empty or too large")
    if any(type(packet) is not FrozenPacket for packet in result):
        raise ValueError("packets must be exact FrozenPacket records")
    if len({packet.packet_id for packet in result}) != len(result):
        raise ValueError("packet ids must be unique")
    return tuple(sorted(result, key=lambda packet: packet.packet_id))


def _validate_confirmatory_balance(cases: Sequence[ProblemCase]) -> None:
    if len(cases) != V3_CONFIRMATORY_CASE_COUNT:
        raise ValueError("confirmatory v3 requires exactly 96 independent cases")
    source_counts = Counter(case.source for case in cases)
    if source_counts != {
        ProblemSource.INTERNAL: V3_CONFIRMATORY_SOURCE_CASE_COUNT,
        ProblemSource.EXTERNAL: V3_CONFIRMATORY_SOURCE_CASE_COUNT,
    }:
        raise ValueError("confirmatory v3 requires exactly 48 cases per source")
    domains = {case.domain for case in cases}
    if domains != set(MathDomain):
        raise ValueError("confirmatory v3 requires the four declared mathematical domains")
    cells = Counter((case.source, case.domain, case.difficulty) for case in cases)
    expected_keys = {
        (source, domain, difficulty)
        for source in ProblemSource
        for domain in domains
        for difficulty in DifficultyBand
    }
    if set(cells) != expected_keys or set(cells.values()) != {4}:
        raise ValueError("every source/domain/difficulty cell must contain exactly four cases")


def _validate_pilot_balance(cases: Sequence[ProblemCase]) -> None:
    if len(cases) != V3_PILOT_CASE_COUNT:
        raise ValueError("pilot v3 requires exactly 24 independent cases")
    cells = Counter((case.source, case.domain, case.difficulty) for case in cases)
    expected = {
        (source, domain, difficulty)
        for source in ProblemSource
        for domain in MathDomain
        for difficulty in DifficultyBand
    }
    if set(cells) != expected or set(cells.values()) != {1}:
        raise ValueError("pilot v3 requires one case in every source/domain/difficulty cell")


def _validate_packet_bindings(
    cases: Sequence[ProblemCase], packets: Sequence[FrozenPacket]
) -> None:
    packet_by_id = {packet.packet_id: packet for packet in packets}
    brain_ids = {case.brain_packet_id for case in cases}
    sham_ids = {case.sham_packet_id for case in cases}
    if brain_ids != set(packet_by_id) or sham_ids != set(packet_by_id):
        raise ValueError("brain and sham mappings must each use every packet exactly once")
    snapshots = {packet.snapshot_manifest_digest for packet in packets}
    policies = {packet.retrieval_policy_digest for packet in packets}
    if len(snapshots) != 1 or len(policies) != 1:
        raise ValueError("all packets must share one frozen snapshot and retrieval policy")
    for case in cases:
        brain = packet_by_id[case.brain_packet_id]
        sham = packet_by_id[case.sham_packet_id]
        if brain.origin_case_id != case.case_id:
            raise ValueError("brain packet must originate from its target case")
        if sham.origin_case_id == case.case_id:
            raise ValueError("sham mapping must be a derangement")
        if not set(case.required_relevance_keys).issubset(brain.relevance_keys):
            raise ValueError("brain packet omits required relevance keys")
        if set(case.required_relevance_keys) & set(sham.relevance_keys):
            raise ValueError("sham packet overlaps target relevance keys")
        if set(brain.source_digests) & set(sham.source_digests):
            raise ValueError("brain and sham packet source sets must be disjoint")
        if brain.text_digest == sham.text_digest:
            raise ValueError("brain and sham packet text must differ")
        if brain.matching_key != sham.matching_key:
            raise ValueError("brain and sham packets are not exactly matched")


def build_three_arm_plan(
    cases: Sequence[ProblemCase],
    packets: Sequence[FrozenPacket],
    *,
    config: ThreeArmConfig,
    allocation_seed: bytes,
) -> ThreeArmPlan:
    """Seal a balanced label-private schedule from gold-free cases and frozen packets."""

    if type(config) is not ThreeArmConfig:
        raise ValueError("plan requires an exact ThreeArmConfig")
    seed = _seed(allocation_seed, "allocation seed")
    normalized_cases = _normalized_cases(cases)
    normalized_packets = _normalized_packets(packets)
    if config.stage is StudyStage.CONFIRMATORY:
        _validate_confirmatory_balance(normalized_cases)
    else:
        _validate_pilot_balance(normalized_cases)
    _validate_packet_bindings(normalized_cases, normalized_packets)

    case_digest = _digest_json([case.commitment_record() for case in normalized_cases])
    packet_digest = _digest_json(
        [packet.commitment_record() for packet in normalized_packets]
    )
    design_universe_digest = _digest_json(
        {
            "case_universe_digest": case_digest,
            "config_digest": config.digest,
            "packet_universe_digest": packet_digest,
        }
    )
    seed_commitment = _seed_commitment(seed, design_universe_digest)
    packet_by_id = {packet.packet_id: packet for packet in normalized_packets}
    arm_orders = tuple(permutations(tuple(StudyArm)))

    order_by_case: dict[str, tuple[StudyArm, ...]] = {}
    blocks: dict[tuple[ProblemSource, MathDomain], list[ProblemCase]] = defaultdict(list)
    for case in normalized_cases:
        blocks[(case.source, case.domain)].append(case)
    for block, block_cases in sorted(blocks.items(), key=lambda item: str(item[0])):
        ranked_orders = sorted(
            arm_orders,
            key=lambda order: _rank(
                seed, "block-arm-orders", *block, *(arm.value for arm in order)
            ),
        )
        difficulty_counts = Counter(case.difficulty for case in block_cases)
        if len(block_cases) == 12 and difficulty_counts == Counter(
            {difficulty: 4 for difficulty in DifficultyBand}
        ):
            # Each of the six orders occurs twice.  Within each four-case difficulty cell,
            # first-arm counts are 2/1/1, preventing a 3/1/0 service-order confound.
            ranked_arms = sorted(
                StudyArm,
                key=lambda arm: _rank(seed, "block-first-arms", *block, arm.value),
            )
            orders_by_first = {
                arm: tuple(order for order in ranked_orders if order[0] is arm)
                for arm in StudyArm
            }
            ranked_difficulties = sorted(
                DifficultyBand,
                key=lambda difficulty: _rank(
                    seed, "block-difficulties", *block, difficulty.value
                ),
            )
            first, second, third = ranked_arms
            allocation_rows = (
                (
                    orders_by_first[first][0],
                    orders_by_first[first][1],
                    orders_by_first[second][0],
                    orders_by_first[third][0],
                ),
                (
                    orders_by_first[second][0],
                    orders_by_first[second][1],
                    orders_by_first[third][1],
                    orders_by_first[first][0],
                ),
                (
                    orders_by_first[third][0],
                    orders_by_first[third][1],
                    orders_by_first[first][1],
                    orders_by_first[second][1],
                ),
            )
            for difficulty, allocated_orders in zip(
                ranked_difficulties, allocation_rows, strict=True
            ):
                ranked_cases = sorted(
                    (case for case in block_cases if case.difficulty is difficulty),
                    key=lambda case: _rank(
                        seed, "difficulty-case-order", *block, difficulty.value, case.case_id
                    ),
                )
                for case, order in zip(ranked_cases, allocated_orders, strict=True):
                    order_by_case[case.case_id] = order
        elif len(block_cases) == 3 and difficulty_counts == Counter(
            {difficulty: 1 for difficulty in DifficultyBand}
        ):
            # A pilot source/domain block contains exactly three cases.  Arbitrarily
            # taking the first three of six ranked permutations can leave an arm absent
            # from a service position (2/1/0).  The six permutations instead partition
            # into two Latin triples; either triple places every arm exactly once in
            # every position.  HMAC ranking chooses the triple and its case mapping.
            latin_triples = (
                (
                    (StudyArm.BRAIN, StudyArm.SHAM, StudyArm.EMPTY),
                    (StudyArm.SHAM, StudyArm.EMPTY, StudyArm.BRAIN),
                    (StudyArm.EMPTY, StudyArm.BRAIN, StudyArm.SHAM),
                ),
                (
                    (StudyArm.BRAIN, StudyArm.EMPTY, StudyArm.SHAM),
                    (StudyArm.EMPTY, StudyArm.SHAM, StudyArm.BRAIN),
                    (StudyArm.SHAM, StudyArm.BRAIN, StudyArm.EMPTY),
                ),
            )
            selected_triple = min(
                latin_triples,
                key=lambda triple: _rank(
                    seed,
                    "pilot-latin-triple",
                    *block,
                    *(arm.value for order in triple for arm in order),
                ),
            )
            ranked_cases = sorted(
                block_cases,
                key=lambda case: _rank(seed, "pilot-block-case-order", *block, case.case_id),
            )
            ranked_selected_orders = sorted(
                selected_triple,
                key=lambda order: _rank(
                    seed,
                    "pilot-latin-order",
                    *block,
                    *(arm.value for arm in order),
                ),
            )
            for case, order in zip(ranked_cases, ranked_selected_orders, strict=True):
                order_by_case[case.case_id] = order
        else:
            ranked_cases = sorted(
                block_cases,
                key=lambda case: _rank(seed, "block-case-order", *block, case.case_id),
            )
            for index, case in enumerate(ranked_cases):
                order_by_case[case.case_id] = ranked_orders[index % len(ranked_orders)]

    scheduled_cases = sorted(
        normalized_cases,
        key=lambda case: _rank(seed, "global-case-schedule", case.case_id),
    )
    assignments: list[ArmAssignment] = []
    for case in scheduled_cases:
        for within_case_order, arm in enumerate(order_by_case[case.case_id]):
            packet_id: str | None
            context: str
            if arm is StudyArm.BRAIN:
                packet_id = case.brain_packet_id
                context = packet_by_id[packet_id].text
            elif arm is StudyArm.SHAM:
                packet_id = case.sham_packet_id
                context = packet_by_id[packet_id].text
            else:
                packet_id = None
                context = ""
            task_id = _sha256_bytes(
                b"math-brain/three-arm-v3/task\x00"
                + seed
                + b"\x00"
                + case.case_id.encode("utf-8")
                + b"\x00"
                + arm.value.encode("ascii")
            )
            assignments.append(
                ArmAssignment(
                    case_id=case.case_id,
                    arm=arm,
                    task_id=task_id,
                    packet_id=packet_id,
                    within_case_order=within_case_order,
                    schedule_index=len(assignments),
                    problem_digest=case.problem_digest,
                    context_digest=_sha256_bytes(context.encode("utf-8")),
                )
            )
    assignment_tuple = tuple(assignments)
    schedule_digest = _digest_json(
        [assignment.commitment_record() for assignment in assignment_tuple]
    )
    return ThreeArmPlan(
        config=config,
        cases=normalized_cases,
        packets=normalized_packets,
        assignments=assignment_tuple,
        case_universe_digest=case_digest,
        packet_universe_digest=packet_digest,
        allocation_seed_commitment=seed_commitment,
        schedule_digest=schedule_digest,
    )


def _revalidate_plan(plan: ThreeArmPlan, *, allocation_seed: bytes) -> None:
    """Rebuild the full solver-view and assignment closure from the revealed seed."""

    if type(plan) is not ThreeArmPlan:
        raise ValueError("evaluation plan must be exact")
    plan.config.__post_init__()
    for case in plan.cases:
        case.__post_init__()
    for packet in plan.packets:
        packet.__post_init__()
    for assignment in plan.assignments:
        assignment.__post_init__()
    plan.__post_init__()
    rebuilt = build_three_arm_plan(
        plan.cases,
        plan.packets,
        config=plan.config,
        allocation_seed=allocation_seed,
    )
    if rebuilt != plan:
        raise ValueError("revealed seed does not reconstruct the exact sealed plan")


class _Ed25519ReceiptVerifier:
    __slots__ = ("_domain", "_public_bytes")

    def __init__(self, public_key_bytes: bytes, *, domain: str) -> None:
        if type(public_key_bytes) is not bytes or len(public_key_bytes) != 32:
            raise ValueError("Ed25519 public key must contain exactly 32 bytes")
        self._public_bytes = bytes(public_key_bytes)
        self._domain = _text(domain, "receipt authority domain", maximum_bytes=128)

    @property
    def commitment(self) -> str:
        return _sha256_bytes(
            b"math-brain/three-arm-v3/verification-key\x00"
            + self._domain.encode("ascii")
            + b"\x00"
            + self._public_bytes
        )

    def public_key_bytes(self) -> bytes:
        return self._public_bytes

    def _message(self, record: Mapping[str, Any]) -> bytes:
        return (
            b"math-brain/three-arm-v3/receipt\x00"
            + self._domain.encode("ascii")
            + b"\x00"
            + canonical_json(record).encode("utf-8")
        )

    def _verify(self, record: Mapping[str, Any], signature: str) -> None:
        _signature(signature, "Ed25519 receipt signature")
        try:
            # Derive the verifier from the same bytes that define ``commitment``.  Keeping
            # a second mutable key object would permit key substitution while preserving
            # the externally pinned commitment.
            Ed25519PublicKey.from_public_bytes(self._public_bytes).verify(
                bytes.fromhex(signature.removeprefix(_SIGNATURE_PREFIX)),
                self._message(record),
            )
        except (InvalidSignature, ValueError) as error:
            raise ValueError("Ed25519 receipt signature is invalid") from error


class _Ed25519ReceiptSigner:
    __slots__ = ("_domain", "_private_key", "_verifier")

    verifier_type: type[_Ed25519ReceiptVerifier] = _Ed25519ReceiptVerifier

    def __init__(self, key_material: bytes, *, domain: str) -> None:
        seed = _seed(key_material, "receipt signing key material")
        private_bytes = hashlib.sha256(
            b"math-brain/three-arm-v3/signing-key\x00"
            + domain.encode("ascii")
            + b"\x00"
            + seed
        ).digest()
        self._private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)
        self._domain = _text(domain, "receipt signer domain", maximum_bytes=128)
        public_bytes = self._private_key.public_key().public_bytes_raw()
        self._verifier = self.verifier_type(public_bytes)

    @property
    def commitment(self) -> str:
        return self._verifier.commitment

    def verifier(self) -> _Ed25519ReceiptVerifier:
        return self._verifier

    def _sign(self, record: Mapping[str, Any]) -> str:
        signature = self._private_key.sign(self._verifier._message(record))
        return _SIGNATURE_PREFIX + signature.hex()


@dataclass(frozen=True, slots=True)
class ScoredAttempt:
    """Authenticated closure over one started slot, runtime artifact, and score."""

    plan_digest: str
    assignment_digest: str
    task_id: str
    schedule_index: int
    problem_digest: str
    context_digest: str
    prompt_digest: str
    started_record_digest: str
    terminal_record_digest: str
    ledger_manifest_digest: str
    transcript_digest: str
    response_digest: str
    model_evidence_digest: str
    executable_digest: str
    loaded_code_digest: str
    parser_digest: str
    scorer_spec_digest: str
    scorer_manifest_digest: str
    scorer_evidence_digest: str
    runtime_policy_digest: str
    runtime_eligible: bool
    started_once_verified: bool
    correct: bool
    served_model: str
    effort: str
    model_identity_verified: bool
    scorer_blinded: bool
    authority_signature: str

    def __post_init__(self) -> None:
        for label, value in (
            ("attempt plan digest", self.plan_digest),
            ("attempt assignment digest", self.assignment_digest),
            ("scored task id", self.task_id),
            ("attempt problem digest", self.problem_digest),
            ("attempt context digest", self.context_digest),
            ("attempt prompt digest", self.prompt_digest),
            ("started ledger record digest", self.started_record_digest),
            ("terminal ledger record digest", self.terminal_record_digest),
            ("ledger manifest digest", self.ledger_manifest_digest),
            ("transcript digest", self.transcript_digest),
            ("response digest", self.response_digest),
            ("model evidence digest", self.model_evidence_digest),
            ("executable digest", self.executable_digest),
            ("loaded code digest", self.loaded_code_digest),
            ("parser digest", self.parser_digest),
            ("scorer specification digest", self.scorer_spec_digest),
            ("scorer manifest digest", self.scorer_manifest_digest),
            ("scorer evidence digest", self.scorer_evidence_digest),
            ("attempt runtime policy digest", self.runtime_policy_digest),
        ):
            _digest(value, label)
        _signature(self.authority_signature, "attempt authority signature")
        if type(self.schedule_index) is not int or self.schedule_index < 0:
            raise ValueError("attempt schedule index must be nonnegative")
        for label, value in (
            ("runtime eligibility", self.runtime_eligible),
            ("one-start evidence", self.started_once_verified),
            ("correctness", self.correct),
            ("model identity evidence", self.model_identity_verified),
            ("scorer blindness evidence", self.scorer_blinded),
        ):
            if type(value) is not bool:
                raise ValueError(f"{label} must be a boolean")
        _text(self.served_model, "served model", maximum_bytes=128)
        _text(self.effort, "effort", maximum_bytes=64)

    def unsigned_record(self) -> dict[str, Any]:
        return {
            "assignment_digest": self.assignment_digest,
            "context_digest": self.context_digest,
            "correct": self.correct,
            "effort": self.effort,
            "executable_digest": self.executable_digest,
            "ledger_manifest_digest": self.ledger_manifest_digest,
            "loaded_code_digest": self.loaded_code_digest,
            "model_evidence_digest": self.model_evidence_digest,
            "model_identity_verified": self.model_identity_verified,
            "parser_digest": self.parser_digest,
            "plan_digest": self.plan_digest,
            "problem_digest": self.problem_digest,
            "prompt_digest": self.prompt_digest,
            "response_digest": self.response_digest,
            "runtime_eligible": self.runtime_eligible,
            "runtime_policy_digest": self.runtime_policy_digest,
            "schedule_index": self.schedule_index,
            "scorer_blinded": self.scorer_blinded,
            "scorer_evidence_digest": self.scorer_evidence_digest,
            "scorer_manifest_digest": self.scorer_manifest_digest,
            "scorer_spec_digest": self.scorer_spec_digest,
            "served_model": self.served_model,
            "started_once_verified": self.started_once_verified,
            "started_record_digest": self.started_record_digest,
            "task_id": self.task_id,
            "terminal_record_digest": self.terminal_record_digest,
            "transcript_digest": self.transcript_digest,
        }

    def commitment_record(self) -> dict[str, Any]:
        return {**self.unsigned_record(), "authority_signature": self.authority_signature}


class ScoringReceiptVerifier(_Ed25519ReceiptVerifier):
    """Public-key-only verifier accepted by aggregate analysis."""

    __slots__ = ()

    def __init__(self, public_key_bytes: bytes) -> None:
        super().__init__(public_key_bytes, domain="scored-attempt-v1")

    def verify(self, plan: ThreeArmPlan, attempt: ScoredAttempt) -> None:
        if type(attempt) is not ScoredAttempt:
            raise ValueError("attempt must be an exact authenticated record")
        attempt.__post_init__()
        if self.commitment != plan.config.scoring_authority_commitment:
            raise ValueError("scoring verifier differs from the precommitment")
        if attempt.plan_digest != plan.digest:
            raise ValueError("attempt belongs to another plan")
        assignment = next(
            (item for item in plan.assignments if item.task_id == attempt.task_id), None
        )
        if assignment is None:
            raise ValueError("attempt task is not in the sealed plan")
        case = next(item for item in plan.cases if item.case_id == assignment.case_id)
        expected_bindings = (
            attempt.assignment_digest == assignment.digest,
            attempt.schedule_index == assignment.schedule_index,
            attempt.problem_digest == assignment.problem_digest,
            attempt.context_digest == assignment.context_digest,
            attempt.runtime_policy_digest == plan.config.runtime_policy_digest,
            attempt.scorer_spec_digest == case.scorer_spec_digest,
            attempt.scorer_manifest_digest == plan.config.scorer_manifest_digest,
        )
        if not all(expected_bindings):
            raise ValueError("attempt receipt differs from its sealed assignment")
        self._verify(attempt.unsigned_record(), attempt.authority_signature)


class ScoringReceiptSigner(_Ed25519ReceiptSigner):
    """Private signer held by the isolated deterministic scoring lifecycle."""

    __slots__ = ()

    verifier_type = ScoringReceiptVerifier

    def __init__(self, key: bytes) -> None:
        super().__init__(key, domain="scored-attempt-v1")

    def verifier(self) -> ScoringReceiptVerifier:
        verifier = super().verifier()
        assert type(verifier) is ScoringReceiptVerifier
        return verifier

    def mint(
        self,
        plan: ThreeArmPlan,
        *,
        task_id: str,
        prompt_digest: str,
        started_record_digest: str,
        terminal_record_digest: str,
        ledger_manifest_digest: str,
        transcript_digest: str,
        response_digest: str,
        model_evidence_digest: str,
        executable_digest: str,
        loaded_code_digest: str,
        parser_digest: str,
        scorer_evidence_digest: str,
        runtime_eligible: bool,
        started_once_verified: bool,
        correct: bool,
        served_model: str,
        effort: str,
        model_identity_verified: bool,
        scorer_blinded: bool,
    ) -> ScoredAttempt:
        if type(plan) is not ThreeArmPlan:
            raise ValueError("attempt receipt requires an exact plan")
        if self.commitment != plan.config.scoring_authority_commitment:
            raise ValueError("scoring authority differs from the precommitment")
        assignment = next(
            (item for item in plan.assignments if item.task_id == task_id), None
        )
        if assignment is None:
            raise ValueError("attempt task is not in the sealed plan")
        case = next(item for item in plan.cases if item.case_id == assignment.case_id)
        pending = _SIGNATURE_PREFIX + "0" * 128
        draft = ScoredAttempt(
            plan_digest=plan.digest,
            assignment_digest=assignment.digest,
            task_id=task_id,
            schedule_index=assignment.schedule_index,
            problem_digest=assignment.problem_digest,
            context_digest=assignment.context_digest,
            prompt_digest=prompt_digest,
            started_record_digest=started_record_digest,
            terminal_record_digest=terminal_record_digest,
            ledger_manifest_digest=ledger_manifest_digest,
            transcript_digest=transcript_digest,
            response_digest=response_digest,
            model_evidence_digest=model_evidence_digest,
            executable_digest=executable_digest,
            loaded_code_digest=loaded_code_digest,
            parser_digest=parser_digest,
            scorer_spec_digest=case.scorer_spec_digest,
            scorer_manifest_digest=plan.config.scorer_manifest_digest,
            scorer_evidence_digest=scorer_evidence_digest,
            runtime_policy_digest=plan.config.runtime_policy_digest,
            runtime_eligible=runtime_eligible,
            started_once_verified=started_once_verified,
            correct=correct,
            served_model=served_model,
            effort=effort,
            model_identity_verified=model_identity_verified,
            scorer_blinded=scorer_blinded,
            authority_signature=pending,
        )
        return replace(draft, authority_signature=self._sign(draft.unsigned_record()))


@dataclass(frozen=True, slots=True)
class IntegrityAudit:
    plan_digest: str
    preregistration_digest: str
    result_manifest_digest: str
    bank_manifest_digest: str
    packet_manifest_digest: str
    runtime_manifest_digest: str
    scorer_manifest_digest: str
    ledger_manifest_digest: str
    report_digest: str
    external_chronology_verified: bool
    packet_matching_verified: bool
    contamination_clear: bool
    brain_snapshot_read_only: bool
    sterile_controls_verified: bool
    ledger_complete: bool
    schedule_integrity_verified: bool
    scorer_integrity_verified: bool
    cluster_independence_verified: bool
    authority_signature: str

    def __post_init__(self) -> None:
        for label, value in (
            ("audit plan digest", self.plan_digest),
            ("audit preregistration digest", self.preregistration_digest),
            ("audit result manifest digest", self.result_manifest_digest),
            ("audit bank manifest digest", self.bank_manifest_digest),
            ("audit packet manifest digest", self.packet_manifest_digest),
            ("audit runtime manifest digest", self.runtime_manifest_digest),
            ("audit scorer manifest digest", self.scorer_manifest_digest),
            ("audit ledger manifest digest", self.ledger_manifest_digest),
            ("audit report digest", self.report_digest),
        ):
            _digest(value, label)
        _signature(self.authority_signature, "audit authority signature")
        for value in self.boolean_values:
            if type(value) is not bool:
                raise ValueError("integrity audit gates must be booleans")

    @property
    def boolean_values(self) -> tuple[bool, ...]:
        return (
            self.external_chronology_verified,
            self.packet_matching_verified,
            self.contamination_clear,
            self.brain_snapshot_read_only,
            self.sterile_controls_verified,
            self.ledger_complete,
            self.schedule_integrity_verified,
            self.scorer_integrity_verified,
            self.cluster_independence_verified,
        )

    @property
    def passed(self) -> bool:
        return all(self.boolean_values)

    def unsigned_record(self) -> dict[str, Any]:
        return {
            "bank_manifest_digest": self.bank_manifest_digest,
            "brain_snapshot_read_only": self.brain_snapshot_read_only,
            "cluster_independence_verified": self.cluster_independence_verified,
            "contamination_clear": self.contamination_clear,
            "external_chronology_verified": self.external_chronology_verified,
            "ledger_complete": self.ledger_complete,
            "ledger_manifest_digest": self.ledger_manifest_digest,
            "packet_manifest_digest": self.packet_manifest_digest,
            "packet_matching_verified": self.packet_matching_verified,
            "plan_digest": self.plan_digest,
            "preregistration_digest": self.preregistration_digest,
            "report_digest": self.report_digest,
            "result_manifest_digest": self.result_manifest_digest,
            "runtime_manifest_digest": self.runtime_manifest_digest,
            "schedule_integrity_verified": self.schedule_integrity_verified,
            "scorer_integrity_verified": self.scorer_integrity_verified,
            "scorer_manifest_digest": self.scorer_manifest_digest,
            "sterile_controls_verified": self.sterile_controls_verified,
        }


class AuditReceiptVerifier(_Ed25519ReceiptVerifier):
    """Public-key-only verifier accepted by aggregate analysis."""

    __slots__ = ()

    def __init__(self, public_key_bytes: bytes) -> None:
        super().__init__(public_key_bytes, domain="integrity-audit-v1")

    def verify(
        self,
        plan: ThreeArmPlan,
        audit: IntegrityAudit,
        *,
        result_manifest_digest: str,
        expected_preregistration_digest: str,
    ) -> None:
        if type(audit) is not IntegrityAudit:
            raise ValueError("audit must be an exact authenticated record")
        audit.__post_init__()
        if self.commitment != plan.config.audit_authority_commitment:
            raise ValueError("audit verifier differs from the precommitment")
        expected_bindings = (
            audit.plan_digest == plan.digest,
            audit.preregistration_digest == expected_preregistration_digest,
            audit.result_manifest_digest == result_manifest_digest,
            audit.packet_manifest_digest == plan.packet_universe_digest,
            audit.scorer_manifest_digest == plan.config.scorer_manifest_digest,
        )
        if not all(expected_bindings):
            raise ValueError("audit receipt differs from the sealed evidence universe")
        self._verify(audit.unsigned_record(), audit.authority_signature)


class AuditReceiptSigner(_Ed25519ReceiptSigner):
    """Private signer held separately by the independent integrity auditor."""

    __slots__ = ()

    verifier_type = AuditReceiptVerifier

    def __init__(self, key: bytes) -> None:
        super().__init__(key, domain="integrity-audit-v1")

    def verifier(self) -> AuditReceiptVerifier:
        verifier = super().verifier()
        assert type(verifier) is AuditReceiptVerifier
        return verifier

    def mint(
        self,
        plan: ThreeArmPlan,
        *,
        result_manifest_digest: str,
        bank_manifest_digest: str,
        runtime_manifest_digest: str,
        ledger_manifest_digest: str,
        report_digest: str,
        external_chronology_verified: bool,
        packet_matching_verified: bool,
        contamination_clear: bool,
        brain_snapshot_read_only: bool,
        sterile_controls_verified: bool,
        ledger_complete: bool,
        schedule_integrity_verified: bool,
        scorer_integrity_verified: bool,
        cluster_independence_verified: bool,
    ) -> IntegrityAudit:
        if type(plan) is not ThreeArmPlan:
            raise ValueError("audit receipt requires an exact plan")
        if self.commitment != plan.config.audit_authority_commitment:
            raise ValueError("audit authority differs from the precommitment")
        pending = _SIGNATURE_PREFIX + "0" * 128
        draft = IntegrityAudit(
            plan_digest=plan.digest,
            preregistration_digest=plan.public_precommitment_digest,
            result_manifest_digest=result_manifest_digest,
            bank_manifest_digest=bank_manifest_digest,
            packet_manifest_digest=plan.packet_universe_digest,
            runtime_manifest_digest=runtime_manifest_digest,
            scorer_manifest_digest=plan.config.scorer_manifest_digest,
            ledger_manifest_digest=ledger_manifest_digest,
            report_digest=report_digest,
            external_chronology_verified=external_chronology_verified,
            packet_matching_verified=packet_matching_verified,
            contamination_clear=contamination_clear,
            brain_snapshot_read_only=brain_snapshot_read_only,
            sterile_controls_verified=sterile_controls_verified,
            ledger_complete=ledger_complete,
            schedule_integrity_verified=schedule_integrity_verified,
            scorer_integrity_verified=scorer_integrity_verified,
            cluster_independence_verified=cluster_independence_verified,
            authority_signature=pending,
        )
        return replace(draft, authority_signature=self._sign(draft.unsigned_record()))


@dataclass(frozen=True, slots=True)
class ContrastSummary:
    mean_delta: float
    exact_one_sided_p: float
    interval_lower: float
    interval_upper: float
    positive_cases: int
    tied_cases: int
    negative_cases: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "exact_one_sided_p": self.exact_one_sided_p,
            "interval_lower": self.interval_lower,
            "interval_upper": self.interval_upper,
            "mean_delta": self.mean_delta,
            "negative_cases": self.negative_cases,
            "positive_cases": self.positive_cases,
            "tied_cases": self.tied_cases,
        }


@dataclass(frozen=True, slots=True)
class SourceSummary:
    source: ProblemSource
    case_count: int
    brain_rate: float
    sham_rate: float
    empty_rate: float
    brain_minus_sham: ContrastSummary
    brain_minus_empty: ContrastSummary
    sham_minus_empty: ContrastSummary

    def to_dict(self) -> dict[str, Any]:
        return {
            "brain_minus_empty": self.brain_minus_empty.to_dict(),
            "brain_minus_sham": self.brain_minus_sham.to_dict(),
            "brain_rate": self.brain_rate,
            "case_count": self.case_count,
            "empty_rate": self.empty_rate,
            "sham_minus_empty": self.sham_minus_empty.to_dict(),
            "sham_rate": self.sham_rate,
            "source": self.source.value,
        }


@dataclass(frozen=True, slots=True)
class ThreeArmSummary:
    plan_digest: str
    preregistration_digest: str
    result_universe_digest: str
    scoring_authority_commitment: str
    audit_authority_commitment: str
    audit_report_digest: str
    audit_signature: str
    case_count: int
    attempt_count: int
    served_model: str
    effort: str
    brain_rate: float
    sham_rate: float
    empty_rate: float
    brain_minus_sham: ContrastSummary
    brain_minus_empty: ContrastSummary
    sham_minus_empty: ContrastSummary
    source_summaries: tuple[SourceSummary, ...]
    worst_supported_stratum_brain_minus_empty: float | None
    failed_gates: tuple[str, ...]
    method_helped: bool

    def __post_init__(self) -> None:
        for label, value in (
            ("summary plan digest", self.plan_digest),
            ("summary preregistration digest", self.preregistration_digest),
            ("summary result universe digest", self.result_universe_digest),
            ("summary scoring verifier commitment", self.scoring_authority_commitment),
            ("summary audit verifier commitment", self.audit_authority_commitment),
            ("summary audit report digest", self.audit_report_digest),
        ):
            _digest(value, label)
        _signature(self.audit_signature, "summary audit signature")
        if type(self.failed_gates) is not tuple or tuple(sorted(set(self.failed_gates))) != (
            self.failed_gates
        ):
            raise ValueError("summary failed gates must be unique and sorted")
        if type(self.method_helped) is not bool or self.method_helped != (not self.failed_gates):
            raise ValueError("summary decision must equal the sealed gate conjunction")
        if self.worst_supported_stratum_brain_minus_empty is not None and (
            type(self.worst_supported_stratum_brain_minus_empty) is not float
            or not math.isfinite(self.worst_supported_stratum_brain_minus_empty)
        ):
            raise ValueError("worst supported stratum must be a finite float or null")

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_count": self.attempt_count,
            "audit_authority_commitment": self.audit_authority_commitment,
            "audit_signature": self.audit_signature,
            "audit_report_digest": self.audit_report_digest,
            "brain_minus_empty": self.brain_minus_empty.to_dict(),
            "brain_minus_sham": self.brain_minus_sham.to_dict(),
            "brain_rate": self.brain_rate,
            "case_count": self.case_count,
            "effort": self.effort,
            "empty_rate": self.empty_rate,
            "failed_gates": list(self.failed_gates),
            "method_helped": self.method_helped,
            "plan_digest": self.plan_digest,
            "preregistration_digest": self.preregistration_digest,
            "result_universe_digest": self.result_universe_digest,
            "scoring_authority_commitment": self.scoring_authority_commitment,
            "served_model": self.served_model,
            "sham_minus_empty": self.sham_minus_empty.to_dict(),
            "sham_rate": self.sham_rate,
            "source_summaries": [item.to_dict() for item in self.source_summaries],
            "worst_supported_stratum_brain_minus_empty": (
                self.worst_supported_stratum_brain_minus_empty
            ),
        }


@dataclass(frozen=True, slots=True)
class SignedThreeArmSummary:
    summary: ThreeArmSummary
    analysis_authority_commitment: str
    authority_signature: str

    def __post_init__(self) -> None:
        if type(self.summary) is not ThreeArmSummary:
            raise ValueError("signed result requires an exact ThreeArmSummary")
        self.summary.__post_init__()
        _digest(self.analysis_authority_commitment, "analysis verifier commitment")
        _signature(self.authority_signature, "analysis authority signature")

    def unsigned_record(self) -> dict[str, Any]:
        return {
            "analysis_authority_commitment": self.analysis_authority_commitment,
            "record_type": "org.math-research-brain/three-arm-signed-summary-v1",
            "summary": self.summary.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.unsigned_record(), "authority_signature": self.authority_signature}


class AnalysisReceiptVerifier(_Ed25519ReceiptVerifier):
    """Public-key-only verifier for the final aggregate decision."""

    __slots__ = ()

    def __init__(self, public_key_bytes: bytes) -> None:
        super().__init__(public_key_bytes, domain="aggregate-summary-v1")

    def verify(
        self,
        plan: ThreeArmPlan,
        signed: SignedThreeArmSummary,
        attempts: Iterable[ScoredAttempt],
        *,
        audit: IntegrityAudit,
        scoring_verifier: ScoringReceiptVerifier,
        audit_verifier: AuditReceiptVerifier,
        allocation_seed: bytes,
        expected_plan_digest: str,
        expected_preregistration_digest: str,
    ) -> None:
        if type(signed) is not SignedThreeArmSummary:
            raise ValueError("final result must be an exact signed summary")
        signed.__post_init__()
        if self.commitment != plan.config.analysis_authority_commitment:
            raise ValueError("analysis verifier differs from the precommitment")
        if signed.analysis_authority_commitment != self.commitment:
            raise ValueError("signed summary names another analysis verifier")
        if signed.summary.plan_digest != plan.digest:
            raise ValueError("signed summary belongs to another plan")
        expected_summary = summarize_three_arm(
            plan,
            tuple(attempts),
            audit=audit,
            scoring_verifier=scoring_verifier,
            audit_verifier=audit_verifier,
            allocation_seed=allocation_seed,
            expected_plan_digest=expected_plan_digest,
            expected_preregistration_digest=expected_preregistration_digest,
        )
        if signed.summary != expected_summary:
            raise ValueError("signed summary differs from independent evidence replay")
        self._verify(signed.unsigned_record(), signed.authority_signature)


class AnalysisReceiptSigner(_Ed25519ReceiptSigner):
    """Private signer held by the isolated aggregate-analysis process."""

    __slots__ = ()
    verifier_type = AnalysisReceiptVerifier

    def __init__(self, key: bytes) -> None:
        super().__init__(key, domain="aggregate-summary-v1")

    def verifier(self) -> AnalysisReceiptVerifier:
        verifier = super().verifier()
        assert type(verifier) is AnalysisReceiptVerifier
        return verifier

    def analyze_and_sign(
        self,
        plan: ThreeArmPlan,
        attempts: Iterable[ScoredAttempt],
        *,
        audit: IntegrityAudit,
        scoring_verifier: ScoringReceiptVerifier,
        audit_verifier: AuditReceiptVerifier,
        allocation_seed: bytes,
        expected_plan_digest: str,
        expected_preregistration_digest: str,
    ) -> SignedThreeArmSummary:
        if self.commitment != plan.config.analysis_authority_commitment:
            raise ValueError("analysis signer differs from the precommitment")
        summary = summarize_three_arm(
            plan,
            tuple(attempts),
            audit=audit,
            scoring_verifier=scoring_verifier,
            audit_verifier=audit_verifier,
            allocation_seed=allocation_seed,
            expected_plan_digest=expected_plan_digest,
            expected_preregistration_digest=expected_preregistration_digest,
        )
        pending = _SIGNATURE_PREFIX + "0" * 128
        draft = SignedThreeArmSummary(
            summary=summary,
            analysis_authority_commitment=self.commitment,
            authority_signature=pending,
        )
        return replace(draft, authority_signature=self._sign(draft.unsigned_record()))


def _exact_one_sided_sign_p(deltas: Sequence[int]) -> float:
    nonzero = [value for value in deltas if value]
    if not nonzero:
        return 1.0
    positive = sum(value > 0 for value in nonzero)
    numerator = sum(math.comb(len(nonzero), count) for count in range(positive, len(nonzero) + 1))
    return numerator / (2 ** len(nonzero))


def _bootstrap_interval(
    deltas: Sequence[int], *, seed: bytes, domain: str, repetitions: int
) -> tuple[float, float]:
    if len(deltas) < 2:
        raise ValueError("paired bootstrap requires at least two case clusters")
    # One committed HMAC seeds an isolated deterministic PRNG stream.  ``choices`` keeps
    # the 4,096 x N bootstrap loop inexpensive enough to run repeatedly in validation.
    bootstrap_seed = int.from_bytes(_rank(seed, "paired-bootstrap", domain), "big")
    generator = random.Random(bootstrap_seed)
    means = [
        sum(generator.choices(deltas, k=len(deltas))) / len(deltas)
        for _repetition in range(repetitions)
    ]
    means.sort()
    lower_index = math.floor(0.025 * (repetitions - 1))
    upper_index = math.ceil(0.975 * (repetitions - 1))
    return means[lower_index], means[upper_index]


def _contrast(
    deltas: Sequence[int], *, seed: bytes, domain: str, repetitions: int
) -> ContrastSummary:
    lower, upper = _bootstrap_interval(
        deltas, seed=seed, domain=domain, repetitions=repetitions
    )
    counts = Counter(deltas)
    return ContrastSummary(
        mean_delta=sum(deltas) / len(deltas),
        exact_one_sided_p=_exact_one_sided_sign_p(deltas),
        interval_lower=lower,
        interval_upper=upper,
        positive_cases=counts[1],
        tied_cases=counts[0],
        negative_cases=counts[-1],
    )


def summarize_three_arm(
    plan: ThreeArmPlan,
    attempts: Iterable[ScoredAttempt],
    *,
    audit: IntegrityAudit,
    scoring_verifier: ScoringReceiptVerifier,
    audit_verifier: AuditReceiptVerifier,
    allocation_seed: bytes,
    expected_plan_digest: str,
    expected_preregistration_digest: str,
) -> ThreeArmSummary:
    """Validate the exact first-attempt universe and emit an aggregate-only decision."""

    _digest(expected_plan_digest, "externally expected plan digest")
    _digest(expected_preregistration_digest, "externally expected preregistration digest")
    _revalidate_plan(plan, allocation_seed=allocation_seed)
    if plan.digest != expected_plan_digest:
        raise ValueError("plan differs from the externally expected digest")
    if plan.public_precommitment_digest != expected_preregistration_digest:
        raise ValueError("preregistration differs from the externally expected digest")
    if type(scoring_verifier) is not ScoringReceiptVerifier:
        raise ValueError("summary requires an exact public scoring verifier")
    if type(audit_verifier) is not AuditReceiptVerifier:
        raise ValueError("summary requires an exact public audit verifier")
    materialized = tuple(attempts)
    if any(type(item) is not ScoredAttempt for item in materialized):
        raise ValueError("attempts must be exact ScoredAttempt records")
    if len({item.task_id for item in materialized}) != len(materialized):
        raise ValueError("summary rejects duplicate attempts")
    expected_task_ids = tuple(item.task_id for item in plan.assignments)
    if tuple(item.task_id for item in materialized) != expected_task_ids:
        raise ValueError("summary requires the exact ordered sealed attempt universe")
    for attempt in materialized:
        scoring_verifier.verify(plan, attempt)
    started_records = {item.started_record_digest for item in materialized}
    terminal_records = {item.terminal_record_digest for item in materialized}
    if len(started_records) != len(materialized):
        raise ValueError("attempt receipts reuse a STARTED ledger record")
    if len(terminal_records) != len(materialized):
        raise ValueError("attempt receipts reuse a terminal ledger record")
    if not started_records.isdisjoint(terminal_records):
        raise ValueError("STARTED and terminal ledger record universes must be disjoint")
    ledger_manifests = {item.ledger_manifest_digest for item in materialized}
    if len(ledger_manifests) != 1:
        raise ValueError("attempt receipts disagree on the ledger manifest")
    result_digest = _digest_json(
        [attempt.commitment_record() for attempt in materialized]
    )
    audit_verifier.verify(
        plan,
        audit,
        result_manifest_digest=result_digest,
        expected_preregistration_digest=expected_preregistration_digest,
    )
    if audit.ledger_manifest_digest != next(iter(ledger_manifests)):
        raise ValueError("integrity audit differs from the attempt ledger manifest")

    attempt_by_task = {item.task_id: item for item in materialized}
    scores: dict[str, dict[StudyArm, int]] = defaultdict(dict)
    for assignment in plan.assignments:
        scores[assignment.case_id][assignment.arm] = int(
            attempt_by_task[assignment.task_id].correct
        )
    if any(set(arm_scores) != set(StudyArm) for arm_scores in scores.values()):
        raise ValueError("every case must have a complete scored triplet")

    brain_values = [scores[case.case_id][StudyArm.BRAIN] for case in plan.cases]
    sham_values = [scores[case.case_id][StudyArm.SHAM] for case in plan.cases]
    empty_values = [scores[case.case_id][StudyArm.EMPTY] for case in plan.cases]
    brain_sham_deltas = [
        left - right for left, right in zip(brain_values, sham_values, strict=True)
    ]
    brain_empty_deltas = [
        left - right for left, right in zip(brain_values, empty_values, strict=True)
    ]
    sham_empty_deltas = [
        left - right for left, right in zip(sham_values, empty_values, strict=True)
    ]
    bootstrap_seed = bytes.fromhex(plan.schedule_digest.removeprefix(_DIGEST_PREFIX))

    brain_minus_sham = _contrast(
        brain_sham_deltas,
        seed=bootstrap_seed,
        domain="brain-minus-sham",
        repetitions=plan.config.bootstrap_repetitions,
    )
    brain_minus_empty = _contrast(
        brain_empty_deltas,
        seed=bootstrap_seed,
        domain="brain-minus-empty",
        repetitions=plan.config.bootstrap_repetitions,
    )
    sham_minus_empty = _contrast(
        sham_empty_deltas,
        seed=bootstrap_seed,
        domain="sham-minus-empty",
        repetitions=plan.config.bootstrap_repetitions,
    )

    source_summaries: list[SourceSummary] = []
    for source in ProblemSource:
        source_cases = [case for case in plan.cases if case.source is source]
        brain = [scores[case.case_id][StudyArm.BRAIN] for case in source_cases]
        sham = [scores[case.case_id][StudyArm.SHAM] for case in source_cases]
        empty = [scores[case.case_id][StudyArm.EMPTY] for case in source_cases]
        source_brain_sham = [
            left - right for left, right in zip(brain, sham, strict=True)
        ]
        source_brain_empty = [
            left - right for left, right in zip(brain, empty, strict=True)
        ]
        source_sham_empty = [
            left - right for left, right in zip(sham, empty, strict=True)
        ]
        source_summaries.append(
            SourceSummary(
                source=source,
                case_count=len(source_cases),
                brain_rate=sum(brain) / len(brain),
                sham_rate=sum(sham) / len(sham),
                empty_rate=sum(empty) / len(empty),
                brain_minus_sham=_contrast(
                    source_brain_sham,
                    seed=bootstrap_seed,
                    domain=f"{source.value}-brain-minus-sham",
                    repetitions=plan.config.bootstrap_repetitions,
                ),
                brain_minus_empty=_contrast(
                    source_brain_empty,
                    seed=bootstrap_seed,
                    domain=f"{source.value}-brain-minus-empty",
                    repetitions=plan.config.bootstrap_repetitions,
                ),
                sham_minus_empty=_contrast(
                    source_sham_empty,
                    seed=bootstrap_seed,
                    domain=f"{source.value}-sham-minus-empty",
                    repetitions=plan.config.bootstrap_repetitions,
                ),
            )
        )

    strata: dict[tuple[str, str], list[ProblemCase]] = defaultdict(list)
    for case in plan.cases:
        strata[("source_domain", f"{case.source.value}:{case.domain}")].append(case)
        strata[("source_difficulty", f"{case.source.value}:{case.difficulty.value}")].append(case)
    supported_stratum_deltas = []
    for stratum_cases in strata.values():
        if len(stratum_cases) < 8:
            continue
        supported_stratum_deltas.append(
            sum(
                scores[case.case_id][StudyArm.BRAIN]
                - scores[case.case_id][StudyArm.EMPTY]
                for case in stratum_cases
            )
            / len(stratum_cases)
        )
    # The engineering pilot has only one case in each source/domain/difficulty cell,
    # so no stratum reaches the preregistered support floor of eight cases.  Absence
    # of an adequately supported stratum is not a measured -1.0 regression.
    worst_stratum = min(supported_stratum_deltas) if supported_stratum_deltas else None

    models = {item.served_model for item in materialized}
    efforts = {item.effort for item in materialized}
    failed: list[str] = []
    if plan.config.stage is not StudyStage.CONFIRMATORY:
        failed.append("confirmatory_stage")
    for label, contrast in (
        ("brain_minus_sham", brain_minus_sham),
        ("brain_minus_empty", brain_minus_empty),
    ):
        if contrast.mean_delta < plan.config.minimum_effect:
            failed.append(f"{label}_minimum_effect")
        if contrast.exact_one_sided_p > plan.config.alpha:
            failed.append(f"{label}_exact_p")
        if contrast.interval_lower <= 0:
            failed.append(f"{label}_interval_lower")
    if sham_minus_empty.mean_delta < -0.02:
        failed.append("sham_specificity_loss")
    if any(item.sham_minus_empty.mean_delta < -0.02 for item in source_summaries):
        failed.append("source_sham_specificity_loss")
    if any(
        item.brain_minus_empty.mean_delta < plan.config.minimum_effect
        for item in source_summaries
    ):
        failed.append("source_brain_minus_empty_minimum_effect")
    if any(
        item.brain_minus_sham.mean_delta < plan.config.minimum_effect
        for item in source_summaries
    ):
        failed.append("source_brain_minus_sham_minimum_effect")
    if (
        worst_stratum is not None
        and worst_stratum < -V3_MAX_SUPPORTED_STRATUM_REGRESSION
    ):
        failed.append("supported_stratum_regression")
    empty_rate = sum(empty_values) / len(empty_values)
    if not V3_EMPTY_RATE_FLOOR <= empty_rate <= V3_EMPTY_RATE_CEILING:
        failed.append("overall_difficulty")
    if any(
        not V3_EMPTY_RATE_FLOOR <= item.empty_rate <= V3_EMPTY_RATE_CEILING
        for item in source_summaries
    ):
        failed.append("source_difficulty")
    if not audit.passed:
        failed.append("integrity_audit")
    if not all(item.runtime_eligible for item in materialized):
        failed.append("runtime_eligibility")
    if any(
        len({getattr(item, field_name) for item in materialized}) != 1
        for field_name in ("executable_digest", "loaded_code_digest", "parser_digest")
    ):
        failed.append("runtime_artifact_consistency")
    if not all(item.started_once_verified for item in materialized):
        failed.append("one_start_integrity")
    if len(models) != 1 or not all(item.model_identity_verified for item in materialized):
        failed.append("served_model_identity")
    if len(efforts) != 1:
        failed.append("effort_consistency")
    if not all(item.scorer_blinded for item in materialized):
        failed.append("scorer_blindness")

    failed_tuple = tuple(sorted(set(failed)))
    return ThreeArmSummary(
        plan_digest=plan.digest,
        preregistration_digest=expected_preregistration_digest,
        result_universe_digest=result_digest,
        scoring_authority_commitment=scoring_verifier.commitment,
        audit_authority_commitment=audit_verifier.commitment,
        audit_report_digest=audit.report_digest,
        audit_signature=audit.authority_signature,
        case_count=len(plan.cases),
        attempt_count=len(materialized),
        served_model=next(iter(models)) if len(models) == 1 else "mixed_or_unverified",
        effort=next(iter(efforts)) if len(efforts) == 1 else "mixed",
        brain_rate=sum(brain_values) / len(brain_values),
        sham_rate=sum(sham_values) / len(sham_values),
        empty_rate=empty_rate,
        brain_minus_sham=brain_minus_sham,
        brain_minus_empty=brain_minus_empty,
        sham_minus_empty=sham_minus_empty,
        source_summaries=tuple(source_summaries),
        worst_supported_stratum_brain_minus_empty=worst_stratum,
        failed_gates=failed_tuple,
        method_helped=not failed_tuple,
    )


__all__ = [
    "V3_BOOTSTRAP_REPETITIONS",
    "V3_CONFIRMATORY_CASE_COUNT",
    "V3_PILOT_CASE_COUNT",
    "AnalysisReceiptSigner",
    "AnalysisReceiptVerifier",
    "AuditReceiptSigner",
    "AuditReceiptVerifier",
    "ContrastSummary",
    "DifficultyBand",
    "FrozenPacket",
    "IntegrityAudit",
    "MathDomain",
    "ProblemCase",
    "ProblemSource",
    "ScoredAttempt",
    "ScoringReceiptSigner",
    "ScoringReceiptVerifier",
    "SignedThreeArmSummary",
    "SolverTask",
    "SourceSummary",
    "StudyArm",
    "StudyStage",
    "ThreeArmConfig",
    "ThreeArmPlan",
    "ThreeArmSummary",
    "TokenizerReceipt",
    "build_three_arm_plan",
    "summarize_three_arm",
]
