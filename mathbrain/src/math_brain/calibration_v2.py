"""Public-only difficulty and shortcut calibration for hard mathematical traces.

This module deliberately has no model runner and no private-data integration.  It consumes
public synthetic cases plus two separately named digest-bound verifier receipts, measures full-case
brain-off difficulty, and tries several cheap line-level classifiers that should *not* be
able to find the faulty line.  All classifiers are standard-library Bernoulli naive Bayes
models evaluated with either the held family or the held template excluded from training.

The resulting report is immutable, canonical, answer-free aggregate evidence.  A separate
one-way state machine commits the calibration seed before the public case set is bound and
refuses to freeze a report unless every gate passes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import islice
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4

from .models import canonical_json


CALIBRATION_SCHEMA = "org.math-research-brain/public-calibration-report"
CALIBRATION_SCHEMA_VERSION = 5
CALIBRATION_INPUT_SCHEMA = "org.math-research-brain/public-calibration-input"
CALIBRATION_INPUT_SCHEMA_VERSION = 4
FREEZE_SCHEMA = "org.math-research-brain/calibration-freeze"
FREEZE_SCHEMA_VERSION = 3
ORACLE_SCHEMA = "org.math-research-brain/public-oracle-receipt"
ORACLE_SCHEMA_VERSION = 1
CASE_SCHEMA = "org.math-research-brain/public-calibration-case"
CASE_SCHEMA_VERSION = 3
CALIBRATION_BUNDLE_SCHEMA = "org.math-research-brain/public-calibration-bundle"
CALIBRATION_BUNDLE_SCHEMA_VERSION = 1
MIN_CALIBRATION_CASES_V2 = 200
MAX_CALIBRATION_CASES_V2 = 400

_MAX_TEXT_BYTES = 32_768
_MAX_CASES = MAX_CALIBRATION_CASES_V2
_MAX_INPUT_BYTES = 16_000_000
_MAX_TRACE_INPUT_LINES = 13
_MAX_ELIGIBLE_INPUT_LINES = 11
_MAX_ORACLE_INPUTS = 9
_SHA256_PREFIX = "sha256:"
_TOKEN_RE = re.compile(r"[a-zA-Z_][a-zA-Z_0-9]*|\d+(?:\.\d+)?|[^\w\s]")
_NUMBER_RE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:/\d+)?")
_SPACE_RE = re.compile(r"\s+")
_SUSPICIOUS_RE = re.compile(
    r"\b(?:blunder|incorrect|obvious(?:ly)?\s+wrong|oops|faulty|mistake|todo|xxx)\b",
    re.IGNORECASE,
)


def _digest(value: Any) -> str:
    return _SHA256_PREFIX + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _strict_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if not all(type(key) is str for key in value):
        raise ValueError(f"{label} keys must be strings")
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"invalid {label} keys; missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{label} must be an object")
    return value


def _text(value: Any, label: str, *, max_bytes: int = _MAX_TEXT_BYTES) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{label} must be a nonempty plain string")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8") from exc
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{label} must use Unicode NFC normalization")
    if size > max_bytes:
        raise ValueError(f"{label} exceeds the {max_bytes}-byte budget")
    return value


def _require_canonical_json_text(raw: str, parsed: Any, label: str) -> None:
    body = raw[:-2] if raw.endswith("\r\n") else raw[:-1] if raw.endswith("\n") else raw
    if body != canonical_json(parsed):
        raise ValueError(f"{label} must use canonical UTF-8 JSON bytes")


def _boolean(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} must be boolean")
    return value


def _integer(value: Any, label: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{label} must be an integer")
    return value


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        raise ValueError("rate denominator must be positive")
    return round(numerator / denominator, 6)


def _bounded_rate(value: Any, label: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError(f"{label} must lie in [0, 1]")
    return result


def _nonnegative_number(value: Any, label: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        raise ValueError(f"{label} must be finite and non-negative")
    return result


def _sha256(value: Any, label: str) -> str:
    text = _text(value, label, max_bytes=71)
    if len(text) != 71 or not text.startswith(_SHA256_PREFIX):
        raise ValueError(f"{label} must be a SHA-256 digest")
    try:
        bytes.fromhex(text.removeprefix(_SHA256_PREFIX))
    except ValueError as exc:
        raise ValueError(f"{label} must be hexadecimal") from exc
    return text


def _bounded_tuple(
    values: Iterable[Any],
    *,
    limit: int,
    label: str,
    over_limit_message: str | None = None,
) -> tuple[Any, ...]:
    """Materialize at most ``limit`` items without trusting a caller's length hint."""

    try:
        iterator = iter(values)
    except TypeError as exc:
        raise ValueError(f"{label} must be iterable") from exc
    result = tuple(islice(iterator, limit + 1))
    if len(result) > limit:
        raise ValueError(
            over_limit_message or f"{label} exceeds the {limit}-item input budget"
        )
    return result


def _bounded_cases(cases: Iterable[CalibrationCase]) -> tuple[CalibrationCase, ...]:
    values = _bounded_tuple(
        cases,
        limit=_MAX_CASES,
        label="calibration case set",
        over_limit_message=(
            "case set must contain between 1 and 400 cases; freeze-ready calibration "
            "requires between 200 and 400 cases"
        ),
    )
    if not all(type(case) is CalibrationCase for case in values):
        raise ValueError("case set contains an invalid record")
    return values


class BaselineKind(StrEnum):
    PROMPT_ONLY = "prompt_only"
    LOCATION = "location"
    LENGTH = "length"
    NUMERIC_SUMMARY = "numeric_summary"
    BAG_CHAR = "bag_char"


class DifficultyBand(StrEnum):
    TARGET = "target"
    BORDERLINE = "borderline"
    REJECTED = "rejected"


class FreezeState(StrEnum):
    DRAFT = "draft"
    SEED_COMMITTED = "seed_committed"
    DATA_BOUND = "data_bound"
    FROZEN = "frozen"


@dataclass(frozen=True, slots=True)
class CalibrationConfig:
    """Public thresholds.  Borderline difficulty is reported but cannot be frozen."""

    target_accuracy_min: float = 0.45
    target_accuracy_max: float = 0.75
    reject_accuracy_below: float = 0.30
    reject_accuracy_above: float = 0.85
    minimum_fault_position_entropy: float = 0.75
    maximum_fault_position_share: float = 0.25
    maximum_position_uniform_total_variation: float = 0.25
    minimum_cases_per_eligible_rank: float = 2.0
    maximum_baseline_excess_over_chance: float = 0.05
    maximum_worst_family_baseline_excess_over_chance: float = 0.10
    minimum_plausibility_rate: float = 0.70
    maximum_mechanism_excess_over_chance: float = 0.10
    minimum_family_count: int = 4
    maximum_family_count: int = 8
    minimum_template_count: int = 4
    maximum_template_count: int = 16
    minimum_generator_cell_count: int = 2
    maximum_generator_cell_count: int = 4
    maximum_domain_fault_cell_count: int = 8
    minimum_generator_cell_cases: int = 40
    minimum_generator_cell_families: int = 4
    minimum_generator_cell_templates: int = 4
    minimum_generator_cell_domains: int = 2
    minimum_generator_cell_fault_kinds: int = 2
    minimum_domain_fault_cell_cases: int = 40
    minimum_domain_fault_cell_families: int = 2
    minimum_domain_fault_cell_templates: int = 4
    minimum_family_cases: int = 40
    minimum_family_templates: int = 4
    minimum_family_domains: int = 2
    minimum_family_fault_kinds: int = 2
    minimum_family_domain_class_cases: int = 8
    minimum_family_fault_class_cases: int = 8
    minimum_family_template_cases: int = 12
    minimum_family_template_domains: int = 2
    minimum_family_template_fault_kinds: int = 2
    minimum_family_template_generator_cells: int = 2
    minimum_template_cases: int = 20
    minimum_template_families: int = 2
    minimum_template_domains: int = 2
    minimum_template_fault_kinds: int = 2

    def __post_init__(self) -> None:
        names = (
            "target_accuracy_min",
            "target_accuracy_max",
            "reject_accuracy_below",
            "reject_accuracy_above",
            "minimum_fault_position_entropy",
            "maximum_fault_position_share",
            "maximum_position_uniform_total_variation",
            "maximum_baseline_excess_over_chance",
            "maximum_worst_family_baseline_excess_over_chance",
            "minimum_plausibility_rate",
            "maximum_mechanism_excess_over_chance",
        )
        for name in names:
            _bounded_rate(getattr(self, name), name)
        if (
            type(self.minimum_cases_per_eligible_rank) not in (int, float)
            or not math.isfinite(self.minimum_cases_per_eligible_rank)
            or self.minimum_cases_per_eligible_rank < 1.0
        ):
            raise ValueError("minimum cases per eligible rank must be finite and at least one")
        if not (
            self.reject_accuracy_below
            < self.target_accuracy_min
            < self.target_accuracy_max
            < self.reject_accuracy_above
        ):
            raise ValueError("difficulty thresholds must be strictly ordered")
        if (
            self.maximum_baseline_excess_over_chance
            > self.maximum_worst_family_baseline_excess_over_chance
        ):
            raise ValueError("baseline pooled threshold cannot exceed its family-fold threshold")
        integer_names = (
            "minimum_family_count",
            "maximum_family_count",
            "minimum_template_count",
            "maximum_template_count",
            "minimum_generator_cell_count",
            "maximum_generator_cell_count",
            "maximum_domain_fault_cell_count",
            "minimum_generator_cell_cases",
            "minimum_generator_cell_families",
            "minimum_generator_cell_templates",
            "minimum_generator_cell_domains",
            "minimum_generator_cell_fault_kinds",
            "minimum_domain_fault_cell_cases",
            "minimum_domain_fault_cell_families",
            "minimum_domain_fault_cell_templates",
            "minimum_family_cases",
            "minimum_family_templates",
            "minimum_family_domains",
            "minimum_family_fault_kinds",
            "minimum_family_domain_class_cases",
            "minimum_family_fault_class_cases",
            "minimum_family_template_cases",
            "minimum_family_template_domains",
            "minimum_family_template_fault_kinds",
            "minimum_family_template_generator_cells",
            "minimum_template_cases",
            "minimum_template_families",
            "minimum_template_domains",
            "minimum_template_fault_kinds",
        )
        for name in integer_names:
            if _integer(getattr(self, name), name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.minimum_family_count > self.maximum_family_count:
            raise ValueError("family-count bounds are inverted")
        if self.minimum_template_count > self.maximum_template_count:
            raise ValueError("template-count bounds are inverted")
        if self.minimum_generator_cell_count > self.maximum_generator_cell_count:
            raise ValueError("generator-cell-count bounds are inverted")

    def to_dict(self) -> dict[str, Any]:
        return {
            "maximum_baseline_excess_over_chance": (
                self.maximum_baseline_excess_over_chance
            ),
            "maximum_worst_family_baseline_excess_over_chance": (
                self.maximum_worst_family_baseline_excess_over_chance
            ),
            "maximum_domain_fault_cell_count": self.maximum_domain_fault_cell_count,
            "maximum_family_count": self.maximum_family_count,
            "maximum_fault_position_share": self.maximum_fault_position_share,
            "maximum_position_uniform_total_variation": (
                self.maximum_position_uniform_total_variation
            ),
            "maximum_generator_cell_count": self.maximum_generator_cell_count,
            "maximum_mechanism_excess_over_chance": self.maximum_mechanism_excess_over_chance,
            "maximum_template_count": self.maximum_template_count,
            "minimum_domain_fault_cell_cases": self.minimum_domain_fault_cell_cases,
            "minimum_domain_fault_cell_families": self.minimum_domain_fault_cell_families,
            "minimum_domain_fault_cell_templates": self.minimum_domain_fault_cell_templates,
            "minimum_cases_per_eligible_rank": self.minimum_cases_per_eligible_rank,
            "minimum_family_cases": self.minimum_family_cases,
            "minimum_family_count": self.minimum_family_count,
            "minimum_family_domains": self.minimum_family_domains,
            "minimum_family_domain_class_cases": self.minimum_family_domain_class_cases,
            "minimum_family_fault_class_cases": self.minimum_family_fault_class_cases,
            "minimum_family_fault_kinds": self.minimum_family_fault_kinds,
            "minimum_family_template_cases": self.minimum_family_template_cases,
            "minimum_family_template_domains": self.minimum_family_template_domains,
            "minimum_family_template_fault_kinds": (
                self.minimum_family_template_fault_kinds
            ),
            "minimum_family_template_generator_cells": (
                self.minimum_family_template_generator_cells
            ),
            "minimum_family_templates": self.minimum_family_templates,
            "minimum_fault_position_entropy": self.minimum_fault_position_entropy,
            "minimum_generator_cell_cases": self.minimum_generator_cell_cases,
            "minimum_generator_cell_count": self.minimum_generator_cell_count,
            "minimum_generator_cell_domains": self.minimum_generator_cell_domains,
            "minimum_generator_cell_families": self.minimum_generator_cell_families,
            "minimum_generator_cell_fault_kinds": self.minimum_generator_cell_fault_kinds,
            "minimum_generator_cell_templates": self.minimum_generator_cell_templates,
            "minimum_plausibility_rate": self.minimum_plausibility_rate,
            "minimum_template_cases": self.minimum_template_cases,
            "minimum_template_count": self.minimum_template_count,
            "minimum_template_domains": self.minimum_template_domains,
            "minimum_template_families": self.minimum_template_families,
            "minimum_template_fault_kinds": self.minimum_template_fault_kinds,
            "reject_accuracy_above": self.reject_accuracy_above,
            "reject_accuracy_below": self.reject_accuracy_below,
            "target_accuracy_max": self.target_accuracy_max,
            "target_accuracy_min": self.target_accuracy_min,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> CalibrationConfig:
        value = _mapping(raw, "calibration config")
        expected = {
            "maximum_baseline_excess_over_chance",
            "maximum_worst_family_baseline_excess_over_chance",
            "maximum_domain_fault_cell_count",
            "maximum_family_count",
            "maximum_fault_position_share",
            "maximum_position_uniform_total_variation",
            "maximum_generator_cell_count",
            "maximum_mechanism_excess_over_chance",
            "maximum_template_count",
            "minimum_domain_fault_cell_cases",
            "minimum_domain_fault_cell_families",
            "minimum_domain_fault_cell_templates",
            "minimum_cases_per_eligible_rank",
            "minimum_family_cases",
            "minimum_family_count",
            "minimum_family_domains",
            "minimum_family_domain_class_cases",
            "minimum_family_fault_class_cases",
            "minimum_family_fault_kinds",
            "minimum_family_template_cases",
            "minimum_family_template_domains",
            "minimum_family_template_fault_kinds",
            "minimum_family_template_generator_cells",
            "minimum_family_templates",
            "minimum_fault_position_entropy",
            "minimum_generator_cell_cases",
            "minimum_generator_cell_count",
            "minimum_generator_cell_domains",
            "minimum_generator_cell_families",
            "minimum_generator_cell_fault_kinds",
            "minimum_generator_cell_templates",
            "minimum_plausibility_rate",
            "minimum_template_cases",
            "minimum_template_count",
            "minimum_template_domains",
            "minimum_template_families",
            "minimum_template_fault_kinds",
            "reject_accuracy_above",
            "reject_accuracy_below",
            "target_accuracy_max",
            "target_accuracy_min",
        }
        _strict_keys(value, expected, "calibration config")
        return cls(**{name: value[name] for name in expected})

    def digest(self) -> str:
        return _digest(self.to_dict())


DEFAULT_CONFIG = CalibrationConfig()


@dataclass(frozen=True, slots=True)
class OracleReceipt:
    """Digest-bound declared verifier result.

    Distinct verifier IDs are a structural integration gate, not cryptographic proof that two
    organizations or implementations are independent.  The caller must run the named verifiers.
    """

    case_binding_digest: str
    verifier_id: str
    clean_valid: bool
    faulty_valid: bool
    repaired_valid: bool
    first_invalid_line: int
    receipt_digest: str
    schema: str = ORACLE_SCHEMA
    schema_version: int = ORACLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema != ORACLE_SCHEMA or self.schema_version != ORACLE_SCHEMA_VERSION:
            raise ValueError("unsupported public oracle receipt schema")
        _sha256(self.case_binding_digest, "oracle case binding digest")
        _text(self.verifier_id, "oracle verifier id", max_bytes=256)
        _boolean(self.clean_valid, "oracle clean_valid")
        _boolean(self.faulty_valid, "oracle faulty_valid")
        _boolean(self.repaired_valid, "oracle repaired_valid")
        if _integer(self.first_invalid_line, "oracle first_invalid_line") <= 0:
            raise ValueError("oracle first_invalid_line must be positive")
        if not self.clean_valid or self.faulty_valid or not self.repaired_valid:
            raise ValueError(
                "poisoned oracle receipt: clean and repaired traces must pass and faulty must fail"
            )
        expected = _digest(self.content_payload())
        _sha256(self.receipt_digest, "oracle receipt digest")
        if not hmac.compare_digest(expected, self.receipt_digest):
            raise ValueError("oracle receipt digest does not match its content")

    def content_payload(self) -> dict[str, Any]:
        return {
            "case_binding_digest": self.case_binding_digest,
            "clean_valid": self.clean_valid,
            "faulty_valid": self.faulty_valid,
            "first_invalid_line": self.first_invalid_line,
            "repaired_valid": self.repaired_valid,
            "schema": self.schema,
            "schema_version": self.schema_version,
            "verifier_id": self.verifier_id,
        }

    def to_dict(self) -> dict[str, Any]:
        result = self.content_payload()
        result["receipt_digest"] = self.receipt_digest
        return result

    @classmethod
    def issue(
        cls,
        *,
        case_binding_digest: str,
        verifier_id: str,
        first_invalid_line: int,
        clean_valid: bool = True,
        faulty_valid: bool = False,
        repaired_valid: bool = True,
    ) -> OracleReceipt:
        payload = {
            "case_binding_digest": case_binding_digest,
            "clean_valid": clean_valid,
            "faulty_valid": faulty_valid,
            "first_invalid_line": first_invalid_line,
            "repaired_valid": repaired_valid,
            "schema": ORACLE_SCHEMA,
            "schema_version": ORACLE_SCHEMA_VERSION,
            "verifier_id": verifier_id,
        }
        return cls(receipt_digest=_digest(payload), **payload)

    @classmethod
    def from_dict(cls, raw: Any) -> OracleReceipt:
        value = _mapping(raw, "oracle receipt")
        expected = {
            "case_binding_digest",
            "clean_valid",
            "faulty_valid",
            "first_invalid_line",
            "receipt_digest",
            "repaired_valid",
            "schema",
            "schema_version",
            "verifier_id",
        }
        _strict_keys(value, expected, "oracle receipt")
        return cls(**{name: value[name] for name in expected})


def _case_binding_payload(
    *,
    case_id: str,
    family: str,
    template_id: str,
    generator_cell: str,
    domain: str,
    fault_kind: str,
    prompt: str,
    faulty_trace: Sequence[str],
    eligible_fault_lines: Sequence[int],
    fault_line: int,
    brain_off_correct: bool,
) -> dict[str, Any]:
    return {
        "brain_off_correct": brain_off_correct,
        "case_id": case_id,
        "domain": domain,
        "family": family,
        "fault_kind": fault_kind,
        "fault_line": fault_line,
        "faulty_trace": list(faulty_trace),
        "eligible_fault_lines": list(eligible_fault_lines),
        "prompt": prompt,
        "generator_cell": generator_cell,
        "template_id": template_id,
    }


@dataclass(frozen=True, slots=True)
class CalibrationCase:
    """One public faulty trace and one brain-off full-case outcome."""

    case_id: str
    family: str
    template_id: str
    generator_cell: str
    domain: str
    fault_kind: str
    prompt: str
    faulty_trace: tuple[str, ...]
    eligible_fault_lines: tuple[int, ...]
    fault_line: int
    brain_off_correct: bool
    oracles: tuple[OracleReceipt, ...]
    schema: str = CASE_SCHEMA
    schema_version: int = CASE_SCHEMA_VERSION
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema != CASE_SCHEMA or self.schema_version != CASE_SCHEMA_VERSION:
            raise ValueError("unsupported public calibration case schema")
        _text(self.case_id, "case id", max_bytes=256)
        _text(self.family, "case family", max_bytes=256)
        _text(self.template_id, "case template id", max_bytes=256)
        _text(self.generator_cell, "case generator cell", max_bytes=256)
        _text(self.domain, "case domain", max_bytes=256)
        _text(self.fault_kind, "case fault kind", max_bytes=256)
        _text(self.prompt, "case prompt", max_bytes=4096)
        if type(self.faulty_trace) is not tuple or not 6 <= len(self.faulty_trace) <= 12:
            raise ValueError("score-bearing faulty trace must contain between 6 and 12 lines")
        for index, line in enumerate(self.faulty_trace, 1):
            _text(line, f"faulty trace line {index}", max_bytes=2048)
        if (
            type(self.eligible_fault_lines) is not tuple
            or len(self.eligible_fault_lines) < 4
            or tuple(sorted(set(self.eligible_fault_lines)))
            != self.eligible_fault_lines
        ):
            raise ValueError("eligible fault lines must be a sorted set of at least four lines")
        if any(
            not 2 <= _integer(line, "eligible fault line") < len(self.faulty_trace)
            for line in self.eligible_fault_lines
        ):
            raise ValueError("eligible fault lines must be interior trace lines")
        if not 2 <= _integer(self.fault_line, "fault line") < len(self.faulty_trace):
            raise ValueError("score-bearing fault line must be interior")
        if self.fault_line not in self.eligible_fault_lines:
            raise ValueError("score-bearing fault line must be declared eligible")
        _boolean(self.brain_off_correct, "brain-off full-case outcome")
        if (
            type(self.oracles) is not tuple
            or not 2 <= len(self.oracles) <= 8
            or not all(type(receipt) is OracleReceipt for receipt in self.oracles)
        ):
            raise ValueError("case requires at least two validated oracle receipts")
        canonical_oracles = tuple(
            sorted(
                self.oracles,
                key=lambda receipt: (receipt.verifier_id, receipt.receipt_digest),
            )
        )
        object.__setattr__(self, "oracles", canonical_oracles)
        if len({receipt.verifier_id for receipt in self.oracles}) != len(self.oracles):
            raise ValueError("case oracle receipts must name distinct verifier implementations")
        if len({receipt.receipt_digest for receipt in self.oracles}) != len(self.oracles):
            raise ValueError("case oracle receipts must be independently digest-bound")
        binding = _digest(self.binding_payload())
        for receipt in self.oracles:
            if not hmac.compare_digest(binding, receipt.case_binding_digest):
                raise ValueError("oracle receipt is bound to different case content")
            if receipt.first_invalid_line != self.fault_line:
                raise ValueError("poisoned oracle receipt: certified fault line disagrees")
        object.__setattr__(self, "content_digest", _digest(self.content_payload()))

    def binding_payload(self) -> dict[str, Any]:
        return _case_binding_payload(
            case_id=self.case_id,
            family=self.family,
            template_id=self.template_id,
            generator_cell=self.generator_cell,
            domain=self.domain,
            fault_kind=self.fault_kind,
            prompt=self.prompt,
            faulty_trace=self.faulty_trace,
            eligible_fault_lines=self.eligible_fault_lines,
            fault_line=self.fault_line,
            brain_off_correct=self.brain_off_correct,
        )

    @property
    def receipts_attest_semantic_independence(self) -> bool:
        """Always false: two caller-created records cannot prove independent execution."""

        return False

    def content_payload(self) -> dict[str, Any]:
        result = self.binding_payload()
        result.update(
            {
                "oracles": [receipt.to_dict() for receipt in self.oracles],
                "schema": self.schema,
                "schema_version": self.schema_version,
            }
        )
        return result

    def to_dict(self) -> dict[str, Any]:
        result = self.content_payload()
        result["content_digest"] = self.content_digest
        return result

    @classmethod
    def binding_digest_for(
        cls,
        *,
        case_id: str,
        family: str,
        template_id: str,
        generator_cell: str,
        domain: str,
        fault_kind: str,
        prompt: str,
        faulty_trace: Sequence[str],
        eligible_fault_lines: Sequence[int],
        fault_line: int,
        brain_off_correct: bool,
    ) -> str:
        trace = _bounded_tuple(
            faulty_trace,
            limit=_MAX_TRACE_INPUT_LINES,
            label="faulty trace",
        )
        eligible = _bounded_tuple(
            eligible_fault_lines,
            limit=_MAX_ELIGIBLE_INPUT_LINES,
            label="eligible fault lines",
        )
        return _digest(
            _case_binding_payload(
                case_id=case_id,
                family=family,
                template_id=template_id,
                generator_cell=generator_cell,
                domain=domain,
                fault_kind=fault_kind,
                prompt=prompt,
                faulty_trace=trace,
                eligible_fault_lines=eligible,
                fault_line=fault_line,
                brain_off_correct=brain_off_correct,
            )
        )

    @classmethod
    def build(
        cls,
        *,
        case_id: str,
        family: str,
        template_id: str,
        generator_cell: str,
        domain: str,
        fault_kind: str,
        prompt: str,
        faulty_trace: Sequence[str],
        eligible_fault_lines: Sequence[int],
        fault_line: int,
        brain_off_correct: bool,
        oracles: Sequence[OracleReceipt],
    ) -> CalibrationCase:
        """Admit a case only from already-issued, distinct verifier receipts."""

        trace = _bounded_tuple(
            faulty_trace,
            limit=_MAX_TRACE_INPUT_LINES,
            label="faulty trace",
        )
        eligible = _bounded_tuple(
            eligible_fault_lines,
            limit=_MAX_ELIGIBLE_INPUT_LINES,
            label="eligible fault lines",
        )
        oracle_values = _bounded_tuple(
            oracles,
            limit=_MAX_ORACLE_INPUTS,
            label="oracle receipts",
        )
        return cls(
            case_id=case_id,
            family=family,
            template_id=template_id,
            generator_cell=generator_cell,
            domain=domain,
            fault_kind=fault_kind,
            prompt=prompt,
            faulty_trace=trace,
            eligible_fault_lines=eligible,
            fault_line=fault_line,
            brain_off_correct=brain_off_correct,
            oracles=oracle_values,
        )

    @classmethod
    def from_dict(cls, raw: Any) -> CalibrationCase:
        value = _mapping(raw, "calibration case")
        expected = {
            "brain_off_correct",
            "case_id",
            "content_digest",
            "domain",
            "eligible_fault_lines",
            "family",
            "fault_kind",
            "fault_line",
            "faulty_trace",
            "generator_cell",
            "oracles",
            "prompt",
            "schema",
            "schema_version",
            "template_id",
        }
        _strict_keys(value, expected, "calibration case")
        trace = value["faulty_trace"]
        if type(trace) is not list:
            raise ValueError("faulty trace must be an array")
        eligible = value["eligible_fault_lines"]
        if type(eligible) is not list:
            raise ValueError("eligible fault lines must be an array")
        raw_oracles = value["oracles"]
        if type(raw_oracles) is not list:
            raise ValueError("case oracle receipts must be an array")
        case = cls(
            case_id=value["case_id"],
            family=value["family"],
            template_id=value["template_id"],
            generator_cell=value["generator_cell"],
            domain=value["domain"],
            fault_kind=value["fault_kind"],
            prompt=value["prompt"],
            faulty_trace=tuple(trace),
            eligible_fault_lines=tuple(eligible),
            fault_line=value["fault_line"],
            brain_off_correct=value["brain_off_correct"],
            oracles=tuple(OracleReceipt.from_dict(item) for item in raw_oracles),
            schema=value["schema"],
            schema_version=value["schema_version"],
        )
        supplied = _sha256(value["content_digest"], "case content digest")
        if not hmac.compare_digest(case.content_digest, supplied):
            raise ValueError("case content digest does not match immutable content")
        return case


def case_set_digest(cases: Iterable[CalibrationCase]) -> str:
    values = _bounded_cases(cases)
    if not values or len(values) > _MAX_CASES:
        raise ValueError(f"case set must contain between 1 and {_MAX_CASES} cases")
    ordered = sorted((case.case_id, case.content_digest) for case in values)
    if len({case_id for case_id, _ in ordered}) != len(ordered):
        raise ValueError("case ids must be unique")
    return _digest({"case_digests": ordered, "schema": CASE_SCHEMA})


def seed_commitment(seed: str) -> str:
    seed_text = _text(seed, "calibration seed", max_bytes=4096)
    return _SHA256_PREFIX + hashlib.sha256(
        b"math-research-brain/calibration-v2-seed\x00" + seed_text.encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class CalibrationFreeze:
    """One-way seed/data/report binding.  Every transition returns a new immutable value."""

    state: FreezeState
    config_digest: str
    seed_digest: str | None = None
    bound_case_set_digest: str | None = None
    frozen_report_digest: str | None = None
    schema: str = FREEZE_SCHEMA
    schema_version: int = FREEZE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema != FREEZE_SCHEMA or self.schema_version != FREEZE_SCHEMA_VERSION:
            raise ValueError("unsupported calibration freeze schema")
        if type(self.state) is not FreezeState:
            raise ValueError("calibration freeze state is invalid")
        _sha256(self.config_digest, "freeze config digest")
        if not hmac.compare_digest(self.config_digest, DEFAULT_CONFIG.digest()):
            raise ValueError("freeze lifecycle requires the sealed calibration-v2 config")
        expected_presence = {
            FreezeState.DRAFT: (False, False, False),
            FreezeState.SEED_COMMITTED: (True, False, False),
            FreezeState.DATA_BOUND: (True, True, False),
            FreezeState.FROZEN: (True, True, True),
        }[self.state]
        values = (self.seed_digest, self.bound_case_set_digest, self.frozen_report_digest)
        if tuple(value is not None for value in values) != expected_presence:
            raise ValueError("freeze state fields do not match its one-way lifecycle")
        for value, label in zip(
            values,
            ("freeze seed digest", "freeze case-set digest", "freeze report digest"),
        ):
            if value is not None:
                _sha256(value, label)

    @classmethod
    def draft(cls, config: CalibrationConfig = DEFAULT_CONFIG) -> CalibrationFreeze:
        if type(config) is not CalibrationConfig:
            raise ValueError("freeze draft requires a validated calibration config")
        if not hmac.compare_digest(config.digest(), DEFAULT_CONFIG.digest()):
            raise ValueError("freeze draft cannot relax the sealed calibration-v2 config")
        return cls(state=FreezeState.DRAFT, config_digest=config.digest())

    def commit_seed(self, seed: str) -> CalibrationFreeze:
        if self.state is not FreezeState.DRAFT:
            raise ValueError("calibration seed can only be committed from draft")
        return CalibrationFreeze(
            state=FreezeState.SEED_COMMITTED,
            config_digest=self.config_digest,
            seed_digest=seed_commitment(seed),
        )

    def bind_cases(self, cases: Iterable[CalibrationCase]) -> CalibrationFreeze:
        if self.state is not FreezeState.SEED_COMMITTED:
            raise ValueError("public cases can only be bound after seed commitment")
        values = _bounded_cases(cases)
        _validate_design(values, DEFAULT_CONFIG)
        return CalibrationFreeze(
            state=FreezeState.DATA_BOUND,
            config_digest=self.config_digest,
            seed_digest=self.seed_digest,
            bound_case_set_digest=case_set_digest(values),
        )

    def verify_seed(self, seed: str) -> None:
        if self.seed_digest is None or not hmac.compare_digest(
            self.seed_digest, seed_commitment(seed)
        ):
            raise ValueError("calibration seed does not open the prior commitment")

    def freeze(
        self,
        report: CalibrationReport,
        *,
        cases: Iterable[CalibrationCase],
        seed: str,
        config: CalibrationConfig = DEFAULT_CONFIG,
    ) -> CalibrationFreeze:
        """Freeze only a report reproduced from the exact bound public inputs.

        A digest-bearing report is not an authority: callers can construct dataclass values.
        Recomputing here makes the state transition depend on the bound cases rather than on
        caller-supplied aggregate metrics or gate rows.
        """

        if self.state is not FreezeState.DATA_BOUND:
            raise ValueError("only a data-bound calibration can be frozen")
        if type(report) is not CalibrationReport:
            raise ValueError("freeze requires a validated calibration report")
        if (
            report.config_digest != self.config_digest
            or report.seed_commitment != self.seed_digest
            or report.case_set_digest != self.bound_case_set_digest
        ):
            raise ValueError("calibration report does not match the committed state")
        values = _bounded_cases(cases)
        canonical = generate_calibration_report(
            values,
            state=self,
            seed=seed,
            config=config,
        )
        if not hmac.compare_digest(report.content_digest, canonical.content_digest):
            raise ValueError(
                "calibration report does not match deterministic recomputation"
            )
        if not canonical.accepted:
            raise ValueError("failed calibration reports cannot be frozen")
        return CalibrationFreeze(
            state=FreezeState.FROZEN,
            config_digest=self.config_digest,
            seed_digest=self.seed_digest,
            bound_case_set_digest=self.bound_case_set_digest,
            frozen_report_digest=canonical.content_digest,
        )

    def unseal(self) -> CalibrationFreeze:
        raise ValueError("calibration freeze is one-way and cannot be unsealed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "bound_case_set_digest": self.bound_case_set_digest,
            "config_digest": self.config_digest,
            "frozen_report_digest": self.frozen_report_digest,
            "schema": self.schema,
            "schema_version": self.schema_version,
            "seed_digest": self.seed_digest,
            "state": self.state.value,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> CalibrationFreeze:
        value = _mapping(raw, "calibration freeze")
        expected = {
            "bound_case_set_digest",
            "config_digest",
            "frozen_report_digest",
            "schema",
            "schema_version",
            "seed_digest",
            "state",
        }
        _strict_keys(value, expected, "calibration freeze")
        try:
            state = FreezeState(value["state"])
        except (TypeError, ValueError) as exc:
            raise ValueError("unsupported calibration freeze state") from exc
        return cls(
            state=state,
            config_digest=value["config_digest"],
            seed_digest=value["seed_digest"],
            bound_case_set_digest=value["bound_case_set_digest"],
            frozen_report_digest=value["frozen_report_digest"],
            schema=value["schema"],
            schema_version=value["schema_version"],
        )

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())

@dataclass(frozen=True, slots=True)
class PlausibilityCheck:
    passed: bool
    reasons: tuple[str, ...]


def cheap_plausibility_check(case: CalibrationCase) -> PlausibilityCheck:
    """Reject glaring artifacts without attempting mathematical verification."""

    if type(case) is not CalibrationCase:
        raise ValueError("plausibility check requires a calibration case")
    reasons: list[str] = []
    trace = case.faulty_trace
    if len(trace) < 3:
        reasons.append("too_few_lines")
    if len(set(trace)) != len(trace):
        reasons.append("duplicate_lines")
    suspicious = tuple(line for line in trace if _SUSPICIOUS_RE.search(line))
    if suspicious:
        reasons.append("explicit_error_marker")
    if any(any(ord(character) < 32 and character not in "\t" for character in line) for line in trace):
        reasons.append("control_character")
    pairs = (("(", ")"), ("[", "]"), ("{", "}"))
    if any(line.count(left) != line.count(right) for line in trace for left, right in pairs):
        reasons.append("unbalanced_delimiter")
    lengths = [len(line.strip()) for line in trace]
    typical = max(1.0, float(median(lengths)))
    fault_length = lengths[case.fault_line - 1]
    if fault_length < 0.35 * typical or fault_length > 2.85 * typical:
        reasons.append("fault_line_length_outlier")
    if not _TOKEN_RE.findall(trace[case.fault_line - 1]):
        reasons.append("fault_line_has_no_tokens")
    return PlausibilityCheck(passed=not reasons, reasons=tuple(sorted(set(reasons))))


@dataclass(frozen=True, slots=True)
class DifficultyMetric:
    correct: int
    total: int
    accuracy: float
    band: DifficultyBand

    def __post_init__(self) -> None:
        if _integer(self.total, "difficulty total") <= 0:
            raise ValueError("difficulty total must be positive")
        if not 0 <= _integer(self.correct, "difficulty correct") <= self.total:
            raise ValueError("difficulty correct count is invalid")
        if _bounded_rate(self.accuracy, "difficulty accuracy") != _rate(
            self.correct, self.total
        ):
            raise ValueError("difficulty accuracy does not match its counts")
        if type(self.band) is not DifficultyBand:
            raise ValueError("difficulty band is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "accuracy": self.accuracy,
            "band": self.band.value,
            "correct": self.correct,
            "total": self.total,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> DifficultyMetric:
        value = _mapping(raw, "difficulty metric")
        _strict_keys(value, {"accuracy", "band", "correct", "total"}, "difficulty metric")
        try:
            band = DifficultyBand(value["band"])
        except (TypeError, ValueError) as exc:
            raise ValueError("unsupported difficulty band") from exc
        return cls(
            correct=value["correct"],
            total=value["total"],
            accuracy=value["accuracy"],
            band=band,
        )


@dataclass(frozen=True, slots=True)
class FaultPositionMetric:
    normalized_entropy: float
    maximum_position_share: float
    uniform_total_variation: float
    minimum_cases_per_eligible_rank: float
    eligible_arity_count: int
    minimum_distinct_ranks: int
    maximum_eligible_arity: int

    def __post_init__(self) -> None:
        _bounded_rate(self.normalized_entropy, "fault-position entropy")
        _bounded_rate(self.maximum_position_share, "maximum fault-position share")
        _bounded_rate(self.uniform_total_variation, "position uniform total variation")
        _nonnegative_number(
            self.minimum_cases_per_eligible_rank,
            "minimum cases per eligible rank",
        )
        for name in (
            "eligible_arity_count",
            "minimum_distinct_ranks",
            "maximum_eligible_arity",
        ):
            if _integer(getattr(self, name), name) <= 0:
                raise ValueError("fault-position support counts must be positive")
        if self.minimum_distinct_ranks > self.maximum_eligible_arity:
            raise ValueError("distinct ranks cannot exceed eligible arity")

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible_arity_count": self.eligible_arity_count,
            "maximum_position_share": self.maximum_position_share,
            "maximum_eligible_arity": self.maximum_eligible_arity,
            "minimum_cases_per_eligible_rank": self.minimum_cases_per_eligible_rank,
            "minimum_distinct_ranks": self.minimum_distinct_ranks,
            "normalized_entropy": self.normalized_entropy,
            "uniform_total_variation": self.uniform_total_variation,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> FaultPositionMetric:
        value = _mapping(raw, "fault-position metric")
        expected = {
            "eligible_arity_count",
            "maximum_position_share",
            "maximum_eligible_arity",
            "minimum_cases_per_eligible_rank",
            "minimum_distinct_ranks",
            "normalized_entropy",
            "uniform_total_variation",
        }
        _strict_keys(value, expected, "fault-position metric")
        return cls(**{name: value[name] for name in expected})


@dataclass(frozen=True, slots=True)
class EligibleAritySupport:
    eligible_arity: int
    cases: int

    def __post_init__(self) -> None:
        if not 4 <= _integer(self.eligible_arity, "eligible arity") <= 10:
            raise ValueError("eligible arity must lie between four and ten")
        if _integer(self.cases, "eligible-arity cases") <= 0:
            raise ValueError("eligible-arity support must be positive")

    def to_dict(self) -> dict[str, int]:
        return {"cases": self.cases, "eligible_arity": self.eligible_arity}

    @classmethod
    def from_dict(cls, raw: Any) -> EligibleAritySupport:
        value = _mapping(raw, "eligible-arity support")
        _strict_keys(value, {"cases", "eligible_arity"}, "eligible-arity support")
        return cls(cases=value["cases"], eligible_arity=value["eligible_arity"])


def _chance_from_arity_supports(
    supports: tuple[EligibleAritySupport, ...],
    total_cases: int,
    label: str,
) -> float:
    if type(supports) is not tuple or not supports:
        raise ValueError(f"{label} requires eligible-arity support")
    if not all(type(item) is EligibleAritySupport for item in supports):
        raise ValueError(f"{label} contains invalid eligible-arity support")
    arities = tuple(item.eligible_arity for item in supports)
    if arities != tuple(sorted(set(arities))):
        raise ValueError(f"{label} eligible arities must be canonical and unique")
    if sum(item.cases for item in supports) != total_cases:
        raise ValueError(f"{label} eligible-arity support does not match total cases")
    return sum(item.cases / item.eligible_arity for item in supports) / total_cases


@dataclass(frozen=True, slots=True)
class LocalizationFoldMetric:
    fold_index: int
    total_cases: int
    correct_cases: int
    eligible_arity_supports: tuple[EligibleAritySupport, ...]
    top1_accuracy: float
    chance_accuracy: float
    excess_over_chance: float

    def __post_init__(self) -> None:
        if _integer(self.fold_index, "localization fold index") < 0:
            raise ValueError("localization fold index must be non-negative")
        if _integer(self.total_cases, "localization fold total cases") <= 0:
            raise ValueError("localization fold total cases must be positive")
        if not 0 <= _integer(
            self.correct_cases, "localization fold correct cases"
        ) <= self.total_cases:
            raise ValueError("localization fold correct cases must lie within total cases")
        expected_accuracy = round(self.correct_cases / self.total_cases, 6)
        if _bounded_rate(self.top1_accuracy, "localization fold top-1 accuracy") != (
            expected_accuracy
        ):
            raise ValueError("localization fold top-1 accuracy does not match its counts")
        raw_chance = _chance_from_arity_supports(
            self.eligible_arity_supports,
            self.total_cases,
            "localization fold",
        )
        expected_chance = round(raw_chance, 6)
        if _bounded_rate(self.chance_accuracy, "localization fold chance accuracy") != (
            expected_chance
        ):
            raise ValueError("localization fold chance does not match its arity support")
        expected_excess = round(
            max(0.0, self.correct_cases / self.total_cases - raw_chance),
            6,
        )
        if _bounded_rate(
            self.excess_over_chance,
            "localization fold excess over chance",
        ) != expected_excess:
            raise ValueError("localization fold chance excess does not match its metrics")

    def to_dict(self) -> dict[str, Any]:
        return {
            "chance_accuracy": self.chance_accuracy,
            "correct_cases": self.correct_cases,
            "eligible_arity_supports": [
                item.to_dict() for item in self.eligible_arity_supports
            ],
            "excess_over_chance": self.excess_over_chance,
            "fold_index": self.fold_index,
            "top1_accuracy": self.top1_accuracy,
            "total_cases": self.total_cases,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> LocalizationFoldMetric:
        value = _mapping(raw, "localization fold metric")
        expected = {
            "chance_accuracy",
            "correct_cases",
            "eligible_arity_supports",
            "excess_over_chance",
            "fold_index",
            "top1_accuracy",
            "total_cases",
        }
        _strict_keys(value, expected, "localization fold metric")
        raw_supports = value["eligible_arity_supports"]
        if type(raw_supports) is not list:
            raise ValueError("localization fold arity support must be an array")
        return cls(
            chance_accuracy=value["chance_accuracy"],
            correct_cases=value["correct_cases"],
            eligible_arity_supports=tuple(
                EligibleAritySupport.from_dict(item) for item in raw_supports
            ),
            excess_over_chance=value["excess_over_chance"],
            fold_index=value["fold_index"],
            top1_accuracy=value["top1_accuracy"],
            total_cases=value["total_cases"],
        )


@dataclass(frozen=True, slots=True)
class BaselineMetric:
    name: str
    split: str
    total_cases: int
    correct_cases: int
    eligible_arity_supports: tuple[EligibleAritySupport, ...]
    top1_accuracy: float
    chance_accuracy: float
    excess_over_chance: float
    family_folds: tuple[LocalizationFoldMetric, ...]
    worst_family_excess_over_chance: float

    def __post_init__(self) -> None:
        _text(self.name, "baseline name", max_bytes=256)
        _text(self.split, "baseline split", max_bytes=256)
        if _integer(self.total_cases, "baseline total cases") <= 0:
            raise ValueError("baseline total cases must be positive")
        if not 0 <= _integer(self.correct_cases, "baseline correct cases") <= self.total_cases:
            raise ValueError("baseline correct cases must lie within total cases")
        expected_accuracy = round(self.correct_cases / self.total_cases, 6)
        if _bounded_rate(self.top1_accuracy, "baseline top-1 accuracy") != expected_accuracy:
            raise ValueError("baseline top-1 accuracy does not match its case counts")
        raw_chance = _chance_from_arity_supports(
            self.eligible_arity_supports,
            self.total_cases,
            "baseline",
        )
        expected_chance = round(raw_chance, 6)
        if _bounded_rate(self.chance_accuracy, "baseline chance accuracy") != expected_chance:
            raise ValueError("baseline chance does not match its arity support")
        expected_excess = round(
            max(0.0, self.correct_cases / self.total_cases - raw_chance),
            6,
        )
        if _bounded_rate(self.excess_over_chance, "baseline excess over chance") != (
            expected_excess
        ):
            raise ValueError("baseline chance excess does not match its metrics")
        if type(self.family_folds) is not tuple or len(self.family_folds) < 2:
            raise ValueError("baseline requires multiple family folds")
        if not all(type(item) is LocalizationFoldMetric for item in self.family_folds):
            raise ValueError("baseline contains an invalid family fold")
        if tuple(item.fold_index for item in self.family_folds) != tuple(
            range(len(self.family_folds))
        ):
            raise ValueError("baseline family-fold indices must be canonical")
        if sum(item.total_cases for item in self.family_folds) != self.total_cases or sum(
            item.correct_cases for item in self.family_folds
        ) != self.correct_cases:
            raise ValueError("baseline family folds do not aggregate to pooled counts")
        pooled_support = Counter(
            {
                item.eligible_arity: item.cases
                for item in self.eligible_arity_supports
            }
        )
        fold_support: Counter[int] = Counter()
        for fold in self.family_folds:
            fold_support.update(
                {
                    item.eligible_arity: item.cases
                    for item in fold.eligible_arity_supports
                }
            )
        if fold_support != pooled_support:
            raise ValueError("baseline family folds do not aggregate to pooled arity support")
        expected_worst = max(item.excess_over_chance for item in self.family_folds)
        if _bounded_rate(
            self.worst_family_excess_over_chance,
            "baseline worst-family excess over chance",
        ) != expected_worst:
            raise ValueError("baseline worst-family excess does not match its folds")

    def to_dict(self) -> dict[str, Any]:
        return {
            "chance_accuracy": self.chance_accuracy,
            "correct_cases": self.correct_cases,
            "eligible_arity_supports": [
                item.to_dict() for item in self.eligible_arity_supports
            ],
            "excess_over_chance": self.excess_over_chance,
            "family_folds": [item.to_dict() for item in self.family_folds],
            "name": self.name,
            "split": self.split,
            "top1_accuracy": self.top1_accuracy,
            "total_cases": self.total_cases,
            "worst_family_excess_over_chance": self.worst_family_excess_over_chance,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> BaselineMetric:
        value = _mapping(raw, "baseline metric")
        expected = {
            "chance_accuracy",
            "correct_cases",
            "eligible_arity_supports",
            "excess_over_chance",
            "family_folds",
            "name",
            "split",
            "top1_accuracy",
            "total_cases",
            "worst_family_excess_over_chance",
        }
        _strict_keys(value, expected, "baseline metric")
        raw_supports = value["eligible_arity_supports"]
        raw_folds = value["family_folds"]
        if type(raw_supports) is not list or type(raw_folds) is not list:
            raise ValueError("baseline arity supports and family folds must be arrays")
        return cls(
            chance_accuracy=value["chance_accuracy"],
            correct_cases=value["correct_cases"],
            eligible_arity_supports=tuple(
                EligibleAritySupport.from_dict(item) for item in raw_supports
            ),
            excess_over_chance=value["excess_over_chance"],
            family_folds=tuple(LocalizationFoldMetric.from_dict(item) for item in raw_folds),
            name=value["name"],
            split=value["split"],
            top1_accuracy=value["top1_accuracy"],
            total_cases=value["total_cases"],
            worst_family_excess_over_chance=value[
                "worst_family_excess_over_chance"
            ],
        )


@dataclass(frozen=True, slots=True)
class MechanismPredictionMetric:
    """Macro recall for predicting the fault mechanism from domain/topic surface only."""

    name: str
    split: str
    classes: int
    total: int
    class_supports: tuple[int, ...]
    class_correct: tuple[int, ...]
    balanced_accuracy: float
    chance_accuracy: float
    excess_over_chance: float
    family_folds: int
    worst_family_excess_over_chance: float

    def __post_init__(self) -> None:
        _text(self.name, "mechanism-prediction name", max_bytes=256)
        _text(self.split, "mechanism-prediction split", max_bytes=256)
        if _integer(self.classes, "mechanism classes") < 2:
            raise ValueError("mechanism prediction requires at least two classes")
        if _integer(self.total, "mechanism total") <= 0:
            raise ValueError("mechanism prediction total must be positive")
        if (
            type(self.class_supports) is not tuple
            or type(self.class_correct) is not tuple
            or len(self.class_supports) != self.classes
            or len(self.class_correct) != self.classes
        ):
            raise ValueError("mechanism class-count vectors are malformed")
        if any(_integer(value, "mechanism support") <= 0 for value in self.class_supports):
            raise ValueError("every mechanism class needs positive support")
        if any(
            _integer(correct, "mechanism correct") < 0 or correct > support
            for correct, support in zip(self.class_correct, self.class_supports)
        ):
            raise ValueError("mechanism correct counts exceed their supports")
        if sum(self.class_supports) != self.total:
            raise ValueError("mechanism supports do not sum to the total")
        expected = round(
            sum(
                correct / support
                for correct, support in zip(self.class_correct, self.class_supports)
            )
            / self.classes,
            6,
        )
        if _bounded_rate(self.balanced_accuracy, "mechanism balanced accuracy") != expected:
            raise ValueError("mechanism balanced accuracy does not match class recalls")
        expected_chance = round(1.0 / self.classes, 6)
        if _bounded_rate(self.chance_accuracy, "mechanism chance accuracy") != expected_chance:
            raise ValueError("mechanism chance accuracy does not match its class count")
        expected_excess = round(max(0.0, self.balanced_accuracy - expected_chance), 6)
        if _bounded_rate(self.excess_over_chance, "mechanism excess over chance") != expected_excess:
            raise ValueError("mechanism excess over chance is inconsistent")
        if _integer(self.family_folds, "mechanism family folds") < 2:
            raise ValueError("mechanism prediction requires multiple family folds")
        _bounded_rate(
            self.worst_family_excess_over_chance,
            "mechanism worst-family excess over chance",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "balanced_accuracy": self.balanced_accuracy,
            "chance_accuracy": self.chance_accuracy,
            "class_correct": list(self.class_correct),
            "class_supports": list(self.class_supports),
            "classes": self.classes,
            "excess_over_chance": self.excess_over_chance,
            "family_folds": self.family_folds,
            "name": self.name,
            "split": self.split,
            "total": self.total,
            "worst_family_excess_over_chance": self.worst_family_excess_over_chance,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> MechanismPredictionMetric:
        value = _mapping(raw, "mechanism-prediction metric")
        expected = {
            "balanced_accuracy",
            "chance_accuracy",
            "class_correct",
            "class_supports",
            "classes",
            "excess_over_chance",
            "family_folds",
            "name",
            "split",
            "total",
            "worst_family_excess_over_chance",
        }
        _strict_keys(value, expected, "mechanism-prediction metric")
        supports = value["class_supports"]
        correct = value["class_correct"]
        if type(supports) is not list or type(correct) is not list:
            raise ValueError("mechanism class-count vectors must be arrays")
        scalar_fields = expected - {"class_supports", "class_correct"}
        return cls(
            **{name: value[name] for name in scalar_fields},
            class_supports=tuple(supports),
            class_correct=tuple(correct),
        )


@dataclass(frozen=True, slots=True)
class StratumMetric:
    stratum_index: int
    stratum_kind: str
    cases: int
    families: int
    templates: int
    domains: int
    fault_kinds: int
    generator_cells: int
    brain_off_accuracy: float
    plausibility_rate: float
    fault_position_entropy: float
    maximum_fault_position_share: float
    position_uniform_total_variation: float
    minimum_cases_per_eligible_rank: float

    def __post_init__(self) -> None:
        if _integer(self.stratum_index, "stratum index") < 0:
            raise ValueError("stratum index must be non-negative")
        if self.stratum_kind not in {
            "domain_fault",
            "family",
            "family_template",
            "generator_cell",
            "template",
        }:
            raise ValueError("stratum kind is not registered")
        for name in (
            "cases",
            "families",
            "templates",
            "domains",
            "fault_kinds",
            "generator_cells",
        ):
            if _integer(getattr(self, name), f"stratum {name}") <= 0:
                raise ValueError("stratum counts must be positive")
        for name in (
            "brain_off_accuracy",
            "plausibility_rate",
            "fault_position_entropy",
            "maximum_fault_position_share",
            "position_uniform_total_variation",
        ):
            _bounded_rate(getattr(self, name), f"stratum {name}")
        _nonnegative_number(
            self.minimum_cases_per_eligible_rank,
            "stratum minimum cases per eligible rank",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "brain_off_accuracy": self.brain_off_accuracy,
            "cases": self.cases,
            "domains": self.domains,
            "families": self.families,
            "fault_kinds": self.fault_kinds,
            "fault_position_entropy": self.fault_position_entropy,
            "generator_cells": self.generator_cells,
            "maximum_fault_position_share": self.maximum_fault_position_share,
            "minimum_cases_per_eligible_rank": self.minimum_cases_per_eligible_rank,
            "plausibility_rate": self.plausibility_rate,
            "position_uniform_total_variation": self.position_uniform_total_variation,
            "stratum_index": self.stratum_index,
            "stratum_kind": self.stratum_kind,
            "templates": self.templates,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> StratumMetric:
        value = _mapping(raw, "stratum metric")
        expected = {
            "brain_off_accuracy",
            "cases",
            "domains",
            "families",
            "fault_kinds",
            "fault_position_entropy",
            "generator_cells",
            "maximum_fault_position_share",
            "minimum_cases_per_eligible_rank",
            "position_uniform_total_variation",
            "plausibility_rate",
            "stratum_index",
            "stratum_kind",
            "templates",
        }
        _strict_keys(value, expected, "stratum metric")
        return cls(**{name: value[name] for name in expected})


@dataclass(frozen=True, slots=True)
class GateResult:
    name: str
    passed: bool
    observed: float
    relation: str
    threshold: float

    def __post_init__(self) -> None:
        _text(self.name, "gate name", max_bytes=256)
        _boolean(self.passed, "gate result")
        observed = _nonnegative_number(self.observed, "gate observed value")
        threshold = _nonnegative_number(self.threshold, "gate threshold")
        if self.relation not in {"<=", ">="}:
            raise ValueError("gate relation must be <= or >=")
        expected = observed <= threshold if self.relation == "<=" else observed >= threshold
        if self.passed != expected:
            raise ValueError("gate result does not match its observation and threshold")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "observed": self.observed,
            "passed": self.passed,
            "relation": self.relation,
            "threshold": self.threshold,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> GateResult:
        value = _mapping(raw, "gate result")
        expected = {"name", "observed", "passed", "relation", "threshold"}
        _strict_keys(value, expected, "gate result")
        return cls(**{name: value[name] for name in expected})


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    config: CalibrationConfig
    config_digest: str
    seed_commitment: str
    case_set_digest: str
    case_count: int
    family_count: int
    template_count: int
    domain_count: int
    fault_kind_count: int
    difficulty: DifficultyMetric
    fault_positions: FaultPositionMetric
    plausibility_rate: float
    baselines: tuple[BaselineMetric, ...]
    domain_fault_cross_prediction: MechanismPredictionMetric
    strata: tuple[StratumMetric, ...]
    gates: tuple[GateResult, ...]
    schema: str = CALIBRATION_SCHEMA
    schema_version: int = CALIBRATION_SCHEMA_VERSION
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema != CALIBRATION_SCHEMA or self.schema_version != CALIBRATION_SCHEMA_VERSION:
            raise ValueError("unsupported public calibration report schema")
        if type(self.config) is not CalibrationConfig:
            raise ValueError("report config is invalid")
        if not hmac.compare_digest(self.config.digest(), DEFAULT_CONFIG.digest()):
            raise ValueError("report must use the sealed calibration-v2 config")
        _sha256(self.config_digest, "report config digest")
        if not hmac.compare_digest(self.config_digest, self.config.digest()):
            raise ValueError("report config digest does not match its thresholds")
        _sha256(self.seed_commitment, "report seed commitment")
        _sha256(self.case_set_digest, "report case-set digest")
        for name in (
            "case_count",
            "family_count",
            "template_count",
            "domain_count",
            "fault_kind_count",
        ):
            if _integer(getattr(self, name), name) <= 0:
                raise ValueError(f"{name} must be positive")
        _bounded_rate(self.plausibility_rate, "report plausibility rate")
        if not self.baselines or not self.strata or not self.gates:
            raise ValueError("aggregate report sections must not be empty")
        if type(self.difficulty) is not DifficultyMetric:
            raise ValueError("report difficulty metric is invalid")
        if type(self.fault_positions) is not FaultPositionMetric:
            raise ValueError("report fault-position metric is invalid")
        if self.difficulty.total != self.case_count:
            raise ValueError("report difficulty count does not match case count")
        if type(self.baselines) is not tuple or not all(
            type(metric) is BaselineMetric for metric in self.baselines
        ):
            raise ValueError("report contains an invalid baseline metric")
        expected_baselines = {
            f"{kind.value}__{suffix}"
            for kind in BaselineKind
            for suffix in ("lofo", "within_family")
        }
        if {metric.name for metric in self.baselines} != expected_baselines:
            raise ValueError("report must contain each registered shortcut baseline exactly once")
        if len({metric.name for metric in self.baselines}) != len(self.baselines):
            raise ValueError("report baseline names must be unique")
        if any(metric.total_cases != self.case_count for metric in self.baselines):
            raise ValueError("report baselines must use the report case population")
        if any(len(metric.family_folds) != self.family_count for metric in self.baselines):
            raise ValueError("report baselines must use every report family fold")
        structural_signatures = {
            (
                metric.total_cases,
                metric.eligible_arity_supports,
                tuple(
                    (
                        fold.total_cases,
                        fold.eligible_arity_supports,
                    )
                    for fold in metric.family_folds
                ),
            )
            for metric in self.baselines
        }
        if len(structural_signatures) != 1:
            raise ValueError("report baselines must share one structural case population")
        if type(self.domain_fault_cross_prediction) is not MechanismPredictionMetric:
            raise ValueError("report cross-prediction metric is invalid")
        if type(self.strata) is not tuple or not all(
            type(stratum) is StratumMetric for stratum in self.strata
        ):
            raise ValueError("report contains an invalid stratum metric")
        expected_kinds = {
            "domain_fault",
            "family",
            "family_template",
            "generator_cell",
            "template",
        }
        if {stratum.stratum_kind for stratum in self.strata} != expected_kinds:
            raise ValueError("report must contain every registered stratum kind")
        for kind in expected_kinds:
            if sum(
                stratum.cases
                for stratum in self.strata
                if stratum.stratum_kind == kind
            ) != self.case_count:
                raise ValueError(f"{kind} strata do not partition the case set")
        family_stratum_cases = tuple(
            stratum.cases
            for stratum in self.strata
            if stratum.stratum_kind == "family"
        )
        if any(
            tuple(fold.total_cases for fold in metric.family_folds)
            != family_stratum_cases
            for metric in self.baselines
        ):
            raise ValueError("baseline family folds do not match report family strata")
        if tuple(stratum.stratum_index for stratum in self.strata) != tuple(
            range(len(self.strata))
        ):
            raise ValueError("stratum indices must be canonical and contiguous")
        if type(self.gates) is not tuple or not all(
            type(gate) is GateResult for gate in self.gates
        ):
            raise ValueError("report contains an invalid gate result")
        if len({gate.name for gate in self.gates}) != len(self.gates):
            raise ValueError("report gate names must be unique")
        expected_gates = _report_gates(
            difficulty=self.difficulty,
            positions=self.fault_positions,
            plausibility_rate=self.plausibility_rate,
            baselines=self.baselines,
            cross_prediction=self.domain_fault_cross_prediction,
            strata=self.strata,
            config=self.config,
        )
        if self.gates != expected_gates:
            raise ValueError("report gates do not match the canonical metric-derived gate set")
        payload = self.content_payload()
        _assert_answer_free_aggregate(payload)
        object.__setattr__(self, "content_digest", _digest(payload))

    @property
    def accepted(self) -> bool:
        return all(gate.passed for gate in self.gates)

    def content_payload(self) -> dict[str, Any]:
        return {
            "baselines": [metric.to_dict() for metric in self.baselines],
            "case_count": self.case_count,
            "case_set_digest": self.case_set_digest,
            "config": self.config.to_dict(),
            "config_digest": self.config_digest,
            "difficulty": self.difficulty.to_dict(),
            "domain_count": self.domain_count,
            "domain_fault_cross_prediction": self.domain_fault_cross_prediction.to_dict(),
            "family_count": self.family_count,
            "fault_kind_count": self.fault_kind_count,
            "fault_positions": self.fault_positions.to_dict(),
            "gates": [gate.to_dict() for gate in self.gates],
            "plausibility_rate": self.plausibility_rate,
            "schema": self.schema,
            "schema_version": self.schema_version,
            "seed_commitment": self.seed_commitment,
            "strata": [stratum.to_dict() for stratum in self.strata],
            "template_count": self.template_count,
        }

    def to_dict(self) -> dict[str, Any]:
        result = self.content_payload()
        result["accepted"] = self.accepted
        result["content_digest"] = self.content_digest
        return result

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Any) -> CalibrationReport:
        value = _mapping(raw, "calibration report")
        expected = {
            "accepted",
            "baselines",
            "case_count",
            "case_set_digest",
            "config",
            "config_digest",
            "content_digest",
            "difficulty",
            "domain_count",
            "domain_fault_cross_prediction",
            "family_count",
            "fault_kind_count",
            "fault_positions",
            "gates",
            "plausibility_rate",
            "schema",
            "schema_version",
            "seed_commitment",
            "strata",
            "template_count",
        }
        _strict_keys(value, expected, "calibration report")
        for name in ("baselines", "gates", "strata"):
            if type(value[name]) is not list:
                raise ValueError(f"report {name} must be an array")
        report = cls(
            config=CalibrationConfig.from_dict(value["config"]),
            config_digest=value["config_digest"],
            seed_commitment=value["seed_commitment"],
            case_set_digest=value["case_set_digest"],
            case_count=value["case_count"],
            family_count=value["family_count"],
            template_count=value["template_count"],
            domain_count=value["domain_count"],
            fault_kind_count=value["fault_kind_count"],
            difficulty=DifficultyMetric.from_dict(value["difficulty"]),
            fault_positions=FaultPositionMetric.from_dict(value["fault_positions"]),
            plausibility_rate=value["plausibility_rate"],
            baselines=tuple(
                BaselineMetric.from_dict(item) for item in value["baselines"]
            ),
            domain_fault_cross_prediction=MechanismPredictionMetric.from_dict(
                value["domain_fault_cross_prediction"]
            ),
            strata=tuple(StratumMetric.from_dict(item) for item in value["strata"]),
            gates=tuple(GateResult.from_dict(item) for item in value["gates"]),
            schema=value["schema"],
            schema_version=value["schema_version"],
        )
        if _boolean(value["accepted"], "report accepted") != report.accepted:
            raise ValueError("report accepted flag does not match canonical gates")
        supplied = _sha256(value["content_digest"], "report content digest")
        if not hmac.compare_digest(supplied, report.content_digest):
            raise ValueError("report content digest does not match canonical content")
        return report


@dataclass(frozen=True, slots=True)
class CalibrationBundle:
    """One self-contained final artifact; never publish report and freeze separately."""

    report: CalibrationReport
    frozen_state: CalibrationFreeze | None
    schema: str = CALIBRATION_BUNDLE_SCHEMA
    schema_version: int = CALIBRATION_BUNDLE_SCHEMA_VERSION
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            self.schema != CALIBRATION_BUNDLE_SCHEMA
            or self.schema_version != CALIBRATION_BUNDLE_SCHEMA_VERSION
        ):
            raise ValueError("unsupported calibration bundle schema")
        if type(self.report) is not CalibrationReport:
            raise ValueError("calibration bundle report is invalid")
        if self.report.accepted:
            if type(self.frozen_state) is not CalibrationFreeze:
                raise ValueError("accepted calibration bundle requires a frozen state")
            frozen = self.frozen_state
            if (
                frozen.state is not FreezeState.FROZEN
                or frozen.config_digest != self.report.config_digest
                or frozen.seed_digest != self.report.seed_commitment
                or frozen.bound_case_set_digest != self.report.case_set_digest
                or frozen.frozen_report_digest != self.report.content_digest
            ):
                raise ValueError("bundle frozen state does not bind the bundled report")
        elif self.frozen_state is not None:
            raise ValueError("failed calibration bundle cannot contain a frozen state")
        object.__setattr__(self, "content_digest", _digest(self.content_payload()))

    def content_payload(self) -> dict[str, Any]:
        return {
            "frozen_state": (
                None if self.frozen_state is None else self.frozen_state.to_dict()
            ),
            "report": self.report.to_dict(),
            "schema": self.schema,
            "schema_version": self.schema_version,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.content_payload(), "content_digest": self.content_digest}

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Any) -> CalibrationBundle:
        value = _mapping(raw, "calibration bundle")
        expected = {
            "content_digest",
            "frozen_state",
            "report",
            "schema",
            "schema_version",
        }
        _strict_keys(value, expected, "calibration bundle")
        raw_frozen = value["frozen_state"]
        if raw_frozen is not None and type(raw_frozen) is not dict:
            raise ValueError("bundle frozen state must be an object or null")
        bundle = cls(
            report=CalibrationReport.from_dict(value["report"]),
            frozen_state=(
                None
                if raw_frozen is None
                else CalibrationFreeze.from_dict(raw_frozen)
            ),
            schema=value["schema"],
            schema_version=value["schema_version"],
        )
        supplied = _sha256(value["content_digest"], "calibration bundle digest")
        if not hmac.compare_digest(supplied, bundle.content_digest):
            raise ValueError("calibration bundle digest does not match canonical content")
        return bundle


def _assert_answer_free_aggregate(value: Any) -> None:
    forbidden = {
        "answer",
        "answers",
        "case_id",
        "fault_line",
        "faulty_trace",
        "oracle",
        "oracles",
        "prompt",
        "repair",
        "solution",
        "trace",
    }
    stack = [value]
    while stack:
        current = stack.pop()
        if type(current) is dict:
            overlap = forbidden.intersection(current)
            if overlap:
                raise ValueError(f"aggregate report contains forbidden answer field: {sorted(overlap)}")
            stack.extend(current.values())
        elif type(current) in (list, tuple):
            stack.extend(current)


def _template_surface(case: CalibrationCase) -> str:
    fields = (case.prompt, *case.faulty_trace)
    normalized = [
        _SPACE_RE.sub(" ", _NUMBER_RE.sub("<n>", field.lower())).strip() for field in fields
    ]
    return _digest({"surface_template": normalized})


def _validate_design(
    cases: Sequence[CalibrationCase],
    config: CalibrationConfig = DEFAULT_CONFIG,
) -> None:
    if type(cases) is not tuple or not all(
        type(case) is CalibrationCase for case in cases
    ):
        raise ValueError("design validation requires exact calibration case records")
    if type(config) is not CalibrationConfig:
        raise ValueError("design validation requires a calibrated config")
    if not MIN_CALIBRATION_CASES_V2 <= len(cases) <= MAX_CALIBRATION_CASES_V2:
        raise ValueError(
            "freeze-ready calibration requires between "
            f"{MIN_CALIBRATION_CASES_V2} and {MAX_CALIBRATION_CASES_V2} cases"
        )
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("case ids must be unique")
    exact_surfaces: dict[str, str] = {}
    surface_templates: dict[str, str] = {}
    for case in cases:
        exact = _digest({"prompt": case.prompt, "trace": case.faulty_trace})
        previous = exact_surfaces.setdefault(exact, case.case_id)
        if previous != case.case_id:
            raise ValueError("duplicate case surface detected")
        signature = _template_surface(case)
        prior_template = surface_templates.setdefault(signature, case.template_id)
        if prior_template != case.template_id:
            raise ValueError("duplicate normalized template is declared under different ids")
    family_count = len({case.family for case in cases})
    template_count = len({case.template_id for case in cases})
    generator_cell_count = len({case.generator_cell for case in cases})
    domains = {case.domain for case in cases}
    fault_kinds = {case.fault_kind for case in cases}
    observed_domain_fault = {(case.domain, case.fault_kind) for case in cases}
    domain_fault_count = len(observed_domain_fault)
    if not config.minimum_family_count <= family_count <= config.maximum_family_count:
        raise ValueError("family count lies outside the sealed calibration bounds")
    if not config.minimum_template_count <= template_count <= config.maximum_template_count:
        raise ValueError("template count lies outside the sealed calibration bounds")
    if not (
        config.minimum_generator_cell_count
        <= generator_cell_count
        <= config.maximum_generator_cell_count
    ):
        raise ValueError("generator-cell count lies outside the sealed calibration bounds")
    if domain_fault_count > config.maximum_domain_fault_cell_count:
        raise ValueError("domain-by-fault cell count exceeds the sealed calibration bound")
    if len(domains) < 2 or len(fault_kinds) < 2:
        raise ValueError("domain-by-fault cross-prediction requires multiple domains and faults")
    expected_domain_fault = {
        (domain, fault_kind) for domain in domains for fault_kind in fault_kinds
    }
    if observed_domain_fault != expected_domain_fault:
        raise ValueError("domain-by-fault design must contain the complete Cartesian product")
    by_family: dict[str, list[CalibrationCase]] = defaultdict(list)
    for case in cases:
        by_family[case.family].append(case)
    for family_cases in by_family.values():
        domain_support = Counter(case.domain for case in family_cases)
        fault_support = Counter(case.fault_kind for case in family_cases)
        if set(domain_support) != domains or min(domain_support.values()) < (
            config.minimum_family_domain_class_cases
        ):
            raise ValueError("every family must support every domain class at the sealed floor")
        if set(fault_support) != fault_kinds or min(fault_support.values()) < (
            config.minimum_family_fault_class_cases
        ):
            raise ValueError("every family must support every fault class at the sealed floor")


def _position_metric(cases: Sequence[CalibrationCase]) -> FaultPositionMetric:
    total = len(cases)
    grouped: dict[int, Counter[int]] = defaultdict(Counter)
    for case in cases:
        eligible_arity = len(case.eligible_fault_lines)
        rank = case.eligible_fault_lines.index(case.fault_line)
        grouped[eligible_arity][rank] += 1
    weighted_entropy = 0.0
    weighted_tv = 0.0
    maximum_share = 0.0
    minimum_distinct = min(len(counts) for counts in grouped.values())
    minimum_cases_per_rank = min(
        counts.get(rank, 0)
        for arity, counts in grouped.items()
        for rank in range(arity)
    )
    for arity, counts in grouped.items():
        support = sum(counts.values())
        probabilities = tuple(counts.get(rank, 0) / support for rank in range(arity))
        entropy = -sum(
            probability * math.log(probability)
            for probability in probabilities
            if probability > 0.0
        )
        normalized = 1.0 if arity == 1 else entropy / math.log(arity)
        uniform = 1.0 / arity
        variation = 0.5 * sum(abs(probability - uniform) for probability in probabilities)
        weight = support / total
        weighted_entropy += weight * normalized
        weighted_tv += weight * variation
        maximum_share = max(maximum_share, max(probabilities))
    return FaultPositionMetric(
        normalized_entropy=round(weighted_entropy, 6),
        maximum_position_share=round(maximum_share, 6),
        uniform_total_variation=round(weighted_tv, 6),
        minimum_cases_per_eligible_rank=round(minimum_cases_per_rank, 6),
        eligible_arity_count=len(grouped),
        minimum_distinct_ranks=minimum_distinct,
        maximum_eligible_arity=max(grouped),
    )


def _difficulty(cases: Sequence[CalibrationCase], config: CalibrationConfig) -> DifficultyMetric:
    correct = sum(case.brain_off_correct for case in cases)
    accuracy = _rate(correct, len(cases))
    if accuracy < config.reject_accuracy_below or accuracy > config.reject_accuracy_above:
        band = DifficultyBand.REJECTED
    elif config.target_accuracy_min <= accuracy <= config.target_accuracy_max:
        band = DifficultyBand.TARGET
    else:
        band = DifficultyBand.BORDERLINE
    return DifficultyMetric(correct=correct, total=len(cases), accuracy=accuracy, band=band)


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(token.lower() for token in _TOKEN_RE.findall(text))


def _hashed_feature_buckets(
    prefix: str,
    values: Iterable[str],
    *,
    buckets: int = 256,
) -> set[str]:
    if buckets <= 0:
        raise ValueError("feature bucket count must be positive")
    result: set[str] = set()
    for value in values:
        digest = hashlib.blake2s(value.encode("utf-8"), digest_size=4).digest()
        bucket = int.from_bytes(digest, "big") % buckets
        result.add(f"{prefix}:{bucket:03d}")
    return result


def _feature_set(case: CalibrationCase, line_index: int, kind: BaselineKind) -> frozenset[str]:
    line = case.faulty_trace[line_index]
    if kind is BaselineKind.PROMPT_ONLY:
        tokens = _tokens(case.prompt)
        position = line_index + 1
        # Prompt features alone are constant across candidate lines and therefore cannot
        # localize a prompt-encoded answer.  Cross each cheap prompt feature with the
        # candidate position so leaks such as "inspect slot three" are actually tested.
        return frozenset(
            {f"prompt-token-at:{position}:{token}" for token in tokens}
            | {
                f"prompt-length-at:{position}:{min(20, len(case.prompt) // 24)}",
                f"candidate-position:{position}",
            }
        )
    if kind is BaselineKind.LOCATION:
        line_count = len(case.faulty_trace)
        position = line_index + 1
        decile = min(9, (10 * line_index) // max(1, line_count))
        return frozenset(
            {
                f"absolute-position:{position}",
                f"relative-decile:{decile}",
                f"trace-lines:{line_count}",
                f"is-first:{position == 1}",
                f"is-last:{position == line_count}",
            }
        )
    if kind is BaselineKind.LENGTH:
        lengths = [len(value) for value in case.faulty_trace]
        typical = max(1.0, float(median(lengths)))
        token_count = len(_tokens(line))
        relative_bucket = min(20, int(10 * len(line) / typical))
        return frozenset(
            {
                f"char-bucket:{min(40, len(line) // 8)}",
                f"token-bucket:{min(30, token_count // 2)}",
                f"relative-length:{relative_bucket}",
            }
        )
    if kind is BaselineKind.NUMERIC_SUMMARY:
        numbers = re.findall(r"-?\d+(?:\.\d+)?", line)
        signs = Counter(character for character in line if character in "+-*/^=")
        magnitudes = [len(value.lstrip("-").split(".")[0]) for value in numbers]
        return frozenset(
            {
                f"number-count:{min(12, len(numbers))}",
                f"largest-digits:{min(12, max(magnitudes, default=0))}",
                f"minus-count:{min(8, signs['-'])}",
                f"plus-count:{min(8, signs['+'])}",
                f"mul-count:{min(8, signs['*'])}",
                f"div-count:{min(8, signs['/'])}",
                f"equals-count:{min(8, signs['='])}",
                f"paren-depth-proxy:{min(12, line.count('('))}",
            }
        )
    if kind is BaselineKind.BAG_CHAR:
        lowered = _NUMBER_RE.sub("#", line.lower())
        compact = _SPACE_RE.sub(" ", lowered).strip()
        token_features = _hashed_feature_buckets("line-token", _tokens(lowered))
        char_features = _hashed_feature_buckets(
            "char3",
            (
                compact[index:index + 3]
                for index in range(max(0, len(compact) - 2))
            ),
        )
        return frozenset(token_features | char_features)
    raise AssertionError("unreachable baseline kind")


@dataclass(frozen=True, slots=True)
class _BernoulliModel:
    base_score: float
    weights: Mapping[str, float]

    def score(self, features: frozenset[str]) -> float:
        return self.base_score + sum(
            self.weights.get(feature, 0.0) for feature in features
        )

    def predict(self, features: frozenset[str]) -> bool:
        return self.score(features) >= 0.0


def _fit_model(
    cases: Sequence[CalibrationCase],
    kind: BaselineKind,
    feature_cache: Mapping[tuple[str, int], frozenset[str]],
) -> _BernoulliModel:
    positive: list[frozenset[str]] = []
    negative: list[frozenset[str]] = []
    for case in cases:
        # Endpoints are structurally ineligible by the score-bearing case contract, so a
        # shortcut model receives no credit merely for ruling them out.
        for index in (line - 1 for line in case.eligible_fault_lines):
            target = positive if index + 1 == case.fault_line else negative
            target.append(feature_cache[(case.content_digest, index)])
    if not positive or not negative:
        raise ValueError("shortcut baseline requires both faulty and non-faulty lines")
    return _fit_binary_presence_model(positive, negative)


def _fit_binary_presence_model(
    positive: Sequence[frozenset[str]],
    negative: Sequence[frozenset[str]],
) -> _BernoulliModel:
    if not positive or not negative:
        raise ValueError("Bernoulli baseline requires both classes")
    positive_counts = Counter(feature for row in positive for feature in row)
    negative_counts = Counter(feature for row in negative for feature in row)
    vocabulary = sorted(set(positive_counts) | set(negative_counts))
    base = 0.0
    weights: dict[str, float] = {}
    for feature in vocabulary:
        p_positive = (positive_counts[feature] + 1.0) / (len(positive) + 2.0)
        p_negative = (negative_counts[feature] + 1.0) / (len(negative) + 2.0)
        absent_log_odds = math.log1p(-p_positive) - math.log1p(-p_negative)
        base += absent_log_odds
        weights[feature] = (
            math.log(p_positive)
            - math.log(p_negative)
            - absent_log_odds
        )
    return _BernoulliModel(base_score=base, weights=weights)


def _baseline_metric(
    name: str,
    split: str,
    results_by_family: Mapping[str, Sequence[tuple[bool, int]]],
) -> BaselineMetric:
    if len(results_by_family) < 2 or any(not values for values in results_by_family.values()):
        raise ValueError("shortcut baseline requires multiple nonempty family folds")
    flattened = tuple(
        result
        for family in sorted(results_by_family)
        for result in results_by_family[family]
    )
    total = len(flattened)
    correct = sum(is_correct for is_correct, _ in flattened)
    arity_counts = Counter(arity for _, arity in flattened)
    arity_supports = tuple(
        EligibleAritySupport(eligible_arity=arity, cases=arity_counts[arity])
        for arity in sorted(arity_counts)
    )
    raw_chance = _chance_from_arity_supports(arity_supports, total, "baseline")
    folds: list[LocalizationFoldMetric] = []
    for fold_index, family in enumerate(sorted(results_by_family)):
        values = results_by_family[family]
        fold_total = len(values)
        fold_correct = sum(is_correct for is_correct, _ in values)
        fold_counts = Counter(arity for _, arity in values)
        fold_supports = tuple(
            EligibleAritySupport(eligible_arity=arity, cases=fold_counts[arity])
            for arity in sorted(fold_counts)
        )
        fold_chance = _chance_from_arity_supports(
            fold_supports,
            fold_total,
            "localization fold",
        )
        folds.append(
            LocalizationFoldMetric(
                fold_index=fold_index,
                total_cases=fold_total,
                correct_cases=fold_correct,
                eligible_arity_supports=fold_supports,
                top1_accuracy=round(fold_correct / fold_total, 6),
                chance_accuracy=round(fold_chance, 6),
                excess_over_chance=round(
                    max(0.0, fold_correct / fold_total - fold_chance),
                    6,
                ),
            )
        )
    return BaselineMetric(
        name=name,
        split=split,
        total_cases=total,
        correct_cases=correct,
        eligible_arity_supports=arity_supports,
        top1_accuracy=round(correct / total, 6),
        chance_accuracy=round(raw_chance, 6),
        excess_over_chance=round(max(0.0, correct / total - raw_chance), 6),
        family_folds=tuple(folds),
        worst_family_excess_over_chance=max(
            fold.excess_over_chance for fold in folds
        ),
    )


def _evaluate_disjoint_baseline(
    cases: Sequence[CalibrationCase],
    kind: BaselineKind,
    *,
    within_family: bool,
) -> BaselineMetric:
    feature_cache = {
        (case.content_digest, index): _feature_set(case, index, kind)
        for case in cases
        for index in (line - 1 for line in case.eligible_fault_lines)
    }
    models: dict[tuple[str, str], _BernoulliModel] = {}
    results: dict[str, list[tuple[bool, int]]] = defaultdict(list)
    for case in cases:
        group = (case.family, case.template_id)
        model = models.get(group)
        if model is None:
            if within_family:
                training = tuple(
                    other
                    for other in cases
                    if other.family == case.family
                    and other.template_id != case.template_id
                )
            else:
                training = tuple(
                    other
                    for other in cases
                    if other.family != case.family
                )
            if not training:
                raise ValueError("no split-disjoint training cases are available")
            model = _fit_model(training, kind, feature_cache)
            models[group] = model
        eligible = tuple(line - 1 for line in case.eligible_fault_lines)
        predicted_index = max(
            eligible,
            key=lambda index: (
                model.score(feature_cache[(case.content_digest, index)]),
                -index,
            ),
        )
        results[case.family].append(
            (
                predicted_index + 1 == case.fault_line,
                len(eligible),
            )
        )
    suffix = "within_family" if within_family else "lofo"
    split = (
        "within-family-leave-template-out-v2"
        if within_family
        else "leave-family-out-v3"
    )
    return _baseline_metric(f"{kind.value}__{suffix}", split, results)


def _mechanism_features(case: CalibrationCase) -> frozenset[str]:
    normalized_prompt = _SPACE_RE.sub(
        " ", _NUMBER_RE.sub("#", case.prompt.lower())
    ).strip()
    token_features = _hashed_feature_buckets(
        "topic-token", _tokens(normalized_prompt)
    )
    char_features = _hashed_feature_buckets(
        "topic-char3",
        (
            normalized_prompt[index:index + 3]
            for index in range(max(0, len(normalized_prompt) - 2))
        ),
    )
    domain_feature = _hashed_feature_buckets("declared-domain", (case.domain,))
    return frozenset(
        token_features | char_features | domain_feature
    )


def _fit_mechanism_models(
    cases: Sequence[CalibrationCase],
    labels: Sequence[str],
    feature_cache: Mapping[str, frozenset[str]],
) -> dict[str, _BernoulliModel]:
    rows = tuple((feature_cache[case.content_digest], case.fault_kind) for case in cases)
    result: dict[str, _BernoulliModel] = {}
    for label in labels:
        positive = tuple(features for features, actual in rows if actual == label)
        negative = tuple(features for features, actual in rows if actual != label)
        if not positive or not negative:
            raise ValueError(
                "every disjoint training set must contain every fault mechanism"
            )
        result[label] = _fit_binary_presence_model(positive, negative)
    return result


def _evaluate_cross_prediction(
    cases: Sequence[CalibrationCase], config: CalibrationConfig
) -> MechanismPredictionMetric:
    labels = tuple(sorted({case.fault_kind for case in cases}))
    if len(labels) < 2:
        raise ValueError("mechanism prediction requires multiple fault kinds")
    feature_cache = {
        case.content_digest: _mechanism_features(case) for case in cases
    }
    models: dict[tuple[str, str], dict[str, _BernoulliModel]] = {}
    supports = Counter[str]()
    correct = Counter[str]()
    family_rows: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for case in cases:
        group = (case.family, case.template_id)
        group_models = models.get(group)
        if group_models is None:
            training = tuple(
                other
                for other in cases
                if other.family != case.family
            )
            if not training:
                raise ValueError(
                    "no family-disjoint mechanism-prediction data are available"
                )
            try:
                group_models = _fit_mechanism_models(
                    training,
                    labels,
                    feature_cache,
                )
            except ValueError:
                class_supports = tuple(
                    sum(case.fault_kind == label for case in cases) for label in labels
                )
                return MechanismPredictionMetric(
                    name="domain_topic_mechanism_prediction",
                    split="leave-family-out-v3",
                    classes=len(labels),
                    total=len(cases),
                    class_supports=class_supports,
                    class_correct=class_supports,
                    balanced_accuracy=1.0,
                    chance_accuracy=round(1.0 / len(labels), 6),
                    excess_over_chance=round(1.0 - 1.0 / len(labels), 6),
                    family_folds=len({case.family for case in cases}),
                    worst_family_excess_over_chance=1.0,
                )
            models[group] = group_models
        features = feature_cache[case.content_digest]
        predicted = max(
            labels,
            key=lambda label: (group_models[label].score(features), label),
        )
        supports[case.fault_kind] += 1
        family_rows[case.family].append((case.fault_kind, predicted))
        if predicted == case.fault_kind:
            correct[case.fault_kind] += 1
    class_supports = tuple(supports[label] for label in labels)
    class_correct = tuple(correct[label] for label in labels)
    balanced = sum(
        hits / support for hits, support in zip(class_correct, class_supports)
    ) / len(labels)
    chance = 1.0 / len(labels)
    family_excesses: list[float] = []
    for family in sorted(family_rows):
        rows = family_rows[family]
        recalls: list[float] = []
        for label in labels:
            label_rows = tuple(row for row in rows if row[0] == label)
            if not label_rows:
                recalls = [1.0]
                break
            recalls.append(sum(actual == predicted for actual, predicted in label_rows) / len(label_rows))
        family_balanced = sum(recalls) / len(recalls)
        family_excesses.append(max(0.0, family_balanced - chance))
    return MechanismPredictionMetric(
        name="domain_topic_mechanism_prediction",
        split="leave-family-out-v3",
        classes=len(labels),
        total=len(cases),
        class_supports=class_supports,
        class_correct=class_correct,
        balanced_accuracy=round(balanced, 6),
        chance_accuracy=round(chance, 6),
        excess_over_chance=round(max(0.0, balanced - chance), 6),
        family_folds=len(family_rows),
        worst_family_excess_over_chance=round(max(family_excesses), 6),
    )


def _stratum_metrics(cases: Sequence[CalibrationCase]) -> tuple[StratumMetric, ...]:
    selectors = (
        ("domain_fault", lambda case: (case.domain, case.fault_kind)),
        ("family", lambda case: case.family),
        ("family_template", lambda case: (case.family, case.template_id)),
        ("generator_cell", lambda case: case.generator_cell),
        ("template", lambda case: case.template_id),
    )
    result: list[StratumMetric] = []
    for kind, selector in selectors:
        grouped: dict[Any, list[CalibrationCase]] = defaultdict(list)
        for case in cases:
            grouped[selector(case)].append(case)
        for key in sorted(grouped, key=lambda value: canonical_json(value)):
            values = grouped[key]
            positions = _position_metric(values)
            plausible = sum(cheap_plausibility_check(case).passed for case in values)
            correct = sum(case.brain_off_correct for case in values)
            result.append(
                StratumMetric(
                    stratum_index=len(result),
                    stratum_kind=kind,
                    cases=len(values),
                    families=len({case.family for case in values}),
                    templates=len({case.template_id for case in values}),
                    domains=len({case.domain for case in values}),
                    fault_kinds=len({case.fault_kind for case in values}),
                    generator_cells=len({case.generator_cell for case in values}),
                    brain_off_accuracy=_rate(correct, len(values)),
                    plausibility_rate=_rate(plausible, len(values)),
                    fault_position_entropy=positions.normalized_entropy,
                    maximum_fault_position_share=positions.maximum_position_share,
                    position_uniform_total_variation=(
                        positions.uniform_total_variation
                    ),
                    minimum_cases_per_eligible_rank=(
                        positions.minimum_cases_per_eligible_rank
                    ),
                )
            )
    return tuple(result)


def _stratum_supports(
    stratum: StratumMetric, config: CalibrationConfig
) -> tuple[tuple[str, int, int], ...]:
    if stratum.stratum_kind == "generator_cell":
        return (
            ("cases", stratum.cases, config.minimum_generator_cell_cases),
            ("families", stratum.families, config.minimum_generator_cell_families),
            ("templates", stratum.templates, config.minimum_generator_cell_templates),
            ("domains", stratum.domains, config.minimum_generator_cell_domains),
            (
                "fault_kinds",
                stratum.fault_kinds,
                config.minimum_generator_cell_fault_kinds,
            ),
        )
    if stratum.stratum_kind == "domain_fault":
        return (
            ("cases", stratum.cases, config.minimum_domain_fault_cell_cases),
            ("families", stratum.families, config.minimum_domain_fault_cell_families),
            ("templates", stratum.templates, config.minimum_domain_fault_cell_templates),
        )
    if stratum.stratum_kind == "family":
        return (
            ("cases", stratum.cases, config.minimum_family_cases),
            ("templates", stratum.templates, config.minimum_family_templates),
            ("domains", stratum.domains, config.minimum_family_domains),
            ("fault_kinds", stratum.fault_kinds, config.minimum_family_fault_kinds),
        )
    if stratum.stratum_kind == "family_template":
        return (
            ("cases", stratum.cases, config.minimum_family_template_cases),
            ("domains", stratum.domains, config.minimum_family_template_domains),
            (
                "fault_kinds",
                stratum.fault_kinds,
                config.minimum_family_template_fault_kinds,
            ),
            (
                "generator_cells",
                stratum.generator_cells,
                config.minimum_family_template_generator_cells,
            ),
        )
    if stratum.stratum_kind == "template":
        return (
            ("cases", stratum.cases, config.minimum_template_cases),
            ("families", stratum.families, config.minimum_template_families),
            ("domains", stratum.domains, config.minimum_template_domains),
            ("fault_kinds", stratum.fault_kinds, config.minimum_template_fault_kinds),
        )
    raise AssertionError("unreachable stratum kind")


def _stratum_gates(
    strata: Sequence[StratumMetric], config: CalibrationConfig
) -> tuple[GateResult, ...]:
    result: list[GateResult] = []
    for stratum in strata:
        prefix = f"stratum_{stratum.stratum_index:04d}_{stratum.stratum_kind}"
        for label, observed, threshold in _stratum_supports(stratum, config):
            result.append(
                GateResult(
                    name=f"{prefix}_minimum_{label}",
                    passed=observed >= threshold,
                    observed=observed,
                    relation=">=",
                    threshold=threshold,
                )
            )
        result.extend(
            (
                GateResult(
                    name=f"{prefix}_brain_off_target_minimum",
                    passed=stratum.brain_off_accuracy >= config.target_accuracy_min,
                    observed=stratum.brain_off_accuracy,
                    relation=">=",
                    threshold=config.target_accuracy_min,
                ),
                GateResult(
                    name=f"{prefix}_brain_off_target_maximum",
                    passed=stratum.brain_off_accuracy <= config.target_accuracy_max,
                    observed=stratum.brain_off_accuracy,
                    relation="<=",
                    threshold=config.target_accuracy_max,
                ),
                GateResult(
                    name=f"{prefix}_brain_off_hard_reject_low",
                    passed=stratum.brain_off_accuracy >= config.reject_accuracy_below,
                    observed=stratum.brain_off_accuracy,
                    relation=">=",
                    threshold=config.reject_accuracy_below,
                ),
                GateResult(
                    name=f"{prefix}_brain_off_hard_reject_high",
                    passed=stratum.brain_off_accuracy <= config.reject_accuracy_above,
                    observed=stratum.brain_off_accuracy,
                    relation="<=",
                    threshold=config.reject_accuracy_above,
                ),
                GateResult(
                    name=f"{prefix}_fault_position_entropy",
                    passed=(
                        stratum.fault_position_entropy
                        >= config.minimum_fault_position_entropy
                    ),
                    observed=stratum.fault_position_entropy,
                    relation=">=",
                    threshold=config.minimum_fault_position_entropy,
                ),
                GateResult(
                    name=f"{prefix}_maximum_fault_position_share",
                    passed=(
                        stratum.maximum_fault_position_share
                        <= config.maximum_fault_position_share
                    ),
                    observed=stratum.maximum_fault_position_share,
                    relation="<=",
                    threshold=config.maximum_fault_position_share,
                ),
                GateResult(
                    name=f"{prefix}_position_uniform_total_variation",
                    passed=(
                        stratum.position_uniform_total_variation
                        <= config.maximum_position_uniform_total_variation
                    ),
                    observed=stratum.position_uniform_total_variation,
                    relation="<=",
                    threshold=config.maximum_position_uniform_total_variation,
                ),
                GateResult(
                    name=f"{prefix}_minimum_cases_per_eligible_rank",
                    passed=(
                        stratum.minimum_cases_per_eligible_rank
                        >= config.minimum_cases_per_eligible_rank
                    ),
                    observed=stratum.minimum_cases_per_eligible_rank,
                    relation=">=",
                    threshold=config.minimum_cases_per_eligible_rank,
                ),
                GateResult(
                    name=f"{prefix}_faulty_trace_plausibility",
                    passed=stratum.plausibility_rate >= config.minimum_plausibility_rate,
                    observed=stratum.plausibility_rate,
                    relation=">=",
                    threshold=config.minimum_plausibility_rate,
                ),
            )
        )
    return tuple(result)


def _report_gates(
    *,
    difficulty: DifficultyMetric,
    positions: FaultPositionMetric,
    plausibility_rate: float,
    baselines: Sequence[BaselineMetric],
    cross_prediction: MechanismPredictionMetric,
    strata: Sequence[StratumMetric],
    config: CalibrationConfig,
) -> tuple[GateResult, ...]:
    """Derive the complete canonical gate manifest from report metrics."""

    gates: list[GateResult] = [
        GateResult(
            name="brain_off_target_minimum",
            passed=difficulty.accuracy >= config.target_accuracy_min,
            observed=difficulty.accuracy,
            relation=">=",
            threshold=config.target_accuracy_min,
        ),
        GateResult(
            name="brain_off_target_maximum",
            passed=difficulty.accuracy <= config.target_accuracy_max,
            observed=difficulty.accuracy,
            relation="<=",
            threshold=config.target_accuracy_max,
        ),
        GateResult(
            name="brain_off_hard_reject_low",
            passed=difficulty.accuracy >= config.reject_accuracy_below,
            observed=difficulty.accuracy,
            relation=">=",
            threshold=config.reject_accuracy_below,
        ),
        GateResult(
            name="brain_off_hard_reject_high",
            passed=difficulty.accuracy <= config.reject_accuracy_above,
            observed=difficulty.accuracy,
            relation="<=",
            threshold=config.reject_accuracy_above,
        ),
        GateResult(
            name="fault_position_entropy",
            passed=positions.normalized_entropy >= config.minimum_fault_position_entropy,
            observed=positions.normalized_entropy,
            relation=">=",
            threshold=config.minimum_fault_position_entropy,
        ),
        GateResult(
            name="maximum_fault_position_share",
            passed=positions.maximum_position_share <= config.maximum_fault_position_share,
            observed=positions.maximum_position_share,
            relation="<=",
            threshold=config.maximum_fault_position_share,
        ),
        GateResult(
            name="position_uniform_total_variation",
            passed=(
                positions.uniform_total_variation
                <= config.maximum_position_uniform_total_variation
            ),
            observed=positions.uniform_total_variation,
            relation="<=",
            threshold=config.maximum_position_uniform_total_variation,
        ),
        GateResult(
            name="minimum_cases_per_eligible_rank",
            passed=(
                positions.minimum_cases_per_eligible_rank
                >= config.minimum_cases_per_eligible_rank
            ),
            observed=positions.minimum_cases_per_eligible_rank,
            relation=">=",
            threshold=config.minimum_cases_per_eligible_rank,
        ),
        GateResult(
            name="faulty_trace_plausibility",
            passed=plausibility_rate >= config.minimum_plausibility_rate,
            observed=plausibility_rate,
            relation=">=",
            threshold=config.minimum_plausibility_rate,
        ),
    ]
    for metric in baselines:
        gates.extend(
            (
                GateResult(
                    name=f"{metric.name}_excess_over_chance",
                    passed=(
                        metric.excess_over_chance
                        <= config.maximum_baseline_excess_over_chance
                    ),
                    observed=metric.excess_over_chance,
                    relation="<=",
                    threshold=config.maximum_baseline_excess_over_chance,
                ),
                GateResult(
                    name=f"{metric.name}_worst_family_excess_over_chance",
                    passed=(
                        metric.worst_family_excess_over_chance
                        <= config.maximum_worst_family_baseline_excess_over_chance
                    ),
                    observed=metric.worst_family_excess_over_chance,
                    relation="<=",
                    threshold=config.maximum_worst_family_baseline_excess_over_chance,
                ),
            )
        )
    gates.extend(
        (
            GateResult(
                name=f"{cross_prediction.name}_excess_over_chance",
                passed=(
                    cross_prediction.excess_over_chance
                    <= config.maximum_mechanism_excess_over_chance
                ),
                observed=cross_prediction.excess_over_chance,
                relation="<=",
                threshold=config.maximum_mechanism_excess_over_chance,
            ),
            GateResult(
                name=f"{cross_prediction.name}_worst_family_excess_over_chance",
                passed=(
                    cross_prediction.worst_family_excess_over_chance
                    <= config.maximum_mechanism_excess_over_chance
                ),
                observed=cross_prediction.worst_family_excess_over_chance,
                relation="<=",
                threshold=config.maximum_mechanism_excess_over_chance,
            ),
        )
    )
    gates.extend(_stratum_gates(strata, config))
    return tuple(gates)


def generate_calibration_report(
    cases: Iterable[CalibrationCase],
    *,
    state: CalibrationFreeze,
    seed: str,
    config: CalibrationConfig = DEFAULT_CONFIG,
) -> CalibrationReport:
    """Create deterministic aggregate evidence from a previously committed public design."""

    values = _bounded_cases(cases)
    if type(state) is not CalibrationFreeze or state.state is not FreezeState.DATA_BOUND:
        raise ValueError("calibration report requires a data-bound freeze state")
    if type(config) is not CalibrationConfig or config.digest() != state.config_digest:
        raise ValueError("calibration config does not match the committed state")
    state.verify_seed(seed)
    digest = case_set_digest(values)
    if state.bound_case_set_digest is None or not hmac.compare_digest(
        digest, state.bound_case_set_digest
    ):
        raise ValueError("calibration cases do not match the bound public case set")
    _validate_design(values, config)

    difficulty = _difficulty(values, config)
    positions = _position_metric(values)
    plausible_count = sum(cheap_plausibility_check(case).passed for case in values)
    plausibility_rate = _rate(plausible_count, len(values))
    baselines = tuple(
        _evaluate_disjoint_baseline(
            values,
            kind,
            within_family=within_family,
        )
        for kind in BaselineKind
        for within_family in (False, True)
    )
    cross_prediction = _evaluate_cross_prediction(values, config)
    strata = _stratum_metrics(values)

    gates = _report_gates(
        difficulty=difficulty,
        positions=positions,
        plausibility_rate=plausibility_rate,
        baselines=baselines,
        cross_prediction=cross_prediction,
        strata=strata,
        config=config,
    )

    return CalibrationReport(
        config=config,
        config_digest=config.digest(),
        seed_commitment=state.seed_digest or "",
        case_set_digest=digest,
        case_count=len(values),
        family_count=len({case.family for case in values}),
        template_count=len({case.template_id for case in values}),
        domain_count=len({case.domain for case in values}),
        fault_kind_count=len({case.fault_kind for case in values}),
        difficulty=difficulty,
        fault_positions=positions,
        plausibility_rate=plausibility_rate,
        baselines=baselines,
        domain_fault_cross_prediction=cross_prediction,
        strata=strata,
        gates=gates,
    )


def calibration_input_document(
    cases: Iterable[CalibrationCase], config: CalibrationConfig = DEFAULT_CONFIG
) -> dict[str, Any]:
    if type(config) is not CalibrationConfig or not hmac.compare_digest(
        config.digest(), DEFAULT_CONFIG.digest()
    ):
        raise ValueError("calibration input must use the sealed calibration-v2 config")
    values = _bounded_cases(cases)
    _validate_design(values, config)
    case_set_digest(values)
    ordered = sorted(values, key=lambda case: (case.case_id, case.content_digest))
    payload = {
        "cases": [case.to_dict() for case in ordered],
        "config": config.to_dict(),
        "schema": CALIBRATION_INPUT_SCHEMA,
        "schema_version": CALIBRATION_INPUT_SCHEMA_VERSION,
    }
    return {**payload, "content_digest": _digest(payload)}


def load_calibration_input(raw: str) -> tuple[CalibrationConfig, tuple[CalibrationCase, ...]]:
    if type(raw) is not str or len(raw.encode("utf-8")) > _MAX_INPUT_BYTES:
        raise ValueError("calibration input exceeds the JSON budget")
    try:
        parsed = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid calibration input JSON") from exc
    value = _mapping(parsed, "calibration input")
    _require_canonical_json_text(raw, parsed, "calibration input")
    _strict_keys(
        value,
        {"cases", "config", "content_digest", "schema", "schema_version"},
        "calibration input",
    )
    if (
        value["schema"] != CALIBRATION_INPUT_SCHEMA
        or value["schema_version"] != CALIBRATION_INPUT_SCHEMA_VERSION
    ):
        raise ValueError("unsupported calibration input schema")
    supplied_digest = _sha256(value["content_digest"], "calibration input digest")
    digest_payload = {
        "cases": value["cases"],
        "config": value["config"],
        "schema": value["schema"],
        "schema_version": value["schema_version"],
    }
    if not hmac.compare_digest(supplied_digest, _digest(digest_payload)):
        raise ValueError("calibration input digest does not match its canonical payload")
    raw_cases = value["cases"]
    if type(raw_cases) is not list:
        raise ValueError("calibration cases must be an array")
    if not MIN_CALIBRATION_CASES_V2 <= len(raw_cases) <= MAX_CALIBRATION_CASES_V2:
        raise ValueError(
            "freeze-ready calibration input requires between "
            f"{MIN_CALIBRATION_CASES_V2} and {MAX_CALIBRATION_CASES_V2} cases"
        )
    config = CalibrationConfig.from_dict(value["config"])
    if not hmac.compare_digest(config.digest(), DEFAULT_CONFIG.digest()):
        raise ValueError("calibration input must use the sealed calibration-v2 config")
    cases = tuple(CalibrationCase.from_dict(case) for case in raw_cases)
    if cases != tuple(sorted(cases, key=lambda case: (case.case_id, case.content_digest))):
        raise ValueError("calibration input cases must use canonical case order")
    _validate_design(cases, config)
    case_set_digest(cases)
    return config, cases


def load_calibration_freeze(raw: str) -> CalibrationFreeze:
    if type(raw) is not str or len(raw.encode("utf-8")) > 1_000_000:
        raise ValueError("calibration freeze exceeds the JSON budget")
    try:
        parsed = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid calibration freeze JSON") from exc
    _require_canonical_json_text(raw, parsed, "calibration freeze")
    return CalibrationFreeze.from_dict(parsed)


def load_calibration_report(raw: str) -> CalibrationReport:
    if type(raw) is not str or len(raw.encode("utf-8")) > 2_000_000:
        raise ValueError("calibration report exceeds the JSON budget")
    try:
        parsed = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid calibration report JSON") from exc
    _require_canonical_json_text(raw, parsed, "calibration report")
    return CalibrationReport.from_dict(parsed)


def load_calibration_bundle(raw: str) -> CalibrationBundle:
    if type(raw) is not str or len(raw.encode("utf-8")) > 4_000_000:
        raise ValueError("calibration bundle exceeds the JSON budget")
    try:
        parsed = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid calibration bundle JSON") from exc
    _require_canonical_json_text(raw, parsed, "calibration bundle")
    return CalibrationBundle.from_dict(parsed)


def _matching_existing_bundle(path: Path, expected: CalibrationBundle) -> bool:
    try:
        existing = load_calibration_bundle(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise FileExistsError(
            "calibration bundle destination exists but is not the same valid bundle"
        ) from exc
    if (
        not hmac.compare_digest(existing.content_digest, expected.content_digest)
        or existing.canonical_json() != expected.canonical_json()
    ):
        raise FileExistsError("calibration bundle destination contains a different run")
    return True


def publish_calibration_bundle(path: str | Path, bundle: CalibrationBundle) -> bool:
    """Atomically publish one bundle without replacement; identical resume is idempotent.

    The temporary file is fully flushed and reloaded before an atomic same-directory hard-link
    creates the final name.  A crash before that link leaves no final artifact.  The link cannot
    replace an existing destination on either Windows or POSIX.
    """

    if type(bundle) is not CalibrationBundle:
        raise ValueError("bundle publisher requires a validated calibration bundle")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        _matching_existing_bundle(target, bundle)
        return False
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(bundle.canonical_json())
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        reloaded = load_calibration_bundle(temporary.read_text(encoding="utf-8"))
        if reloaded.canonical_json() != bundle.canonical_json():
            raise ValueError("temporary calibration bundle failed deterministic reload")
        try:
            os.link(temporary, target)
        except FileExistsError:
            _matching_existing_bundle(target, bundle)
            return False
        published = load_calibration_bundle(target.read_text(encoding="utf-8"))
        if published.canonical_json() != bundle.canonical_json():
            raise ValueError("published calibration bundle failed deterministic reload")
        return True
    finally:
        temporary.unlink(missing_ok=True)


def write_new_text(path: str | Path, text: str) -> None:
    """Write a new UTF-8 artifact without silently replacing calibration evidence."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.write("\n")
