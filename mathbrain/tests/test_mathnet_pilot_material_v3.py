from __future__ import annotations

import hashlib
import hmac
import inspect
import itertools
import json
import pickle
from collections.abc import Iterator

import pytest

import math_brain.mathnet_bank_v3 as bank_module
import math_brain.mathnet_pilot_material_v3 as material_module
from math_brain.exact_answer_v3 import ExactRationalV3
from math_brain.mathnet_bank_v3 import MATHNET_HF_ROW_COUNT_V3
from math_brain.mathnet_pilot_material_v3 import (
    GoldFreeMathNetPilotUniverseV3,
    MathNetPilotGoldVaultV3,
    MathNetPilotLanguageStateV3,
    MathNetPilotMaterialCodeV3,
    MathNetPilotMaterialV3,
    build_synthetic_mathnet_pilot_material_v3,
    verify_synthetic_mathnet_pilot_material_v3,
)
from math_brain.models import canonical_json

VAULT_KEY = bytes.fromhex("42" * 32)
WRONG_VAULT_KEY = bytes.fromhex("24" * 32)

PROMPT_SENTINEL = "PROMPT-SENTINEL: compute the reduced fraction seven ninths."
NULL_PROMPT_SENTINEL = "NULL-PROMPT-SENTINEL: compute five."
REJECTED_PROMPT_SENTINEL = "REJECTED-PROMPT-SENTINEL: prove one equals one."
SOURCE_ID_SENTINEL = "SOURCE-ID-SENTINEL"
SOLUTION_SENTINEL = r"SOLUTION-SENTINEL therefore \boxed{7/9}."


def _row(
    index: int,
    *,
    language: str | None = "French",
    problem: str | None = None,
    solutions: list[str] | None = None,
    problem_type: str | None = "final answer only",
    final_answer: str | None = None,
    source_id: str | None = None,
) -> dict[str, object]:
    return {
        "id": source_id if source_id is not None else f"pilot-source-{index:05d}",
        "problem_markdown": problem if problem is not None else f"Calculez {index}.",
        "solutions_markdown": solutions if solutions is not None else [],
        "images": [],
        "country": f"Country-{index}",
        "competition": f"Competition-{index}",
        "topics_flat": [],
        "language": language,
        "problem_type": problem_type,
        "final_answer": final_answer,
    }


def _full_rows(*, changed_gold: bool = False) -> Iterator[dict[str, object]]:
    if changed_gold:
        yield _row(
            0,
            language="English",
            problem=PROMPT_SENTINEL,
            solutions=[
                r"SOLUTION-CHANGED-A therefore \boxed{11/13}.",
                r"SOLUTION-CHANGED-B also gives \boxed{11/13}.",
            ],
            final_answer="11/13",
            source_id=SOURCE_ID_SENTINEL,
        )
    else:
        yield _row(
            0,
            language="English",
            problem=PROMPT_SENTINEL,
            solutions=[SOLUTION_SENTINEL],
            final_answer="7/9",
            source_id=SOURCE_ID_SENTINEL,
        )
    yield _row(
        1,
        language=None,
        problem=NULL_PROMPT_SENTINEL,
        solutions=[r"NULL-SOLUTION-SENTINEL hence \boxed{5}."],
        final_answer="5",
    )
    yield _row(
        2,
        language=None,
        problem=REJECTED_PROMPT_SENTINEL,
        problem_type="proof",
    )
    for index in range(3, MATHNET_HF_ROW_COUNT_V3):
        yield _row(index)


@pytest.fixture(scope="module")
def material_pair() -> tuple[MathNetPilotMaterialV3, MathNetPilotMaterialV3]:
    return (
        build_synthetic_mathnet_pilot_material_v3(
            _full_rows(),
            vault_key=VAULT_KEY,
        ),
        build_synthetic_mathnet_pilot_material_v3(
            _full_rows(changed_gold=True),
            vault_key=VAULT_KEY,
        ),
    )


def _walk_public(value: object) -> Iterator[tuple[str | None, object]]:
    if type(value) is dict:
        for key, nested in value.items():
            assert type(key) is str
            yield key, nested
            yield from _walk_public(nested)
        return
    if type(value) is list:
        for nested in value:
            yield None, nested
            yield from _walk_public(nested)
        return
    assert type(value) in (str, int, bool, type(None))


def test_full_source_material_is_complete_and_gold_separated(material_pair) -> None:
    material, _changed = material_pair
    material.validate_current()
    material.vault.verify_key(VAULT_KEY)

    expected_keys = tuple(code.value for code in MathNetPilotMaterialCodeV3)
    assert tuple(key for key, _count in material.universe.screen_counts) == expected_keys
    counts = dict(material.universe.screen_counts)
    assert counts == {
        MathNetPilotMaterialCodeV3.SOURCE_ENGLISH_ELIGIBLE.value: 1,
        MathNetPilotMaterialCodeV3.NULL_LANGUAGE_AUDIT_QUEUE.value: 1,
        MathNetPilotMaterialCodeV3.NON_ENGLISH.value: MATHNET_HF_ROW_COUNT_V3 - 3,
        MathNetPilotMaterialCodeV3.MISSING_SOURCE_METADATA.value: 0,
        MathNetPilotMaterialCodeV3.NON_TEXT_ONLY.value: 0,
        MathNetPilotMaterialCodeV3.UNSAFE_PROMPT_MARKUP.value: 0,
        MathNetPilotMaterialCodeV3.UNSUPPORTED_PROBLEM_TYPE.value: 1,
        MathNetPilotMaterialCodeV3.MISSING_ANSWER.value: 0,
        MathNetPilotMaterialCodeV3.MISSING_SOLUTION_EVIDENCE.value: 0,
        MathNetPilotMaterialCodeV3.UNSUPPORTED_ANSWER.value: 0,
        MathNetPilotMaterialCodeV3.MISSING_SOLUTION_CONFIRMATION.value: 0,
        MathNetPilotMaterialCodeV3.SOLUTION_DISAGREEMENT.value: 0,
        MathNetPilotMaterialCodeV3.TEXT_BUDGET_EXCEEDED.value: 0,
    }
    assert sum(counts.values()) == MATHNET_HF_ROW_COUNT_V3
    assert len(material.universe.rows) == len(material.vault.entries) == 2

    universe_coordinates = tuple(
        row.selection_coordinate_digest for row in material.universe.rows
    )
    vault_coordinates = tuple(
        entry.selection_coordinate_digest for entry in material.vault.entries
    )
    assert universe_coordinates == tuple(sorted(universe_coordinates))
    assert universe_coordinates == vault_coordinates
    assert len(set(universe_coordinates)) == 2
    assert len({row.case_id for row in material.universe.rows}) == 2
    assert all(
        row.case_id == "mathnet-pilot:" + row.selection_coordinate_digest[7:]
        for row in material.universe.rows
    )

    by_state = {row.raw_language_state: row for row in material.universe.rows}
    assert set(by_state) == {
        MathNetPilotLanguageStateV3.SOURCE_ENGLISH,
        MathNetPilotLanguageStateV3.NULL_AUDIT_REQUIRED,
    }
    null_row = by_state[MathNetPilotLanguageStateV3.NULL_AUDIT_REQUIRED]
    assert null_row.problem_text == NULL_PROMPT_SENTINEL
    for row in by_state.values():
        with pytest.raises(RuntimeError, match="replay-final"):
            row.language_review_view()
    assert set(null_row.selection_record()) == {
        "blind_gold_audit_required",
        "case_id",
        "event_family_digest",
        "problem_text_digest",
        "raw_language_state",
        "rights_state",
        "selection_coordinate_digest",
    }

    public = material.public_commitment_payload()
    assert set(public) == {
        "content_digest",
        "engineering_pilot_only",
        "lookup_blinding_attested",
        "runtime_isolation_manifest_required",
        "schema",
        "schema_version",
        "selector_handoff_authorized",
        "universe",
        "vault",
    }
    assert set(public["universe"]) == {
        "candidate_count",
        "coordinate_universe_digest",
        "engineering_pilot_only",
        "gold_free_ordered_rows_digest",
        "membership_annotation_scoreability_gated",
        "membership_chronology_attested",
        "private_gold_present",
        "public_content_digest",
        "public_ordered_source_rows_digest",
        "schema",
        "schema_version",
        "screen_counts",
        "source_binding",
        "universe_content_digest",
    }
    assert set(public["vault"]) == {
        "blind_gold_audit_required",
        "bound_universe_content_digest",
        "chronology_attested",
        "content_digest",
        "entry_count",
        "hmac_holder_identity_attested",
        "private_gold_audit_authority",
        "public_hmac_commitment",
        "source_binding_digest",
        "vault_key_commitment",
    }
    assert public["engineering_pilot_only"] is True
    assert public["lookup_blinding_attested"] is False
    assert public["runtime_isolation_manifest_required"] is True
    assert public["selector_handoff_authorized"] is False
    assert public["vault"]["blind_gold_audit_required"] is True
    assert public["vault"]["private_gold_audit_authority"] is False
    assert public["vault"]["hmac_holder_identity_attested"] is False
    assert public["vault"]["chronology_attested"] is False
    assert public["universe"]["membership_annotation_scoreability_gated"] is True
    assert public["universe"]["membership_chronology_attested"] is False
    assert public["vault"]["bound_universe_content_digest"] == (
        material.universe.content_digest
    )


def test_public_commitments_are_recursively_gold_free(material_pair) -> None:
    material, _changed = material_pair
    public = material.public_commitment_payload()
    walked = tuple(_walk_public(public))
    forbidden_keys = {
        "answer",
        "confirming_solution_indices",
        "country",
        "entries",
        "final_answer",
        "final_answer_text_digest",
        "gold",
        "ordered_entry_digests",
        "private_content_digest",
        "private_ordered_source_rows_digest",
        "problem_markdown",
        "problem_text",
        "solution_text_digests",
        "source_id",
        "source_ordinal",
        "source_row_commitment",
        "topics_flat",
        "vault_key",
    }
    assert forbidden_keys.isdisjoint(key for key, _value in walked if key is not None)

    serialized = canonical_json(public)
    forbidden_text = {
        PROMPT_SENTINEL,
        NULL_PROMPT_SENTINEL,
        REJECTED_PROMPT_SENTINEL,
        SOURCE_ID_SENTINEL,
        SOLUTION_SENTINEL,
        "7/9",
        material.private_content_digest,
        material.vault.private_content_digest,
        material.vault.private_ordered_source_rows_digest,
        *(entry.content_digest for entry in material.vault.entries),
        *(entry.source_row_commitment for entry in material.vault.entries),
        *(entry.gold.content_digest for entry in material.vault.entries),
        *(
            digest
            for entry in material.vault.entries
            for digest in entry.gold.solution_text_digests
        ),
    }
    assert all(value not in serialized for value in forbidden_text)
    rendered = "\n".join((repr(material), repr(material.universe), repr(material.vault)))
    assert all(
        value not in rendered
        for value in (
            PROMPT_SENTINEL,
            NULL_PROMPT_SENTINEL,
            SOURCE_ID_SENTINEL,
            SOLUTION_SENTINEL,
        )
    )


def test_gold_mutation_preserves_universe_but_changes_keyed_vault(material_pair) -> None:
    base, changed = material_pair
    assert base.universe == changed.universe
    assert (
        base.universe.public_commitment_payload()
        == changed.universe.public_commitment_payload()
    )
    assert tuple(row.case_id for row in base.universe.rows) == tuple(
        row.case_id for row in changed.universe.rows
    )
    assert tuple(
        entry.selection_coordinate_digest for entry in base.vault.entries
    ) == tuple(entry.selection_coordinate_digest for entry in changed.vault.entries)
    assert sum(
        left.content_digest != right.content_digest
        for left, right in zip(base.vault.entries, changed.vault.entries, strict=True)
    ) == 1
    assert base.vault.vault_key_commitment == changed.vault.vault_key_commitment
    assert (
        base.vault.private_ordered_source_rows_digest
        != changed.vault.private_ordered_source_rows_digest
    )
    assert base.vault.private_content_digest != changed.vault.private_content_digest
    assert base.vault.public_hmac_commitment != changed.vault.public_hmac_commitment
    assert base.vault.content_digest != changed.vault.content_digest
    assert base.private_content_digest != changed.private_content_digest
    assert base.content_digest != changed.content_digest
    assert base.public_commitment_payload() != changed.public_commitment_payload()

    expected_key_commitment = "sha256:" + hashlib.sha256(
        material_module._VAULT_KEY_DOMAIN + VAULT_KEY
    ).hexdigest()
    expected_hmac = "hmac-sha256:" + hmac.new(
        VAULT_KEY,
        material_module._VAULT_HMAC_DOMAIN
        + base.vault.private_content_digest.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    assert base.vault.vault_key_commitment == expected_key_commitment
    assert base.vault.public_hmac_commitment == expected_hmac
    assert base.content_digest == material_module._digest(
        base._public_payload(),
        domain=material_module._MATERIAL_PUBLIC_DOMAIN,
    )


def test_nested_tampering_is_detected(material_pair) -> None:
    material, _changed = material_pair
    row = material.universe.rows[0]
    original_problem = row.problem_text
    try:
        object.__setattr__(row, "problem_text", "tampered problem")
        with pytest.raises(ValueError, match="problem-text digest is stale"):
            material.validate_current()
    finally:
        object.__setattr__(row, "problem_text", original_problem)
    material.validate_current()

    original_counts = material.universe.screen_counts
    changed_counts = dict(original_counts)
    changed_counts[MathNetPilotMaterialCodeV3.SOURCE_ENGLISH_ELIGIBLE.value] += 1
    changed_counts[MathNetPilotMaterialCodeV3.NULL_LANGUAGE_AUDIT_QUEUE.value] -= 1
    try:
        object.__setattr__(
            material.universe,
            "screen_counts",
            tuple(
                (code.value, changed_counts[code.value])
                for code in MathNetPilotMaterialCodeV3
            ),
        )
        with pytest.raises(ValueError, match="language states differ"):
            material.validate_current()
    finally:
        object.__setattr__(material.universe, "screen_counts", original_counts)
    material.validate_current()

    entry = material.vault.entries[0]
    original_gold = entry.gold
    try:
        object.__setattr__(
            entry,
            "gold",
            material_module.MathNetPrivateGoldV3(
                answer=ExactRationalV3(999, 1),
                final_answer_text_digest=original_gold.final_answer_text_digest,
                solution_text_digests=original_gold.solution_text_digests,
                confirming_solution_indices=original_gold.confirming_solution_indices,
                confirmation_rule=original_gold.confirmation_rule,
            ),
        )
        with pytest.raises(ValueError, match="vault-entry digest is stale"):
            material.validate_current()
    finally:
        object.__setattr__(entry, "gold", original_gold)
    material.validate_current()


def test_builder_authority_key_and_transport_guards_are_fail_closed(material_pair) -> None:
    material, _changed = material_pair
    signature = inspect.signature(build_synthetic_mathnet_pilot_material_v3)
    assert tuple(signature.parameters) == ("rows", "vault_key")
    assert signature.parameters["vault_key"].kind is inspect.Parameter.KEYWORD_ONLY
    for artifact_type in (
        GoldFreeMathNetPilotUniverseV3,
        MathNetPilotGoldVaultV3,
        MathNetPilotMaterialV3,
    ):
        with pytest.raises(TypeError):
            artifact_type()
    for bad_key in (b"x" * 31, b"x" * 65, bytearray(b"x" * 32)):
        with pytest.raises(ValueError, match="32 to 64 immutable bytes"):
            build_synthetic_mathnet_pilot_material_v3((), vault_key=bad_key)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="row count"):
        build_synthetic_mathnet_pilot_material_v3((), vault_key=VAULT_KEY)
    duplicate = (_row(0), _row(1, source_id="pilot-source-00000"))
    with pytest.raises(ValueError, match="globally unique"):
        build_synthetic_mathnet_pilot_material_v3(duplicate, vault_key=VAULT_KEY)

    class BombRows:
        def __iter__(self) -> Iterator[object]:
            raise AssertionError("wrong-key verification consumed rows")

    with pytest.raises(ValueError, match="key differs"):
        verify_synthetic_mathnet_pilot_material_v3(
            material,
            BombRows(),
            vault_key=WRONG_VAULT_KEY,
        )
    with pytest.raises(TypeError, match="pickle boundary"):
        pickle.dumps(material.vault)
    with pytest.raises(TypeError, match="pickle boundary"):
        pickle.dumps(material)
    gold = material.vault.entries[0].gold
    with pytest.raises(ValueError, match="private-gold digests differ"):
        bank_module._MathNetScalarScreenV3(
            rejection_code=None,
            gold=gold,
            final_answer_text_digest="sha256:" + "0" * 64,
            solution_text_digests=gold.solution_text_digests,
        )


def test_tiny_pin_count_order_and_replay_guards(monkeypatch) -> None:
    monkeypatch.setattr(bank_module, "MATHNET_HF_ROW_COUNT_V3", 3)
    monkeypatch.setattr(material_module, "MATHNET_HF_ROW_COUNT_V3", 3)
    rows = tuple(
        _row(
            index,
            language="English",
            solutions=[rf"Hence \boxed{{{index + 1}}}."],
            final_answer=str(index + 1),
        )
        for index in range(3)
    )
    material = build_synthetic_mathnet_pilot_material_v3(rows, vault_key=VAULT_KEY)
    verify_synthetic_mathnet_pilot_material_v3(
        material,
        rows,
        vault_key=VAULT_KEY,
    )
    with pytest.raises(ValueError, match="row count"):
        build_synthetic_mathnet_pilot_material_v3(rows[:2], vault_key=VAULT_KEY)
    with pytest.raises(ValueError, match="beyond the pinned count"):
        build_synthetic_mathnet_pilot_material_v3(
            itertools.chain(rows, (_row(3),)),
            vault_key=VAULT_KEY,
        )
    duplicate = (rows[0], rows[0], rows[2])
    with pytest.raises(ValueError, match="globally unique"):
        build_synthetic_mathnet_pilot_material_v3(duplicate, vault_key=VAULT_KEY)
    with pytest.raises(ValueError, match="differs from the supplied source rows"):
        verify_synthetic_mathnet_pilot_material_v3(
            material,
            tuple(reversed(rows)),
            vault_key=VAULT_KEY,
        )
    reversed_material = build_synthetic_mathnet_pilot_material_v3(
        tuple(reversed(rows)),
        vault_key=VAULT_KEY,
    )
    assert reversed_material.private_content_digest != material.private_content_digest

    original_entries = material.vault.entries
    first, second, *remaining = original_entries
    swapped_entries = tuple(
        sorted(
            (
                material_module.MathNetPilotVaultEntryV3(
                    selection_coordinate_digest=first.selection_coordinate_digest,
                    source_row_commitment=second.source_row_commitment,
                    gold=second.gold,
                ),
                material_module.MathNetPilotVaultEntryV3(
                    selection_coordinate_digest=second.selection_coordinate_digest,
                    source_row_commitment=first.source_row_commitment,
                    gold=first.gold,
                ),
                *remaining,
            ),
            key=lambda entry: entry.selection_coordinate_digest,
        )
    )
    swapped_vault = MathNetPilotGoldVaultV3._derive(
        source_binding_digest=material.vault.source_binding_digest,
        bound_universe_content_digest=material.universe.content_digest,
        entries=swapped_entries,
        private_ordered_source_rows_digest=(
            material.vault.private_ordered_source_rows_digest
        ),
        vault_key=VAULT_KEY,
    )
    swapped_material = MathNetPilotMaterialV3._derive(
        universe=material.universe,
        vault=swapped_vault,
    )
    swapped_material.validate_current()
    swapped_material.vault.verify_key(VAULT_KEY)
    assert (
        swapped_material.vault.public_hmac_commitment
        != material.vault.public_hmac_commitment
    )
    with pytest.raises(ValueError, match="differs from the supplied source rows"):
        verify_synthetic_mathnet_pilot_material_v3(
            swapped_material,
            rows,
            vault_key=VAULT_KEY,
        )

    duplicate_commitment_entries = (
        original_entries[0],
        material_module.MathNetPilotVaultEntryV3(
            selection_coordinate_digest=original_entries[1].selection_coordinate_digest,
            source_row_commitment=original_entries[0].source_row_commitment,
            gold=original_entries[1].gold,
        ),
        *original_entries[2:],
    )
    with pytest.raises(ValueError, match="source-row commitments must be unique"):
        MathNetPilotGoldVaultV3._derive(
            source_binding_digest=material.vault.source_binding_digest,
            bound_universe_content_digest=material.universe.content_digest,
            entries=duplicate_commitment_entries,
            private_ordered_source_rows_digest=(
                material.vault.private_ordered_source_rows_digest
            ),
            vault_key=VAULT_KEY,
        )


def test_public_commitment_is_json_serializable(material_pair) -> None:
    material, _changed = material_pair
    public = material.public_commitment_payload()
    assert json.loads(canonical_json(public)) == public
