"""Independent exact-oracle bundle for MathIR v4 benchmark certification.

This module deliberately does not import :mod:`math_brain.benchmark`.  It reimplements each
family algorithm and emits a small neutral expression specification.  The generator converts
that specification to MathIR only on the verification path, so serialized answers and traces are
checked by code that is separate from the code that created them.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import comb, gcd, isqrt
from typing import Any, Mapping


ExprSpec = tuple[Any, ...]
RelationSpec = tuple[ExprSpec, ExprSpec]


@dataclass(frozen=True, slots=True)
class IndependentProblem:
    prompt: str
    answer: str
    difficulty: str
    witness: tuple[tuple[str, int], ...]
    relations: tuple[RelationSpec, ...]


def _c(value: int) -> ExprSpec:
    return ("const", value)


def _v(name: str) -> ExprSpec:
    return ("var", name)


def _add(left: ExprSpec, right: ExprSpec) -> ExprSpec:
    return ("add", left, right)


def _sub(left: ExprSpec, right: ExprSpec) -> ExprSpec:
    return ("sub", left, right)


def _mul(left: ExprSpec, right: ExprSpec) -> ExprSpec:
    return ("mul", left, right)


def _pow(left: ExprSpec, right: ExprSpec) -> ExprSpec:
    return ("pow", left, right)


def _rel(left: ExprSpec, right: ExprSpec) -> RelationSpec:
    return (left, right)


def _pairs(values: Mapping[str, int]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(values.items()))


def _require(givens: Mapping[str, int], names: set[str]) -> None:
    if set(givens) != names:
        raise ValueError("independent verifier received unexpected givens")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in givens.values()):
        raise ValueError("independent verifier givens must be integers")


def _bundle(
    prompt: str,
    answer: str,
    difficulty: str,
    witness: Mapping[str, int],
    relations: tuple[RelationSpec, ...],
) -> IndependentProblem:
    if not relations:
        raise ValueError("independent certificate must contain meaningful relations")
    return IndependentProblem(prompt, answer, difficulty, _pairs(witness), relations)


def _verify_progression(g: Mapping[str, int]) -> IndependentProblem:
    _require(g, {"a", "d", "n"})
    a, d, n = g["a"], g["d"], g["n"]
    if not (1 <= a <= 30 and 1 <= d <= 12 and 3 <= n <= 30):
        raise ValueError("progression givens outside verifier domain")
    last = a + (n - 1) * d
    total = n * (a + last) // 2
    return _bundle(
        f"Find the sum of the first {n} terms of the arithmetic progression "
        f"with first term {a} and common difference {d}.",
        f"S = {total}",
        "elementary",
        {"L": last, "S": total},
        (
            _rel(_v("L"), _add(_c(a), _mul(_c(n - 1), _c(d)))),
            _rel(_mul(_c(2), _v("S")), _mul(_c(n), _add(_c(a), _v("L")))),
            _rel(_v("S"), _c(total)),
        ),
    )


def _verify_linear(g: Mapping[str, int]) -> IndependentProblem:
    _require(g, {"a", "b", "c"})
    a, b, c = g["a"], g["b"], g["c"]
    allowed_a = set(range(-12, -1)) | set(range(2, 13))
    allowed_b = set(range(-20, 0)) | set(range(1, 21))
    if a not in allowed_a or b not in allowed_b or (c - b) % a:
        raise ValueError("linear givens outside verifier domain")
    root = (c - b) // a
    if not -30 <= root <= 30 or root == 0:
        raise ValueError("linear root outside verifier domain")
    return _bundle(
        f"Solve over the integers: ({a} * x) + ({b}) = {c}.",
        f"x = {root}",
        "elementary",
        {"x": root},
        (
            _rel(_add(_mul(_c(a), _v("x")), _c(b)), _c(c)),
            _rel(_mul(_c(a), _v("x")), _sub(_c(c), _c(b))),
            _rel(_v("x"), _c(root)),
        ),
    )


_CANONICAL_PYTHAGOREAN_LEGS = {
    tuple(sorted(((m * m - n * n) * scale, 2 * m * n * scale)))
    for m in range(2, 31)
    for n in range(1, m)
    if gcd(m, n) == 1 and (m - n) % 2 == 1
    for scale in range(1, 9)
}


def _verify_pythagorean(g: Mapping[str, int]) -> IndependentProblem:
    _require(g, {"a", "b"})
    a, b = g["a"], g["b"]
    square = a * a + b * b
    hypotenuse = isqrt(square)
    if (
        (a, b) not in _CANONICAL_PYTHAGOREAN_LEGS
        or hypotenuse * hypotenuse != square
    ):
        raise ValueError("Pythagorean legs outside verifier domain")
    return _bundle(
        f"A right triangle has legs {a} and {b}. Find its integer hypotenuse.",
        f"h = {hypotenuse}",
        "elementary",
        {"h": hypotenuse},
        (
            _rel(
                _mul(_v("h"), _v("h")),
                _add(_mul(_c(a), _c(a)), _mul(_c(b), _c(b))),
            ),
            _rel(_v("h"), _c(hypotenuse)),
        ),
    )


def _verify_quadratic(g: Mapping[str, int]) -> IndependentProblem:
    _require(g, {"b", "c"})
    b, c = g["b"], g["c"]
    gap = isqrt(b * b - 4 * c)
    if gap * gap != b * b - 4 * c or (-b - gap) % 2 or (-b + gap) % 2:
        raise ValueError("quadratic givens lack integer roots")
    lower, upper = (-b - gap) // 2, (-b + gap) // 2
    if not (1 <= lower < upper <= 40):
        raise ValueError("quadratic roots outside verifier domain")
    return _bundle(
        f"Find the larger integer root of x^2 + ({b} * x) + ({c}) = 0.",
        f"x = {upper}",
        "undergraduate",
        {"r1": lower, "r2": upper},
        (
            _rel(_add(_v("r1"), _v("r2")), _c(-b)),
            _rel(_mul(_v("r1"), _v("r2")), _c(c)),
            _rel(_v("r2"), _c(upper)),
        ),
    )


def _verify_subsets(g: Mapping[str, int]) -> IndependentProblem:
    _require(g, {"k", "n"})
    k, n = g["k"], g["n"]
    if not (12 <= n <= 75 and 2 <= k <= 10):
        raise ValueError("subset givens outside verifier domain")
    count = comb(n, k)
    witness = {"c0": 1, "N": count}
    relations: list[RelationSpec] = [_rel(_v("c0"), _c(1))]
    previous = "c0"
    for selected in range(1, k + 1):
        current = f"c{selected}"
        witness[current] = comb(n, selected)
        relations.append(
            _rel(
                _mul(_c(selected), _v(current)),
                _mul(
                    _add(_sub(_c(n), _c(selected)), _c(1)),
                    _v(previous),
                ),
            )
        )
        previous = current
    relations.append(_rel(_v("N"), _v(previous)))
    return _bundle(
        f"How many {k}-element subsets does an {n}-element set have?",
        f"N = {count}",
        "undergraduate",
        witness,
        tuple(relations),
    )


def _verify_geometric(g: Mapping[str, int]) -> IndependentProblem:
    _require(g, {"a", "n", "r"})
    a, n, ratio = g["a"], g["n"], g["r"]
    if not (1 <= a <= 20 and 3 <= n <= 12 and 2 <= ratio <= 4):
        raise ValueError("geometric givens outside verifier domain")
    last = a * ratio ** (n - 1)
    total = a * (ratio**n - 1) // (ratio - 1)
    return _bundle(
        f"Find the sum of the first {n} terms of the geometric progression "
        f"with first term {a} and ratio {ratio}.",
        f"S = {total}",
        "elementary",
        {"L": last, "S": total},
        (
            _rel(_v("L"), _mul(_c(a), _pow(_c(ratio), _c(n - 1)))),
            _rel(
                _mul(_sub(_c(ratio), _c(1)), _v("S")),
                _mul(_c(a), _sub(_pow(_c(ratio), _c(n)), _c(1))),
            ),
            _rel(_v("S"), _c(total)),
        ),
    )


_SYSTEM_MATRICES = {
    (1, 1, 1, -1),
    (2, 1, 1, -1),
    (1, 2, 3, -1),
    (2, -1, 1, 2),
    (3, 1, 1, 2),
    (1, -2, 2, 1),
    (2, 3, 1, -1),
    (3, -2, 2, 1),
}


def _verify_system(g: Mapping[str, int]) -> IndependentProblem:
    _require(g, {"a", "b", "c", "d", "e", "f"})
    a, b, c, d, e, f = (g[name] for name in ("a", "b", "c", "d", "e", "f"))
    if (a, b, c, d) not in _SYSTEM_MATRICES:
        raise ValueError("system matrix outside verifier domain")
    determinant = a * d - b * c
    x, x_remainder = divmod(e * d - b * f, determinant)
    y, y_remainder = divmod(a * f - e * c, determinant)
    if x_remainder or y_remainder or not (1 <= x <= 20 and 1 <= y <= 20):
        raise ValueError("system solution outside verifier domain")
    return _bundle(
        f"Solve the system {a}x + {b}y = {e}, {c}x + {d}y = {f}; report x.",
        f"x = {x}",
        "undergraduate",
        {"x": x, "y": y},
        (
            _rel(_add(_mul(_c(a), _v("x")), _mul(_c(b), _v("y"))), _c(e)),
            _rel(_add(_mul(_c(c), _v("x")), _mul(_c(d), _v("y"))), _c(f)),
            _rel(_v("x"), _c(x)),
        ),
    )


def _verify_horner(g: Mapping[str, int]) -> IndependentProblem:
    _require(g, {"a0", "a1", "a2", "a3", "x"})
    a0, a1, a2, a3, x = (g[name] for name in ("a0", "a1", "a2", "a3", "x"))
    if not (1 <= a3 <= 5 and all(0 <= v <= 4 for v in (a0, a1, a2)) and 2 <= x <= 6):
        raise ValueError("Horner givens outside verifier domain")
    h1 = a3 * x + a2
    h2 = h1 * x + a1
    value = h2 * x + a0
    return _bundle(
        f"Evaluate {a3}x^3 + {a2}x^2 + {a1}x + {a0} at x = {x} using Horner's rule.",
        f"P({x}) = {value}",
        "undergraduate",
        {"h1": h1, "h2": h2, "value": value},
        (
            _rel(_v("h1"), _add(_mul(_c(a3), _c(x)), _c(a2))),
            _rel(_v("h2"), _add(_mul(_v("h1"), _c(x)), _c(a1))),
            _rel(_v("value"), _add(_mul(_v("h2"), _c(x)), _c(a0))),
            _rel(
                _sub(_v("value"), _mul(_c(a3), _pow(_c(x), _c(3)))),
                _add(
                    _add(
                        _mul(_c(a2), _pow(_c(x), _c(2))),
                        _mul(_c(a1), _c(x)),
                    ),
                    _c(a0),
                ),
            ),
        ),
    )


def _extended_gcd(a: int, b: int) -> tuple[int, int, int]:
    old_r, r, old_s, s, old_t, t = a, b, 1, 0, 0, 1
    while r:
        quotient = old_r // r
        old_r, r = r, old_r - quotient * r
        old_s, s = s, old_s - quotient * s
        old_t, t = t, old_t - quotient * t
    return old_r, old_s, old_t


def _verify_gcd(g: Mapping[str, int]) -> IndependentProblem:
    _require(g, {"a", "b"})
    a, b = g["a"], g["b"]
    if not (100 <= a <= 999 and 10 <= b <= 99):
        raise ValueError("gcd givens outside verifier domain")
    witness: dict[str, int] = {}
    relations: list[RelationSpec] = []
    dividend, divisor, step = a, b, 0
    while divisor:
        quotient, remainder = divmod(dividend, divisor)
        witness[f"q{step}"] = quotient
        witness[f"r{step}"] = remainder
        relations.append(
            _rel(
                _c(dividend),
                _add(_mul(_v(f"q{step}"), _c(divisor)), _v(f"r{step}")),
            )
        )
        dividend, divisor = divisor, remainder
        step += 1
    common, u, v = _extended_gcd(a, b)
    witness.update({"g": common, "u": u, "v": v})
    relations.extend(
        (
            _rel(_add(_mul(_c(a), _v("u")), _mul(_c(b), _v("v"))), _v("g")),
            _rel(_v("g"), _c(common)),
        )
    )
    return _bundle(
        f"Use the Euclidean algorithm to compute gcd({a}, {b}).",
        f"gcd = {common}",
        "undergraduate",
        witness,
        tuple(relations),
    )


def _verify_inverse(g: Mapping[str, int]) -> IndependentProblem:
    _require(g, {"a", "m"})
    a, modulus = g["a"], g["m"]
    if not (11 <= modulus <= 100 and 2 <= a < modulus and gcd(a, modulus) == 1):
        raise ValueError("inverse givens outside verifier domain")
    inverse = pow(a, -1, modulus)
    quotient = (a * inverse - 1) // modulus
    return _bundle(
        f"Find the least positive inverse of {a} modulo {modulus}.",
        f"inverse = {inverse}",
        "undergraduate",
        {"inverse": inverse, "q": quotient},
        (
            _rel(
                _mul(_c(a), _v("inverse")),
                _add(_c(1), _mul(_v("q"), _c(modulus))),
            ),
            _rel(_v("inverse"), _c(inverse)),
        ),
    )


_CRT_MODULI = {(5, 7), (5, 11), (7, 11), (7, 13), (11, 13), (11, 17), (13, 17)}


def _verify_crt(g: Mapping[str, int]) -> IndependentProblem:
    _require(g, {"m1", "m2", "r1", "r2"})
    m1, m2, r1, r2 = (g[name] for name in ("m1", "m2", "r1", "r2"))
    if (m1, m2) not in _CRT_MODULI or not (1 <= r1 < m1 and 0 <= r2 < m2):
        raise ValueError("CRT givens outside verifier domain")
    solution = next(x for x in range(m1 * m2) if x % m1 == r1 and x % m2 == r2)
    q1, q2 = (solution - r1) // m1, (solution - r2) // m2
    return _bundle(
        f"Find the least nonnegative x with x = {r1} (mod {m1}) and x = {r2} (mod {m2}).",
        f"x = {solution}",
        "undergraduate",
        {"q1": q1, "q2": q2, "x": solution},
        (
            _rel(_sub(_v("x"), _c(r1)), _mul(_v("q1"), _c(m1))),
            _rel(_sub(_v("x"), _c(r2)), _mul(_v("q2"), _c(m2))),
            _rel(_v("x"), _c(solution)),
        ),
    )


def _verify_determinant(g: Mapping[str, int]) -> IndependentProblem:
    names = {f"a{row}{column}" for row in range(1, 4) for column in range(1, 4)}
    _require(g, names)
    a, b, c = (g[name] for name in ("a11", "a12", "a13"))
    d, e, f = (g[name] for name in ("a21", "a22", "a23"))
    lower_g, h, i = (g[name] for name in ("a31", "a32", "a33"))
    if any(g[name] != 0 for name in ("a21", "a31", "a32")):
        raise ValueError("determinant matrix outside verifier domain")
    if any(not 1 <= g[name] <= 6 for name in ("a11", "a22", "a33")):
        raise ValueError("determinant diagonal outside verifier domain")
    if any(not -2 <= g[name] <= 2 for name in ("a12", "a13", "a23")):
        raise ValueError("determinant off-diagonal outside verifier domain")
    m1, m2, m3 = e * i - f * h, d * i - f * lower_g, d * h - e * lower_g
    determinant = a * m1 - b * m2 + c * m3
    matrix = "; ".join(
        ",".join(str(g[f"a{row}{column}"]) for column in range(1, 4))
        for row in range(1, 4)
    )
    return _bundle(
        f"Compute the determinant of the 3 by 3 matrix with rows [{matrix}].",
        f"det = {determinant}",
        "undergraduate",
        {"det": determinant, "m1": m1, "m2": m2, "m3": m3},
        (
            _rel(_v("m1"), _sub(_mul(_c(e), _c(i)), _mul(_c(f), _c(h)))),
            _rel(
                _v("m2"),
                _sub(_mul(_c(d), _c(i)), _mul(_c(f), _c(lower_g))),
            ),
            _rel(
                _v("m3"),
                _sub(_mul(_c(d), _c(h)), _mul(_c(e), _c(lower_g))),
            ),
            _rel(
                _v("det"),
                _add(
                    _sub(_mul(_c(a), _v("m1")), _mul(_c(b), _v("m2"))),
                    _mul(_c(c), _v("m3")),
                ),
            ),
            _rel(
                _sub(_v("det"), _mul(_mul(_c(a), _c(e)), _c(i))),
                _c(0),
            ),
        ),
    )


def _verify_derivative(g: Mapping[str, int]) -> IndependentProblem:
    _require(g, {"a1", "a2", "a3", "x"})
    a1, a2, a3, x = (g[name] for name in ("a1", "a2", "a3", "x"))
    if not all(1 <= value <= 6 for value in (a1, a2, a3, x)):
        raise ValueError("derivative givens outside verifier domain")
    value = 3 * a3 * x * x + 2 * a2 * x + a1
    return _bundle(
        f"For P(x) = {a3}x^3 + {a2}x^2 + {a1}x, compute P'({x}).",
        f"P'({x}) = {value}",
        "undergraduate",
        {"derivative": value},
        (
            _rel(
                _v("derivative"),
                _add(
                    _add(
                        _mul(_c(3 * a3), _mul(_c(x), _c(x))),
                        _mul(_c(2 * a2), _c(x)),
                    ),
                    _c(a1),
                ),
            ),
        ),
    )


def _verify_integral(g: Mapping[str, int]) -> IndependentProblem:
    _require(g, {"b", "c", "p"})
    bound, coefficient, exponent = g["b"], g["c"], g["p"]
    if not (1 <= bound <= 6 and 1 <= exponent <= 8 and coefficient % (exponent + 1) == 0):
        raise ValueError("integral givens outside verifier domain")
    if not 1 <= coefficient // (exponent + 1) <= 20:
        raise ValueError("integral coefficient outside verifier domain")
    value = coefficient * bound ** (exponent + 1) // (exponent + 1)
    return _bundle(
        f"Evaluate the definite integral of {coefficient}x^{exponent} from 0 to {bound}.",
        f"integral = {value}",
        "undergraduate",
        {"I": value},
        (
            _rel(
                _mul(_add(_c(exponent), _c(1)), _v("I")),
                _mul(_c(coefficient), _pow(_c(bound), _add(_c(exponent), _c(1)))),
            ),
            _rel(_v("I"), _c(value)),
        ),
    )


def _verify_triangle(g: Mapping[str, int]) -> IndependentProblem:
    _require(g, {"base", "height"})
    base, height = g["base"], g["height"]
    if base % 2 or not (2 <= base <= 50 and 1 <= height <= 30):
        raise ValueError("triangle givens outside verifier domain")
    area = base * height // 2
    return _bundle(
        f"Find the area of a triangle with base {base} and height {height}.",
        f"area = {area}",
        "elementary",
        {"A": area},
        (
            _rel(_add(_v("A"), _v("A")), _mul(_c(base), _c(height))),
            _rel(_v("A"), _c(area)),
        ),
    )


def _verify_hypergeometric(g: Mapping[str, int]) -> IndependentProblem:
    _require(g, {"bad", "draws", "good", "successes"})
    bad, draws, good, successes = (g[name] for name in ("bad", "draws", "good", "successes"))
    misses = draws - successes
    if not (4 <= good <= 18 and 4 <= bad <= 18 and 2 <= draws <= 6):
        raise ValueError("hypergeometric givens outside verifier domain")
    if not (1 <= successes < draws and successes <= good and misses <= bad):
        raise ValueError("hypergeometric event outside verifier domain")
    choose_good, choose_bad = comb(good, successes), comb(bad, misses)
    favorable = choose_good * choose_bad
    total = comb(good + bad, draws)
    common = gcd(favorable, total)
    numerator, denominator = favorable // common, total // common
    witness: dict[str, int] = {
        "F": favorable,
        "den": denominator,
        "num": numerator,
    }
    relations: list[RelationSpec] = []
    for prefix, population, selected in (
        ("cg", good, successes),
        ("cb", bad, misses),
        ("ct", good + bad, draws),
    ):
        witness[f"{prefix}0"] = 1
        relations.append(_rel(_v(f"{prefix}0"), _c(1)))
        for chosen in range(1, selected + 1):
            previous, current = f"{prefix}{chosen - 1}", f"{prefix}{chosen}"
            witness[current] = comb(population, chosen)
            relations.append(
                _rel(
                    _mul(_c(chosen), _v(current)),
                    _mul(
                        _add(_sub(_c(population), _c(chosen)), _c(1)),
                        _v(previous),
                    ),
                )
            )
    good_term = f"cg{successes}"
    bad_term = f"cb{misses}"
    total_term = f"ct{draws}"
    relations.extend(
        (
            _rel(_v("F"), _mul(_v(good_term), _v(bad_term))),
            _rel(_mul(_v("num"), _v(total_term)), _mul(_v("F"), _v("den"))),
        )
    )
    return _bundle(
        f"A population has {good} marked and {bad} unmarked objects. "
        f"If {draws} are drawn without replacement, find the probability of exactly "
        f"{successes} marked objects.",
        f"p = {numerator}/{denominator}",
        "undergraduate",
        witness,
        tuple(relations),
    )


def _verify_recurrence(g: Mapping[str, int]) -> IndependentProblem:
    _require(g, {"n", "p", "q", "u0"})
    n, multiplier, increment, initial = (g[name] for name in ("n", "p", "q", "u0"))
    if not (2 <= n <= 8 and 2 <= multiplier <= 4 and 1 <= increment <= 10):
        raise ValueError("recurrence givens outside verifier domain")
    if not 1 <= initial <= 10:
        raise ValueError("recurrence initial value outside verifier domain")
    witness: dict[str, int] = {}
    relations: list[RelationSpec] = []
    previous_value = initial
    previous_expr = _c(initial)
    for step in range(1, n + 1):
        value = multiplier * previous_value + increment
        name = f"u{step}"
        witness[name] = value
        relations.append(
            _rel(_v(name), _add(_mul(_c(multiplier), previous_expr), _c(increment)))
        )
        previous_value, previous_expr = value, _v(name)
    return _bundle(
        f"Let u_0 = {initial} and u_(k+1) = {multiplier}u_k + {increment}. Find u_{n}.",
        f"u_{n} = {previous_value}",
        "undergraduate",
        witness,
        tuple(relations),
    )


_BASE_DIGITS = "0123456789ABCDEF"


def _base_representation(number: int, base: int) -> tuple[str, tuple[int, ...]]:
    digits: list[int] = []
    remaining = number
    while remaining:
        remaining, digit = divmod(remaining, base)
        digits.append(digit)
    return "".join(_BASE_DIGITS[digit] for digit in reversed(digits)), tuple(digits)


def _verify_base_conversion(g: Mapping[str, int]) -> IndependentProblem:
    _require(g, {"base", "number"})
    base, number = g["base"], g["number"]
    if not (2 <= base <= 16 and 100 <= number <= 999):
        raise ValueError("base-conversion givens outside verifier domain")
    representation, digits = _base_representation(number, base)
    witness: dict[str, int] = {f"d{i}": digit for i, digit in enumerate(digits)}
    relations: list[RelationSpec] = []
    quotient = number
    for index, digit in enumerate(digits):
        next_quotient = quotient // base
        witness[f"q{index}"] = quotient
        witness[f"q{index + 1}"] = next_quotient
        relations.append(
            _rel(
                _v(f"q{index}"),
                _add(_mul(_c(base), _v(f"q{index + 1}")), _v(f"d{index}")),
            )
        )
        quotient = next_quotient
    relations.append(_rel(_v("q0"), _c(number)))
    return _bundle(
        f"Convert decimal {number} to base {base}.",
        f"digits = {representation}",
        "elementary",
        witness,
        tuple(relations),
    )


_VERIFIERS = {
    "arithmetic_progression": _verify_progression,
    "base_conversion": _verify_base_conversion,
    "crt_two_moduli": _verify_crt,
    "determinant_3x3": _verify_determinant,
    "gcd_euclidean": _verify_gcd,
    "geometric_progression": _verify_geometric,
    "horner_polynomial": _verify_horner,
    "hypergeometric_probability": _verify_hypergeometric,
    "linear_equation": _verify_linear,
    "modular_inverse": _verify_inverse,
    "monomial_integral": _verify_integral,
    "polynomial_derivative": _verify_derivative,
    "pythagorean_hypotenuse": _verify_pythagorean,
    "quadratic_integer_roots": _verify_quadratic,
    "recurrence": _verify_recurrence,
    "subset_count": _verify_subsets,
    "triangle_area": _verify_triangle,
    "two_by_two_system": _verify_system,
}


def verify_family(family: str, givens: Mapping[str, int]) -> IndependentProblem:
    """Recompute one complete problem and certificate through the independent bundle."""

    verifier = _VERIFIERS.get(family)
    if verifier is None:
        raise ValueError(f"unsupported independent benchmark family: {family}")
    return verifier(givens)


__all__ = ["ExprSpec", "IndependentProblem", "RelationSpec", "verify_family"]
