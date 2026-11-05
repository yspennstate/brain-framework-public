from __future__ import annotations

import copy
import hashlib
import inspect
import json
from fractions import Fraction

import pytest

import math_brain
import math_brain.recurrence_binding_v3 as binding_module
import math_brain.recurrence_manifest_v3 as manifest_module
from math_brain.recurrence_binding_v3 import (
    RecurrenceGroupBindingV3,
    RecurrenceInstanceV3,
    build_recurrence_group_binding_v3,
)
from math_brain.recurrence_manifest_v3 import (
    MAX_RECURRENCE_BINDING_BANK_JSON_BYTES_V3,
    RecurrenceBindingBankEntryV3,
    RecurrenceBindingBankManifestV3,
    build_preregistered_recurrence_source_role_claim_bank_v3,
    build_recurrence_binding_bank_manifest_v3,
    verify_preregistered_recurrence_source_role_claim_bank_v3,
)
from math_brain.source_role_v3 import SourceRoleClaimBankV3


def _binding(*, r: int = 2, c0: int = 1, c1: int = 3) -> RecurrenceGroupBindingV3:
    return build_recurrence_group_binding_v3(RecurrenceInstanceV3(r=r, c0=c0, c1=c1))


def _manifest(*bindings: RecurrenceGroupBindingV3) -> RecurrenceBindingBankManifestV3:
    return build_recurrence_binding_bank_manifest_v3(bindings)


def _seed(label: str) -> bytes:
    return hashlib.sha256(label.encode("ascii")).digest()


def _entry_index(
    manifest: RecurrenceBindingBankManifestV3,
    binding: RecurrenceGroupBindingV3,
) -> int:
    return next(
        entry.bank_index
        for entry in manifest.entries
        if entry.binding_digest == binding.content_digest
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
def default_manifest() -> RecurrenceBindingBankManifestV3:
    return _manifest(_binding(), _binding(r=3))


@pytest.fixture(scope="module")
def default_plan(
    default_manifest: RecurrenceBindingBankManifestV3,
) -> SourceRoleClaimBankV3:
    return build_preregistered_recurrence_source_role_claim_bank_v3(
        manifest=default_manifest,
        bank_index=0,
        schedule_seed=_seed("default"),
        expected_manifest_digest=default_manifest.content_digest,
    )


def test_manifest_canonicalizes_binding_order_and_round_trips_exactly(
    default_manifest: RecurrenceBindingBankManifestV3,
) -> None:
    first = _binding(r=2)
    second = _binding(r=3)
    reversed_input = _manifest(second, first)
    forward_input = _manifest(first, second)

    assert reversed_input == forward_input == default_manifest
    assert tuple(entry.bank_index for entry in default_manifest.entries) == (0, 1)
    assert tuple(entry.binding_digest for entry in default_manifest.entries) == tuple(
        sorted(entry.binding_digest for entry in default_manifest.entries)
    )
    assert RecurrenceBindingBankManifestV3.from_dict(
        default_manifest.to_dict()
    ) == default_manifest
    assert RecurrenceBindingBankManifestV3.from_json(
        default_manifest.canonical_json()
    ) == default_manifest
    assert default_manifest.content_digest == (
        "sha256:b1a171fc63f9606393e2f3184e4fa76887b320efeeb925f42a8a09a167c0ec13"
    )
    assert len(default_manifest.canonical_json().encode("utf-8")) < (
        MAX_RECURRENCE_BINDING_BANK_JSON_BYTES_V3
    )


def test_manifest_and_schedule_api_expose_no_raw_semantic_or_answer_axes(
    default_manifest: RecurrenceBindingBankManifestV3,
) -> None:
    assert tuple(inspect.signature(build_recurrence_binding_bank_manifest_v3).parameters) == (
        "bindings",
    )
    assert tuple(
        inspect.signature(
            build_preregistered_recurrence_source_role_claim_bank_v3
        ).parameters
    ) == ("manifest", "bank_index", "schedule_seed", "expected_manifest_digest")
    assert not hasattr(manifest_module, "build_source_role_claim_bank_v3")
    assert not hasattr(manifest_module, "source_role_input_digest_v3")
    assert math_brain.RecurrenceBindingBankManifestV3 is RecurrenceBindingBankManifestV3
    assert (
        math_brain.build_recurrence_binding_bank_manifest_v3
        is build_recurrence_binding_bank_manifest_v3
    )

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
        "surface",
        "tag",
        "template",
        "trace",
        "witness",
    }
    serialized = tuple(item.lower() for item in _all_text(default_manifest.to_dict()))
    assert all(fragment not in item for fragment in forbidden for item in serialized)

    with pytest.raises(TypeError):
        build_recurrence_binding_bank_manifest_v3(  # type: ignore[call-arg]
            (_binding(),),
            schedule_seed=_seed("forbidden"),
        )


def test_manifest_rejects_empty_duplicate_and_noncanonical_binding_inputs() -> None:
    binding = _binding()
    with pytest.raises(ValueError, match="must not be empty"):
        _manifest()
    with pytest.raises(ValueError, match="instances must be unique"):
        _manifest(binding, binding)
    with pytest.raises(ValueError, match="noncanonical binding"):
        build_recurrence_binding_bank_manifest_v3((binding, object()))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="iterable"):
        build_recurrence_binding_bank_manifest_v3(1)  # type: ignore[arg-type]


def test_iterable_and_direct_dict_caps_fire_before_unbounded_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _binding()
    monkeypatch.setattr(manifest_module, "MAX_RECURRENCE_BINDING_BANK_ENTRIES_V3", 2)
    with pytest.raises(ValueError, match="exceeds its entry cap"):
        build_recurrence_binding_bank_manifest_v3(binding for _ in range(3))

    raw = {
        "content_digest": binding.content_digest,
        "entries": [object(), object(), object()],
        "schema": manifest_module.RECURRENCE_BINDING_BANK_SCHEMA_V3,
        "schema_version": 1,
    }
    with pytest.raises(ValueError, match="entry count is outside its cap"):
        RecurrenceBindingBankManifestV3.from_dict(raw)


def test_reordering_or_reindexing_entries_fails_even_after_outer_rehash(
    default_manifest: RecurrenceBindingBankManifestV3,
) -> None:
    reordered = copy.deepcopy(default_manifest.to_dict())
    reordered["entries"].reverse()
    for index, entry in enumerate(reordered["entries"]):
        entry["bank_index"] = index
    payload = {key: value for key, value in reordered.items() if key != "content_digest"}
    reordered["content_digest"] = manifest_module._digest(
        payload,
        domain=manifest_module._MANIFEST_DIGEST_DOMAIN,
    )
    with pytest.raises(ValueError, match="ordered by binding digest"):
        RecurrenceBindingBankManifestV3.from_dict(reordered)

    reindexed = copy.deepcopy(default_manifest.to_dict())
    reindexed["entries"][0]["bank_index"] = 1
    reindexed["entries"][1]["bank_index"] = 0
    payload = {key: value for key, value in reindexed.items() if key != "content_digest"}
    reindexed["content_digest"] = manifest_module._digest(
        payload,
        domain=manifest_module._MANIFEST_DIGEST_DOMAIN,
    )
    with pytest.raises(ValueError, match="indices must be contiguous"):
        RecurrenceBindingBankManifestV3.from_dict(reindexed)


def test_nested_binding_tamper_is_reexecuted_before_outer_digest_trust(
    default_manifest: RecurrenceBindingBankManifestV3,
) -> None:
    raw = copy.deepcopy(default_manifest.to_dict())
    raw_binding = raw["entries"][0]["binding"]
    raw_binding["offsets"][0]["value"] = {"denominator": 1, "numerator": 999}
    binding_payload = {
        key: value for key, value in raw_binding.items() if key != "content_digest"
    }
    raw_binding["content_digest"] = binding_module._digest(
        binding_payload,
        domain=binding_module._BINDING_DIGEST_DOMAIN,
    )
    raw["entries"][0]["binding_digest"] = raw_binding["content_digest"]
    manifest_payload = {key: value for key, value in raw.items() if key != "content_digest"}
    raw["content_digest"] = manifest_module._digest(
        manifest_payload,
        domain=manifest_module._MANIFEST_DIGEST_DOMAIN,
    )
    with pytest.raises(ValueError, match="differ from exact execution"):
        RecurrenceBindingBankManifestV3.from_dict(raw)


class _TupleSubclass(tuple):
    pass


class _ListSubclass(list):
    pass


class _ManifestSubclass(RecurrenceBindingBankManifestV3):
    def _validate_entries(self) -> None:
        return None


def test_exact_type_and_frozen_nested_mutation_boundaries_fail_closed(
    default_manifest: RecurrenceBindingBankManifestV3,
) -> None:
    with pytest.raises(ValueError, match="exact integer"):
        RecurrenceBindingBankEntryV3(bank_index=True, binding=_binding())
    with pytest.raises(ValueError, match="subclasses are forbidden"):
        _ManifestSubclass(entries=())

    list_raw = default_manifest.to_dict()
    list_raw["entries"] = _ListSubclass(list_raw["entries"])
    with pytest.raises(ValueError, match="exact array"):
        RecurrenceBindingBankManifestV3.from_dict(list_raw)

    tuple_manifest = _manifest(_binding(), _binding(r=3))
    object.__setattr__(tuple_manifest, "entries", _TupleSubclass(tuple_manifest.entries))
    with pytest.raises(ValueError, match="exact tuple"):
        tuple_manifest.to_dict()

    index_manifest = _manifest(_binding(), _binding(r=3))
    object.__setattr__(index_manifest.entries[0], "bank_index", True)
    with pytest.raises(ValueError, match="exact integer"):
        index_manifest.to_dict()

    binding_manifest = _manifest(_binding(), _binding(r=3))
    object.__setattr__(
        binding_manifest.entries[0].binding.program.instance,
        "r",
        7,
    )
    with pytest.raises(ValueError):
        binding_manifest.to_dict()
    with pytest.raises(ValueError):
        build_preregistered_recurrence_source_role_claim_bank_v3(
            manifest=binding_manifest,
            bank_index=0,
            schedule_seed=_seed("default"),
            expected_manifest_digest=binding_manifest.content_digest,
        )


def test_expected_manifest_digest_fails_before_index_seed_or_scheduler_access(
    default_manifest: RecurrenceBindingBankManifestV3,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = _manifest(_binding(r=4))

    def forbidden_scheduler(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("schedule selection ran before external manifest validation")

    monkeypatch.setattr(
        manifest_module,
        "_build_source_role_claim_bank_v3",
        forbidden_scheduler,
    )
    with pytest.raises(ValueError, match="external commitment"):
        build_preregistered_recurrence_source_role_claim_bank_v3(
            manifest=default_manifest,
            bank_index=object(),  # type: ignore[arg-type]
            schedule_seed=object(),  # type: ignore[arg-type]
            expected_manifest_digest=other.content_digest,
        )
    with pytest.raises(ValueError, match="external commitment"):
        verify_preregistered_recurrence_source_role_claim_bank_v3(
            manifest=default_manifest,
            bank_index=object(),  # type: ignore[arg-type]
            claim_bank=object(),  # type: ignore[arg-type]
            schedule_seed=object(),  # type: ignore[arg-type]
            expected_manifest_digest=other.content_digest,
        )

    corrupted = _manifest(_binding(), _binding(r=3))
    object.__setattr__(corrupted.entries[0], "bank_index", True)
    with pytest.raises(ValueError, match="canonical SHA-256"):
        corrupted.validate_expected_digest("not-a-digest")


def test_contextual_schedule_replays_exact_binding_semantics(
    default_manifest: RecurrenceBindingBankManifestV3,
    default_plan: SourceRoleClaimBankV3,
) -> None:
    entry = default_manifest.entries[0]
    source_bank, targets, offsets = entry.binding.source_role_inputs()

    assert default_plan.group_binding_digest == entry.context_digest(
        default_manifest.content_digest
    )
    assert default_plan.group_binding_digest != entry.binding_digest
    assert tuple(claim.target for claim in default_plan.claims) == targets
    for claim, offset in zip(default_plan.claims, offsets, strict=True):
        assert claim.offset == offset
        weighted = sum(
            (
                Fraction(weight) * source
                for weight, source in zip(
                    claim.weights,
                    source_bank.source_values,
                    strict=True,
                )
            ),
            Fraction(0),
        )
        assert claim.target_value - weighted == offset

    verify_preregistered_recurrence_source_role_claim_bank_v3(
        manifest=default_manifest,
        bank_index=0,
        claim_bank=default_plan,
        schedule_seed=_seed("default"),
        expected_manifest_digest=default_manifest.content_digest,
    )


def test_schedule_seed_changes_bank_but_never_manifest_bytes(
    default_manifest: RecurrenceBindingBankManifestV3,
    default_plan: SourceRoleClaimBankV3,
) -> None:
    before = default_manifest.canonical_json()
    other = build_preregistered_recurrence_source_role_claim_bank_v3(
        manifest=default_manifest,
        bank_index=0,
        schedule_seed=_seed("other"),
        expected_manifest_digest=default_manifest.content_digest,
    )
    assert default_manifest.canonical_json() == before
    assert other.schedule_seed_commitment != default_plan.schedule_seed_commitment
    assert tuple(claim.offset for claim in other.claims) == tuple(
        claim.offset for claim in default_plan.claims
    )


def test_context_digest_rejects_cross_manifest_and_cross_entry_transplant() -> None:
    shared = _binding(r=2)
    manifest_a = _manifest(shared, _binding(r=3))
    manifest_b = _manifest(shared, _binding(r=4))
    index_a = _entry_index(manifest_a, shared)
    index_b = _entry_index(manifest_b, shared)
    plan_a = build_preregistered_recurrence_source_role_claim_bank_v3(
        manifest=manifest_a,
        bank_index=index_a,
        schedule_seed=_seed("shared"),
        expected_manifest_digest=manifest_a.content_digest,
    )
    plan_b = build_preregistered_recurrence_source_role_claim_bank_v3(
        manifest=manifest_b,
        bank_index=index_b,
        schedule_seed=_seed("shared"),
        expected_manifest_digest=manifest_b.content_digest,
    )
    assert plan_a.group_binding_digest != plan_b.group_binding_digest
    with pytest.raises(ValueError, match="not bound to this manifest entry"):
        verify_preregistered_recurrence_source_role_claim_bank_v3(
            manifest=manifest_b,
            bank_index=index_b,
            claim_bank=plan_a,
            schedule_seed=_seed("shared"),
            expected_manifest_digest=manifest_b.content_digest,
        )

    other_index = 1 - index_a
    with pytest.raises(ValueError, match="not bound to this manifest entry"):
        verify_preregistered_recurrence_source_role_claim_bank_v3(
            manifest=manifest_a,
            bank_index=other_index,
            claim_bank=plan_a,
            schedule_seed=_seed("shared"),
            expected_manifest_digest=manifest_a.content_digest,
        )


def test_wrong_schedule_seed_and_valid_manifest_substitution_are_distinguished(
    default_manifest: RecurrenceBindingBankManifestV3,
    default_plan: SourceRoleClaimBankV3,
) -> None:
    with pytest.raises(ValueError, match="commitment"):
        verify_preregistered_recurrence_source_role_claim_bank_v3(
            manifest=default_manifest,
            bank_index=0,
            claim_bank=default_plan,
            schedule_seed=_seed("wrong"),
            expected_manifest_digest=default_manifest.content_digest,
        )

    substituted = _manifest(_binding(r=2), _binding(r=4))
    RecurrenceBindingBankManifestV3.from_json(substituted.canonical_json())
    with pytest.raises(ValueError, match="external commitment"):
        substituted.validate_expected_digest(default_manifest.content_digest)


def test_json_boundary_rejects_duplicates_noncanonical_text_limits_and_constants(
    default_manifest: RecurrenceBindingBankManifestV3,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="duplicate JSON key"):
        RecurrenceBindingBankManifestV3.from_json('{"schema":"a","schema":"b"}')
    with pytest.raises(ValueError, match="canonical encoding"):
        RecurrenceBindingBankManifestV3.from_json(
            json.dumps(default_manifest.to_dict(), indent=2, sort_keys=True)
        )
    with pytest.raises(ValueError, match="byte budget"):
        RecurrenceBindingBankManifestV3.from_json(
            " " * (MAX_RECURRENCE_BINDING_BANK_JSON_BYTES_V3 + 1)
        )
    with pytest.raises(ValueError, match="valid UTF-8"):
        RecurrenceBindingBankManifestV3.from_json("\ud800")
    with pytest.raises(ValueError, match="non-finite JSON number"):
        RecurrenceBindingBankManifestV3.from_json('{"value":NaN}')

    raw = default_manifest.canonical_json()

    def recursive_encoder(_value: object) -> str:
        raise RecursionError("synthetic canonical encoder recursion")

    monkeypatch.setattr(manifest_module, "canonical_json", recursive_encoder)
    with pytest.raises(ValueError, match="cannot be canonically re-encoded"):
        RecurrenceBindingBankManifestV3.from_json(raw)


def test_serialized_schema_and_digest_boundaries_require_exact_types(
    default_manifest: RecurrenceBindingBankManifestV3,
) -> None:
    raw = default_manifest.to_dict()
    raw["schema_version"] = True
    with pytest.raises(ValueError, match="exact integer"):
        RecurrenceBindingBankManifestV3.from_dict(raw)

    raw = default_manifest.to_dict()
    raw["schema"] = True
    with pytest.raises(ValueError, match="schema is unsupported"):
        RecurrenceBindingBankManifestV3.from_dict(raw)

    raw = default_manifest.to_dict()
    raw["content_digest"] = default_manifest.content_digest.upper()
    with pytest.raises(ValueError, match="canonical SHA-256"):
        RecurrenceBindingBankManifestV3.from_dict(raw)
