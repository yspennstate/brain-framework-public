from __future__ import annotations

import copy
import hashlib
import inspect
import json
from dataclasses import fields
from enum import StrEnum
from fractions import Fraction

import pytest

import math_brain.recurrence_binding_v3 as recurrence_module
from math_brain.recurrence_binding_v3 import (
    MAX_RECURRENCE_BINDING_JSON_BYTES_V3,
    RECURRENCE_BINDING_SCHEMA_V3,
    RECURRENCE_PROGRAM_SCHEMA_V3,
    RecurrenceClaimV3,
    RecurrenceGroupBindingV3,
    RecurrenceInstanceV3,
    RecurrenceSemanticProgramV3,
    RecurrenceSourceExpressionKindV3,
    RecurrenceSourceExpressionV3,
    RecurrenceSourceRoleV3,
    RecurrenceTermOffsetV3,
    build_recurrence_group_binding_v3,
    build_recurrence_source_role_claim_bank_v3,
    verify_recurrence_source_role_claim_bank_v3,
)
from math_brain.source_role_v3 import (
    SourceRoleClaimBankV3,
)
from math_brain.source_role_v3 import (
    build_source_role_claim_bank_v3 as build_raw_source_role_claim_bank_v3,
)


class _IntSubclass(int):
    pass


class _RecurrenceInstanceSubclass(RecurrenceInstanceV3):
    pass


def _instance(*, r: int = 2, c0: int = 1, c1: int = 3) -> RecurrenceInstanceV3:
    return RecurrenceInstanceV3(r=r, c0=c0, c1=c1)


def _binding(*, r: int = 2, c0: int = 1, c1: int = 3) -> RecurrenceGroupBindingV3:
    return build_recurrence_group_binding_v3(_instance(r=r, c0=c0, c1=c1))


def _seed(label: str) -> bytes:
    return hashlib.sha256(label.encode("ascii")).digest()


def _plan(
    binding: RecurrenceGroupBindingV3,
    *,
    seed: bytes | None = None,
) -> SourceRoleClaimBankV3:
    return build_recurrence_source_role_claim_bank_v3(
        binding=binding,
        schedule_seed=seed or _seed("default"),
        expected_binding_digest=binding.content_digest,
    )


def _independent_closed_form(instance: RecurrenceInstanceV3) -> tuple[Fraction, ...]:
    root = Fraction(instance.r)
    return tuple(
        root**index
        * (
            Fraction(instance.c0)
            + index * (Fraction(instance.c1, instance.r) - instance.c0)
            + Fraction(index * (index - 1), 2 * instance.r * instance.r)
        )
        for index in range(8)
    )


def _dot(weights: tuple[int, int, int], values: tuple[Fraction, ...]) -> Fraction:
    return sum(
        (
            Fraction(weight) * value
            for weight, value in zip(weights, values, strict=True)
        ),
        Fraction(0),
    )


def _all_text(value: object) -> tuple[str, ...]:
    if type(value) is dict:
        result: list[str] = []
        for key, item in value.items():
            result.append(key)
            result.extend(_all_text(item))
        return tuple(result)
    if type(value) is list:
        return tuple(text for item in value for text in _all_text(item))
    if type(value) is str:
        return (value,)
    return ()


@pytest.fixture(scope="module")
def default_binding() -> RecurrenceGroupBindingV3:
    return _binding()


@pytest.fixture(scope="module")
def default_plan(default_binding: RecurrenceGroupBindingV3) -> SourceRoleClaimBankV3:
    return _plan(default_binding)


def test_exact_typed_execution_drives_every_source_offset_and_target(
    default_binding: RecurrenceGroupBindingV3,
    default_plan: SourceRoleClaimBankV3,
) -> None:
    expected_terms = (
        Fraction(1),
        Fraction(3),
        Fraction(9),
        Fraction(26),
        Fraction(72),
        Fraction(192),
        Fraction(496),
        Fraction(1248),
    )
    assert _independent_closed_form(default_binding.program.instance) == expected_terms

    source_bank, targets, offsets = default_binding.source_role_inputs()
    assert source_bank.source_names == ("canonical", "first_alt", "second_alt")
    assert source_bank.source_values == (Fraction(2), Fraction(1), Fraction(3))
    assert targets == tuple(f"claim{index}" for index in range(8))
    assert offsets == expected_terms

    expected_source_semantics = {
        RecurrenceSourceExpressionKindV3.OPERATOR_ROOT_MULTIPLICITY: (
            RecurrenceSourceRoleV3.CANONICAL,
            Fraction(2),
        ),
        RecurrenceSourceExpressionKindV3.FORCING_ANNIHILATOR_MULTIPLICITY: (
            RecurrenceSourceRoleV3.FIRST_ALTERNATIVE,
            Fraction(1),
        ),
        RecurrenceSourceExpressionKindV3.COMBINED_ANNIHILATOR_MULTIPLICITY: (
            RecurrenceSourceRoleV3.SECOND_ALTERNATIVE,
            Fraction(3),
        ),
    }
    for receipt in default_binding.sources:
        assert (receipt.role, receipt.value) == expected_source_semantics[
            receipt.expression.kind
        ]
    for receipt in default_binding.offsets:
        assert receipt.claim is RecurrenceClaimV3(f"claim{receipt.expression.index}")
        assert receipt.value == expected_terms[receipt.expression.index]

    for index in range(6):
        assert expected_terms[index + 2] == (
            2 * 2 * expected_terms[index + 1]
            - 2**2 * expected_terms[index]
            + 2**index
        )
    for claim, offset in zip(default_plan.claims, expected_terms, strict=True):
        assert claim.offset == offset
        assert claim.target_value - _dot(claim.weights, source_bank.source_values) == offset

    assert default_plan.group_binding_digest == default_binding.content_digest
    assert default_plan.input_digest == default_binding.source_role_input_digest()
    verify_recurrence_source_role_claim_bank_v3(
        binding=default_binding,
        claim_bank=default_plan,
        schedule_seed=_seed("default"),
        expected_binding_digest=default_binding.content_digest,
    )


def test_program_and_binding_have_strict_canonical_round_trips(
    default_binding: RecurrenceGroupBindingV3,
) -> None:
    program = default_binding.program
    assert RecurrenceSemanticProgramV3.from_dict(program.to_dict()) == program
    assert RecurrenceSemanticProgramV3.from_json(program.canonical_json()) == program
    assert RecurrenceGroupBindingV3.from_dict(default_binding.to_dict()) == default_binding
    assert (
        RecurrenceGroupBindingV3.from_json(default_binding.canonical_json())
        == default_binding
    )
    assert len(default_binding.canonical_json().encode("utf-8")) <= (
        MAX_RECURRENCE_BINDING_JSON_BYTES_V3
    )
    assert default_binding.program.program_digest == (
        "sha256:4046adaf84a9a510f55690ac7ffcc36aedfbf2affb12c08d27b62c1f0c96c4a2"
    )
    assert default_binding.content_digest == (
        "sha256:2a93aef806a749e42e5cefc023c07978fdfb99be91c9449a9ae8bd98b18c47eb"
    )
    assert default_binding.source_role_input_digest() == (
        "sha256:af7d6545dd294a949725da384f085d29c9de0a25436088ee3801dcdfc2515652"
    )
    assert len(default_binding.canonical_json().encode("utf-8")) == 2200


def test_binding_is_functionally_independent_of_private_schedule_seed(
    default_binding: RecurrenceGroupBindingV3,
    default_plan: SourceRoleClaimBankV3,
) -> None:
    before = default_binding.canonical_json()
    other_plan = _plan(default_binding, seed=_seed("other"))

    assert default_binding.canonical_json() == before
    assert default_binding.content_digest == RecurrenceGroupBindingV3.from_json(
        before
    ).content_digest
    assert tuple(claim.offset for claim in default_plan.claims) == tuple(
        claim.offset for claim in other_plan.claims
    )
    assert default_plan.schedule_seed_commitment != other_plan.schedule_seed_commitment
    assert default_plan != other_plan


def test_public_api_and_serialized_shape_expose_no_raw_or_answer_axes(
    default_binding: RecurrenceGroupBindingV3,
) -> None:
    assert tuple(inspect.signature(build_recurrence_group_binding_v3).parameters) == (
        "instance",
    )
    assert tuple(
        inspect.signature(build_recurrence_source_role_claim_bank_v3).parameters
    ) == ("binding", "schedule_seed", "expected_binding_digest")
    assert tuple(inspect.signature(RecurrenceSemanticProgramV3).parameters) == ("instance",)
    assert tuple(field.name for field in fields(RecurrenceTermOffsetV3)) == ("index",)
    assert RecurrenceTermOffsetV3(4).to_dict() == {"index": 4, "kind": "recurrence_term"}

    assert not hasattr(recurrence_module, "build_source_role_claim_bank_v3")
    assert not hasattr(recurrence_module, "source_role_input_digest_v3")
    forbidden = {
        "answer",
        "counter",
        "fault",
        "mechanism",
        "nonce",
        "position",
        "prompt",
        "repair",
        "scaffold",
        "seed",
        "tag",
        "template",
        "trace",
        "witness",
    }
    serialized_text = tuple(item.lower() for item in _all_text(default_binding.to_dict()))
    assert all(fragment not in item for fragment in forbidden for item in serialized_text)

    with pytest.raises(TypeError):
        build_recurrence_source_role_claim_bank_v3(
            binding=default_binding,
            schedule_seed=_seed("default"),
            expected_binding_digest=default_binding.content_digest,
            offsets=(Fraction(0),) * 8,  # type: ignore[call-arg]
        )


@pytest.mark.parametrize(
    "arguments",
    [
        {"r": True, "c0": 1, "c1": 3},
        {"r": 2.0, "c0": 1, "c1": 3},
        {"r": _IntSubclass(2), "c0": 1, "c1": 3},
        {"r": 1, "c0": 1, "c1": 3},
        {"r": 8, "c0": 1, "c1": 3},
        {"r": 2, "c0": -10, "c1": 3},
        {"r": 2, "c0": 1, "c1": 10},
    ],
)
def test_instance_boundary_requires_exact_bounded_integers(
    arguments: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="exact integer"):
        RecurrenceInstanceV3(**arguments)  # type: ignore[arg-type]


def test_node_and_parser_boundaries_reject_coercion_and_noncanonical_values(
    default_binding: RecurrenceGroupBindingV3,
) -> None:
    with pytest.raises(ValueError, match="exact integer"):
        RecurrenceTermOffsetV3(True)
    with pytest.raises(ValueError, match="exact integer"):
        RecurrenceTermOffsetV3(_IntSubclass(1))
    with pytest.raises(ValueError, match="exact integer"):
        RecurrenceTermOffsetV3(8)
    with pytest.raises(ValueError, match="kind is invalid"):
        RecurrenceSourceExpressionV3("operator_root_multiplicity")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="kind is invalid"):
        RecurrenceSourceExpressionV3.from_dict({"kind": 1})
    with pytest.raises(ValueError, match="exact text"):
        RecurrenceTermOffsetV3.from_dict({"index": 0, "kind": True})

    instance_raw = default_binding.program.instance.to_dict()
    instance_raw["domain"] = True
    with pytest.raises(ValueError, match="exact text"):
        RecurrenceInstanceV3.from_dict(instance_raw)

    program_raw = default_binding.program.to_dict()
    program_raw["schema_version"] = True
    with pytest.raises(ValueError, match="exact integer"):
        RecurrenceSemanticProgramV3.from_dict(program_raw)

    binding_raw = default_binding.to_dict()
    binding_raw["schema_version"] = True
    with pytest.raises(ValueError, match="exact integer"):
        RecurrenceGroupBindingV3.from_dict(binding_raw)

    source_raw = default_binding.sources[0].to_dict()
    source_raw["value"] = {"denominator": 2, "numerator": 2}
    with pytest.raises(ValueError, match="reduced fraction"):
        recurrence_module.RecurrenceSourceReceiptV3.from_dict(source_raw)
    with pytest.raises(ValueError, match="subclasses are forbidden"):
        _RecurrenceInstanceSubclass(r=2, c0=1, c1=3)


def test_canonical_json_boundary_rejects_duplicates_pretty_text_and_bad_unicode(
    default_binding: RecurrenceGroupBindingV3,
) -> None:
    with pytest.raises(ValueError, match="duplicate JSON key"):
        RecurrenceGroupBindingV3.from_json('{"schema":"a","schema":"b"}')
    with pytest.raises(ValueError, match="canonical encoding"):
        RecurrenceGroupBindingV3.from_json(
            json.dumps(default_binding.to_dict(), indent=2, sort_keys=True)
        )
    with pytest.raises(ValueError, match="byte budget"):
        RecurrenceGroupBindingV3.from_json(
            " " * (MAX_RECURRENCE_BINDING_JSON_BYTES_V3 + 1)
        )
    with pytest.raises(ValueError, match="valid UTF-8"):
        RecurrenceGroupBindingV3.from_json("\ud800")


def test_serialized_tamper_fails_even_after_attacker_rehashes_payload(
    default_binding: RecurrenceGroupBindingV3,
) -> None:
    for case in (
        "source_role",
        "source_expression",
        "source_value",
        "offset_claim",
        "offset_expression",
        "offset_value",
    ):
        binding_raw = copy.deepcopy(default_binding.to_dict())
        if case == "source_role":
            binding_raw["sources"][0]["role"] = "first_alt"
        elif case == "source_expression":
            binding_raw["sources"][0]["expression"]["kind"] = (
                "forcing_annihilator_multiplicity"
            )
        elif case == "source_value":
            binding_raw["sources"][0]["value"] = {
                "denominator": 1,
                "numerator": 99,
            }
        elif case == "offset_claim":
            binding_raw["offsets"][0]["claim"] = "claim1"
        elif case == "offset_expression":
            binding_raw["offsets"][0]["expression"]["index"] = 1
        else:
            binding_raw["offsets"][0]["value"] = {
                "denominator": 1,
                "numerator": 99,
            }
        binding_payload = {
            key: value for key, value in binding_raw.items() if key != "content_digest"
        }
        binding_raw["content_digest"] = recurrence_module._digest(
            binding_payload,
            domain=recurrence_module._BINDING_DIGEST_DOMAIN,
        )
        with pytest.raises(ValueError, match="differ from exact execution"):
            RecurrenceGroupBindingV3.from_dict(binding_raw)

    for case in ("source_expression", "offset_expression", "evaluator"):
        program_raw = copy.deepcopy(default_binding.program.to_dict())
        if case == "source_expression":
            program_raw["source_expressions"][0]["kind"] = (
                "forcing_annihilator_multiplicity"
            )
        elif case == "offset_expression":
            program_raw["offset_expressions"][0]["index"] = 1
        else:
            program_raw["evaluator"] = "attacker-replacement"
        program_payload = {
            key: value for key, value in program_raw.items() if key != "program_digest"
        }
        program_raw["program_digest"] = recurrence_module._digest(
            program_payload,
            domain=recurrence_module._PROGRAM_DIGEST_DOMAIN,
        )
        with pytest.raises(ValueError):
            RecurrenceSemanticProgramV3.from_dict(program_raw)


class _ForeignSourceKind(StrEnum):
    OPERATOR = "operator_root_multiplicity"


class _ForeignSourceRole(StrEnum):
    CANONICAL = "canonical"


class _ForeignClaim(StrEnum):
    CLAIM1 = "claim1"


class _TupleSubclass(tuple):
    pass


class _ListSubclass(list):
    pass


def test_direct_dict_parsing_checks_exact_container_and_cardinality_before_items() -> None:
    program_raw = _binding().program.to_dict()
    program_raw["source_expressions"] = _ListSubclass(
        program_raw["source_expressions"]
    )
    with pytest.raises(ValueError, match="exact arrays"):
        RecurrenceSemanticProgramV3.from_dict(program_raw)

    program_raw = _binding().program.to_dict()
    program_raw["source_expressions"].append(object())
    with pytest.raises(ValueError, match="exactly three sources and eight offsets"):
        RecurrenceSemanticProgramV3.from_dict(program_raw)

    binding_raw = _binding().to_dict()
    binding_raw["offsets"].append(object())
    with pytest.raises(ValueError, match="exactly three sources and eight offsets"):
        RecurrenceGroupBindingV3.from_dict(binding_raw)


def test_hostile_nested_mutation_rejects_equal_coercions_and_tuple_subclasses() -> None:
    foreign_kind = _binding()
    object.__setattr__(
        foreign_kind.program.source_expressions[0],
        "kind",
        _ForeignSourceKind.OPERATOR,
    )
    with pytest.raises(ValueError, match="kind is invalid"):
        foreign_kind.to_dict()

    foreign_role = _binding()
    object.__setattr__(foreign_role.sources[0], "role", _ForeignSourceRole.CANONICAL)
    with pytest.raises(ValueError, match="role is invalid"):
        foreign_role.to_dict()

    foreign_claim = _binding()
    object.__setattr__(foreign_claim.offsets[1], "claim", _ForeignClaim.CLAIM1)
    with pytest.raises(ValueError, match="claim is invalid"):
        foreign_claim.to_dict()

    boolean_index = _binding()
    attacker_view = boolean_index.to_dict()
    object.__setattr__(boolean_index.program.offset_expressions[1], "index", True)
    attacker_view["program"]["offset_expressions"][1]["index"] = True
    attacker_view["offsets"][1]["expression"]["index"] = True
    attacker_program_payload = {
        key: value
        for key, value in attacker_view["program"].items()
        if key != "program_digest"
    }
    attacker_program_digest = recurrence_module._digest(
        attacker_program_payload,
        domain=recurrence_module._PROGRAM_DIGEST_DOMAIN,
    )
    attacker_view["program"]["program_digest"] = attacker_program_digest
    object.__setattr__(
        boolean_index.program,
        "program_digest",
        attacker_program_digest,
    )
    attacker_binding_payload = {
        key: value for key, value in attacker_view.items() if key != "content_digest"
    }
    object.__setattr__(
        boolean_index,
        "content_digest",
        recurrence_module._digest(
            attacker_binding_payload,
            domain=recurrence_module._BINDING_DIGEST_DOMAIN,
        ),
    )
    with pytest.raises(ValueError, match="exact integer"):
        boolean_index.to_dict()

    for owner_name, attribute in (
        ("program", "source_expressions"),
        ("program", "offset_expressions"),
        ("binding", "sources"),
        ("binding", "offsets"),
    ):
        tuple_subclass = _binding()
        owner = tuple_subclass.program if owner_name == "program" else tuple_subclass
        object.__setattr__(owner, attribute, _TupleSubclass(getattr(owner, attribute)))
        with pytest.raises(ValueError, match="exact"):
            tuple_subclass.to_dict()

    assert _binding().sources[0].role is RecurrenceSourceRoleV3.CANONICAL


def test_binding_transplant_wrong_seed_and_wrong_preregistration_fail_closed(
    default_binding: RecurrenceGroupBindingV3,
    default_plan: SourceRoleClaimBankV3,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other_binding = _binding(r=3)
    with pytest.raises(ValueError, match="not bound to the recurrence execution"):
        verify_recurrence_source_role_claim_bank_v3(
            binding=other_binding,
            claim_bank=default_plan,
            schedule_seed=_seed("default"),
            expected_binding_digest=other_binding.content_digest,
        )
    with pytest.raises(ValueError, match="commitment"):
        verify_recurrence_source_role_claim_bank_v3(
            binding=default_binding,
            claim_bank=default_plan,
            schedule_seed=_seed("wrong"),
            expected_binding_digest=default_binding.content_digest,
        )

    source_bank, targets, offsets = default_binding.source_role_inputs()
    forged_transplant = build_raw_source_role_claim_bank_v3(
        source_bank=source_bank,
        group_binding_digest=other_binding.content_digest,
        targets=targets,
        offsets=offsets,
        schedule_seed=_seed("default"),
    )
    with pytest.raises(ValueError, match="preregistered input digest"):
        verify_recurrence_source_role_claim_bank_v3(
            binding=other_binding,
            claim_bank=forged_transplant,
            schedule_seed=_seed("default"),
            expected_binding_digest=other_binding.content_digest,
        )

    def forbidden_scheduler(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("low-level schedule selection ran before binding validation")

    monkeypatch.setattr(
        recurrence_module,
        "_build_source_role_claim_bank_v3",
        forbidden_scheduler,
    )
    with pytest.raises(ValueError, match="preregistered digest"):
        build_recurrence_source_role_claim_bank_v3(
            binding=default_binding,
            schedule_seed=_seed("default"),
            expected_binding_digest=other_binding.content_digest,
        )
    with pytest.raises(ValueError, match="preregistered digest"):
        verify_recurrence_source_role_claim_bank_v3(
            binding=default_binding,
            claim_bank=object(),  # type: ignore[arg-type]
            schedule_seed=object(),  # type: ignore[arg-type]
            expected_binding_digest=other_binding.content_digest,
        )


def test_every_legitimate_input_changes_the_binding_digest() -> None:
    bindings = (
        _binding(),
        _binding(r=3),
        _binding(c0=2),
        _binding(c1=4),
    )
    assert len({binding.content_digest for binding in bindings}) == len(bindings)
    assert len({binding.program.program_digest for binding in bindings}) == len(bindings)


def test_duplicate_zero_recurrence_offsets_are_legitimate_and_seed_bounds_delegate() -> None:
    binding = _binding(c0=0, c1=0)
    assert tuple(receipt.value for receipt in binding.offsets)[:2] == (
        Fraction(0),
        Fraction(0),
    )
    plan = _plan(binding, seed=_seed("zero-offsets"))
    assert tuple(claim.offset for claim in plan.claims)[:2] == (Fraction(0), Fraction(0))

    for invalid_seed in (b"x" * 31, b"x" * 65):
        with pytest.raises(ValueError, match="32 to 64 bytes"):
            build_recurrence_source_role_claim_bank_v3(
                binding=binding,
                schedule_seed=invalid_seed,
                expected_binding_digest=binding.content_digest,
            )


def test_representative_boundary_sweep_stays_exact_and_bounded() -> None:
    for r in (2, 7):
        for c0 in (-9, 0, 9):
            for c1 in (-9, 0, 9):
                binding = _binding(r=r, c0=c0, c1=c1)
                expected = _independent_closed_form(binding.program.instance)
                observed = tuple(receipt.value for receipt in binding.offsets)
                assert observed == expected
                assert all(value.denominator == 1 for value in observed)
                assert max(abs(value) for value in observed) <= 52_236_156
                assert binding.to_dict()["schema"] == RECURRENCE_BINDING_SCHEMA_V3
                assert binding.program.to_dict()["schema"] == RECURRENCE_PROGRAM_SCHEMA_V3
