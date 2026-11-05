"""Strict canonical-JSON Ed25519 framing for new study-v3 evidence leaves.

This module intentionally does not replace the legacy three-arm receipt wire format.  A
signature proves only that one private-key holder signed the exact validated bytes under one
fixed domain.  It does not prove runtime isolation, chronology, reviewer identity,
independence, or the truth of any signed field; those facts require external evidence and
out-of-band commitments.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .models import canonical_json

CANONICAL_ED25519_MAX_RECORD_BYTES_V3 = 16 * 1024 * 1024
CANONICAL_ED25519_MAX_DEPTH_V3 = 32
CANONICAL_ED25519_MAX_NODES_V3 = 200_000

_DOMAIN_RE = re.compile(r"[a-z0-9][a-z0-9./_-]{0,127}\Z")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SIGNATURE_RE = re.compile(r"ed25519:[0-9a-f]{128}\Z")
_MESSAGE_PREFIX = b"org.math-research-brain/canonical-ed25519/v3/message\x00"
_AUTHORITY_PREFIX = b"org.math-research-brain/canonical-ed25519/v3/authority-key\x00"
_FINGERPRINT_PREFIX = b"org.math-research-brain/canonical-ed25519/v3/public-key\x00"
_ED25519_FIELD = 2**255 - 19
_ED25519_ORDER = 2**252 + 27742317777372353535851937790883648493
_ED25519_D = (-121665 * pow(121666, -1, _ED25519_FIELD)) % _ED25519_FIELD
_ED25519_SQRT_MINUS_ONE = pow(2, (_ED25519_FIELD - 1) // 4, _ED25519_FIELD)
_ED25519_IDENTITY = (0, 1, 1, 0)


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _domain_bytes(value: object) -> bytes:
    if type(value) is not str or _DOMAIN_RE.fullmatch(value) is None:
        raise ValueError("canonical Ed25519 domain must be bounded lowercase ASCII")
    return value.encode("ascii")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_float(value: str) -> None:
    raise ValueError(f"floating-point JSON number is forbidden: {value}")


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _validate_json_tree(value: object) -> None:
    if type(value) is not dict:
        raise ValueError("canonical Ed25519 record root must be an exact object")
    stack: list[tuple[object, int]] = [(value, 0)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > CANONICAL_ED25519_MAX_NODES_V3:
            raise ValueError("canonical Ed25519 record exceeds its node budget")
        if depth > CANONICAL_ED25519_MAX_DEPTH_V3:
            raise ValueError("canonical Ed25519 record exceeds its nesting budget")
        if current is None or type(current) in {bool, int}:
            continue
        if type(current) is str:
            try:
                current.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("canonical Ed25519 record contains invalid Unicode") from exc
            continue
        if type(current) is list:
            stack.extend((item, depth + 1) for item in reversed(current))
            continue
        if type(current) is dict:
            for key, item in reversed(tuple(current.items())):
                if type(key) is not str:
                    raise ValueError("canonical Ed25519 record key must be exact text")
                try:
                    key.encode("utf-8")
                except UnicodeEncodeError as exc:
                    raise ValueError(
                        "canonical Ed25519 record key contains invalid Unicode"
                    ) from exc
                stack.append((item, depth + 1))
            continue
        raise ValueError("canonical Ed25519 record contains a forbidden JSON value")


def _canonical_record(value: object) -> bytes:
    if type(value) is not bytes:
        raise ValueError("canonical Ed25519 record must be immutable bytes")
    if not 2 <= len(value) <= CANONICAL_ED25519_MAX_RECORD_BYTES_V3:
        raise ValueError("canonical Ed25519 record exceeds its byte budget")
    try:
        decoded = value.decode("utf-8", errors="strict")
        parsed = json.loads(
            decoded,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
        _validate_json_tree(parsed)
        rebuilt = canonical_json(parsed).encode("utf-8")
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ValueError("canonical Ed25519 record is invalid") from exc
    if rebuilt != value:
        raise ValueError("canonical Ed25519 record is not canonically encoded")
    return value


def _framed_message(domain: bytes, record: bytes) -> bytes:
    validated = _canonical_record(record)
    return (
        _MESSAGE_PREFIX
        + len(domain).to_bytes(2, "big")
        + domain
        + len(validated).to_bytes(8, "big")
        + validated
    )


def _authority_commitment(domain: bytes, public_key: bytes) -> str:
    return _sha256(
        _AUTHORITY_PREFIX
        + len(domain).to_bytes(2, "big")
        + domain
        + public_key
    )


def _key_fingerprint(public_key: bytes) -> str:
    return _sha256(_FINGERPRINT_PREFIX + public_key)


def _point_add(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    x1, y1, z1, t1 = left
    x2, y2, z2, t2 = right
    field = _ED25519_FIELD
    a = ((y1 - x1) * (y2 - x2)) % field
    b = ((y1 + x1) * (y2 + x2)) % field
    c = (2 * _ED25519_D * t1 * t2) % field
    d = (2 * z1 * z2) % field
    e = (b - a) % field
    f = (d - c) % field
    g = (d + c) % field
    h = (b + a) % field
    return (e * f % field, g * h % field, f * g % field, e * h % field)


def _point_double(
    point: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    x, y, z, _t = point
    field = _ED25519_FIELD
    a = x * x % field
    b = y * y % field
    c = 2 * z * z % field
    d = -a % field
    e = ((x + y) * (x + y) - a - b) % field
    g = (d + b) % field
    f = (g - c) % field
    h = (d - b) % field
    return (e * f % field, g * h % field, f * g % field, e * h % field)


def _scalar_multiply(
    scalar: int,
    point: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    result = _ED25519_IDENTITY
    addend = point
    remaining = scalar
    while remaining:
        if remaining & 1:
            result = _point_add(result, addend)
        addend = _point_double(addend)
        remaining >>= 1
    return result


def _is_identity(point: tuple[int, int, int, int]) -> bool:
    x, y, z, _t = point
    return x % _ED25519_FIELD == 0 and (y - z) % _ED25519_FIELD == 0


def _validate_prime_subgroup_point(value: bytes, label: str) -> None:
    """Reject noncanonical, off-curve, identity, torsion, and mixed-order points."""

    if type(value) is not bytes or len(value) != 32:
        raise ValueError(f"{label} must contain exactly 32 immutable bytes")
    encoded = int.from_bytes(value, "little")
    sign = encoded >> 255
    y = encoded & ((1 << 255) - 1)
    field = _ED25519_FIELD
    if y >= field:
        raise ValueError(f"{label} is not canonically encoded")
    y_squared = y * y % field
    numerator = (y_squared - 1) % field
    denominator = (_ED25519_D * y_squared + 1) % field
    if denominator == 0:
        raise ValueError(f"{label} is not an Ed25519 curve point")
    x_squared = numerator * pow(denominator, field - 2, field) % field
    x = pow(x_squared, (field + 3) // 8, field)
    if x * x % field != x_squared:
        x = x * _ED25519_SQRT_MINUS_ONE % field
    if x * x % field != x_squared:
        raise ValueError(f"{label} is not an Ed25519 curve point")
    if x == 0 and sign == 1:
        raise ValueError(f"{label} has a noncanonical sign bit")
    if x & 1 != sign:
        x = field - x
    point = (x, y, 1, x * y % field)
    if _is_identity(point) or not _is_identity(
        _scalar_multiply(_ED25519_ORDER, point)
    ):
        raise ValueError(f"{label} is not a nonidentity prime-subgroup point")


def _signature_bytes(value: object) -> bytes:
    if type(value) is not str or _SIGNATURE_RE.fullmatch(value) is None:
        raise ValueError("Ed25519 signature must be canonical lowercase hexadecimal")
    signature = bytes.fromhex(value.removeprefix("ed25519:"))
    _validate_prime_subgroup_point(signature[:32], "Ed25519 signature R point")
    if int.from_bytes(signature[32:], "little") >= _ED25519_ORDER:
        raise ValueError("Ed25519 signature scalar is not canonical")
    return signature


class CanonicalEd25519VerifierV3:
    """Immutable-byte verifier for one fixed, caller-chosen protocol domain."""

    __slots__ = ("_domain", "_public_bytes")

    def __init__(self, public_key_bytes: bytes, *, domain: str) -> None:
        if type(self) is not CanonicalEd25519VerifierV3:
            raise ValueError("canonical Ed25519 verifier subclasses are forbidden")
        if type(public_key_bytes) is not bytes or len(public_key_bytes) != 32:
            raise ValueError("Ed25519 public key must contain exactly 32 immutable bytes")
        domain_bytes = _domain_bytes(domain)
        _validate_prime_subgroup_point(public_key_bytes, "Ed25519 public key")
        self._public_bytes = bytes(public_key_bytes)
        self._domain = domain_bytes

    @property
    def authority_commitment(self) -> str:
        value = _authority_commitment(self._domain, self._public_bytes)
        if _DIGEST_RE.fullmatch(value) is None:  # pragma: no cover - defensive invariant
            raise ValueError("canonical Ed25519 authority commitment is invalid")
        return value

    @property
    def key_fingerprint(self) -> str:
        value = _key_fingerprint(self._public_bytes)
        if _DIGEST_RE.fullmatch(value) is None:  # pragma: no cover - defensive invariant
            raise ValueError("canonical Ed25519 key fingerprint is invalid")
        return value

    def public_key_bytes(self) -> bytes:
        return self._public_bytes

    def verify(self, canonical_record: bytes, signature: str) -> None:
        if type(self) is not CanonicalEd25519VerifierV3:
            raise ValueError("canonical Ed25519 verifier must be exact")
        signature_bytes = _signature_bytes(signature)
        message = _framed_message(self._domain, canonical_record)
        try:
            Ed25519PublicKey.from_public_bytes(self._public_bytes).verify(
                signature_bytes,
                message,
            )
        except (InvalidSignature, ValueError) as exc:
            raise ValueError("canonical Ed25519 signature is invalid") from exc


class CanonicalEd25519SignerV3:
    """Exact-seed signer; caller, not this class, must provide independent custody."""

    __slots__ = ("_domain", "_private_key", "_public_bytes")

    def __init__(self, private_seed: bytes, *, domain: str) -> None:
        if type(self) is not CanonicalEd25519SignerV3:
            raise ValueError("canonical Ed25519 signer subclasses are forbidden")
        if type(private_seed) is not bytes or len(private_seed) != 32:
            raise ValueError("Ed25519 private seed must contain exactly 32 immutable bytes")
        self._domain = _domain_bytes(domain)
        self._private_key = Ed25519PrivateKey.from_private_bytes(private_seed)
        self._public_bytes = self._private_key.public_key().public_bytes_raw()

    @property
    def authority_commitment(self) -> str:
        return _authority_commitment(self._domain, self._public_bytes)

    @property
    def key_fingerprint(self) -> str:
        return _key_fingerprint(self._public_bytes)

    def verifier(self) -> CanonicalEd25519VerifierV3:
        return CanonicalEd25519VerifierV3(
            self._public_bytes,
            domain=self._domain.decode("ascii"),
        )

    def sign(self, canonical_record: bytes) -> str:
        if type(self) is not CanonicalEd25519SignerV3:
            raise ValueError("canonical Ed25519 signer must be exact")
        signature = self._private_key.sign(
            _framed_message(self._domain, canonical_record)
        )
        return "ed25519:" + signature.hex()


__all__ = [
    "CANONICAL_ED25519_MAX_DEPTH_V3",
    "CANONICAL_ED25519_MAX_NODES_V3",
    "CANONICAL_ED25519_MAX_RECORD_BYTES_V3",
    "CanonicalEd25519SignerV3",
    "CanonicalEd25519VerifierV3",
]
