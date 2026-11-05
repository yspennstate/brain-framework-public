from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from math_brain.exact_answer_v3 import ExactRationalV3
from math_brain.models import canonical_json
from math_brain.three_arm_evaluation import (
    DifficultyBand,
    FrozenPacket,
    MathDomain,
    ProblemCase,
    ProblemSource,
    StudyStage,
    ThreeArmConfig,
    TokenizerReceipt,
    build_three_arm_plan,
)
from math_brain.three_arm_pilot_protocol_v3 import (
    EXACT_RATIONAL_SCORER_MANIFEST_DIGEST_V3,
    SOLVER_PROMPT_TEMPLATE_DIGEST_V3,
    SOLVER_RESPONSE_SCHEMA_DIGEST_V3,
    ScoreDispositionV3,
    render_solver_prompt_v3,
    score_arm_blind_capsule_v3,
)
from math_brain.three_arm_pilot_task_map_v3 import (
    PILOT_TASK_MAP_COUNT_V3,
    PilotTaskIdentityBindingV3,
    SealedPilotTaskMapV3,
    build_sealed_pilot_task_map_v3,
    score_capsule_from_sealed_map_v3,
    solver_projection_from_sealed_map_v3,
    verify_sealed_pilot_task_map_v3,
)

RESPONSE_SEED = bytes(range(32))
SCORE_SEED = bytes(range(32, 64))
COMMITMENT_KEY = bytes(range(64, 96))


def digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def pilot_plan(*, allocation_seed: bytes = b"pilot-map-allocation-seed-v3-0001"):
    rows = tuple(
        (source, domain, difficulty)
        for source in ProblemSource
        for domain in MathDomain
        for difficulty in DifficultyBand
    )
    packets = tuple(
        FrozenPacket(
            packet_id=f"packet-{index:03d}",
            origin_case_id=f"case-{index:03d}",
            text=f"method-{index:03d}",
            snapshot_manifest_digest=digest("snapshot"),
            retrieval_policy_digest=digest("retrieval-policy"),
            query_digest=digest(f"query-{index}"),
            format_template_digest=digest("format"),
            section_shape_digest=digest("shape"),
            age_status_vector_digest=digest("age-status"),
            source_digests=(digest(f"source-{index}"),),
            relevance_keys=(f"case-key:{index:03d}",),
            tokenizer_receipts=(
                TokenizerReceipt(
                    tokenizer_id="engineering-proxy",
                    tokenizer_artifact_digest=digest("tokenizer"),
                    token_count=3,
                    token_ids_digest=digest(f"token-ids-{index}"),
                ),
            ),
        )
        for index, _row in enumerate(rows)
    )
    cases = tuple(
        ProblemCase(
            case_id=f"case-{index:03d}",
            source=source,
            domain=domain,
            difficulty=difficulty,
            independence_cluster_id=f"cluster-{index:03d}",
            problem_text=f"Solve the sealed problem number {index}.",
            scorer_spec_digest=digest("exact-rational-scorer"),
            brain_packet_id=f"packet-{index:03d}",
            sham_packet_id=f"packet-{(index + 1) % len(rows):03d}",
            required_relevance_keys=(f"case-key:{index:03d}",),
        )
        for index, (source, domain, difficulty) in enumerate(rows)
    )
    config = ThreeArmConfig(
        stage=StudyStage.PILOT,
        runtime_policy_digest=digest("runtime-policy"),
        prompt_template_digest=SOLVER_PROMPT_TEMPLATE_DIGEST_V3,
        response_schema_digest=SOLVER_RESPONSE_SCHEMA_DIGEST_V3,
        scorer_manifest_digest=EXACT_RATIONAL_SCORER_MANIFEST_DIGEST_V3,
        chronology_policy_digest=digest("chronology-policy"),
        scoring_authority_commitment=digest("scoring-authority"),
        audit_authority_commitment=digest("audit-authority"),
        analysis_authority_commitment=digest("analysis-authority"),
    )
    return build_three_arm_plan(
        cases,
        packets,
        config=config,
        allocation_seed=allocation_seed,
    )


def task_map(plan=None) -> SealedPilotTaskMapV3:
    if plan is None:
        plan = pilot_plan()
    return build_sealed_pilot_task_map_v3(
        plan,
        response_seed=RESPONSE_SEED,
        score_seed=SCORE_SEED,
        commitment_key=COMMITMENT_KEY,
    )


def test_map_seals_exactly_72_unique_role_separated_ids_and_replays() -> None:
    plan = pilot_plan()
    sealed = task_map(plan)

    assert len(sealed.entries) == PILOT_TASK_MAP_COUNT_V3 == 72
    assert len({entry.plan_task_id for entry in sealed.entries}) == 72
    assert len({entry.response_task_id for entry in sealed.entries}) == 72
    assert len({entry.score_id for entry in sealed.entries}) == 72
    assert tuple(entry.schedule_index for entry in sealed.entries) == tuple(range(72))
    assert sealed == task_map(plan)

    verify_sealed_pilot_task_map_v3(
        sealed,
        plan,
        response_seed=RESPONSE_SEED,
        score_seed=SCORE_SEED,
        commitment_key=COMMITMENT_KEY,
        expected_public_precommitment_digest=sealed.public_precommitment_digest,
    )


def test_public_precommitment_hides_every_private_join_and_overclaim_flag() -> None:
    sealed = task_map()
    public = canonical_json(sealed.public_precommitment())

    assert '"entries"' not in public
    assert "plan_task_id" not in public
    assert "response_task_id" not in public
    assert "score_id" not in public
    assert sealed.public_precommitment()["chronology_authenticated_in_artifact"] is False
    assert sealed.public_precommitment()["served_model_identity_verified"] is False
    for entry in sealed.entries:
        assert entry.plan_task_id not in public
        assert entry.response_task_id not in public
        assert entry.score_id not in public


def test_seeds_are_deterministic_role_separated_and_change_only_their_universe() -> None:
    plan = pilot_plan()
    baseline = task_map(plan)
    changed_response = build_sealed_pilot_task_map_v3(
        plan,
        response_seed=b"R" * 32,
        score_seed=SCORE_SEED,
        commitment_key=COMMITMENT_KEY,
    )
    changed_score = build_sealed_pilot_task_map_v3(
        plan,
        response_seed=RESPONSE_SEED,
        score_seed=b"S" * 32,
        commitment_key=COMMITMENT_KEY,
    )

    assert tuple(item.response_task_id for item in baseline.entries) != tuple(
        item.response_task_id for item in changed_response.entries
    )
    assert tuple(item.score_id for item in baseline.entries) == tuple(
        item.score_id for item in changed_response.entries
    )
    assert tuple(item.response_task_id for item in baseline.entries) == tuple(
        item.response_task_id for item in changed_score.entries
    )
    assert tuple(item.score_id for item in baseline.entries) != tuple(
        item.score_id for item in changed_score.entries
    )

    for duplicated in (RESPONSE_SEED, SCORE_SEED):
        with pytest.raises(ValueError, match="role-separated"):
            build_sealed_pilot_task_map_v3(
                plan,
                response_seed=duplicated,
                score_seed=duplicated,
                commitment_key=COMMITMENT_KEY,
            )


def test_projection_is_stable_and_contains_no_private_plan_or_arm_identifier() -> None:
    plan = pilot_plan()
    sealed = task_map(plan)
    for ordinal in (0, 1, 35, 71):
        projection = solver_projection_from_sealed_map_v3(sealed, plan, ordinal=ordinal)
        repeated = solver_projection_from_sealed_map_v3(sealed, plan, ordinal=ordinal)
        binding = sealed.binding_at(ordinal)
        prompt = render_solver_prompt_v3(projection)

        assert projection == repeated
        assert projection.task_id == binding.response_task_id
        assert binding.plan_task_id not in prompt
        assert binding.score_id not in prompt
        assert "StudyArm" not in prompt and '"arm"' not in prompt


def test_presealed_score_capsule_uses_fixed_join_and_scores_without_arm_metadata() -> None:
    plan = pilot_plan()
    sealed = task_map(plan)
    projection = solver_projection_from_sealed_map_v3(sealed, plan, ordinal=4)
    response_text = canonical_json(
        {
            "abstain": False,
            "concise_reasoning": "The exact computation gives three quarters.",
            "final_answer": "3/4",
            "task_id": projection.task_id,
        }
    )
    capsule = score_capsule_from_sealed_map_v3(
        sealed,
        plan,
        ordinal=4,
        response_text=response_text,
        expected_answer=ExactRationalV3(3, 4),
    )
    result = score_arm_blind_capsule_v3(capsule)

    assert capsule.score_id == sealed.entries[4].score_id
    assert capsule.task_id == sealed.entries[4].response_task_id
    assert capsule.problem_digest == plan.assignments[4].problem_digest
    assert result.disposition is ScoreDispositionV3.SCORED
    assert result.correct is True
    encoded = capsule.canonical_json()
    assert sealed.entries[4].plan_task_id not in encoded
    assert "arm" not in encoded and "schedule_index" not in encoded


def test_cold_replay_rejects_wrong_expected_digest_seed_key_and_plan() -> None:
    plan = pilot_plan()
    sealed = task_map(plan)
    kwargs = {
        "response_seed": RESPONSE_SEED,
        "score_seed": SCORE_SEED,
        "commitment_key": COMMITMENT_KEY,
        "expected_public_precommitment_digest": sealed.public_precommitment_digest,
    }
    with pytest.raises(ValueError, match="expected public precommitment"):
        verify_sealed_pilot_task_map_v3(
            sealed,
            plan,
            **{**kwargs, "expected_public_precommitment_digest": digest("wrong public")},
        )
    with pytest.raises(ValueError, match="cold replay"):
        verify_sealed_pilot_task_map_v3(
            sealed,
            plan,
            **{**kwargs, "response_seed": b"R" * 32},
        )
    with pytest.raises(ValueError, match="cold replay"):
        verify_sealed_pilot_task_map_v3(
            sealed,
            plan,
            **{**kwargs, "commitment_key": b"K" * 32},
        )
    with pytest.raises(ValueError, match="another plan"):
        solver_projection_from_sealed_map_v3(
            sealed,
            pilot_plan(allocation_seed=b"A" * 32),
            ordinal=0,
        )


def test_validly_shaped_tampering_and_duplicate_ids_fail_closed() -> None:
    plan = pilot_plan()
    sealed = task_map(plan)
    tampered = replace(
        sealed,
        mapping_commitment="hmac-sha256:" + "0" * 64,
    )
    with pytest.raises(ValueError, match="cold replay"):
        verify_sealed_pilot_task_map_v3(
            tampered,
            plan,
            response_seed=RESPONSE_SEED,
            score_seed=SCORE_SEED,
            commitment_key=COMMITMENT_KEY,
            expected_public_precommitment_digest=tampered.public_precommitment_digest,
        )

    duplicated = replace(
        sealed.entries[1],
        response_task_id=sealed.entries[0].response_task_id,
    )
    with pytest.raises(ValueError, match="reuses a response task"):
        replace(sealed, entries=(sealed.entries[0], duplicated, *sealed.entries[2:]))

    malformed = object.__new__(PilotTaskIdentityBindingV3)
    with pytest.raises((AttributeError, ValueError)):
        replace(sealed, entries=(malformed, *sealed.entries[1:]))


def test_confirmatory_or_malformed_cardinality_plan_cannot_enter_pilot_map() -> None:
    plan = pilot_plan()
    confirmatory_config = replace(plan.config, stage=StudyStage.CONFIRMATORY)
    confirmatory_shaped = replace(plan, config=confirmatory_config)
    with pytest.raises(ValueError, match="confirmatory"):
        task_map(confirmatory_shaped)

    with pytest.raises(ValueError, match="assignment schedule"):
        replace(plan, assignments=plan.assignments[:-1])
