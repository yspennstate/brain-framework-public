"""Outcome-free, commitment-bound dispatch for the successor paired study."""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .models import canonical_json

SUCCESSOR_PAIR_DISPATCH_SCHEMA_V2 = "org.math-research-brain/successor-pair-dispatch"
SUCCESSOR_PAIR_DISPATCH_SCHEMA_VERSION_V2 = 1
SUCCESSOR_PAIR_COUNT_V2 = 20
SUCCESSOR_CONDITION_SLOT_COUNT_V2 = 40

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_DOMAIN = b"org.math-research-brain/successor-pair-dispatch/v2\0"


class SuccessorConditionV2(StrEnum):
    BRAIN_ON = "brain_on"
    BRAIN_OFF = "brain_off"


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _digest_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _validate_digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical SHA-256 digest")
    return value


def _validate_pair_id(value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError("pair id must be a nonempty plain string")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError("pair id must be valid UTF-8") from exc
    if size > 128 or any(ord(character) < 33 for character in value):
        raise ValueError("pair id must be a compact visible identifier")
    return value


def _validate_seed(order_seed: object) -> bytes:
    if type(order_seed) is not bytes or not 32 <= len(order_seed) <= 64:
        raise ValueError("order seed must be exact bytes between 32 and 64 bytes")
    return order_seed


def _seed_commitment(order_seed: bytes) -> str:
    return _digest_bytes(_DOMAIN + b"order-seed\0" + order_seed)


def _rank(order_seed: bytes, purpose: bytes, pair_id: str) -> bytes:
    return hmac.new(
        order_seed,
        _DOMAIN + purpose + b"\0" + pair_id.encode("utf-8"),
        hashlib.sha256,
    ).digest()


@dataclass(frozen=True, slots=True)
class SuccessorPairDispatchV2:
    pair_id: str
    dispatch_index: int
    condition_order: tuple[SuccessorConditionV2, SuccessorConditionV2]
    matched_batch_digest: str

    def __post_init__(self) -> None:
        self.validate_current()

    def validate_current(self) -> None:
        """Recheck a frozen record in case low-level mutation bypassed construction."""

        _validate_pair_id(self.pair_id)
        if type(self.dispatch_index) is not int or not 0 <= self.dispatch_index < (
            SUCCESSOR_PAIR_COUNT_V2
        ):
            raise ValueError("dispatch index is outside the fixed pair population")
        if (
            type(self.condition_order) is not tuple
            or len(self.condition_order) != 2
            or not all(type(item) is SuccessorConditionV2 for item in self.condition_order)
            or set(self.condition_order) != set(SuccessorConditionV2)
        ):
            raise ValueError("condition order must contain brain_on and brain_off exactly once")
        _validate_digest(self.matched_batch_digest, "matched batch digest")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return {
            "condition_order": [condition.value for condition in self.condition_order],
            "dispatch_index": self.dispatch_index,
            "matched_batch_digest": self.matched_batch_digest,
            "pair_id": self.pair_id,
        }

    @classmethod
    def from_dict(cls, raw: object) -> SuccessorPairDispatchV2:
        if type(raw) is not dict:
            raise ValueError("pair dispatch must be an exact object")
        expected = {
            "condition_order",
            "dispatch_index",
            "matched_batch_digest",
            "pair_id",
        }
        if set(raw) != expected or not all(type(key) is str for key in raw):
            raise ValueError("pair dispatch has invalid fields")
        condition_order = raw["condition_order"]
        if type(condition_order) is not list or len(condition_order) != 2:
            raise ValueError("pair dispatch condition order must be a two-item array")
        try:
            parsed_order = tuple(SuccessorConditionV2(item) for item in condition_order)
        except (TypeError, ValueError) as exc:
            raise ValueError("pair dispatch contains an unknown condition") from exc
        return cls(
            pair_id=raw["pair_id"],
            dispatch_index=raw["dispatch_index"],
            condition_order=parsed_order,
            matched_batch_digest=raw["matched_batch_digest"],
        )


def _dispatch_payload_digest(dispatches: tuple[SuccessorPairDispatchV2, ...]) -> str:
    return _digest([dispatch.to_dict() for dispatch in dispatches])


@dataclass(frozen=True, slots=True)
class SuccessorPairDispatchPlanV2:
    order_seed_commitment: str
    pair_dispatch_payload_digest: str
    dispatches: tuple[SuccessorPairDispatchV2, ...]

    def __post_init__(self) -> None:
        self.validate_current()

    def validate_current(self) -> None:
        """Revalidate nested records, balance, and the complete payload binding."""

        _validate_digest(self.order_seed_commitment, "order-seed commitment")
        _validate_digest(self.pair_dispatch_payload_digest, "pair-dispatch payload digest")
        if (
            type(self.dispatches) is not tuple
            or len(self.dispatches) != SUCCESSOR_PAIR_COUNT_V2
            or not all(type(item) is SuccessorPairDispatchV2 for item in self.dispatches)
        ):
            raise ValueError("pair dispatch plan requires exactly twenty exact records")
        for dispatch in self.dispatches:
            dispatch.validate_current()
        if tuple(item.dispatch_index for item in self.dispatches) != tuple(
            range(SUCCESSOR_PAIR_COUNT_V2)
        ):
            raise ValueError("pair dispatch indices must be canonical and complete")
        if len({item.pair_id for item in self.dispatches}) != SUCCESSOR_PAIR_COUNT_V2:
            raise ValueError("pair dispatch plan must contain twenty unique pair ids")
        brain_on_first = sum(
            item.condition_order[0] is SuccessorConditionV2.BRAIN_ON
            for item in self.dispatches
        )
        if brain_on_first != SUCCESSOR_PAIR_COUNT_V2 // 2:
            raise ValueError("pair dispatch plan must have exact ten/ten condition balance")
        expected_digest = _dispatch_payload_digest(self.dispatches)
        if not hmac.compare_digest(expected_digest, self.pair_dispatch_payload_digest):
            raise ValueError("pair dispatch payload digest does not bind its records")

    def verify_order_seed(self, order_seed: bytes) -> None:
        self.validate_current()
        seed = _validate_seed(order_seed)
        if not hmac.compare_digest(_seed_commitment(seed), self.order_seed_commitment):
            raise ValueError("order seed does not match its commitment")
        pair_batches = {
            dispatch.pair_id: dispatch.matched_batch_digest for dispatch in self.dispatches
        }
        expected = _dispatches_for_seed(pair_batches, seed)
        if expected != self.dispatches:
            raise ValueError("order seed does not generate the committed dispatch payload")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return {
            "dispatches": [dispatch.to_dict() for dispatch in self.dispatches],
            "order_seed_commitment": self.order_seed_commitment,
            "pair_dispatch_payload_digest": self.pair_dispatch_payload_digest,
            "schema": SUCCESSOR_PAIR_DISPATCH_SCHEMA_V2,
            "schema_version": SUCCESSOR_PAIR_DISPATCH_SCHEMA_VERSION_V2,
        }

    @classmethod
    def from_dict(cls, raw: object) -> SuccessorPairDispatchPlanV2:
        if type(raw) is not dict:
            raise ValueError("pair dispatch plan must be an exact object")
        expected = {
            "dispatches",
            "order_seed_commitment",
            "pair_dispatch_payload_digest",
            "schema",
            "schema_version",
        }
        if set(raw) != expected or not all(type(key) is str for key in raw):
            raise ValueError("pair dispatch plan has invalid fields")
        if (
            type(raw["schema"]) is not str
            or raw["schema"] != SUCCESSOR_PAIR_DISPATCH_SCHEMA_V2
            or type(raw["schema_version"]) is not int
            or raw["schema_version"] != SUCCESSOR_PAIR_DISPATCH_SCHEMA_VERSION_V2
        ):
            raise ValueError("pair dispatch plan has an unsupported schema")
        dispatches = raw["dispatches"]
        if type(dispatches) is not list:
            raise ValueError("pair dispatch records must be an array")
        return cls(
            order_seed_commitment=raw["order_seed_commitment"],
            pair_dispatch_payload_digest=raw["pair_dispatch_payload_digest"],
            dispatches=tuple(SuccessorPairDispatchV2.from_dict(item) for item in dispatches),
        )


@dataclass(frozen=True, slots=True)
class SuccessorConditionSlotV2:
    ordinal: int
    pair_id: str
    dispatch_index: int
    condition: SuccessorConditionV2
    matched_batch_digest: str

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or not 0 <= self.ordinal < (
            SUCCESSOR_CONDITION_SLOT_COUNT_V2
        ):
            raise ValueError("condition-slot ordinal is outside the fixed population")
        _validate_pair_id(self.pair_id)
        if type(self.dispatch_index) is not int or self.dispatch_index != self.ordinal // 2:
            raise ValueError("condition slot must remain adjacent within its dispatch pair")
        if type(self.condition) is not SuccessorConditionV2:
            raise ValueError("condition slot contains an invalid condition")
        _validate_digest(self.matched_batch_digest, "condition-slot matched batch digest")


def _dispatches_for_seed(
    pair_batches: dict[str, str], order_seed: bytes
) -> tuple[SuccessorPairDispatchV2, ...]:
    pair_ids = tuple(pair_batches)
    chronology = sorted(
        pair_ids,
        key=lambda pair_id: (_rank(order_seed, b"pair-rank", pair_id), pair_id),
    )
    balance_rank = sorted(
        pair_ids,
        key=lambda pair_id: (_rank(order_seed, b"condition-balance", pair_id), pair_id),
    )
    brain_on_first = set(balance_rank[: SUCCESSOR_PAIR_COUNT_V2 // 2])
    return tuple(
        SuccessorPairDispatchV2(
            pair_id=pair_id,
            dispatch_index=dispatch_index,
            condition_order=(
                (
                    SuccessorConditionV2.BRAIN_ON,
                    SuccessorConditionV2.BRAIN_OFF,
                )
                if pair_id in brain_on_first
                else (
                    SuccessorConditionV2.BRAIN_OFF,
                    SuccessorConditionV2.BRAIN_ON,
                )
            ),
            matched_batch_digest=pair_batches[pair_id],
        )
        for dispatch_index, pair_id in enumerate(chronology)
    )


def build_successor_pair_dispatch_v2(
    pair_batches: dict[str, str], *, order_seed: bytes
) -> SuccessorPairDispatchPlanV2:
    """Seal exactly twenty pair orders without accepting any outcome material."""

    if type(pair_batches) is not dict or len(pair_batches) != SUCCESSOR_PAIR_COUNT_V2:
        raise ValueError("successor dispatch requires an exact twenty-pair batch mapping")
    if not all(type(key) is str for key in pair_batches):
        raise ValueError("successor dispatch pair ids must be plain strings")
    for pair_id, batch_digest in pair_batches.items():
        _validate_pair_id(pair_id)
        _validate_digest(batch_digest, "matched batch digest")
    seed = _validate_seed(order_seed)
    dispatches = _dispatches_for_seed(pair_batches, seed)
    return SuccessorPairDispatchPlanV2(
        order_seed_commitment=_seed_commitment(seed),
        pair_dispatch_payload_digest=_dispatch_payload_digest(dispatches),
        dispatches=dispatches,
    )


def successor_condition_slots_v2(
    plan: SuccessorPairDispatchPlanV2,
) -> tuple[SuccessorConditionSlotV2, ...]:
    """Derive the exact adjacent forty-slot chronology from a verified plan."""

    if type(plan) is not SuccessorPairDispatchPlanV2:
        raise ValueError("condition slots require an exact successor dispatch plan")
    plan.validate_current()
    return tuple(
        SuccessorConditionSlotV2(
            ordinal=2 * dispatch.dispatch_index + offset,
            pair_id=dispatch.pair_id,
            dispatch_index=dispatch.dispatch_index,
            condition=condition,
            matched_batch_digest=dispatch.matched_batch_digest,
        )
        for dispatch in plan.dispatches
        for offset, condition in enumerate(dispatch.condition_order)
    )


__all__ = [
    "SUCCESSOR_CONDITION_SLOT_COUNT_V2",
    "SUCCESSOR_PAIR_COUNT_V2",
    "SUCCESSOR_PAIR_DISPATCH_SCHEMA_V2",
    "SUCCESSOR_PAIR_DISPATCH_SCHEMA_VERSION_V2",
    "SuccessorConditionSlotV2",
    "SuccessorConditionV2",
    "SuccessorPairDispatchPlanV2",
    "SuccessorPairDispatchV2",
    "build_successor_pair_dispatch_v2",
    "successor_condition_slots_v2",
]
