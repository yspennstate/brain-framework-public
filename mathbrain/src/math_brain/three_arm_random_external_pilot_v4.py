"""Honest 24-case/72-slot allocation for the random-external v4 pilot.

The failed curated MathNet lane required one external case in every semantic
domain/difficulty cell.  A random external sample has no such audited labels.  This
module therefore keeps the twelve internal cells exactly balanced while representing
every external domain and difficulty as absent.  It never invents allocation labels.

Automatic retrieval is an intention-to-treat intervention.  A preregistered retrieval
abstention is retained as a case where BRAIN, SHAM, and EMPTY all receive empty context;
it cannot remove, replace, or top up the case.  When retrieval selects a packet, the
brain and sham packets must be a byte/token-shape-matched, source-disjoint
derangement.  Later semantic relevance audits may be reported but do not gate this
allocation.
"""

from __future__ import annotations

import hashlib
import hmac
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import permutations

from .models import canonical_json
from .three_arm_evaluation import (
    ArmAssignment,
    DifficultyBand,
    FrozenPacket,
    MathDomain,
    ProblemSource,
    SolverTask,
    StudyArm,
    StudyStage,
    ThreeArmConfig,
)

RANDOM_EXTERNAL_PILOT_SCHEMA_V4 = "org.math-research-brain/random-external-three-arm-pilot/v4"
RANDOM_EXTERNAL_PILOT_CASE_COUNT_V4 = 24
RANDOM_EXTERNAL_PILOT_SOURCE_CASE_COUNT_V4 = 12
RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4 = 72

_DIGEST_PREFIX = "sha256:"
_DIGEST_LENGTH = len(_DIGEST_PREFIX) + 64
_MAX_PROBLEM_BYTES = 128_000
_MAX_CONTEXT_BYTES = 256_000
_RANK_DOMAIN = b"math-brain/random-external-three-arm-v4/rank\x00"
_SEED_DOMAIN = b"math-brain/random-external-three-arm-v4/seed\x00"
_TASK_DOMAIN = b"math-brain/random-external-three-arm-v4/task\x00"


def _sha256(raw: bytes) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(raw).hexdigest()


def _digest_json(value: object) -> str:
    return _sha256(canonical_json(value).encode("utf-8"))


def _digest(value: object, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != _DIGEST_LENGTH
        or not value.startswith(_DIGEST_PREFIX)
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{label} must be canonical sha256:<lowercase hex>")
    return value


def _text(value: object, label: str, *, maximum: int = 4096) -> str:
    if type(value) is not str:
        raise ValueError(f"{label} must be exact text")
    try:
        raw = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} must be valid UTF-8") from error
    if not value.strip() or len(raw) > maximum:
        raise ValueError(f"{label} is empty or exceeds its byte budget")
    return value


def _seed(value: object) -> bytes:
    if type(value) is not bytes or len(value) < 32:
        raise ValueError("v4 allocation seed must contain at least 256 bits")
    return value


def _rank(seed: bytes, role: str, *parts: object) -> bytes:
    material = bytearray(_RANK_DOMAIN)
    for part in (role, *parts):
        encoded = str(part).encode("utf-8")
        material.extend(len(encoded).to_bytes(4, "big"))
        material.extend(encoded)
    return hmac.new(seed, bytes(material), hashlib.sha256).digest()


def _seed_commitment(seed: bytes, design_digest: str) -> str:
    return _sha256(_SEED_DOMAIN + design_digest.encode("ascii") + b"\x00" + seed)


class AutomaticRetrievalDispositionV4(StrEnum):
    SELECTED = "selected"
    ABSTAINED = "abstained"


@dataclass(frozen=True, slots=True)
class RandomExternalPilotCaseV4:
    """One gold-free case; external semantic labels are deliberately absent."""

    case_id: str
    source: ProblemSource
    domain: MathDomain | None
    difficulty: DifficultyBand | None
    independence_cluster_id: str
    problem_text: str = field(repr=False)
    scorer_spec_digest: str
    retrieval_disposition: AutomaticRetrievalDispositionV4
    retrieval_receipt_digest: str
    brain_packet_id: str | None
    sham_packet_id: str | None
    retrieval_abstention_reason_digest: str | None

    def __post_init__(self) -> None:
        if type(self) is not RandomExternalPilotCaseV4:
            raise ValueError("random-external case subclasses are forbidden")
        _text(self.case_id, "v4 case id", maximum=128)
        _text(self.independence_cluster_id, "v4 independence cluster", maximum=128)
        _text(self.problem_text, "v4 problem text", maximum=_MAX_PROBLEM_BYTES)
        _digest(self.scorer_spec_digest, "v4 scorer specification")
        _digest(self.retrieval_receipt_digest, "v4 retrieval receipt")
        if type(self.source) is not ProblemSource:
            raise ValueError("v4 case source must be exact")
        if self.source is ProblemSource.INTERNAL:
            if type(self.domain) is not MathDomain or type(self.difficulty) is not DifficultyBand:
                raise ValueError("internal v4 cases require exact audited semantic labels")
        elif self.domain is not None or self.difficulty is not None:
            raise ValueError("random external v4 cases must not invent semantic labels")
        if type(self.retrieval_disposition) is not AutomaticRetrievalDispositionV4:
            raise ValueError("v4 retrieval disposition must be exact")
        if self.retrieval_disposition is AutomaticRetrievalDispositionV4.SELECTED:
            if self.retrieval_abstention_reason_digest is not None:
                raise ValueError("selected retrieval cannot carry an abstention reason")
            _text(self.brain_packet_id, "v4 brain packet id", maximum=128)
            _text(self.sham_packet_id, "v4 sham packet id", maximum=128)
            if self.brain_packet_id == self.sham_packet_id:
                raise ValueError("brain and sham packet ids must differ")
        else:
            if self.brain_packet_id is not None or self.sham_packet_id is not None:
                raise ValueError("retrieval abstention cannot expose a packet")
            _digest(
                self.retrieval_abstention_reason_digest,
                "v4 retrieval abstention reason",
            )

    @property
    def problem_digest(self) -> str:
        return _sha256(self.problem_text.encode("utf-8"))

    @property
    def selection_lane(self) -> str:
        if self.source is ProblemSource.INTERNAL:
            return "internal-blind-author-v3"
        return "mathnet-random-autoretrieval-v4"

    def commitment_record(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "brain_packet_id": self.brain_packet_id,
            "case_id": self.case_id,
            "difficulty": None if self.difficulty is None else self.difficulty.value,
            "domain": None if self.domain is None else self.domain.value,
            "independence_cluster_id": self.independence_cluster_id,
            "problem_digest": self.problem_digest,
            "retrieval_abstention_reason_digest": self.retrieval_abstention_reason_digest,
            "retrieval_disposition": self.retrieval_disposition.value,
            "retrieval_receipt_digest": self.retrieval_receipt_digest,
            "scorer_spec_digest": self.scorer_spec_digest,
            "selection_lane": self.selection_lane,
            "sham_packet_id": self.sham_packet_id,
            "source": self.source.value,
        }


@dataclass(frozen=True, slots=True)
class RandomExternalPilotConfigV4:
    """Commitments that make this a distinct post-failure engineering lane."""

    base: ThreeArmConfig
    authenticated_score_authority_commitment: str
    curated_lane_failure_evidence_digest: str
    external_selection_precommitment_digest: str
    external_selection_precommit_anchor_evidence_digest: str
    external_selection_capability_digest: str
    external_gold_audit_signed_report_digest: str
    internal_selection_precommitment_digest: str
    internal_selection_capability_digest: str
    retrieval_protocol_digest: str
    sham_matching_precommitment_digest: str
    tokenizer_attestation_digest: str
    engineering_proxy_only: bool = True
    external_semantic_balance_claimed: bool = False
    relevance_audit_gates_selection: bool = False
    confirmatory_claim_authorized: bool = False
    served_model_identity_verified: bool = False

    def __post_init__(self) -> None:
        if type(self) is not RandomExternalPilotConfigV4:
            raise ValueError("random-external config subclasses are forbidden")
        if type(self.base) is not ThreeArmConfig or self.base.stage is not StudyStage.PILOT:
            raise ValueError("random-external v4 requires the exact pilot base config")
        self.base.__post_init__()
        for value, label in (
            (
                self.authenticated_score_authority_commitment,
                "authenticated-score authority",
            ),
            (self.curated_lane_failure_evidence_digest, "curated-lane failure evidence"),
            (self.external_selection_precommitment_digest, "external selection precommitment"),
            (
                self.external_selection_precommit_anchor_evidence_digest,
                "external selection precommit anchor evidence",
            ),
            (self.external_selection_capability_digest, "external selection capability"),
            (
                self.external_gold_audit_signed_report_digest,
                "external gold-audit signed report",
            ),
            (self.internal_selection_precommitment_digest, "internal selection precommitment"),
            (self.internal_selection_capability_digest, "internal selection capability"),
            (self.retrieval_protocol_digest, "automatic retrieval protocol"),
            (self.sham_matching_precommitment_digest, "sham matching precommitment"),
            (self.tokenizer_attestation_digest, "tokenizer attestation"),
        ):
            _digest(value, f"v4 {label}")
        if self.authenticated_score_authority_commitment in {
            self.base.scoring_authority_commitment,
            self.base.audit_authority_commitment,
            self.base.analysis_authority_commitment,
        }:
            raise ValueError(
                "authenticated-score authority must be distinct from every legacy authority"
            )
        if (
            self.engineering_proxy_only is not True
            or self.external_semantic_balance_claimed is not False
            or self.relevance_audit_gates_selection is not False
            or self.confirmatory_claim_authorized is not False
            or self.served_model_identity_verified is not False
        ):
            raise ValueError("random-external v4 config overclaims its authority")

    def to_dict(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "authenticated_score_authority_commitment": (
                self.authenticated_score_authority_commitment
            ),
            "base_config": self.base.to_dict(),
            "confirmatory_claim_authorized": self.confirmatory_claim_authorized,
            "curated_lane_failure_evidence_digest": (self.curated_lane_failure_evidence_digest),
            "engineering_proxy_only": self.engineering_proxy_only,
            "external_selection_precommitment_digest": (
                self.external_selection_precommitment_digest
            ),
            "external_selection_precommit_anchor_evidence_digest": (
                self.external_selection_precommit_anchor_evidence_digest
            ),
            "external_selection_capability_digest": (self.external_selection_capability_digest),
            "external_gold_audit_signed_report_digest": (
                self.external_gold_audit_signed_report_digest
            ),
            "external_semantic_balance_claimed": self.external_semantic_balance_claimed,
            "internal_selection_precommitment_digest": (
                self.internal_selection_precommitment_digest
            ),
            "internal_selection_capability_digest": (self.internal_selection_capability_digest),
            "relevance_audit_gates_selection": self.relevance_audit_gates_selection,
            "retrieval_protocol_digest": self.retrieval_protocol_digest,
            "schema": RANDOM_EXTERNAL_PILOT_SCHEMA_V4,
            "served_model_identity_verified": self.served_model_identity_verified,
            "sham_matching_precommitment_digest": (self.sham_matching_precommitment_digest),
            "tokenizer_attestation_digest": self.tokenizer_attestation_digest,
        }

    @property
    def digest(self) -> str:
        return _digest_json(self.to_dict())


def _normalized_cases(
    cases: Sequence[RandomExternalPilotCaseV4],
) -> tuple[RandomExternalPilotCaseV4, ...]:
    if type(cases) not in {list, tuple}:
        raise ValueError("v4 cases must be a bounded sequence")
    result = tuple(cases)
    if len(result) != RANDOM_EXTERNAL_PILOT_CASE_COUNT_V4 or any(
        type(case) is not RandomExternalPilotCaseV4 for case in result
    ):
        raise ValueError("v4 requires exactly 24 exact cases")
    for case in result:
        case.__post_init__()
    for label, values in (
        ("case id", tuple(case.case_id for case in result)),
        ("independence cluster", tuple(case.independence_cluster_id for case in result)),
        ("problem body", tuple(case.problem_digest for case in result)),
    ):
        if len(set(values)) != RANDOM_EXTERNAL_PILOT_CASE_COUNT_V4:
            raise ValueError(f"v4 repeats a {label}")
    source_counts = Counter(case.source for case in result)
    if source_counts != {
        ProblemSource.INTERNAL: RANDOM_EXTERNAL_PILOT_SOURCE_CASE_COUNT_V4,
        ProblemSource.EXTERNAL: RANDOM_EXTERNAL_PILOT_SOURCE_CASE_COUNT_V4,
    }:
        raise ValueError("v4 requires exactly twelve cases per provenance source")
    internal_cells = Counter(
        (case.domain, case.difficulty) for case in result if case.source is ProblemSource.INTERNAL
    )
    expected_cells = {
        (domain, difficulty) for domain in MathDomain for difficulty in DifficultyBand
    }
    if set(internal_cells) != expected_cells or set(internal_cells.values()) != {1}:
        raise ValueError("v4 internal cases must fill every audited semantic cell once")
    return tuple(sorted(result, key=lambda case: case.case_id))


def _normalized_packets(packets: Sequence[FrozenPacket]) -> tuple[FrozenPacket, ...]:
    if type(packets) not in {list, tuple}:
        raise ValueError("v4 packets must be a bounded sequence")
    result = tuple(packets)
    if len(result) > RANDOM_EXTERNAL_PILOT_CASE_COUNT_V4 or any(
        type(packet) is not FrozenPacket for packet in result
    ):
        raise ValueError("v4 packet universe is invalid")
    for packet in result:
        packet.__post_init__()
    if len({packet.packet_id for packet in result}) != len(result):
        raise ValueError("v4 packet ids must be unique")
    return tuple(sorted(result, key=lambda packet: packet.packet_id))


def _validate_packet_bindings(
    cases: Sequence[RandomExternalPilotCaseV4], packets: Sequence[FrozenPacket]
) -> None:
    packet_by_id = {packet.packet_id: packet for packet in packets}
    selected = tuple(
        case
        for case in cases
        if case.retrieval_disposition is AutomaticRetrievalDispositionV4.SELECTED
    )
    brain_ids = tuple(case.brain_packet_id for case in selected)
    sham_ids = tuple(case.sham_packet_id for case in selected)
    if (
        len(packet_by_id) != len(selected)
        or len(set(brain_ids)) != len(selected)
        or len(set(sham_ids)) != len(selected)
    ):
        raise ValueError("v4 selected brain/sham mappings must be one-to-one")
    if set(brain_ids) != set(packet_by_id) or set(sham_ids) != set(packet_by_id):
        raise ValueError("v4 selected brain/sham mappings must each use every packet once")
    if len(packet_by_id) == 1:
        raise ValueError("one selected packet cannot form a sham derangement")
    if packets and (
        len({packet.snapshot_manifest_digest for packet in packets}) != 1
        or len({packet.retrieval_policy_digest for packet in packets}) != 1
    ):
        raise ValueError("v4 packets must share one snapshot and retrieval policy")
    for case in selected:
        brain = packet_by_id[case.brain_packet_id]
        sham = packet_by_id[case.sham_packet_id]
        if brain.origin_case_id != case.case_id or sham.origin_case_id == case.case_id:
            raise ValueError("v4 brain origin or sham derangement is invalid")
        if brain.text_digest == sham.text_digest:
            raise ValueError("v4 brain and sham packet text must differ")
        if brain.matching_key != sham.matching_key:
            raise ValueError("v4 brain and sham packets are not byte/token-shape matched")
        if set(brain.source_digests) & set(sham.source_digests):
            raise ValueError("v4 brain and sham packets reuse a source")


def _source_order_counter(
    cases: Sequence[RandomExternalPilotCaseV4],
    assignments: Sequence[ArmAssignment],
) -> dict[ProblemSource, Counter[tuple[StudyArm, StudyArm, StudyArm]]]:
    case_by_id = {case.case_id: case for case in cases}
    grouped: dict[str, list[ArmAssignment]] = defaultdict(list)
    for assignment in assignments:
        grouped[assignment.case_id].append(assignment)
    counters = {source: Counter() for source in ProblemSource}
    for case_id, values in grouped.items():
        ordered = tuple(
            item.arm for item in sorted(values, key=lambda item: item.within_case_order)
        )
        if len(ordered) != 3:
            raise ValueError("v4 case lacks one complete arm order")
        counters[case_by_id[case_id].source][ordered] += 1
    return counters


@dataclass(frozen=True, slots=True)
class RandomExternalPilotPlanV4:
    config: RandomExternalPilotConfigV4
    cases: tuple[RandomExternalPilotCaseV4, ...] = field(repr=False)
    packets: tuple[FrozenPacket, ...] = field(repr=False)
    assignments: tuple[ArmAssignment, ...] = field(repr=False)
    case_universe_digest: str
    packet_universe_digest: str
    allocation_seed_commitment: str
    schedule_digest: str

    def __post_init__(self) -> None:
        if type(self) is not RandomExternalPilotPlanV4:
            raise ValueError("random-external plan subclasses are forbidden")
        if type(self.config) is not RandomExternalPilotConfigV4:
            raise ValueError("v4 plan requires the exact random-external config")
        self.config.__post_init__()
        if (
            type(self.cases) is not tuple
            or type(self.packets) is not tuple
            or type(self.assignments) is not tuple
        ):
            raise ValueError("v4 plan records must be exact tuples")
        cases = _normalized_cases(self.cases)
        packets = _normalized_packets(self.packets)
        if cases != self.cases or packets != self.packets:
            raise ValueError("v4 plan universes are not canonically ordered")
        _validate_packet_bindings(cases, packets)
        for value, label in (
            (self.case_universe_digest, "v4 case universe"),
            (self.packet_universe_digest, "v4 packet universe"),
            (self.allocation_seed_commitment, "v4 allocation seed commitment"),
            (self.schedule_digest, "v4 schedule"),
        ):
            _digest(value, label)
        if self.case_universe_digest != _digest_json([case.commitment_record() for case in cases]):
            raise ValueError("v4 case universe differs from its commitment")
        if self.packet_universe_digest != _digest_json(
            [packet.commitment_record() for packet in packets]
        ):
            raise ValueError("v4 packet universe differs from its commitment")
        if len(self.assignments) != RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4 or any(
            type(item) is not ArmAssignment for item in self.assignments
        ):
            raise ValueError("v4 plan requires exactly 72 exact assignments")
        for item in self.assignments:
            item.__post_init__()
        if tuple(item.schedule_index for item in self.assignments) != tuple(
            range(RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4)
        ):
            raise ValueError("v4 assignments must retain exact schedule order")
        if self.schedule_digest != _digest_json(
            [item.commitment_record() for item in self.assignments]
        ):
            raise ValueError("v4 schedule differs from its commitment")
        if len({item.task_id for item in self.assignments}) != (
            RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4
        ):
            raise ValueError("v4 task ids must be globally unique")
        case_by_id = {case.case_id: case for case in cases}
        packet_by_id = {packet.packet_id: packet for packet in packets}
        grouped: dict[str, list[ArmAssignment]] = defaultdict(list)
        for item in self.assignments:
            if item.case_id not in case_by_id:
                raise ValueError("v4 assignment references an unknown case")
            grouped[item.case_id].append(item)
            case = case_by_id[item.case_id]
            expected_packet: str | None
            if item.arm is StudyArm.BRAIN:
                expected_packet = case.brain_packet_id
            elif item.arm is StudyArm.SHAM:
                expected_packet = case.sham_packet_id
            else:
                expected_packet = None
            context = "" if expected_packet is None else packet_by_id[expected_packet].text
            if (
                item.packet_id != expected_packet
                or item.problem_digest != case.problem_digest
                or item.context_digest != _sha256(context.encode("utf-8"))
            ):
                raise ValueError("v4 assignment differs from its case/packet binding")
        if set(grouped) != set(case_by_id):
            raise ValueError("v4 assignment and case universes differ")
        for values in grouped.values():
            if (
                len(values) != 3
                or {item.arm for item in values} != set(StudyArm)
                or {item.within_case_order for item in values} != {0, 1, 2}
            ):
                raise ValueError("v4 case does not have exactly one slot per arm")
        expected_order_counts = Counter({order: 2 for order in permutations(tuple(StudyArm))})
        for counter in _source_order_counter(cases, self.assignments).values():
            if counter != expected_order_counts:
                raise ValueError("v4 service order is not exactly balanced within source")

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
        packet_by_id = {packet.packet_id: packet for packet in self.packets}
        context = "" if assignment.packet_id is None else packet_by_id[assignment.packet_id].text
        task = SolverTask(
            task_id=assignment.task_id,
            problem_text=case.problem_text,
            brain_context=context,
            problem_digest=case.problem_digest,
            context_digest=_sha256(context.encode("utf-8")),
        )
        if task.problem_digest != assignment.problem_digest or task.context_digest != (
            assignment.context_digest
        ):
            raise ValueError("v4 solver projection differs from the sealed assignment")
        return task

    def public_precommitment(self) -> dict[str, object]:
        selected = sum(
            case.retrieval_disposition is AutomaticRetrievalDispositionV4.SELECTED
            for case in self.cases
        )
        return {
            "allocation_seed_commitment": self.allocation_seed_commitment,
            "automatic_retrieval_abstained_case_count": len(self.cases) - selected,
            "automatic_retrieval_selected_case_count": selected,
            "case_count": len(self.cases),
            "case_universe_digest": self.case_universe_digest,
            "config_digest": self.config.digest,
            "curated_external_lane_pooled": False,
            "external_semantic_balance_claimed": False,
            "internal_semantic_balance": "one_case_per_domain_difficulty_cell",
            "packet_universe_digest": self.packet_universe_digest,
            "plan_digest": self.digest,
            "relevance_audit_gates_selection": False,
            "schema": RANDOM_EXTERNAL_PILOT_SCHEMA_V4,
            "schedule_digest": self.schedule_digest,
            "source_counts": {"external": 12, "internal": 12},
            "source_service_order_balance": "each_of_six_orders_exactly_twice",
            "stage": "engineering_proxy",
            "task_count": len(self.assignments),
        }

    @property
    def public_precommitment_digest(self) -> str:
        return _digest_json(self.public_precommitment())


def build_random_external_pilot_plan_v4(
    cases: Sequence[RandomExternalPilotCaseV4],
    packets: Sequence[FrozenPacket],
    *,
    config: RandomExternalPilotConfigV4,
    allocation_seed: bytes,
) -> RandomExternalPilotPlanV4:
    """Build the exact ITT plan without assigning external semantic labels."""

    if type(config) is not RandomExternalPilotConfigV4:
        raise ValueError("v4 plan builder requires the exact config")
    config.__post_init__()
    seed = _seed(allocation_seed)
    normalized_cases = _normalized_cases(cases)
    normalized_packets = _normalized_packets(packets)
    _validate_packet_bindings(normalized_cases, normalized_packets)
    case_digest = _digest_json([case.commitment_record() for case in normalized_cases])
    packet_digest = _digest_json([packet.commitment_record() for packet in normalized_packets])
    design_digest = _digest_json(
        {
            "case_universe_digest": case_digest,
            "config_digest": config.digest,
            "packet_universe_digest": packet_digest,
        }
    )
    seed_commitment = _seed_commitment(seed, design_digest)
    packet_by_id = {packet.packet_id: packet for packet in normalized_packets}
    arm_orders = tuple(permutations(tuple(StudyArm)))
    order_by_case: dict[str, tuple[StudyArm, StudyArm, StudyArm]] = {}
    for source in ProblemSource:
        source_cases = sorted(
            (case for case in normalized_cases if case.source is source),
            key=lambda case: _rank(seed, "source-case-order", source.value, case.case_id),
        )
        instances = [(order, replicate) for order in arm_orders for replicate in range(2)]
        ranked_instances = sorted(
            instances,
            key=lambda item: _rank(
                seed,
                "source-arm-order",
                source.value,
                item[1],
                *(arm.value for arm in item[0]),
            ),
        )
        for case, (order, _replicate) in zip(source_cases, ranked_instances, strict=True):
            order_by_case[case.case_id] = order
    scheduled_cases = sorted(
        normalized_cases,
        key=lambda case: _rank(seed, "global-case-schedule", case.case_id),
    )
    assignments: list[ArmAssignment] = []
    for case in scheduled_cases:
        for within_case_order, arm in enumerate(order_by_case[case.case_id]):
            if arm is StudyArm.BRAIN:
                packet_id = case.brain_packet_id
            elif arm is StudyArm.SHAM:
                packet_id = case.sham_packet_id
            else:
                packet_id = None
            context = "" if packet_id is None else packet_by_id[packet_id].text
            task_id = _sha256(
                _TASK_DOMAIN
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
                    context_digest=_sha256(context.encode("utf-8")),
                )
            )
    assignment_tuple = tuple(assignments)
    return RandomExternalPilotPlanV4(
        config=config,
        cases=normalized_cases,
        packets=normalized_packets,
        assignments=assignment_tuple,
        case_universe_digest=case_digest,
        packet_universe_digest=packet_digest,
        allocation_seed_commitment=seed_commitment,
        schedule_digest=_digest_json(
            [assignment.commitment_record() for assignment in assignment_tuple]
        ),
    )


def verify_random_external_pilot_plan_v4(
    plan: RandomExternalPilotPlanV4,
    *,
    allocation_seed: bytes,
    expected_public_precommitment_digest: str,
) -> None:
    """Cold-rebuild the complete v4 plan from its separately revealed seed."""

    if type(plan) is not RandomExternalPilotPlanV4:
        raise ValueError("v4 plan verification requires the exact plan type")
    plan.__post_init__()
    expected = _digest(expected_public_precommitment_digest, "expected v4 precommitment")
    if not hmac.compare_digest(expected, plan.public_precommitment_digest):
        raise ValueError("v4 plan differs from its externally expected precommitment")
    rebuilt = build_random_external_pilot_plan_v4(
        plan.cases,
        plan.packets,
        config=plan.config,
        allocation_seed=allocation_seed,
    )
    if rebuilt != plan:
        raise ValueError("revealed v4 allocation seed does not rebuild the plan")


__all__ = [
    "RANDOM_EXTERNAL_PILOT_CASE_COUNT_V4",
    "RANDOM_EXTERNAL_PILOT_SCHEMA_V4",
    "RANDOM_EXTERNAL_PILOT_SOURCE_CASE_COUNT_V4",
    "RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4",
    "AutomaticRetrievalDispositionV4",
    "RandomExternalPilotCaseV4",
    "RandomExternalPilotConfigV4",
    "RandomExternalPilotPlanV4",
    "build_random_external_pilot_plan_v4",
    "verify_random_external_pilot_plan_v4",
]
