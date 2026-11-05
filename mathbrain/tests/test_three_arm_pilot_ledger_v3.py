from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from types import MethodType

import pytest

from math_brain.attempt_ledger import (
    ArtifactPayload,
    AttemptHistoryRecord,
    AttemptLedger,
    DispatchMaterial,
    OutcomeKind,
    SlotCommitment,
    StartedAttempt,
    StoredOutcome,
    TerminalTransition,
)
from math_brain.models import canonical_json
from math_brain.three_arm_pilot_ledger_v3 import (
    FINAL_PILOT_ATTEMPT_COUNT_V3,
    build_final_pilot_ledger_manifest_v3,
    verify_final_pilot_ledger_manifest_v3,
)

KEY = bytes(range(32))


def digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def material(index: int) -> DispatchMaterial:
    return DispatchMaterial(
        run_spec_digest=digest(f"run-{index}"),
        batch_digest=digest("sealed-pilot-batch"),
        prompt=f"Solve opaque pilot task {index}.",
        output_schema_json=b'{"additionalProperties":false,"type":"object"}',
        request_policy_digest=digest("request-policy"),
        dispatch_nonce=digest(f"dispatch-{index}"),
    )


def slot(index: int) -> SlotCommitment:
    return material(index).seal(key=KEY, slot_id=f"slot_{index:032x}")


def synthetic_ledger() -> tuple[AttemptLedger, tuple[AttemptHistoryRecord, ...]]:
    ledger_digest = digest("pilot-ledger")
    slots = tuple(slot(index) for index in range(FINAL_PILOT_ATTEMPT_COUNT_V3))
    records: list[AttemptHistoryRecord] = []
    for index, commitment in enumerate(slots):
        kind = OutcomeKind.FAILED if index in {7, 61} else OutcomeKind.RETURNED
        started = StartedAttempt(
            slot_id=commitment.slot_id,
            ordinal=index,
            ledger_digest=ledger_digest,
            record_digest=digest(f"started-{index}"),
            runtime_id=f"run_{index:032x}",
            runtime_device=1,
            runtime_inode=index + 1,
            prerequisite_terminal_digest=None,
        )
        outcome = StoredOutcome(
            slot_id=commitment.slot_id,
            ordinal=index,
            ledger_digest=ledger_digest,
            started_record_digest=started.record_digest,
            kind=kind,
            object_digest=digest(f"object-{index}"),
            media_type="application/json",
            selection_record_digest=digest(f"selection-{index}"),
        )
        terminal = TerminalTransition(
            slot_id=commitment.slot_id,
            ordinal=index,
            kind=kind,
            object_digest=outcome.object_digest,
            record_digest=digest(f"terminal-{index}"),
        )
        records.append(
            AttemptHistoryRecord(
                ordinal=index,
                slot=commitment,
                started=started,
                selected_outcome=outcome,
                terminal=terminal,
            )
        )
    ledger = object.__new__(AttemptLedger)
    ledger._ledger_digest = ledger_digest  # type: ignore[attr-defined]
    ledger._slots = slots  # type: ignore[attr-defined]
    frozen = tuple(records)
    ledger.history_records = MethodType(lambda self: frozen, ledger)  # type: ignore[method-assign]
    return ledger, frozen


def test_final_manifest_binds_all_72_histories_without_payloads() -> None:
    ledger, _records = synthetic_ledger()
    manifest = build_final_pilot_ledger_manifest_v3(
        ledger,
        expected_ledger_digest=ledger.ledger_digest,
    )
    assert len(manifest.entries) == 72
    assert manifest.returned_count == 70
    assert manifest.failed_count == 2
    assert manifest.public_commitment()["history_digest"] == manifest.history_digest
    assert manifest.public_commitment()["all_slots_terminal"] is True
    assert manifest.public_commitment()["no_start_prerequisite_chain"] is True
    public = canonical_json(manifest.public_commitment())
    assert "entries" not in public
    assert "application/json" not in public
    assert "runtime" not in public

    verify_final_pilot_ledger_manifest_v3(
        ledger,
        manifest,
        expected_ledger_digest=ledger.ledger_digest,
        expected_history_digest=manifest.history_digest,
    )


def test_manifest_rejects_pending_or_orphaned_and_chained_slots() -> None:
    ledger, records = synthetic_ledger()
    pending = list(records)
    pending[3] = replace(pending[3], started=None, selected_outcome=None, terminal=None)
    frozen_pending = tuple(pending)
    ledger.history_records = MethodType(  # type: ignore[method-assign]
        lambda self: frozen_pending,
        ledger,
    )
    with pytest.raises(ValueError, match="no durable STARTED"):
        build_final_pilot_ledger_manifest_v3(
            ledger,
            expected_ledger_digest=ledger.ledger_digest,
        )

    chained = list(records)
    assert chained[1].started is not None
    chained[1] = replace(
        chained[1],
        started=replace(
            chained[1].started,
            prerequisite_terminal_digest=digest("previous terminal"),
        ),
    )
    frozen_chained = tuple(chained)
    ledger.history_records = MethodType(  # type: ignore[method-assign]
        lambda self: frozen_chained,
        ledger,
    )
    with pytest.raises(ValueError, match="predecessor-dependent"):
        build_final_pilot_ledger_manifest_v3(
            ledger,
            expected_ledger_digest=ledger.ledger_digest,
        )


def test_manifest_rejects_unstable_history_wrong_ledger_and_tampering() -> None:
    ledger, records = synthetic_ledger()
    altered = list(records)
    assert altered[-1].terminal is not None
    altered[-1] = replace(
        altered[-1],
        terminal=replace(altered[-1].terminal, record_digest=digest("altered terminal")),
    )
    views = iter((records, tuple(altered)))
    ledger.history_records = MethodType(  # type: ignore[method-assign]
        lambda self: next(views),
        ledger,
    )
    with pytest.raises(ValueError, match="did not remain stable"):
        build_final_pilot_ledger_manifest_v3(
            ledger,
            expected_ledger_digest=ledger.ledger_digest,
        )

    ledger, _records = synthetic_ledger()
    with pytest.raises(ValueError, match="externally expected precommitment"):
        build_final_pilot_ledger_manifest_v3(
            ledger,
            expected_ledger_digest=digest("another ledger"),
        )
    manifest = build_final_pilot_ledger_manifest_v3(
        ledger,
        expected_ledger_digest=ledger.ledger_digest,
    )
    with pytest.raises(ValueError, match="entry universe differs"):
        replace(manifest, entry_universe_digest=digest("tampered entries"))
    with pytest.raises(ValueError, match="externally expected history"):
        verify_final_pilot_ledger_manifest_v3(
            ledger,
            manifest,
            expected_ledger_digest=ledger.ledger_digest,
            expected_history_digest=digest("another history"),
        )


def test_attempt_ledger_history_projection_uses_one_payload_free_snapshot(
    tmp_path: Path,
) -> None:
    ledger = AttemptLedger.create(tmp_path / "ledger", key=KEY, slots=(slot(0), slot(1)))
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    runtime_parent = tmp_path / "runtime"
    runtime_parent.mkdir()

    for index, commitment in enumerate(ledger.slots):
        runtime = ledger.allocate_runtime_directory(
            commitment.slot_id,
            runtime_parent=runtime_parent,
            repository_root=repository,
        )
        started = ledger.start(commitment.slot_id, runtime=runtime, dispatch=material(index))
        if index == 0:
            ledger.record_returned(
                started,
                ArtifactPayload(b'{"private":"payload"}', "application/json"),
            )
        else:
            ledger.record_failure(started, failure_code="test_failure")

    history = ledger.history_records()
    assert tuple(record.ordinal for record in history) == (0, 1)
    assert tuple(record.terminal.kind for record in history if record.terminal) == (
        OutcomeKind.RETURNED,
        OutcomeKind.FAILED,
    )
    assert "private" not in repr(history)
