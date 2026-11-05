"""Independently committed answer-axis plans for hard-challenge v3.

The recurrence binding manifest is frozen before this layer.  An axis seed then
orders the *complete* Cartesian design of eight claim positions by the two
source-role fault mechanisms for every manifest entry.  Callers cannot supply a
root claim or mechanism to the builder, and changing a source-role schedule
cannot change any axis identity.

Digests prove exact content identity, not publication time.  Chronology still
requires an external immutable commitment to both the binding-manifest digest
and the resulting axis-plan digest.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .models import canonical_json
from .recurrence_manifest_v3 import (
    MAX_RECURRENCE_BINDING_BANK_ENTRIES_V3,
    RecurrenceBindingBankManifestV3,
    verify_preregistered_recurrence_source_role_claim_bank_v3,
)
from .source_role_v3 import (
    SOURCE_ROLE_CLAIM_COUNT_V3,
    SourceRoleClaimBankV3,
    SourceRoleFaultV3,
)

RECURRENCE_AXIS_PLAN_SCHEMA_V3 = "org.math-research-brain/recurrence-fault-axis-plan"
RECURRENCE_AXIS_PLAN_SCHEMA_VERSION_V3 = 1
RECURRENCE_CLAIM_POSITIONS_V3 = 8
RECURRENCE_AXIS_FAULTS_V3 = (
    SourceRoleFaultV3.SWAP_CANONICAL_FIRST,
    SourceRoleFaultV3.SWAP_CANONICAL_SECOND,
)
if SOURCE_ROLE_CLAIM_COUNT_V3 != RECURRENCE_CLAIM_POSITIONS_V3:
    raise RuntimeError("source-role claim count changed without a recurrence axis schema bump")
RECURRENCE_AXES_PER_BANK_V3 = (
    RECURRENCE_CLAIM_POSITIONS_V3 * len(RECURRENCE_AXIS_FAULTS_V3)
)
MAX_RECURRENCE_AXIS_SLOTS_V3 = (
    MAX_RECURRENCE_BINDING_BANK_ENTRIES_V3 * RECURRENCE_AXES_PER_BANK_V3
)
MAX_RECURRENCE_AXIS_PLAN_JSON_BYTES_V3 = 32 * 1024 * 1024

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_AXIS_DIGEST_DOMAIN = "org.math-research-brain/recurrence-fault-axis/v3"
_PLAN_DIGEST_DOMAIN = "org.math-research-brain/recurrence-fault-axis-plan/v3"
_AXIS_SEED_DOMAIN = b"org.math-research-brain/recurrence-fault-axis-seed/v3\0"
_AXIS_ORDER_DOMAIN = b"org.math-research-brain/recurrence-fault-axis-order/v3\0"
_BANK_RECEIPT_DOMAIN = "org.math-research-brain/verified-recurrence-claim-bank/v3"
_PLAN_RECEIPT_DOMAIN = "org.math-research-brain/verified-recurrence-axis-plan/v3"


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


def _validate_digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical SHA-256 digest")
    return value


def _digest(value: object, *, domain: str) -> str:
    payload = {"domain": domain, "payload": value}
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _validate_seed(axis_seed: object) -> bytes:
    if type(axis_seed) is not bytes or not 32 <= len(axis_seed) <= 64:
        raise ValueError("recurrence axis seed must be exact bytes from 32 to 64 bytes")
    return axis_seed


def recurrence_axis_seed_commitment_v3(
    *, axis_seed: bytes, manifest_digest: str
) -> str:
    """Create the value that must be committed before an axis plan is ordered."""

    seed = _validate_seed(axis_seed)
    manifest = _validate_digest(manifest_digest, "recurrence axis seed manifest")
    return "sha256:" + hashlib.sha256(
        _AXIS_SEED_DOMAIN + manifest.encode("ascii") + b"\0" + seed
    ).hexdigest()


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _load_canonical_json(raw: object) -> object:
    if type(raw) is not str:
        raise ValueError("recurrence axis-plan JSON must be exact text")
    try:
        encoded = raw.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("recurrence axis-plan JSON must be valid UTF-8 text") from exc
    if len(encoded) > MAX_RECURRENCE_AXIS_PLAN_JSON_BYTES_V3:
        raise ValueError("recurrence axis-plan JSON exceeds its byte budget")
    try:
        parsed = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ValueError(f"recurrence axis-plan JSON is invalid: {exc}") from exc
    try:
        encoded_again = canonical_json(parsed)
    except (RecursionError, ValueError) as exc:
        raise ValueError("recurrence axis-plan JSON cannot be canonically re-encoded") from exc
    if raw != encoded_again:
        raise ValueError("recurrence axis-plan JSON must use canonical encoding")
    return parsed


def _fault(value: object, label: str) -> SourceRoleFaultV3:
    if type(value) is not str:
        raise ValueError(f"{label} must be an exact fault-mechanism string")
    try:
        fault = SourceRoleFaultV3(value)
    except ValueError as exc:
        raise ValueError(f"{label} is unsupported") from exc
    if fault not in RECURRENCE_AXIS_FAULTS_V3:
        raise ValueError(f"{label} is unsupported by axis-plan schema v1")
    return fault


def _slot_axis_payload(
    *,
    manifest_digest: str,
    entry_context_digest: str,
    bank_index: int,
    claim_index: int,
    fault: SourceRoleFaultV3,
) -> dict[str, object]:
    return {
        "bank_index": bank_index,
        "claim_index": claim_index,
        "entry_context_digest": entry_context_digest,
        "fault": fault.value,
        "manifest_digest": manifest_digest,
    }


@dataclass(frozen=True, slots=True)
class RecurrenceAxisSlotV3:
    """One private answer-axis slot from a complete balanced plan."""

    slot_index: int
    bank_index: int
    claim_index: int
    fault: SourceRoleFaultV3
    entry_context_digest: str
    manifest_digest: str
    axis_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not RecurrenceAxisSlotV3:
            raise ValueError("recurrence axis-slot subclasses are forbidden")
        self._validate_fields()
        object.__setattr__(
            self,
            "axis_digest",
            _digest(
                _slot_axis_payload(
                    manifest_digest=self.manifest_digest,
                    entry_context_digest=self.entry_context_digest,
                    bank_index=self.bank_index,
                    claim_index=self.claim_index,
                    fault=self.fault,
                ),
                domain=_AXIS_DIGEST_DOMAIN,
            ),
        )
        self.validate_current()

    def _validate_fields(self) -> None:
        _integer(
            self.slot_index,
            "recurrence axis slot index",
            minimum=0,
            maximum=MAX_RECURRENCE_AXIS_SLOTS_V3 - 1,
        )
        _integer(
            self.bank_index,
            "recurrence axis bank index",
            minimum=0,
            maximum=MAX_RECURRENCE_BINDING_BANK_ENTRIES_V3 - 1,
        )
        _integer(
            self.claim_index,
            "recurrence axis claim index",
            minimum=0,
            maximum=RECURRENCE_CLAIM_POSITIONS_V3 - 1,
        )
        if type(self.fault) is not SourceRoleFaultV3:
            raise ValueError("recurrence axis fault requires an exact mechanism")
        _validate_digest(self.entry_context_digest, "recurrence axis entry context")
        _validate_digest(self.manifest_digest, "recurrence axis manifest")

    def validate_current(self) -> None:
        if type(self) is not RecurrenceAxisSlotV3:
            raise ValueError("recurrence axis-slot subclasses are forbidden")
        self._validate_fields()
        supplied = _validate_digest(self.axis_digest, "recurrence axis digest")
        expected = _digest(
            _slot_axis_payload(
                manifest_digest=self.manifest_digest,
                entry_context_digest=self.entry_context_digest,
                bank_index=self.bank_index,
                claim_index=self.claim_index,
                fault=self.fault,
            ),
            domain=_AXIS_DIGEST_DOMAIN,
        )
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("recurrence axis digest does not bind its exact slot")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return {
            "axis_digest": self.axis_digest,
            "bank_index": self.bank_index,
            "claim_index": self.claim_index,
            "entry_context_digest": self.entry_context_digest,
            "fault": self.fault.value,
            "manifest_digest": self.manifest_digest,
            "slot_index": self.slot_index,
        }

    @classmethod
    def from_dict(cls, raw: object) -> RecurrenceAxisSlotV3:
        value = _strict_keys(
            raw,
            {
                "axis_digest",
                "bank_index",
                "claim_index",
                "entry_context_digest",
                "fault",
                "manifest_digest",
                "slot_index",
            },
            "recurrence axis slot",
        )
        slot = cls(
            slot_index=_integer(
                value["slot_index"],
                "serialized recurrence axis slot index",
                minimum=0,
                maximum=MAX_RECURRENCE_AXIS_SLOTS_V3 - 1,
            ),
            bank_index=_integer(
                value["bank_index"],
                "serialized recurrence axis bank index",
                minimum=0,
                maximum=MAX_RECURRENCE_BINDING_BANK_ENTRIES_V3 - 1,
            ),
            claim_index=_integer(
                value["claim_index"],
                "serialized recurrence axis claim index",
                minimum=0,
                maximum=RECURRENCE_CLAIM_POSITIONS_V3 - 1,
            ),
            fault=_fault(value["fault"], "serialized recurrence axis fault"),
            entry_context_digest=_validate_digest(
                value["entry_context_digest"],
                "serialized recurrence axis entry context",
            ),
            manifest_digest=_validate_digest(
                value["manifest_digest"], "serialized recurrence axis manifest"
            ),
        )
        supplied = _validate_digest(value["axis_digest"], "serialized recurrence axis digest")
        if not hmac.compare_digest(supplied, slot.axis_digest):
            raise ValueError("serialized recurrence axis digest is incorrect")
        return slot


def _plan_payload(
    *,
    manifest_digest: str,
    axis_seed_commitment: str,
    slots: tuple[RecurrenceAxisSlotV3, ...],
) -> dict[str, object]:
    return {
        "axis_seed_commitment": axis_seed_commitment,
        "manifest_digest": manifest_digest,
        "schema": RECURRENCE_AXIS_PLAN_SCHEMA_V3,
        "schema_version": RECURRENCE_AXIS_PLAN_SCHEMA_VERSION_V3,
        "slots": [slot.to_dict() for slot in slots],
    }


@dataclass(frozen=True, slots=True)
class RecurrenceAxisPlanV3:
    """Complete Root x Mechanism plan for every frozen manifest entry."""

    manifest_digest: str
    axis_seed_commitment: str
    slots: tuple[RecurrenceAxisSlotV3, ...]
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not RecurrenceAxisPlanV3:
            raise ValueError("recurrence axis-plan subclasses are forbidden")
        self._validate_fields()
        object.__setattr__(
            self,
            "content_digest",
            _digest(
                _plan_payload(
                    manifest_digest=self.manifest_digest,
                    axis_seed_commitment=self.axis_seed_commitment,
                    slots=self.slots,
                ),
                domain=_PLAN_DIGEST_DOMAIN,
            ),
        )
        self.validate_current()

    def _validate_fields(self) -> None:
        _validate_digest(self.manifest_digest, "recurrence axis-plan manifest")
        _validate_digest(self.axis_seed_commitment, "recurrence axis seed commitment")
        if type(self.slots) is not tuple:
            raise ValueError("recurrence axis-plan slots must be an exact tuple")
        if not RECURRENCE_AXES_PER_BANK_V3 <= len(self.slots) <= MAX_RECURRENCE_AXIS_SLOTS_V3:
            raise ValueError("recurrence axis-plan slot count is outside its bounded design")
        if len(self.slots) % RECURRENCE_AXES_PER_BANK_V3:
            raise ValueError("recurrence axis-plan must contain complete per-bank axis blocks")
        if not all(type(slot) is RecurrenceAxisSlotV3 for slot in self.slots):
            raise ValueError("recurrence axis-plan contains a noncanonical slot")
        for slot in self.slots:
            slot.validate_current()
        if tuple(slot.slot_index for slot in self.slots) != tuple(range(len(self.slots))):
            raise ValueError("recurrence axis-plan slot indices must be contiguous and canonical")
        if any(slot.manifest_digest != self.manifest_digest for slot in self.slots):
            raise ValueError("recurrence axis-plan contains a cross-manifest slot")
        bank_count = len(self.slots) // RECURRENCE_AXES_PER_BANK_V3
        if set(slot.bank_index for slot in self.slots) != set(range(bank_count)):
            raise ValueError("recurrence axis-plan bank indices must be contiguous and complete")
        slots_by_bank: list[list[RecurrenceAxisSlotV3]] = [
            [] for _ in range(bank_count)
        ]
        for slot in self.slots:
            slots_by_bank[slot.bank_index].append(slot)
        expected_axes = {
            (claim_index, fault)
            for claim_index in range(RECURRENCE_CLAIM_POSITIONS_V3)
            for fault in RECURRENCE_AXIS_FAULTS_V3
        }
        for bank_slots in slots_by_bank:
            if len(bank_slots) != RECURRENCE_AXES_PER_BANK_V3:
                raise ValueError("recurrence axis-plan has an incomplete per-bank axis block")
            if {(slot.claim_index, slot.fault) for slot in bank_slots} != expected_axes:
                raise ValueError("recurrence axis-plan must contain each Root x Mechanism once")
            if len({slot.entry_context_digest for slot in bank_slots}) != 1:
                raise ValueError("recurrence axis-plan bank block mixes entry contexts")
        if len({slot.axis_digest for slot in self.slots}) != len(self.slots):
            raise ValueError("recurrence axis-plan axis identities must be unique")

    def validate_current(self) -> None:
        if type(self) is not RecurrenceAxisPlanV3:
            raise ValueError("recurrence axis-plan subclasses are forbidden")
        self._validate_fields()
        supplied = _validate_digest(self.content_digest, "recurrence axis-plan content digest")
        expected = _digest(
            _plan_payload(
                manifest_digest=self.manifest_digest,
                axis_seed_commitment=self.axis_seed_commitment,
                slots=self.slots,
            ),
            domain=_PLAN_DIGEST_DOMAIN,
        )
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("recurrence axis-plan digest does not bind its exact design")

    def validate_expected_digest(self, expected_axis_plan_digest: str) -> None:
        expected = _validate_digest(
            expected_axis_plan_digest, "expected recurrence axis-plan digest"
        )
        self.validate_current()
        if not hmac.compare_digest(expected, self.content_digest):
            raise ValueError("recurrence axis-plan does not match its external commitment")

    def validate_against_manifest(self, manifest: RecurrenceBindingBankManifestV3) -> None:
        self.validate_current()
        if type(manifest) is not RecurrenceBindingBankManifestV3:
            raise ValueError("recurrence axis-plan validation requires an exact manifest")
        manifest.validate_current()
        if not hmac.compare_digest(self.manifest_digest, manifest.content_digest):
            raise ValueError("recurrence axis-plan is not bound to this manifest")
        if len(self.slots) != len(manifest.entries) * RECURRENCE_AXES_PER_BANK_V3:
            raise ValueError("recurrence axis-plan does not cover every manifest entry")
        for slot in self.slots:
            expected_context = manifest.entries[slot.bank_index].context_digest(
                manifest.content_digest
            )
            if not hmac.compare_digest(slot.entry_context_digest, expected_context):
                raise ValueError("recurrence axis slot is not bound to its manifest entry")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return _plan_payload(
            manifest_digest=self.manifest_digest,
            axis_seed_commitment=self.axis_seed_commitment,
            slots=self.slots,
        ) | {"content_digest": self.content_digest}

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, raw: object) -> RecurrenceAxisPlanV3:
        value = _strict_keys(
            raw,
            {
                "axis_seed_commitment",
                "content_digest",
                "manifest_digest",
                "schema",
                "schema_version",
                "slots",
            },
            "recurrence axis plan",
        )
        if type(value["schema"]) is not str or value["schema"] != RECURRENCE_AXIS_PLAN_SCHEMA_V3:
            raise ValueError("recurrence axis-plan schema is unsupported")
        _integer(
            value["schema_version"],
            "recurrence axis-plan schema version",
            minimum=RECURRENCE_AXIS_PLAN_SCHEMA_VERSION_V3,
            maximum=RECURRENCE_AXIS_PLAN_SCHEMA_VERSION_V3,
        )
        raw_slots = value["slots"]
        if type(raw_slots) is not list:
            raise ValueError("recurrence axis-plan serialized slots must be an exact array")
        if not RECURRENCE_AXES_PER_BANK_V3 <= len(raw_slots) <= MAX_RECURRENCE_AXIS_SLOTS_V3:
            raise ValueError("recurrence axis-plan serialized slot count is outside its cap")
        plan = cls(
            manifest_digest=_validate_digest(
                value["manifest_digest"], "serialized recurrence axis-plan manifest"
            ),
            axis_seed_commitment=_validate_digest(
                value["axis_seed_commitment"],
                "serialized recurrence axis seed commitment",
            ),
            slots=tuple(RecurrenceAxisSlotV3.from_dict(item) for item in raw_slots),
        )
        supplied = _validate_digest(
            value["content_digest"], "serialized recurrence axis-plan digest"
        )
        if not hmac.compare_digest(supplied, plan.content_digest):
            raise ValueError("serialized recurrence axis-plan digest is incorrect")
        return plan

    @classmethod
    def from_json(cls, raw: object) -> RecurrenceAxisPlanV3:
        return cls.from_dict(_load_canonical_json(raw))


def _axis_rank(
    axis_seed: bytes,
    *,
    manifest_digest: str,
    entry_context_digest: str,
    bank_index: int,
    claim_index: int,
    fault: SourceRoleFaultV3,
) -> bytes:
    message = (
        _AXIS_ORDER_DOMAIN
        + manifest_digest.encode("ascii")
        + b"\0"
        + entry_context_digest.encode("ascii")
        + b"\0"
        + bank_index.to_bytes(4, "big")
        + claim_index.to_bytes(2, "big")
        + b"\0"
        + fault.value.encode("ascii")
    )
    return hmac.new(axis_seed, message, hashlib.sha256).digest()


def build_recurrence_axis_plan_v3(
    *,
    manifest: RecurrenceBindingBankManifestV3,
    axis_seed: bytes,
    expected_manifest_digest: str,
    expected_axis_seed_commitment: str,
) -> RecurrenceAxisPlanV3:
    """Build a complete answer-axis design without accepting any answer axis."""

    if type(manifest) is not RecurrenceBindingBankManifestV3:
        raise ValueError("recurrence axis planning requires an exact binding manifest")
    manifest.validate_expected_digest(expected_manifest_digest)
    expected_seed = _validate_digest(
        expected_axis_seed_commitment, "expected recurrence axis seed commitment"
    )
    seed = _validate_seed(axis_seed)
    observed_seed = recurrence_axis_seed_commitment_v3(
        axis_seed=seed, manifest_digest=manifest.content_digest
    )
    if not hmac.compare_digest(expected_seed, observed_seed):
        raise ValueError("recurrence axis seed does not match its external commitment")
    slots: list[RecurrenceAxisSlotV3] = []
    slot_index = 0
    for entry in manifest.entries:
        context_digest = entry.context_digest(manifest.content_digest)
        axes = tuple(
            (claim_index, fault)
            for claim_index in range(RECURRENCE_CLAIM_POSITIONS_V3)
            for fault in RECURRENCE_AXIS_FAULTS_V3
        )
        ordered = sorted(
            axes,
            key=lambda axis: (
                _axis_rank(
                    seed,
                    manifest_digest=manifest.content_digest,
                    entry_context_digest=context_digest,
                    bank_index=entry.bank_index,
                    claim_index=axis[0],
                    fault=axis[1],
                ),
                axis[0],
                axis[1].value,
            ),
        )
        for claim_index, fault in ordered:
            slots.append(
                RecurrenceAxisSlotV3(
                    slot_index=slot_index,
                    bank_index=entry.bank_index,
                    claim_index=claim_index,
                    fault=fault,
                    entry_context_digest=context_digest,
                    manifest_digest=manifest.content_digest,
                )
            )
            slot_index += 1
    return RecurrenceAxisPlanV3(
        manifest_digest=manifest.content_digest,
        axis_seed_commitment=observed_seed,
        slots=tuple(slots),
    )


def verify_recurrence_axis_plan_v3(
    *,
    manifest: RecurrenceBindingBankManifestV3,
    axis_plan: RecurrenceAxisPlanV3,
    axis_seed: bytes,
    expected_manifest_digest: str,
    expected_axis_seed_commitment: str,
    expected_axis_plan_digest: str,
) -> None:
    """Replay a plan only after both external commitments are supplied."""

    if type(manifest) is not RecurrenceBindingBankManifestV3:
        raise ValueError("recurrence axis verification requires an exact binding manifest")
    manifest.validate_expected_digest(expected_manifest_digest)
    expected_plan = _validate_digest(
        expected_axis_plan_digest, "expected recurrence axis-plan digest"
    )
    expected_seed = _validate_digest(
        expected_axis_seed_commitment, "expected recurrence axis seed commitment"
    )
    if type(axis_plan) is not RecurrenceAxisPlanV3:
        raise ValueError("recurrence axis verification requires an exact axis plan")
    axis_plan.validate_expected_digest(expected_plan)
    if not hmac.compare_digest(axis_plan.axis_seed_commitment, expected_seed):
        raise ValueError("recurrence axis plan has the wrong external seed commitment")
    axis_plan.validate_against_manifest(manifest)
    seed = _validate_seed(axis_seed)
    if not hmac.compare_digest(
        axis_plan.axis_seed_commitment,
        recurrence_axis_seed_commitment_v3(
            axis_seed=seed, manifest_digest=manifest.content_digest
        ),
    ):
        raise ValueError("recurrence axis seed does not match its commitment")
    rebuilt = build_recurrence_axis_plan_v3(
        manifest=manifest,
        axis_seed=seed,
        expected_manifest_digest=expected_manifest_digest,
        expected_axis_seed_commitment=expected_seed,
    )
    if rebuilt != axis_plan:
        raise ValueError("recurrence axis seed does not reconstruct the committed plan")


@dataclass(frozen=True, slots=True, init=False)
class ReplayCheckedRecurrenceClaimBankV3:
    """Nominal runtime marker returned by the supported full-replay facade.

    This type prevents accidental use of a raw claim bank.  It is not a signed
    external receipt and cannot prove chronology against hostile Python code.
    """

    manifest_digest: str
    bank_index: int
    entry_context_digest: str
    binding_digest: str
    claim_bank: SourceRoleClaimBankV3
    verification_digest: str

    def __new__(cls, *_args: object, **_kwargs: object) -> ReplayCheckedRecurrenceClaimBankV3:
        raise TypeError("replay-checked claim banks are returned by the replay facade")

    def validate_current(self) -> None:
        if type(self) is not ReplayCheckedRecurrenceClaimBankV3:
            raise ValueError("replay-checked recurrence claim-bank subclasses are forbidden")
        manifest_digest = _validate_digest(
            self.manifest_digest, "replay-checked recurrence claim-bank manifest"
        )
        bank_index = _integer(
            self.bank_index,
            "replay-checked recurrence claim-bank index",
            minimum=0,
            maximum=MAX_RECURRENCE_BINDING_BANK_ENTRIES_V3 - 1,
        )
        entry_context = _validate_digest(
            self.entry_context_digest, "replay-checked recurrence claim-bank entry context"
        )
        binding_digest = _validate_digest(
            self.binding_digest, "replay-checked recurrence claim-bank binding"
        )
        if type(self.claim_bank) is not SourceRoleClaimBankV3:
            raise ValueError("replay-checked marker requires an exact source-role bank")
        self.claim_bank.validate_current()
        if not hmac.compare_digest(self.claim_bank.group_binding_digest, entry_context):
            raise ValueError("replay-checked bank has the wrong entry context")
        expected = _digest(
            {
                "bank_index": bank_index,
                "binding_digest": binding_digest,
                "claim_bank_input_digest": self.claim_bank.input_digest,
                "claim_bank_payload_digest": self.claim_bank.payload_digest,
                "entry_context_digest": entry_context,
                "manifest_digest": manifest_digest,
                "schedule_seed_commitment": self.claim_bank.schedule_seed_commitment,
            },
            domain=_BANK_RECEIPT_DOMAIN,
        )
        supplied = _validate_digest(
            self.verification_digest, "replay-checked recurrence claim-bank digest"
        )
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("replay-checked recurrence claim-bank digest is inconsistent")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return {
            "bank_index": self.bank_index,
            "binding_digest": self.binding_digest,
            "claim_bank": self.claim_bank.to_dict(),
            "entry_context_digest": self.entry_context_digest,
            "manifest_digest": self.manifest_digest,
            "verification_digest": self.verification_digest,
        }


def _new_replay_checked_claim_bank(
    *,
    manifest_digest: str,
    bank_index: int,
    entry_context_digest: str,
    binding_digest: str,
    claim_bank: SourceRoleClaimBankV3,
) -> ReplayCheckedRecurrenceClaimBankV3:
    receipt = object.__new__(ReplayCheckedRecurrenceClaimBankV3)
    object.__setattr__(receipt, "manifest_digest", manifest_digest)
    object.__setattr__(receipt, "bank_index", bank_index)
    object.__setattr__(receipt, "entry_context_digest", entry_context_digest)
    object.__setattr__(receipt, "binding_digest", binding_digest)
    object.__setattr__(receipt, "claim_bank", claim_bank)
    object.__setattr__(
        receipt,
        "verification_digest",
        _digest(
            {
                "bank_index": bank_index,
                "binding_digest": binding_digest,
                "claim_bank_input_digest": claim_bank.input_digest,
                "claim_bank_payload_digest": claim_bank.payload_digest,
                "entry_context_digest": entry_context_digest,
                "manifest_digest": manifest_digest,
                "schedule_seed_commitment": claim_bank.schedule_seed_commitment,
            },
            domain=_BANK_RECEIPT_DOMAIN,
        ),
    )
    receipt.validate_current()
    return receipt


def replay_and_bind_preregistered_recurrence_claim_bank_v3(
    *,
    manifest: RecurrenceBindingBankManifestV3,
    bank_index: int,
    claim_bank: SourceRoleClaimBankV3,
    schedule_seed: bytes,
    expected_manifest_digest: str,
) -> ReplayCheckedRecurrenceClaimBankV3:
    """Replay a claim bank and return the nominal checked runtime type."""

    if type(manifest) is not RecurrenceBindingBankManifestV3:
        raise ValueError("replay-checked recurrence binding requires an exact manifest")
    manifest.validate_expected_digest(expected_manifest_digest)
    index = _integer(
        bank_index,
        "replay-checked recurrence claim-bank index",
        minimum=0,
        maximum=len(manifest.entries) - 1,
    )
    verify_preregistered_recurrence_source_role_claim_bank_v3(
        manifest=manifest,
        bank_index=index,
        claim_bank=claim_bank,
        schedule_seed=schedule_seed,
        expected_manifest_digest=expected_manifest_digest,
    )
    entry = manifest.entries[index]
    return _new_replay_checked_claim_bank(
        manifest_digest=manifest.content_digest,
        bank_index=index,
        entry_context_digest=entry.context_digest(manifest.content_digest),
        binding_digest=entry.binding_digest,
        claim_bank=claim_bank,
    )


@dataclass(frozen=True, slots=True, init=False)
class ReplayCheckedRecurrenceAxisPlanV3:
    """Nominal runtime marker returned by the supported plan-replay facade.

    External publication supplies chronology; this in-process type only prevents
    accidental substitution of an unchecked plan in later typed adapters.
    """

    manifest_digest: str
    axis_plan: RecurrenceAxisPlanV3
    verification_digest: str

    def __new__(cls, *_args: object, **_kwargs: object) -> ReplayCheckedRecurrenceAxisPlanV3:
        raise TypeError("replay-checked axis plans are returned by the replay facade")

    def validate_current(self) -> None:
        if type(self) is not ReplayCheckedRecurrenceAxisPlanV3:
            raise ValueError("replay-checked recurrence axis-plan subclasses are forbidden")
        manifest_digest = _validate_digest(
            self.manifest_digest, "replay-checked recurrence axis-plan manifest"
        )
        if type(self.axis_plan) is not RecurrenceAxisPlanV3:
            raise ValueError("replay-checked recurrence axis marker requires an exact plan")
        self.axis_plan.validate_current()
        if not hmac.compare_digest(manifest_digest, self.axis_plan.manifest_digest):
            raise ValueError("replay-checked recurrence axis marker has the wrong manifest")
        expected = _digest(
            {
                "axis_plan_digest": self.axis_plan.content_digest,
                "axis_seed_commitment": self.axis_plan.axis_seed_commitment,
                "manifest_digest": manifest_digest,
            },
            domain=_PLAN_RECEIPT_DOMAIN,
        )
        supplied = _validate_digest(
            self.verification_digest, "replay-checked recurrence axis-plan digest"
        )
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("replay-checked recurrence axis-plan digest is inconsistent")


def _new_replay_checked_axis_plan(
    *, manifest_digest: str, axis_plan: RecurrenceAxisPlanV3
) -> ReplayCheckedRecurrenceAxisPlanV3:
    receipt = object.__new__(ReplayCheckedRecurrenceAxisPlanV3)
    object.__setattr__(receipt, "manifest_digest", manifest_digest)
    object.__setattr__(receipt, "axis_plan", axis_plan)
    object.__setattr__(
        receipt,
        "verification_digest",
        _digest(
            {
                "axis_plan_digest": axis_plan.content_digest,
                "axis_seed_commitment": axis_plan.axis_seed_commitment,
                "manifest_digest": manifest_digest,
            },
            domain=_PLAN_RECEIPT_DOMAIN,
        ),
    )
    receipt.validate_current()
    return receipt


def replay_and_bind_recurrence_axis_plan_v3(
    *,
    manifest: RecurrenceBindingBankManifestV3,
    axis_plan: RecurrenceAxisPlanV3,
    axis_seed: bytes,
    expected_manifest_digest: str,
    expected_axis_seed_commitment: str,
    expected_axis_plan_digest: str,
) -> ReplayCheckedRecurrenceAxisPlanV3:
    verify_recurrence_axis_plan_v3(
        manifest=manifest,
        axis_plan=axis_plan,
        axis_seed=axis_seed,
        expected_manifest_digest=expected_manifest_digest,
        expected_axis_seed_commitment=expected_axis_seed_commitment,
        expected_axis_plan_digest=expected_axis_plan_digest,
    )
    return _new_replay_checked_axis_plan(
        manifest_digest=manifest.content_digest,
        axis_plan=axis_plan,
    )


__all__ = [
    "MAX_RECURRENCE_AXIS_PLAN_JSON_BYTES_V3",
    "MAX_RECURRENCE_AXIS_SLOTS_V3",
    "RECURRENCE_AXES_PER_BANK_V3",
    "RECURRENCE_AXIS_FAULTS_V3",
    "RECURRENCE_AXIS_PLAN_SCHEMA_V3",
    "RECURRENCE_AXIS_PLAN_SCHEMA_VERSION_V3",
    "RECURRENCE_CLAIM_POSITIONS_V3",
    "RecurrenceAxisPlanV3",
    "RecurrenceAxisSlotV3",
    "ReplayCheckedRecurrenceAxisPlanV3",
    "ReplayCheckedRecurrenceClaimBankV3",
    "build_recurrence_axis_plan_v3",
    "recurrence_axis_seed_commitment_v3",
    "replay_and_bind_preregistered_recurrence_claim_bank_v3",
    "replay_and_bind_recurrence_axis_plan_v3",
    "verify_recurrence_axis_plan_v3",
]
