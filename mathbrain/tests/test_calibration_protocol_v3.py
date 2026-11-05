from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError, replace

import pytest

import math_brain
import math_brain.calibration_protocol_v3 as protocol
from math_brain.calibration_plan_v3 import AxisCounts, CalibrationPlan
from math_brain.calibration_protocol_v3 import (
    CalibrationOutcomeBinding,
    PreregistrationCandidate,
    VerifierIdentityBinding,
    bind_calibration_outcomes,
    build_preregistered_corpus,
    verify_calibration_outcome_binding,
)
from math_brain.calibration_v2 import CalibrationCase, OracleReceipt

SEED = "public-calibration-seed-003"


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _plan() -> CalibrationPlan:
    counts = AxisCounts(
        family=4,
        template=4,
        layout=4,
        domain=2,
        fault=2,
        generator_cell=2,
    )
    return CalibrationPlan.build(
        generator_source_digest=_digest("protocol fixture generator source"),
        generator_bundle_digest=_digest("protocol fixture generator bundle"),
        evaluator_protocol_digest=_digest("protocol fixture evaluator"),
        derivation_algorithm_digest=_digest("protocol fixture derivation algorithm"),
        derivation_parameters_digest=_digest("protocol fixture derivation parameters"),
        case_count=288,
        axis_counts=counts,
        eligible_arities=(4, 5),
        position_blind_template_fingerprints=tuple(
            _digest(f"protocol template {index}") for index in range(counts.template)
        ),
        generator_cell_signatures=tuple(
            _digest(f"protocol cell {index}") for index in range(counts.generator_cell)
        ),
        verifier_implementation_digests=tuple(
            sorted(
                (
                    _digest("protocol verifier A"),
                    _digest("protocol verifier B"),
                    _digest("protocol verifier C"),
                    _digest("protocol verifier D"),
                )
            )
        ),
    )


def _letters(value: int) -> str:
    chars: list[str] = []
    current = value
    while True:
        chars.append(chr(ord("a") + current % 26))
        current //= 26
        if current == 0:
            return "".join(reversed(chars))


def _candidates(plan: CalibrationPlan) -> tuple[PreregistrationCandidate, ...]:
    values: list[PreregistrationCandidate] = []
    identities = _identities(plan)
    coordinate_count = (
        plan.axis_counts.family * plan.axis_counts.template * sum(plan.eligible_arities)
    )
    repetitions = plan.case_count // coordinate_count
    counter = 0
    for family_index in range(plan.axis_counts.family):
        for template_index in range(plan.axis_counts.template):
            for arity in plan.eligible_arities:
                for fault_rank in range(arity):
                    for repetition in range(repetitions):
                        combination = (
                            family_index + 2 * template_index + fault_rank + repetition + arity
                        ) % (plan.axis_counts.domain * plan.axis_counts.fault)
                        domain_index = combination // plan.axis_counts.fault
                        fault_index = combination % plan.axis_counts.fault
                        cell_index = (
                            family_index + template_index + fault_rank + repetition + arity
                        ) % plan.axis_counts.generator_cell
                        layout_index = combination % plan.axis_counts.layout
                        eligible = tuple(range(2, 2 + arity))
                        token = _letters(counter)
                        trace = (
                            f"Initialize token_{token} in template_{template_index}.",
                            f"Set state_a_{token} by the checked recurrence.",
                            f"Set state_b_{token} by the checked recurrence.",
                            f"Set state_c_{token} by the checked recurrence.",
                            f"Set state_d_{token} by the checked recurrence.",
                            f"Set state_e_{token} by the checked recurrence.",
                            f"Cross-check token_{token} against the invariant.",
                            f"Conclude the requested value for token_{token}.",
                        )
                        values.append(
                            PreregistrationCandidate(
                                stable_case_id=f"case-{counter:04d}",
                                generator_counter=counter,
                                family_index=family_index,
                                family_label=f"family-{family_index}",
                                template_index=template_index,
                                template_label=f"template-{template_index}",
                                layout_index=layout_index,
                                layout_label=f"layout-{layout_index}",
                                domain_index=domain_index,
                                domain_label=f"domain-{domain_index}",
                                fault_index=fault_index,
                                fault_label=f"fault-{fault_index}",
                                generator_cell_index=cell_index,
                                generator_cell_label=f"cell-{cell_index}",
                                template_fingerprint=(
                                    plan.position_blind_template_fingerprints[template_index]
                                ),
                                generator_cell_signature=(
                                    plan.generator_cell_signatures[cell_index]
                                ),
                                verifier_implementation_digests=(
                                    plan.verifier_implementation_digests
                                ),
                                verifier_identities=identities,
                                executed_verifier_implementation_digests=(
                                    plan.verifier_implementation_digests[:2]
                                    if domain_index == 0
                                    else plan.verifier_implementation_digests[2:]
                                ),
                                semantic_program_digest=_digest(f"program {counter}"),
                                prompt=(
                                    f"Audit token_{token} using shared template_"
                                    f"{template_index} and identify the first invalid line."
                                ),
                                faulty_trace=trace,
                                eligible_fault_lines=eligible,
                                fault_line=eligible[fault_rank],
                            )
                        )
                        counter += 1
    assert counter == plan.case_count
    return tuple(values)


def _identities(plan: CalibrationPlan) -> tuple[VerifierIdentityBinding, ...]:
    return tuple(
        VerifierIdentityBinding(
            implementation_digest=digest,
            verifier_id=f"verifier-{index}",
        )
        for index, digest in enumerate(plan.verifier_implementation_digests)
    )


def _outcomes(
    candidates: tuple[PreregistrationCandidate, ...],
    identities: tuple[VerifierIdentityBinding, ...],
) -> tuple[CalibrationCase, ...]:
    cases: list[CalibrationCase] = []
    for candidate in candidates:
        correct = candidate.generator_counter % 5 < 3
        binding = CalibrationCase.binding_digest_for(
            case_id=candidate.stable_case_id,
            family=candidate.family_label,
            template_id=candidate.template_label,
            generator_cell=candidate.generator_cell_label,
            domain=candidate.domain_label,
            fault_kind=candidate.fault_label,
            prompt=candidate.prompt,
            faulty_trace=candidate.faulty_trace,
            eligible_fault_lines=candidate.eligible_fault_lines,
            fault_line=candidate.fault_line,
            brain_off_correct=correct,
        )
        executed = set(candidate.executed_verifier_implementation_digests)
        receipts = tuple(
            OracleReceipt.issue(
                case_binding_digest=binding,
                verifier_id=identity.verifier_id,
                first_invalid_line=candidate.fault_line,
            )
            for identity in identities
            if identity.implementation_digest in executed
        )
        cases.append(
            CalibrationCase.build(
                case_id=candidate.stable_case_id,
                family=candidate.family_label,
                template_id=candidate.template_label,
                generator_cell=candidate.generator_cell_label,
                domain=candidate.domain_label,
                fault_kind=candidate.fault_label,
                prompt=candidate.prompt,
                faulty_trace=candidate.faulty_trace,
                eligible_fault_lines=candidate.eligible_fault_lines,
                fault_line=candidate.fault_line,
                brain_off_correct=correct,
                oracles=receipts,
            )
        )
    return tuple(cases)


@pytest.fixture(scope="module")
def fixture() -> tuple[
    CalibrationPlan,
    tuple[PreregistrationCandidate, ...],
    tuple[VerifierIdentityBinding, ...],
    tuple[CalibrationCase, ...],
]:
    plan = _plan()
    candidates = _candidates(plan)
    identities = _identities(plan)
    return plan, candidates, identities, _outcomes(candidates, identities)


def test_build_derives_every_manifest_digest_from_exact_rendering(fixture: tuple) -> None:
    plan, candidates, _, _ = fixture
    corpus = build_preregistered_corpus(plan, SEED, candidates)

    corpus.validate_against(plan)
    assert tuple(row.selection_ticket for row in corpus.manifest.rows) == tuple(
        sorted(row.selection_ticket for row in corpus.manifest.rows)
    )
    for row, binding, candidate in zip(
        corpus.manifest.rows,
        corpus.materialized_bindings,
        corpus.candidates,
        strict=True,
    ):
        assert row.solver_view_digest == candidate.solver_view_digest()
        assert row.outcome_free_content_digest == candidate.outcome_free_content_digest()
        assert row.coordinate_digest == binding.coordinate_digest()
        assert row.stable_case_id == candidate.stable_case_id


def test_solver_view_and_preoutcome_payload_have_no_observed_outcome_field(
    fixture: tuple,
) -> None:
    _, candidates, _, _ = fixture
    candidate = candidates[0]
    serialized = repr((candidate.solver_view_payload(), candidate.outcome_free_payload())).lower()

    for forbidden in (
        "brain_off",
        "correct",
        "prediction",
        "response",
        "receipt",
        "oracle",
    ):
        assert forbidden not in serialized
    assert "fault_line" not in candidate.solver_view_payload()
    assert "fault_label" not in candidate.solver_view_payload()
    assert "verifier" not in repr(candidate.solver_view_payload()).lower()


def test_candidate_tampering_is_revalidated_at_consumption(fixture: tuple) -> None:
    plan = _plan()
    candidates = _candidates(plan)
    corpus = build_preregistered_corpus(plan, SEED, candidates)
    victim = corpus.candidates[0]
    object.__setattr__(victim, "generator_counter", True)

    with pytest.raises(ValueError, match="generator counter must be an integer"):
        corpus.validate_against(plan)


def test_candidate_and_corpus_are_immutable(fixture: tuple) -> None:
    plan, candidates, _, _ = fixture
    corpus = build_preregistered_corpus(plan, SEED, candidates)

    with pytest.raises(FrozenInstanceError):
        corpus.candidates = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        corpus.candidates[0].prompt = "changed"  # type: ignore[misc]


def test_one_over_candidate_cap_fails_without_exhausting_iterator() -> None:
    plan = _plan()
    candidate = _candidates(plan)[0]
    seen = 0

    def unbounded():
        nonlocal seen
        while True:
            seen += 1
            yield candidate

    with pytest.raises(ValueError, match="exceeds the 400-item budget"):
        build_preregistered_corpus(plan, SEED, unbounded())
    assert seen == 401


def test_outcome_binding_links_exact_v2_input_to_v3_commitment(fixture: tuple) -> None:
    plan, candidates, identities, outcomes = fixture
    corpus = build_preregistered_corpus(plan, SEED, candidates)

    binding = bind_calibration_outcomes(plan, corpus, reversed(outcomes), identities)
    repeated = bind_calibration_outcomes(plan, corpus, outcomes, identities)

    assert type(binding) is CalibrationOutcomeBinding
    assert binding.content_digest == repeated.content_digest
    assert binding.plan_digest == plan.digest()
    assert binding.manifest_digest == corpus.manifest.digest()
    assert binding.preregistration_commitment == corpus.commitment
    assert tuple(link.case_index for link in binding.case_links) == tuple(range(plan.case_count))
    assert binding.canonical_json().endswith("}")
    assert CalibrationOutcomeBinding.from_json(binding.canonical_json()) == binding
    verify_calibration_outcome_binding(
        binding,
        plan,
        corpus,
        outcomes,
        identities,
    )


def test_each_case_uses_only_its_preregistered_family_verifier_pair(
    fixture: tuple,
) -> None:
    plan, candidates, identities, outcomes = fixture
    corpus = build_preregistered_corpus(plan, SEED, candidates)
    bind_calibration_outcomes(plan, corpus, outcomes, identities)
    outcomes_by_id = {case.case_id: case for case in outcomes}
    identity_by_digest = {
        identity.implementation_digest: identity.verifier_id for identity in identities
    }

    for candidate in corpus.candidates:
        expected = {
            identity_by_digest[digest]
            for digest in candidate.executed_verifier_implementation_digests
        }
        assert {
            receipt.verifier_id for receipt in outcomes_by_id[candidate.stable_case_id].oracles
        } == (expected)
        assert len(expected) == 2


def test_outcome_binding_loader_rejects_duplicates_and_digest_tampering(
    fixture: tuple,
) -> None:
    plan, candidates, identities, outcomes = fixture
    corpus = build_preregistered_corpus(plan, SEED, candidates)
    binding = bind_calibration_outcomes(plan, corpus, outcomes, identities)
    raw = binding.canonical_json()
    duplicate = raw.replace(
        '"schema":',
        '"schema":"duplicate","schema":',
        1,
    )

    with pytest.raises(ValueError, match="duplicate JSON object key"):
        CalibrationOutcomeBinding.from_json(duplicate)

    changed = binding.to_dict()
    changed["calibration_input_digest"] = _digest("substituted input")
    with pytest.raises(ValueError, match="content digest does not match"):
        CalibrationOutcomeBinding.from_dict(changed)


def test_outcome_binding_verification_revalidates_object_tampering(fixture: tuple) -> None:
    plan, candidates, identities, outcomes = fixture
    corpus = build_preregistered_corpus(plan, SEED, candidates)
    binding = bind_calibration_outcomes(plan, corpus, outcomes, identities)
    object.__setattr__(binding.case_links[0], "case_index", True)

    with pytest.raises(ValueError, match="case index must be an integer"):
        verify_calibration_outcome_binding(
            binding,
            plan,
            corpus,
            outcomes,
            identities,
        )


def test_rendered_trace_substitution_fails_even_with_fresh_receipts(fixture: tuple) -> None:
    plan, candidates, identities, outcomes = fixture
    corpus = build_preregistered_corpus(plan, SEED, candidates)
    original = outcomes[0]
    original_candidate = next(
        candidate for candidate in candidates if candidate.stable_case_id == original.case_id
    )
    executed = set(original_candidate.executed_verifier_implementation_digests)
    changed_trace = ("Substituted rendering.", *original.faulty_trace[1:])
    digest = CalibrationCase.binding_digest_for(
        case_id=original.case_id,
        family=original.family,
        template_id=original.template_id,
        generator_cell=original.generator_cell,
        domain=original.domain,
        fault_kind=original.fault_kind,
        prompt=original.prompt,
        faulty_trace=changed_trace,
        eligible_fault_lines=original.eligible_fault_lines,
        fault_line=original.fault_line,
        brain_off_correct=original.brain_off_correct,
    )
    substituted = CalibrationCase.build(
        case_id=original.case_id,
        family=original.family,
        template_id=original.template_id,
        generator_cell=original.generator_cell,
        domain=original.domain,
        fault_kind=original.fault_kind,
        prompt=original.prompt,
        faulty_trace=changed_trace,
        eligible_fault_lines=original.eligible_fault_lines,
        fault_line=original.fault_line,
        brain_off_correct=original.brain_off_correct,
        oracles=tuple(
            OracleReceipt.issue(
                case_binding_digest=digest,
                verifier_id=identity.verifier_id,
                first_invalid_line=original.fault_line,
            )
            for identity in identities
            if identity.implementation_digest in executed
        ),
    )
    changed = (substituted, *outcomes[1:])

    with pytest.raises(ValueError, match="faulty trace does not match preregistration"):
        bind_calibration_outcomes(plan, corpus, changed, identities)


def test_verifier_digest_to_receipt_identity_mapping_is_exact(fixture: tuple) -> None:
    plan, candidates, identities, outcomes = fixture
    corpus = build_preregistered_corpus(plan, SEED, candidates)
    changed = (
        replace(identities[0], implementation_digest=_digest("unreviewed verifier")),
        identities[1],
    )

    with pytest.raises(ValueError, match="exact preregistered implementations"):
        bind_calibration_outcomes(plan, corpus, outcomes, changed)

    swapped_ids = (
        replace(identities[0], verifier_id=identities[1].verifier_id),
        replace(identities[1], verifier_id=identities[0].verifier_id),
        *identities[2:],
    )
    with pytest.raises(ValueError, match="does not match preregistration"):
        bind_calibration_outcomes(plan, corpus, outcomes, swapped_ids)


def test_postoutcome_correctness_may_change_only_with_a_new_bound_v2_digest(
    fixture: tuple,
) -> None:
    plan, candidates, identities, outcomes = fixture
    corpus = build_preregistered_corpus(plan, SEED, candidates)
    first = bind_calibration_outcomes(plan, corpus, outcomes, identities)

    original = outcomes[0]
    original_candidate = next(
        candidate for candidate in candidates if candidate.stable_case_id == original.case_id
    )
    executed = set(original_candidate.executed_verifier_implementation_digests)
    changed_correct = not original.brain_off_correct
    digest = CalibrationCase.binding_digest_for(
        case_id=original.case_id,
        family=original.family,
        template_id=original.template_id,
        generator_cell=original.generator_cell,
        domain=original.domain,
        fault_kind=original.fault_kind,
        prompt=original.prompt,
        faulty_trace=original.faulty_trace,
        eligible_fault_lines=original.eligible_fault_lines,
        fault_line=original.fault_line,
        brain_off_correct=changed_correct,
    )
    changed_case = CalibrationCase.build(
        case_id=original.case_id,
        family=original.family,
        template_id=original.template_id,
        generator_cell=original.generator_cell,
        domain=original.domain,
        fault_kind=original.fault_kind,
        prompt=original.prompt,
        faulty_trace=original.faulty_trace,
        eligible_fault_lines=original.eligible_fault_lines,
        fault_line=original.fault_line,
        brain_off_correct=changed_correct,
        oracles=tuple(
            OracleReceipt.issue(
                case_binding_digest=digest,
                verifier_id=identity.verifier_id,
                first_invalid_line=original.fault_line,
            )
            for identity in identities
            if identity.implementation_digest in executed
        ),
    )
    second = bind_calibration_outcomes(
        plan,
        corpus,
        (changed_case, *outcomes[1:]),
        identities,
    )

    assert second.preregistration_commitment == first.preregistration_commitment
    assert second.calibration_input_digest != first.calibration_input_digest
    assert second.calibration_case_set_digest != first.calibration_case_set_digest
    assert second.content_digest != first.content_digest


def test_bool_and_collection_subclasses_are_rejected(fixture: tuple) -> None:
    _, candidates, _, _ = fixture
    original = candidates[0]

    with pytest.raises(ValueError, match="generator counter must be an integer"):
        replace(original, generator_counter=True)

    class TupleSubclass(tuple):
        pass

    with pytest.raises(ValueError, match="eligible lines"):
        replace(original, eligible_fault_lines=TupleSubclass(original.eligible_fault_lines))


def test_executed_verifier_subset_is_exact_bounded_and_precommitted() -> None:
    plan = _plan()
    candidates = _candidates(plan)
    original = candidates[0]

    with pytest.raises(ValueError, match="at least two executed verifiers"):
        replace(
            original,
            executed_verifier_implementation_digests=(
                original.executed_verifier_implementation_digests[0],
            ),
        )
    with pytest.raises(ValueError, match="outside the reviewed plan set"):
        replace(
            original,
            executed_verifier_implementation_digests=tuple(
                sorted(
                    (
                        plan.verifier_implementation_digests[0],
                        _digest("unreviewed executed verifier"),
                    )
                )
            ),
        )

    corpus = build_preregistered_corpus(plan, SEED, candidates)
    object.__setattr__(
        corpus.candidates[0].verifier_identities[0],
        "verifier_id",
        "post-commit-substitution",
    )
    with pytest.raises(ValueError, match="does not match exact rendered candidate"):
        corpus.validate_against(plan)


def test_corpus_rejects_mixed_preregistered_verifier_identity_maps() -> None:
    plan = _plan()
    candidates = _candidates(plan)
    original = candidates[0]
    changed_identities = (
        replace(
            original.verifier_identities[0],
            verifier_id=f"changed-{original.verifier_identities[0].verifier_id}",
        ),
        *original.verifier_identities[1:],
    )
    changed = replace(original, verifier_identities=changed_identities)

    with pytest.raises(ValueError, match="one verifier identity mapping"):
        build_preregistered_corpus(plan, SEED, (changed, *candidates[1:]))


def test_outcome_binding_revalidates_nested_identity_and_link_records(
    fixture: tuple,
) -> None:
    plan, candidates, identities, outcomes = fixture
    corpus = build_preregistered_corpus(plan, SEED, candidates)

    identity_binding = bind_calibration_outcomes(plan, corpus, outcomes, identities)
    object.__setattr__(identity_binding.verifier_identities[0], "verifier_id", "")
    with pytest.raises(ValueError, match="verifier id"):
        replace(identity_binding)

    serialized_binding = bind_calibration_outcomes(plan, corpus, outcomes, identities)
    object.__setattr__(
        serialized_binding.verifier_identities[0],
        "verifier_id",
        "valid-but-post-construction-change",
    )
    with pytest.raises(ValueError, match="content changed after construction"):
        serialized_binding.canonical_json()

    link_binding = bind_calibration_outcomes(plan, corpus, outcomes, identities)
    object.__setattr__(link_binding.case_links[0], "stable_case_id", "")
    with pytest.raises(ValueError, match="stable case id"):
        replace(link_binding)


def test_protocol_types_forbid_subclasses(fixture: tuple) -> None:
    _, candidates, _, _ = fixture

    class CandidateSubclass(PreregistrationCandidate):
        pass

    values = {name: getattr(candidates[0], name) for name in candidates[0].__dataclass_fields__}
    with pytest.raises(ValueError, match="subclasses are forbidden"):
        CandidateSubclass(**values)


def test_protocol_schema_versions_are_pinned() -> None:
    assert protocol.SOLVER_VIEW_SCHEMA_VERSION == 1
    assert protocol.OUTCOME_FREE_CASE_SCHEMA_VERSION == 2
    assert protocol.OUTCOME_BINDING_SCHEMA_VERSION == 2


def test_protocol_types_are_exported_from_package_api() -> None:
    assert math_brain.CalibrationOutcomeBinding is CalibrationOutcomeBinding
    assert math_brain.PreregistrationCandidate is PreregistrationCandidate
    assert math_brain.VerifierIdentityBinding is VerifierIdentityBinding
    assert math_brain.bind_calibration_outcomes is bind_calibration_outcomes
    assert math_brain.build_preregistered_corpus is build_preregistered_corpus
    assert math_brain.verify_calibration_outcome_binding is verify_calibration_outcome_binding
