"""Pre-axis recurrence binding-bank manifests for hard-challenge v3.

This layer freezes full typed recurrence bindings before any schedule seed,
fault, answer, template, or solver-view axis exists.  Its digest proves exact
content identity, not publication time; chronology still requires an external
immutable commitment to ``content_digest``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .models import canonical_json
from .recurrence_binding_v3 import RecurrenceGroupBindingV3
from .source_role_v3 import (
    SourceRoleClaimBankV3,
)
from .source_role_v3 import (
    build_source_role_claim_bank_v3 as _build_source_role_claim_bank_v3,
)
from .source_role_v3 import (
    source_role_input_digest_v3 as _source_role_input_digest_v3,
)

RECURRENCE_BINDING_BANK_SCHEMA_V3 = (
    "org.math-research-brain/recurrence-binding-bank-manifest"
)
RECURRENCE_BINDING_BANK_SCHEMA_VERSION_V3 = 1
MAX_RECURRENCE_BINDING_BANK_ENTRIES_V3 = 2_166
MAX_RECURRENCE_BINDING_BANK_JSON_BYTES_V3 = 8 * 1024 * 1024

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_MANIFEST_DIGEST_DOMAIN = "org.math-research-brain/recurrence-binding-bank/v3"
_ENTRY_CONTEXT_DIGEST_DOMAIN = (
    "org.math-research-brain/recurrence-binding-bank-entry-context/v3"
)


def _strict_keys(raw: object, expected: set[str], label: str) -> dict[str, Any]:
    if type(raw) is not dict or not all(type(key) is str for key in raw):
        raise ValueError(f"{label} must be an exact object")
    if set(raw) != expected:
        raise ValueError(f"{label} keys must be exactly {sorted(expected)}")
    return raw


def _integer(value: object, label: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an exact integer in [{minimum}, {maximum}]")
    return value


def _digest(value: object, *, domain: str) -> str:
    payload = {"domain": domain, "payload": value}
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _validate_digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical SHA-256 digest")
    return value


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _load_canonical_json(raw: object, label: str) -> object:
    if type(raw) is not str:
        raise ValueError(f"{label} JSON must be exact text")
    try:
        encoded = raw.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} JSON must be valid UTF-8 text") from exc
    if len(encoded) > MAX_RECURRENCE_BINDING_BANK_JSON_BYTES_V3:
        raise ValueError(f"{label} JSON exceeds its byte budget")
    try:
        parsed = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ValueError(f"{label} JSON is invalid: {exc}") from exc
    try:
        canonical = canonical_json(parsed)
    except (RecursionError, ValueError) as exc:
        raise ValueError(f"{label} JSON cannot be canonically re-encoded") from exc
    if raw != canonical:
        raise ValueError(f"{label} JSON must use canonical encoding")
    return parsed


def _instance_key(binding: RecurrenceGroupBindingV3) -> tuple[int, int, int]:
    instance = binding.program.instance
    return (instance.r, instance.c0, instance.c1)


@dataclass(frozen=True, slots=True)
class RecurrenceBindingBankEntryV3:
    """One canonically indexed, fully replayable typed recurrence binding."""

    bank_index: int
    binding: RecurrenceGroupBindingV3
    binding_digest: str = field(init=False)
    program_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not RecurrenceBindingBankEntryV3:
            raise ValueError("recurrence binding-bank entry subclasses are forbidden")
        _integer(
            self.bank_index,
            "recurrence binding-bank index",
            minimum=0,
            maximum=MAX_RECURRENCE_BINDING_BANK_ENTRIES_V3 - 1,
        )
        if type(self.binding) is not RecurrenceGroupBindingV3:
            raise ValueError("recurrence binding-bank entry requires an exact binding")
        self.binding.validate_current()
        object.__setattr__(self, "binding_digest", self.binding.content_digest)
        object.__setattr__(self, "program_digest", self.binding.program.program_digest)
        self.validate_current()

    def validate_current(self) -> None:
        if type(self) is not RecurrenceBindingBankEntryV3:
            raise ValueError("recurrence binding-bank entry subclasses are forbidden")
        _integer(
            self.bank_index,
            "recurrence binding-bank index",
            minimum=0,
            maximum=MAX_RECURRENCE_BINDING_BANK_ENTRIES_V3 - 1,
        )
        if type(self.binding) is not RecurrenceGroupBindingV3:
            raise ValueError("recurrence binding-bank entry binding is invalid")
        self.binding.validate_current()
        binding_digest = _validate_digest(
            self.binding_digest, "recurrence binding-bank entry binding digest"
        )
        program_digest = _validate_digest(
            self.program_digest, "recurrence binding-bank entry program digest"
        )
        if not hmac.compare_digest(binding_digest, self.binding.content_digest):
            raise ValueError("recurrence binding-bank entry digest does not match its binding")
        if not hmac.compare_digest(program_digest, self.binding.program.program_digest):
            raise ValueError("recurrence binding-bank entry program digest is incorrect")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return {
            "bank_index": self.bank_index,
            "binding": self.binding.to_dict(),
            "binding_digest": self.binding_digest,
            "program_digest": self.program_digest,
        }

    def context_digest(self, manifest_digest: str) -> str:
        self.validate_current()
        manifest = _validate_digest(manifest_digest, "recurrence binding-bank manifest digest")
        return _digest(
            {
                "bank_index": self.bank_index,
                "binding_digest": self.binding_digest,
                "manifest_digest": manifest,
            },
            domain=_ENTRY_CONTEXT_DIGEST_DOMAIN,
        )

    @classmethod
    def from_dict(cls, raw: object) -> RecurrenceBindingBankEntryV3:
        value = _strict_keys(
            raw,
            {"bank_index", "binding", "binding_digest", "program_digest"},
            "recurrence binding-bank entry",
        )
        entry = cls(
            bank_index=_integer(
                value["bank_index"],
                "serialized recurrence binding-bank index",
                minimum=0,
                maximum=MAX_RECURRENCE_BINDING_BANK_ENTRIES_V3 - 1,
            ),
            binding=RecurrenceGroupBindingV3.from_dict(value["binding"]),
        )
        supplied_binding = _validate_digest(
            value["binding_digest"], "serialized recurrence binding digest"
        )
        supplied_program = _validate_digest(
            value["program_digest"], "serialized recurrence program digest"
        )
        if not hmac.compare_digest(supplied_binding, entry.binding_digest):
            raise ValueError("serialized recurrence binding-bank entry digest is incorrect")
        if not hmac.compare_digest(supplied_program, entry.program_digest):
            raise ValueError("serialized recurrence binding-bank program digest is incorrect")
        return entry


def _manifest_payload(
    entries: tuple[RecurrenceBindingBankEntryV3, ...],
) -> dict[str, object]:
    return {
        "entries": [entry.to_dict() for entry in entries],
        "schema": RECURRENCE_BINDING_BANK_SCHEMA_V3,
        "schema_version": RECURRENCE_BINDING_BANK_SCHEMA_VERSION_V3,
    }


@dataclass(frozen=True, slots=True)
class RecurrenceBindingBankManifestV3:
    """Canonical pre-axis bank whose digest must be committed externally."""

    entries: tuple[RecurrenceBindingBankEntryV3, ...]
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not RecurrenceBindingBankManifestV3:
            raise ValueError("recurrence binding-bank manifest subclasses are forbidden")
        self._validate_entries()
        object.__setattr__(
            self,
            "content_digest",
            _digest(_manifest_payload(self.entries), domain=_MANIFEST_DIGEST_DOMAIN),
        )
        self.validate_current()

    def _validate_entries(self) -> None:
        if type(self) is not RecurrenceBindingBankManifestV3:
            raise ValueError("recurrence binding-bank manifest subclasses are forbidden")
        if type(self.entries) is not tuple:
            raise ValueError("recurrence binding-bank entries must be an exact tuple")
        if not 1 <= len(self.entries) <= MAX_RECURRENCE_BINDING_BANK_ENTRIES_V3:
            raise ValueError(
                "recurrence binding-bank manifest must contain between 1 and "
                f"{MAX_RECURRENCE_BINDING_BANK_ENTRIES_V3} entries"
            )
        if not all(type(entry) is RecurrenceBindingBankEntryV3 for entry in self.entries):
            raise ValueError("recurrence binding-bank manifest contains an invalid entry")
        for entry in self.entries:
            entry.validate_current()
        if tuple(entry.bank_index for entry in self.entries) != tuple(range(len(self.entries))):
            raise ValueError("recurrence binding-bank indices must be contiguous and canonical")
        binding_digests = tuple(entry.binding_digest for entry in self.entries)
        if binding_digests != tuple(sorted(binding_digests)):
            raise ValueError("recurrence binding-bank entries must be ordered by binding digest")
        unique_fields = {
            "instances": tuple(_instance_key(entry.binding) for entry in self.entries),
            "binding digests": binding_digests,
            "program digests": tuple(entry.program_digest for entry in self.entries),
        }
        for label, values in unique_fields.items():
            if len(set(values)) != len(values):
                raise ValueError(f"recurrence binding-bank {label} must be unique")

    def validate_current(self) -> None:
        if type(self) is not RecurrenceBindingBankManifestV3:
            raise ValueError("recurrence binding-bank manifest subclasses are forbidden")
        self._validate_entries()
        supplied = _validate_digest(
            self.content_digest, "recurrence binding-bank manifest content digest"
        )
        expected = _digest(_manifest_payload(self.entries), domain=_MANIFEST_DIGEST_DOMAIN)
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("recurrence binding-bank digest does not bind its exact entries")

    def validate_expected_digest(self, expected_manifest_digest: str) -> None:
        expected = _validate_digest(
            expected_manifest_digest, "expected recurrence binding-bank manifest digest"
        )
        self.validate_current()
        if not hmac.compare_digest(expected, self.content_digest):
            raise ValueError("recurrence binding-bank does not match its external commitment")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return _manifest_payload(self.entries) | {"content_digest": self.content_digest}

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, raw: object) -> RecurrenceBindingBankManifestV3:
        value = _strict_keys(
            raw,
            {"content_digest", "entries", "schema", "schema_version"},
            "recurrence binding-bank manifest",
        )
        if (
            type(value["schema"]) is not str
            or value["schema"] != RECURRENCE_BINDING_BANK_SCHEMA_V3
        ):
            raise ValueError("recurrence binding-bank manifest schema is unsupported")
        _integer(
            value["schema_version"],
            "recurrence binding-bank manifest schema version",
            minimum=RECURRENCE_BINDING_BANK_SCHEMA_VERSION_V3,
            maximum=RECURRENCE_BINDING_BANK_SCHEMA_VERSION_V3,
        )
        raw_entries = value["entries"]
        if type(raw_entries) is not list:
            raise ValueError("recurrence binding-bank serialized entries must be an exact array")
        if not 1 <= len(raw_entries) <= MAX_RECURRENCE_BINDING_BANK_ENTRIES_V3:
            raise ValueError("recurrence binding-bank serialized entry count is outside its cap")
        manifest = cls(
            entries=tuple(RecurrenceBindingBankEntryV3.from_dict(item) for item in raw_entries)
        )
        supplied = _validate_digest(
            value["content_digest"], "serialized recurrence binding-bank digest"
        )
        if not hmac.compare_digest(supplied, manifest.content_digest):
            raise ValueError("serialized recurrence binding-bank digest is incorrect")
        return manifest

    @classmethod
    def from_json(cls, raw: object) -> RecurrenceBindingBankManifestV3:
        return cls.from_dict(_load_canonical_json(raw, "recurrence binding-bank manifest"))


def _bounded_rebuilt_bindings(
    bindings: Iterable[RecurrenceGroupBindingV3],
) -> tuple[RecurrenceGroupBindingV3, ...]:
    if type(bindings) in (str, bytes):
        raise ValueError("recurrence binding-bank input must be an iterable of typed bindings")
    try:
        iterator = iter(bindings)
    except TypeError as exc:
        raise ValueError("recurrence binding-bank input must be iterable") from exc
    rebuilt: list[RecurrenceGroupBindingV3] = []
    for index, binding in enumerate(iterator):
        if index >= MAX_RECURRENCE_BINDING_BANK_ENTRIES_V3:
            raise ValueError("recurrence binding-bank input exceeds its entry cap")
        if type(binding) is not RecurrenceGroupBindingV3:
            raise ValueError("recurrence binding-bank input contains a noncanonical binding")
        binding.validate_current()
        rebuilt.append(RecurrenceGroupBindingV3.from_dict(binding.to_dict()))
    if not rebuilt:
        raise ValueError("recurrence binding-bank input must not be empty")
    return tuple(sorted(rebuilt, key=lambda item: item.content_digest))


def build_recurrence_binding_bank_manifest_v3(
    bindings: Iterable[RecurrenceGroupBindingV3],
) -> RecurrenceBindingBankManifestV3:
    canonical = _bounded_rebuilt_bindings(bindings)
    entries = tuple(
        RecurrenceBindingBankEntryV3(bank_index=index, binding=binding)
        for index, binding in enumerate(canonical)
    )
    return RecurrenceBindingBankManifestV3(entries=entries)


def _validated_entry(
    manifest: RecurrenceBindingBankManifestV3,
    bank_index: int,
    expected_manifest_digest: str,
) -> RecurrenceBindingBankEntryV3:
    if type(manifest) is not RecurrenceBindingBankManifestV3:
        raise ValueError("recurrence scheduling requires an exact binding-bank manifest")
    manifest.validate_expected_digest(expected_manifest_digest)
    index = _integer(
        bank_index,
        "recurrence binding-bank schedule index",
        minimum=0,
        maximum=len(manifest.entries) - 1,
    )
    return manifest.entries[index]


def build_preregistered_recurrence_source_role_claim_bank_v3(
    *,
    manifest: RecurrenceBindingBankManifestV3,
    bank_index: int,
    schedule_seed: bytes,
    expected_manifest_digest: str,
) -> SourceRoleClaimBankV3:
    """Schedule one manifest entry only after its external digest is supplied."""

    entry = _validated_entry(manifest, bank_index, expected_manifest_digest)
    source_bank, targets, offsets = entry.binding.source_role_inputs()
    return _build_source_role_claim_bank_v3(
        source_bank=source_bank,
        group_binding_digest=entry.context_digest(manifest.content_digest),
        targets=targets,
        offsets=offsets,
        schedule_seed=schedule_seed,
    )


def verify_preregistered_recurrence_source_role_claim_bank_v3(
    *,
    manifest: RecurrenceBindingBankManifestV3,
    bank_index: int,
    claim_bank: SourceRoleClaimBankV3,
    schedule_seed: bytes,
    expected_manifest_digest: str,
) -> None:
    entry = _validated_entry(manifest, bank_index, expected_manifest_digest)
    if type(claim_bank) is not SourceRoleClaimBankV3:
        raise ValueError("preregistered recurrence verification requires an exact claim bank")
    claim_bank.validate_current()
    context_digest = entry.context_digest(manifest.content_digest)
    if not hmac.compare_digest(claim_bank.group_binding_digest, context_digest):
        raise ValueError("source-role claim bank is not bound to this manifest entry")
    source_bank, targets, offsets = entry.binding.source_role_inputs()
    expected_input_digest = _source_role_input_digest_v3(
        source_bank=source_bank,
        group_binding_digest=context_digest,
        targets=targets,
        offsets=offsets,
    )
    claim_bank.verify_schedule_seed(
        schedule_seed,
        expected_input_digest=expected_input_digest,
    )
    rebuilt = build_preregistered_recurrence_source_role_claim_bank_v3(
        manifest=manifest,
        bank_index=bank_index,
        schedule_seed=schedule_seed,
        expected_manifest_digest=expected_manifest_digest,
    )
    if rebuilt != claim_bank:
        raise ValueError("manifest entry does not reconstruct the source-role claim bank")


__all__ = [
    "MAX_RECURRENCE_BINDING_BANK_ENTRIES_V3",
    "MAX_RECURRENCE_BINDING_BANK_JSON_BYTES_V3",
    "RECURRENCE_BINDING_BANK_SCHEMA_V3",
    "RECURRENCE_BINDING_BANK_SCHEMA_VERSION_V3",
    "RecurrenceBindingBankEntryV3",
    "RecurrenceBindingBankManifestV3",
    "build_preregistered_recurrence_source_role_claim_bank_v3",
    "build_recurrence_binding_bank_manifest_v3",
    "verify_preregistered_recurrence_source_role_claim_bank_v3",
]
