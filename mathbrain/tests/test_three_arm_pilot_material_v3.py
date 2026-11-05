from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

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
from math_brain.three_arm_pilot_material_v3 import (
    ShamPacketAssignmentV3,
    ShamPacketMatchingV3,
    ShamTargetV3,
    build_sham_packet_matching_v3,
    verify_sham_packet_matching_v3,
)

_MATCHING_SEED = b"sealed-sham-matching-seed-v3" * 2


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _records_digest(records) -> str:
    encoded = canonical_json([item.commitment_record() for item in records]).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _universe() -> tuple[list[ShamTargetV3], list[FrozenPacket]]:
    targets: list[ShamTargetV3] = []
    packets: list[FrozenPacket] = []
    for index in range(24):
        case_id = f"case-{index:02d}"
        packet_id = f"packet-{index:02d}"
        relevance_key = f"case-key:{index:02d}"
        targets.append(
            ShamTargetV3(
                case_id=case_id,
                brain_packet_id=packet_id,
                required_relevance_keys=(relevance_key,),
            )
        )
        packets.append(
            FrozenPacket(
                packet_id=packet_id,
                origin_case_id=case_id,
                text=f"method-card-{index:02d}",
                snapshot_manifest_digest=_digest("snapshot"),
                retrieval_policy_digest=_digest("retrieval-policy"),
                query_digest=_digest(f"query-{index:02d}"),
                format_template_digest=_digest("format"),
                section_shape_digest=_digest("shape"),
                age_status_vector_digest=_digest("age-status"),
                source_digests=(_digest(f"source-{index:02d}"),),
                relevance_keys=(relevance_key,),
                tokenizer_receipts=(
                    TokenizerReceipt(
                        tokenizer_id="pinned-tokenizer",
                        tokenizer_artifact_digest=_digest("tokenizer-artifact"),
                        token_count=5,
                        token_ids_digest=_digest(f"token-ids-{index:02d}"),
                    ),
                ),
            )
        )
    return targets, packets


def _matching():
    targets, packets = _universe()
    return build_sham_packet_matching_v3(
        targets,
        packets,
        matching_seed=_MATCHING_SEED,
    )


def test_matching_is_canonical_bijective_and_replayable() -> None:
    matching = _matching()

    assert len(matching.assignments) == 24
    assert tuple(item.case_id for item in matching.assignments) == tuple(
        sorted(item.case_id for item in matching.assignments)
    )
    assert {item.brain_packet_id for item in matching.assignments} == {
        packet.packet_id for packet in matching.packets
    }
    assert {item.sham_packet_id for item in matching.assignments} == {
        packet.packet_id for packet in matching.packets
    }
    assert all(item.brain_packet_id != item.sham_packet_id for item in matching.assignments)
    assert matching.public_commitment()["matching_digest"] == matching.digest
    verify_sham_packet_matching_v3(
        matching,
        matching_seed=_MATCHING_SEED,
        expected_matching_digest=matching.digest,
    )


def test_matching_is_independent_of_input_order() -> None:
    targets, packets = _universe()
    forward = build_sham_packet_matching_v3(
        targets,
        packets,
        matching_seed=_MATCHING_SEED,
    )
    reversed_inputs = build_sham_packet_matching_v3(
        list(reversed(targets)),
        list(reversed(packets)),
        matching_seed=_MATCHING_SEED,
    )

    assert reversed_inputs == forward
    assert reversed_inputs.digest == forward.digest


def test_matching_output_passes_the_three_arm_planner_binding_checks() -> None:
    matching = _matching()
    assignment_by_case = {item.case_id: item for item in matching.assignments}
    cases = []
    index = 0
    for source in ProblemSource:
        for domain in MathDomain:
            for difficulty in DifficultyBand:
                case_id = f"case-{index:02d}"
                assignment = assignment_by_case[case_id]
                cases.append(
                    ProblemCase(
                        case_id=case_id,
                        source=source,
                        domain=domain,
                        difficulty=difficulty,
                        independence_cluster_id=f"cluster-{index:02d}",
                        problem_text=f"Compute the sealed exact answer for item {index}.",
                        scorer_spec_digest=_digest("exact-rational-scorer"),
                        brain_packet_id=assignment.brain_packet_id,
                        sham_packet_id=assignment.sham_packet_id,
                        required_relevance_keys=(f"case-key:{index:02d}",),
                    )
                )
                index += 1
    config = ThreeArmConfig(
        stage=StudyStage.PILOT,
        runtime_policy_digest=_digest("runtime"),
        prompt_template_digest=_digest("prompt"),
        response_schema_digest=_digest("response-schema"),
        scorer_manifest_digest=_digest("scorer-manifest"),
        chronology_policy_digest=_digest("chronology"),
        scoring_authority_commitment=_digest("scoring-authority"),
        audit_authority_commitment=_digest("audit-authority"),
        analysis_authority_commitment=_digest("analysis-authority"),
    )

    plan = build_three_arm_plan(
        cases,
        matching.packets,
        config=config,
        allocation_seed=b"pilot-allocation-seed" * 2,
    )

    assert len(plan.cases) == 24
    assert len(plan.assignments) == 72


def test_rejects_noncanonical_exact_types_and_wrong_pilot_size() -> None:
    targets, packets = _universe()
    with pytest.raises(ValueError, match="nonempty bounded tuple"):
        ShamTargetV3("case", "packet", ["key"])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="list or tuple"):
        build_sham_packet_matching_v3(
            (target for target in targets),  # type: ignore[arg-type]
            packets,
            matching_seed=_MATCHING_SEED,
        )
    with pytest.raises(ValueError, match="exactly 24 targets"):
        build_sham_packet_matching_v3(
            targets[:-1],
            packets,
            matching_seed=_MATCHING_SEED,
        )
    with pytest.raises(ValueError, match="256 bits"):
        build_sham_packet_matching_v3(
            targets,
            packets,
            matching_seed=bytearray(_MATCHING_SEED),  # type: ignore[arg-type]
        )


def test_record_subclasses_are_forbidden() -> None:
    class TargetSubclass(ShamTargetV3):
        pass

    class AssignmentSubclass(ShamPacketAssignmentV3):
        pass

    class MatchingSubclass(ShamPacketMatchingV3):
        pass

    with pytest.raises(ValueError, match="subclasses are forbidden"):
        TargetSubclass("case", "packet", ("key",))
    with pytest.raises(ValueError, match="subclasses are forbidden"):
        AssignmentSubclass("case", "brain", "sham")
    matching = _matching()
    with pytest.raises(ValueError, match="subclasses are forbidden"):
        MatchingSubclass(
            targets=matching.targets,
            packets=matching.packets,
            assignments=matching.assignments,
            target_universe_digest=matching.target_universe_digest,
            packet_universe_digest=matching.packet_universe_digest,
            matching_seed_commitment=matching.matching_seed_commitment,
            assignment_digest=matching.assignment_digest,
        )


def test_rejects_duplicate_target_and_packet_ids() -> None:
    targets, packets = _universe()
    duplicate_targets = list(targets)
    duplicate_targets[1] = replace(duplicate_targets[1], case_id=targets[0].case_id)
    with pytest.raises(ValueError, match="target case ids must be unique"):
        build_sham_packet_matching_v3(
            duplicate_targets,
            packets,
            matching_seed=_MATCHING_SEED,
        )

    duplicate_packets = list(packets)
    duplicate_packets[1] = replace(duplicate_packets[1], packet_id=packets[0].packet_id)
    with pytest.raises(ValueError, match="packet ids must be unique"):
        build_sham_packet_matching_v3(
            targets,
            duplicate_packets,
            matching_seed=_MATCHING_SEED,
        )


def test_rejects_duplicate_packet_text_digests() -> None:
    targets, packets = _universe()
    duplicate_text_packets = list(packets)
    duplicate_text_packets[1] = replace(duplicate_text_packets[1], text=packets[0].text)

    with pytest.raises(ValueError, match="text digests must be unique"):
        build_sham_packet_matching_v3(
            targets,
            duplicate_text_packets,
            matching_seed=_MATCHING_SEED,
        )


def test_rejects_brain_origin_and_relevance_mismatches() -> None:
    targets, packets = _universe()
    wrong_origin_packets = list(packets)
    wrong_origin_packets[0] = replace(packets[0], origin_case_id="another-case")
    with pytest.raises(ValueError, match="originate from its target case"):
        build_sham_packet_matching_v3(
            targets,
            wrong_origin_packets,
            matching_seed=_MATCHING_SEED,
        )

    wrong_key_targets = list(targets)
    wrong_key_targets[0] = replace(targets[0], required_relevance_keys=("missing-key",))
    with pytest.raises(ValueError, match="omits required relevance keys"):
        build_sham_packet_matching_v3(
            wrong_key_targets,
            packets,
            matching_seed=_MATCHING_SEED,
        )


@pytest.mark.parametrize("field", ["snapshot_manifest_digest", "retrieval_policy_digest"])
def test_rejects_mixed_frozen_packet_provenance(field: str) -> None:
    targets, packets = _universe()
    changed_packets = list(packets)
    changed_packets[0] = replace(packets[0], **{field: _digest(f"different-{field}")})

    with pytest.raises(ValueError, match="share one"):
        build_sham_packet_matching_v3(
            targets,
            changed_packets,
            matching_seed=_MATCHING_SEED,
        )


def test_rejects_repeated_sources_when_they_destroy_all_compatible_edges() -> None:
    targets, packets = _universe()
    shared_source = (_digest("shared-source"),)
    repeated_source_packets = [replace(packet, source_digests=shared_source) for packet in packets]

    with pytest.raises(ValueError, match="no compatible sham packet"):
        build_sham_packet_matching_v3(
            targets,
            repeated_source_packets,
            matching_seed=_MATCHING_SEED,
        )


def test_rejects_an_unmatched_matching_key_partition() -> None:
    targets, packets = _universe()
    mismatched_packets = list(packets)
    receipt = packets[0].tokenizer_receipts[0]
    mismatched_packets[0] = replace(
        packets[0],
        tokenizer_receipts=(replace(receipt, token_count=receipt.token_count + 1),),
    )

    with pytest.raises(ValueError, match="no compatible sham packet"):
        build_sham_packet_matching_v3(
            targets,
            mismatched_packets,
            matching_seed=_MATCHING_SEED,
        )


def test_constructor_rejects_a_repeated_sham_packet_even_with_a_fresh_digest() -> None:
    matching = _matching()
    assignments = list(matching.assignments)
    replacement_index = next(
        index
        for index in range(1, len(assignments))
        if assignments[index].sham_packet_id != assignments[0].brain_packet_id
    )
    assignments[0] = replace(
        assignments[0],
        sham_packet_id=assignments[replacement_index].sham_packet_id,
    )
    assignment_tuple = tuple(assignments)

    with pytest.raises(ValueError, match="use every packet exactly once"):
        replace(
            matching,
            assignments=assignment_tuple,
            assignment_digest=_records_digest(assignment_tuple),
        )


def test_verifier_rejects_an_alternative_valid_matching() -> None:
    matching = _matching()
    assignments = tuple(
        ShamPacketAssignmentV3(
            case_id=f"case-{index:02d}",
            brain_packet_id=f"packet-{index:02d}",
            sham_packet_id=f"packet-{(index + 1) % 24:02d}",
        )
        for index in range(24)
    )
    if assignments == matching.assignments:
        assignments = tuple(
            replace(item, sham_packet_id=f"packet-{(index + 2) % 24:02d}")
            for index, item in enumerate(assignments)
        )
    alternative = replace(
        matching,
        assignments=assignments,
        assignment_digest=_records_digest(assignments),
    )

    with pytest.raises(ValueError, match="does not reconstruct"):
        verify_sham_packet_matching_v3(
            alternative,
            matching_seed=_MATCHING_SEED,
            expected_matching_digest=alternative.digest,
        )


def test_verifier_rejects_wrong_seed_or_external_digest() -> None:
    matching = _matching()
    with pytest.raises(ValueError, match="does not reconstruct"):
        verify_sham_packet_matching_v3(
            matching,
            matching_seed=b"different-matching-seed-material" * 2,
            expected_matching_digest=matching.digest,
        )
    with pytest.raises(ValueError, match="externally expected digest"):
        verify_sham_packet_matching_v3(
            matching,
            matching_seed=_MATCHING_SEED,
            expected_matching_digest=_digest("another-matching"),
        )
