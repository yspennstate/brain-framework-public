from __future__ import annotations

from dataclasses import fields

import pytest

from math_brain.power_v2 import PowerScenarioV2, plan_power_v2


def test_planner_uses_cases_as_the_only_independent_units() -> None:
    assert {field.name for field in fields(PowerScenarioV2)} == {
        "case_count",
        "confidence_z",
        "discordance",
        "maximum_exact_sign_flip_p",
        "minimum_observed_delta",
        "minimum_strictly_positive_cases",
        "true_delta",
    }
    with pytest.raises(TypeError):
        PowerScenarioV2(  # type: ignore[call-arg]
            case_count=40,
            cases_per_pair=20,
            true_delta=0.06,
            discordance=0.30,
        )


def test_more_independent_cases_improve_mean_and_interval_power() -> None:
    small = plan_power_v2(PowerScenarioV2(case_count=40, true_delta=0.08, discordance=0.30))
    larger = plan_power_v2(PowerScenarioV2(case_count=80, true_delta=0.08, discordance=0.30))
    assert larger.mean_delta_gate_power > small.mean_delta_gate_power
    assert larger.interval_gate_normal_approximation > small.interval_gate_normal_approximation
    assert larger.positive_case_gate_power > small.positive_case_gate_power


def test_report_exposes_each_case_cluster_gate_and_a_joint_probability() -> None:
    report = plan_power_v2(PowerScenarioV2(case_count=40, true_delta=0.08, discordance=0.30))
    document = report.to_dict()
    assert set(document) == {
        "all_modelled_gates_upper_bound",
        "exact_sign_flip_gate_power",
        "interval_gate_normal_approximation",
        "mean_delta_gate_power",
        "median_case_gate_power",
        "positive_case_gate_power",
        "scenario",
        "structural_joint_gate_power",
    }
    assert report.structural_joint_gate_power <= min(
        report.mean_delta_gate_power,
        report.positive_case_gate_power,
        report.median_case_gate_power,
        report.exact_sign_flip_gate_power,
    )
    assert report.all_modelled_gates_upper_bound == min(
        report.structural_joint_gate_power,
        report.interval_gate_normal_approximation,
    )


def test_exact_sign_flip_gate_uses_independent_case_count() -> None:
    four = plan_power_v2(
        PowerScenarioV2(
            case_count=4,
            true_delta=1.0,
            discordance=1.0,
            minimum_observed_delta=1.0,
            minimum_strictly_positive_cases=1,
        )
    )
    five = plan_power_v2(
        PowerScenarioV2(
            case_count=5,
            true_delta=1.0,
            discordance=1.0,
            minimum_observed_delta=1.0,
            minimum_strictly_positive_cases=1,
        )
    )
    assert four.exact_sign_flip_gate_power == 0.0  # 1 / 2**4 > 0.05
    assert five.exact_sign_flip_gate_power == 1.0  # 1 / 2**5 <= 0.05
    assert four.structural_joint_gate_power == 0.0
    assert five.structural_joint_gate_power == 1.0


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"case_count": 0}, "positive integer"),
        ({"case_count": 513}, "512 independent cases"),
        ({"true_delta": 0.31}, "cannot exceed"),
        ({"minimum_strictly_positive_cases": 41}, "cannot exceed"),
        ({"maximum_exact_sign_flip_p": 0.06}, "sign-flip threshold"),
        ({"discordance": float("nan")}, "finite"),
    ],
)
def test_invalid_power_scenarios_fail_closed(changes: dict[str, object], message: str) -> None:
    values: dict[str, object] = {
        "case_count": 40,
        "true_delta": 0.04,
        "discordance": 0.30,
    }
    values.update(changes)
    with pytest.raises(ValueError, match=message):
        PowerScenarioV2(**values)  # type: ignore[arg-type]


def test_scenario_subclasses_and_numeric_subclasses_are_rejected() -> None:
    class ScenarioSubclass(PowerScenarioV2):
        pass

    class FloatSubclass(float):
        pass

    with pytest.raises(ValueError, match="exact PowerScenarioV2"):
        ScenarioSubclass(case_count=40, true_delta=0.04, discordance=0.30)
    with pytest.raises(ValueError, match="must be a number"):
        PowerScenarioV2(
            case_count=40,
            true_delta=FloatSubclass(0.04),
            discordance=0.30,
        )
    with pytest.raises(ValueError, match="positive and finite"):
        PowerScenarioV2(
            case_count=40,
            true_delta=0.04,
            discordance=0.30,
            confidence_z=True,
        )


def test_exact_degenerate_scenarios_do_not_flatter_the_design() -> None:
    all_ties = plan_power_v2(PowerScenarioV2(case_count=40, true_delta=0.0, discordance=0.0))
    assert all_ties.mean_delta_gate_power == 0.0
    assert all_ties.positive_case_gate_power == 0.0
    assert all_ties.median_case_gate_power == 0.0
    assert all_ties.exact_sign_flip_gate_power == 0.0
    assert all_ties.interval_gate_normal_approximation == 0.0
    assert all_ties.structural_joint_gate_power == 0.0
    assert all_ties.all_modelled_gates_upper_bound == 0.0

    all_improvements = plan_power_v2(
        PowerScenarioV2(
            case_count=12,
            true_delta=1.0,
            discordance=1.0,
            minimum_observed_delta=1.0,
            minimum_strictly_positive_cases=12,
        )
    )
    assert all_improvements.mean_delta_gate_power == 1.0
    assert all_improvements.positive_case_gate_power == 1.0
    assert all_improvements.median_case_gate_power == 1.0
    assert all_improvements.exact_sign_flip_gate_power == 1.0
    assert all_improvements.interval_gate_normal_approximation == 1.0
    assert all_improvements.structural_joint_gate_power == 1.0
    assert all_improvements.all_modelled_gates_upper_bound == 1.0


def test_maximum_grid_probabilities_remain_in_the_unit_interval() -> None:
    report = plan_power_v2(PowerScenarioV2(case_count=512, true_delta=0.08, discordance=1.0))
    values = (
        report.mean_delta_gate_power,
        report.positive_case_gate_power,
        report.median_case_gate_power,
        report.exact_sign_flip_gate_power,
        report.structural_joint_gate_power,
        report.interval_gate_normal_approximation,
        report.all_modelled_gates_upper_bound,
    )
    assert all(0.0 <= value <= 1.0 for value in values)
