"""Payload-free final history manifest for the sealed 72-slot engineering pilot."""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field

from .attempt_ledger import (
    AttemptHistoryRecord,
    AttemptLedger,
    OutcomeKind,
    SlotCommitment,
    StartedAttempt,
    StoredOutcome,
    TerminalTransition,
)
from .models import canonical_json
from .three_arm_evaluation import V3_PILOT_CASE_COUNT

FINAL_PILOT_LEDGER_SCHEMA_V3 = "org.math-research-brain/final-pilot-ledger-history/v3"
FINAL_PILOT_LEDGER_SCHEMA_VERSION_V3 = 1
FINAL_PILOT_ATTEMPT_COUNT_V3 = V3_PILOT_CASE_COUNT * 3

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_SLOT_ID_RE = re.compile(r"slot_[0-9a-f]{32}\Z", re.ASCII)


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _digest_json(value: object) -> str:
    return _sha256(canonical_json(value).encode("utf-8"))


def _digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical SHA-256 digest")
    return value


def _slot_id(value: object) -> str:
    if type(value) is not str or _SLOT_ID_RE.fullmatch(value) is None:
        raise ValueError("final ledger slot id is invalid")
    return value


def _media_type(value: object) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > 128:
        raise ValueError("final ledger media type is invalid")
    return value


@dataclass(frozen=True, slots=True)
class FinalPilotLedgerEntryV3:
    """All immutable record links for one slot, without response/failure payload bytes."""

    ordinal: int
    slot_id: str
    slot_commitment_digest: str
    started_record_digest: str
    prerequisite_terminal_digest: None
    selection_record_digest: str
    outcome_object_digest: str
    outcome_kind: OutcomeKind
    outcome_media_type: str
    terminal_record_digest: str

    def __post_init__(self) -> None:
        if type(self) is not FinalPilotLedgerEntryV3:
            raise ValueError("final ledger entry subclasses are forbidden")
        if type(self.ordinal) is not int or not 0 <= self.ordinal < FINAL_PILOT_ATTEMPT_COUNT_V3:
            raise ValueError("final ledger ordinal is invalid")
        _slot_id(self.slot_id)
        for label, value in (
            ("slot commitment digest", self.slot_commitment_digest),
            ("STARTED record digest", self.started_record_digest),
            ("selection record digest", self.selection_record_digest),
            ("outcome object digest", self.outcome_object_digest),
            ("terminal record digest", self.terminal_record_digest),
        ):
            _digest(value, label)
        if self.prerequisite_terminal_digest is not None:
            raise ValueError("pilot slots must not use predecessor-dependent STARTED records")
        if type(self.outcome_kind) is not OutcomeKind:
            raise ValueError("final ledger outcome kind must be exact")
        _media_type(self.outcome_media_type)

    def to_dict(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "ordinal": self.ordinal,
            "outcome_kind": self.outcome_kind.value,
            "outcome_media_type": self.outcome_media_type,
            "outcome_object_digest": self.outcome_object_digest,
            "prerequisite_terminal_digest": self.prerequisite_terminal_digest,
            "selection_record_digest": self.selection_record_digest,
            "slot_commitment_digest": self.slot_commitment_digest,
            "slot_id": self.slot_id,
            "started_record_digest": self.started_record_digest,
            "terminal_record_digest": self.terminal_record_digest,
        }


@dataclass(frozen=True, slots=True)
class FinalPilotLedgerManifestV3:
    """Complete immutable history; public serialization exposes commitments only."""

    ledger_digest: str
    entries: tuple[FinalPilotLedgerEntryV3, ...] = field(repr=False)
    entry_universe_digest: str
    returned_count: int
    failed_count: int

    def __post_init__(self) -> None:
        if type(self) is not FinalPilotLedgerManifestV3:
            raise ValueError("final ledger manifest subclasses are forbidden")
        _digest(self.ledger_digest, "final ledger precommitment digest")
        _digest(self.entry_universe_digest, "final ledger entry-universe digest")
        if type(self.entries) is not tuple or len(self.entries) != FINAL_PILOT_ATTEMPT_COUNT_V3:
            raise ValueError("final pilot ledger requires exactly 72 entries")
        if any(type(entry) is not FinalPilotLedgerEntryV3 for entry in self.entries):
            raise ValueError("final ledger entries must be exact records")
        for entry in self.entries:
            entry.__post_init__()
        if tuple(entry.ordinal for entry in self.entries) != tuple(
            range(FINAL_PILOT_ATTEMPT_COUNT_V3)
        ):
            raise ValueError("final ledger entries must retain exact slot order")
        if len({entry.slot_id for entry in self.entries}) != len(self.entries):
            raise ValueError("final ledger entries reuse a slot id")
        expected_entry_digest = _digest_json([entry.to_dict() for entry in self.entries])
        if not hmac.compare_digest(self.entry_universe_digest, expected_entry_digest):
            raise ValueError("final ledger entry universe differs from its commitment")
        if type(self.returned_count) is not int or type(self.failed_count) is not int:
            raise ValueError("final ledger outcome counts must be exact integers")
        observed_returned = sum(
            entry.outcome_kind is OutcomeKind.RETURNED for entry in self.entries
        )
        observed_failed = sum(entry.outcome_kind is OutcomeKind.FAILED for entry in self.entries)
        if (self.returned_count, self.failed_count) != (observed_returned, observed_failed):
            raise ValueError("final ledger outcome counts differ from the entries")
        if self.returned_count + self.failed_count != FINAL_PILOT_ATTEMPT_COUNT_V3:
            raise ValueError("final ledger does not contain 72 terminal outcomes")

    def private_record(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "all_slots_terminal": True,
            "entry_universe_digest": self.entry_universe_digest,
            "entries": [entry.to_dict() for entry in self.entries],
            "exactly_one_started_record_per_slot": True,
            "failed_count": self.failed_count,
            "ledger_digest": self.ledger_digest,
            "no_start_prerequisite_chain": True,
            "returned_count": self.returned_count,
            "schema": FINAL_PILOT_LEDGER_SCHEMA_V3,
            "schema_version": FINAL_PILOT_LEDGER_SCHEMA_VERSION_V3,
            "slot_count": len(self.entries),
        }

    @property
    def history_digest(self) -> str:
        return _digest_json(self.private_record())

    def public_commitment(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "all_slots_terminal": True,
            "entry_universe_digest": self.entry_universe_digest,
            "exactly_one_started_record_per_slot": True,
            "failed_count": self.failed_count,
            "history_digest": self.history_digest,
            "ledger_digest": self.ledger_digest,
            "no_start_prerequisite_chain": True,
            "returned_count": self.returned_count,
            "schema": FINAL_PILOT_LEDGER_SCHEMA_V3,
            "schema_version": FINAL_PILOT_LEDGER_SCHEMA_VERSION_V3,
            "slot_count": len(self.entries),
        }


def _entry_from_history_v3(record: AttemptHistoryRecord) -> FinalPilotLedgerEntryV3:
    if type(record) is not AttemptHistoryRecord:
        raise ValueError("ledger history must contain exact projection records")
    if type(record.slot) is not SlotCommitment:
        raise ValueError("ledger history slot commitment must be exact")
    record.slot.__post_init__()
    started = record.started
    outcome = record.selected_outcome
    terminal = record.terminal
    if type(started) is not StartedAttempt:
        raise ValueError(f"slot {record.ordinal} has no durable STARTED record")
    if type(outcome) is not StoredOutcome:
        raise ValueError(f"slot {record.ordinal} has no selected durable outcome")
    if type(terminal) is not TerminalTransition:
        raise ValueError(f"slot {record.ordinal} has no terminal transition")
    if (
        record.ordinal != started.ordinal
        or record.ordinal != outcome.ordinal
        or (record.ordinal != terminal.ordinal)
    ):
        raise ValueError("final ledger record ordinals disagree")
    slot_id = record.slot.slot_id
    if slot_id != started.slot_id or slot_id != outcome.slot_id or slot_id != terminal.slot_id:
        raise ValueError("final ledger record slot identities disagree")
    if started.prerequisite_terminal_digest is not None:
        raise ValueError("pilot history used a predecessor-dependent STARTED transition")
    if outcome.started_record_digest != started.record_digest:
        raise ValueError("selected outcome is bound to another STARTED record")
    if outcome.kind is not terminal.kind or outcome.object_digest != terminal.object_digest:
        raise ValueError("selected outcome and terminal transition disagree")
    if not hmac.compare_digest(started.ledger_digest, outcome.ledger_digest):
        raise ValueError("STARTED and selected outcome name different ledgers")
    return FinalPilotLedgerEntryV3(
        ordinal=record.ordinal,
        slot_id=slot_id,
        slot_commitment_digest=_digest_json(record.slot.to_dict(ordinal=record.ordinal)),
        started_record_digest=started.record_digest,
        prerequisite_terminal_digest=None,
        selection_record_digest=outcome.selection_record_digest,
        outcome_object_digest=outcome.object_digest,
        outcome_kind=outcome.kind,
        outcome_media_type=outcome.media_type,
        terminal_record_digest=terminal.record_digest,
    )


def build_final_pilot_ledger_manifest_v3(
    ledger: AttemptLedger,
    *,
    expected_ledger_digest: str,
) -> FinalPilotLedgerManifestV3:
    """Build only after all 72 immutable slots have one independent terminal outcome."""

    if type(ledger) is not AttemptLedger:
        raise ValueError("final ledger assembly requires an exact AttemptLedger")
    expected = _digest(expected_ledger_digest, "externally expected ledger digest")
    if not hmac.compare_digest(ledger.ledger_digest, expected):
        raise ValueError("pilot ledger differs from the externally expected precommitment")
    if type(ledger.slots) is not tuple or len(ledger.slots) != FINAL_PILOT_ATTEMPT_COUNT_V3:
        raise ValueError("final pilot ledger must have exactly 72 precommitted slots")

    first = ledger.history_records()
    second = ledger.history_records()
    if type(first) is not tuple or type(second) is not tuple or first != second:
        raise ValueError("pilot ledger history did not remain stable during final assembly")
    if len(first) != FINAL_PILOT_ATTEMPT_COUNT_V3:
        raise ValueError("final pilot ledger history must contain exactly 72 slots")
    entries = tuple(_entry_from_history_v3(record) for record in first)
    if any(
        started.ledger_digest != expected
        for started in (record.started for record in first)
        if started is not None
    ):
        raise ValueError("pilot STARTED record names another ledger")
    entry_digest = _digest_json([entry.to_dict() for entry in entries])
    return FinalPilotLedgerManifestV3(
        ledger_digest=expected,
        entries=entries,
        entry_universe_digest=entry_digest,
        returned_count=sum(entry.outcome_kind is OutcomeKind.RETURNED for entry in entries),
        failed_count=sum(entry.outcome_kind is OutcomeKind.FAILED for entry in entries),
    )


def verify_final_pilot_ledger_manifest_v3(
    ledger: AttemptLedger,
    manifest: FinalPilotLedgerManifestV3,
    *,
    expected_ledger_digest: str,
    expected_history_digest: str,
) -> None:
    """Cold-replay a final history against two externally expected digests."""

    if type(manifest) is not FinalPilotLedgerManifestV3:
        raise ValueError("final ledger verification requires an exact manifest")
    manifest.__post_init__()
    expected_history = _digest(expected_history_digest, "expected final history digest")
    if not hmac.compare_digest(manifest.history_digest, expected_history):
        raise ValueError("final ledger manifest differs from the externally expected history")
    rebuilt = build_final_pilot_ledger_manifest_v3(
        ledger,
        expected_ledger_digest=expected_ledger_digest,
    )
    if not hmac.compare_digest(
        canonical_json(rebuilt.private_record()),
        canonical_json(manifest.private_record()),
    ):
        raise ValueError("final ledger manifest differs from cold history replay")


__all__ = [
    "FINAL_PILOT_ATTEMPT_COUNT_V3",
    "FinalPilotLedgerEntryV3",
    "FinalPilotLedgerManifestV3",
    "build_final_pilot_ledger_manifest_v3",
    "verify_final_pilot_ledger_manifest_v3",
]
