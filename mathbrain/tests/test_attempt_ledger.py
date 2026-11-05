from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

import math_brain.attempt_ledger as ledger_module
from math_brain.attempt_ledger import (
    ArtifactPayload,
    AttemptLedger,
    AttemptRunnerFailed,
    DispatchMaterial,
    LedgerFormatError,
    LedgerIntegrityError,
    LedgerStateError,
    OutcomeKind,
    SlotBurnedError,
    SlotCommitment,
    SlotState,
    new_slot_id,
)


KEY = bytes(range(32))


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


def _material(index: int, *, prompt: str | None = None) -> DispatchMaterial:
    return DispatchMaterial(
        run_spec_digest=_digest(f"run-spec-{index}"),
        batch_digest=_digest(f"batch-{index}"),
        prompt=prompt if prompt is not None else f"Solve opaque batch {index}.\nReturn JSON.",
        output_schema_json=b'{"additionalProperties":false,"type":"object"}',
        request_policy_digest=_digest(f"policy-{index}"),
        dispatch_nonce=_digest(f"dispatch-{index}"),
    )


def _slot(index: int, *, prompt: str | None = None, key: bytes = KEY) -> SlotCommitment:
    return _material(index, prompt=prompt).seal(
        key=key,
        slot_id=f"slot_{index:032x}",
    )


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    runtime_parent = tmp_path / "runtime"
    runtime_parent.mkdir()
    return repository, runtime_parent


def _ledger(tmp_path: Path, *, count: int = 2, key: bytes = KEY) -> AttemptLedger:
    return AttemptLedger.create(
        tmp_path / "ledger",
        key=key,
        slots=tuple(_slot(index, key=key) for index in range(count)),
    )


def _start_first(
    ledger: AttemptLedger,
    tmp_path: Path,
    *,
    index: int = 0,
):
    repository, runtime_parent = _paths(tmp_path)
    slot_id = ledger.slots[index].slot_id
    runtime = ledger.allocate_runtime_directory(
        slot_id,
        runtime_parent=runtime_parent,
        repository_root=repository,
    )
    return ledger.start(slot_id, runtime=runtime, dispatch=_material(index))


def test_precommit_is_ordered_opaque_and_contains_no_raw_prompt_holdout_or_key(
    tmp_path: Path,
) -> None:
    sentinel = "PRIVATE-HOLDOUT-CANARY-DO-NOT-PERSIST"
    slots = (_slot(0, prompt=f"{sentinel}\nproof body"), _slot(1))
    ledger = AttemptLedger.create(tmp_path / "ledger", key=KEY, slots=slots)

    persisted = b"".join(path.read_bytes() for path in ledger.root.rglob("*\u002ejson"))
    assert sentinel.encode() not in persisted
    assert KEY not in persisted
    assert persisted.index(slots[0].slot_id.encode()) < persisted.index(slots[1].slot_id.encode())
    assert b"prompt_commitment" in persisted
    assert b"brain_on" not in persisted
    assert b"brain_off" not in persisted


def test_wrong_caller_key_cannot_open_and_no_key_material_is_stored(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    assert KEY not in (ledger.root / "precommit.json").read_bytes()
    with pytest.raises(LedgerIntegrityError, match="HMAC verification failed"):
        AttemptLedger.open(ledger.root, key=b"x" * 32)


def test_dispatch_material_is_recomputed_before_runtime_allocation_or_started(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path, count=1)
    repository, runtime_parent = _paths(tmp_path)
    slot_id = ledger.slots[0].slot_id
    wrong = replace(_material(0), prompt="different prompt")
    calls = 0

    def must_not_run(_runtime: Path, _dispatch: DispatchMaterial) -> ArtifactPayload:
        nonlocal calls
        calls += 1
        return ArtifactPayload(b'{"unexpected":true}')

    with pytest.raises(LedgerIntegrityError, match="differs from the precommitment"):
        ledger.execute_first_attempt(
            slot_id,
            runtime_parent=runtime_parent,
            repository_root=repository,
            dispatch=wrong,
            runner=must_not_run,
        )
    assert calls == 0
    assert list(runtime_parent.iterdir()) == []
    assert not list((ledger.root / "transitions").glob("*.started.json"))


def test_slots_must_start_in_exact_precommitted_order(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    repository, runtime_parent = _paths(tmp_path)
    first, second = (slot.slot_id for slot in ledger.slots)
    second_runtime = ledger.allocate_runtime_directory(
        second,
        runtime_parent=runtime_parent,
        repository_root=repository,
    )
    with pytest.raises(LedgerStateError, match="exact precommitted order"):
        ledger.start(second, runtime=second_runtime, dispatch=_material(1))

    first_runtime = ledger.allocate_runtime_directory(
        first,
        runtime_parent=runtime_parent,
        repository_root=repository,
    )
    ledger.start(first, runtime=first_runtime, dispatch=_material(0))
    ledger.start(second, runtime=second_runtime, dispatch=_material(1))
    assert ledger.state(first) is SlotState.STARTED
    assert ledger.state(second) is SlotState.STARTED


def test_start_after_binds_second_started_to_exact_preceding_returned_record(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    repository, runtime_parent = _paths(tmp_path)
    first, second = (slot.slot_id for slot in ledger.slots)
    first_runtime = ledger.allocate_runtime_directory(
        first,
        runtime_parent=runtime_parent,
        repository_root=repository,
    )
    first_started = ledger.start_after(
        first,
        runtime=first_runtime,
        dispatch=_material(0),
        prerequisite_terminal=None,
    )
    second_runtime = ledger.allocate_runtime_directory(
        second,
        runtime_parent=runtime_parent,
        repository_root=repository,
    )
    with pytest.raises(LedgerStateError, match="exact preceding RETURNED"):
        ledger.start_after(
            second,
            runtime=second_runtime,
            dispatch=_material(1),
            prerequisite_terminal=None,
        )
    assert ledger.started_attempt(second) is None

    first_terminal = ledger.record_returned(
        first_started,
        ArtifactPayload(b'{"reviewer":"a"}'),
    )
    forged = replace(first_terminal, record_digest=_digest("forged terminal"))
    with pytest.raises(LedgerStateError, match="exact preceding RETURNED"):
        ledger.start_after(
            second,
            runtime=second_runtime,
            dispatch=_material(1),
            prerequisite_terminal=forged,
        )
    second_started = ledger.start_after(
        second,
        runtime=second_runtime,
        dispatch=_material(1),
        prerequisite_terminal=first_terminal,
    )
    assert second_started.prerequisite_terminal_digest == first_terminal.record_digest

    reopened = AttemptLedger.open(ledger.root, key=KEY)
    assert (
        reopened.started_attempt(second).prerequisite_terminal_digest
        == first_terminal.record_digest
    )


def test_start_after_rejects_failed_predecessor_without_burning_second_slot(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    repository, runtime_parent = _paths(tmp_path)
    first, second = (slot.slot_id for slot in ledger.slots)
    first_runtime = ledger.allocate_runtime_directory(
        first,
        runtime_parent=runtime_parent,
        repository_root=repository,
    )
    first_started = ledger.start_after(
        first,
        runtime=first_runtime,
        dispatch=_material(0),
        prerequisite_terminal=None,
    )
    first_terminal = ledger.record_failure(first_started, failure_code="test_failure")
    second_runtime = ledger.allocate_runtime_directory(
        second,
        runtime_parent=runtime_parent,
        repository_root=repository,
    )
    with pytest.raises(LedgerStateError, match="exact preceding RETURNED"):
        ledger.start_after(
            second,
            runtime=second_runtime,
            dispatch=_material(1),
            prerequisite_terminal=first_terminal,
        )
    assert ledger.state(second) is SlotState.PENDING
    assert ledger.started_attempt(second) is None


def test_concurrent_start_uses_real_o_excl_and_has_exactly_one_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _ledger(tmp_path, count=1)
    repository, runtime_parent = _paths(tmp_path)
    slot_id = ledger.slots[0].slot_id
    runtime = ledger.allocate_runtime_directory(
        slot_id,
        runtime_parent=runtime_parent,
        repository_root=repository,
    )
    barrier = threading.Barrier(2)
    calls_lock = threading.Lock()
    snapshot_calls = 0
    original_snapshot = AttemptLedger._history_snapshot

    def synchronized_snapshot(instance: AttemptLedger):
        nonlocal snapshot_calls
        snapshot = original_snapshot(instance)
        with calls_lock:
            call_index = snapshot_calls
            snapshot_calls += 1
        if call_index < 2:
            barrier.wait(timeout=5)
        return snapshot

    monkeypatch.setattr(AttemptLedger, "_history_snapshot", synchronized_snapshot)

    def try_start() -> str:
        try:
            ledger.start(slot_id, runtime=runtime, dispatch=_material(0))
        except SlotBurnedError:
            return "burned"
        return "started"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _index: try_start(), range(2)))
    assert sorted(results) == ["burned", "started"]
    assert len(list((ledger.root / "transitions").glob("*.started.json"))) == 1
    assert ledger.state(slot_id) is SlotState.STARTED


def test_precommit_final_name_is_absent_until_staged_bytes_are_fsynced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    original_write = ledger_module._write_and_fsync

    def block_precommit(descriptor: int, raw: bytes) -> None:
        if b'"record_type":"PRECOMMITTED"' in raw:
            entered.set()
            assert release.wait(timeout=5)
        original_write(descriptor, raw)

    monkeypatch.setattr(ledger_module, "_write_and_fsync", block_precommit)
    root = tmp_path / "ledger"
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(AttemptLedger.create, root, key=KEY, slots=(_slot(0),))
        assert entered.wait(timeout=5)
        assert not (root / "precommit.json").exists()
        staged = list((root / "staging").glob("*.pending"))
        assert len(staged) == 1
        assert staged[0].stat().st_size == 0
        with pytest.raises(LedgerFormatError, match="cannot stat ledger record"):
            AttemptLedger.open(root, key=KEY)
        release.set()
        ledger = future.result(timeout=5)
    assert AttemptLedger.open(ledger.root, key=KEY).ledger_digest == ledger.ledger_digest


def test_started_final_name_is_absent_during_zero_length_staging_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _ledger(tmp_path, count=1)
    repository, runtime_parent = _paths(tmp_path)
    slot_id = ledger.slots[0].slot_id
    runtime = ledger.allocate_runtime_directory(
        slot_id,
        runtime_parent=runtime_parent,
        repository_root=repository,
    )
    entered = threading.Event()
    release = threading.Event()
    original_write = ledger_module._write_and_fsync
    blocked = False

    def block_started(descriptor: int, raw: bytes) -> None:
        nonlocal blocked
        if b'"record_type":"STARTED"' in raw and not blocked:
            blocked = True
            entered.set()
            assert release.wait(timeout=5)
        original_write(descriptor, raw)

    monkeypatch.setattr(ledger_module, "_write_and_fsync", block_started)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            ledger.start,
            slot_id,
            runtime=runtime,
            dispatch=_material(0),
        )
        assert entered.wait(timeout=5)
        final_path = ledger.root / "transitions" / f"000000-{slot_id}.started.json"
        assert not final_path.exists()
        staged = list((ledger.root / "staging").glob("*.pending"))
        assert len(staged) == 1
        assert staged[0].stat().st_size == 0
        assert ledger.state(slot_id) is SlotState.PENDING
        assert AttemptLedger.open(ledger.root, key=KEY).state(slot_id) is SlotState.PENDING
        report = ledger.recover_orphans()
        assert report.finalized == ()
        assert report.burned_without_outcome == ()
        winner = ledger.start(slot_id, runtime=runtime, dispatch=_material(0))
        release.set()
        with pytest.raises(SlotBurnedError, match="concurrently burned"):
            future.result(timeout=5)
    assert winner.slot_id == slot_id
    assert ledger.state(slot_id) is SlotState.STARTED


def test_failed_staging_unlink_cannot_damage_published_final_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _ledger(tmp_path, count=1)
    repository, runtime_parent = _paths(tmp_path)
    slot_id = ledger.slots[0].slot_id
    runtime = ledger.allocate_runtime_directory(
        slot_id,
        runtime_parent=runtime_parent,
        repository_root=repository,
    )
    original_unlink = Path.unlink

    def refuse_staging_unlink(path: Path, *args, **kwargs) -> None:
        if path.parent == ledger.root / "staging" and path.name.endswith(".pending"):
            raise PermissionError("simulated staging cleanup failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse_staging_unlink)
    started = ledger.start(slot_id, runtime=runtime, dispatch=_material(0))
    final_path = ledger.root / "transitions" / f"000000-{slot_id}.started.json"
    staged = list((ledger.root / "staging").glob("*.pending"))
    assert len(staged) == 1
    assert os.path.samefile(staged[0], final_path)
    final_raw = final_path.read_bytes()
    assert staged[0].read_bytes() == final_raw
    assert AttemptLedger.open(ledger.root, key=KEY).state(slot_id) is SlotState.STARTED

    monkeypatch.setattr(Path, "unlink", original_unlink)
    staged[0].unlink()
    assert final_path.read_bytes() == final_raw
    assert ledger.state(started.slot_id) is SlotState.STARTED


class _SimulatedProcessCrash(BaseException):
    pass


def test_started_is_fsynced_before_runner_and_crash_recovery_never_retries(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path, count=1)
    repository, runtime_parent = _paths(tmp_path)
    slot_id = ledger.slots[0].slot_id
    calls = 0

    def crashing_runner(runtime: Path, dispatch: DispatchMaterial) -> ArtifactPayload:
        nonlocal calls
        calls += 1
        assert runtime.is_dir()
        assert dispatch == _material(0)
        assert ledger.state(slot_id) is SlotState.STARTED
        raise _SimulatedProcessCrash

    with pytest.raises(_SimulatedProcessCrash):
        ledger.execute_first_attempt(
            slot_id,
            runtime_parent=runtime_parent,
            repository_root=repository,
            dispatch=_material(0),
            runner=crashing_runner,
        )
    assert calls == 1

    reopened = AttemptLedger.open(ledger.root, key=KEY)

    def must_not_run(_runtime: Path, _dispatch: DispatchMaterial) -> ArtifactPayload:
        nonlocal calls
        calls += 1
        return ArtifactPayload(b'{"unexpected":true}')

    with pytest.raises(SlotBurnedError):
        reopened.execute_first_attempt(
            slot_id,
            runtime_parent=runtime_parent,
            repository_root=repository,
            dispatch=_material(0),
            runner=must_not_run,
        )
    assert calls == 1
    report = reopened.recover_orphans()
    assert report.burned_without_outcome == (slot_id,)
    assert report.finalized == ()


def test_returned_object_is_durable_before_terminal_and_valid_orphan_recovers(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path, count=1)
    started = _start_first(ledger, tmp_path / "execution")
    stored = ledger.persist_returned(started, ArtifactPayload(b'{"answer":7}'))
    object_path = ledger.root / "objects" / "sha256" / f"{stored.object_digest[7:]}.json"
    returned_path = ledger.root / "transitions" / (f"000000-{started.slot_id}.returned.json")
    assert object_path.is_file()
    assert not returned_path.exists()
    assert ledger.state(started.slot_id) is SlotState.STARTED

    reopened = AttemptLedger.open(ledger.root, key=KEY)
    selected = reopened.selected_outcome(started.slot_id)
    assert selected is not None
    assert selected.object_digest == stored.object_digest
    assert reopened.load_selected_artifact(started.slot_id).content == b'{"answer":7}'
    report = reopened.recover_orphans()
    assert len(report.finalized) == 1
    assert report.finalized[0].kind is OutcomeKind.RETURNED
    assert report.finalized[0].object_digest == stored.object_digest
    assert reopened.state(started.slot_id) is SlotState.RETURNED


def test_failure_object_is_durable_before_failed_transition_and_recovers(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, count=1)
    started = _start_first(ledger, tmp_path / "execution")
    stored = ledger.persist_failure(started, failure_code="timeout")
    failed_path = ledger.root / "transitions" / f"000000-{started.slot_id}.failed.json"
    assert not failed_path.exists()

    report = AttemptLedger.open(ledger.root, key=KEY).recover_orphans()
    assert len(report.finalized) == 1
    assert report.finalized[0].kind is OutcomeKind.FAILED
    assert report.finalized[0].object_digest == stored.object_digest


def test_foreign_signed_orphan_is_rejected_not_finalized(tmp_path: Path) -> None:
    first = AttemptLedger.create(tmp_path / "first", key=KEY, slots=(_slot(0),))
    second = AttemptLedger.create(tmp_path / "second", key=KEY, slots=(_slot(0),))
    first_started = _start_first(first, tmp_path / "first-execution")
    _start_first(second, tmp_path / "second-execution")
    foreign = first.persist_returned(first_started, ArtifactPayload(b'{"answer":1}'))
    source = first.root / "objects" / "sha256" / f"{foreign.object_digest[7:]}.json"
    target = second.root / "objects" / "sha256" / source.name
    target.write_bytes(source.read_bytes())

    with pytest.raises(LedgerIntegrityError, match="another ledger"):
        second.recover_orphans()
    terminal = second.root / "transitions" / f"000000-{second.slots[0].slot_id}.returned.json"
    assert not terminal.exists()
    with pytest.raises(LedgerIntegrityError, match="another ledger"):
        second.state(second.slots[0].slot_id)


def test_forged_started_token_cannot_persist_an_outcome(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, count=1)
    started = _start_first(ledger, tmp_path / "execution")
    forged = replace(started, runtime_inode=started.runtime_inode + 1)
    with pytest.raises(LedgerStateError, match="does not match"):
        ledger.persist_returned(forged, ArtifactPayload(b'{"answer":1}'))


def test_one_started_attempt_cannot_persist_two_outcomes(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, count=1)
    started = _start_first(ledger, tmp_path / "execution")
    ledger.persist_returned(started, ArtifactPayload(b'{"answer":1}'))
    with pytest.raises(LedgerStateError, match="already won"):
        ledger.persist_returned(started, ArtifactPayload(b'{"answer":2}'))


def test_concurrent_different_outcomes_have_one_o_excl_winner_and_do_not_brick(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _ledger(tmp_path, count=1)
    started = _start_first(ledger, tmp_path / "execution")
    barrier = threading.Barrier(2)
    original_select = AttemptLedger._select_outcome

    def synchronized_select(instance, started_attempt, candidate):
        barrier.wait(timeout=5)
        return original_select(instance, started_attempt, candidate)

    monkeypatch.setattr(AttemptLedger, "_select_outcome", synchronized_select)

    def persist(answer: int) -> tuple[str, str | None]:
        try:
            stored = ledger.persist_returned(
                started,
                ArtifactPayload(f'{{"answer":{answer}}}'.encode("ascii")),
            )
        except LedgerStateError:
            return "lost", None
        return "won", stored.object_digest

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(persist, (1, 2)))
    assert sorted(result[0] for result in results) == ["lost", "won"]
    winning_digest = next(result[1] for result in results if result[0] == "won")
    assert winning_digest is not None
    assert len(list((ledger.root / "objects" / "sha256").glob("*.json"))) == 1
    assert len(list((ledger.root / "transitions").glob("*.outcome-selected.json"))) == 1

    reopened = AttemptLedger.open(ledger.root, key=KEY)
    selected = reopened.selected_outcome(started.slot_id)
    assert selected is not None
    assert selected.object_digest == winning_digest
    report = reopened.recover_orphans()
    assert len(report.finalized) == 1
    assert reopened.state(started.slot_id) is SlotState.RETURNED


def test_partial_selection_staging_is_invisible_and_loser_never_parses_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _ledger(tmp_path, count=1)
    started = _start_first(ledger, tmp_path / "execution")
    entered = threading.Event()
    release = threading.Event()
    original_write = ledger_module._write_and_fsync
    blocked = False

    def block_first_selection(descriptor: int, raw: bytes) -> None:
        nonlocal blocked
        if b'"record_type":"OUTCOME_SELECTED"' in raw and not blocked:
            blocked = True
            entered.set()
            assert release.wait(timeout=5)
        original_write(descriptor, raw)

    monkeypatch.setattr(ledger_module, "_write_and_fsync", block_first_selection)
    with ThreadPoolExecutor(max_workers=1) as executor:
        loser = executor.submit(
            ledger.persist_returned,
            started,
            ArtifactPayload(b'{"answer":1}'),
        )
        assert entered.wait(timeout=5)
        selection_path = (
            ledger.root / "transitions" / (f"000000-{started.slot_id}.outcome-selected.json")
        )
        assert not selection_path.exists()
        staged = list((ledger.root / "staging").glob("*.pending"))
        assert len(staged) == 1
        assert staged[0].stat().st_size == 0
        assert ledger.state(started.slot_id) is SlotState.STARTED
        assert AttemptLedger.open(ledger.root, key=KEY).state(started.slot_id) is SlotState.STARTED
        assert ledger.recover_orphans().burned_without_outcome == (started.slot_id,)
        winner = ledger.persist_returned(started, ArtifactPayload(b'{"answer":2}'))
        release.set()
        with pytest.raises(LedgerStateError, match="already won"):
            loser.result(timeout=5)
    assert len(list((ledger.root / "objects" / "sha256").glob("*.json"))) == 1
    assert ledger.selected_outcome(started.slot_id) == winner
    assert AttemptLedger.open(ledger.root, key=KEY).selected_outcome(started.slot_id) == winner


def test_object_and_terminal_final_names_are_never_partially_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _ledger(tmp_path, count=1)
    started = _start_first(ledger, tmp_path / "execution")
    entered = threading.Event()
    release = threading.Event()
    original_write = ledger_module._write_and_fsync
    target = b'"record_type":"OUTCOME_OBJECT"'
    phase_blocked = False

    def block_target(descriptor: int, raw: bytes) -> None:
        nonlocal phase_blocked
        if target in raw and not phase_blocked:
            phase_blocked = True
            entered.set()
            assert release.wait(timeout=5)
        original_write(descriptor, raw)

    monkeypatch.setattr(ledger_module, "_write_and_fsync", block_target)
    with ThreadPoolExecutor(max_workers=1) as executor:
        object_future = executor.submit(
            ledger.persist_returned,
            started,
            ArtifactPayload(b'{"answer":3}'),
        )
        assert entered.wait(timeout=5)
        assert not list((ledger.root / "objects" / "sha256").glob("*.json"))
        assert ledger.state(started.slot_id) is SlotState.STARTED
        assert AttemptLedger.open(ledger.root, key=KEY).state(started.slot_id) is SlotState.STARTED
        assert ledger.recover_orphans().burned_without_outcome == (started.slot_id,)
        with pytest.raises(LedgerStateError, match="not yet durable"):
            ledger.selected_outcome(started.slot_id)
        winner = ledger.persist_returned(
            started,
            ArtifactPayload(b'{"answer":3}'),
        )
        release.set()
        loser_result = object_future.result(timeout=5)
    assert loser_result == winner
    stored = winner

    entered.clear()
    release.clear()
    phase_blocked = False
    target = b'"record_type":"RETURNED"'
    with ThreadPoolExecutor(max_workers=1) as executor:
        terminal_future = executor.submit(ledger.finalize, stored)
        assert entered.wait(timeout=5)
        assert not list((ledger.root / "transitions").glob("*.returned.json"))
        assert ledger.state(started.slot_id) is SlotState.STARTED
        assert ledger.load_selected_artifact(started.slot_id).content == b'{"answer":3}'
        recovered = AttemptLedger.open(ledger.root, key=KEY).recover_orphans()
        assert len(recovered.finalized) == 1
        recovered_terminal = recovered.finalized[0]
        terminal_path = ledger.root / "transitions" / (f"000000-{started.slot_id}.returned.json")
        recovered_raw = terminal_path.read_bytes()
        release.set()
        terminal = terminal_future.result(timeout=5)
    assert terminal == recovered_terminal
    assert terminal_path.read_bytes() == recovered_raw
    assert ledger.state(started.slot_id) is SlotState.RETURNED


@pytest.mark.parametrize("publish_terminal", [False, True])
def test_history_validation_retries_a_mixed_atomic_publication_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    publish_terminal: bool,
) -> None:
    ledger = _ledger(tmp_path, count=1)
    started = _start_first(ledger, tmp_path / "execution")
    entered = threading.Event()
    release = threading.Event()
    original_load_selection = AttemptLedger._load_selection
    blocked = False

    def block_after_absent_selection(instance, ordinal, slot_id, started_attempt):
        nonlocal blocked
        selection = original_load_selection(instance, ordinal, slot_id, started_attempt)
        if selection is None and not blocked:
            blocked = True
            entered.set()
            assert release.wait(timeout=5)
        return selection

    monkeypatch.setattr(AttemptLedger, "_load_selection", block_after_absent_selection)
    with ThreadPoolExecutor(max_workers=1) as executor:
        reader = executor.submit(ledger.state, started.slot_id)
        assert entered.wait(timeout=5)
        try:
            if publish_terminal:
                ledger.record_returned(started, ArtifactPayload(b'{"answer":4}'))
            else:
                ledger.persist_returned(started, ArtifactPayload(b'{"answer":4}'))
        finally:
            release.set()
        expected = SlotState.RETURNED if publish_terminal else SlotState.STARTED
        assert reader.result(timeout=5) is expected
    reopened = AttemptLedger.open(ledger.root, key=KEY)
    assert reopened.state(started.slot_id) is expected


def test_history_snapshot_retries_when_started_was_missed_before_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _ledger(tmp_path, count=1)
    repository, runtime_parent = _paths(tmp_path)
    slot_id = ledger.slots[0].slot_id
    runtime = ledger.allocate_runtime_directory(
        slot_id,
        runtime_parent=runtime_parent,
        repository_root=repository,
    )
    entered = threading.Event()
    release = threading.Event()
    original_load_started = AttemptLedger._load_started
    blocked = False

    def block_after_absent_started(instance, ordinal, selected_slot_id):
        nonlocal blocked
        started = original_load_started(instance, ordinal, selected_slot_id)
        if selected_slot_id == slot_id and started is None and not blocked:
            blocked = True
            entered.set()
            assert release.wait(timeout=5)
        return started

    monkeypatch.setattr(AttemptLedger, "_load_started", block_after_absent_started)
    with ThreadPoolExecutor(max_workers=1) as executor:
        reader = executor.submit(ledger.state, slot_id)
        assert entered.wait(timeout=5)
        try:
            started = ledger.start(slot_id, runtime=runtime, dispatch=_material(0))
            ledger.persist_returned(started, ArtifactPayload(b'{"answer":5}'))
        finally:
            release.set()
        assert reader.result(timeout=5) is SlotState.STARTED
    assert AttemptLedger.open(ledger.root, key=KEY).state(slot_id) is SlotState.STARTED


def test_quiescent_history_validates_once_and_ignores_abandoned_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _ledger(tmp_path, count=1)
    (ledger.root / "staging" / "abandoned.pending").write_bytes(b"partial private evidence")
    original_validate_once = AttemptLedger._validate_history_once
    calls = 0

    def count_validate_once(instance):
        nonlocal calls
        calls += 1
        return original_validate_once(instance)

    monkeypatch.setattr(AttemptLedger, "_validate_history_once", count_validate_once)
    assert ledger.state(ledger.slots[0].slot_id) is SlotState.PENDING
    assert calls == 1


def test_history_snapshot_retry_is_bounded_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _ledger(tmp_path, count=1)
    inventory_calls = 0
    validation_calls = 0
    original_validate_once = AttemptLedger._validate_history_once

    def changing_inventory(_instance):
        nonlocal inventory_calls
        inventory_calls += 1
        return (("transitions", (str(inventory_calls),)), ("objects", ()))

    def count_validate_once(instance):
        nonlocal validation_calls
        validation_calls += 1
        return original_validate_once(instance)

    monkeypatch.setattr(AttemptLedger, "_history_inventory", changing_inventory)
    monkeypatch.setattr(AttemptLedger, "_validate_history_once", count_validate_once)
    with pytest.raises(LedgerStateError, match="did not stabilize"):
        ledger.state(ledger.slots[0].slot_id)
    assert validation_calls == ledger_module.MAX_HISTORY_SNAPSHOT_ATTEMPTS
    assert inventory_calls == 2 * ledger_module.MAX_HISTORY_SNAPSHOT_ATTEMPTS


def test_quiescent_invalid_history_fails_on_first_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _ledger(tmp_path, count=1)
    validation_calls = 0

    def reject_once(_instance):
        nonlocal validation_calls
        validation_calls += 1
        raise LedgerIntegrityError("stable invalid history")

    monkeypatch.setattr(AttemptLedger, "_validate_history_once", reject_once)
    with pytest.raises(LedgerIntegrityError, match="stable invalid history"):
        ledger.state(ledger.slots[0].slot_id)
    assert validation_calls == 1


def test_crash_after_selection_before_object_burns_without_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _ledger(tmp_path, count=1)
    started = _start_first(ledger, tmp_path / "execution")
    original_write = ledger_module._write_exclusive

    def crash_before_object(
        path: Path,
        raw: bytes,
        *,
        staging_directory: Path,
    ) -> None:
        if path.parent.name == "sha256":
            raise _SimulatedProcessCrash
        original_write(path, raw, staging_directory=staging_directory)

    monkeypatch.setattr(ledger_module, "_write_exclusive", crash_before_object)
    with pytest.raises(_SimulatedProcessCrash):
        ledger.persist_returned(started, ArtifactPayload(b'{"answer":1}'))
    monkeypatch.setattr(ledger_module, "_write_exclusive", original_write)

    reopened = AttemptLedger.open(ledger.root, key=KEY)
    report = reopened.recover_orphans()
    assert report.burned_without_outcome == (started.slot_id,)
    assert report.finalized == ()
    assert reopened.state(started.slot_id) is SlotState.STARTED
    assert not list((ledger.root / "objects" / "sha256").glob("*.json"))
    assert not list((ledger.root / "transitions").glob("*.returned.json"))


def test_regular_fake_runner_failure_is_sanitized_durable_and_not_retried(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path, count=1)
    repository, runtime_parent = _paths(tmp_path)
    slot_id = ledger.slots[0].slot_id
    private_message = "PRIVATE-HOLDOUT-ANSWER-IN-EXCEPTION"
    calls = 0

    def failing_runner(_runtime: Path, _dispatch: DispatchMaterial) -> ArtifactPayload:
        nonlocal calls
        calls += 1
        raise ValueError(private_message)

    with pytest.raises(AttemptRunnerFailed) as caught:
        ledger.execute_first_attempt(
            slot_id,
            runtime_parent=runtime_parent,
            repository_root=repository,
            dispatch=_material(0),
            runner=failing_runner,
        )
    assert caught.value.terminal.kind is OutcomeKind.FAILED
    assert ledger.state(slot_id) is SlotState.FAILED
    assert calls == 1
    assert private_message.encode() not in b"".join(
        path.read_bytes() for path in ledger.root.rglob("*.json")
    )
    with pytest.raises(SlotBurnedError):
        ledger.execute_first_attempt(
            slot_id,
            runtime_parent=runtime_parent,
            repository_root=repository,
            dispatch=_material(0),
            runner=failing_runner,
        )
    assert calls == 1


def test_successful_fake_runner_runs_once_and_returns_content_address(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, count=1)
    repository, runtime_parent = _paths(tmp_path)
    slot_id = ledger.slots[0].slot_id
    calls = 0

    def fake_runner(runtime: Path, dispatch: DispatchMaterial) -> ArtifactPayload:
        nonlocal calls
        calls += 1
        assert re.fullmatch(r"run_[0-9a-f]{32}", runtime.name)
        assert dispatch == _material(0)
        return ArtifactPayload(b'{"answer":42,"valid":true}')

    terminal = ledger.execute_first_attempt(
        slot_id,
        runtime_parent=runtime_parent,
        repository_root=repository,
        dispatch=_material(0),
        runner=fake_runner,
    )
    assert calls == 1
    assert terminal.kind is OutcomeKind.RETURNED
    assert terminal.object_digest.startswith("sha256:")
    assert ledger.state(slot_id) is SlotState.RETURNED


def test_runtime_directories_are_opaque_empty_and_outside_git(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, count=1)
    repository, runtime_parent = _paths(tmp_path)
    inside = repository / "runtime"
    inside.mkdir()
    slot_id = ledger.slots[0].slot_id
    with pytest.raises(ValueError, match="outside every Git repository"):
        ledger.allocate_runtime_directory(
            slot_id,
            runtime_parent=inside,
            repository_root=repository,
        )

    runtime = ledger.allocate_runtime_directory(
        slot_id,
        runtime_parent=runtime_parent,
        repository_root=repository,
    )
    assert re.fullmatch(r"run_[0-9a-f]{32}", runtime.path.name)
    assert slot_id not in runtime.path.name
    assert list(runtime.path.iterdir()) == []
    ledger.start(slot_id, runtime=runtime, dispatch=_material(0))
    started_raw = next((ledger.root / "transitions").glob("*.started.json")).read_bytes()
    assert str(runtime_parent).encode() not in started_raw
    assert str(repository).encode() not in started_raw


def test_runtime_parent_inside_ledger_is_rejected_before_it_can_corrupt_layout(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path, count=1)
    repository, _runtime_parent = _paths(tmp_path)
    slot_id = ledger.slots[0].slot_id
    with pytest.raises(ValueError, match="outside the ledger root"):
        ledger.allocate_runtime_directory(
            slot_id,
            runtime_parent=ledger.root / "objects",
            repository_root=repository,
        )
    AttemptLedger.open(ledger.root, key=KEY)


def test_runtime_directory_must_remain_empty_and_keep_its_identity(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, count=1)
    repository, runtime_parent = _paths(tmp_path)
    slot_id = ledger.slots[0].slot_id
    runtime = ledger.allocate_runtime_directory(
        slot_id,
        runtime_parent=runtime_parent,
        repository_root=repository,
    )
    (runtime.path / "unexpected.txt").write_text("x", encoding="utf-8")
    with pytest.raises(LedgerStateError, match="must be empty"):
        ledger.start(slot_id, runtime=runtime, dispatch=_material(0))


@pytest.mark.parametrize(
    "raw",
    [
        b'{ "answer": 1 }',
        b'{"answer":1}\n',
        b'{"answer":NaN}',
        b'{"answer":1,"answer":2}',
    ],
)
def test_json_artifacts_must_use_exact_strict_canonical_json(raw: bytes) -> None:
    with pytest.raises((LedgerFormatError, ValueError)):
        ArtifactPayload(raw)


def test_canonical_json_artifact_may_contain_finite_evaluation_numbers() -> None:
    payload = ArtifactPayload(b'{"elapsed_seconds":1.25,"score":0.0}')
    assert payload.content == b'{"elapsed_seconds":1.25,"score":0.0}'


def test_canonical_json_artifact_allows_empty_values_but_not_empty_keys() -> None:
    payload = ArtifactPayload(b'{"result":{"thread":{"preview":""}}}')
    assert payload.content == b'{"result":{"thread":{"preview":""}}}'
    with pytest.raises(LedgerFormatError, match="must contain 1"):
        ArtifactPayload(b'{"":1}')


def test_noncanonical_transition_file_is_detected_before_trust(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, count=1)
    precommit = ledger.root / "precommit.json"
    precommit.write_bytes(precommit.read_bytes() + b"\n")
    with pytest.raises(LedgerFormatError, match="exact canonical encoding"):
        AttemptLedger.open(ledger.root, key=KEY)


def test_boolean_schema_version_cannot_alias_integer_one(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, count=1)
    precommit = ledger.root / "precommit.json"
    value = json.loads(precommit.read_text(encoding="ascii"))
    value["schema_version"] = True
    precommit.write_text(
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
        encoding="ascii",
    )
    with pytest.raises(LedgerFormatError, match="unsupported ledger record schema"):
        AttemptLedger.open(ledger.root, key=KEY)


def test_missing_layout_directory_fails_closed_instead_of_recreating_history(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path, count=1)
    (ledger.root / "transitions").rmdir()
    with pytest.raises(LedgerFormatError, match="not a real directory"):
        AttemptLedger.open(ledger.root, key=KEY)
    assert not (ledger.root / "transitions").exists()


def test_all_record_creation_uses_o_excl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_open = os.open
    creation_flags: list[int] = []

    def observing_open(path: os.PathLike[str] | str, flags: int, mode: int = 0o777) -> int:
        if flags & os.O_CREAT:
            creation_flags.append(flags)
        return real_open(path, flags, mode)

    monkeypatch.setattr(ledger_module.os, "open", observing_open)
    ledger = _ledger(tmp_path, count=1)
    started = _start_first(ledger, tmp_path / "execution")
    ledger.record_returned(started, ArtifactPayload(b'{"answer":1}'))
    assert len(creation_flags) >= 4
    assert all(flags & os.O_EXCL for flags in creation_flags)


def test_each_record_is_fsynced_before_transition_returns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_write = ledger_module._write_and_fsync
    fsynced_record_types: list[str] = []

    def observing_write(descriptor: int, raw: bytes) -> None:
        original_write(descriptor, raw)
        value = json.loads(raw.decode("ascii"))
        fsynced_record_types.append(value["body"]["record_type"])

    monkeypatch.setattr(ledger_module, "_write_and_fsync", observing_write)
    ledger = _ledger(tmp_path, count=1)
    started = _start_first(ledger, tmp_path / "execution")
    ledger.record_returned(started, ArtifactPayload(b'{"answer":1}'))
    assert fsynced_record_types == [
        "PRECOMMITTED",
        "STARTED",
        "OUTCOME_SELECTED",
        "OUTCOME_OBJECT",
        "RETURNED",
    ]


def test_terminal_finalization_is_idempotent_without_rewriting(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, count=1)
    started = _start_first(ledger, tmp_path / "execution")
    stored = ledger.persist_returned(started, ArtifactPayload(b'{"answer":1}'))
    first = ledger.finalize(stored)
    path = ledger.root / "transitions" / f"000000-{started.slot_id}.returned.json"
    original = path.read_bytes()
    second = ledger.finalize(stored)
    assert second == first
    assert path.read_bytes() == original


def test_slot_id_generator_is_opaque_and_condition_free() -> None:
    generated = {new_slot_id() for _ in range(32)}
    assert len(generated) == 32
    assert all(re.fullmatch(r"slot_[0-9a-f]{32}", value) for value in generated)
    assert all("brain" not in value and "pair" not in value for value in generated)
