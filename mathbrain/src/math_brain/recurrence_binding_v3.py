"""Typed answer-free recurrence provenance for v3 source-role schedules.

The binding has no free-form group identifier, arbitrary expression tree, packed
seed, answer axis, or surface field.  Its only inputs are the three exact integer
parameters of one fixed recurrence.  Source facts and the eight offset terms are
re-executed from that recurrence before any private source-role schedule is used.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from fractions import Fraction
from typing import Any

from .models import canonical_json
from .source_role_v3 import (
    SourceRoleBankV3,
    SourceRoleClaimBankV3,
)
from .source_role_v3 import (
    build_source_role_claim_bank_v3 as _build_source_role_claim_bank_v3,
)
from .source_role_v3 import (
    source_role_input_digest_v3 as _source_role_input_digest_v3,
)

RECURRENCE_PROGRAM_SCHEMA_V3 = "org.math-research-brain/recurrence-semantic-program"
RECURRENCE_BINDING_SCHEMA_V3 = "org.math-research-brain/recurrence-group-binding"
RECURRENCE_SCHEMA_VERSION_V3 = 1
RECURRENCE_EVALUATOR_V3 = "forced-linear-recurrence-exact-v1"
MAX_RECURRENCE_BINDING_JSON_BYTES_V3 = 16_384
MAX_RECURRENCE_FRACTION_BITS_V3 = 512

_DOMAIN_V3 = "forced_recurrence_resonance"
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_PROGRAM_DIGEST_DOMAIN = "org.math-research-brain/recurrence-program/v3"
_BINDING_DIGEST_DOMAIN = "org.math-research-brain/recurrence-binding/v3"


def _strict_keys(raw: object, expected: set[str], label: str) -> dict[str, Any]:
    if type(raw) is not dict or not all(type(key) is str for key in raw):
        raise ValueError(f"{label} must be an exact object")
    if set(raw) != expected:
        raise ValueError(f"{label} keys must be exactly {sorted(expected)}")
    return raw


def _integer(
    value: object,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an exact integer in [{minimum}, {maximum}]")
    return value


def _text(value: object, label: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{label} must be exact text")
    return value


def _digest(value: object, *, domain: str) -> str:
    payload = {"domain": domain, "payload": value}
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _validate_digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical SHA-256 digest")
    return value


def _bounded_fraction(value: Fraction, label: str) -> Fraction:
    if type(value) is not Fraction:
        raise ValueError(f"{label} must be an exact Fraction")
    if (
        abs(value.numerator).bit_length() > MAX_RECURRENCE_FRACTION_BITS_V3
        or value.denominator.bit_length() > MAX_RECURRENCE_FRACTION_BITS_V3
    ):
        raise ValueError(f"{label} exceeds the recurrence exact-fraction budget")
    return value


def _fraction_dict(value: Fraction) -> dict[str, int]:
    checked = _bounded_fraction(value, "serialized recurrence value")
    return {"denominator": checked.denominator, "numerator": checked.numerator}


def _fraction_from_dict(raw: object, label: str) -> Fraction:
    value = _strict_keys(raw, {"denominator", "numerator"}, label)
    numerator = _integer(
        value["numerator"],
        f"{label} numerator",
        minimum=-(1 << MAX_RECURRENCE_FRACTION_BITS_V3),
        maximum=1 << MAX_RECURRENCE_FRACTION_BITS_V3,
    )
    denominator = _integer(
        value["denominator"],
        f"{label} denominator",
        minimum=1,
        maximum=1 << MAX_RECURRENCE_FRACTION_BITS_V3,
    )
    result = _bounded_fraction(Fraction(numerator, denominator), label)
    if result.numerator != numerator or result.denominator != denominator:
        raise ValueError(f"{label} must use a reduced fraction with positive denominator")
    return result


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_canonical_json(raw: object, label: str) -> object:
    if type(raw) is not str:
        raise ValueError(f"{label} JSON must be exact text")
    try:
        encoded = raw.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} JSON must be valid UTF-8 text") from exc
    if len(encoded) > MAX_RECURRENCE_BINDING_JSON_BYTES_V3:
        raise ValueError(f"{label} JSON exceeds its byte budget")
    try:
        parsed = json.loads(raw, object_pairs_hook=_reject_duplicate_object)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{label} JSON is invalid: {exc}") from exc
    if raw != canonical_json(parsed):
        raise ValueError(f"{label} JSON must use canonical encoding")
    return parsed


@dataclass(frozen=True, slots=True)
class RecurrenceInstanceV3:
    """Only independently chosen mathematical inputs to the recurrence binding."""

    r: int
    c0: int
    c1: int

    def __post_init__(self) -> None:
        self.validate_current()

    def validate_current(self) -> None:
        if type(self) is not RecurrenceInstanceV3:
            raise ValueError("recurrence instance subclasses are forbidden")
        _integer(self.r, "recurrence root", minimum=2, maximum=7)
        _integer(self.c0, "recurrence initial a0", minimum=-9, maximum=9)
        _integer(self.c1, "recurrence initial a1", minimum=-9, maximum=9)

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return {"c0": self.c0, "c1": self.c1, "domain": _DOMAIN_V3, "r": self.r}

    @classmethod
    def from_dict(cls, raw: object) -> RecurrenceInstanceV3:
        value = _strict_keys(raw, {"c0", "c1", "domain", "r"}, "recurrence instance")
        if _text(value["domain"], "recurrence instance domain") != _DOMAIN_V3:
            raise ValueError("recurrence instance domain is unsupported")
        return cls(r=value["r"], c0=value["c0"], c1=value["c1"])


class RecurrenceSourceRoleV3(StrEnum):
    CANONICAL = "canonical"
    FIRST_ALTERNATIVE = "first_alt"
    SECOND_ALTERNATIVE = "second_alt"


class RecurrenceSourceExpressionKindV3(StrEnum):
    OPERATOR_ROOT_MULTIPLICITY = "operator_root_multiplicity"
    FORCING_ANNIHILATOR_MULTIPLICITY = "forcing_annihilator_multiplicity"
    COMBINED_ANNIHILATOR_MULTIPLICITY = "combined_annihilator_multiplicity"


@dataclass(frozen=True, slots=True)
class RecurrenceSourceExpressionV3:
    kind: RecurrenceSourceExpressionKindV3

    def __post_init__(self) -> None:
        self.validate_current()

    def validate_current(self) -> None:
        if type(self) is not RecurrenceSourceExpressionV3:
            raise ValueError("recurrence source expression subclasses are forbidden")
        if type(self.kind) is not RecurrenceSourceExpressionKindV3:
            raise ValueError("recurrence source expression kind is invalid")

    def to_dict(self) -> dict[str, str]:
        self.validate_current()
        return {"kind": self.kind.value}

    @classmethod
    def from_dict(cls, raw: object) -> RecurrenceSourceExpressionV3:
        value = _strict_keys(raw, {"kind"}, "recurrence source expression")
        try:
            kind = RecurrenceSourceExpressionKindV3(
                _text(value["kind"], "recurrence source expression kind")
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("recurrence source expression kind is invalid") from exc
        return cls(kind=kind)


@dataclass(frozen=True, slots=True)
class RecurrenceTermOffsetV3:
    """The only allowed offset AST: one schema-fixed recurrence term."""

    index: int

    def __post_init__(self) -> None:
        self.validate_current()

    def validate_current(self) -> None:
        if type(self) is not RecurrenceTermOffsetV3:
            raise ValueError("recurrence offset subclasses are forbidden")
        _integer(self.index, "recurrence offset index", minimum=0, maximum=7)

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return {"index": self.index, "kind": "recurrence_term"}

    @classmethod
    def from_dict(cls, raw: object) -> RecurrenceTermOffsetV3:
        value = _strict_keys(raw, {"index", "kind"}, "recurrence offset expression")
        if _text(value["kind"], "recurrence offset expression kind") != "recurrence_term":
            raise ValueError("recurrence offset expression kind is invalid")
        return cls(index=value["index"])


class RecurrenceClaimV3(StrEnum):
    CLAIM0 = "claim0"
    CLAIM1 = "claim1"
    CLAIM2 = "claim2"
    CLAIM3 = "claim3"
    CLAIM4 = "claim4"
    CLAIM5 = "claim5"
    CLAIM6 = "claim6"
    CLAIM7 = "claim7"


_SOURCE_EXPRESSION_KINDS = (
    RecurrenceSourceExpressionKindV3.OPERATOR_ROOT_MULTIPLICITY,
    RecurrenceSourceExpressionKindV3.FORCING_ANNIHILATOR_MULTIPLICITY,
    RecurrenceSourceExpressionKindV3.COMBINED_ANNIHILATOR_MULTIPLICITY,
)


def _trusted_source_expressions() -> tuple[RecurrenceSourceExpressionV3, ...]:
    return tuple(RecurrenceSourceExpressionV3(kind) for kind in _SOURCE_EXPRESSION_KINDS)


def _trusted_offset_expressions() -> tuple[RecurrenceTermOffsetV3, ...]:
    return tuple(RecurrenceTermOffsetV3(index) for index in range(8))


def _program_payload(
    instance: RecurrenceInstanceV3,
    source_expressions: tuple[RecurrenceSourceExpressionV3, ...],
    offset_expressions: tuple[RecurrenceTermOffsetV3, ...],
) -> dict[str, object]:
    return {
        "evaluator": RECURRENCE_EVALUATOR_V3,
        "instance": instance.to_dict(),
        "offset_expressions": [expression.to_dict() for expression in offset_expressions],
        "schema": RECURRENCE_PROGRAM_SCHEMA_V3,
        "schema_version": RECURRENCE_SCHEMA_VERSION_V3,
        "source_expressions": [expression.to_dict() for expression in source_expressions],
    }


@dataclass(frozen=True, slots=True)
class RecurrenceSemanticProgramV3:
    instance: RecurrenceInstanceV3
    source_expressions: tuple[RecurrenceSourceExpressionV3, ...] = field(init=False)
    offset_expressions: tuple[RecurrenceTermOffsetV3, ...] = field(init=False)
    program_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.instance) is not RecurrenceInstanceV3:
            raise ValueError("recurrence program requires an exact recurrence instance")
        self.instance.validate_current()
        source_expressions = _trusted_source_expressions()
        offset_expressions = _trusted_offset_expressions()
        object.__setattr__(self, "source_expressions", source_expressions)
        object.__setattr__(self, "offset_expressions", offset_expressions)
        object.__setattr__(
            self,
            "program_digest",
            _digest(
                _program_payload(self.instance, source_expressions, offset_expressions),
                domain=_PROGRAM_DIGEST_DOMAIN,
            ),
        )
        self.validate_current()

    def validate_current(self) -> None:
        if type(self) is not RecurrenceSemanticProgramV3:
            raise ValueError("recurrence program subclasses are forbidden")
        if type(self.instance) is not RecurrenceInstanceV3:
            raise ValueError("recurrence program instance is invalid")
        self.instance.validate_current()
        if type(self.source_expressions) is not tuple or len(self.source_expressions) != 3:
            raise ValueError("recurrence source expressions must be an exact three-item tuple")
        for expression in self.source_expressions:
            if type(expression) is not RecurrenceSourceExpressionV3:
                raise ValueError("recurrence program contains an invalid source expression")
            expression.validate_current()
        if self.source_expressions != _trusted_source_expressions():
            raise ValueError("recurrence source expressions are not the schema-fixed sequence")
        if type(self.offset_expressions) is not tuple or len(self.offset_expressions) != 8:
            raise ValueError("recurrence offset expressions must be an exact eight-item tuple")
        for expression in self.offset_expressions:
            if type(expression) is not RecurrenceTermOffsetV3:
                raise ValueError("recurrence program contains an invalid offset expression")
            expression.validate_current()
        if self.offset_expressions != _trusted_offset_expressions():
            raise ValueError("recurrence offset expressions are not exactly terms a0 through a7")
        _validate_digest(self.program_digest, "recurrence program digest")
        expected = _digest(
            _program_payload(self.instance, self.source_expressions, self.offset_expressions),
            domain=_PROGRAM_DIGEST_DOMAIN,
        )
        if not hmac.compare_digest(expected, self.program_digest):
            raise ValueError("recurrence program digest does not bind its typed program")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return _program_payload(
            self.instance, self.source_expressions, self.offset_expressions
        ) | {"program_digest": self.program_digest}

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, raw: object) -> RecurrenceSemanticProgramV3:
        expected_keys = {
            "evaluator",
            "instance",
            "offset_expressions",
            "program_digest",
            "schema",
            "schema_version",
            "source_expressions",
        }
        value = _strict_keys(raw, expected_keys, "recurrence semantic program")
        schema = _text(value["schema"], "recurrence semantic program schema")
        schema_version = _integer(
            value["schema_version"],
            "recurrence semantic program schema version",
            minimum=RECURRENCE_SCHEMA_VERSION_V3,
            maximum=RECURRENCE_SCHEMA_VERSION_V3,
        )
        evaluator = _text(value["evaluator"], "recurrence semantic program evaluator")
        if (
            schema != RECURRENCE_PROGRAM_SCHEMA_V3
            or schema_version != RECURRENCE_SCHEMA_VERSION_V3
            or evaluator != RECURRENCE_EVALUATOR_V3
        ):
            raise ValueError("recurrence semantic program schema is unsupported")
        raw_sources = value["source_expressions"]
        raw_offsets = value["offset_expressions"]
        if type(raw_sources) is not list or type(raw_offsets) is not list:
            raise ValueError("recurrence program expressions must be exact arrays")
        if len(raw_sources) != 3 or len(raw_offsets) != 8:
            raise ValueError("recurrence program requires exactly three sources and eight offsets")
        parsed_sources = tuple(
            RecurrenceSourceExpressionV3.from_dict(item) for item in raw_sources
        )
        parsed_offsets = tuple(RecurrenceTermOffsetV3.from_dict(item) for item in raw_offsets)
        program = cls(instance=RecurrenceInstanceV3.from_dict(value["instance"]))
        if parsed_sources != program.source_expressions:
            raise ValueError("serialized recurrence sources differ from the trusted recipe")
        if parsed_offsets != program.offset_expressions:
            raise ValueError("serialized recurrence offsets differ from the trusted recipe")
        digest = _validate_digest(value["program_digest"], "serialized program digest")
        if not hmac.compare_digest(digest, program.program_digest):
            raise ValueError("serialized recurrence program digest is incorrect")
        return program

    @classmethod
    def from_json(cls, raw: object) -> RecurrenceSemanticProgramV3:
        return cls.from_dict(_load_canonical_json(raw, "recurrence program"))


@dataclass(frozen=True, slots=True)
class RecurrenceSourceReceiptV3:
    role: RecurrenceSourceRoleV3
    expression: RecurrenceSourceExpressionV3
    value: Fraction

    def __post_init__(self) -> None:
        self.validate_current()

    def validate_current(self) -> None:
        if type(self) is not RecurrenceSourceReceiptV3:
            raise ValueError("recurrence source receipt subclasses are forbidden")
        if type(self.role) is not RecurrenceSourceRoleV3:
            raise ValueError("recurrence source receipt role is invalid")
        if type(self.expression) is not RecurrenceSourceExpressionV3:
            raise ValueError("recurrence source receipt expression is invalid")
        self.expression.validate_current()
        _bounded_fraction(self.value, "recurrence source receipt value")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return {
            "expression": self.expression.to_dict(),
            "role": self.role.value,
            "value": _fraction_dict(self.value),
        }

    @classmethod
    def from_dict(cls, raw: object) -> RecurrenceSourceReceiptV3:
        value = _strict_keys(raw, {"expression", "role", "value"}, "source receipt")
        try:
            role = RecurrenceSourceRoleV3(
                _text(value["role"], "recurrence source receipt role")
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("recurrence source receipt role is invalid") from exc
        return cls(
            role=role,
            expression=RecurrenceSourceExpressionV3.from_dict(value["expression"]),
            value=_fraction_from_dict(value["value"], "source receipt value"),
        )


@dataclass(frozen=True, slots=True)
class RecurrenceOffsetReceiptV3:
    claim: RecurrenceClaimV3
    expression: RecurrenceTermOffsetV3
    value: Fraction

    def __post_init__(self) -> None:
        self.validate_current()

    def validate_current(self) -> None:
        if type(self) is not RecurrenceOffsetReceiptV3:
            raise ValueError("recurrence offset receipt subclasses are forbidden")
        if type(self.claim) is not RecurrenceClaimV3:
            raise ValueError("recurrence offset receipt claim is invalid")
        if type(self.expression) is not RecurrenceTermOffsetV3:
            raise ValueError("recurrence offset receipt expression is invalid")
        self.expression.validate_current()
        _bounded_fraction(self.value, "recurrence offset receipt value")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return {
            "claim": self.claim.value,
            "expression": self.expression.to_dict(),
            "value": _fraction_dict(self.value),
        }

    @classmethod
    def from_dict(cls, raw: object) -> RecurrenceOffsetReceiptV3:
        value = _strict_keys(raw, {"claim", "expression", "value"}, "offset receipt")
        try:
            claim = RecurrenceClaimV3(
                _text(value["claim"], "recurrence offset receipt claim")
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("recurrence offset receipt claim is invalid") from exc
        return cls(
            claim=claim,
            expression=RecurrenceTermOffsetV3.from_dict(value["expression"]),
            value=_fraction_from_dict(value["value"], "offset receipt value"),
        )


def _recurrence_terms(instance: RecurrenceInstanceV3) -> tuple[Fraction, ...]:
    instance.validate_current()
    root = Fraction(instance.r)
    terms = [Fraction(instance.c0), Fraction(instance.c1)]
    for index in range(6):
        middle = _bounded_fraction(Fraction(2) * root * terms[-1], "recurrence middle term")
        trailing = _bounded_fraction(root * root * terms[-2], "recurrence trailing term")
        forcing = _bounded_fraction(root**index, "recurrence forcing term")
        terms.append(
            _bounded_fraction(middle - trailing + forcing, "recurrence derived term")
        )
    for index, observed in enumerate(terms):
        closed_form = _bounded_fraction(
            root**index
            * (
                Fraction(instance.c0)
                + index * (Fraction(instance.c1, instance.r) - instance.c0)
                + Fraction(index * (index - 1), 2 * instance.r * instance.r)
            ),
            "recurrence closed-form term",
        )
        if observed != closed_form:
            raise ValueError("iterative and closed-form recurrence executions disagree")
    return tuple(terms)


def _source_receipts(
    program: RecurrenceSemanticProgramV3,
) -> tuple[RecurrenceSourceReceiptV3, ...]:
    program.validate_current()

    def execute(
        expression: RecurrenceSourceExpressionV3,
    ) -> tuple[RecurrenceSourceRoleV3, Fraction]:
        expression.validate_current()
        if (
            expression.kind
            is RecurrenceSourceExpressionKindV3.OPERATOR_ROOT_MULTIPLICITY
        ):
            return RecurrenceSourceRoleV3.CANONICAL, Fraction(2)
        if (
            expression.kind
            is RecurrenceSourceExpressionKindV3.FORCING_ANNIHILATOR_MULTIPLICITY
        ):
            return RecurrenceSourceRoleV3.FIRST_ALTERNATIVE, Fraction(1)
        if (
            expression.kind
            is RecurrenceSourceExpressionKindV3.COMBINED_ANNIHILATOR_MULTIPLICITY
        ):
            return RecurrenceSourceRoleV3.SECOND_ALTERNATIVE, Fraction(3)
        raise ValueError("recurrence source expression has no trusted evaluator")

    receipts: list[RecurrenceSourceReceiptV3] = []
    for expression in program.source_expressions:
        role, source_value = execute(expression)
        receipts.append(
            RecurrenceSourceReceiptV3(
                role=role,
                expression=expression,
                value=source_value,
            )
        )
    return tuple(receipts)


def _offset_receipts(
    program: RecurrenceSemanticProgramV3,
) -> tuple[RecurrenceOffsetReceiptV3, ...]:
    program.validate_current()
    terms = _recurrence_terms(program.instance)
    return tuple(
        RecurrenceOffsetReceiptV3(
            claim=RecurrenceClaimV3(f"claim{expression.index}"),
            expression=expression,
            value=terms[expression.index],
        )
        for expression in program.offset_expressions
    )


def _binding_payload(
    program: RecurrenceSemanticProgramV3,
    sources: tuple[RecurrenceSourceReceiptV3, ...],
    offsets: tuple[RecurrenceOffsetReceiptV3, ...],
) -> dict[str, object]:
    return {
        "offsets": [receipt.to_dict() for receipt in offsets],
        "program": program.to_dict(),
        "schema": RECURRENCE_BINDING_SCHEMA_V3,
        "schema_version": RECURRENCE_SCHEMA_VERSION_V3,
        "sources": [receipt.to_dict() for receipt in sources],
    }


@dataclass(frozen=True, slots=True)
class RecurrenceGroupBindingV3:
    program: RecurrenceSemanticProgramV3
    sources: tuple[RecurrenceSourceReceiptV3, ...] = field(init=False)
    offsets: tuple[RecurrenceOffsetReceiptV3, ...] = field(init=False)
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.program) is not RecurrenceSemanticProgramV3:
            raise ValueError("recurrence binding requires an exact semantic program")
        self.program.validate_current()
        sources = _source_receipts(self.program)
        offsets = _offset_receipts(self.program)
        object.__setattr__(self, "sources", sources)
        object.__setattr__(self, "offsets", offsets)
        object.__setattr__(
            self,
            "content_digest",
            _digest(
                _binding_payload(self.program, sources, offsets),
                domain=_BINDING_DIGEST_DOMAIN,
            ),
        )
        self.validate_current()

    def validate_current(self) -> None:
        if type(self) is not RecurrenceGroupBindingV3:
            raise ValueError("recurrence binding subclasses are forbidden")
        if type(self.program) is not RecurrenceSemanticProgramV3:
            raise ValueError("recurrence binding program is invalid")
        self.program.validate_current()
        expected_sources = _source_receipts(self.program)
        expected_offsets = _offset_receipts(self.program)
        if type(self.sources) is not tuple or len(self.sources) != 3:
            raise ValueError("recurrence source receipts must be an exact three-item tuple")
        for item in self.sources:
            if type(item) is not RecurrenceSourceReceiptV3:
                raise ValueError("recurrence binding contains an invalid source receipt")
            item.validate_current()
        if self.sources != expected_sources:
            raise ValueError("recurrence source receipts differ from exact execution")
        if type(self.offsets) is not tuple or len(self.offsets) != 8:
            raise ValueError("recurrence offset receipts must be an exact eight-item tuple")
        for item in self.offsets:
            if type(item) is not RecurrenceOffsetReceiptV3:
                raise ValueError("recurrence binding contains an invalid offset receipt")
            item.validate_current()
        if self.offsets != expected_offsets:
            raise ValueError("recurrence offset receipts differ from exact execution")
        _validate_digest(self.content_digest, "recurrence binding digest")
        expected_digest = _digest(
            _binding_payload(self.program, self.sources, self.offsets),
            domain=_BINDING_DIGEST_DOMAIN,
        )
        if not hmac.compare_digest(expected_digest, self.content_digest):
            raise ValueError("recurrence binding digest does not bind its exact execution")

    def source_role_inputs(
        self,
    ) -> tuple[SourceRoleBankV3, tuple[str, ...], tuple[Fraction, ...]]:
        self.validate_current()
        source_bank = SourceRoleBankV3(
            source_names=tuple(receipt.role.value for receipt in self.sources),
            source_values=tuple(receipt.value for receipt in self.sources),
        )
        targets = tuple(receipt.claim.value for receipt in self.offsets)
        offsets = tuple(receipt.value for receipt in self.offsets)
        return source_bank, targets, offsets

    def source_role_input_digest(self) -> str:
        source_bank, targets, offsets = self.source_role_inputs()
        return _source_role_input_digest_v3(
            source_bank=source_bank,
            group_binding_digest=self.content_digest,
            targets=targets,
            offsets=offsets,
        )

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return _binding_payload(self.program, self.sources, self.offsets) | {
            "content_digest": self.content_digest
        }

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, raw: object) -> RecurrenceGroupBindingV3:
        expected_keys = {
            "content_digest",
            "offsets",
            "program",
            "schema",
            "schema_version",
            "sources",
        }
        value = _strict_keys(raw, expected_keys, "recurrence group binding")
        schema = _text(value["schema"], "recurrence group binding schema")
        schema_version = _integer(
            value["schema_version"],
            "recurrence group binding schema version",
            minimum=RECURRENCE_SCHEMA_VERSION_V3,
            maximum=RECURRENCE_SCHEMA_VERSION_V3,
        )
        if (
            schema != RECURRENCE_BINDING_SCHEMA_V3
            or schema_version != RECURRENCE_SCHEMA_VERSION_V3
        ):
            raise ValueError("recurrence group binding schema is unsupported")
        raw_sources = value["sources"]
        raw_offsets = value["offsets"]
        if type(raw_sources) is not list or type(raw_offsets) is not list:
            raise ValueError("recurrence binding receipts must be exact arrays")
        if len(raw_sources) != 3 or len(raw_offsets) != 8:
            raise ValueError("recurrence binding requires exactly three sources and eight offsets")
        parsed_sources = tuple(RecurrenceSourceReceiptV3.from_dict(item) for item in raw_sources)
        parsed_offsets = tuple(RecurrenceOffsetReceiptV3.from_dict(item) for item in raw_offsets)
        binding = cls(program=RecurrenceSemanticProgramV3.from_dict(value["program"]))
        if parsed_sources != binding.sources:
            raise ValueError("serialized recurrence sources differ from exact execution")
        if parsed_offsets != binding.offsets:
            raise ValueError("serialized recurrence offsets differ from exact execution")
        digest = _validate_digest(value["content_digest"], "serialized binding digest")
        if not hmac.compare_digest(digest, binding.content_digest):
            raise ValueError("serialized recurrence binding digest is incorrect")
        return binding

    @classmethod
    def from_json(cls, raw: object) -> RecurrenceGroupBindingV3:
        return cls.from_dict(_load_canonical_json(raw, "recurrence binding"))


def build_recurrence_group_binding_v3(
    instance: RecurrenceInstanceV3,
) -> RecurrenceGroupBindingV3:
    if type(instance) is not RecurrenceInstanceV3:
        raise ValueError("recurrence binding construction requires an exact instance")
    instance.validate_current()
    return RecurrenceGroupBindingV3(program=RecurrenceSemanticProgramV3(instance=instance))


def _validated_expected_binding(
    binding: RecurrenceGroupBindingV3,
    expected_binding_digest: str,
) -> str:
    if type(binding) is not RecurrenceGroupBindingV3:
        raise ValueError("recurrence source-role integration requires an exact binding")
    binding.validate_current()
    expected = _validate_digest(expected_binding_digest, "expected recurrence binding digest")
    if not hmac.compare_digest(expected, binding.content_digest):
        raise ValueError("recurrence binding does not match its preregistered digest")
    return expected


def build_recurrence_source_role_claim_bank_v3(
    *,
    binding: RecurrenceGroupBindingV3,
    schedule_seed: bytes,
    expected_binding_digest: str,
) -> SourceRoleClaimBankV3:
    """Schedule only re-executed typed recurrence values from a pinned binding."""

    _validated_expected_binding(binding, expected_binding_digest)
    source_bank, targets, offsets = binding.source_role_inputs()
    return _build_source_role_claim_bank_v3(
        source_bank=source_bank,
        group_binding_digest=binding.content_digest,
        targets=targets,
        offsets=offsets,
        schedule_seed=schedule_seed,
    )


def verify_recurrence_source_role_claim_bank_v3(
    *,
    binding: RecurrenceGroupBindingV3,
    claim_bank: SourceRoleClaimBankV3,
    schedule_seed: bytes,
    expected_binding_digest: str,
) -> None:
    _validated_expected_binding(binding, expected_binding_digest)
    if type(claim_bank) is not SourceRoleClaimBankV3:
        raise ValueError("recurrence verification requires an exact source-role claim bank")
    claim_bank.validate_current()
    if not hmac.compare_digest(claim_bank.group_binding_digest, binding.content_digest):
        raise ValueError("source-role claim bank is not bound to the recurrence execution")
    claim_bank.verify_schedule_seed(
        schedule_seed,
        expected_input_digest=binding.source_role_input_digest(),
    )
    rebuilt = build_recurrence_source_role_claim_bank_v3(
        binding=binding,
        schedule_seed=schedule_seed,
        expected_binding_digest=expected_binding_digest,
    )
    if rebuilt != claim_bank:
        raise ValueError("recurrence binding does not reconstruct the source-role claim bank")


__all__ = [
    "MAX_RECURRENCE_BINDING_JSON_BYTES_V3",
    "MAX_RECURRENCE_FRACTION_BITS_V3",
    "RECURRENCE_BINDING_SCHEMA_V3",
    "RECURRENCE_EVALUATOR_V3",
    "RECURRENCE_PROGRAM_SCHEMA_V3",
    "RECURRENCE_SCHEMA_VERSION_V3",
    "RecurrenceClaimV3",
    "RecurrenceGroupBindingV3",
    "RecurrenceInstanceV3",
    "RecurrenceOffsetReceiptV3",
    "RecurrenceSemanticProgramV3",
    "RecurrenceSourceExpressionKindV3",
    "RecurrenceSourceExpressionV3",
    "RecurrenceSourceReceiptV3",
    "RecurrenceSourceRoleV3",
    "RecurrenceTermOffsetV3",
    "build_recurrence_group_binding_v3",
    "build_recurrence_source_role_claim_bank_v3",
    "verify_recurrence_source_role_claim_bank_v3",
]
