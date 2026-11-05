from __future__ import annotations

import copy
import hashlib
import inspect
import json
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

import math_brain.hard_blind_pilot_v1 as pilot_module
from math_brain.attempt_ledger import (
    ArtifactPayload,
    AttemptLedger,
    AttemptRunnerFailed,
    DispatchMaterial,
    SlotBurnedError,
    SlotState,
)
from math_brain.codex_runner import ExecutableProvenance
from math_brain.hard_blind_pilot_v1 import (
    HARD_BLIND_PILOT_HISTORICAL_SAMPLE_DIGEST_V1,
    HARD_BLIND_PILOT_ORIGINAL_CASE_IDS_V1,
    HARD_BLIND_PILOT_SAMPLE_COMMITMENT_V1,
    HARD_BLIND_PRIVATE_ARTIFACT_MEDIA_TYPE_V1,
    BoundHardBlindPilotV1,
    HardBlindPilotAttemptStatusV1,
    HardBlindPilotCaseResultV1,
    HardBlindPilotRunPolicyV1,
    HardBlindPilotScoringV1,
    assert_safe_hard_blind_public_payload_v1,
    bind_hard_blind_pilot_ledger_v1,
    capture_hard_blind_pilot_run_policy_v1,
    create_hard_blind_pilot_ledger_v1,
    execute_hard_blind_pilot_v1,
    hard_blind_pilot_balance_v1,
    hard_blind_pilot_progress_v1,
    hard_blind_solver_output_schema_json_v1,
    hard_blind_solver_output_schema_v1,
    hard_blind_solver_prompt_v1,
    load_hard_blind_private_state_v1,
    open_hard_blind_pilot_ledger_v1,
    prepare_hard_blind_pilot_v1,
    save_hard_blind_private_state_v1,
    score_hard_blind_pilot_v1,
)
from math_brain.hard_challenge_v2 import (
    ANSWER_SCHEMA_V2,
    ANSWER_SCHEMA_VERSION_V2,
    SolverOutputV2,
    generate_public_calibration_bank_v2,
)

ROOT = Path(__file__).parents[1]
SOURCE_REVISION = "a" * 40


@pytest.fixture(scope="module")
def run_policy() -> HardBlindPilotRunPolicyV1:
    return capture_hard_blind_pilot_run_policy_v1(
        source_revision=SOURCE_REVISION,
        executable_path=sys.executable,
    )


@pytest.fixture(scope="module")
def prepared(run_policy: HardBlindPilotRunPolicyV1):
    return prepare_hard_blind_pilot_v1(run_policy)


@pytest.fixture(scope="module")
def pristine_ledger(prepared, tmp_path_factory: pytest.TempPathFactory) -> AttemptLedger:
    root = tmp_path_factory.mktemp("hard-blind-pristine-ledger")
    return AttemptLedger.create(
        root / "ledger",
        key=prepared.private_material.ledger_key,
        slots=prepared.commitments,
    )


@pytest.fixture(scope="module")
def bound(prepared, pristine_ledger: AttemptLedger) -> BoundHardBlindPilotV1:
    return BoundHardBlindPilotV1(prepared, pristine_ledger.ledger_digest)


@pytest.fixture(scope="module")
def public_payload(bound: BoundHardBlindPilotV1) -> dict:
    return bound.public_preregistration_payload()


def test_fixed_sample_uses_the_historical_lf_without_final_lf_digest() -> None:
    raw = "\n".join(HARD_BLIND_PILOT_ORIGINAL_CASE_IDS_V1).encode("ascii")
    assert len(HARD_BLIND_PILOT_ORIGINAL_CASE_IDS_V1) == 64
    assert len(set(HARD_BLIND_PILOT_ORIGINAL_CASE_IDS_V1)) == 64
    assert not raw.endswith(b"\n")
    assert "sha256:" + hashlib.sha256(raw).hexdigest() == (
        HARD_BLIND_PILOT_HISTORICAL_SAMPLE_DIGEST_V1
    )
    assert HARD_BLIND_PILOT_HISTORICAL_SAMPLE_DIGEST_V1 == (
        "sha256:83b7d57feae92f046feaff6b5428f58861800bc996e33bc11a178d68690cef2a"
    )
    canonical_json_digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                list(HARD_BLIND_PILOT_ORIGINAL_CASE_IDS_V1),
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    assert canonical_json_digest != HARD_BLIND_PILOT_HISTORICAL_SAMPLE_DIGEST_V1
    assert HARD_BLIND_PILOT_SAMPLE_COMMITMENT_V1 not in {
        canonical_json_digest,
        HARD_BLIND_PILOT_HISTORICAL_SAMPLE_DIGEST_V1,
    }


def test_fixed_sample_has_every_predeclared_exact_balance() -> None:
    balance = hard_blind_pilot_balance_v1()
    assert balance["validated_case_count"] == 64
    assert set(balance["domains"].values()) == {16}
    assert set(balance["families"].values()) == {16}
    assert set(balance["templates"].values()) == {16}
    assert set(balance["fault_variants"].values()) == {32}
    assert set(balance["generator_cells"].values()) == {32}
    assert set(balance["fault_lines"].values()) == {8}


def test_solver_schema_and_prompt_are_exact_answer_free_views() -> None:
    schema = hard_blind_solver_output_schema_v1()
    schema_json = hard_blind_solver_output_schema_json_v1()
    assert json.loads(schema_json) == schema
    assert (
        schema_json
        == json.dumps(
            schema,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema"] == {"const": ANSWER_SCHEMA_V2}
    assert schema["properties"]["schema_version"] == {"const": ANSWER_SCHEMA_VERSION_V2}
    assert schema["properties"]["repair"]["properties"]["target"] == {"const": "value"}

    case = generate_public_calibration_bank_v2()[0]
    prompt = hard_blind_solver_prompt_v1(case)
    assert case.prompt in prompt
    assert "\n".join(case.trace(faulty=True)) in prompt
    assert case.case_id not in prompt
    assert case.repair_replacement not in prompt
    assert "Return one JSON object" in prompt
    assert SolverOutputV2.from_dict(case.expected_solver_output.to_dict()) == (
        case.expected_solver_output
    )


def test_run_policy_pins_requested_runtime_and_omits_executable_path(
    run_policy: HardBlindPilotRunPolicyV1,
) -> None:
    payload = run_policy.public_payload()
    assert payload["requested_model"] == "gpt-5.6-sol"
    assert payload["requested_provider"] == "openai"
    assert payload["requested_effort"] == "ultra"
    assert payload["timeout_seconds"] == 180
    assert payload["max_workers"] == 1
    assert set(payload["executable"]) == {"sha256_digest", "size_bytes"}
    assert run_policy.executable_provenance.canonical_path not in repr(payload)
    assert payload["command_policy_digest"].startswith("sha256:")
    assert payload["process_isolation_policy_digest"].startswith("sha256:")
    assert payload["request_policy_digest"].startswith("sha256:")

    with pytest.raises(ValueError, match="fixed to one worker"):
        replace(run_policy, max_workers=2)
    with pytest.raises(ValueError, match="exactly 180"):
        replace(run_policy, timeout_seconds=181)


def test_public_payload_is_aggregate_only_and_adversarially_allowlisted(
    prepared,
    bound: BoundHardBlindPilotV1,
    public_payload: dict,
) -> None:
    payload = public_payload
    assert_safe_hard_blind_public_payload_v1(payload, bound=bound)
    rendered = json.dumps(payload, sort_keys=True)
    assert prepared.policy.executable_provenance.canonical_path not in rendered
    assert prepared.private_material.selection_seed not in rendered
    assert prepared.private_material.case_id_key.hex() not in rendered
    assert prepared.private_material.ledger_key.hex() not in rendered
    assert all(case_id not in rendered for case_id in HARD_BLIND_PILOT_ORIGINAL_CASE_IDS_V1)
    for forbidden in (
        "case_id_key",
        "dispatch_nonce",
        "executable_path",
        "fault_rank",
        "manifest",
        "selection_seed",
        "slot_id",
        "stable_case_id",
    ):
        assert forbidden not in rendered
    assert payload["model_identity_verified"] is False
    assert payload["calibration_public_commitment_digest"] == (
        prepared.preregistration.public_commitment_digest()
    )
    assert "bridge" not in rendered.lower()

    unknown = copy.deepcopy(payload)
    unknown["slots"] = []
    with pytest.raises(ValueError, match="fields differ"):
        assert_safe_hard_blind_public_payload_v1(unknown, bound=bound)

    path_leak = copy.deepcopy(payload)
    path_leak["run_policy"]["executable_path"] = "not-public"
    with pytest.raises(ValueError, match="fields differ"):
        assert_safe_hard_blind_public_payload_v1(path_leak, bound=bound)

    changed_model = copy.deepcopy(payload)
    changed_model["run_policy"]["requested_model"] = prepared.private_material.selection_seed
    with pytest.raises(ValueError, match="requested runtime"):
        assert_safe_hard_blind_public_payload_v1(changed_model, bound=bound)

    changed_balance = copy.deepcopy(payload)
    changed_balance["balance"]["domains"] = {"aggregate": prepared.private_material.selection_seed}
    with pytest.raises(ValueError, match="public balance"):
        assert_safe_hard_blind_public_payload_v1(changed_balance, bound=bound)

    changed_threshold = copy.deepcopy(payload)
    changed_threshold["thresholds"]["reject_above"] = 0.95
    with pytest.raises(ValueError, match="strict pilot gates"):
        assert_safe_hard_blind_public_payload_v1(changed_threshold, bound=bound)

    digest_exfiltration = copy.deepcopy(payload)
    digest_exfiltration["attempt_universe_commitment"] = (
        "sha256:" + prepared.private_material.ledger_key.hex()
    )
    with pytest.raises(ValueError, match="exact bound preregistration"):
        assert_safe_hard_blind_public_payload_v1(digest_exfiltration, bound=bound)

    revision_exfiltration = copy.deepcopy(payload)
    revision_exfiltration["source_revision"] = prepared.private_material.selection_seed
    with pytest.raises(ValueError, match="exact bound preregistration"):
        assert_safe_hard_blind_public_payload_v1(revision_exfiltration, bound=bound)

    class DictSubclass(dict):
        pass

    with pytest.raises(ValueError, match="exact object"):
        assert_safe_hard_blind_public_payload_v1(DictSubclass(payload), bound=bound)


def test_every_slot_hmac_binds_raw_prompt_schema_policy_and_nonce(prepared) -> None:
    assert len(prepared.slots) == 64
    assert len({slot.slot_id for slot in prepared.slots}) == 64
    assert len({slot.dispatch.dispatch_nonce for slot in prepared.slots}) == 64
    slot = prepared.slots[0]
    key = prepared.private_material.ledger_key
    assert slot.dispatch.seal(key=key, slot_id=slot.slot_id) == slot.commitment

    changed_prompt = replace(slot.dispatch, prompt=slot.dispatch.prompt + " altered")
    assert changed_prompt.seal(key=key, slot_id=slot.slot_id) != slot.commitment
    changed_schema = DispatchMaterial(
        run_spec_digest=slot.dispatch.run_spec_digest,
        batch_digest=slot.dispatch.batch_digest,
        prompt=slot.dispatch.prompt,
        output_schema_json=b'{"type":"object"}',
        request_policy_digest=slot.dispatch.request_policy_digest,
        dispatch_nonce=slot.dispatch.dispatch_nonce,
    )
    assert changed_schema.seal(key=key, slot_id=slot.slot_id) != slot.commitment


def test_published_ledger_digest_rejects_a_second_initialized_root(
    prepared,
    bound: BoundHardBlindPilotV1,
    public_payload: dict,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    root = tmp_path_factory.mktemp("hard-blind-second-ledger")
    second = AttemptLedger.create(
        root / "ledger",
        key=prepared.private_material.ledger_key,
        slots=prepared.commitments,
    )
    assert second.ledger_digest != bound.attempt_ledger_digest
    with pytest.raises(ValueError, match="published ledger digest"):
        bound.validate_ledger(second)

    substituted = copy.deepcopy(public_payload)
    substituted["attempt_ledger_digest"] = second.ledger_digest
    with pytest.raises(ValueError, match="exact bound preregistration"):
        assert_safe_hard_blind_public_payload_v1(substituted, bound=bound)


def test_binding_refuses_a_ledger_beneath_a_git_repository(
    prepared,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path_factory.mktemp("hard-blind-inside-git")
    repository = root / "repository"
    private_parent = repository / "private"
    (repository / ".git").mkdir(parents=True)
    private_parent.mkdir()
    monkeypatch.setattr(
        pilot_module.PreparedHardBlindPilotV1,
        "validate_current",
        lambda _self: None,
    )
    wrapper_target = private_parent / "wrapper-ledger"
    with pytest.raises(ValueError, match="outside every Git repository"):
        create_hard_blind_pilot_ledger_v1(prepared, wrapper_target)
    assert not wrapper_target.exists()

    ledger = AttemptLedger.create(
        private_parent / "ledger",
        key=prepared.private_material.ledger_key,
        slots=prepared.commitments,
    )
    with pytest.raises(ValueError, match="outside every Git repository"):
        open_hard_blind_pilot_ledger_v1(prepared, ledger.root)
    with pytest.raises(ValueError, match="outside every Git repository"):
        bind_hard_blind_pilot_ledger_v1(prepared, ledger)


def test_private_state_is_canonical_o_excl_and_fully_reconstructed(
    prepared,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    private_root = tmp_path_factory.mktemp("hard-blind-private")
    state_path = private_root / "pilot-state.json"
    saved = save_hard_blind_private_state_v1(state_path, prepared)
    raw = saved.read_bytes()
    assert (
        raw
        == json.dumps(
            json.loads(raw),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )
    assert prepared.private_material.ledger_key.hex().encode() in raw
    restored = load_hard_blind_private_state_v1(saved)
    assert restored.policy == prepared.policy
    assert restored.private_material == prepared.private_material
    assert restored.run_spec_digest == prepared.run_spec_digest
    assert restored.slots == prepared.slots

    with pytest.raises(FileExistsError):
        save_hard_blind_private_state_v1(state_path, prepared)

    duplicate = raw.replace(b'{"body":', b'{"body":null,"body":', 1)
    duplicate_path = private_root / "duplicate.json"
    duplicate_path.write_bytes(duplicate)
    with pytest.raises(ValueError, match="invalid JSON"):
        load_hard_blind_private_state_v1(duplicate_path)


def test_private_state_load_rejects_a_file_symlink_before_resolution(
    prepared,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    private_root = tmp_path_factory.mktemp("hard-blind-private-symlink")
    target = save_hard_blind_private_state_v1(private_root / "target.json", prepared)
    link = private_root / "link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("file symlink creation is unavailable")
    with pytest.raises(ValueError, match="non-symlink"):
        load_hard_blind_private_state_v1(link)


def test_burned_started_and_invalid_artifact_keep_all_64_in_denominator(
    prepared,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    root = tmp_path_factory.mktemp("hard-blind-ledger")
    ledger = AttemptLedger.create(
        root / "ledger",
        key=prepared.private_material.ledger_key,
        slots=prepared.commitments,
    )
    runtime_parent = root / "runtime"
    runtime_parent.mkdir()
    bound = BoundHardBlindPilotV1(prepared, ledger.ledger_digest)
    first = prepared.slots[0]
    terminal = ledger.execute_first_attempt(
        first.slot_id,
        runtime_parent=runtime_parent,
        repository_root=ROOT,
        dispatch=first.dispatch,
        runner=lambda _runtime, _dispatch: ArtifactPayload(
            b"not-a-codex-artifact",
            HARD_BLIND_PRIVATE_ARTIFACT_MEDIA_TYPE_V1,
        ),
    )
    assert terminal.slot_id == first.slot_id
    assert ledger.state(first.slot_id) is SlotState.RETURNED
    with pytest.raises(SlotBurnedError):
        ledger.execute_first_attempt(
            first.slot_id,
            runtime_parent=runtime_parent,
            repository_root=ROOT,
            dispatch=first.dispatch,
            runner=lambda _runtime, _dispatch: ArtifactPayload(b"second"),
        )

    progress = hard_blind_pilot_progress_v1(bound, ledger=ledger)
    assert progress["complete"] is False
    assert progress["terminal_count"] == 1
    with pytest.raises(RuntimeError, match="all 64 selected slots"):
        score_hard_blind_pilot_v1(bound, ledger=ledger)

    orphan = prepared.slots[1]
    orphan_runtime = ledger.allocate_runtime_directory(
        orphan.slot_id,
        runtime_parent=runtime_parent,
        repository_root=ROOT,
    )
    ledger.start(orphan.slot_id, runtime=orphan_runtime, dispatch=orphan.dispatch)
    assert ledger.state(orphan.slot_id) is SlotState.STARTED

    def fail_runner(_runtime, _dispatch):
        raise RuntimeError("synthetic runner failure")

    for slot in prepared.slots[2:]:
        with pytest.raises(AttemptRunnerFailed):
            ledger.execute_first_attempt(
                slot.slot_id,
                runtime_parent=runtime_parent,
                repository_root=ROOT,
                dispatch=slot.dispatch,
                runner=fail_runner,
            )
    completed = hard_blind_pilot_progress_v1(bound, ledger=ledger)
    assert completed["complete"] is True
    assert completed["terminal_count"] == 63
    assert completed["burned_without_outcome_count"] == 1
    assert completed["scorable_count"] == 64
    scoring = score_hard_blind_pilot_v1(bound, ledger=ledger)
    assert len(scoring.results) == 64
    assert scoring.results[0].status is HardBlindPilotAttemptStatusV1.ARTIFACT_INVALID
    assert scoring.results[1].status is HardBlindPilotAttemptStatusV1.NOT_RETURNED
    assert Counter(result.status for result in scoring.results) == Counter(
        {
            HardBlindPilotAttemptStatusV1.ARTIFACT_INVALID: 1,
            HardBlindPilotAttemptStatusV1.NOT_RETURNED: 1,
            HardBlindPilotAttemptStatusV1.RUNNER_FAILED: 62,
        }
    )
    assert scoring.correct_count == 0
    assert scoring.accuracy == 0.0
    assert scoring.decision == "reject_too_hard"
    summary = scoring.privacy_safe_summary()
    assert sum(group["total"] for group in summary["per_domain"].values()) == 64
    assert sum(group["total"] for group in summary["per_family"].values()) == 64
    assert "per_case" not in summary
    assert summary["model_identity_verified"] is False


def test_progress_recovers_a_signed_selected_orphan_before_classification(
    prepared,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    root = tmp_path_factory.mktemp("hard-blind-recoverable-orphan")
    ledger = AttemptLedger.create(
        root / "ledger",
        key=prepared.private_material.ledger_key,
        slots=prepared.commitments,
    )
    runtime_parent = root / "runtime"
    runtime_parent.mkdir()
    bound = BoundHardBlindPilotV1(prepared, ledger.ledger_digest)
    slot = prepared.slots[0]
    runtime = ledger.allocate_runtime_directory(
        slot.slot_id,
        runtime_parent=runtime_parent,
        repository_root=ROOT,
    )
    started = ledger.start(slot.slot_id, runtime=runtime, dispatch=slot.dispatch)
    ledger.persist_returned(
        started,
        ArtifactPayload(
            b"not-a-codex-artifact",
            HARD_BLIND_PRIVATE_ARTIFACT_MEDIA_TYPE_V1,
        ),
    )
    assert ledger.state(slot.slot_id) is SlotState.STARTED

    progress = hard_blind_pilot_progress_v1(bound, ledger=ledger)
    assert ledger.state(slot.slot_id) is SlotState.RETURNED
    assert progress["terminal_count"] == 1
    assert progress["burned_without_outcome_count"] == 0
    assert progress["complete"] is False


def _synthetic_scoring(correct: int) -> HardBlindPilotScoringV1:
    results = []
    for ordinal, case_id in enumerate(HARD_BLIND_PILOT_ORIGINAL_CASE_IDS_V1):
        is_correct = ordinal < correct
        results.append(
            HardBlindPilotCaseResultV1(
                ordinal=ordinal,
                original_case_id=case_id,
                domain=f"domain-{ordinal // 16}",
                derivation_family=f"family-{ordinal % 4}",
                status=(
                    HardBlindPilotAttemptStatusV1.CORRECT
                    if is_correct
                    else HardBlindPilotAttemptStatusV1.INCORRECT
                ),
                correct=is_correct,
                response_digest="sha256:" + f"{ordinal + 1:064x}",
                verifier_evidence_digest="sha256:" + f"{ordinal + 101:064x}",
            )
        )
    return HardBlindPilotScoringV1(
        run_spec_digest="sha256:" + "1" * 64,
        attempt_ledger_digest="sha256:" + "2" * 64,
        results=tuple(results),
    )


def test_strict_difficulty_thresholds_use_the_fixed_64_case_denominator() -> None:
    assert _synthetic_scoring(19).accuracy < 0.30
    assert _synthetic_scoring(19).decision == "reject_too_hard"
    assert 0.45 <= _synthetic_scoring(29).accuracy <= 0.75
    assert _synthetic_scoring(29).decision == "target_band"
    assert _synthetic_scoring(49).decision == "outside_target_not_rejected"
    assert _synthetic_scoring(55).accuracy > 0.85
    assert _synthetic_scoring(55).decision == "reject_too_easy"


def test_scoring_record_rejects_substituted_fixed_case_identity() -> None:
    scoring = _synthetic_scoring(0)
    substituted = replace(
        scoring.results[0],
        original_case_id=HARD_BLIND_PILOT_ORIGINAL_CASE_IDS_V1[1],
    )
    with pytest.raises(ValueError, match="case identities"):
        replace(scoring, results=(substituted, *scoring.results[1:]))


def test_nonselected_smoke_case_can_never_consume_a_fixed_slot() -> None:
    smoke = pilot_module._smoke_case_v1()
    assert smoke.case_id not in HARD_BLIND_PILOT_ORIGINAL_CASE_IDS_V1
    assert hard_blind_solver_prompt_v1(smoke)


def test_known_relational_shortcut_disables_selected_collection_before_smoke(
    bound: BoundHardBlindPilotV1,
    pristine_ledger: AttemptLedger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke_calls: list[str] = []
    monkeypatch.setattr(
        pilot_module,
        "run_nonselected_hard_blind_smoke_v1",
        lambda *_args, **_kwargs: smoke_calls.append("unexpected"),
    )
    assert pilot_module.HARD_BLIND_PILOT_COLLECTION_ENABLED_V1 is False
    assert "relational shortcut" in pilot_module.HARD_BLIND_PILOT_COLLECTION_BLOCKER_V1
    with pytest.raises(RuntimeError, match="selected collection is disabled"):
        execute_hard_blind_pilot_v1(
            bound,
            ledger=pristine_ledger,
            smoke_runtime_directory=ROOT,
            runtime_parent=ROOT,
            repository_root=ROOT,
            auth_file=ROOT / "unused-auth.json",
        )
    assert smoke_calls == []
    assert all(
        pristine_ledger.state(slot.slot_id) is SlotState.PENDING for slot in bound.prepared.slots
    )


def test_execute_requires_a_fresh_live_smoke_before_any_selected_start(
    prepared,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert "smoke_artifact" not in inspect.signature(execute_hard_blind_pilot_v1).parameters
    root = tmp_path_factory.mktemp("hard-blind-fresh-smoke")
    ledger = AttemptLedger.create(
        root / "ledger",
        key=prepared.private_material.ledger_key,
        slots=prepared.commitments,
    )
    runtime_parent = root / "runtime"
    smoke_runtime = root / "smoke"
    runtime_parent.mkdir()
    smoke_runtime.mkdir()
    auth_file = root / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    bound = BoundHardBlindPilotV1(prepared, ledger.ledger_digest)
    calls: list[str] = []

    monkeypatch.setattr(
        pilot_module.PreparedHardBlindPilotV1,
        "validate_current",
        lambda _self: None,
    )
    monkeypatch.setattr(pilot_module, "HARD_BLIND_PILOT_COLLECTION_ENABLED_V1", True)

    def fail_fresh_smoke(*_args, **_kwargs):
        calls.append("fresh-smoke")
        raise RuntimeError("usage limit")

    monkeypatch.setattr(
        pilot_module,
        "run_nonselected_hard_blind_smoke_v1",
        fail_fresh_smoke,
    )
    with pytest.raises(RuntimeError, match="usage limit"):
        execute_hard_blind_pilot_v1(
            bound,
            ledger=ledger,
            smoke_runtime_directory=smoke_runtime,
            runtime_parent=runtime_parent,
            repository_root=ROOT,
            auth_file=auth_file,
        )
    assert calls == ["fresh-smoke"]
    assert all(ledger.state(slot.slot_id) is SlotState.PENDING for slot in prepared.slots)


def test_lifecycle_lock_refuses_concurrent_progress(
    bound: BoundHardBlindPilotV1,
    pristine_ledger: AttemptLedger,
) -> None:
    with (
        pilot_module._hard_blind_lifecycle_lock_v1(pristine_ledger),
        pytest.raises(RuntimeError, match="already active"),
    ):
        hard_blind_pilot_progress_v1(bound, ledger=pristine_ledger)
    assert hard_blind_pilot_progress_v1(bound, ledger=pristine_ledger)["complete"] is False


def test_executable_and_auth_preflight_fail_before_smoke_or_selected_start(
    prepared,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path_factory.mktemp("hard-blind-live-preflight")
    ledger = AttemptLedger.create(
        root / "ledger",
        key=prepared.private_material.ledger_key,
        slots=prepared.commitments,
    )
    runtime_parent = root / "runtime"
    smoke_runtime = root / "smoke"
    runtime_parent.mkdir()
    smoke_runtime.mkdir()
    auth_file = root / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    bound = BoundHardBlindPilotV1(prepared, ledger.ledger_digest)
    smoke_calls: list[str] = []
    monkeypatch.setattr(
        pilot_module.PreparedHardBlindPilotV1,
        "validate_current",
        lambda _self: None,
    )
    monkeypatch.setattr(pilot_module, "HARD_BLIND_PILOT_COLLECTION_ENABLED_V1", True)
    monkeypatch.setattr(
        pilot_module,
        "run_nonselected_hard_blind_smoke_v1",
        lambda *_args, **_kwargs: smoke_calls.append("unexpected"),
    )
    expected = prepared.policy.executable_provenance
    changed = ExecutableProvenance(
        canonical_path=expected.canonical_path,
        size_bytes=expected.size_bytes + 1,
        sha256_digest=expected.sha256_digest,
    )
    original_capture = pilot_module.capture_codex_executable_provenance
    monkeypatch.setattr(
        pilot_module,
        "capture_codex_executable_provenance",
        lambda _path: changed,
    )
    with pytest.raises(ValueError, match="differs from preregistered"):
        execute_hard_blind_pilot_v1(
            bound,
            ledger=ledger,
            smoke_runtime_directory=smoke_runtime,
            runtime_parent=runtime_parent,
            repository_root=ROOT,
            auth_file=auth_file,
        )
    monkeypatch.setattr(
        pilot_module,
        "capture_codex_executable_provenance",
        original_capture,
    )
    changed_auth = root / "renamed.json"
    changed_auth.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="regular-file constraints"):
        execute_hard_blind_pilot_v1(
            bound,
            ledger=ledger,
            smoke_runtime_directory=smoke_runtime,
            runtime_parent=runtime_parent,
            repository_root=ROOT,
            auth_file=changed_auth,
        )
    assert smoke_calls == []
    assert all(ledger.state(slot.slot_id) is SlotState.PENDING for slot in prepared.slots)


def test_exact_record_boundaries_reject_subclasses(run_policy) -> None:
    class PolicySubclass(HardBlindPilotRunPolicyV1):
        pass

    subclass = PolicySubclass(
        source_revision=run_policy.source_revision,
        executable_provenance=run_policy.executable_provenance,
    )
    with pytest.raises(ValueError, match="exact run policy"):
        prepare_hard_blind_pilot_v1(subclass)

    with pytest.raises(ValueError, match="exact bound-pilot"):
        score_hard_blind_pilot_v1(object(), ledger=object())
