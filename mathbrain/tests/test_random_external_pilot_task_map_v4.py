from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from math_brain.exact_answer_v3 import ExactRationalV3
from math_brain.random_external_pilot_task_map_v4 import (
    build_sealed_random_external_task_map_v4,
    score_capsule_from_random_external_task_map_v4,
    solver_projection_from_random_external_task_map_v4,
    verify_sealed_random_external_task_map_v4,
)
from math_brain.three_arm_evaluation import (
    DifficultyBand,
    FrozenPacket,
    MathDomain,
    ProblemSource,
    StudyStage,
    ThreeArmConfig,
    TokenizerReceipt,
)
from math_brain.three_arm_random_external_pilot_v4 import (
    AutomaticRetrievalDispositionV4,
    RandomExternalPilotCaseV4,
    RandomExternalPilotConfigV4,
    build_random_external_pilot_plan_v4,
)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _config() -> RandomExternalPilotConfigV4:
    return RandomExternalPilotConfigV4(
        authenticated_score_authority_commitment=_digest(
            "authenticated-score-authority"
        ),
        base=ThreeArmConfig(
            stage=StudyStage.PILOT,
            runtime_policy_digest=_digest("runtime"),
            prompt_template_digest=_digest("prompt"),
            response_schema_digest=_digest("response"),
            scorer_manifest_digest=_digest("scorer"),
            chronology_policy_digest=_digest("chronology"),
            scoring_authority_commitment=_digest("score-authority"),
            audit_authority_commitment=_digest("audit-authority"),
            analysis_authority_commitment=_digest("analysis-authority"),
        ),
        curated_lane_failure_evidence_digest=_digest("curated-failure"),
        external_selection_precommitment_digest=_digest("external-selection"),
        external_selection_precommit_anchor_evidence_digest=_digest(
            "external-selection-precommit-anchor-evidence"
        ),
        external_selection_capability_digest=_digest("external-selection-capability"),
        external_gold_audit_signed_report_digest=_digest("external-gold-signed-report"),
        internal_selection_precommitment_digest=_digest("internal-selection"),
        internal_selection_capability_digest=_digest("internal-selection-capability"),
        retrieval_protocol_digest=_digest("retrieval"),
        sham_matching_precommitment_digest=_digest("sham"),
        tokenizer_attestation_digest=_digest("tokenizer"),
    )


def _packet(index: int) -> FrozenPacket:
    return FrozenPacket(
        packet_id=f"packet-{index:02d}",
        origin_case_id=(f"internal-{index:02d}" if index < 12 else f"external-{index - 12:02d}"),
        text=f"FROZEN PACKET {index:02d}: " + (chr(65 + index % 26) * 64),
        snapshot_manifest_digest=_digest("snapshot"),
        retrieval_policy_digest=_digest("retrieval-policy"),
        query_digest=_digest(f"query-{index}"),
        format_template_digest=_digest("format"),
        section_shape_digest=_digest("shape"),
        age_status_vector_digest=_digest("age"),
        source_digests=(_digest(f"source-{index}"),),
        relevance_keys=(f"key-{index:02d}",),
        tokenizer_receipts=(
            TokenizerReceipt(
                tokenizer_id="proxy-v1",
                tokenizer_artifact_digest=_digest("tokenizer-artifact"),
                token_count=32,
                token_ids_digest=_digest(f"tokens-{index}"),
            ),
        ),
    )


def _plan(*, allocation_seed: bytes = b"a" * 32):
    cells = [(domain, difficulty) for domain in MathDomain for difficulty in DifficultyBand]
    cases = []
    packets = [_packet(index) for index in range(24)]
    for index in range(24):
        internal = index < 12
        domain, difficulty = cells[index] if internal else (None, None)
        cases.append(
            RandomExternalPilotCaseV4(
                case_id=(f"internal-{index:02d}" if internal else f"external-{index - 12:02d}"),
                source=ProblemSource.INTERNAL if internal else ProblemSource.EXTERNAL,
                domain=domain,
                difficulty=difficulty,
                independence_cluster_id=f"cluster-{index:02d}",
                problem_text=f"Compute the exact value for task {index:02d}.",
                scorer_spec_digest=_digest("scorer-spec"),
                retrieval_disposition=AutomaticRetrievalDispositionV4.SELECTED,
                retrieval_receipt_digest=_digest(f"receipt-{index}"),
                brain_packet_id=f"packet-{index:02d}",
                sham_packet_id=f"packet-{(index + 1) % 24:02d}",
                retrieval_abstention_reason_digest=None,
            )
        )
    return build_random_external_pilot_plan_v4(
        cases,
        packets,
        config=_config(),
        allocation_seed=allocation_seed,
    )


def _task_map(plan=None):
    return build_sealed_random_external_task_map_v4(
        _plan() if plan is None else plan,
        response_seed=b"r" * 32,
        score_seed=b"s" * 32,
        commitment_key=b"c" * 32,
    )


def test_seals_exactly_72_unique_private_response_and_score_joins() -> None:
    plan = _plan()
    task_map = _task_map(plan)

    assert len(task_map.entries) == 72
    assert len({item.private_task_id for item in task_map.entries}) == 72
    assert len({item.response_task_id for item in task_map.entries}) == 72
    assert len({item.score_id for item in task_map.entries}) == 72
    assert tuple(item.private_task_id for item in task_map.entries) == tuple(
        item.task_id for item in plan.assignments
    )
    assert all(item.private_task_id != item.response_task_id for item in task_map.entries)


def test_public_map_binds_v4_plan_without_arm_or_case_id_universe() -> None:
    task_map = _task_map()
    public = task_map.public_precommitment()

    assert public["curated_lane_pooled"] is False
    assert public["external_semantic_balance_claimed"] is False
    assert public["task_count"] == 72
    assert public["case_count"] == 24
    encoded = str(public)
    assert "internal-00" not in encoded
    assert "external-00" not in encoded
    assert "solvertaskv3_" not in encoded
    assert "scorev3_" not in encoded


def test_solver_projection_exposes_only_condition_neutral_protocol_fields() -> None:
    plan = _plan()
    task_map = _task_map(plan)
    projection = solver_projection_from_random_external_task_map_v4(task_map, plan, ordinal=0)

    assert set(projection.prompt_payload()) == {
        "optional_supporting_material",
        "problem_text",
        "task_id",
    }
    payload = str(projection.prompt_payload())
    assert "brain" not in payload.lower()
    assert "sham" not in payload.lower()
    assert "external" not in payload.lower()
    assert projection.task_id == task_map.entries[0].response_task_id
    assert projection.task_id != plan.assignments[0].task_id


def test_score_capsule_uses_precommitted_blind_score_identity() -> None:
    plan = _plan()
    task_map = _task_map(plan)
    capsule = score_capsule_from_random_external_task_map_v4(
        task_map,
        plan,
        ordinal=3,
        response_text=(
            '{"abstain":false,"concise_reasoning":"exact",'
            '"final_answer":"1/2","task_id":"' + task_map.entries[3].response_task_id + '"}'
        ),
        expected_answer=ExactRationalV3(1, 2),
    )

    assert capsule.score_id == task_map.entries[3].score_id
    assert capsule.task_id == task_map.entries[3].response_task_id
    assert not ({"arm", "source", "case_id"} & set(capsule.to_dict()))


def test_cold_replay_requires_external_map_digest_and_all_three_secrets() -> None:
    plan = _plan()
    task_map = _task_map(plan)
    verify_sealed_random_external_task_map_v4(
        task_map,
        plan,
        response_seed=b"r" * 32,
        score_seed=b"s" * 32,
        commitment_key=b"c" * 32,
        expected_public_precommitment_digest=task_map.public_precommitment_digest,
    )

    with pytest.raises(ValueError, match="external precommitment"):
        verify_sealed_random_external_task_map_v4(
            task_map,
            plan,
            response_seed=b"r" * 32,
            score_seed=b"s" * 32,
            commitment_key=b"c" * 32,
            expected_public_precommitment_digest=_digest("decoy"),
        )
    with pytest.raises(ValueError, match="cold replay"):
        verify_sealed_random_external_task_map_v4(
            task_map,
            plan,
            response_seed=b"x" * 32,
            score_seed=b"s" * 32,
            commitment_key=b"c" * 32,
            expected_public_precommitment_digest=task_map.public_precommitment_digest,
        )


def test_cold_replay_rejects_tampered_private_join() -> None:
    plan = _plan()
    task_map = _task_map(plan)
    tampered_entry = replace(task_map.entries[0], context_digest=_digest("decoy-context"))
    tampered = replace(task_map, entries=(tampered_entry, *task_map.entries[1:]))

    with pytest.raises(ValueError, match="cold replay"):
        verify_sealed_random_external_task_map_v4(
            tampered,
            plan,
            response_seed=b"r" * 32,
            score_seed=b"s" * 32,
            commitment_key=b"c" * 32,
            expected_public_precommitment_digest=tampered.public_precommitment_digest,
        )


def test_projection_rejects_map_bound_to_another_plan() -> None:
    first_plan = _plan(allocation_seed=b"a" * 32)
    second_plan = _plan(allocation_seed=b"b" * 32)
    task_map = _task_map(first_plan)

    with pytest.raises(ValueError, match="another plan"):
        solver_projection_from_random_external_task_map_v4(task_map, second_plan, ordinal=0)


def test_role_secrets_must_be_distinct() -> None:
    with pytest.raises(ValueError, match="role-separated"):
        build_sealed_random_external_task_map_v4(
            _plan(),
            response_seed=b"r" * 32,
            score_seed=b"r" * 32,
            commitment_key=b"c" * 32,
        )


def test_same_inputs_replay_and_seed_change_remints_whole_identity_universe() -> None:
    plan = _plan()
    first = _task_map(plan)
    replay = _task_map(plan)
    changed = build_sealed_random_external_task_map_v4(
        plan,
        response_seed=b"x" * 32,
        score_seed=b"y" * 32,
        commitment_key=b"z" * 32,
    )

    assert replay == first
    assert changed != first
    assert {item.response_task_id for item in changed.entries}.isdisjoint(
        {item.response_task_id for item in first.entries}
    )
    assert {item.score_id for item in changed.entries}.isdisjoint(
        {item.score_id for item in first.entries}
    )
