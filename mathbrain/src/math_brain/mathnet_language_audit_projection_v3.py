"""Replay-final, gold-free MathNet language-audit projection for study v3.

This module deliberately stops before operational authorization.  Its authoritative builder
turns the exact source-pinned pilot universe into an unsigned canonical JSON artifact
containing only keyed HMAC pseudonyms and the original null-language prompts.  Authoritative
derivation and verification require a complete source/vault replay; JSON reconstruction does
not.  The returned digest is tamper evidence rather than authentication.  Every handoff,
signature, code-identity, runtime-identity, and lookup-blinding gate therefore remains false.

The HMAC hides the relationship between an item ID and the internal selection coordinate
when the pseudonym key stays secret.  It cannot hide a public MathNet prompt: the prompt
itself remains a lookup key, and may contain source clues in its prose.  A real reviewer must
eventually run without network, source cache, vault, or any preparation key.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .mathnet_acquisition_v3 import (
    PreparedMathNetPilotEvaluatorVaultV3,
    PreparedMathNetPilotUniverseV3,
    PreparedPinnedMathNetSourceV3,
    _prepared_pilot_universe_value_v3,
    verify_pinned_mathnet_pilot_artifacts_v3,
)
from .mathnet_pilot_material_v3 import (
    GoldFreeMathNetPilotRowV3,
    GoldFreeMathNetPilotUniverseV3,
    MathNetPilotLanguageStateV3,
)
from .models import canonical_json

MATHNET_LANGUAGE_AUDIT_PROJECTION_SCHEMA_V3 = (
    "org.math-research-brain/mathnet-language-audit-projection"
)
MATHNET_LANGUAGE_AUDIT_PROJECTION_VERSION_V3 = 3
MATHNET_LANGUAGE_AUDIT_MAX_ITEMS_V3 = 10_000
MATHNET_LANGUAGE_AUDIT_MAX_PROMPT_BYTES_V3 = 32 * 1024
MATHNET_LANGUAGE_AUDIT_MAX_JSON_BYTES_V3 = 64 * 1024 * 1024

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_AUDIT_ITEM_ID_RE = re.compile(r"mathnet-language-audit:[0-9a-f]{64}\Z")
_AUDIT_ITEM_ID_DOMAIN = b"math-brain/mathnet-null-language-audit/v3/item-id\x00"
_PSEUDONYM_KEY_COMMITMENT_DOMAIN = b"math-brain/mathnet-null-language-audit/v3/key\x00"
_PROJECTION_DOMAIN = "org.math-research-brain/mathnet-language-audit-projection/v3"
_REVIEWER_PAYLOAD_DOMAIN = (
    "org.math-research-brain/mathnet-language-audit-reviewer-payload/v3"
)
_PROJECTION_TOKEN_V3 = object()
_DIGEST_SIZE_SENTINEL_V3 = "sha256:" + "0" * 64


def _digest(value: object, *, domain: str) -> str:
    return "sha256:" + hashlib.sha256(
        canonical_json({"domain": domain, "payload": value}).encode("utf-8")
    ).hexdigest()


def _validate_digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical SHA-256 digest")
    return value


def _bounded_text(value: object, label: str, *, maximum: int) -> str:
    if type(value) is not str:
        raise ValueError(f"{label} must be exact text")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8") from exc
    if not value.strip() or len(encoded) > maximum:
        raise ValueError(f"{label} is empty or exceeds its byte budget")
    return value


def _secret_key(value: object, label: str) -> bytes:
    if type(value) is not bytes or not 32 <= len(value) <= 64:
        raise ValueError(f"{label} must contain 32 to 64 immutable bytes")
    return value


def _validated_batch_size(batch_size: object) -> int:
    if type(batch_size) is not int or not 1 <= batch_size <= 65_536:
        raise ValueError("MathNet language-audit batch size must be in [1, 65536]")
    return batch_size


def _mapping(value: object, label: str) -> dict[str, Any]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ValueError(f"{label} must be an exact string-keyed object")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} has unexpected fields")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _load_canonical_json(raw: object) -> object:
    if type(raw) is not str:
        raise ValueError("MathNet language-audit JSON must be exact text")
    try:
        encoded = raw.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("MathNet language-audit JSON must be valid UTF-8") from exc
    if len(encoded) > MATHNET_LANGUAGE_AUDIT_MAX_JSON_BYTES_V3:
        raise ValueError("MathNet language-audit JSON exceeds its byte budget")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_nonfinite,
        )
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ValueError("MathNet language-audit JSON is invalid") from exc
    if raw != canonical_json(value):
        raise ValueError("MathNet language-audit JSON is not canonical")
    return value


def _projection_fixed_payload_v3() -> dict[str, object]:
    return {
        "confirmatory_source_gate": False,
        "engineering_pilot_only": True,
        "external_signature_attested": False,
        "in_process_creation_identity_attested": False,
        "language_audit_handoff_authorized": False,
        "local_source_replay_required_for_use": True,
        "lookup_blinding_attested": False,
        "preparation_code_identity_attested": False,
        "preparation_runtime_identity_attested": False,
        "private_gold_present": False,
        "pseudonym_key_freshness_attested": False,
        "schema": MATHNET_LANGUAGE_AUDIT_PROJECTION_SCHEMA_V3,
        "schema_version": MATHNET_LANGUAGE_AUDIT_PROJECTION_VERSION_V3,
        "selector_handoff_authorized": False,
        "structured_source_metadata_present": False,
        "wrapper_self_authenticating": False,
    }


@dataclass(frozen=True, slots=True)
class MathNetLanguageAuditItemV3:
    """The complete and deliberately tiny view for one language reviewer item."""

    audit_item_id: str
    problem_text: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self) is not MathNetLanguageAuditItemV3:
            raise ValueError("MathNet language-audit item subclasses are forbidden")
        if type(self.audit_item_id) is not str or _AUDIT_ITEM_ID_RE.fullmatch(
            self.audit_item_id
        ) is None:
            raise ValueError("MathNet language-audit item id is invalid")
        _bounded_text(
            self.problem_text,
            "MathNet language-audit problem text",
            maximum=MATHNET_LANGUAGE_AUDIT_MAX_PROMPT_BYTES_V3,
        )

    def to_dict(self) -> dict[str, str]:
        self.__post_init__()
        return {
            "audit_item_id": self.audit_item_id,
            "problem_text": self.problem_text,
        }

    @classmethod
    def from_dict(cls, raw: object) -> MathNetLanguageAuditItemV3:
        value = _mapping(raw, "MathNet language-audit item")
        _exact_keys(value, {"audit_item_id", "problem_text"}, "language-audit item")
        return cls(
            audit_item_id=value["audit_item_id"],
            problem_text=value["problem_text"],
        )


def _validate_projection_transport_budget_v3(
    *,
    items: tuple[MathNetLanguageAuditItemV3, ...],
    pseudonym_key_commitment: str,
    content_digest: str,
) -> None:
    """Enforce the exact canonical UTF-8 size without materializing the full JSON."""

    empty_root = _projection_fixed_payload_v3() | {
        "content_digest": content_digest,
        "items": [],
        "pseudonym_key_commitment": pseudonym_key_commitment,
    }
    size = len(canonical_json(empty_root).encode("utf-8"))
    if items:
        size += len(items) - 1
    for item in items:
        size += len(canonical_json(item.to_dict()).encode("utf-8"))
        if size > MATHNET_LANGUAGE_AUDIT_MAX_JSON_BYTES_V3:
            raise ValueError("MathNet language-audit projection exceeds its byte budget")


@dataclass(frozen=True, slots=True, init=False)
class UnsignedMathNetAuditProjectionV3:
    """Unsigned queue transport; only replay verification can establish its derivation."""

    items: tuple[MathNetLanguageAuditItemV3, ...] = field(repr=False)
    pseudonym_key_commitment: str
    content_digest: str
    _token: object = field(repr=False, compare=False)

    def __new__(
        cls,
        token: object = None,
    ) -> UnsignedMathNetAuditProjectionV3:
        if token is not _PROJECTION_TOKEN_V3:
            raise TypeError("language-audit projections require the replay-final builder")
        return object.__new__(cls)

    @classmethod
    def _derive(
        cls,
        *,
        items: tuple[MathNetLanguageAuditItemV3, ...],
        pseudonym_key_commitment: str,
    ) -> UnsignedMathNetAuditProjectionV3:
        instance = cls(_PROJECTION_TOKEN_V3)
        object.__setattr__(instance, "items", items)
        object.__setattr__(
            instance,
            "pseudonym_key_commitment",
            pseudonym_key_commitment,
        )
        object.__setattr__(instance, "_token", _PROJECTION_TOKEN_V3)
        instance._validate_fields()
        _validate_projection_transport_budget_v3(
            items=instance.items,
            pseudonym_key_commitment=instance.pseudonym_key_commitment,
            content_digest=_DIGEST_SIZE_SENTINEL_V3,
        )
        object.__setattr__(
            instance,
            "content_digest",
            _digest(instance._payload_without_digest(), domain=_PROJECTION_DOMAIN),
        )
        instance.validate_current()
        return instance

    def _validate_fields(self) -> None:
        if (
            type(self) is not UnsignedMathNetAuditProjectionV3
            or self._token is not _PROJECTION_TOKEN_V3
        ):
            raise ValueError("MathNet language-audit projection is not exact")
        if type(self.items) is not tuple or not 1 <= len(self.items) <= (
            MATHNET_LANGUAGE_AUDIT_MAX_ITEMS_V3
        ):
            raise ValueError("MathNet language-audit item count is invalid")
        if any(type(item) is not MathNetLanguageAuditItemV3 for item in self.items):
            raise ValueError("MathNet language-audit items must be exact")
        for item in self.items:
            item.__post_init__()
        item_ids = tuple(item.audit_item_id for item in self.items)
        if item_ids != tuple(sorted(item_ids)) or len(set(item_ids)) != len(item_ids):
            raise ValueError("MathNet language-audit item ids must be unique and sorted")
        _validate_digest(
            self.pseudonym_key_commitment,
            "MathNet language-audit pseudonym-key commitment",
        )

    def _payload_without_digest(self) -> dict[str, object]:
        return _projection_fixed_payload_v3() | {
            "items": [item.to_dict() for item in self.items],
            "pseudonym_key_commitment": self.pseudonym_key_commitment,
        }

    def validate_current(self) -> None:
        self._validate_fields()
        supplied = _validate_digest(
            self.content_digest,
            "MathNet language-audit projection digest",
        )
        _validate_projection_transport_budget_v3(
            items=self.items,
            pseudonym_key_commitment=self.pseudonym_key_commitment,
            content_digest=supplied,
        )
        expected = _digest(self._payload_without_digest(), domain=_PROJECTION_DOMAIN)
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("MathNet language-audit projection digest is stale")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return self._payload_without_digest() | {"content_digest": self.content_digest}

    def canonical_json(self) -> str:
        value = canonical_json(self.to_dict())
        if len(value.encode("utf-8")) > MATHNET_LANGUAGE_AUDIT_MAX_JSON_BYTES_V3:
            raise ValueError("MathNet language-audit projection exceeds its byte budget")
        return value

    def reviewer_payload_json(self) -> str:
        """Return the exact unsigned prompt/ID payload committed for a reviewer."""

        self.validate_current()
        payload = {
            "items": [item.to_dict() for item in self.items],
            "schema": MATHNET_LANGUAGE_AUDIT_PROJECTION_SCHEMA_V3,
            "schema_version": MATHNET_LANGUAGE_AUDIT_PROJECTION_VERSION_V3,
        }
        encoded = canonical_json(payload)
        if len(encoded.encode("utf-8")) > MATHNET_LANGUAGE_AUDIT_MAX_JSON_BYTES_V3:
            raise ValueError("MathNet language-audit reviewer payload exceeds its byte budget")
        expected = self.public_commitment_payload()["reviewer_payload_digest"]
        observed = _digest(payload, domain=_REVIEWER_PAYLOAD_DOMAIN)
        if type(expected) is not str or not hmac.compare_digest(expected, observed):
            raise ValueError("MathNet language-audit reviewer payload digest differs")
        return encoded

    def public_commitment_payload(self) -> dict[str, object]:
        self.validate_current()
        reviewer_payload = {
            "items": [item.to_dict() for item in self.items],
            "schema": MATHNET_LANGUAGE_AUDIT_PROJECTION_SCHEMA_V3,
            "schema_version": MATHNET_LANGUAGE_AUDIT_PROJECTION_VERSION_V3,
        }
        return _projection_fixed_payload_v3() | {
            "content_digest": self.content_digest,
            "item_count": len(self.items),
            "pseudonym_key_commitment": self.pseudonym_key_commitment,
            "reviewer_payload_digest": _digest(
                reviewer_payload,
                domain=_REVIEWER_PAYLOAD_DOMAIN,
            ),
        }

    @classmethod
    def from_dict(cls, raw: object) -> UnsignedMathNetAuditProjectionV3:
        value = _mapping(raw, "MathNet language-audit projection")
        fixed = _projection_fixed_payload_v3()
        expected_keys = set(fixed) | {
            "content_digest",
            "items",
            "pseudonym_key_commitment",
        }
        _exact_keys(value, expected_keys, "MathNet language-audit projection")
        for key, expected in fixed.items():
            if value[key] != expected or type(value[key]) is not type(expected):
                raise ValueError(f"MathNet language-audit fixed field {key} differs")
        raw_items = value["items"]
        if type(raw_items) is not list:
            raise ValueError("MathNet language-audit items must be an exact array")
        rebuilt = cls._derive(
            items=tuple(MathNetLanguageAuditItemV3.from_dict(item) for item in raw_items),
            pseudonym_key_commitment=value["pseudonym_key_commitment"],
        )
        supplied = _validate_digest(
            value["content_digest"],
            "serialized MathNet language-audit projection digest",
        )
        if not hmac.compare_digest(rebuilt.content_digest, supplied):
            raise ValueError("serialized MathNet language-audit projection digest differs")
        return rebuilt

    @classmethod
    def from_json(cls, raw: object) -> UnsignedMathNetAuditProjectionV3:
        return cls.from_dict(_load_canonical_json(raw))

    def __repr__(self) -> str:
        return (
            "UnsignedMathNetAuditProjectionV3("
            f"item_count={len(self.items)!r}, "
            f"content_digest={self.content_digest!r}, "
            "language_audit_handoff_authorized=False)"
        )

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("MathNet language-audit projections require canonical JSON transport")


def _pseudonym_key_commitment_v3(pseudonym_key: bytes) -> str:
    return "sha256:" + hashlib.sha256(
        _PSEUDONYM_KEY_COMMITMENT_DOMAIN + pseudonym_key
    ).hexdigest()


def _audit_item_id_v3(
    *,
    pseudonym_key: bytes,
    universe_content_digest: str,
    selection_coordinate_digest: str,
) -> str:
    _validate_digest(universe_content_digest, "MathNet language-audit universe digest")
    _validate_digest(
        selection_coordinate_digest,
        "MathNet language-audit selection-coordinate digest",
    )
    material = (
        _AUDIT_ITEM_ID_DOMAIN
        + universe_content_digest.encode("ascii")
        + b"\x00"
        + selection_coordinate_digest.encode("ascii")
    )
    return "mathnet-language-audit:" + hmac.new(
        pseudonym_key,
        material,
        hashlib.sha256,
    ).hexdigest()


def _replay_final_null_language_rows_v3(
    source: PreparedPinnedMathNetSourceV3,
    prepared_universe: PreparedMathNetPilotUniverseV3,
    evaluator_vault: PreparedMathNetPilotEvaluatorVaultV3,
    *,
    vault_key: bytes,
    pseudonym_key: bytes,
    batch_size: int,
) -> tuple[GoldFreeMathNetPilotRowV3, ...]:
    if type(source) is not PreparedPinnedMathNetSourceV3:
        raise ValueError("language-audit projection requires an exact prepared source")
    if type(prepared_universe) is not PreparedMathNetPilotUniverseV3:
        raise ValueError("language-audit projection requires an exact prepared universe")
    if type(evaluator_vault) is not PreparedMathNetPilotEvaluatorVaultV3:
        raise ValueError("language-audit projection requires an exact evaluator vault")
    vault_key = _secret_key(vault_key, "MathNet language-audit vault key")
    pseudonym_key = _secret_key(
        pseudonym_key,
        "MathNet language-audit pseudonym key",
    )
    if hmac.compare_digest(vault_key, pseudonym_key):
        raise ValueError("MathNet vault and language-audit pseudonym keys must differ")
    batch_size = _validated_batch_size(batch_size)

    # This full source replay is deliberately complete before any prompt row is projected.
    # The returned object remains unsigned and handoff-ineligible despite this local check.
    verify_pinned_mathnet_pilot_artifacts_v3(
        source,
        prepared_universe,
        evaluator_vault,
        vault_key=vault_key,
        batch_size=batch_size,
    )

    universe = _prepared_pilot_universe_value_v3(prepared_universe)
    if type(universe) is not GoldFreeMathNetPilotUniverseV3:
        raise ValueError("language-audit projection has no exact gold-free universe")
    universe.validate_current()
    rows: list[GoldFreeMathNetPilotRowV3] = []
    for row in universe.rows:
        if type(row) is not GoldFreeMathNetPilotRowV3:
            raise ValueError("language-audit projection encountered a nonexact row")
        row.validate_current()
        if row.raw_language_state is MathNetPilotLanguageStateV3.NULL_AUDIT_REQUIRED:
            rows.append(row)
    counts = dict(prepared_universe.screen_counts)
    expected = counts.get("null_language_audit_queue")
    if type(expected) is not int or expected <= 0 or len(rows) != expected:
        raise ValueError("language-audit row count differs from the prepared universe")
    return tuple(rows)


def build_unsigned_mathnet_language_audit_projection_v3(
    source: PreparedPinnedMathNetSourceV3,
    prepared_universe: PreparedMathNetPilotUniverseV3,
    evaluator_vault: PreparedMathNetPilotEvaluatorVaultV3,
    *,
    vault_key: bytes,
    pseudonym_key: bytes,
    batch_size: int = 512,
) -> UnsignedMathNetAuditProjectionV3:
    """Replay the source/vault pair, then emit only HMAC ids and raw null prompts."""

    key = _secret_key(pseudonym_key, "MathNet language-audit pseudonym key")
    rows = _replay_final_null_language_rows_v3(
        source,
        prepared_universe,
        evaluator_vault,
        vault_key=vault_key,
        pseudonym_key=key,
        batch_size=batch_size,
    )
    universe_content_digest = prepared_universe.universe_content_digest
    items = tuple(
        sorted(
            (
                MathNetLanguageAuditItemV3(
                    audit_item_id=_audit_item_id_v3(
                        pseudonym_key=key,
                        universe_content_digest=universe_content_digest,
                        selection_coordinate_digest=row.selection_coordinate_digest,
                    ),
                    problem_text=row.problem_text,
                )
                for row in rows
            ),
            key=lambda item: item.audit_item_id,
        )
    )
    if len({item.audit_item_id for item in items}) != len(items):
        raise ValueError("MathNet language-audit pseudonym collision")
    projection = UnsignedMathNetAuditProjectionV3._derive(
        items=items,
        pseudonym_key_commitment=_pseudonym_key_commitment_v3(key),
    )
    projection.validate_current()
    return projection


def verify_unsigned_mathnet_language_audit_projection_v3(
    source: PreparedPinnedMathNetSourceV3,
    prepared_universe: PreparedMathNetPilotUniverseV3,
    evaluator_vault: PreparedMathNetPilotEvaluatorVaultV3,
    projection: UnsignedMathNetAuditProjectionV3,
    *,
    vault_key: bytes,
    pseudonym_key: bytes,
    batch_size: int = 512,
) -> None:
    """Rebuild an unsigned projection after another complete source/vault replay."""

    if type(projection) is not UnsignedMathNetAuditProjectionV3:
        raise ValueError("language-audit verification requires an exact unsigned projection")
    projection.validate_current()
    key = _secret_key(pseudonym_key, "MathNet language-audit pseudonym key")
    supplied_commitment = _pseudonym_key_commitment_v3(key)
    if not hmac.compare_digest(
        supplied_commitment,
        projection.pseudonym_key_commitment,
    ):
        raise ValueError("MathNet language-audit pseudonym key commitment differs")
    rebuilt = build_unsigned_mathnet_language_audit_projection_v3(
        source,
        prepared_universe,
        evaluator_vault,
        vault_key=vault_key,
        pseudonym_key=key,
        batch_size=batch_size,
    )
    if projection != rebuilt:
        raise ValueError("MathNet language-audit projection differs from deterministic replay")


__all__ = [
    "MATHNET_LANGUAGE_AUDIT_MAX_ITEMS_V3",
    "MATHNET_LANGUAGE_AUDIT_MAX_JSON_BYTES_V3",
    "MATHNET_LANGUAGE_AUDIT_MAX_PROMPT_BYTES_V3",
    "MATHNET_LANGUAGE_AUDIT_PROJECTION_SCHEMA_V3",
    "MATHNET_LANGUAGE_AUDIT_PROJECTION_VERSION_V3",
    "MathNetLanguageAuditItemV3",
    "UnsignedMathNetAuditProjectionV3",
    "build_unsigned_mathnet_language_audit_projection_v3",
    "verify_unsigned_mathnet_language_audit_projection_v3",
]
