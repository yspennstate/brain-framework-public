from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import math_brain.exact_answer_v3 as exact_answer_module
from math_brain.exact_answer_v3 import ExactRationalV3
from math_brain.models import canonical_json
from math_brain.three_arm_evaluation import (
    DifficultyBand,
    FrozenPacket,
    MathDomain,
    ProblemCase,
    ProblemSource,
    SolverTask,
    StudyArm,
    StudyStage,
    ThreeArmConfig,
    TokenizerReceipt,
    build_three_arm_plan,
)
from math_brain.three_arm_pilot_protocol_v3 import (
    EXACT_RATIONAL_ANSWER_CONTRACT_DIGEST_V3,
    EXACT_RATIONAL_PARSER_SOURCE_ARTIFACT_DIGEST_V3,
    EXACT_RATIONAL_SCORER_MANIFEST_DIGEST_V3,
    EXACT_RATIONAL_SCORING_RULE_DIGEST_V3,
    IN_PROCESS_PARSER_IDENTITY_VERIFIED_V3,
    LOADED_CODE_EVIDENCE_REQUIRED_V3,
    PRIVATE_ASSEMBLER_REQUIRED_BINDINGS_V3,
    SOLVER_OUTPUT_MAX_BYTES_V3,
    SOLVER_PROMPT_TEMPLATE_DIGEST_V3,
    SOLVER_RESPONSE_SCHEMA_DIGEST_V3,
    ArmBlindScoreCapsuleV3,
    ScoreDispositionV3,
    SolverOutputErrorCodeV3,
    SolverOutputErrorV3,
    SolverTaskProjectionV3,
    assert_three_arm_protocol_config_v3,
    exact_rational_parser_source_artifact_digest_v3,
    make_arm_blind_score_capsule_v3,
    make_solver_task_projection_v3,
    new_opaque_score_id_v3,
    new_solver_response_task_id_v3,
    parse_solver_output_v3,
    render_solver_prompt_v3,
    score_arm_blind_capsule_v3,
    solver_prompt_digest_v3,
    verify_exact_rational_parser_source_artifact_v3,
    verify_score_result_v3,
)


def digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def response_task_id(label: str) -> str:
    return "solvertaskv3_" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def solver_task(
    *,
    task_id: str | None = None,
    problem: str = "Compute 1/2 + 1/4.",
    supporting_material: str = "Use a common denominator and reduce the result.",
) -> SolverTask:
    return SolverTask(
        task_id=digest("task") if task_id is None else task_id,
        problem_text=problem,
        brain_context=supporting_material,
        problem_digest="sha256:" + hashlib.sha256(problem.encode("utf-8")).hexdigest(),
        context_digest=(
            "sha256:" + hashlib.sha256(supporting_material.encode("utf-8")).hexdigest()
        ),
    )


def solver_projection(
    *,
    response_id: str | None = None,
    task_id: str | None = None,
    problem: str = "Compute 1/2 + 1/4.",
    supporting_material: str = "Use a common denominator and reduce the result.",
) -> SolverTaskProjectionV3:
    task = solver_task(
        task_id=task_id,
        problem=problem,
        supporting_material=supporting_material,
    )
    if response_id is None:
        return make_solver_task_projection_v3(task)
    return SolverTaskProjectionV3(
        task_id=response_id,
        problem_text=task.problem_text,
        supporting_material=task.brain_context,
        problem_digest=task.problem_digest,
        supporting_material_digest=task.context_digest,
    )


def response(
    task_id: str,
    *,
    final_answer: str | None = "3/4",
    abstain: bool = False,
    reasoning: str = "A common denominator gives 2/4 + 1/4 = 3/4.",
) -> str:
    return canonical_json(
        {
            "abstain": abstain,
            "concise_reasoning": reasoning,
            "final_answer": final_answer,
            "task_id": task_id,
        }
    )


def all_keys(value: Any) -> tuple[str, ...]:
    found: list[str] = []
    if type(value) is dict:
        for key, child in value.items():
            found.append(key)
            found.extend(all_keys(child))
    elif type(value) is list:
        for child in value:
            found.extend(all_keys(child))
    return tuple(found)


def real_pilot_plan():
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
                    tokenizer_id="pinned-tokenizer",
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
            scorer_spec_digest=digest("scorer-int-rational"),
            brain_packet_id=f"packet-{index:03d}",
            sham_packet_id=f"packet-{(index + 1) % len(rows):03d}",
            required_relevance_keys=(f"case-key:{index:03d}",),
        )
        for index, (source, domain, difficulty) in enumerate(rows)
    )
    config = ThreeArmConfig(
        stage=StudyStage.PILOT,
        runtime_policy_digest=digest("runtime"),
        prompt_template_digest=SOLVER_PROMPT_TEMPLATE_DIGEST_V3,
        response_schema_digest=SOLVER_RESPONSE_SCHEMA_DIGEST_V3,
        scorer_manifest_digest=EXACT_RATIONAL_SCORER_MANIFEST_DIGEST_V3,
        chronology_policy_digest=digest("chronology"),
        scoring_authority_commitment=digest("scoring-authority"),
        audit_authority_commitment=digest("audit-authority"),
        analysis_authority_commitment=digest("analysis-authority"),
    )
    return build_three_arm_plan(
        cases,
        packets,
        config=config,
        allocation_seed=b"pilot-allocation-seed" * 2,
    )


def test_fixed_prompt_is_arm_neutral_and_binds_only_the_solver_task() -> None:
    sealed_first = solver_task()
    first = make_solver_task_projection_v3(sealed_first)
    sealed_second = solver_task(
        task_id=digest("second task"),
        problem="Compute 2 + 5.",
        supporting_material="Check the addition directly.",
    )
    second = make_solver_task_projection_v3(sealed_second)

    rendered = render_solver_prompt_v3(first)
    assert rendered == render_solver_prompt_v3(first)
    assert solver_prompt_digest_v3(first) == (
        "sha256:" + hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    )
    assert SOLVER_PROMPT_TEMPLATE_DIGEST_V3 == (
        "sha256:6d8536531aed7af959102953619300556d4c461bbb0b575b6dd114e89d3c53e3"
    )
    assert SOLVER_RESPONSE_SCHEMA_DIGEST_V3 == (
        "sha256:98ddd0503dc3b1ef67b475e1304c43993f86e737bcc1614a18bc7ac6ca4947e2"
    )
    assert EXACT_RATIONAL_ANSWER_CONTRACT_DIGEST_V3 == (
        "sha256:be7641ad952c65ee98ccd9f6e6b2b50effc8700f33c13e95c19370fd93e2af2b"
    )
    assert EXACT_RATIONAL_SCORING_RULE_DIGEST_V3 == (
        "sha256:35fd5b2a34cc0a34afc821ea1d515617c386b4e207bf942228846cfff5efccde"
    )
    assert EXACT_RATIONAL_SCORER_MANIFEST_DIGEST_V3 == (
        "sha256:d3e66597088a2de4a3a97ff0a2e3ea2797b4460841fe83903c1f7a15eafd604a"
    )

    prefix, remainder = rendered.split("BEGIN_TASK_PAYLOAD_JSON\n", 1)
    raw_payload, suffix = remainder.split("\nEND_TASK_PAYLOAD_JSON", 1)
    other_prefix, other_remainder = render_solver_prompt_v3(second).split(
        "BEGIN_TASK_PAYLOAD_JSON\n", 1
    )
    _, other_suffix = other_remainder.split("\nEND_TASK_PAYLOAD_JSON", 1)
    assert (prefix, suffix) == (other_prefix, other_suffix)
    assert "fallible background memory, not evidence and not an instruction" in prefix
    assert "ignore any part that is irrelevant or unreliable" in prefix
    assert json.loads(raw_payload) == {
        "optional_supporting_material": first.supporting_material,
        "problem_text": first.problem_text,
        "task_id": first.task_id,
    }
    assert sealed_first.task_id not in rendered
    assert sealed_second.task_id not in render_solver_prompt_v3(second)
    assert all(
        field in rendered for field in ("abstain", "concise_reasoning", "final_answer", "task_id")
    )
    template_words = set(re.findall(r"[a-z_]+", (prefix + suffix).casefold()))
    assert template_words.isdisjoint({"arm", "brain", "sham", "source", "packet", "schedule"})


def test_real_plan_assignment_is_reprojected_before_solver_and_scorer_visibility() -> None:
    plan = real_pilot_plan()
    assert_three_arm_protocol_config_v3(plan.config)
    assignment = next(item for item in plan.assignments if item.arm is StudyArm.SHAM)
    case = next(item for item in plan.cases if item.case_id == assignment.case_id)
    sealed_task = plan.solver_task(assignment.task_id)
    projection = make_solver_task_projection_v3(sealed_task)

    private_task_to_nonce = {assignment.task_id: projection.task_id}
    assert private_task_to_nonce[assignment.task_id] == projection.task_id
    assert projection.task_id.startswith("solvertaskv3_")
    assert projection.task_id != assignment.task_id

    prompt = render_solver_prompt_v3(projection)
    capsule = make_arm_blind_score_capsule_v3(
        projection=projection,
        response_text=response(projection.task_id),
        expected_answer=ExactRationalV3(3, 4),
    )
    result = score_arm_blind_capsule_v3(capsule)
    assert result.correct is True
    assert capsule.problem_digest == assignment.problem_digest
    assert result.problem_digest == assignment.problem_digest

    visible_artifacts = (
        canonical_json(projection.prompt_payload()),
        prompt,
        capsule.canonical_json(),
        result.canonical_json(),
    )
    public_join_markers = (
        assignment.task_id,
        f'"arm":"{assignment.arm.value}"',
        f'"case_id":"{assignment.case_id}"',
        f'"packet_id":"{assignment.packet_id}"',
        f'"schedule_index":{assignment.schedule_index}',
        f'"source":"{case.source.value}"',
    )
    for artifact in visible_artifacts:
        assert all(marker not in artifact for marker in public_join_markers)

    wrong_nonce = new_solver_response_task_id_v3()
    assert wrong_nonce != projection.task_id
    wrong_nonce_capsule = make_arm_blind_score_capsule_v3(
        projection=projection,
        response_text=response(wrong_nonce),
        expected_answer=ExactRationalV3(3, 4),
    )
    wrong_nonce_result = score_arm_blind_capsule_v3(wrong_nonce_capsule)
    assert wrong_nonce_result.disposition is ScoreDispositionV3.TASK_ID_MISMATCH
    assert wrong_nonce_result.correct is False

    leaked_plan_id_capsule = make_arm_blind_score_capsule_v3(
        projection=projection,
        response_text=response(assignment.task_id),
        expected_answer=ExactRationalV3(3, 4),
    )
    leaked_plan_id_result = score_arm_blind_capsule_v3(leaked_plan_id_capsule)
    assert leaked_plan_id_result.disposition is ScoreDispositionV3.MALFORMED
    assert leaked_plan_id_result.correct is False


@pytest.mark.parametrize(
    "change",
    [
        {"prompt_template_digest": digest("wrong prompt")},
        {"response_schema_digest": digest("wrong response schema")},
        {"scorer_manifest_digest": digest("wrong scorer manifest")},
    ],
)
def test_plan_config_must_bind_exact_prompt_response_and_scorer_commitments(
    change: dict[str, str],
) -> None:
    config = real_pilot_plan().config
    assert_three_arm_protocol_config_v3(config)
    with pytest.raises(ValueError, match="differs from the solver protocol commitment"):
        assert_three_arm_protocol_config_v3(replace(config, **change))


def test_exact_answer_parser_source_artifact_is_bound_cross_platform_without_overclaim() -> None:
    raw = Path(exact_answer_module.__file__).read_bytes()
    lf = raw.replace(b"\r\n", b"\n")
    crlf = lf.replace(b"\n", b"\r\n")
    assert EXACT_RATIONAL_PARSER_SOURCE_ARTIFACT_DIGEST_V3 == (
        "sha256:527d3444db071d6370b01c8b99c4b8018c207bf7a38047d8362dccf4017426ae"
    )
    assert exact_rational_parser_source_artifact_digest_v3(lf) == (
        EXACT_RATIONAL_PARSER_SOURCE_ARTIFACT_DIGEST_V3
    )
    assert exact_rational_parser_source_artifact_digest_v3(crlf) == (
        EXACT_RATIONAL_PARSER_SOURCE_ARTIFACT_DIGEST_V3
    )
    verify_exact_rational_parser_source_artifact_v3(raw)
    with pytest.raises(ValueError, match="source artifact differs"):
        verify_exact_rational_parser_source_artifact_v3(raw + b"\n# tampered\n")


def test_digest_bound_score_explicitly_requires_private_authenticated_assembly() -> None:
    assert IN_PROCESS_PARSER_IDENTITY_VERIFIED_V3 is False
    assert LOADED_CODE_EVIDENCE_REQUIRED_V3 is True
    assert PRIVATE_ASSEMBLER_REQUIRED_BINDINGS_V3 == (
        "audited_gold",
        "exactly_72_unique_plan_task_ids",
        "exactly_72_unique_response_task_ids",
        "exactly_72_unique_score_ids",
        "ledger_terminal_record",
        "loaded_scorer_and_parser_code_evidence",
        "prestarted_sealed_plan_task_response_nonce_bijection",
        "prompt_record",
        "response_nonce_no_remint",
        "signed_scored_attempt",
        "transcript_record",
    )


def test_output_parser_accepts_only_the_exact_four_field_schema() -> None:
    task_id = response_task_id("task")
    raw = json.dumps(
        {
            "task_id": task_id,
            "concise_reasoning": "Reduce six eighths.",
            "final_answer": "6/8",
            "abstain": False,
        },
        indent=2,
    )
    parsed = parse_solver_output_v3(raw, expected_task_id=task_id)
    assert parsed.to_dict() == {
        "abstain": False,
        "concise_reasoning": "Reduce six eighths.",
        "final_answer": "6/8",
        "task_id": task_id,
    }
    assert set(parsed.to_dict()) == {
        "abstain",
        "concise_reasoning",
        "final_answer",
        "task_id",
    }

    decomposed_reasoning = "Cafe\u0301 arithmetic."
    assert (
        parse_solver_output_v3(
            response(task_id, reasoning=decomposed_reasoning),
            expected_task_id=task_id,
        ).concise_reasoning
        == decomposed_reasoning
    )


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",
        "{}",
        '{"abstain":false,"abstain":false,"concise_reasoning":"x",'
        '"final_answer":"1","task_id":"sha256:' + "0" * 64 + '"}',
        canonical_json(
            {
                "abstain": False,
                "concise_reasoning": "x",
                "extra": None,
                "final_answer": "1",
                "task_id": response_task_id("task"),
            }
        ),
        canonical_json(
            {
                "abstain": 0,
                "concise_reasoning": "x",
                "final_answer": "1",
                "task_id": response_task_id("task"),
            }
        ),
        canonical_json(
            {
                "abstain": False,
                "concise_reasoning": "x",
                "final_answer": None,
                "task_id": response_task_id("task"),
            }
        ),
        canonical_json(
            {
                "abstain": True,
                "concise_reasoning": "x",
                "final_answer": "1",
                "task_id": response_task_id("task"),
            }
        ),
        "\ufeff" + response(response_task_id("task")),
        "```json\n" + response(response_task_id("task")) + "\n```",
    ],
)
def test_output_parser_rejects_malformed_or_inconsistent_responses(raw: str) -> None:
    with pytest.raises(SolverOutputErrorV3) as caught:
        parse_solver_output_v3(raw)
    assert caught.value.code is SolverOutputErrorCodeV3.MALFORMED


def test_output_parser_enforces_byte_bound_and_task_binding() -> None:
    task_id = response_task_id("task")
    oversized = response(task_id, reasoning="x" * SOLVER_OUTPUT_MAX_BYTES_V3)
    with pytest.raises(SolverOutputErrorV3) as caught:
        parse_solver_output_v3(oversized)
    assert caught.value.code is SolverOutputErrorCodeV3.MALFORMED

    with pytest.raises(SolverOutputErrorV3) as caught:
        parse_solver_output_v3(response(response_task_id("other")), expected_task_id=task_id)
    assert caught.value.code is SolverOutputErrorCodeV3.TASK_ID_MISMATCH

    with pytest.raises(ValueError, match="fresh solvertaskv3_ nonce"):
        parse_solver_output_v3(response(task_id), expected_task_id=digest("plan task id"))


def test_exact_rational_scoring_is_deterministic_and_digest_bound() -> None:
    task_id = response_task_id("task")
    projection = solver_projection(response_id=task_id)
    capsule = make_arm_blind_score_capsule_v3(
        projection=projection,
        response_text=response(task_id, final_answer="6/8"),
        expected_answer=ExactRationalV3(3, 4),
    )
    result = score_arm_blind_capsule_v3(capsule)
    assert result == score_arm_blind_capsule_v3(capsule)
    assert result.disposition is ScoreDispositionV3.SCORED
    assert result.submitted_answer == ExactRationalV3(3, 4)
    assert result.correct is True
    assert result.scoring_rule_digest == EXACT_RATIONAL_SCORING_RULE_DIGEST_V3
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", result.digest)
    verify_score_result_v3(capsule, result)

    with pytest.raises(ValueError, match="deterministic recomputation"):
        verify_score_result_v3(capsule, replace(result, correct=False))
    with pytest.raises(ValueError, match="another capsule"):
        verify_score_result_v3(
            replace(capsule, expected_answer=ExactRationalV3(7, 8)),
            result,
        )


@pytest.mark.parametrize(
    ("raw_answer", "normalized"),
    [
        ("0", ExactRationalV3(0, 1)),
        ("0/1", ExactRationalV3(0, 1)),
        ("0/2", ExactRationalV3(0, 1)),
        ("6/8", ExactRationalV3(3, 4)),
    ],
)
def test_raw_zero_and_reducible_lexemes_normalize_before_scoring(
    raw_answer: str,
    normalized: ExactRationalV3,
) -> None:
    projection = solver_projection()
    capsule = make_arm_blind_score_capsule_v3(
        projection=projection,
        response_text=response(projection.task_id, final_answer=raw_answer),
        expected_answer=normalized,
    )
    result = score_arm_blind_capsule_v3(capsule)
    assert result.disposition is ScoreDispositionV3.SCORED
    assert result.submitted_answer == normalized
    assert result.correct is True


@pytest.mark.parametrize(
    ("raw_factory", "expected_disposition"),
    [
        (lambda task_id: "not json", ScoreDispositionV3.MALFORMED),
        (
            lambda task_id: response(response_task_id("wrong task")),
            ScoreDispositionV3.TASK_ID_MISMATCH,
        ),
        (
            lambda task_id: response(task_id, final_answer=None, abstain=True),
            ScoreDispositionV3.ABSTAINED,
        ),
        (
            lambda task_id: response(task_id, final_answer="approximately 0.75"),
            ScoreDispositionV3.INVALID_ANSWER,
        ),
    ],
)
def test_malformed_mismatched_abstained_and_invalid_outputs_score_incorrect(
    raw_factory: Any,
    expected_disposition: ScoreDispositionV3,
) -> None:
    task_id = response_task_id("task")
    projection = solver_projection(response_id=task_id)
    capsule = make_arm_blind_score_capsule_v3(
        projection=projection,
        response_text=raw_factory(task_id),
        expected_answer=ExactRationalV3(3, 4),
    )
    result = score_arm_blind_capsule_v3(capsule)
    assert result.disposition is expected_disposition
    assert result.correct is False
    assert result.submitted_answer is None
    verify_score_result_v3(capsule, result)


def test_wrong_exact_rational_is_a_valid_but_incorrect_score() -> None:
    task_id = response_task_id("task")
    projection = solver_projection(response_id=task_id)
    capsule = make_arm_blind_score_capsule_v3(
        projection=projection,
        response_text=response(task_id, final_answer="2/3"),
        expected_answer=ExactRationalV3(3, 4),
    )
    result = score_arm_blind_capsule_v3(capsule)
    assert result.disposition is ScoreDispositionV3.SCORED
    assert result.submitted_answer == ExactRationalV3(2, 3)
    assert result.correct is False


def test_score_ids_are_fresh_opaque_join_keys() -> None:
    first = new_opaque_score_id_v3()
    second = new_opaque_score_id_v3()
    assert first != second
    assert re.fullmatch(r"scorev3_[0-9a-f]{64}", first)
    assert re.fullmatch(r"scorev3_[0-9a-f]{64}", second)


def test_scorer_visible_serialization_has_no_assignment_metadata_fields() -> None:
    task_id = response_task_id("task")
    projection = solver_projection(response_id=task_id)
    capsule = ArmBlindScoreCapsuleV3(
        score_id=new_opaque_score_id_v3(),
        task_id=task_id,
        problem_digest=projection.problem_digest,
        response_text=response(task_id),
        expected_answer=ExactRationalV3(3, 4),
    )
    result = score_arm_blind_capsule_v3(capsule)
    documents = (capsule.to_dict(), result.to_dict())
    forbidden_components = {
        "arm",
        "case",
        "context",
        "gold",
        "gold-source",
        "gold_source",
        "packet",
        "schedule",
        "source",
    }
    for document in documents:
        for key in all_keys(document):
            normalized = key.casefold().replace("-", "_")
            components = set(normalized.split("_"))
            assert normalized not in forbidden_components
            assert components.isdisjoint(forbidden_components)

        serialized = canonical_json(document)
        for private_marker in (
            "case_internal_17",
            "packet_relevant_4",
            "number_theory:hard",
            "schedule_index_9",
        ):
            assert private_marker not in serialized

    assert set(capsule.to_dict()) == {
        "expected_answer",
        "problem_digest",
        "response_text",
        "schema",
        "schema_version",
        "score_id",
        "task_id",
    }
