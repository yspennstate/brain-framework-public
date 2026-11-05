from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

import math_brain.external_blind_gold_audit_collect_v3 as collector
import math_brain.external_blind_gold_audit_v3 as gold
import math_brain.mathnet_body_card_review_v3 as review
from math_brain.attempt_ledger import ArtifactPayload, AttemptLedger, SlotState
from math_brain.canonical_ed25519_v3 import CanonicalEd25519SignerV3
from math_brain.codex_runner import ExecutableProvenance
from math_brain.exact_answer_v3 import ExactRationalV3
from math_brain.mathnet_bank_v3 import MathNetPrivateGoldV3
from math_brain.models import canonical_json


def digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def item_id(index: int) -> str:
    return f"pilot-curation:{index + 1:064x}"


def card_id(index: int) -> str:
    return f"pilot-card:{index + 1:064x}"


def _projection_value() -> dict[str, object]:
    return {
        "cards": [
            {
                "card_id": card_id(index),
                "guidance": f"Synthetic guidance {index + 1}.",
                "title": f"Synthetic method {index + 1}",
            }
            for index in range(review.BODY_CARD_FROZEN_CARD_COUNT_V3)
        ],
        "content_digest": digest("external-gold synthetic projection content"),
        "engineering_upper_bound_only": True,
        "gold_present": False,
        "items": [
            {
                "problem_text": f"Compute the exact synthetic value for case {index + 1}.",
                "review_item_id": item_id(index),
            }
            for index in range(review.BODY_CARD_FROZEN_ITEM_COUNT_V3)
        ],
        "schema": review.BODY_CARD_PROJECTION_SCHEMA_V3,
        "schema_version": review.BODY_CARD_REVIEW_VERSION_V3,
        "source_metadata_present": False,
    }


def _selection(
    projection: review.FrozenBodyCardProjectionV3,
    *,
    offset: int = 0,
) -> gold.ColdReplayVerifiedBodyCardSelectionV3:
    selected = tuple(
        review.SelectedBodyCardCaseV3(
            review_item_id=projection.items[offset + index].review_item_id,
            domain=cell[0],
            difficulty=cell[1],
            card_id=card_id(index % review.BODY_CARD_FROZEN_CARD_COUNT_V3),
            event_family_digest=digest(f"event family {offset + index}"),
        )
        for index, cell in enumerate(review.BODY_CARD_CELL_ORDER_V3)
    )
    vector_digest = review._domain_digest(
        [item.to_dict() for item in selected],
        domain="org.math-research-brain/engineering-pilot-body-card-selection-vector/v1",
    )
    report = review.BodyCardSelectionReportV3(
        status=review.BodyCardSelectionStatusV3.FEASIBLE,
        selected=selected,
        candidate_count=len(selected),
        reviewer_a_decision_digest=digest(f"selection {offset} reviewer A"),
        reviewer_b_decision_digest=digest(f"selection {offset} reviewer B"),
        event_family_bindings_digest=digest(f"selection {offset} bindings"),
        selection_vector_digest=vector_digest,
        review_precommitment_digest=digest(f"selection {offset} precommitment"),
        reviewer_a_key_fingerprint=digest(f"selection {offset} key A"),
        reviewer_b_key_fingerprint=digest(f"selection {offset} key B"),
    )
    return gold.ColdReplayVerifiedBodyCardSelectionV3._derive(
        report=report,
        signed_pair_digest=digest(f"selection {offset} signed pair"),
        review_precommitment_digest=report.review_precommitment_digest,
        bindings_digest=report.event_family_bindings_digest,
    )


def _private_gold(index: int) -> MathNetPrivateGoldV3:
    return MathNetPrivateGoldV3(
        answer=ExactRationalV3(index + 1, 1),
        final_answer_text_digest=digest(f"final answer {index}"),
        solution_text_digests=(digest(f"solution {index}"),),
        confirming_solution_indices=(),
    )


def _vault_value(
    projection: review.FrozenBodyCardProjectionV3,
) -> dict[str, object]:
    return {
        "card_map": [
            {
                "card_id": card.card_id,
                "family": f"family-{index}",
                "record_id": f"record-{index}",
                "semantic_digest": digest(f"card semantic {index}"),
            }
            for index, card in enumerate(projection.cards)
        ],
        "items": [
            {
                "event_family_digest": digest(f"event family {index}"),
                "gold": _private_gold(index)._private_dict(),
                "problem_text_digest": digest_text(item.problem_text),
                "review_item_id": item.review_item_id,
                "selection_coordinate_digest": digest(f"coordinate {index}"),
                "source_ordinal": index,
            }
            for index, item in enumerate(projection.items)
        ],
        "projection_content_digest": projection.content_digest,
        "source_manifest_digest": digest("source manifest"),
        "universe_content_digest": digest("private universe"),
    }


def digest_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


@pytest.fixture
def study(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    projection_value = _projection_value()
    projection_raw = (canonical_json(projection_value) + "\n").encode("utf-8")
    monkeypatch.setattr(
        review,
        "BODY_CARD_FROZEN_PROJECTION_RAW_DIGEST_V3",
        digest_bytes(projection_raw),
    )
    monkeypatch.setattr(
        review,
        "BODY_CARD_FROZEN_PROJECTION_CONTENT_DIGEST_V3",
        projection_value["content_digest"],
    )
    projection = review.load_frozen_body_card_projection_v3(projection_raw)
    vault_value = _vault_value(projection)
    vault_raw = (canonical_json(vault_value) + "\n").encode("utf-8")
    monkeypatch.setattr(
        gold,
        "EXTERNAL_GOLD_VAULT_RAW_DIGEST_V3",
        digest_bytes(vault_raw),
    )
    vault = gold.load_frozen_external_gold_vault_v3(vault_raw, projection)
    selection = _selection(projection)
    alias_key = b"\x22" * 32
    plan_a = gold.build_external_gold_audit_plan_v3(
        selection,
        projection,
        gold.ExternalGoldReviewerSlotV3.REVIEWER_A,
        alias_key=alias_key,
    )
    plan_b = gold.build_external_gold_audit_plan_v3(
        selection,
        projection,
        gold.ExternalGoldReviewerSlotV3.REVIEWER_B,
        alias_key=alias_key,
    )
    assert plan_a.review_item_order_ids != plan_b.review_item_order_ids
    return {
        "alias_key": alias_key,
        "plan_a": plan_a,
        "plan_b": plan_b,
        "projection": projection,
        "selection": selection,
        "vault": vault,
        "vault_raw": vault_raw,
        "vault_value": vault_value,
    }


def digest_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _material() -> collector.ExternalGoldPrivateMaterialV3:
    return collector.ExternalGoldPrivateMaterialV3(
        ledger_key=b"\x11" * 32,
        alias_key=b"\x22" * 32,
        reviewer_a_seed=b"\x33" * 32,
        reviewer_b_seed=b"\x44" * 32,
    )


def _policy() -> collector.ExternalGoldAuditRunPolicyV3:
    executable = str(Path(sys.executable).resolve())
    return collector.ExternalGoldAuditRunPolicyV3(
        executable_provenance=ExecutableProvenance(
            canonical_path=executable,
            size_bytes=max(1, Path(executable).stat().st_size),
            sha256_digest=digest("synthetic executable"),
        )
    )


@dataclass(frozen=True)
class BoundFixture:
    prepared: collector.PreparedExternalGoldAuditsV3
    bound: collector.BoundExternalGoldAuditsV3
    ledger: AttemptLedger
    repository: Path
    runtime: Path
    auth_file: Path
    private_state: Path


def _bound_fixture(tmp_path: Path, study: dict[str, object]) -> BoundFixture:
    repository = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    repository.mkdir()
    runtime.mkdir()
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    material = _material()
    prepared = collector.prepare_external_gold_audits_v3(
        study["selection"],
        study["projection"],
        study["vault"],
        _policy(),
        private_material=material,
        auth_snapshot=collector.ExternalGoldAuthSnapshotV3(
            canonical_path=str(auth_file.resolve()),
            device=1,
            inode=2,
            size_bytes=2,
            content_commitment="hmac-sha256:" + "1" * 64,
        ),
        code_identity=collector.ExternalGoldCodeIdentityV3(
            repository_root=str(repository.resolve()),
            commit_oid="1" * 40,
            annotated_tag="external-blind-gold-audit-v3-test",
            tag_object_oid="2" * 40,
        ),
    )
    private_state = collector.save_external_gold_private_state_v3(
        (tmp_path / "prepared-private-state.json").resolve(),
        prepared,
    )
    ledger = collector.create_external_gold_audit_ledger_v3(
        prepared,
        tmp_path / "ledger",
    )
    bound = collector.bind_external_gold_audits_v3(
        prepared,
        ledger,
        private_state_path=private_state,
    )
    return BoundFixture(
        prepared,
        bound,
        ledger,
        repository,
        runtime,
        auth_file,
        private_state,
    )


def _run_evidence(ordinal: int, policy: collector.ExternalGoldAuditRunPolicyV3) -> (
    gold.ExternalGoldRunEvidenceV3
):
    return gold.ExternalGoldRunEvidenceV3(
        protocol_execution_id=digest(f"protocol execution {ordinal}"),
        execution_nonce=f"{ordinal + 1:064x}",
        thread_id=f"thread-{ordinal}",
        turn_id=f"turn-{ordinal}",
        transcript_digest=digest(f"transcript {ordinal}"),
        stderr_digest=digest(f"stderr {ordinal}"),
        runtime_directory_device=1,
        runtime_directory_inode=100 + ordinal,
        codex_home_directory_device=1,
        codex_home_directory_inode=200 + ordinal,
        codex_home_path_digest=digest(f"codex home {ordinal}"),
        executable_sha256_digest=policy.executable_provenance.sha256_digest,
        executable_size_bytes=policy.executable_provenance.size_bytes,
        command_policy_digest=policy.command_policy_digest,
        process_policy_digest=policy.process_policy_digest,
        request_policy_digest=policy.request_policy_digest,
        requested_model=policy.requested_model,
        requested_provider=policy.requested_provider,
        requested_effort=policy.requested_effort,
        accepted_model=policy.requested_model,
        accepted_provider=policy.requested_provider,
        accepted_effort=policy.requested_effort,
        timeout_seconds=policy.timeout_seconds,
        model_identity_verified=False,
    )


def _decisions(
    plan: gold.ExternalGoldAuditPlanV3,
    *,
    unsolved_review_id: str | None = None,
    answer_override: dict[str, ExactRationalV3] | None = None,
) -> tuple[gold.ExternalGoldAuditDecisionV3, ...]:
    overrides = {} if answer_override is None else answer_override
    result = []
    for binding in plan.bindings:
        index = int(binding.review_item_id.split(":", 1)[1], 16) - 1
        if binding.review_item_id == unsolved_review_id:
            result.append(
                gold.ExternalGoldAuditDecisionV3(
                    case_id=binding.case_id,
                    status=gold.ExternalGoldDecisionStatusV3.UNSOLVED,
                    answer=None,
                    verification="Unable to verify exactly.",
                )
            )
        else:
            result.append(
                gold.ExternalGoldAuditDecisionV3(
                    case_id=binding.case_id,
                    status=gold.ExternalGoldDecisionStatusV3.SOLVED,
                    answer=overrides.get(
                        binding.review_item_id,
                        ExactRationalV3(index + 1, 1),
                    ),
                    verification="Checked by exact substitution.",
                )
            )
    return tuple(sorted(result, key=lambda item: item.case_id))


def _manual_pair(
    fixture: BoundFixture,
    *,
    unsolved_review_id: str | None = None,
    answer_override: dict[str, ExactRationalV3] | None = None,
) -> tuple[
    gold.ColdReplayVerifiedExternalGoldPairV3,
    gold.CollectedExternalGoldAuditV3,
    gold.CollectedExternalGoldAuditV3,
    bytes,
]:
    collected: list[gold.CollectedExternalGoldAuditV3] = []
    for ordinal, (plan, slot) in enumerate(
        zip(fixture.prepared.plans, fixture.prepared.slots, strict=True)
    ):
        artifact = canonical_json({"private_fixture_artifact": ordinal}).encode("utf-8")
        attempt = gold.ExternalGoldAttemptEvidenceV3(
            ledger_digest=fixture.ledger.ledger_digest,
            slot_id=slot.slot_id,
            ordinal=ordinal,
            started_record_digest=digest(f"started {ordinal}"),
            selection_record_digest=digest(f"outcome selection {ordinal}"),
            terminal_record_digest=digest(f"terminal {ordinal}"),
            object_digest=digest(f"object {ordinal}"),
            artifact_payload_digest=digest_bytes(artifact),
            terminal_kind="RETURNED",
            prerequisite_terminal_digest=None,
        )
        receipt = gold.ExternalGoldAuditReceiptV3(
            reviewer_slot=slot.reviewer_slot,
            status=gold.ExternalGoldReceiptStatusV3.RETURNED,
            decisions=_decisions(
                plan,
                unsolved_review_id=unsolved_review_id,
                answer_override=answer_override,
            ),
            failure_code=None,
            selection_report_digest=plan.selection_report_digest,
            projection_raw_file_digest=plan.projection_raw_file_digest,
            projection_content_digest=plan.projection_content_digest,
            order_digest=plan.order_digest,
            presentation_digest=plan.presentation_digest,
            prompt_digest=plan.prompt_digest,
            output_schema_digest=plan.output_schema_digest,
            audit_precommitment_digest=fixture.bound.public_precommitment_digest,
            attempt=attempt,
            run=_run_evidence(ordinal, fixture.prepared.policy),
        )
        collected.append(
            gold.CollectedExternalGoldAuditV3(
                manifest=gold.sign_external_gold_audit_receipt_v3(
                    receipt,
                    fixture.prepared.private_material.signer(slot.reviewer_slot),
                ),
                artifact_bytes=artifact,
            )
        )
    raw = gold.serialize_signed_external_gold_audit_pair_v3(
        collected[0],
        collected[1],
    )
    pair = gold.ColdReplayVerifiedExternalGoldPairV3._derive_after_artifact_replay(
        reviewer_a=collected[0],
        reviewer_b=collected[1],
        signed_pair_bytes=raw,
        precommitment_digest=fixture.bound.public_precommitment_digest,
    )
    return pair, collected[0], collected[1], raw


def test_body_only_projections_and_public_commitment_do_not_leak_gold_or_metadata(
    study: dict[str, object],
    tmp_path: Path,
) -> None:
    fixture = _bound_fixture(tmp_path, study)
    selected_ids = set(study["selection"].selected_review_item_ids)
    for plan in fixture.prepared.plans:
        payload = json.loads(plan.payload_json)
        assert set(payload) == {"items", "schema", "schema_version"}
        assert len(payload["items"]) == 12
        assert all(set(item) == {"case_id", "problem"} for item in payload["items"])
        assert all(item["case_id"].startswith("audit-case:") for item in payload["items"])
        assert not any(review_id in plan.payload_json for review_id in selected_ids)
        assert not any(card_id(index) in plan.payload_json for index in range(20))
        assert "external-gold" not in plan.prompt_text.lower()
        assert b"external-gold" not in plan.output_schema_json.lower()
    assert fixture.prepared.reviewer_a_plan.review_item_order_ids != (
        fixture.prepared.reviewer_b_plan.review_item_order_ids
    )
    public_text = fixture.bound.public_precommitment_bytes.decode("utf-8")
    assert not any(review_id in public_text for review_id in selected_ids)
    assert not any(
        item.problem_text in public_text for item in study["projection"].items
    )
    core = fixture.bound.public_precommitment["core"]
    assert core["gold_present_in_reviewer_payload"] is False
    assert core["source_metadata_present_in_reviewer_payload"] is False
    assert core["served_model_identity_verified"] is False
    assert core["provider_independence_verified"] is False
    assert core["epistemic_independence_verified"] is False
    assert core["reviewer_origin_verified"] is False


def test_task_order_mismatch_is_rejected(study: dict[str, object]) -> None:
    plan = study["plan_a"]
    by_case = {item.case_id: item for item in _decisions(plan)}
    ordered = [by_case[case_id].to_private_dict() for case_id in plan.case_order_ids]
    ordered[0], ordered[1] = ordered[1], ordered[0]
    raw = canonical_json(
        {
            "decisions": ordered,
            "schema": gold.EXTERNAL_GOLD_OUTPUT_SCHEMA_V3,
            "schema_version": gold.EXTERNAL_GOLD_AUDIT_VERSION_V3,
        }
    )
    with pytest.raises(ValueError, match="order differs"):
        gold.parse_external_gold_audit_output_v3(raw, plan)


def test_fabricated_signer_and_receipt_fail_anchored_authentication(
    study: dict[str, object],
    tmp_path: Path,
) -> None:
    fixture = _bound_fixture(tmp_path, study)
    _pair, reviewer_a, reviewer_b, _raw = _manual_pair(fixture)
    rogue = CanonicalEd25519SignerV3(
        b"\x99" * 32,
        domain=gold.external_gold_reviewer_signing_domain_v3(
            gold.ExternalGoldReviewerSlotV3.REVIEWER_A
        ),
    )
    rogue_manifest = gold.SignedExternalGoldAuditReceiptV3(
        receipt=reviewer_a.manifest.receipt,
        key_fingerprint=rogue.verifier().key_fingerprint,
        authority_commitment=rogue.verifier().authority_commitment,
        signature=rogue.sign(reviewer_a.manifest.receipt.canonical_record()),
    )
    fabricated = gold.CollectedExternalGoldAuditV3(
        manifest=rogue_manifest,
        artifact_bytes=reviewer_a.artifact_bytes,
    )
    raw = gold.serialize_signed_external_gold_audit_pair_v3(fabricated, reviewer_b)
    with pytest.raises(ValueError, match="authority differs"):
        gold.authenticate_signed_external_gold_audit_pair_bytes_v3(
            raw,
            precommitment_bytes=fixture.bound.public_precommitment_bytes,
            expected_precommitment_digest=fixture.bound.public_precommitment_digest,
        )


def test_selection_substitution_is_rejected(
    study: dict[str, object],
    tmp_path: Path,
) -> None:
    fixture = _bound_fixture(tmp_path, study)
    pair, _a, _b, _raw = _manual_pair(fixture)
    substituted = _selection(study["projection"], offset=12)
    plan_a = gold.build_external_gold_audit_plan_v3(
        substituted,
        study["projection"],
        gold.ExternalGoldReviewerSlotV3.REVIEWER_A,
        alias_key=study["alias_key"],
    )
    plan_b = gold.build_external_gold_audit_plan_v3(
        substituted,
        study["projection"],
        gold.ExternalGoldReviewerSlotV3.REVIEWER_B,
        alias_key=study["alias_key"],
    )
    with pytest.raises(ValueError, match="differs from its exact task plan"):
        gold.assemble_external_gold_audit_v3(
            pair,
            substituted,
            study["projection"],
            study["vault"],
            plan_a,
            plan_b,
            report_key=b"\xaa" * 32,
        )


def test_vault_body_mismatch_fails_strict_loader(
    study: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changed = json.loads(json.dumps(study["vault_value"]))
    changed["items"][0]["problem_text_digest"] = digest("wrong problem body")
    raw = (canonical_json(changed) + "\n").encode("utf-8")
    monkeypatch.setattr(gold, "EXTERNAL_GOLD_VAULT_RAW_DIGEST_V3", digest_bytes(raw))
    with pytest.raises(ValueError, match="problem body differs"):
        gold.load_frozen_external_gold_vault_v3(raw, study["projection"])


def test_private_source_gold_disagreement_fails_whole_lane_without_topup(
    study: dict[str, object],
    tmp_path: Path,
) -> None:
    fixture = _bound_fixture(tmp_path, study)
    target = study["selection"].selected_review_item_ids[0]
    pair, _a, _b, _raw = _manual_pair(
        fixture,
        answer_override={target: ExactRationalV3(999, 1)},
    )
    report = gold.assemble_external_gold_audit_v3(
        pair,
        study["selection"],
        study["projection"],
        study["vault"],
        fixture.prepared.reviewer_a_plan,
        fixture.prepared.reviewer_b_plan,
        report_key=b"\xab" * 32,
    )
    assert report.status is gold.ExternalGoldAuditStatusV3.INFEASIBLE
    assert report.failure_codes == ("source_gold_disagreement",)
    assert report.audited_selected_count == 12
    assert report.admitted_count == 0
    assert report.admitted == ()


def test_unsolved_case_fails_exact_denominator_and_never_tops_up(
    study: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _bound_fixture(tmp_path, study)
    target = study["selection"].selected_review_item_ids[0]
    pair, _a, _b, _raw = _manual_pair(fixture, unsolved_review_id=target)

    def private_gold_must_remain_closed(_vault: object) -> object:
        raise AssertionError("private gold was opened before complete reviewer agreement")

    monkeypatch.setattr(
        gold.FrozenExternalGoldVaultV3,
        "_entry_by_review_id",
        private_gold_must_remain_closed,
    )
    monkeypatch.setattr(
        gold.FrozenExternalGoldVaultV3,
        "validate_current",
        private_gold_must_remain_closed,
    )
    report = gold.assemble_external_gold_audit_v3(
        pair,
        study["selection"],
        study["projection"],
        study["vault"],
        fixture.prepared.reviewer_a_plan,
        fixture.prepared.reviewer_b_plan,
        report_key=b"\xac" * 32,
    )
    assert report.status is gold.ExternalGoldAuditStatusV3.INFEASIBLE
    assert report.failure_codes == ("reviewer_unsolved",)
    assert report.audited_selected_count == 12
    assert report.admitted == ()
    selected = set(study["selection"].selected_review_item_ids)
    assert {
        binding.review_item_id
        for plan in fixture.prepared.plans
        for binding in plan.bindings
    } == selected


def _install_inert_execution(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[int],
) -> None:
    monkeypatch.setattr(collector, "_validate_live_inputs", lambda *args, **kwargs: b"{}")

    def fake_native_slot(
        prepared: collector.PreparedExternalGoldAuditsV3,
        ledger: AttemptLedger,
        slot: collector.PreparedExternalGoldSlotV3,
        *,
        runtime_parent: Path,
        repository_root: Path,
        auth_bytes: bytes,
    ):
        del prepared, auth_bytes
        calls.append(slot.ordinal)

        def runner(_runtime: Path, _dispatch: object) -> ArtifactPayload:
            return ArtifactPayload(
                canonical_json({"synthetic_slot": slot.ordinal}).encode("utf-8")
            )

        return ledger.execute_first_attempt(
            slot.slot_id,
            runtime_parent=runtime_parent,
            repository_root=repository_root,
            dispatch=slot.dispatch,
            runner=runner,
        )

    monkeypatch.setattr(collector, "_run_native_slot", fake_native_slot)


def test_collector_never_retries_and_started_records_have_no_predecessor(
    study: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _bound_fixture(tmp_path, study)
    calls: list[int] = []
    _install_inert_execution(monkeypatch, calls)
    first = collector.collect_external_gold_audits_v3(
        fixture.bound,
        ledger=fixture.ledger,
        public_precommitment_bytes=fixture.bound.public_precommitment_bytes,
        expected_public_precommitment_digest=fixture.bound.public_precommitment_digest,
        runtime_parent=fixture.runtime,
        repository_root=fixture.repository,
        auth_file=fixture.auth_file,
    )
    second = collector.collect_external_gold_audits_v3(
        fixture.bound,
        ledger=fixture.ledger,
        public_precommitment_bytes=fixture.bound.public_precommitment_bytes,
        expected_public_precommitment_digest=fixture.bound.public_precommitment_digest,
        runtime_parent=fixture.runtime,
        repository_root=fixture.repository,
        auth_file=fixture.auth_file,
    )
    assert calls == [0, 1]
    assert first == second
    assert all(
        fixture.ledger.started_attempt(slot.slot_id).prerequisite_terminal_digest is None
        for slot in fixture.prepared.slots
    )
    assert all(
        receipt.manifest.receipt.failure_code == "artifact_invalid" for receipt in first
    )
    signed_pair = gold.serialize_signed_external_gold_audit_pair_v3(first[0], first[1])
    replayed = collector.cold_replay_external_gold_audits_v3(
        signed_pair,
        bound=fixture.bound,
        public_precommitment_bytes=fixture.bound.public_precommitment_bytes,
        expected_public_precommitment_digest=fixture.bound.public_precommitment_digest,
    )
    assert replayed.reviewer_a.failure_code == "artifact_invalid"
    assert replayed.reviewer_b.failure_code == "artifact_invalid"


def test_private_state_restart_restores_exact_slots_without_remint(
    study: dict[str, object],
    tmp_path: Path,
) -> None:
    fixture = _bound_fixture(tmp_path, study)
    raw = collector.serialize_external_gold_private_state_v3(fixture.prepared)
    assert fixture.bound.private_state_digest == digest_bytes(raw)
    private_path = collector.save_external_gold_private_state_v3(
        (tmp_path / "external-gold-private-state.json").resolve(),
        fixture.prepared,
    )
    restored = collector.load_external_gold_private_state_v3(
        private_path,
        study["selection"],
        study["projection"],
        study["vault"],
    )
    reopened = collector.open_external_gold_audit_ledger_v3(
        restored,
        fixture.ledger.root,
    )
    rebound = collector.restore_bound_external_gold_audits_v3(
        restored,
        reopened,
        private_state_path=private_path,
        public_precommitment_bytes=fixture.bound.public_precommitment_bytes,
        expected_public_precommitment_digest=fixture.bound.public_precommitment_digest,
    )
    assert tuple(slot.slot_id for slot in restored.slots) == tuple(
        slot.slot_id for slot in fixture.prepared.slots
    )
    assert tuple(slot.dispatch.dispatch_nonce for slot in restored.slots) == tuple(
        slot.dispatch.dispatch_nonce for slot in fixture.prepared.slots
    )
    assert rebound.public_precommitment_digest == fixture.bound.public_precommitment_digest

    tampered = json.loads(raw[:-1])
    tampered["slots"][0]["dispatch_nonce"] = digest("reminted dispatch nonce")
    tampered_raw = (canonical_json(tampered) + "\n").encode("utf-8")
    reminted = collector.deserialize_external_gold_private_state_v3(
        tampered_raw,
        study["selection"],
        study["projection"],
        study["vault"],
    )
    reminted_path = collector.save_external_gold_private_state_v3(
        (tmp_path / "reminted-private-state.json").resolve(),
        reminted,
    )
    with pytest.raises(ValueError, match="differs from anchored precommitment"):
        collector.restore_bound_external_gold_audits_v3(
            reminted,
            reopened,
            private_state_path=reminted_path,
            public_precommitment_bytes=fixture.bound.public_precommitment_bytes,
            expected_public_precommitment_digest=fixture.bound.public_precommitment_digest,
        )


def test_valid_artifacts_bind_exact_decision_and_run_receipts(
    study: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _bound_fixture(tmp_path, study)
    artifacts: dict[bytes, SimpleNamespace] = {}
    monkeypatch.setattr(collector, "_validate_live_inputs", lambda *args, **kwargs: b"{}")

    def fake_native_slot(
        prepared: collector.PreparedExternalGoldAuditsV3,
        ledger: AttemptLedger,
        slot: collector.PreparedExternalGoldSlotV3,
        *,
        runtime_parent: Path,
        repository_root: Path,
        auth_bytes: bytes,
    ):
        del auth_bytes
        plan = prepared.plans[slot.ordinal]
        decisions = {item.case_id: item for item in _decisions(plan)}
        output_text = canonical_json(
            {
                "decisions": [
                    decisions[case_id].to_private_dict() for case_id in plan.case_order_ids
                ],
                "schema": gold.EXTERNAL_GOLD_OUTPUT_SCHEMA_V3,
                "schema_version": gold.EXTERNAL_GOLD_AUDIT_VERSION_V3,
            }
        )
        raw = canonical_json({"synthetic_valid_artifact": slot.ordinal}).encode("utf-8")
        policy = prepared.policy
        artifacts[raw] = SimpleNamespace(
            _raw=raw,
            prompt_text=plan.prompt_text,
            output_schema_json=plan.output_schema_json,
            requested_model=policy.requested_model,
            requested_provider=policy.requested_provider,
            requested_effort=policy.requested_effort,
            timeout_seconds=policy.timeout_seconds,
            executable_path=policy.executable_provenance.canonical_path,
            executable_size_bytes=policy.executable_provenance.size_bytes,
            executable_sha256_digest=policy.executable_provenance.sha256_digest,
            command_tail_policy_digest=policy.command_policy_digest,
            solver_process_isolation_policy_digest=policy.process_policy_digest,
            request_policy_material=collector._expected_request_policy_material(policy),
            standard_popen_used=True,
            executable_lock_enforced=True,
            codex_home_initial_auth_only=True,
            codex_home_cleanup_verified=True,
            credential_leak_scan_passed=True,
            process_exit_code=0,
            forced_termination=False,
            audit=SimpleNamespace(
                eligible=True,
                reasons=(),
                accepted_model=policy.requested_model,
                accepted_provider=policy.requested_provider,
                accepted_effort=policy.requested_effort,
                reroutes=(),
                final_message_count=1,
                model_identity_verified=False,
            ),
            output_text=output_text,
            protocol_execution_id=digest(f"valid protocol {slot.ordinal}"),
            execution_nonce=f"{slot.ordinal + 11:064x}",
            thread_id=f"valid-thread-{slot.ordinal}",
            turn_id=f"valid-turn-{slot.ordinal}",
            transcript_hash=digest(f"valid transcript {slot.ordinal}"),
            stderr_hash=digest(f"valid stderr {slot.ordinal}"),
            runtime_directory_device=1,
            runtime_directory_inode=300 + slot.ordinal,
            codex_home_directory_device=1,
            codex_home_directory_inode=400 + slot.ordinal,
            codex_home_path_digest=digest(f"valid home {slot.ordinal}"),
        )

        def runner(_runtime: Path, _dispatch: object) -> ArtifactPayload:
            return ArtifactPayload(raw)

        return ledger.execute_first_attempt(
            slot.slot_id,
            runtime_parent=runtime_parent,
            repository_root=repository_root,
            dispatch=slot.dispatch,
            runner=runner,
        )

    monkeypatch.setattr(collector, "_run_native_slot", fake_native_slot)
    monkeypatch.setattr(
        collector,
        "deserialize_codex_run_result",
        lambda raw: artifacts[raw],
    )
    monkeypatch.setattr(
        collector,
        "serialize_codex_run_result",
        lambda artifact: artifact._raw,
    )
    collected = collector.collect_external_gold_audits_v3(
        fixture.bound,
        ledger=fixture.ledger,
        public_precommitment_bytes=fixture.bound.public_precommitment_bytes,
        expected_public_precommitment_digest=fixture.bound.public_precommitment_digest,
        runtime_parent=fixture.runtime,
        repository_root=fixture.repository,
        auth_file=fixture.auth_file,
    )
    assert all(
        item.manifest.receipt.status is gold.ExternalGoldReceiptStatusV3.RETURNED
        for item in collected
    )
    assert all(len(item.manifest.receipt.decisions) == 12 for item in collected)
    assert all(item.manifest.receipt.run is not None for item in collected)
    signed_pair = gold.serialize_signed_external_gold_audit_pair_v3(
        collected[0],
        collected[1],
    )
    replayed = collector.cold_replay_external_gold_audits_v3(
        signed_pair,
        bound=fixture.bound,
        public_precommitment_bytes=fixture.bound.public_precommitment_bytes,
        expected_public_precommitment_digest=fixture.bound.public_precommitment_digest,
    )
    report = gold.assemble_external_gold_audit_v3(
        replayed,
        study["selection"],
        study["projection"],
        study["vault"],
        fixture.prepared.reviewer_a_plan,
        fixture.prepared.reviewer_b_plan,
        report_key=b"\xae" * 32,
    )
    assert report.status is gold.ExternalGoldAuditStatusV3.FEASIBLE
    assert report.admitted_count == 12


def test_orphaned_started_is_terminalized_and_never_retried(
    study: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _bound_fixture(tmp_path, study)
    first_slot = fixture.prepared.slots[0]
    lease = fixture.ledger.allocate_runtime_directory(
        first_slot.slot_id,
        runtime_parent=fixture.runtime,
        repository_root=fixture.repository,
    )
    fixture.ledger.start(
        first_slot.slot_id,
        runtime=lease,
        dispatch=first_slot.dispatch,
    )
    calls: list[int] = []
    _install_inert_execution(monkeypatch, calls)
    result = collector.collect_external_gold_audits_v3(
        fixture.bound,
        ledger=fixture.ledger,
        public_precommitment_bytes=fixture.bound.public_precommitment_bytes,
        expected_public_precommitment_digest=fixture.bound.public_precommitment_digest,
        runtime_parent=fixture.runtime,
        repository_root=fixture.repository,
        auth_file=fixture.auth_file,
    )
    assert calls == [1]
    assert fixture.ledger.state(first_slot.slot_id) is SlotState.FAILED
    assert result[0].manifest.receipt.failure_code == "orphaned_started"
    assert result[0].manifest.receipt.attempt.prerequisite_terminal_digest is None
    assert result[1].manifest.receipt.attempt.prerequisite_terminal_digest is None


def test_public_authority_flags_remain_false_after_feasible_assembly(
    study: dict[str, object],
    tmp_path: Path,
) -> None:
    fixture = _bound_fixture(tmp_path, study)
    pair, _a, _b, _raw = _manual_pair(fixture)
    report = gold.assemble_external_gold_audit_v3(
        pair,
        study["selection"],
        study["projection"],
        study["vault"],
        fixture.prepared.reviewer_a_plan,
        fixture.prepared.reviewer_b_plan,
        report_key=b"\xad" * 32,
    )
    assert report.status is gold.ExternalGoldAuditStatusV3.FEASIBLE
    assert len(report.admitted) == 12
    public = report.to_public_dict()
    assert public["confirmatory_claim_authorized"] is False
    assert public["served_model_identity_verified"] is False
    assert public["provider_independence_verified"] is False
    assert public["epistemic_independence_verified"] is False
    assert public["reviewer_origin_verified"] is False
