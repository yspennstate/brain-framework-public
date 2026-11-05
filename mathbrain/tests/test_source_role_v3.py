from __future__ import annotations

import hashlib
import inspect
from dataclasses import replace
from fractions import Fraction
from itertools import permutations

import pytest

import math_brain.source_role_v3 as source_role_module
from math_brain.source_role_v3 import (
    MAXIMUM_COEFFICIENT_ORBIT_PLANE_SUPPORT_V3,
    SOURCE_ROLE_CLAIM_COUNT_V3,
    SourceRoleBankV3,
    SourceRoleClaimBankV3,
    SourceRoleClaimV3,
    SourceRoleFaultV3,
    build_source_role_claim_bank_v3,
    coefficient_plane_outlier_candidates_v3,
    maximum_affine_plane_support_v3,
    maximum_coefficient_orbit_plane_support_v3,
    maximum_term_position_affine_plane_support_v3,
    source_role_input_digest_v3,
    term_position_plane_outlier_candidates_v3,
)


def _binding(label: str = "group-a") -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


def _source_bank(
    *,
    names: tuple[str, str, str] = ("canonical", "first_alt", "second_alt"),
    values: tuple[Fraction, Fraction, Fraction] = (
        Fraction(2),
        Fraction(3),
        Fraction(5),
    ),
) -> SourceRoleBankV3:
    return SourceRoleBankV3(source_names=names, source_values=values)


def _targets() -> tuple[str, ...]:
    return tuple(f"claim{index}" for index in range(SOURCE_ROLE_CLAIM_COUNT_V3))


def _offsets() -> tuple[Fraction, ...]:
    return tuple(Fraction(index, 3) for index in range(SOURCE_ROLE_CLAIM_COUNT_V3))


def _build(
    *,
    schedule_seed: bytes = b"s" * 32,
    group_binding_digest: str | None = None,
    source_bank: SourceRoleBankV3 | None = None,
    targets: tuple[str, ...] | None = None,
    offsets: tuple[Fraction, ...] | None = None,
) -> SourceRoleClaimBankV3:
    return build_source_role_claim_bank_v3(
        source_bank=source_bank or _source_bank(),
        group_binding_digest=group_binding_digest or _binding(),
        targets=targets or _targets(),
        offsets=offsets or _offsets(),
        schedule_seed=schedule_seed,
    )


def _dot(
    weights: tuple[int, int, int],
    sources: tuple[Fraction, Fraction, Fraction],
) -> Fraction:
    return sum(
        (
            Fraction(weight) * source
            for weight, source in zip(weights, sources, strict=True)
        ),
        Fraction(0),
    )


def _claim(
    index: int,
    weights: tuple[int, int, int],
    *,
    term_order: tuple[int, int, int] = (0, 1, 2),
) -> SourceRoleClaimV3:
    offset = Fraction(index, 7)
    return SourceRoleClaimV3(
        target=f"point{index}",
        target_value=offset + _dot(weights, _source_bank().source_values),
        weights=weights,
        offset=offset,
        term_order=term_order,
    )


def _assert_all_coefficient_orientations_separate(
    claim: SourceRoleClaimV3, source_bank: SourceRoleBankV3
) -> None:
    for oriented in permutations(claim.weights):
        canonical, first, second = oriented
        assignments = (
            oriented,
            (first, canonical, second),
            (second, first, canonical),
        )
        assert len({_dot(assignment, source_bank.source_values) for assignment in assignments}) == 3


def _assert_balanced_term_orders(claims: tuple[SourceRoleClaimV3, ...]) -> None:
    orders = tuple(claim.term_order for claim in claims)
    assert set(orders) == set(permutations(range(3)))
    counts = tuple(
        sum(order[position] == source for order in orders)
        for source in range(3)
        for position in range(3)
    )
    assert set(counts) <= {2, 3}


def test_each_claim_genuinely_depends_on_three_sources_and_faults_only_swap_roles() -> None:
    plan = _build()
    canonical, first_alt, second_alt = plan.source_bank.source_values

    assert len(plan.claims) == SOURCE_ROLE_CLAIM_COUNT_V3
    assert len({tuple(sorted(claim.weights)) for claim in plan.claims}) == len(plan.claims)
    assert (
        maximum_coefficient_orbit_plane_support_v3(plan.claims)
        <= MAXIMUM_COEFFICIENT_ORBIT_PLANE_SUPPORT_V3
    )
    _assert_balanced_term_orders(plan.claims)

    for claim in plan.claims:
        clean = claim.value(plan.source_bank)
        fault_first = claim.value(
            plan.source_bank, SourceRoleFaultV3.SWAP_CANONICAL_FIRST
        )
        fault_second = claim.value(
            plan.source_bank, SourceRoleFaultV3.SWAP_CANONICAL_SECOND
        )
        assert clean == claim.target_value
        assert clean == claim.offset + _dot(claim.weights, plan.source_bank.source_values)
        assert len({clean, fault_first, fault_second}) == 3
        _assert_all_coefficient_orientations_separate(claim, plan.source_bank)

        clean_terms = claim.ordered_terms(plan.source_bank)
        first_terms = claim.ordered_terms(
            plan.source_bank, SourceRoleFaultV3.SWAP_CANONICAL_FIRST
        )
        second_terms = claim.ordered_terms(
            plan.source_bank, SourceRoleFaultV3.SWAP_CANONICAL_SECOND
        )
        expected_names = set(plan.source_bank.source_names)
        assert {name for name, _ in clean_terms} == expected_names
        assert {name for name, _ in first_terms} == expected_names
        assert {name for name, _ in second_terms} == expected_names
        assert tuple(name for name, _ in clean_terms) == tuple(
            name for name, _ in first_terms
        )
        assert tuple(name for name, _ in clean_terms) == tuple(
            name for name, _ in second_terms
        )
        assert all(
            coefficient * source != 0
            for coefficient, source in zip(
                claim.weights, plan.source_bank.source_values, strict=True
            )
        )
        assert claim.structural_signature == (
            "add-three-multiplied-sources-plus-offset",
            claim.term_order,
        )

        weight_canonical, weight_first, weight_second = claim.weights
        assert fault_first - clean == (weight_canonical - weight_first) * (
            first_alt - canonical
        )
        assert fault_second - clean == (weight_canonical - weight_second) * (
            second_alt - canonical
        )


def test_offsets_are_schedule_independent_and_source_changes_have_exact_effect() -> None:
    first = _build(schedule_seed=b"s" * 32)
    second = _build(schedule_seed=b"t" * 32)
    assert tuple(claim.offset for claim in first.claims) == _offsets()
    assert tuple(claim.offset for claim in second.claims) == _offsets()

    changed_sources = SourceRoleBankV3(
        source_names=first.source_bank.source_names,
        source_values=(Fraction(7), *first.source_bank.source_values[1:]),
    )
    delta = changed_sources.source_values[0] - first.source_bank.source_values[0]
    for claim in first.claims:
        assert claim.value(changed_sources) - claim.value(first.source_bank) == (
            claim.weights[0] * delta
        )


def test_term_order_schedule_is_balanced_and_independent_of_weight_acceptance() -> None:
    seed = b"s" * 32
    plan = _build(schedule_seed=seed)
    changed_source_plan = _build(
        schedule_seed=seed,
        source_bank=_source_bank(values=(Fraction(7), Fraction(11), Fraction(13))),
    )
    changed_offset_plan = _build(
        schedule_seed=seed,
        offsets=tuple(offset + Fraction(100) for offset in _offsets()),
    )

    _assert_balanced_term_orders(plan.claims)
    assert tuple(claim.term_order for claim in changed_source_plan.claims) == tuple(
        claim.term_order for claim in plan.claims
    )
    assert tuple(claim.term_order for claim in changed_offset_plan.claims) == tuple(
        claim.term_order for claim in plan.claims
    )


def test_schedule_is_context_committed_and_exactly_replayable() -> None:
    seed = b"s" * 32
    plan = _build(schedule_seed=seed)

    assert _build(schedule_seed=seed) == plan
    assert _build(schedule_seed=b"t" * 32) != plan
    assert _build(schedule_seed=seed, group_binding_digest=_binding("group-b")) != plan
    plan.verify_schedule_seed(seed, expected_input_digest=plan.input_digest)
    with pytest.raises(ValueError, match="commitment"):
        plan.verify_schedule_seed(
            b"x" * 32, expected_input_digest=plan.input_digest
        )
    with pytest.raises(ValueError, match="preregistered input digest"):
        plan.verify_schedule_seed(seed, expected_input_digest=_binding("wrong-input"))

    contextual_plans = (
        plan,
        _build(schedule_seed=seed, group_binding_digest=_binding("group-b")),
        _build(
            schedule_seed=seed,
            source_bank=_source_bank(
                names=("renamed_c", "renamed_a", "renamed_b")
            ),
        ),
        _build(
            schedule_seed=seed,
            offsets=tuple(offset + Fraction(1) for offset in _offsets()),
        ),
    )
    assert len({candidate.input_digest for candidate in contextual_plans}) == len(
        contextual_plans
    )
    assert len({candidate.schedule_seed_commitment for candidate in contextual_plans}) == len(
        contextual_plans
    )

    public_parameters = inspect.signature(build_source_role_claim_bank_v3).parameters
    forbidden_cue_parameters = {
        "fault",
        "mechanism",
        "outcome",
        "position",
        "template",
    }
    assert forbidden_cue_parameters.isdisjoint(public_parameters)


def test_preregistered_input_digest_rejects_semantic_reassignment_after_rehash() -> None:
    seed = b"s" * 32
    original = _build(schedule_seed=seed)
    swapped_targets = (*_targets()[1:2], _targets()[0], *_targets()[2:])
    swapped_offsets = (*_offsets()[1:2], _offsets()[0], *_offsets()[2:])
    reassigned = _build(
        schedule_seed=seed,
        targets=swapped_targets,
        offsets=swapped_offsets,
    )

    reassigned.verify_schedule_seed(
        seed, expected_input_digest=reassigned.input_digest
    )
    with pytest.raises(ValueError, match="preregistered input digest"):
        reassigned.verify_schedule_seed(
            seed, expected_input_digest=original.input_digest
        )

    renamed = _build(
        schedule_seed=seed,
        source_bank=_source_bank(names=("other_c", "other_a", "other_b")),
    )
    with pytest.raises(ValueError, match="preregistered input digest"):
        renamed.verify_schedule_seed(seed, expected_input_digest=original.input_digest)


def test_payload_and_nested_state_are_revalidated_after_construction() -> None:
    plan = _build()
    changed_nonce = plan.bank_nonce + 1 if plan.bank_nonce < 4095 else plan.bank_nonce - 1
    with pytest.raises(ValueError, match="payload digest"):
        replace(plan, bank_nonce=changed_nonce)

    other = _build(schedule_seed=b"t" * 32)
    with pytest.raises(ValueError, match="payload digest"):
        replace(plan, schedule_seed_commitment=other.schedule_seed_commitment)

    object.__setattr__(plan.claims[0], "target_value", Fraction(999))
    with pytest.raises(ValueError, match="exact target value"):
        plan.to_dict()
    with pytest.raises(ValueError, match="exact target value"):
        plan.verify_schedule_seed(b"s" * 32, expected_input_digest=plan.input_digest)


def test_bank_boundary_rejects_fault_ambiguity_for_any_coefficient_orientation() -> None:
    plan = _build()
    claim = plan.claims[0]
    ambiguous_weights = (1, 7, 3)
    object.__setattr__(claim, "weights", ambiguous_weights)
    object.__setattr__(
        claim,
        "target_value",
        claim.offset + _dot(ambiguous_weights, plan.source_bank.source_values),
    )
    with pytest.raises(ValueError, match="does not separate both mechanisms"):
        plan.validate_current()


def test_bank_boundary_rejects_coefficients_outside_the_bounded_pool() -> None:
    plan = _build()
    claim = plan.claims[0]
    unbounded_weights = (2, 14, 6)
    object.__setattr__(claim, "weights", unbounded_weights)
    object.__setattr__(
        claim,
        "target_value",
        claim.offset + _dot(unbounded_weights, plan.source_bank.source_values),
    )
    with pytest.raises(ValueError, match="approved bounded pool"):
        plan.validate_current()


def test_affine_plane_audit_identifies_a_single_canonical_outlier() -> None:
    weights = (
        (1, 2, 5),
        (2, 3, 5),
        (3, 4, 5),
        (4, 6, 5),
        (6, 7, 5),
        (7, 9, 5),
        (9, 11, 5),
        (11, 13, 7),
    )
    claims = tuple(_claim(index, point) for index, point in enumerate(weights))

    assert maximum_affine_plane_support_v3(claims) == 7
    assert coefficient_plane_outlier_candidates_v3(claims) == (7,)
    assert maximum_coefficient_orbit_plane_support_v3(claims) >= 7


def test_orbit_plane_gate_detects_seven_collinear_row_orbits() -> None:
    claims = tuple(
        _claim(index, (index + 1, index + 2, index + 3)) for index in range(7)
    )
    assert maximum_coefficient_orbit_plane_support_v3(claims) == 7


def test_solver_visible_geometry_is_audited_after_balanced_term_permutations() -> None:
    term_orders = (
        (0, 1, 2),
        (0, 2, 1),
        (1, 0, 2),
        (1, 2, 0),
        (2, 0, 1),
        (2, 1, 0),
        (0, 1, 2),
        (1, 2, 0),
    )
    canonical_weights = (
        (11, 1, 5),
        (-2, 5, 3),
        (7, -7, 5),
        (5, -3, 7),
        (-11, 5, 9),
        (5, 3, 11),
        (2, 9, 5),
        (5, -2, 9),
    )
    claims = tuple(
        _claim(index, weights, term_order=term_order)
        for index, (weights, term_order) in enumerate(
            zip(canonical_weights, term_orders, strict=True)
        )
    )

    _assert_balanced_term_orders(claims)
    assert maximum_affine_plane_support_v3(claims) == 3
    assert maximum_term_position_affine_plane_support_v3(claims) == 8
    assert term_position_plane_outlier_candidates_v3(claims) == tuple(range(8))


def test_orbit_geometry_gate_is_invariant_to_fault_and_term_coordinate_permutations() -> None:
    plan = _build()
    baseline = maximum_coefficient_orbit_plane_support_v3(plan.claims)
    reoriented: list[SourceRoleClaimV3] = []
    for index, claim in enumerate(plan.claims):
        fault = (
            SourceRoleFaultV3.SWAP_CANONICAL_FIRST
            if index % 2 == 0
            else SourceRoleFaultV3.SWAP_CANONICAL_SECOND
        )
        weights = claim.coefficients(fault)
        reoriented.append(
            replace(
                claim,
                weights=weights,
                target_value=claim.offset + _dot(weights, plan.source_bank.source_values),
                term_order=tuple(reversed(claim.term_order)),
            )
        )
    assert maximum_coefficient_orbit_plane_support_v3(tuple(reoriented)) == baseline


def test_schedule_selection_and_validity_never_consult_clean_only_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("answer-sensitive geometry entered schedule selection")

    monkeypatch.setattr(source_role_module, "maximum_affine_plane_support_v3", forbidden)
    monkeypatch.setattr(
        source_role_module, "coefficient_plane_outlier_candidates_v3", forbidden
    )
    monkeypatch.setattr(
        source_role_module, "maximum_term_position_affine_plane_support_v3", forbidden
    )
    monkeypatch.setattr(
        source_role_module, "term_position_plane_outlier_candidates_v3", forbidden
    )
    plan = _build()
    plan.validate_current()
    plan.verify_schedule_seed(b"s" * 32, expected_input_digest=plan.input_digest)


def test_exact_type_and_shape_boundaries_reject_ambiguous_inputs() -> None:
    names = ("canonical", "first_alt", "second_alt")
    with pytest.raises(ValueError, match="pairwise distinct"):
        SourceRoleBankV3(names, (Fraction(1), Fraction(1), Fraction(2)))
    with pytest.raises(ValueError, match="nonzero"):
        SourceRoleBankV3(names, (Fraction(0), Fraction(1), Fraction(2)))
    with pytest.raises(ValueError, match="exact source values"):
        SourceRoleBankV3(names, (1, Fraction(2), Fraction(3)))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="pairwise distinct"):
        SourceRoleBankV3(("same", "same", "other"), (Fraction(1), Fraction(2), Fraction(3)))

    valid = {
        "target": "target",
        "target_value": Fraction(14),
        "weights": (1, 2, 3),
        "offset": Fraction(0),
        "term_order": (0, 1, 2),
    }
    with pytest.raises(ValueError, match="distinct nonzero"):
        SourceRoleClaimV3(**(valid | {"weights": (1, 1, 2)}))
    with pytest.raises(ValueError, match="distinct nonzero"):
        SourceRoleClaimV3(**(valid | {"weights": (0, 1, 2)}))
    with pytest.raises(ValueError, match="target value"):
        SourceRoleClaimV3(**(valid | {"target_value": 14}))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exact Fraction"):
        SourceRoleClaimV3(**(valid | {"offset": 0}))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="permutation"):
        SourceRoleClaimV3(**(valid | {"term_order": (0, 0, 2)}))

    claim = SourceRoleClaimV3(**valid)
    with pytest.raises(ValueError, match="fault"):
        claim.coefficients("swap_canonical_first")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"targets": _targets()[:-1]}, "exactly eight targets"),
        ({"targets": ("duplicate",) * SOURCE_ROLE_CLAIM_COUNT_V3}, "unique"),
        ({"offsets": _offsets()[:-1]}, "eight exact offsets"),
        ({"offsets": (0, *_offsets()[1:])}, "eight exact offsets"),
        ({"schedule_seed": b"short"}, "32 to 64 bytes"),
        ({"group_binding_digest": "not-a-digest"}, "SHA-256 digest"),
    ],
)
def test_construction_boundaries_fail_closed(
    overrides: dict[str, object], message: str
) -> None:
    arguments: dict[str, object] = {
        "source_bank": _source_bank(),
        "group_binding_digest": _binding(),
        "targets": _targets(),
        "offsets": _offsets(),
        "schedule_seed": b"s" * 32,
    }
    arguments.update(overrides)
    with pytest.raises(ValueError, match=message):
        build_source_role_claim_bank_v3(**arguments)  # type: ignore[arg-type]


def test_input_digest_matches_the_preregistered_semantic_boundary() -> None:
    expected = source_role_input_digest_v3(
        source_bank=_source_bank(),
        group_binding_digest=_binding(),
        targets=_targets(),
        offsets=_offsets(),
    )
    assert _build().input_digest == expected


def test_small_seed_and_group_sweep_preserves_fault_invariant_schedule_guards() -> None:
    for index in range(16):
        seed = hashlib.sha256(f"seed-{index}".encode("ascii")).digest()
        plan = _build(
            schedule_seed=seed,
            group_binding_digest=_binding(f"group-{index}"),
        )
        assert max(abs(weight) for claim in plan.claims for weight in claim.weights) <= 11
        assert len({tuple(sorted(claim.weights)) for claim in plan.claims}) == len(plan.claims)
        assert (
            maximum_coefficient_orbit_plane_support_v3(plan.claims)
            <= MAXIMUM_COEFFICIENT_ORBIT_PLANE_SUPPORT_V3
        )
        _assert_balanced_term_orders(plan.claims)
        for claim in plan.claims:
            assert claim.value(plan.source_bank) == claim.target_value
            _assert_all_coefficient_orientations_separate(claim, plan.source_bank)
        plan.verify_schedule_seed(seed, expected_input_digest=plan.input_digest)
