"""Sealed, role-separated challenge cases for causal math-brain evaluation.

The module is intentionally model-free.  It builds synthetic cases from a strict
descriptor, replays every gold/faulty replacement with exact arithmetic, commits
ordered banks to Merkle manifests, and renders the only solver-facing payload.
Holdout records are evaluator data: ordinary improvement code receives only a
``DevelopmentWorkbench`` and a commitment to the unopened holdout manifest.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .benchmark import SolverView


CONFIG_SCHEMA = "org.math-research-brain/challenge-bank-config"
CONFIG_SCHEMA_VERSION = 1
CASE_SCHEMA = "org.math-research-brain/challenge-case"
CASE_SCHEMA_VERSION = 1
CARD_SCHEMA = "org.math-research-brain/reference-card"
CARD_SCHEMA_VERSION = 1
MANIFEST_SCHEMA = "org.math-research-brain/challenge-manifest"
MANIFEST_SCHEMA_VERSION = 1
SEAL_SCHEMA = "org.math-research-brain/challenge-split-seal"
SEAL_SCHEMA_VERSION = 1
BANK_SEAL_SCHEMA = "org.math-research-brain/challenge-bank-seal"
BANK_SEAL_SCHEMA_VERSION = 1
RETRIEVAL_RECORD_SCHEMA = "org.math-research-brain/retrieval-audit-digests"
RETRIEVAL_RECORD_SCHEMA_VERSION = 1
GENERATOR_VERSION = "sealed-challenge-v1"
HOLDOUT_PAYLOAD_SCHEMA = "org.math-research-brain/challenge-holdout-payload"
HOLDOUT_PAYLOAD_SCHEMA_VERSION = 1
MERKLE_ALGORITHM = "rfc6962-sha256"

MAX_CONFIG_BYTES = 512_000
MAX_SOLVER_VIEW_BYTES = 128_000
MAX_TEXT_BYTES = 16_384
MAX_FINITE_COUNT_BOUND = 1_000_000
MAX_INDUCTION_MODULUS = 256
MAX_CONGRUENCE_MODULUS = 10_000
PAIR_COUNT = 10

FAMILY_ORDER = (
    "quantifier_scope",
    "illegal_cancellation",
    "branch_loss",
    "induction_reachability",
    "index_region",
    "norm_spectral_radius",
    "local_global",
    "feasible_boundary",
    "asymptotic_scale",
    "implication_equivalence",
    "modular_cancellation",
    "candidate_validation",
    "overlap_omission",
    "missing_coprimality",
    "linear_invariant",
    "independence_assumption",
    "sign_case",
    "initial_data",
    "aggregate_pointwise",
    "degree_threshold",
)


class Split(StrEnum):
    DEVELOPMENT = "development"
    HOLDOUT = "holdout"


class Difficulty(StrEnum):
    MEDIUM = "medium"
    HARD = "hard"


class TraceVariant(StrEnum):
    CLEAN = "clean"
    FAULTY = "faulty"


class NormalizerKind(StrEnum):
    BOOLEAN = "boolean"
    INTEGER = "integer"
    INTEGER_LIST = "integer_list"
    INTEGER_PAIR = "integer_pair"
    INTERVAL_LIST = "interval_list"
    RANK_NULLITY = "rank_nullity"
    RATIONAL = "rational"
    RESIDUE_SET = "residue_set"


class WitnessVerifierKind(StrEnum):
    AGGREGATE_COUNTEREXAMPLE = "aggregate_pointwise-v1"
    CONGRUENCE_DIVISIBILITY = "congruence-divisibility-v1"
    DEGREE_SEQUENCE = "degree-sequence-v1"
    DENOMINATOR_DOMAIN = "denominator-domain-v1"
    DIAGONAL = "diagonal-v1"
    FEASIBLE_SUPPORT_POINT = "feasible-support-point-v1"
    FINITE_PROBABILITY = "finite-probability-v1"
    FINITE_SUM_COUNT = "finite-sum-count-v1"
    INDUCTION_RESIDUE = "induction-residue-v1"
    LINEAR_KERNEL_RANK_NULLITY = "linear-kernel-rank-nullity-v1"
    MATRIX_VECTOR_NORM = "matrix-vector-norm-v1"
    ONE_SIDED_SLOPE = "one-sided-slope-v1"
    POLYNOMIAL_EVAL = "polynomial-eval-v1"
    PROBABILITY_IDENTITY = "probability-identity-v1"
    RECURRENCE_PREFIX = "recurrence-prefix-v1"
    SCALAR_EXACT_RATIONAL = "scalar-exact-rational-v1"
    TWO_POINT_SQUARED_RATIO = "two-point-squared-ratio-v1"


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_bytes(value: Any) -> bytes:
    try:
        return canonical_json(value).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("value is not valid UTF-8") from exc


def content_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def _sha256_bytes(value: bytes) -> bytes:
    return hashlib.sha256(value).digest()


def _strict_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object with string keys")
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"invalid {label} keys; missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object with string keys")
    return value


def _sequence(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def _string(value: Any, label: str, *, max_bytes: int = MAX_TEXT_BYTES) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8") from exc
    if size > max_bytes:
        raise ValueError(f"{label} exceeds the {max_bytes}-byte budget")
    return value


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if abs(value) > 10**12:
        raise ValueError(f"{label} exceeds the integer budget")
    return value


def _bounded_integer(
    value: Any, label: str, *, minimum: int, maximum: int
) -> int:
    result = _integer(value, label)
    if not minimum <= result <= maximum:
        raise ValueError(f"{label} must be in [{minimum}, {maximum}]")
    return result


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _sha256_string(value: Any, label: str) -> str:
    text = _string(value, label, max_bytes=71)
    if len(text) != 71 or not text.startswith("sha256:"):
        raise ValueError(f"{label} must be a SHA-256 digest")
    try:
        bytes.fromhex(text[7:])
    except ValueError as exc:
        raise ValueError(f"{label} must be hexadecimal") from exc
    return text


def _verify_supplied_content_digest(
    value: Mapping[str, Any], *, label: str, mismatch_message: str
) -> str:
    supplied = _sha256_string(value.get("content_digest"), f"{label} content digest")
    expected = content_digest(
        {key: item for key, item in value.items() if key != "content_digest"}
    )
    if not hmac.compare_digest(supplied, expected):
        raise ValueError(mismatch_message)
    return supplied


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _reject_nonfinite_json_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {token}")


def _finite_json_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise ValueError(f"non-finite JSON number is not allowed: {token}")
    return value


def strict_json_loads(raw: str, *, max_bytes: int = MAX_CONFIG_BYTES) -> Any:
    if not isinstance(raw, str):
        raise ValueError("JSON input must be text")
    try:
        encoded = raw.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("JSON input must be valid UTF-8") from exc
    if not encoded or len(encoded) > max_bytes:
        raise ValueError(f"JSON input must contain between 1 and {max_bytes} bytes")
    try:
        return json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_json_constant,
            parse_float=_finite_json_float,
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc.msg}") from exc
    except RecursionError as exc:
        raise ValueError("invalid JSON: nesting budget exceeded") from exc


def _fraction(value: Any, label: str) -> Fraction:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a rational number")
    if isinstance(value, int):
        return Fraction(_integer(value, label), 1)
    text = _string(value, label, max_bytes=128)
    if not re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:/[1-9][0-9]*)?", text):
        raise ValueError(f"{label} must be a canonical integer or fraction")
    result = Fraction(text)
    if str(result) != text:
        raise ValueError(f"{label} must be reduced and canonical")
    return result


def _fraction_token(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else str(value)


def _int_list(value: Any, label: str) -> tuple[int, ...]:
    values = _sequence(value, label)
    if len(values) > 256:
        raise ValueError(f"{label} exceeds the 256-item budget")
    return tuple(_integer(item, f"{label} item") for item in values)


def _matrix(value: Any, label: str) -> tuple[tuple[Fraction, ...], ...]:
    rows = _sequence(value, label)
    if not rows or len(rows) > 16:
        raise ValueError(f"{label} must have between 1 and 16 rows")
    parsed = tuple(
        tuple(_fraction(item, f"{label} entry") for item in _sequence(row, f"{label} row"))
        for row in rows
    )
    if not parsed[0] or len(parsed[0]) > 16 or any(len(row) != len(parsed[0]) for row in parsed):
        raise ValueError(f"{label} must be a non-empty rectangular matrix")
    return parsed


def _poly(coefficients: Sequence[Fraction], x: Fraction) -> Fraction:
    result = Fraction(0)
    for coefficient in reversed(coefficients):
        result = result * x + coefficient
    return result


def _lattice_triangle_count(upper: int) -> int:
    return upper * (upper + 1) // 2


def _union_multiples_count(*, a: int, b: int, limit: int) -> int:
    return limit // a + limit // b - limit // math.lcm(a, b)


def _poly_from_raw(value: Any, label: str) -> tuple[Fraction, ...]:
    coefficients = _sequence(value, label)
    if not coefficients or len(coefficients) > 32:
        raise ValueError(f"{label} must have between 1 and 32 coefficients")
    return tuple(_fraction(item, f"{label} coefficient") for item in coefficients)


def _numbered_trace(lines: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(lines, tuple) or not 1 <= len(lines) <= 64:
        raise ValueError("trace must be a tuple with between 1 and 64 lines")
    for number, line in enumerate(lines, 1):
        text = _string(line, "trace line")
        if not text.startswith(f"{number}. "):
            raise ValueError("trace lines must be consecutively numbered")
    return tuple(lines)


@dataclass(frozen=True, slots=True)
class ReferenceCard:
    card_id: str
    family: str
    title: str
    guidance: str
    retrieval_cues: tuple[str, ...]
    schema: str = CARD_SCHEMA
    schema_version: int = CARD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not re.fullmatch(r"C(?:0[1-9]|1[0-9]|20)", self.card_id):
            raise ValueError("reference card id must be C01 through C20")
        if self.family not in FAMILY_ORDER:
            raise ValueError("reference card family is not registered")
        _string(self.title, "reference card title", max_bytes=256)
        _string(self.guidance, "reference card guidance")
        if not isinstance(self.retrieval_cues, tuple) or not self.retrieval_cues:
            raise ValueError("reference card cues must be a non-empty tuple")
        for cue in self.retrieval_cues:
            _string(cue, "reference card cue", max_bytes=128)
        if self.schema != CARD_SCHEMA or self.schema_version != CARD_SCHEMA_VERSION:
            raise ValueError("unsupported reference-card schema")
        combined = " ".join((self.title, self.guidance, *self.retrieval_cues))
        if re.search(r"\b[DH](?:0[1-9]|1[0-9]|20)\b", combined):
            raise ValueError("reference cards must not contain case identifiers")
        if re.search(r"\b-?[0-9]+(?:/[0-9]+)?\b", combined):
            raise ValueError("reference cards must not contain case constants")

    def content_payload(self) -> dict[str, Any]:
        return {
            "card_id": self.card_id,
            "family": self.family,
            "guidance": self.guidance,
            "retrieval_cues": list(self.retrieval_cues),
            "schema": self.schema,
            "schema_version": self.schema_version,
            "title": self.title,
        }

    def digest(self) -> str:
        return content_digest(self.content_payload())

    def to_dict(self) -> dict[str, Any]:
        value = self.content_payload()
        value["content_digest"] = self.digest()
        return value


@dataclass(frozen=True, slots=True)
class ReplacementNormalizer:
    kind: NormalizerKind
    prefix: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, NormalizerKind):
            raise ValueError("replacement normalizer kind is invalid")
        _string(self.prefix, "replacement prefix", max_bytes=128)

    def normalize(self, line: str) -> Any:
        text = _string(line, "replacement line")
        if not text.startswith(self.prefix):
            raise ValueError("replacement line does not match its registered normalizer")
        raw = text[len(self.prefix) :]
        value = strict_json_loads(raw, max_bytes=16_384)
        return self.normalize_json_value(value)

    def normalize_json_value(self, value: Any) -> Any:
        """Convert one JSON-decoded claim into its exact pure-replay value."""

        if self.kind is NormalizerKind.BOOLEAN:
            return _boolean(value, "replacement claim")
        if self.kind is NormalizerKind.INTEGER:
            return _integer(value, "replacement claim")
        if self.kind in (NormalizerKind.INTEGER_LIST, NormalizerKind.RESIDUE_SET):
            result = _int_list(value, "replacement claim")
            if self.kind is NormalizerKind.RESIDUE_SET and (
                result != tuple(sorted(result)) or len(result) != len(set(result))
            ):
                raise ValueError("residue-set replacements must be sorted and unique")
            return result
        if self.kind is NormalizerKind.INTEGER_PAIR:
            result = _int_list(value, "replacement claim")
            if len(result) != 2:
                raise ValueError("integer-pair replacements must have exactly two entries")
            return result
        if self.kind is NormalizerKind.INTERVAL_LIST:
            result = _sequence(value, "replacement claim")
            if not all(isinstance(item, str) and item for item in result):
                raise ValueError("interval replacements must be strings")
            return tuple(result)
        if self.kind is NormalizerKind.RANK_NULLITY:
            mapping = _mapping(value, "replacement claim")
            _strict_keys(mapping, {"nullity", "rank"}, "rank-nullity replacement")
            return {
                "nullity": _integer(mapping["nullity"], "claimed nullity"),
                "rank": _integer(mapping["rank"], "claimed rank"),
            }
        if self.kind is NormalizerKind.RATIONAL:
            return _fraction(value, "replacement claim")
        raise AssertionError("unreachable normalizer kind")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "prefix": self.prefix}


@dataclass(frozen=True, slots=True)
class WitnessSpec:
    verifier: WitnessVerifierKind
    params: dict[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.verifier, WitnessVerifierKind):
            raise ValueError("witness verifier is invalid")
        if not isinstance(self.params, dict):
            raise ValueError("witness parameters must be an object")
        canonical_bytes(self.params)

    def to_dict(self) -> dict[str, Any]:
        return {"params": self.params, "verifier": self.verifier.value}


@dataclass(frozen=True, slots=True)
class ReplayResult:
    accepted: bool
    first_invalid_line: int | None
    repair_replacement: str | None
    verifier: str


@dataclass(frozen=True, slots=True)
class ChallengeCase:
    case_id: str
    split: Split
    index: int
    family: str
    domain: str
    difficulty: Difficulty
    prompt: str
    retrieval_query: str
    clean_trace: tuple[str, ...]
    faulty_line: int
    faulty_replacement: str
    repair_replacement: str
    witness: WitnessSpec
    replacement_normalizer: ReplacementNormalizer
    gold_card: str
    schema: str = CASE_SCHEMA
    schema_version: int = CASE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.split, Split) or not isinstance(self.difficulty, Difficulty):
            raise ValueError("case split and difficulty must be registered enum values")
        if not 0 <= self.index < len(FAMILY_ORDER):
            raise ValueError("case index must be in the twenty-family bank")
        expected_id = ("D" if self.split is Split.DEVELOPMENT else "H") + f"{self.index + 1:02d}"
        if self.case_id != expected_id:
            raise ValueError("case id does not match split and family index")
        if self.family != FAMILY_ORDER[self.index]:
            raise ValueError("case family does not match the registered order")
        if self.gold_card != f"C{self.index + 1:02d}":
            raise ValueError("case gold card does not match the registered family card")
        _string(self.domain, "case domain", max_bytes=128)
        _string(self.prompt, "case prompt")
        _string(self.retrieval_query, "case retrieval query")
        _numbered_trace(self.clean_trace)
        if not 1 <= self.faulty_line <= len(self.clean_trace):
            raise ValueError("fault line must identify one numbered trace line")
        _string(self.faulty_replacement, "faulty replacement")
        _string(self.repair_replacement, "repair replacement")
        if self.repair_replacement != self.clean_trace[self.faulty_line - 1]:
            raise ValueError("exact repair must restore the clean line")
        if self.faulty_replacement == self.repair_replacement:
            raise ValueError("faulty replacement must differ from its exact repair")
        expected_prefix = f"{self.faulty_line}. claim = "
        if self.replacement_normalizer.prefix != expected_prefix:
            raise ValueError("replacement normalizer must bind the fault line exactly")
        if self.schema != CASE_SCHEMA or self.schema_version != CASE_SCHEMA_VERSION:
            raise ValueError("unsupported challenge-case schema")

    def trace(self, variant: TraceVariant) -> tuple[str, ...]:
        if not isinstance(variant, TraceVariant):
            raise ValueError("trace variant is invalid")
        if variant is TraceVariant.CLEAN:
            return self.clean_trace
        result = list(self.clean_trace)
        result[self.faulty_line - 1] = self.faulty_replacement
        return tuple(result)

    @property
    def fault_line(self) -> int:
        return self.faulty_line

    @property
    def error_family(self) -> str:
        return self.family

    @property
    def clean_replacement_line(self) -> str:
        return self.repair_replacement

    def solver_view(self, use_faulty: bool) -> SolverView:
        """Return the repository's exact, shared solver payload type."""

        if not isinstance(use_faulty, bool):
            raise ValueError("use_faulty must be a boolean")
        variant = TraceVariant.FAULTY if use_faulty else TraceVariant.CLEAN
        return SolverView(prompt=self.prompt, numbered_solution=self.trace(variant))

    def certify(self) -> bool:
        return certify_case(self)

    def verify_repair(self, replacement_line: str) -> tuple[bool, str]:
        passed = replacement_line == self.repair_replacement
        if passed:
            try:
                candidate = self.replacement_normalizer.normalize(replacement_line)
                passed = verify_witness(self.witness, candidate)
            except ValueError:
                passed = False
        evidence = content_digest(
            {
                "case_digest": self.digest(),
                "kind": "repair-replay-v1",
                "passed": passed,
                "replacement_digest": content_digest({"replacement": replacement_line}),
                "verifier": self.witness.verifier.value,
            }
        )
        return passed, evidence

    def verify_witness(self, json_payload: Any) -> tuple[bool, str]:
        """Replay a JSON-bound witness after its registered exact normalization."""

        try:
            payload_digest = content_digest({"json_payload": json_payload})
        except (TypeError, ValueError):
            passed = False
            payload_digest = content_digest(
                {"json_payload_type": type(json_payload).__name__}
            )
        else:
            try:
                candidate = self.replacement_normalizer.normalize_json_value(json_payload)
                passed = verify_witness(self.witness, candidate)
            except (TypeError, ValueError):
                passed = False
        evidence = content_digest(
            {
                "case_digest": self.digest(),
                "kind": "json-witness-replay-v1",
                "passed": passed,
                "payload_digest": payload_digest,
                "verifier": self.witness.verifier.value,
            }
        )
        return passed, evidence

    def replay(self, trace: Sequence[str]) -> ReplayResult:
        candidate_trace = tuple(trace)
        if len(candidate_trace) != len(self.clean_trace):
            return ReplayResult(False, 1, self.repair_replacement, self.witness.verifier.value)
        for number, (actual, expected) in enumerate(zip(candidate_trace, self.clean_trace), 1):
            if number != self.faulty_line and actual != expected:
                return ReplayResult(False, number, expected, self.witness.verifier.value)
        try:
            candidate = self.replacement_normalizer.normalize(
                candidate_trace[self.faulty_line - 1]
            )
            accepted = verify_witness(self.witness, candidate)
        except ValueError:
            accepted = False
        return ReplayResult(
            accepted,
            None if accepted else self.faulty_line,
            None if accepted else self.repair_replacement,
            self.witness.verifier.value,
        )

    def content_payload(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "clean_trace": list(self.clean_trace),
            "difficulty": self.difficulty.value,
            "domain": self.domain,
            "family": self.family,
            "faulty_line": self.faulty_line,
            "faulty_replacement": self.faulty_replacement,
            "gold_card": self.gold_card,
            "index": self.index,
            "prompt": self.prompt,
            "repair_replacement": self.repair_replacement,
            "replacement_normalizer": self.replacement_normalizer.to_dict(),
            "retrieval_query": self.retrieval_query,
            "schema": self.schema,
            "schema_version": self.schema_version,
            "split": self.split.value,
            "witness": self.witness.to_dict(),
        }

    def digest(self) -> str:
        return content_digest(self.content_payload())

    def to_dict(self) -> dict[str, Any]:
        value = self.content_payload()
        value["content_digest"] = self.digest()
        return value


def _exact_params(params: Mapping[str, Any], expected: set[str], label: str) -> dict[str, Any]:
    value = _mapping(params, label)
    _strict_keys(value, expected, label)
    return value


def _rref_rank(matrix: tuple[tuple[Fraction, ...], ...]) -> int:
    work = [list(row) for row in matrix]
    row = 0
    columns = len(work[0])
    for column in range(columns):
        pivot = next((candidate for candidate in range(row, len(work)) if work[candidate][column]), None)
        if pivot is None:
            continue
        work[row], work[pivot] = work[pivot], work[row]
        scale = work[row][column]
        work[row] = [entry / scale for entry in work[row]]
        for other in range(len(work)):
            if other == row:
                continue
            factor = work[other][column]
            if factor:
                work[other] = [
                    left - factor * right for left, right in zip(work[other], work[row])
                ]
        row += 1
        if row == len(work):
            break
    return row


def _eval_rational_ast(node: Any, *, depth: int = 0) -> Fraction:
    if depth > 24:
        raise ValueError("rational expression exceeds depth budget")
    if isinstance(node, int) and not isinstance(node, bool):
        return Fraction(_integer(node, "rational literal"), 1)
    value = _mapping(node, "rational expression")
    _strict_keys(value, {"args", "op"}, "rational expression")
    op = _string(value["op"], "rational operation", max_bytes=16)
    args = _sequence(value["args"], "rational arguments")
    allowed_arity = {"add": 2, "div": 2, "mul": 2, "pow": 2, "sub": 2}
    if op not in allowed_arity or len(args) != allowed_arity[op]:
        raise ValueError("rational expression operation or arity is invalid")
    left = _eval_rational_ast(args[0], depth=depth + 1)
    right = _eval_rational_ast(args[1], depth=depth + 1)
    if op == "add":
        return left + right
    if op == "sub":
        return left - right
    if op == "mul":
        return left * right
    if op == "div":
        if not right:
            raise ValueError("division by zero in rational expression")
        return left / right
    if right.denominator != 1 or not 0 <= right.numerator <= 12:
        raise ValueError("rational powers require a small nonnegative integer exponent")
    result = left**right.numerator
    if result.numerator.bit_length() > 1024 or result.denominator.bit_length() > 1024:
        raise ValueError("rational expression exceeds bit budget")
    return result


def verify_witness(spec: WitnessSpec, candidate: Any) -> bool:
    """Replay one typed witness with exact, allowlisted operations only."""

    kind = spec.verifier
    params = spec.params
    if kind is WitnessVerifierKind.DIAGONAL:
        value = _exact_params(params, {"domain", "offset"}, "diagonal parameters")
        domain = _int_list(value["domain"], "diagonal domain")
        offset = _integer(value["offset"], "diagonal offset")
        return candidate == tuple(x + offset for x in domain)

    if kind is WitnessVerifierKind.DENOMINATOR_DOMAIN:
        value = _exact_params(
            params,
            {"candidate_pool", "lhs_den", "lhs_num", "rhs_den", "rhs_num"},
            "denominator parameters",
        )
        pool = _int_list(value["candidate_pool"], "candidate pool")
        lhs_num = _poly_from_raw(value["lhs_num"], "left numerator")
        lhs_den = _poly_from_raw(value["lhs_den"], "left denominator")
        rhs_num = _poly_from_raw(value["rhs_num"], "right numerator")
        rhs_den = _poly_from_raw(value["rhs_den"], "right denominator")
        roots = []
        for integer in pool:
            x = Fraction(integer)
            left_denominator = _poly(lhs_den, x)
            right_denominator = _poly(rhs_den, x)
            if not left_denominator or not right_denominator:
                continue
            if _poly(lhs_num, x) / left_denominator == _poly(rhs_num, x) / right_denominator:
                roots.append(integer)
        return candidate == tuple(sorted(set(roots)))

    if kind is WitnessVerifierKind.POLYNOMIAL_EVAL:
        value = _exact_params(
            params, {"candidate_pool", "coefficients", "target"}, "polynomial parameters"
        )
        coefficients = _poly_from_raw(value["coefficients"], "polynomial coefficients")
        target = _fraction(value["target"], "polynomial target")
        roots = tuple(
            x
            for x in _int_list(value["candidate_pool"], "polynomial candidate pool")
            if _poly(coefficients, Fraction(x)) == target
        )
        return candidate == tuple(sorted(set(roots)))

    if kind is WitnessVerifierKind.INDUCTION_RESIDUE:
        value = _exact_params(params, {"bases", "modulus", "step"}, "induction parameters")
        modulus = _bounded_integer(
            value["modulus"],
            "induction modulus",
            minimum=1,
            maximum=MAX_INDUCTION_MODULUS,
        )
        step = _bounded_integer(
            value["step"],
            "induction step",
            minimum=1,
            maximum=MAX_INDUCTION_MODULUS,
        )
        frontier = {base % modulus for base in _int_list(value["bases"], "induction bases")}
        reached = set(frontier)
        while frontier:
            residue = frontier.pop()
            successor = (residue + step) % modulus
            if successor not in reached:
                reached.add(successor)
                frontier.add(successor)
        return candidate == tuple(sorted(reached))

    if kind is WitnessVerifierKind.FINITE_SUM_COUNT:
        value = _mapping(params, "finite-count parameters")
        mode = value.get("mode")
        if mode == "lattice_triangle":
            _strict_keys(value, {"mode", "upper"}, "lattice-count parameters")
            upper = _bounded_integer(
                value["upper"],
                "lattice upper bound",
                minimum=1,
                maximum=MAX_FINITE_COUNT_BOUND,
            )
            expected = _lattice_triangle_count(upper)
        elif mode == "union_multiples":
            _strict_keys(value, {"a", "b", "limit", "mode"}, "union-count parameters")
            limit = _bounded_integer(
                value["limit"],
                "union limit",
                minimum=1,
                maximum=MAX_FINITE_COUNT_BOUND,
            )
            a = _bounded_integer(
                value["a"],
                "first divisor",
                minimum=1,
                maximum=MAX_FINITE_COUNT_BOUND,
            )
            b = _bounded_integer(
                value["b"],
                "second divisor",
                minimum=1,
                maximum=MAX_FINITE_COUNT_BOUND,
            )
            expected = _union_multiples_count(a=a, b=b, limit=limit)
        else:
            raise ValueError("unsupported finite-count mode")
        return candidate == expected

    if kind is WitnessVerifierKind.MATRIX_VECTOR_NORM:
        value = _exact_params(params, {"matrix"}, "matrix-norm parameters")
        matrix = _matrix(value["matrix"], "matrix")
        columns = len(matrix[0])
        gram = tuple(
            tuple(sum(row[i] * row[j] for row in matrix) for j in range(columns))
            for i in range(columns)
        )
        if any(gram[i][j] for i in range(columns) for j in range(columns) if i != j):
            raise ValueError("v1 matrix-norm witnesses require diagonal A-transpose-A")
        expected = max(gram[i][i] for i in range(columns))
        return isinstance(candidate, Fraction) and candidate == expected

    if kind is WitnessVerifierKind.TWO_POINT_SQUARED_RATIO:
        value = _exact_params(params, {"function", "left", "right"}, "two-point parameters")
        if value["function"] != "xy":
            raise ValueError("v1 two-point verifier supports only the xy polynomial")
        left = _int_list(value["left"], "left point")
        right = _int_list(value["right"], "right point")
        if len(left) != 2 or len(right) != 2:
            raise ValueError("two-point witness requires two planar points")
        numerator = Fraction((left[0] * left[1] - right[0] * right[1]) ** 2)
        denominator = Fraction(sum((a - b) ** 2 for a, b in zip(left, right)))
        if not denominator:
            raise ValueError("two-point witness requires distinct points")
        return isinstance(candidate, Fraction) and candidate == numerator / denominator

    if kind is WitnessVerifierKind.FEASIBLE_SUPPORT_POINT:
        value = _exact_params(params, {"objective", "points"}, "feasible-support parameters")
        objective = _int_list(value["objective"], "objective")
        points = tuple(_int_list(point, "feasible point") for point in _sequence(value["points"], "points"))
        if not points or any(len(point) != len(objective) for point in points):
            raise ValueError("feasible points must match the objective dimension")
        scores = [sum(weight * coordinate for weight, coordinate in zip(objective, point)) for point in points]
        best = max(scores)
        maximizers = tuple(point for point, score in zip(points, scores) if score == best)
        return candidate in maximizers

    if kind is WitnessVerifierKind.SCALAR_EXACT_RATIONAL:
        value = _exact_params(params, {"expression"}, "scalar-rational parameters")
        return isinstance(candidate, Fraction) and candidate == _eval_rational_ast(
            value["expression"]
        )

    if kind is WitnessVerifierKind.FINITE_PROBABILITY:
        value = _exact_params(params, {"event", "variables"}, "finite-probability parameters")
        variables = _integer(value["variables"], "truth-table variable count")
        if variables != 2 or value["event"] != "biconditional":
            raise ValueError("v1 finite-probability witness supports the two-variable biconditional")
        outcomes = [(left, right) for left in (False, True) for right in (False, True)]
        satisfying = sum(left == right for left, right in outcomes)
        return isinstance(candidate, Fraction) and candidate == Fraction(
            satisfying, len(outcomes)
        )

    if kind is WitnessVerifierKind.CONGRUENCE_DIVISIBILITY:
        value = _mapping(params, "congruence parameters")
        mode = value.get("mode")
        if mode == "linear_congruence":
            _strict_keys(value, {"a", "b", "mode", "modulus"}, "linear-congruence parameters")
            a = _integer(value["a"], "congruence coefficient")
            b = _integer(value["b"], "congruence target")
            modulus = _bounded_integer(
                value["modulus"],
                "congruence modulus",
                minimum=1,
                maximum=MAX_CONGRUENCE_MODULUS,
            )
            expected = tuple(x for x in range(modulus) if (a * x - b) % modulus == 0)
        elif mode == "divisibility_implication":
            _strict_keys(value, {"a", "b", "c", "mode"}, "divisibility parameters")
            a = _integer(value["a"], "divisor")
            b = _integer(value["b"], "first factor")
            c = _integer(value["c"], "second factor")
            if not a:
                raise ValueError("divisor must be nonzero")
            premise = (b * c) % a == 0
            expected = (not premise) or c % a == 0
        else:
            raise ValueError("unsupported congruence verifier mode")
        return candidate == expected

    if kind is WitnessVerifierKind.LINEAR_KERNEL_RANK_NULLITY:
        value = _exact_params(params, {"matrix"}, "linear parameters")
        matrix = _matrix(value["matrix"], "linear matrix")
        rank = _rref_rank(matrix)
        expected = {"nullity": len(matrix[0]) - rank, "rank": rank}
        return candidate == expected

    if kind is WitnessVerifierKind.PROBABILITY_IDENTITY:
        value = _exact_params(params, {"event", "outcomes"}, "probability parameters")
        if value["event"] != "all_zero":
            raise ValueError("unsupported probability event")
        outcomes = tuple(_int_list(outcome, "probability outcome") for outcome in _sequence(value["outcomes"], "outcomes"))
        if not outcomes or any(len(outcome) != len(outcomes[0]) for outcome in outcomes):
            raise ValueError("probability outcomes must be a non-empty rectangular table")
        favorable = sum(all(bit == 0 for bit in outcome) for outcome in outcomes)
        return isinstance(candidate, Fraction) and candidate == Fraction(
            favorable, len(outcomes)
        )

    if kind is WitnessVerifierKind.RECURRENCE_PREFIX:
        value = _exact_params(params, {"coefficients", "initials", "order"}, "recurrence parameters")
        order = _integer(value["order"], "recurrence order")
        coefficients = _int_list(value["coefficients"], "recurrence coefficients")
        initials = _int_list(value["initials"], "recurrence initials")
        if order <= 0 or len(coefficients) != order:
            raise ValueError("recurrence coefficient count must equal its positive order")
        return candidate == (len(initials) >= order)

    if kind is WitnessVerifierKind.AGGREGATE_COUNTEREXAMPLE:
        value = _exact_params(params, {"length", "target_sum"}, "aggregate parameters")
        length = _integer(value["length"], "aggregate length")
        target = _integer(value["target_sum"], "aggregate target")
        return (
            isinstance(candidate, tuple)
            and len(candidate) == length
            and sum(candidate) == target
            and any(entry != 0 for entry in candidate)
        )

    if kind is WitnessVerifierKind.DEGREE_SEQUENCE:
        value = _exact_params(params, {"degree"}, "degree parameters")
        degree = _integer(value["degree"], "polynomial degree")
        if degree < 0:
            raise ValueError("polynomial degree must be nonnegative")
        return candidate == degree + 1

    if kind is WitnessVerifierKind.ONE_SIDED_SLOPE:
        value = _exact_params(params, {"numerator", "right"}, "sign-case parameters")
        numerator = _fraction(value["numerator"], "sign numerator")
        right = _fraction(value["right"], "sign right side")
        if numerator <= 0 or right <= 0:
            raise ValueError("v1 sign witness requires positive coefficients")
        endpoint = numerator / right
        expected = (f"(0,{_fraction_token(endpoint)})",)
        return candidate == expected

    raise AssertionError("unreachable witness verifier")


@dataclass(frozen=True, slots=True)
class _CaseParts:
    domain: str
    prompt: str
    retrieval_query: str
    trace_prefix: tuple[str, str]
    normalizer: NormalizerKind
    clean_candidate: Any
    faulty_candidate: Any
    witness: WitnessSpec


def _claim_line(value: Any) -> str:
    return "3. claim = " + canonical_json(value)


def _case_parts(family: str, raw_params: Mapping[str, Any]) -> _CaseParts:
    p = _mapping(raw_params, f"{family} parameters")
    if family == "quantifier_scope":
        _strict_keys(p, {"domain", "offset"}, "quantifier parameters")
        domain = list(_int_list(p["domain"], "quantifier domain"))
        offset = _integer(p["offset"], "quantifier offset")
        clean = [x + offset for x in domain]
        faulty = [clean[0]] * len(clean)
        return _CaseParts(
            "logic",
            f"For each x in {domain}, choose y with y = x + {offset}. Review the proposed witness list in matching x order.",
            "Check quantifier order and whether a witness may depend on the quantified input.",
            ("1. Read the universal input before choosing its witness.", "2. Evaluate the relation separately at every listed input."),
            NormalizerKind.INTEGER_LIST,
            clean,
            faulty,
            WitnessSpec(WitnessVerifierKind.DIAGONAL, {"domain": domain, "offset": offset}),
        )
    if family == "illegal_cancellation":
        _strict_keys(p, {"left_root", "right_root"}, "cancellation parameters")
        a = _integer(p["left_root"], "left root")
        b = _integer(p["right_root"], "right root")
        if a == b:
            raise ValueError("cancellation roots must be distinct")
        pool = sorted({a - 1, a, b, b + 1})
        coefficients = [a * b, -(a + b), 1]
        return _CaseParts(
            "algebra",
            f"Solve (x - {a})(x - {b}) = 0 over the integers in {pool}. Audit the step that cancels a factor.",
            "Check the zero case and nonzero precondition before cancelling a factor.",
            ("1. A product is zero when at least one factor is zero.", "2. Test every candidate in the original product equation."),
            NormalizerKind.INTEGER_LIST,
            sorted([a, b]),
            [b],
            WitnessSpec(
                WitnessVerifierKind.DENOMINATOR_DOMAIN,
                {"candidate_pool": pool, "lhs_den": [1], "lhs_num": coefficients, "rhs_den": [1], "rhs_num": [0]},
            ),
        )
    if family == "branch_loss":
        _strict_keys(p, {"root"}, "branch parameters")
        root = _integer(p["root"], "branch root")
        if root <= 0:
            raise ValueError("branch root must be positive")
        pool = [-root, 0, root]
        return _CaseParts(
            "algebra",
            f"Find every integer x satisfying x squared = {root * root}, using candidate pool {pool}.",
            "After taking a square root, keep both sign branches and replay candidates.",
            ("1. Squaring erases the sign of a real input.", "2. Substitute each candidate into the unsimplified equation."),
            NormalizerKind.INTEGER_LIST,
            [-root, root],
            [root],
            WitnessSpec(WitnessVerifierKind.POLYNOMIAL_EVAL, {"candidate_pool": pool, "coefficients": [0, 0, 1], "target": root * root}),
        )
    if family == "induction_reachability":
        _strict_keys(p, {"base", "step"}, "induction parameters")
        base = _integer(p["base"], "induction base")
        step = _bounded_integer(
            p["step"],
            "induction step",
            minimum=2,
            maximum=MAX_INDUCTION_MODULUS,
        )
        clean = [base % step]
        return _CaseParts(
            "discrete mathematics",
            f"An induction starts at n = {base} and advances only by {step}. Which residue classes modulo {step} can it reach?",
            "Audit which residue classes the base cases and induction stride actually reach.",
            ("1. Reduce each base index modulo the induction stride.", "2. Close only those residues under the stated step."),
            NormalizerKind.RESIDUE_SET,
            clean,
            list(range(step)),
            WitnessSpec(WitnessVerifierKind.INDUCTION_RESIDUE, {"bases": [base], "modulus": step, "step": step}),
        )
    if family == "index_region":
        _strict_keys(p, {"upper"}, "index-region parameters")
        upper = _bounded_integer(
            p["upper"],
            "index upper bound",
            minimum=1,
            maximum=MAX_FINITE_COUNT_BOUND,
        )
        clean = _lattice_triangle_count(upper)
        return _CaseParts(
            "combinatorics",
            f"Count integer pairs (i,j) with 1 <= i <= j <= {upper} after swapping the order of summation.",
            "When swapping sums, preserve the same lattice region and derive both dependent bounds.",
            ("1. The admissible pairs form a triangular lattice region.", "2. Count the allowed second indices for each first index."),
            NormalizerKind.INTEGER,
            clean,
            upper * upper,
            WitnessSpec(WitnessVerifierKind.FINITE_SUM_COUNT, {"mode": "lattice_triangle", "upper": upper}),
        )
    if family == "norm_spectral_radius":
        _strict_keys(p, {"scale"}, "norm parameters")
        scale = _integer(p["scale"], "matrix scale")
        matrix = [[0, scale], [0, 0]]
        return _CaseParts(
            "linear algebra",
            f"For A = {matrix}, determine the squared Euclidean operator norm; do not replace it by the spectral radius.",
            "Distinguish eigenvalue radius from singular values and the induced operator norm.",
            ("1. Form the Gram matrix A-transpose times A.", "2. Read the largest diagonal eigenvalue of that Gram matrix."),
            NormalizerKind.RATIONAL,
            str(scale * scale),
            "0",
            WitnessSpec(WitnessVerifierKind.MATRIX_VECTOR_NORM, {"matrix": matrix}),
        )
    if family == "local_global":
        _strict_keys(p, {"left", "right"}, "local-global parameters")
        left = list(_int_list(p["left"], "left point"))
        right = list(_int_list(p["right"], "right point"))
        if len(left) != 2 or len(right) != 2:
            raise ValueError("local-global points must be planar")
        numerator = (left[0] * left[1] - right[0] * right[1]) ** 2
        denominator = sum((a - b) ** 2 for a, b in zip(left, right))
        ratio = Fraction(numerator, denominator)
        return _CaseParts(
            "analysis",
            f"For f(x,y)=xy, compute the squared output-to-input ratio between {left} and {right} as a global-control check.",
            "Use a genuine two-point test; radial or coordinate-axis checks do not establish global Lipschitz control.",
            ("1. Evaluate the function at both complete points.", "2. Divide the squared output difference by squared Euclidean distance."),
            NormalizerKind.RATIONAL,
            _fraction_token(ratio),
            "0",
            WitnessSpec(WitnessVerifierKind.TWO_POINT_SQUARED_RATIO, {"function": "xy", "left": left, "right": right}),
        )
    if family == "feasible_boundary":
        _strict_keys(p, {"objective", "points"}, "feasible-boundary parameters")
        objective = list(_int_list(p["objective"], "objective"))
        points = [list(_int_list(point, "support point")) for point in _sequence(p["points"], "support points")]
        scores = [sum(a * b for a, b in zip(objective, point)) for point in points]
        clean = points[scores.index(max(scores))]
        return _CaseParts(
            "optimization",
            f"Maximize the linear objective with coefficients {objective} over the feasible support points {points}.",
            "Inspect the full feasible boundary, including origins, faces, and the distinction between a ball and a sphere.",
            ("1. Evaluate the objective at every registered support point.", "2. Retain a feasible point attaining the largest exact value."),
            NormalizerKind.INTEGER_PAIR,
            clean,
            points[0] if points[0] != clean else points[-1],
            WitnessSpec(WitnessVerifierKind.FEASIBLE_SUPPORT_POINT, {"objective": objective, "points": points}),
        )
    if family == "asymptotic_scale":
        _strict_keys(p, {"size"}, "asymptotic parameters")
        size = _integer(p["size"], "asymptotic size")
        expression = {"args": [size, {"args": [size, 1], "op": "sub"}], "op": "mul"}
        return _CaseParts(
            "asymptotics",
            f"A contribution occurs once for every ordered pair of distinct indices chosen from {size} positions. Compute its exact multiplicity.",
            "Retain combinatorial multiplicity before translating an exact count into a bound or asymptotic rate.",
            ("1. Choose the first index from all positions.", "2. Choose the second index from the remaining positions."),
            NormalizerKind.RATIONAL,
            str(size * (size - 1)),
            str(size),
            WitnessSpec(WitnessVerifierKind.SCALAR_EXACT_RATIONAL, {"expression": expression}),
        )
    if family == "implication_equivalence":
        _strict_keys(p, {"context", "variables"}, "implication parameters")
        variables = _integer(p["variables"], "truth variable count")
        context = _string(p["context"], "implication context", max_bytes=128)
        return _CaseParts(
            "logic",
            f"In the {context} truth-table check on {variables} propositions, compute the probability that P and Q have the same truth value.",
            "An equivalence requires both directions; audit each implication instead of proving only one.",
            ("1. Enumerate the complete truth table.", "2. Count rows where each proposition implies the other."),
            NormalizerKind.RATIONAL,
            "1/2",
            "3/4",
            WitnessSpec(WitnessVerifierKind.FINITE_PROBABILITY, {"event": "biconditional", "variables": variables}),
        )
    if family == "modular_cancellation":
        _strict_keys(p, {"a", "b", "modulus"}, "modular parameters")
        a = _integer(p["a"], "modular coefficient")
        b = _integer(p["b"], "modular target")
        modulus = _bounded_integer(
            p["modulus"],
            "modulus",
            minimum=1,
            maximum=MAX_CONGRUENCE_MODULUS,
        )
        clean = [x for x in range(modulus) if (a * x - b) % modulus == 0]
        faulty = [clean[0]]
        return _CaseParts(
            "number theory",
            f"List all residues x modulo {modulus} satisfying {a}x congruent to {b} modulo {modulus}.",
            "Before cancelling a modular factor, test invertibility and adjust the modulus when required.",
            ("1. Replay every residue in one complete modulus.", "2. Keep all residues whose divisibility remainder vanishes."),
            NormalizerKind.RESIDUE_SET,
            clean,
            faulty,
            WitnessSpec(WitnessVerifierKind.CONGRUENCE_DIVISIBILITY, {"a": a, "b": b, "mode": "linear_congruence", "modulus": modulus}),
        )
    if family == "candidate_validation":
        _strict_keys(p, {"pole"}, "candidate-validation parameters")
        pole = _integer(p["pole"], "pole")
        pool = [pole - 1, pole, pole + 1]
        return _CaseParts(
            "algebra",
            f"Solve 1/(x - {pole}) = x - {pole} using candidate pool {pool}, respecting the original domain.",
            "Replay every transformed candidate in the original equation and its domain restrictions.",
            ("1. Reject points where an original denominator vanishes.", "2. Compare both sides exactly at every remaining candidate."),
            NormalizerKind.INTEGER_LIST,
            [pole - 1, pole + 1],
            pool,
            WitnessSpec(WitnessVerifierKind.DENOMINATOR_DOMAIN, {"candidate_pool": pool, "lhs_den": [-pole, 1], "lhs_num": [1], "rhs_den": [1], "rhs_num": [-pole, 1]}),
        )
    if family == "overlap_omission":
        _strict_keys(p, {"a", "b", "limit"}, "overlap parameters")
        a = _bounded_integer(
            p["a"],
            "first divisor",
            minimum=1,
            maximum=MAX_FINITE_COUNT_BOUND,
        )
        b = _bounded_integer(
            p["b"],
            "second divisor",
            minimum=1,
            maximum=MAX_FINITE_COUNT_BOUND,
        )
        limit = _bounded_integer(
            p["limit"],
            "overlap limit",
            minimum=1,
            maximum=MAX_FINITE_COUNT_BOUND,
        )
        clean = _union_multiples_count(a=a, b=b, limit=limit)
        faulty = limit // a + limit // b
        return _CaseParts(
            "combinatorics",
            f"Among integers from 1 through {limit}, count those divisible by {a} or by {b}.",
            "Use inclusion-exclusion so elements in an overlap are counted exactly once.",
            ("1. Count each divisibility set separately.", "2. Subtract the intersection from the combined count."),
            NormalizerKind.INTEGER,
            clean,
            faulty,
            WitnessSpec(WitnessVerifierKind.FINITE_SUM_COUNT, {"a": a, "b": b, "limit": limit, "mode": "union_multiples"}),
        )
    if family == "missing_coprimality":
        _strict_keys(p, {"a", "b", "c"}, "coprimality parameters")
        a = _integer(p["a"], "divisor")
        b = _integer(p["b"], "first factor")
        c = _integer(p["c"], "second factor")
        return _CaseParts(
            "number theory",
            f"For a={a}, b={b}, c={c}, decide whether a dividing bc logically forces a to divide c in this instance.",
            "A divisibility cancellation needs the relevant coprimality or primality hypothesis.",
            ("1. Verify the divisibility premise by an exact remainder.", "2. Verify the proposed conclusion independently by another remainder."),
            NormalizerKind.BOOLEAN,
            False,
            True,
            WitnessSpec(WitnessVerifierKind.CONGRUENCE_DIVISIBILITY, {"a": a, "b": b, "c": c, "mode": "divisibility_implication"}),
        )
    if family == "linear_invariant":
        _strict_keys(p, {"matrix"}, "linear-invariant parameters")
        matrix = p["matrix"]
        parsed = _matrix(matrix, "linear matrix")
        rank = _rref_rank(parsed)
        clean = {"nullity": len(parsed[0]) - rank, "rank": rank}
        faulty = {"nullity": len(parsed[0]) - sum(1 for i in range(min(len(parsed), len(parsed[0]))) if parsed[i][i]), "rank": sum(1 for i in range(min(len(parsed), len(parsed[0]))) if parsed[i][i])}
        if faulty == clean:
            faulty = {"nullity": clean["nullity"] - 1, "rank": clean["rank"] + 1}
        return _CaseParts(
            "linear algebra",
            f"Compute rank and nullity of the matrix {matrix}, using its full domain dimension.",
            "Apply rank-nullity with the domain dimension; do not count only visible diagonal entries or nonzero eigenvalues.",
            ("1. Row-reduce using exact rational operations.", "2. Subtract the pivot count from the number of columns."),
            NormalizerKind.RANK_NULLITY,
            clean,
            faulty,
            WitnessSpec(WitnessVerifierKind.LINEAR_KERNEL_RANK_NULLITY, {"matrix": matrix}),
        )
    if family == "independence_assumption":
        _strict_keys(p, {"outcomes"}, "independence parameters")
        outcomes = p["outcomes"]
        favorable = sum(all(bit == 0 for bit in outcome) for outcome in outcomes)
        joint = Fraction(favorable, len(outcomes))
        product = Fraction(1, 2) ** len(outcomes[0])
        return _CaseParts(
            "probability",
            f"A uniform model has outcome triples {outcomes}. Compute the probability that all three coordinates are zero.",
            "Marginals or pairwise independence do not establish mutual independence; replay the joint event directly.",
            ("1. Enumerate the registered finite outcomes.", "2. Count outcomes satisfying every coordinate condition simultaneously."),
            NormalizerKind.RATIONAL,
            _fraction_token(joint),
            _fraction_token(product),
            WitnessSpec(WitnessVerifierKind.PROBABILITY_IDENTITY, {"event": "all_zero", "outcomes": outcomes}),
        )
    if family == "sign_case":
        _strict_keys(p, {"numerator", "right"}, "sign-case parameters")
        numerator = _fraction(p["numerator"], "sign numerator")
        right = _fraction(p["right"], "sign right side")
        endpoint = numerator / right
        clean = [f"(0,{_fraction_token(endpoint)})"]
        faulty = [f"(-inf,{_fraction_token(endpoint)})"]
        return _CaseParts(
            "inequalities",
            f"Solve {p['numerator']}/x > {p['right']} over the real line, excluding the pole.",
            "Variable-sign inequality operations require separate interval cases around every zero and pole.",
            ("1. Mark the pole and the zero of the rearranged numerator.", "2. Test the sign on each open interval between critical points."),
            NormalizerKind.INTERVAL_LIST,
            clean,
            faulty,
            WitnessSpec(WitnessVerifierKind.ONE_SIDED_SLOPE, {"numerator": p["numerator"], "right": p["right"]}),
        )
    if family == "initial_data":
        _strict_keys(p, {"coefficients", "initials", "order"}, "initial-data parameters")
        order = _integer(p["order"], "recurrence order")
        coefficients = list(_int_list(p["coefficients"], "recurrence coefficients"))
        initials = list(_int_list(p["initials"], "recurrence initials"))
        determined = len(initials) >= order
        return _CaseParts(
            "discrete mathematics",
            f"A recurrence of order {order} has coefficients {coefficients} but only initial data {initials}. Decide whether one sequence is determined.",
            "An order-r recurrence needs enough independent initial values before forward replay is unique.",
            ("1. Count the recurrence order from the lagged terms.", "2. Compare that order with the supplied independent initial values."),
            NormalizerKind.BOOLEAN,
            determined,
            not determined,
            WitnessSpec(WitnessVerifierKind.RECURRENCE_PREFIX, {"coefficients": coefficients, "initials": initials, "order": order}),
        )
    if family == "aggregate_pointwise":
        _strict_keys(p, {"values"}, "aggregate parameters")
        values = list(_int_list(p["values"], "aggregate values"))
        target = sum(values)
        if not any(values):
            raise ValueError("aggregate case requires a nonzero counterexample")
        return _CaseParts(
            "analysis",
            f"A list of length {len(values)} has total sum {target}. Review whether that aggregate forces each entry to vanish by checking the candidate list.",
            "An aggregate parity, mean, or sum statement does not imply the corresponding pointwise statement.",
            ("1. Add every candidate entry exactly.", "2. Check whether at least one entry contradicts the claimed pointwise conclusion."),
            NormalizerKind.INTEGER_LIST,
            values,
            [0] * len(values),
            WitnessSpec(WitnessVerifierKind.AGGREGATE_COUNTEREXAMPLE, {"length": len(values), "target_sum": target}),
        )
    if family == "degree_threshold":
        _strict_keys(p, {"degree"}, "degree-threshold parameters")
        degree = _integer(p["degree"], "degree")
        return _CaseParts(
            "algebra",
            f"How many distinct sample points guarantee that two polynomials of degree at most {degree} agreeing there are identical?",
            "A degree-d polynomial identity requires enough distinct samples to force every coefficient difference to vanish.",
            ("1. Subtract the two polynomials to obtain the difference polynomial.", "2. Compare its possible number of distinct roots with its degree."),
            NormalizerKind.INTEGER,
            degree + 1,
            degree,
            WitnessSpec(WitnessVerifierKind.DEGREE_SEQUENCE, {"degree": degree}),
        )
    raise ValueError(f"unsupported challenge family: {family}")


def _build_case(
    *,
    split: Split,
    index: int,
    family: str,
    difficulty: Difficulty,
    case_id: str,
    params: Mapping[str, Any],
) -> ChallengeCase:
    parts = _case_parts(family, params)
    clean_line = _claim_line(parts.clean_candidate)
    faulty_line = _claim_line(parts.faulty_candidate)
    case = ChallengeCase(
        case_id=case_id,
        split=split,
        index=index,
        family=family,
        domain=parts.domain,
        difficulty=difficulty,
        prompt=parts.prompt,
        retrieval_query=parts.retrieval_query,
        clean_trace=(parts.trace_prefix[0], parts.trace_prefix[1], clean_line),
        faulty_line=3,
        faulty_replacement=faulty_line,
        repair_replacement=clean_line,
        witness=parts.witness,
        replacement_normalizer=ReplacementNormalizer(parts.normalizer, "3. claim = "),
        gold_card=f"C{index + 1:02d}",
    )
    certify_case(case)
    return case


def certify_case(case: ChallengeCase) -> bool:
    if not isinstance(case, ChallengeCase):
        raise ValueError("case certification input must be a ChallengeCase")
    clean = case.trace(TraceVariant.CLEAN)
    faulty = case.trace(TraceVariant.FAULTY)
    differences = [index for index, pair in enumerate(zip(clean, faulty), 1) if pair[0] != pair[1]]
    if differences != [case.faulty_line]:
        raise ValueError("faulty trace must contain exactly one replacement")
    clean_result = case.replay(clean)
    faulty_result = case.replay(faulty)
    repaired = list(faulty)
    repaired[case.faulty_line - 1] = case.repair_replacement
    repaired_result = case.replay(repaired)
    if not clean_result.accepted:
        raise ValueError("clean trace failed independent witness replay")
    if faulty_result.accepted or faulty_result.first_invalid_line != case.faulty_line:
        raise ValueError("faulty trace was not rejected at its unique fault line")
    if not repaired_result.accepted:
        raise ValueError("exact repair did not restore a replayable trace")
    return True


def assignment_variant(case_index: int, pair_id: int) -> TraceVariant:
    if isinstance(case_index, bool) or not isinstance(case_index, int) or not 0 <= case_index < 20:
        raise ValueError("case index must be an integer in [0, 19]")
    if isinstance(pair_id, bool) or not isinstance(pair_id, int) or not 0 <= pair_id < PAIR_COUNT:
        raise ValueError("pair id must be an integer in [0, 9]")
    return TraceVariant.CLEAN if (case_index + pair_id) % 10 < 5 else TraceVariant.FAULTY


def _salted_digest(domain: bytes, salt: bytes, *parts: bytes) -> str:
    if not isinstance(salt, bytes) or len(salt) < 16:
        raise ValueError("runtime salt must contain at least 16 bytes")
    payload = bytearray(domain)
    for part in (salt, *parts):
        payload.extend(len(part).to_bytes(8, "big"))
        payload.extend(part)
    return "sha256:" + hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class OpaqueAssignment:
    assignment_digest: str
    case_digest: str
    view_digest: str
    solver_view: SolverView

    def __post_init__(self) -> None:
        _sha256_string(self.assignment_digest, "assignment digest")
        _sha256_string(self.case_digest, "case digest")
        _sha256_string(self.view_digest, "view digest")
        if not isinstance(self.solver_view, SolverView):
            raise ValueError("opaque assignment must contain the shared SolverView type")


def render_solver_view(case: ChallengeCase, *, pair_id: int, runtime_salt: bytes) -> SolverView:
    variant = assignment_variant(case.index, pair_id)
    # Validate the salt even though identifiers live in the evaluator-only envelope.
    _salted_digest(b"math-brain/challenge/salt-check/v1\x00", runtime_salt, b"")
    return case.solver_view(variant is TraceVariant.FAULTY)


def opaque_case_id(case: ChallengeCase, runtime_salt: bytes) -> str:
    if not isinstance(case, ChallengeCase):
        raise ValueError("opaque case id input must be a ChallengeCase")
    return _salted_digest(
        b"math-brain/challenge/case/v1\x00",
        runtime_salt,
        case.case_id.encode("utf-8"),
        case.digest().encode("ascii"),
    )


def opaque_view_digest(
    case: ChallengeCase, solver_view: SolverView, runtime_salt: bytes
) -> str:
    if not isinstance(case, ChallengeCase) or not isinstance(solver_view, SolverView):
        raise ValueError("opaque view digest requires a challenge case and shared SolverView")
    allowed_traces = {
        case.trace(TraceVariant.CLEAN),
        case.trace(TraceVariant.FAULTY),
    }
    if solver_view.prompt != case.prompt or solver_view.numbered_solution not in allowed_traces:
        raise ValueError("solver view is not bound to the supplied challenge case")
    return _salted_digest(
        b"math-brain/challenge/view/v1\x00",
        runtime_salt,
        canonical_bytes(solver_view.to_dict()),
    )


def render_opaque_assignment(
    case: ChallengeCase, *, pair_id: int, runtime_salt: bytes
) -> OpaqueAssignment:
    variant = assignment_variant(case.index, pair_id)
    solver_view = case.solver_view(variant is TraceVariant.FAULTY)
    case_digest = opaque_case_id(case, runtime_salt)
    assignment_digest = _salted_digest(
        b"math-brain/challenge/assignment/v1\x00",
        runtime_salt,
        case.case_id.encode(),
        pair_id.to_bytes(2, "big"),
    )
    view_digest = opaque_view_digest(case, solver_view, runtime_salt)
    return OpaqueAssignment(
        assignment_digest,
        case_digest,
        view_digest,
        solver_view,
    )


def _merkle_root(leaves: Sequence[bytes]) -> bytes:
    if not leaves:
        return _sha256_bytes(b"")
    if len(leaves) == 1:
        return leaves[0]
    split = 1 << ((len(leaves) - 1).bit_length() - 1)
    return _sha256_bytes(b"\x01" + _merkle_root(leaves[:split]) + _merkle_root(leaves[split:]))


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    case_id: str
    content_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {"case_id": self.case_id, "content_digest": self.content_digest}


@dataclass(frozen=True, slots=True)
class ChallengeManifest:
    bank_id: str
    split: Split
    entries: tuple[ManifestEntry, ...]
    family_order: tuple[str, ...]
    difficulty_counts: tuple[tuple[str, int], ...]
    merkle_root: str
    generator_version: str = GENERATOR_VERSION
    merkle_algorithm: str = MERKLE_ALGORITHM
    schema: str = MANIFEST_SCHEMA
    schema_version: int = MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _string(self.bank_id, "manifest bank id", max_bytes=128)
        if not isinstance(self.split, Split):
            raise ValueError("manifest split is invalid")
        if len(self.entries) != 20 or len({entry.case_id for entry in self.entries}) != 20:
            raise ValueError("challenge manifest must contain exactly twenty unique entries")
        if self.family_order != FAMILY_ORDER:
            raise ValueError("challenge manifest family order is not canonical")
        if self.difficulty_counts != (("hard", 10), ("medium", 10)):
            raise ValueError("challenge manifest must contain ten medium and ten hard cases")
        _sha256_string(self.merkle_root, "manifest Merkle root")
        if self.generator_version != GENERATOR_VERSION or self.merkle_algorithm != MERKLE_ALGORITHM:
            raise ValueError("unsupported challenge generator or Merkle algorithm")
        if self.schema != MANIFEST_SCHEMA or self.schema_version != MANIFEST_SCHEMA_VERSION:
            raise ValueError("unsupported challenge-manifest schema")

    def to_dict(self) -> dict[str, Any]:
        return {
            "bank_id": self.bank_id,
            "difficulty_counts": dict(self.difficulty_counts),
            "entries": [entry.to_dict() for entry in self.entries],
            "family_order": list(self.family_order),
            "generator_version": self.generator_version,
            "merkle_algorithm": self.merkle_algorithm,
            "merkle_root": self.merkle_root,
            "schema": self.schema,
            "schema_version": self.schema_version,
            "split": self.split.value,
        }

    def digest(self) -> str:
        return content_digest(self.to_dict())

    def seal(self) -> SplitSeal:
        return SplitSeal(
            bank_id=self.bank_id,
            split=self.split,
            case_count=len(self.entries),
            family_count=len(self.family_order),
            difficulty_counts=self.difficulty_counts,
            merkle_root=self.merkle_root,
            manifest_digest=self.digest(),
        )


@dataclass(frozen=True, slots=True)
class SplitSeal:
    bank_id: str
    split: Split
    case_count: int
    family_count: int
    difficulty_counts: tuple[tuple[str, int], ...]
    merkle_root: str
    manifest_digest: str
    schema: str = SEAL_SCHEMA
    schema_version: int = SEAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _string(self.bank_id, "seal bank id", max_bytes=128)
        if self.case_count != 20 or self.family_count != 20:
            raise ValueError("split seal must bind twenty cases and twenty families")
        if self.difficulty_counts != (("hard", 10), ("medium", 10)):
            raise ValueError("split seal difficulty balance is invalid")
        _sha256_string(self.merkle_root, "seal Merkle root")
        _sha256_string(self.manifest_digest, "sealed manifest digest")
        if self.schema != SEAL_SCHEMA or self.schema_version != SEAL_SCHEMA_VERSION:
            raise ValueError("unsupported split-seal schema")

    def to_dict(self) -> dict[str, Any]:
        return {
            "bank_id": self.bank_id,
            "case_count": self.case_count,
            "difficulty_counts": dict(self.difficulty_counts),
            "family_count": self.family_count,
            "manifest_digest": self.manifest_digest,
            "merkle_root": self.merkle_root,
            "schema": self.schema,
            "schema_version": self.schema_version,
            "split": self.split.value,
        }

    def digest(self) -> str:
        return content_digest(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Any) -> SplitSeal:
        value = _mapping(raw, "split seal")
        _strict_keys(
            value,
            {
                "bank_id",
                "case_count",
                "difficulty_counts",
                "family_count",
                "manifest_digest",
                "merkle_root",
                "schema",
                "schema_version",
                "split",
            },
            "split seal",
        )
        counts = _mapping(value["difficulty_counts"], "seal difficulty counts")
        _strict_keys(counts, {"hard", "medium"}, "seal difficulty counts")
        return cls(
            bank_id=value["bank_id"],
            split=Split(value["split"]),
            case_count=_integer(value["case_count"], "seal case count"),
            family_count=_integer(value["family_count"], "seal family count"),
            difficulty_counts=tuple(
                sorted(
                    (
                        (key, _integer(item, f"{key} difficulty count"))
                        for key, item in counts.items()
                    )
                )
            ),
            merkle_root=value["merkle_root"],
            manifest_digest=value["manifest_digest"],
            schema=value["schema"],
            schema_version=_integer(value["schema_version"], "seal schema version"),
        )


@dataclass(frozen=True, slots=True)
class BankSeal:
    bank_id: str
    reference_cards_digest: str
    development_seal_digest: str
    holdout_seal_digest: str
    schema: str = BANK_SEAL_SCHEMA
    schema_version: int = BANK_SEAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _string(self.bank_id, "bank-seal id", max_bytes=128)
        _sha256_string(self.reference_cards_digest, "reference-card commitment")
        _sha256_string(self.development_seal_digest, "development-seal commitment")
        _sha256_string(self.holdout_seal_digest, "holdout-seal commitment")
        if self.schema != BANK_SEAL_SCHEMA or self.schema_version != BANK_SEAL_SCHEMA_VERSION:
            raise ValueError("unsupported bank-seal schema")

    def to_dict(self) -> dict[str, Any]:
        return {
            "bank_id": self.bank_id,
            "development_seal_digest": self.development_seal_digest,
            "holdout_seal_digest": self.holdout_seal_digest,
            "reference_cards_digest": self.reference_cards_digest,
            "schema": self.schema,
            "schema_version": self.schema_version,
        }

    def digest(self) -> str:
        return content_digest(self.to_dict())


def build_manifest(bank_id: str, split: Split, cases: Iterable[ChallengeCase]) -> ChallengeManifest:
    materialized = tuple(cases)
    if len(materialized) != 20:
        raise ValueError("a challenge split must contain exactly twenty cases")
    if any(case.split is not split for case in materialized):
        raise ValueError("manifest cases do not match the requested split")
    if tuple(case.index for case in materialized) != tuple(range(20)):
        raise ValueError("manifest cases must be ordered by all twenty canonical indices")
    for case in materialized:
        certify_case(case)
    leaves = tuple(_sha256_bytes(b"\x00" + canonical_bytes(case.to_dict())) for case in materialized)
    counts = tuple(sorted((difficulty.value, sum(case.difficulty is difficulty for case in materialized)) for difficulty in Difficulty))
    return ChallengeManifest(
        bank_id=bank_id,
        split=split,
        entries=tuple(ManifestEntry(case.case_id, case.digest()) for case in materialized),
        family_order=tuple(case.family for case in materialized),
        difficulty_counts=counts,
        merkle_root="sha256:" + _merkle_root(leaves).hex(),
    )


def _normalize_tokens(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", text.casefold()))


def has_contiguous_token_overlap(left: str, right: str, *, width: int = 12) -> bool:
    if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
        raise ValueError("token-overlap width must be positive")
    left_tokens = _normalize_tokens(left)
    right_tokens = _normalize_tokens(right)
    if len(left_tokens) < width or len(right_tokens) < width:
        return False
    shingles = {left_tokens[index : index + width] for index in range(len(left_tokens) - width + 1)}
    return any(right_tokens[index : index + width] in shingles for index in range(len(right_tokens) - width + 1))


def _cards_digest(cards: Sequence[ReferenceCard]) -> str:
    return content_digest([card.to_dict() for card in cards])


@dataclass(frozen=True, slots=True)
class RetrievalAuditRecord:
    query_digest: str
    trigger_digest: str
    candidate_digest: str
    returned_digest: str
    scores_digest: str
    abstention_digest: str
    context_digest: str
    schema: str = RETRIEVAL_RECORD_SCHEMA
    schema_version: int = RETRIEVAL_RECORD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for label, value in (
            ("query", self.query_digest),
            ("trigger", self.trigger_digest),
            ("candidate", self.candidate_digest),
            ("returned", self.returned_digest),
            ("scores", self.scores_digest),
            ("abstention", self.abstention_digest),
            ("context", self.context_digest),
        ):
            _sha256_string(value, f"{label} digest")
        if self.schema != RETRIEVAL_RECORD_SCHEMA or self.schema_version != RETRIEVAL_RECORD_SCHEMA_VERSION:
            raise ValueError("unsupported retrieval-record schema")

    def to_dict(self) -> dict[str, Any]:
        return {
            "abstention_digest": self.abstention_digest,
            "candidate_digest": self.candidate_digest,
            "context_digest": self.context_digest,
            "query_digest": self.query_digest,
            "returned_digest": self.returned_digest,
            "schema": self.schema,
            "schema_version": self.schema_version,
            "scores_digest": self.scores_digest,
            "trigger_digest": self.trigger_digest,
        }


def record_retrieval(
    *,
    query: str,
    triggered: bool,
    candidates: Sequence[str],
    returned: Sequence[str],
    scores: Sequence[tuple[str, float]],
    abstained: bool,
    context: str,
) -> RetrievalAuditRecord:
    _string(query, "retrieval query")
    if not isinstance(triggered, bool) or not isinstance(abstained, bool):
        raise ValueError("retrieval trigger and abstention must be booleans")
    candidate_tuple = tuple(_string(item, "candidate card", max_bytes=128) for item in candidates)
    returned_tuple = tuple(_string(item, "returned card", max_bytes=128) for item in returned)
    if not set(returned_tuple).issubset(candidate_tuple):
        raise ValueError("returned cards must be selected from candidates")
    score_rows = []
    for card, score in scores:
        _string(card, "scored card", max_bytes=128)
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
            raise ValueError("retrieval scores must be finite numbers")
        score_rows.append([card, float(score)])
    if tuple(card for card, _ in scores) != returned_tuple:
        raise ValueError("scores must be ordered exactly with returned cards")
    if abstained != (not returned_tuple):
        raise ValueError("abstention must agree with the returned-card set")
    if not isinstance(context, str):
        raise ValueError("retrieval context must be text")
    return RetrievalAuditRecord(
        query_digest=content_digest({"query": query}),
        trigger_digest=content_digest({"triggered": triggered}),
        candidate_digest=content_digest({"candidates": list(candidate_tuple)}),
        returned_digest=content_digest({"returned": list(returned_tuple)}),
        scores_digest=content_digest({"scores": score_rows}),
        abstention_digest=content_digest({"abstained": abstained}),
        context_digest=content_digest({"context": context}),
    )


@dataclass(frozen=True, slots=True)
class ReferenceRetrieval:
    card_ids: tuple[str, ...]
    scores: tuple[tuple[str, float], ...]
    context: str
    audit: RetrievalAuditRecord


_RETRIEVAL_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "be",
        "before",
        "by",
        "each",
        "every",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "only",
        "or",
        "the",
        "there",
        "to",
        "when",
    }
)


def _retrieval_terms(text: str) -> frozenset[str]:
    return frozenset(
        token
        for token in _normalize_tokens(text)
        if token not in _RETRIEVAL_STOP_WORDS and len(token) > 1
    )


@dataclass(frozen=True, slots=True)
class ReferenceCorpus:
    """Read-only lexical retrieval over generic cards, never case relevance metadata."""

    cards: tuple[ReferenceCard, ...]

    def __post_init__(self) -> None:
        if len(self.cards) != 20 or tuple(card.card_id for card in self.cards) != tuple(
            f"C{index:02d}" for index in range(1, 21)
        ):
            raise ValueError("reference corpus requires canonical C01-C20 cards")

    def retrieve(self, query: str, limit: int = 3) -> ReferenceRetrieval:
        _string(query, "reference query")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("reference retrieval limit must be in [1, 20]")
        query_terms = _retrieval_terms(query)
        ranked: list[tuple[str, float, ReferenceCard]] = []
        for card in self.cards:
            body = " ".join((card.title, card.guidance, *card.retrieval_cues))
            body_terms = _retrieval_terms(body)
            overlap = len(query_terms & body_terms)
            cue_hits = sum(
                bool(query_terms & _retrieval_terms(cue)) for cue in card.retrieval_cues
            )
            score = float(overlap * 4 + cue_hits)
            if score > 0:
                ranked.append((card.card_id, score, card))
        ranked.sort(key=lambda row: (-row[1], row[0]))
        selected = tuple(ranked[:limit])
        card_ids = tuple(row[0] for row in selected)
        scores = tuple((row[0], row[1]) for row in selected)
        context = "\n\n".join(
            f"[{row[2].card_id}] {row[2].title}\n{row[2].guidance}" for row in selected
        )
        audit = record_retrieval(
            query=query,
            triggered=bool(query_terms),
            candidates=tuple(card.card_id for card in self.cards),
            returned=card_ids,
            scores=scores,
            abstained=not card_ids,
            context=context,
        )
        return ReferenceRetrieval(card_ids, scores, context, audit)

    def retrieve_for_solver_view(
        self, solver_view: SolverView, limit: int = 3
    ) -> ReferenceRetrieval:
        """Primary challenge path: retrieve only from the solver-visible prompt."""

        if not isinstance(solver_view, SolverView):
            raise ValueError("prompt-driven retrieval requires the shared SolverView")
        return self.retrieve(solver_view.prompt, limit=limit)


def certify_reference_retrieval(
    cards: Sequence[ReferenceCard], cases: Sequence[ChallengeCase]
) -> bool:
    """Certify the authored-query oracle-routing diagnostic, not primary retrieval."""

    corpus = ReferenceCorpus(tuple(cards))
    misses = [
        case.case_id
        for case in cases
        if case.gold_card not in corpus.retrieve(case.retrieval_query, limit=3).card_ids
    ]
    if misses:
        raise ValueError("reference retrieval top-three seal gate failed")
    return True


@dataclass(frozen=True, slots=True)
class ShamCorpus:
    """A matched-latency control surface that can never return mathematical content."""

    def retrieve(self, query: str) -> tuple[()]:
        _string(query, "sham retrieval query")
        return ()

    def audit(self, query: str) -> RetrievalAuditRecord:
        self.retrieve(query)
        return record_retrieval(
            query=query,
            triggered=bool(_retrieval_terms(query)),
            candidates=(),
            returned=(),
            scores=(),
            abstained=True,
            context="",
        )

    def retrieve_for_solver_view(
        self, solver_view: SolverView, limit: int = 3
    ) -> ReferenceRetrieval:
        """Return primary-path empty evidence for the solver-visible prompt."""

        if not isinstance(solver_view, SolverView):
            raise ValueError("prompt-driven sham retrieval requires the shared SolverView")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("reference retrieval limit must be in [1, 20]")
        return ReferenceRetrieval((), (), "", self.audit(solver_view.prompt))


def _lookup_case(cases: Sequence[ChallengeCase], case_id: str, prefix: str) -> ChallengeCase:
    if not re.fullmatch(prefix + r"(?:0[1-9]|1[0-9]|20)", case_id):
        raise ValueError(f"case id must be {prefix}01 through {prefix}20")
    return cases[int(case_id[1:]) - 1]


@dataclass(frozen=True, slots=True)
class DevelopmentWorkbench:
    _cases: tuple[ChallengeCase, ...]
    manifest: ChallengeManifest
    reference_cards: tuple[ReferenceCard, ...]

    @property
    def case_ids(self) -> tuple[str, ...]:
        return tuple(case.case_id for case in self._cases)

    def case(self, case_id: str) -> ChallengeCase:
        return _lookup_case(self._cases, case_id, "D")

    def retrieval_query(self, case_id: str) -> str:
        """Backward-compatible authored-query oracle diagnostic."""

        return self.oracle_retrieval_query(case_id)

    def oracle_retrieval_query(self, case_id: str) -> str:
        return self.case(case_id).retrieval_query

    def render(self, case_id: str, *, pair_id: int, runtime_salt: bytes) -> SolverView:
        return render_solver_view(self.case(case_id), pair_id=pair_id, runtime_salt=runtime_salt)

    def opaque_assignment(
        self, case_id: str, *, pair_id: int, runtime_salt: bytes
    ) -> OpaqueAssignment:
        return render_opaque_assignment(
            self.case(case_id), pair_id=pair_id, runtime_salt=runtime_salt
        )

    def replay(self, case_id: str, trace: Sequence[str]) -> ReplayResult:
        return self.case(case_id).replay(trace)


@dataclass(frozen=True, slots=True)
class HoldoutUnsealRequest:
    evaluation_manifest_digest: str
    purpose: str
    evaluator_role: str
    acknowledge_no_improvement_use: bool

    def __post_init__(self) -> None:
        _sha256_string(self.evaluation_manifest_digest, "evaluation manifest digest")
        if self.purpose != "sealed_evaluation" or self.evaluator_role != "evaluator":
            raise ValueError("holdout unsealing is restricted to the evaluator role")
        if self.acknowledge_no_improvement_use is not True:
            raise ValueError("holdout unsealing requires a no-improvement-use acknowledgement")


@dataclass(frozen=True, slots=True)
class HoldoutEvaluator:
    _cases: tuple[ChallengeCase, ...]
    manifest: ChallengeManifest

    @property
    def case_count(self) -> int:
        return len(self._cases)

    def case(self, index: int) -> ChallengeCase:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < 20:
            raise ValueError("holdout case index must be in [0, 19]")
        return self._cases[index]

    def render(self, case_id: str, *, pair_id: int, runtime_salt: bytes) -> SolverView:
        return render_solver_view(
            _lookup_case(self._cases, case_id, "H"), pair_id=pair_id, runtime_salt=runtime_salt
        )

    def replay(self, case_id: str, trace: Sequence[str]) -> ReplayResult:
        return _lookup_case(self._cases, case_id, "H").replay(trace)

    def opaque_assignment(
        self, case_id: str, *, pair_id: int, runtime_salt: bytes
    ) -> OpaqueAssignment:
        return render_opaque_assignment(
            _lookup_case(self._cases, case_id, "H"),
            pair_id=pair_id,
            runtime_salt=runtime_salt,
        )

    def retrieval_query(self, case_id: str) -> str:
        """Backward-compatible authored-query oracle diagnostic."""

        return self.oracle_retrieval_query(case_id)

    def oracle_retrieval_query(self, case_id: str) -> str:
        return _lookup_case(self._cases, case_id, "H").retrieval_query


@dataclass(frozen=True, slots=True)
class SealedChallengeBank:
    bank_id: str
    development: DevelopmentWorkbench
    reference_cards: tuple[ReferenceCard, ...]
    holdout_seal: SplitSeal
    bank_seal: BankSeal
    holdout_payload_digest: str
    config_digest: str

    def __post_init__(self) -> None:
        _sha256_string(self.config_digest, "challenge config digest")
        _sha256_string(self.holdout_payload_digest, "holdout payload digest")
        if self.holdout_seal.split is not Split.HOLDOUT:
            raise ValueError("public holdout seal has the wrong split")

    def unseal_holdout(
        self, request: HoldoutUnsealRequest, *, payload_json: str
    ) -> HoldoutEvaluator:
        if not isinstance(request, HoldoutUnsealRequest):
            raise ValueError("holdout access requires an explicit unseal request")
        value = _mapping(strict_json_loads(payload_json), "holdout payload")
        supplied_digest = _verify_supplied_content_digest(
            value,
            label="holdout payload",
            mismatch_message="holdout payload self-digest mismatch",
        )
        if not hmac.compare_digest(supplied_digest, self.holdout_payload_digest):
            raise ValueError("holdout payload does not match the precommitted digest")
        sealed = seal_holdout_payload_dict(value, reference_cards=self.reference_cards)
        if not hmac.compare_digest(supplied_digest, sealed["content_digest"]):
            raise ValueError("holdout payload self-digest mismatch")
        cases = _parse_split_specs(value["holdout_specs"], Split.HOLDOUT)
        for development_case, holdout_case in zip(self.development._cases, cases):
            if development_case.difficulty is not holdout_case.difficulty:
                raise ValueError("holdout difficulty tiers must match development tiers")
            if development_case.prompt == holdout_case.prompt:
                raise ValueError("holdout instances must be distinct from development instances")
        manifest = build_manifest(self.bank_id, Split.HOLDOUT, cases)
        if manifest.seal() != self.holdout_seal:
            raise ValueError("holdout payload does not open the precommitted split seal")
        return HoldoutEvaluator(cases, manifest)


def _parse_cards(raw: Any) -> tuple[ReferenceCard, ...]:
    rows = _sequence(raw, "reference cards")
    if len(rows) != 20:
        raise ValueError("challenge config must contain exactly twenty reference cards")
    cards = []
    for index, row in enumerate(rows):
        value = _mapping(row, "reference card descriptor")
        _strict_keys(value, {"card_id", "family", "guidance", "retrieval_cues", "title"}, "reference card descriptor")
        cues = _sequence(value["retrieval_cues"], "reference cues")
        if not all(isinstance(cue, str) for cue in cues):
            raise ValueError("reference cues must be strings")
        card = ReferenceCard(value["card_id"], value["family"], value["title"], value["guidance"], tuple(cues))
        if card.card_id != f"C{index + 1:02d}" or card.family != FAMILY_ORDER[index]:
            raise ValueError("reference cards must follow the canonical C01-C20 family order")
        cards.append(card)
    return tuple(cards)


def _parse_split_specs(raw: Any, split: Split) -> tuple[ChallengeCase, ...]:
    field = "development" if split is Split.DEVELOPMENT else "holdout"
    rows = _sequence(raw, f"{field} specs")
    if len(rows) != 20:
        raise ValueError(f"challenge config must contain exactly twenty {field} specs")
    cases = []
    for index, row in enumerate(rows):
        value = _mapping(row, f"{field} family descriptor")
        _strict_keys(
            value,
            {field, "difficulty", "family", "gold_card"},
            f"{field} family descriptor",
        )
        family = _string(value["family"], "case family", max_bytes=128)
        if family != FAMILY_ORDER[index] or value["gold_card"] != f"C{index + 1:02d}":
            raise ValueError("case specs must follow the canonical family and card order")
        difficulty = Difficulty(value["difficulty"])
        spec = _mapping(value[field], f"{field} case spec")
        _strict_keys(spec, {"case_id", "parameters"}, f"{field} case spec")
        cases.append(
            _build_case(
                split=split,
                index=index,
                family=family,
                difficulty=difficulty,
                case_id=spec["case_id"],
                params=_mapping(spec["parameters"], f"{field} parameters"),
            )
        )
    difficulties = tuple(case.difficulty.value for case in cases)
    if difficulties.count("medium") != 10 or difficulties.count("hard") != 10:
        raise ValueError("each split must contain exactly ten medium and ten hard cases")
    return tuple(cases)


def _public_expected_commitments(
    bank_id: str,
    cards: tuple[ReferenceCard, ...],
    development_manifest: ChallengeManifest,
    holdout_seal: SplitSeal,
    holdout_payload_digest: str,
) -> tuple[dict[str, str], BankSeal]:
    cards_digest = _cards_digest(cards)
    development_seal = development_manifest.seal()
    bank_seal = BankSeal(bank_id, cards_digest, development_seal.digest(), holdout_seal.digest())
    expected = {
        "bank_seal_digest": bank_seal.digest(),
        "development_manifest_digest": development_manifest.digest(),
        "holdout_payload_digest": holdout_payload_digest,
        "reference_cards_digest": cards_digest,
    }
    return expected, bank_seal


def seal_holdout_payload_dict(
    raw: Mapping[str, Any], *, reference_cards: Sequence[ReferenceCard] | None = None
) -> dict[str, Any]:
    """Seal evaluator-only holdout data without writing or exposing it to improvement APIs."""

    value = dict(_mapping(raw, "holdout payload"))
    _strict_keys(
        value,
        {
            "bank_id",
            "content_digest",
            "generator_version",
            "holdout_specs",
            "schema",
            "schema_version",
        },
        "holdout payload",
    )
    if (
        value["schema"] != HOLDOUT_PAYLOAD_SCHEMA
        or value["schema_version"] != HOLDOUT_PAYLOAD_SCHEMA_VERSION
    ):
        raise ValueError("unsupported holdout-payload schema")
    if value["generator_version"] != GENERATOR_VERSION:
        raise ValueError("unsupported holdout generator")
    bank_id = _string(value["bank_id"], "holdout bank id", max_bytes=128)
    cases = _parse_split_specs(value["holdout_specs"], Split.HOLDOUT)
    build_manifest(bank_id, Split.HOLDOUT, cases)
    if reference_cards is not None:
        certify_reference_retrieval(tuple(reference_cards), cases)
        prompts = tuple(case.prompt for case in cases)
        for card in reference_cards:
            card_text = " ".join((card.title, card.guidance, *card.retrieval_cues))
            if any(has_contiguous_token_overlap(card_text, prompt) for prompt in prompts):
                raise ValueError("reference card contains a forbidden holdout prompt overlap")
    value["content_digest"] = content_digest(
        {key: item for key, item in value.items() if key != "content_digest"}
    )
    return value


def seal_config_dict(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return a canonical descriptor with fresh commitments; useful only before preregistration."""

    value = dict(_mapping(raw, "challenge config"))
    _strict_keys(
        value,
        {
            "bank_id",
            "content_digest",
            "development_specs",
            "expected_commitments",
            "generator_version",
            "holdout_seal",
            "reference_cards",
            "schema",
            "schema_version",
        },
        "challenge config",
    )
    if value["schema"] != CONFIG_SCHEMA or value["schema_version"] != CONFIG_SCHEMA_VERSION:
        raise ValueError("unsupported challenge-config schema")
    if value["generator_version"] != GENERATOR_VERSION:
        raise ValueError("unsupported challenge generator")
    bank_id = _string(value["bank_id"], "bank id", max_bytes=128)
    cards = _parse_cards(value["reference_cards"])
    development = _parse_split_specs(value["development_specs"], Split.DEVELOPMENT)
    prompts = tuple(case.prompt for case in development)
    for card in cards:
        card_text = " ".join((card.title, card.guidance, *card.retrieval_cues))
        if any(has_contiguous_token_overlap(card_text, prompt) for prompt in prompts):
            raise ValueError("reference card contains a forbidden twelve-token prompt overlap")
    certify_reference_retrieval(cards, development)
    development_manifest = build_manifest(bank_id, Split.DEVELOPMENT, development)
    holdout_seal = SplitSeal.from_dict(value["holdout_seal"])
    if holdout_seal.bank_id != bank_id or holdout_seal.split is not Split.HOLDOUT:
        raise ValueError("holdout seal does not match the public bank")
    supplied_expected = _mapping(value["expected_commitments"], "expected commitments")
    _strict_keys(
        supplied_expected,
        {
            "bank_seal_digest",
            "development_manifest_digest",
            "holdout_payload_digest",
            "reference_cards_digest",
        },
        "expected commitments",
    )
    holdout_payload_digest = _sha256_string(
        supplied_expected["holdout_payload_digest"], "holdout payload commitment"
    )
    expected, _ = _public_expected_commitments(
        bank_id, cards, development_manifest, holdout_seal, holdout_payload_digest
    )
    value["expected_commitments"] = expected
    value["content_digest"] = content_digest({key: item for key, item in value.items() if key != "content_digest"})
    return value


def load_challenge_bank_json(raw: str) -> SealedChallengeBank:
    value = _mapping(strict_json_loads(raw), "challenge config")
    supplied_digest = _verify_supplied_content_digest(
        value,
        label="challenge config",
        mismatch_message="challenge config content digest mismatch",
    )
    sealed = seal_config_dict(value)
    if not hmac.compare_digest(supplied_digest, sealed["content_digest"]):
        raise ValueError("challenge config content digest mismatch")
    supplied_expected = _mapping(value["expected_commitments"], "expected commitments")
    _strict_keys(
        supplied_expected,
        {
            "bank_seal_digest",
            "development_manifest_digest",
            "holdout_payload_digest",
            "reference_cards_digest",
        },
        "expected commitments",
    )
    for key, expected in sealed["expected_commitments"].items():
        actual = _sha256_string(supplied_expected[key], f"{key} commitment")
        if not hmac.compare_digest(actual, expected):
            raise ValueError(f"challenge config {key} mismatch")
    bank_id = value["bank_id"]
    cards = _parse_cards(value["reference_cards"])
    development = _parse_split_specs(value["development_specs"], Split.DEVELOPMENT)
    development_manifest = build_manifest(bank_id, Split.DEVELOPMENT, development)
    holdout_seal = SplitSeal.from_dict(value["holdout_seal"])
    holdout_payload_digest = supplied_expected["holdout_payload_digest"]
    _, bank_seal = _public_expected_commitments(
        bank_id, cards, development_manifest, holdout_seal, holdout_payload_digest
    )
    return SealedChallengeBank(
        bank_id=bank_id,
        development=DevelopmentWorkbench(development, development_manifest, cards),
        reference_cards=cards,
        holdout_seal=holdout_seal,
        bank_seal=bank_seal,
        holdout_payload_digest=holdout_payload_digest,
        config_digest=supplied_digest,
    )


def load_challenge_bank(path: str | Path) -> SealedChallengeBank:
    config_path = Path(path)
    try:
        raw = config_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("challenge config must be valid UTF-8") from exc
    return load_challenge_bank_json(raw)


__all__ = [
    "BANK_SEAL_SCHEMA",
    "CASE_SCHEMA",
    "CONFIG_SCHEMA",
    "FAMILY_ORDER",
    "GENERATOR_VERSION",
    "HOLDOUT_PAYLOAD_SCHEMA",
    "PAIR_COUNT",
    "BankSeal",
    "ChallengeCase",
    "ChallengeManifest",
    "DevelopmentWorkbench",
    "Difficulty",
    "HoldoutEvaluator",
    "HoldoutUnsealRequest",
    "OpaqueAssignment",
    "ReferenceCard",
    "ReferenceCorpus",
    "ReferenceRetrieval",
    "ReplayResult",
    "RetrievalAuditRecord",
    "SealedChallengeBank",
    "ShamCorpus",
    "SolverView",
    "Split",
    "SplitSeal",
    "TraceVariant",
    "assignment_variant",
    "build_manifest",
    "canonical_bytes",
    "canonical_json",
    "certify_case",
    "content_digest",
    "certify_reference_retrieval",
    "has_contiguous_token_overlap",
    "load_challenge_bank",
    "load_challenge_bank_json",
    "opaque_case_id",
    "opaque_view_digest",
    "record_retrieval",
    "render_opaque_assignment",
    "render_solver_view",
    "seal_config_dict",
    "seal_holdout_payload_dict",
    "strict_json_loads",
    "verify_witness",
]
