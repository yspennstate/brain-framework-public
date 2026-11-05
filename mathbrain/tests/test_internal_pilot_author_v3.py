from __future__ import annotations

import json
from dataclasses import replace

import pytest

from math_brain.internal_pilot_author_v3 import (
    INTERNAL_AUTHOR_CANDIDATES_PER_CELL_V3,
    INTERNAL_AUTHOR_CELL_ORDER_V3,
    INTERNAL_AUTHOR_SLOT_COUNT_V3,
    InternalAuthorSlotV3,
    build_internal_author_precommitment_v3,
    build_internal_author_slots_v3,
    internal_author_output_schema_json_v3,
    make_internal_author_public_commitment_v3,
    parse_internal_author_output_v3,
    render_internal_author_prompt_v3,
    verify_internal_author_precommitment_v3,
)
from math_brain.models import canonical_json

SEED = bytes(range(32))


def valid_output(slot: InternalAuthorSlotV3) -> dict[str, object]:
    return {
        "author_answer": {"denominator": 4, "numerator": 3},
        "candidate_id": slot.candidate_id,
        "checks": {
            "no_diagram_required": True,
            "no_external_reference": True,
            "no_solution_in_problem": True,
            "self_contained": True,
            "single_exact_scalar": True,
        },
        "problem_text": (
            "Let x be the unique real number satisfying 4x - 1 = 2. "
            "Determine the exact value of x as a reduced rational number."
        ),
        "schema": "org.math-research-brain/internal-pilot-author-output",
        "schema_version": 1,
        "verification": [
            "Adding one to both sides gives 4x = 3.",
            "Dividing by four gives the reduced rational 3/4.",
        ],
    }


def test_slot_universe_is_exactly_three_per_fixed_cell_and_replayable() -> None:
    slots = build_internal_author_slots_v3(SEED)
    assert len(slots) == INTERNAL_AUTHOR_SLOT_COUNT_V3 == 36
    assert len({slot.candidate_id for slot in slots}) == 36
    assert tuple((slot.domain, slot.difficulty, slot.candidate_ordinal) for slot in slots) == tuple(
        (domain, difficulty, ordinal)
        for domain, difficulty in INTERNAL_AUTHOR_CELL_ORDER_V3
        for ordinal in range(INTERNAL_AUTHOR_CANDIDATES_PER_CELL_V3)
    )
    assert slots == build_internal_author_slots_v3(SEED)
    assert {slot.candidate_id for slot in slots}.isdisjoint(
        {slot.candidate_id for slot in build_internal_author_slots_v3(b"z" * 32)}
    )

    precommitment = build_internal_author_precommitment_v3(SEED)
    verify_internal_author_precommitment_v3(precommitment, seed=SEED)
    with pytest.raises(ValueError, match="deterministic replay"):
        verify_internal_author_precommitment_v3(precommitment, seed=b"z" * 32)


def test_precommitment_rejects_reordering_duplicates_and_short_seeds() -> None:
    with pytest.raises(ValueError, match="256 bits"):
        build_internal_author_slots_v3(b"short")
    precommitment = build_internal_author_precommitment_v3(SEED)
    with pytest.raises(ValueError, match="fixed cell/ordinal order"):
        replace(precommitment, slots=tuple(reversed(precommitment.slots)))
    duplicate = (precommitment.slots[0], *precommitment.slots[1:-1], precommitment.slots[0])
    with pytest.raises(ValueError):
        replace(precommitment, slots=duplicate)


def test_prompt_payload_contains_only_cell_data_and_schema_is_slot_bound() -> None:
    slot = build_internal_author_slots_v3(SEED)[0]
    prompt = render_internal_author_prompt_v3(slot)
    payload = prompt.split("BEGIN_AUTHOR_CELL_JSON\n", 1)[1].split("\nEND_AUTHOR_CELL_JSON", 1)[0]
    assert set(json.loads(payload)) == {
        "candidate_id",
        "difficulty",
        "difficulty_rubric",
        "domain",
        "domain_rubric",
    }
    schema = json.loads(internal_author_output_schema_json_v3(slot))
    assert schema["additionalProperties"] is False
    assert schema["properties"]["candidate_id"]["const"] == slot.candidate_id
    assert schema["properties"]["author_answer"]["additionalProperties"] is False
    assert schema["properties"]["checks"]["additionalProperties"] is False


def test_strict_parser_accepts_exact_output_and_keeps_gold_private() -> None:
    slot = build_internal_author_slots_v3(SEED)[0]
    parsed = parse_internal_author_output_v3(canonical_json(valid_output(slot)), slot)
    assert parsed.candidate_id == slot.candidate_id
    assert parsed.author_answer.numerator == 3
    assert parsed.author_answer.denominator == 4
    assert parsed.problem_digest.startswith("sha256:")

    commitment = make_internal_author_public_commitment_v3(
        parsed,
        commitment_key=b"k" * 32,
    )
    public = commitment.to_dict()
    serialized = canonical_json(public)
    assert "problem_text" not in serialized
    assert "author_answer" not in serialized
    assert "numerator" not in serialized
    assert "denominator" not in serialized
    assert "3/4" not in serialized
    assert public["gold_commitment"].startswith("hmac-sha256:")
    assert commitment == make_internal_author_public_commitment_v3(
        parsed,
        commitment_key=b"k" * 32,
    )
    assert commitment != make_internal_author_public_commitment_v3(
        parsed,
        commitment_key=b"q" * 32,
    )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.update(extra=None),
        lambda value: value.update(candidate_id="authorv3_" + "0" * 64),
        lambda value: value["checks"].update(self_contained=False),
        lambda value: value["author_answer"].update(numerator=6, denominator=8),
        lambda value: value.update(verification=["Only one step."]),
        lambda value: value.update(problem_text="too short"),
        lambda value: value.update(schema_version=True),
    ],
)
def test_parser_fails_closed_on_schema_and_semantic_violations(mutator: object) -> None:
    slot = build_internal_author_slots_v3(SEED)[0]
    value = valid_output(slot)
    mutator(value)  # type: ignore[operator]
    with pytest.raises(ValueError):
        parse_internal_author_output_v3(canonical_json(value), slot)


def test_parser_rejects_duplicate_keys_markdown_bom_and_non_nfc() -> None:
    slot = build_internal_author_slots_v3(SEED)[0]
    valid = canonical_json(valid_output(slot))
    duplicate = valid[:-1] + ',"schema_version":1}'
    for raw in (duplicate, "```json\n" + valid + "\n```", "\ufeff" + valid):
        with pytest.raises(ValueError):
            parse_internal_author_output_v3(raw, slot)

    value = valid_output(slot)
    value["verification"][0] = "Cafe\u0301 is not NFC."
    with pytest.raises(ValueError, match="NFC"):
        parse_internal_author_output_v3(canonical_json(value), slot)
