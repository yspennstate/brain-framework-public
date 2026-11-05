"""Regression evidence for the public hard-v2 schedule inversion blocker."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable

import pytest

import math_brain.hard_challenge_v2 as challenge_module
from math_brain.hard_challenge_v2 import FamilyV2, HardChallengeCaseV2

InstanceLocator = Callable[[str], tuple[FamilyV2, int]]


def _locate_instance_from_public_tag(prompt: str) -> tuple[FamilyV2, int]:
    match = re.search(r"Opaque case tag ([0-9]{20})", prompt)
    assert match is not None
    tag = match.group(1)
    candidates = [
        (family, instance)
        for family in challenge_module.FAMILY_ORDER_V2
        for instance in range(20)
        if challenge_module._surface_case_tag_v2(family, instance * 512) == tag
    ]
    assert len(candidates) == 1
    return candidates[0]


def _locate_instance_from_public_legend(prompt: str) -> tuple[FamilyV2, int]:
    """Recover the instance after pretending the public case tag was redacted."""

    prompt = re.sub(r"Opaque case tag [0-9]{20}", "Opaque case tag [REDACTED]", prompt)
    candidates = [
        (family, instance)
        for family in challenge_module.FAMILY_ORDER_V2
        for instance in range(20)
        if challenge_module._surface_legend_v2(
            family,
            challenge_module._surface_aliases_v2(family, instance),
        )
        in prompt
    ]
    assert len(candidates) == 1
    return candidates[0]


def _visible_template(trace: tuple[str, ...]) -> int:
    first_body = trace[0].split(". ", 1)[1]
    matches = [
        template
        for marker, template in (
            ("Record ", 0),
            ("Ledger ", 1),
            ("Using ", 2),
            ("[", 3),
        )
        if first_body.startswith(marker)
    ]
    assert len(matches) == 1
    return matches[0]


def _redact_displayed_arithmetic(trace: tuple[str, ...]) -> tuple[str, ...]:
    redacted: list[str] = []
    for line in trace:
        target = re.search(r"claim v-[0-9]{2}", line)
        assert target is not None
        redacted.append(line[: target.end()] + " [DISPLAYED ARITHMETIC REDACTED]")
    return tuple(redacted)


def _decode_public_schedule(
    prompt: str,
    trace: tuple[str, ...],
    *,
    locate_instance: InstanceLocator,
) -> tuple[int, HardChallengeCaseV2]:
    """Regenerate an answer using surface schedule fields, never basis arithmetic."""

    family, instance = locate_instance(prompt)
    template = _visible_template(trace)
    aliases = challenge_module._surface_aliases_v2(family, instance)
    rendered_claim_roles = {
        challenge_module._render_surface_identifier_v2(alias): int(name.removeprefix("claim"))
        for name, alias in aliases.items()
        if name.startswith("claim")
    }
    visible_role_order: list[int] = []
    for line in trace[2:10]:
        target = re.search(r"claim (v-[0-9]{2})", line)
        assert target is not None
        visible_role_order.append(rendered_claim_roles[target.group(1)])

    base_root_by_instance = {
        numeric_instance: root
        for root, group in enumerate(challenge_module._CALIBRATION_BASE_ROOT_GROUPS_V2)
        for numeric_instance in group
    }
    coordinate_candidates: list[tuple[int, int]] = []
    for derivation in range(4):
        root_role = (
            instance
            + challenge_module._RELATIONAL_ROLE_SHIFTS_V2[derivation][template]
        ) % 8
        detail_roles = tuple(role for role in range(8) if role != root_role)
        permutation = challenge_module._DERIVATION_DETAIL_PERMUTATIONS_V2[derivation]
        ordered_details = tuple(detail_roles[index] for index in permutation)
        position = challenge_module._CALIBRATION_ROOT_PERMUTATIONS_V2[derivation][template][
            base_root_by_instance[instance]
        ]
        expected_order = (
            *ordered_details[:position],
            root_role,
            *ordered_details[position:],
        )
        if expected_order == tuple(visible_role_order):
            coordinate_candidates.append((derivation, position))
    assert len(coordinate_candidates) == 1
    derivation, position = coordinate_candidates[0]

    fault_variant = (instance % 2) ^ challenge_module._CALIBRATION_MECHANISM_FLIPS_V2[
        derivation
    ][template]
    parameter_cell = ((instance // 2) % 2) ^ (derivation % 2)
    seed = challenge_module.encode_development_seed_v2(
        position=position,
        template=template,
        derivation_family=derivation,
        fault_variant=fault_variant,
        parameter_cell=parameter_cell,
        instance=instance,
    )
    return seed, challenge_module.generate_development_case_v2(family, seed)


@pytest.mark.parametrize(
    "locate_instance",
    (_locate_instance_from_public_tag, _locate_instance_from_public_legend),
    ids=("case-tag", "tag-redacted-alias-fingerprint"),
)
def test_public_schedule_recovers_every_seed_and_complete_answer(
    locate_instance: InstanceLocator,
) -> None:
    cases = challenge_module.generate_public_calibration_bank_v2()
    seed_correct = 0
    line_correct = 0
    mechanism_correct = 0
    complete_answer_correct = 0
    verifier_accepts = 0
    records: list[dict[str, object]] = []

    for case in cases:
        seed, regenerated = _decode_public_schedule(
            case.prompt,
            _redact_displayed_arithmetic(case.trace(faulty=True)),
            locate_instance=locate_instance,
        )
        assert regenerated.prompt == case.prompt
        assert regenerated.trace(faulty=True) == case.trace(faulty=True)
        seed_correct += seed == case.seed
        line_correct += regenerated.faulty_line == case.faulty_line
        mechanism_correct += regenerated.mechanism == case.mechanism
        complete_answer_correct += (
            regenerated.expected_solver_output.to_dict()
            == case.expected_solver_output.to_dict()
        )
        verifier_accepts += case.verify_solver_output(regenerated.expected_solver_output)[0]
        records.append(
            {
                "case_id": case.case_id,
                "seed": seed,
                "solver_output": regenerated.expected_solver_output.to_dict(),
            }
        )

    assert len(cases) == 320
    assert seed_correct == 320
    assert line_correct == 320
    assert mechanism_correct == 320
    assert complete_answer_correct == 320
    assert verifier_accepts == 320
    records_digest = "sha256:" + hashlib.sha256(
        challenge_module.canonical_json_v2(records).encode("utf-8")
    ).hexdigest()
    assert records_digest == (
        "sha256:7f9ffd0baa5d943ff4c78c3d10f5acb23e4ab2932bf723227bd126efb3d5e935"
    )
