from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from math_brain.calibration_v2 import (
    BaselineKind,
    CalibrationBundle,
    CalibrationCase,
    CalibrationConfig,
    CalibrationFreeze,
    CalibrationReport,
    DEFAULT_CONFIG,
    DifficultyBand,
    FreezeState,
    GateResult,
    OracleReceipt,
    calibration_input_document,
    case_set_digest,
    generate_calibration_report,
    load_calibration_input,
    load_calibration_bundle,
    load_calibration_report,
    publish_calibration_bundle,
    _baseline_metric,
)
from math_brain.cli import main
from math_brain.models import canonical_json


def _certified_case(
    *,
    case_id: str,
    family: str,
    template_id: str,
    generator_cell: str,
    domain: str,
    fault_kind: str,
    prompt: str,
    faulty_trace: Sequence[str],
    fault_line: int,
    brain_off_correct: bool,
    eligible_fault_lines: Sequence[int] | None = None,
) -> CalibrationCase:
    eligible = tuple(
        range(2, len(faulty_trace))
        if eligible_fault_lines is None
        else eligible_fault_lines
    )
    values = {
        "case_id": case_id,
        "family": family,
        "template_id": template_id,
        "generator_cell": generator_cell,
        "domain": domain,
        "fault_kind": fault_kind,
        "prompt": prompt,
        "faulty_trace": tuple(faulty_trace),
        "eligible_fault_lines": eligible,
        "fault_line": fault_line,
        "brain_off_correct": brain_off_correct,
    }
    binding = CalibrationCase.binding_digest_for(**values)
    receipts = (
        OracleReceipt.issue(
            case_binding_digest=binding,
            verifier_id="symbolic-replay-oracle-v1",
            first_invalid_line=fault_line,
        ),
        OracleReceipt.issue(
            case_binding_digest=binding,
            verifier_id="independent-family-oracle-v1",
            first_invalid_line=fault_line,
        ),
    )
    return CalibrationCase.build(**values, oracles=receipts)


def _cases(
    *,
    family_count: int = 4,
    template_count: int = 5,
    repetitions: int = 12,
    fixed_position: int | None = None,
    family_fixed_position: bool = False,
    family_template_fixed_position: bool = False,
    shortcut: bool = False,
    correct: bool | None = None,
    implausible_repetitions: int = 0,
    mechanism_leak: bool = False,
    partial_mechanism_leak: bool = False,
    prompt_position_leak: bool = False,
    one_bad_difficulty_cell: bool = False,
) -> tuple[CalibrationCase, ...]:
    result: list[CalibrationCase] = []
    for family in range(family_count):
        for template in range(template_count):
            for repetition in range(repetitions):
                index = (
                    family * template_count * repetitions
                    + template * repetitions
                    + repetition
                )
                fault_line = (
                    fixed_position
                    or (2 + family % 4 if family_fixed_position else None)
                    or (
                        2 + (family + template) % 4
                        if family_template_fixed_position
                        else None
                    )
                    or 2 + (family + 2 * template + repetition) % 4
                )
                domain = (
                    (
                        f"domain{repetition % 2}"
                        if repetition < 8
                        else f"domain{1 - repetition % 2}"
                    )
                    if mechanism_leak
                    else (
                        f"domain{(repetition // 4) % 2}"
                        if partial_mechanism_leak
                        else f"domain{(repetition // 2) % 2}"
                    )
                )
                fault_kind = f"fault{repetition % (4 if partial_mechanism_leak else 2)}"
                trace = []
                for line_number in range(1, 7):
                    marker = " rareleak" if shortcut and line_number == fault_line else ""
                    if repetition < implausible_repetitions and line_number == fault_line:
                        marker += " obviously wrong"
                    trace.append(
                        f"Step {line_number}: symbol{family}_{template} value "
                        f"{1000 + repetition * 17 + line_number * 3} is derived{marker}"
                    )
                brain_off_correct = (
                    repetition < (2 * repetitions) // 3
                    if correct is None
                    else correct
                )
                if one_bad_difficulty_cell:
                    brain_off_correct = (
                        True
                        if domain == "domain0" and fault_kind == "fault0"
                        else repetition < (2 * repetitions) // 3
                    )
                result.append(
                    _certified_case(
                        case_id=f"C{index:04d}",
                        family=f"family{family}",
                        template_id=f"template{template}",
                        generator_cell=(
                            f"cohort{(family + template + repetition // 4) % 2}"
                        ),
                        domain=domain,
                        fault_kind=fault_kind,
                        prompt=(
                            f"Establish family{family} template{template} "
                            f"instance {100 + repetition}"
                            + (
                                f" using faultslot{fault_line}"
                                if prompt_position_leak
                                else ""
                            )
                            + (
                                f" mechsignal{fault_kind}"
                                if partial_mechanism_leak
                                and repetition % 4 < 2
                                else ""
                            )
                        ),
                        faulty_trace=trace,
                        fault_line=fault_line,
                        brain_off_correct=brain_off_correct,
                    )
                )
    return tuple(result)


def _recertify(case: CalibrationCase, **changes: object) -> CalibrationCase:
    values = case.binding_payload()
    values.update(changes)
    return _certified_case(**values)


@pytest.fixture(scope="module")
def passing_bundle() -> tuple[
    tuple[CalibrationCase, ...], CalibrationFreeze, CalibrationReport
]:
    cases = _cases()
    state = CalibrationFreeze.draft().commit_seed("public-calibration-seed").bind_cases(cases)
    report = generate_calibration_report(
        cases,
        state=state,
        seed="public-calibration-seed",
    )
    return cases, state, report


def test_public_calibration_hits_all_target_gates_and_strata(
    passing_bundle: tuple[
        tuple[CalibrationCase, ...], CalibrationFreeze, CalibrationReport
    ],
) -> None:
    _, _, report = passing_bundle
    assert report.accepted
    assert report.difficulty.accuracy == 0.666667
    assert report.difficulty.band is DifficultyBand.TARGET
    assert report.fault_positions.normalized_entropy == 1.0
    assert report.fault_positions.maximum_position_share == 0.25
    assert report.fault_positions.uniform_total_variation == 0.0
    assert report.fault_positions.minimum_cases_per_eligible_rank == 60.0
    assert report.plausibility_rate == 1.0
    assert {stratum.stratum_kind for stratum in report.strata} == {
        "domain_fault",
        "family",
        "family_template",
        "generator_cell",
        "template",
    }
    assert len(report.strata) == 35
    assert all(stratum.brain_off_accuracy == 0.666667 for stratum in report.strata)
    assert all(
        stratum.maximum_fault_position_share == 0.25
        for stratum in report.strata
    )
    assert {metric.name for metric in report.baselines} == {
        f"{kind.value}__{suffix}"
        for kind in BaselineKind
        for suffix in ("lofo", "within_family")
    }
    assert {metric.split for metric in report.baselines} == {
        "leave-family-out-v3",
        "within-family-leave-template-out-v2",
    }
    assert all(metric.excess_over_chance <= 0.05 for metric in report.baselines)
    assert all(
        metric.worst_family_excess_over_chance <= 0.10
        for metric in report.baselines
    )
    assert all(
        metric.total_cases == report.case_count
        and metric.top1_accuracy
        == round(metric.correct_cases / metric.total_cases, 6)
        for metric in report.baselines
    )
    assert report.domain_fault_cross_prediction.name == "domain_topic_mechanism_prediction"
    assert report.domain_fault_cross_prediction.excess_over_chance <= 0.10
    assert (
        report.domain_fault_cross_prediction.worst_family_excess_over_chance
        <= 0.10
    )


def test_freeze_ready_calibration_requires_preregistered_case_count() -> None:
    cases = _cases()
    draft = CalibrationFreeze.draft().commit_seed("case-count-seed")
    with pytest.raises(ValueError, match="between 200 and 400"):
        draft.bind_cases(cases[:199])
    oversized = _cases(family_count=21)[:401]
    with pytest.raises(ValueError, match="between 200 and 400"):
        draft.bind_cases(oversized)
    with pytest.raises(ValueError, match="between 200 and 400"):
        calibration_input_document(cases[:199])


def test_freeze_lifecycle_rejects_relaxed_threshold_authority() -> None:
    relaxed = CalibrationConfig(
        maximum_baseline_excess_over_chance=1.0,
        maximum_worst_family_baseline_excess_over_chance=1.0,
    )
    with pytest.raises(ValueError, match="cannot relax"):
        CalibrationFreeze.draft(relaxed)
    payload = CalibrationFreeze.draft().to_dict()
    payload["config_digest"] = relaxed.digest()
    with pytest.raises(ValueError, match="sealed calibration-v2 config"):
        CalibrationFreeze.from_dict(payload)


def test_report_is_deterministic_order_independent_and_contains_no_answers(
    passing_bundle: tuple[
        tuple[CalibrationCase, ...], CalibrationFreeze, CalibrationReport
    ],
) -> None:
    cases, state, first = passing_bundle
    second = generate_calibration_report(
        reversed(cases),
        state=state,
        seed="public-calibration-seed",
    )
    assert first.canonical_json() == second.canonical_json()
    assert first.content_digest == second.content_digest
    raw = first.canonical_json()
    for forbidden in (
        '"answer"',
        '"case_id"',
        '"fault_line"',
        '"faulty_trace"',
        '"oracle"',
        '"oracles"',
        '"prompt"',
        '"solution"',
        '"trace"',
    ):
        assert forbidden not in raw
    assert cases[0].prompt not in raw
    assert cases[0].case_id not in raw
    assert load_calibration_report(raw).canonical_json() == raw
    assert canonical_json(calibration_input_document(cases)) == canonical_json(
        calibration_input_document(reversed(cases))
    )
    tampered = json.loads(raw)
    tampered["accepted"] = False
    with pytest.raises(ValueError, match="accepted flag"):
        load_calibration_report(canonical_json(tampered))


def test_input_digest_order_and_canonical_bytes_are_enforced() -> None:
    cases = _cases()
    document = calibration_input_document(cases)
    raw = canonical_json(document)
    config, loaded = load_calibration_input(raw)
    assert config == CalibrationConfig()
    assert loaded == tuple(
        sorted(cases, key=lambda case: (case.case_id, case.content_digest))
    )
    reordered = json.loads(raw)
    reordered["cases"].reverse()
    with pytest.raises(ValueError, match="digest does not match"):
        load_calibration_input(canonical_json(reordered))
    with pytest.raises(ValueError, match="canonical UTF-8 JSON bytes"):
        load_calibration_input(json.dumps(document, ensure_ascii=False, indent=2))


def test_case_iterables_stop_at_the_sealed_bound() -> None:
    source = _cases()[0]
    yielded = 0

    def unbounded_cases():
        nonlocal yielded
        while True:
            yielded += 1
            yield source

    with pytest.raises(ValueError, match="between 200 and 400"):
        case_set_digest(unbounded_cases())
    assert yielded == 401


def test_python_subclasses_cannot_cross_calibration_boundaries() -> None:
    source = _cases()[0]

    class TextSubclass(str):
        def encode(self, *_args, **_kwargs):
            return b"short"

    with pytest.raises(ValueError, match="plain string"):
        replace(source, prompt=TextSubclass("x" * 100_000))

    class CaseSubclass(CalibrationCase):
        pass

    subclass_case = CaseSubclass(
        case_id=source.case_id,
        family=source.family,
        template_id=source.template_id,
        generator_cell=source.generator_cell,
        domain=source.domain,
        fault_kind=source.fault_kind,
        prompt=source.prompt,
        faulty_trace=source.faulty_trace,
        eligible_fault_lines=source.eligible_fault_lines,
        fault_line=source.fault_line,
        brain_off_correct=source.brain_off_correct,
        oracles=source.oracles,
    )
    with pytest.raises(ValueError, match="invalid record"):
        case_set_digest((subclass_case,))

    class ConfigSubclass(CalibrationConfig):
        pass

    with pytest.raises(ValueError, match="validated calibration config"):
        CalibrationFreeze.draft(ConfigSubclass())


def test_non_nfc_case_text_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unicode NFC"):
        replace(_cases()[0], prompt="Prove Cafe\u0301 is represented canonically")


def test_report_loader_rejects_relaxed_config_without_later_verifier() -> None:
    cases = _cases()
    state = CalibrationFreeze.draft().commit_seed("sealed-report-seed").bind_cases(cases)
    report = generate_calibration_report(cases, state=state, seed="sealed-report-seed")
    payload = json.loads(report.canonical_json())
    relaxed = CalibrationConfig(
        maximum_baseline_excess_over_chance=1.0,
        maximum_worst_family_baseline_excess_over_chance=1.0,
    )
    payload["config"] = relaxed.to_dict()
    payload["config_digest"] = relaxed.digest()
    with pytest.raises(ValueError, match="sealed calibration-v2 config"):
        load_calibration_report(canonical_json(payload))


def test_oracle_receipts_are_a_canonical_set_not_an_ordered_digest_input() -> None:
    case = _cases()[0]
    reordered = replace(case, oracles=tuple(reversed(case.oracles)))
    assert reordered.oracles == case.oracles
    assert reordered.content_digest == case.content_digest


def test_surface_shortcut_leakage_fails_top1_chance_excess_gate() -> None:
    cases = _cases(shortcut=True)
    state = CalibrationFreeze.draft().commit_seed("leak-seed").bind_cases(cases)
    report = generate_calibration_report(cases, state=state, seed="leak-seed")
    bag = max(
        (
            metric
            for metric in report.baselines
            if metric.name.startswith("bag_char__")
        ),
        key=lambda metric: metric.excess_over_chance,
    )
    assert bag.top1_accuracy > 0.85
    assert bag.excess_over_chance > 0.60
    assert not report.accepted
    assert not next(
        gate
        for gate in report.gates
        if gate.name == f"{bag.name}_excess_over_chance"
    ).passed


def test_localization_chance_is_case_weighted_under_variable_arity() -> None:
    cases: list[CalibrationCase] = []
    for index, case in enumerate(_cases()):
        if index % 2:
            cases.append(case)
            continue
        trace = case.faulty_trace + tuple(
            f"Step {line}: retained auxiliary {case.case_id}-{line}"
            for line in range(7, 11)
        )
        cases.append(
            _recertify(
                case,
                faulty_trace=trace,
                eligible_fault_lines=tuple(range(2, 10)),
                fault_line=2 + (index // 2) % 8,
            )
        )
    values = tuple(cases)
    state = CalibrationFreeze.draft().commit_seed("variable-arity-seed").bind_cases(values)
    report = generate_calibration_report(values, state=state, seed="variable-arity-seed")
    assert all(metric.total_cases == len(values) for metric in report.baselines)
    assert all(metric.chance_accuracy == 0.1875 for metric in report.baselines)
    assert all(
        metric.excess_over_chance
        == round(max(0.0, metric.top1_accuracy - metric.chance_accuracy), 6)
        for metric in report.baselines
    )


def test_top1_family_gate_catches_leak_old_line_metric_would_pass() -> None:
    metric = _baseline_metric(
        "counterexample",
        "synthetic-disjoint-split",
        {
            "family-a": ((True, 5), (False, 5), (False, 5)),
            "family-b": ((False, 4),),
        },
    )
    old_pooled_balanced_accuracy = 0.5 * (1 / 4 + 12 / 15)
    old_worst_family_balanced_accuracy = 0.5 * (1 / 3 + 10 / 12)
    assert old_pooled_balanced_accuracy <= 0.55
    assert old_worst_family_balanced_accuracy <= 0.60
    assert metric.chance_accuracy == 0.2125
    assert metric.top1_accuracy == 0.25
    assert metric.excess_over_chance == 0.0375
    assert metric.excess_over_chance <= DEFAULT_CONFIG.maximum_baseline_excess_over_chance
    assert metric.worst_family_excess_over_chance == 0.133333
    assert (
        metric.worst_family_excess_over_chance
        > DEFAULT_CONFIG.maximum_worst_family_baseline_excess_over_chance
    )


def test_report_rejects_baselines_from_a_different_structural_population(
    passing_bundle: tuple[
        tuple[CalibrationCase, ...], CalibrationFreeze, CalibrationReport
    ],
) -> None:
    _, _, report = passing_bundle
    original = report.baselines[0]
    too_small = replace(
        _baseline_metric(
            original.name,
            original.split,
            {"family-a": ((False, 4),), "family-b": ((False, 4),)},
        ),
        name=original.name,
        split=original.split,
    )
    with pytest.raises(ValueError, match="report case population"):
        replace(report, baselines=(too_small, *report.baselines[1:]))

    wrong_arities = _baseline_metric(
        original.name,
        original.split,
        {
            f"family-{family}": tuple((False, 5) for _ in range(60))
            for family in range(4)
        },
    )
    with pytest.raises(ValueError, match="one structural case population"):
        replace(report, baselines=(wrong_arities, *report.baselines[1:]))


def test_prompt_encoded_position_fails_prompt_only_localization_gate() -> None:
    cases = _cases(prompt_position_leak=True)
    state = CalibrationFreeze.draft().commit_seed("prompt-leak-seed").bind_cases(cases)
    report = generate_calibration_report(cases, state=state, seed="prompt-leak-seed")
    prompt_only = next(
        metric
        for metric in report.baselines
        if metric.name == "prompt_only__lofo"
    )
    assert prompt_only.top1_accuracy > 0.85
    assert prompt_only.excess_over_chance > 0.60
    assert not report.accepted


def test_gate_manifest_cannot_be_replaced_with_a_substitute_pass() -> None:
    cases = _cases(correct=True)
    state = CalibrationFreeze.draft().commit_seed("gate-forgery-seed").bind_cases(cases)
    report = generate_calibration_report(cases, state=state, seed="gate-forgery-seed")
    assert not report.accepted
    substitute = GateResult(
        name="substitute_pass",
        passed=True,
        observed=0.0,
        relation="<=",
        threshold=1.0,
    )
    with pytest.raises(ValueError, match="canonical metric-derived gate set"):
        replace(report, gates=(substitute,))


def test_cartesian_domain_mechanism_skew_fails_prediction_gate() -> None:
    cases = _cases(mechanism_leak=True)
    state = CalibrationFreeze.draft().commit_seed("mechanism-seed").bind_cases(cases)
    report = generate_calibration_report(cases, state=state, seed="mechanism-seed")
    metric = report.domain_fault_cross_prediction
    assert metric.balanced_accuracy > 0.65
    assert not next(
        gate
        for gate in report.gates
        if gate.name == "domain_topic_mechanism_prediction_excess_over_chance"
    ).passed


def test_multiclass_mechanism_leak_is_compared_with_chance_not_binary_ceiling() -> None:
    cases = _cases(
        family_count=5,
        template_count=5,
        repetitions=16,
        partial_mechanism_leak=True,
    )
    state = CalibrationFreeze.draft().commit_seed("multiclass-leak-seed").bind_cases(cases)
    report = generate_calibration_report(
        cases,
        state=state,
        seed="multiclass-leak-seed",
    )
    metric = report.domain_fault_cross_prediction
    assert metric.classes == 4
    assert metric.chance_accuracy == 0.25
    assert metric.balanced_accuracy >= 0.50
    assert metric.excess_over_chance >= 0.25
    assert not next(
        gate
        for gate in report.gates
        if gate.name == "domain_topic_mechanism_prediction_excess_over_chance"
    ).passed


def test_sparse_structural_cell_and_raw_labels_fail_closed_without_report_leakage() -> None:
    original = _cases()
    cases = tuple(
        _recertify(
            case,
            generator_cell=(
                "sensitive-label-marker"
                if index < 20
                else f"dense-cell-{(index - 20) % 3}"
            ),
        )
        for index, case in enumerate(original)
    )
    state = CalibrationFreeze.draft().commit_seed("sparse-cell-seed").bind_cases(cases)
    report = generate_calibration_report(cases, state=state, seed="sparse-cell-seed")
    sparse = next(
        stratum
        for stratum in report.strata
        if stratum.stratum_kind == "generator_cell" and stratum.cases == 20
    )
    assert not next(
        gate
        for gate in report.gates
        if gate.name
        == f"stratum_{sparse.stratum_index:04d}_generator_cell_minimum_cases"
    ).passed
    raw = report.canonical_json()
    assert "sensitive-label-marker" not in raw
    assert not report.accepted


def test_fixed_fault_position_fails_aggregate_and_per_cell_gates() -> None:
    cases = _cases(fixed_position=2)
    state = CalibrationFreeze.draft().commit_seed("fixed-seed").bind_cases(cases)
    report = generate_calibration_report(cases, state=state, seed="fixed-seed")
    assert report.fault_positions.normalized_entropy == 0.0
    assert report.fault_positions.maximum_position_share == 1.0
    assert report.fault_positions.uniform_total_variation == 0.75
    assert not next(
        gate for gate in report.gates if gate.name == "fault_position_entropy"
    ).passed
    assert not next(
        gate for gate in report.gates if gate.name == "maximum_fault_position_share"
    ).passed
    assert not next(
        gate
        for gate in report.gates
        if gate.name == "position_uniform_total_variation"
    ).passed
    assert any(
        not gate.passed and gate.name.endswith("_fault_position_entropy")
        for gate in report.gates
        if gate.name.startswith("stratum_")
    )
    assert any(
        not gate.passed and gate.name.endswith("_maximum_fault_position_share")
        for gate in report.gates
        if gate.name.startswith("stratum_")
    )


def test_absent_eligible_rank_cannot_hide_behind_average_support() -> None:
    rank_schedule = (0, 1, 2, 3, 4, 5, 6, 0, 1, 2, 3, 4)
    cases = []
    for index, case in enumerate(_cases()):
        trace = case.faulty_trace + tuple(
            f"Step {line}: auxiliary value {case.case_id}-{line} is retained"
            for line in range(7, 11)
        )
        cases.append(
            _recertify(
                case,
                faulty_trace=trace,
                eligible_fault_lines=tuple(range(2, 10)),
                fault_line=2 + rank_schedule[index % 12],
            )
        )
    values = tuple(cases)
    state = CalibrationFreeze.draft().commit_seed("missing-rank-seed").bind_cases(values)
    report = generate_calibration_report(values, state=state, seed="missing-rank-seed")
    assert report.fault_positions.normalized_entropy > 0.75
    assert report.fault_positions.uniform_total_variation <= 0.25
    assert report.fault_positions.minimum_cases_per_eligible_rank == 0.0
    gate = next(
        gate
        for gate in report.gates
        if gate.name == "minimum_cases_per_eligible_rank"
    )
    assert not gate.passed
    assert not report.accepted


@pytest.mark.parametrize(
    ("implausible_repetitions", "expected_rate", "expected_pass"),
    [(3, 0.75, True), (4, 0.666667, False)],
)
def test_plausibility_gate_accepts_seventy_percent_but_cells_cannot_hide(
    implausible_repetitions: int,
    expected_rate: float,
    expected_pass: bool,
) -> None:
    cases = _cases(implausible_repetitions=implausible_repetitions)
    state = CalibrationFreeze.draft().commit_seed("plausibility-seed").bind_cases(cases)
    report = generate_calibration_report(cases, state=state, seed="plausibility-seed")
    aggregate = next(
        item for item in report.gates if item.name == "faulty_trace_plausibility"
    )
    assert report.plausibility_rate == expected_rate
    assert aggregate.passed is expected_pass
    if expected_pass:
        assert any(
            not item.passed and item.name.endswith("_faulty_trace_plausibility")
            for item in report.gates
            if item.name.startswith("stratum_")
        )


def test_bad_cell_difficulty_fails_while_aggregate_remains_in_target() -> None:
    cases = _cases(one_bad_difficulty_cell=True)
    state = CalibrationFreeze.draft().commit_seed("cell-difficulty-seed").bind_cases(cases)
    report = generate_calibration_report(
        cases, state=state, seed="cell-difficulty-seed"
    )
    assert report.difficulty.band is DifficultyBand.TARGET
    bad_cell = next(
        stratum
        for stratum in report.strata
        if stratum.stratum_kind == "domain_fault"
        and stratum.brain_off_accuracy == 1.0
    )
    assert bad_cell.brain_off_accuracy == 1.0
    assert not next(
        gate
        for gate in report.gates
        if gate.name
        == (
            f"stratum_{bad_cell.stratum_index:04d}_domain_fault_"
            "brain_off_target_maximum"
        )
    ).passed


def test_duplicate_normalized_template_is_rejected() -> None:
    cases = list(_cases())
    original = cases[0]
    duplicate = _certified_case(
        case_id="duplicate-template-case",
        family=original.family,
        template_id="dishonest-new-template-id",
        generator_cell=original.generator_cell,
        domain=original.domain,
        fault_kind=original.fault_kind,
        prompt=original.prompt.replace("instance 100", "instance 99999"),
        faulty_trace=tuple(
            line.replace("value ", "value 9", 1) for line in original.faulty_trace
        ),
        fault_line=original.fault_line,
        brain_off_correct=original.brain_off_correct,
    )
    cases.append(duplicate)
    with pytest.raises(ValueError, match="duplicate normalized template"):
        CalibrationFreeze.draft().commit_seed("duplicate-seed").bind_cases(cases)


def test_domain_fault_design_requires_complete_cartesian_product() -> None:
    cases = tuple(
        _recertify(case, fault_kind="fault0")
        if case.domain == "domain1" and case.fault_kind == "fault1"
        else case
        for case in _cases()
    )
    with pytest.raises(ValueError, match="complete Cartesian product"):
        CalibrationFreeze.draft().commit_seed("cartesian-seed").bind_cases(cases)


def test_each_family_requires_nontrivial_support_for_every_domain_class() -> None:
    source = _cases()
    retained_domain1 = False
    cases: list[CalibrationCase] = []
    for case in source:
        if case.family == "family0" and case.domain == "domain1":
            if not retained_domain1:
                retained_domain1 = True
                cases.append(case)
            else:
                cases.append(_recertify(case, domain="domain0"))
        else:
            cases.append(case)
    with pytest.raises(ValueError, match="every domain class"):
        CalibrationFreeze.draft().commit_seed("family-support-seed").bind_cases(cases)


def test_family_specific_fixed_positions_fail_conditional_and_within_family_gates() -> None:
    cases = _cases(family_fixed_position=True)
    state = CalibrationFreeze.draft().commit_seed("family-position-seed").bind_cases(cases)
    report = generate_calibration_report(
        cases,
        state=state,
        seed="family-position-seed",
    )
    assert report.fault_positions.maximum_position_share == 0.25
    assert report.fault_positions.normalized_entropy == 1.0
    assert report.fault_positions.uniform_total_variation == 0.0
    location = next(
        metric
        for metric in report.baselines
        if metric.name == "location__within_family"
    )
    assert location.top1_accuracy > 0.95
    assert location.excess_over_chance > 0.70
    family_strata = tuple(
        stratum for stratum in report.strata if stratum.stratum_kind == "family"
    )
    assert all(stratum.maximum_fault_position_share == 1.0 for stratum in family_strata)
    assert all(
        stratum.position_uniform_total_variation == 0.75
        for stratum in family_strata
    )
    assert not report.accepted


def test_family_template_latin_square_cannot_hide_fixed_positions() -> None:
    cases = _cases(
        template_count=4,
        repetitions=15,
        family_template_fixed_position=True,
    )
    state = CalibrationFreeze.draft().commit_seed("latin-square-seed").bind_cases(cases)
    report = generate_calibration_report(cases, state=state, seed="latin-square-seed")
    assert report.fault_positions.maximum_position_share == 0.25
    assert all(
        stratum.maximum_fault_position_share == 0.25
        for stratum in report.strata
        if stratum.stratum_kind in {"family", "template"}
    )
    pair_strata = tuple(
        stratum
        for stratum in report.strata
        if stratum.stratum_kind == "family_template"
    )
    assert len(pair_strata) == 16
    assert all(stratum.maximum_fault_position_share == 1.0 for stratum in pair_strata)
    assert all(
        stratum.position_uniform_total_variation == 0.75
        for stratum in pair_strata
    )
    assert not report.accepted


def test_poisoned_oracle_and_mismatched_fault_line_are_rejected() -> None:
    case = _cases()[0]
    with pytest.raises(ValueError, match="poisoned oracle receipt"):
        OracleReceipt.issue(
            case_binding_digest=case.oracles[0].case_binding_digest,
            verifier_id="poisoned-oracle",
            first_invalid_line=case.fault_line,
            faulty_valid=True,
        )
    changed_line = 2 if case.fault_line != 2 else 3
    with pytest.raises(ValueError, match="bound to different case content"):
        replace(case, fault_line=changed_line)


def test_two_distinct_oracle_receipts_are_mandatory() -> None:
    case = _cases()[0]
    values = case.binding_payload()
    with pytest.raises(ValueError, match="at least two"):
        CalibrationCase.build(**values, oracles=(case.oracles[0],))
    duplicate_verifier = OracleReceipt.issue(
        case_binding_digest=case.oracles[0].case_binding_digest,
        verifier_id=case.oracles[0].verifier_id,
        first_invalid_line=case.fault_line,
    )
    with pytest.raises(ValueError, match="distinct verifier"):
        CalibrationCase.build(
            **values,
            oracles=(case.oracles[0], duplicate_verifier),
        )


def test_two_caller_forged_receipts_never_attest_semantic_independence() -> None:
    source = _cases()[0]
    values = source.binding_payload()
    binding = CalibrationCase.binding_digest_for(**values)
    forged = tuple(
        OracleReceipt.issue(
            case_binding_digest=binding,
            verifier_id=f"caller-forged-{index}",
            first_invalid_line=source.fault_line,
        )
        for index in range(2)
    )
    structurally_admitted = CalibrationCase.build(**values, oracles=forged)
    assert structurally_admitted.receipts_attest_semantic_independence is False
    assert "semantic_independence" not in structurally_admitted.to_dict()


@pytest.mark.parametrize(
    ("line_count", "fault_line", "message"),
    [
        (5, 2, "between 6 and 12"),
        (13, 2, "between 6 and 12"),
        (6, 1, "interior"),
        (6, 6, "interior"),
    ],
)
def test_score_bearing_cases_require_six_to_twelve_lines_and_interior_fault(
    line_count: int,
    fault_line: int,
    message: str,
) -> None:
    values = {
        "case_id": f"bad-shape-{line_count}-{fault_line}",
        "family": "shape-family",
        "template_id": "shape-template",
        "generator_cell": "shape-cell",
        "domain": "shape-domain",
        "fault_kind": "shape-fault",
        "prompt": "Check the shaped derivation",
        "faulty_trace": tuple(
            f"Line {index}: x equals {index}" for index in range(line_count)
        ),
        "eligible_fault_lines": tuple(range(2, line_count)),
        "fault_line": fault_line,
        "brain_off_correct": False,
    }
    binding = CalibrationCase.binding_digest_for(**values)
    receipts = tuple(
        OracleReceipt.issue(
            case_binding_digest=binding,
            verifier_id=f"shape-oracle-{index}",
            first_invalid_line=fault_line,
        )
        for index in range(2)
    )
    with pytest.raises(ValueError, match=message):
        CalibrationCase.build(**values, oracles=receipts)


@pytest.mark.parametrize("correct", [False, True])
def test_extreme_brain_off_accuracy_is_hard_rejected(correct: bool) -> None:
    cases = _cases(correct=correct)
    state = CalibrationFreeze.draft().commit_seed("difficulty-seed").bind_cases(cases)
    report = generate_calibration_report(cases, state=state, seed="difficulty-seed")
    assert report.difficulty.band is DifficultyBand.REJECTED
    assert not report.accepted
    with pytest.raises(ValueError, match="failed calibration reports"):
        state.freeze(
            report,
            cases=cases,
            seed="difficulty-seed",
        )


def test_freeze_is_immutable_one_way_and_rejects_post_freeze_transitions(
    passing_bundle: tuple[
        tuple[CalibrationCase, ...], CalibrationFreeze, CalibrationReport
    ],
) -> None:
    cases, state, report = passing_bundle
    frozen = state.freeze(
        report,
        cases=cases,
        seed="public-calibration-seed",
    )
    assert frozen.state is FreezeState.FROZEN
    with pytest.raises(FrozenInstanceError):
        frozen.state = FreezeState.DRAFT  # type: ignore[misc]
    with pytest.raises(ValueError, match="cannot be unsealed"):
        frozen.unseal()
    with pytest.raises(ValueError, match="only be committed from draft"):
        frozen.commit_seed("new-seed")
    payload = frozen.to_dict()
    payload["state"] = FreezeState.DATA_BOUND.value
    with pytest.raises(ValueError, match="fields do not match"):
        CalibrationFreeze.from_dict(payload)


def test_seed_commitment_must_open_and_case_set_cannot_mutate(
    passing_bundle: tuple[
        tuple[CalibrationCase, ...], CalibrationFreeze, CalibrationReport
    ],
) -> None:
    cases, state, _ = passing_bundle
    with pytest.raises(ValueError, match="does not open"):
        generate_calibration_report(cases, state=state, seed="wrong-seed")
    with pytest.raises(ValueError, match="bound public case set"):
        generate_calibration_report(cases[:-1], state=state, seed="public-calibration-seed")


def test_cli_tool_atomically_writes_and_verifies_one_bundle(
    tmp_path: Path,
    passing_bundle: tuple[
        tuple[CalibrationCase, ...], CalibrationFreeze, CalibrationReport
    ],
) -> None:
    cases, state, report = passing_bundle
    input_path = tmp_path / "input.json"
    state_path = tmp_path / "state.json"
    seed_path = tmp_path / "seed.txt"
    bundle_path = tmp_path / "calibration.bundle.json"
    input_path.write_text(
        canonical_json(calibration_input_document(cases)), encoding="utf-8"
    )
    state_path.write_text(state.canonical_json(), encoding="utf-8")
    seed_path.write_text("public-calibration-seed\n", encoding="utf-8")
    assert main(
        [
            "calibrate-v2",
            str(input_path),
            "--state",
            str(state_path),
            "--seed-file",
            str(seed_path),
            "--bundle",
            str(bundle_path),
        ]
    ) == 0
    raw = bundle_path.read_text(encoding="utf-8")
    bundle = load_calibration_bundle(raw)
    assert bundle.report.accepted
    assert bundle.report.content_digest == report.content_digest
    assert bundle.frozen_state is not None
    assert bundle.frozen_state.state is FreezeState.FROZEN
    assert bundle.frozen_state.frozen_report_digest == report.content_digest
    assert main(
        [
            "verify-calibration-v2",
            str(input_path),
            "--state",
            str(state_path),
            "--seed-file",
            str(seed_path),
            "--bundle",
            str(bundle_path),
        ]
    ) == 0
    # An identical rerun is an idempotent resume and never rewrites the artifact.
    assert main(
        [
            "calibrate-v2",
            str(input_path),
            "--state",
            str(state_path),
            "--seed-file",
            str(seed_path),
            "--bundle",
            str(bundle_path),
        ]
    ) == 0
    assert bundle_path.read_text(encoding="utf-8") == raw

    malformed_path = tmp_path / "malformed.bundle.json"
    malformed_path.write_text("{}", encoding="utf-8")
    with pytest.raises(FileExistsError, match="not the same valid bundle"):
        main(
            [
                "calibrate-v2",
                str(input_path),
                "--state",
                str(state_path),
                "--seed-file",
                str(seed_path),
                "--bundle",
                str(malformed_path),
            ]
        )
    assert malformed_path.read_text(encoding="utf-8") == "{}"


def test_bundle_publish_failure_before_atomic_link_leaves_no_final_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    passing_bundle: tuple[
        tuple[CalibrationCase, ...], CalibrationFreeze, CalibrationReport
    ],
) -> None:
    cases, state, report = passing_bundle
    frozen = state.freeze(
        report,
        cases=cases,
        seed="public-calibration-seed",
    )
    bundle = CalibrationBundle(report=report, frozen_state=frozen)
    target = tmp_path / "interrupted.bundle.json"

    def fail_link(_source: object, _target: object) -> None:
        raise OSError("simulated pre-publish interruption")

    monkeypatch.setattr("math_brain.calibration_v2.os.link", fail_link)
    with pytest.raises(OSError, match="simulated pre-publish"):
        publish_calibration_bundle(target, bundle)
    assert not target.exists()
    assert not tuple(tmp_path.glob(".*.tmp"))


def test_bundle_requires_exactly_one_valid_acceptance_state(
    passing_bundle: tuple[
        tuple[CalibrationCase, ...], CalibrationFreeze, CalibrationReport
    ],
) -> None:
    cases, state, accepted_report = passing_bundle
    frozen = state.freeze(
        accepted_report,
        cases=cases,
        seed="public-calibration-seed",
    )
    with pytest.raises(ValueError, match="requires a frozen state"):
        CalibrationBundle(report=accepted_report, frozen_state=None)

    failed_cases = _cases(correct=True)
    failed_state = (
        CalibrationFreeze.draft()
        .commit_seed("failed-bundle-seed")
        .bind_cases(failed_cases)
    )
    failed_report = generate_calibration_report(
        failed_cases,
        state=failed_state,
        seed="failed-bundle-seed",
    )
    assert not failed_report.accepted
    failed_bundle = CalibrationBundle(report=failed_report, frozen_state=None)
    assert load_calibration_bundle(
        failed_bundle.canonical_json()
    ).canonical_json() == failed_bundle.canonical_json()
    with pytest.raises(ValueError, match="failed calibration bundle"):
        CalibrationBundle(report=failed_report, frozen_state=frozen)
