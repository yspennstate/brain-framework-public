"""Deterministic public-development challenges with subtle, causal faults.

Version 2 is deliberately separate from the historical sealed challenge bank.
It contains no holdout reader, unsealing path, retrieval card, or model runner.
Each generated case has one interior root fault, an unchanged downstream suffix,
an exact one-line repair, and a separately typed diagnostic witness.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from fractions import Fraction
from itertools import combinations, islice
from pathlib import Path
from types import FunctionType, MappingProxyType
from typing import Any

from .benchmark import SolverView
from .calibration_v2 import CalibrationCase, OracleReceipt

CASE_SCHEMA_V2 = "org.math-research-brain/hard-challenge-case"
CASE_SCHEMA_VERSION_V2 = 2
WITNESS_SCHEMA_V2 = "org.math-research-brain/hard-challenge-witness"
WITNESS_SCHEMA_VERSION_V2 = 2
ANSWER_SCHEMA_V2 = "org.math-research-brain/hard-challenge-answer"
ANSWER_SCHEMA_VERSION_V2 = 2
SEMANTIC_PROGRAM_SCHEMA_V2 = "org.math-research-brain/hard-challenge-semantic-program"
SEMANTIC_PROGRAM_SCHEMA_VERSION_V2 = 2
INTERPRETER_SCHEMA_V2 = "org.math-research-brain/hard-challenge-interpreter-v1"
VERIFIER_ARTIFACT_SCHEMA_V2 = "org.math-research-brain/hard-challenge-verifier-artifact-v1"
GENERATOR_VERSION_V2 = "public-hard-challenge-v2-relational-surface"

MIN_TRACE_LINES_V2 = 6
MAX_TRACE_LINES_V2 = 12
MAX_CASE_JSON_BYTES_V2 = 32_768
MAX_PROMPT_BYTES_V2 = 4_096
MAX_LINE_BYTES_V2 = 1_024
MAX_WITNESS_JSON_BYTES_V2 = 4_096
MAX_SOLVER_OUTPUT_BYTES_V2 = 4_096
MAX_PARAMETER_JSON_BYTES_V2 = 4_096
MAX_SEED_V2 = 2**32 - 1
MAX_INTEGER_ABS_V2 = 10**9
MAX_BANK_SEEDS_V2 = 96


class FamilyV2(StrEnum):
    GENERALIZED_CRT_PERIOD = "generalized_crt_period"
    BOUNDED_COMPOSITION_OVERLAP = "bounded_composition_overlap"
    NEGATIVE_EDGE_FINALIZATION = "negative_edge_finalization"
    FORCED_RECURRENCE_RESONANCE = "forced_recurrence_resonance"


FAMILY_ORDER_V2 = tuple(FamilyV2)


class DerivationFamilyV2(StrEnum):
    FORWARD_INVARIANT = "forward_invariant"
    ENDPOINT_FIRST = "endpoint_first"
    ALTERNATING_CROSS_CHECK = "alternating_cross_check"
    REVERSE_BACK_CHECK = "reverse_back_check"


DERIVATION_FAMILY_ORDER_V2 = tuple(DerivationFamilyV2)


class FaultMechanismV2(StrEnum):
    SINGLE_SOURCE_SHORTCUT = "single_source_shortcut"
    DUPLICATED_CORRECTION = "duplicated_correction"


FAULT_MECHANISM_ORDER_V2 = tuple(FaultMechanismV2)


class ParameterCellV2(StrEnum):
    TABLE_DRIVEN = "table_driven"
    CONSTRUCTIVE = "constructive"


PARAMETER_CELL_ORDER_V2 = tuple(ParameterCellV2)


CALIBRATION_SCHEDULE_VERSION_V2 = "public-hard-v2/orthogonal-320/3"
CALIBRATION_CASE_COUNT_V2 = 320
CALIBRATION_ELIGIBLE_FAULT_LINES_V2 = tuple(range(3, 11))

# Versioned sealed schedule constants.  These are deliberately tables rather
# than low-order formulas: an earlier parity formula made the fault mechanism
# exactly recoverable from root parity and template parity.
_CALIBRATION_MECHANISM_FLIPS_V2 = (
    (1, 1, 1, 1),
    (1, 0, 1, 0),
    (0, 0, 0, 0),
    (0, 1, 1, 0),
)
_CALIBRATION_BASE_ROOT_GROUPS_V2 = (
    (0, 3, 1),
    (4, 7, 2),
    (5, 6, 8),
    (9, 10, 11),
    (12, 15),
    (16, 19),
    (13, 14),
    (17, 18),
)
_CALIBRATION_ROOT_PERMUTATIONS_V2 = (
    (
        (3, 0, 1, 6, 4, 2, 7, 5),
        (4, 6, 5, 3, 0, 2, 1, 7),
        (2, 6, 4, 1, 7, 0, 3, 5),
        (1, 4, 5, 0, 2, 7, 3, 6),
    ),
    (
        (2, 1, 7, 3, 5, 6, 0, 4),
        (5, 4, 7, 1, 0, 6, 2, 3),
        (1, 3, 6, 0, 5, 7, 4, 2),
        (0, 6, 7, 4, 3, 2, 5, 1),
    ),
    (
        (6, 3, 4, 0, 2, 1, 5, 7),
        (5, 7, 1, 2, 4, 3, 0, 6),
        (7, 0, 3, 6, 4, 2, 5, 1),
        (4, 3, 6, 2, 0, 5, 7, 1),
    ),
    (
        (1, 6, 2, 3, 7, 4, 5, 0),
        (7, 0, 1, 5, 4, 2, 3, 6),
        (4, 2, 3, 0, 6, 7, 5, 1),
        (5, 3, 6, 4, 0, 7, 2, 1),
    ),
)

_DERIVATION_MANDATORY_PERMUTATIONS_V2 = (
    (0, 1),
    (1, 0),
    (0, 1),
    (1, 0),
)
_DERIVATION_DETAIL_PERMUTATIONS_V2 = (
    (0, 1, 2, 3, 4, 5, 6),
    (6, 0, 1, 2, 3, 4, 5),
    (0, 2, 4, 6, 1, 3, 5),
    (6, 5, 4, 3, 2, 1, 0),
)

# Claim-role shifts exclude both the root-position and fault-mechanism axes.
# The table keeps every discourse/derivation slice supported by several roles.
_RELATIONAL_ROLE_SHIFTS_V2 = (
    (0, 1, 2, 3),
    (2, 3, 4, 5),
    (4, 5, 6, 7),
    (6, 7, 0, 1),
)


class WitnessKindV2(StrEnum):
    CRT_PERIOD_COUNTEREXAMPLE = "crt-period-counterexample-v2"
    COMPOSITION_OVERLAP_ASSIGNMENT = "composition-overlap-assignment-v2"
    NEGATIVE_EDGE_PATH = "negative-edge-path-v2"
    RESONANCE_RESIDUAL = "resonance-residual-v2"


class MathValueKindV2(StrEnum):
    INTEGER = "integer"
    RATIONAL = "rational"
    INTEGER_VECTOR = "integer_vector"
    SYMBOL_VECTOR = "symbol_vector"


class UniversalWitnessCodeV2(StrEnum):
    SOLUTION_PAIR = "solution_pair"
    FEASIBLE_ASSIGNMENT = "feasible_assignment"
    DIRECTED_PATH = "directed_path"
    POLYNOMIAL_IDENTITY = "polynomial_identity"


class ExpressionKindV2(StrEnum):
    LITERAL = "literal"
    STATE = "state"
    ADD = "add"
    SUBTRACT = "subtract"
    MULTIPLY = "multiply"


class TransitionKindV2(StrEnum):
    CHECK = "check"
    ASSIGN_CHECKED = "assign_checked"
    DERIVE = "derive"


_FAMILY_CODES = {
    FamilyV2.GENERALIZED_CRT_PERIOD: "CRT",
    FamilyV2.BOUNDED_COMPOSITION_OVERLAP: "COM",
    FamilyV2.NEGATIVE_EDGE_FINALIZATION: "SPT",
    FamilyV2.FORCED_RECURRENCE_RESONANCE: "REC",
}

_FAMILY_WITNESSES = {
    FamilyV2.GENERALIZED_CRT_PERIOD: WitnessKindV2.CRT_PERIOD_COUNTEREXAMPLE,
    FamilyV2.BOUNDED_COMPOSITION_OVERLAP: WitnessKindV2.COMPOSITION_OVERLAP_ASSIGNMENT,
    FamilyV2.NEGATIVE_EDGE_FINALIZATION: WitnessKindV2.NEGATIVE_EDGE_PATH,
    FamilyV2.FORCED_RECURRENCE_RESONANCE: WitnessKindV2.RESONANCE_RESIDUAL,
}

_UNIVERSAL_WITNESS_CODES = {
    FamilyV2.GENERALIZED_CRT_PERIOD: UniversalWitnessCodeV2.SOLUTION_PAIR,
    FamilyV2.BOUNDED_COMPOSITION_OVERLAP: UniversalWitnessCodeV2.FEASIBLE_ASSIGNMENT,
    FamilyV2.NEGATIVE_EDGE_FINALIZATION: UniversalWitnessCodeV2.DIRECTED_PATH,
    FamilyV2.FORCED_RECURRENCE_RESONANCE: UniversalWitnessCodeV2.POLYNOMIAL_IDENTITY,
}

_VERIFIER_NAMES = {
    FamilyV2.GENERALIZED_CRT_PERIOD: ("bounded-residue-enumeration-v2", "bezout-crt-v2"),
    FamilyV2.BOUNDED_COMPOSITION_OVERLAP: (
        "inclusion-exclusion-v2",
        "bounded-dynamic-program-v2",
    ),
    FamilyV2.NEGATIVE_EDGE_FINALIZATION: (
        "bellman-ford-v2",
        "simple-path-enumeration-v2",
    ),
    FamilyV2.FORCED_RECURRENCE_RESONANCE: (
        "symbolic-finite-difference-v2",
        "characteristic-derivative-identity-v2",
    ),
}

_DOMAIN_NAMES = {
    FamilyV2.GENERALIZED_CRT_PERIOD: "number theory",
    FamilyV2.BOUNDED_COMPOSITION_OVERLAP: "enumerative combinatorics",
    FamilyV2.NEGATIVE_EDGE_FINALIZATION: "directed graph algorithms",
    FamilyV2.FORCED_RECURRENCE_RESONANCE: "linear recurrences",
}

_CLAIM_NAMES_V2 = tuple(f"claim{index}" for index in range(8))

# These descriptions are the complete public semantics of every randomized
# surface alias.  No target, source, or downstream state is intentionally left
# unlegendized: a solver can evaluate each displayed equality from the prompt.
_SURFACE_DESCRIPTIONS_V2 = {
    FamilyV2.GENERALIZED_CRT_PERIOD: {
        "product_basis": "the raw modulus product m1*m2",
        "reduced_pair_basis": "the twice-reduced product (m1*m2)/gcd(m1,m2)^2",
        "period_basis": "the solution-class period lcm(m1,m2)",
        "g": "gcd(m1,m2)",
        "reduced1": "the first reduced modulus m1/gcd(m1,m2)",
        "reduced2": "the second reduced modulus m2/gcd(m1,m2)",
        "q0": "the least CRT quotient solving the reduced congruence",
        "x0": "the least nonnegative simultaneous solution",
        "claim0": "the solution-class period lcm(m1,m2)",
        "claim1": "the next solution x0+lcm(m1,m2)",
        "claim2": "the span of two solution periods 2*lcm(m1,m2)",
        "claim3": "the solution x0+2*lcm(m1,m2)",
        "claim4": "the period-plus-gcd audit lcm(m1,m2)+gcd(m1,m2)",
        "claim5": "the period plus the first reduced modulus",
        "claim6": "the period plus the second reduced modulus",
        "claim7": "the period plus the least CRT quotient",
        "checksum": "the sum of the eight claim quantities",
        "checksum_copy": "a copied value of the eight-claim checksum",
    },
    FamilyV2.BOUNDED_COMPOSITION_OVERLAP: {
        "single_shift_basis": "the count produced by one upper-bound shift",
        "pair_intersection_basis": "the count produced by repeating a pair correction",
        "joint_basis": "the intersection count J for violating the first two bounds",
        "U": "the unbounded stars-and-bars count U",
        "S": "the sum S of the three single-bound violation counts",
        "single1": "the first single-bound violation count",
        "single2": "the second single-bound violation count",
        "single3": "the third single-bound violation count",
        "total": "the required coordinate total",
        "claim0": "the intersection count J for violating the first two bounds",
        "claim1": "the corrected bounded count U-S+J",
        "claim2": "J plus the first single-bound violation count",
        "claim3": "J plus the second single-bound violation count",
        "claim4": "J plus the third single-bound violation count",
        "claim5": "twice the true joint intersection count 2*J",
        "claim6": "J plus the required coordinate total",
        "claim7": "the unbounded count plus J",
        "checksum": "the sum of the eight claim quantities",
        "checksum_copy": "a copied value of the eight-claim checksum",
    },
    FamilyV2.NEGATIVE_EDGE_FINALIZATION: {
        "sa": "the direct s-to-a path length",
        "sb": "the direct s-to-b path length",
        "incoming_basis": "the shortest complete incoming-path length A at a",
        "second_pass_basis": "the value from applying the a-improvement twice",
        "ba": "the b-to-a edge weight",
        "at": "the a-to-t edge weight",
        "bt": "the b-to-t edge weight",
        "st": "the direct s-to-t edge weight",
        "claim0": "the shortest complete incoming-path length A at a",
        "claim1": "the shortest s-to-t path length A+weight(a,t)",
        "claim2": "the direct-label improvement length(s,a)-A",
        "claim3": "A plus weight(b,t)",
        "claim4": "A plus weight(s,t)",
        "claim5": "twice the complete incoming-path length 2*A",
        "claim6": "A minus weight(b,a)",
        "claim7": "A plus the direct s-to-b path length",
        "checksum": "the sum of the eight claim quantities",
        "checksum_copy": "a copied value of the eight-claim checksum",
    },
    FamilyV2.FORCED_RECURRENCE_RESONANCE: {
        "linear_degree": "the unadjusted single-resonance trial degree 1",
        "factor_count": "the characteristic-root multiplicity D=2",
        "cubic_degree": "the degree 3 produced by duplicating the correction",
        "r": "the repeated characteristic root r",
        "K": "the particular coefficient K=1/(2*r^2)",
        "c0": "the initial value a_0",
        "c1": "the initial value a_1",
        "claim0": "the required resonance multiplier degree D",
        "claim1": "the degree audit D+1",
        "claim2": "twice the required degree 2*D",
        "claim3": "the required degree plus the root D+r",
        "claim4": "the required degree plus the initial value D+a_0",
        "claim5": "the required degree plus the initial value D+a_1",
        "claim6": "the predecessor degree D-1",
        "claim7": "the coefficient-weighted degree D*K",
        "checksum": "the sum of the eight claim quantities",
        "checksum_copy": "a copied value of the eight-claim checksum",
    },
}

_REPAIR_DESCRIPTIONS = {
    FaultMechanismV2.SINGLE_SOURCE_SHORTCUT: (
        "replace the single-source shortcut with the independently derived value"
    ),
    FaultMechanismV2.DUPLICATED_CORRECTION: (
        "remove the repeated correction and retain one independently derived value"
    ),
}

_FAMILY_TRUTHS = {
    FamilyV2.GENERALIZED_CRT_PERIOD: (
        "A compatible two-modulus system is one residue class modulo the least common multiple."
    ),
    FamilyV2.BOUNDED_COMPOSITION_OVERLAP: (
        "Every violated upper bound shifts by its bound plus one, and intersection shifts add."
    ),
    FamilyV2.NEGATIVE_EDGE_FINALIZATION: (
        "A negative incoming edge can improve a smallest tentative label after it is extracted."
    ),
    FamilyV2.FORCED_RECURRENCE_RESONANCE: (
        "A trial resonant with a root of multiplicity m must be multiplied by n^m."
    ),
}

_RELEVANCE_KEYS = {
    FamilyV2.GENERALIZED_CRT_PERIOD: (
        "generalized-crt",
        "least-common-multiple",
        "noncoprime-moduli",
        "uniqueness-period",
    ),
    FamilyV2.BOUNDED_COMPOSITION_OVERLAP: (
        "bounded-compositions",
        "inclusion-exclusion",
        "joint-shift",
        "overlap",
    ),
    FamilyV2.NEGATIVE_EDGE_FINALIZATION: (
        "directed-shortest-path",
        "negative-edge",
        "premature-finalization",
        "relaxation",
    ),
    FamilyV2.FORCED_RECURRENCE_RESONANCE: (
        "annihilator",
        "forced-recurrence",
        "resonance",
        "trial-solution",
    ),
}


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _reject_float(token: str) -> None:
    raise ValueError(f"floating-point JSON numbers are not allowed: {token}")


def _reject_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON numbers are not allowed: {token}")


def strict_json_loads_v2(raw: str, *, max_bytes: int = MAX_CASE_JSON_BYTES_V2) -> Any:
    """Load bounded JSON while rejecting duplicate keys, floats, and constants."""

    # Exact built-ins are a security boundary: a str subclass can override
    # ``encode`` and make an oversized payload appear to fit the byte budget.
    if type(raw) is not str:
        raise ValueError("JSON input must be text")
    checked_max_bytes = _integer(
        max_bytes,
        "JSON byte budget",
        minimum=1,
        maximum=MAX_CASE_JSON_BYTES_V2,
    )
    try:
        encoded = raw.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("JSON input must be valid UTF-8") from exc
    if not encoded or len(encoded) > checked_max_bytes:
        raise ValueError(f"JSON input must contain between 1 and {checked_max_bytes} bytes")
    try:
        return json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc.msg}") from exc
    except RecursionError as exc:
        raise ValueError("invalid JSON: nesting budget exceeded") from exc


def canonical_json_v2(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_bytes_v2(value: Any) -> bytes:
    try:
        return canonical_json_v2(value).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("value must be valid UTF-8") from exc


def content_digest_v2(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes_v2(value)).hexdigest()


def calibration_schedule_digest_v2() -> str:
    """Bind every versioned table that defines the 320-case calibration grid."""

    return content_digest_v2(
        {
            "base_root_groups": _CALIBRATION_BASE_ROOT_GROUPS_V2,
            "derivation_detail_permutations": _DERIVATION_DETAIL_PERMUTATIONS_V2,
            "derivation_mandatory_permutations": (_DERIVATION_MANDATORY_PERMUTATIONS_V2),
            "mechanism_flips": _CALIBRATION_MECHANISM_FLIPS_V2,
            "relational_role_shifts": _RELATIONAL_ROLE_SHIFTS_V2,
            "root_permutations": _CALIBRATION_ROOT_PERMUTATIONS_V2,
            "version": CALIBRATION_SCHEDULE_VERSION_V2,
        }
    )


def _strict_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    # Keep hostile dict/key subclasses outside every serialized record parser.
    if type(value) is not dict or not all(type(key) is str for key in value):
        raise ValueError(f"{label} must be an object with string keys")
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"invalid {label} keys; missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )
    return value


def _integer(value: Any, label: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int:
        raise ValueError(f"{label} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{label} must be in [{minimum}, {maximum}]")
    return value


def _text(value: Any, label: str, *, max_bytes: int) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8") from exc
    if size > max_bytes:
        raise ValueError(f"{label} exceeds the {max_bytes}-byte budget")
    return value


def _freeze_json(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        raise ValueError("JSON value exceeds the nesting budget")
    if type(value) in (bool, float) or value is None:
        raise ValueError(
            "parameters and witnesses allow only strings, integers, arrays, and objects"
        )
    if type(value) is int:
        return _integer(
            value,
            "JSON integer",
            minimum=-MAX_INTEGER_ABS_V2,
            maximum=MAX_INTEGER_ABS_V2,
        )
    if type(value) is str:
        return _text(value, "JSON string", max_bytes=MAX_LINE_BYTES_V2)
    if type(value) in (list, tuple):
        if len(value) > 64:
            raise ValueError("JSON array exceeds the 64-item budget")
        return tuple(_freeze_json(item, depth=depth + 1) for item in value)
    if type(value) in (dict, type(MappingProxyType({}))):
        if len(value) > 64 or not all(type(key) is str for key in value):
            raise ValueError("JSON object has invalid keys or exceeds the 64-key budget")
        return MappingProxyType(
            {key: _freeze_json(value[key], depth=depth + 1) for key in sorted(value)}
        )
    raise ValueError(f"unsupported JSON value type: {type(value).__name__}")


def _rejected_type_v2(value: Any) -> str:
    if value is None:
        return "none"
    for expected, label in (
        (str, "string"),
        (bytes, "bytes"),
        (dict, "object"),
        (list, "list"),
        (tuple, "tuple"),
        (int, "integer"),
        (float, "float"),
    ):
        if type(value) is expected:
            return label
    return "other"


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _canonical_fraction(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else str(value)


def _parse_fraction(value: Any, label: str) -> Fraction:
    text = _text(value, label, max_bytes=128)
    if not re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:/[1-9][0-9]*)?", text):
        raise ValueError(f"{label} must be a canonical integer or fraction")
    result = Fraction(text)
    if _canonical_fraction(result) != text:
        raise ValueError(f"{label} must be reduced and canonical")
    return result


@dataclass(frozen=True, slots=True)
class NormalizedMathValueV2:
    """One canonical mathematical value shared by every solver-output family."""

    kind: MathValueKindV2
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if type(self.kind) is not MathValueKindV2:
            raise ValueError("normalized math value kind is invalid")
        frozen = _freeze_json(self.payload)
        if not isinstance(frozen, Mapping):
            raise ValueError("normalized math value payload must be an object")
        object.__setattr__(self, "payload", frozen)
        value = _thaw_json(frozen)
        if self.kind is MathValueKindV2.INTEGER:
            _strict_keys(value, {"value"}, "integer math value")
            _integer(
                value["value"],
                "integer math value",
                minimum=-MAX_INTEGER_ABS_V2,
                maximum=MAX_INTEGER_ABS_V2,
            )
            return
        if self.kind is MathValueKindV2.RATIONAL:
            _strict_keys(value, {"denominator", "numerator"}, "rational math value")
            numerator = _integer(
                value["numerator"],
                "rational numerator",
                minimum=-MAX_INTEGER_ABS_V2,
                maximum=MAX_INTEGER_ABS_V2,
            )
            denominator = _integer(
                value["denominator"],
                "rational denominator",
                minimum=2,
                maximum=MAX_INTEGER_ABS_V2,
            )
            if numerator == 0 or math.gcd(abs(numerator), denominator) != 1:
                raise ValueError("rational math value must be nonzero, reduced, and canonical")
            return
        if self.kind is MathValueKindV2.INTEGER_VECTOR:
            _strict_keys(value, {"items"}, "integer-vector math value")
            items = value["items"]
            if type(items) is not list or not 1 <= len(items) <= 16:
                raise ValueError("integer vector must contain between 1 and 16 items")
            for item in items:
                _integer(
                    item,
                    "integer-vector item",
                    minimum=-MAX_INTEGER_ABS_V2,
                    maximum=MAX_INTEGER_ABS_V2,
                )
            return
        if self.kind is MathValueKindV2.SYMBOL_VECTOR:
            _strict_keys(value, {"items"}, "symbol-vector math value")
            items = value["items"]
            if type(items) is not list or not 1 <= len(items) <= 16:
                raise ValueError("symbol vector must contain between 1 and 16 items")
            for item in items:
                symbol = _text(item, "symbol-vector item", max_bytes=64)
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", symbol):
                    raise ValueError("symbol-vector items must be simple identifiers")
            return
        raise AssertionError("unreachable normalized math value kind")

    @classmethod
    def scalar(cls, value: int | Fraction) -> NormalizedMathValueV2:
        if type(value) not in (int, Fraction):
            raise ValueError("scalar input must be an exact integer or fraction")
        fraction = Fraction(value)
        if fraction.denominator == 1:
            return cls(MathValueKindV2.INTEGER, {"value": fraction.numerator})
        return cls(
            MathValueKindV2.RATIONAL,
            {"denominator": fraction.denominator, "numerator": fraction.numerator},
        )

    @classmethod
    def integer_vector(cls, values: Sequence[int]) -> NormalizedMathValueV2:
        if type(values) not in (list, tuple):
            raise ValueError("integer-vector input must be a bounded list or tuple")
        return cls(MathValueKindV2.INTEGER_VECTOR, {"items": list(values)})

    @classmethod
    def symbol_vector(cls, values: Sequence[str]) -> NormalizedMathValueV2:
        if type(values) not in (list, tuple):
            raise ValueError("symbol-vector input must be a bounded list or tuple")
        return cls(MathValueKindV2.SYMBOL_VECTOR, {"items": list(values)})

    def as_fraction(self) -> Fraction:
        if self.kind is MathValueKindV2.INTEGER:
            return Fraction(self.payload["value"])
        if self.kind is MathValueKindV2.RATIONAL:
            return Fraction(self.payload["numerator"], self.payload["denominator"])
        raise ValueError("math value is not scalar")

    def as_integer_vector(self) -> tuple[int, ...]:
        if self.kind is not MathValueKindV2.INTEGER_VECTOR:
            raise ValueError("math value is not an integer vector")
        return tuple(self.payload["items"])

    def as_symbol_vector(self) -> tuple[str, ...]:
        if self.kind is not MathValueKindV2.SYMBOL_VECTOR:
            raise ValueError("math value is not a symbol vector")
        return tuple(self.payload["items"])

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "payload": _thaw_json(self.payload)}

    @classmethod
    def from_dict(cls, raw: Any) -> NormalizedMathValueV2:
        value = _strict_keys(raw, {"kind", "payload"}, "normalized math value")
        try:
            kind = MathValueKindV2(_text(value["kind"], "normalized math value kind", max_bytes=64))
        except (TypeError, ValueError) as exc:
            raise ValueError("normalized math value kind is invalid") from exc
        return cls(kind=kind, payload=value["payload"])


@dataclass(frozen=True, slots=True)
class RepairSubmissionV2:
    target: str
    value: NormalizedMathValueV2

    def __post_init__(self) -> None:
        target = _text(self.target, "repair target", max_bytes=64)
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", target):
            raise ValueError("repair target must be a simple identifier")
        if type(self.value) is not NormalizedMathValueV2:
            raise ValueError("repair value must be normalized")

    def to_dict(self) -> dict[str, Any]:
        return {"target": self.target, "value": self.value.to_dict()}

    @classmethod
    def from_dict(cls, raw: Any) -> RepairSubmissionV2:
        value = _strict_keys(raw, {"target", "value"}, "repair submission")
        return cls(
            target=value["target"],
            value=NormalizedMathValueV2.from_dict(value["value"]),
        )


@dataclass(frozen=True, slots=True)
class WitnessSubmissionV2:
    code: UniversalWitnessCodeV2
    values: tuple[NormalizedMathValueV2, ...]

    def __post_init__(self) -> None:
        if type(self.code) is not UniversalWitnessCodeV2:
            raise ValueError("universal witness code is invalid")
        if (
            type(self.values) is not tuple
            or not 1 <= len(self.values) <= 8
            or not all(type(value) is NormalizedMathValueV2 for value in self.values)
        ):
            raise ValueError("universal witness must contain 1 to 8 normalized values")

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "values": [value.to_dict() for value in self.values]}

    @classmethod
    def from_dict(cls, raw: Any) -> WitnessSubmissionV2:
        value = _strict_keys(raw, {"code", "values"}, "witness submission")
        try:
            code = UniversalWitnessCodeV2(
                _text(value["code"], "universal witness code", max_bytes=64)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("universal witness code is invalid") from exc
        values = value["values"]
        if type(values) is not list or len(values) > 8:
            raise ValueError("witness values must be a bounded array")
        return cls(code=code, values=tuple(NormalizedMathValueV2.from_dict(v) for v in values))


@dataclass(frozen=True, slots=True)
class SolverOutputV2:
    first_invalid_line: int
    error_code: str
    repair: RepairSubmissionV2
    witness: WitnessSubmissionV2
    schema: str = ANSWER_SCHEMA_V2
    schema_version: int = ANSWER_SCHEMA_VERSION_V2

    def __post_init__(self) -> None:
        schema = _text(self.schema, "answer schema", max_bytes=128)
        schema_version = _integer(
            self.schema_version, "answer schema version", minimum=0, maximum=100
        )
        error_code = _text(self.error_code, "universal error code", max_bytes=64)
        if schema != ANSWER_SCHEMA_V2 or schema_version != ANSWER_SCHEMA_VERSION_V2:
            raise ValueError("unsupported hard-challenge answer schema")
        _integer(self.first_invalid_line, "first invalid line", minimum=2, maximum=11)
        if error_code != "invalid_transition":
            raise ValueError("unsupported universal error code")
        if (
            type(self.repair) is not RepairSubmissionV2
            or type(self.witness) is not WitnessSubmissionV2
        ):
            raise ValueError("solver output contains invalid normalized records")
        if len(canonical_bytes_v2(self.to_dict())) > MAX_SOLVER_OUTPUT_BYTES_V2:
            raise ValueError("solver output exceeds its aggregate byte budget")

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_code": self.error_code,
            "first_invalid_line": self.first_invalid_line,
            "repair": self.repair.to_dict(),
            "schema": self.schema,
            "schema_version": self.schema_version,
            "witness": self.witness.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> SolverOutputV2:
        value = _strict_keys(
            raw,
            {"error_code", "first_invalid_line", "repair", "schema", "schema_version", "witness"},
            "solver output",
        )
        return cls(
            first_invalid_line=_integer(
                value["first_invalid_line"], "first invalid line", minimum=2, maximum=11
            ),
            error_code=value["error_code"],
            repair=RepairSubmissionV2.from_dict(value["repair"]),
            witness=WitnessSubmissionV2.from_dict(value["witness"]),
            schema=value["schema"],
            schema_version=_integer(
                value["schema_version"], "answer schema version", minimum=0, maximum=100
            ),
        )

    @classmethod
    def from_json(cls, raw: str) -> SolverOutputV2:
        return cls.from_dict(strict_json_loads_v2(raw, max_bytes=MAX_SOLVER_OUTPUT_BYTES_V2))


@dataclass(frozen=True, slots=True)
class SemanticExpressionV2:
    kind: ExpressionKindV2
    value: NormalizedMathValueV2 | None = None
    name: str | None = None
    arguments: tuple[SemanticExpressionV2, ...] = ()

    def __post_init__(self) -> None:
        if type(self.kind) is not ExpressionKindV2:
            raise ValueError("semantic expression kind is invalid")
        if type(self.arguments) is not tuple:
            raise ValueError("semantic expression arguments must be a tuple")
        if self.kind is ExpressionKindV2.LITERAL:
            if (
                type(self.value) is not NormalizedMathValueV2
                or self.name is not None
                or self.arguments
            ):
                raise ValueError("literal expression fields are invalid")
            self.value.as_fraction()
            return
        if self.kind is ExpressionKindV2.STATE:
            if self.value is not None or self.arguments or self.name is None:
                raise ValueError("state expression fields are invalid")
            name = _text(self.name, "semantic state name", max_bytes=64)
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
                raise ValueError("semantic state name must be a simple identifier")
            return
        if self.value is not None or self.name is not None:
            raise ValueError("compound expression cannot carry an atom")
        expected = 2 if self.kind is ExpressionKindV2.SUBTRACT else range(2, 9)
        if isinstance(expected, int):
            valid_length = len(self.arguments) == expected
        else:
            valid_length = len(self.arguments) in expected
        if not valid_length or not all(
            type(argument) is SemanticExpressionV2 for argument in self.arguments
        ):
            raise ValueError("compound semantic expression has invalid arguments")

    @classmethod
    def literal(cls, value: int | Fraction) -> SemanticExpressionV2:
        return cls(ExpressionKindV2.LITERAL, value=NormalizedMathValueV2.scalar(value))

    @classmethod
    def state(cls, name: str) -> SemanticExpressionV2:
        return cls(ExpressionKindV2.STATE, name=name)

    @classmethod
    def operation(
        cls, kind: ExpressionKindV2, *arguments: SemanticExpressionV2
    ) -> SemanticExpressionV2:
        return cls(kind, arguments=tuple(arguments))

    def to_dict(self) -> dict[str, Any]:
        if self.kind is ExpressionKindV2.LITERAL:
            return {"kind": self.kind.value, "value": self.value.to_dict()}
        if self.kind is ExpressionKindV2.STATE:
            return {"kind": self.kind.value, "name": self.name}
        return {
            "arguments": [argument.to_dict() for argument in self.arguments],
            "kind": self.kind.value,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> SemanticExpressionV2:
        if type(raw) is not dict or not all(type(key) is str for key in raw):
            raise ValueError("semantic expression must be an object")
        try:
            kind = ExpressionKindV2(
                _text(raw.get("kind"), "semantic expression kind", max_bytes=64)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("semantic expression kind is invalid") from exc
        if kind is ExpressionKindV2.LITERAL:
            value = _strict_keys(raw, {"kind", "value"}, "literal expression")
            return cls(kind, value=NormalizedMathValueV2.from_dict(value["value"]))
        if kind is ExpressionKindV2.STATE:
            value = _strict_keys(raw, {"kind", "name"}, "state expression")
            return cls(kind, name=value["name"])
        value = _strict_keys(raw, {"arguments", "kind"}, "compound expression")
        arguments = value["arguments"]
        if type(arguments) is not list or len(arguments) > 8:
            raise ValueError("semantic expression arguments must be a bounded array")
        return cls(kind, arguments=tuple(cls.from_dict(item) for item in arguments))


@dataclass(frozen=True, slots=True)
class SemanticTransitionV2:
    transition_id: str
    kind: TransitionKindV2
    expression: SemanticExpressionV2
    surface_expression: SemanticExpressionV2
    surface_target: str
    display: str
    target: str | None = None
    declared: NormalizedMathValueV2 | None = None

    def __post_init__(self) -> None:
        transition_id = _text(self.transition_id, "transition id", max_bytes=32)
        if not re.fullmatch(r"s[0-9]{2}", transition_id):
            raise ValueError("transition id must use sNN form")
        if (
            type(self.kind) is not TransitionKindV2
            or type(self.expression) is not SemanticExpressionV2
        ):
            raise ValueError("semantic transition is invalid")
        if type(self.surface_expression) is not SemanticExpressionV2:
            raise ValueError("semantic surface expression is invalid")
        surface_target = _text(self.surface_target, "semantic surface target", max_bytes=16)
        if not re.fullmatch(r"v[0-9]{2}", surface_target):
            raise ValueError("semantic surface target must use vNN form")
        if self.declared is not None and type(self.declared) is not NormalizedMathValueV2:
            raise ValueError("transition declaration must be normalized")
        display = _text(self.display, "transition display", max_bytes=MAX_LINE_BYTES_V2 - 64)
        if display != _transition_display_v2(
            self.kind,
            self.surface_target,
            self.surface_expression,
            self.declared,
        ):
            raise ValueError("transition display is not the canonical rendering of its AST")
        if self.kind is TransitionKindV2.CHECK:
            if self.target is not None or self.declared is None:
                raise ValueError("check transition fields are invalid")
        elif self.kind is TransitionKindV2.ASSIGN_CHECKED:
            if self.target is None or self.declared is None:
                raise ValueError("checked assignment fields are invalid")
        elif self.kind is TransitionKindV2.DERIVE:
            if self.target is None or self.declared is not None:
                raise ValueError("derived transition fields are invalid")
        else:
            raise AssertionError("unreachable transition kind")
        if self.target is not None:
            target = _text(self.target, "transition target", max_bytes=64)
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", target):
                raise ValueError("transition target must be a simple identifier")
        if self.declared is not None:
            self.declared.as_fraction()

    def to_dict(self) -> dict[str, Any]:
        return {
            "declared": None if self.declared is None else self.declared.to_dict(),
            "display": self.display,
            "expression": self.expression.to_dict(),
            "kind": self.kind.value,
            "surface_expression": self.surface_expression.to_dict(),
            "surface_target": self.surface_target,
            "target": self.target,
            "transition_id": self.transition_id,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> SemanticTransitionV2:
        value = _strict_keys(
            raw,
            {
                "declared",
                "display",
                "expression",
                "kind",
                "surface_expression",
                "surface_target",
                "target",
                "transition_id",
            },
            "semantic transition",
        )
        try:
            kind = TransitionKindV2(_text(value["kind"], "semantic transition kind", max_bytes=64))
        except (TypeError, ValueError) as exc:
            raise ValueError("semantic transition kind is invalid") from exc
        declared = value["declared"]
        return cls(
            transition_id=value["transition_id"],
            kind=kind,
            expression=SemanticExpressionV2.from_dict(value["expression"]),
            surface_expression=SemanticExpressionV2.from_dict(value["surface_expression"]),
            surface_target=value["surface_target"],
            display=value["display"],
            target=value["target"],
            declared=None if declared is None else NormalizedMathValueV2.from_dict(declared),
        )


@dataclass(frozen=True, slots=True)
class SemanticProgramV2:
    initial_state: Mapping[str, NormalizedMathValueV2]
    surface_aliases: Mapping[str, str]
    prefix: tuple[SemanticTransitionV2, ...]
    clean_root: SemanticTransitionV2
    faulty_root: SemanticTransitionV2
    suffix: tuple[SemanticTransitionV2, ...]
    root_line: int
    schema: str = SEMANTIC_PROGRAM_SCHEMA_V2
    schema_version: int = SEMANTIC_PROGRAM_SCHEMA_VERSION_V2

    def __post_init__(self) -> None:
        schema = _text(self.schema, "semantic program schema", max_bytes=128)
        schema_version = _integer(
            self.schema_version,
            "semantic program schema version",
            minimum=0,
            maximum=100,
        )
        if (
            schema != SEMANTIC_PROGRAM_SCHEMA_V2
            or schema_version != SEMANTIC_PROGRAM_SCHEMA_VERSION_V2
        ):
            raise ValueError("unsupported semantic program schema")
        if type(self.initial_state) not in (dict, type(MappingProxyType({}))) or not (
            1 <= len(self.initial_state) <= 64
        ):
            raise ValueError("semantic initial state is invalid")
        state: dict[str, NormalizedMathValueV2] = {}
        for name in sorted(self.initial_state):
            if type(name) is not str or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
                raise ValueError("semantic state names must be simple identifiers")
            value = self.initial_state[name]
            if type(value) is not NormalizedMathValueV2:
                raise ValueError("semantic state values must be normalized")
            value.as_fraction()
            state[name] = value
        object.__setattr__(self, "initial_state", MappingProxyType(state))
        if type(self.surface_aliases) not in (dict, type(MappingProxyType({}))):
            raise ValueError("semantic surface aliases must be an exact mapping")
        aliases: dict[str, str] = {}
        for name in sorted(self.surface_aliases):
            if type(name) is not str or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
                raise ValueError("semantic surface alias keys must be identifiers")
            alias = _text(self.surface_aliases[name], "semantic surface alias", max_bytes=16)
            if not re.fullmatch(r"v[0-9]{2}", alias):
                raise ValueError("semantic surface aliases must use vNN form")
            aliases[name] = alias
        if not aliases or len(set(aliases.values())) != len(aliases):
            raise ValueError("semantic surface aliases must be a nonempty bijection")
        if set(aliases.values()) != {f"v{index:02d}" for index in range(1, len(aliases) + 1)}:
            raise ValueError("semantic surface aliases must form one canonical range")
        object.__setattr__(self, "surface_aliases", MappingProxyType(aliases))
        if type(self.prefix) is not tuple or type(self.suffix) is not tuple:
            raise ValueError("semantic transition sequences must be tuples")
        if (
            type(self.clean_root) is not SemanticTransitionV2
            or type(self.faulty_root) is not SemanticTransitionV2
        ):
            raise ValueError("semantic roots are invalid")
        transitions = (*self.prefix, self.clean_root, *self.suffix)
        if len(transitions) != MAX_TRACE_LINES_V2 or not all(
            type(item) is SemanticTransitionV2 for item in transitions
        ):
            raise ValueError("semantic program must contain exactly twelve typed transitions")
        root_line = _integer(self.root_line, "semantic root line", minimum=3, maximum=10)
        if root_line != len(self.prefix) + 1:
            raise ValueError("semantic root line does not match the independent position axis")
        clean_shape = (
            self.clean_root.transition_id,
            self.clean_root.kind,
            self.clean_root.expression.to_dict(),
            self.clean_root.target,
        )
        faulty_shape = (
            self.faulty_root.transition_id,
            self.faulty_root.kind,
            self.faulty_root.expression.to_dict(),
            self.faulty_root.target,
        )
        if (
            clean_shape != faulty_shape
            or self.clean_root.kind is not TransitionKindV2.ASSIGN_CHECKED
        ):
            raise ValueError("clean and faulty roots must declare the same typed transition")
        if self.clean_root.declared == self.faulty_root.declared:
            raise ValueError("clean and faulty root declarations must differ")
        identifiers = [item.transition_id for item in transitions]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("semantic transition ids must be unique")
        required_names = set(state)
        for transition in (*transitions, self.faulty_root):
            required_names.update(_expression_state_names_v2(transition.expression))
            if transition.target is not None:
                required_names.add(transition.target)
            if not _expression_state_names_v2(transition.surface_expression) <= set(
                aliases.values()
            ):
                raise ValueError("surface expression uses an unregistered public alias")
            semantic_target = (
                transition.expression.name
                if transition.kind is TransitionKindV2.CHECK
                else transition.target
            )
            if semantic_target is None or aliases.get(semantic_target) != (
                transition.surface_target
            ):
                raise ValueError("surface target is not bound to its semantic target")
        if not required_names <= set(aliases):
            raise ValueError("semantic program omits aliases for executable state")

        for transition in (*self.prefix, self.clean_root, *self.suffix):
            if transition.surface_expression != _alias_expression_v2(
                transition.expression, aliases
            ):
                raise ValueError("clean surface expression is not bound to executable math")
        if not _surface_arithmetic_valid_v2(self, faulty=False) or not _surface_arithmetic_valid_v2(
            self, faulty=True
        ):
            raise ValueError("displayed basis arithmetic does not support its declaration")

    def transitions(self, *, faulty: bool) -> tuple[SemanticTransitionV2, ...]:
        if type(faulty) is not bool:
            raise ValueError("faulty semantic selector must be boolean")
        return (*self.prefix, self.faulty_root if faulty else self.clean_root, *self.suffix)

    def render(self, *, template: int, faulty: bool) -> tuple[str, ...]:
        checked_template = _integer(template, "discourse template", minimum=0, maximum=3)
        rendered: list[str] = []
        for number, transition in enumerate(self.transitions(faulty=faulty), 1):
            # All candidates share one discourse scaffold.  Their target and basis
            # aliases remain visible; only the per-instance alias permutation changes.
            role = "verify" if transition.kind is TransitionKindV2.CHECK else "record"
            if checked_template == 0:
                body = f"Record {transition.transition_id} [{role}]: {transition.display}"
            elif checked_template == 1:
                body = f"Ledger {transition.transition_id} [{role}] :: {transition.display}"
            elif checked_template == 2:
                body = f"Using {transition.transition_id} [{role}], register {transition.display}"
            else:
                body = f"[{role}] {transition.display} [checkpoint]"
            rendered.append(f"{number}. {body}")
        return tuple(rendered)

    def to_dict(self) -> dict[str, Any]:
        return {
            "clean_root": self.clean_root.to_dict(),
            "faulty_root": self.faulty_root.to_dict(),
            "initial_state": {name: value.to_dict() for name, value in self.initial_state.items()},
            "prefix": [item.to_dict() for item in self.prefix],
            "root_line": self.root_line,
            "schema": self.schema,
            "schema_version": self.schema_version,
            "surface_aliases": dict(self.surface_aliases),
            "suffix": [item.to_dict() for item in self.suffix],
        }

    @classmethod
    def from_dict(cls, raw: Any) -> SemanticProgramV2:
        value = _strict_keys(
            raw,
            {
                "clean_root",
                "faulty_root",
                "initial_state",
                "prefix",
                "root_line",
                "schema",
                "schema_version",
                "surface_aliases",
                "suffix",
            },
            "semantic program",
        )
        initial = value["initial_state"]
        surface_aliases = value["surface_aliases"]
        prefix = value["prefix"]
        suffix = value["suffix"]
        if (
            type(initial) is not dict
            or type(surface_aliases) is not dict
            or type(prefix) is not list
            or type(suffix) is not list
        ):
            raise ValueError("semantic program arrays and state are invalid")
        if not all(type(name) is str for name in initial):
            raise ValueError("semantic initial-state keys must be exact strings")
        if len(prefix) > MAX_TRACE_LINES_V2 or len(suffix) > MAX_TRACE_LINES_V2:
            raise ValueError("semantic program transition budget exceeded")
        return cls(
            initial_state={
                name: NormalizedMathValueV2.from_dict(item) for name, item in initial.items()
            },
            surface_aliases=surface_aliases,
            prefix=tuple(SemanticTransitionV2.from_dict(item) for item in prefix),
            clean_root=SemanticTransitionV2.from_dict(value["clean_root"]),
            faulty_root=SemanticTransitionV2.from_dict(value["faulty_root"]),
            suffix=tuple(SemanticTransitionV2.from_dict(item) for item in suffix),
            root_line=_integer(value["root_line"], "semantic root line", minimum=3, maximum=10),
            schema=value["schema"],
            schema_version=_integer(
                value["schema_version"], "semantic schema version", minimum=0, maximum=100
            ),
        )


@dataclass(frozen=True, slots=True)
class SemanticExecutionV2:
    completed: bool
    root_valid: bool
    prefix_valid: bool
    suffix_valid: bool
    invalid_lines: tuple[int, ...]
    final_state_digest: str


def _evaluate_expression_v2(
    expression: SemanticExpressionV2, state: Mapping[str, Fraction], *, depth: int = 0
) -> Fraction:
    if depth > 8:
        raise ValueError("semantic expression depth budget exceeded")
    if expression.kind is ExpressionKindV2.LITERAL:
        return expression.value.as_fraction()
    if expression.kind is ExpressionKindV2.STATE:
        if expression.name not in state:
            raise ValueError("semantic expression references missing state")
        return state[expression.name]
    values = tuple(
        _evaluate_expression_v2(argument, state, depth=depth + 1)
        for argument in expression.arguments
    )
    if expression.kind is ExpressionKindV2.ADD:
        return sum(values, Fraction())
    if expression.kind is ExpressionKindV2.SUBTRACT:
        return values[0] - values[1]
    if expression.kind is ExpressionKindV2.MULTIPLY:
        result = Fraction(1)
        for value in values:
            result *= value
        if abs(result.numerator) > MAX_INTEGER_ABS_V2 or result.denominator > MAX_INTEGER_ABS_V2:
            raise ValueError("semantic expression result exceeds scalar budget")
        return result
    raise AssertionError("unreachable semantic expression kind")


def _surface_arithmetic_valid_v2(program: SemanticProgramV2, *, faulty: bool) -> bool:
    """Check every displayed basis using only public aliases and prior surface state."""

    try:
        state = {
            program.surface_aliases[name]: value.as_fraction()
            for name, value in program.initial_state.items()
        }
        for transition in program.transitions(faulty=faulty):
            result = _evaluate_expression_v2(transition.surface_expression, state)
            if transition.kind in (
                TransitionKindV2.CHECK,
                TransitionKindV2.ASSIGN_CHECKED,
            ):
                declared = transition.declared.as_fraction()
                if result != declared:
                    return False
                if transition.kind is TransitionKindV2.ASSIGN_CHECKED:
                    state[transition.surface_target] = declared
            else:
                state[transition.surface_target] = result
        return True
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return False


def execute_semantic_program_v2(program: SemanticProgramV2, *, faulty: bool) -> SemanticExecutionV2:
    """Execute every transition; an invalid root declaration is injected before the suffix."""

    if type(program) is not SemanticProgramV2 or type(faulty) is not bool:
        raise ValueError("semantic execution requires a typed program and boolean selector")
    state = {name: value.as_fraction() for name, value in program.initial_state.items()}
    invalid: list[int] = []
    prefix_valid = True
    suffix_valid = True
    root_valid = False
    completed = True
    try:
        for line, transition in enumerate(program.transitions(faulty=faulty), 1):
            result = _evaluate_expression_v2(transition.expression, state)
            valid = True
            if transition.kind is TransitionKindV2.CHECK:
                valid = result == transition.declared.as_fraction()
            elif transition.kind is TransitionKindV2.ASSIGN_CHECKED:
                valid = result == transition.declared.as_fraction()
                state[transition.target] = transition.declared.as_fraction()
            elif transition.kind is TransitionKindV2.DERIVE:
                state[transition.target] = result
            if not valid:
                invalid.append(line)
            if line < program.root_line:
                prefix_valid &= valid
            elif line == program.root_line:
                root_valid = valid
            else:
                suffix_valid &= valid
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        completed = False
        suffix_valid = False
    serialized_state = {name: _canonical_fraction(value) for name, value in sorted(state.items())}
    return SemanticExecutionV2(
        completed=completed,
        root_valid=root_valid,
        prefix_valid=prefix_valid,
        suffix_valid=suffix_valid,
        invalid_lines=tuple(invalid),
        final_state_digest=content_digest_v2(
            {"interpreter_schema": INTERPRETER_SCHEMA_V2, "state": serialized_state}
        ),
    )


def _state(name: str) -> SemanticExpressionV2:
    return SemanticExpressionV2.state(name)


def _literal(value: int | Fraction) -> SemanticExpressionV2:
    return SemanticExpressionV2.literal(value)


def _add(*arguments: SemanticExpressionV2) -> SemanticExpressionV2:
    return SemanticExpressionV2.operation(ExpressionKindV2.ADD, *arguments)


def _subtract(left: SemanticExpressionV2, right: SemanticExpressionV2) -> SemanticExpressionV2:
    return SemanticExpressionV2.operation(ExpressionKindV2.SUBTRACT, left, right)


def _multiply(*arguments: SemanticExpressionV2) -> SemanticExpressionV2:
    return SemanticExpressionV2.operation(ExpressionKindV2.MULTIPLY, *arguments)


def _expression_state_names_v2(expression: SemanticExpressionV2) -> set[str]:
    if expression.kind is ExpressionKindV2.STATE:
        return {expression.name}
    result: set[str] = set()
    for argument in expression.arguments:
        result.update(_expression_state_names_v2(argument))
    return result


def _alias_expression_v2(
    expression: SemanticExpressionV2, aliases: Mapping[str, str]
) -> SemanticExpressionV2:
    if expression.kind is ExpressionKindV2.LITERAL:
        return expression
    if expression.kind is ExpressionKindV2.STATE:
        try:
            return _state(aliases[expression.name])
        except KeyError as exc:
            raise ValueError("semantic expression has no public alias") from exc
    return SemanticExpressionV2.operation(
        expression.kind,
        *(_alias_expression_v2(argument, aliases) for argument in expression.arguments),
    )


def _render_math_value_v2(value: NormalizedMathValueV2) -> str:
    return _canonical_fraction(value.as_fraction())


def _render_surface_identifier_v2(name: str) -> str:
    value = _text(name, "public surface identifier", max_bytes=16)
    if not re.fullmatch(r"v[0-9]{2}", value):
        raise ValueError("public surface identifiers must use vNN form")
    return f"{value[0]}-{value[1:]}"


def _render_expression_v2(expression: SemanticExpressionV2) -> str:
    if expression.kind is ExpressionKindV2.LITERAL:
        return _render_math_value_v2(expression.value)
    if expression.kind is ExpressionKindV2.STATE:
        return _render_surface_identifier_v2(expression.name)
    rendered = tuple(_render_expression_v2(argument) for argument in expression.arguments)
    if expression.kind is ExpressionKindV2.ADD:
        return "(" + "+".join(rendered) + ")"
    if expression.kind is ExpressionKindV2.SUBTRACT:
        return f"({rendered[0]}-{rendered[1]})"
    if expression.kind is ExpressionKindV2.MULTIPLY:
        return "(" + "*".join(rendered) + ")"
    raise AssertionError("unreachable expression renderer")


def _transition_display_v2(
    kind: TransitionKindV2,
    surface_target: str,
    expression: SemanticExpressionV2,
    declared: NormalizedMathValueV2 | None,
) -> str:
    target = _render_surface_identifier_v2(surface_target)
    basis = _render_expression_v2(expression)
    if kind in (TransitionKindV2.CHECK, TransitionKindV2.ASSIGN_CHECKED):
        value = _render_math_value_v2(declared)
        return f"claim {target} = {value:>6}; basis {basis}."
    return f"claim {target} = {basis}; basis derived relation."


def _check_transition(
    transition_id: str,
    expression: SemanticExpressionV2,
    declared: int | Fraction,
    *,
    aliases: Mapping[str, str],
) -> SemanticTransitionV2:
    if expression.kind is not ExpressionKindV2.STATE:
        raise ValueError("public check targets require one named semantic state")
    normalized = NormalizedMathValueV2.scalar(declared)
    surface = _alias_expression_v2(expression, aliases)
    surface_target = aliases[expression.name]
    return SemanticTransitionV2(
        transition_id=transition_id,
        kind=TransitionKindV2.CHECK,
        expression=expression,
        surface_expression=surface,
        surface_target=surface_target,
        display=_transition_display_v2(TransitionKindV2.CHECK, surface_target, surface, normalized),
        declared=normalized,
    )


def _root_transition(
    transition_id: str,
    target: str,
    expression: SemanticExpressionV2,
    declared: int | Fraction,
    *,
    aliases: Mapping[str, str],
    surface_expression: SemanticExpressionV2 | None = None,
) -> SemanticTransitionV2:
    normalized = NormalizedMathValueV2.scalar(declared)
    semantic_surface = expression if surface_expression is None else surface_expression
    surface = _alias_expression_v2(semantic_surface, aliases)
    surface_target = aliases[target]
    return SemanticTransitionV2(
        transition_id=transition_id,
        kind=TransitionKindV2.ASSIGN_CHECKED,
        expression=expression,
        surface_expression=surface,
        surface_target=surface_target,
        display=_transition_display_v2(
            TransitionKindV2.ASSIGN_CHECKED,
            surface_target,
            surface,
            normalized,
        ),
        target=target,
        declared=normalized,
    )


def _checked_detail_transition(
    transition_id: str,
    target: str,
    expression: SemanticExpressionV2,
    declared: int | Fraction,
    *,
    aliases: Mapping[str, str],
) -> SemanticTransitionV2:
    """Give every eligible foil the root's checked-assignment surface shape."""

    return _root_transition(
        transition_id,
        target,
        expression,
        declared,
        aliases=aliases,
    )


def _derive_transition(
    transition_id: str,
    target: str,
    expression: SemanticExpressionV2,
    *,
    aliases: Mapping[str, str],
) -> SemanticTransitionV2:
    surface = _alias_expression_v2(expression, aliases)
    surface_target = aliases[target]
    return SemanticTransitionV2(
        transition_id=transition_id,
        kind=TransitionKindV2.DERIVE,
        expression=expression,
        surface_expression=surface,
        surface_target=surface_target,
        display=_transition_display_v2(TransitionKindV2.DERIVE, surface_target, surface, None),
        target=target,
    )


_SOLVER_OUTPUT_CONTRACT_V2 = (
    "Return one JSON object with exactly these keys: "
    '{"schema":"org.math-research-brain/hard-challenge-answer",'
    '"schema_version":2,"first_invalid_line":N,"error_code":"invalid_transition",'
    '"repair":{"target":"value","value":VALUE},'
    '"witness":{"code":CODE,"values":[VALUE,...]}}. '
    'Every VALUE is {"kind":KIND,"payload":PAYLOAD}. The four allowed KIND forms are '
    "integer/{value:N}, rational/{numerator:P,denominator:Q}, "
    "integer_vector/{items:[N,...]}, and symbol_vector/{items:[NAME,...]}. "
    "All cases use the same witness CODE list: solution_pair, feasible_assignment, "
    "directed_path, polynomial_identity. The witness values arrays are exactly: "
    "solution_pair=[integer_vector(first solution,next solution)]; "
    "feasible_assignment=[integer_vector(joint shifted-violation assignment),"
    "integer(correct bounded count)]; "
    "directed_path=[symbol_vector(path vertices),integer(path length)]; "
    "polynomial_identity=[rational(particular coefficient),"
    "integer_vector(second-difference coefficients),"
    "integer_vector(scaled residual coefficients),integer(faulty residual)]. "
    'The repair target is always the literal string "value". Use reduced rationals and '
    "no extra keys."
)


def _validate_witness_payload(kind: WitnessKindV2, payload: Mapping[str, Any]) -> None:
    value = _thaw_json(payload)
    if kind is WitnessKindV2.CRT_PERIOD_COUNTEREXAMPLE:
        _strict_keys(
            value,
            {"claimed_period", "first_solution", "second_solution", "true_period"},
            "CRT witness payload",
        )
        for key, item in value.items():
            _integer(item, f"CRT witness {key}", minimum=0, maximum=MAX_INTEGER_ABS_V2)
        return
    if kind is WitnessKindV2.COMPOSITION_OVERLAP_ASSIGNMENT:
        _strict_keys(
            value,
            {
                "correct_count",
                "faulty_count",
                "faulty_overlap",
                "overlap_assignment",
                "true_overlap",
            },
            "composition witness payload",
        )
        assignment = value["overlap_assignment"]
        if not isinstance(assignment, list) or len(assignment) != 3:
            raise ValueError("composition witness assignment must have three coordinates")
        for item in assignment:
            _integer(item, "composition witness coordinate", minimum=0, maximum=10_000)
        for key in ("correct_count", "faulty_count", "faulty_overlap", "true_overlap"):
            _integer(value[key], f"composition witness {key}", minimum=0, maximum=10**7)
        return
    if kind is WitnessKindV2.NEGATIVE_EDGE_PATH:
        _strict_keys(
            value,
            {"edge_weights", "faulty_distance", "path", "path_length"},
            "shortest-path witness payload",
        )
        if value["path"] != ["s", "b", "a", "t"]:
            raise ValueError("shortest-path witness must use the registered path")
        weights = value["edge_weights"]
        if not isinstance(weights, list) or len(weights) != 3:
            raise ValueError("shortest-path witness must contain three edge weights")
        for item in weights:
            _integer(item, "shortest-path witness edge", minimum=-10_000, maximum=10_000)
        for key in ("faulty_distance", "path_length"):
            _integer(value[key], f"shortest-path witness {key}", minimum=-(10**6), maximum=10**6)
        return
    if kind is WitnessKindV2.RESONANCE_RESIDUAL:
        _strict_keys(
            value,
            {"faulty_residual", "index", "particular_coefficient", "required_residual"},
            "recurrence witness payload",
        )
        _integer(value["index"], "recurrence witness index", minimum=0, maximum=64)
        _parse_fraction(value["faulty_residual"], "faulty recurrence residual")
        _parse_fraction(value["particular_coefficient"], "particular coefficient")
        _integer(
            value["required_residual"],
            "required recurrence residual",
            minimum=-MAX_INTEGER_ABS_V2,
            maximum=MAX_INTEGER_ABS_V2,
        )
        return
    raise AssertionError("unreachable witness kind")


@dataclass(frozen=True, slots=True)
class DiagnosticWitnessV2:
    kind: WitnessKindV2
    payload: Mapping[str, Any]
    schema: str = WITNESS_SCHEMA_V2
    schema_version: int = WITNESS_SCHEMA_VERSION_V2

    def __post_init__(self) -> None:
        if type(self.kind) is not WitnessKindV2:
            raise ValueError("witness kind is not registered")
        schema = _text(self.schema, "witness schema", max_bytes=128)
        schema_version = _integer(
            self.schema_version, "witness schema version", minimum=0, maximum=100
        )
        if schema != WITNESS_SCHEMA_V2 or schema_version != WITNESS_SCHEMA_VERSION_V2:
            raise ValueError("unsupported hard-challenge witness schema")
        frozen = _freeze_json(self.payload)
        if not isinstance(frozen, Mapping):
            raise ValueError("witness payload must be an object")
        object.__setattr__(self, "payload", frozen)
        _validate_witness_payload(self.kind, frozen)
        if len(canonical_bytes_v2(self.to_dict())) > MAX_WITNESS_JSON_BYTES_V2:
            raise ValueError("witness exceeds its aggregate byte budget")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "payload": _thaw_json(self.payload),
            "schema": self.schema,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> DiagnosticWitnessV2:
        value = _strict_keys(
            raw,
            {"kind", "payload", "schema", "schema_version"},
            "hard-challenge witness",
        )
        try:
            kind = WitnessKindV2(_text(value["kind"], "witness kind", max_bytes=64))
        except (TypeError, ValueError) as exc:
            raise ValueError("witness kind is not registered") from exc
        return cls(
            kind=kind,
            payload=value["payload"],
            schema=value["schema"],
            schema_version=_integer(
                value["schema_version"],
                "witness schema version",
                minimum=0,
                maximum=100,
            ),
        )


@dataclass(frozen=True, slots=True)
class ReplayResultV2:
    accepted: bool
    first_invalid_line: int | None
    exact_repair: str | None


@dataclass(frozen=True, slots=True)
class CertificationReportV2:
    structural_passed: bool
    deterministic_binding_passed: bool
    artifact_binding_passed: bool
    primary_verifier_passed: bool
    independent_verifier_passed: bool
    witness_passed: bool
    root_fault_rejected: bool
    semantic_clean_passed: bool
    semantic_faulty_root_rejected: bool
    semantic_clean_suffix_passed: bool
    semantic_faulty_suffix_passed: bool
    evidence_digest: str

    @property
    def passed(self) -> bool:
        return all(
            (
                self.structural_passed,
                self.deterministic_binding_passed,
                self.artifact_binding_passed,
                self.primary_verifier_passed,
                self.independent_verifier_passed,
                self.witness_passed,
                self.root_fault_rejected,
                self.semantic_clean_passed,
                self.semantic_faulty_root_rejected,
                self.semantic_clean_suffix_passed,
                self.semantic_faulty_suffix_passed,
            )
        )


@dataclass(frozen=True, slots=True)
class HardChallengeCaseV2:
    case_id: str
    family: FamilyV2
    seed: int
    parameters: Mapping[str, Any]
    semantic_program: SemanticProgramV2
    expected_repair: RepairSubmissionV2
    expected_witness: WitnessSubmissionV2
    prompt: str
    clean_trace: tuple[str, ...]
    faulty_line: int
    faulty_replacement: str
    repair_replacement: str
    foil_lines: tuple[int, ...]
    witness: DiagnosticWitnessV2
    primary_verifier: str
    independent_verifier: str
    verifier_source_digest: str
    verifier_artifact_digest: str
    primary_oracle_digest: str
    independent_oracle_digest: str
    witness_oracle_digest: str
    interpreter_schema: str = INTERPRETER_SCHEMA_V2
    generator_version: str = GENERATOR_VERSION_V2
    schema: str = CASE_SCHEMA_V2
    schema_version: int = CASE_SCHEMA_VERSION_V2

    def __post_init__(self) -> None:
        if type(self.family) is not FamilyV2:
            raise ValueError("challenge family is not registered")
        _integer(self.seed, "case seed", minimum=0, maximum=MAX_SEED_V2)
        expected_id = f"DV2-{_FAMILY_CODES[self.family]}-{self.seed:08x}"
        case_id = _text(self.case_id, "case id", max_bytes=64)
        if case_id != expected_id:
            raise ValueError("case id is not bound to family and seed")
        interpreter_schema = _text(self.interpreter_schema, "interpreter schema", max_bytes=128)
        if interpreter_schema != INTERPRETER_SCHEMA_V2:
            raise ValueError("case interpreter schema is not supported")
        frozen = _freeze_json(self.parameters)
        if not isinstance(frozen, Mapping):
            raise ValueError("case parameters must be an object")
        object.__setattr__(self, "parameters", frozen)
        if len(canonical_bytes_v2(_thaw_json(frozen))) > MAX_PARAMETER_JSON_BYTES_V2:
            raise ValueError("case parameters exceed their aggregate byte budget")
        prompt = _text(self.prompt, "case prompt", max_bytes=MAX_PROMPT_BYTES_V2)
        if not prompt.endswith(_SOLVER_OUTPUT_CONTRACT_V2):
            raise ValueError("case prompt must expose the universal solver-output contract")
        if type(self.semantic_program) is not SemanticProgramV2:
            raise ValueError("case semantic program has the wrong type")
        expected_aliases = _surface_aliases_v2(self.family, self.instance)
        if dict(self.semantic_program.surface_aliases) != expected_aliases:
            raise ValueError("surface aliases are not bound independently to family and instance")
        if _surface_legend_v2(self.family, expected_aliases) not in prompt:
            raise ValueError("case prompt does not expose the complete public alias legend")
        if (
            type(self.expected_repair) is not RepairSubmissionV2
            or type(self.expected_witness) is not WitnessSubmissionV2
        ):
            raise ValueError("case normalized answer records have the wrong type")
        if type(self.clean_trace) is not tuple or len(self.clean_trace) != MAX_TRACE_LINES_V2:
            raise ValueError("clean trace must contain exactly twelve lines")
        for number, line in enumerate(self.clean_trace, 1):
            text = _text(line, "trace line", max_bytes=MAX_LINE_BYTES_V2)
            if not text.startswith(f"{number}. "):
                raise ValueError("trace lines must be consecutively numbered")
        faulty_line = _integer(self.faulty_line, "fault line", minimum=3, maximum=10)
        if faulty_line != 3 + self.position or self.semantic_program.root_line != self.faulty_line:
            raise ValueError("fault line must be bound only to the independent position axis")
        rendered_clean = self.semantic_program.render(template=self.template, faulty=False)
        rendered_faulty = self.semantic_program.render(template=self.template, faulty=True)
        if self.clean_trace != rendered_clean:
            raise ValueError("clean trace is not the rendering of its typed semantic program")
        if self.repair_replacement != self.clean_trace[self.faulty_line - 1]:
            raise ValueError("repair must restore exactly one clean trace line")
        if self.faulty_replacement != rendered_faulty[self.faulty_line - 1]:
            raise ValueError("faulty replacement is not the rendering of its typed root")
        prefix = f"{self.faulty_line}. "
        for label, line in (
            ("faulty replacement", self.faulty_replacement),
            ("repair replacement", self.repair_replacement),
        ):
            if not _text(line, label, max_bytes=MAX_LINE_BYTES_V2).startswith(prefix):
                raise ValueError(f"{label} must remain bound to the fault line")
        if (
            type(self.foil_lines) is not tuple
            or len(self.foil_lines) < 3
            or any(type(line) is not int for line in self.foil_lines)
            or tuple(sorted(set(self.foil_lines))) != self.foil_lines
            or self.faulty_line in self.foil_lines
            or any(not 1 <= line <= len(self.clean_trace) for line in self.foil_lines)
        ):
            raise ValueError(
                "foil lines must be sorted, unique, in range, and distinct from the fault"
            )
        if type(self.witness) is not DiagnosticWitnessV2:
            raise ValueError("case witness has the wrong type")
        if self.witness.kind is not _FAMILY_WITNESSES[self.family]:
            raise ValueError("case family and witness kind disagree")
        expected_verifiers = _VERIFIER_NAMES[self.family]
        _text(self.primary_verifier, "primary verifier", max_bytes=128)
        _text(self.independent_verifier, "independent verifier", max_bytes=128)
        if (self.primary_verifier, self.independent_verifier) != expected_verifiers:
            raise ValueError("case verifier names do not match the family")
        for value, label in (
            (self.verifier_source_digest, "verifier source digest"),
            (self.verifier_artifact_digest, "verifier artifact digest"),
            (self.primary_oracle_digest, "primary oracle digest"),
            (self.independent_oracle_digest, "independent oracle digest"),
            (self.witness_oracle_digest, "witness oracle digest"),
        ):
            digest = _text(value, label, max_bytes=71)
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise ValueError(f"{label} must be a lowercase SHA-256 digest")
        if (
            self.verifier_source_digest != PINNED_VERIFIER_SOURCE_DIGEST_V2
            or self.verifier_artifact_digest != PINNED_VERIFIER_ARTIFACT_DIGEST_V2
        ):
            raise ValueError("case verifier binding does not match the pinned loaded artifact")
        expected_oracles = PINNED_FAMILY_ORACLE_DIGESTS_V2[self.family]
        if (
            self.primary_oracle_digest,
            self.independent_oracle_digest,
            self.witness_oracle_digest,
        ) != expected_oracles:
            raise ValueError("case oracle digests do not match the family implementation")
        root = self.semantic_program.clean_root
        if self.expected_repair != RepairSubmissionV2("value", root.declared):
            raise ValueError("normalized repair is not bound to the clean root declaration")
        if self.expected_witness.code is not _UNIVERSAL_WITNESS_CODES[self.family]:
            raise ValueError("normalized witness code is not bound to the case family")
        generator_version = _text(self.generator_version, "generator version", max_bytes=128)
        schema = _text(self.schema, "case schema", max_bytes=128)
        schema_version = _integer(
            self.schema_version, "case schema version", minimum=0, maximum=100
        )
        if generator_version != GENERATOR_VERSION_V2:
            raise ValueError("unsupported hard-challenge generator version")
        if schema != CASE_SCHEMA_V2 or schema_version != CASE_SCHEMA_VERSION_V2:
            raise ValueError("unsupported hard-challenge case schema")
        if len(canonical_bytes_v2(self.to_dict())) > MAX_CASE_JSON_BYTES_V2:
            raise ValueError("case exceeds its aggregate byte budget")

    def trace(self, *, faulty: bool) -> tuple[str, ...]:
        if type(faulty) is not bool:
            raise ValueError("faulty selector must be a boolean")
        if not faulty:
            return self.clean_trace
        lines = list(self.clean_trace)
        lines[self.faulty_line - 1] = self.faulty_replacement
        return tuple(lines)

    def solver_view(self, *, faulty: bool) -> SolverView:
        return SolverView(prompt=self.prompt, numbered_solution=self.trace(faulty=faulty))

    def solver_view_digest(self, *, faulty: bool) -> str:
        """Digest the exact shared solver payload, including the selected trace."""

        return content_digest_v2(self.solver_view(faulty=faulty).to_dict())

    @property
    def clean_solver_view(self) -> SolverView:
        return self.solver_view(faulty=False)

    @property
    def faulty_solver_view(self) -> SolverView:
        return self.solver_view(faulty=True)

    @property
    def domain(self) -> str:
        return _DOMAIN_NAMES[self.family]

    @property
    def derivation_family(self) -> str:
        return DERIVATION_FAMILY_ORDER_V2[self.derivation_family_index].value

    @property
    def calibration_family(self) -> str:
        """Cross-domain family used by the calibration protocol."""

        return self.derivation_family

    @property
    def difficulty(self) -> str:
        return "hard-v2"

    @property
    def mechanism(self) -> str:
        return FAULT_MECHANISM_ORDER_V2[self.fault_variant].value

    @property
    def fault_variant(self) -> int:
        return (self.seed // 128) % len(FAULT_MECHANISM_ORDER_V2)

    @property
    def repair_description(self) -> str:
        return _REPAIR_DESCRIPTIONS[FAULT_MECHANISM_ORDER_V2[self.fault_variant]]

    @property
    def template_id(self) -> str:
        return f"public-hard-v2/discourse-{self.template}"

    @property
    def generator_cell(self) -> str:
        """Answer-free parameter constructor, independent of layout and fault."""

        return f"public-hard-v2/parameter-{self.parameter_construction}"

    @property
    def position(self) -> int:
        return self.seed % 8

    @property
    def template(self) -> int:
        return (self.seed // 8) % 4

    @property
    def derivation_family_index(self) -> int:
        return (self.seed // 32) % len(DERIVATION_FAMILY_ORDER_V2)

    @property
    def parameter_cell(self) -> int:
        return (self.seed // 256) % len(PARAMETER_CELL_ORDER_V2)

    @property
    def parameter_construction(self) -> str:
        return PARAMETER_CELL_ORDER_V2[self.parameter_cell].value

    @property
    def instance(self) -> int:
        return self.seed // 512

    @property
    def relevance_keys(self) -> tuple[str, ...]:
        return _RELEVANCE_KEYS[self.family]

    @property
    def root_line(self) -> int:
        return self.faulty_line

    @property
    def root_localization(self) -> int:
        return self.faulty_line

    @property
    def error_family(self) -> str:
        return self.family.value

    @property
    def family_truth(self) -> str:
        return _FAMILY_TRUTHS[self.family]

    @property
    def repair_verifier_digest(self) -> str:
        return content_digest_v2(
            {
                "artifact_digest": self.verifier_artifact_digest,
                "case_digest": self.digest(),
                "independent_oracle_digest": self.independent_oracle_digest,
                "interpreter_schema": self.interpreter_schema,
                "kind": "bound-repair-verifier-v2",
                "normalized_repair_digest": content_digest_v2(self.expected_repair.to_dict()),
                "primary_oracle_digest": self.primary_oracle_digest,
                "source_digest": self.verifier_source_digest,
            }
        )

    @property
    def witness_verifier_digest(self) -> str:
        return content_digest_v2(
            {
                "artifact_digest": self.verifier_artifact_digest,
                "case_digest": self.digest(),
                "interpreter_schema": self.interpreter_schema,
                "kind": "bound-witness-verifier-v2",
                "normalized_witness_digest": content_digest_v2(self.expected_witness.to_dict()),
                "source_digest": self.verifier_source_digest,
                "witness_oracle_digest": self.witness_oracle_digest,
            }
        )

    def replay(self, trace: Sequence[str]) -> ReplayResultV2:
        if type(trace) not in (list, tuple):
            return ReplayResultV2(False, 1, self.repair_replacement)
        if len(trace) != len(self.clean_trace):
            return ReplayResultV2(False, 1, self.repair_replacement)
        candidate: list[str] = []
        try:
            for line in trace:
                candidate.append(_text(line, "replay line", max_bytes=MAX_LINE_BYTES_V2))
        except (TypeError, ValueError, UnicodeEncodeError):
            return ReplayResultV2(False, 1, self.repair_replacement)
        for number, (actual, expected) in enumerate(
            zip(candidate, self.clean_trace, strict=True), 1
        ):
            if actual != expected:
                repair = self.repair_replacement if number == self.faulty_line else expected
                return ReplayResultV2(False, number, repair)
        return ReplayResultV2(True, None, None)

    def verify_exact_repair(self, line: str) -> tuple[bool, str]:
        try:
            checked = _text(line, "legacy repair line", max_bytes=MAX_LINE_BYTES_V2)
            passed = hmac.compare_digest(
                checked.encode("utf-8"), self.repair_replacement.encode("utf-8")
            )
            proposal_digest = content_digest_v2({"legacy_repair": checked})
        except (TypeError, ValueError, UnicodeEncodeError):
            passed = False
            proposal_digest = content_digest_v2({"rejected_type": _rejected_type_v2(line)})
        evidence = content_digest_v2(
            {
                "artifact_digest": self.verifier_artifact_digest,
                "case_digest": self.digest(),
                "interpreter_schema": self.interpreter_schema,
                "kind": "legacy-exact-one-line-repair-v2",
                "passed": passed,
                "proposal_digest": proposal_digest,
                "source_digest": self.verifier_source_digest,
            }
        )
        return passed, evidence

    def verify_repair(self, proposal: Any) -> tuple[bool, str]:
        """Verify a universal normalized repair, never generator prose."""

        try:
            if type(proposal) is RepairSubmissionV2:
                candidate = proposal
            elif type(proposal) is dict:
                candidate = RepairSubmissionV2.from_dict(proposal)
            else:
                raise ValueError("repair proposal must be a normalized object")
            proposal_digest = content_digest_v2(candidate.to_dict())
            normalized = hmac.compare_digest(
                canonical_bytes_v2(candidate.to_dict()),
                canonical_bytes_v2(self.expected_repair.to_dict()),
            )
        except Exception:
            candidate = None
            normalized = False
            proposal_digest = content_digest_v2({"rejected_type": _rejected_type_v2(proposal)})
        artifact_ok, observed = _verifier_binding_status_v2(self)
        primary = artifact_ok and _family_primary_verification(self)
        independent = artifact_ok and _family_independent_verification(self)
        semantic = execute_semantic_program_v2(self.semantic_program, faulty=False)
        passed = (
            normalized and primary and independent and semantic.root_valid and semantic.suffix_valid
        )
        evidence = content_digest_v2(
            {
                "artifact_binding_passed": artifact_ok,
                "case_digest": self.digest(),
                "expected_artifact_digest": self.verifier_artifact_digest,
                "independent_recomputed": independent,
                "interpreter_schema": self.interpreter_schema,
                "kind": "hard-challenge-repair-recomputation-v2",
                "normalized_repair_passed": normalized,
                "observed_artifact_digest": observed[1],
                "observed_source_digest": observed[0],
                "passed": passed,
                "primary_recomputed": primary,
                "proposal_digest": proposal_digest,
                "source_digest": self.verifier_source_digest,
            }
        )
        return passed, evidence

    def verify_witness(self, proposal: Any) -> tuple[bool, str]:
        """Normalize a typed witness proposal and verify it from case parameters."""

        try:
            if type(proposal) is WitnessSubmissionV2:
                candidate = proposal
            elif type(proposal) is dict:
                candidate = WitnessSubmissionV2.from_dict(proposal)
            else:
                raise ValueError("witness proposal must be a normalized object")
            proposal_digest = content_digest_v2(candidate.to_dict())
        except Exception:
            candidate = None
            proposal_digest = content_digest_v2({"rejected_type": _rejected_type_v2(proposal)})
        artifact_ok, observed = _verifier_binding_status_v2(self)
        passed = bool(
            candidate is not None and artifact_ok and _verify_universal_witness_v2(self, candidate)
        )
        evidence = content_digest_v2(
            {
                "artifact_binding_passed": artifact_ok,
                "case_digest": self.digest(),
                "expected_artifact_digest": self.verifier_artifact_digest,
                "interpreter_schema": self.interpreter_schema,
                "kind": "hard-challenge-witness-recomputation-v2",
                "observed_artifact_digest": observed[1],
                "observed_source_digest": observed[0],
                "passed": passed,
                "proposal_digest": proposal_digest,
                "source_digest": self.verifier_source_digest,
                "witness_oracle_digest": self.witness_oracle_digest,
            }
        )
        return passed, evidence

    @property
    def expected_solver_output(self) -> SolverOutputV2:
        return SolverOutputV2(
            first_invalid_line=self.faulty_line,
            error_code="invalid_transition",
            repair=self.expected_repair,
            witness=self.expected_witness,
        )

    def verify_solver_output(self, proposal: Any) -> tuple[bool, str]:
        """Fail closed on one bounded universal solver-output object or JSON string."""

        try:
            if type(proposal) is SolverOutputV2:
                candidate = proposal
            elif type(proposal) is dict:
                candidate = SolverOutputV2.from_dict(proposal)
            elif type(proposal) is str:
                candidate = SolverOutputV2.from_json(proposal)
            else:
                raise ValueError("solver output must be an object or bounded JSON text")
            proposal_digest = content_digest_v2(candidate.to_dict())
            repair_passed = self.verify_repair(candidate.repair)[0]
            witness_passed = self.verify_witness(candidate.witness)[0]
            passed = (
                candidate.first_invalid_line == self.faulty_line
                and candidate.error_code == "invalid_transition"
                and repair_passed
                and witness_passed
            )
        except Exception:
            passed = False
            proposal_digest = content_digest_v2({"rejected_type": _rejected_type_v2(proposal)})
        artifact_ok, observed = _verifier_binding_status_v2(self)
        passed = passed and artifact_ok
        evidence = content_digest_v2(
            {
                "artifact_binding_passed": artifact_ok,
                "artifact_digest": self.verifier_artifact_digest,
                "case_digest": self.digest(),
                "interpreter_schema": self.interpreter_schema,
                "kind": "universal-solver-output-verification-v2",
                "observed_artifact_digest": observed[1],
                "observed_source_digest": observed[0],
                "passed": passed,
                "proposal_digest": proposal_digest,
                "source_digest": self.verifier_source_digest,
            }
        )
        return passed, evidence

    def content_payload(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "clean_trace": list(self.clean_trace),
            "derivation_family": self.derivation_family,
            "domain": self.domain,
            "family": self.family.value,
            "fault_variant": self.fault_variant,
            "faulty_line": self.faulty_line,
            "faulty_replacement": self.faulty_replacement,
            "foil_lines": list(self.foil_lines),
            "generator_cell": self.generator_cell,
            "generator_version": self.generator_version,
            "independent_verifier": self.independent_verifier,
            "independent_oracle_digest": self.independent_oracle_digest,
            "interpreter_schema": self.interpreter_schema,
            "mechanism": self.mechanism,
            "parameter_cell": self.parameter_cell,
            "parameter_construction": self.parameter_construction,
            "parameters": _thaw_json(self.parameters),
            "primary_verifier": self.primary_verifier,
            "primary_oracle_digest": self.primary_oracle_digest,
            "prompt": self.prompt,
            "repair_description": self.repair_description,
            "repair_replacement": self.repair_replacement,
            "expected_repair": self.expected_repair.to_dict(),
            "expected_witness": self.expected_witness.to_dict(),
            "schema": self.schema,
            "schema_version": self.schema_version,
            "seed": self.seed,
            "semantic_program": self.semantic_program.to_dict(),
            "verifier_artifact_digest": self.verifier_artifact_digest,
            "verifier_source_digest": self.verifier_source_digest,
            "witness": self.witness.to_dict(),
            "witness_oracle_digest": self.witness_oracle_digest,
        }

    def digest(self) -> str:
        return content_digest_v2(self.content_payload())

    def to_dict(self) -> dict[str, Any]:
        value = self.content_payload()
        value["content_digest"] = self.digest()
        return value

    def canonical_json(self) -> str:
        return canonical_json_v2(self.to_dict())

    def certify(self) -> bool:
        return certify_case_v2(self)

    @classmethod
    def from_dict(cls, raw: Any) -> HardChallengeCaseV2:
        value = _strict_keys(
            raw,
            {
                "case_id",
                "clean_trace",
                "content_digest",
                "derivation_family",
                "domain",
                "family",
                "fault_variant",
                "faulty_line",
                "faulty_replacement",
                "foil_lines",
                "generator_cell",
                "generator_version",
                "independent_verifier",
                "independent_oracle_digest",
                "interpreter_schema",
                "mechanism",
                "parameter_cell",
                "parameter_construction",
                "parameters",
                "primary_verifier",
                "primary_oracle_digest",
                "prompt",
                "repair_description",
                "repair_replacement",
                "expected_repair",
                "expected_witness",
                "schema",
                "schema_version",
                "seed",
                "semantic_program",
                "verifier_artifact_digest",
                "verifier_source_digest",
                "witness",
                "witness_oracle_digest",
            },
            "hard-challenge case",
        )
        try:
            family = FamilyV2(_text(value["family"], "challenge family", max_bytes=64))
        except (TypeError, ValueError) as exc:
            raise ValueError("challenge family is not registered") from exc
        clean_trace = value["clean_trace"]
        foil_lines = value["foil_lines"]
        if type(clean_trace) is not list or not all(type(line) is str for line in clean_trace):
            raise ValueError("clean trace must be an array of strings")
        if type(foil_lines) is not list:
            raise ValueError("foil lines must be an array")
        case = cls(
            case_id=value["case_id"],
            family=family,
            seed=_integer(value["seed"], "case seed", minimum=0, maximum=MAX_SEED_V2),
            parameters=value["parameters"],
            semantic_program=SemanticProgramV2.from_dict(value["semantic_program"]),
            expected_repair=RepairSubmissionV2.from_dict(value["expected_repair"]),
            expected_witness=WitnessSubmissionV2.from_dict(value["expected_witness"]),
            prompt=value["prompt"],
            clean_trace=tuple(clean_trace),
            faulty_line=_integer(value["faulty_line"], "fault line", minimum=0, maximum=64),
            faulty_replacement=value["faulty_replacement"],
            repair_replacement=value["repair_replacement"],
            foil_lines=tuple(
                _integer(item, "foil line", minimum=1, maximum=64) for item in foil_lines
            ),
            witness=DiagnosticWitnessV2.from_dict(value["witness"]),
            primary_verifier=value["primary_verifier"],
            independent_verifier=value["independent_verifier"],
            verifier_source_digest=value["verifier_source_digest"],
            verifier_artifact_digest=value["verifier_artifact_digest"],
            primary_oracle_digest=value["primary_oracle_digest"],
            independent_oracle_digest=value["independent_oracle_digest"],
            witness_oracle_digest=value["witness_oracle_digest"],
            interpreter_schema=value["interpreter_schema"],
            generator_version=value["generator_version"],
            schema=value["schema"],
            schema_version=_integer(
                value["schema_version"], "case schema version", minimum=0, maximum=100
            ),
        )
        domain = _text(value["domain"], "challenge domain", max_bytes=128)
        derivation_family = _text(value["derivation_family"], "derivation family", max_bytes=64)
        mechanism = _text(value["mechanism"], "fault mechanism", max_bytes=64)
        parameter_construction = _text(
            value["parameter_construction"], "parameter construction", max_bytes=64
        )
        generator_cell = _text(value["generator_cell"], "generator cell", max_bytes=128)
        repair_description = _text(value["repair_description"], "repair description", max_bytes=256)
        if domain != case.domain:
            raise ValueError("serialized challenge domain is not bound to its family")
        if derivation_family != case.derivation_family:
            raise ValueError("serialized derivation family is not bound to its seed axis")
        if _integer(value["fault_variant"], "fault variant", minimum=0, maximum=1) != (
            case.fault_variant
        ):
            raise ValueError("serialized fault variant is not bound to its seed axis")
        if mechanism != case.mechanism:
            raise ValueError("serialized fault mechanism is not bound to its variant")
        if _integer(value["parameter_cell"], "parameter cell", minimum=0, maximum=1) != (
            case.parameter_cell
        ):
            raise ValueError("serialized parameter cell is not bound to its seed axis")
        if parameter_construction != case.parameter_construction:
            raise ValueError("serialized parameter construction is not bound to its cell")
        if generator_cell != case.generator_cell:
            raise ValueError("serialized generator cell is not bound to its parameter cell")
        if repair_description != case.repair_description:
            raise ValueError("serialized repair description is not bound to its variant")
        supplied_digest = value["content_digest"]
        if type(supplied_digest) is not str or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", supplied_digest
        ):
            raise ValueError("case content digest must be a lowercase SHA-256 digest")
        if not hmac.compare_digest(supplied_digest, case.digest()):
            raise ValueError("hard-challenge case content digest mismatch")
        expected = generate_development_case_v2(family, case.seed)
        if case.content_payload() != expected.content_payload():
            raise ValueError("case does not match its deterministic public generator")
        if not case.certify():
            raise ValueError("case failed mathematical certification")
        return case

    @classmethod
    def from_json(cls, raw: str) -> HardChallengeCaseV2:
        case = cls.from_dict(strict_json_loads_v2(raw))
        if raw != case.canonical_json():
            raise ValueError("hard-challenge JSON must use the exact canonical encoding")
        return case


@dataclass(frozen=True, slots=True)
class _RelationalClaimV2:
    target: str
    correct_expression: SemanticExpressionV2
    correct_value: int | Fraction
    faulty_expression: SemanticExpressionV2
    faulty_value: int | Fraction


def _surface_aliases_v2(family: FamilyV2, instance: int) -> dict[str, str]:
    """Permute aliases by domain and numeric instance, never by another axis."""

    descriptions = _SURFACE_DESCRIPTIONS_V2[family]
    ranked = sorted(
        descriptions,
        key=lambda name: hashlib.sha256(
            f"{GENERATOR_VERSION_V2}\0surface-alias\0{family.value}\0{instance}\0{name}".encode()
        ).digest(),
    )
    return {name: f"v{index:02d}" for index, name in enumerate(ranked, 1)}


def _surface_legend_v2(family: FamilyV2, aliases: Mapping[str, str]) -> str:
    descriptions = _SURFACE_DESCRIPTIONS_V2[family]
    if set(aliases) != set(descriptions):
        raise ValueError("public alias legend must cover the complete domain vocabulary")
    entries = sorted(
        (
            _render_surface_identifier_v2(alias),
            descriptions[name],
        )
        for name, alias in aliases.items()
    )
    return (
        "Case-permuted alias key: "
        + "; ".join(f"{alias} means {description}" for alias, description in entries)
        + ". "
    )


def _relational_role_index_v2(family: FamilyV2, seed: int) -> int:
    """Select the corrupted claim role without consulting position or fault."""

    del family  # Every domain uses the same cross-domain role schedule.
    instance = seed // 512
    template = (seed // 8) % 4
    derivation = (seed // 32) % len(DERIVATION_FAMILY_ORDER_V2)
    return (instance + _RELATIONAL_ROLE_SHIFTS_V2[derivation][template]) % len(_CLAIM_NAMES_V2)


def _relational_claim_transitions_v2(
    family: FamilyV2,
    seed: int,
    aliases: Mapping[str, str],
    claims: tuple[_RelationalClaimV2, ...],
) -> tuple[
    tuple[
        SemanticTransitionV2,
        SemanticTransitionV2,
        SemanticTransitionV2,
        SemanticTransitionV2,
        SemanticTransitionV2,
        SemanticTransitionV2,
        SemanticTransitionV2,
    ],
    SemanticTransitionV2,
    SemanticTransitionV2,
]:
    if len(claims) != 8 or tuple(claim.target for claim in claims) != _CLAIM_NAMES_V2:
        raise ValueError("relational claim bank must contain the eight canonical roles")
    root_index = _relational_role_index_v2(family, seed)
    root_claim = claims[root_index]
    details = tuple(
        _checked_detail_transition(
            "s00",
            claim.target,
            claim.correct_expression,
            claim.correct_value,
            aliases=aliases,
        )
        for index, claim in enumerate(claims)
        if index != root_index
    )
    clean_root = _root_transition(
        "s00",
        root_claim.target,
        root_claim.correct_expression,
        root_claim.correct_value,
        aliases=aliases,
    )
    faulty_root = _root_transition(
        "s00",
        root_claim.target,
        root_claim.correct_expression,
        root_claim.faulty_value,
        aliases=aliases,
        surface_expression=root_claim.faulty_expression,
    )
    return details, clean_root, faulty_root


def _claim_checksum_consequences_v2(
    aliases: Mapping[str, str],
) -> tuple[SemanticTransitionV2, SemanticTransitionV2]:
    return (
        _derive_transition(
            "s00",
            "checksum",
            _add(*(_state(name) for name in _CLAIM_NAMES_V2)),
            aliases=aliases,
        ),
        _derive_transition(
            "s00",
            "checksum_copy",
            _add(_state("checksum"), _literal(0)),
            aliases=aliases,
        ),
    )


def _semantic_layout(
    seed: int,
    *,
    initial_state: Mapping[str, int | Fraction],
    surface_aliases: Mapping[str, str],
    mandatory: tuple[SemanticTransitionV2, SemanticTransitionV2],
    details: tuple[
        SemanticTransitionV2,
        SemanticTransitionV2,
        SemanticTransitionV2,
        SemanticTransitionV2,
        SemanticTransitionV2,
        SemanticTransitionV2,
        SemanticTransitionV2,
    ],
    clean_root: SemanticTransitionV2,
    faulty_root: SemanticTransitionV2,
    consequences: tuple[SemanticTransitionV2, SemanticTransitionV2],
) -> tuple[SemanticProgramV2, tuple[str, ...], int, str, tuple[int, ...]]:
    """Build orthogonal position/template axes and renumber away semantic-role markers."""

    position = seed % 8
    derivation_index = (seed // 32) % len(DERIVATION_FAMILY_ORDER_V2)
    mandatory_order = _DERIVATION_MANDATORY_PERMUTATIONS_V2[derivation_index]
    detail_order = _DERIVATION_DETAIL_PERMUTATIONS_V2[derivation_index]
    organized_mandatory = tuple(mandatory[index] for index in mandatory_order)
    organized_details = tuple(details[index] for index in detail_order)
    raw_prefix = (*organized_mandatory, *organized_details[:position])
    raw_suffix = (*organized_details[position:], *consequences)
    prefix = tuple(
        replace(transition, transition_id=f"s{line:02d}")
        for line, transition in enumerate(raw_prefix, 1)
    )
    root_line = len(prefix) + 1
    clean = replace(clean_root, transition_id=f"s{root_line:02d}")
    faulty = replace(faulty_root, transition_id=f"s{root_line:02d}")
    suffix = tuple(
        replace(transition, transition_id=f"s{line:02d}")
        for line, transition in enumerate(raw_suffix, root_line + 1)
    )
    program = SemanticProgramV2(
        initial_state={
            name: NormalizedMathValueV2.scalar(value) for name, value in initial_state.items()
        },
        surface_aliases=surface_aliases,
        prefix=prefix,
        clean_root=clean,
        faulty_root=faulty,
        suffix=suffix,
        root_line=root_line,
    )
    template = (seed // 8) % 4
    clean_trace = program.render(template=template, faulty=False)
    faulty_trace = program.render(template=template, faulty=True)
    foil_lines = tuple(line for line in range(3, 11) if line != root_line)
    return program, clean_trace, root_line, faulty_trace[root_line - 1], foil_lines


def _stars_and_bars(total: int) -> int:
    return math.comb(total + 2, 2) if total >= 0 else 0


def _artifact_fields_v2(family: FamilyV2) -> dict[str, str]:
    primary, independent, witness = PINNED_FAMILY_ORACLE_DIGESTS_V2[family]
    return {
        "independent_oracle_digest": independent,
        "primary_oracle_digest": primary,
        "verifier_artifact_digest": PINNED_VERIFIER_ARTIFACT_DIGEST_V2,
        "verifier_source_digest": PINNED_VERIFIER_SOURCE_DIGEST_V2,
        "witness_oracle_digest": witness,
    }


def _surface_case_tag_v2(family: FamilyV2, seed: int) -> str:
    """Return a fixed-width tag keyed only by domain and numeric instance."""

    instance = seed // 512
    material = (f"{GENERATOR_VERSION_V2}\0case-tag\0{family.value}\0{instance}").encode()
    value = int.from_bytes(hashlib.sha256(material).digest()[:16], "big") % 10**20
    return f"{value:020d}"


def _crt_case(seed: int) -> HardChallengeCaseV2:
    instance = seed // 512
    fault_variant = (seed // 128) % len(FAULT_MECHANISM_ORDER_V2)
    parameter_cell = (seed // 256) % len(PARAMETER_CELL_ORDER_V2)
    if parameter_cell == 0:
        pairs = (
            (12, 18),
            (14, 21),
            (15, 25),
            (18, 24),
            (20, 30),
            (21, 28),
            (22, 33),
            (24, 30),
        )
        m1, m2 = pairs[instance % len(pairs)]
    else:
        shared_factors = (5, 4, 2, 3, 6, 4, 7, 11)
        reduced_pairs = ((2, 3), (3, 5), (7, 11), (5, 7), (3, 5), (5, 7), (3, 5), (2, 3))
        index = instance % len(shared_factors)
        shared = shared_factors[index]
        left, right = reduced_pairs[index]
        m1, m2 = shared * left, shared * right
    g = math.gcd(m1, m2)
    period = math.lcm(m1, m2)
    product_period = m1 * m2
    repeated_reduction_period = product_period // (g * g)
    claimed = (product_period, repeated_reduction_period)[fault_variant]
    x0 = 1 + ((17 * instance + 5) % (period - 1))
    r1, r2 = x0 % m1, x0 % m2
    reduced_1, reduced_2 = m1 // g, m2 // g
    quotient = (((r2 - r1) // g) * pow(reduced_1, -1, reduced_2)) % reduced_2
    aliases = _surface_aliases_v2(FamilyV2.GENERALIZED_CRT_PERIOD, instance)
    faulty_basis = ("product_basis", "reduced_pair_basis")[fault_variant]
    claim_offsets = (
        _literal(0),
        _state("x0"),
        _state("period_basis"),
        _add(_state("period_basis"), _state("x0")),
        _state("g"),
        _state("reduced1"),
        _state("reduced2"),
        _state("q0"),
    )
    offset_values = (0, x0, period, period + x0, g, reduced_1, reduced_2, quotient)
    claims = tuple(
        _RelationalClaimV2(
            target=target,
            correct_expression=_add(_state("period_basis"), offset),
            correct_value=period + offset_value,
            faulty_expression=_add(_state(faulty_basis), offset),
            faulty_value=claimed + offset_value,
        )
        for target, offset, offset_value in zip(
            _CLAIM_NAMES_V2, claim_offsets, offset_values, strict=True
        )
    )
    details, clean_root, faulty_root = _relational_claim_transitions_v2(
        FamilyV2.GENERALIZED_CRT_PERIOD,
        seed,
        aliases,
        claims,
    )
    program, clean, fault_line, faulty, foils = _semantic_layout(
        seed,
        initial_state={
            "product_basis": product_period,
            "reduced_pair_basis": repeated_reduction_period,
            "g": g,
            "q0": quotient,
            "reduced1": reduced_1,
            "reduced2": reduced_2,
            "period_basis": period,
            "x0": x0,
        },
        surface_aliases=aliases,
        mandatory=(
            _check_transition("s00", _state("g"), g, aliases=aliases),
            _check_transition("s00", _state("x0"), x0, aliases=aliases),
        ),
        details=details,
        clean_root=clean_root,
        faulty_root=faulty_root,
        consequences=_claim_checksum_consequences_v2(aliases),
    )
    witness = DiagnosticWitnessV2(
        WitnessKindV2.CRT_PERIOD_COUNTEREXAMPLE,
        {
            "claimed_period": claimed,
            "first_solution": x0,
            "second_solution": x0 + period,
            "true_period": period,
        },
    )
    return HardChallengeCaseV2(
        case_id=f"DV2-CRT-{seed:08x}",
        family=FamilyV2.GENERALIZED_CRT_PERIOD,
        seed=seed,
        parameters={
            "m1": m1,
            "m2": m2,
            "product_period": product_period,
            "r1": r1,
            "r2": r2,
            "repeated_reduction_period": repeated_reduction_period,
            "true_period": period,
            "x0": x0,
        },
        semantic_program=program,
        expected_repair=RepairSubmissionV2("value", program.clean_root.declared),
        expected_witness=WitnessSubmissionV2(
            UniversalWitnessCodeV2.SOLUTION_PAIR,
            (NormalizedMathValueV2.integer_vector((x0, x0 + period)),),
        ),
        prompt=(
            f"Opaque case tag {_surface_case_tag_v2(FamilyV2.GENERALIZED_CRT_PERIOD, seed)}. "
            f"Review the numbered description of all integers x satisfying x≡{r1} (mod {m1}) "
            f"and x≡{r2} (mod {m2}). "
            + _surface_legend_v2(FamilyV2.GENERALIZED_CRT_PERIOD, aliases)
            + "Decide whether the conclusion follows. If not, "
            "identify the first invalid line and report a normalized repair and witness. "
            + _SOLVER_OUTPUT_CONTRACT_V2
        ),
        clean_trace=clean,
        faulty_line=fault_line,
        faulty_replacement=faulty,
        repair_replacement=clean[fault_line - 1],
        foil_lines=foils,
        witness=witness,
        primary_verifier=_VERIFIER_NAMES[FamilyV2.GENERALIZED_CRT_PERIOD][0],
        independent_verifier=_VERIFIER_NAMES[FamilyV2.GENERALIZED_CRT_PERIOD][1],
        **_artifact_fields_v2(FamilyV2.GENERALIZED_CRT_PERIOD),
    )


def _composition_case(seed: int) -> HardChallengeCaseV2:
    instance = seed // 512
    fault_variant = (seed // 128) % len(FAULT_MECHANISM_ORDER_V2)
    parameter_cell = (seed // 256) % len(PARAMETER_CELL_ORDER_V2)
    if parameter_cell == 0:
        b1 = 4 + instance % 4
        b2 = 5 + (instance // 4) % 4
    else:
        b1 = 6 + (instance // 4) % 4
        b2 = 4 + instance % 4
    total = b1 + b2 + 2
    b3 = total - 3
    unbounded = _stars_and_bars(total)
    single_counts = tuple(_stars_and_bars(total - (bound + 1)) for bound in (b1, b2, b3))
    single_total = sum(single_counts)
    true_overlap = 1
    single_shift_overlap = 3
    repeated_joint_overlap = 2
    faulty_overlap = (single_shift_overlap, repeated_joint_overlap)[fault_variant]
    correct_count = unbounded - single_total + true_overlap
    faulty_count = unbounded - single_total + faulty_overlap
    aliases = _surface_aliases_v2(FamilyV2.BOUNDED_COMPOSITION_OVERLAP, instance)
    faulty_basis = (
        "single_shift_basis",
        "pair_intersection_basis",
    )[fault_variant]
    claim_offsets = (
        _literal(0),
        _subtract(_state("U"), _state("S")),
        _state("single1"),
        _state("single2"),
        _state("single3"),
        _state("joint_basis"),
        _state("total"),
        _state("U"),
    )
    offset_values = (
        0,
        unbounded - single_total,
        single_counts[0],
        single_counts[1],
        single_counts[2],
        true_overlap,
        total,
        unbounded,
    )
    claims = tuple(
        _RelationalClaimV2(
            target=target,
            correct_expression=_add(_state("joint_basis"), offset),
            correct_value=true_overlap + offset_value,
            faulty_expression=_add(_state(faulty_basis), offset),
            faulty_value=faulty_overlap + offset_value,
        )
        for target, offset, offset_value in zip(
            _CLAIM_NAMES_V2, claim_offsets, offset_values, strict=True
        )
    )
    details, clean_root, faulty_root = _relational_claim_transitions_v2(
        FamilyV2.BOUNDED_COMPOSITION_OVERLAP,
        seed,
        aliases,
        claims,
    )
    program, clean, fault_line, faulty, foils = _semantic_layout(
        seed,
        initial_state={
            "single_shift_basis": single_shift_overlap,
            "pair_intersection_basis": repeated_joint_overlap,
            "joint_basis": true_overlap,
            "S": single_total,
            "U": unbounded,
            "single1": single_counts[0],
            "single2": single_counts[1],
            "single3": single_counts[2],
            "total": total,
        },
        surface_aliases=aliases,
        mandatory=(
            _check_transition("s00", _state("U"), unbounded, aliases=aliases),
            _check_transition("s00", _state("S"), single_total, aliases=aliases),
        ),
        details=details,
        clean_root=clean_root,
        faulty_root=faulty_root,
        consequences=_claim_checksum_consequences_v2(aliases),
    )
    witness = DiagnosticWitnessV2(
        WitnessKindV2.COMPOSITION_OVERLAP_ASSIGNMENT,
        {
            "correct_count": correct_count,
            "faulty_count": faulty_count,
            "faulty_overlap": faulty_overlap,
            "overlap_assignment": [b1 + 1, b2 + 1, 0],
            "true_overlap": true_overlap,
        },
    )
    return HardChallengeCaseV2(
        case_id=f"DV2-COM-{seed:08x}",
        family=FamilyV2.BOUNDED_COMPOSITION_OVERLAP,
        seed=seed,
        parameters={
            "b1": b1,
            "b2": b2,
            "b3": b3,
            "correct_count": correct_count,
            "pair_intersection_overlap": repeated_joint_overlap,
            "single_shift_overlap": single_shift_overlap,
            "single_total": single_total,
            "total": total,
            "true_overlap": true_overlap,
            "unbounded": unbounded,
        },
        semantic_program=program,
        expected_repair=RepairSubmissionV2("value", program.clean_root.declared),
        expected_witness=WitnessSubmissionV2(
            UniversalWitnessCodeV2.FEASIBLE_ASSIGNMENT,
            (
                NormalizedMathValueV2.integer_vector((b1 + 1, b2 + 1, 0)),
                NormalizedMathValueV2.scalar(correct_count),
            ),
        ),
        prompt=(
            f"Opaque case tag {_surface_case_tag_v2(FamilyV2.BOUNDED_COMPOSITION_OVERLAP, seed)}. "
            f"Review the numbered count of triples with x1+x2+x3={total} and "
            f"0<=x1<={b1}, 0<=x2<={b2}, 0<=x3<={b3}. "
            + _surface_legend_v2(FamilyV2.BOUNDED_COMPOSITION_OVERLAP, aliases)
            + "Decide whether the conclusion "
            "follows. If not, identify the first invalid line and report a normalized repair "
            "and witness. " + _SOLVER_OUTPUT_CONTRACT_V2
        ),
        clean_trace=clean,
        faulty_line=fault_line,
        faulty_replacement=faulty,
        repair_replacement=clean[fault_line - 1],
        foil_lines=foils,
        witness=witness,
        primary_verifier=_VERIFIER_NAMES[FamilyV2.BOUNDED_COMPOSITION_OVERLAP][0],
        independent_verifier=_VERIFIER_NAMES[FamilyV2.BOUNDED_COMPOSITION_OVERLAP][1],
        **_artifact_fields_v2(FamilyV2.BOUNDED_COMPOSITION_OVERLAP),
    )


def _shortest_path_case(seed: int) -> HardChallengeCaseV2:
    instance = seed // 512
    fault_variant = (seed // 128) % len(FAULT_MECHANISM_ORDER_V2)
    parameter_cell = (seed // 256) % len(PARAMETER_CELL_ORDER_V2)
    if parameter_cell == 0:
        sa = 4 + instance % 3
        sb = sa + 3 + (instance // 3) % 3
        true_a = sa - 1 - instance % 2
    else:
        true_a = 2 + instance % 4
        sa = true_a + 1 + (instance // 4) % 2
        sb = sa + 4 + instance % 3
    ba = true_a - sb
    second_pass_a = 2 * true_a - sa
    faulty_a = (sa, second_pass_a)[fault_variant]
    at = 2 + (instance // 5) % 4
    true_t = true_a + at
    faulty_t = faulty_a + at
    bt = true_t + 4
    st = true_t + 7
    aliases = _surface_aliases_v2(FamilyV2.NEGATIVE_EDGE_FINALIZATION, instance)
    faulty_basis = ("sa", "second_pass_basis")[fault_variant]
    correct_expressions = (
        _add(_state("incoming_basis"), _literal(0)),
        _add(_state("incoming_basis"), _state("at")),
        _subtract(_state("sa"), _state("incoming_basis")),
        _add(_state("incoming_basis"), _state("bt")),
        _add(_state("incoming_basis"), _state("st")),
        _add(_state("incoming_basis"), _state("incoming_basis")),
        _subtract(_state("incoming_basis"), _state("ba")),
        _add(_state("incoming_basis"), _state("sb")),
    )
    faulty_expressions = (
        _add(_state(faulty_basis), _literal(0)),
        _add(_state(faulty_basis), _state("at")),
        _subtract(_state("sa"), _state(faulty_basis)),
        _add(_state(faulty_basis), _state("bt")),
        _add(_state(faulty_basis), _state("st")),
        _add(_state(faulty_basis), _state("incoming_basis")),
        _subtract(_state(faulty_basis), _state("ba")),
        _add(_state(faulty_basis), _state("sb")),
    )
    correct_values = (
        true_a,
        true_a + at,
        sa - true_a,
        true_a + bt,
        true_a + st,
        2 * true_a,
        true_a - ba,
        true_a + sb,
    )
    faulty_values = (
        faulty_a,
        faulty_a + at,
        sa - faulty_a,
        faulty_a + bt,
        faulty_a + st,
        faulty_a + true_a,
        faulty_a - ba,
        faulty_a + sb,
    )
    claims = tuple(
        _RelationalClaimV2(target, correct_expression, correct_value, bad_expression, bad_value)
        for target, correct_expression, correct_value, bad_expression, bad_value in zip(
            _CLAIM_NAMES_V2,
            correct_expressions,
            correct_values,
            faulty_expressions,
            faulty_values,
            strict=True,
        )
    )
    details, clean_root, faulty_root = _relational_claim_transitions_v2(
        FamilyV2.NEGATIVE_EDGE_FINALIZATION,
        seed,
        aliases,
        claims,
    )
    program, clean, fault_line, faulty, foils = _semantic_layout(
        seed,
        initial_state={
            "at": at,
            "ba": ba,
            "bt": bt,
            "sa": sa,
            "sb": sb,
            "second_pass_basis": second_pass_a,
            "st": st,
            "incoming_basis": true_a,
        },
        surface_aliases=aliases,
        mandatory=(
            _check_transition("s00", _state("sa"), sa, aliases=aliases),
            _check_transition("s00", _state("sb"), sb, aliases=aliases),
        ),
        details=details,
        clean_root=clean_root,
        faulty_root=faulty_root,
        consequences=_claim_checksum_consequences_v2(aliases),
    )
    witness = DiagnosticWitnessV2(
        WitnessKindV2.NEGATIVE_EDGE_PATH,
        {
            "edge_weights": [sb, ba, at],
            "faulty_distance": faulty_t,
            "path": ["s", "b", "a", "t"],
            "path_length": true_t,
        },
    )
    return HardChallengeCaseV2(
        case_id=f"DV2-SPT-{seed:08x}",
        family=FamilyV2.NEGATIVE_EDGE_FINALIZATION,
        seed=seed,
        parameters={
            "at": at,
            "ba": ba,
            "bt": bt,
            "sa": sa,
            "sb": sb,
            "second_pass_a": second_pass_a,
            "st": st,
            "true_a": true_a,
            "true_t": true_t,
        },
        semantic_program=program,
        expected_repair=RepairSubmissionV2("value", program.clean_root.declared),
        expected_witness=WitnessSubmissionV2(
            UniversalWitnessCodeV2.DIRECTED_PATH,
            (
                NormalizedMathValueV2.symbol_vector(("s", "b", "a", "t")),
                NormalizedMathValueV2.scalar(true_t),
            ),
        ),
        prompt=(
            f"Opaque case tag {_surface_case_tag_v2(FamilyV2.NEGATIVE_EDGE_FINALIZATION, seed)}. "
            f"Review the numbered shortest-path analysis for arcs s→a:{sa}, s→b:{sb}, "
            f"b→a:{ba}, a→t:{at}, b→t:{bt}, and s→t:{st}. "
            + _surface_legend_v2(FamilyV2.NEGATIVE_EDGE_FINALIZATION, aliases)
            + "Decide whether its conclusion "
            "follows. If not, identify the first invalid line and report a normalized repair "
            "and witness. " + _SOLVER_OUTPUT_CONTRACT_V2
        ),
        clean_trace=clean,
        faulty_line=fault_line,
        faulty_replacement=faulty,
        repair_replacement=clean[fault_line - 1],
        foil_lines=foils,
        witness=witness,
        primary_verifier=_VERIFIER_NAMES[FamilyV2.NEGATIVE_EDGE_FINALIZATION][0],
        independent_verifier=_VERIFIER_NAMES[FamilyV2.NEGATIVE_EDGE_FINALIZATION][1],
        **_artifact_fields_v2(FamilyV2.NEGATIVE_EDGE_FINALIZATION),
    )


def _recurrence_case(seed: int) -> HardChallengeCaseV2:
    instance = seed // 512
    fault_variant = (seed // 128) % len(FAULT_MECHANISM_ORDER_V2)
    parameter_cell = (seed // 256) % len(PARAMETER_CELL_ORDER_V2)
    if parameter_cell == 0:
        r = (2, 3, 4)[instance % 3]
        c0 = instance % 5 - 2
        c1 = (instance // 7) % 7 - 3
    else:
        r = (5, 6, 7)[instance % 3]
        c0 = 3 + instance % 5
        c1 = -5 + (instance // 5) % 7
    coefficient = Fraction(1, 2 * r * r)
    coefficient_token = _canonical_fraction(coefficient)
    faulty_degree = (1, 3)[fault_variant]
    faulty_delta = _second_difference_coefficients_v2(faulty_degree)
    faulty_residual = coefficient * r * r * faulty_delta[0]
    aliases = _surface_aliases_v2(FamilyV2.FORCED_RECURRENCE_RESONANCE, instance)
    faulty_basis = ("linear_degree", "cubic_degree")[fault_variant]
    correct_expressions = (
        _add(_state("factor_count"), _literal(0)),
        _add(_state("factor_count"), _literal(1)),
        _add(_state("factor_count"), _state("factor_count")),
        _add(_state("factor_count"), _state("r")),
        _add(_state("factor_count"), _state("c0")),
        _add(_state("factor_count"), _state("c1")),
        _subtract(_state("factor_count"), _literal(1)),
        _multiply(_state("factor_count"), _state("K")),
    )
    faulty_expressions = (
        _add(_state(faulty_basis), _literal(0)),
        _add(_state(faulty_basis), _literal(1)),
        _add(_state(faulty_basis), _state("factor_count")),
        _add(_state(faulty_basis), _state("r")),
        _add(_state(faulty_basis), _state("c0")),
        _add(_state(faulty_basis), _state("c1")),
        _subtract(_state(faulty_basis), _literal(1)),
        _multiply(_state(faulty_basis), _state("K")),
    )
    correct_values = (
        2,
        3,
        4,
        2 + r,
        2 + c0,
        2 + c1,
        1,
        2 * coefficient,
    )
    faulty_values = (
        faulty_degree,
        faulty_degree + 1,
        faulty_degree + 2,
        faulty_degree + r,
        faulty_degree + c0,
        faulty_degree + c1,
        faulty_degree - 1,
        faulty_degree * coefficient,
    )
    claims = tuple(
        _RelationalClaimV2(target, correct_expression, correct_value, bad_expression, bad_value)
        for target, correct_expression, correct_value, bad_expression, bad_value in zip(
            _CLAIM_NAMES_V2,
            correct_expressions,
            correct_values,
            faulty_expressions,
            faulty_values,
            strict=True,
        )
    )
    details, clean_root, faulty_root = _relational_claim_transitions_v2(
        FamilyV2.FORCED_RECURRENCE_RESONANCE,
        seed,
        aliases,
        claims,
    )
    program, clean, fault_line, faulty, foils = _semantic_layout(
        seed,
        initial_state={
            "K": coefficient,
            "c0": c0,
            "c1": c1,
            "cubic_degree": 3,
            "linear_degree": 1,
            "factor_count": 2,
            "r": r,
        },
        surface_aliases=aliases,
        mandatory=(
            _check_transition("s00", _state("r"), r, aliases=aliases),
            _check_transition("s00", _state("K"), coefficient, aliases=aliases),
        ),
        details=details,
        clean_root=clean_root,
        faulty_root=faulty_root,
        consequences=_claim_checksum_consequences_v2(aliases),
    )
    witness = DiagnosticWitnessV2(
        WitnessKindV2.RESONANCE_RESIDUAL,
        {
            "faulty_residual": _canonical_fraction(faulty_residual),
            "index": 0,
            "particular_coefficient": coefficient_token,
            "required_residual": 1,
        },
    )
    return HardChallengeCaseV2(
        case_id=f"DV2-REC-{seed:08x}",
        family=FamilyV2.FORCED_RECURRENCE_RESONANCE,
        seed=seed,
        parameters={
            "c0": c0,
            "c1": c1,
            "coefficient_denominator": coefficient.denominator,
            "coefficient_numerator": coefficient.numerator,
            "multiplicity": 2,
            "r": r,
        },
        semantic_program=program,
        expected_repair=RepairSubmissionV2("value", program.clean_root.declared),
        expected_witness=WitnessSubmissionV2(
            UniversalWitnessCodeV2.POLYNOMIAL_IDENTITY,
            (
                NormalizedMathValueV2.scalar(coefficient),
                NormalizedMathValueV2.integer_vector((2,)),
                NormalizedMathValueV2.integer_vector((1,)),
                NormalizedMathValueV2.scalar(faulty_residual),
            ),
        ),
        prompt=(
            f"Opaque case tag {_surface_case_tag_v2(FamilyV2.FORCED_RECURRENCE_RESONANCE, seed)}. "
            f"Review the numbered solution of a_(n+2)-{2 * r}a_(n+1)+{r * r}a_n={r}^n "
            f"with a_0={c0}, a_1={c1}. "
            + _surface_legend_v2(FamilyV2.FORCED_RECURRENCE_RESONANCE, aliases)
            + "Decide whether the conclusion follows. If not, "
            "identify the first invalid line and report a normalized repair and witness. "
            + _SOLVER_OUTPUT_CONTRACT_V2
        ),
        clean_trace=clean,
        faulty_line=fault_line,
        faulty_replacement=faulty,
        repair_replacement=clean[fault_line - 1],
        foil_lines=foils,
        witness=witness,
        primary_verifier=_VERIFIER_NAMES[FamilyV2.FORCED_RECURRENCE_RESONANCE][0],
        independent_verifier=_VERIFIER_NAMES[FamilyV2.FORCED_RECURRENCE_RESONANCE][1],
        **_artifact_fields_v2(FamilyV2.FORCED_RECURRENCE_RESONANCE),
    )


def encode_development_seed_v2(
    *,
    position: int,
    template: int,
    derivation_family: int,
    fault_variant: int,
    parameter_cell: int,
    instance: int,
) -> int:
    """Encode independent public-development coordinates into one bounded seed."""

    checked_position = _integer(position, "seed position", minimum=0, maximum=7)
    checked_template = _integer(template, "seed template", minimum=0, maximum=3)
    checked_derivation = _integer(derivation_family, "seed derivation family", minimum=0, maximum=3)
    checked_fault = _integer(fault_variant, "seed fault variant", minimum=0, maximum=1)
    checked_cell = _integer(parameter_cell, "seed parameter cell", minimum=0, maximum=1)
    checked_instance = _integer(
        instance, "seed numeric instance", minimum=0, maximum=MAX_SEED_V2 // 512
    )
    encoded = (
        checked_position
        + 8 * checked_template
        + 32 * checked_derivation
        + 128 * checked_fault
        + 256 * checked_cell
        + 512 * checked_instance
    )
    return _integer(encoded, "encoded development seed", minimum=0, maximum=MAX_SEED_V2)


def generate_development_case_v2(family: FamilyV2 | str, seed: int) -> HardChallengeCaseV2:
    """Generate one deterministic, public-development-only hard challenge."""

    try:
        if type(family) is FamilyV2:
            parsed_family = family
        elif type(family) is str:
            parsed_family = FamilyV2(family)
        else:
            raise ValueError("challenge family is not registered")
    except (TypeError, ValueError) as exc:
        raise ValueError("challenge family is not registered") from exc
    checked_seed = _integer(seed, "case seed", minimum=0, maximum=MAX_SEED_V2)
    builders = {
        FamilyV2.GENERALIZED_CRT_PERIOD: _crt_case,
        FamilyV2.BOUNDED_COMPOSITION_OVERLAP: _composition_case,
        FamilyV2.NEGATIVE_EDGE_FINALIZATION: _shortest_path_case,
        FamilyV2.FORCED_RECURRENCE_RESONANCE: _recurrence_case,
    }
    return builders[parsed_family](checked_seed)


def generate_public_calibration_bank_v2() -> tuple[HardChallengeCaseV2, ...]:
    """Generate the fixed 320-case orthogonal public-calibration design."""

    base_root_by_q = {
        q: root for root, group in enumerate(_CALIBRATION_BASE_ROOT_GROUPS_V2) for q in group
    }
    if set(base_root_by_q) != set(range(20)):
        raise AssertionError("sealed calibration root groups must partition q=0..19")
    cases: list[HardChallengeCaseV2] = []
    for domain_index, domain in enumerate(FAMILY_ORDER_V2):
        for derivation_index in range(len(DERIVATION_FAMILY_ORDER_V2)):
            for template in range(4):
                for repetition in range(5):
                    q = 5 * domain_index + repetition
                    fault_variant = (q % 2) ^ _CALIBRATION_MECHANISM_FLIPS_V2[derivation_index][
                        template
                    ]
                    parameter_cell = ((q // 2) % 2) ^ (derivation_index % 2)
                    base_root = base_root_by_q[q]
                    position = _CALIBRATION_ROOT_PERMUTATIONS_V2[derivation_index][template][
                        base_root
                    ]
                    seed = encode_development_seed_v2(
                        position=position,
                        template=template,
                        derivation_family=derivation_index,
                        fault_variant=fault_variant,
                        parameter_cell=parameter_cell,
                        instance=q,
                    )
                    cases.append(generate_development_case_v2(domain, seed))
    if len(cases) != CALIBRATION_CASE_COUNT_V2:
        raise AssertionError("sealed public calibration schedule has the wrong size")
    return tuple(cases)


def adapt_calibration_case_v2(
    case: HardChallengeCaseV2,
    *,
    brain_off_correct: bool,
) -> CalibrationCase:
    """Bind one certified rendered hard case to calibration-v2's public record."""

    if type(case) is not HardChallengeCaseV2:
        raise ValueError("calibration adaptation requires an exact hard-challenge case")
    if type(brain_off_correct) is not bool:
        raise ValueError("brain-off correctness must be an exact boolean")
    report = certification_report_v2(case)
    if not report.passed:
        raise ValueError("hard-challenge case must pass exact certification before adaptation")
    values = {
        "brain_off_correct": brain_off_correct,
        "case_id": case.case_id,
        "domain": case.domain,
        "eligible_fault_lines": CALIBRATION_ELIGIBLE_FAULT_LINES_V2,
        "family": case.derivation_family,
        "fault_kind": case.mechanism,
        "fault_line": case.faulty_line,
        "faulty_trace": case.trace(faulty=True),
        "generator_cell": case.generator_cell,
        "prompt": case.prompt,
        "template_id": case.template_id,
    }
    binding = CalibrationCase.binding_digest_for(**values)
    receipts = tuple(
        OracleReceipt.issue(
            case_binding_digest=binding,
            verifier_id=f"{name}@{digest}",
            first_invalid_line=case.faulty_line,
        )
        for name, digest in (
            (case.primary_verifier, case.primary_oracle_digest),
            (case.independent_verifier, case.independent_oracle_digest),
        )
    )
    return CalibrationCase.build(**values, oracles=receipts)


def adapt_public_calibration_bank_v2(
    brain_off_outcomes: Mapping[str, bool],
) -> tuple[CalibrationCase, ...]:
    """Adapt the fixed bank only when one exact outcome is supplied per case id."""

    if type(brain_off_outcomes) is not dict or not all(
        type(case_id) is str and type(outcome) is bool
        for case_id, outcome in brain_off_outcomes.items()
    ):
        raise ValueError("brain-off outcomes must be an exact string-to-boolean object")
    cases = generate_public_calibration_bank_v2()
    expected_ids = {case.case_id for case in cases}
    supplied_ids = set(brain_off_outcomes)
    if supplied_ids != expected_ids:
        raise ValueError(
            "brain-off outcomes must cover the fixed calibration bank exactly; "
            f"missing={sorted(expected_ids - supplied_ids)}, "
            f"extra={sorted(supplied_ids - expected_ids)}"
        )
    return tuple(
        adapt_calibration_case_v2(
            case,
            brain_off_correct=brain_off_outcomes[case.case_id],
        )
        for case in cases
    )


def generate_public_development_bank_v2(
    seeds: Iterable[int] = range(64),
) -> tuple[HardChallengeCaseV2, ...]:
    """Generate a bounded cross-product of public seeds and all v2 families."""

    if isinstance(seeds, str | bytes):
        raise ValueError("seeds must be an iterable of integers")
    try:
        materialized = tuple(islice(iter(seeds), MAX_BANK_SEEDS_V2 + 1))
    except TypeError as exc:
        raise ValueError("seeds must be an iterable of integers") from exc
    if not 1 <= len(materialized) <= MAX_BANK_SEEDS_V2:
        raise ValueError(f"seed collection must contain between 1 and {MAX_BANK_SEEDS_V2} entries")
    checked = tuple(
        _integer(seed, "case seed", minimum=0, maximum=MAX_SEED_V2) for seed in materialized
    )
    if len(set(checked)) != len(checked):
        raise ValueError("seed collection must be unique")
    return tuple(
        generate_development_case_v2(family, seed) for seed in checked for family in FAMILY_ORDER_V2
    )


def _crt_faulty_period_v2(case: HardChallengeCaseV2) -> int:
    p = case.parameters
    return (p["product_period"], p["repeated_reduction_period"])[case.fault_variant]


def _composition_faulty_values_v2(case: HardChallengeCaseV2) -> tuple[int, int]:
    p = case.parameters
    overlap = (p["single_shift_overlap"], p["pair_intersection_overlap"])[case.fault_variant]
    return overlap, p["unbounded"] - p["single_total"] + overlap


def _path_faulty_values_v2(case: HardChallengeCaseV2) -> tuple[int, int]:
    p = case.parameters
    distance_a = (p["sa"], p["second_pass_a"])[case.fault_variant]
    return distance_a, distance_a + p["at"]


def _recurrence_faulty_degree_v2(case: HardChallengeCaseV2) -> int:
    return (1, 3)[case.fault_variant]


def _crt_verification(
    case: HardChallengeCaseV2, witness_payload: Mapping[str, Any] | None = None
) -> tuple[bool, bool, bool]:
    p = case.parameters
    m1, m2, r1, r2 = p["m1"], p["m2"], p["r1"], p["r2"]
    expected_x0, expected_period = p["x0"], p["true_period"]
    periods = [step for step in range(1, m1 * m2 + 1) if step % m1 == step % m2 == 0]
    residue_solutions = [x for x in range(expected_period) if x % m1 == r1 and x % m2 == r2]
    primary = periods[0] == expected_period and residue_solutions == [expected_x0]

    g = math.gcd(m1, m2)
    reduced_modulus = m2 // g
    multiplier = ((r2 - r1) // g) * pow(m1 // g, -1, reduced_modulus)
    constructed = (r1 + m1 * (multiplier % reduced_modulus)) % math.lcm(m1, m2)
    secondary = (
        (r2 - r1) % g == 0 and math.lcm(m1, m2) == expected_period and constructed == expected_x0
    )

    witness = case.witness.payload if witness_payload is None else witness_payload
    first, second = witness["first_solution"], witness["second_solution"]
    claimed_period = _crt_faulty_period_v2(case)
    claim_refuted = claimed_period != expected_period and (
        (claimed_period > expected_period and second < claimed_period)
        or (
            claimed_period < expected_period
            and (claimed_period % m1 != 0 or claimed_period % m2 != 0)
        )
    )
    witness_passed = (
        first == expected_x0
        and second == first + expected_period
        and claim_refuted
        and all(first % modulus == residue for modulus, residue in ((m1, r1), (m2, r2)))
        and all(second % modulus == residue for modulus, residue in ((m1, r1), (m2, r2)))
        and witness["claimed_period"] == claimed_period
        and witness["true_period"] == expected_period
    )
    return primary, secondary, witness_passed


def _composition_verification(
    case: HardChallengeCaseV2, witness_payload: Mapping[str, Any] | None = None
) -> tuple[bool, bool, bool]:
    p = case.parameters
    bounds = (p["b1"], p["b2"], p["b3"])
    total = p["total"]
    inclusion_exclusion = 0
    for subset_size in range(4):
        for subset in combinations(range(3), subset_size):
            residual = total - sum(bounds[index] + 1 for index in subset)
            inclusion_exclusion += (-1) ** subset_size * _stars_and_bars(residual)
    primary = inclusion_exclusion == p["correct_count"]

    counts = [0] * (total + 1)
    counts[0] = 1
    for bound in bounds:
        next_counts = [0] * (total + 1)
        for subtotal, count in enumerate(counts):
            for value in range(min(bound, total - subtotal) + 1):
                next_counts[subtotal + value] += count
        counts = next_counts
    secondary = counts[total] == p["correct_count"]

    witness = case.witness.payload if witness_payload is None else witness_payload
    faulty_overlap, faulty_count = _composition_faulty_values_v2(case)
    assignment = tuple(witness["overlap_assignment"])
    overlap_points = [
        (x1, x2, total - x1 - x2)
        for x1 in range(bounds[0] + 1, total + 1)
        for x2 in range(bounds[1] + 1, total + 1)
        if 0 <= total - x1 - x2 <= bounds[2]
    ]
    witness_passed = (
        overlap_points == [assignment]
        and witness["true_overlap"] == len(overlap_points) == p["true_overlap"]
        and witness["faulty_overlap"] == faulty_overlap
        and witness["correct_count"] == p["correct_count"]
        and witness["faulty_count"] == faulty_count
        and faulty_count != p["correct_count"]
    )
    return primary, secondary, witness_passed


def _path_verification(
    case: HardChallengeCaseV2, witness_payload: Mapping[str, Any] | None = None
) -> tuple[bool, bool, bool]:
    p = case.parameters
    edges = (
        ("s", "a", p["sa"]),
        ("s", "b", p["sb"]),
        ("b", "a", p["ba"]),
        ("a", "t", p["at"]),
        ("b", "t", p["bt"]),
        ("s", "t", p["st"]),
    )
    distances: dict[str, int | None] = {vertex: None for vertex in ("s", "a", "b", "t")}
    distances["s"] = 0
    for _ in range(3):
        changed = False
        for left, right, weight in edges:
            if distances[left] is None:
                continue
            candidate = distances[left] + weight
            if distances[right] is None or candidate < distances[right]:
                distances[right] = candidate
                changed = True
        if not changed:
            break
    primary = distances["a"] == p["true_a"] and distances["t"] == p["true_t"]

    adjacency: dict[str, list[tuple[str, int]]] = {vertex: [] for vertex in distances}
    for left, right, weight in edges:
        adjacency[left].append((right, weight))
    lengths: list[int] = []

    def visit(vertex: str, seen: frozenset[str], length: int) -> None:
        if vertex == "t":
            lengths.append(length)
            return
        for right, weight in adjacency[vertex]:
            if right not in seen:
                visit(right, seen | {right}, length + weight)

    visit("s", frozenset({"s"}), 0)
    secondary = min(lengths) == p["true_t"]

    witness = case.witness.payload if witness_payload is None else witness_payload
    weights = tuple(witness["edge_weights"])
    _, faulty_t = _path_faulty_values_v2(case)
    witness_passed = (
        tuple(witness["path"]) == ("s", "b", "a", "t")
        and weights == (p["sb"], p["ba"], p["at"])
        and sum(weights) == witness["path_length"] == p["true_t"]
        and witness["faulty_distance"] == faulty_t
        and p["true_t"] != faulty_t
    )
    return primary, secondary, witness_passed


def _recurrence_verification(
    case: HardChallengeCaseV2, witness_payload: Mapping[str, Any] | None = None
) -> tuple[bool, bool, bool]:
    p = case.parameters
    r = p["r"]
    coefficient = Fraction(p["coefficient_numerator"], p["coefficient_denominator"])

    def particular(index: int) -> Fraction:
        return coefficient * index * index * r**index

    def operator(sequence: Any, index: int) -> Fraction:
        return sequence(index + 2) - 2 * r * sequence(index + 1) + r * r * sequence(index)

    primary = all(operator(particular, index) == r**index for index in range(6))

    p0, p1 = particular(0), particular(1)
    a_value = Fraction(p["c0"]) - p0
    b_value = (Fraction(p["c1"]) - p1) / r - a_value

    def closed(index: int) -> Fraction:
        return (a_value + b_value * index) * r**index + particular(index)

    forward = [Fraction(p["c0"]), Fraction(p["c1"])]
    for index in range(6):
        forward.append(2 * r * forward[-1] - r * r * forward[-2] + r**index)
    secondary = all(closed(index) == value for index, value in enumerate(forward))

    def faulty_particular(index: int) -> Fraction:
        return coefficient * index ** _recurrence_faulty_degree_v2(case) * r**index

    witness = case.witness.payload if witness_payload is None else witness_payload
    witness_index = witness["index"]
    faulty_residual = operator(faulty_particular, witness_index)
    witness_passed = (
        faulty_residual == _parse_fraction(witness["faulty_residual"], "faulty residual")
        and faulty_residual != r**witness_index
        and witness["required_residual"] == r**witness_index
        and _parse_fraction(witness["particular_coefficient"], "particular coefficient")
        == coefficient
    )
    return primary, secondary, witness_passed


def _crt_primary_v2(case: HardChallengeCaseV2) -> bool:
    p = case.parameters
    solutions = [
        value
        for value in range(p["true_period"])
        if value % p["m1"] == p["r1"] and value % p["m2"] == p["r2"]
    ]
    periods = [
        step
        for step in range(1, p["m1"] * p["m2"] + 1)
        if step % p["m1"] == 0 and step % p["m2"] == 0
    ]
    return solutions == [p["x0"]] and periods[0] == p["true_period"]


def _crt_independent_v2(case: HardChallengeCaseV2) -> bool:
    p = case.parameters
    g = math.gcd(p["m1"], p["m2"])
    reduced = p["m2"] // g
    multiplier = ((p["r2"] - p["r1"]) // g) * pow(p["m1"] // g, -1, reduced)
    constructed = (p["r1"] + p["m1"] * (multiplier % reduced)) % math.lcm(p["m1"], p["m2"])
    return (
        (p["r2"] - p["r1"]) % g == 0
        and math.lcm(p["m1"], p["m2"]) == p["true_period"]
        and constructed == p["x0"]
    )


def _crt_witness_v2(case: HardChallengeCaseV2, payload: Mapping[str, Any]) -> bool:
    p = case.parameters
    first = payload["first_solution"]
    second = payload["second_solution"]
    claimed_period = _crt_faulty_period_v2(case)
    claim_refuted = claimed_period != p["true_period"] and (
        (claimed_period > p["true_period"] and second < claimed_period)
        or (
            claimed_period < p["true_period"]
            and (claimed_period % p["m1"] != 0 or claimed_period % p["m2"] != 0)
        )
    )
    return (
        first == p["x0"]
        and second == first + p["true_period"]
        and claim_refuted
        and all(
            value % modulus == residue
            for value in (first, second)
            for modulus, residue in ((p["m1"], p["r1"]), (p["m2"], p["r2"]))
        )
        and payload["claimed_period"] == claimed_period
        and payload["true_period"] == p["true_period"]
    )


def _composition_primary_v2(case: HardChallengeCaseV2) -> bool:
    p = case.parameters
    bounds = (p["b1"], p["b2"], p["b3"])
    result = 0
    for subset_size in range(4):
        for subset in combinations(range(3), subset_size):
            residual = p["total"] - sum(bounds[index] + 1 for index in subset)
            result += (-1) ** subset_size * _stars_and_bars(residual)
    return result == p["correct_count"]


def _composition_independent_v2(case: HardChallengeCaseV2) -> bool:
    p = case.parameters
    counts = [0] * (p["total"] + 1)
    counts[0] = 1
    for bound in (p["b1"], p["b2"], p["b3"]):
        updated = [0] * (p["total"] + 1)
        for subtotal, count in enumerate(counts):
            for value in range(min(bound, p["total"] - subtotal) + 1):
                updated[subtotal + value] += count
        counts = updated
    return counts[p["total"]] == p["correct_count"]


def _composition_witness_v2(case: HardChallengeCaseV2, payload: Mapping[str, Any]) -> bool:
    p = case.parameters
    faulty_overlap, faulty_count = _composition_faulty_values_v2(case)
    assignment = tuple(payload["overlap_assignment"])
    overlap_points = [
        (x1, x2, p["total"] - x1 - x2)
        for x1 in range(p["b1"] + 1, p["total"] + 1)
        for x2 in range(p["b2"] + 1, p["total"] + 1)
        if 0 <= p["total"] - x1 - x2 <= p["b3"]
    ]
    return (
        overlap_points == [assignment]
        and payload["true_overlap"] == len(overlap_points) == p["true_overlap"]
        and payload["faulty_overlap"] == faulty_overlap
        and payload["correct_count"] == p["correct_count"]
        and payload["faulty_count"] == faulty_count
        and faulty_count != p["correct_count"]
    )


def _path_edges_v2(case: HardChallengeCaseV2) -> tuple[tuple[str, str, int], ...]:
    p = case.parameters
    return (
        ("s", "a", p["sa"]),
        ("s", "b", p["sb"]),
        ("b", "a", p["ba"]),
        ("a", "t", p["at"]),
        ("b", "t", p["bt"]),
        ("s", "t", p["st"]),
    )


def _path_primary_v2(case: HardChallengeCaseV2) -> bool:
    distances: dict[str, int | None] = {vertex: None for vertex in ("s", "a", "b", "t")}
    distances["s"] = 0
    for _ in range(3):
        changed = False
        for left, right, weight in _path_edges_v2(case):
            if distances[left] is None:
                continue
            candidate = distances[left] + weight
            if distances[right] is None or candidate < distances[right]:
                distances[right] = candidate
                changed = True
        if not changed:
            break
    return (
        distances["a"] == case.parameters["true_a"] and distances["t"] == case.parameters["true_t"]
    )


def _path_independent_v2(case: HardChallengeCaseV2) -> bool:
    adjacency: dict[str, list[tuple[str, int]]] = {vertex: [] for vertex in ("s", "a", "b", "t")}
    for left, right, weight in _path_edges_v2(case):
        adjacency[left].append((right, weight))
    lengths: list[int] = []

    def visit(vertex: str, seen: frozenset[str], length: int) -> None:
        if vertex == "t":
            lengths.append(length)
            return
        for right, weight in adjacency[vertex]:
            if right not in seen:
                visit(right, seen | {right}, length + weight)

    visit("s", frozenset({"s"}), 0)
    return min(lengths) == case.parameters["true_t"]


def _path_witness_v2(case: HardChallengeCaseV2, payload: Mapping[str, Any]) -> bool:
    p = case.parameters
    weights = tuple(payload["edge_weights"])
    _, faulty_t = _path_faulty_values_v2(case)
    return (
        tuple(payload["path"]) == ("s", "b", "a", "t")
        and weights == (p["sb"], p["ba"], p["at"])
        and sum(weights) == payload["path_length"] == p["true_t"]
        and payload["faulty_distance"] == faulty_t
        and p["true_t"] != faulty_t
    )


def _shifted_power_coefficients_v2(degree: int, shift: int) -> tuple[int, ...]:
    return tuple(
        math.comb(degree, power) * shift ** (degree - power) for power in range(degree + 1)
    )


def _second_difference_coefficients_v2(degree: int) -> tuple[int, ...]:
    at_two = _shifted_power_coefficients_v2(degree, 2)
    at_one = _shifted_power_coefficients_v2(degree, 1)
    at_zero = _shifted_power_coefficients_v2(degree, 0)
    coefficients = [
        at_two[index] - 2 * at_one[index] + at_zero[index] for index in range(degree + 1)
    ]
    while len(coefficients) > 1 and coefficients[-1] == 0:
        coefficients.pop()
    return tuple(coefficients)


def _recurrence_primary_v2(case: HardChallengeCaseV2) -> bool:
    p = case.parameters
    coefficient = Fraction(p["coefficient_numerator"], p["coefficient_denominator"])
    delta = _second_difference_coefficients_v2(p["multiplicity"])
    scaled = tuple(coefficient * p["r"] * p["r"] * value for value in delta)
    return delta == (2,) and scaled == (Fraction(1),)


def _recurrence_independent_v2(case: HardChallengeCaseV2) -> bool:
    p = case.parameters
    r = p["r"]
    characteristic_at_r = r * r - 2 * r * r + r * r
    derivative_at_r = 2 * r - 2 * r
    second_derivative = 2
    coefficient = Fraction(p["coefficient_numerator"], p["coefficient_denominator"])
    return (
        p["multiplicity"] == 2
        and characteristic_at_r == 0
        and derivative_at_r == 0
        and second_derivative == 2
        and coefficient * second_derivative * r * r == 1
    )


def _recurrence_witness_v2(case: HardChallengeCaseV2, payload: Mapping[str, Any]) -> bool:
    p = case.parameters
    coefficient = Fraction(p["coefficient_numerator"], p["coefficient_denominator"])
    faulty_degree = _recurrence_faulty_degree_v2(case)
    faulty_delta = _second_difference_coefficients_v2(faulty_degree)
    faulty_residual = coefficient * p["r"] * p["r"] * faulty_delta[0]
    return (
        faulty_degree in (1, 3)
        and _parse_fraction(payload["faulty_residual"], "faulty residual") == faulty_residual
        and faulty_residual != 1
        and payload["required_residual"] == 1
        and payload["index"] == 0
        and _parse_fraction(payload["particular_coefficient"], "particular coefficient")
        == coefficient
    )


_PRIMARY_ORACLES_V2 = {
    FamilyV2.GENERALIZED_CRT_PERIOD: _crt_primary_v2,
    FamilyV2.BOUNDED_COMPOSITION_OVERLAP: _composition_primary_v2,
    FamilyV2.NEGATIVE_EDGE_FINALIZATION: _path_primary_v2,
    FamilyV2.FORCED_RECURRENCE_RESONANCE: _recurrence_primary_v2,
}

_INDEPENDENT_ORACLES_V2 = {
    FamilyV2.GENERALIZED_CRT_PERIOD: _crt_independent_v2,
    FamilyV2.BOUNDED_COMPOSITION_OVERLAP: _composition_independent_v2,
    FamilyV2.NEGATIVE_EDGE_FINALIZATION: _path_independent_v2,
    FamilyV2.FORCED_RECURRENCE_RESONANCE: _recurrence_independent_v2,
}

_WITNESS_ORACLES_V2 = {
    FamilyV2.GENERALIZED_CRT_PERIOD: _crt_witness_v2,
    FamilyV2.BOUNDED_COMPOSITION_OVERLAP: _composition_witness_v2,
    FamilyV2.NEGATIVE_EDGE_FINALIZATION: _path_witness_v2,
    FamilyV2.FORCED_RECURRENCE_RESONANCE: _recurrence_witness_v2,
}


def _family_primary_verification(case: HardChallengeCaseV2) -> bool:
    return _PRIMARY_ORACLES_V2[case.family](case)


def _family_independent_verification(case: HardChallengeCaseV2) -> bool:
    return _INDEPENDENT_ORACLES_V2[case.family](case)


def _family_witness_verification(case: HardChallengeCaseV2, payload: Mapping[str, Any]) -> bool:
    return _WITNESS_ORACLES_V2[case.family](case, payload)


def _verify_universal_witness_v2(case: HardChallengeCaseV2, witness: WitnessSubmissionV2) -> bool:
    try:
        p = case.parameters
        if case.family is FamilyV2.GENERALIZED_CRT_PERIOD:
            if witness.code is not UniversalWitnessCodeV2.SOLUTION_PAIR or len(witness.values) != 1:
                return False
            first, second = witness.values[0].as_integer_vector()
            claimed_period = _crt_faulty_period_v2(case)
            claim_refuted = claimed_period != p["true_period"] and (
                (claimed_period > p["true_period"] and second < claimed_period)
                or (
                    claimed_period < p["true_period"]
                    and (claimed_period % p["m1"] != 0 or claimed_period % p["m2"] != 0)
                )
            )
            return (
                first == p["x0"]
                and second == first + p["true_period"]
                and claim_refuted
                and all(
                    value % modulus == residue
                    for value in (first, second)
                    for modulus, residue in ((p["m1"], p["r1"]), (p["m2"], p["r2"]))
                )
            )
        if case.family is FamilyV2.BOUNDED_COMPOSITION_OVERLAP:
            if (
                witness.code is not UniversalWitnessCodeV2.FEASIBLE_ASSIGNMENT
                or len(witness.values) != 2
            ):
                return False
            assignment = witness.values[0].as_integer_vector()
            count = witness.values[1].as_fraction()
            return (
                assignment == (p["b1"] + 1, p["b2"] + 1, 0)
                and sum(assignment) == p["total"]
                and count == p["correct_count"]
                and _composition_primary_v2(case)
                and _composition_independent_v2(case)
            )
        if case.family is FamilyV2.NEGATIVE_EDGE_FINALIZATION:
            if witness.code is not UniversalWitnessCodeV2.DIRECTED_PATH or len(witness.values) != 2:
                return False
            path = witness.values[0].as_symbol_vector()
            length = witness.values[1].as_fraction()
            _, faulty_t = _path_faulty_values_v2(case)
            return path == ("s", "b", "a", "t") and length == p["true_t"] and length != faulty_t
        if (
            witness.code is not UniversalWitnessCodeV2.POLYNOMIAL_IDENTITY
            or len(witness.values) != 4
        ):
            return False
        coefficient = witness.values[0].as_fraction()
        delta = witness.values[1].as_integer_vector()
        scaled = witness.values[2].as_integer_vector()
        faulty = witness.values[3].as_fraction()
        expected_coefficient = Fraction(p["coefficient_numerator"], p["coefficient_denominator"])
        faulty_degree = _recurrence_faulty_degree_v2(case)
        faulty_delta = _second_difference_coefficients_v2(faulty_degree)
        expected_faulty = coefficient * p["r"] * p["r"] * faulty_delta[0]
        return (
            coefficient == expected_coefficient
            and delta == _second_difference_coefficients_v2(2) == (2,)
            and scaled == (1,)
            and tuple(coefficient * p["r"] * p["r"] * value for value in delta) == (Fraction(1),)
            and faulty_degree in (1, 3)
            and faulty == expected_faulty
            and faulty != 1
        )
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return False


def _module_source_digest_v2() -> str:
    source = Path(__file__).read_bytes()
    if not source or len(source) > 2_000_000:
        raise ValueError("verifier source artifact exceeds its byte budget")
    return "sha256:" + hashlib.sha256(source).hexdigest()


def _callable_code_digest_v2(value: Any) -> str:
    function = getattr(value, "__func__", value)
    if not isinstance(function, FunctionType):
        return content_digest_v2({"invalid_callable_type": type(value).__name__[:64]})

    def code_payload(code: Any) -> dict[str, Any]:
        constants: list[Any] = []
        for constant in code.co_consts:
            if hasattr(constant, "co_code"):
                constants.append({"code": code_payload(constant)})
            elif isinstance(constant, bytes):
                constants.append({"bytes": constant.hex()})
            elif isinstance(constant, tuple):
                constants.append({"tuple": [repr(item) for item in constant]})
            elif isinstance(constant, str | int | float | bool) or constant is None:
                constants.append({"literal": constant})
            else:
                constants.append({"type": type(constant).__name__})
        return {
            "argcount": code.co_argcount,
            "bytecode": code.co_code.hex(),
            "cellvars": list(code.co_cellvars),
            "constants": constants,
            "flags": code.co_flags,
            "freevars": list(code.co_freevars),
            "kwonlyargcount": code.co_kwonlyargcount,
            "names": list(code.co_names),
            "posonlyargcount": code.co_posonlyargcount,
            "varnames": list(code.co_varnames),
        }

    return content_digest_v2(
        {"code": code_payload(function.__code__), "qualname": function.__qualname__}
    )


def _current_family_oracle_digests_v2(
    family: FamilyV2,
) -> tuple[str, str, str]:
    return (
        _callable_code_digest_v2(_PRIMARY_ORACLES_V2[family]),
        _callable_code_digest_v2(_INDEPENDENT_ORACLES_V2[family]),
        _callable_code_digest_v2(_WITNESS_ORACLES_V2[family]),
    )


def _loaded_verifier_artifact_digest_v2() -> str:
    callables = {
        "evaluate_expression": _evaluate_expression_v2,
        "execute_program": execute_semantic_program_v2,
        "normalize_math_value": NormalizedMathValueV2.from_dict,
        "normalize_repair": RepairSubmissionV2.from_dict,
        "normalize_solver_output": SolverOutputV2.from_dict,
        "normalize_solver_output_json": SolverOutputV2.from_json,
        "normalize_witness": WitnessSubmissionV2.from_dict,
        "strict_json_parser": strict_json_loads_v2,
        "render_expression": _render_expression_v2,
        "render_transition": _transition_display_v2,
        "universal_witness": _verify_universal_witness_v2,
        "verify_repair": HardChallengeCaseV2.verify_repair,
        "verify_solver_output": HardChallengeCaseV2.verify_solver_output,
        "verify_witness": HardChallengeCaseV2.verify_witness,
    }
    for family in FAMILY_ORDER_V2:
        code = _FAMILY_CODES[family].lower()
        callables[f"{code}_primary"] = _PRIMARY_ORACLES_V2[family]
        callables[f"{code}_independent"] = _INDEPENDENT_ORACLES_V2[family]
        callables[f"{code}_witness"] = _WITNESS_ORACLES_V2[family]
    return content_digest_v2(
        {
            "callables": {
                name: _callable_code_digest_v2(value) for name, value in sorted(callables.items())
            },
            "interpreter_schema": INTERPRETER_SCHEMA_V2,
            "schema": VERIFIER_ARTIFACT_SCHEMA_V2,
        }
    )


def _verifier_binding_status_v2(
    case: HardChallengeCaseV2,
) -> tuple[bool, tuple[str, str]]:
    try:
        source = _module_source_digest_v2()
        artifact = _loaded_verifier_artifact_digest_v2()
        family = _current_family_oracle_digests_v2(case.family)
        passed = (
            source == case.verifier_source_digest
            and artifact == case.verifier_artifact_digest
            and family
            == (
                case.primary_oracle_digest,
                case.independent_oracle_digest,
                case.witness_oracle_digest,
            )
            and case.interpreter_schema == INTERPRETER_SCHEMA_V2
        )
        return passed, (source, artifact)
    except Exception:
        failed = content_digest_v2({"binding": "unavailable"})
        return False, (failed, failed)


def certify_case_v2(case: HardChallengeCaseV2) -> bool:
    return certification_report_v2(case).passed


def certification_report_v2(case: HardChallengeCaseV2) -> CertificationReportV2:
    if type(case) is not HardChallengeCaseV2:
        raise ValueError("certification requires a HardChallengeCaseV2")
    faulty = case.trace(faulty=True)
    differences = [
        number
        for number, (clean_line, faulty_line) in enumerate(
            zip(case.clean_trace, faulty, strict=True), 1
        )
        if clean_line != faulty_line
    ]
    structural = (
        differences == [case.faulty_line]
        and case.repair_replacement == case.clean_trace[case.faulty_line - 1]
        and case.replay(case.clean_trace).accepted
        and not case.replay(faulty).accepted
        and case.clean_trace == case.semantic_program.render(template=case.template, faulty=False)
        and faulty == case.semantic_program.render(template=case.template, faulty=True)
    )
    expected = generate_development_case_v2(case.family, case.seed)
    deterministic = case.content_payload() == expected.content_payload()
    artifact_binding, observed = _verifier_binding_status_v2(case)
    primary = artifact_binding and _family_primary_verification(case)
    independent = artifact_binding and _family_independent_verification(case)
    witness = artifact_binding and _family_witness_verification(case, case.witness.payload)
    clean_execution = execute_semantic_program_v2(case.semantic_program, faulty=False)
    faulty_execution = execute_semantic_program_v2(case.semantic_program, faulty=True)
    semantic_clean = (
        clean_execution.completed
        and clean_execution.prefix_valid
        and clean_execution.root_valid
        and clean_execution.suffix_valid
        and clean_execution.invalid_lines == ()
    )
    semantic_faulty_root_rejected = (
        faulty_execution.completed
        and faulty_execution.prefix_valid
        and not faulty_execution.root_valid
        and faulty_execution.invalid_lines == (case.faulty_line,)
    )
    semantic_clean_suffix = clean_execution.completed and clean_execution.suffix_valid
    semantic_faulty_suffix = faulty_execution.completed and faulty_execution.suffix_valid
    root_rejected = semantic_faulty_root_rejected and (
        case.semantic_program.faulty_root.declared != case.semantic_program.clean_root.declared
    )
    evidence = content_digest_v2(
        {
            "artifact_binding_passed": artifact_binding,
            "case_digest": case.digest(),
            "clean_final_state_digest": clean_execution.final_state_digest,
            "deterministic_binding_passed": deterministic,
            "faulty_final_state_digest": faulty_execution.final_state_digest,
            "independent_oracle_digest": case.independent_oracle_digest,
            "independent_verifier_passed": independent,
            "interpreter_schema": case.interpreter_schema,
            "observed_artifact_digest": observed[1],
            "observed_source_digest": observed[0],
            "primary_oracle_digest": case.primary_oracle_digest,
            "primary_verifier_passed": primary,
            "root_fault_rejected": root_rejected,
            "semantic_clean_passed": semantic_clean,
            "semantic_clean_suffix_passed": semantic_clean_suffix,
            "semantic_faulty_root_rejected": semantic_faulty_root_rejected,
            "semantic_faulty_suffix_passed": semantic_faulty_suffix,
            "structural_passed": structural,
            "verifier_artifact_digest": case.verifier_artifact_digest,
            "verifier_source_digest": case.verifier_source_digest,
            "witness_oracle_digest": case.witness_oracle_digest,
            "witness_passed": witness,
        }
    )
    return CertificationReportV2(
        structural_passed=structural,
        deterministic_binding_passed=deterministic,
        artifact_binding_passed=artifact_binding,
        primary_verifier_passed=primary,
        independent_verifier_passed=independent,
        witness_passed=witness,
        root_fault_rejected=root_rejected,
        semantic_clean_passed=semantic_clean,
        semantic_faulty_root_rejected=semantic_faulty_root_rejected,
        semantic_clean_suffix_passed=semantic_clean_suffix,
        semantic_faulty_suffix_passed=semantic_faulty_suffix,
        evidence_digest=evidence,
    )


PINNED_VERIFIER_SOURCE_DIGEST_V2 = _module_source_digest_v2()
PINNED_FAMILY_ORACLE_DIGESTS_V2 = MappingProxyType(
    {family: _current_family_oracle_digests_v2(family) for family in FAMILY_ORDER_V2}
)
PINNED_VERIFIER_ARTIFACT_DIGEST_V2 = _loaded_verifier_artifact_digest_v2()
