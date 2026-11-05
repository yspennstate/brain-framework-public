"""Deterministic, public-safe benchmark generation for mathematical error detection.

The trusted benchmark core deliberately uses only the Python standard library.  Items are
synthetic, self-contained MathIR records: each has a correct numbered derivation, a copy with
one semantically false line, two validators with different failure modes, and a replayable repair.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from fractions import Fraction
from math import comb, gcd, isqrt
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from .benchmark_verifier import ExprSpec, verify_family
from .models import canonical_json


MATHIR_SCHEMA = "org.math-research-brain/mathir-benchmark-item"
MATHIR_SCHEMA_VERSION = 4
GENERATOR_VERSION = "synthetic-v4-independent"
MANIFEST_SCHEMA = "org.math-research-brain/benchmark-manifest"
MANIFEST_SCHEMA_VERSION = 4
MERKLE_ALGORITHM = "rfc6962-sha256"
SEMANTIC_SCHEMA = "org.math-research-brain/canonical-givens"
SEMANTIC_SCHEMA_VERSION = 1
SCHEDULE_ALGORITHM = "balanced-affine-without-replacement-v1"
SOLVER_VIEW_SCHEMA = "org.math-research-brain/solver-view"
SOLVER_VIEW_SCHEMA_VERSION = 1
PRIVATE_DATA_LICENSE = "LicenseRef-Math-Brain-All-Rights-Reserved"

MAX_ITEM_JSON_BYTES = 256_000
MAX_MANIFEST_JSON_BYTES = 64_000
MAX_SOLVER_VIEW_JSON_BYTES = 128_000
MAX_EXPRESSION_DEPTH = 32
MAX_EXPRESSION_NODES = 256
MAX_FRACTION_BITS = 512
MAX_SOLUTION_STEPS = 64
MAX_MANIFEST_ITEMS = 10_000
BENCHMARK_CAPACITY = 10_000
MAX_STRING_BYTES = 16_384
MAX_SHORT_STRING_BYTES = 1_024
MAX_INTEGER_ABS = 10**12
MAX_RNG_BOUND_BITS = 512


def _utf8_bytes(value: Any, label: str) -> bytes:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} is not valid UTF-8 text") from exc


def _normalized_generator_source_hash() -> str:
    """Hash this generator's committed text independent of checkout line endings."""

    source = Path(__file__).read_text(encoding="utf-8")
    normalized = source.replace("\r\n", "\n").replace("\r", "\n")
    return "sha256:" + hashlib.sha256(_utf8_bytes(normalized, "generator source")).hexdigest()


GENERATOR_SOURCE_HASH = _normalized_generator_source_hash()


def _normalized_generator_bundle_hash() -> str:
    """Commit both generation and independent-verification source files."""

    root = Path(__file__).parent
    payload = bytearray(b"math-research-brain/generator-bundle/v1\x00")
    for name in ("benchmark.py", "benchmark_verifier.py"):
        source = (root / name).read_text(encoding="utf-8")
        normalized = source.replace("\r\n", "\n").replace("\r", "\n")
        encoded_name = name.encode("utf-8")
        encoded_source = normalized.encode("utf-8")
        payload.extend(len(encoded_name).to_bytes(4, "big"))
        payload.extend(encoded_name)
        payload.extend(len(encoded_source).to_bytes(8, "big"))
        payload.extend(encoded_source)
    return "sha256:" + hashlib.sha256(payload).hexdigest()


GENERATOR_BUNDLE_HASH = _normalized_generator_bundle_hash()


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        _utf8_bytes(canonical_json(value), "canonical JSON")
    ).hexdigest()


def _strict_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} keys must be strings")
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"invalid {label} keys; missing={missing}, extra={extra}")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if abs(value) > MAX_INTEGER_ABS:
        raise ValueError(f"{label} exceeds the integer budget")
    return value


def _string(
    value: Any,
    label: str,
    *,
    max_bytes: int = MAX_SHORT_STRING_BYTES,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    byte_length = len(_utf8_bytes(value, label))
    if byte_length > max_bytes:
        raise ValueError(f"{label} exceeds the {max_bytes}-byte budget")
    if not allow_empty and not value.strip():
        raise ValueError(f"{label} must not be empty")
    return value


def _sha256_string(value: Any, label: str) -> str:
    text = _string(value, label, max_bytes=71)
    if len(text) != 71 or not text.startswith("sha256:"):
        raise ValueError(f"{label} must be a SHA-256 digest")
    try:
        bytes.fromhex(text.removeprefix("sha256:"))
    except ValueError as exc:
        raise ValueError(f"{label} must be hexadecimal") from exc
    return text


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        value[key] = item
    return value


def _strict_json_loads(raw: Any, *, label: str, max_bytes: int) -> Any:
    text = _string(raw, f"{label} JSON", max_bytes=max_bytes)
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid {label} JSON: {exc.msg}") from exc
    except RecursionError as exc:
        raise ValueError(f"invalid {label} JSON: nesting budget exceeded") from exc


class CounterRNG:
    """A reproducible SHA-256 counter stream with unbiased bounded sampling.

    A separate stream label is used for every item, so generating item 9000 does not require
    generating items 0 through 8999 and parallel generation cannot change the result.
    """

    _DOMAIN = b"math-research-brain/counter-rng/v1\x00"

    def __init__(self, seed: str | bytes, *, stream: str | bytes = b"") -> None:
        if not isinstance(seed, (str, bytes)) or not isinstance(stream, (str, bytes)):
            raise ValueError("seed and stream must be strings or bytes")
        seed_bytes = _utf8_bytes(seed, "seed") if isinstance(seed, str) else seed
        stream_bytes = _utf8_bytes(stream, "stream") if isinstance(stream, str) else stream
        if not seed_bytes:
            raise ValueError("seed must not be empty")
        if len(seed_bytes) > MAX_SHORT_STRING_BYTES or len(stream_bytes) > MAX_SHORT_STRING_BYTES:
            raise ValueError("seed and stream must each fit the 1024-byte budget")
        self._key = _sha256(
            self._DOMAIN
            + len(seed_bytes).to_bytes(4, "big")
            + seed_bytes
            + len(stream_bytes).to_bytes(4, "big")
            + stream_bytes
        )
        self._counter = 0

    def _block(self) -> bytes:
        if self._counter >= 1 << 128:
            raise OverflowError("counter stream exhausted")
        block = _sha256(self._key + self._counter.to_bytes(16, "big"))
        self._counter += 1
        return block

    def randbelow(self, upper: int) -> int:
        """Return a uniform integer in ``range(upper)`` using rejection sampling."""

        if isinstance(upper, bool) or not isinstance(upper, int) or upper <= 0:
            raise ValueError("upper must be a positive integer")
        if upper.bit_length() > MAX_RNG_BOUND_BITS:
            raise ValueError(
                f"upper exceeds the {MAX_RNG_BOUND_BITS}-bit counter-RNG width budget"
            )
        space = 1 << 256
        if upper > space:
            bit_count = upper.bit_length()
            block_count = (bit_count + 255) // 256
            mask = (1 << bit_count) - 1
            while True:
                candidate = int.from_bytes(
                    b"".join(self._block() for _ in range(block_count)),
                    "big",
                ) & mask
                if candidate < upper:
                    return candidate
        limit = space - (space % upper)
        while True:
            candidate = int.from_bytes(self._block(), "big")
            if candidate < limit:
                return candidate % upper

    def randint(self, lower: int, upper: int) -> int:
        if lower > upper:
            raise ValueError("lower must be at most upper")
        return lower + self.randbelow(upper - lower + 1)

    def choice(self, values: Sequence[Any]) -> Any:
        if not values:
            raise ValueError("cannot choose from an empty sequence")
        return values[self.randbelow(len(values))]


class ExprOp(StrEnum):
    CONST = "const"
    VAR = "var"
    ADD = "add"
    SUB = "sub"
    MUL = "mul"
    DIV = "div"
    POW = "pow"
    NEG = "neg"


_ARITY = {
    ExprOp.CONST: 0,
    ExprOp.VAR: 0,
    ExprOp.ADD: 2,
    ExprOp.SUB: 2,
    ExprOp.MUL: 2,
    ExprOp.DIV: 2,
    ExprOp.POW: 2,
    ExprOp.NEG: 1,
}


@dataclass(frozen=True, slots=True)
class Expr:
    """Small, total expression tree; no source-language evaluation is used."""

    op: ExprOp
    args: tuple[Expr, ...] = ()
    value: int | str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.op, ExprOp):
            raise ValueError("expression op must be an ExprOp")
        if not isinstance(self.args, tuple) or not all(
            isinstance(argument, Expr) for argument in self.args
        ):
            raise ValueError("expression args must be a tuple of expressions")
        if len(self.args) != _ARITY[self.op]:
            raise ValueError(f"{self.op.value} expects {_ARITY[self.op]} arguments")
        if self.op is ExprOp.CONST:
            _integer(self.value, "constant expression value")
        elif self.op is ExprOp.VAR:
            variable = _string(self.value, "variable expression value", max_bytes=128)
            if not variable.isidentifier():
                raise ValueError("variable expression value must be an identifier")
        elif self.value is not None:
            raise ValueError(f"{self.op.value} expression must not carry a value")

    @classmethod
    def const(cls, value: int) -> Expr:
        return cls(ExprOp.CONST, value=value)

    @classmethod
    def var(cls, name: str) -> Expr:
        return cls(ExprOp.VAR, value=name)

    def to_dict(self) -> dict[str, Any]:
        _validate_expression_budget(self)
        result: dict[str, Any] = {"op": self.op.value}
        if self.op in {ExprOp.CONST, ExprOp.VAR}:
            result["value"] = self.value
        else:
            result["args"] = [arg.to_dict() for arg in self.args]
        return result

    @classmethod
    def from_dict(cls, raw: Any) -> Expr:
        return cls._from_dict(raw, depth=1, remaining=[MAX_EXPRESSION_NODES])

    @classmethod
    def _from_dict(cls, raw: Any, *, depth: int, remaining: list[int]) -> Expr:
        if depth > MAX_EXPRESSION_DEPTH:
            raise ValueError("expression exceeds the depth budget")
        remaining[0] -= 1
        if remaining[0] < 0:
            raise ValueError("expression exceeds the node budget")
        value = _mapping(raw, "expression")
        if "op" not in value:
            raise ValueError("expression is missing op")
        try:
            op = ExprOp(_string(value["op"], "expression op", max_bytes=16))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unsupported expression op: {value.get('op')!r}") from exc
        if op in {ExprOp.CONST, ExprOp.VAR}:
            _strict_keys(value, {"op", "value"}, "expression")
            expression_value = value["value"]
            if op is ExprOp.CONST:
                expression_value = _integer(expression_value, "constant")
            return cls(op=op, value=expression_value)
        _strict_keys(value, {"op", "args"}, "expression")
        args = value["args"]
        if not isinstance(args, list):
            raise ValueError("expression args must be an array")
        if len(args) != _ARITY[op]:
            raise ValueError(f"{op.value} expects {_ARITY[op]} arguments")
        return cls(
            op=op,
            args=tuple(
                cls._from_dict(argument, depth=depth + 1, remaining=remaining)
                for argument in args
            ),
        )


def _validate_expression_budget(expression: Any) -> None:
    if not isinstance(expression, Expr):
        raise ValueError("expression must be an Expr")
    stack = [(expression, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if depth > MAX_EXPRESSION_DEPTH:
            raise ValueError("expression exceeds the depth budget")
        if nodes > MAX_EXPRESSION_NODES:
            raise ValueError("expression exceeds the node budget")
        stack.extend((argument, depth + 1) for argument in current.args)


def _binary(op: ExprOp, left: Expr, right: Expr) -> Expr:
    return Expr(op=op, args=(left, right))


def _add(left: Expr, right: Expr) -> Expr:
    return _binary(ExprOp.ADD, left, right)


def _sub(left: Expr, right: Expr) -> Expr:
    return _binary(ExprOp.SUB, left, right)


def _mul(left: Expr, right: Expr) -> Expr:
    return _binary(ExprOp.MUL, left, right)


def _div(left: Expr, right: Expr) -> Expr:
    return _binary(ExprOp.DIV, left, right)


def _pow(left: Expr, right: Expr) -> Expr:
    return _binary(ExprOp.POW, left, right)


def _neg(value: Expr) -> Expr:
    return Expr(op=ExprOp.NEG, args=(value,))


def _bounded_fraction(value: Fraction, label: str) -> Fraction:
    numerator_bits = abs(value.numerator).bit_length()
    denominator_bits = value.denominator.bit_length()
    if numerator_bits > MAX_FRACTION_BITS or denominator_bits > MAX_FRACTION_BITS:
        raise ValueError(
            f"{label} exceeds the {MAX_FRACTION_BITS}-bit exact-fraction budget"
        )
    return value


def _preflight_power(base: Fraction, exponent: int) -> None:
    if exponent == 0 or base.numerator == 0:
        return
    numerator_bound = max(1, abs(base.numerator).bit_length()) * exponent
    denominator_bound = base.denominator.bit_length() * exponent
    if numerator_bound > MAX_FRACTION_BITS or denominator_bound > MAX_FRACTION_BITS:
        raise ValueError("power would exceed the exact-fraction bit budget")


def evaluate_expression(expression: Expr, environment: Mapping[str, int]) -> Fraction:
    """Interpret MathIR without ``eval`` or any extensible dispatch."""

    _validate_expression_budget(expression)
    if not isinstance(environment, Mapping):
        raise ValueError("expression environment must be a mapping")

    def visit(current: Expr) -> Fraction:
        if current.op is ExprOp.CONST:
            return _bounded_fraction(Fraction(int(current.value)), "constant")
        if current.op is ExprOp.VAR:
            name = str(current.value)
            if name not in environment:
                raise ValueError(f"unbound variable: {name}")
            value = environment[name]
            return _bounded_fraction(
                Fraction(_integer(value, f"environment value for {name}")),
                f"environment value for {name}",
            )
        values = tuple(visit(argument) for argument in current.args)
        if current.op is ExprOp.ADD:
            return _bounded_fraction(values[0] + values[1], "addition result")
        if current.op is ExprOp.SUB:
            return _bounded_fraction(values[0] - values[1], "subtraction result")
        if current.op is ExprOp.MUL:
            return _bounded_fraction(values[0] * values[1], "multiplication result")
        if current.op is ExprOp.DIV:
            if values[1] == 0:
                raise ValueError("division by zero")
            return _bounded_fraction(values[0] / values[1], "division result")
        if current.op is ExprOp.POW:
            exponent = values[1]
            if exponent.denominator != 1 or not 0 <= exponent <= 12:
                raise ValueError("exponent must be an integer in [0, 12]")
            integer_exponent = int(exponent)
            _preflight_power(values[0], integer_exponent)
            return _bounded_fraction(
                values[0] ** integer_exponent,
                "power result",
            )
        if current.op is ExprOp.NEG:
            return _bounded_fraction(-values[0], "negation result")
        raise ValueError(f"unsupported expression op: {current.op!r}")

    return visit(expression)


def render_expression(expression: Expr) -> str:
    _validate_expression_budget(expression)

    def visit(current: Expr) -> str:
        if current.op is ExprOp.CONST:
            return str(current.value)
        if current.op is ExprOp.VAR:
            return str(current.value)
        if current.op is ExprOp.NEG:
            return f"(-{visit(current.args[0])})"
        symbols = {
            ExprOp.ADD: "+",
            ExprOp.SUB: "-",
            ExprOp.MUL: "*",
            ExprOp.DIV: "/",
            ExprOp.POW: "^",
        }
        left, right = current.args
        return f"({visit(left)} {symbols[current.op]} {visit(right)})"

    return visit(expression)


@dataclass(frozen=True, slots=True)
class Relation:
    left: Expr
    right: Expr
    comparator: str = "="

    def __post_init__(self) -> None:
        if not isinstance(self.left, Expr) or not isinstance(self.right, Expr):
            raise ValueError("relation sides must be expressions")
        _validate_expression_budget(self.left)
        _validate_expression_budget(self.right)
        if _string(self.comparator, "relation comparator", max_bytes=4) != "=":
            raise ValueError("MathIR v1 supports equality relations only")

    def to_dict(self) -> dict[str, Any]:
        return {
            "comparator": self.comparator,
            "left": self.left.to_dict(),
            "right": self.right.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> Relation:
        value = _mapping(raw, "relation")
        _strict_keys(value, {"comparator", "left", "right"}, "relation")
        return cls(
            left=Expr.from_dict(value["left"]),
            right=Expr.from_dict(value["right"]),
            comparator=value["comparator"],
        )


def _relation(left: Expr, right: Expr) -> Relation:
    return Relation(left=left, right=right)


def evaluate_relation(relation: Relation, environment: Mapping[str, int]) -> bool:
    if not isinstance(relation, Relation):
        raise ValueError("relation must be a Relation")
    return evaluate_expression(relation.left, environment) == evaluate_expression(
        relation.right, environment
    )


def render_relation(relation: Relation) -> str:
    if not isinstance(relation, Relation):
        raise ValueError("relation must be a Relation")
    return (
        f"{render_expression(relation.left)} {relation.comparator} "
        f"{render_expression(relation.right)}"
    )


@dataclass(frozen=True, slots=True)
class SolutionStep:
    number: int
    text: str
    assertion: Relation

    def __post_init__(self) -> None:
        number = _integer(self.number, "solution step number")
        if number <= 0:
            raise ValueError("solution step number must be positive")
        text = _string(self.text, "solution step text", max_bytes=MAX_STRING_BYTES)
        if not text.startswith(f"{number}. "):
            raise ValueError("solution step text must carry its canonical number prefix")
        if not isinstance(self.assertion, Relation):
            raise ValueError("solution step assertion must be a relation")

    def to_dict(self) -> dict[str, Any]:
        return {
            "assertion": self.assertion.to_dict(),
            "number": self.number,
            "text": self.text,
        }

    def digest(self) -> str:
        return _digest(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Any) -> SolutionStep:
        value = _mapping(raw, "solution step")
        _strict_keys(value, {"assertion", "number", "text"}, "solution step")
        return cls(
            number=_integer(value["number"], "solution step number"),
            text=value["text"],
            assertion=Relation.from_dict(value["assertion"]),
        )


@dataclass(frozen=True, slots=True)
class Solution:
    steps: tuple[SolutionStep, ...]
    conclusion: str

    def __post_init__(self) -> None:
        if not isinstance(self.steps, tuple) or not all(
            isinstance(step, SolutionStep) for step in self.steps
        ):
            raise ValueError("solution steps must be a tuple of solution steps")
        if not self.steps:
            raise ValueError("solution must contain at least one step")
        if len(self.steps) > MAX_SOLUTION_STEPS:
            raise ValueError("solution exceeds the step-count budget")
        if tuple(step.number for step in self.steps) != tuple(range(1, len(self.steps) + 1)):
            raise ValueError("solution steps must be consecutively numbered from one")
        _string(self.conclusion, "solution conclusion", max_bytes=MAX_STRING_BYTES)

    def to_dict(self) -> dict[str, Any]:
        return {
            "conclusion": self.conclusion,
            "steps": [step.to_dict() for step in self.steps],
        }

    def digest(self) -> str:
        return _digest(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Any) -> Solution:
        value = _mapping(raw, "solution")
        _strict_keys(value, {"conclusion", "steps"}, "solution")
        steps = value["steps"]
        if not isinstance(steps, list):
            raise ValueError("solution steps must be an array")
        if not 1 <= len(steps) <= MAX_SOLUTION_STEPS:
            raise ValueError("solution must contain between 1 and 64 steps")
        return cls(
            steps=tuple(SolutionStep.from_dict(step) for step in steps),
            conclusion=value["conclusion"],
        )


class Difficulty(StrEnum):
    ELEMENTARY = "elementary"
    UNDERGRADUATE = "undergraduate"


def _semantic_fingerprint(
    family: str,
    parameters: tuple[tuple[str, int], ...],
) -> str:
    """Bind semantic identity to the family and canonical public givens only."""

    return _digest(
        {
            "givens": {name: value for name, value in parameters},
            "schema": SEMANTIC_SCHEMA,
            "schema_version": SEMANTIC_SCHEMA_VERSION,
            "family": family,
        }
    )


@dataclass(frozen=True, slots=True)
class Problem:
    family: str
    difficulty: Difficulty
    prompt: str
    answer: str
    parameters: tuple[tuple[str, int], ...]
    witness: tuple[tuple[str, int], ...]
    semantic_fingerprint: str

    def __post_init__(self) -> None:
        family = _string(self.family, "problem family", max_bytes=128)
        if not family.replace("_", "").isalnum():
            raise ValueError("problem family must be a non-empty slug")
        if not isinstance(self.difficulty, Difficulty):
            raise ValueError("difficulty must be a Difficulty")
        _string(self.prompt, "problem prompt", max_bytes=MAX_STRING_BYTES)
        _string(self.answer, "problem answer", max_bytes=MAX_STRING_BYTES)
        for label, pairs in (("parameters", self.parameters), ("witness", self.witness)):
            if not isinstance(pairs, tuple) or len(pairs) > MAX_SOLUTION_STEPS:
                raise ValueError(f"problem {label} must be a bounded tuple")
            if not all(
                isinstance(pair, tuple) and len(pair) == 2 for pair in pairs
            ):
                raise ValueError(f"problem {label} entries must be name-value pairs")
            names = [
                _string(name, f"problem {label} name", max_bytes=128)
                for name, _ in pairs
            ]
            if names != sorted(names) or len(names) != len(set(names)):
                raise ValueError(f"problem {label} must be uniquely named and sorted")
            if any(not name.isidentifier() for name in names):
                raise ValueError(f"problem {label} names must be identifiers")
            for name, entry_value in pairs:
                _integer(entry_value, f"problem {label} value for {name}")
        _sha256_string(self.semantic_fingerprint, "problem semantic fingerprint")
        expected_fingerprint = _semantic_fingerprint(family, self.parameters)
        if not hmac.compare_digest(self.semantic_fingerprint, expected_fingerprint):
            raise ValueError("problem semantic fingerprint does not match canonical givens")

    @property
    def parameter_map(self) -> dict[str, int]:
        return dict(self.parameters)

    @property
    def witness_map(self) -> dict[str, int]:
        return dict(self.witness)

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "difficulty": self.difficulty.value,
            "family": self.family,
            "parameters": {name: value for name, value in self.parameters},
            "prompt": self.prompt,
            "semantic_fingerprint": self.semantic_fingerprint,
            "witness": {name: value for name, value in self.witness},
        }

    @classmethod
    def from_dict(cls, raw: Any) -> Problem:
        value = _mapping(raw, "problem")
        _strict_keys(
            value,
            {
                "answer",
                "difficulty",
                "family",
                "parameters",
                "prompt",
                "semantic_fingerprint",
                "witness",
            },
            "problem",
        )
        parameters = _mapping(value["parameters"], "problem parameters")
        witness = _mapping(value["witness"], "problem witness")
        try:
            difficulty = Difficulty(value["difficulty"])
        except (TypeError, ValueError) as exc:
            raise ValueError("unsupported problem difficulty") from exc
        return cls(
            family=value["family"],
            difficulty=difficulty,
            prompt=value["prompt"],
            answer=value["answer"],
            parameters=_pairs(parameters),
            witness=_pairs(witness),
            semantic_fingerprint=value["semantic_fingerprint"],
        )


@dataclass(frozen=True, slots=True)
class Generation:
    generator_version: str
    seed: str
    index: int
    family: str

    def __post_init__(self) -> None:
        if _string(self.generator_version, "generator version", max_bytes=128) != GENERATOR_VERSION:
            raise ValueError(f"unsupported generator version: {self.generator_version}")
        _string(self.seed, "generation seed", max_bytes=MAX_SHORT_STRING_BYTES)
        if _integer(self.index, "generation index") < 0:
            raise ValueError("generation index must be non-negative")
        _string(self.family, "generation family", max_bytes=128)

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "generator_version": self.generator_version,
            "index": self.index,
            "seed": self.seed,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> Generation:
        value = _mapping(raw, "generation")
        _strict_keys(value, {"family", "generator_version", "index", "seed"}, "generation")
        return cls(
            generator_version=value["generator_version"],
            seed=value["seed"],
            index=_integer(value["index"], "generation index"),
            family=value["family"],
        )


class FaultKind(StrEnum):
    OFF_BY_ONE = "off_by_one"
    SIGN_ERROR = "sign_error"
    DROPPED_FACTOR = "dropped_factor"
    WRONG_OPERATION = "wrong_operation"


FAULT_ORDER = (
    FaultKind.OFF_BY_ONE,
    FaultKind.SIGN_ERROR,
    FaultKind.DROPPED_FACTOR,
    FaultKind.WRONG_OPERATION,
)

# The other mutations remain useful engineering fuzz cases, but held-out shortcut baselines
# learn their sign counts or expression skeletons.  Only this stratum supports solver-valid scores.
SOLVER_VALID_FAULTS = (FaultKind.OFF_BY_ONE,)

_FAULT_MUTATIONS = {
    FaultKind.OFF_BY_ONE: "increment_integer_literal",
    FaultKind.SIGN_ERROR: "negate_integer_literal",
    FaultKind.DROPPED_FACTOR: "drop_multiplicative_factor",
    FaultKind.WRONG_OPERATION: "swap_add_sub_operator",
}


@dataclass(frozen=True, slots=True)
class Fault:
    step_number: int
    kind: FaultKind
    mutation: str
    ast_path: tuple[int, ...]
    description: str

    def __post_init__(self) -> None:
        if _integer(self.step_number, "fault step number") <= 0:
            raise ValueError("fault requires a positive step number and description")
        if not isinstance(self.kind, FaultKind):
            raise ValueError("fault kind must be a FaultKind")
        mutation = _string(self.mutation, "fault mutation", max_bytes=128)
        if mutation != _FAULT_MUTATIONS[self.kind]:
            raise ValueError("fault mutation does not match its semantic kind")
        if not isinstance(self.ast_path, tuple) or not 1 <= len(self.ast_path) <= (
            MAX_EXPRESSION_DEPTH + 1
        ):
            raise ValueError("fault AST path must be a bounded tuple")
        if any(isinstance(part, bool) or part not in {0, 1} for part in self.ast_path):
            raise ValueError("fault AST path components must be zero or one")
        _string(self.description, "fault description", max_bytes=MAX_STRING_BYTES)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ast_path": list(self.ast_path),
            "description": self.description,
            "kind": self.kind.value,
            "mutation": self.mutation,
            "step_number": self.step_number,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> Fault:
        value = _mapping(raw, "fault")
        _strict_keys(
            value,
            {"ast_path", "description", "kind", "mutation", "step_number"},
            "fault",
        )
        path = value["ast_path"]
        if not isinstance(path, list):
            raise ValueError("fault AST path must be an array")
        try:
            kind = FaultKind(value["kind"])
        except (TypeError, ValueError) as exc:
            raise ValueError("unsupported fault kind") from exc
        return cls(
            step_number=_integer(value["step_number"], "fault step number"),
            kind=kind,
            mutation=value["mutation"],
            ast_path=tuple(_integer(part, "fault AST path component") for part in path),
            description=value["description"],
        )


@dataclass(frozen=True, slots=True)
class RepairReplay:
    expected_faulty_digest: str
    step_number: int
    expected_faulty_step_digest: str
    replacement: SolutionStep
    expected_repaired_digest: str

    def __post_init__(self) -> None:
        _sha256_string(self.expected_faulty_digest, "expected faulty solution digest")
        if _integer(self.step_number, "repair step number") <= 0:
            raise ValueError("repair step number must be positive")
        _sha256_string(self.expected_faulty_step_digest, "expected faulty step digest")
        if not isinstance(self.replacement, SolutionStep):
            raise ValueError("repair replacement must be a solution step")
        _sha256_string(self.expected_repaired_digest, "expected repaired solution digest")

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_faulty_digest": self.expected_faulty_digest,
            "expected_faulty_step_digest": self.expected_faulty_step_digest,
            "expected_repaired_digest": self.expected_repaired_digest,
            "replacement": self.replacement.to_dict(),
            "step_number": self.step_number,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> RepairReplay:
        value = _mapping(raw, "repair replay")
        _strict_keys(
            value,
            {
                "expected_faulty_digest",
                "expected_faulty_step_digest",
                "expected_repaired_digest",
                "replacement",
                "step_number",
            },
            "repair replay",
        )
        return cls(
            expected_faulty_digest=value["expected_faulty_digest"],
            step_number=_integer(value["step_number"], "repair step number"),
            expected_faulty_step_digest=value["expected_faulty_step_digest"],
            replacement=SolutionStep.from_dict(value["replacement"]),
            expected_repaired_digest=value["expected_repaired_digest"],
        )


@dataclass(frozen=True, slots=True)
class Provenance:
    """Public-record origin and rights metadata, bound into the item digest."""

    kind: str = "synthetic"
    license_expression: str = PRIVATE_DATA_LICENSE
    generator_source: str = "src/math_brain/benchmark.py"
    generator_source_hash: str = GENERATOR_SOURCE_HASH
    generator_bundle_hash: str = GENERATOR_BUNDLE_HASH

    def __post_init__(self) -> None:
        if _string(self.kind, "provenance kind", max_bytes=64) != "synthetic":
            raise ValueError("benchmark provenance kind must be synthetic")
        license_expression = _string(
            self.license_expression,
            "provenance license expression",
            max_bytes=256,
        )
        if license_expression != PRIVATE_DATA_LICENSE:
            raise ValueError("noncanonical private benchmark license")
        if _string(self.generator_source, "generator source", max_bytes=256) != (
            "src/math_brain/benchmark.py"
        ):
            raise ValueError("unexpected benchmark generator source")
        source_hash = _sha256_string(self.generator_source_hash, "generator source hash")
        if not hmac.compare_digest(source_hash, GENERATOR_SOURCE_HASH):
            raise ValueError("generator source hash does not match the current trusted source")
        bundle_hash = _sha256_string(self.generator_bundle_hash, "generator bundle hash")
        if not hmac.compare_digest(bundle_hash, GENERATOR_BUNDLE_HASH):
            raise ValueError("generator bundle hash does not match the trusted verifier bundle")

    def to_dict(self) -> dict[str, Any]:
        return {
            "generator_bundle_hash": self.generator_bundle_hash,
            "generator_source": self.generator_source,
            "generator_source_hash": self.generator_source_hash,
            "kind": self.kind,
            "license_expression": self.license_expression,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> Provenance:
        value = _mapping(raw, "provenance")
        _strict_keys(
            value,
            {
                "generator_bundle_hash",
                "generator_source",
                "generator_source_hash",
                "kind",
                "license_expression",
            },
            "provenance",
        )
        return cls(
            kind=value["kind"],
            license_expression=value["license_expression"],
            generator_bundle_hash=value["generator_bundle_hash"],
            generator_source=value["generator_source"],
            generator_source_hash=value["generator_source_hash"],
        )


@dataclass(frozen=True, slots=True)
class SolverView:
    """The entire and only payload a benchmark solver is allowed to receive."""

    prompt: str
    numbered_solution: tuple[str, ...]
    schema: str = SOLVER_VIEW_SCHEMA
    schema_version: int = SOLVER_VIEW_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _string(self.prompt, "solver prompt", max_bytes=MAX_STRING_BYTES)
        if not isinstance(self.numbered_solution, tuple):
            raise ValueError("numbered solution must be a tuple")
        if not 1 <= len(self.numbered_solution) <= MAX_SOLUTION_STEPS:
            raise ValueError("numbered solution must contain between 1 and 64 steps")
        for number, line in enumerate(self.numbered_solution, 1):
            text = _string(line, "solver solution line", max_bytes=MAX_STRING_BYTES)
            if not text.startswith(f"{number}. "):
                raise ValueError("solver solution lines must be consecutively numbered")
        if self.schema != SOLVER_VIEW_SCHEMA or self.schema_version != SOLVER_VIEW_SCHEMA_VERSION:
            raise ValueError("unsupported solver-view schema")
        serialized_size = len(
            _utf8_bytes(canonical_json(self.to_dict()), "solver view JSON")
        )
        if serialized_size > MAX_SOLVER_VIEW_JSON_BYTES:
            raise ValueError(
                f"solver view exceeds the {MAX_SOLVER_VIEW_JSON_BYTES}-byte aggregate budget"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "numbered_solution": list(self.numbered_solution),
            "prompt": self.prompt,
            "schema": self.schema,
            "schema_version": self.schema_version,
        }

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Any) -> SolverView:
        value = _mapping(raw, "solver view")
        _strict_keys(
            value,
            {"numbered_solution", "prompt", "schema", "schema_version"},
            "solver view",
        )
        lines = value["numbered_solution"]
        if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
            raise ValueError("numbered solution must be an array of strings")
        return cls(
            prompt=value["prompt"],
            numbered_solution=tuple(lines),
            schema=value["schema"],
            schema_version=_integer(value["schema_version"], "solver-view schema version"),
        )

    @classmethod
    def from_json(cls, raw: str) -> SolverView:
        return cls.from_dict(
            _strict_json_loads(
                raw,
                label="solver view",
                max_bytes=MAX_SOLVER_VIEW_JSON_BYTES,
            )
        )


def opaque_solver_id(run_nonce: bytes, item_id: str) -> str:
    """Derive a run-local opaque identifier without exposing stable coordinates."""

    if not isinstance(run_nonce, bytes) or not 16 <= len(run_nonce) <= 256:
        raise ValueError("run nonce must contain between 16 and 256 bytes")
    stable_id = _string(item_id, "stable benchmark item id", max_bytes=80)
    digest = hmac.new(
        run_nonce,
        b"math-research-brain/solver-id/v1\x00" + stable_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return "solver_" + digest


@dataclass(frozen=True, slots=True)
class SolverAssignment:
    """Opaque run-local handle paired with the only solver-visible payload."""

    solver_id: str
    view: SolverView

    def __post_init__(self) -> None:
        solver_id = _string(self.solver_id, "opaque solver id", max_bytes=71)
        if len(solver_id) != 71 or not solver_id.startswith("solver_"):
            raise ValueError("opaque solver id has an invalid shape")
        try:
            bytes.fromhex(solver_id.removeprefix("solver_"))
        except ValueError as exc:
            raise ValueError("opaque solver id must be hexadecimal") from exc
        if not isinstance(self.view, SolverView):
            raise ValueError("solver assignment requires a SolverView")

    def to_dict(self) -> dict[str, Any]:
        return {"solver_id": self.solver_id, "view": self.view.to_dict()}


def _benchmark_item_payload(
    *,
    item_id: str,
    generation: Generation,
    problem: Problem,
    clean_solution: Solution,
    faulty_solution: Solution,
    fault: Fault,
    repair: RepairReplay,
    validators: tuple[str, ...],
    provenance: Provenance,
    schema: str = MATHIR_SCHEMA,
    schema_version: int = MATHIR_SCHEMA_VERSION,
) -> dict[str, Any]:
    return {
        "clean_solution": clean_solution.to_dict(),
        "fault": fault.to_dict(),
        "faulty_solution": faulty_solution.to_dict(),
        "generation": generation.to_dict(),
        "item_id": item_id,
        "problem": problem.to_dict(),
        "provenance": provenance.to_dict(),
        "repair": repair.to_dict(),
        "schema": schema,
        "schema_version": schema_version,
        "validators": list(validators),
    }


@dataclass(frozen=True, slots=True)
class BenchmarkItem:
    item_id: str
    generation: Generation
    problem: Problem
    clean_solution: Solution
    faulty_solution: Solution
    fault: Fault
    repair: RepairReplay
    content_digest: str
    validators: tuple[str, ...] = (
        "assertion-evaluator-v1",
        "independent-family-verifier-v1",
    )
    provenance: Provenance = field(default_factory=Provenance)
    schema: str = MATHIR_SCHEMA
    schema_version: int = MATHIR_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema != MATHIR_SCHEMA or self.schema_version != MATHIR_SCHEMA_VERSION:
            raise ValueError("unsupported MathIR v4 benchmark schema; regenerate legacy records")
        _string(self.item_id, "item id", max_bytes=80)
        if not all(
            (
                isinstance(self.generation, Generation),
                isinstance(self.problem, Problem),
                isinstance(self.clean_solution, Solution),
                isinstance(self.faulty_solution, Solution),
                isinstance(self.fault, Fault),
                isinstance(self.repair, RepairReplay),
                isinstance(self.provenance, Provenance),
            )
        ):
            raise ValueError("benchmark item contains an invalid component type")
        if self.item_id != _item_id(self.generation):
            raise ValueError("item id does not match generation coordinates")
        if self.generation.family != self.problem.family:
            raise ValueError("generation and problem families disagree")
        if self.validators != (
            "assertion-evaluator-v1",
            "independent-family-verifier-v1",
        ):
            raise ValueError("MathIR v4 requires both independent validator identifiers")
        expected_digest = _digest(self.content_payload())
        _sha256_string(self.content_digest, "benchmark content digest")
        if not hmac.compare_digest(self.content_digest, expected_digest):
            raise ValueError("benchmark content digest does not match immutable item content")

    def content_payload(self) -> dict[str, Any]:
        return _benchmark_item_payload(
            item_id=self.item_id,
            generation=self.generation,
            problem=self.problem,
            clean_solution=self.clean_solution,
            faulty_solution=self.faulty_solution,
            fault=self.fault,
            repair=self.repair,
            validators=self.validators,
            provenance=self.provenance,
            schema=self.schema,
            schema_version=self.schema_version,
        )

    def to_dict(self) -> dict[str, Any]:
        value = self.content_payload()
        value["content_digest"] = self.content_digest
        return value

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())

    def digest(self) -> str:
        return _digest(self.to_dict())

    def solver_view(self, *, use_faulty: bool) -> SolverView:
        if not isinstance(use_faulty, bool):
            raise ValueError("use_faulty must be a boolean")
        solution = self.faulty_solution if use_faulty else self.clean_solution
        return SolverView(
            prompt=self.problem.prompt,
            numbered_solution=tuple(step.text for step in solution.steps),
        )

    def solver_assignment(self, run_nonce: bytes, *, use_faulty: bool) -> SolverAssignment:
        return SolverAssignment(
            solver_id=opaque_solver_id(run_nonce, self.item_id),
            view=self.solver_view(use_faulty=use_faulty),
        )

    @classmethod
    def from_dict(cls, raw: Any) -> BenchmarkItem:
        value = _mapping(raw, "benchmark item")
        version = _integer(value.get("schema_version"), "schema version")
        if version in {1, 2, 3}:
            raise ValueError("legacy MathIR records must be regenerated from their coordinates")
        if version != MATHIR_SCHEMA_VERSION:
            raise ValueError("unsupported MathIR benchmark schema version")
        _strict_keys(
            value,
            {
                "clean_solution",
                "content_digest",
                "fault",
                "faulty_solution",
                "generation",
                "item_id",
                "problem",
                "provenance",
                "repair",
                "schema",
                "schema_version",
                "validators",
            },
            "benchmark item",
        )
        validators = value["validators"]
        if not isinstance(validators, list) or not all(
            isinstance(item, str) for item in validators
        ):
            raise ValueError("validators must be an array of strings")
        item = cls(
            item_id=value["item_id"],
            generation=Generation.from_dict(value["generation"]),
            problem=Problem.from_dict(value["problem"]),
            clean_solution=Solution.from_dict(value["clean_solution"]),
            faulty_solution=Solution.from_dict(value["faulty_solution"]),
            fault=Fault.from_dict(value["fault"]),
            repair=RepairReplay.from_dict(value["repair"]),
            content_digest=value["content_digest"],
            validators=tuple(validators),
            provenance=Provenance.from_dict(value["provenance"]),
            schema=value["schema"],
            schema_version=version,
        )
        certify_item(item)
        return item

    @classmethod
    def from_json(cls, raw: str) -> BenchmarkItem:
        return cls.from_dict(
            _strict_json_loads(raw, label="benchmark", max_bytes=MAX_ITEM_JSON_BYTES)
        )


@dataclass(frozen=True, slots=True)
class ValidationReport:
    validator: str
    passed: bool
    failing_steps: tuple[int, ...]
    detail: str


def _pairs(values: Mapping[str, Any]) -> tuple[tuple[str, int], ...]:
    if len(values) > MAX_SOLUTION_STEPS:
        raise ValueError("parameter mapping exceeds the entry budget")
    if not all(isinstance(name, str) for name in values):
        raise ValueError("parameter names must be strings")
    result = []
    for name, value in sorted(values.items()):
        name = _string(name, "parameter name", max_bytes=128)
        result.append((name, _integer(value, f"parameter {name}")))
    return tuple(result)


def _item_id(generation: Generation) -> str:
    # The visible prefix is a structural fence across incompatible generator namespaces.
    return "mathir_v4_" + hashlib.sha256(
        _utf8_bytes(
            "item-id-v4-independent\x00" + canonical_json(generation.to_dict()),
            "item identity payload",
        )
    ).hexdigest()


def _step(number: int, assertion: Relation) -> SolutionStep:
    return SolutionStep(number, f"{number}. {render_relation(assertion)}.", assertion)


def _solution(relations: Sequence[Relation], conclusion: str) -> Solution:
    steps = tuple(_step(index, relation) for index, relation in enumerate(relations, 1))
    return Solution(steps, conclusion)


def differing_steps(clean: Solution, faulty: Solution) -> tuple[int, ...]:
    if not isinstance(clean, Solution) or not isinstance(faulty, Solution):
        raise ValueError("solution variants must be Solution values")
    if len(clean.steps) != len(faulty.steps) or clean.conclusion != faulty.conclusion:
        raise ValueError("solution variants must have equal length and conclusion")
    return tuple(
        clean_step.number
        for clean_step, faulty_step in zip(clean.steps, faulty.steps, strict=True)
        if clean_step != faulty_step
    )


def replay_repair(item: BenchmarkItem, solution: Solution | None = None) -> Solution:
    """Replay the recorded single-line repair with hash preconditions."""

    if not isinstance(item, BenchmarkItem):
        raise ValueError("repair item must be a BenchmarkItem")
    if solution is not None and not isinstance(solution, Solution):
        raise ValueError("repair input must be a Solution")
    source = item.faulty_solution if solution is None else solution
    repair = item.repair
    if not hmac.compare_digest(source.digest(), repair.expected_faulty_digest):
        raise ValueError("repair input digest does not match recorded faulty solution")
    if not 1 <= repair.step_number <= len(source.steps):
        raise ValueError("repair target step is out of range")
    target = source.steps[repair.step_number - 1]
    if not hmac.compare_digest(target.digest(), repair.expected_faulty_step_digest):
        raise ValueError("repair target line digest does not match")
    steps = list(source.steps)
    steps[repair.step_number - 1] = repair.replacement
    repaired = Solution(tuple(steps), source.conclusion)
    if not hmac.compare_digest(repaired.digest(), repair.expected_repaired_digest):
        raise ValueError("replayed repair does not match expected output")
    return repaired


def validate_assertions(problem: Problem, solution: Solution) -> ValidationReport:
    """Validator one: directly interpret each structured equality under the witness."""

    if not isinstance(problem, Problem) or not isinstance(solution, Solution):
        raise ValueError("assertion validation requires a Problem and Solution")
    failures: list[int] = []
    details: list[str] = []
    for step in solution.steps:
        canonical_text = f"{step.number}. {render_relation(step.assertion)}."
        if step.text != canonical_text:
            failures.append(step.number)
            details.append(f"step {step.number} text does not render its assertion")
            continue
        try:
            if not evaluate_relation(step.assertion, problem.witness_map):
                failures.append(step.number)
                details.append(f"step {step.number} equality is false under the exact witness")
        except ValueError as exc:
            failures.append(step.number)
            details.append(f"step {step.number} is not evaluable: {exc}")
    return ValidationReport(
        validator="assertion-evaluator-v1",
        passed=not failures,
        failing_steps=tuple(dict.fromkeys(failures)),
        detail="; ".join(details) if details else "all structured equalities evaluate exactly",
    )




@dataclass(frozen=True, slots=True)
class _ProblemExpectation:
    prompt: str
    answer: str
    difficulty: Difficulty
    witness: tuple[tuple[str, int], ...]
    relations: tuple[Relation, ...] = ()


def _require_parameters(parameters: Mapping[str, int], expected: set[str]) -> None:
    if set(parameters) != expected:
        raise ValueError(
            f"family parameter keys disagree; missing={sorted(expected - set(parameters))}, "
            f"extra={sorted(set(parameters) - expected)}"
        )




# MathIR v4 breadth families.  Coordinates select only public givens; every answer,
# witness, trace, and semantic identity is re-derived from those givens.
@dataclass(frozen=True, slots=True)
class FamilySpec:
    name: str
    domain_size: int
    unrank: Callable[[int], Mapping[str, int]]
    oracle: Callable[[Mapping[str, int]], _ProblemExpectation]

    def __post_init__(self) -> None:
        if not self.name.replace("_", "").isalnum():
            raise ValueError("family specification requires a slug name")
        if self.domain_size < 556:
            raise ValueError("each family domain must cover its full-bank quota")


def _mixed_unrank(index: int, sizes: Sequence[int]) -> tuple[int, ...]:
    capacity = 1
    for size in sizes:
        if size <= 0:
            raise ValueError("mixed-radix sizes must be positive")
        capacity *= size
    if not 0 <= index < capacity:
        raise ValueError("family rank falls outside its canonical domain")
    digits: list[int] = []
    remaining = index
    for size in sizes:
        digits.append(remaining % size)
        remaining //= size
    return tuple(digits)


def _bounded_power(base: int, exponent: int) -> int:
    value = base**exponent
    if abs(value) > MAX_INTEGER_ABS:
        raise ValueError("family arithmetic exceeds the integer budget")
    return value


def _family_expectation(
    *,
    prompt: str,
    answer: str,
    difficulty: Difficulty,
    witness: Mapping[str, int],
    relations: Sequence[Relation],
) -> _ProblemExpectation:
    if not witness or not relations:
        raise ValueError("family certificate requires a witness and meaningful relations")
    return _ProblemExpectation(
        prompt=prompt,
        answer=answer,
        difficulty=difficulty,
        witness=_pairs(witness),
        relations=tuple(relations),
    )


def _unrank_progression(index: int) -> Mapping[str, int]:
    ai, di, ni = _mixed_unrank(index, (30, 12, 28))
    return {"a": ai + 1, "d": di + 1, "n": ni + 3}


def _oracle_progression_v4(parameters: Mapping[str, int]) -> _ProblemExpectation:
    _require_parameters(parameters, {"a", "d", "n"})
    a, d, n = (parameters[name] for name in ("a", "d", "n"))
    if not (1 <= a <= 30 and 1 <= d <= 12 and 3 <= n <= 30):
        raise ValueError("progression givens fall outside the canonical domain")
    last = a + (n - 1) * d
    total = n * (a + last) // 2
    return _family_expectation(
        prompt=(
            f"Find the sum of the first {n} terms of the arithmetic progression "
            f"with first term {a} and common difference {d}."
        ),
        answer=f"S = {total}",
        difficulty=Difficulty.ELEMENTARY,
        witness={"L": last, "S": total},
        relations=(
            _relation(
                Expr.var("L"),
                _add(Expr.const(a), _mul(Expr.const(n - 1), Expr.const(d))),
            ),
            _relation(
                _mul(Expr.const(2), Expr.var("S")),
                _mul(Expr.const(n), _add(Expr.const(a), Expr.var("L"))),
            ),
            _relation(Expr.var("S"), Expr.const(total)),
        ),
    )


_LINEAR_A = tuple(range(-12, -1)) + tuple(range(2, 13))
_LINEAR_B = tuple(range(-20, 0)) + tuple(range(1, 21))
_NONZERO_SMALL = tuple(range(-30, 0)) + tuple(range(1, 31))


def _unrank_linear(index: int) -> Mapping[str, int]:
    ai, bi, xi = _mixed_unrank(index, (len(_LINEAR_A), len(_LINEAR_B), 60))
    a, b, root = _LINEAR_A[ai], _LINEAR_B[bi], _NONZERO_SMALL[xi]
    return {"a": a, "b": b, "c": a * root + b}


def _oracle_linear_v4(parameters: Mapping[str, int]) -> _ProblemExpectation:
    _require_parameters(parameters, {"a", "b", "c"})
    a, b, c = (parameters[name] for name in ("a", "b", "c"))
    if a not in _LINEAR_A or b not in _LINEAR_B or (c - b) % a:
        raise ValueError("linear givens do not have a canonical integer solution")
    root = (c - b) // a
    if root not in _NONZERO_SMALL:
        raise ValueError("linear solution falls outside the canonical domain")
    return _family_expectation(
        prompt=f"Solve over the integers: ({a} * x) + ({b}) = {c}.",
        answer=f"x = {root}",
        difficulty=Difficulty.ELEMENTARY,
        witness={"x": root},
        relations=(
            _relation(
                _add(_mul(Expr.const(a), Expr.var("x")), Expr.const(b)),
                Expr.const(c),
            ),
            _relation(
                _mul(Expr.const(a), Expr.var("x")),
                _sub(Expr.const(c), Expr.const(b)),
            ),
            _relation(Expr.var("x"), Expr.const(root)),
        ),
    )


_PYTHAGOREAN_GIVENS = tuple(
    sorted(
        {
            tuple(sorted(((m * m - n * n) * scale, 2 * m * n * scale)))
            for m in range(2, 31)
            for n in range(1, m)
            if gcd(m, n) == 1 and (m - n) % 2 == 1
            for scale in range(1, 9)
        }
    )
)


def _unrank_pythagorean(index: int) -> Mapping[str, int]:
    a, b = _PYTHAGOREAN_GIVENS[index]
    return {"a": a, "b": b}


def _oracle_pythagorean_v4(parameters: Mapping[str, int]) -> _ProblemExpectation:
    _require_parameters(parameters, {"a", "b"})
    a, b = parameters["a"], parameters["b"]
    square = a * a + b * b
    h = isqrt(square)
    if min(a, b) <= 0 or h * h != square or (a, b) not in _PYTHAGOREAN_GIVENS:
        raise ValueError("legs are not in the canonical Pythagorean domain")
    return _family_expectation(
        prompt=f"A right triangle has legs {a} and {b}. Find its integer hypotenuse.",
        answer=f"h = {h}",
        difficulty=Difficulty.ELEMENTARY,
        witness={"h": h},
        relations=(
            _relation(
                _mul(Expr.var("h"), Expr.var("h")),
                _add(_mul(Expr.const(a), Expr.const(a)), _mul(Expr.const(b), Expr.const(b))),
            ),
            _relation(Expr.var("h"), Expr.const(h)),
        ),
    )


_QUADRATIC_ROOTS = tuple((lower, upper) for lower in range(1, 41) for upper in range(lower + 1, 41))


def _unrank_quadratic(index: int) -> Mapping[str, int]:
    lower, upper = _QUADRATIC_ROOTS[index]
    return {"b": -(lower + upper), "c": lower * upper}


def _oracle_quadratic_v4(parameters: Mapping[str, int]) -> _ProblemExpectation:
    _require_parameters(parameters, {"b", "c"})
    b, c = parameters["b"], parameters["c"]
    discriminant = b * b - 4 * c
    root_gap = isqrt(discriminant)
    if root_gap * root_gap != discriminant or (-b + root_gap) % 2:
        raise ValueError("quadratic givens lack canonical integer roots")
    lower, upper = sorted(((-b - root_gap) // 2, (-b + root_gap) // 2))
    if (lower, upper) not in _QUADRATIC_ROOTS:
        raise ValueError("quadratic roots fall outside the canonical domain")
    return _family_expectation(
        prompt=f"Find the larger integer root of x^2 + ({b} * x) + ({c}) = 0.",
        answer=f"x = {upper}",
        difficulty=Difficulty.UNDERGRADUATE,
        witness={"r1": lower, "r2": upper},
        relations=(
            _relation(_add(Expr.var("r1"), Expr.var("r2")), Expr.const(-b)),
            _relation(_mul(Expr.var("r1"), Expr.var("r2")), Expr.const(c)),
            _relation(Expr.var("r2"), Expr.const(upper)),
        ),
    )


def _unrank_subsets(index: int) -> Mapping[str, int]:
    ni, ki = _mixed_unrank(index, (64, 9))
    return {"k": ki + 2, "n": ni + 12}


def _oracle_subsets_v4(parameters: Mapping[str, int]) -> _ProblemExpectation:
    _require_parameters(parameters, {"k", "n"})
    k, n = parameters["k"], parameters["n"]
    if not (12 <= n <= 75 and 2 <= k <= 10):
        raise ValueError("subset givens fall outside the canonical domain")
    count = comb(n, k)
    if count > MAX_INTEGER_ABS:
        raise ValueError("subset count exceeds the integer budget")
    witness = {"c0": 1, "N": count}
    relations = [_relation(Expr.var("c0"), Expr.const(1))]
    previous = "c0"
    for selected in range(1, k + 1):
        current = f"c{selected}"
        witness[current] = comb(n, selected)
        relations.append(
            _relation(
                _mul(Expr.const(selected), Expr.var(current)),
                _mul(
                    _add(
                        _sub(Expr.const(n), Expr.const(selected)),
                        Expr.const(1),
                    ),
                    Expr.var(previous),
                ),
            )
        )
        previous = current
    relations.append(_relation(Expr.var("N"), Expr.var(previous)))
    return _family_expectation(
        prompt=f"How many {k}-element subsets does an {n}-element set have?",
        answer=f"N = {count}",
        difficulty=Difficulty.UNDERGRADUATE,
        witness=witness,
        relations=relations,
    )


def _unrank_geometric(index: int) -> Mapping[str, int]:
    ai, ri, ni = _mixed_unrank(index, (20, 3, 10))
    return {"a": ai + 1, "n": ni + 3, "r": ri + 2}


def _oracle_geometric(parameters: Mapping[str, int]) -> _ProblemExpectation:
    _require_parameters(parameters, {"a", "n", "r"})
    a, n, ratio = (parameters[name] for name in ("a", "n", "r"))
    if not (1 <= a <= 20 and 3 <= n <= 12 and 2 <= ratio <= 4):
        raise ValueError("geometric givens fall outside the canonical domain")
    power = _bounded_power(ratio, n)
    total = a * (power - 1) // (ratio - 1)
    last = a * ratio ** (n - 1)
    return _family_expectation(
        prompt=(f"Find the sum of the first {n} terms of the geometric progression "
                f"with first term {a} and ratio {ratio}."),
        answer=f"S = {total}",
        difficulty=Difficulty.ELEMENTARY,
        witness={"L": last, "S": total},
        relations=(
            _relation(
                Expr.var("L"),
                _mul(Expr.const(a), _pow(Expr.const(ratio), Expr.const(n - 1))),
            ),
            _relation(
                _mul(_sub(Expr.const(ratio), Expr.const(1)), Expr.var("S")),
                _mul(
                    Expr.const(a),
                    _sub(_pow(Expr.const(ratio), Expr.const(n)), Expr.const(1)),
                ),
            ),
            _relation(Expr.var("S"), Expr.const(total)),
        ),
    )


_SYSTEM_MATRICES = (
    (1, 1, 1, -1),
    (2, 1, 1, -1),
    (1, 2, 3, -1),
    (2, -1, 1, 2),
    (3, 1, 1, 2),
    (1, -2, 2, 1),
    (2, 3, 1, -1),
    (3, -2, 2, 1),
)


def _unrank_system(index: int) -> Mapping[str, int]:
    mi, xi, yi = _mixed_unrank(index, (len(_SYSTEM_MATRICES), 20, 20))
    a, b, c, d = _SYSTEM_MATRICES[mi]
    x, y = xi + 1, yi + 1
    return {"a": a, "b": b, "c": c, "d": d, "e": a * x + b * y, "f": c * x + d * y}


def _oracle_system(parameters: Mapping[str, int]) -> _ProblemExpectation:
    _require_parameters(parameters, {"a", "b", "c", "d", "e", "f"})
    a, b, c, d, e, f = (parameters[name] for name in ("a", "b", "c", "d", "e", "f"))
    if (a, b, c, d) not in _SYSTEM_MATRICES:
        raise ValueError("system matrix falls outside the canonical domain")
    determinant = a * d - b * c
    x_num, y_num = e * d - b * f, a * f - e * c
    if x_num % determinant or y_num % determinant:
        raise ValueError("system does not have a canonical integer solution")
    x, y = x_num // determinant, y_num // determinant
    if not (1 <= x <= 20 and 1 <= y <= 20):
        raise ValueError("system solution falls outside the canonical domain")
    return _family_expectation(
        prompt=(f"Solve the system {a}x + {b}y = {e}, {c}x + {d}y = {f}; report x."),
        answer=f"x = {x}",
        difficulty=Difficulty.UNDERGRADUATE,
        witness={"x": x, "y": y},
        relations=(
            _relation(
                _add(_mul(Expr.const(a), Expr.var("x")), _mul(Expr.const(b), Expr.var("y"))),
                Expr.const(e),
            ),
            _relation(
                _add(_mul(Expr.const(c), Expr.var("x")), _mul(Expr.const(d), Expr.var("y"))),
                Expr.const(f),
            ),
            _relation(Expr.var("x"), Expr.const(x)),
        ),
    )


def _unrank_horner(index: int) -> Mapping[str, int]:
    a3, a2, a1, a0, x = _mixed_unrank(index, (5, 5, 5, 5, 5))
    return {"a0": a0, "a1": a1, "a2": a2, "a3": a3 + 1, "x": x + 2}


def _oracle_horner(parameters: Mapping[str, int]) -> _ProblemExpectation:
    _require_parameters(parameters, {"a0", "a1", "a2", "a3", "x"})
    a0, a1, a2, a3, x = (parameters[name] for name in ("a0", "a1", "a2", "a3", "x"))
    if not (1 <= a3 <= 5 and all(0 <= value <= 4 for value in (a0, a1, a2)) and 2 <= x <= 6):
        raise ValueError("polynomial givens fall outside the canonical domain")
    value = ((a3 * x + a2) * x + a1) * x + a0
    h1 = a3 * x + a2
    h2 = h1 * x + a1
    return _family_expectation(
        prompt=f"Evaluate {a3}x^3 + {a2}x^2 + {a1}x + {a0} at x = {x} using Horner's rule.",
        answer=f"P({x}) = {value}",
        difficulty=Difficulty.UNDERGRADUATE,
        witness={"h1": h1, "h2": h2, "value": value},
        relations=(
            _relation(
                Expr.var("h1"),
                _add(_mul(Expr.const(a3), Expr.const(x)), Expr.const(a2)),
            ),
            _relation(
                Expr.var("h2"),
                _add(_mul(Expr.var("h1"), Expr.const(x)), Expr.const(a1)),
            ),
            _relation(
                Expr.var("value"),
                _add(_mul(Expr.var("h2"), Expr.const(x)), Expr.const(a0)),
            ),
            _relation(
                _sub(
                    Expr.var("value"),
                    _mul(
                        Expr.const(a3),
                        _pow(Expr.const(x), Expr.const(3)),
                    ),
                ),
                _add(
                    _add(
                        _mul(
                            Expr.const(a2),
                            _pow(Expr.const(x), Expr.const(2)),
                        ),
                        _mul(Expr.const(a1), Expr.const(x)),
                    ),
                    Expr.const(a0),
                ),
            ),
        ),
    )


def _unrank_gcd(index: int) -> Mapping[str, int]:
    ai, bi = _mixed_unrank(index, (900, 90))
    return {"a": ai + 100, "b": bi + 10}


def _extended_gcd(a: int, b: int) -> tuple[int, int, int]:
    old_r, r, old_s, s, old_t, t = a, b, 1, 0, 0, 1
    while r:
        quotient = old_r // r
        old_r, r = r, old_r - quotient * r
        old_s, s = s, old_s - quotient * s
        old_t, t = t, old_t - quotient * t
    return old_r, old_s, old_t


def _oracle_gcd(parameters: Mapping[str, int]) -> _ProblemExpectation:
    _require_parameters(parameters, {"a", "b"})
    a, b = parameters["a"], parameters["b"]
    if not (100 <= a <= 999 and 10 <= b <= 99):
        raise ValueError("gcd givens fall outside the canonical domain")
    witness: dict[str, int] = {}
    relations: list[Relation] = []
    dividend, current_divisor, step = a, b, 0
    while current_divisor:
        quotient, remainder = divmod(dividend, current_divisor)
        witness[f"q{step}"] = quotient
        witness[f"r{step}"] = remainder
        relations.append(
            _relation(
                Expr.const(dividend),
                _add(
                    _mul(Expr.var(f"q{step}"), Expr.const(current_divisor)),
                    Expr.var(f"r{step}"),
                ),
            )
        )
        dividend, current_divisor = current_divisor, remainder
        step += 1
    divisor, u, v = _extended_gcd(a, b)
    witness.update({"g": divisor, "u": u, "v": v})
    relations.extend(
        (
            _relation(
                _add(_mul(Expr.const(a), Expr.var("u")), _mul(Expr.const(b), Expr.var("v"))),
                Expr.var("g"),
            ),
            _relation(Expr.var("g"), Expr.const(divisor)),
        )
    )
    return _family_expectation(
        prompt=f"Use the Euclidean algorithm to compute gcd({a}, {b}).",
        answer=f"gcd = {divisor}",
        difficulty=Difficulty.UNDERGRADUATE,
        witness=witness,
        relations=relations,
    )


_MODULAR_INVERSE_GIVENS = tuple(
    (a, modulus)
    for modulus in range(11, 101)
    for a in range(2, modulus)
    if gcd(a, modulus) == 1
)


def _unrank_modular_inverse(index: int) -> Mapping[str, int]:
    a, modulus = _MODULAR_INVERSE_GIVENS[index]
    return {"a": a, "m": modulus}


def _oracle_modular_inverse(parameters: Mapping[str, int]) -> _ProblemExpectation:
    _require_parameters(parameters, {"a", "m"})
    a, modulus = parameters["a"], parameters["m"]
    if not (11 <= modulus <= 100 and 2 <= a < modulus and gcd(a, modulus) == 1):
        raise ValueError("modular inverse givens fall outside the canonical domain")
    inverse = pow(a, -1, modulus)
    quotient = (a * inverse - 1) // modulus
    return _family_expectation(
        prompt=f"Find the least positive inverse of {a} modulo {modulus}.",
        answer=f"inverse = {inverse}",
        difficulty=Difficulty.UNDERGRADUATE,
        witness={"inverse": inverse, "q": quotient},
        relations=(
            _relation(
                _mul(Expr.const(a), Expr.var("inverse")),
                _add(Expr.const(1), _mul(Expr.var("q"), Expr.const(modulus))),
            ),
            _relation(Expr.var("inverse"), Expr.const(inverse)),
        ),
    )


_CRT_MODULI = ((5, 7), (5, 11), (7, 11), (7, 13), (11, 13), (11, 17), (13, 17))
_CRT_GIVENS = tuple(
    (r1, m1, r2, m2)
    for m1, m2 in _CRT_MODULI
    for r1 in range(1, m1)
    for r2 in range(m2)
)


def _unrank_crt(index: int) -> Mapping[str, int]:
    r1, m1, r2, m2 = _CRT_GIVENS[index]
    return {"m1": m1, "m2": m2, "r1": r1, "r2": r2}


def _oracle_crt(parameters: Mapping[str, int]) -> _ProblemExpectation:
    _require_parameters(parameters, {"m1", "m2", "r1", "r2"})
    m1, m2, r1, r2 = (parameters[name] for name in ("m1", "m2", "r1", "r2"))
    if (m1, m2) not in _CRT_MODULI or not (1 <= r1 < m1 and 0 <= r2 < m2):
        raise ValueError("CRT givens fall outside the canonical domain")
    solution = next(x for x in range(m1 * m2) if x % m1 == r1 and x % m2 == r2)
    q1, q2 = (solution - r1) // m1, (solution - r2) // m2
    return _family_expectation(
        prompt=f"Find the least nonnegative x with x = {r1} (mod {m1}) and x = {r2} (mod {m2}).",
        answer=f"x = {solution}",
        difficulty=Difficulty.UNDERGRADUATE,
        witness={"q1": q1, "q2": q2, "x": solution},
        relations=(
            _relation(
                _sub(Expr.var("x"), Expr.const(r1)),
                _mul(Expr.var("q1"), Expr.const(m1)),
            ),
            _relation(
                _sub(Expr.var("x"), Expr.const(r2)),
                _mul(Expr.var("q2"), Expr.const(m2)),
            ),
            _relation(Expr.var("x"), Expr.const(solution)),
        ),
    )


def _unrank_determinant(index: int) -> Mapping[str, int]:
    d1, d2, d3, u12, u13, u23 = _mixed_unrank(index, (6, 6, 6, 5, 5, 5))
    return {
        "a11": d1 + 1,
        "a12": u12 - 2,
        "a13": u13 - 2,
        "a21": 0,
        "a22": d2 + 1,
        "a23": u23 - 2,
        "a31": 0,
        "a32": 0,
        "a33": d3 + 1,
    }


def _determinant(parameters: Mapping[str, int]) -> int:
    a, b, c = (parameters[name] for name in ("a11", "a12", "a13"))
    d, e, f = (parameters[name] for name in ("a21", "a22", "a23"))
    g, h, i = (parameters[name] for name in ("a31", "a32", "a33"))
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def _oracle_determinant(parameters: Mapping[str, int]) -> _ProblemExpectation:
    names = {f"a{row}{column}" for row in range(1, 4) for column in range(1, 4)}
    _require_parameters(parameters, names)
    if any(parameters[name] != 0 for name in ("a21", "a31", "a32")):
        raise ValueError("determinant matrix is outside the upper-triangular domain")
    if any(not 1 <= parameters[name] <= 6 for name in ("a11", "a22", "a33")):
        raise ValueError("determinant diagonal is outside the canonical domain")
    if any(not -2 <= parameters[name] <= 2 for name in ("a12", "a13", "a23")):
        raise ValueError("determinant off-diagonal is outside the canonical domain")
    value = _determinant(parameters)
    a, b, c = (parameters[name] for name in ("a11", "a12", "a13"))
    d, e, f = (parameters[name] for name in ("a21", "a22", "a23"))
    lower_g, h, i = (parameters[name] for name in ("a31", "a32", "a33"))
    m1, m2, m3 = e * i - f * h, d * i - f * lower_g, d * h - e * lower_g
    matrix = "; ".join(
        ",".join(str(parameters[f"a{row}{column}"]) for column in range(1, 4))
        for row in range(1, 4)
    )
    return _family_expectation(
        prompt=f"Compute the determinant of the 3 by 3 matrix with rows [{matrix}].",
        answer=f"det = {value}",
        difficulty=Difficulty.UNDERGRADUATE,
        witness={"det": value, "m1": m1, "m2": m2, "m3": m3},
        relations=(
            _relation(
                Expr.var("m1"),
                _sub(_mul(Expr.const(e), Expr.const(i)), _mul(Expr.const(f), Expr.const(h))),
            ),
            _relation(
                Expr.var("m2"),
                _sub(
                    _mul(Expr.const(d), Expr.const(i)),
                    _mul(Expr.const(f), Expr.const(lower_g)),
                ),
            ),
            _relation(
                Expr.var("m3"),
                _sub(
                    _mul(Expr.const(d), Expr.const(h)),
                    _mul(Expr.const(e), Expr.const(lower_g)),
                ),
            ),
            _relation(
                Expr.var("det"),
                _add(
                    _sub(_mul(Expr.const(a), Expr.var("m1")), _mul(Expr.const(b), Expr.var("m2"))),
                    _mul(Expr.const(c), Expr.var("m3")),
                ),
            ),
            _relation(
                _sub(
                    Expr.var("det"),
                    _mul(_mul(Expr.const(a), Expr.const(e)), Expr.const(i)),
                ),
                Expr.const(0),
            ),
        ),
    )


def _unrank_derivative(index: int) -> Mapping[str, int]:
    a3, a2, a1, x = _mixed_unrank(index, (6, 6, 6, 6))
    return {"a1": a1 + 1, "a2": a2 + 1, "a3": a3 + 1, "x": x + 1}


def _oracle_derivative(parameters: Mapping[str, int]) -> _ProblemExpectation:
    _require_parameters(parameters, {"a1", "a2", "a3", "x"})
    a1, a2, a3, x = (parameters[name] for name in ("a1", "a2", "a3", "x"))
    if not all(1 <= value <= 6 for value in (a1, a2, a3, x)):
        raise ValueError("derivative givens fall outside the canonical domain")
    value = 3 * a3 * x * x + 2 * a2 * x + a1
    expression = _add(
        _add(
            _mul(Expr.const(3 * a3), _mul(Expr.const(x), Expr.const(x))),
            _mul(Expr.const(2 * a2), Expr.const(x)),
        ),
        Expr.const(a1),
    )
    return _family_expectation(
        prompt=f"For P(x) = {a3}x^3 + {a2}x^2 + {a1}x, compute P'({x}).",
        answer=f"P'({x}) = {value}",
        difficulty=Difficulty.UNDERGRADUATE,
        witness={"derivative": value},
        relations=(_relation(Expr.var("derivative"), expression),),
    )


def _unrank_integral(index: int) -> Mapping[str, int]:
    qi, pi, bi = _mixed_unrank(index, (20, 8, 6))
    exponent, quotient, bound = pi + 1, qi + 1, bi + 1
    return {"b": bound, "c": (exponent + 1) * quotient, "p": exponent}


def _oracle_integral(parameters: Mapping[str, int]) -> _ProblemExpectation:
    _require_parameters(parameters, {"b", "c", "p"})
    bound, coefficient, exponent = (parameters[name] for name in ("b", "c", "p"))
    if not (1 <= bound <= 6 and 1 <= exponent <= 8 and coefficient % (exponent + 1) == 0):
        raise ValueError("integral givens fall outside the canonical domain")
    quotient = coefficient // (exponent + 1)
    if not 1 <= quotient <= 20:
        raise ValueError("integral coefficient falls outside the canonical domain")
    value = quotient * _bounded_power(bound, exponent + 1)
    return _family_expectation(
        prompt=f"Evaluate the definite integral of {coefficient}x^{exponent} from 0 to {bound}.",
        answer=f"integral = {value}",
        difficulty=Difficulty.UNDERGRADUATE,
        witness={"I": value},
        relations=(
            _relation(
                _mul(_add(Expr.const(exponent), Expr.const(1)), Expr.var("I")),
                _mul(
                    Expr.const(coefficient),
                    _pow(
                        Expr.const(bound),
                        _add(Expr.const(exponent), Expr.const(1)),
                    ),
                ),
            ),
            _relation(Expr.var("I"), Expr.const(value)),
        ),
    )


def _unrank_triangle(index: int) -> Mapping[str, int]:
    bi, hi = _mixed_unrank(index, (25, 30))
    return {"base": 2 * (bi + 1), "height": hi + 1}


def _oracle_triangle(parameters: Mapping[str, int]) -> _ProblemExpectation:
    _require_parameters(parameters, {"base", "height"})
    base, height = parameters["base"], parameters["height"]
    if base % 2 or not (2 <= base <= 50 and 1 <= height <= 30):
        raise ValueError("triangle givens fall outside the canonical domain")
    area = base * height // 2
    return _family_expectation(
        prompt=f"Find the area of a triangle with base {base} and height {height}.",
        answer=f"area = {area}",
        difficulty=Difficulty.ELEMENTARY,
        witness={"A": area},
        relations=(
            _relation(
                _add(Expr.var("A"), Expr.var("A")),
                _mul(Expr.const(base), Expr.const(height)),
            ),
            _relation(Expr.var("A"), Expr.const(area)),
        ),
    )


_HYPERGEOMETRIC_GIVENS = tuple(
    (good, bad, draws, successes)
    for good in range(4, 19)
    for bad in range(4, 19)
    for draws in range(2, 7)
    for successes in range(1, draws)
    if successes <= good and draws - successes <= bad
)


def _unrank_hypergeometric(index: int) -> Mapping[str, int]:
    good, bad, draws, successes = _HYPERGEOMETRIC_GIVENS[index]
    return {"bad": bad, "draws": draws, "good": good, "successes": successes}


def _oracle_hypergeometric(parameters: Mapping[str, int]) -> _ProblemExpectation:
    _require_parameters(parameters, {"bad", "draws", "good", "successes"})
    bad, draws, good, successes = (
        parameters[name] for name in ("bad", "draws", "good", "successes")
    )
    if (good, bad, draws, successes) not in _HYPERGEOMETRIC_GIVENS:
        raise ValueError("hypergeometric givens fall outside the canonical domain")
    favorable = comb(good, successes) * comb(bad, draws - successes)
    total = comb(good + bad, draws)
    common = gcd(favorable, total)
    numerator, denominator = favorable // common, total // common
    misses = draws - successes
    witness: dict[str, int] = {
        "F": favorable,
        "den": denominator,
        "num": numerator,
    }
    relations: list[Relation] = []
    for prefix, population, selected in (
        ("cg", good, successes),
        ("cb", bad, misses),
        ("ct", good + bad, draws),
    ):
        witness[f"{prefix}0"] = 1
        relations.append(_relation(Expr.var(f"{prefix}0"), Expr.const(1)))
        for chosen in range(1, selected + 1):
            previous, current = f"{prefix}{chosen - 1}", f"{prefix}{chosen}"
            witness[current] = comb(population, chosen)
            relations.append(
                _relation(
                    _mul(Expr.const(chosen), Expr.var(current)),
                    _mul(
                        _add(
                            _sub(Expr.const(population), Expr.const(chosen)),
                            Expr.const(1),
                        ),
                        Expr.var(previous),
                    ),
                )
            )
    relations.extend(
        (
            _relation(
                Expr.var("F"),
                _mul(Expr.var(f"cg{successes}"), Expr.var(f"cb{misses}")),
            ),
            _relation(
                _mul(Expr.var("num"), Expr.var(f"ct{draws}")),
                _mul(Expr.var("F"), Expr.var("den")),
            ),
        )
    )
    return _family_expectation(
        prompt=(f"A population has {good} marked and {bad} unmarked objects. "
                f"If {draws} are drawn without replacement, find the probability of exactly "
                f"{successes} marked objects."),
        answer=f"p = {numerator}/{denominator}",
        difficulty=Difficulty.UNDERGRADUATE,
        witness=witness,
        relations=relations,
    )


def _unrank_recurrence(index: int) -> Mapping[str, int]:
    ui, pi, qi, ni = _mixed_unrank(index, (10, 3, 10, 7))
    return {"n": ni + 2, "p": pi + 2, "q": qi + 1, "u0": ui + 1}


def _oracle_recurrence(parameters: Mapping[str, int]) -> _ProblemExpectation:
    _require_parameters(parameters, {"n", "p", "q", "u0"})
    n, multiplier, increment, initial = (
        parameters[name] for name in ("n", "p", "q", "u0")
    )
    if not (2 <= n <= 8 and 2 <= multiplier <= 4 and 1 <= increment <= 10 and 1 <= initial <= 10):
        raise ValueError("recurrence givens fall outside the canonical domain")
    witness: dict[str, int] = {}
    relations: list[Relation] = []
    previous_value = initial
    previous_expr = Expr.const(initial)
    for step in range(1, n + 1):
        value = multiplier * previous_value + increment
        name = f"u{step}"
        witness[name] = value
        relations.append(
            _relation(
                Expr.var(name),
                _add(_mul(Expr.const(multiplier), previous_expr), Expr.const(increment)),
            )
        )
        previous_value, previous_expr = value, Expr.var(name)
    return _family_expectation(
        prompt=(f"Let u_0 = {initial} and u_(k+1) = {multiplier}u_k + {increment}. "
                f"Find u_{n}."),
        answer=f"u_{n} = {previous_value}",
        difficulty=Difficulty.UNDERGRADUATE,
        witness=witness,
        relations=relations,
    )


_BASE_DIGITS = "0123456789ABCDEF"


def _base_representation(number: int, base: int) -> str:
    digits: list[str] = []
    remaining = number
    while remaining:
        remaining, digit = divmod(remaining, base)
        digits.append(_BASE_DIGITS[digit])
    return "".join(reversed(digits)) or "0"


def _unrank_base_conversion(index: int) -> Mapping[str, int]:
    ni, bi = _mixed_unrank(index, (900, 15))
    return {"base": bi + 2, "number": ni + 100}


def _oracle_base_conversion(parameters: Mapping[str, int]) -> _ProblemExpectation:
    _require_parameters(parameters, {"base", "number"})
    base, number = parameters["base"], parameters["number"]
    if not (2 <= base <= 16 and 100 <= number <= 999):
        raise ValueError("base-conversion givens fall outside the canonical domain")
    representation = _base_representation(number, base)
    digits: list[int] = []
    remaining = number
    while remaining:
        remaining, digit = divmod(remaining, base)
        digits.append(digit)
    witness: dict[str, int] = {f"d{i}": digit for i, digit in enumerate(digits)}
    relations: list[Relation] = []
    quotient = number
    for index, digit in enumerate(digits):
        next_quotient = quotient // base
        witness[f"q{index}"] = quotient
        witness[f"q{index + 1}"] = next_quotient
        relations.append(
            _relation(
                Expr.var(f"q{index}"),
                _add(
                    _mul(Expr.const(base), Expr.var(f"q{index + 1}")),
                    Expr.var(f"d{index}"),
                ),
            )
        )
        quotient = next_quotient
    relations.append(_relation(Expr.var("q0"), Expr.const(number)))
    return _family_expectation(
        prompt=f"Convert decimal {number} to base {base}.",
        answer=f"digits = {representation}",
        difficulty=Difficulty.ELEMENTARY,
        witness=witness,
        relations=relations,
    )


FAMILY_SPECS = {
    spec.name: spec
    for spec in (
        FamilySpec(
            "arithmetic_progression",
            30 * 12 * 28,
            _unrank_progression,
            _oracle_progression_v4,
        ),
        FamilySpec("base_conversion", 900 * 15, _unrank_base_conversion, _oracle_base_conversion),
        FamilySpec("crt_two_moduli", len(_CRT_GIVENS), _unrank_crt, _oracle_crt),
        FamilySpec(
            "determinant_3x3",
            6 * 6 * 6 * 5 * 5 * 5,
            _unrank_determinant,
            _oracle_determinant,
        ),
        FamilySpec("gcd_euclidean", 900 * 90, _unrank_gcd, _oracle_gcd),
        FamilySpec("geometric_progression", 20 * 3 * 10, _unrank_geometric, _oracle_geometric),
        FamilySpec("horner_polynomial", 5**5, _unrank_horner, _oracle_horner),
        FamilySpec(
            "hypergeometric_probability",
            len(_HYPERGEOMETRIC_GIVENS),
            _unrank_hypergeometric,
            _oracle_hypergeometric,
        ),
        FamilySpec(
            "linear_equation",
            len(_LINEAR_A) * len(_LINEAR_B) * 60,
            _unrank_linear,
            _oracle_linear_v4,
        ),
        FamilySpec(
            "modular_inverse",
            len(_MODULAR_INVERSE_GIVENS),
            _unrank_modular_inverse,
            _oracle_modular_inverse,
        ),
        FamilySpec("monomial_integral", 20 * 8 * 6, _unrank_integral, _oracle_integral),
        FamilySpec("polynomial_derivative", 6**4, _unrank_derivative, _oracle_derivative),
        FamilySpec(
            "pythagorean_hypotenuse",
            len(_PYTHAGOREAN_GIVENS),
            _unrank_pythagorean,
            _oracle_pythagorean_v4,
        ),
        FamilySpec(
            "quadratic_integer_roots",
            len(_QUADRATIC_ROOTS),
            _unrank_quadratic,
            _oracle_quadratic_v4,
        ),
        FamilySpec("recurrence", 10 * 3 * 10 * 7, _unrank_recurrence, _oracle_recurrence),
        FamilySpec("subset_count", 64 * 9, _unrank_subsets, _oracle_subsets_v4),
        FamilySpec("triangle_area", 25 * 30, _unrank_triangle, _oracle_triangle),
        FamilySpec(
            "two_by_two_system",
            len(_SYSTEM_MATRICES) * 20 * 20,
            _unrank_system,
            _oracle_system,
        ),
    )
}

_INDEPENDENT_VALIDATOR = "independent-family-verifier-v1"
_VALIDATORS = ("assertion-evaluator-v1", _INDEPENDENT_VALIDATOR)


def _expr_from_spec(spec: ExprSpec) -> Expr:
    """Convert the verifier bundle's neutral tuple form into trusted MathIR."""

    if not isinstance(spec, tuple) or not spec or not isinstance(spec[0], str):
        raise ValueError("independent expression specification must be a non-empty tuple")
    operation = spec[0]
    if operation == ExprOp.CONST.value and len(spec) == 2:
        return Expr.const(_integer(spec[1], "independent constant"))
    if operation == ExprOp.VAR.value and len(spec) == 2:
        return Expr.var(_string(spec[1], "independent variable", max_bytes=128))
    if operation == ExprOp.NEG.value and len(spec) == 2:
        return _neg(_expr_from_spec(spec[1]))
    binary_builders = {
        ExprOp.ADD.value: _add,
        ExprOp.SUB.value: _sub,
        ExprOp.MUL.value: _mul,
        ExprOp.DIV.value: _div,
        ExprOp.POW.value: _pow,
    }
    builder = binary_builders.get(operation)
    if builder is None or len(spec) != 3:
        raise ValueError(f"unsupported independent expression specification: {operation!r}")
    return builder(_expr_from_spec(spec[1]), _expr_from_spec(spec[2]))


def _relations_from_independent(
    relations: Sequence[tuple[ExprSpec, ExprSpec]],
) -> tuple[Relation, ...]:
    converted: list[Relation] = []
    for relation in relations:
        if not isinstance(relation, tuple) or len(relation) != 2:
            raise ValueError("independent relation specification must have two sides")
        converted.append(
            _relation(_expr_from_spec(relation[0]), _expr_from_spec(relation[1]))
        )
    return tuple(converted)


def validate_problem_binding(problem: Problem, solution: Solution) -> ValidationReport:
    """Re-derive all statement fields through the separate verifier bundle."""

    if not isinstance(problem, Problem) or not isinstance(solution, Solution):
        raise ValueError("problem binding requires a Problem and Solution")
    try:
        expected = verify_family(problem.family, problem.parameter_map)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        return ValidationReport(
            "independent-problem-verifier-v1",
            False,
            (),
            f"invalid problem: {exc}",
        )
    mismatches: list[str] = []
    if problem.prompt != expected.prompt:
        mismatches.append("prompt")
    if problem.answer != expected.answer:
        mismatches.append("answer")
    if problem.difficulty.value != expected.difficulty:
        mismatches.append("difficulty")
    if problem.witness != expected.witness:
        mismatches.append("witness")
    if solution.conclusion != expected.answer:
        mismatches.append("solution conclusion")
    return ValidationReport(
        validator="independent-problem-verifier-v1",
        passed=not mismatches,
        failing_steps=(),
        detail=(
            "problem statement and conclusion match the separate verifier bundle"
            if not mismatches
            else "mismatched fields: " + ", ".join(mismatches)
        ),
    )


def validate_family_trace(problem: Problem, solution: Solution) -> ValidationReport:
    """Validator two: reconstruct the derivation in the separate verifier module."""

    if not isinstance(problem, Problem) or not isinstance(solution, Solution):
        raise ValueError("family-trace validation requires a Problem and Solution")
    try:
        bundle = verify_family(problem.family, problem.parameter_map)
        expected = _relations_from_independent(bundle.relations)
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        return ValidationReport(
            _INDEPENDENT_VALIDATOR,
            False,
            (),
            f"invalid family parameters: {exc}",
        )
    failures: list[int] = []
    limit = max(len(expected), len(solution.steps))
    for position in range(limit):
        if position >= len(expected) or position >= len(solution.steps):
            failures.append(position + 1)
            continue
        step = solution.steps[position]
        expected_step = _step(position + 1, expected[position])
        if step != expected_step:
            failures.append(position + 1)
    return ValidationReport(
        validator=_INDEPENDENT_VALIDATOR,
        passed=not failures,
        failing_steps=tuple(failures),
        detail=(
            "trace exactly matches independent family reconstruction"
            if not failures
            else "trace mismatch"
        ),
    )


def validate_solution(item: BenchmarkItem, solution: Solution) -> tuple[ValidationReport, ...]:
    if not isinstance(item, BenchmarkItem) or not isinstance(solution, Solution):
        raise ValueError("solution validation requires a BenchmarkItem and Solution")
    return (
        validate_assertions(item.problem, solution),
        validate_family_trace(item.problem, solution),
    )


def _assert_declared_ast_mutation(item: BenchmarkItem) -> None:
    faulty = item.faulty_solution.steps[item.fault.step_number - 1].assertion
    matched = any(
        candidate.kind is item.fault.kind
        and candidate.step_number == item.fault.step_number
        and candidate.ast_path == item.fault.ast_path
        and candidate.bad_relation == faulty
        for candidate in _enumerate_mutations(
            item.clean_solution,
            item.problem.witness_map,
        )
    )
    if not matched:
        raise ValueError("declared fault does not match the one-node AST mutation")


def _certify_mathematical_invariants(item: BenchmarkItem) -> None:
    """Check content, statement, fault, repair, and mathematical invariants."""

    if not isinstance(item, BenchmarkItem):
        raise ValueError("certification input must be a benchmark item")
    _sha256_string(item.content_digest, "certification content digest")
    if not hmac.compare_digest(item.content_digest, _digest(item.content_payload())):
        raise ValueError("certification content digest does not match item content")
    for label, solution in (
        ("clean", item.clean_solution),
        ("faulty", item.faulty_solution),
    ):
        binding = validate_problem_binding(item.problem, solution)
        if not binding.passed:
            raise ValueError(f"{label} problem binding failed: {binding.detail}")
    differences = differing_steps(item.clean_solution, item.faulty_solution)
    if differences != (item.fault.step_number,):
        raise ValueError(f"fault must alter exactly its recorded line; differences={differences}")
    _assert_declared_ast_mutation(item)
    if item.repair.step_number != item.fault.step_number:
        raise ValueError("repair and fault target different lines")
    repaired = replay_repair(item)
    if repaired != item.clean_solution:
        raise ValueError("repair replay does not recover the clean solution")
    clean_reports = validate_solution(item, item.clean_solution)
    if not all(report.passed for report in clean_reports):
        raise ValueError(f"clean solution failed validation: {clean_reports}")
    faulty_reports = validate_solution(item, item.faulty_solution)
    if any(report.passed for report in faulty_reports):
        raise ValueError(f"a validator accepted the intentional fault: {faulty_reports}")
    if any(report.failing_steps != (item.fault.step_number,) for report in faulty_reports):
        raise ValueError(f"fault did not localize uniquely: {faulty_reports}")
    repaired_reports = validate_solution(item, repaired)
    if not all(report.passed for report in repaired_reports):
        raise ValueError(f"repaired solution failed validation: {repaired_reports}")


def certify_item(item: BenchmarkItem) -> None:
    """Reject records that differ from deterministic generation at their coordinates."""

    _certify_mathematical_invariants(item)
    _assert_deterministic_generation(item)


@dataclass(frozen=True, slots=True)
class _Draft:
    problem: Problem
    clean: Solution


@dataclass(frozen=True, slots=True)
class _MutationCandidate:
    kind: FaultKind
    step_number: int
    ast_path: tuple[int, ...]
    bad_relation: Relation
    description: str


def _expression_nodes(
    expression: Expr,
    path: tuple[int, ...] = (),
) -> Iterator[tuple[tuple[int, ...], Expr]]:
    yield path, expression
    for index, argument in enumerate(expression.args):
        yield from _expression_nodes(argument, path + (index,))


def _replace_expression(
    expression: Expr,
    path: tuple[int, ...],
    replacement: Expr,
) -> Expr:
    if not path:
        return replacement
    branch = path[0]
    if branch >= len(expression.args):
        raise ValueError("fault AST path does not address an expression node")
    arguments = list(expression.args)
    arguments[branch] = _replace_expression(arguments[branch], path[1:], replacement)
    return Expr(expression.op, tuple(arguments), expression.value)


def _replace_relation_expression(
    relation: Relation,
    path: tuple[int, ...],
    replacement: Expr,
) -> Relation:
    if not path or path[0] not in {0, 1}:
        raise ValueError("fault AST path must begin with a relation side")
    if path[0] == 0:
        return Relation(
            _replace_expression(relation.left, path[1:], replacement),
            relation.right,
            relation.comparator,
        )
    return Relation(
        relation.left,
        _replace_expression(relation.right, path[1:], replacement),
        relation.comparator,
    )


def _enumerate_mutations(
    solution: Solution,
    witness: Mapping[str, int],
) -> tuple[_MutationCandidate, ...]:
    """Enumerate false, evaluable one-node mutations across all meaningful lines."""

    candidates: list[_MutationCandidate] = []
    seen: set[tuple[FaultKind, int, tuple[int, ...], Relation]] = set()

    def add_candidate(
        *,
        kind: FaultKind,
        step_number: int,
        ast_path: tuple[int, ...],
        bad_relation: Relation,
        description: str,
    ) -> None:
        key = (kind, step_number, ast_path, bad_relation)
        if key in seen:
            return
        try:
            is_false = not evaluate_relation(bad_relation, witness)
        except ValueError:
            return
        if not is_false:
            return
        seen.add(key)
        candidates.append(
            _MutationCandidate(kind, step_number, ast_path, bad_relation, description)
        )

    for step in solution.steps:
        relation = step.assertion
        for side, expression in enumerate((relation.left, relation.right)):
            for local_path, node in _expression_nodes(expression):
                node_path = (side,) + local_path
                if node.op is ExprOp.CONST:
                    value = int(node.value)
                    try:
                        incremented = Expr.const(value + 1)
                    except ValueError:
                        incremented = None
                    if incremented is not None:
                        add_candidate(
                            kind=FaultKind.OFF_BY_ONE,
                            step_number=step.number,
                            ast_path=node_path,
                            bad_relation=_replace_relation_expression(
                                relation,
                                node_path,
                                incremented,
                            ),
                            description="incremented one integer literal in the certificate",
                        )
                    if value:
                        add_candidate(
                            kind=FaultKind.SIGN_ERROR,
                            step_number=step.number,
                            ast_path=node_path,
                            bad_relation=_replace_relation_expression(
                                relation,
                                node_path,
                                Expr.const(-value),
                            ),
                            description="negated one nonzero integer literal in the certificate",
                        )
                elif node.op is ExprOp.MUL:
                    for dropped in (0, 1):
                        add_candidate(
                            kind=FaultKind.DROPPED_FACTOR,
                            step_number=step.number,
                            ast_path=node_path + (dropped,),
                            bad_relation=_replace_relation_expression(
                                relation,
                                node_path,
                                node.args[1 - dropped],
                            ),
                            description="removed one factor from a multiplication node",
                        )
                elif node.op in {ExprOp.ADD, ExprOp.SUB}:
                    swapped = ExprOp.SUB if node.op is ExprOp.ADD else ExprOp.ADD
                    add_candidate(
                        kind=FaultKind.WRONG_OPERATION,
                        step_number=step.number,
                        ast_path=node_path,
                        bad_relation=_replace_relation_expression(
                            relation,
                            node_path,
                            Expr(swapped, node.args),
                        ),
                        description="swapped one addition/subtraction operator",
                    )
    return tuple(candidates)




FAMILY_ORDER = tuple(sorted(FAMILY_SPECS))


def _affine_permutation(seed: str, label: str, rank: int, modulus: int) -> int:
    """Permute a finite canonical domain without replacement."""

    if not 0 <= rank < modulus:
        raise ValueError("affine rank falls outside its domain")
    rng = CounterRNG(seed, stream=f"affine:{label}")
    while True:
        multiplier = 1 + rng.randbelow(modulus - 1)
        if gcd(multiplier, modulus) == 1:
            break
    offset = rng.randbelow(modulus)
    return (multiplier * rank + offset) % modulus


def _scheduled_family(seed: str, index: int) -> tuple[int, str, int]:
    family_slot = index % len(FAMILY_ORDER)
    permuted_slot = _affine_permutation(
        seed,
        "family-order",
        family_slot,
        len(FAMILY_ORDER),
    )
    family = FAMILY_ORDER[permuted_slot]
    local_rank = index // len(FAMILY_ORDER)
    return family_slot, family, local_rank


def _scheduled_fault(index: int) -> FaultKind:
    family_slot = index % len(FAMILY_ORDER)
    family_round = index // len(FAMILY_ORDER)
    return FAULT_ORDER[(family_round + family_slot % len(FAULT_ORDER)) % len(FAULT_ORDER)]


def _draft_from_givens(family: str, givens: Mapping[str, int]) -> _Draft:
    parameters = _pairs(givens)
    expectation = FAMILY_SPECS[family].oracle(dict(parameters))
    problem = Problem(
        family=family,
        difficulty=expectation.difficulty,
        prompt=expectation.prompt,
        answer=expectation.answer,
        parameters=parameters,
        witness=expectation.witness,
        semantic_fingerprint=_semantic_fingerprint(family, parameters),
    )
    clean = _solution(expectation.relations, problem.answer)
    return _Draft(problem, clean)


def _build_item_from_coordinates(seed: str, index: int) -> BenchmarkItem:
    """Build one deterministic item without calling the public certifier."""

    _string(seed, "item seed", max_bytes=MAX_SHORT_STRING_BYTES)
    if not 0 <= _integer(index, "item index") < BENCHMARK_CAPACITY:
        raise ValueError(f"item index must be in [0, {BENCHMARK_CAPACITY})")
    _, family, local_rank = _scheduled_family(seed, index)
    spec = FAMILY_SPECS[family]
    domain_rank = _affine_permutation(seed, f"family-domain:{family}", local_rank, spec.domain_size)
    draft = _draft_from_givens(family, spec.unrank(domain_rank))
    scheduled_kind = _scheduled_fault(index)
    candidates = tuple(
        candidate
        for candidate in _enumerate_mutations(draft.clean, draft.problem.witness_map)
        if candidate.kind is scheduled_kind
    )
    if not candidates:
        raise ValueError(
            f"family {family} exposes no eligible {scheduled_kind.value} mutation site"
        )
    mutation_rng = CounterRNG(
        seed,
        stream=f"mutation-site-v1:{index}:{scheduled_kind.value}",
    )
    selected = mutation_rng.choice(candidates)
    step_number, bad_relation = selected.step_number, selected.bad_relation
    faulty_steps = list(draft.clean.steps)
    faulty_steps[step_number - 1] = _step(step_number, bad_relation)
    faulty = Solution(tuple(faulty_steps), draft.clean.conclusion)
    repair = RepairReplay(
        expected_faulty_digest=faulty.digest(),
        step_number=step_number,
        expected_faulty_step_digest=faulty.steps[step_number - 1].digest(),
        replacement=draft.clean.steps[step_number - 1],
        expected_repaired_digest=draft.clean.digest(),
    )
    generation = Generation(GENERATOR_VERSION, seed, index, family)
    item_id = _item_id(generation)
    validators = _VALIDATORS
    provenance = Provenance()
    fault = Fault(
        step_number=step_number,
        kind=scheduled_kind,
        mutation=_FAULT_MUTATIONS[scheduled_kind],
        ast_path=selected.ast_path,
        description=selected.description,
    )
    content_digest = _digest(
        _benchmark_item_payload(
            item_id=item_id,
            generation=generation,
            problem=draft.problem,
            clean_solution=draft.clean,
            faulty_solution=faulty,
            fault=fault,
            repair=repair,
            validators=validators,
            provenance=provenance,
        )
    )
    return BenchmarkItem(
        item_id=item_id,
        generation=generation,
        problem=draft.problem,
        clean_solution=draft.clean,
        faulty_solution=faulty,
        fault=fault,
        repair=repair,
        content_digest=content_digest,
        validators=validators,
        provenance=provenance,
    )


def _assert_deterministic_generation(item: BenchmarkItem) -> None:
    """Bind all trusted record fields to a non-recursive coordinate replay."""

    expected = _build_item_from_coordinates(
        item.generation.seed,
        item.generation.index,
    )
    comparisons = (
        ("generation", item.generation, expected.generation),
        ("problem", item.problem, expected.problem),
        ("clean solution", item.clean_solution, expected.clean_solution),
        ("faulty solution", item.faulty_solution, expected.faulty_solution),
        ("fault label", item.fault, expected.fault),
        ("repair", item.repair, expected.repair),
        ("validators", item.validators, expected.validators),
        ("provenance", item.provenance, expected.provenance),
        ("schema", (item.schema, item.schema_version), (expected.schema, expected.schema_version)),
        ("item id", item.item_id, expected.item_id),
        ("content digest", item.content_digest, expected.content_digest),
    )
    mismatches = [
        label
        for label, actual, regenerated in comparisons
        if actual != regenerated
    ]
    if mismatches:
        raise ValueError(
            "deterministic generation replay mismatch: " + ", ".join(mismatches)
        )


def generate_item(seed: str, index: int) -> BenchmarkItem:
    """Generate and self-certify one item from stable public coordinates."""

    item = _build_item_from_coordinates(seed, index)
    _certify_mathematical_invariants(item)
    return item


def generate_bank(seed: str, count: int, *, start: int = 0) -> Iterator[BenchmarkItem]:
    """Lazily generate exactly ``count`` independently addressed items."""

    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or not 0 <= count <= MAX_MANIFEST_ITEMS
    ):
        raise ValueError(f"count must be an integer in [0, {MAX_MANIFEST_ITEMS}]")
    if (
        isinstance(start, bool)
        or not isinstance(start, int)
        or not 0 <= start <= BENCHMARK_CAPACITY
        or start + count > BENCHMARK_CAPACITY
    ):
        raise ValueError(f"requested window must fit the {BENCHMARK_CAPACITY}-item bank")
    for index in range(start, start + count):
        yield generate_item(seed, index)


def _merkle_root(leaf_hashes: Sequence[bytes]) -> bytes:
    """RFC 6962 Merkle Tree Hash over already domain-separated leaf hashes."""

    if not leaf_hashes:
        return _sha256(b"")
    if len(leaf_hashes) == 1:
        return leaf_hashes[0]
    split = 1 << ((len(leaf_hashes) - 1).bit_length() - 1)
    return _sha256(b"\x01" + _merkle_root(leaf_hashes[:split]) + _merkle_root(leaf_hashes[split:]))


def _manifest_count_pairs(
    values: Mapping[str, Any],
    label: str,
    max_entries: int,
) -> tuple[tuple[str, int], ...]:
    if len(values) > max_entries or not all(isinstance(key, str) for key in values):
        raise ValueError(f"manifest {label} exceeds its entry budget")
    return tuple(
        sorted(
            (
                _string(key, f"manifest {label} key", max_bytes=256),
                _integer(value, f"manifest {label} value"),
            )
            for key, value in values.items()
        )
    )


@dataclass(frozen=True, slots=True)
class BenchmarkManifest:
    seed: str
    start_index: int
    item_count: int
    family_counts: tuple[tuple[str, int], ...]
    fault_counts: tuple[tuple[str, int], ...]
    fault_location_counts: tuple[tuple[str, int], ...]
    family_fault_counts: tuple[tuple[str, int], ...]
    merkle_root: str
    semantic_root: str
    first_item_id: str | None
    last_item_id: str | None
    generator_version: str = GENERATOR_VERSION
    merkle_algorithm: str = MERKLE_ALGORITHM
    schedule_algorithm: str = SCHEDULE_ALGORITHM
    schema: str = MANIFEST_SCHEMA
    schema_version: int = MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema != MANIFEST_SCHEMA or self.schema_version != MANIFEST_SCHEMA_VERSION:
            raise ValueError("unsupported benchmark manifest v4 schema")
        if (
            self.generator_version != GENERATOR_VERSION
            or self.merkle_algorithm != MERKLE_ALGORITHM
            or self.schedule_algorithm != SCHEDULE_ALGORITHM
        ):
            raise ValueError("unsupported generator, schedule, or Merkle algorithm")
        _string(self.seed, "manifest seed", max_bytes=MAX_SHORT_STRING_BYTES)
        if (
            isinstance(self.start_index, bool)
            or not isinstance(self.start_index, int)
            or not 0 <= self.start_index <= BENCHMARK_CAPACITY
        ):
            raise ValueError("manifest start index falls outside the benchmark bank")
        if (
            isinstance(self.item_count, bool)
            or not isinstance(self.item_count, int)
            or not 0 <= self.item_count <= MAX_MANIFEST_ITEMS
        ):
            raise ValueError(
                f"manifest item count must be an integer in [0, {MAX_MANIFEST_ITEMS}]"
            )
        if self.start_index + self.item_count > BENCHMARK_CAPACITY:
            raise ValueError("manifest window exceeds the benchmark bank")
        if not isinstance(self.family_counts, tuple) or len(self.family_counts) > 64:
            raise ValueError("manifest family counts must be a bounded tuple")
        if not all(
            isinstance(entry, tuple)
            and len(entry) == 2
            and isinstance(entry[0], str)
            and isinstance(entry[1], int)
            and not isinstance(entry[1], bool)
            for entry in self.family_counts
        ):
            raise ValueError("manifest family counts must contain string-integer pairs")
        for family, count in self.family_counts:
            _string(family, "manifest family name", max_bytes=128)
            _integer(count, f"manifest count for {family}")
        if sum(count for _, count in self.family_counts) != self.item_count:
            raise ValueError("manifest family counts do not match item count")
        if any(count <= 0 for _, count in self.family_counts):
            raise ValueError("manifest family counts must be positive")
        if self.family_counts != tuple(sorted(self.family_counts)):
            raise ValueError("manifest family counts must be sorted")
        families = tuple(family for family, _ in self.family_counts)
        if len(families) != len(set(families)):
            raise ValueError("manifest family names must be unique")
        if not set(families) <= set(FAMILY_ORDER):
            raise ValueError("manifest names an unsupported benchmark family")
        for label, counts, limit in (
            ("fault counts", self.fault_counts, len(FAULT_ORDER)),
            ("fault-location counts", self.fault_location_counts, MAX_SOLUTION_STEPS),
            ("family-fault counts", self.family_fault_counts, len(FAMILY_ORDER) * len(FAULT_ORDER)),
        ):
            if not isinstance(counts, tuple) or len(counts) > limit:
                raise ValueError(f"manifest {label} must be a bounded tuple")
            if counts != tuple(sorted(counts)):
                raise ValueError(f"manifest {label} must be sorted")
            keys = [key for key, _ in counts]
            if len(keys) != len(set(keys)):
                raise ValueError(f"manifest {label} keys must be unique")
            for key, count in counts:
                _string(key, f"manifest {label} key", max_bytes=256)
                if _integer(count, f"manifest {label} value") <= 0:
                    raise ValueError(f"manifest {label} values must be positive")
        if sum(count for _, count in self.fault_counts) != self.item_count:
            raise ValueError("manifest fault counts do not match item count")
        if sum(count for _, count in self.fault_location_counts) != self.item_count:
            raise ValueError("manifest fault-location counts do not match item count")
        if sum(count for _, count in self.family_fault_counts) != self.item_count:
            raise ValueError("manifest family-fault counts do not match item count")
        expected_fault_keys = {kind.value for kind in FAULT_ORDER}
        fault_values = dict(self.fault_counts)
        if not set(fault_values) <= expected_fault_keys:
            raise ValueError("manifest names an unsupported fault kind")
        location_values = dict(self.fault_location_counts)
        for location in location_values:
            prefix, separator, number_text = location.partition("_")
            if prefix != "step" or separator != "_" or not number_text.isdecimal():
                raise ValueError("manifest names an invalid fault location")
            if not 1 <= int(number_text) <= MAX_SOLUTION_STEPS:
                raise ValueError("manifest fault location exceeds the step budget")
        pair_values = dict(self.family_fault_counts)
        expected_pairs = {
            f"{family}|{kind.value}"
            for family in FAMILY_ORDER
            for kind in FAULT_ORDER
        }
        if not set(pair_values) <= expected_pairs:
            raise ValueError("manifest names an unsupported family/fault pair")
        for family, family_count in self.family_counts:
            subtotal = sum(
                pair_values.get(f"{family}|{kind.value}", 0)
                for kind in FAULT_ORDER
            )
            if subtotal != family_count:
                raise ValueError("manifest family and family/fault subtotals disagree")
        for kind, fault_count in self.fault_counts:
            subtotal = sum(
                pair_values.get(f"{family}|{kind}", 0)
                for family in FAMILY_ORDER
            )
            if subtotal != fault_count:
                raise ValueError("manifest fault and family/fault subtotals disagree")
        if self.item_count == BENCHMARK_CAPACITY:
            if self.start_index != 0:
                raise ValueError("a full-bank manifest must start at coordinate zero")
            family_values = dict(self.family_counts)
            if set(family_values) != set(FAMILY_ORDER):
                raise ValueError("full-bank manifest must represent all benchmark families")
            if max(family_values.values()) - min(family_values.values()) > 1:
                raise ValueError("full-bank family counts differ by more than one")
            if dict(self.fault_counts) != {kind.value: 2_500 for kind in FAULT_ORDER}:
                raise ValueError("full-bank manifest must contain 2,500 faults of each kind")
            if {key for key, _ in self.family_fault_counts} != expected_pairs:
                raise ValueError("full-bank manifest must represent every family/fault pair")
        _sha256_string(self.merkle_root, "manifest Merkle root")
        _sha256_string(self.semantic_root, "manifest semantic root")
        if self.item_count == 0 and (
            self.first_item_id is not None or self.last_item_id is not None
        ):
            raise ValueError("empty manifest cannot name boundary items")
        if self.item_count and (self.first_item_id is None or self.last_item_id is None):
            raise ValueError("non-empty manifest must name boundary items")
        for label, item_id in (
            ("first item id", self.first_item_id),
            ("last item id", self.last_item_id),
        ):
            if item_id is not None:
                _string(item_id, label, max_bytes=80)

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_counts": {family: count for family, count in self.family_counts},
            "family_fault_counts": {key: count for key, count in self.family_fault_counts},
            "fault_counts": {kind: count for kind, count in self.fault_counts},
            "fault_location_counts": {
                location: count for location, count in self.fault_location_counts
            },
            "first_item_id": self.first_item_id,
            "generator_version": self.generator_version,
            "item_count": self.item_count,
            "last_item_id": self.last_item_id,
            "merkle_algorithm": self.merkle_algorithm,
            "merkle_root": self.merkle_root,
            "schedule_algorithm": self.schedule_algorithm,
            "schema": self.schema,
            "schema_version": self.schema_version,
            "seed": self.seed,
            "semantic_root": self.semantic_root,
            "start_index": self.start_index,
        }

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())

    def digest(self) -> str:
        return _digest(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Any) -> BenchmarkManifest:
        value = _mapping(raw, "benchmark manifest")
        version = _integer(value.get("schema_version"), "manifest schema version")
        if version in {1, 2, 3}:
            raise ValueError("legacy manifests must be regenerated under MathIR v4")
        if version != MANIFEST_SCHEMA_VERSION:
            raise ValueError("unsupported benchmark manifest schema version")
        _strict_keys(
            value,
            {
                "family_counts",
                "family_fault_counts",
                "fault_counts",
                "fault_location_counts",
                "first_item_id",
                "generator_version",
                "item_count",
                "last_item_id",
                "merkle_algorithm",
                "merkle_root",
                "schedule_algorithm",
                "schema",
                "schema_version",
                "seed",
                "semantic_root",
                "start_index",
            },
            "benchmark manifest",
        )
        family_counts = _mapping(value["family_counts"], "manifest family counts")
        fault_counts = _mapping(value["fault_counts"], "manifest fault counts")
        fault_locations = _mapping(
            value["fault_location_counts"],
            "manifest fault-location counts",
        )
        family_faults = _mapping(
            value["family_fault_counts"],
            "manifest family-fault counts",
        )
        return cls(
            seed=value["seed"],
            start_index=_integer(value["start_index"], "manifest start index"),
            item_count=_integer(value["item_count"], "manifest item count"),
            family_counts=_pairs(family_counts),
            fault_counts=_manifest_count_pairs(fault_counts, "fault counts", 4),
            fault_location_counts=_manifest_count_pairs(
                fault_locations,
                "fault-location counts",
                MAX_SOLUTION_STEPS,
            ),
            family_fault_counts=_manifest_count_pairs(
                family_faults,
                "family-fault counts",
                len(FAMILY_ORDER) * len(FAULT_ORDER),
            ),
            merkle_root=value["merkle_root"],
            semantic_root=value["semantic_root"],
            first_item_id=value["first_item_id"],
            last_item_id=value["last_item_id"],
            generator_version=value["generator_version"],
            merkle_algorithm=value["merkle_algorithm"],
            schedule_algorithm=value["schedule_algorithm"],
            schema=value["schema"],
            schema_version=version,
        )

    @classmethod
    def from_json(cls, raw: str) -> BenchmarkManifest:
        return cls.from_dict(
            _strict_json_loads(raw, label="manifest", max_bytes=MAX_MANIFEST_JSON_BYTES)
        )


def build_manifest(
    items: Iterable[BenchmarkItem],
    *,
    seed: str | None = None,
    start: int | None = None,
) -> BenchmarkManifest:
    """Commit an ordered item stream to a reproducible Merkle root."""

    if seed is not None:
        _string(seed, "manifest seed", max_bytes=MAX_SHORT_STRING_BYTES)
    if start is not None and (isinstance(start, bool) or not isinstance(start, int) or start < 0):
        raise ValueError("manifest start must be a non-negative integer")
    leaves: list[bytes] = []
    semantic_leaves: list[bytes] = []
    counts: Counter[str] = Counter()
    fault_counts: Counter[str] = Counter()
    fault_locations: Counter[str] = Counter()
    family_faults: Counter[str] = Counter()
    observed_seed: str | None = None
    observed_start: int | None = None
    first_id: str | None = None
    last_id: str | None = None
    previous_index: int | None = None
    seen_ids: set[str] = set()
    seen_semantics: set[str] = set()
    for item in items:
        if len(leaves) >= MAX_MANIFEST_ITEMS:
            raise ValueError(
                f"manifest item count exceeds the {MAX_MANIFEST_ITEMS}-item budget"
            )
        certify_item(item)
        if observed_seed is None:
            observed_seed = item.generation.seed
            observed_start = item.generation.index
            first_id = item.item_id
        elif item.generation.seed != observed_seed:
            raise ValueError("manifest items must share one seed")
        if previous_index is not None and item.generation.index != previous_index + 1:
            raise ValueError("manifest items must have consecutive indices in order")
        if item.item_id in seen_ids:
            raise ValueError("manifest contains a duplicate item id")
        if item.problem.semantic_fingerprint in seen_semantics:
            raise ValueError("manifest contains repeated canonical givens")
        previous_index = item.generation.index
        seen_ids.add(item.item_id)
        seen_semantics.add(item.problem.semantic_fingerprint)
        counts[item.problem.family] += 1
        fault_counts[item.fault.kind.value] += 1
        fault_locations[f"step_{item.fault.step_number}"] += 1
        family_faults[f"{item.problem.family}|{item.fault.kind.value}"] += 1
        last_id = item.item_id
        leaves.append(
            _sha256(
                b"\x00" + _utf8_bytes(item.canonical_json(), "benchmark item JSON")
            )
        )
        semantic_leaves.append(
            _sha256(
                b"\x00"
                + bytes.fromhex(item.problem.semantic_fingerprint.removeprefix("sha256:"))
            )
        )
    final_seed = observed_seed if observed_seed is not None else seed
    final_start = observed_start if observed_start is not None else start
    if final_seed is None:
        raise ValueError("empty item streams require an explicit seed")
    if final_start is None:
        final_start = 0
    if seed is not None and observed_seed is not None and seed != observed_seed:
        raise ValueError("explicit manifest seed does not match item stream")
    if start is not None and observed_start is not None and start != observed_start:
        raise ValueError("explicit manifest start does not match item stream")
    return BenchmarkManifest(
        seed=final_seed,
        start_index=final_start,
        item_count=len(leaves),
        family_counts=tuple(sorted(counts.items())),
        fault_counts=tuple(sorted(fault_counts.items())),
        fault_location_counts=tuple(sorted(fault_locations.items())),
        family_fault_counts=tuple(sorted(family_faults.items())),
        merkle_root="sha256:" + _merkle_root(leaves).hex(),
        semantic_root="sha256:" + _merkle_root(semantic_leaves).hex(),
        first_item_id=first_id,
        last_item_id=last_id,
    )


def generate_manifest(seed: str, count: int, *, start: int = 0) -> BenchmarkManifest:
    return build_manifest(generate_bank(seed, count, start=start), seed=seed, start=start)


def verify_manifest(manifest: BenchmarkManifest) -> bool:
    """Regenerate a manifest and compare every committed field in constant-time for digests."""

    if not isinstance(manifest, BenchmarkManifest):
        raise ValueError("manifest verification input must be a BenchmarkManifest")
    regenerated = generate_manifest(
        manifest.seed,
        manifest.item_count,
        start=manifest.start_index,
    )
    return hmac.compare_digest(regenerated.digest(), manifest.digest())


__all__ = [
    "BENCHMARK_CAPACITY",
    "BenchmarkItem",
    "BenchmarkManifest",
    "CounterRNG",
    "Difficulty",
    "Expr",
    "ExprOp",
    "FAMILY_ORDER",
    "FAMILY_SPECS",
    "FAULT_ORDER",
    "FamilySpec",
    "Fault",
    "FaultKind",
    "GENERATOR_SOURCE_HASH",
    "GENERATOR_BUNDLE_HASH",
    "GENERATOR_VERSION",
    "Generation",
    "MANIFEST_SCHEMA_VERSION",
    "MATHIR_SCHEMA",
    "MATHIR_SCHEMA_VERSION",
    "MAX_EXPRESSION_DEPTH",
    "MAX_EXPRESSION_NODES",
    "MAX_FRACTION_BITS",
    "MAX_ITEM_JSON_BYTES",
    "MAX_MANIFEST_ITEMS",
    "MAX_RNG_BOUND_BITS",
    "MAX_SOLVER_VIEW_JSON_BYTES",
    "MAX_SOLUTION_STEPS",
    "MAX_STRING_BYTES",
    "Problem",
    "PRIVATE_DATA_LICENSE",
    "Provenance",
    "Relation",
    "RepairReplay",
    "SCHEDULE_ALGORITHM",
    "SEMANTIC_SCHEMA",
    "SEMANTIC_SCHEMA_VERSION",
    "Solution",
    "SolutionStep",
    "SOLVER_VALID_FAULTS",
    "SolverAssignment",
    "SolverView",
    "ValidationReport",
    "build_manifest",
    "certify_item",
    "differing_steps",
    "evaluate_expression",
    "evaluate_relation",
    "generate_bank",
    "generate_item",
    "generate_manifest",
    "opaque_solver_id",
    "render_expression",
    "render_relation",
    "replay_repair",
    "validate_assertions",
    "validate_family_trace",
    "validate_problem_binding",
    "validate_solution",
    "verify_manifest",
]
