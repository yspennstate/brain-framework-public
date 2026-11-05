"""Exact three-source claim bank for the leakage-resistant v3 generator.

Every claim genuinely uses three pairwise-distinct, nonzero mathematical
sources.  A fault swaps coefficient roles instead of introducing a source that
is absent from clean lines.  Schedule material is evaluator-held and committed;
public group identifiers alone cannot reconstruct weights or term order.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from functools import lru_cache
from itertools import combinations, permutations
from math import gcd
from typing import Any

from .models import canonical_json

SOURCE_ROLE_SCHEMA_V3 = "org.math-research-brain/source-role-claim-bank"
SOURCE_ROLE_SCHEMA_VERSION_V3 = 1
SOURCE_ROLE_CLAIM_COUNT_V3 = 8
MAXIMUM_COEFFICIENT_ORBIT_PLANE_SUPPORT_V3 = 6

_DOMAIN = b"org.math-research-brain/source-role-claim-bank/v3\0"
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_IDENTIFIER_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_WEIGHT_POOL = (-11, -9, -7, -5, -3, -2, -1, 1, 2, 3, 5, 7, 9, 11)
_TERM_ORDER_PERMUTATIONS = tuple(permutations(range(3)))


class SourceRoleFaultV3(StrEnum):
    SWAP_CANONICAL_FIRST = "swap_canonical_first"
    SWAP_CANONICAL_SECOND = "swap_canonical_second"


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _validate_digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical SHA-256 digest")
    return value


def _identifier(value: object, label: str) -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a compact identifier")
    return value


def _exact_fraction(value: object, label: str) -> Fraction:
    if type(value) is not Fraction:
        raise ValueError(f"{label} must be an exact Fraction")
    return value


def _fraction_dict(value: Fraction) -> dict[str, int]:
    return {"denominator": value.denominator, "numerator": value.numerator}


def _validate_seed(schedule_seed: object) -> bytes:
    if type(schedule_seed) is not bytes or not 32 <= len(schedule_seed) <= 64:
        raise ValueError("source-role schedule seed must be exact bytes from 32 to 64 bytes")
    return schedule_seed


def _schedule_commitment(schedule_seed: bytes, input_digest: str) -> str:
    return _digest_bytes(
        _DOMAIN
        + b"schedule-seed\0"
        + input_digest.encode("ascii")
        + b"\0"
        + schedule_seed
    )


def _term_schedule_material(
    schedule_seed: bytes,
    *,
    group_binding_digest: str,
    purpose: str,
    payload: bytes,
) -> bytes:
    message = (
        _DOMAIN
        + b"term-order-schedule\0"
        + purpose.encode("ascii")
        + b"\0"
        + group_binding_digest.encode("ascii")
        + b"\0"
        + payload
    )
    return hmac.new(schedule_seed, message, hashlib.sha256).digest()


def _balanced_term_order_schedule(
    schedule_seed: bytes,
    *,
    group_binding_digest: str,
) -> tuple[tuple[int, int, int], ...]:
    """Return all six orders plus a keyed deranged pair, then key-shuffle rows."""

    first_extra = min(
        _TERM_ORDER_PERMUTATIONS,
        key=lambda order: _term_schedule_material(
            schedule_seed,
            group_binding_digest=group_binding_digest,
            purpose="extra-first",
            payload=bytes(order),
        ),
    )
    deranged_partners = tuple(
        order
        for order in _TERM_ORDER_PERMUTATIONS
        if all(left != right for left, right in zip(first_extra, order, strict=True))
    )
    second_extra = min(
        deranged_partners,
        key=lambda order: _term_schedule_material(
            schedule_seed,
            group_binding_digest=group_binding_digest,
            purpose="extra-second",
            payload=bytes(first_extra + order),
        ),
    )
    unshuffled = (*_TERM_ORDER_PERMUTATIONS, first_extra, second_extra)
    ranked = sorted(
        enumerate(unshuffled),
        key=lambda item: _term_schedule_material(
            schedule_seed,
            group_binding_digest=group_binding_digest,
            purpose="row-shuffle",
            payload=item[0].to_bytes(2, "big") + bytes(item[1]),
        ),
    )
    return tuple(order for _, order in ranked)


def _rank_material(
    schedule_seed: bytes,
    *,
    group_binding_digest: str,
    bank_nonce: int,
    claim_index: int,
    candidate_nonce: int,
    purpose: str,
) -> bytes:
    payload = (
        _DOMAIN
        + purpose.encode("ascii")
        + b"\0"
        + group_binding_digest.encode("ascii")
        + b"\0"
        + bank_nonce.to_bytes(4, "big")
        + claim_index.to_bytes(2, "big")
        + candidate_nonce.to_bytes(2, "big")
    )
    return hmac.new(schedule_seed, payload, hashlib.sha256).digest()


@dataclass(frozen=True, slots=True)
class SourceRoleBankV3:
    source_names: tuple[str, str, str]
    source_values: tuple[Fraction, Fraction, Fraction]

    def __post_init__(self) -> None:
        self.validate_current()

    def validate_current(self) -> None:
        if (
            type(self.source_names) is not tuple
            or len(self.source_names) != 3
            or not all(type(name) is str for name in self.source_names)
        ):
            raise ValueError("source-role bank requires three exact source names")
        for name in self.source_names:
            _identifier(name, "source name")
        if len(set(self.source_names)) != 3:
            raise ValueError("source-role names must be pairwise distinct")
        if (
            type(self.source_values) is not tuple
            or len(self.source_values) != 3
            or not all(type(value) is Fraction for value in self.source_values)
        ):
            raise ValueError("source-role bank requires three exact source values")
        if len(set(self.source_values)) != 3 or any(value == 0 for value in self.source_values):
            raise ValueError("source-role values must be nonzero and pairwise distinct")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return {
            "source_names": list(self.source_names),
            "source_values": [_fraction_dict(value) for value in self.source_values],
        }


@dataclass(frozen=True, slots=True)
class SourceRoleClaimV3:
    target: str
    target_value: Fraction
    weights: tuple[int, int, int]
    offset: Fraction
    term_order: tuple[int, int, int]

    def __post_init__(self) -> None:
        self.validate_current()

    def validate_current(self) -> None:
        _identifier(self.target, "source-role target")
        _exact_fraction(self.target_value, "source-role target value")
        if (
            type(self.weights) is not tuple
            or len(self.weights) != 3
            or not all(type(weight) is int for weight in self.weights)
            or any(weight == 0 for weight in self.weights)
            or len(set(self.weights)) != 3
        ):
            raise ValueError("source-role weights must be three distinct nonzero exact integers")
        _exact_fraction(self.offset, "source-role offset")
        if (
            type(self.term_order) is not tuple
            or len(self.term_order) != 3
            or set(self.term_order) != {0, 1, 2}
            or not all(type(index) is int for index in self.term_order)
        ):
            raise ValueError("source-role term order must be a permutation of 0,1,2")

    def coefficients(
        self, fault: SourceRoleFaultV3 | None = None
    ) -> tuple[int, int, int]:
        self.validate_current()
        if fault is None:
            return self.weights
        if type(fault) is not SourceRoleFaultV3:
            raise ValueError("source-role fault must be exact or None")
        result = list(self.weights)
        alternative = 1 if fault is SourceRoleFaultV3.SWAP_CANONICAL_FIRST else 2
        result[0], result[alternative] = result[alternative], result[0]
        return tuple(result)

    def term_position_coefficients(
        self, fault: SourceRoleFaultV3 | None = None
    ) -> tuple[int, int, int]:
        """Return coefficients in the exact order exposed by the rendered terms."""

        coefficients = self.coefficients(fault)
        return tuple(coefficients[index] for index in self.term_order)

    def value(
        self,
        source_bank: SourceRoleBankV3,
        fault: SourceRoleFaultV3 | None = None,
    ) -> Fraction:
        if type(source_bank) is not SourceRoleBankV3:
            raise ValueError("claim evaluation requires an exact source-role bank")
        source_bank.validate_current()
        return self.offset + sum(
            (
                Fraction(coefficient) * source
                for coefficient, source in zip(
                    self.coefficients(fault), source_bank.source_values, strict=True
                )
            ),
            Fraction(0),
        )

    def ordered_terms(
        self,
        source_bank: SourceRoleBankV3,
        fault: SourceRoleFaultV3 | None = None,
    ) -> tuple[tuple[str, int], tuple[str, int], tuple[str, int]]:
        if type(source_bank) is not SourceRoleBankV3:
            raise ValueError("ordered terms require an exact source-role bank")
        source_bank.validate_current()
        coefficients = self.coefficients(fault)
        return tuple(
            (source_bank.source_names[index], coefficients[index]) for index in self.term_order
        )

    @property
    def structural_signature(self) -> tuple[str, tuple[int, int, int]]:
        """Shape deliberately omits coefficient values, which change under a fault."""

        self.validate_current()
        return ("add-three-multiplied-sources-plus-offset", self.term_order)

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return {
            "offset": _fraction_dict(self.offset),
            "target": self.target,
            "target_value": _fraction_dict(self.target_value),
            "term_order": list(self.term_order),
            "weights": list(self.weights),
        }


def source_role_input_digest_v3(
    *,
    source_bank: SourceRoleBankV3,
    group_binding_digest: str,
    targets: tuple[str, ...],
    offsets: tuple[Fraction, ...],
) -> str:
    """Bind the preregistered semantic inputs independently of generated schedule fields."""

    if type(source_bank) is not SourceRoleBankV3:
        raise ValueError("source-role input requires an exact source bank")
    source_bank.validate_current()
    group_binding_digest = _validate_digest(group_binding_digest, "source-role group binding")
    if (
        type(targets) is not tuple
        or len(targets) != SOURCE_ROLE_CLAIM_COUNT_V3
        or not all(type(target) is str for target in targets)
    ):
        raise ValueError("source-role input requires exactly eight targets")
    for target in targets:
        _identifier(target, "source-role target")
    if len(set(targets)) != len(targets):
        raise ValueError("source-role input targets must be unique")
    if (
        type(offsets) is not tuple
        or len(offsets) != SOURCE_ROLE_CLAIM_COUNT_V3
        or not all(type(offset) is Fraction for offset in offsets)
    ):
        raise ValueError("source-role input requires exactly eight exact offsets")
    return _digest(
        {
            "claims": [
                {"offset": _fraction_dict(offset), "target": target}
                for target, offset in zip(targets, offsets, strict=True)
            ],
            "group_binding_digest": group_binding_digest,
            "schema": f"{SOURCE_ROLE_SCHEMA_V3}/input",
            "schema_version": SOURCE_ROLE_SCHEMA_VERSION_V3,
            "source_bank": source_bank.to_dict(),
        }
    )


def _cross(left: tuple[int, int, int], right: tuple[int, int, int]) -> tuple[int, int, int]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _subtract(
    left: tuple[int, int, int], right: tuple[int, int, int]
) -> tuple[int, int, int]:
    return tuple(left[index] - right[index] for index in range(3))


def _dot(left: tuple[int, int, int], right: tuple[int, int, int]) -> int:
    return sum(a * b for a, b in zip(left, right, strict=True))


def _validate_claim_tuple(
    claims: object,
    *,
    minimum: int,
    label: str,
) -> tuple[SourceRoleClaimV3, ...]:
    if type(claims) is not tuple or len(claims) < minimum:
        raise ValueError(f"{label} requires at least {minimum} exact claims")
    if not all(type(claim) is SourceRoleClaimV3 for claim in claims):
        raise ValueError(f"{label} received an invalid claim")
    for claim in claims:
        claim.validate_current()
    return claims


def _maximum_affine_plane_support(points: tuple[tuple[int, int, int], ...]) -> int:
    maximum = 0
    found_plane = False
    for first, second, third in combinations(points, 3):
        normal = _cross(_subtract(second, first), _subtract(third, first))
        if normal == (0, 0, 0):
            continue
        found_plane = True
        support = sum(_dot(normal, _subtract(point, first)) == 0 for point in points)
        maximum = max(maximum, support)
    return maximum if found_plane else len(points)


def _plane_outlier_candidates(
    points: tuple[tuple[int, int, int], ...],
) -> tuple[int, ...]:
    candidates: list[int] = []
    for index in range(len(points)):
        remaining = points[:index] + points[index + 1 :]
        if _maximum_affine_plane_support(remaining) == len(remaining):
            candidates.append(index)
    return tuple(candidates)


def maximum_affine_plane_support_v3(claims: tuple[SourceRoleClaimV3, ...]) -> int:
    """Return the largest plane support in canonical source-role coordinates."""

    validated = _validate_claim_tuple(claims, minimum=3, label="affine support")
    return _maximum_affine_plane_support(tuple(claim.weights for claim in validated))


def maximum_term_position_affine_plane_support_v3(
    claims: tuple[SourceRoleClaimV3, ...],
) -> int:
    """Return plane support in the coefficient coordinates exposed to a solver."""

    validated = _validate_claim_tuple(
        claims, minimum=3, label="term-position affine support"
    )
    return _maximum_affine_plane_support(
        tuple(claim.term_position_coefficients() for claim in validated)
    )


def coefficient_plane_outlier_candidates_v3(
    claims: tuple[SourceRoleClaimV3, ...],
) -> tuple[int, ...]:
    """Indices whose removal makes all remaining coefficient points coplanar."""

    validated = _validate_claim_tuple(
        claims, minimum=5, label="coefficient outlier audit"
    )
    return _plane_outlier_candidates(tuple(claim.weights for claim in validated))


def term_position_plane_outlier_candidates_v3(
    claims: tuple[SourceRoleClaimV3, ...],
) -> tuple[int, ...]:
    """Return visible-position indices that alone sit outside a common plane."""

    validated = _validate_claim_tuple(
        claims, minimum=5, label="term-position outlier audit"
    )
    return _plane_outlier_candidates(
        tuple(claim.term_position_coefficients() for claim in validated)
    )


def _normalized_plane(
    first: tuple[int, int, int],
    second: tuple[int, int, int],
    third: tuple[int, int, int],
) -> tuple[int, int, int, int] | None:
    normal = _cross(_subtract(second, first), _subtract(third, first))
    if normal == (0, 0, 0):
        return None
    coefficients = (*normal, _dot(normal, first))
    divisor = 0
    for coefficient in coefficients:
        divisor = gcd(divisor, abs(coefficient))
    normalized = tuple(coefficient // divisor for coefficient in coefficients)
    if next(coefficient for coefficient in normalized[:3] if coefficient != 0) < 0:
        normalized = tuple(-coefficient for coefficient in normalized)
    return normalized


@lru_cache(maxsize=4096)
def _maximum_coefficient_orbit_plane_support_from_key(
    orbit_key: tuple[tuple[int, int, int], ...],
) -> int:
    orbits = tuple(tuple(permutations(weights)) for weights in orbit_key)
    seen_planes: set[tuple[int, int, int, int]] = set()
    maximum = 0
    for first_index, second_index, third_index in combinations(range(len(orbits)), 3):
        for first in orbits[first_index]:
            for second in orbits[second_index]:
                for third in orbits[third_index]:
                    plane = _normalized_plane(first, second, third)
                    if plane is None or plane in seen_planes:
                        continue
                    seen_planes.add(plane)
                    normal = plane[:3]
                    offset = plane[3]
                    support = sum(
                        any(_dot(normal, point) == offset for point in orbit)
                        for orbit in orbits
                    )
                    maximum = max(maximum, support)
                    if maximum == len(orbits):
                        return maximum
    return maximum


def maximum_coefficient_orbit_plane_support_v3(
    claims: tuple[SourceRoleClaimV3, ...],
) -> int:
    """Maximum rows that can be coplanar under independent S3 role permutations.

    This predicate depends only on each row's unordered coefficient orbit.  It is
    therefore unchanged by either fault swap, a proposed repair, or term rendering,
    unlike a clean ordered-coordinate gate that would reveal answer information.
    """

    validated = _validate_claim_tuple(
        claims, minimum=3, label="coefficient-orbit affine support"
    )
    orbit_key = tuple(sorted(tuple(sorted(claim.weights)) for claim in validated))
    return _maximum_coefficient_orbit_plane_support_from_key(orbit_key)


def _coefficient_value(
    weights: tuple[int, int, int],
    source_values: tuple[Fraction, Fraction, Fraction],
) -> Fraction:
    return sum(
        (
            Fraction(weight) * source
            for weight, source in zip(weights, source_values, strict=True)
        ),
        Fraction(0),
    )


def _coefficient_orbit_separates_mechanisms(
    source_bank: SourceRoleBankV3,
    weights: tuple[int, int, int],
) -> bool:
    """Require separation for every orientation a repair hypothesis can induce."""

    for oriented in permutations(weights):
        canonical, first, second = oriented
        role_assignments = (
            oriented,
            (first, canonical, second),
            (second, first, canonical),
        )
        if len(
            {
                _coefficient_value(assignment, source_bank.source_values)
                for assignment in role_assignments
            }
        ) != 3:
            return False
    return True


class _CandidateRejected(ValueError):
    pass


def _candidate_claim(
    *,
    source_bank: SourceRoleBankV3,
    target: str,
    offset: Fraction,
    term_order: tuple[int, int, int],
    schedule_seed: bytes,
    group_binding_digest: str,
    bank_nonce: int,
    claim_index: int,
    candidate_nonce: int,
) -> SourceRoleClaimV3:
    weight_material = _rank_material(
        schedule_seed,
        group_binding_digest=group_binding_digest,
        bank_nonce=bank_nonce,
        claim_index=claim_index,
        candidate_nonce=candidate_nonce,
        purpose="weights",
    )
    ranked_weights = sorted(
        _WEIGHT_POOL,
        key=lambda weight: hashlib.sha256(
            weight_material + weight.to_bytes(2, "big", signed=True)
        ).digest(),
    )
    weights = tuple(ranked_weights[:3])
    if not _coefficient_orbit_separates_mechanisms(source_bank, weights):
        raise _CandidateRejected("candidate coefficient orbit does not separate both mechanisms")
    target_value = offset + _coefficient_value(weights, source_bank.source_values)
    claim = SourceRoleClaimV3(
        target=target,
        target_value=target_value,
        weights=weights,
        offset=offset,
        term_order=term_order,
    )
    if claim.value(source_bank) != target_value:
        raise AssertionError("derived source-role offset failed exact target construction")
    return claim


def _claims_for_nonce(
    *,
    source_bank: SourceRoleBankV3,
    targets: tuple[str, ...],
    offsets: tuple[Fraction, ...],
    term_orders: tuple[tuple[int, int, int], ...],
    schedule_seed: bytes,
    group_binding_digest: str,
    bank_nonce: int,
) -> tuple[SourceRoleClaimV3, ...]:
    claims: list[SourceRoleClaimV3] = []
    rows = zip(targets, offsets, term_orders, strict=True)
    for claim_index, (target, offset, term_order) in enumerate(rows):
        for candidate_nonce in range(256):
            try:
                claim = _candidate_claim(
                    source_bank=source_bank,
                    target=target,
                    offset=offset,
                    term_order=term_order,
                    schedule_seed=schedule_seed,
                    group_binding_digest=group_binding_digest,
                    bank_nonce=bank_nonce,
                    claim_index=claim_index,
                    candidate_nonce=candidate_nonce,
                )
            except _CandidateRejected:
                continue
            claims.append(claim)
            break
        else:
            raise ValueError("unable to derive a mechanism-separating coefficient triple")
    return tuple(claims)


def _payload_digest(
    *,
    source_bank: SourceRoleBankV3,
    group_binding_digest: str,
    input_digest: str,
    schedule_seed_commitment: str,
    bank_nonce: int,
    claims: tuple[SourceRoleClaimV3, ...],
) -> str:
    return _digest(
        {
            "bank_nonce": bank_nonce,
            "claims": [claim.to_dict() for claim in claims],
            "group_binding_digest": group_binding_digest,
            "input_digest": input_digest,
            "schedule_seed_commitment": schedule_seed_commitment,
            "source_bank": source_bank.to_dict(),
        }
    )


def _validate_balanced_term_order_design(claims: tuple[SourceRoleClaimV3, ...]) -> None:
    orders = tuple(claim.term_order for claim in claims)
    if set(orders) != set(_TERM_ORDER_PERMUTATIONS):
        raise ValueError("source-role term schedule must include every three-source order")
    counts = tuple(
        sum(order[position] == source for order in orders)
        for source in range(3)
        for position in range(3)
    )
    if any(count not in {2, 3} for count in counts):
        raise ValueError("source-role term schedule is not a balanced Latin-square extension")


@dataclass(frozen=True, slots=True)
class SourceRoleClaimBankV3:
    source_bank: SourceRoleBankV3
    group_binding_digest: str
    input_digest: str
    schedule_seed_commitment: str
    bank_nonce: int
    claims: tuple[SourceRoleClaimV3, ...]
    payload_digest: str

    def __post_init__(self) -> None:
        self.validate_current()

    def validate_current(self) -> None:
        if type(self.source_bank) is not SourceRoleBankV3:
            raise ValueError("claim bank requires an exact source-role bank")
        self.source_bank.validate_current()
        _validate_digest(self.group_binding_digest, "source-role group binding")
        _validate_digest(self.input_digest, "source-role input digest")
        _validate_digest(self.schedule_seed_commitment, "source-role schedule commitment")
        if type(self.bank_nonce) is not int or not 0 <= self.bank_nonce < 4096:
            raise ValueError("source-role bank nonce is outside its bounded search")
        if (
            type(self.claims) is not tuple
            or len(self.claims) != SOURCE_ROLE_CLAIM_COUNT_V3
            or not all(type(claim) is SourceRoleClaimV3 for claim in self.claims)
        ):
            raise ValueError("source-role claim bank requires exactly eight exact claims")
        for claim in self.claims:
            claim.validate_current()
        if len({claim.target for claim in self.claims}) != len(self.claims):
            raise ValueError("source-role claim targets must be unique")
        if any(weight not in _WEIGHT_POOL for claim in self.claims for weight in claim.weights):
            raise ValueError("source-role bank coefficients must use the approved bounded pool")
        expected_input_digest = source_role_input_digest_v3(
            source_bank=self.source_bank,
            group_binding_digest=self.group_binding_digest,
            targets=tuple(claim.target for claim in self.claims),
            offsets=tuple(claim.offset for claim in self.claims),
        )
        if not hmac.compare_digest(expected_input_digest, self.input_digest):
            raise ValueError("source-role input digest does not bind its semantic inputs")
        coefficient_orbits = {tuple(sorted(claim.weights)) for claim in self.claims}
        if len(coefficient_orbits) != len(self.claims):
            raise ValueError("source-role coefficient orbits must be unique")
        _validate_balanced_term_order_design(self.claims)
        for claim in self.claims:
            if claim.value(self.source_bank) != claim.target_value:
                raise ValueError("source-role offset does not derive its exact target value")
            if not _coefficient_orbit_separates_mechanisms(self.source_bank, claim.weights):
                raise ValueError("source-role coefficient orbit does not separate both mechanisms")
        if maximum_coefficient_orbit_plane_support_v3(self.claims) > (
            MAXIMUM_COEFFICIENT_ORBIT_PLANE_SUPPORT_V3
        ):
            raise ValueError("source-role coefficient orbits admit a seven-row plane shortcut")
        _validate_digest(self.payload_digest, "source-role payload digest")
        expected = _payload_digest(
            source_bank=self.source_bank,
            group_binding_digest=self.group_binding_digest,
            input_digest=self.input_digest,
            schedule_seed_commitment=self.schedule_seed_commitment,
            bank_nonce=self.bank_nonce,
            claims=self.claims,
        )
        if not hmac.compare_digest(expected, self.payload_digest):
            raise ValueError("source-role payload digest does not bind its claim bank")

    def verify_schedule_seed(
        self,
        schedule_seed: bytes,
        *,
        expected_input_digest: str,
    ) -> None:
        self.validate_current()
        expected_input = _validate_digest(
            expected_input_digest, "expected source-role input digest"
        )
        if not hmac.compare_digest(expected_input, self.input_digest):
            raise ValueError("source-role bank does not match its preregistered input digest")
        seed = _validate_seed(schedule_seed)
        if not hmac.compare_digest(
            _schedule_commitment(seed, self.input_digest), self.schedule_seed_commitment
        ):
            raise ValueError("source-role schedule seed does not match its commitment")
        rebuilt = build_source_role_claim_bank_v3(
            source_bank=self.source_bank,
            group_binding_digest=self.group_binding_digest,
            targets=tuple(claim.target for claim in self.claims),
            offsets=tuple(claim.offset for claim in self.claims),
            schedule_seed=seed,
        )
        if rebuilt != self:
            raise ValueError("source-role schedule seed does not generate the committed bank")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return {
            "bank_nonce": self.bank_nonce,
            "claims": [claim.to_dict() for claim in self.claims],
            "group_binding_digest": self.group_binding_digest,
            "input_digest": self.input_digest,
            "payload_digest": self.payload_digest,
            "schedule_seed_commitment": self.schedule_seed_commitment,
            "schema": SOURCE_ROLE_SCHEMA_V3,
            "schema_version": SOURCE_ROLE_SCHEMA_VERSION_V3,
            "source_bank": self.source_bank.to_dict(),
        }


def build_source_role_claim_bank_v3(
    *,
    source_bank: SourceRoleBankV3,
    group_binding_digest: str,
    targets: tuple[str, ...],
    offsets: tuple[Fraction, ...],
    schedule_seed: bytes,
) -> SourceRoleClaimBankV3:
    """Build eight genuine mixed-source claims from private committed schedule material."""

    group_binding_digest = _validate_digest(group_binding_digest, "source-role group binding")
    seed = _validate_seed(schedule_seed)
    input_digest = source_role_input_digest_v3(
        source_bank=source_bank,
        group_binding_digest=group_binding_digest,
        targets=targets,
        offsets=offsets,
    )
    term_orders = _balanced_term_order_schedule(
        seed, group_binding_digest=group_binding_digest
    )

    for bank_nonce in range(4096):
        claims = _claims_for_nonce(
            source_bank=source_bank,
            targets=targets,
            offsets=offsets,
            term_orders=term_orders,
            schedule_seed=seed,
            group_binding_digest=group_binding_digest,
            bank_nonce=bank_nonce,
        )
        if len({tuple(sorted(claim.weights)) for claim in claims}) != len(claims):
            continue
        if maximum_coefficient_orbit_plane_support_v3(claims) > (
            MAXIMUM_COEFFICIENT_ORBIT_PLANE_SUPPORT_V3
        ):
            continue
        commitment = _schedule_commitment(seed, input_digest)
        return SourceRoleClaimBankV3(
            source_bank=source_bank,
            group_binding_digest=group_binding_digest,
            input_digest=input_digest,
            schedule_seed_commitment=commitment,
            bank_nonce=bank_nonce,
            claims=claims,
            payload_digest=_payload_digest(
                source_bank=source_bank,
                group_binding_digest=group_binding_digest,
                input_digest=input_digest,
                schedule_seed_commitment=commitment,
                bank_nonce=bank_nonce,
                claims=claims,
            ),
        )
    raise ValueError("unable to derive a source-role bank with unique coefficient orbits")


__all__ = [
    "MAXIMUM_COEFFICIENT_ORBIT_PLANE_SUPPORT_V3",
    "SOURCE_ROLE_CLAIM_COUNT_V3",
    "SOURCE_ROLE_SCHEMA_V3",
    "SOURCE_ROLE_SCHEMA_VERSION_V3",
    "SourceRoleBankV3",
    "SourceRoleClaimBankV3",
    "SourceRoleClaimV3",
    "SourceRoleFaultV3",
    "build_source_role_claim_bank_v3",
    "coefficient_plane_outlier_candidates_v3",
    "maximum_affine_plane_support_v3",
    "maximum_coefficient_orbit_plane_support_v3",
    "maximum_term_position_affine_plane_support_v3",
    "source_role_input_digest_v3",
    "term_position_plane_outlier_candidates_v3",
]
