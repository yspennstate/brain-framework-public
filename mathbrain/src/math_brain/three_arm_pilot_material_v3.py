"""Pure deterministic sham-packet matching for the sealed v3 pilot.

This module only assigns already-rendered :class:`FrozenPacket` records.  It neither
renders nor pads packet text, and it never invents tokenizer receipts.  A revealed seed
lets an independent verifier replay the exact canonical perfect-matching search.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .models import canonical_json
from .three_arm_evaluation import V3_PILOT_CASE_COUNT, FrozenPacket

_DIGEST_PREFIX = "sha256:"
_DIGEST_LENGTH = len(_DIGEST_PREFIX) + 64
_MAX_IDENTIFIER_BYTES = 128
_MAX_RELEVANCE_KEYS = 128
_RECORD_TYPE = "org.math-research-brain/sham-packet-matching-v3"
_HMAC_DOMAIN = b"math-brain/three-arm-v3/sham-packet-matching-v1\x00"


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


def _text(value: Any, label: str, *, maximum_bytes: int = _MAX_IDENTIFIER_BYTES) -> str:
    if type(value) is not str:
        raise ValueError(f"{label} must be a string")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} must be valid UTF-8") from error
    if not value.strip():
        raise ValueError(f"{label} must not be empty")
    if len(encoded) > maximum_bytes:
        raise ValueError(f"{label} exceeds its byte budget")
    return value


def _sorted_unique_texts(values: Any, label: str) -> tuple[str, ...]:
    if type(values) is not tuple or not 1 <= len(values) <= _MAX_RELEVANCE_KEYS:
        raise ValueError(f"{label} must be a nonempty bounded tuple")
    for value in values:
        _text(value, label, maximum_bytes=256)
    if tuple(sorted(set(values))) != values:
        raise ValueError(f"{label} must be unique and sorted")
    return values


def _seed(value: Any) -> bytes:
    if type(value) is not bytes or len(value) < 32:
        raise ValueError("matching seed must contain at least 256 bits")
    return value


def _rank(seed: bytes, domain: str, *parts: object) -> bytes:
    material = bytearray(_HMAC_DOMAIN)
    for item in (domain, *parts):
        encoded = str(item).encode("utf-8")
        material.extend(len(encoded).to_bytes(4, "big"))
        material.extend(encoded)
    return hmac.new(seed, bytes(material), hashlib.sha256).digest()


def _seed_commitment(seed: bytes, design_universe_digest: str) -> str:
    return _sha256_bytes(
        _HMAC_DOMAIN
        + b"seed-commitment\x00"
        + design_universe_digest.encode("ascii")
        + b"\x00"
        + seed
    )


@dataclass(frozen=True, slots=True)
class ShamTargetV3:
    """One case and the already-rendered packet relevant to that case."""

    case_id: str
    brain_packet_id: str
    required_relevance_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self) is not ShamTargetV3:
            raise ValueError("ShamTargetV3 subclasses are forbidden")
        _text(self.case_id, "target case id")
        _text(self.brain_packet_id, "target brain packet id")
        _sorted_unique_texts(self.required_relevance_keys, "target relevance keys")

    def commitment_record(self) -> dict[str, Any]:
        return {
            "brain_packet_id": self.brain_packet_id,
            "case_id": self.case_id,
            "required_relevance_keys": list(self.required_relevance_keys),
        }


@dataclass(frozen=True, slots=True)
class ShamPacketAssignmentV3:
    """One canonical brain-to-sham packet assignment."""

    case_id: str
    brain_packet_id: str
    sham_packet_id: str

    def __post_init__(self) -> None:
        if type(self) is not ShamPacketAssignmentV3:
            raise ValueError("ShamPacketAssignmentV3 subclasses are forbidden")
        _text(self.case_id, "assignment case id")
        _text(self.brain_packet_id, "assignment brain packet id")
        _text(self.sham_packet_id, "assignment sham packet id")
        if self.brain_packet_id == self.sham_packet_id:
            raise ValueError("brain and sham packet ids must differ")

    def commitment_record(self) -> dict[str, Any]:
        return {
            "brain_packet_id": self.brain_packet_id,
            "case_id": self.case_id,
            "sham_packet_id": self.sham_packet_id,
        }


def _normalized_targets(targets: Sequence[ShamTargetV3]) -> tuple[ShamTargetV3, ...]:
    if type(targets) not in {list, tuple}:
        raise ValueError("targets must be a bounded list or tuple")
    result = tuple(targets)
    if len(result) != V3_PILOT_CASE_COUNT:
        raise ValueError("v3 pilot sham matching requires exactly 24 targets")
    if any(type(target) is not ShamTargetV3 for target in result):
        raise ValueError("targets must be exact ShamTargetV3 records")
    for target in result:
        target.__post_init__()
    if len({target.case_id for target in result}) != len(result):
        raise ValueError("target case ids must be unique")
    if len({target.brain_packet_id for target in result}) != len(result):
        raise ValueError("target brain packet ids must be unique")
    return tuple(sorted(result, key=lambda target: target.case_id))


def _normalized_packets(packets: Sequence[FrozenPacket]) -> tuple[FrozenPacket, ...]:
    if type(packets) not in {list, tuple}:
        raise ValueError("packets must be a bounded list or tuple")
    result = tuple(packets)
    if len(result) != V3_PILOT_CASE_COUNT:
        raise ValueError("v3 pilot sham matching requires exactly 24 packets")
    if any(type(packet) is not FrozenPacket for packet in result):
        raise ValueError("packets must be exact FrozenPacket records")
    for packet in result:
        packet.__post_init__()
    if len({packet.packet_id for packet in result}) != len(result):
        raise ValueError("packet ids must be unique")
    if len({packet.text_digest for packet in result}) != len(result):
        raise ValueError("packet text digests must be unique")
    return tuple(sorted(result, key=lambda packet: packet.packet_id))


def _validate_design(
    targets: Sequence[ShamTargetV3], packets: Sequence[FrozenPacket]
) -> tuple[tuple[ShamTargetV3, ...], tuple[FrozenPacket, ...]]:
    normalized_targets = _normalized_targets(targets)
    normalized_packets = _normalized_packets(packets)
    packet_by_id = {packet.packet_id: packet for packet in normalized_packets}
    if {target.brain_packet_id for target in normalized_targets} != set(packet_by_id):
        raise ValueError("target brain mapping must use every packet exactly once")
    if len({packet.snapshot_manifest_digest for packet in normalized_packets}) != 1:
        raise ValueError("all packets must share one frozen snapshot")
    if len({packet.retrieval_policy_digest for packet in normalized_packets}) != 1:
        raise ValueError("all packets must share one retrieval policy")
    for target in normalized_targets:
        brain = packet_by_id[target.brain_packet_id]
        if brain.origin_case_id != target.case_id:
            raise ValueError("brain packet must originate from its target case")
        if not set(target.required_relevance_keys).issubset(brain.relevance_keys):
            raise ValueError("brain packet omits required relevance keys")
    return normalized_targets, normalized_packets


def _compatible(target: ShamTargetV3, brain: FrozenPacket, candidate: FrozenPacket) -> bool:
    """Mirror ``three_arm_evaluation._validate_packet_bindings`` exactly."""

    return (
        candidate.packet_id != brain.packet_id
        and candidate.origin_case_id != target.case_id
        and not set(target.required_relevance_keys).intersection(candidate.relevance_keys)
        and not set(brain.source_digests).intersection(candidate.source_digests)
        and brain.text_digest != candidate.text_digest
        and brain.matching_key == candidate.matching_key
    )


def _validate_assignments(
    targets: tuple[ShamTargetV3, ...],
    packets: tuple[FrozenPacket, ...],
    assignments: Any,
) -> tuple[ShamPacketAssignmentV3, ...]:
    if type(assignments) is not tuple or len(assignments) != V3_PILOT_CASE_COUNT:
        raise ValueError("assignments must be an exact 24-record tuple")
    if any(type(item) is not ShamPacketAssignmentV3 for item in assignments):
        raise ValueError("assignments must be exact ShamPacketAssignmentV3 records")
    for assignment in assignments:
        assignment.__post_init__()
    canonical = tuple(sorted(assignments, key=lambda item: item.case_id))
    if assignments != canonical:
        raise ValueError("assignments must be in canonical case-id order")
    target_by_case = {target.case_id: target for target in targets}
    packet_by_id = {packet.packet_id: packet for packet in packets}
    if {item.case_id for item in assignments} != set(target_by_case):
        raise ValueError("assignment and target case universes differ")
    if len({item.case_id for item in assignments}) != len(assignments):
        raise ValueError("assignment case ids must be unique")
    if {item.brain_packet_id for item in assignments} != set(packet_by_id):
        raise ValueError("assignment brain mapping must use every packet exactly once")
    if {item.sham_packet_id for item in assignments} != set(packet_by_id):
        raise ValueError("assignment sham mapping must use every packet exactly once")
    if len({item.sham_packet_id for item in assignments}) != len(assignments):
        raise ValueError("sham packet assignment must be bijective")
    for assignment in assignments:
        target = target_by_case[assignment.case_id]
        if assignment.brain_packet_id != target.brain_packet_id:
            raise ValueError("assignment changes the target brain packet")
        brain = packet_by_id[assignment.brain_packet_id]
        sham = packet_by_id[assignment.sham_packet_id]
        if not _compatible(target, brain, sham):
            raise ValueError("assignment contains an incompatible sham packet")
    return assignments


@dataclass(frozen=True, slots=True)
class ShamPacketMatchingV3:
    """Sealed 24-packet perfect matching with replayable commitments."""

    targets: tuple[ShamTargetV3, ...] = field(repr=False)
    packets: tuple[FrozenPacket, ...] = field(repr=False)
    assignments: tuple[ShamPacketAssignmentV3, ...]
    target_universe_digest: str
    packet_universe_digest: str
    matching_seed_commitment: str
    assignment_digest: str

    def __post_init__(self) -> None:
        if type(self) is not ShamPacketMatchingV3:
            raise ValueError("ShamPacketMatchingV3 subclasses are forbidden")
        targets, packets = _validate_design(self.targets, self.packets)
        if targets != self.targets or packets != self.packets:
            raise ValueError("matching universes must be in canonical order")
        assignments = _validate_assignments(targets, packets, self.assignments)
        for label, value in (
            ("target universe digest", self.target_universe_digest),
            ("packet universe digest", self.packet_universe_digest),
            ("matching seed commitment", self.matching_seed_commitment),
            ("assignment digest", self.assignment_digest),
        ):
            _digest(value, label)
        expected_target_digest = _digest_json([target.commitment_record() for target in targets])
        expected_packet_digest = _digest_json([packet.commitment_record() for packet in packets])
        expected_assignment_digest = _digest_json(
            [assignment.commitment_record() for assignment in assignments]
        )
        if self.target_universe_digest != expected_target_digest:
            raise ValueError("target universe differs from its commitment")
        if self.packet_universe_digest != expected_packet_digest:
            raise ValueError("packet universe differs from its commitment")
        if self.assignment_digest != expected_assignment_digest:
            raise ValueError("assignment differs from its commitment")

    def commitment_record(self) -> dict[str, Any]:
        return {
            "assignment_digest": self.assignment_digest,
            "matching_seed_commitment": self.matching_seed_commitment,
            "packet_count": len(self.packets),
            "packet_universe_digest": self.packet_universe_digest,
            "record_type": _RECORD_TYPE,
            "target_count": len(self.targets),
            "target_universe_digest": self.target_universe_digest,
        }

    @property
    def digest(self) -> str:
        return _digest_json(self.commitment_record())

    def public_commitment(self) -> dict[str, Any]:
        return {**self.commitment_record(), "matching_digest": self.digest}


def build_sham_packet_matching_v3(
    targets: Sequence[ShamTargetV3],
    packets: Sequence[FrozenPacket],
    *,
    matching_seed: bytes,
) -> ShamPacketMatchingV3:
    """Find the deterministic perfect matching over the exact 24-packet universe."""

    seed = _seed(matching_seed)
    normalized_targets, normalized_packets = _validate_design(targets, packets)
    target_digest = _digest_json([target.commitment_record() for target in normalized_targets])
    packet_digest = _digest_json([packet.commitment_record() for packet in normalized_packets])
    design_digest = _digest_json(
        {
            "packet_universe_digest": packet_digest,
            "target_universe_digest": target_digest,
        }
    )
    packet_by_id = {packet.packet_id: packet for packet in normalized_packets}
    target_by_case = {target.case_id: target for target in normalized_targets}

    compatible_packet_ids: dict[str, tuple[str, ...]] = {}
    for target in normalized_targets:
        brain = packet_by_id[target.brain_packet_id]
        compatible_packet_ids[target.case_id] = tuple(
            packet.packet_id for packet in normalized_packets if _compatible(target, brain, packet)
        )
        if not compatible_packet_ids[target.case_id]:
            raise ValueError(f"no compatible sham packet for target {target.case_id}")

    ordered_targets = sorted(
        normalized_targets,
        key=lambda target: (
            _rank(seed, "target-order", design_digest, target.case_id, target.brain_packet_id),
            target.case_id,
        ),
    )
    ordered_candidates = {
        target.case_id: tuple(
            sorted(
                compatible_packet_ids[target.case_id],
                key=lambda packet_id: (
                    _rank(
                        seed,
                        "candidate-order",
                        design_digest,
                        target.case_id,
                        target.brain_packet_id,
                        packet_id,
                    ),
                    packet_id,
                ),
            )
        )
        for target in normalized_targets
    }

    # The augmenting-path search is polynomial and bounded by the sealed 24-by-24 graph.
    # It therefore finds a perfect matching when one exists without factorial backtracking.
    packet_owner: dict[str, str] = {}

    def augment(case_id: str, visited_packet_ids: set[str]) -> bool:
        for packet_id in ordered_candidates[case_id]:
            if packet_id in visited_packet_ids:
                continue
            visited_packet_ids.add(packet_id)
            owner = packet_owner.get(packet_id)
            if owner is None or augment(owner, visited_packet_ids):
                packet_owner[packet_id] = case_id
                return True
        return False

    for target in ordered_targets:
        if not augment(target.case_id, set()):
            raise ValueError("no perfect sham-packet matching exists")

    sham_by_case = {case_id: packet_id for packet_id, case_id in packet_owner.items()}
    if set(sham_by_case) != set(target_by_case) or set(packet_owner) != set(packet_by_id):
        raise ValueError("matching search did not consume the exact packet universe")
    assignments = tuple(
        ShamPacketAssignmentV3(
            case_id=target.case_id,
            brain_packet_id=target.brain_packet_id,
            sham_packet_id=sham_by_case[target.case_id],
        )
        for target in normalized_targets
    )
    assignment_digest = _digest_json([assignment.commitment_record() for assignment in assignments])
    return ShamPacketMatchingV3(
        targets=normalized_targets,
        packets=normalized_packets,
        assignments=assignments,
        target_universe_digest=target_digest,
        packet_universe_digest=packet_digest,
        matching_seed_commitment=_seed_commitment(seed, design_digest),
        assignment_digest=assignment_digest,
    )


def verify_sham_packet_matching_v3(
    matching: ShamPacketMatchingV3,
    *,
    matching_seed: bytes,
    expected_matching_digest: str,
) -> None:
    """Replay the seed-bound search against an externally expected commitment."""

    if type(matching) is not ShamPacketMatchingV3:
        raise ValueError("matching must be an exact ShamPacketMatchingV3 record")
    matching.__post_init__()
    _digest(expected_matching_digest, "externally expected matching digest")
    if matching.digest != expected_matching_digest:
        raise ValueError("matching differs from the externally expected digest")
    rebuilt = build_sham_packet_matching_v3(
        matching.targets,
        matching.packets,
        matching_seed=matching_seed,
    )
    if rebuilt != matching:
        raise ValueError("revealed seed does not reconstruct the exact sham matching")


__all__ = [
    "ShamPacketAssignmentV3",
    "ShamPacketMatchingV3",
    "ShamTargetV3",
    "build_sham_packet_matching_v3",
    "verify_sham_packet_matching_v3",
]
