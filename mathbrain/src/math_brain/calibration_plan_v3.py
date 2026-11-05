"""Outcome-blind preregistration artifacts for public calibration corpora.

The plan fixes every implementation and design dependency that is allowed to affect
case selection.  The manifest then fixes one complete, index-only selection before
any solver outcome is read.  Materialization is checked through the deliberately
outcome-free :class:`MaterializedCaseBinding` view.

Digests and deterministic selection tickets are commitments, not semantic or
chronology proofs.  A digest called a ``position_blind_template_fingerprint`` is
only as trustworthy as the reviewed source-bound implementation that produced it,
and this module cannot prove that a public commitment preceded outcome observation.
Independent source replay and an externally timestamped publication remain required.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .models import canonical_json

PLAN_SCHEMA = "org.math-research-brain/calibration-plan"
PLAN_SCHEMA_VERSION = 1
MANIFEST_SCHEMA = "org.math-research-brain/calibration-selection-manifest"
MANIFEST_SCHEMA_VERSION = 1
MATERIALIZED_BINDING_SCHEMA = "org.math-research-brain/calibration-materialized-binding"
MATERIALIZED_BINDING_SCHEMA_VERSION = 1
COMMITMENT_SCHEMA = "org.math-research-brain/calibration-preregistration-commitment"
COMMITMENT_SCHEMA_VERSION = 1
SELECTION_TICKET_SCHEMA = "org.math-research-brain/calibration-selection-ticket"
SELECTION_TICKET_SCHEMA_VERSION = 1

MIN_CALIBRATION_CASES = 200
MAX_CALIBRATION_CASES = 400
MIN_ELIGIBLE_ARITY = 4
MAX_ELIGIBLE_ARITY = 10
MAX_AXIS_COUNT = MAX_CALIBRATION_CASES
MAX_PLAN_JSON_BYTES = 128_000
MAX_MANIFEST_JSON_BYTES = 2_000_000
MAX_TEXT_BYTES = 4_096
MAX_LABEL_BYTES = 512
MAX_STABLE_CASE_ID_BYTES = 512
MAX_VERIFIER_IMPLEMENTATIONS = 16
MIN_SUPPORTED_FAULT_LINE = 2
MAX_SUPPORTED_FAULT_LINE = 11

_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


# This is the exact CalibrationConfig digest at the v3 preregistration boundary.  It is
# deliberately literal rather than imported from calibration_v2: that lets calibration_v2
# import this standalone module later without a cycle.  A compatibility canary fails loudly
# whenever DEFAULT_CONFIG changes, forcing a reviewed profile/version update rather than an
# accidental relaxation.
SEALED_PROFILE_DIGEST = "sha256:d97f962674c3378d8ce1743666e2dfebb49b74f77c3cd76252f91f90e77abed5"

MIN_FAMILY_COUNT = 4
MAX_FAMILY_COUNT = 8
MIN_TEMPLATE_COUNT = 4
MAX_TEMPLATE_COUNT = 16
MIN_GENERATOR_CELL_COUNT = 2
MAX_GENERATOR_CELL_COUNT = 4
MIN_DOMAIN_COUNT = 2
MIN_FAULT_COUNT = 2
MAX_DOMAIN_FAULT_CELLS = 8
MIN_DOMAIN_FAULT_CELL_CASES = 40
MIN_FAMILY_CASES = 40
MIN_GENERATOR_CELL_CASES = 40
MIN_TEMPLATE_CASES = 20
MIN_FAMILY_DOMAIN_CASES = 8
MIN_FAMILY_FAULT_CASES = 8
MIN_FAMILY_TEMPLATE_CASES = 12
MIN_CASES_PER_ELIGIBLE_RANK = 2
MIN_GENERATOR_CELL_FAMILIES = 4
MIN_GENERATOR_CELL_TEMPLATES = 4
MIN_GENERATOR_CELL_DOMAINS = 2
MIN_GENERATOR_CELL_FAULTS = 2
MIN_DOMAIN_FAULT_CELL_FAMILIES = 2
MIN_DOMAIN_FAULT_CELL_TEMPLATES = 4
MIN_FAMILY_TEMPLATES = 4
MIN_FAMILY_DOMAINS = 2
MIN_FAMILY_FAULTS = 2
MIN_FAMILY_TEMPLATE_DOMAINS = 2
MIN_FAMILY_TEMPLATE_FAULTS = 2
MIN_FAMILY_TEMPLATE_GENERATOR_CELLS = 2
MIN_TEMPLATE_FAMILIES = 2
MIN_TEMPLATE_DOMAINS = 2
MIN_TEMPLATE_FAULTS = 2
MIN_FAULT_POSITION_ENTROPY = 0.75
MAX_FAULT_POSITION_SHARE = 0.25
MAX_POSITION_UNIFORM_TOTAL_VARIATION = 0.25


def _strict_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if not all(type(key) is str for key in value):
        raise ValueError(f"{label} keys must be strings")
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"invalid {label} keys; missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if type(value) is not list:
        raise ValueError(f"{label} must be an array")
    return value


def _bounded_list(value: Any, *, label: str, maximum: int) -> list[Any]:
    result = _list(value, label)
    if len(result) > maximum:
        raise ValueError(f"{label} contains more than {maximum} entries")
    return result


def _bounded_tuple(values: Iterable[Any], *, label: str, maximum: int) -> tuple[Any, ...]:
    if type(values) in (str, bytes):
        raise ValueError(f"{label} must be an iterable of entries, not text")
    try:
        iterator = iter(values)
    except TypeError as exc:
        raise ValueError(f"{label} must be iterable") from exc
    result: list[Any] = []
    for index, value in enumerate(iterator):
        if index >= maximum:
            raise ValueError(f"{label} contains more than {maximum} entries")
        result.append(value)
    return tuple(result)


def _integer(value: Any, label: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{label} must be an integer")
    return value


def _text(value: Any, label: str, *, max_bytes: int = MAX_TEXT_BYTES) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{label} must be a nonempty, whitespace-trimmed string")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8") from exc
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{label} must use Unicode NFC normalization")
    if len(encoded) > max_bytes:
        raise ValueError(f"{label} exceeds the {max_bytes}-byte budget")
    return value


def _sha256(value: Any, label: str) -> str:
    text = _text(value, label, max_bytes=71)
    if _SHA256_RE.fullmatch(text) is None:
        raise ValueError(f"{label} must be a lowercase sha256: digest")
    return text


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _load_canonical_json(raw: str, *, label: str, max_bytes: int) -> Any:
    if type(raw) is not str:
        raise ValueError(f"{label} must be text")
    try:
        size = len(raw.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8") from exc
    if size > max_bytes:
        raise ValueError(f"{label} exceeds the {max_bytes}-byte budget")
    try:
        parsed = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"{label} is not valid bounded JSON") from exc
    if raw != canonical_json(parsed):
        raise ValueError(f"{label} must use exact canonical UTF-8 JSON bytes")
    return parsed


def _balanced_position_is_valid(*, arity: int, support: int) -> bool:
    """Whether the optimal integer rank allocation meets every sealed position gate."""

    quotient, remainder = divmod(support, arity)
    counts = (quotient + 1,) * remainder + (quotient,) * (arity - remainder)
    if min(counts) < MIN_CASES_PER_ELIGIBLE_RANK:
        return False
    probabilities = tuple(count / support for count in counts)
    entropy = -sum(probability * math.log(probability) for probability in probabilities)
    normalized_entropy = round(entropy / math.log(arity), 6)
    uniform = 1.0 / arity
    total_variation = round(
        0.5 * sum(abs(probability - uniform) for probability in probabilities),
        6,
    )
    maximum_share = round(max(probabilities), 6)
    return (
        normalized_entropy >= MIN_FAULT_POSITION_ENTROPY
        and maximum_share <= MAX_FAULT_POSITION_SHARE
        and total_variation <= MAX_POSITION_UNIFORM_TOTAL_VARIATION
    )


@lru_cache(maxsize=512)
def _family_template_case_count_is_feasible(
    *,
    case_count: int,
    family_count: int,
    template_count: int,
    eligible_arities: tuple[int, ...],
) -> bool:
    """Bounded exact DP over realizable integer family-template stratum sizes.

    The most-even integer allocation simultaneously maximizes entropy and minimizes
    maximum share and total variation.  Within the sealed arity/support bounds, every
    such allocation that clears the rank floor and share ceiling also clears the two
    aggregate position gates, so each accepted support is an exact existence witness.
    """

    per_arity_totals = {0}
    for arity in eligible_arities:
        valid_supports = tuple(
            support
            for support in range(
                MIN_CASES_PER_ELIGIBLE_RANK * arity,
                case_count + 1,
            )
            if _balanced_position_is_valid(arity=arity, support=support)
        )
        if not valid_supports:
            return False
        per_arity_totals = {
            subtotal + support
            for subtotal in per_arity_totals
            for support in valid_supports
            if subtotal + support <= case_count
        }
        if not per_arity_totals:
            return False

    valid_stratum_sizes = tuple(
        total for total in per_arity_totals if total >= MIN_FAMILY_TEMPLATE_CASES
    )
    if not valid_stratum_sizes:
        return False
    reachable_totals = {0}
    for _ in range(family_count * template_count):
        reachable_totals = {
            subtotal + size
            for subtotal in reachable_totals
            for size in valid_stratum_sizes
            if subtotal + size <= case_count
        }
        if not reachable_totals:
            return False
    return case_count in reachable_totals


@dataclass(frozen=True, slots=True)
class AxisCounts:
    family: int
    template: int
    layout: int
    domain: int
    fault: int
    generator_cell: int

    def __post_init__(self) -> None:
        if type(self) is not AxisCounts:
            raise ValueError("axis counts subclasses are forbidden")
        values = {
            name: _integer(getattr(self, name), f"{name} axis count")
            for name in ("family", "template", "layout", "domain", "fault", "generator_cell")
        }
        if not MIN_FAMILY_COUNT <= values["family"] <= MAX_FAMILY_COUNT:
            raise ValueError(
                f"family axis count must lie in [{MIN_FAMILY_COUNT}, {MAX_FAMILY_COUNT}]"
            )
        if not MIN_TEMPLATE_COUNT <= values["template"] <= MAX_TEMPLATE_COUNT:
            raise ValueError(
                f"template axis count must lie in [{MIN_TEMPLATE_COUNT}, {MAX_TEMPLATE_COUNT}]"
            )
        if not 1 <= values["layout"] <= MAX_AXIS_COUNT:
            raise ValueError(f"layout axis count must lie in [1, {MAX_AXIS_COUNT}]")
        if values["domain"] < MIN_DOMAIN_COUNT:
            raise ValueError(f"domain axis count must be at least {MIN_DOMAIN_COUNT}")
        if values["fault"] < MIN_FAULT_COUNT:
            raise ValueError(f"fault axis count must be at least {MIN_FAULT_COUNT}")
        if not MIN_GENERATOR_CELL_COUNT <= values["generator_cell"] <= MAX_GENERATOR_CELL_COUNT:
            raise ValueError(
                "generator-cell axis count must lie in "
                f"[{MIN_GENERATOR_CELL_COUNT}, {MAX_GENERATOR_CELL_COUNT}]"
            )
        if values["domain"] * values["fault"] > MAX_DOMAIN_FAULT_CELLS:
            raise ValueError(f"domain x fault cells cannot exceed {MAX_DOMAIN_FAULT_CELLS}")

    def to_dict(self) -> dict[str, int]:
        return {
            "domain": self.domain,
            "family": self.family,
            "fault": self.fault,
            "generator_cell": self.generator_cell,
            "layout": self.layout,
            "template": self.template,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> AxisCounts:
        value = _mapping(raw, "axis counts")
        expected = {"domain", "family", "fault", "generator_cell", "layout", "template"}
        _strict_keys(value, expected, "axis counts")
        return cls(
            family=value["family"],
            template=value["template"],
            layout=value["layout"],
            domain=value["domain"],
            fault=value["fault"],
            generator_cell=value["generator_cell"],
        )


@dataclass(frozen=True, slots=True)
class CalibrationPlan:
    """Canonical, immutable preregistration of the selection machinery."""

    schema_version: int
    sealed_profile_digest: str
    generator_source_digest: str
    generator_bundle_digest: str
    evaluator_protocol_digest: str
    derivation_algorithm_digest: str
    derivation_parameters_digest: str
    case_count: int
    axis_counts: AxisCounts
    eligible_arities: tuple[int, ...]
    position_blind_template_fingerprints: tuple[str, ...]
    generator_cell_signatures: tuple[str, ...]
    verifier_implementation_digests: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self) is not CalibrationPlan:
            raise ValueError("calibration plan subclasses are forbidden")
        if _integer(self.schema_version, "plan schema version") != PLAN_SCHEMA_VERSION:
            raise ValueError(f"plan schema version must be {PLAN_SCHEMA_VERSION}")
        profile = _sha256(self.sealed_profile_digest, "sealed profile digest")
        if not hmac.compare_digest(profile, SEALED_PROFILE_DIGEST):
            raise ValueError("calibration plan rejects any relaxed or substituted profile")
        for name in (
            "generator_source_digest",
            "generator_bundle_digest",
            "evaluator_protocol_digest",
            "derivation_algorithm_digest",
            "derivation_parameters_digest",
        ):
            _sha256(getattr(self, name), name.replace("_", " "))
        case_count = _integer(self.case_count, "case count")
        if not MIN_CALIBRATION_CASES <= case_count <= MAX_CALIBRATION_CASES:
            raise ValueError(
                f"case count must lie in [{MIN_CALIBRATION_CASES}, {MAX_CALIBRATION_CASES}]"
            )
        if type(self.axis_counts) is not AxisCounts:
            raise ValueError("axis_counts must be AxisCounts")
        for name, count in self.axis_counts.to_dict().items():
            if count > case_count:
                raise ValueError(f"{name} axis count cannot exceed case count")

        if type(self.eligible_arities) is not tuple or not self.eligible_arities:
            raise ValueError("eligible arities must be a nonempty tuple")
        if len(self.eligible_arities) > MAX_ELIGIBLE_ARITY - MIN_ELIGIBLE_ARITY + 1:
            raise ValueError("eligible arities contain too many entries")
        for arity in self.eligible_arities:
            value = _integer(arity, "eligible arity")
            if not MIN_ELIGIBLE_ARITY <= value <= MAX_ELIGIBLE_ARITY:
                raise ValueError(
                    f"eligible arity must lie in [{MIN_ELIGIBLE_ARITY}, {MAX_ELIGIBLE_ARITY}]"
                )
        if tuple(sorted(set(self.eligible_arities))) != self.eligible_arities:
            raise ValueError("eligible arities must be strictly increasing and unique")

        per_family_template_floor = max(
            MIN_FAMILY_TEMPLATE_CASES,
            sum(MIN_CASES_PER_ELIGIBLE_RANK * arity for arity in self.eligible_arities),
        )
        feasibility_bounds = {
            "domain x fault cell support": (
                self.axis_counts.domain * self.axis_counts.fault * MIN_DOMAIN_FAULT_CELL_CASES
            ),
            "family support": self.axis_counts.family * MIN_FAMILY_CASES,
            "generator-cell support": (self.axis_counts.generator_cell * MIN_GENERATOR_CELL_CASES),
            "template support": self.axis_counts.template * MIN_TEMPLATE_CASES,
            "family x domain support": (
                self.axis_counts.family * self.axis_counts.domain * MIN_FAMILY_DOMAIN_CASES
            ),
            "family x fault support": (
                self.axis_counts.family * self.axis_counts.fault * MIN_FAMILY_FAULT_CASES
            ),
            "complete family x template support": (
                self.axis_counts.family * self.axis_counts.template * per_family_template_floor
            ),
            "family x domain x fault support": (
                self.axis_counts.family
                * self.axis_counts.domain
                * self.axis_counts.fault
                * MIN_FAMILY_DOMAIN_CASES
            ),
        }
        for label, required_cases in feasibility_bounds.items():
            if required_cases > case_count:
                raise ValueError(
                    f"case count cannot satisfy sealed {label}: requires at least {required_cases}"
                )
        if not _family_template_case_count_is_feasible(
            case_count=case_count,
            family_count=self.axis_counts.family,
            template_count=self.axis_counts.template,
            eligible_arities=self.eligible_arities,
        ):
            raise ValueError(
                "case count cannot realize the discrete family x template "
                "fault-position constraints"
            )

        self._validate_digest_axis(
            self.position_blind_template_fingerprints,
            self.axis_counts.template,
            "position-blind template fingerprints",
        )
        self._validate_digest_axis(
            self.generator_cell_signatures,
            self.axis_counts.generator_cell,
            "generator-cell signatures",
        )
        verifiers = self.verifier_implementation_digests
        if type(verifiers) is not tuple:
            raise ValueError("verifier implementation digests must be a tuple")
        if not 2 <= len(verifiers) <= MAX_VERIFIER_IMPLEMENTATIONS:
            raise ValueError(
                "plan requires between two and "
                f"{MAX_VERIFIER_IMPLEMENTATIONS} verifier implementations"
            )
        for digest in verifiers:
            _sha256(digest, "verifier implementation digest")
        if tuple(sorted(set(verifiers))) != verifiers:
            raise ValueError("verifier implementation digests must be sorted and unique")

    @staticmethod
    def _validate_digest_axis(values: Any, expected_count: int, label: str) -> None:
        if type(values) is not tuple or len(values) != expected_count:
            raise ValueError(f"{label} must contain exactly {expected_count} entries")
        for value in values:
            _sha256(value, label[:-1])
        if len(set(values)) != len(values):
            raise ValueError(f"{label} must be unique; aliases are forbidden")

    @classmethod
    def build(
        cls,
        *,
        generator_source_digest: str,
        generator_bundle_digest: str,
        evaluator_protocol_digest: str,
        derivation_algorithm_digest: str,
        derivation_parameters_digest: str,
        case_count: int,
        axis_counts: AxisCounts,
        eligible_arities: Iterable[int],
        position_blind_template_fingerprints: Iterable[str],
        generator_cell_signatures: Iterable[str],
        verifier_implementation_digests: Iterable[str],
    ) -> CalibrationPlan:
        return cls(
            schema_version=PLAN_SCHEMA_VERSION,
            sealed_profile_digest=SEALED_PROFILE_DIGEST,
            generator_source_digest=generator_source_digest,
            generator_bundle_digest=generator_bundle_digest,
            evaluator_protocol_digest=evaluator_protocol_digest,
            derivation_algorithm_digest=derivation_algorithm_digest,
            derivation_parameters_digest=derivation_parameters_digest,
            case_count=case_count,
            axis_counts=axis_counts,
            eligible_arities=_bounded_tuple(
                eligible_arities,
                label="eligible arities",
                maximum=MAX_ELIGIBLE_ARITY,
            ),
            position_blind_template_fingerprints=_bounded_tuple(
                position_blind_template_fingerprints,
                label="position-blind template fingerprints",
                maximum=MAX_AXIS_COUNT,
            ),
            generator_cell_signatures=_bounded_tuple(
                generator_cell_signatures,
                label="generator-cell signatures",
                maximum=MAX_AXIS_COUNT,
            ),
            verifier_implementation_digests=_bounded_tuple(
                verifier_implementation_digests,
                label="verifier implementation digests",
                maximum=MAX_VERIFIER_IMPLEMENTATIONS,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "axis_counts": self.axis_counts.to_dict(),
            "case_count": self.case_count,
            "derivation_algorithm_digest": self.derivation_algorithm_digest,
            "derivation_parameters_digest": self.derivation_parameters_digest,
            "eligible_arities": list(self.eligible_arities),
            "evaluator_protocol_digest": self.evaluator_protocol_digest,
            "generator_bundle_digest": self.generator_bundle_digest,
            "generator_cell_signatures": list(self.generator_cell_signatures),
            "generator_source_digest": self.generator_source_digest,
            "position_blind_template_fingerprints": list(self.position_blind_template_fingerprints),
            "schema": PLAN_SCHEMA,
            "schema_version": self.schema_version,
            "sealed_profile_digest": self.sealed_profile_digest,
            "verifier_implementation_digests": list(self.verifier_implementation_digests),
        }

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())

    def digest(self) -> str:
        return _digest(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Any) -> CalibrationPlan:
        value = _mapping(raw, "calibration plan")
        expected = {
            "axis_counts",
            "case_count",
            "derivation_algorithm_digest",
            "derivation_parameters_digest",
            "eligible_arities",
            "evaluator_protocol_digest",
            "generator_bundle_digest",
            "generator_cell_signatures",
            "generator_source_digest",
            "position_blind_template_fingerprints",
            "schema",
            "schema_version",
            "sealed_profile_digest",
            "verifier_implementation_digests",
        }
        _strict_keys(value, expected, "calibration plan")
        if value["schema"] != PLAN_SCHEMA:
            raise ValueError("invalid calibration plan schema")
        return cls(
            schema_version=value["schema_version"],
            sealed_profile_digest=value["sealed_profile_digest"],
            generator_source_digest=value["generator_source_digest"],
            generator_bundle_digest=value["generator_bundle_digest"],
            evaluator_protocol_digest=value["evaluator_protocol_digest"],
            derivation_algorithm_digest=value["derivation_algorithm_digest"],
            derivation_parameters_digest=value["derivation_parameters_digest"],
            case_count=value["case_count"],
            axis_counts=AxisCounts.from_dict(value["axis_counts"]),
            eligible_arities=tuple(
                _bounded_list(
                    value["eligible_arities"],
                    label="eligible arities",
                    maximum=MAX_ELIGIBLE_ARITY - MIN_ELIGIBLE_ARITY + 1,
                )
            ),
            position_blind_template_fingerprints=tuple(
                _bounded_list(
                    value["position_blind_template_fingerprints"],
                    label="position-blind template fingerprints",
                    maximum=MAX_TEMPLATE_COUNT,
                )
            ),
            generator_cell_signatures=tuple(
                _bounded_list(
                    value["generator_cell_signatures"],
                    label="generator-cell signatures",
                    maximum=MAX_GENERATOR_CELL_COUNT,
                )
            ),
            verifier_implementation_digests=tuple(
                _bounded_list(
                    value["verifier_implementation_digests"],
                    label="verifier implementation digests",
                    maximum=MAX_VERIFIER_IMPLEMENTATIONS,
                )
            ),
        )

    @classmethod
    def from_json(cls, raw: str) -> CalibrationPlan:
        return cls.from_dict(
            _load_canonical_json(
                raw,
                label="calibration plan",
                max_bytes=MAX_PLAN_JSON_BYTES,
            )
        )


def selection_ticket(
    plan: CalibrationPlan,
    seed: str,
    *,
    generator_counter: int,
    stable_case_id: str,
    outcome_free_content_digest: str,
    semantic_program_digest: str,
    solver_view_digest: str,
) -> str:
    """Derive the canonical seed ticket for one outcome-free generated case."""

    plan = _reconstruct_plan(plan)
    canonical_seed = _text(seed, "calibration seed", max_bytes=MAX_TEXT_BYTES)
    counter = _integer(generator_counter, "generator counter")
    if not 0 <= counter < plan.case_count:
        raise ValueError("generator counter is outside the exact plan range")
    case_id = _text(
        stable_case_id,
        "stable case id",
        max_bytes=MAX_STABLE_CASE_ID_BYTES,
    )
    content = _sha256(outcome_free_content_digest, "outcome-free content digest")
    program = _sha256(semantic_program_digest, "semantic program digest")
    view = _sha256(solver_view_digest, "solver view digest")
    return _digest(
        {
            "generator_counter": counter,
            "outcome_free_content_digest": content,
            "plan_digest": plan.digest(),
            "schema": SELECTION_TICKET_SCHEMA,
            "schema_version": SELECTION_TICKET_SCHEMA_VERSION,
            "seed": canonical_seed,
            "semantic_program_digest": program,
            "solver_view_digest": view,
            "stable_case_id": case_id,
        }
    )


@dataclass(frozen=True, slots=True)
class ManifestRow:
    case_index: int
    coordinate_digest: str
    stable_case_id: str
    generator_counter: int
    outcome_free_content_digest: str
    semantic_program_digest: str
    solver_view_digest: str
    selection_ticket: str
    family_index: int
    template_index: int
    layout_index: int
    domain_index: int
    fault_index: int
    generator_cell_index: int
    eligible_arity: int
    fault_rank: int

    def __post_init__(self) -> None:
        if type(self) is not ManifestRow:
            raise ValueError("manifest row subclasses are forbidden")
        for name in (
            "case_index",
            "generator_counter",
            "family_index",
            "template_index",
            "layout_index",
            "domain_index",
            "fault_index",
            "generator_cell_index",
            "eligible_arity",
            "fault_rank",
        ):
            value = _integer(getattr(self, name), name.replace("_", " "))
            if value < 0:
                raise ValueError(f"{name.replace('_', ' ')} must be non-negative")
        for name in (
            "case_index",
            "generator_counter",
            "family_index",
            "template_index",
            "layout_index",
            "domain_index",
            "fault_index",
            "generator_cell_index",
        ):
            if getattr(self, name) >= MAX_AXIS_COUNT:
                raise ValueError(f"{name.replace('_', ' ')} must be smaller than {MAX_AXIS_COUNT}")
        _sha256(self.coordinate_digest, "coordinate digest")
        _text(
            self.stable_case_id,
            "stable case id",
            max_bytes=MAX_STABLE_CASE_ID_BYTES,
        )
        _sha256(self.outcome_free_content_digest, "outcome-free content digest")
        _sha256(self.semantic_program_digest, "semantic program digest")
        _sha256(self.solver_view_digest, "solver view digest")
        _sha256(self.selection_ticket, "selection ticket")
        if not MIN_ELIGIBLE_ARITY <= self.eligible_arity <= MAX_ELIGIBLE_ARITY:
            raise ValueError("eligible arity is outside the supported range")
        if self.fault_rank >= self.eligible_arity:
            raise ValueError("fault rank must be smaller than eligible arity")

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_index": self.case_index,
            "coordinate_digest": self.coordinate_digest,
            "generator_counter": self.generator_counter,
            "domain_index": self.domain_index,
            "eligible_arity": self.eligible_arity,
            "family_index": self.family_index,
            "fault_index": self.fault_index,
            "fault_rank": self.fault_rank,
            "generator_cell_index": self.generator_cell_index,
            "layout_index": self.layout_index,
            "outcome_free_content_digest": self.outcome_free_content_digest,
            "selection_ticket": self.selection_ticket,
            "semantic_program_digest": self.semantic_program_digest,
            "solver_view_digest": self.solver_view_digest,
            "stable_case_id": self.stable_case_id,
            "template_index": self.template_index,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> ManifestRow:
        value = _mapping(raw, "manifest row")
        expected = {
            "case_index",
            "coordinate_digest",
            "domain_index",
            "eligible_arity",
            "family_index",
            "fault_index",
            "fault_rank",
            "generator_counter",
            "generator_cell_index",
            "layout_index",
            "outcome_free_content_digest",
            "selection_ticket",
            "semantic_program_digest",
            "solver_view_digest",
            "stable_case_id",
            "template_index",
        }
        _strict_keys(value, expected, "manifest row")
        return cls(**{name: value[name] for name in expected})


def _require_diversity(
    rows: tuple[ManifestRow, ...],
    *,
    label: str,
    minimum_cases: int,
    dimensions: tuple[tuple[str, int, str], ...],
) -> None:
    if len(rows) < minimum_cases:
        raise ValueError(f"{label} has fewer than {minimum_cases} rows")
    for dimension, minimum, attribute in dimensions:
        observed = len({getattr(row, attribute) for row in rows})
        if observed < minimum:
            raise ValueError(
                f"{label} has fewer than {minimum} distinct {dimension}; observed={observed}"
            )


def _require_rank_support(
    rows: tuple[ManifestRow, ...],
    *,
    label: str,
    required_arities: tuple[int, ...] | None = None,
) -> dict[int, dict[int, int]]:
    grouped: dict[int, dict[int, int]] = {}
    for row in rows:
        counts = grouped.setdefault(row.eligible_arity, {})
        counts[row.fault_rank] = counts.get(row.fault_rank, 0) + 1
    if required_arities is not None and set(grouped) != set(required_arities):
        missing = sorted(set(required_arities) - set(grouped))
        raise ValueError(f"{label} omits declared eligible arities: {missing}")
    if not grouped:
        raise ValueError(f"{label} has no eligible-rank observations")

    for arity, counts in grouped.items():
        thin = {
            rank: counts.get(rank, 0)
            for rank in range(arity)
            if counts.get(rank, 0) < MIN_CASES_PER_ELIGIBLE_RANK
        }
        if thin:
            raise ValueError(
                f"{label} eligible-rank support is incomplete for arity={arity}; thin={thin}"
            )
    return grouped


def _validate_position_balance(
    rows: tuple[ManifestRow, ...],
    *,
    label: str,
    required_arities: tuple[int, ...] | None = None,
) -> None:
    grouped = _require_rank_support(
        rows,
        label=label,
        required_arities=required_arities,
    )
    total = len(rows)
    weighted_entropy = 0.0
    weighted_tv = 0.0
    maximum_share = 0.0
    for arity, counts in grouped.items():
        support = sum(counts.values())
        probabilities = tuple(counts.get(rank, 0) / support for rank in range(arity))
        entropy = -sum(
            probability * math.log(probability)
            for probability in probabilities
            if probability > 0.0
        )
        normalized_entropy = entropy / math.log(arity)
        uniform = 1.0 / arity
        total_variation = 0.5 * sum(abs(probability - uniform) for probability in probabilities)
        weight = support / total
        weighted_entropy += weight * normalized_entropy
        weighted_tv += weight * total_variation
        maximum_share = max(maximum_share, max(probabilities))

    rounded_entropy = round(weighted_entropy, 6)
    rounded_tv = round(weighted_tv, 6)
    rounded_share = round(maximum_share, 6)
    if rounded_entropy < MIN_FAULT_POSITION_ENTROPY:
        raise ValueError(f"{label} fault-position entropy is below the sealed floor")
    if rounded_share > MAX_FAULT_POSITION_SHARE:
        raise ValueError(f"{label} maximum fault-position share exceeds the sealed ceiling")
    if rounded_tv > MAX_POSITION_UNIFORM_TOTAL_VARIATION:
        raise ValueError(f"{label} fault-position total variation exceeds the sealed ceiling")


@dataclass(frozen=True, slots=True)
class SelectionManifest:
    """Complete, canonical selection rows bound to one exact plan digest."""

    schema_version: int
    plan_digest: str
    seed: str
    rows: tuple[ManifestRow, ...]

    def __post_init__(self) -> None:
        if type(self) is not SelectionManifest:
            raise ValueError("selection manifest subclasses are forbidden")
        if _integer(self.schema_version, "manifest schema version") != MANIFEST_SCHEMA_VERSION:
            raise ValueError(f"manifest schema version must be {MANIFEST_SCHEMA_VERSION}")
        _sha256(self.plan_digest, "plan digest")
        _text(self.seed, "calibration seed", max_bytes=MAX_TEXT_BYTES)
        if type(self.rows) is not tuple:
            raise ValueError("manifest rows must be a tuple")
        if not MIN_CALIBRATION_CASES <= len(self.rows) <= MAX_CALIBRATION_CASES:
            raise ValueError(
                "manifest must contain between "
                f"{MIN_CALIBRATION_CASES} and {MAX_CALIBRATION_CASES} rows"
            )
        if not all(type(row) is ManifestRow for row in self.rows):
            raise ValueError("manifest rows must be ManifestRow values")
        if tuple(row.case_index for row in self.rows) != tuple(range(len(self.rows))):
            raise ValueError("manifest rows must have contiguous canonical case indices")
        coordinate_digests = tuple(row.coordinate_digest for row in self.rows)
        if len(set(coordinate_digests)) != len(coordinate_digests):
            raise ValueError("manifest coordinate digests must be unique")
        unique_fields = {
            "stable case ids": tuple(row.stable_case_id for row in self.rows),
            "generator counters": tuple(row.generator_counter for row in self.rows),
            "outcome-free content digests": tuple(
                row.outcome_free_content_digest for row in self.rows
            ),
            "semantic program digests": tuple(row.semantic_program_digest for row in self.rows),
            "solver view digests": tuple(row.solver_view_digest for row in self.rows),
            "selection tickets": tuple(row.selection_ticket for row in self.rows),
        }
        for label, values in unique_fields.items():
            if len(set(values)) != len(values):
                raise ValueError(f"manifest {label} must be unique")
        counters = tuple(row.generator_counter for row in self.rows)
        if set(counters) != set(range(len(self.rows))):
            raise ValueError("generator counters must be the complete canonical range")
        tickets = tuple(row.selection_ticket for row in self.rows)
        if tickets != tuple(sorted(tickets)):
            raise ValueError("manifest rows must be ordered by selection ticket")

    @classmethod
    def build(
        cls,
        plan: CalibrationPlan,
        seed: str,
        rows: Iterable[ManifestRow],
    ) -> SelectionManifest:
        if type(plan) is not CalibrationPlan:
            raise ValueError("plan must be an exact CalibrationPlan")
        manifest = cls(
            schema_version=MANIFEST_SCHEMA_VERSION,
            plan_digest=plan.digest(),
            seed=_text(seed, "calibration seed", max_bytes=MAX_TEXT_BYTES),
            rows=_bounded_tuple(
                rows,
                label="selection manifest rows",
                maximum=MAX_CALIBRATION_CASES,
            ),
        )
        manifest.validate_against(plan)
        return manifest

    def validate_against(self, plan: CalibrationPlan) -> None:
        self = _reconstruct_manifest(self)
        plan = _reconstruct_plan(plan)
        if not hmac.compare_digest(self.plan_digest, plan.digest()):
            raise ValueError("manifest plan digest does not match the exact calibration plan")
        if len(self.rows) != plan.case_count:
            raise ValueError("manifest row count does not match plan case count")
        counts = plan.axis_counts
        axis_values: dict[str, list[int]] = {
            "family": [],
            "template": [],
            "layout": [],
            "domain": [],
            "fault": [],
            "generator cell": [],
        }
        for row in self.rows:
            expected_ticket = selection_ticket(
                plan,
                self.seed,
                generator_counter=row.generator_counter,
                stable_case_id=row.stable_case_id,
                outcome_free_content_digest=row.outcome_free_content_digest,
                semantic_program_digest=row.semantic_program_digest,
                solver_view_digest=row.solver_view_digest,
            )
            if not hmac.compare_digest(row.selection_ticket, expected_ticket):
                raise ValueError("selection ticket does not match plan, seed, counter, and content")
            bounds = {
                "family": (row.family_index, counts.family),
                "template": (row.template_index, counts.template),
                "layout": (row.layout_index, counts.layout),
                "domain": (row.domain_index, counts.domain),
                "fault": (row.fault_index, counts.fault),
                "generator cell": (row.generator_cell_index, counts.generator_cell),
            }
            for label, (index, upper) in bounds.items():
                if index >= upper:
                    raise ValueError(f"{label} index {index} is outside plan bounds")
                axis_values[label].append(index)
            if row.eligible_arity not in plan.eligible_arities:
                raise ValueError("manifest row eligible arity is not preregistered")

        expected_axis_counts = {
            "family": counts.family,
            "template": counts.template,
            "layout": counts.layout,
            "domain": counts.domain,
            "fault": counts.fault,
            "generator cell": counts.generator_cell,
        }
        for label, upper in expected_axis_counts.items():
            observed = set(axis_values[label])
            if observed != set(range(upper)):
                missing = sorted(set(range(upper)) - observed)
                raise ValueError(f"{label} axis is incomplete; missing indices={missing}")

        family_rows: dict[int, list[ManifestRow]] = {}
        template_rows: dict[int, list[ManifestRow]] = {}
        cell_rows: dict[int, list[ManifestRow]] = {}
        domain_fault_rows: dict[tuple[int, int], list[ManifestRow]] = {}
        family_template_rows: dict[tuple[int, int], list[ManifestRow]] = {}
        for row in self.rows:
            family_rows.setdefault(row.family_index, []).append(row)
            template_rows.setdefault(row.template_index, []).append(row)
            cell_rows.setdefault(row.generator_cell_index, []).append(row)
            domain_fault_rows.setdefault((row.domain_index, row.fault_index), []).append(row)
            family_template_rows.setdefault((row.family_index, row.template_index), []).append(row)

        for family_index in range(counts.family):
            rows = tuple(family_rows.get(family_index, ()))
            label = f"family index {family_index}"
            _require_diversity(
                rows,
                label=label,
                minimum_cases=MIN_FAMILY_CASES,
                dimensions=(
                    ("templates", MIN_FAMILY_TEMPLATES, "template_index"),
                    ("domains", MIN_FAMILY_DOMAINS, "domain_index"),
                    ("faults", MIN_FAMILY_FAULTS, "fault_index"),
                ),
            )
            for domain_index in range(counts.domain):
                for fault_index in range(counts.fault):
                    support = sum(
                        row.domain_index == domain_index and row.fault_index == fault_index
                        for row in rows
                    )
                    if support < MIN_FAMILY_DOMAIN_CASES:
                        raise ValueError(
                            f"family/domain/fault ({family_index}, {domain_index}, "
                            f"{fault_index}) has fewer than {MIN_FAMILY_DOMAIN_CASES} rows"
                        )
        for template_index in range(counts.template):
            rows = tuple(template_rows.get(template_index, ()))
            label = f"template index {template_index}"
            _require_diversity(
                rows,
                label=label,
                minimum_cases=MIN_TEMPLATE_CASES,
                dimensions=(
                    ("families", MIN_TEMPLATE_FAMILIES, "family_index"),
                    ("domains", MIN_TEMPLATE_DOMAINS, "domain_index"),
                    ("faults", MIN_TEMPLATE_FAULTS, "fault_index"),
                ),
            )
        for cell_index in range(counts.generator_cell):
            rows = tuple(cell_rows.get(cell_index, ()))
            label = f"generator cell index {cell_index}"
            _require_diversity(
                rows,
                label=label,
                minimum_cases=MIN_GENERATOR_CELL_CASES,
                dimensions=(
                    ("families", MIN_GENERATOR_CELL_FAMILIES, "family_index"),
                    ("templates", MIN_GENERATOR_CELL_TEMPLATES, "template_index"),
                    ("domains", MIN_GENERATOR_CELL_DOMAINS, "domain_index"),
                    ("faults", MIN_GENERATOR_CELL_FAULTS, "fault_index"),
                ),
            )
            _require_rank_support(
                rows,
                label=label,
                required_arities=plan.eligible_arities,
            )
        for domain_index in range(counts.domain):
            for fault_index in range(counts.fault):
                rows = tuple(domain_fault_rows.get((domain_index, fault_index), ()))
                label = f"domain/fault ({domain_index}, {fault_index})"
                _require_diversity(
                    rows,
                    label=label,
                    minimum_cases=MIN_DOMAIN_FAULT_CELL_CASES,
                    dimensions=(
                        ("families", MIN_DOMAIN_FAULT_CELL_FAMILIES, "family_index"),
                        ("templates", MIN_DOMAIN_FAULT_CELL_TEMPLATES, "template_index"),
                    ),
                )
                _require_rank_support(
                    rows,
                    label=label,
                    required_arities=plan.eligible_arities,
                )
        for family_index in range(counts.family):
            for template_index in range(counts.template):
                rows = tuple(family_template_rows.get((family_index, template_index), ()))
                label = f"family/template ({family_index}, {template_index})"
                _require_diversity(
                    rows,
                    label=label,
                    minimum_cases=MIN_FAMILY_TEMPLATE_CASES,
                    dimensions=(
                        ("domains", MIN_FAMILY_TEMPLATE_DOMAINS, "domain_index"),
                        ("faults", MIN_FAMILY_TEMPLATE_FAULTS, "fault_index"),
                        (
                            "generator cells",
                            MIN_FAMILY_TEMPLATE_GENERATOR_CELLS,
                            "generator_cell_index",
                        ),
                    ),
                )
                _require_rank_support(
                    rows,
                    label=label,
                    required_arities=plan.eligible_arities,
                )

        _validate_position_balance(
            self.rows,
            label="global manifest",
            required_arities=plan.eligible_arities,
        )
        for family_index in range(counts.family):
            _validate_position_balance(
                tuple(family_rows[family_index]),
                label=f"family index {family_index}",
            )
        for template_index in range(counts.template):
            _validate_position_balance(
                tuple(template_rows[template_index]),
                label=f"template index {template_index}",
            )
        for cell_index in range(counts.generator_cell):
            _validate_position_balance(
                tuple(cell_rows[cell_index]),
                label=f"generator cell index {cell_index}",
                required_arities=plan.eligible_arities,
            )
        for domain_index in range(counts.domain):
            for fault_index in range(counts.fault):
                _validate_position_balance(
                    tuple(domain_fault_rows[(domain_index, fault_index)]),
                    label=f"domain/fault ({domain_index}, {fault_index})",
                    required_arities=plan.eligible_arities,
                )
        for family_index in range(counts.family):
            for template_index in range(counts.template):
                rows = tuple(family_template_rows[(family_index, template_index)])
                label = f"family/template ({family_index}, {template_index})"
                _validate_position_balance(
                    rows,
                    label=label,
                    required_arities=plan.eligible_arities,
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_digest": self.plan_digest,
            "rows": [row.to_dict() for row in self.rows],
            "schema": MANIFEST_SCHEMA,
            "schema_version": self.schema_version,
            "seed": self.seed,
        }

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())

    def digest(self) -> str:
        return _digest(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Any) -> SelectionManifest:
        value = _mapping(raw, "selection manifest")
        expected = {"plan_digest", "rows", "schema", "schema_version", "seed"}
        _strict_keys(value, expected, "selection manifest")
        if value["schema"] != MANIFEST_SCHEMA:
            raise ValueError("invalid selection manifest schema")
        rows = _bounded_list(
            value["rows"],
            label="selection manifest rows",
            maximum=MAX_CALIBRATION_CASES,
        )
        return cls(
            schema_version=value["schema_version"],
            plan_digest=value["plan_digest"],
            seed=value["seed"],
            rows=tuple(ManifestRow.from_dict(row) for row in rows),
        )

    @classmethod
    def from_json(cls, raw: str) -> SelectionManifest:
        return cls.from_dict(
            _load_canonical_json(
                raw,
                label="selection manifest",
                max_bytes=MAX_MANIFEST_JSON_BYTES,
            )
        )


@dataclass(frozen=True, slots=True)
class MaterializedCaseBinding:
    """Public selection metadata only; there is intentionally no outcome field."""

    case_index: int
    stable_case_id: str
    generator_counter: int
    outcome_free_content_digest: str
    semantic_program_digest: str
    solver_view_digest: str
    selection_ticket: str
    family_label: str
    template_label: str
    layout_label: str
    domain_label: str
    fault_label: str
    generator_cell_label: str
    template_fingerprint: str
    generator_cell_signature: str
    verifier_implementation_digests: tuple[str, ...]
    eligible_fault_lines: tuple[int, ...]
    fault_line: int

    def __post_init__(self) -> None:
        if type(self) is not MaterializedCaseBinding:
            raise ValueError("materialized case binding subclasses are forbidden")
        if _integer(self.case_index, "case index") < 0:
            raise ValueError("case index must be non-negative")
        if self.case_index >= MAX_CALIBRATION_CASES:
            raise ValueError("case index exceeds the supported calibration range")
        counter = _integer(self.generator_counter, "generator counter")
        if not 0 <= counter < MAX_CALIBRATION_CASES:
            raise ValueError("generator counter exceeds the supported calibration range")
        _text(
            self.stable_case_id,
            "stable case id",
            max_bytes=MAX_STABLE_CASE_ID_BYTES,
        )
        _sha256(self.outcome_free_content_digest, "outcome-free content digest")
        _sha256(self.semantic_program_digest, "semantic program digest")
        _sha256(self.solver_view_digest, "solver view digest")
        _sha256(self.selection_ticket, "selection ticket")
        for name in (
            "family_label",
            "template_label",
            "layout_label",
            "domain_label",
            "fault_label",
            "generator_cell_label",
        ):
            _text(getattr(self, name), name.replace("_", " "), max_bytes=MAX_LABEL_BYTES)
        _sha256(self.template_fingerprint, "template fingerprint")
        _sha256(self.generator_cell_signature, "generator-cell signature")
        verifiers = self.verifier_implementation_digests
        if type(verifiers) is not tuple or not 2 <= len(verifiers) <= MAX_VERIFIER_IMPLEMENTATIONS:
            raise ValueError(
                "materialized case requires between two and "
                f"{MAX_VERIFIER_IMPLEMENTATIONS} verifier digests"
            )
        for digest in verifiers:
            _sha256(digest, "verifier implementation digest")
        if tuple(sorted(set(verifiers))) != verifiers:
            raise ValueError("materialized verifier digests must be sorted and unique")
        lines = self.eligible_fault_lines
        if type(lines) is not tuple:
            raise ValueError("eligible fault lines must be a tuple")
        if not MIN_ELIGIBLE_ARITY <= len(lines) <= MAX_ELIGIBLE_ARITY:
            raise ValueError("eligible fault lines have unsupported arity")
        for line in lines:
            value = _integer(line, "eligible fault line")
            if not MIN_SUPPORTED_FAULT_LINE <= value <= MAX_SUPPORTED_FAULT_LINE:
                raise ValueError(
                    "eligible fault lines must lie in the supported solver trace range"
                )
        if tuple(sorted(set(lines))) != lines:
            raise ValueError("eligible fault lines must be sorted and unique")
        fault_line = _integer(self.fault_line, "fault line")
        if not MIN_SUPPORTED_FAULT_LINE <= fault_line <= MAX_SUPPORTED_FAULT_LINE:
            raise ValueError("fault line must lie in the supported solver trace range")
        if fault_line not in lines:
            raise ValueError("fault line must be one of the eligible fault lines")

    def coordinate_payload(self) -> dict[str, Any]:
        return {
            "case_index": self.case_index,
            "domain_label": self.domain_label,
            "eligible_fault_lines": list(self.eligible_fault_lines),
            "family_label": self.family_label,
            "fault_label": self.fault_label,
            "fault_line": self.fault_line,
            "generator_counter": self.generator_counter,
            "generator_cell_label": self.generator_cell_label,
            "generator_cell_signature": self.generator_cell_signature,
            "layout_label": self.layout_label,
            "outcome_free_content_digest": self.outcome_free_content_digest,
            "schema": MATERIALIZED_BINDING_SCHEMA,
            "schema_version": MATERIALIZED_BINDING_SCHEMA_VERSION,
            "selection_ticket": self.selection_ticket,
            "semantic_program_digest": self.semantic_program_digest,
            "solver_view_digest": self.solver_view_digest,
            "stable_case_id": self.stable_case_id,
            "template_fingerprint": self.template_fingerprint,
            "template_label": self.template_label,
            "verifier_implementation_digests": list(self.verifier_implementation_digests),
        }

    def coordinate_digest(self) -> str:
        return _digest(self.coordinate_payload())


def _reconstruct_axis_counts(value: AxisCounts) -> AxisCounts:
    if type(value) is not AxisCounts:
        raise ValueError("axis counts must be an exact AxisCounts record")
    return AxisCounts(
        family=value.family,
        template=value.template,
        layout=value.layout,
        domain=value.domain,
        fault=value.fault,
        generator_cell=value.generator_cell,
    )


def _reconstruct_plan(value: CalibrationPlan) -> CalibrationPlan:
    if type(value) is not CalibrationPlan:
        raise ValueError("plan must be an exact CalibrationPlan")
    axis_counts = _reconstruct_axis_counts(value.axis_counts)
    return CalibrationPlan(
        schema_version=value.schema_version,
        sealed_profile_digest=value.sealed_profile_digest,
        generator_source_digest=value.generator_source_digest,
        generator_bundle_digest=value.generator_bundle_digest,
        evaluator_protocol_digest=value.evaluator_protocol_digest,
        derivation_algorithm_digest=value.derivation_algorithm_digest,
        derivation_parameters_digest=value.derivation_parameters_digest,
        case_count=value.case_count,
        axis_counts=axis_counts,
        eligible_arities=value.eligible_arities,
        position_blind_template_fingerprints=value.position_blind_template_fingerprints,
        generator_cell_signatures=value.generator_cell_signatures,
        verifier_implementation_digests=value.verifier_implementation_digests,
    )


def _reconstruct_manifest_row(value: ManifestRow) -> ManifestRow:
    if type(value) is not ManifestRow:
        raise ValueError("manifest rows must be exact ManifestRow records")
    return ManifestRow(
        case_index=value.case_index,
        coordinate_digest=value.coordinate_digest,
        stable_case_id=value.stable_case_id,
        generator_counter=value.generator_counter,
        outcome_free_content_digest=value.outcome_free_content_digest,
        semantic_program_digest=value.semantic_program_digest,
        solver_view_digest=value.solver_view_digest,
        selection_ticket=value.selection_ticket,
        family_index=value.family_index,
        template_index=value.template_index,
        layout_index=value.layout_index,
        domain_index=value.domain_index,
        fault_index=value.fault_index,
        generator_cell_index=value.generator_cell_index,
        eligible_arity=value.eligible_arity,
        fault_rank=value.fault_rank,
    )


def _reconstruct_manifest(value: SelectionManifest) -> SelectionManifest:
    if type(value) is not SelectionManifest:
        raise ValueError("manifest must be an exact SelectionManifest")
    if type(value.rows) is not tuple:
        raise ValueError("manifest rows must be a tuple")
    if not MIN_CALIBRATION_CASES <= len(value.rows) <= MAX_CALIBRATION_CASES:
        raise ValueError(
            "manifest must contain between "
            f"{MIN_CALIBRATION_CASES} and {MAX_CALIBRATION_CASES} rows"
        )
    rows = tuple(_reconstruct_manifest_row(row) for row in value.rows)
    return SelectionManifest(
        schema_version=value.schema_version,
        plan_digest=value.plan_digest,
        seed=value.seed,
        rows=rows,
    )


def _reconstruct_binding(value: MaterializedCaseBinding) -> MaterializedCaseBinding:
    if type(value) is not MaterializedCaseBinding:
        raise ValueError("materialized cases must be exact MaterializedCaseBinding records")
    return MaterializedCaseBinding(
        case_index=value.case_index,
        stable_case_id=value.stable_case_id,
        generator_counter=value.generator_counter,
        outcome_free_content_digest=value.outcome_free_content_digest,
        semantic_program_digest=value.semantic_program_digest,
        solver_view_digest=value.solver_view_digest,
        selection_ticket=value.selection_ticket,
        family_label=value.family_label,
        template_label=value.template_label,
        layout_label=value.layout_label,
        domain_label=value.domain_label,
        fault_label=value.fault_label,
        generator_cell_label=value.generator_cell_label,
        template_fingerprint=value.template_fingerprint,
        generator_cell_signature=value.generator_cell_signature,
        verifier_implementation_digests=value.verifier_implementation_digests,
        eligible_fault_lines=value.eligible_fault_lines,
        fault_line=value.fault_line,
    )


def _bind_label(
    *,
    axis: str,
    index: int,
    label: str,
    index_to_label: dict[int, str],
    label_to_index: dict[str, int],
) -> None:
    previous_label = index_to_label.setdefault(index, label)
    if previous_label != label:
        raise ValueError(f"{axis} index aliases two labels")
    previous_index = label_to_index.setdefault(label, index)
    if previous_index != index:
        raise ValueError(f"{axis} label aliases two indices")


def validate_materialization(
    plan: CalibrationPlan,
    manifest: SelectionManifest,
    cases: Iterable[MaterializedCaseBinding],
) -> None:
    """Validate the complete public materialization without accepting outcomes.

    The typed input is a capability boundary: a caller must project generated cases to
    :class:`MaterializedCaseBinding`, which has no accuracy, prediction, answer, or model
    response fields.
    """

    plan = _reconstruct_plan(plan)
    manifest = _reconstruct_manifest(manifest)
    manifest.validate_against(plan)
    materialized: list[MaterializedCaseBinding] = []
    for index, case in enumerate(cases):
        if index >= MAX_CALIBRATION_CASES:
            raise ValueError("materialization contains too many cases")
        materialized.append(_reconstruct_binding(case))
    if len(materialized) != plan.case_count:
        raise ValueError("materialization case count does not match plan")

    mappings: dict[str, tuple[dict[int, str], dict[str, int], int]] = {
        "family": ({}, {}, plan.axis_counts.family),
        "template": ({}, {}, plan.axis_counts.template),
        "layout": ({}, {}, plan.axis_counts.layout),
        "domain": ({}, {}, plan.axis_counts.domain),
        "fault": ({}, {}, plan.axis_counts.fault),
        "generator cell": ({}, {}, plan.axis_counts.generator_cell),
    }

    for expected_index, (row, case) in enumerate(zip(manifest.rows, materialized, strict=True)):
        if case.case_index != expected_index or row.case_index != expected_index:
            raise ValueError("materialization is not in contiguous manifest order")
        identity_fields = (
            "stable_case_id",
            "generator_counter",
            "outcome_free_content_digest",
            "semantic_program_digest",
            "solver_view_digest",
            "selection_ticket",
        )
        for field in identity_fields:
            if getattr(case, field) != getattr(row, field):
                raise ValueError(f"materialized {field.replace('_', ' ')} does not match manifest")
        if len(case.eligible_fault_lines) != row.eligible_arity:
            raise ValueError("eligible arity does not match the materialized eligible lines")
        # This is the central position invariant.  Do not weaken it to membership-only:
        # a rank/line mismatch silently reintroduces post-selection position freedom.
        if case.fault_line != case.eligible_fault_lines[row.fault_rank]:
            raise ValueError("fault line does not equal eligible_fault_lines[fault_rank]")
        expected_coordinate = case.coordinate_digest()
        if not hmac.compare_digest(row.coordinate_digest, expected_coordinate):
            raise ValueError("manifest coordinate digest does not match materialized metadata")

        expected_template = plan.position_blind_template_fingerprints[row.template_index]
        if not hmac.compare_digest(case.template_fingerprint, expected_template):
            raise ValueError("template fingerprint substitution or index mismatch")
        expected_cell = plan.generator_cell_signatures[row.generator_cell_index]
        if not hmac.compare_digest(case.generator_cell_signature, expected_cell):
            raise ValueError("generator-cell signature substitution or index mismatch")
        if case.verifier_implementation_digests != plan.verifier_implementation_digests:
            raise ValueError("verifier implementation digest substitution or omission")

        coordinates = (
            ("family", row.family_index, case.family_label),
            ("template", row.template_index, case.template_label),
            ("layout", row.layout_index, case.layout_label),
            ("domain", row.domain_index, case.domain_label),
            ("fault", row.fault_index, case.fault_label),
            ("generator cell", row.generator_cell_index, case.generator_cell_label),
        )
        for axis, index, label in coordinates:
            index_to_label, label_to_index, _ = mappings[axis]
            _bind_label(
                axis=axis,
                index=index,
                label=label,
                index_to_label=index_to_label,
                label_to_index=label_to_index,
            )

    for axis, (index_to_label, label_to_index, expected_count) in mappings.items():
        if set(index_to_label) != set(range(expected_count)):
            raise ValueError(f"{axis} index mapping is incomplete")
        if len(label_to_index) != expected_count:
            raise ValueError(f"{axis} label mapping is not bijective")


def preregistration_commitment(
    plan: CalibrationPlan,
    manifest: SelectionManifest,
    seed: str,
) -> str:
    """Bind the exact plan, complete manifest, and canonical seed in one digest."""

    plan = _reconstruct_plan(plan)
    manifest = _reconstruct_manifest(manifest)
    manifest.validate_against(plan)
    canonical_seed = _text(seed, "calibration seed", max_bytes=MAX_TEXT_BYTES)
    if canonical_seed != manifest.seed:
        raise ValueError("commitment seed does not match the ticketed manifest seed")
    return _digest(
        {
            "manifest_digest": manifest.digest(),
            "plan_digest": plan.digest(),
            "schema": COMMITMENT_SCHEMA,
            "schema_version": COMMITMENT_SCHEMA_VERSION,
            "seed": canonical_seed,
        }
    )


__all__ = [
    "COMMITMENT_SCHEMA",
    "MANIFEST_SCHEMA",
    "MAX_CALIBRATION_CASES",
    "MAX_MANIFEST_JSON_BYTES",
    "MAX_PLAN_JSON_BYTES",
    "MIN_CALIBRATION_CASES",
    "PLAN_SCHEMA",
    "SEALED_PROFILE_DIGEST",
    "SELECTION_TICKET_SCHEMA",
    "AxisCounts",
    "CalibrationPlan",
    "ManifestRow",
    "MaterializedCaseBinding",
    "SelectionManifest",
    "preregistration_commitment",
    "selection_ticket",
    "validate_materialization",
]
