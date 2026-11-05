from __future__ import annotations

import hashlib
import inspect
from dataclasses import replace

import pytest

from math_brain.models import canonical_json
from math_brain.successor_dispatch_v2 import (
    SUCCESSOR_CONDITION_SLOT_COUNT_V2,
    SUCCESSOR_PAIR_COUNT_V2,
    SuccessorConditionV2,
    SuccessorPairDispatchPlanV2,
    build_successor_pair_dispatch_v2,
    successor_condition_slots_v2,
)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def _document_digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _pair_batches() -> dict[str, str]:
    return {f"pair-{index:02d}": _digest(f"batch-{index:02d}") for index in range(20)}


def test_successor_dispatch_seals_exact_ten_ten_adjacent_chronology() -> None:
    plan = build_successor_pair_dispatch_v2(_pair_batches(), order_seed=b"a" * 32)
    plan.verify_order_seed(b"a" * 32)
    with pytest.raises(ValueError, match="does not match"):
        plan.verify_order_seed(b"b" * 32)

    assert len(plan.dispatches) == SUCCESSOR_PAIR_COUNT_V2
    assert [dispatch.dispatch_index for dispatch in plan.dispatches] == list(range(20))
    assert len({dispatch.pair_id for dispatch in plan.dispatches}) == 20
    assert sum(
        dispatch.condition_order[0] is SuccessorConditionV2.BRAIN_ON
        for dispatch in plan.dispatches
    ) == 10

    slots = successor_condition_slots_v2(plan)
    assert len(slots) == SUCCESSOR_CONDITION_SLOT_COUNT_V2
    assert [slot.ordinal for slot in slots] == list(range(40))
    for dispatch in plan.dispatches:
        pair_slots = slots[2 * dispatch.dispatch_index : 2 * dispatch.dispatch_index + 2]
        assert tuple(slot.pair_id for slot in pair_slots) == (dispatch.pair_id,) * 2
        assert tuple(slot.condition for slot in pair_slots) == dispatch.condition_order
        assert {slot.matched_batch_digest for slot in pair_slots} == {
            dispatch.matched_batch_digest
        }


def test_successor_dispatch_is_deterministic_and_seed_separated() -> None:
    batches = _pair_batches()
    first = build_successor_pair_dispatch_v2(batches, order_seed=b"a" * 32)
    replay = build_successor_pair_dispatch_v2(batches, order_seed=b"a" * 32)
    second = build_successor_pair_dispatch_v2(batches, order_seed=b"b" * 32)

    assert first == replay
    assert first.order_seed_commitment != second.order_seed_commitment
    assert first.pair_dispatch_payload_digest != second.pair_dispatch_payload_digest
    assert first.dispatches != second.dispatches
    assert {item.pair_id: item.matched_batch_digest for item in first.dispatches} == batches
    assert {item.pair_id: item.matched_batch_digest for item in second.dispatches} == batches


def test_successor_dispatch_round_trips_and_rejects_tampering() -> None:
    plan = build_successor_pair_dispatch_v2(_pair_batches(), order_seed=b"c" * 32)
    assert SuccessorPairDispatchPlanV2.from_dict(plan.to_dict()) == plan

    changed_dispatch = replace(
        plan.dispatches[0],
        matched_batch_digest=_digest("tampered-batch"),
    )
    with pytest.raises(ValueError, match="does not bind"):
        replace(plan, dispatches=(changed_dispatch, *plan.dispatches[1:]))

    document = plan.to_dict()
    document["schema_version"] = True
    with pytest.raises(ValueError, match="unsupported schema"):
        SuccessorPairDispatchPlanV2.from_dict(document)


def test_order_seed_must_generate_the_exact_bound_payload() -> None:
    first = build_successor_pair_dispatch_v2(_pair_batches(), order_seed=b"a" * 32)
    second = build_successor_pair_dispatch_v2(_pair_batches(), order_seed=b"b" * 32)
    transplanted_commitment = replace(
        first,
        order_seed_commitment=second.order_seed_commitment,
    )
    with pytest.raises(ValueError, match="does not generate"):
        transplanted_commitment.verify_order_seed(b"b" * 32)

    document = first.to_dict()
    dispatches = document["dispatches"]
    assert type(dispatches) is list
    on_index = next(
        index for index, item in enumerate(dispatches) if item["condition_order"][0] == "brain_on"
    )
    off_index = next(
        index
        for index, item in enumerate(dispatches)
        if item["condition_order"][0] == "brain_off"
    )
    for index in (on_index, off_index):
        dispatches[index]["condition_order"].reverse()
    document["pair_dispatch_payload_digest"] = _document_digest(dispatches)
    balanced_forgery = SuccessorPairDispatchPlanV2.from_dict(document)
    with pytest.raises(ValueError, match="does not generate"):
        balanced_forgery.verify_order_seed(b"a" * 32)


def test_condition_slot_consumption_revalidates_frozen_nested_records() -> None:
    plan = build_successor_pair_dispatch_v2(_pair_batches(), order_seed=b"d" * 32)
    dispatch = plan.dispatches[0]
    object.__setattr__(dispatch, "condition_order", tuple(reversed(dispatch.condition_order)))

    with pytest.raises(ValueError, match=r"balance|bind"):
        successor_condition_slots_v2(plan)


def test_successor_dispatch_rejects_bad_inputs() -> None:
    batches = _pair_batches()
    with pytest.raises(ValueError, match="twenty-pair"):
        build_successor_pair_dispatch_v2(dict(list(batches.items())[:-1]), order_seed=b"a" * 32)
    with pytest.raises(ValueError, match="canonical SHA-256"):
        build_successor_pair_dispatch_v2(
            {**batches, "pair-00": "not-a-digest"},
            order_seed=b"a" * 32,
        )
    with pytest.raises(ValueError, match="exact bytes"):
        build_successor_pair_dispatch_v2(batches, order_seed=bytearray(32))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exact bytes"):
        build_successor_pair_dispatch_v2(batches, order_seed=b"short")


def test_successor_dispatch_builder_has_no_outcome_input() -> None:
    signature = inspect.signature(build_successor_pair_dispatch_v2)
    assert tuple(signature.parameters) == ("pair_batches", "order_seed")
    assert all("outcome" not in name for name in signature.parameters)
