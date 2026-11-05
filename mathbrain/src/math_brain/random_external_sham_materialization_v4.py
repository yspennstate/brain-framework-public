"""Retrieval-to-packet, sham, and plan-binding authority for pilot v4.

This module is the narrow bridge between domain-free retrieval and the random-external
three-arm plan.  It retains every retrieval abstention as an ITT case, turns selected
drafts into exact token-attested :class:`FrozenPacket` records, and finds a deterministic
source-disjoint bijective derangement.  Zero selected packets is a valid empty-context
lane; exactly one selected packet is terminally infeasible because no derangement exists.

The public builder is useful for freezing commitments.  Authority comes only from
``cold_replay_random_external_sham_materialization_v4``: it reruns domain-free retrieval
from the canonical case bytes, rematerializes tokens and padding, rebuilds the sham
matching, and returns an unforgeable-in-process capability.  No caller-supplied
``verified`` boolean or free-form capability label is accepted.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections import Counter
from dataclasses import dataclass, field

from .brain_corpus_v3 import BRAIN_PACKET_FORMAT_SHA256_V3, BrainCorpusManifestV3
from .domain_free_retrieval_v4 import (
    DomainFreeRetrievalDispositionV4,
    DomainFreeRetrievalExecutionManifestV4,
    DomainFreeRetrievalPolicyV4,
    DomainFreeRetrievalPreparationV4,
    cold_replay_domain_free_retrieval_v4,
)
from .models import canonical_json
from .random_external_tokenizer_attestation_v4 import (
    PacketPaddingInputV4,
    PacketPaddingPolicyV4,
    RandomExternalTokenizerAttestationV4,
    materialize_random_external_tokenizer_attestation_v4,
)
from .three_arm_evaluation import (
    DifficultyBand,
    FrozenPacket,
    MathDomain,
    ProblemSource,
)
from .three_arm_random_external_pilot_v4 import (
    RANDOM_EXTERNAL_PILOT_CASE_COUNT_V4,
    RANDOM_EXTERNAL_PILOT_SOURCE_CASE_COUNT_V4,
    AutomaticRetrievalDispositionV4,
    RandomExternalPilotCaseV4,
    RandomExternalPilotPlanV4,
)
from .three_arm_tokenizer_attestation_v3 import VerifiedTokenizerSuiteV3

RANDOM_EXTERNAL_SHAM_MATERIALIZATION_SCHEMA_V4 = (
    "org.math-research-brain/random-external-sham-materialization/v4"
)
RANDOM_EXTERNAL_SHAM_MATCHING_SCHEMA_V4 = "org.math-research-brain/random-external-sham-matching/v4"
RANDOM_EXTERNAL_MATERIALIZATION_REPLAY_SCHEMA_V4 = (
    "org.math-research-brain/random-external-materialization-cold-replay/v4"
)
RANDOM_EXTERNAL_PLAN_MATERIALIZATION_BINDING_SCHEMA_V4 = (
    "org.math-research-brain/random-external-plan-materialization-binding/v4"
)

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_CASE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}\Z", re.ASCII)
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}\Z", re.ASCII)
_MATERIALIZATION_DOMAIN = "math-brain/random-external-sham-materialization-v4"
_DESCRIPTOR_DOMAIN = "math-brain/random-external-case-descriptors-v4"
_ASSIGNMENT_DOMAIN = "math-brain/random-external-sham-assignments-v4"
_MATCHING_DOMAIN = "math-brain/random-external-sham-matching-v4"
_MATCHING_SEED_DOMAIN = b"math-brain/random-external-sham-matching-v4/seed\x00"
_MATCHING_RANK_DOMAIN = b"math-brain/random-external-sham-matching-v4/rank\x00"
_PACKET_ID_DOMAIN = "math-brain/random-external-frozen-packet-id-v4"
_ABSTENTION_DOMAIN = "math-brain/random-external-retrieval-abstention-v4"
_REPLAY_DOMAIN = "math-brain/random-external-materialization-cold-replay-v4"
_PLAN_BINDING_DOMAIN = "math-brain/random-external-plan-materialization-binding-v4"
_REPLAY_SEAL = object()
_PLAN_BINDING_SEAL = object()


class RandomExternalMaterializationError(ValueError):
    """Raised when retrieval, packet, sham, or plan lineage fails closed."""


class RandomExternalShamInfeasible(RandomExternalMaterializationError):
    """Raised when selected packets cannot form the preregistered derangement."""


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _plain_digest(value: object) -> str:
    return _sha256(canonical_json(value).encode("utf-8"))


def _domain_digest(domain: str, value: object) -> str:
    return _sha256(domain.encode("ascii") + b"\x00" + canonical_json(value).encode("utf-8"))


def _digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise RandomExternalMaterializationError(f"{label} must be a canonical SHA-256 digest")
    return value


def _identifier(value: object, label: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise RandomExternalMaterializationError(f"{label} is invalid")
    return value


def _secret(value: object) -> bytes:
    if type(value) is not bytes or len(value) < 32:
        raise RandomExternalMaterializationError(
            "v4 sham-matching seed must contain at least 256 bits"
        )
    return value


def _rank(seed: bytes, role: str, design_digest: str, *parts: object) -> bytes:
    material = bytearray(_MATCHING_RANK_DOMAIN)
    for item in (role, design_digest, *parts):
        encoded = str(item).encode("utf-8")
        material.extend(len(encoded).to_bytes(4, "big"))
        material.extend(encoded)
    return hmac.new(seed, bytes(material), hashlib.sha256).digest()


def _seed_commitment(seed: bytes, design_digest: str) -> str:
    return _sha256(_MATCHING_SEED_DOMAIN + design_digest.encode("ascii") + b"\x00" + seed)


@dataclass(frozen=True, slots=True)
class RandomExternalCaseDescriptorV4:
    """Selection-side plan metadata absent from the retrieval query."""

    case_id: str
    source: ProblemSource
    domain: MathDomain | None
    difficulty: DifficultyBand | None
    independence_cluster_id: str
    scorer_spec_digest: str

    def __post_init__(self) -> None:
        if type(self) is not RandomExternalCaseDescriptorV4:
            raise RandomExternalMaterializationError("case-descriptor subclasses are forbidden")
        if type(self.case_id) is not str or _CASE_ID_RE.fullmatch(self.case_id) is None:
            raise RandomExternalMaterializationError("case-descriptor id is invalid")
        _identifier(self.independence_cluster_id, "case-descriptor independence cluster")
        _digest(self.scorer_spec_digest, "case-descriptor scorer specification")
        if type(self.source) is not ProblemSource:
            raise RandomExternalMaterializationError("case-descriptor source must be exact")
        if self.source is ProblemSource.INTERNAL:
            if type(self.domain) is not MathDomain or type(self.difficulty) is not DifficultyBand:
                raise RandomExternalMaterializationError(
                    "internal case descriptors require audited domain and difficulty"
                )
        elif self.domain is not None or self.difficulty is not None:
            raise RandomExternalMaterializationError(
                "random external descriptors must not invent semantic labels"
            )

    def commitment_record(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "case_id": self.case_id,
            "difficulty": None if self.difficulty is None else self.difficulty.value,
            "domain": None if self.domain is None else self.domain.value,
            "independence_cluster_id": self.independence_cluster_id,
            "scorer_spec_digest": self.scorer_spec_digest,
            "source": self.source.value,
        }


@dataclass(frozen=True, slots=True)
class RandomExternalShamAssignmentV4:
    case_id: str
    brain_packet_id: str
    sham_packet_id: str

    def __post_init__(self) -> None:
        if type(self) is not RandomExternalShamAssignmentV4:
            raise RandomExternalMaterializationError("sham-assignment subclasses are forbidden")
        _identifier(self.case_id, "sham-assignment case id")
        _identifier(self.brain_packet_id, "brain packet id")
        _identifier(self.sham_packet_id, "sham packet id")
        if self.brain_packet_id == self.sham_packet_id:
            raise RandomExternalMaterializationError("sham assignment is not a derangement")

    def commitment_record(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "brain_packet_id": self.brain_packet_id,
            "case_id": self.case_id,
            "sham_packet_id": self.sham_packet_id,
        }


def _normalized_descriptors(
    descriptors: tuple[RandomExternalCaseDescriptorV4, ...] | list[RandomExternalCaseDescriptorV4],
) -> tuple[RandomExternalCaseDescriptorV4, ...]:
    if type(descriptors) not in {list, tuple}:
        raise RandomExternalMaterializationError("case descriptors must be a bounded sequence")
    result = tuple(descriptors)
    if len(result) != RANDOM_EXTERNAL_PILOT_CASE_COUNT_V4 or any(
        type(item) is not RandomExternalCaseDescriptorV4 for item in result
    ):
        raise RandomExternalMaterializationError("v4 requires exactly 24 case descriptors")
    for item in result:
        item.__post_init__()
    case_ids = tuple(item.case_id for item in result)
    if tuple(sorted(set(case_ids))) != case_ids:
        raise RandomExternalMaterializationError(
            "case descriptors must be unique and sorted by case id"
        )
    if len({item.independence_cluster_id for item in result}) != len(result):
        raise RandomExternalMaterializationError("case descriptors repeat an independence cluster")
    counts = Counter(item.source for item in result)
    if counts != {
        ProblemSource.INTERNAL: RANDOM_EXTERNAL_PILOT_SOURCE_CASE_COUNT_V4,
        ProblemSource.EXTERNAL: RANDOM_EXTERNAL_PILOT_SOURCE_CASE_COUNT_V4,
    }:
        raise RandomExternalMaterializationError("v4 requires twelve descriptors per source")
    internal_cells = Counter(
        (item.domain, item.difficulty) for item in result if item.source is ProblemSource.INTERNAL
    )
    expected_cells = {
        (domain, difficulty) for domain in MathDomain for difficulty in DifficultyBand
    }
    if set(internal_cells) != expected_cells or set(internal_cells.values()) != {1}:
        raise RandomExternalMaterializationError(
            "internal descriptors must fill each domain-difficulty cell once"
        )
    return result


def _packet_id(
    *,
    case_id: str,
    draft_digest: str,
    padding_receipt_digest: str,
    tokenizer_attestation_digest: str,
) -> str:
    digest = _domain_digest(
        _PACKET_ID_DOMAIN,
        {
            "case_id": case_id,
            "draft_digest": draft_digest,
            "padding_receipt_digest": padding_receipt_digest,
            "tokenizer_attestation_digest": tokenizer_attestation_digest,
        },
    )
    return "packetv4_" + digest.removeprefix("sha256:")


def _abstention_reason_digest(receipt: object) -> str:
    reason = getattr(receipt, "abstention_reason", None)
    content_digest = getattr(receipt, "content_digest", None)
    if reason is None:
        raise RandomExternalMaterializationError("abstained receipt lacks an exact reason")
    return _domain_digest(
        _ABSTENTION_DOMAIN,
        {
            "abstention_reason": reason.value,
            "retrieval_receipt_digest": content_digest,
        },
    )


def _matching_compatible(brain: FrozenPacket, candidate: FrozenPacket) -> bool:
    return (
        candidate.packet_id != brain.packet_id
        and candidate.origin_case_id != brain.origin_case_id
        and brain.text_digest != candidate.text_digest
        and brain.matching_key == candidate.matching_key
        and not set(brain.source_digests).intersection(candidate.source_digests)
    )


def _build_matching(
    packets: tuple[FrozenPacket, ...],
    *,
    seed: bytes,
    design_digest: str,
) -> tuple[RandomExternalShamAssignmentV4, ...]:
    if not packets:
        return ()
    if len(packets) == 1:
        raise RandomExternalShamInfeasible(
            "one selected packet cannot form a source-disjoint sham derangement"
        )
    packet_by_case = {packet.origin_case_id: packet for packet in packets}
    if len(packet_by_case) != len(packets):
        raise RandomExternalMaterializationError("selected packets repeat an origin case")
    candidates: dict[str, tuple[str, ...]] = {}
    for brain in packets:
        candidates[brain.origin_case_id] = tuple(
            candidate.packet_id for candidate in packets if _matching_compatible(brain, candidate)
        )
        if not candidates[brain.origin_case_id]:
            raise RandomExternalShamInfeasible(
                f"no source-disjoint sham packet for {brain.origin_case_id}"
            )
    ordered_cases = sorted(
        packet_by_case,
        key=lambda case_id: (
            _rank(seed, "case-order", design_digest, case_id),
            case_id,
        ),
    )
    ordered_candidates = {
        case_id: tuple(
            sorted(
                packet_ids,
                key=lambda packet_id: (
                    _rank(
                        seed,
                        "candidate-order",
                        design_digest,
                        case_id,
                        packet_id,
                    ),
                    packet_id,
                ),
            )
        )
        for case_id, packet_ids in candidates.items()
    }
    packet_owner: dict[str, str] = {}

    def augment(case_id: str, visited: set[str]) -> bool:
        for packet_id in ordered_candidates[case_id]:
            if packet_id in visited:
                continue
            visited.add(packet_id)
            owner = packet_owner.get(packet_id)
            if owner is None or augment(owner, visited):
                packet_owner[packet_id] = case_id
                return True
        return False

    for case_id in ordered_cases:
        if not augment(case_id, set()):
            raise RandomExternalShamInfeasible(
                "selected packet graph has no source-disjoint perfect derangement"
            )
    sham_by_case = {case_id: packet_id for packet_id, case_id in packet_owner.items()}
    if set(sham_by_case) != set(packet_by_case) or set(packet_owner) != {
        packet.packet_id for packet in packets
    }:
        raise RandomExternalMaterializationError("sham search did not consume its exact universe")
    return tuple(
        RandomExternalShamAssignmentV4(
            case_id=case_id,
            brain_packet_id=packet_by_case[case_id].packet_id,
            sham_packet_id=sham_by_case[case_id],
        )
        for case_id in sorted(packet_by_case)
    )


@dataclass(frozen=True, slots=True)
class RandomExternalShamMaterializationV4:
    retrieval_preparation: DomainFreeRetrievalPreparationV4 = field(repr=False)
    descriptors: tuple[RandomExternalCaseDescriptorV4, ...] = field(repr=False)
    tokenizer_attestation: RandomExternalTokenizerAttestationV4 = field(repr=False)
    packets: tuple[FrozenPacket, ...] = field(repr=False)
    cases: tuple[RandomExternalPilotCaseV4, ...] = field(repr=False)
    assignments: tuple[RandomExternalShamAssignmentV4, ...]
    descriptor_universe_digest: str
    packet_universe_digest: str
    assignment_digest: str
    matching_seed_commitment: str
    sham_matching_precommitment_digest: str
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not RandomExternalShamMaterializationV4:
            raise RandomExternalMaterializationError(
                "sham-materialization subclasses are forbidden"
            )
        self._validate_fields()
        object.__setattr__(
            self,
            "content_digest",
            _domain_digest(_MATERIALIZATION_DOMAIN, self._payload()),
        )

    @property
    def selected_count(self) -> int:
        return len(self.packets)

    @property
    def abstained_count(self) -> int:
        return len(self.cases) - len(self.packets)

    @property
    def tokenizer_attestation_digest(self) -> str:
        return self.tokenizer_attestation.content_digest

    @property
    def retrieval_protocol_digest(self) -> str:
        return self.retrieval_preparation.policy_digest

    @property
    def served_model_identity_verified(self) -> bool:
        return False

    @property
    def confirmatory_claim_authorized(self) -> bool:
        return False

    def _matching_record(self) -> dict[str, object]:
        return {
            "assignment_digest": self.assignment_digest,
            "bijective_derangement_required": True,
            "matching_seed_commitment": self.matching_seed_commitment,
            "packet_universe_digest": self.packet_universe_digest,
            "schema": RANDOM_EXTERNAL_SHAM_MATCHING_SCHEMA_V4,
            "selected_count": self.selected_count,
            "singleton_selected_infeasible": True,
            "source_disjoint_required": True,
            "zero_selected_valid": True,
        }

    def _validate_fields(self) -> None:
        if type(self.retrieval_preparation) is not DomainFreeRetrievalPreparationV4:
            raise RandomExternalMaterializationError(
                "sham materialization requires exact retrieval preparation"
            )
        self.retrieval_preparation.validate_current()
        if len(self.retrieval_preparation.cases) != RANDOM_EXTERNAL_PILOT_CASE_COUNT_V4:
            raise RandomExternalMaterializationError(
                "v4 materialization requires 24 retrieval cases"
            )
        descriptors = _normalized_descriptors(self.descriptors)
        if descriptors != self.descriptors:
            raise RandomExternalMaterializationError("case descriptors are not canonical")
        retrieval_cases = self.retrieval_preparation.cases
        if tuple(item.case_id for item in descriptors) != tuple(
            item.case_id for item in retrieval_cases
        ) or any(
            descriptor.source is not retrieval_case.source
            for descriptor, retrieval_case in zip(descriptors, retrieval_cases, strict=True)
        ):
            raise RandomExternalMaterializationError(
                "case descriptors differ from the retrieval case universe"
            )
        if len({item.problem_digest for item in retrieval_cases}) != len(retrieval_cases):
            raise RandomExternalMaterializationError("v4 retrieval cases repeat a problem body")
        if type(self.tokenizer_attestation) is not RandomExternalTokenizerAttestationV4:
            raise RandomExternalMaterializationError(
                "sham materialization requires exact tokenizer evidence"
            )
        self.tokenizer_attestation.validate_current()
        if (
            self.tokenizer_attestation.policy.base_format_template_digest
            != BRAIN_PACKET_FORMAT_SHA256_V3
        ):
            raise RandomExternalMaterializationError(
                "packet-padding policy is not bound to the frozen retrieval renderer"
            )
        drafts = self.retrieval_preparation.drafts
        expected_inputs = tuple(
            PacketPaddingInputV4(
                case_id=draft.case_id,
                draft_digest=draft.content_digest,
                input_text=draft.render_fragment,
                input_format_template_digest=draft.format_template_digest,
            )
            for draft in drafts
        )
        if self.tokenizer_attestation.inputs != expected_inputs:
            raise RandomExternalMaterializationError(
                "tokenizer evidence differs from selected retrieval drafts"
            )
        if len(drafts) == 1:
            raise RandomExternalShamInfeasible(
                "one selected packet cannot form a source-disjoint sham derangement"
            )
        if type(self.packets) is not tuple or len(self.packets) != len(drafts):
            raise RandomExternalMaterializationError("packet universe differs from selected drafts")
        if any(type(packet) is not FrozenPacket for packet in self.packets):
            raise RandomExternalMaterializationError("packet universe must contain exact records")
        for packet in self.packets:
            packet.__post_init__()
        if tuple(packet.packet_id for packet in self.packets) != tuple(
            sorted(packet.packet_id for packet in self.packets)
        ):
            raise RandomExternalMaterializationError("packets must be in canonical id order")
        if len({packet.packet_id for packet in self.packets}) != len(self.packets):
            raise RandomExternalMaterializationError("packet ids must be unique")
        draft_by_case = {draft.case_id: draft for draft in drafts}
        receipt_by_case = {
            receipt.case_id: receipt for receipt in self.retrieval_preparation.receipts
        }
        padding_by_case = {
            receipt.case_id: receipt for receipt in self.tokenizer_attestation.receipts
        }
        card_by_id = {card.record_id: card for card in self.retrieval_preparation.corpus.cards}
        packet_by_case = {packet.origin_case_id: packet for packet in self.packets}
        if set(packet_by_case) != set(draft_by_case):
            raise RandomExternalMaterializationError("packet origins differ from selected drafts")
        for case_id, draft in draft_by_case.items():
            retrieval_receipt = receipt_by_case[case_id]
            padding_receipt = padding_by_case[case_id]
            packet = packet_by_case[case_id]
            selected_card = card_by_id[retrieval_receipt.selected_record_ids[0]]
            expected_id = _packet_id(
                case_id=case_id,
                draft_digest=draft.content_digest,
                padding_receipt_digest=padding_receipt.content_digest,
                tokenizer_attestation_digest=self.tokenizer_attestation.content_digest,
            )
            if (
                packet.packet_id != expected_id
                or packet.text != padding_receipt.final_text
                or packet.snapshot_manifest_digest != self.retrieval_preparation.corpus_digest
                or packet.retrieval_policy_digest != self.retrieval_preparation.policy_digest
                or packet.query_digest != draft.query_digest
                or packet.format_template_digest
                != self.tokenizer_attestation.policy.output_format_template_digest
                or packet.section_shape_digest != draft.section_shape_digest
                or packet.age_status_vector_digest != draft.age_status_vector_digest
                or packet.source_digests != draft.source_digests
                or packet.relevance_keys != (selected_card.family,)
                or packet.tokenizer_receipts != padding_receipt.measurement.tokenizer_receipts
            ):
                raise RandomExternalMaterializationError(
                    "frozen packet differs from retrieval and tokenizer lineage"
                )
        if self.packets and len({packet.matching_key for packet in self.packets}) != 1:
            raise RandomExternalMaterializationError(
                "selected packets do not share one exact matching signature"
            )
        for value, label in (
            (self.descriptor_universe_digest, "case-descriptor universe"),
            (self.packet_universe_digest, "packet universe"),
            (self.assignment_digest, "sham assignment"),
            (self.matching_seed_commitment, "sham matching seed"),
            (self.sham_matching_precommitment_digest, "sham matching precommitment"),
        ):
            _digest(value, label)
        expected_descriptors = _domain_digest(
            _DESCRIPTOR_DOMAIN,
            [item.commitment_record() for item in self.descriptors],
        )
        expected_packets = _plain_digest([packet.commitment_record() for packet in self.packets])
        if self.descriptor_universe_digest != expected_descriptors:
            raise RandomExternalMaterializationError("descriptor-universe digest is stale")
        if self.packet_universe_digest != expected_packets:
            raise RandomExternalMaterializationError("packet-universe digest is stale")
        if type(self.assignments) is not tuple or len(self.assignments) != len(self.packets):
            raise RandomExternalMaterializationError(
                "sham assignment count differs from selected packets"
            )
        if any(type(item) is not RandomExternalShamAssignmentV4 for item in self.assignments):
            raise RandomExternalMaterializationError("sham assignments must be exact records")
        for item in self.assignments:
            item.__post_init__()
        if tuple(item.case_id for item in self.assignments) != tuple(
            sorted(item.case_id for item in self.assignments)
        ):
            raise RandomExternalMaterializationError("sham assignments are not canonical")
        if {item.case_id for item in self.assignments} != set(packet_by_case):
            raise RandomExternalMaterializationError("sham cases differ from packet origins")
        if {item.brain_packet_id for item in self.assignments} != {
            packet.packet_id for packet in self.packets
        } or {item.sham_packet_id for item in self.assignments} != {
            packet.packet_id for packet in self.packets
        }:
            raise RandomExternalMaterializationError(
                "brain and sham mappings must each use every packet exactly once"
            )
        packet_by_id = {packet.packet_id: packet for packet in self.packets}
        for item in self.assignments:
            brain = packet_by_id[item.brain_packet_id]
            sham = packet_by_id[item.sham_packet_id]
            if brain.origin_case_id != item.case_id or not _matching_compatible(brain, sham):
                raise RandomExternalMaterializationError(
                    "sham assignment violates source-disjoint derangement"
                )
        expected_assignments = _domain_digest(
            _ASSIGNMENT_DOMAIN,
            [item.commitment_record() for item in self.assignments],
        )
        if self.assignment_digest != expected_assignments:
            raise RandomExternalMaterializationError("sham-assignment digest is stale")
        expected_sham_precommitment = _domain_digest(
            _MATCHING_DOMAIN,
            self._matching_record(),
        )
        if self.sham_matching_precommitment_digest != expected_sham_precommitment:
            raise RandomExternalMaterializationError("sham-matching precommitment is stale")
        if type(self.cases) is not tuple or len(self.cases) != len(self.descriptors):
            raise RandomExternalMaterializationError("plan case universe is incomplete")
        if any(type(item) is not RandomExternalPilotCaseV4 for item in self.cases):
            raise RandomExternalMaterializationError("plan cases must be exact v4 records")
        for item in self.cases:
            item.__post_init__()
        expected_cases = _build_plan_cases(
            self.retrieval_preparation,
            self.descriptors,
            self.assignments,
        )
        if self.cases != expected_cases:
            raise RandomExternalMaterializationError(
                "plan cases differ from retrieval and sham evidence"
            )

    def _payload(self) -> dict[str, object]:
        return {
            "abstained_count": self.abstained_count,
            "automatic_retrieval_itt": True,
            "case_descriptor_universe_digest": self.descriptor_universe_digest,
            "case_universe_digest": _plain_digest(
                [item.commitment_record() for item in self.cases]
            ),
            "confirmatory_claim_authorized": False,
            "packet_universe_digest": self.packet_universe_digest,
            "retrieval_preparation_digest": self.retrieval_preparation.content_digest,
            "retrieval_protocol_digest": self.retrieval_protocol_digest,
            "schema": RANDOM_EXTERNAL_SHAM_MATERIALIZATION_SCHEMA_V4,
            "selected_count": self.selected_count,
            "served_model_identity_verified": False,
            "sham_matching_precommitment_digest": (self.sham_matching_precommitment_digest),
            "tokenizer_attestation_digest": self.tokenizer_attestation_digest,
        }

    def validate_current(self) -> None:
        if type(self) is not RandomExternalShamMaterializationV4:
            raise RandomExternalMaterializationError(
                "sham-materialization subclasses are forbidden"
            )
        self._validate_fields()
        expected = _domain_digest(_MATERIALIZATION_DOMAIN, self._payload())
        if not hmac.compare_digest(self.content_digest, expected):
            raise RandomExternalMaterializationError("sham-materialization digest is stale")

    def public_commitment(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"materialization_digest": self.content_digest}


def _build_plan_cases(
    preparation: DomainFreeRetrievalPreparationV4,
    descriptors: tuple[RandomExternalCaseDescriptorV4, ...],
    assignments: tuple[RandomExternalShamAssignmentV4, ...],
) -> tuple[RandomExternalPilotCaseV4, ...]:
    descriptor_by_case = {item.case_id: item for item in descriptors}
    retrieval_case_by_id = {item.case_id: item for item in preparation.cases}
    assignment_by_case = {item.case_id: item for item in assignments}
    result: list[RandomExternalPilotCaseV4] = []
    for receipt in preparation.receipts:
        descriptor = descriptor_by_case[receipt.case_id]
        retrieval_case = retrieval_case_by_id[receipt.case_id]
        if receipt.disposition is DomainFreeRetrievalDispositionV4.SELECTED:
            assignment = assignment_by_case[receipt.case_id]
            disposition = AutomaticRetrievalDispositionV4.SELECTED
            brain_packet_id = assignment.brain_packet_id
            sham_packet_id = assignment.sham_packet_id
            abstention_digest = None
        else:
            disposition = AutomaticRetrievalDispositionV4.ABSTAINED
            brain_packet_id = None
            sham_packet_id = None
            abstention_digest = _abstention_reason_digest(receipt)
        result.append(
            RandomExternalPilotCaseV4(
                case_id=receipt.case_id,
                source=descriptor.source,
                domain=descriptor.domain,
                difficulty=descriptor.difficulty,
                independence_cluster_id=descriptor.independence_cluster_id,
                problem_text=retrieval_case.problem_text,
                scorer_spec_digest=descriptor.scorer_spec_digest,
                retrieval_disposition=disposition,
                retrieval_receipt_digest=receipt.content_digest,
                brain_packet_id=brain_packet_id,
                sham_packet_id=sham_packet_id,
                retrieval_abstention_reason_digest=abstention_digest,
            )
        )
    return tuple(result)


def build_random_external_sham_materialization_v4(
    preparation: DomainFreeRetrievalPreparationV4,
    descriptors: tuple[RandomExternalCaseDescriptorV4, ...] | list[RandomExternalCaseDescriptorV4],
    *,
    tokenizer_suite: VerifiedTokenizerSuiteV3,
    packet_padding_policy: PacketPaddingPolicyV4,
    matching_seed: bytes,
) -> RandomExternalShamMaterializationV4:
    """Freeze packet and sham commitments from one exact retrieval preparation."""

    if type(preparation) is not DomainFreeRetrievalPreparationV4:
        raise RandomExternalMaterializationError(
            "v4 materialization requires exact domain-free retrieval preparation"
        )
    preparation.validate_current()
    normalized_descriptors = _normalized_descriptors(descriptors)
    if len(preparation.cases) != RANDOM_EXTERNAL_PILOT_CASE_COUNT_V4:
        raise RandomExternalMaterializationError("v4 materialization requires 24 retrieval cases")
    if tuple(item.case_id for item in normalized_descriptors) != tuple(
        item.case_id for item in preparation.cases
    ) or any(
        descriptor.source is not retrieval_case.source
        for descriptor, retrieval_case in zip(
            normalized_descriptors,
            preparation.cases,
            strict=True,
        )
    ):
        raise RandomExternalMaterializationError(
            "case descriptors differ from the retrieval case universe"
        )
    if len(preparation.drafts) == 1:
        raise RandomExternalShamInfeasible(
            "one selected packet cannot form a source-disjoint sham derangement"
        )
    if type(packet_padding_policy) is not PacketPaddingPolicyV4:
        raise RandomExternalMaterializationError(
            "v4 materialization requires an exact packet-padding policy"
        )
    packet_padding_policy.__post_init__()
    if packet_padding_policy.base_format_template_digest != BRAIN_PACKET_FORMAT_SHA256_V3:
        raise RandomExternalMaterializationError(
            "packet-padding policy is not bound to the frozen retrieval renderer"
        )
    seed = _secret(matching_seed)
    padding_inputs = tuple(
        PacketPaddingInputV4(
            case_id=draft.case_id,
            draft_digest=draft.content_digest,
            input_text=draft.render_fragment,
            input_format_template_digest=draft.format_template_digest,
        )
        for draft in preparation.drafts
    )
    tokenizer_attestation = materialize_random_external_tokenizer_attestation_v4(
        padding_inputs,
        suite=tokenizer_suite,
        policy=packet_padding_policy,
    )
    retrieval_receipt_by_case = {item.case_id: item for item in preparation.receipts}
    card_by_id = {card.record_id: card for card in preparation.corpus.cards}
    packets: list[FrozenPacket] = []
    for padding_receipt in tokenizer_attestation.receipts:
        retrieval_receipt = retrieval_receipt_by_case[padding_receipt.case_id]
        selected_card = card_by_id[retrieval_receipt.selected_record_ids[0]]
        draft = next(item for item in preparation.drafts if item.case_id == padding_receipt.case_id)
        packets.append(
            FrozenPacket(
                packet_id=_packet_id(
                    case_id=draft.case_id,
                    draft_digest=draft.content_digest,
                    padding_receipt_digest=padding_receipt.content_digest,
                    tokenizer_attestation_digest=tokenizer_attestation.content_digest,
                ),
                origin_case_id=draft.case_id,
                text=padding_receipt.final_text,
                snapshot_manifest_digest=preparation.corpus_digest,
                retrieval_policy_digest=preparation.policy_digest,
                query_digest=draft.query_digest,
                format_template_digest=(packet_padding_policy.output_format_template_digest),
                section_shape_digest=draft.section_shape_digest,
                age_status_vector_digest=draft.age_status_vector_digest,
                source_digests=draft.source_digests,
                relevance_keys=(selected_card.family,),
                tokenizer_receipts=padding_receipt.measurement.tokenizer_receipts,
            )
        )
    packet_tuple = tuple(sorted(packets, key=lambda packet: packet.packet_id))
    packet_digest = _plain_digest([packet.commitment_record() for packet in packet_tuple])
    descriptor_digest = _domain_digest(
        _DESCRIPTOR_DOMAIN,
        [item.commitment_record() for item in normalized_descriptors],
    )
    design_digest = _domain_digest(
        _MATCHING_DOMAIN + "/design",
        {
            "case_descriptor_universe_digest": descriptor_digest,
            "packet_universe_digest": packet_digest,
            "retrieval_preparation_digest": preparation.content_digest,
            "tokenizer_attestation_digest": tokenizer_attestation.content_digest,
        },
    )
    assignments = _build_matching(
        packet_tuple,
        seed=seed,
        design_digest=design_digest,
    )
    assignment_digest = _domain_digest(
        _ASSIGNMENT_DOMAIN,
        [item.commitment_record() for item in assignments],
    )
    seed_commitment = _seed_commitment(seed, design_digest)
    matching_record = {
        "assignment_digest": assignment_digest,
        "bijective_derangement_required": True,
        "matching_seed_commitment": seed_commitment,
        "packet_universe_digest": packet_digest,
        "schema": RANDOM_EXTERNAL_SHAM_MATCHING_SCHEMA_V4,
        "selected_count": len(packet_tuple),
        "singleton_selected_infeasible": True,
        "source_disjoint_required": True,
        "zero_selected_valid": True,
    }
    cases = _build_plan_cases(preparation, normalized_descriptors, assignments)
    return RandomExternalShamMaterializationV4(
        retrieval_preparation=preparation,
        descriptors=normalized_descriptors,
        tokenizer_attestation=tokenizer_attestation,
        packets=packet_tuple,
        cases=cases,
        assignments=assignments,
        descriptor_universe_digest=descriptor_digest,
        packet_universe_digest=packet_digest,
        assignment_digest=assignment_digest,
        matching_seed_commitment=seed_commitment,
        sham_matching_precommitment_digest=_domain_digest(
            _MATCHING_DOMAIN,
            matching_record,
        ),
    )


@dataclass(frozen=True, slots=True)
class RandomExternalMaterializationReplayV4:
    materialization: RandomExternalShamMaterializationV4 = field(repr=False)
    content_digest: str = field(init=False)
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self) is not RandomExternalMaterializationReplayV4:
            raise RandomExternalMaterializationError(
                "cold-replay capability subclasses are forbidden"
            )
        if self._seal is not _REPLAY_SEAL:
            raise RandomExternalMaterializationError(
                "cold-replay capability must be minted by raw-input replay"
            )
        if type(self.materialization) is not RandomExternalShamMaterializationV4:
            raise RandomExternalMaterializationError(
                "cold-replay capability requires exact materialization"
            )
        self.materialization.validate_current()
        object.__setattr__(
            self,
            "content_digest",
            _domain_digest(_REPLAY_DOMAIN, self._payload()),
        )

    def _payload(self) -> dict[str, object]:
        return {
            "case_manifest_digest": (
                self.materialization.retrieval_preparation.case_manifest_digest
            ),
            "materialization_digest": self.materialization.content_digest,
            "retrieval_preparation_digest": (
                self.materialization.retrieval_preparation.content_digest
            ),
            "schema": RANDOM_EXTERNAL_MATERIALIZATION_REPLAY_SCHEMA_V4,
        }

    def validate_current(self) -> None:
        if self._seal is not _REPLAY_SEAL:
            raise RandomExternalMaterializationError("cold-replay capability seal is invalid")
        self.materialization.validate_current()
        expected = _domain_digest(_REPLAY_DOMAIN, self._payload())
        if not hmac.compare_digest(self.content_digest, expected):
            raise RandomExternalMaterializationError("cold-replay capability digest is stale")

    def public_commitment(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"cold_replay_capability_digest": self.content_digest}


def cold_replay_random_external_sham_materialization_v4(
    case_manifest_bytes: bytes,
    *,
    expected_case_manifest_digest: str,
    retrieval_execution_manifest: DomainFreeRetrievalExecutionManifestV4,
    expected_retrieval_execution_manifest_digest: str,
    corpus: BrainCorpusManifestV3,
    expected_corpus_digest: str,
    retrieval_policy: DomainFreeRetrievalPolicyV4,
    expected_retrieval_policy_digest: str,
    expected_retrieval_preparation_digest: str,
    descriptors: tuple[RandomExternalCaseDescriptorV4, ...] | list[RandomExternalCaseDescriptorV4],
    tokenizer_suite: VerifiedTokenizerSuiteV3,
    packet_padding_policy: PacketPaddingPolicyV4,
    matching_seed: bytes,
    expected_materialization_digest: str,
) -> RandomExternalMaterializationReplayV4:
    """Cold-rebuild retrieval, tokenizer receipts, packets, and sham matching."""

    preparation = cold_replay_domain_free_retrieval_v4(
        case_manifest_bytes,
        expected_case_manifest_digest=expected_case_manifest_digest,
        execution_manifest=retrieval_execution_manifest,
        expected_execution_manifest_digest=(expected_retrieval_execution_manifest_digest),
        corpus=corpus,
        expected_corpus_digest=expected_corpus_digest,
        policy=retrieval_policy,
        expected_policy_digest=expected_retrieval_policy_digest,
        expected_preparation_digest=expected_retrieval_preparation_digest,
    )
    rebuilt = build_random_external_sham_materialization_v4(
        preparation,
        descriptors,
        tokenizer_suite=tokenizer_suite,
        packet_padding_policy=packet_padding_policy,
        matching_seed=matching_seed,
    )
    expected = _digest(
        expected_materialization_digest,
        "expected v4 sham materialization",
    )
    if not hmac.compare_digest(rebuilt.content_digest, expected):
        raise RandomExternalMaterializationError(
            "cold replay differs from the expected v4 sham materialization"
        )
    return RandomExternalMaterializationReplayV4(
        materialization=rebuilt,
        _seal=_REPLAY_SEAL,
    )


@dataclass(frozen=True, slots=True)
class RandomExternalPlanBindingV4:
    replay: RandomExternalMaterializationReplayV4 = field(repr=False)
    plan: RandomExternalPilotPlanV4 = field(repr=False)
    content_digest: str = field(init=False)
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self) is not RandomExternalPlanBindingV4:
            raise RandomExternalMaterializationError("plan-binding subclasses are forbidden")
        self._validate_fields()
        object.__setattr__(
            self,
            "content_digest",
            _domain_digest(_PLAN_BINDING_DOMAIN, self._payload()),
        )

    def _validate_fields(self) -> None:
        if self._seal is not _PLAN_BINDING_SEAL:
            raise RandomExternalMaterializationError("plan binding must come from the binder")
        if type(self.replay) is not RandomExternalMaterializationReplayV4:
            raise RandomExternalMaterializationError("plan binding requires cold-replay capability")
        self.replay.validate_current()
        if type(self.plan) is not RandomExternalPilotPlanV4:
            raise RandomExternalMaterializationError("plan binding requires exact v4 plan")
        self.plan.__post_init__()
        materialization = self.replay.materialization
        if (
            self.plan.cases != materialization.cases
            or self.plan.packets != materialization.packets
            or self.plan.packet_universe_digest != materialization.packet_universe_digest
            or self.plan.config.retrieval_protocol_digest
            != materialization.retrieval_protocol_digest
            or self.plan.config.sham_matching_precommitment_digest
            != materialization.sham_matching_precommitment_digest
            or self.plan.config.tokenizer_attestation_digest
            != materialization.tokenizer_attestation_digest
        ):
            raise RandomExternalMaterializationError(
                "plan differs from cold-replayed retrieval, sham, or tokenizer evidence"
            )
        if (
            self.plan.config.engineering_proxy_only is not True
            or self.plan.config.served_model_identity_verified is not False
            or self.plan.config.confirmatory_claim_authorized is not False
        ):
            raise RandomExternalMaterializationError("v4 plan overclaims materialization authority")

    def _payload(self) -> dict[str, object]:
        materialization = self.replay.materialization
        return {
            "cold_replay_capability_digest": self.replay.content_digest,
            "confirmatory_claim_authorized": False,
            "packet_universe_digest": materialization.packet_universe_digest,
            "plan_digest": self.plan.digest,
            "schema": RANDOM_EXTERNAL_PLAN_MATERIALIZATION_BINDING_SCHEMA_V4,
            "served_model_identity_verified": False,
            "sham_matching_precommitment_digest": (
                materialization.sham_matching_precommitment_digest
            ),
            "tokenizer_attestation_digest": materialization.tokenizer_attestation_digest,
        }

    def validate_current(self) -> None:
        if type(self) is not RandomExternalPlanBindingV4:
            raise RandomExternalMaterializationError("plan-binding subclasses are forbidden")
        if self._seal is not _PLAN_BINDING_SEAL:
            raise RandomExternalMaterializationError("plan-binding seal is invalid")
        self._validate_fields()
        expected = _domain_digest(_PLAN_BINDING_DOMAIN, self._payload())
        if not hmac.compare_digest(self.content_digest, expected):
            raise RandomExternalMaterializationError("plan-binding digest is stale")

    def public_commitment(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"plan_materialization_binding_digest": self.content_digest}


def bind_random_external_materialization_to_plan_v4(
    replay: RandomExternalMaterializationReplayV4,
    plan: RandomExternalPilotPlanV4,
) -> RandomExternalPlanBindingV4:
    """Bind a plan only to a sealed raw-input replay capability."""

    return RandomExternalPlanBindingV4(
        replay=replay,
        plan=plan,
        _seal=_PLAN_BINDING_SEAL,
    )


__all__ = [
    "RANDOM_EXTERNAL_MATERIALIZATION_REPLAY_SCHEMA_V4",
    "RANDOM_EXTERNAL_PLAN_MATERIALIZATION_BINDING_SCHEMA_V4",
    "RANDOM_EXTERNAL_SHAM_MATCHING_SCHEMA_V4",
    "RANDOM_EXTERNAL_SHAM_MATERIALIZATION_SCHEMA_V4",
    "RandomExternalCaseDescriptorV4",
    "RandomExternalMaterializationError",
    "RandomExternalMaterializationReplayV4",
    "RandomExternalPlanBindingV4",
    "RandomExternalShamAssignmentV4",
    "RandomExternalShamInfeasible",
    "RandomExternalShamMaterializationV4",
    "bind_random_external_materialization_to_plan_v4",
    "build_random_external_sham_materialization_v4",
    "cold_replay_random_external_sham_materialization_v4",
]
