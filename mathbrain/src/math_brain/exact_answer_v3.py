"""Shared bounded exact-scalar grammar for both brain-benefit source lanes."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

MAX_EXACT_COMPONENT_V3 = 10**12
MAX_EXACT_ANSWER_BYTES_V3 = 256

_CANONICAL_INT_RE = re.compile(r"(?:0|[+-]?[1-9][0-9]*)\Z", re.ASCII)
_CANONICAL_UNSIGNED_RE = re.compile(r"(?:[1-9][0-9]*)\Z", re.ASCII)
_PLAIN_FRACTION_RE = re.compile(
    r"(?P<n>0|[+-]?[1-9][0-9]*)/(?P<d>[1-9][0-9]*)\Z",
    re.ASCII,
)
_TEX_FRACTION_RE = re.compile(
    r"\\(?:d?frac)\{(?P<n>0|[+-]?[1-9][0-9]*)\}"
    r"\{(?P<d>[1-9][0-9]*)\}\Z",
    re.ASCII,
)


def _bounded_utf8(raw: object) -> str:
    if type(raw) is not str:
        raise ValueError("exact answer must be exact text")
    if len(raw) > MAX_EXACT_ANSWER_BYTES_V3:
        raise ValueError("exact answer exceeds its byte budget")
    try:
        encoded = raw.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("exact answer must be valid UTF-8 text") from exc
    if len(encoded) > MAX_EXACT_ANSWER_BYTES_V3:
        raise ValueError("exact answer exceeds its byte budget")
    return raw


@dataclass(frozen=True, slots=True)
class ExactRationalV3:
    """A reduced bounded rational with a positive denominator."""

    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        if type(self) is not ExactRationalV3:
            raise ValueError("exact-rational subclasses are forbidden")
        self.validate_current()

    def validate_current(self) -> None:
        if type(self) is not ExactRationalV3:
            raise ValueError("exact-rational subclasses are forbidden")
        if type(self.numerator) is not int or type(self.denominator) is not int:
            raise ValueError("exact-rational components must be exact integers")
        if self.denominator <= 0:
            raise ValueError("exact-rational denominator must be positive")
        if abs(self.numerator) > MAX_EXACT_COMPONENT_V3 or (
            self.denominator > MAX_EXACT_COMPONENT_V3
        ):
            raise ValueError("exact-rational component exceeds its bound")
        if math.gcd(abs(self.numerator), self.denominator) != 1:
            raise ValueError("exact rational must be reduced")
        if self.numerator == 0 and self.denominator != 1:
            raise ValueError("zero must use denominator one")

    def to_dict(self) -> dict[str, int]:
        self.validate_current()
        return {"denominator": self.denominator, "numerator": self.numerator}


def _unwrap_exact_answer_v3(text: str) -> str:
    if text.startswith("$") and text.endswith("$") and len(text) >= 2:
        inner = text[1:-1]
        if "$" in inner:
            raise ValueError("nested dollar wrappers are forbidden")
        return inner
    if text.startswith(r"\(") and text.endswith(r"\)"):
        return text[2:-2]
    for command in (r"\boxed", r"\fbox"):
        prefix = command + "{"
        if text.startswith(prefix):
            depth = 1
            for index in range(len(prefix), len(text)):
                char = text[index]
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        if index != len(text) - 1:
                            raise ValueError("answer wrapper must consume the entire token")
                        return text[len(prefix) : index]
            raise ValueError("answer wrapper braces are unbalanced")
    return text


def parse_exact_rational_v3(raw: object) -> ExactRationalV3:
    """Parse one canonical integer/fraction token, with at most one outer wrapper."""

    text = _bounded_utf8(raw).strip(" \t\r\n")
    if not text or any(ord(char) > 127 for char in text):
        raise ValueError("exact answer must be a nonempty ASCII token")
    lexeme = _unwrap_exact_answer_v3(text)
    if not lexeme or lexeme != lexeme.strip(" \t\r\n"):
        raise ValueError("whitespace inside an answer wrapper is forbidden")

    numerator: int
    denominator: int
    match = _CANONICAL_INT_RE.fullmatch(lexeme)
    if match is not None:
        numerator, denominator = int(lexeme), 1
    else:
        match = _PLAIN_FRACTION_RE.fullmatch(lexeme) or _TEX_FRACTION_RE.fullmatch(lexeme)
        if match is None:
            raise ValueError("answer is not a canonical integer or fraction")
        numerator = int(match.group("n"))
        denominator_text = match.group("d")
        if _CANONICAL_UNSIGNED_RE.fullmatch(denominator_text) is None:
            raise ValueError("fraction denominator is not canonical")
        denominator = int(denominator_text)
    if abs(numerator) > MAX_EXACT_COMPONENT_V3 or denominator > MAX_EXACT_COMPONENT_V3:
        raise ValueError("raw exact-answer component exceeds its bound")
    divisor = math.gcd(abs(numerator), denominator)
    return ExactRationalV3(numerator // divisor, denominator // divisor)


__all__ = ["MAX_EXACT_COMPONENT_V3", "ExactRationalV3", "parse_exact_rational_v3"]
