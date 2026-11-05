from __future__ import annotations

import copy
import hashlib
import inspect
import json

import pytest

import math_brain.recurrence_axis_v3 as axis_module
from math_brain.recurrence_axis_v3 import (
    MAX_RECURRENCE_AXIS_PLAN_JSON_BYTES_V3,
    RECURRENCE_AXES_PER_BANK_V3,
    RECURRENCE_AXIS_FAULTS_V3,
    RecurrenceAxisPlanV3,
    RecurrenceAxisSlotV3,
    ReplayCheckedRecurrenceAxisPlanV3,
    ReplayCheckedRecurrenceClaimBankV3,
    build_recurrence_axis_plan_v3,
    recurrence_axis_seed_commitment_v3,
    replay_and_bind_preregistered_recurrence_claim_bank_v3,
    replay_and_bind_recurrence_axis_plan_v3,
    verify_recurrence_axis_plan_v3,
)
from math_brain.recurrence_binding_v3 import (
    RecurrenceGroupBindingV3,
    RecurrenceInstanceV3,
    build_recurrence_group_binding_v3,
)
from math_brain.recurrence_manifest_v3 import (
    RecurrenceBindingBankManifestV3,
    build_preregistered_recurrence_source_role_claim_bank_v3,
    build_recurrence_binding_bank_manifest_v3,
)
from math_brain.source_role_v3 import SourceRoleClaimBankV3, SourceRoleFaultV3


def _seed(label: str) -> bytes:
    return hashlib.sha256(label.encode("ascii")).digest()


def _binding(*, r: int = 2, c0: int = 1, c1: int = 3) -> RecurrenceGroupBindingV3:
    return build_recurrence_group_binding_v3(RecurrenceInstanceV3(r=r, c0=c0, c1=c1))


def _manifest(*bindings: RecurrenceGroupBindingV3) -> RecurrenceBindingBankManifestV3:
    return build_recurrence_binding_bank_manifest_v3(bindings)


def _bank(
    manifest: RecurrenceBindingBankManifestV3,
    *,
    bank_index: int,
    seed: bytes,
) -> SourceRoleClaimBankV3:
    return build_preregistered_recurrence_source_role_claim_bank_v3(
        manifest=manifest,
        bank_index=bank_index,
        schedule_seed=seed,
        expected_manifest_digest=manifest.content_digest,
    )


def _axis_commitment(
    manifest: RecurrenceBindingBankManifestV3, seed: bytes
) -> str:
    return recurrence_axis_seed_commitment_v3(
        axis_seed=seed,
        manifest_digest=manifest.content_digest,
    )


def _axis_plan(
    manifest: RecurrenceBindingBankManifestV3, label: str
) -> RecurrenceAxisPlanV3:
    seed = _seed(label)
    return build_recurrence_axis_plan_v3(
        manifest=manifest,
        axis_seed=seed,
        expected_manifest_digest=manifest.content_digest,
        expected_axis_seed_commitment=_axis_commitment(manifest, seed),
    )


@pytest.fixture(scope="module")
def manifest() -> RecurrenceBindingBankManifestV3:
    return _manifest(_binding(), _binding(r=3))


@pytest.fixture(scope="module")
def plan(manifest: RecurrenceBindingBankManifestV3) -> RecurrenceAxisPlanV3:
    return _axis_plan(manifest, "axis-default")


def test_axis_plan_is_complete_balanced_deterministic_and_round_trips(
    manifest: RecurrenceBindingBankManifestV3,
    plan: RecurrenceAxisPlanV3,
) -> None:
    rebuilt = _axis_plan(manifest, "axis-default")
    assert rebuilt == plan
    assert len(plan.slots) == len(manifest.entries) * RECURRENCE_AXES_PER_BANK_V3
    assert tuple(slot.slot_index for slot in plan.slots) == tuple(range(len(plan.slots)))
    for entry in manifest.entries:
        slots = tuple(slot for slot in plan.slots if slot.bank_index == entry.bank_index)
        assert len(slots) == 16
        assert {(slot.claim_index, slot.fault) for slot in slots} == {
            (claim_index, fault)
            for claim_index in range(8)
            for fault in RECURRENCE_AXIS_FAULTS_V3
        }
        assert {slot.entry_context_digest for slot in slots} == {
            entry.context_digest(manifest.content_digest)
        }
        assert all(
            sum(slot.claim_index == claim_index for slot in slots) == 2
            for claim_index in range(8)
        )
        assert all(
            sum(slot.fault is fault for slot in slots) == 8
            for fault in RECURRENCE_AXIS_FAULTS_V3
        )

    assert RecurrenceAxisPlanV3.from_dict(plan.to_dict()) == plan
    assert RecurrenceAxisPlanV3.from_json(plan.canonical_json()) == plan
    assert plan.content_digest == (
        "sha256:cb5cb19401c76ecce81e74b44e735ec981cd5e398efcea6ad3054366f1c7c8a4"
    )


def test_axis_api_accepts_no_raw_answer_or_schedule_axis(
    manifest: RecurrenceBindingBankManifestV3,
) -> None:
    assert tuple(inspect.signature(build_recurrence_axis_plan_v3).parameters) == (
        "manifest",
        "axis_seed",
        "expected_manifest_digest",
        "expected_axis_seed_commitment",
    )
    assert tuple(inspect.signature(verify_recurrence_axis_plan_v3).parameters) == (
        "manifest",
        "axis_plan",
        "axis_seed",
        "expected_manifest_digest",
        "expected_axis_seed_commitment",
        "expected_axis_plan_digest",
    )
    assert tuple(
        inspect.signature(
            replay_and_bind_preregistered_recurrence_claim_bank_v3
        ).parameters
    ) == (
        "manifest",
        "bank_index",
        "claim_bank",
        "schedule_seed",
        "expected_manifest_digest",
    )
    forbidden = {
        "answer",
        "claim_index",
        "fault",
        "mechanism",
        "position",
        "root",
        "schedule_seed",
        "surface",
        "template",
    }
    assert not forbidden & set(inspect.signature(build_recurrence_axis_plan_v3).parameters)

    with pytest.raises(TypeError):
        build_recurrence_axis_plan_v3(  # type: ignore[call-arg]
            manifest=manifest,
            axis_seed=_seed("axis"),
            expected_manifest_digest=manifest.content_digest,
            expected_axis_seed_commitment=_axis_commitment(manifest, _seed("axis")),
            fault=SourceRoleFaultV3.SWAP_CANONICAL_FIRST,
        )


def test_expected_manifest_fails_before_seed_or_axis_generation(
    manifest: RecurrenceBindingBankManifestV3,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = _manifest(_binding(r=4))

    def forbidden_seed(_value: object) -> bytes:
        raise AssertionError("axis seed was inspected before the external manifest")

    monkeypatch.setattr(axis_module, "_validate_seed", forbidden_seed)
    with pytest.raises(ValueError, match="external commitment"):
        build_recurrence_axis_plan_v3(
            manifest=manifest,
            axis_seed=object(),  # type: ignore[arg-type]
            expected_manifest_digest=other.content_digest,
            expected_axis_seed_commitment=object(),  # type: ignore[arg-type]
        )


def test_expected_axis_plan_fails_before_seed_replay(
    manifest: RecurrenceBindingBankManifestV3,
    plan: RecurrenceAxisPlanV3,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = _axis_plan(manifest, "other-axis")

    def forbidden_seed(_value: object) -> bytes:
        raise AssertionError("axis seed was inspected before the external axis plan")

    monkeypatch.setattr(axis_module, "_validate_seed", forbidden_seed)
    with pytest.raises(ValueError, match="external commitment"):
        verify_recurrence_axis_plan_v3(
            manifest=manifest,
            axis_plan=plan,
            axis_seed=object(),  # type: ignore[arg-type]
            expected_manifest_digest=manifest.content_digest,
            expected_axis_seed_commitment=plan.axis_seed_commitment,
            expected_axis_plan_digest=other.content_digest,
        )


def test_expected_axis_seed_commitment_fails_before_any_axis_ranking(
    manifest: RecurrenceBindingBankManifestV3,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = _seed("axis-default")

    def forbidden_rank(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("axis ranking ran before the external seed commitment")

    monkeypatch.setattr(axis_module, "_axis_rank", forbidden_rank)
    with pytest.raises(ValueError, match="external commitment"):
        build_recurrence_axis_plan_v3(
            manifest=manifest,
            axis_seed=seed,
            expected_manifest_digest=manifest.content_digest,
            expected_axis_seed_commitment=_axis_commitment(
                manifest, _seed("different-axis")
            ),
        )


def test_axis_seed_only_changes_private_order_and_plan_commitment(
    manifest: RecurrenceBindingBankManifestV3,
    plan: RecurrenceAxisPlanV3,
) -> None:
    other = _axis_plan(manifest, "other-axis")
    assert other != plan
    assert other.axis_seed_commitment != plan.axis_seed_commitment
    assert other.content_digest != plan.content_digest
    identity = lambda slot: (  # noqa: E731
        slot.bank_index,
        slot.claim_index,
        slot.fault,
        slot.axis_digest,
    )
    assert {identity(slot) for slot in other.slots} == {identity(slot) for slot in plan.slots}
    assert tuple(identity(slot) for slot in other.slots) != tuple(
        identity(slot) for slot in plan.slots
    )


def test_source_role_schedule_cannot_change_axis_plan(
    manifest: RecurrenceBindingBankManifestV3,
    plan: RecurrenceAxisPlanV3,
) -> None:
    first = _bank(manifest, bank_index=0, seed=_seed("schedule-a"))
    second = _bank(manifest, bank_index=0, seed=_seed("schedule-b"))
    assert first != second
    assert first.payload_digest != second.payload_digest
    after = _axis_plan(manifest, "axis-default")
    assert after == plan
    assert not hasattr(plan, "schedule_seed_commitment")
    assert all(not hasattr(slot, "claim_bank") for slot in plan.slots)


def test_axis_verification_rejects_wrong_seed_cross_manifest_and_transplant(
    manifest: RecurrenceBindingBankManifestV3,
    plan: RecurrenceAxisPlanV3,
) -> None:
    verify_recurrence_axis_plan_v3(
        manifest=manifest,
        axis_plan=plan,
        axis_seed=_seed("axis-default"),
        expected_manifest_digest=manifest.content_digest,
        expected_axis_seed_commitment=plan.axis_seed_commitment,
        expected_axis_plan_digest=plan.content_digest,
    )
    with pytest.raises(ValueError, match="seed does not match"):
        verify_recurrence_axis_plan_v3(
            manifest=manifest,
            axis_plan=plan,
            axis_seed=_seed("wrong-axis"),
            expected_manifest_digest=manifest.content_digest,
            expected_axis_seed_commitment=plan.axis_seed_commitment,
            expected_axis_plan_digest=plan.content_digest,
        )

    other_manifest = _manifest(_binding(), _binding(r=4))
    with pytest.raises(ValueError, match="external commitment"):
        verify_recurrence_axis_plan_v3(
            manifest=other_manifest,
            axis_plan=plan,
            axis_seed=_seed("axis-default"),
            expected_manifest_digest=manifest.content_digest,
            expected_axis_seed_commitment=plan.axis_seed_commitment,
            expected_axis_plan_digest=plan.content_digest,
        )

    transplanted = copy.deepcopy(plan.to_dict())
    first, second = transplanted["slots"][0], transplanted["slots"][16]
    first["entry_context_digest"] = second["entry_context_digest"]
    first["axis_digest"] = axis_module._digest(
        axis_module._slot_axis_payload(
            manifest_digest=first["manifest_digest"],
            entry_context_digest=first["entry_context_digest"],
            bank_index=first["bank_index"],
            claim_index=first["claim_index"],
            fault=SourceRoleFaultV3(first["fault"]),
        ),
        domain=axis_module._AXIS_DIGEST_DOMAIN,
    )
    payload = {key: value for key, value in transplanted.items() if key != "content_digest"}
    transplanted["content_digest"] = axis_module._digest(
        payload, domain=axis_module._PLAN_DIGEST_DOMAIN
    )
    with pytest.raises(ValueError, match="mixes entry contexts"):
        RecurrenceAxisPlanV3.from_dict(transplanted)


def test_replay_checked_markers_are_distinct_nominal_runtime_types(
    manifest: RecurrenceBindingBankManifestV3,
    plan: RecurrenceAxisPlanV3,
) -> None:
    bank_seed = _seed("schedule-receipt")
    raw_bank = _bank(manifest, bank_index=0, seed=bank_seed)
    bank_receipt = replay_and_bind_preregistered_recurrence_claim_bank_v3(
        manifest=manifest,
        bank_index=0,
        claim_bank=raw_bank,
        schedule_seed=bank_seed,
        expected_manifest_digest=manifest.content_digest,
    )
    axis_receipt = replay_and_bind_recurrence_axis_plan_v3(
        manifest=manifest,
        axis_plan=plan,
        axis_seed=_seed("axis-default"),
        expected_manifest_digest=manifest.content_digest,
        expected_axis_seed_commitment=plan.axis_seed_commitment,
        expected_axis_plan_digest=plan.content_digest,
    )
    assert type(raw_bank) is SourceRoleClaimBankV3
    assert type(bank_receipt) is ReplayCheckedRecurrenceClaimBankV3
    assert type(axis_receipt) is ReplayCheckedRecurrenceAxisPlanV3
    assert bank_receipt.claim_bank is raw_bank
    assert axis_receipt.axis_plan is plan
    bank_receipt.validate_current()
    axis_receipt.validate_current()
    assert bank_receipt == replay_and_bind_preregistered_recurrence_claim_bank_v3(
        manifest=manifest,
        bank_index=0,
        claim_bank=raw_bank,
        schedule_seed=bank_seed,
        expected_manifest_digest=manifest.content_digest,
    )
    with pytest.raises(TypeError, match="returned by the replay facade"):
        ReplayCheckedRecurrenceClaimBankV3()
    with pytest.raises(TypeError, match="returned by the replay facade"):
        ReplayCheckedRecurrenceAxisPlanV3()


def test_nominal_markers_do_not_claim_signed_or_external_provenance(
    manifest: RecurrenceBindingBankManifestV3,
    plan: RecurrenceAxisPlanV3,
) -> None:
    schedule_seed = _seed("nominal-marker")
    bank = _bank(manifest, bank_index=0, seed=schedule_seed)
    entry = manifest.entries[0]

    forged_bank_marker = axis_module._new_replay_checked_claim_bank(
        manifest_digest=manifest.content_digest,
        bank_index=0,
        entry_context_digest=entry.context_digest(manifest.content_digest),
        binding_digest=entry.binding_digest,
        claim_bank=bank,
    )
    forged_plan_marker = axis_module._new_replay_checked_axis_plan(
        manifest_digest=manifest.content_digest,
        axis_plan=plan,
    )
    forged_bank_marker.validate_current()
    forged_plan_marker.validate_current()
    assert type(forged_bank_marker) is ReplayCheckedRecurrenceClaimBankV3
    assert type(forged_plan_marker) is ReplayCheckedRecurrenceAxisPlanV3


def test_replay_checked_bank_rejects_wrong_seed_entry_manifest_and_nested_tamper(
    manifest: RecurrenceBindingBankManifestV3,
) -> None:
    schedule_seed = _seed("receipt-bank")
    bank = _bank(manifest, bank_index=0, seed=schedule_seed)
    with pytest.raises(ValueError, match="commitment"):
        replay_and_bind_preregistered_recurrence_claim_bank_v3(
            manifest=manifest,
            bank_index=0,
            claim_bank=bank,
            schedule_seed=_seed("wrong-schedule"),
            expected_manifest_digest=manifest.content_digest,
        )
    with pytest.raises(ValueError, match="not bound"):
        replay_and_bind_preregistered_recurrence_claim_bank_v3(
            manifest=manifest,
            bank_index=1,
            claim_bank=bank,
            schedule_seed=schedule_seed,
            expected_manifest_digest=manifest.content_digest,
        )

    other_manifest = _manifest(_binding(), _binding(r=4))
    with pytest.raises(ValueError, match="external commitment"):
        replay_and_bind_preregistered_recurrence_claim_bank_v3(
            manifest=other_manifest,
            bank_index=0,
            claim_bank=bank,
            schedule_seed=schedule_seed,
            expected_manifest_digest=manifest.content_digest,
        )

    receipt = replay_and_bind_preregistered_recurrence_claim_bank_v3(
        manifest=manifest,
        bank_index=0,
        claim_bank=bank,
        schedule_seed=schedule_seed,
        expected_manifest_digest=manifest.content_digest,
    )
    object.__setattr__(receipt, "binding_digest", manifest.entries[1].binding_digest)
    with pytest.raises(ValueError, match="digest is inconsistent"):
        receipt.validate_current()


class _SlotSubclass(RecurrenceAxisSlotV3):
    pass


class _PlanSubclass(RecurrenceAxisPlanV3):
    pass


class _TupleSubclass(tuple):
    pass


def test_exact_types_and_mutation_boundaries_fail_closed(
    plan: RecurrenceAxisPlanV3,
) -> None:
    slot = plan.slots[0]
    with pytest.raises(ValueError, match="subclasses are forbidden"):
        _SlotSubclass(
            slot_index=slot.slot_index,
            bank_index=slot.bank_index,
            claim_index=slot.claim_index,
            fault=slot.fault,
            entry_context_digest=slot.entry_context_digest,
            manifest_digest=slot.manifest_digest,
        )
    with pytest.raises(ValueError, match="subclasses are forbidden"):
        _PlanSubclass(
            manifest_digest=plan.manifest_digest,
            axis_seed_commitment=plan.axis_seed_commitment,
            slots=plan.slots,
        )

    mutated = copy.deepcopy(plan)
    object.__setattr__(mutated, "slots", _TupleSubclass(mutated.slots))
    with pytest.raises(ValueError, match="exact tuple"):
        mutated.validate_current()

    mutated = copy.deepcopy(plan)
    object.__setattr__(mutated.slots[0], "claim_index", True)
    with pytest.raises(ValueError, match="exact integer"):
        mutated.validate_current()

    raw = plan.to_dict()
    raw["schema_version"] = True
    with pytest.raises(ValueError, match="exact integer"):
        RecurrenceAxisPlanV3.from_dict(raw)


def test_plan_rejects_missing_duplicate_and_reordered_axes_even_after_rehash(
    plan: RecurrenceAxisPlanV3,
) -> None:
    missing = copy.deepcopy(plan.to_dict())
    missing["slots"].pop()
    for index, slot in enumerate(missing["slots"]):
        slot["slot_index"] = index
    with pytest.raises(ValueError, match="complete per-bank"):
        RecurrenceAxisPlanV3.from_dict(missing)

    duplicate = copy.deepcopy(plan.to_dict())
    replacement = copy.deepcopy(duplicate["slots"][0])
    replacement["slot_index"] = duplicate["slots"][1]["slot_index"]
    duplicate["slots"][1] = replacement
    payload = {key: value for key, value in duplicate.items() if key != "content_digest"}
    duplicate["content_digest"] = axis_module._digest(
        payload, domain=axis_module._PLAN_DIGEST_DOMAIN
    )
    with pytest.raises(ValueError, match="Root x Mechanism"):
        RecurrenceAxisPlanV3.from_dict(duplicate)

    reordered = copy.deepcopy(plan.to_dict())
    reordered["slots"][0], reordered["slots"][1] = (
        reordered["slots"][1],
        reordered["slots"][0],
    )
    with pytest.raises(ValueError, match="slot indices must be contiguous"):
        RecurrenceAxisPlanV3.from_dict(reordered)


def test_full_seed_replay_rejects_a_structurally_valid_rehashed_permutation(
    manifest: RecurrenceBindingBankManifestV3,
    plan: RecurrenceAxisPlanV3,
) -> None:
    reordered = copy.deepcopy(plan.to_dict())
    reordered["slots"][0], reordered["slots"][1] = (
        reordered["slots"][1],
        reordered["slots"][0],
    )
    for slot_index, slot in enumerate(reordered["slots"]):
        slot["slot_index"] = slot_index
    payload = {key: value for key, value in reordered.items() if key != "content_digest"}
    reordered["content_digest"] = axis_module._digest(
        payload, domain=axis_module._PLAN_DIGEST_DOMAIN
    )
    structurally_valid = RecurrenceAxisPlanV3.from_dict(reordered)
    structurally_valid.validate_against_manifest(manifest)

    with pytest.raises(ValueError, match="does not reconstruct"):
        verify_recurrence_axis_plan_v3(
            manifest=manifest,
            axis_plan=structurally_valid,
            axis_seed=_seed("axis-default"),
            expected_manifest_digest=manifest.content_digest,
            expected_axis_seed_commitment=plan.axis_seed_commitment,
            expected_axis_plan_digest=structurally_valid.content_digest,
        )


def test_canonical_json_boundary_rejects_duplicates_limits_and_noncanonical_text(
    plan: RecurrenceAxisPlanV3,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="duplicate JSON key"):
        RecurrenceAxisPlanV3.from_json('{"schema":"a","schema":"b"}')
    with pytest.raises(ValueError, match="canonical encoding"):
        RecurrenceAxisPlanV3.from_json(json.dumps(plan.to_dict(), indent=2, sort_keys=True))
    with pytest.raises(ValueError, match="byte budget"):
        RecurrenceAxisPlanV3.from_json(" " * (MAX_RECURRENCE_AXIS_PLAN_JSON_BYTES_V3 + 1))
    with pytest.raises(ValueError, match="valid UTF-8"):
        RecurrenceAxisPlanV3.from_json("\ud800")
    with pytest.raises(ValueError, match="non-finite"):
        RecurrenceAxisPlanV3.from_json('{"value":NaN}')

    raw = plan.canonical_json()

    def recursive_encoder(_value: object) -> str:
        raise RecursionError("synthetic recursion")

    monkeypatch.setattr(axis_module, "canonical_json", recursive_encoder)
    with pytest.raises(ValueError, match="cannot be canonically re-encoded"):
        RecurrenceAxisPlanV3.from_json(raw)
