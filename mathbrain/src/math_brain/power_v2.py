"""Dependency-free power checks at the independent-case level.

Each case cluster is modelled as an improvement (+1), harm (-1), or tie (0).
The twenty repeated observations used by evaluation v2 are deliberately not
counted as twenty independent units.  The planner exactly enumerates the mean,
strict-positive, median, and one-sided sign-flip gates under this coarse model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction


def _probability(value: float, label: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError(f"{label} must be finite and in [0, 1]")
    return result


def _positive_integer(value: int, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


@dataclass(frozen=True, slots=True)
class PowerScenarioV2:
    case_count: int
    true_delta: float
    discordance: float
    minimum_observed_delta: float = 0.04
    minimum_strictly_positive_cases: int = 12
    maximum_exact_sign_flip_p: float = 0.05
    confidence_z: float = 1.959963984540054

    def __post_init__(self) -> None:
        if type(self) is not PowerScenarioV2:
            raise ValueError("power scenarios must be exact PowerScenarioV2 records")
        cases = _positive_integer(self.case_count, "case count")
        delta = _probability(self.true_delta, "true delta")
        discordance = _probability(self.discordance, "discordance")
        gate = _probability(self.minimum_observed_delta, "minimum observed delta")
        positives = _positive_integer(
            self.minimum_strictly_positive_cases,
            "minimum strictly positive cases",
        )
        sign_flip_p = _probability(
            self.maximum_exact_sign_flip_p,
            "maximum exact sign-flip p",
        )
        if delta > discordance:
            raise ValueError("true delta cannot exceed total discordance")
        if positives > cases:
            raise ValueError("positive-case gate cannot exceed case count")
        if cases > 512:
            raise ValueError("planning grid is capped at 512 independent cases")
        if gate > 1.0:
            raise AssertionError("probability validation failed")
        if not 0.0 < sign_flip_p <= 0.05:
            raise ValueError("exact sign-flip threshold must lie in (0, 0.05]")
        if (
            type(self.confidence_z) not in (int, float)
            or not math.isfinite(self.confidence_z)
            or self.confidence_z <= 0
        ):
            raise ValueError("confidence z must be positive and finite")

    @property
    def improvement_probability(self) -> float:
        return (self.discordance + self.true_delta) / 2.0

    @property
    def harm_probability(self) -> float:
        return (self.discordance - self.true_delta) / 2.0


@dataclass(frozen=True, slots=True)
class PowerReportV2:
    scenario: PowerScenarioV2
    mean_delta_gate_power: float
    positive_case_gate_power: float
    median_case_gate_power: float
    exact_sign_flip_gate_power: float
    structural_joint_gate_power: float
    interval_gate_normal_approximation: float
    all_modelled_gates_upper_bound: float

    def to_dict(self) -> dict[str, object]:
        return {
            "all_modelled_gates_upper_bound": self.all_modelled_gates_upper_bound,
            "exact_sign_flip_gate_power": self.exact_sign_flip_gate_power,
            "interval_gate_normal_approximation": self.interval_gate_normal_approximation,
            "mean_delta_gate_power": self.mean_delta_gate_power,
            "median_case_gate_power": self.median_case_gate_power,
            "positive_case_gate_power": self.positive_case_gate_power,
            "scenario": {
                "case_count": self.scenario.case_count,
                "confidence_z": self.scenario.confidence_z,
                "discordance": self.scenario.discordance,
                "maximum_exact_sign_flip_p": self.scenario.maximum_exact_sign_flip_p,
                "minimum_observed_delta": self.scenario.minimum_observed_delta,
                "minimum_strictly_positive_cases": (self.scenario.minimum_strictly_positive_cases),
                "true_delta": self.scenario.true_delta,
            },
            "structural_joint_gate_power": self.structural_joint_gate_power,
        }


def _multinomial_probability(
    total: int,
    positive: int,
    negative: int,
    *,
    positive_probability: float,
    negative_probability: float,
) -> float:
    tied = total - positive - negative
    tie_probability = 1.0 - positive_probability - negative_probability
    if tie_probability < -1e-12:
        raise ValueError("improvement, harm, and tie probabilities must be nonnegative")
    probabilities = (
        positive_probability,
        negative_probability,
        max(0.0, tie_probability),
    )
    counts = (positive, negative, tied)
    logarithm = math.lgamma(total + 1) - sum(math.lgamma(count + 1) for count in counts)
    for count, probability in zip(counts, probabilities):
        if not count:
            continue
        if probability == 0.0:
            return 0.0
        logarithm += count * math.log(probability)
    return math.exp(logarithm)


def _sign_flip_p_values(maximum_cases: int) -> dict[tuple[int, int], float]:
    """Return exact one-sided sign-flip tails keyed by (nonzero, positive)."""

    result: dict[tuple[int, int], float] = {(0, 0): 1.0}
    for nonzero in range(1, maximum_cases + 1):
        denominator = 1 << nonzero
        tail = 0
        for positive in range(nonzero, -1, -1):
            tail += math.comb(nonzero, positive)
            result[(nonzero, positive)] = tail / denominator
    return result


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _unit_probability(value: float) -> float:
    if not -1e-12 <= value <= 1.0 + 1e-12:
        raise ArithmeticError("enumerated probability escaped the unit interval")
    return min(1.0, max(0.0, value))


def _minimum_total_difference(scenario: PowerScenarioV2) -> int:
    threshold = Fraction(str(scenario.minimum_observed_delta)) * scenario.case_count
    return -(-threshold.numerator // threshold.denominator)


def _median_is_positive(total: int, positive: int, negative: int) -> bool:
    if positive > total // 2:
        return True
    return total % 2 == 0 and positive == total // 2 and negative < total // 2


def plan_power_v2(scenario: PowerScenarioV2) -> PowerReportV2:
    """Compute exact structural-gate powers and a labelled interval approximation."""

    if type(scenario) is not PowerScenarioV2:
        raise ValueError("power planning requires an exact PowerScenarioV2")
    cases = scenario.case_count
    improvement = scenario.improvement_probability
    harm = scenario.harm_probability
    minimum_difference = _minimum_total_difference(scenario)
    sign_flip_p = _sign_flip_p_values(cases)
    mean_gate = 0.0
    positive_gate = 0.0
    median_gate = 0.0
    randomization_gate = 0.0
    structural_joint = 0.0
    for positive in range(cases + 1):
        for negative in range(cases - positive + 1):
            probability = _multinomial_probability(
                cases,
                positive,
                negative,
                positive_probability=improvement,
                negative_probability=harm,
            )
            if probability == 0.0:
                continue
            mean_passed = positive - negative >= minimum_difference
            positive_passed = positive >= scenario.minimum_strictly_positive_cases
            median_passed = _median_is_positive(cases, positive, negative)
            randomization_passed = (
                sign_flip_p[(positive + negative, positive)] <= scenario.maximum_exact_sign_flip_p
            )
            mean_gate += probability * mean_passed
            positive_gate += probability * positive_passed
            median_gate += probability * median_passed
            randomization_gate += probability * randomization_passed
            structural_joint += probability * (
                mean_passed and positive_passed and median_passed and randomization_passed
            )

    per_case_variance = scenario.discordance - scenario.true_delta**2
    if per_case_variance == 0.0:
        interval_gate = float(scenario.true_delta > 0.0)
    else:
        standard_error = math.sqrt(per_case_variance / cases)
        interval_gate = _normal_cdf(scenario.true_delta / standard_error - scenario.confidence_z)

    mean_gate = _unit_probability(mean_gate)
    positive_gate = _unit_probability(positive_gate)
    median_gate = _unit_probability(median_gate)
    randomization_gate = _unit_probability(randomization_gate)
    structural_joint = _unit_probability(structural_joint)
    interval_gate = _unit_probability(interval_gate)
    all_upper = min(structural_joint, interval_gate)
    return PowerReportV2(
        scenario=scenario,
        mean_delta_gate_power=mean_gate,
        positive_case_gate_power=positive_gate,
        median_case_gate_power=median_gate,
        exact_sign_flip_gate_power=randomization_gate,
        structural_joint_gate_power=structural_joint,
        interval_gate_normal_approximation=interval_gate,
        all_modelled_gates_upper_bound=all_upper,
    )


__all__ = ["PowerReportV2", "PowerScenarioV2", "plan_power_v2"]
